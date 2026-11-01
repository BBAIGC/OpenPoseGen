"""Installed Qwen-Image 2.1 GGUF image-edit chain for storyboard stills.

Uses the same real loaders, encoder, prefix cache and sampler as the user's
Qwen2.1 workflow. Never downloads weights. All weights are dropped after each
image; the H3 caller must release its own bundle before entering this helper.
"""
from __future__ import annotations

import inspect
from contextlib import contextmanager
import logging
import math
from pathlib import Path
import re
import threading
import uuid

import folder_paths
import nodes

from .resource_control import release_resources

MODEL = "qwen-image-2.1-UC-Q6_K.gguf"
ENCODER = "qwen3vl_8b_int8_convrot.safetensors"
VAE = "qwen_image_2.1_vae_bf16.safetensors"
MAX_REFERENCES = 16
PREVIOUS_REFERENCE_POLICIES = ("完整参考（兼容）", "结构参考（原图细节优先）")
log = logging.getLogger(__name__)
DEFAULT_SETTINGS = {"model": MODEL, "clip": ENCODER, "vae": VAE,
                    "steps": 25, "cfg": 1.0, "sampler": "euler", "scheduler": "simple",
                    "denoise": 1.0, "cache_device": "auto", "cache_dtype": "default",
                    "clip_device": "default", "storyboard_long_edge": 2048,
                    "previous_reference_policy": PREVIOUS_REFERENCE_POLICIES[0], "reference_long_edge": 0}


def _outputs(value):
    if hasattr(value, "result"):
        return value.result
    if isinstance(value, dict) and "result" in value:
        return value["result"]
    return value


def _class(name):
    cls = nodes.NODE_CLASS_MAPPINGS.get(name)
    if cls is None:
        raise RuntimeError(f"缺少已安装的 {name} 节点，请重启 ComfyUI 并检查 Qwen2.1 工作流。")
    return cls


def normalize_settings(settings=None):
    """Resolve defaults into serializable effective values, without loading weights."""
    if settings is not None and not isinstance(settings, dict):
        raise ValueError("Image2.1配置格式无效，请连接图片模型配置节点。")
    values = dict(DEFAULT_SETTINGS)
    if settings:
        unknown = set(settings) - set(DEFAULT_SETTINGS)
        if unknown:
            raise ValueError("Image2.1配置含不支持的选项：" + "、".join(sorted(map(str, unknown))))
        values.update(settings)
    for name in ("model", "clip", "vae", "sampler", "scheduler", "cache_device", "cache_dtype", "clip_device"):
        if not isinstance(values[name], str) or not values[name].strip():
            raise ValueError(f"Image2.1的{name}选项不能为空。")
        values[name] = values[name].strip()
    model_name = values["model"].replace("\\", "/").rsplit("/", 1)[-1].lower()
    if not re.search(r"qwen[-_]?image[-_]?2[._-]?1(?:[-_.]|$)", model_name):
        raise ValueError("图片主模型必须属于Qwen Image 2.1系列。")
    if not model_name.endswith((".gguf", ".safetensors")):
        raise ValueError("Image2.1主模型仅支持GGUF或safetensors权重。")
    try:
        steps = float(values["steps"])
        if isinstance(values["steps"], bool) or not math.isfinite(steps) or not steps.is_integer() or not 1 <= steps <= 10000:
            raise ValueError
        values["steps"] = int(steps)
        for key, upper in (("cfg", 100), ("denoise", 1)):
            if isinstance(values[key], bool):
                raise ValueError
            value = float(values[key])
            if not math.isfinite(value) or not 0 <= value <= upper:
                raise ValueError
            values[key] = value
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Image2.1步数应为1～10000的整数，CFG应为0～100，降噪应为0～1。") from exc
    for key, options in (("cache_device", ("auto", "gpu", "cpu", "off")),
                         ("cache_dtype", ("default", "int8", "int4")),
                         ("clip_device", ("default", "cpu"))):
        if values[key] not in options:
            raise ValueError(f"Image2.1的{key}选项无效。")
    edge = values["storyboard_long_edge"]
    if type(edge) is not int or not 0 <= edge <= 4096 or edge % 32:
        raise ValueError("分镜图长边应为0～4096的32倍数；0表示跟随视频尺寸。")
    reference_edge = values["reference_long_edge"]
    if type(reference_edge) is not int or not 0 <= reference_edge <= 4096 or reference_edge % 32:
        raise ValueError("参考读取长边应为0～4096的32倍数；0自动选择最高2K。")
    if (not isinstance(values["previous_reference_policy"], str) or
            values["previous_reference_policy"] not in PREVIOUS_REFERENCE_POLICIES):
        raise ValueError("上镜参考策略无效，请选择完整参考或结构参考。")
    return values


def storyboard_canvas(width, height, long_edge=0):
    """Expand a still canvas on the 32px lattice; never shrink the request."""
    if not long_edge or max(width, height) >= long_edge:
        return width, height
    scale = long_edge / max(width, height)
    return (min(4096, math.ceil(width * scale / 32) * 32),
            min(4096, math.ceil(height * scale / 32) * 32))


def _structure_reference_slots(reference_roles):
    """A shared user-source slot always keeps its original detail budget."""
    roles = reference_roles or []
    protected = {entry["slot"] for entry in roles if entry["role"] in ("primary", "additional")}
    previous = {entry["slot"] for entry in roles if entry["role"] == "previous_storyboard"}
    return previous - protected


def _check_weights(settings):
    is_gguf = settings["model"].lower().endswith(".gguf")
    model_folder = "unet" if is_gguf else "diffusion_models"
    for folder, filename in ((model_folder, settings["model"]), ("text_encoders", settings["clip"]), ("vae", settings["vae"])):
        # Selections are library names, not arbitrary absolute filesystem paths.
        normalized = filename.replace("\\", "/")
        if normalized.startswith("/") or ":" in normalized or ".." in normalized.split("/"):
            raise ValueError("请选择模型目录内的权重文件。")
        path = folder_paths.get_full_path(folder, filename)
        if not path or not Path(path).is_file():
            raise ValueError(f"未找到本机 Qwen2.1 权重：{filename}。")
    for name in (("UnetLoaderGGUF",) if is_gguf else ()) + ("TextEncodeQwenImage21", "QwenImage21Cache"):
        _class(name)
    if not is_gguf and not hasattr(nodes, "UNETLoader"):
        raise RuntimeError("缺少ComfyUI原生UNETLoader，请检查安装。")
    sampler_schema = nodes.KSampler.INPUT_TYPES().get("required", {})
    for option, key in (("sampler_name", "sampler"), ("scheduler", "scheduler")):
        field = sampler_schema.get(option)
        if not field or not isinstance(field[0], (list, tuple)) or settings[key] not in field[0]:
            raise ValueError(f"当前ComfyUI不支持Image2.1的{key}选项：{settings[key]}。")
    # Detect incompatible old node revisions before any model is loaded.
    try:
        inspect.signature(_class("TextEncodeQwenImage21").execute).bind(
            clip=None, prompt="", negative_prompt="", vae=None, resolution=0, images={})
        inspect.signature(_class("QwenImage21Cache").execute).bind(
            model=None, device=settings["cache_device"], dtype=settings["cache_dtype"])
    except (TypeError, ValueError) as exc:
        raise RuntimeError("当前Qwen2.1编码或缓存节点版本不兼容，请检查本机工作流节点。") from exc


def _read_image(filename, max_long_edge=2048):
    import numpy as np
    from PIL import Image, ImageOps
    import torch

    filename = Path(filename)
    if not filename.is_file():
        raise ValueError(f"参考图不存在：{filename.name}。")
    with Image.open(filename) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        # Original references may be upscaled 8K images. Keep their geometry
        # while bounding vision/VAE tokens to the requested generation scale.
        image.thumbnail((max_long_edge, max_long_edge), Image.Resampling.LANCZOS)
        pixels = np.asarray(image, dtype=np.float32) / 255.0
    return torch.from_numpy(pixels).unsqueeze(0)


def validate_image_backend(settings=None):
    """Cheap preflight: no GPU initialization or model loading."""
    values = normalize_settings(settings)
    _check_weights(values)
    return {**values, "encoder": values["clip"], "max_references": MAX_REFERENCES}


def reference_role_prompt(prompt, reference_count, reference_roles=None):
    """Bind image roles while leaving explicit requested environment edits intact."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("分镜图片提示词为空，请先完成分镜规划。")
    if (not isinstance(reference_count, int) or isinstance(reference_count, bool)
            or not 1 <= reference_count <= MAX_REFERENCES):
        raise ValueError("Image2.1参考图数量应为1～16。")
    roles = ("<image1>是主参考。仅保持本镜未要求修改的背景、照明与整体色调。"
             "人物衣着、颜色、姿态、物件状态和构图以本镜正文的明确要求为准；"
             "本镜要求改变环境时也按正文执行，各参考图只提供被指定的用途。")
    if reference_roles is not None:
        if not isinstance(reference_roles, list) or not reference_roles or len(reference_roles) > MAX_REFERENCES + 1:
            raise ValueError("参考图角色映射无效。")
        mappings = []
        source_slots = {}
        for entry in reference_roles:
            if (not isinstance(entry, dict) or entry.get("role") not in ("primary", "previous_storyboard", "additional")
                    or type(entry.get("slot")) is not int or not 1 <= entry["slot"] <= reference_count
                    or not isinstance(entry.get("source_id"), str)
                    or not re.fullmatch(r"(?:图|分镜)[1-9]\d*|结束帧", entry["source_id"])):
                raise ValueError("参考图角色或输入槽编号无效。")
            prior_slot = source_slots.setdefault(entry["source_id"], entry["slot"])
            if prior_slot != entry["slot"]:
                raise ValueError("同一参考图来源不能映射到多个输入槽，请重新规划参考图。")
            source = ("上一" if entry["role"] == "previous_storyboard" else "用户素材") + entry["source_id"]
            if isinstance(entry.get("description"), str) and entry["description"].strip():
                source += "（" + re.sub(r"\s+", " ", entry["description"].strip())[:160] + "）"
            role = {"primary": "主参考与环境基准", "previous_storyboard": "相邻分镜几何与状态参考",
                    "additional": "附加身份、物体与细节参考"}[entry["role"]]
            mappings.append(f"{source}→<image{entry['slot']}>（{role}）")
        if not any(entry["role"] == "primary" and entry["slot"] == 1 for entry in reference_roles):
            raise ValueError("输入图像1缺少主参考角色。")
        roles += "输入图像对应关系：" + "；".join(mappings) + "。"
        # Source labels in planner prose are library IDs, not encoder slot IDs.
        # Replace only complete, declared labels in one pass; unknown labels stay
        # untouched rather than being guessed from their displayed number.
        source_pattern = r"(?<![A-Za-z0-9@])@?(?:(?:用户)?素材|参考)?(?:图[1-9]\d*|分镜[1-9]\d*|结束帧)(?!\d)"
        def source_slot(match):
            token = match.group(0)
            identity = re.sub(r"^@?(?:(?:用户)?素材|参考)?", "", token)
            slot = source_slots.get(identity)
            return f"<image{slot}>" if slot is not None else token
        prompt = re.sub(source_pattern, source_slot, prompt)
        if any(entry["role"] == "previous_storyboard" for entry in reference_roles):
            roles += ("上一分镜图只提供结构、几何关系和状态；人物身份与未要求修改的自然细节以指定用户素材为准，衣着和颜色按本镜正文的明确要求。"
                      "不复制上一分镜的模糊、磨皮或细节损失。"
                      "它是上一镜的开始画面，"
                      "不代表上一镜动作之后的结束状态。本镜开场状态、实体状态账本和明确环境要求优先，"
                      "已改变的物件位置、持有物和衣着以本镜当前状态为准；"
                      "按本镜要求重新组织构图、机位和姿态，不照搬上一分镜的构图或已过期状态。")
        if any(entry["role"] == "additional" for entry in reference_roles):
            roles += "附加用户素材提供其被指定的身份、物体与细节，不自动替换主参考场景和光照。"
    elif reference_count > 1:
        roles += "其余参考图仅提供身份、物体与细节参考，不自动替换主参考的场景和光照。"
    if reference_count > 1:
        roles += ("多张参考中被绑定为同一角色的主体仍是同一人物或物体，不从各参考分别复制并叠加一套人体或道具。"
                  "不同的明确角色以及本镜明确新增的主体分别保留；手部和道具互动按角色绑定与本镜要求组织。")
    return roles + "\n\n" + prompt


def _notify_progress(callback, phase, **values):
    if callback is None:
        return
    try:
        callback({"phase": phase, **values})
    except Exception:
        # A disconnected viewer must not discard an otherwise successful image.
        log.debug("Image2.1 progress listener failed", exc_info=True)


@contextmanager
def _sampling_progress(callback):
    """Observe native sampler progress while preserving installed wrappers."""
    if callback is None:
        yield
        return
    import comfy.utils as utils

    original = utils.PROGRESS_BAR_HOOK
    owner_thread = threading.get_ident()

    def proxy(value, total, *args, **kwargs):
        if original is not None:
            original(value, total, *args, **kwargs)
        if threading.get_ident() != owner_thread:
            return
        if (isinstance(value, (int, float)) and not isinstance(value, bool)
                and isinstance(total, (int, float)) and not isinstance(total, bool)
                and math.isfinite(value) and math.isfinite(total) and total > 0
                and 0 <= value <= total):
            _notify_progress(callback, "sampling", value=value, total=total,
                             percent=value / total * 100)

    utils.PROGRESS_BAR_HOOK = proxy
    try:
        yield
    finally:
        # Another extension may deliberately replace the hook during sampling.
        # Restore only our installation rather than overwriting its new hook.
        if utils.PROGRESS_BAR_HOOK is proxy:
            utils.PROGRESS_BAR_HOOK = original


def generate_reference(prompt, reference_path, width, height, seed=42, reference_paths=None, settings=None,
                       progress_callback=None, reference_roles=None, storyboard=False, text_only=False,
                       init_from_reference=False, negative_prompt=''):
    """Return a saved still path; reference 1 anchors identity/appearance.

Additional references stay independent and are capped at the installed
encoder's 16-image maximum. H3 consumes the resulting one first frame.
"""
    import torch
    import comfy.model_management as mm
    from PIL import Image

    settings = normalize_settings(settings)
    _check_weights(settings)
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("分镜图片提示词为空，请先完成分镜规划。")
    width, height = int(width), int(height)
    if not 64 <= width <= 4096 or not 64 <= height <= 4096 or width % 32 or height % 32:
        raise ValueError("Qwen2.1 图像宽高应为64～4096的32倍数。")
    if not isinstance(storyboard, bool):
        raise ValueError("分镜生图执行标记无效。")
    if storyboard:
        width, height = storyboard_canvas(width, height, settings["storyboard_long_edge"])
    paths = [str(reference_path)] if reference_path else []
    for filename in reference_paths or []:
        if str(filename) not in paths:
            paths.append(str(filename))
    if not paths and not text_only:
        raise ValueError("请至少提供一张原始参考图。")
    if type(init_from_reference) is not bool or not isinstance(negative_prompt, str):
        raise ValueError("图像修复采样参数无效。")
    if init_from_reference and not paths:
        raise ValueError("低降噪修复需要输入原图。")
    if len(paths) > MAX_REFERENCES:
        raise ValueError(f"Qwen2.1 每镜最多接入{MAX_REFERENCES}张参考图，请减少本镜引用。")
    encoded_prompt = reference_role_prompt(prompt, len(paths), reference_roles) if paths else prompt
    handles = {}
    pixels = None
    try:
        mm.throw_exception_if_processing_interrupted()
        _notify_progress(progress_callback, "loading")
        if settings["model"].lower().endswith(".gguf"):
            handles["model"] = _class("UnetLoaderGGUF")().load_unet(settings["model"])[0]
        else:
            handles["model"] = nodes.UNETLoader().load_unet(settings["model"], "default")[0]
        handles["model"] = _outputs(_class("QwenImage21Cache").execute(
            model=handles["model"], device=settings["cache_device"], dtype=settings["cache_dtype"]))[0]
        handles["clip"] = nodes.CLIPLoader().load_clip(settings["clip"], "qwen_image", settings["clip_device"])[0]
        handles["vae"] = nodes.VAELoader().load_vae(settings["vae"])[0]
        _notify_progress(progress_callback, "encoding")
        reference_edge = (settings["reference_long_edge"] if (storyboard or init_from_reference) and settings["reference_long_edge"] else
                          min(2048, max(1024, width, height)))
        primary = _read_image(paths[0], reference_edge) if paths else None
        handles["images"] = {"image_1": primary} if paths else {}
        structural_slots = set()
        structure_edge = reference_edge
        if primary is not None and storyboard and settings["previous_reference_policy"] == PREVIOUS_REFERENCE_POLICIES[1]:
            structural_slots = _structure_reference_slots(reference_roles)
            # Budget the previous generated frame below the original source's
            # actual encoded edge, so it contributes layout rather than a
            # recursively accumulated surface-detail standard. Never alter a
            # primary/additional alias or any non-storyboard (tail-repair) call.
            structure_edge = max(64, min(384, (max(primary.shape[1:3]) // 2) // 32 * 32))
        for slot, filename in enumerate(paths[1:], 2):
            handles["images"][f"image_{slot}"] = _read_image(
                filename, structure_edge if slot in structural_slots else reference_edge)
        reference_receipt = []
        for slot in range(1, len(paths)+1):
            tensor = handles["images"][f"image_{slot}"]
            image_height, image_width = map(int, tensor.shape[1:3])
            aliases = [entry for entry in (reference_roles or []) if entry["slot"] == slot]
            role_names = list(dict.fromkeys(entry["role"] for entry in aliases)) or ["primary" if slot == 1 else "additional"]
            source_ids = list(dict.fromkeys(entry["source_id"] for entry in aliases))
            reference_receipt.append({"slot": slot, "roles": role_names, "source_ids": source_ids,
                                      "width": image_width, "height": image_height,
                                      "aligned_32": image_width % 32 == 0 and image_height % 32 == 0,
                                      "encoder_width": max(32, round(image_width / 32) * 32),
                                      "encoder_height": max(32, round(image_height / 32) * 32),
                                      "structural": slot in structural_slots})
        _notify_progress(progress_callback, "reference_receipt", reference_receipt={
            "policy": settings["previous_reference_policy"] if storyboard else PREVIOUS_REFERENCE_POLICIES[0],
            "storyboard": storyboard, "width": width, "height": height,
            "canvas_aligned_32": width % 32 == 0 and height % 32 == 0,
            "references": reference_receipt})
        handles["encoded"] = _outputs(_class("TextEncodeQwenImage21").execute(
            clip=handles["clip"], prompt=encoded_prompt, negative_prompt=negative_prompt,
            vae=handles["vae"], resolution=0, images=handles["images"]))
        # Qwen2.1's 64-channel latent format comes from its own encoder; use
        # the globally selected canvas rather than EmptyLatentImage's SD shape.
        if init_from_reference:
            # A low-denoise repair must start from the input pixels. A zero latent
            # instead regenerates texture and contrast, even if denoise is reduced.
            from comfy.utils import common_upscale
            initial = primary[:, :, :, :3]
            if tuple(initial.shape[1:3]) != (height, width):
                initial = common_upscale(initial.movedim(-1, 1), width, height,
                                         'lanczos', 'disabled').movedim(1, -1)
            handles["latent"] = nodes.VAEEncode().encode(handles["vae"], initial)[0]
            del initial
        else:
            handles["latent"] = {"samples": torch.zeros(
                (1, 64, height // 16, width // 16), device=mm.intermediate_device())}
        _notify_progress(progress_callback, "sampling")
        # Keep the original node method so LC123's encoder-unload wrapper and
        # other installed sampler behavior remain in use for ordinary weights.
        # Turbo weights use their official distilled schedule when selected.
        from .image_sampling import sample_image2
        with _sampling_progress(progress_callback):
            handles["sample"] = sample_image2(
                model=handles["model"], seed=int(seed), steps=settings["steps"], cfg=settings["cfg"],
                sampler=settings["sampler"], scheduler=settings["scheduler"], positive=handles["encoded"][0],
                negative=handles["encoded"][1], latent=handles["latent"], denoise=settings["denoise"],
                model_name=settings["model"])[0]
        _notify_progress(progress_callback, "decoding")
        handles["decoded"] = nodes.VAEDecode().decode(handles["vae"], handles["sample"])[0]
        pixels = (handles["decoded"][0].detach().to(device="cpu", dtype=torch.float32)
                  .clamp(0, 1).mul(255).round().to(torch.uint8).numpy())
        directory = Path(folder_paths.get_output_directory()) / "H3分镜" / "分镜参考图"
        directory.mkdir(parents=True, exist_ok=True)
        output = (directory / f"分镜_{uuid.uuid4().hex[:16]}.png").resolve()
        Image.fromarray(pixels[..., :3], "RGB").save(output)
        _notify_progress(progress_callback, "completed")
        return str(output)
    finally:
        # No model, reference batch, decoded tensor or prefix-cache clone is
        # retained in this module or in ComfyUI's execution output cache.
        pixels = None
        handles.clear()
        release_resources(full=False)
