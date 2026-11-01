"""Pure stage identities and non-destructive video invalidation for saved plans."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

STAGE_SCHEMA = "h3-stage-cache/1"
VIDEO_KEYS = ("基础模型", "原作者模型", "加速权重", "启用加速LoRA", "加速步数", "标准步数",
              "文本编码器", "视频VAE", "音频VAE", "低显存", "稀疏注意力", "4步LoRA", "8步LoRA",
              "视频采样器", "视频调度器", "_voice_settings", "_dialogue_track")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _implementation_identities(names):
    directory = Path(__file__).parent
    return {name: hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in names}


def effective_video_seed(kwargs):
    image_seed = kwargs.get("随机种子", 42)
    video_seed = kwargs.get("视频种子", -1)
    if type(image_seed) is not int or not 0 <= image_seed <= 2**32 - 1:
        raise ValueError("图像与规划随机种子应为0～4294967295的整数。")
    if type(video_seed) is not int or not -1 <= video_seed <= 2**32 - 1:
        raise ValueError("视频种子应为0～4294967295的整数；-1沿用原随机种子。")
    return image_seed if video_seed == -1 else video_seed


def stage_fingerprints(kwargs, references, durations, *, image_settings, width, height):
    automatic = kwargs.get("时长模式", "手动设置") == "AI自动分配"
    planning = {"script": kwargs.get("剧本"), "references": references,
                "mode": kwargs.get("生成模式"), "count": len(durations),
                "timing": {"mode": "AI自动分配", "total": float(kwargs.get("目标总秒数", 30))} if automatic else
                          {"mode": "手动设置", "durations": list(durations)},
                "text": kwargs.get("_text_settings") or {"model": kwargs.get("语言模型")},
                "speech_mode": kwargs.get("声音模式"), "prompt_rules": kwargs.get("_prompt_rules"),
                "prompt_format": kwargs.get("提示词格式", "工作台格式"), "seed": kwargs.get("随机种子"),
                "import_plan_identity": kwargs.get("_import_plan_identity")}
    planning_hash = _digest(planning)
    image = {"planning": planning_hash, "settings": image_settings, "width": width, "height": height,
             "implementation": _implementation_identities(("image_bridge.py", "reference_composition.py", "reference_upscale.py")),
             "aspect": kwargs.get("画幅"), "resolution": kwargs.get("生成档位"),
             "canvas_reference": kwargs.get("画幅参考图", "图1"), "fit_mode": kwargs.get("适配方式"),
             "connection": kwargs.get("镜头衔接"), "tail_processing": kwargs.get("末帧处理"),
             "upscale": kwargs.get("_reference_upscale") if kwargs.get("生成模式") == "AI生成分镜" and
                         kwargs.get("_reference_upscale", {}).get("enabled") else {"enabled": False}}
    if kwargs.get('视频输入') == '文生视频':
        # Text-video has no image pipeline, so hidden image controls cannot
        # invalidate a text-only task or trigger image-model work.
        image = {'planning': planning_hash, 'input_mode': 'T2VA',
                 'width': width, 'height': height, 'aspect': kwargs.get('画幅'),
                 'resolution': kwargs.get('生成档位')}
    image_hash = _digest(image)
    video = {"image": image_hash, "settings": {key: kwargs.get(key) for key in VIDEO_KEYS},
             "implementation": _implementation_identities(("shot_nodes.py", "native_nodes.py"))}
    video_seed = effective_video_seed(kwargs)
    # Legacy tasks did not store a separate video seed. Omitted, inherited and
    # explicitly identical values retain their original video stage identity.
    if kwargs.get("视频种子", -1) >= 0 and video_seed != kwargs.get("随机种子", 42):
        video["video_seed"] = video_seed
    return {"schema": STAGE_SCHEMA, "planning": planning_hash, "image": image_hash, "video": _digest(video)}


def changed_stages(previous, current):
    if not isinstance(previous, dict) or previous.get("schema") != STAGE_SCHEMA:
        raise ValueError("旧任务缺少可验证的分阶段缓存标识。")
    return [stage for stage in ("planning", "image", "video") if previous.get(stage) != current.get(stage)]


def compatible_image_video_redo(previous, current, kwargs, *, keep_plan=False, redo=0):
    """Allow a targeted redo across the audited 1.2.11 I2V adapter update.

    Match every original video setting, seed and image-stage identity. Only
    the two known LF/CRLF source receipts can differ; arbitrary code changes,
    text-video tasks and changed model/audio settings cannot use this path.
    """
    if (not isinstance(previous, dict) or not isinstance(current, dict) or
            not keep_plan or not redo or kwargs.get('视频输入', '图生视频') != '图生视频' or
            previous.get('schema') != STAGE_SCHEMA or
            any(previous.get(key) != current.get(key) for key in ('planning', 'image'))):
        return False
    video = {'image': current['image'], 'settings': {key: kwargs.get(key) for key in VIDEO_KEYS}}
    video_seed = effective_video_seed(kwargs)
    if kwargs.get('视频种子', -1) >= 0 and video_seed != kwargs.get('随机种子', 42):
        video['video_seed'] = video_seed
    shot_hashes = ('dbcca905acf19b6ac8d7ad5f9760614015cec1c065873538ecdd3abaf1d19f71',
                   '2e38b2d593e835f30bb86be10d06add27cc4c5211c81b48647edc8b7192aa4d2')
    native_hashes = ('3320400df93aaa71563f178bbe148ec4e76bc8519109698a3680f1e06c109f68',
                     'bcbc45688bcf820fd16f88e330d08d0e0859779047fe2b3636a9f171535267d3')
    target_shot_hashes = ('f767ae39b0ce12f7c939d80e58d27a2b1a53299b7555276b58e1abc0c5a4fddd',
                          '636f0861d45670f159e12bf73ebc3b0dcdcc3a934e81b60629441f603e86d938')
    implementation = _implementation_identities(('shot_nodes.py', 'native_nodes.py'))
    if (implementation['shot_nodes.py'] not in target_shot_hashes or
            implementation['native_nodes.py'] not in native_hashes or
            current.get('video') != _digest({**video, 'implementation': implementation})):
        return False
    return any(previous.get('video') == _digest({**video, 'implementation': {
        'shot_nodes.py': shot, 'native_nodes.py': native}})
        for shot in shot_hashes for native in native_hashes)


def invalidate_saved_videos(manifest):
    """Forget generated video receipts while preserving plans, jobs and files."""
    archived = {"revision": int(manifest.get("revision", 0)),
                "final_video": copy.deepcopy(manifest.get("final_video")), "final_path": manifest.get("final_path"),
                "scenes": [{key: copy.deepcopy(scene.get(key)) for key in
                            ("index", "video", "video_path", "tail_path", "repaired_image_path", "report") if key in scene}
                           for scene in manifest.get("scenes", [])]}
    if archived["final_path"] or any(scene.get("video_path") for scene in archived["scenes"]):
        manifest.setdefault("video_revision_history", []).append(archived)
    for scene in manifest.get("scenes", []):
        for key in ("video", "video_path", "tail_path", "repaired_image_path", "repair_receipt", "report", "error"):
            scene.pop(key, None)
        scene["status"] = "等待"
        scene["video_timing"] = {"status": "pending", "started_at": None, "elapsed_seconds": 0, "ended_at": None}
    for key in ("final_video", "final_path"):
        manifest.pop(key, None)
    if isinstance(manifest.get("timings"), dict):
        for key in ("video", "joining"):
            manifest["timings"][key] = {"status": "pending", "started_at": None, "elapsed_seconds": 0, "ended_at": None}
    manifest["revision"] = int(manifest.get("revision", 0))+1
    manifest.update(video_settings_changed=True, video_reused=False)
