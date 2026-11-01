"""Disk-backed, interruptible H3 storyboards with one bounded model cache."""
from __future__ import annotations

import copy
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import threading
import time
import uuid

import folder_paths
import nodes
import torch

from .planner import DEMO_SCRIPT, PREFERRED_MODEL, VOICE_MODES
from .prompt_nodes import local_models
from .contract import render_h3_prompt
from .scene_inputs import ASPECTS, RESOLUTIONS, FIT_MODES, fit_reference, resolve_canvas
from .shot_nodes import (AUDIO_VAE, BASE_MODEL, TEXT_ENCODER, TURBO_LORA,
                         TURBO_LORA_4STEP, VIDEO_VAE, H3GenerateShot,
                         H3VideoModelBundle, _combo, _execute_v3, _outputs)
from .native_nodes import AUTHOR_MODEL, H3ReadyModelGate

MODES = ("逐镜参考图", "上一镜末帧", "AI生成分镜", "手动分镜图", "末帧高清续接")
TAIL_SEQUENCE_MODES = ("上一镜末帧", "末帧高清续接")
TAIL_MODES = ("直接续接", "亮度轻校正", "Image2.1修复")
MAX_SHOTS = 100
MAX_REFERENCES = 101  # 100 clips may use 101 shared boundary images.
_BUNDLE_CACHE = {}
_RUN_LOCK = threading.Lock()


def clear_bundle_cache():
    _BUNDLE_CACHE.clear()
    gc.collect()


def parse_durations(value, count):
    count = int(count)
    if not 1 <= count <= MAX_SHOTS:
        raise ValueError("分镜数量应为1～100。")
    try:
        values = json.loads(str(value)) if str(value).lstrip().startswith("[") else re.split(r"[,，;；\s]+", str(value).strip())
        values = [float(x) for x in values if str(x).strip()]
    except (TypeError, ValueError) as exc:
        raise ValueError("每镜秒数请填写逗号分隔的数字，例如10,8,6。") from exc
    if len(values) == 1:
        values *= count
    if len(values) != count:
        raise ValueError(f"已设置{count}镜，请填写{count}个时长，或填写一个统一时长。")
    if any(not math.isfinite(x) or not 1 <= x <= 15 for x in values):
        raise ValueError("每个镜头应为1～15秒，长内容请增加分镜。")
    return values


def resolve_references(raw):
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("参考图库无效，请重新上传图片。") from exc
    if not isinstance(data, list) or not 1 <= len(data) <= MAX_REFERENCES:
        raise ValueError("请上传1～101张参考图；每镜最多一张首帧和一张尾帧。")
    root = Path(folder_paths.get_input_directory()).resolve()
    result, ids = [], set()
    for entry in data:
        if not isinstance(entry, dict):
            raise ValueError("参考图记录无效。")
        identity, name = str(entry.get("id", "")), str(entry.get("filename", ""))
        if not re.fullmatch(r"图[1-9]\d*", identity) or identity in ids:
            raise ValueError("参考图编号重复或无效，请重新选择。")
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"{identity}不存在，请重新上传该图片。")
        if path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".avif"):
            raise ValueError(f"{identity}不是支持的图片格式。")
        stat = path.stat()
        result.append({"id": identity, "filename": path.relative_to(root).as_posix(),
                       "path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
        ids.add(identity)
    return result


def resolve_manual_images(raw, count):
    """Resolve ordered image slots strictly inside the Comfy input directory."""
    try:
        data = json.loads(raw or "[]")
    except (TypeError, ValueError) as exc:
        raise ValueError("手动分镜图片无效，请重新选择图片。") from exc
    if not isinstance(data, list) or len(data) > count + 1:
        raise ValueError("手动图片数量不能超过分镜数量及一张可选结束帧。")
    root = Path(folder_paths.get_input_directory()).resolve()
    result = []
    for index, entry in enumerate(data):
        if entry is None or entry == "" or entry == {}:
            result.append(None)
            continue
        if not isinstance(entry, dict) or entry.get("type", "input") != "input":
            raise ValueError(f"分镜{index + 1}须使用已上传的本地图片。")
        name, subfolder = entry.get("filename"), entry.get("subfolder", "")
        if not isinstance(name, str) or not name or not isinstance(subfolder, str):
            raise ValueError(f"分镜{index + 1}的图片记录无效。")
        path = (root / subfolder / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"分镜{index + 1}图片不存在，请重新上传。")
        if path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".avif"):
            raise ValueError(f"分镜{index + 1}不是支持的图片格式。")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        result.append({"filename": path.relative_to(root).as_posix(), "path": str(path),
                       "sha256": digest.hexdigest()})
    result.extend([None] * (count + 1 - len(result)))
    return result


def _check_mentions(script, references):
    available = {x["id"] for x in references}
    missing = set(re.findall(r"@(图[1-9]\d*)", script)) - available
    if missing:
        raise ValueError("剧本引用了已删除的参考图：" + "、".join(sorted(missing)))


def _canvas_reference(references, aspect, identity="图1"):
    if aspect != "跟随图片" or len(references) == 1:
        return references[0]
    identity = str(identity or "图1").removeprefix("@")
    selected = next((item for item in references if item["id"] == identity), None)
    if selected is None:
        raise ValueError("尺寸基准图已不存在，请在“跟随图片”中重新选择参考图。")
    return selected


def _overrides(raw, count):
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError) as exc:
        raise ValueError("分镜编辑内容无效，请重新编辑该镜提示词。") from exc
    if not isinstance(data, dict):
        raise ValueError("分镜编辑内容应为对象。")
    result = {}
    for key, item in data.items():
        if not str(key).isdigit() or not 1 <= int(key) <= count or not isinstance(item, dict):
            raise ValueError("分镜编辑编号不在当前分镜范围内。")
        result[str(int(key))] = {field: str(item[field]).strip() for field in
                                 ("video_prompt", "image_prompt") if field in item and str(item[field]).strip()}
        if 'video_seed' in item:
            seed = item['video_seed']
            if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
                raise ValueError('单镜视频种子应为0～4294967295的整数。')
            result[str(int(key))]['video_seed'] = seed
    return result


def resolve_scene_video_seed(base_seed, index, scene=None, override=None):
    """Keep a selected shot reproducible without changing any image seed."""
    if type(base_seed) is not int or not 0 <= base_seed <= 2**32 - 1 or type(index) is not int or index < 0:
        raise ValueError('视频种子或镜头编号无效。')
    if isinstance(override, dict) and 'video_seed' in override:
        seed = override['video_seed']
        if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
            raise ValueError('单镜视频种子应为0～4294967295的整数。')
        return seed
    scene = scene or {}
    saved = scene.get('video_seed')
    if scene.get('video_seed_base', base_seed) == base_seed and type(saved) is int and 0 <= saved <= 2**32 - 1:
        return saved
    return (base_seed + index) % 2**32


def _prompt_body(prompt):
    return re.sub(r"^(?:How the reference pictures align|For the target video)[^\n]*\n*", "", str(prompt)).strip()


def _aligned_override(prompt, compiled):
    prefix = compiled.partition("\n\n")[0]
    return prefix + "\n\n" + _prompt_body(prompt)


def _load_image(path):
    """Read owned input/output/temp images without the input-only loader."""
    import numpy as np
    from PIL import Image, ImageOps

    image_path = Path(path).resolve()
    roots = [Path(directory).resolve() for directory in (
        folder_paths.get_input_directory(), folder_paths.get_output_directory(),
        folder_paths.get_temp_directory())]
    if not any(image_path.is_relative_to(root) for root in roots) or not image_path.is_file():
        raise ValueError("图片不存在或不在ComfyUI的输入、输出及临时目录内。")
    with Image.open(image_path) as source:
        source.seek(0)
        image = ImageOps.exif_transpose(source).convert("RGB")
        pixels = np.array(image, dtype=np.float32) / 255.0
    return torch.from_numpy(pixels).unsqueeze(0).contiguous()


def _save_image(image, destination):
    import numpy as np
    from PIL import Image
    from .output_metadata import save_png
    array = (image[0].detach().cpu().clamp(0, 1).numpy() * 255.0).round().astype(np.uint8)
    save_png(Image.fromarray(array[..., :3], "RGB"), destination)


def _collect_generated_image(source, destination):
    """Keep an original image beside this run's clips without deleting its source."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    output_root = Path(folder_paths.get_output_directory()).resolve()
    if not source.is_relative_to(output_root) and not source.is_relative_to(Path(folder_paths.get_temp_directory()).resolve()):
        raise ValueError("生成图片不在ComfyUI的输出或临时目录内。")
    if not destination.is_relative_to(output_root) or not source.is_file():
        raise ValueError("生成图片不存在或保存目录无效。")
    if source != destination:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".partial.png")
        try:
            from .output_metadata import current_metadata, save_png
            if current_metadata():
                from PIL import Image
                with Image.open(source) as image:
                    save_png(image, temporary)
            else:
                shutil.copyfile(source, temporary)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    return str(destination)


def correct_tail_exposure(image, anchor):
    """A small exposure adjustment, without sharpening or invented detail."""
    coefficients = image.new_tensor([0.2126, 0.7152, 0.0722])
    current = (image[..., :3] * coefficients).sum(-1)
    target = (anchor[..., :3] * coefficients).sum(-1)
    active = current[(current > 0.04) & (current < 0.95)]
    reference = target[(target > 0.04) & (target < 0.95)]
    if active.numel() < 32 or reference.numel() < 32:
        return image
    ratio = (reference.mean() / active.mean().clamp_min(0.03)).clamp(0.90, 1.15)
    return (image * ratio).clamp(0, 1).contiguous()


def _preview(path):
    path = Path(path).resolve()
    for kind, root in (("output", folder_paths.get_output_directory()),
                       ("input", folder_paths.get_input_directory()),
                       ("temp", folder_paths.get_temp_directory())):
        root = Path(root).resolve()
        if path.is_relative_to(root):
            relative = path.relative_to(root)
            return {"filename": relative.name, "subfolder": relative.parent.as_posix() if relative.parent != Path(".") else "", "type": kind}
    raise ValueError("预览文件不在ComfyUI的数据目录内。")


def _output_root():
    root = Path(folder_paths.get_output_directory()) / "H3分镜" / "工作台"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _fingerprint(kwargs, refs, durations):
    excluded = {"继续任务", "重做镜头", "执行范围", "分镜覆盖", "unique_id", "prompt", "extra_pnginfo",
                "保留分镜计划", "计划编辑", "生图重做范围", "重生图片", "手动分镜图片", "_dialogue_track", "导入优化计划", "视频种子"}
    values = {key: value for key, value in kwargs.items() if key not in excluded}
    if values.get('视频输入', '图生视频') == '图生视频':
        values.pop('视频输入', None)
    elif values.get('视频输入') == '文生视频':
        for name in ('_image_settings', '画幅参考图', '适配方式', '手动分镜图片'):
            values.pop(name, None)
    values.pop('生图后生成视频',None)
    if values.get('视频短边上限',0)==0:values.pop('视频短边上限',None)
    # Existing V1.1.1 manifests have no format control. The default is identical.
    if values.get("提示词格式", "工作台格式") == "工作台格式":
        values.pop("提示词格式", None)
    if isinstance(values.get("_image_settings"), dict):
        settings = dict(values["_image_settings"])
        for key, default in (("storyboard_long_edge", 0), ("reference_long_edge", 0),
                             ("previous_reference_policy", "完整参考（兼容）")):
            if settings.get(key) == default:
                settings.pop(key, None)
        values["_image_settings"] = settings
    values.pop("衔接超分配置", None)
    if not values.get("_reference_upscale", {}).get("enabled") or values.get("生成模式") != "AI生成分镜":
        values.pop("_reference_upscale", None)
    if kwargs.get("时长模式", "手动设置") == "AI自动分配":
        values.pop("每镜秒数", None)
        values.update(时长模式="AI自动分配", 目标总秒数=float(kwargs.get("目标总秒数", 30.0)))
        values.update(references=refs, duration_count=len(durations))
    else:
        # Default controls do not invalidate manifests created before this mode.
        values.pop("时长模式", None)
        values.pop("目标总秒数", None)
        values.update(references=refs, durations=durations)
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _reference_stage_identities(references):
    result = []
    for reference in references:
        digest = hashlib.sha256()
        with Path(reference["path"]).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024*1024), b""):
                digest.update(chunk)
        result.append({"id": reference["id"], "filename": reference.get("filename"), "sha256": digest.hexdigest()})
    return result


def _legacy_stage_identity(manifest, kwargs, references, durations, image_settings, width, height):
    """Migrate only by reproducing the original whole-task fingerprint."""
    from .stage_cache import stage_fingerprints
    old_video = manifest.get("effective_settings", {}).get("video")
    if not isinstance(old_video, dict) or not manifest.get("plan"):
        return None
    # Old manifests did not hash source bytes; a file replaced since task
    # creation cannot be certified using only an unchanged filename.
    created_at = float(manifest.get("created_at", 0))
    if any(Path(reference["path"]).stat().st_mtime > created_at+1 for reference in references):
        return None
    candidate = dict(kwargs)
    # Reproduce the old shared-seed task first; a newly selected video-only
    # seed is compared by the video stage after this compatibility migration.
    candidate.pop("视频种子", None)
    if manifest.get("dialogue_track") is not None:
        candidate["_dialogue_track"] = copy.deepcopy(manifest["dialogue_track"])
    else:
        candidate.pop("_dialogue_track", None)
    for key, value in old_video.items():
        if value is not None:
            candidate[key] = value
        else:
            candidate.pop(key, None)
    # 完成后 only chooses resource cleanup. Older versions hashed it despite
    # it having no effect on either the source plan or generated content.
    completions = list(dict.fromkeys([candidate.get("完成后"), "保留模型缓存", "释放模型与缓存"]))
    for completion in completions:
        trial = dict(candidate)
        if completion is None:
            trial.pop("完成后", None)
        else:
            trial["完成后"] = completion
        if _fingerprint(trial, references, durations) == manifest.get("fingerprint"):
            return stage_fingerprints(trial, _reference_stage_identities(references), durations,
                                      image_settings=image_settings, width=width, height=height)
    return None


def _manifest_path(identity):
    if not re.fullmatch(r"[0-9a-f]{32}", identity):
        raise ValueError("继续任务的编号无效。")
    return _output_root() / identity / "分镜任务.json"


def _store(manifest):
    destination = _manifest_path(manifest["run_id"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, destination)


def _timing_record(previous=None, status="pending"):
    previous = previous if isinstance(previous, dict) else {}
    elapsed = previous.get("elapsed_seconds", 0.0)
    if not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed < 0:
        elapsed = 0.0
    return {"status": status, "started_at": previous.get("started_at"),
            "elapsed_seconds": float(elapsed), "ended_at": previous.get("ended_at")}


class _TimingTracker:
    """One execution owns monotonic starts; persisted records hold no live clock."""
    def __init__(self, manifest):
        self.active = {}
        self.stages = manifest.setdefault("timings", {})
        records = []
        for name in ("planning", "images", "video", "joining", "total"):
            previous = self.stages.get(name)
            status = previous.get("status", "pending") if isinstance(previous, dict) else "pending"
            self.stages[name] = _timing_record(previous, status)
            records.append(self.stages[name])
        for scene in manifest.get("scenes", []):
            previous = scene.get("video_timing")
            status = previous.get("status", "pending") if isinstance(previous, dict) else "pending"
            scene["video_timing"] = _timing_record(previous, status)
            records.append(scene["video_timing"])
        records.extend(job.get("timing") for job in manifest.get("image_jobs", []))
        # An interrupted process cannot supply a reliable monotonic delta.
        # Keep its last committed baseline, never count offline wall-clock time.
        for record in records:
            if isinstance(record, dict) and record.get("status") == "running":
                record.update(status="pending", started_at=None, ended_at=None)

    def start(self, record):
        key = id(record)
        if key in self.active:
            return
        baseline = float(record.get("elapsed_seconds", 0.0))
        record.update(status="running", started_at=time.time(), ended_at=None,
                      elapsed_seconds=baseline)
        self.active[key] = (record, time.monotonic(), baseline)

    def finish(self, record, status="completed"):
        running = self.active.pop(id(record), None)
        if running is None:
            return
        _, started, baseline = running
        record.update(status=status, elapsed_seconds=baseline + max(0.0, time.monotonic() - started),
                      ended_at=time.time())

    def start_stage(self, name):
        self.start(self.stages[name])

    def finish_stage(self, name, status="completed"):
        self.finish(self.stages[name], status)

    def start_image(self, manifest, job_id):
        job = next((item for item in manifest.get("image_jobs", []) if item["id"] == job_id), None)
        if job is not None:
            self.start(job.setdefault("timing", _timing_record()))

    def finish_image(self, manifest, job_id):
        job = next((item for item in manifest.get("image_jobs", []) if item["id"] == job_id), None)
        if job is not None and "timing" in job:
            self.finish(job["timing"])

    def finish_all(self, status):
        for record, _, _ in list(self.active.values()):
            self.finish(record, status)


def _image_summary(manifest):
    jobs = manifest.get("image_jobs", [])
    completed = sum(job.get("status") == "完成" for job in jobs)
    summary = {"completed": completed, "total": len(jobs),
               "percent": round(100 * completed / len(jobs), 1) if jobs else 0}
    manifest["image_summary"] = summary
    return summary


def _image_job_progress(manifest, node_id, job_id):
    """Stream actual sampler steps without writing the full plan each step."""
    from server import PromptServer
    phases = {"loading": "加载模型", "encoding": "编码参考", "sampling": "采样中",
              "decoding": "解码中", "completed": "解码中", "upscale_dlss": "DLSS5增强参考",
              "upscale_vosr": "VOSR2超分参考", "upscale_cached": "复用超分参考", "upscale_completed": "衔接超分完成"}

    def receive(data):
        job = next((item for item in manifest.get("image_jobs", []) if item["id"] == job_id), None)
        if job is None:
            return
        if isinstance(data.get("reference_receipt"), dict):
            job["reference_receipt"] = copy.deepcopy(data["reference_receipt"])
            # Persist the actual encoder sizes/roles once, independent of UI.
            _store(manifest)
        if isinstance(data.get("receipt"), dict) and data.get("phase") in ("upscale_cached", "upscale_completed"):
            job["upscale_receipt"] = copy.deepcopy(data["receipt"])
            manifest["reference_upscale_receipt"] = copy.deepcopy(data["receipt"])
            _store(manifest)
        job["status"] = phases.get(data.get("phase"), job["status"])
        value, total = data.get("value"), data.get("total")
        if isinstance(value, (int, float)) and isinstance(total, (int, float)) and total > 0:
            job.update(value=value, total=total, percent=round(max(0, min(100, 100 * value / total)), 1))
        manifest['progress_seq']=manifest.get('progress_seq',0)+1
        event = {"node_id": str(node_id), "run_id": manifest["run_id"], "stage": "image_progress",
                 'execution_id':manifest.get('execution_id'),'prompt_id':manifest.get('prompt_id'),'progress_seq':manifest['progress_seq'],
                 "job": dict(job), "image_summary": _image_summary(manifest)}
        from .progress_state import remember
        remember(event,partial=True)
        if PromptServer.instance is not None:
            PromptServer.instance.send_sync("h3-director-image-progress", event)
    return receive


def _image_job_complete(manifest, job_id, image, inserted=True):
    job = next((item for item in manifest.get("image_jobs", []) if item["id"] == job_id), None)
    if job is not None:
        job.update(status="完成", percent=100.0, value=job.get("total", 0), image=image, inserted=inserted)
        job.pop("error", None)
    _image_summary(manifest)


def _snapshot_event(manifest,node_id):
    event = {key: manifest.get(key) for key in
             ("run_id", "status", "stage", "scene_index", "scene_count", "mode", "final_video", "error", "failure_context", "optimized_script", "effective_settings", "image_jobs", "image_summary", "timings", "output_directory", "plan_available", "plan_reused", "revision", "regeneration_applied", "duration_mode", "timing_mode", "scene_seconds", "total_seconds", "allocation_receipt", "manual_missing", "image_edit_receipts")}
    event.update(node_id=str(node_id), scenes=[{key: scene.get(key) for key in
                 ("index", "status", "image", "end_image", "video", "prompt", "display_prompt", "image_prompt", "seconds", "error", "video_timing", "video_seed", "plan_fields", "reference_labels", "spoken_dialogues")}
                 for scene in manifest.get("scenes", [])])
    event['planning_progress'] = manifest.get('planning_progress')
    event.update(execution_id=manifest.get('execution_id'),prompt_id=manifest.get('prompt_id'),progress_seq=manifest.get('progress_seq',0),updated_at=manifest.get('updated_at'))
    event['video_input'] = manifest.get('video_input', '文生视频' if manifest.get('mode') == '文生视频' else '图生视频')
    event['input_mode'] = 'T2VA' if event['video_input'] == '文生视频' else 'I2V'
    event["canvas"] = {"width": manifest.get("width"), "height": manifest.get("height"),
                       "reference_id": manifest.get("canvas_reference_id")}
    for item,scene in zip(event['scenes'],manifest.get('scenes',[])):
        original=scene.get('generated_image_path')
        if original and Path(original).is_file():item['original_image']=_preview(original)
    for job in event.get('image_jobs') or []:
        index=job.get('index',0)-1
        original=(manifest.get('scenes',[])[index].get('generated_image_path') if 0<=index<len(manifest.get('scenes',[])) else manifest.get('final_generated_image_path'))
        if original and Path(original).is_file():job['original_image']=_preview(original)
    bindings = list(manifest.get("source_images", []))
    if manifest.get("mode") in ("AI生成分镜", "上一镜末帧", "手动分镜图", "末帧高清续接"):
        prefix = "首帧" if manifest["mode"] in TAIL_SEQUENCE_MODES else "分镜"
        bindings.extend({"id": f"{prefix}{scene['index']}", "image": scene.get("image"),
                         "status": "就绪" if scene.get("image") else "待生成"}
                        for scene in manifest.get("scenes", []))
    if manifest.get("mode") in ("AI生成分镜", "手动分镜图"):
        ending = (manifest.get("scenes") or [{}])[-1].get("end_image")
        bindings.append({"id": "结束帧", "image": ending, "status": "就绪" if ending else "待生成"})
    event["reference_bindings"] = bindings
    event["video_settings_changed"] = bool(manifest.get("video_settings_changed"))
    event["video_settings_recompiled"] = bool(manifest.get("video_settings_recompiled"))
    event["video_reused"] = manifest.get("video_reused")
    event["prompt_format"] = manifest.get("prompt_format", "工作台格式")
    event["reference_upscale"] = manifest.get("reference_upscale")
    event["reference_upscale_receipt"] = manifest.get("reference_upscale_receipt")
    from .plan_import import export_plan
    event["plan_export"] = export_plan(manifest.get("plan"), manifest.get("scene_seconds", []),
                                      mode=manifest.get("mode"), references=manifest.get("source_images", []),
                                      optimized_script=manifest.get("optimized_script", ""))
    event["plan_imported"] = bool(manifest.get("plan_imported"))
    return event


def _emit(manifest,node_id,stage,scene_index=0):
    from server import PromptServer
    from .progress_state import remember
    manifest.update(stage=stage,scene_index=scene_index,updated_at=time.time(),node_id=str(node_id))
    manifest['progress_seq']=manifest.get('progress_seq',0)+1
    _store(manifest)
    event=_snapshot_event(manifest,node_id)
    remember(event)
    if PromptServer.instance is not None:
        PromptServer.instance.send_sync("h3-director-progress", event)
    return event


def _result_ui(manifest, event):
    """Expose saved assets to Comfy's native index alongside the custom view."""
    directory = _manifest_path(manifest["run_id"]).parent.resolve()
    images, videos, seen = [], [], set()
    if directory.is_dir():
        for path in sorted(directory.iterdir()):
            resolved = path.resolve()
            if (not resolved.is_relative_to(directory) or not path.is_file() or
                    ".partial." in path.name or str(resolved) in seen):
                continue
            kind = resolved.suffix.lower()
            if kind not in (".png", ".jpg", ".jpeg", ".webp", ".mp4"):
                continue
            seen.add(str(resolved))
            (videos if kind == ".mp4" else images).append(_preview(resolved))
    return {"h3_storyboard": [event], "images": images, "videos": videos}


def _execution_result(manifest, event):
    """Append an export source without changing the existing task-record output."""
    references = []
    for source in manifest.get("source_images", []):
        image = source.get("image") or {}
        if image.get("type") == "input":
            references.append({"id": source["id"], **copy.deepcopy(image)})
    exported = {"run_id": manifest["run_id"], "node_id": event.get("node_id"),
                "mode": manifest.get("mode"), "width": manifest.get("width"),
                "height": manifest.get("height"), "references": references,
                "image_jobs": copy.deepcopy(manifest.get("image_jobs", [])),
                "image_settings": copy.deepcopy(manifest.get("effective_settings", {}).get("image", {}))}
    return {"ui": _result_ui(manifest, event),
            "result": (str(_manifest_path(manifest["run_id"])), exported)}


def _regeneration_ids(raw, policy, count, constrained, mode):
    policies = ("保留现有图片", "选择重生", "全部重生")
    if policy not in policies:
        raise ValueError("请选择有效的图片重生范围。")
    available = {f"分镜{i + 1}" for i in range(count)}
    if constrained:
        available.add("结束帧")
    if policy == "保留现有图片":
        return set()
    if mode != "AI生成分镜":
        raise ValueError("选择或全部重生图片只适用于AI生成分镜模式。")
    if policy == "全部重生":
        return available
    try:
        identities = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError) as exc:
        raise ValueError("重生图片列表无效，请重新选择。") from exc
    if not isinstance(identities, list) or any(not isinstance(item, str) or item not in available for item in identities):
        raise ValueError("重生图片编号不在当前分镜范围内。")
    return set(identities)


def _invalidate_video(scene):
    for key in ("video", "video_path", "tail_path", "repaired_image_path", "error"):
        scene.pop(key, None)
    scene["status"] = "等待"


def _revision_path(directory, name, manifest):
    revision = int(manifest.get("revision", 0))
    path = Path(name)
    return directory / (f"{path.stem}_r{revision:03d}{path.suffix}" if revision else name)


def _sync_manual_images(manifest, slots, directory, fit_mode, constrained, revision_advanced=False):
    """Apply the current slots as authoritative; never reuse an explicitly cleared slot."""
    scenes = manifest["scenes"]
    changes = []
    for index, scene in enumerate(scenes):
        source = slots[index]
        previous = scene.get("manual_source")
        current = {key: source[key] for key in ("filename", "sha256")} if source else None
        missing_output = source and not Path(scene.get("image_path", "")).is_file()
        if current != previous or missing_output or not source and (scene.get("image_path") or scene.get("image")):
            changes.append(index)
    final = slots[len(scenes)] if constrained else None
    final_source = {key: final[key] for key in ("filename", "sha256")} if final else None
    final_changed = (final_source != manifest.get("manual_final_source") or
                     bool(final and not Path(manifest.get("final_image_path", "")).is_file()) or
                     bool(not final and manifest.get("final_image_path")))
    if (changes or final_changed) and manifest.get("plan_reused") and not revision_advanced:
        manifest["revision"] = int(manifest.get("revision", 0)) + 1
    for index in changes:
        scene, source = scenes[index], slots[index]
        _invalidate_video(scene)
        for key in ("image_path", "image", "generated_image_path", "image_dirty", "manual_source"):
            scene.pop(key, None)
        if source:
            destination = _revision_path(directory, f"首帧_{index + 1:03d}.png", manifest)
            image = fit_reference(_load_image(source["path"]), manifest["width"], manifest["height"], fit_mode)
            _save_image(image, destination)
            scene.update(image_path=str(destination), image=_preview(destination), status="图片就绪",
                         manual_source={key: source[key] for key in ("filename", "sha256")})
            del image
        if constrained and index:
            _invalidate_video(scenes[index - 1])
    if final_changed:
        for key in ("final_image_path", "final_generated_image_path", "final_image_dirty", "manual_final_source"):
            manifest.pop(key, None)
        if final:
            destination = _revision_path(directory, "结束帧.png", manifest)
            image = fit_reference(_load_image(final["path"]), manifest["width"], manifest["height"], fit_mode)
            _save_image(image, destination)
            manifest.update(final_image_path=str(destination), manual_final_source=final_source)
            del image
        _invalidate_video(scenes[-1])
    if changes or final_changed:
        manifest.pop("final_video", None)
        manifest.pop("final_path", None)
    for index, scene in enumerate(scenes):
        if slots[index] and Path(scene.get("image_path", "")).is_file():
            if scene.get("status") != "完成":
                scene["status"] = "图片就绪"
        elif not slots[index]:
            scene["status"] = "等待手动图片"
        endpoint = (scenes[index + 1].get("image_path") if index + 1 < len(scenes)
                    else manifest.get("final_image_path")) if constrained else None
        scene["end_image_path"] = endpoint
        scene["end_image"] = _preview(endpoint) if endpoint and Path(endpoint).is_file() else None
    manifest["manual_missing"] = [index + 1 for index, source in enumerate(slots[:len(scenes)]) if not source]
    return changes


def _sync_ai_image_edits(manifest, raw, directory, fit_mode, constrained):
    """Apply explicitly submitted replacement/clear commands once, preserving the plan."""
    try:
        edits = json.loads(raw or '[]')
    except (TypeError, ValueError) as error:
        raise ValueError('分镜图片替换记录无效，请重新选择图片。') from error
    scenes = manifest['scenes']
    if not isinstance(edits, list) or len(edits) > len(scenes) + 1:
        raise ValueError('分镜图片替换数量超出当前分镜范围。')
    receipts = manifest.setdefault('image_edit_receipts', {})
    changes = []
    for index, entry in enumerate(edits):
        if not entry:
            continue
        if not isinstance(entry, dict):
            raise ValueError('分镜图片替换记录无效。')
        # A saved edit belongs to its original run and must never affect a new task.
        if entry.get('_run_id') != manifest['run_id']:
            continue
        token = entry.get('_edit_id')
        if not isinstance(token, str) or not re.fullmatch(r'[a-zA-Z0-9-]{8,80}', token):
            raise ValueError('分镜图片替换标识无效，请重新选择。')
        if receipts.get(str(index)) == token:
            continue
        if index == len(scenes) and not constrained:
            raise ValueError('当前镜头衔接方式不使用独立结束帧。')
        clear = entry.get('clear') is True
        source = None if clear else resolve_manual_images(json.dumps([entry]), 0)[0]
        changes.append((index, source, token))
    if not changes:
        return False
    manifest['revision'] = int(manifest.get('revision', 0)) + 1
    for index, source, token in changes:
        if index < len(scenes):
            scene = scenes[index]
            _invalidate_video(scene)
            for key in ('image_path', 'image', 'generated_image_path', 'original_image', 'image_dirty', 'manual_source'):
                scene.pop(key, None)
            if source:
                destination = _revision_path(directory, f'替换首帧_{index + 1:03d}.png', manifest)
                image = fit_reference(_load_image(source['path']), manifest['width'], manifest['height'], fit_mode)
                _save_image(image, destination)
                original = _revision_path(directory, f'替换原图_{index + 1:03d}.png', manifest)
                _save_image(_load_image(source['path']), original)
                scene.update(image_path=str(destination), image=_preview(destination), status='图片就绪',
                             generated_image_path=str(original), original_image=_preview(original),
                             manual_source={key: source[key] for key in ('filename', 'sha256')})
            if constrained and index:
                _invalidate_video(scenes[index - 1])
        else:
            for key in ('final_image_path', 'final_generated_image_path', 'final_image_dirty', 'manual_final_source'):
                manifest.pop(key, None)
            if source:
                destination = _revision_path(directory, '替换结束帧.png', manifest)
                image = fit_reference(_load_image(source['path']), manifest['width'], manifest['height'], fit_mode)
                _save_image(image, destination)
                manifest['final_image_path'] = str(destination)
                manifest['final_generated_image_path'] = str(destination)
            else:
                # The old on-disk endpoint is preserved for recovery, but must not
                # be silently reused through the legacy default filename.
                manifest['final_image_dirty'] = True
            _invalidate_video(scenes[-1])
        receipts[str(index)] = token
    for index, scene in enumerate(scenes):
        endpoint = (scenes[index + 1].get('image_path') if index + 1 < len(scenes)
                    else manifest.get('final_image_path')) if constrained else None
        scene['end_image_path'] = endpoint
        scene['end_image'] = _preview(endpoint) if endpoint and Path(endpoint).is_file() else None
    manifest.pop('final_video', None)
    manifest.pop('final_path', None)
    return True


def _tail_repair_prompt(scene_plan):
    return ("输入图像1是刚完成的视频末帧，也是唯一的当前动作状态和构图基准。"
            "轻度修复模糊、压缩噪声和异常发黑，保持原有姿态、动作进度、肢体关系、服装穿戴、物件位置、持有状态、机位与构图。"
            "不得预演下一镜的新动作，不增加、删除、移动人物或道具。"
            "输入图像2是原始高清素材，仅补充同一主体的身份、真实材质与清晰细节；"
            "不得从图像2恢复已改变的穿搭、姿态、物件位置或已结束的动作，不替换当前环境、光线和色彩。"
            "当前衔接状态：" + str(scene_plan.get("start_state", "延续上一镜视频结束状态")))


def _tail_repair_context(tail_path, original_path, previous_index):
    return {"paths": [str(tail_path), str(original_path)], "roles": [
        {"slot": 1, "role": "primary", "source_id": f"分镜{previous_index}",
         "description": "刚完成视频的真实末帧，当前姿态、动作、穿搭、物件与构图的唯一基准"},
        {"slot": 2, "role": "additional", "source_id": "图1",
         "description": "原始高清素材，仅补身份和材质清晰细节，禁止恢复旧穿搭、姿态或物件状态"}],
        "reference_labels": [f"镜头{previous_index}实际末帧", "原始高清图1（仅身份与细节）"]}


def _tail_repair_receipt(tail_path, original_path, prompt, settings):
    def digest(path):
        value = hashlib.sha256()
        with Path(path).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                value.update(chunk)
        return value.hexdigest()
    return hashlib.sha256(json.dumps({"tail": digest(tail_path), "original": digest(original_path),
                                     "prompt": prompt, "settings": settings}, ensure_ascii=False,
                                    sort_keys=True).encode()).hexdigest()


def _bundle(plan, kwargs):
    input_mode = plan.get('input_mode', 'I2V')
    key = (input_mode,) + tuple(kwargs.get(name) for name in ("基础模型", "原作者模型", "加速权重", "文本编码器", "视频VAE", "音频VAE", "启用加速LoRA", "加速步数", "低显存", "稀疏注意力", "4步LoRA", "8步LoRA"))
    if input_mode == 'T2VA' and kwargs.get('启用加速LoRA') and kwargs.get('加速权重') != '独立LoRA':
        raise ValueError('文生视频请使用基础模型或独立加速LoRA；当前融合模型未确认支持无图输入。')
    if _BUNDLE_CACHE.get("key") != key:
        clear_bundle_cache()
        if kwargs.get("加速权重", "独立LoRA") == "原作者融合模型" and kwargs["启用加速LoRA"]:
            from comfy_extras.nodes_minimax_h3 import MiniMaxH3SigmaShift
            model, clip, vae, audio_vae, _ = H3ReadyModelGate().load(**{
                "分镜计划": json.dumps(plan, ensure_ascii=False), "融合模型": kwargs["原作者模型"],
                "文本编码器": kwargs["文本编码器"], "视频VAE": kwargs["视频VAE"], "音频VAE": kwargs["音频VAE"],
            })
            if kwargs["低显存"]:
                model = _execute_v3("MiniMaxChunkFeedForward", model=model, chunks=4, seq_threshold=4096)[0]
            if kwargs.get("稀疏注意力", True):
                model = _execute_v3("H3SLAAttention", model=model, sparsity_ratio=0.90,
                                    block_size="64", min_seq_len=8192,
                                    dense_last_steps=0, protect_audio=True, enabled=True)[0]
            model = _outputs(MiniMaxH3SigmaShift.execute(model, 12.0, 3.0))[0]
            result = {"model": model, "clip": clip, "vae": vae, "audio_vae": audio_vae,
                      "turbo_enabled": True, "turbo_steps": int(kwargs["加速步数"]),
                      "turbo_lora": "mystic07 · RefDelta r1024 Turbo8", "cfg": 1.0,
                      "sparse_attention": kwargs.get("稀疏注意力", True), "model_name": kwargs["原作者模型"]}
        else:
            result = H3VideoModelBundle().load(
                ready_plan=json.dumps(plan, ensure_ascii=False), unet_name=kwargs["基础模型"],
                clip_name=kwargs["文本编码器"], video_vae_name=kwargs["视频VAE"], audio_vae_name=kwargs["音频VAE"],
                turbo_enabled=kwargs["启用加速LoRA"], accelerated_steps=int(kwargs["加速步数"]),
                low_vram=kwargs["低显存"], sparse_attention=kwargs.get("稀疏注意力", True),
                turbo_lora_4step=kwargs.get("4步LoRA", TURBO_LORA_4STEP),
                turbo_lora=kwargs.get("8步LoRA", TURBO_LORA),
                input_mode=input_mode,
            )[0]
        _BUNDLE_CACHE.update(key=key, bundle=result)
    return {**_BUNDLE_CACHE["bundle"], "ready_plan": json.dumps(plan, ensure_ascii=False)}


def _optimized_script(plan, scenes):
    from .prompt_formats import normalize_prompt_format, render_six_section_scene
    dialogue = {line["id"]: line.get("text", "") for line in plan.get("dialogue_whitelist", [])}
    if normalize_prompt_format(plan.get("prompt_format")) == "H3六段式":
        return "\n\n".join(render_six_section_scene(scene, scenes[index], index, dialogue)
                             for index, scene in enumerate(plan.get("scenes", [])))
    output = []
    for index, scene in enumerate(plan.get("scenes", [])):
        current = scenes[index]
        binding = str(current.get("display_prompt", "")).split("\n", 1)[0]
        lines = [f"镜头{index + 1} · {current['seconds']:g}秒"]
        if binding.startswith("@"):
            lines.append(binding)
        for label, key in (("开场", "start_state"), ("画面", "visual"), ("动作", "action"), ("结束", "end_state")):
            if scene.get(key):
                lines.append(f"{label}：{scene[key]}")
        words = [dialogue.get(item.get("id"), "") for item in scene.get("dialogues", [])]
        if any(words):
            lines.append("对白：" + "；".join(word for word in words if word))
        output.append("\n".join(lines))
    from .prompt_formats import video_prompt_for_format
    return video_prompt_for_format("\n\n".join(output),plan.get('prompt_format'))


def _spoken_dialogues(plan, scene, prompt):
    """Expose the same <d> spans submitted to H3, without planner instructions."""
    if plan.get("audio_mode") == "静音":
        return []
    registry = {line["id"]: line for line in plan.get("dialogue_whitelist", [])}
    assigned = []
    for entry in scene.get("dialogues", []):
        source = registry.get(entry.get("id"))
        if source:
            assigned.append((entry, source))
    result = []
    for match in re.finditer(r"<d>\s*(?:\[[^\]\n]+\]\s*)?(.*?)\s*</d>", str(prompt), re.S):
        text = match.group(1).strip()
        if not text:
            continue
        candidate = next(((entry, source) for entry, source in assigned if source.get("text") == text), None)
        if candidate:
            entry, source = candidate
            result.append({"id": source["id"], "text": text,
                           "speaker": entry.get("speaker_id", source.get("speaker", "S1")),
                           "speaker_description": entry.get("speaker_description", ""),
                           "start": entry.get("start"), "end": entry.get("end"),
                           "offscreen": bool(source.get("offscreen"))})
        else:
            # An explicitly edited video prompt can contain a new spoken span.
            preceding = str(prompt)[max(0, match.start() - 300):match.start()]
            speakers = re.findall(r"[（(](S[1-9]\d*)[）)]", preceding)
            result.append({"text": text, "speaker": speakers[-1] if speakers else "未指定角色"})
    return result


def _reference_ids(scene, references, mode, index):
    declared = scene.get("reference_ids") or []
    if not isinstance(declared, list):
        raise ValueError("分镜参考图编号格式无效，请重新规划。")
    identities = [str(x).removeprefix("@") for x in declared]
    known = {x["id"]: x for x in references}
    if any(x not in known for x in identities):
        raise ValueError(f"镜头{index + 1}引用了不存在的参考图。")
    # In manual mode each uploaded frame maps to its shot by default. Explicit
    # @ references override that mapping; automatic modes anchor to the original.
    if not identities:
        if mode == "逐镜参考图":
            if index >= len(references):
                raise ValueError(f"逐镜参考图模式的镜头{index + 1}缺少首帧，请补图或在剧本中用@指定现有图片。")
            identities = [references[index]["id"]]
        else:
            identities = [references[0]["id"]]
    if mode == "AI生成分镜" and len(set(identities)) > 16:
        raise ValueError(f"镜头{index + 1}超过Image2.1的16张参考图上限。")
    source_roles = scene.get("reference_roles") or {}
    return [{**known[identity], **({"description": source_roles[identity]}
                                  if isinstance(source_roles, dict) and isinstance(source_roles.get(identity), str) else {})}
            for identity in dict.fromkeys(identities)]


def build_reference_context(current_sources, previous_path=None, previous_id="分镜1"):
    """Map source IDs to actual encoder slots, with one preceding still only."""
    if not isinstance(current_sources, list) or not current_sources:
        raise ValueError("本镜缺少有效的上传参考图。")
    paths, roles, labels = [], [], []

    def add(path, role, source_id, description=None):
        if not isinstance(path, str) or not path or not re.fullmatch(r"(?:图|分镜)[1-9]\d*|结束帧", str(source_id)):
            raise ValueError("参考图路径或编号无效。")
        path = str(Path(path).resolve())
        if path not in paths:
            paths.append(path)
        slot = paths.index(path) + 1
        entry = {"role": role, "source_id": source_id, "path": path, "slot": slot}
        if isinstance(description, str) and description.strip():
            entry["description"] = re.sub(r"\s+", " ", description.strip())[:160]
        if entry not in roles:
            roles.append(entry)
            labels.append(("上一" if role == "previous_storyboard" else "用户素材") + source_id + f"→输入图像{slot}")

    primary = current_sources[0]
    add(primary["path"], "primary", primary["id"], primary.get("description"))
    for source in current_sources[1:]:
        add(source["path"], "additional", source["id"], source.get("description"))
    if previous_path:
        add(str(previous_path), "previous_storyboard", previous_id)
    if len(paths) > 16:
        raise ValueError("Image2.1每镜最多16张输入图；接入上一分镜时最多15张不同上传图，请减少本镜引用。")
    return {"paths": paths, "roles": roles, "promptprefix": "输入图像对应关系：" + "；".join(labels) + "。",
            "reference_labels": labels}


def _record_image_references(manifest, job_id, context, scene=None):
    if scene is not None:
        scene.update(image_references=context["roles"], reference_labels=context["reference_labels"])
    else:
        manifest["final_image_references"] = context["roles"]
    job = next((item for item in manifest.get("image_jobs", []) if item["id"] == job_id), None)
    if job is not None:
        job["reference_labels"] = context["reference_labels"]
    _store(manifest)


def _prepare_upscaled_reference(path, settings, manifest, job_id, node_id, run_dir):
    if not path or not settings.get("enabled"):
        return path
    from .reference_upscale import upscale_reference
    # Qwen and H3 must be released before either of the restoration models.
    clear_bundle_cache()
    result = upscale_reference(path, settings, Path(run_dir) / "衔接参考超分",
                               progress_callback=_image_job_progress(manifest, node_id, job_id))
    manifest["reference_upscale_receipt"] = result["receipt"]
    _store(manifest)
    return result["path"]


class H3StoryboardStudio:
    @classmethod
    def INPUT_TYPES(cls):
        models = local_models()
        selected = PREFERRED_MODEL if PREFERRED_MODEL in models else models[0] if models else PREFERRED_MODEL
        return {"required": {
            "剧本": ("STRING", {"multiline": True, "default": DEMO_SCRIPT}),
            "参考图集": ("STRING", {"default": "[]"}),
            "生成模式": (list(MODES), {"default": "上一镜末帧"}),
            "镜头衔接": (["首尾帧约束", "仅首帧"], {"default": "首尾帧约束"}),
            "分镜数量": ("INT", {"default": 3, "min": 1, "max": MAX_SHOTS}),
            "每镜秒数": ("STRING", {"default": "10,10,10"}),
            "画幅": (list(ASPECTS), {"default": "跟随图片"}),
            "生成档位": (list(RESOLUTIONS), {"default": "480P"}),
            "自定义宽度": ("INT", {"default": 640, "min": 64, "max": 2048, "step": 32}),
            "自定义高度": ("INT", {"default": 384, "min": 64, "max": 2048, "step": 32}),
            "适配方式": (list(FIT_MODES), {"default": "等比补边"}),
            "末帧处理": (list(TAIL_MODES), {"default": "亮度轻校正"}),
            "语言模型": (models or [PREFERRED_MODEL], {"default": selected}),
            "声音模式": (list(VOICE_MODES), {"default": "自动识别原稿"}),
            "启用加速LoRA": ("BOOLEAN", {"default": True, "label_on": "启用加速", "label_off": "基础模型标准采样", "tooltip": "开启时使用所选融合权重或独立LoRA；关闭时切换未融合基础模型。"}),
            "加速权重": (["mystic07 · RefDelta r1024 Turbo8", "独立LoRA", "原作者融合模型"], {"default": "mystic07 · RefDelta r1024 Turbo8"}),
            "加速步数": ([4, 8], {"default": 4}),
            "标准步数": ("INT", {"default": 20, "min": 20, "max": 50}),
            "随机种子": ("INT", {"default": 42, "min": 0, "max": 2**32 - 1}),
            "低显存": ("BOOLEAN", {"default": True}),
            "稀疏注意力": ("BOOLEAN", {"default": True, "tooltip": "沿用原作者的SLA设置，仅加速模式生效。"}),
            "完成后": (["保留模型缓存", "释放模型与缓存"], {"default": "保留模型缓存"}),
            "执行范围": (["完整生成", "仅准备分镜图", "仅规划剧本"], {"default": "完整生成"}),
            "继续任务": ("STRING", {"default": ""}),
            "重做镜头": ("INT", {"default": 0, "min": 0, "max": MAX_SHOTS}),
            "分镜覆盖": ("STRING", {"default": "{}"}),
            "基础模型": _combo("diffusion_models", BASE_MODEL),
            "原作者模型": _combo("diffusion_models", AUTHOR_MODEL),
            "文本编码器": _combo("text_encoders", TEXT_ENCODER),
            "视频VAE": _combo("vae", VIDEO_VAE),
            "音频VAE": _combo("vae", AUDIO_VAE),
            "保留分镜计划": ("BOOLEAN", {"default": False}),
            "计划编辑": ("STRING", {"default": "{}", "multiline": True}),
            "生图重做范围": (["保留现有图片", "选择重生", "全部重生"], {"default": "保留现有图片"}),
            "重生图片": ("STRING", {"default": "[]"}),
            "时长模式": (["手动设置", "AI自动分配"], {"default": "手动设置"}),
            "目标总秒数": ("FLOAT", {"default": 30.0, "min": 1.0, "max": 1500.0, "step": 0.5}),
        }, "optional": {
            "文本模型配置": ("H3_TEXT_SETTINGS",),
            "图像模型配置": ("H3_IMAGE_SETTINGS", {'lazy': True}),
            "视频模型配置": ("H3_VIDEO_SETTINGS",),
            "提示词规则": ("H3_PROMPT_RULES",),
            "画幅参考图": ("STRING", {"default": "图1"}),
            "手动分镜图片": ("STRING", {"default": "[]"}),
            "导入优化计划": ("STRING", {"default": "{}"}),
            "提示词格式": (["工作台格式", "H3六段式"], {"default": "工作台格式"}),
            "分镜图长边": ("INT", {"default": 2048, "min": 0, "max": 4096, "step": 32}),
            "参考读取长边": ("INT", {"default": 0, "min": 0, "max": 4096, "step": 32}),
            "衔接超分配置": ("STRING", {"default": "{}"}),
            "视频短边上限": ("INT", {"default":0,"min":0,"max":1088,"step":32}),
            "生图后生成视频": ('BOOLEAN',{'default':False}),
            "视频种子": ("INT", {"default": -1, "min": -1, "max": 2**32 - 1,
                               "tooltip": "仅用于视频采样；-1沿用原随机种子，不改变规划或分镜图片。"}),
            "视频输入": (["图生视频", "文生视频"], {"default": "图生视频"}),
        }, "hidden": {"unique_id": "UNIQUE_ID", "prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"}}

    RETURN_TYPES = ("STRING", "H3_MANUAL_SOURCE")
    RETURN_NAMES = ("任务记录", "分镜生图")
    FUNCTION = "execute"
    CATEGORY = "H3/整稿与分镜"
    OUTPUT_NODE = True
    DESCRIPTION = "五种参考图模式、中文剧本与@图片、1～100镜逐镜生成；支持手动分镜与末帧高清续接。"

    @classmethod
    def VALIDATE_INPUTS(cls, 基础模型=None, 原作者模型=None, 文本编码器=None, 视频VAE=None, 音频VAE=None):
        # Connected video settings select and validate the weights at execution time.
        # Hidden fallback filenames must not require unused fusion weights to be installed.
        return True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # Runs must emit fresh progress and consult the disk-backed resume state.
        return float("nan")

    def check_lazy_status(self, 视频输入='图生视频', **kwargs):
        return ['图像模型配置'] if 视频输入 == '图生视频' and '图像模型配置' in kwargs and kwargs['图像模型配置'] is None else []

    def execute(self, **kwargs):
        if not _RUN_LOCK.acquire(blocking=False):
            raise RuntimeError("已有分镜任务正在执行，请等待或取消后再运行。")
        try:
            from .output_metadata import execution_metadata
            with execution_metadata(kwargs.get('prompt'), kwargs.get('extra_pnginfo')):
                return self._execute(kwargs)
        finally:
            _RUN_LOCK.release()

    def _execute(self, kwargs):
        import comfy.model_management as mm
        from comfy_api.latest import InputImpl, Types
        from comfy_extras.nodes_video import ConcatenateVideo
        from .planner import plan_sequence
        from .resource_control import release_resources
        from .image_bridge import normalize_settings
        from .prompt_presets import normalize_prompt_rules, apply_stage_rules

        kwargs = dict(kwargs)
        video_input = kwargs.get('视频输入', '图生视频')
        if video_input not in ('图生视频', '文生视频'):
            raise ValueError('请选择图生视频或文生视频。')
        text_only = video_input == '文生视频'
        input_mode = 'T2VA' if text_only else 'I2V'
        # Omitted/default mode leaves existing image-based task identities intact.
        if not text_only:
            kwargs.pop('视频输入', None)
        else:
            kwargs.update(生成模式='文生视频', 参考图集='[]', 镜头衔接='仅首帧', 末帧处理='直接续接',
                          手动分镜图片='[]', 生图重做范围='保留现有图片', 重生图片='[]')
        from .prompt_formats import normalize_prompt_format
        from .reference_upscale import normalize_settings as normalize_upscale
        prompt_format = normalize_prompt_format(kwargs.get("提示词格式"))
        kwargs["提示词格式"] = prompt_format
        reference_upscale = normalize_upscale('{}' if text_only else kwargs.get("衔接超分配置", "{}"))
        kwargs["_reference_upscale"] = reference_upscale
        text_settings = kwargs.pop("文本模型配置", None)
        image_settings = kwargs.pop("图像模型配置", None)
        video_settings = kwargs.pop("视频模型配置", None)
        supplied_rules = kwargs.pop("提示词规则", None)
        prompt_rules = normalize_prompt_rules(supplied_rules) if supplied_rules is not None else None
        if prompt_rules is not None:
            kwargs["_prompt_rules"] = prompt_rules
        for title, settings in (("文本", text_settings), ("图像", image_settings), ("视频", video_settings)):
            if settings is not None and not isinstance(settings, dict):
                raise ValueError(f"{title}模型配置无效，请重新连接配置节点。")
        if text_settings:
            kwargs["语言模型"] = text_settings["model"]
            kwargs["_text_settings"] = text_settings
        supplied_image_settings = image_settings is not None
        if text_only:
            image_settings = None
        elif image_settings is None:
            image_settings = {"storyboard_long_edge": kwargs.get("分镜图长边", 2048),
                              "reference_long_edge": kwargs.get("参考读取长边", 0)}
        image_settings = normalize_settings(image_settings)
        kwargs.pop("分镜图长边", None)
        kwargs.pop("参考读取长边", None)
        if not text_only and (supplied_image_settings or image_settings["storyboard_long_edge"] or image_settings["reference_long_edge"]):
            kwargs["_image_settings"] = image_settings
        if video_settings:
            kwargs.update(video_settings)
        from .stage_cache import effective_video_seed as resolve_video_seed
        effective_video_seed = resolve_video_seed(kwargs)
        if kwargs.get("加速权重") == "mystic07 · RefDelta r1024 Turbo8":
            kwargs["加速权重"] = "原作者融合模型"
        if text_only and kwargs.get('启用加速LoRA') and kwargs.get('加速权重') != '独立LoRA':
            raise ValueError('文生视频请将视频加速权重切为独立LoRA，或关闭加速使用基础模型。')

        from .plan_import import parse_import_payload, import_plan
        import_payload = parse_import_payload(kwargs.get("导入优化计划", "{}"))
        references = [] if text_only else resolve_references(kwargs["参考图集"])
        script = str(kwargs["剧本"]).strip()
        if not script and import_payload is None:
            raise ValueError("请先输入完整剧本。")
        from .reference_mentions import normalize_reference_mentions
        if text_only:
            from .planner import validate_text_only_references
            validate_text_only_references(script, references)
        else:
            script = normalize_reference_mentions(script, len(references))
        kwargs["剧本"] = script
        _check_mentions(script, references)
        duration_mode = kwargs.get("时长模式", "手动设置")
        if duration_mode not in ("手动设置", "AI自动分配"):
            raise ValueError("请选择有效的时长模式。")
        target_total = None
        if duration_mode == "AI自动分配":
            count = int(kwargs["分镜数量"])
            try:
                target_total = float(kwargs.get("目标总秒数", 30.0))
            except (TypeError, ValueError) as exc:
                raise ValueError("目标总秒数应为有效数字。") from exc
            if (isinstance(kwargs.get("目标总秒数"), bool) or not 1 <= count <= MAX_SHOTS or not math.isfinite(target_total) or
                    not count <= target_total <= 15 * count or
                    not math.isclose(target_total * 2, round(target_total * 2), abs_tol=1e-6)):
                raise ValueError("自动分配总时长应为镜头数～镜头数×15秒之间的0.5秒倍数。")
            durations = [1.0] * count
        else:
            durations = parse_durations(kwargs["每镜秒数"], kwargs["分镜数量"])
        overrides = _overrides(kwargs.get("分镜覆盖", "{}"), len(durations))
        mode, tail_mode = kwargs["生成模式"], kwargs["末帧处理"]
        if mode == "末帧高清续接":
            tail_mode = kwargs["末帧处理"] = "Image2.1修复"
        constrained = mode not in TAIL_SEQUENCE_MODES and kwargs["镜头衔接"] == "首尾帧约束"
        if (mode not in MODES and not text_only) or tail_mode not in TAIL_MODES:
            raise ValueError("请选择有效的生成模式与末帧处理方式。")
        manual_slots = resolve_manual_images(kwargs.get("手动分镜图片", "[]"), len(durations)) if mode == "手动分镜图" else None
        imported_plan = import_plan(import_payload, count=len(durations), durations=durations, references=references,
                                    mode=mode, voice_mode=kwargs["声音模式"], duration_mode=duration_mode,
                                    target_total_seconds=target_total, prompt_format=prompt_format,
                                    input_mode=input_mode) if import_payload is not None else None
        if imported_plan is not None:
            kwargs["_import_plan_identity"] = hashlib.sha256(json.dumps(imported_plan, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        keep_plan = bool(kwargs.get("保留分镜计划", False))
        edit_raw = kwargs.get("计划编辑", "{}")
        image_policy = kwargs.get("生图重做范围", "保留现有图片")
        image_selection = kwargs.get("重生图片", "[]")
        if mode == "手动分镜图":
            # Prompt edits are useful for export, but never request automatic
            # replacement of an image that the user supplied in a manual slot.
            image_policy, image_selection = "保留现有图片", "[]"
        if not keep_plan and not str(kwargs.get("继续任务", "")).strip():
            # A fresh full run must not inherit an old editor/selection command.
            edit_raw = "{}"
            image_policy, image_selection = "保留现有图片", "[]"
        has_edit_request = edit_raw not in (None, "", "{}", {})
        regeneration = _regeneration_ids(image_selection, image_policy,
                                         len(durations), constrained, mode)
        controlled_images = keep_plan or has_edit_request or image_policy != "保留现有图片"
        identity = str(kwargs.get("继续任务", "")).strip()
        redo = int(kwargs.get("重做镜头", 0))
        if not 0 <= redo <= len(durations):
            raise ValueError("重做镜头不在分镜范围内。")
        fingerprint = _fingerprint(kwargs, references, durations)
        anchor = canvas_reference = canvas_source = None
        if text_only:
            from .scene_inputs import resolve_text_canvas
            width, height, _ = resolve_text_canvas(kwargs['画幅'], kwargs['生成档位'], kwargs['自定义宽度'], kwargs['自定义高度'])
        else:
            anchor = _load_image(references[0]["path"])
            canvas_reference = _canvas_reference(references, kwargs["画幅"], kwargs.get("画幅参考图", "图1"))
            canvas_source = anchor if canvas_reference is references[0] else _load_image(canvas_reference["path"])
            width, height, _ = resolve_canvas(canvas_source, kwargs["画幅"], kwargs["生成档位"], kwargs["自定义宽度"], kwargs["自定义高度"])
        short_limit=kwargs.get('视频短边上限',0)
        if type(short_limit) is not int or short_limit<0 or short_limit>1088 or short_limit%32:
            raise ValueError('视频短边上限应为0或32的倍数；0跟随当前档位。')
        if short_limit and short_limit < min(width,height):
            scale=short_limit/min(width,height)
            width,height=[max(64,round(value*scale/32)*32) for value in (width,height)]
        canvas_source = None
        if mode == "AI生成分镜" and reference_upscale["enabled"] and (len(durations) > 1 or constrained):
            # Validate the would-be generated boundary BEFORE any image model
            # starts, so a 4K still followed by 4x cannot fail only at mirror 2.
            from .image_bridge import storyboard_canvas
            from .reference_upscale import expected_dimensions
            expected_dimensions(*storyboard_canvas(width, height, image_settings["storyboard_long_edge"]), reference_upscale)
        if not text_only:
            anchor = fit_reference(anchor, width, height, kwargs["适配方式"])
        from .stage_cache import stage_fingerprints, changed_stages, invalidate_saved_videos
        reference_identities=_reference_stage_identities(references)
        stage_ids = stage_fingerprints(kwargs, reference_identities, durations,
                                       image_settings=image_settings, width=width, height=height)
        video_settings_changed = False
        stage_migrated = False
        if identity:
            path = _manifest_path(identity)
            if not path.is_file():
                raise ValueError("找不到继续任务，请重新规划。")
            manifest = json.loads(path.read_text(encoding="utf-8"))
            old_stage_ids = manifest.get("stage_fingerprints")
            if not old_stage_ids:
                old_stage_ids = (_legacy_stage_identity(manifest, kwargs, references, durations, image_settings, width, height)
                                 if manifest.get("plan") else stage_ids if manifest.get("fingerprint") == fingerprint else None)
                stage_migrated = old_stage_ids is not None
            if old_stage_ids is None:
                raise ValueError("旧任务的原始设置无法完整核验，已保留原计划和全部图片。请从原任务总览复制完整优化稿，在新任务粘贴后继续；不会自动重做或删除旧图。")
            differences = changed_stages(old_stage_ids, stage_ids)
            if differences == ['video'] and not text_only:
                from .stage_cache import compatible_image_video_redo
                if compatible_image_video_redo(old_stage_ids, stage_ids, kwargs, keep_plan=keep_plan, redo=redo):
                    differences = []
                    stage_migrated = True
            if 'planning' not in differences and 'image' in differences:
                # Existing user-approved images remain usable across an adapter
                # update only when all original submitted parameters reproduce.
                verified=_legacy_stage_identity(manifest,kwargs,references,durations,image_settings,width,height)
                if verified and verified['image']==stage_ids['image']:
                    old_stage_ids=verified;differences=changed_stages(old_stage_ids,stage_ids);stage_migrated=True
            if "planning" in differences:
                raise ValueError("剧本、原参考图、分镜或规划设置已变化，不能沿用此任务；原计划和图片已保留，请新建任务或导入完整优化稿。")
            if "image" in differences:
                raise ValueError("分镜图像配置、画幅或衔接设置已变化，不能作为仅视频重跑；原计划和图片已保留，请在新任务导入完整优化稿后生成。")
            video_settings_changed = "video" in differences
            if video_settings_changed and (not keep_plan or not manifest.get("plan")):
                raise ValueError("沿用已有分镜修改视频参数时，请开启“保留分镜计划”后继续原任务。")
            if video_settings_changed:
                invalidate_saved_videos(manifest)
                clear_bundle_cache()
            else:
                manifest.update(video_settings_changed=False, video_reused=bool(manifest.get("final_path")))
        else:
            if redo:
                raise ValueError("重做镜头需要先选择已有任务。")
            identity = uuid.uuid4().hex
            manifest = {"version": 1, "run_id": identity, "fingerprint": fingerprint, "created_at": time.time(),
                        "mode": mode, "scene_count": len(durations), "width": width, "height": height,
                        "scenes": [{"index": i + 1, "seconds": seconds, "status": "等待"} for i, seconds in enumerate(durations)]}
            if imported_plan is not None:
                manifest.update(plan=imported_plan, plan_imported=True)
        manifest.update(fingerprint=fingerprint, stage_fingerprints=stage_ids)
        if stage_migrated:
            manifest["stage_cache_migrated"] = True
        run_dir = _manifest_path(identity).parent
        run_dir.mkdir(parents=True, exist_ok=True)
        previous_status = manifest.get("status")
        previous_regeneration_applied = manifest.get("regeneration_applied", False)
        manifest.update(status="running", error=None)
        from .progress_state import execution_context
        owner=execution_context(kwargs.get('unique_id',''))
        manifest.update(execution_id=uuid.uuid4().hex,progress_seq=0,prompt_id=owner.get('prompt_id'),node_id=owner.get('node_id',str(kwargs.get('unique_id',''))))
        from .output_metadata import save_workflow_snapshot
        workflow_path = save_workflow_snapshot(run_dir, manifest['execution_id'])
        if workflow_path:
            manifest.setdefault('workflow_snapshots', []).append({'execution_id': manifest['execution_id'],
                'prompt_id': manifest.get('prompt_id'), 'path': workflow_path})
        manifest["canvas_reference_id"] = canvas_reference["id"] if canvas_reference and kwargs["画幅"] == "跟随图片" else None
        manifest['video_input'] = video_input
        manifest.pop("failure_context", None)
        manifest.update(duration_mode=duration_mode, timing_mode=duration_mode)
        manifest["prompt_format"] = prompt_format
        manifest["reference_upscale"] = reference_upscale
        cached_plan = bool(manifest.get("plan"))
        images_ready_before=bool(mode=='AI生成分镜' and cached_plan and all(Path(scene.get('image_path') or '').is_file() for scene in manifest['scenes']) and (not constrained or Path(manifest.get('final_image_path') or '').is_file()))
        supplied_track = kwargs.get("_dialogue_track")
        dialogue_track = supplied_track if isinstance(supplied_track, dict) and supplied_track.get("enabled") else None
        dialogue_changed = cached_plan and manifest.get("dialogue_track") != dialogue_track
        manifest.update(plan_reused=cached_plan, regeneration_applied=False)
        manifest["output_directory"] = str(run_dir.resolve())
        manifest["source_images"] = [{"id": item["id"], "image": _preview(item["path"]), "status": "就绪",'sha256':reference_identities[i]['sha256']}
                                     for i,item in enumerate(references)]
        manifest["effective_settings"] = {"text": text_settings or {"model": kwargs["语言模型"]},
                                          "image": None if text_only else image_settings,
                                          "image_seed": int(kwargs["随机种子"]),
                                          "video_seed": effective_video_seed,
                                          "video": {name: kwargs.get(name) for name in (
                                              "基础模型", "原作者模型", "加速权重", "启用加速LoRA", "加速步数", "标准步数",
                                              "文本编码器", "视频VAE", "音频VAE", "低显存", "稀疏注意力", "4步LoRA", "8步LoRA", "视频采样器", "视频调度器")}}
        node_id = manifest['node_id']
        bundle = last_frame = selected = video = first = end_frame = None
        active_image_job = None
        timings = _TimingTracker(manifest)
        timings.start_stage("total")
        try:
            if not manifest.get("plan"):
                timings.start_stage("planning")
            _emit(manifest, node_id, "planning")
            mm.throw_exception_if_processing_interrupted()
            plan = manifest.get("plan")
            if not plan:
                # Retain cached CPU weights, but let the planner have the GPU.
                mm.unload_all_models()
                mm.soft_empty_cache()
                planning_options = {"script": script, "durations": durations, "model_name": kwargs["语言模型"],
                                    "voice_mode": kwargs["声音模式"], "seed": int(kwargs["随机种子"]), "references": references,
                                    "model_settings": text_settings}
                def planning_progress(receipt):
                    manifest['planning_progress'] = receipt
                    _emit(manifest, node_id, 'planning')
                planning_options['progress'] = planning_progress
                if text_only:
                    planning_options['input_mode'] = input_mode
                if prompt_rules is not None:
                    planning_options["prompt_rules"] = prompt_rules
                if duration_mode == "AI自动分配":
                    planning_options.update(duration_mode=duration_mode, target_total_seconds=target_total)
                plan = plan_sequence(**planning_options)
                if plan.get("status") != "ready" or plan.get("llm_released") is not True:
                    raise RuntimeError("语言模型规划未完成或尚未卸载，视频模型未启动。")
                if len(plan.get("scenes", [])) != len(durations) or len(plan.get("prompts", [])) != len(durations):
                    raise RuntimeError("规划结果镜头数量不符，请重新规划。")
                manifest["plan"] = plan
                timings.finish_stage("planning")
            plan["prompt_format"] = prompt_format
            if duration_mode == "AI自动分配":
                actual_durations = plan.get("scene_seconds")
                if (not isinstance(actual_durations, list) or len(actual_durations) != len(durations) or
                        any(type(seconds) not in (int, float) or not math.isfinite(seconds) or not 1 <= seconds <= 15
                            or not math.isclose(seconds * 2, round(seconds * 2), abs_tol=1e-6) for seconds in actual_durations) or
                        not math.isclose(sum(actual_durations), target_total, abs_tol=1e-6)):
                    raise RuntimeError("自动时长分配结果无效，尚未开始生成图片或视频。")
                durations = [float(seconds) for seconds in actual_durations]
            manifest.update(scene_seconds=durations, total_seconds=sum(durations),
                            allocation_receipt=plan.get("allocation_receipt"))
            for scene, seconds in zip(manifest["scenes"], durations):
                scene["seconds"] = seconds
            manifest.setdefault("base_plan", copy.deepcopy(plan))
            plan_changed = False
            if has_edit_request:
                from .plan_edits import normalize_plan_edits, apply_plan_edits
                edits = normalize_plan_edits(edit_raw, len(durations))
                edited = apply_plan_edits(manifest["base_plan"], edits)
                plan_changed = edited != plan
                plan = edited
                manifest["plan"] = edited
                manifest["plan_edits"] = edits
            manifest["plan_available"] = True
            regeneration_requested = bool(regeneration)
            if regeneration_requested:
                receipt = hashlib.sha256(json.dumps({"images": sorted(regeneration), "plan": plan,
                                                     "overrides": overrides}, ensure_ascii=False,
                                                    sort_keys=True).encode()).hexdigest()
                if (previous_status in ("failed", "cancelled", "plannedready") and
                        not previous_regeneration_applied and manifest.get("regeneration_receipt") == receipt):
                    # Continue the unfinished regeneration request. Completed
                    # images and saved raw outputs from that attempt stay valid.
                    regeneration = set()
                manifest["regeneration_receipt"] = receipt
            revision_advanced = bool(video_settings_changed or cached_plan and (plan_changed or regeneration or redo or dialogue_changed))
            if revision_advanced and not video_settings_changed:
                manifest["revision"] = int(manifest.get("revision", 0)) + 1
            else:
                manifest.setdefault("revision", 0)
            plan = copy.deepcopy(plan)
            sources = []
            invalidate_from = None
            for i, scene in enumerate(manifest["scenes"]):
                scene_plan = plan["scenes"][i]
                changed = overrides.get(str(i + 1), {})
                prompt = changed.get("video_prompt") or scene_plan.get("video_prompt") or plan["prompts"][i]
                image_prompt = changed.get("image_prompt") or scene_plan.get("image_prompt")
                prompt = apply_stage_rules(prompt, prompt_rules, "video")
                from .prompt_formats import video_prompt_for_format
                prompt = video_prompt_for_format(prompt, prompt_format)
                if "dialogue_whitelist" in plan:
                    from .planner import validate_prompt_dialogue
                    validate_prompt_dialogue(prompt, plan["dialogue_whitelist"], scene_plan.get("dialogues", []))
                if image_prompt:
                    image_prompt = apply_stage_rules(image_prompt, prompt_rules, "image")
                if not image_prompt and not text_only:
                    raise RuntimeError(f"镜头{i + 1}缺少静态画面提示词，请重新规划。")
                image_changed = scene.get("image_prompt") is not None and scene["image_prompt"] != image_prompt
                video_changed = scene.get("prompt") is not None and _prompt_body(scene["prompt"]) != _prompt_body(prompt)
                if 'video_seed' in changed:
                    video_changed = video_changed or changed['video_seed'] != resolve_scene_video_seed(effective_video_seed, i, scene)
                if cached_plan and controlled_images and video_changed and not revision_advanced:
                    manifest["revision"] += 1
                    revision_advanced = True
                image_invalid = f"分镜{i + 1}" in regeneration or (not controlled_images and image_changed)
                if mode in TAIL_SEQUENCE_MODES and (image_invalid or video_changed or redo == i + 1):
                    invalidate_from = i if invalidate_from is None else min(i, invalidate_from)
                scene.update(prompt=prompt, image_prompt=image_prompt,
                             spoken_dialogues=_spoken_dialogues(plan, scene_plan, prompt),
                             display_prompt=scene_plan.get("display_prompt", prompt),
                             plan_fields={name: scene_plan.get(name) for name in (
                                 "visual", "action", "start_state", "end_state", "dialogues", "reference_ids",
                                 "video_prompt", "image_prompt")})
                invalid = bool(redo and i + 1 == redo or invalidate_from is not None and i >= invalidate_from)
                if image_invalid or video_changed or invalid or dialogue_changed:
                    _invalidate_video(scene)
                    if image_invalid or invalid and mode == "AI生成分镜" and not controlled_images:
                        scene.pop("image_path", None)
                        scene.pop("image", None)
                        scene.pop("generated_image_path", None)
                        scene["image_dirty"] = True
                    manifest.pop("final_video", None)
                    manifest.pop("final_path", None)
                    if constrained and i > 0 and mode == "AI生成分镜" and (image_invalid or invalid and not controlled_images):
                        # This image is also the previous clip's endpoint.
                        _invalidate_video(manifest["scenes"][i - 1])
                sources.append([] if text_only else _reference_ids(scene_plan, references, mode, i))
                if not constrained:
                    scene.pop("end_image", None)
                    scene.pop("end_image_path", None)
                    if text_only:
                        scene['display_prompt'] = scene['prompt']
                    else:
                        first_id = f"分镜{i + 1}" if mode in ("AI生成分镜", "手动分镜图") else (f"首帧{i + 1}" if mode in TAIL_SEQUENCE_MODES and i else sources[i][0]["id"])
                        scene["display_prompt"] = f"@{first_id} 开始\n" + scene["prompt"]

            if "结束帧" in regeneration:
                manifest.pop("final_image_path", None)
                manifest.pop("final_generated_image_path", None)
                manifest["final_image_dirty"] = True
                _invalidate_video(manifest["scenes"][-1])
                manifest.pop("final_video", None)
                manifest.pop("final_path", None)

            for i, scene in enumerate(manifest["scenes"]):
                if text_only:
                    scene['display_prompt'] = scene['prompt']
                    continue
                if mode in ("AI生成分镜", "手动分镜图"):
                    first_id = f"分镜{i + 1}"
                    end_id = (f"分镜{i + 2}" if i + 1 < len(durations) else "结束帧" if mode == "AI生成分镜" or manual_slots[-1] else None) if constrained else None
                else:
                    first_id = f"首帧{i + 1}" if mode in TAIL_SEQUENCE_MODES and i else sources[i][0]["id"]
                    end_id = None
                    if constrained:
                        end_id = sources[i + 1][0]["id"] if i + 1 < len(durations) else references[len(durations)]["id"] if len(references) > len(durations) else None
                scene["display_prompt"] = f"@{first_id} 开始" + (f" → @{end_id} 结束" if end_id else "") + "\n" + scene["prompt"]
            manifest["optimized_script"] = _optimized_script(plan, manifest["scenes"])
            from .output_metadata import set_story_prompt_metadata
            set_story_prompt_metadata(kwargs.get("剧本"), manifest["optimized_script"], manifest["scenes"])
            # Commit this receipt only after every old video has been invalidated.
            # A cancelled preflight must not make the next run reuse the old soundtrack.
            if dialogue_track is not None:
                manifest["dialogue_track"] = copy.deepcopy(dialogue_track)
            else:
                manifest.pop("dialogue_track", None)
            if mode == "手动分镜图":
                _sync_manual_images(manifest, manual_slots, run_dir, kwargs["适配方式"], constrained, revision_advanced)
            elif mode == "AI生成分镜" and keep_plan and cached_plan:
                _sync_ai_image_edits(manifest, kwargs.get('手动分镜图片', '[]'), run_dir, kwargs['适配方式'], constrained)
                images_ready_before = (all(Path(scene.get('image_path') or '').is_file() for scene in manifest['scenes'])
                                       and (not constrained or Path(manifest.get('final_image_path') or '').is_file()))
            if mode in ("AI生成分镜", "手动分镜图"):
                jobs = []
                previous_jobs = {job["id"]: job for job in manifest.get("image_jobs", [])}
                total_steps = int(image_settings["steps"])
                for i, scene in enumerate(manifest["scenes"]):
                    ready = bool(scene.get("image_path") and Path(scene["image_path"]).is_file())
                    jobs.append({**previous_jobs.get(f"分镜{i + 1}", {}), "id": f"分镜{i + 1}", "index": i + 1, "prompt": scene["image_prompt"],
                                 "status": "完成" if ready else "等待", "percent": 100.0 if ready else None,
                                 "value": total_steps if ready else 0, "total": total_steps,
                                 "image": scene.get("image") if ready else None, "inserted": ready,
                                 "plan_fields": scene.get("plan_fields"),
                                 "reference_labels": scene.get("reference_labels", []),
                                 "timing": _timing_record(previous_jobs.get(f"分镜{i + 1}", {}).get("timing"),
                                                          "completed" if ready else "pending")})
                if constrained:
                    end_path = Path(manifest.get("final_image_path") or str(run_dir / "结束帧.png"))
                    ready = not manifest.get("final_image_dirty") and end_path.is_file() and (mode != "手动分镜图" or bool(manual_slots[-1]))
                    jobs.append({**previous_jobs.get("结束帧", {}), "id": "结束帧", "index": len(durations) + 1, "prompt": apply_stage_rules(plan.get("final_image_prompt", ""), prompt_rules, "image"),
                                 "status": "完成" if ready else "等待", "percent": 100.0 if ready else None,
                                 "value": total_steps if ready else 0, "total": total_steps,
                                 "image": _preview(end_path) if ready else None,
                                 "inserted": ready and bool(manifest["scenes"][-1].get("end_image")),
                                 "timing": _timing_record(previous_jobs.get("结束帧", {}).get("timing"),
                                                          "completed" if ready else "pending")})
                manifest["image_jobs"] = jobs
                _image_summary(manifest)
            elif text_only:
                manifest['image_jobs'] = []
                _image_summary(manifest)
            elif mode in TAIL_SEQUENCE_MODES and tail_mode == "Image2.1修复":
                previous_jobs = {job["id"]: job for job in manifest.get("image_jobs", [])}
                jobs = []
                for i, scene in enumerate(manifest["scenes"]):
                    if not i:
                        continue
                    job_id = f"修复末帧{i + 1}"
                    ready = bool(scene.get("repaired_image_path") and Path(scene["repaired_image_path"]).is_file())
                    jobs.append({**previous_jobs.get(job_id, {}), "id": job_id, "index": i + 1, "kind": "tail_repair",
                                 "prompt": _tail_repair_prompt(plan["scenes"][i]),
                                 "status": "完成" if ready else "等待", "percent": 100.0 if ready else None,
                                 "value": int(image_settings["steps"]) if ready else 0, "total": int(image_settings["steps"]),
                                 "image": _preview(scene["repaired_image_path"]) if ready else None, "inserted": ready,
                                 "timing": _timing_record(previous_jobs.get(job_id, {}).get("timing"), "completed" if ready else "pending")})
                manifest["image_jobs"] = jobs
                _image_summary(manifest)
            _emit(manifest, node_id, "planned")

            if mode == "手动分镜图" and manifest["manual_missing"]:
                manifest["status"] = "manual_waiting"
                timings.finish_stage("total", "pending")
                return _execution_result(manifest, _emit(manifest, node_id, "manual_waiting"))

            if kwargs["执行范围"] == "仅规划剧本" or keep_plan and not cached_plan and not imported_plan or mode == "手动分镜图" and (not cached_plan or not keep_plan):
                manifest["status"] = "plannedready"
                timings.finish_stage("total", "pending")
                event = _emit(manifest, node_id, "planned")
                return _execution_result(manifest, event)

            if mode == "AI生成分镜" or mode in TAIL_SEQUENCE_MODES and tail_mode == "Image2.1修复":
                from .image_bridge import validate_image_backend
                validate_image_backend(settings=image_settings)

            # Finish all independent first frames before loading H3. This avoids
            # retaining both image and video models throughout an AI storyboard.
            for i, scene in enumerate(manifest["scenes"]):
                if text_only:
                    continue
                mm.throw_exception_if_processing_interrupted()
                if mode in TAIL_SEQUENCE_MODES and i > 0:
                    continue
                path = Path(scene.get("image_path", ""))
                if path.is_file() and not scene.get("image_dirty"):
                    continue
                active_image_job = f"分镜{i + 1}" if mode == "AI生成分镜" else None
                timings.start_stage("images")
                if active_image_job:
                    timings.start_image(manifest, active_image_job)
                scene["status"] = "准备图片"
                _emit(manifest, node_id, "images", i + 1)
                image_path = _revision_path(run_dir, f"首帧_{i + 1:03d}.png", manifest)
                if mode == "AI生成分镜":
                    from .image_bridge import generate_reference
                    clear_bundle_cache()
                    release_resources(full=False)
                    generated = scene.get("generated_image_path")
                    if not generated or not Path(generated).is_file():
                        previous_image = (manifest["scenes"][i - 1].get("generated_image_path") or
                                          manifest["scenes"][i - 1].get("image_path")) if i else None
                        if i and (not previous_image or not Path(previous_image).is_file()):
                            raise ValueError(f"上一分镜{i}的图片不存在，请先生成该分镜图。")
                        previous_image = _prepare_upscaled_reference(previous_image, reference_upscale, manifest,
                                                                     active_image_job, node_id, run_dir)
                        context = build_reference_context(sources[i], previous_image, f"分镜{i}")
                        _record_image_references(manifest, active_image_job, context, scene)
                        generated = generate_reference(scene["image_prompt"], context["paths"][0], width, height,
                                                       seed=(int(kwargs["随机种子"]) + i) % 2**32,
                                                       reference_paths=context["paths"], reference_roles=context["roles"],
                                                       settings=image_settings,
                                                       storyboard=True,
                                                       progress_callback=_image_job_progress(manifest, node_id, active_image_job))
                        scene["generated_image_path"] = generated
                        _store(manifest)
                    generated = _collect_generated_image(generated, _revision_path(run_dir, f"分镜原图_{i + 1:03d}.png", manifest))
                    scene["generated_image_path"] = generated
                    _store(manifest)
                    # Independent 32px rounding must not create dark borders.
                    image = fit_reference(_load_image(generated), width, height, '等比裁切')
                else:
                    image = fit_reference(_load_image(sources[i][0]["path"]), width, height, kwargs["适配方式"])
                _save_image(image, image_path)
                del image
                scene.update(image_path=str(image_path), image=_preview(image_path), status="图片就绪")
                scene.pop("image_dirty", None)
                if active_image_job:
                    _image_job_complete(manifest, active_image_job, scene["image"])
                    timings.finish_image(manifest, active_image_job)
                    active_image_job = None
                _emit(manifest, node_id, "images", i + 1)

            if constrained:
                existing_final = manifest.get("final_image_path") if mode == "手动分镜图" else manifest.get("final_image_path") or str(run_dir / "结束帧.png")
                final_path = Path(existing_final) if existing_final else None
                if mode != "手动分镜图" and (manifest.get("final_image_dirty") or not final_path.is_file()):
                    final_path = _revision_path(run_dir, "结束帧.png", manifest)
                if mode == "AI生成分镜" and (manifest.get("final_image_dirty") or not final_path.is_file()):
                    active_image_job = "结束帧"
                    timings.start_stage("images")
                    timings.start_image(manifest, active_image_job)
                    from .image_bridge import generate_reference
                    clear_bundle_cache()
                    release_resources(full=False)
                    _emit(manifest, node_id, "end_image", len(durations))
                    final_prompt = plan.get("final_image_prompt")
                    if final_prompt:
                        final_prompt = apply_stage_rules(final_prompt, prompt_rules, "image")
                    if not final_prompt:
                        raise RuntimeError("分镜计划缺少最终结束画面，请重新规划。")
                    generated = manifest.get("final_generated_image_path")
                    if not generated or not Path(generated).is_file():
                        previous_image = (manifest["scenes"][-1].get("generated_image_path") or
                                          manifest["scenes"][-1].get("image_path"))
                        if not previous_image or not Path(previous_image).is_file():
                            raise ValueError("最后分镜的图片不存在，请先生成该分镜图。")
                        previous_image = _prepare_upscaled_reference(previous_image, reference_upscale, manifest,
                                                                     active_image_job, node_id, run_dir)
                        context = build_reference_context(sources[-1], previous_image, f"分镜{len(durations)}")
                        _record_image_references(manifest, active_image_job, context)
                        generated = generate_reference(final_prompt, context["paths"][0], width, height,
                                                       seed=(int(kwargs["随机种子"]) + len(durations)) % 2**32,
                                                       reference_paths=context["paths"], reference_roles=context["roles"],
                                                       settings=image_settings,
                                                       storyboard=True,
                                                       progress_callback=_image_job_progress(manifest, node_id, active_image_job))
                        manifest["final_generated_image_path"] = generated
                        _store(manifest)
                    generated = _collect_generated_image(generated, _revision_path(run_dir, "结束原图.png", manifest))
                    manifest["final_generated_image_path"] = generated
                    _store(manifest)
                    image = fit_reference(_load_image(generated), width, height, '等比裁切')
                    _save_image(image, final_path)
                    del image
                elif mode == "逐镜参考图" and len(references) > len(durations):
                    timings.start_stage("images")
                    image = fit_reference(_load_image(references[len(durations)]["path"]), width, height, kwargs["适配方式"])
                    _save_image(image, final_path)
                    del image
                if final_path is not None and final_path.is_file():
                    manifest["final_image_path"] = str(final_path)
                    manifest.pop("final_image_dirty", None)
                for i, scene in enumerate(manifest["scenes"]):
                    endpoint = Path(manifest["scenes"][i + 1]["image_path"]) if i + 1 < len(durations) else final_path
                    has_endpoint = endpoint is not None and endpoint.is_file()
                    scene["end_image"] = _preview(endpoint) if has_endpoint else None
                    scene["end_image_path"] = str(endpoint) if has_endpoint else None
                    original = overrides.get(str(i + 1), {}).get("video_prompt")
                    if original:
                        # The text editor stores the full native prompt; update
                        # its alignment preamble when switching to FL2VA.
                        compiled = render_h3_prompt(plan["compiled_prompts"][i], input_mode="FL2VA" if has_endpoint else "I2V", duration_seconds=durations[i])
                        scene["prompt"] = _aligned_override(original, compiled)
                    else:
                        scene["prompt"] = render_h3_prompt(plan["compiled_prompts"][i], input_mode="FL2VA" if has_endpoint else "I2V", duration_seconds=durations[i])
                    start_id = f"分镜{i + 1}" if mode in ("AI生成分镜", "手动分镜图") else sources[i][0]["id"]
                    end_id = (f"分镜{i + 2}" if i + 1 < len(durations) else "结束帧") if mode in ("AI生成分镜", "手动分镜图") else (sources[i + 1][0]["id"] if i + 1 < len(durations) else references[len(durations)]["id"] if has_endpoint else "自由结束")
                    scene["display_prompt"] = (f"@{start_id} 开始 → @{end_id} 结束\n" if has_endpoint else f"@{start_id} 开始\n") + scene["prompt"]
                    scene["prompt"] = apply_stage_rules(scene["prompt"], prompt_rules, "video")
                    scene["prompt"] = video_prompt_for_format(scene["prompt"], prompt_format)
                manifest["optimized_script"] = _optimized_script(plan, manifest["scenes"])
                set_story_prompt_metadata(kwargs.get("剧本"), manifest["optimized_script"], manifest["scenes"])
                if mode == "AI生成分镜" and final_path.is_file():
                    _image_job_complete(manifest, "结束帧", _preview(final_path))
                    timings.finish_image(manifest, "结束帧")
                    active_image_job = None
                timings.finish_stage("images")
                _emit(manifest, node_id, "images_ready")

            timings.finish_stage("images")
            if regeneration_requested:
                manifest["regeneration_applied"] = True

            if not text_only and (kwargs["执行范围"] == "仅准备分镜图" or (mode=='AI生成分镜' and not kwargs.get('生图后生成视频',False) and (not images_ready_before or regeneration_requested))):
                manifest["status"] = "prepared"
                timings.finish_stage("total", "pending")
                event = _emit(manifest, node_id, "prepared")
                return _execution_result(manifest, event)

            steps = int(kwargs["加速步数"]) if kwargs["启用加速LoRA"] else max(20, int(kwargs["标准步数"]))
            for i, scene in enumerate(manifest["scenes"]):
                mm.throw_exception_if_processing_interrupted()
                completed = Path(scene.get("video_path", ""))
                if completed.is_file() and scene.get("status") == "完成":
                    continue
                if text_only:
                    first = None
                elif mode in TAIL_SEQUENCE_MODES and i:
                    previous = manifest["scenes"][i - 1]
                    tail_path = previous.get("tail_path")
                    if not tail_path or not Path(tail_path).is_file():
                        raise RuntimeError(f"镜头{i}的末帧缺失，请先重做该镜头。")
                    first = _load_image(tail_path)
                    if tail_mode == "亮度轻校正":
                        first = correct_tail_exposure(first, anchor)
                    elif tail_mode == "Image2.1修复":
                        from .image_bridge import generate_reference
                        repair_prompt = _tail_repair_prompt(plan["scenes"][i])
                        context = _tail_repair_context(tail_path, references[0]["path"], i)
                        receipt = _tail_repair_receipt(tail_path, references[0]["path"], repair_prompt, image_settings)
                        repaired = scene.get("repaired_image_path")
                        active_image_job = f"修复末帧{i + 1}"
                        if not repaired or not Path(repaired).is_file() or scene.get("repair_receipt") not in (None, receipt):
                            # Drop the video bundle before Qwen loads. The image
                            # bridge unloads its own handles before returning.
                            bundle = selected = None
                            clear_bundle_cache()
                            release_resources(full=False)
                            timings.finish_stage("video")
                            timings.start_stage("images")
                            timings.start_image(manifest, active_image_job)
                            scene["status"] = "修复末帧"
                            _record_image_references(manifest, active_image_job, context, scene)
                            _emit(manifest, node_id, "repair", i + 1)
                            repaired = generate_reference(
                                repair_prompt, tail_path, width, height,
                                seed=(int(kwargs["随机种子"]) + i) % 2**32,
                                reference_paths=context["paths"], reference_roles=context["roles"], settings=image_settings,
                                progress_callback=_image_job_progress(manifest, node_id, active_image_job))
                            scene.update(repaired_image_path=repaired, repair_receipt=receipt)
                            _store(manifest)
                        repaired = _collect_generated_image(repaired, _revision_path(run_dir, f"修复原图_{i + 1:03d}.png", manifest))
                        scene.update(repaired_image_path=repaired, repair_receipt=receipt)
                        _image_job_complete(manifest, active_image_job, _preview(repaired))
                        timings.finish_image(manifest, active_image_job)
                        timings.finish_stage("images")
                        active_image_job = None
                        _store(manifest)
                        first = _load_image(repaired)
                    first = fit_reference(first, width, height, kwargs["适配方式"])
                    image_path = _revision_path(run_dir, f"首帧_{i + 1:03d}.png", manifest)
                    _save_image(first, image_path)
                    scene.update(image_path=str(image_path), image=_preview(image_path))
                else:
                    first = _load_image(scene["image_path"])
                end_frame = _load_image(scene["end_image_path"]) if constrained and scene.get("end_image_path") else None
                timings.start_stage("video")
                if not isinstance(scene.get("video_timing"), dict):
                    scene["video_timing"] = _timing_record()
                timings.start(scene["video_timing"])
                scene["status"] = "生成中"
                _emit(manifest, node_id, "video", i + 1)
                selected_plan = {**plan, "seconds_per_scene": durations[i]}
                if bundle is None:
                    bundle = _bundle(selected_plan, kwargs)
                selected = {**bundle, "ready_plan": json.dumps(selected_plan, ensure_ascii=False),
                            "_voice_settings": kwargs.get("_voice_settings"), "_voice_scene": plan["scenes"][i],
                            "_dialogue_track": kwargs.get("_dialogue_track"),
                            "_user_silent": kwargs.get("声音模式") == "静音",
                            "_dialogue_offset_seconds": sum(round(round(seconds * 24) / 24 * 32000) for seconds in durations[:i]) / 32000}
                scene_seed = resolve_scene_video_seed(effective_video_seed, i, scene, overrides.get(str(i + 1)))
                scene.update(video_seed=scene_seed, video_seed_base=effective_video_seed)
                video, last_frame, report = H3GenerateShot().generate(
                    bundle=selected, prompt=scene["prompt"], width=width, height=height,
                    seconds=durations[i], steps=steps, seed=scene_seed,
                    audio_mode=plan.get("audio_mode", "模型音频"), continue_previous=False,
                    first_frame=first, last_frame=end_frame,
                    sampler_name=kwargs.get("视频采样器", "res_multistep"), scheduler=kwargs.get("视频调度器", "simple"))
                first = end_frame = None
                report = json.loads(report)
                destination = _revision_path(run_dir, f"镜头_{i + 1:03d}.mp4", manifest)
                shutil.move(report["temporary_video"], destination)
                tail_path = _revision_path(run_dir, f"末帧_{i + 1:03d}.png", manifest)
                _save_image(last_frame, tail_path)
                video = last_frame = selected = None
                scene.update(status="完成", video_path=str(destination), video=_preview(destination), tail_path=str(tail_path), report=report)
                timings.finish(scene["video_timing"])
                _emit(manifest, node_id, "video", i + 1)
                gc.collect()
                mm.soft_empty_cache()
            timings.finish_stage("video")
            timings.start_stage("joining")
            _emit(manifest, node_id, "joining", len(durations))
            videos = [InputImpl.VideoFromFile(scene["video_path"]) for scene in manifest["scenes"]]
            joined = ConcatenateVideo.execute(videos={f"video{i}": [item] for i, item in enumerate(videos)}, codec=["auto"]).result[0]
            destination = _revision_path(run_dir, "完整视频.mp4", manifest)
            partial = destination.with_suffix(".partial.mp4")
            try:
                from .output_metadata import current_metadata
                joined.save_to(str(partial), format=Types.VideoContainer.MP4, codec=Types.VideoCodec.H264,
                               metadata=current_metadata())
                mm.throw_exception_if_processing_interrupted()
                os.replace(partial, destination)
            finally:
                partial.unlink(missing_ok=True)
            joined = videos = None
            timings.finish_stage("joining")
            timings.finish_stage("total")
            manifest.update(status="completed", final_video=_preview(destination), final_path=str(destination),
                            video_settings_recompiled=bool(manifest.get("video_settings_changed")), video_settings_changed=False)
            event = _emit(manifest, node_id, "completed", len(durations))
            return _execution_result(manifest, event)
        except (Exception, mm.InterruptProcessingException) as exc:
            cancelled = isinstance(exc, mm.InterruptProcessingException)
            failed_stage = manifest.get("stage", "planning")
            if not cancelled:
                manifest["failure_context"] = {"stage": failed_stage,
                                               "scene_index": int(manifest.get("scene_index") or 0),
                                               "image_id": active_image_job,
                                               "message": str(exc),
                                               "exception_type": type(exc).__name__}
            manifest.update(status="cancelled" if cancelled else "failed", error="已取消" if cancelled else str(exc))
            timings.finish_all(manifest["status"])
            if active_image_job:
                job = next((item for item in manifest.get("image_jobs", []) if item["id"] == active_image_job), None)
                if job is not None:
                    job.update(status="已取消" if cancelled else "失败", error=manifest["error"])
                _image_summary(manifest)
            for scene in manifest["scenes"]:
                if scene["status"] in ("生成中", "准备图片", "修复末帧"):
                    scene.update(status="已取消" if cancelled else "失败", error=manifest["error"])
            _emit(manifest, node_id, manifest["status"], manifest.get("scene_index", 0))
            raise
        finally:
            timings.finish_all({"prepared": "pending", "plannedready": "pending", "manual_waiting": "pending", "completed": "completed", "cancelled": "cancelled",
                                "failed": "failed"}.get(manifest["status"], "failed"))
            bundle = selected = last_frame = video = anchor = first = end_frame = None
            if kwargs["完成后"] == "释放模型与缓存" or manifest["status"] in ("failed", "cancelled"):
                clear_bundle_cache()
                release_resources(full=True, clear_execution=False)
            else:
                gc.collect()
                mm.soft_empty_cache()


STUDIO_NODE_CLASS_MAPPINGS = {"H3StoryboardStudio": H3StoryboardStudio}
STUDIO_NODE_DISPLAY_NAME_MAPPINGS = {"H3StoryboardStudio": "H3 多分镜工作台"}
