"""Asset-library generation through the real, loopback ComfyUI queue.

Building a workflow never queues inference. Qwen receives independent ordered
references; MOSS redraws a single contact sheet, not native multi-image
instruction editing. Model availability is checked without loading weights.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
import copy
import hashlib
import inspect
import io
import json
import math
from pathlib import Path, PurePosixPath
import re
import secrets
import sys
import time
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

import aiohttp

try:
    from .job_store import ACTIVE, FINAL, JobStore, from_timestamp, infer_settings, new_job, now_iso, owned_prompt, public_job
except ImportError:
    # Standalone service imports this file outside a ComfyUI package.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from job_store import ACTIVE, FINAL, JobStore, from_timestamp, infer_settings, new_job, now_iso, owned_prompt, public_job

ROOT = Path(__file__).resolve().parent
VERSION = "1.2.1"
RATIOS = ("1:1", "1:2", "2:1", "9:16", "16:9", "3:4", "4:3", "3:2", "2:3", "5:4", "4:5", "21:9", "9:21")
RESOLUTIONS = {"1K": 1024, "2K": 2048, "4K": 4096}
TURBO_SIGMAS = "1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0"
QWEN_ENGINES = {"qwen", "qwen_turbo8"}
MODEL_SETTINGS = {
    "qwen": {"model": "qwen-image-2.1-UC-Q6_K.gguf", "clip": "qwen3vl_8b_int8_convrot.safetensors",
             "vae": "qwen_image_2.1_vae_bf16.safetensors", "steps": 25, "cfg": 1.0,
             "sampler": "euler", "scheduler": "simple"},
    "qwen_turbo8": {"model": "qwen_image_2.1_turbo_int8_convrot.safetensors", "clip": "qwen3vl_8b_int8_convrot.safetensors",
                    "vae": "qwen_image_2.1_vae_bf16.safetensors", "steps": 8, "cfg": 1.0,
                    "sampler": "euler", "scheduler": "manual_sigmas"},
    "moss": {"model": "jibMixMossRealistic_v2RealityRenew.safetensors", "clip": "qwen3.5_9b_bf16.safetensors",
             "vae": "flux2-vae_small.safetensors", "steps": 12, "cfg": 1.0,
             "sampler": "er_sde", "scheduler": "bong_tangent"},
}
ENGINE_INFO = {
    "qwen": {"name": "千问 2.1 普通 · 25步", "max_references": 16, "reference_mode": "individual",
             "reference_note": "参考图独立按顺序输入，图1、图2对应当前排列顺序。",
             "pipeline_source": "（越狱版）Qwen image 2.1 多图片编辑模型_Codex_V1.1.0.json",
             "reference_resolution": 0,
             "presets": [{"id": "default", "name": "标准 25 步"}]},
    "qwen_turbo8": {"name": "千问 2.1 Turbo · 8步", "max_references": 16, "reference_mode": "individual",
                    "reference_note": "独立Turbo模型，采用现有工作流的8步噪声日程；参考图独立按顺序输入。",
                    "presets": [{"id": "default", "name": "Turbo · 8 步"}]},
    "moss": {"name": "MOSS Image", "max_references": 8, "reference_mode": "contact_sheet",
             "reference_note": "多张素材依次拼成一张参考图后重绘；适合组合草图，可能保留分区布局。",
             "presets": [{"id": "default", "name": "Jib V2 · 12 步"}, {"id": "official", "name": "官方 SFT · 50 步"}]},
}


class GenerationError(ValueError):
    def __init__(self, message, code="generation_error", details=None):
        super().__init__(message)
        self.code, self.details = code, details or {}


def dimensions(resolution="2K", ratio="1:1", width=None, height=None):
    """K is the longest edge; align the other side to the nearest 32px."""
    if resolution not in RESOLUTIONS or ratio not in RATIOS:
        raise GenerationError("请选择1K、2K或4K及有效画幅比例。")
    if (width is None) != (height is None):
        raise GenerationError("自定义尺寸须同时提供宽度和高度。")
    if width is None:
        x, y = map(int, ratio.split(":"))
        side = RESOLUTIONS[resolution]
        width = max(256, int(math.floor(side * x / max(x, y) / 32 + 0.5)) * 32)
        height = max(256, int(math.floor(side * y / max(x, y) / 32 + 0.5)) * 32)
    if any(type(value) is not int or not 256 <= value <= 4096 or value % 32 for value in (width, height)):
        raise GenerationError("宽高须为256～4096之间的32倍数。")
    return {"resolution": resolution, "ratio": ratio, "width": width, "height": height,
            "pixels": width * height, "alignment": 32, "basis": "long_edge"}


def normalize(payload, cloud_model=None):
    if not isinstance(payload, dict):
        raise GenerationError("生成参数必须为对象。")
    engine = payload.get("engine", "qwen")
    is_cloud = isinstance(engine, str) and engine.startswith("bbapi:")
    if engine not in ENGINE_INFO and not (is_cloud and cloud_model and engine == "bbapi:" + cloud_model["id"]):
        raise GenerationError("请选择千问 Image 2.1 或 MOSS Image。")
    engine_info = cloud_model if is_cloud else ENGINE_INFO[engine]
    text = payload.get("prompt", "")
    if not isinstance(text, str) or not text.strip() or len(text) > 20000:
        raise GenerationError("请填写不超过20000字的提示词。")
    original_prompt = payload.get("original_prompt", text)
    if not isinstance(original_prompt, str) or len(original_prompt) > 20000:
        raise GenerationError("原始提示词须为不超过20000字的文本。")
    prompt_preset = payload.get("prompt_preset", "")
    if not isinstance(prompt_preset, str):
        raise GenerationError("提示词预设编号无效。")
    detail_level = payload.get("detail_level", 3)
    if type(detail_level) is not int or not 1 <= detail_level <= 5:
        raise GenerationError("详细程度须为1～5。")
    background = payload.get("background", "auto")
    if background not in ("auto", "preserve"):
        raise GenerationError("请选择自动背景或保留背景。")
    ids = payload.get("asset_ids", [])
    if not isinstance(ids, list) or any(not isinstance(item, str) or len(item) > 200 for item in ids):
        raise GenerationError("素材编号必须为有序列表。")
    if len(ids) > engine_info["max_references"]:
        raise GenerationError(f"当前引擎最多使用{engine_info['max_references']}张素材。")
    roles = payload.get("asset_roles") or {}
    if not isinstance(roles, dict):
        raise GenerationError("素材角色映射格式无效。")
    def role_token(match):
        role = match.group(1)
        value = roles.get(role)
        slot = ids.index(value) + 1 if isinstance(value, str) and value in ids else value
        if type(slot) is not int or not 1 <= slot <= len(ids):
            raise GenerationError(f"提示词中的@{role}尚未绑定素材，请先选择对应素材。")
        return f"<image{slot}>"
    text = re.sub(r"@(形象|角色|服装|姿态|场景|道具)", role_token, text)
    for token in re.findall(r"(?:<image|@图|参考图)(\d+)>?", text, re.I):
        if not 1 <= int(token) <= len(ids):
            raise GenerationError(f"提示词引用了未选择的图{token}。")
    preset = payload.get("model_preset") or payload.get("preset") or "default"
    if preset == "jib":
        preset = "default"
    if preset not in ({"default"} if is_cloud else {entry["id"] for entry in ENGINE_INFO[engine]["presets"]}):
        raise GenerationError("当前引擎没有此预设。")
    seed_mode = payload.get("seed_mode", "fixed" if type(payload.get("seed")) is int else "random")
    if seed_mode not in ("random", "fixed"):
        raise GenerationError("请选择随机种子或固定种子。")
    seed = payload.get("seed")
    if seed_mode == "random" or seed is None or seed == "random":
        seed = secrets.randbits(53)
    if type(seed) is not int or not 0 <= seed <= 0xffffffffffffffff:
        raise GenerationError("种子须为0至18446744073709551615的整数。")
    size = dimensions(payload.get("resolution", "2K"), payload.get("ratio", "3:4"), payload.get("width"), payload.get("height"))
    save_output = payload.get("save_output", True)
    if type(save_output) is not bool:
        raise GenerationError("保存输出须为开关值。")
    auto_open_folder = payload.get("auto_open_folder", False)
    if type(auto_open_folder) is not bool:
        raise GenerationError("完成后定位文件夹须为开关值。")
    loras = [] if is_cloud else payload.get("lora_slots", [{"name": "", "strength": 1.0, "steps": 8, "cfg": 1.0, "enabled": False} for _ in range(5)])
    if not isinstance(loras, list) or len(loras) > 20:
        raise GenerationError("LoRA槽位最多20个。")
    slots = []
    for slot in loras:
        if not isinstance(slot, dict) or not isinstance(slot.get("name", ""), str) or len(slot.get("name", "")) > 1000 or type(slot.get("enabled", False)) is not bool:
            raise GenerationError("LoRA槽位格式无效。")
        strength = slot.get("strength", 1.0)
        if isinstance(strength, bool) or not isinstance(strength, (int, float)) or not math.isfinite(strength) or not -2 <= strength <= 2:
            raise GenerationError("LoRA强度须为-2～2。")
        slot_steps, slot_cfg = slot.get("steps", 8), slot.get("cfg", 1.0)
        if type(slot_steps) is not int or not 1 <= slot_steps <= 100:
            raise GenerationError("每项LoRA细化步数须为1～100的整数。")
        if isinstance(slot_cfg, bool) or not isinstance(slot_cfg, (int, float)) or not math.isfinite(slot_cfg) or not 0 <= slot_cfg <= 20:
            raise GenerationError("每项LoRA细化CFG须为0～20。")
        slots.append({"name": slot.get("name", ""), "strength": float(strength), "steps": slot_steps,
                      "cfg": float(slot_cfg), "enabled": slot.get("enabled", False)})
    if is_cloud:
        if size["resolution"] not in engine_info["resolutions"] or size["ratio"] not in engine_info["ratios"]:
            raise GenerationError("此云端通道不支持当前分辨率或比例。")
        steps, cfg = None, None
    else:
        base = dict(MODEL_SETTINGS[engine])
        if engine == "moss" and preset == "official":
            base.update(steps=50, cfg=3.0)
        steps, cfg = payload.get("steps", base["steps"]), payload.get("cfg", base["cfg"])
        if type(steps) is not int or not 1 <= steps <= 100:
            raise GenerationError("采样步数须为1～100的整数。")
        if isinstance(cfg, bool) or not isinstance(cfg, (int, float)) or not math.isfinite(cfg) or not 0 <= cfg <= 20:
            raise GenerationError("CFG须为0～20。")
        if engine == "qwen_turbo8" and steps != 8:
            raise GenerationError("当前Turbo使用已验证的固定8步日程，步数须为8。")
    role_ids = {}
    for role, value in roles.items():
        if isinstance(value, str) and value in ids:
            role_ids[role] = value
        elif type(value) is int and 1 <= value <= len(ids):
            role_ids[role] = ids[value - 1]
    name = payload.get("name")
    if name is not None and (not isinstance(name, str) or len(name) > 100):
        raise GenerationError("任务名称不能超过100字。")
    external_session = payload.get("external_session")
    if external_session is not None and (not isinstance(external_session, str) or len(external_session) > 128):
        raise GenerationError("外接图片会话编号无效。")
    return {"engine": engine, "prompt": text.strip(), "asset_ids": ids[:], "preset": preset,
            "original_prompt": original_prompt, "prompt_preset": prompt_preset,
            "detail_level": detail_level, "background": background,
            "asset_roles": role_ids, "seed": seed, "save_output": save_output,
            "seed_mode": seed_mode,
            "auto_open_folder": auto_open_folder and save_output,
            "lora_slots": slots, "steps": steps, "cfg": cfg,
            **({"name": name.strip()} if name else {}),
            **({"external_session": external_session} if external_session else {}),
            **{key: size[key] for key in ("resolution", "ratio", "width", "height")}}


def model_settings(settings):
    result = dict(MODEL_SETTINGS[settings["engine"]])
    if settings["engine"] == "moss" and settings["preset"] == "official":
        result.update(model="moss_image_sft_bf16.safetensors", steps=50, cfg=3.0,
                      sampler="euler", scheduler="sgm_uniform")
    result.update({key: settings[key] for key in ("steps", "cfg") if settings.get(key) is not None})
    return result


def input_name(reference):
    filename = reference.get("filename") or reference.get("name")
    subfolder = str(reference.get("subfolder", "")).replace("\\", "/")
    if not isinstance(filename, str) or not filename or "/" in filename or "\\" in filename:
        raise GenerationError("ComfyUI参考图片文件名无效。")
    path = PurePosixPath(subfolder) / filename
    if path.is_absolute() or ":" in str(path) or ".." in path.parts or reference.get("type", "input") != "input":
        raise GenerationError("仅允许 ComfyUI 输入目录内的参考图片。")
    return path.as_posix()


def reference_prompt(text, count, contact_sheet=False):
    pattern = r"(?:<image(\d+)>|@图(\d+)|参考图(\d+))"
    def replace(match):
        number = next(part for part in match.groups() if part is not None)
        return (f"参考拼图第{number}区" if count > 1 else "输入图像") if contact_sheet else f"<image{number}>"
    text = re.sub(pattern, replace, text, flags=re.I)
    if contact_sheet and count > 1:
        return (f"输入图像是{count}个参考分区，按从左到右、从上到下依次编号。"
                "请将所需元素组合成一张完整图像，避免保留拼图边框和分区。\n" + text)
    return text


def api_prompt(settings, references=(), source_count=None):
    """Pure graph builder. References are validated ComfyUI input descriptors."""
    engine, config = settings["engine"], model_settings(settings)
    count = len(references) if source_count is None else source_count
    mode = "图生图" if references else "文生图"
    is_qwen = engine in QWEN_ENGINES
    prompt = reference_prompt(settings["prompt"], count, not is_qwen)
    graph = {}
    def add(kind, title, **inputs):
        identifier = str(len(graph) + 1)
        graph[identifier] = {"class_type": kind, "inputs": inputs, "_meta": {"title": title}}
        return [identifier, 0]
    mode_node = add("PrimitiveStringMultiline", "生成模式", value=mode) if is_qwen and not (engine == "qwen" and references) else None
    image_nodes = [add("LoadImage", f"参考图 {i}", image=input_name(item)) for i, item in enumerate(references, 1)]
    refs = None
    if image_nodes and is_qwen:
        refs = add("CodexReferenceInputs", "有序参考图", 启用参考图数量=len(image_nodes), 对比参考图编号=1,
                   **{f"参考图{i}": link for i, link in enumerate(image_nodes, 1)})
    width, height = settings["width"], settings["height"]
    if engine in ENGINE_INFO:
        loader = "UnetLoaderGGUF" if engine == "qwen" else "UNETLoader"
        loader_inputs = {"unet_name": config["model"]}
        if engine != "qwen":
            loader_inputs["weight_dtype"] = "default"
        model = add(loader, "图像主模型", **loader_inputs)
        clip = add("CLIPLoader", "文本编码器", clip_name=config["clip"],
                   type="qwen_image" if is_qwen else "moss_image", device="default")
        vae = add("VAELoader", "图像 VAE", vae_name=config["vae"])
        if is_qwen:
            values = dict(clip=clip, vae=vae, prompt=prompt, negative_prompt="", resolution=0)
            if refs:
                values["参考图集"] = refs
            if engine == "qwen" and refs:
                # Exact active encoder from the mature multi-image-edit subgraph.
                positive = add("CodexQwenReferenceEncoder", "千问普通版 · 原版多图编码", **values)
            else:
                values["生成模式"] = mode_node
                positive = add("CodexQwenStudioEncoder", "千问独立多图编码", **values)
            negative = [positive[0], 1]
            latent = add("CodexQwenCanvasLatent", "生成尺寸", width=width, height=height, batch_size=1)
        else:
            model = add("ModelSamplingAuraFlow", "MOSS Shift 5", model=model, shift=5.0, sampling="flow")
            positive = add("CLIPTextEncode", "生成提示词", text=prompt, clip=clip)
            negative = add("CLIPTextEncode", "负面提示词", text="", clip=clip)
            if image_nodes:
                scaled = add("ImageScale", "参考图适配画布", image=image_nodes[0], upscale_method="lanczos",
                             width=width, height=height, crop="disabled")
                latent = add("VAEEncode", "参考图潜空间", pixels=scaled, vae=vae)
            else:
                latent = add("EmptyFlux2LatentImage", "生成尺寸", width=width, height=height, batch_size=1)
        if is_qwen:
            model = add("QwenImage21Cache", "千问前缀缓存", model=model, device="auto", dtype="default")
        if engine == "qwen_turbo8":
            noise = add("RandomNoise", "随机种子", noise_seed=settings["seed"])
            guider = add("CFGGuider", "提示词引导", model=model, positive=positive, negative=negative, cfg=config["cfg"])
            sampler = add("KSamplerSelect", "Turbo 采样器 · Euler", sampler_name="euler")
            sigmas = add("ManualSigmas", "Turbo 固定8步日程", sigmas=TURBO_SIGMAS)
            samples = add("SamplerCustomAdvanced", "Turbo 8步采样", noise=noise, guider=guider,
                          sampler=sampler, sigmas=sigmas, latent_image=latent)
        else:
            samples = add("KSampler", "图像采样", model=model, seed=settings["seed"], steps=config["steps"],
                          cfg=config["cfg"], sampler_name=config["sampler"], scheduler=config["scheduler"],
                          positive=positive, negative=negative, latent_image=latent,
                          denoise=0.65 if engine == "moss" and image_nodes else 1.0)
        # Base generation stays unpatched. Each enabled slot adds one real,
        # ordered latent-refinement pass with its own steps/CFG; model patches
        # accumulate in the explicitly selected slot order.
        for index, slot in enumerate(settings.get("lora_slots", []), 1):
            if not slot["enabled"]:
                continue
            model = add("LoraLoaderModelOnly", f"LoRA {index}", model=model, lora_name=slot["name"], strength_model=slot["strength"])
            stage_seed = (settings["seed"] + index) % (1 << 53)
            stage_steps, stage_cfg = slot.get("steps", 8), slot.get("cfg", 1.0)
            if engine == "qwen_turbo8":
                # Same resampled img2img tail as mature image_sampling.py:
                # full_steps=int(steps/denoise), retain only the final steps.
                curve = [float(value) for value in TURBO_SIGMAS.split(",")]
                full_steps = stage_steps * 4
                tail = []
                for position in range(full_steps - stage_steps, full_steps):
                    location = position * (len(curve) - 1) / full_steps
                    left = min(int(location), len(curve) - 2)
                    tail.append(curve[left] + (curve[left + 1] - curve[left]) * (location - left))
                tail.append(0.0)
                noise = add("RandomNoise", f"LoRA {index}细化种子", noise_seed=stage_seed)
                guider = add("CFGGuider", f"LoRA {index}细化引导", model=model, positive=positive, negative=negative, cfg=stage_cfg)
                sampler = add("KSamplerSelect", f"LoRA {index}细化采样器", sampler_name=config["sampler"])
                sigmas = add("ManualSigmas", f"LoRA {index}细化日程", sigmas=", ".join(format(value, ".12g") for value in tail))
                samples = add("SamplerCustomAdvanced", f"LoRA {index}独立细化 · {stage_steps}步", noise=noise,
                              guider=guider, sampler=sampler, sigmas=sigmas, latent_image=samples)
            else:
                samples = add("KSampler", f"LoRA {index}独立细化 · {stage_steps}步", model=model,
                              seed=stage_seed, steps=stage_steps, cfg=stage_cfg, sampler_name=config["sampler"],
                              scheduler=config["scheduler"], positive=positive, negative=negative,
                              latent_image=samples, denoise=0.25)
        decoded = add("VAEDecode", "生成图像", samples=samples, vae=vae)
    if settings.get("save_output", True):
        add("SaveImage", "保存生成结果", images=decoded, filename_prefix=f"资产库/{engine}")
    else:
        add("PreviewImage", "预览生成结果", images=decoded)
    return graph


def cloud_api_prompt(settings, references, provider_config):
    """Use registered BBAPI queue nodes; credentials remain in DPAPI profiles."""
    model = settings["engine"].removeprefix("bbapi:")
    graph = {}
    def add(kind, title, **inputs):
        identifier = str(len(graph) + 1)
        graph[identifier] = {"class_type": kind, "inputs": inputs, "_meta": {"title": title}}
        return [identifier, 0]
    connection = add("BBAPIConnection", "BBAPI 本机连接", 配置名称=provider_config["profile"],
                     接口地址=provider_config["base_url"], 默认模型=model, 超时秒=300)
    images = [add("LoadImage", f"参考图 {index}", image=input_name(item)) for index, item in enumerate(references, 1)]
    values = {"连接": connection, "模型": model, "提示词": re.sub(r"<image(\d+)>", lambda match: "参考图" + match.group(1),
                reference_prompt(settings["prompt"], len(references))),
              "分辨率": settings["resolution"], "比例": settings["ratio"], "批量数量": 1, "并发上限": 1,
              "质量": "省费", "输出模式": "预览（不保存）", "重新生成编号": settings["seed"] % 2147483647}
    if images:
        refs = add("BBAPIReferenceInputs", "云端有序参考图", 参考图数量=len(images),
                   **{f"参考图{index}": link for index, link in enumerate(images, 1)})
        values["参考图"] = refs
    generated = add("BBAPIMultiReference" if images else "BBAPIPromptToImage", "BBAPI 云端生成", **values)
    first_image = [generated[0], 2]
    if settings["save_output"]:
        prefix = "资产库/bbapi_" + re.sub(r"[^A-Za-z0-9_.-]", "_", model)
        add("SaveImage", "保存生成结果", images=first_image, filename_prefix=prefix)
    else:
        add("PreviewImage", "预览生成结果", images=first_image)
    return graph


def canvas_workflow(graph, schemas):
    """Create an ordinary ComfyUI canvas with real ports/widgets and links."""
    nodes, links, port_indices = [], [], {}
    for identifier, item in graph.items():
        schema = schemas.get(item["class_type"], {})
        inputs, widgets = [], []
        specs = {**schema.get("input", {}).get("required", {}), **schema.get("input", {}).get("optional", {})}
        for name, spec in specs.items():
            if name not in item["inputs"]:
                continue
            value = item["inputs"][name]
            options = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            primitive = isinstance(spec[0], list) or spec[0] in ("STRING", "INT", "FLOAT", "BOOLEAN", "COMBO")
            connected = isinstance(value, list) and len(value) == 2 and str(value[0]) in graph
            if connected or not primitive or options.get("forceInput"):
                port_indices[(identifier, name)] = len(inputs)
                port = {"name": name, "type": "COMBO" if isinstance(spec[0], list) else spec[0], "link": None}
                if primitive and not options.get("forceInput"):
                    port["widget"] = {"name": name}
                inputs.append(port)
            if primitive and not options.get("forceInput"):
                choices = spec[0] if isinstance(spec[0], list) else options.get("options", [])
                default = options.get("default", choices[0] if choices else "")
                widgets.append(default if connected else value)
                if options.get("control_after_generate"):
                    widgets.append("fixed")
        if item["class_type"] == "LoadImage":
            widgets.append("image")
        outputs = [{"name": name, "type": kind, "links": []}
                   for name, kind in zip(schema.get("output_name", schema.get("output", [])), schema.get("output", []))]
        index = len(nodes)
        nodes.append({"id": int(identifier), "type": item["class_type"], "title": item.get("_meta", {}).get("title", ""),
                      "pos": [(index // 4) * 470, (index % 4) * 450], "size": [420, max(280, 60 + len(inputs) * 22 + len(widgets) * 26)], "flags": {},
                      "order": index, "mode": 0, "inputs": inputs, "outputs": outputs,
                      "properties": {"Node name for S&R": item["class_type"]}, "widgets_values": widgets})
    by_id = {str(node["id"]): node for node in nodes}
    for identifier, item in graph.items():
        for name, value in item["inputs"].items():
            if not isinstance(value, list) or len(value) != 2 or str(value[0]) not in graph:
                continue
            origin, output = str(value[0]), value[1]
            slot = port_indices[(identifier, name)]
            link = len(links) + 1
            kind = by_id[origin]["outputs"][output]["type"]
            links.append([link, int(origin), output, int(identifier), slot, kind])
            by_id[origin]["outputs"][output]["links"].append(link)
            by_id[identifier]["inputs"][slot]["link"] = link
    return {"last_node_id": len(nodes), "last_link_id": len(links), "nodes": nodes, "links": links,
            "groups": [], "config": {}, "extra": {"asset_library_version": VERSION,
            "ds": {"scale": 0.65, "offset": [60, 60]}}, "version": 0.4}


def image_bytes(path, max_long_edge=None):
    from PIL import Image, ImageOps
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
        raise GenerationError("参考图片不存在或超过64MB。")
    with Image.open(path) as source:
        if source.width * source.height > 80_000_000:
            raise GenerationError("参考图片超过8000万像素，请先缩小。")
        rgba = ImageOps.exif_transpose(source).convert("RGBA")
        background = Image.new("RGBA", rgba.size, "white")
        background.alpha_composite(rgba)
        image = background.convert("RGB")
        if max_long_edge:
            image.thumbnail((max_long_edge, max_long_edge), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def contact_sheet(images):
    from PIL import Image, ImageDraw, ImageOps
    columns = math.ceil(math.sqrt(len(images)))
    rows, cell = math.ceil(len(images) / columns), 512
    sheet = Image.new("RGB", (columns * cell, rows * cell), (232, 232, 232))
    draw = ImageDraw.Draw(sheet)
    for index, data in enumerate(images):
        with Image.open(io.BytesIO(data)) as source:
            image = ImageOps.contain(source.convert("RGB"), (cell - 24, cell - 48), Image.Resampling.LANCZOS)
        x, y = (index % columns) * cell, (index // columns) * cell
        sheet.paste(image, (x + (cell - image.width) // 2, y + 32 + (cell - 40 - image.height) // 2))
        draw.text((x + 12, y + 10), str(index + 1), fill=(48, 48, 48))
    output = io.BytesIO()
    sheet.save(output, "PNG")
    return output.getvalue()


class GenerationService:
    def __init__(self, resolver=None, base_url="http://127.0.0.1:8000", schema_path=None, data_dir=None, on_completed=None, provider=None):
        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1") or parsed.path not in ("", "/"):
            raise ValueError("ComfyUI地址必须为本机HTTP地址。")
        self.resolver, self.base_url = resolver, base_url.rstrip("/")
        self.schemas = json.loads(Path(schema_path or ROOT / "workflows" / "node-schema.json").read_text(encoding="utf-8"))
        self.live_schemas, self.checked_at, self.online = {}, 0, False
        self.store = JobStore(data_dir)
        self.jobs, self.client_id = self.store.jobs, self.store.client_id
        self._reference_lock = asyncio.Lock()
        self._status_lock = asyncio.Lock()
        self._lora_lock = asyncio.Lock()
        self._lora_schema, self._lora_names = None, None
        self._lora_checked_at, self._lora_error = 0, None
        self._jobs_lock, self._save_lock = asyncio.Lock(), asyncio.Lock()
        self._history_recovered, self._last_sync = False, 0
        self._events, self._sequence = deque(maxlen=256), 0
        self._monitor_task, self._ws_tasks = None, {}
        self._ws_ready, self._early_events = {}, deque(maxlen=64)
        self._closed, self._dirty = False, False
        self._pending_submissions = 0
        self.on_completed = on_completed
        self.provider = provider
        self._pending_notifications, self._notification_task = OrderedDict(), None
        for job in self.jobs.values():
            job["_notify_on_completion"] = job.get("status") in ACTIVE

    async def _request(self, method, endpoint, **kwargs):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25), trust_env=False) as session:
                async with session.request(method, self.base_url + endpoint, **kwargs) as response:
                    body = await response.text()
                    try:
                        data = json.loads(body) if body else {}
                    except ValueError:
                        data = {}
                    if response.status >= 400:
                        error = data.get("error") or f"ComfyUI请求失败（{response.status}）。"
                        if isinstance(error, dict):
                            error = error.get("message") or error.get("details") or "工作流校验失败。"
                        raise GenerationError(str(error), "comfy_error", {"node_errors": data.get("node_errors", {})})
                    return data
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            raise GenerationError("无法连接 ComfyUI，请确认本机8000服务已就绪。", "comfy_offline") from error

    async def refresh(self, force=False):
        async with self._status_lock:
            if not force and time.monotonic() - self.checked_at < 20:
                return
            semaphore, collected = asyncio.Semaphore(6), {}
            async def fetch(name):
                async with semaphore:
                    try:
                        result = await self._request("GET", "/object_info/" + name, timeout=aiohttp.ClientTimeout(total=3.8))
                        if result.get(name):
                            collected[name] = result[name]
                    except GenerationError:
                        pass
            try:
                await asyncio.wait_for(asyncio.gather(*(fetch(name) for name in self.schemas)), timeout=4.2)
            except asyncio.TimeoutError:
                pass
            if self._lora_schema is not None:
                collected["LoraLoaderModelOnly"] = self._lora_schema
            self.live_schemas = collected
            self.online, self.checked_at = bool(collected), time.monotonic()

    def _missing(self, settings, graph):
        result = []
        if not self.online:
            return ["ComfyUI服务尚未连接"]
        for name in sorted({node["class_type"] for node in graph.values()}):
            if name not in self.live_schemas:
                result.append("节点未就绪或未响应：" + name)
        if settings["engine"].startswith("bbapi:"):
            if not self.provider or not self.provider.public_config()["configured"]:
                result.append("BBAPI密钥尚未配置")
            elif self.provider.public_config()["connection_status"] != "connected":
                result.append("BBAPI连接尚未通过测试，请先读取模型列表。")
            return result
        config = model_settings(settings)
        checks = [("UnetLoaderGGUF" if settings["engine"] == "qwen" else "UNETLoader", "unet_name", config["model"]),
                  ("CLIPLoader", "clip_name", config["clip"]), ("VAELoader", "vae_name", config["vae"]),
                  ("CLIPLoader", "type", "qwen_image" if settings["engine"] in QWEN_ENGINES else "moss_image"),
                  ("KSampler", "sampler_name", config["sampler"])]
        if settings["engine"] != "qwen_turbo8":
            checks.append(("KSampler", "scheduler", config["scheduler"]))
        for node, field, value in checks:
            spec = self.live_schemas.get(node, {}).get("input", {}).get("required", {}).get(field)
            choices = spec[0] if spec and isinstance(spec[0], list) else spec[1].get("options") if spec and len(spec) > 1 and spec[0] == "COMBO" else None
            if not spec or choices is not None and value not in choices:
                result.append(f"{field}：{value}")
        if settings["engine"] in QWEN_ENGINES and "TextEncodeQwenImage21" not in self.live_schemas:
            result.append("节点：TextEncodeQwenImage21")
        return result

    async def engines(self):
        await self.refresh()
        engines = []
        for engine, info in ENGINE_INFO.items():
            settings = normalize({"engine": engine, "prompt": "模型就绪检查"})
            graph = api_prompt(settings)
            missing = self._missing(settings, graph)
            config = model_settings(settings)
            engines.append({"id": engine, **copy.deepcopy(info), "model_name": config["model"],
                            "steps": config["steps"], "sampler": config["sampler"], "scheduler": config["scheduler"],
                            "ready": not missing,
                            "missing": missing, "template": engine + ".json"})
        if self.provider:
            for info in self.provider.engines():
                missing = []
                for name in ("BBAPIConnection", "BBAPIReferenceInputs", "BBAPIMultiReference", "BBAPIPromptToImage"):
                    if name not in self.live_schemas:
                        missing.append("节点未就绪或未响应：" + name)
                engines.append({**info, "ready": info["ready"] and not missing, "missing": info["missing"] + missing})
        return {"engines": engines, "online": self.online}

    async def model_config(self):
        engines, loras = await asyncio.gather(self.engines(), self._refresh_loras(force=True))
        return {**engines, "loras": loras, "loras_stale": self._lora_error is not None,
                "loras_error": self._lora_error,
                "defaults": {engine: {"steps": config["steps"], "cfg": config["cfg"], "steps_locked": engine == "qwen_turbo8"}
                             for engine, config in MODEL_SETTINGS.items()}, "max_slots": 20, "initial_slots": 5}

    async def _refresh_loras(self, force=False):
        async with self._lora_lock:
            if not force and self._lora_names is not None and time.monotonic() - self._lora_checked_at < 15:
                return list(self._lora_names)
            try:
                result = await asyncio.wait_for(self._request("GET", "/object_info/LoraLoaderModelOnly",
                                               timeout=aiohttp.ClientTimeout(total=3.8)), timeout=4)
                schema = result.get("LoraLoaderModelOnly")
                spec = schema.get("input", {}).get("required", {}).get("lora_name") if isinstance(schema, dict) else None
                choices = spec[0] if spec and isinstance(spec[0], list) else spec[1].get("options") if spec and len(spec) > 1 and spec[0] == "COMBO" else None
                if not isinstance(choices, list) or any(not isinstance(name, str) for name in choices):
                    raise GenerationError("LoRA节点未返回有效模型列表。", "lora_list_unavailable")
                self._lora_schema = schema
                self._lora_names = [name for name in choices if name]
                self._lora_checked_at, self._lora_error = time.monotonic(), None
                self.live_schemas["LoraLoaderModelOnly"] = schema
            except (GenerationError, asyncio.TimeoutError) as error:
                self._lora_error = "LoRA模型列表连接失败，请检查ComfyUI后刷新。"
                if self._lora_names is None:
                    raise GenerationError(self._lora_error, "lora_list_unavailable") from error
            return list(self._lora_names)

    def _validate_loras(self, settings):
        enabled = [slot for slot in settings.get("lora_slots", []) if slot["enabled"]]
        if not enabled:
            return
        if self._lora_names is None or self._lora_error:
            raise GenerationError("LoRA模型列表尚未连接，请刷新模型列表后重试。", "lora_list_unavailable")
        names = self._lora_names
        for slot in enabled:
            if slot["name"] not in names:
                raise GenerationError("已启用的LoRA不在本机模型列表中：" + slot["name"])
            name = slot["name"].lower()
            family = "moss" if "moss" in name else "qwen" if "qwen" in name or "image2.1" in name else "other" if any(token in name for token in ("flux", "z-image", "zimage", "minimax", "sdxl", "sd1.5", "ltx")) else None
            expected = "moss" if settings["engine"] == "moss" else "qwen"
            if family and family != expected:
                raise GenerationError("LoRA文件显示属于另一模型家族，请为当前引擎选择匹配权重：" + slot["name"])

    async def _references(self, settings, references):
        if references is None:
            references = []
            for identifier in settings["asset_ids"]:
                if self.resolver is None:
                    raise GenerationError("素材解析器尚未配置。")
                value = await asyncio.to_thread(self.resolver, identifier)
                references.append(await value if inspect.isawaitable(value) else value)
        if len(references) != len(settings["asset_ids"]):
            raise GenerationError("素材引用数量与素材编号不一致。")
        decoded, descriptors = [], []
        sheet_mode = settings["engine"] == "moss" and len(references) > 1
        for value in references:
            if isinstance(value, dict) and "filename" in value and "path" not in value:
                if sheet_mode:
                    raise GenerationError("多图拼图需要安全的本地素材路径。")
                input_name(value)
                descriptors.append(value)
                continue
            path = value.get("path") if isinstance(value, dict) else value
            data = await asyncio.to_thread(image_bytes, path, 2048 if settings["engine"] == "moss" else None)
            if sheet_mode:
                decoded.append(data)
            else:
                descriptors.append(await self._upload(data))
        if sheet_mode:
            descriptors = [await self._upload(await asyncio.to_thread(contact_sheet, decoded))]
        return descriptors

    async def _upload(self, data):
        name = hashlib.sha256(data).hexdigest()[:24] + ".png"
        form = aiohttp.FormData()
        form.add_field("image", data, filename=name, content_type="image/png")
        form.add_field("type", "input")
        form.add_field("subfolder", "asset-library")
        form.add_field("overwrite", "false")
        descriptor = await self._request("POST", "/upload/image", data=form)
        descriptor = {"filename": descriptor.get("name"), "subfolder": descriptor.get("subfolder", ""), "type": "input"}
        input_name(descriptor)
        return descriptor

    async def build(self, payload, references=None):
        cloud = isinstance(payload, dict) and str(payload.get("engine", "")).startswith("bbapi:")
        if cloud and self.provider is None:
            raise GenerationError("请先配置BBAPI云端连接。")
        cloud_model = self.provider.model(payload["engine"]) if cloud else None
        settings = normalize(payload, cloud_model)
        await self.refresh()
        if any(slot["enabled"] for slot in settings["lora_slots"]):
            await self._refresh_loras()
        self._validate_loras(settings)
        async with self._reference_lock:
            descriptors = await self._references(settings, references)
        graph = cloud_api_prompt(settings, descriptors, self.provider.public_config()) if cloud else api_prompt(settings, descriptors, len(settings["asset_ids"]))
        missing = self._missing(settings, graph)
        warnings = [ENGINE_INFO[settings["engine"]]["reference_note"]] if descriptors and settings["engine"] == "moss" else []
        config = model_settings(settings) if not cloud else {"model": cloud_model["id"], "clip": None, "vae": None, "steps": None, "cfg": None, "sampler": None, "scheduler": None}
        info = ENGINE_INFO[settings["engine"]] if not cloud else {"name": "BBAPI · " + cloud_model["name"], "reference_mode": "individual"}
        engine_info = {"id": settings["engine"], **copy.deepcopy(info),
                       "model_name": config["model"], "clip_name": config["clip"], "vae_name": config["vae"],
                       "steps": config["steps"], "cfg": config["cfg"], "sampler": config["sampler"],
                       "scheduler": config["scheduler"], "lora_enabled": any(slot["enabled"] for slot in settings["lora_slots"]),
                       "reference_resolution": 0 if settings["engine"] in QWEN_ENGINES else None}
        return {"engine": settings["engine"], "prompt": graph,
                "engine_info": engine_info,
                "workflow": canvas_workflow(graph, {**self.schemas, **self.live_schemas}),
                "reference_mode": info["reference_mode"],
                "reference_count": len(settings["asset_ids"]), "warnings": warnings, "settings": settings,
                "ready": not missing, "missing": missing}

    async def _persist(self):
        if self.store.path is None:
            self._dirty = False
            return
        async with self._save_lock:
            snapshot = copy.deepcopy(self.store.snapshot())
            await asyncio.to_thread(self.store.save, snapshot)
            self._dirty = False

    def _owned(self, prompt_id):
        if not isinstance(prompt_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", prompt_id):
            raise GenerationError("任务编号无效。")
        return self.jobs.get(prompt_id)

    @staticmethod
    def _image_key(image):
        return tuple(str(image.get(key, "")).replace("\\", "/") for key in ("filename", "subfolder", "type"))

    def _image_results(self, record, job=None):
        images = []
        settings = (job or {}).get("settings", {})
        saved = {self._image_key(item["original"]): item["saved"] for item in (job or {}).get("_saved_images", [])}
        outputs = record.get("outputs", {})
        terminal = {str(key) for key, value in (job or {}).get("_graph", {}).items()
                    if value.get("class_type") in ("SaveImage", "PreviewImage")}
        chosen = {key: value for key, value in outputs.items() if str(key) in terminal}
        for output in (chosen or outputs).values():
            for image in output.get("images", []):
                safe = {key: image.get(key, "") for key in ("filename", "subfolder", "type")}
                if safe["filename"] and safe["type"] in ("input", "output", "temp"):
                    metadata = image.get("metadata") if isinstance(image.get("metadata"), dict) else {}
                    size = {}
                    for key in ("width", "height"):
                        value = next((value for value in (metadata.get(key), image.get(key), settings.get(key))
                                      if type(value) is int and value > 0), None)
                        if type(value) is int and value > 0:
                            size[key] = value
                    value = {**safe, **size, "url": self.base_url + "/view?" + urlencode(safe)}
                    images.append({**value, **saved.get(self._image_key(safe), {})})
        return images

    async def mark_saved(self, original_image, new_image):
        """Persist result relocation so history refresh never restores a temp URL."""
        if not isinstance(original_image, dict) or not isinstance(new_image, dict):
            raise GenerationError("保存结果图片信息无效。")
        if not original_image.get("filename") or not new_image.get("filename"):
            raise GenerationError("保存结果图片文件名无效。")
        old_key, updated = self._image_key(original_image), 0
        descriptor = {key: new_image.get(key, "") for key in ("filename", "subfolder", "type")}
        if descriptor["type"] not in ("input", "output", "temp"):
            raise GenerationError("保存结果图片类型无效。")
        last_image = dict(new_image)
        async with self._jobs_lock:
            for job in self.jobs.values():
                for index, image in enumerate(job.get("images", [])):
                    if self._image_key(image) != old_key:
                        continue
                    size = {}
                    metadata = new_image.get("metadata") if isinstance(new_image.get("metadata"), dict) else {}
                    for key in ("width", "height"):
                        value = next((value for value in (new_image.get(key), metadata.get(key), image.get(key), job.get("settings", {}).get(key))
                                      if type(value) is int and value > 0), None)
                        if type(value) is int and value > 0:
                            size[key] = value
                    replacement = {**descriptor, **size, "url": new_image.get("url") or self.base_url + "/view?" + urlencode(descriptor)}
                    job["images"][index], last_image = replacement, replacement
                    aliases = job.setdefault("_saved_images", [])
                    for alias in aliases:
                        if self._image_key(alias["saved"]) == old_key or self._image_key(alias["original"]) == old_key:
                            alias["saved"] = replacement
                    if not any(self._image_key(alias["original"]) == old_key for alias in aliases):
                        aliases.append({"original": {key: original_image.get(key, "") for key in ("filename", "subfolder", "type")}, "saved": replacement})
                    updated += 1
            if updated:
                self._dirty = True
                await self._persist()
        return {"updated": updated, "image": last_image}

    def _history_record(self, prompt_id, record):
        prompt = record.get("prompt")
        if not owned_prompt(prompt):
            return
        job = self.jobs.get(prompt_id)
        if job is None:
            settings = infer_settings(prompt)
            extra = prompt[3] or {}
            workflow = extra.get("extra_pnginfo", {}).get("workflow")
            job = new_job(prompt_id, settings, prompt[2], workflow, extra.get("client_id"), prompt[0])
            self.jobs[prompt_id] = job
            # Existing completed history must never open old result folders.
            job["_notify_on_completion"] = False
        state = record.get("status", {})
        status = "completed" if state.get("completed") and state.get("status_str") == "success" else "failed"
        error = None
        for name, message in state.get("messages", []):
            stamp = from_timestamp(message.get("timestamp"))
            if name == "execution_start":
                job["started_at"] = stamp or job.get("started_at")
                if stamp:
                    job["created_at"] = min(job.get("created_at") or stamp, stamp)
            elif name in ("execution_success", "execution_error", "execution_interrupted"):
                job["completed_at"] = stamp or job.get("completed_at") or now_iso()
                if name == "execution_interrupted":
                    status = "cancelled"
                elif name == "execution_error":
                    error = str(message.get("exception_message", "生成失败。"))[:1000]
        job.update(status=status, images=self._image_results(record, job), error=error,
                   completed_at=job.get("completed_at") or now_iso(), progress=1.0 if status == "completed" else job.get("progress"))
        self._dirty = True
        settings = job.get("settings", {})
        if (status == "completed" and job.get("_notify_on_completion") and not job.get("_completion_notified")
                and settings.get("save_output", True) and settings.get("auto_open_folder") and job.get("images")):
            self._pending_notifications[prompt_id] = None
        return job

    def _queue_record(self, item, status):
        if not isinstance(item, (list, tuple)) or len(item) < 4:
            return
        prompt_id = item[1]
        job = self.jobs.get(prompt_id)
        if job is None:
            if not owned_prompt(item):
                return
            settings, extra = infer_settings(item), item[3] or {}
            job = new_job(prompt_id, settings, item[2], extra.get("extra_pnginfo", {}).get("workflow"), extra.get("client_id"), item[0])
            self.jobs[prompt_id] = job
            job["_notify_on_completion"] = True
        if job.get("status") not in FINAL:
            job["status"] = "cancelling" if job.get("status") == "cancelling" else status
            if status == "running":
                job["started_at"] = job.get("started_at") or now_iso()
        self._dirty = True

    async def _sync_jobs(self, force=False):
        async with self._jobs_lock:
            if not force and time.monotonic() - self._last_sync < 0.8:
                return
            timeout = aiohttp.ClientTimeout(total=4)
            if not self._history_recovered:
                history = await self._request("GET", "/history?max_items=80", timeout=timeout)
                for prompt_id, record in history.items():
                    self._history_record(prompt_id, record)
                self._history_recovered = True
            queue = await self._request("GET", "/queue", timeout=timeout)
            pending, running = queue.get("queue_pending", []), queue.get("queue_running", [])
            for item in pending:
                self._queue_record(item, "queued")
            for item in running:
                self._queue_record(item, "running")
            active_ids = {item[1] for item in pending + running if len(item) > 1}
            missing = [identifier for identifier, job in self.jobs.items() if job.get("status") in ACTIVE and identifier not in active_ids]
            semaphore = asyncio.Semaphore(4)
            async def reconcile(identifier):
                async with semaphore:
                    history = await self._request("GET", "/history/" + identifier, timeout=timeout)
                    if identifier in history:
                        self._history_record(identifier, history[identifier])
                    else:
                        job = self.jobs[identifier]
                        job["_misses"] = job.get("_misses", 0) + 1
                        if job["_misses"] >= 2:
                            job.update(status="cancelled" if job.get("_cancel_requested") else "failed",
                                       completed_at=now_iso(), error=None if job.get("_cancel_requested") else "任务已离开队列，ComfyUI未保留结果记录。")
                            self._dirty = True
            if missing:
                await asyncio.gather(*(reconcile(identifier) for identifier in missing))
            self.store.trim()
            self._last_sync = time.monotonic()
            if self._dirty:
                await self._persist()
            self._flush_notifications()

    def _flush_notifications(self):
        if (not self._closed and self.on_completed and self._pending_notifications
                and (self._notification_task is None or self._notification_task.done())):
            self._notification_task = asyncio.create_task(self._notify_completed())

    async def _notify_completed(self):
        # One background callback at a time; neither UI polling nor queue work
        # waits for Explorer or any other completion action.
        while self._pending_notifications and not self._closed:
            identifier, _ = self._pending_notifications.popitem(last=False)
            job = self.jobs.get(identifier)
            if not job or job.get("_completion_notified") or job.get("status") != "completed":
                continue
            job["_completion_notified"], self._dirty = True, True
            await self._persist()
            try:
                value = public_job(job)
                if inspect.iscoroutinefunction(self.on_completed):
                    await self.on_completed(value)
                else:
                    result = await asyncio.to_thread(self.on_completed, value)
                    if inspect.isawaitable(result):
                        await result
                job["_folder_opened"] = True
                job.pop("folder_error", None)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                job["_folder_opened"] = False
                job["folder_error"] = str(error)[:500] or "完成后定位文件夹失败。"
            self._dirty = True
            await self._persist()

    def _emit_event(self, kind, data):
        self._sequence += 1
        self._events.append({"seq": self._sequence, "type": kind, "data": copy.deepcopy(data)})

    @staticmethod
    def _sampling_clock(job, node, value, maximum):
        if str(node) not in job.get("sampler_nodes", [job.get("sampler_node")]):
            job.pop("_sampling_progress", None)
            return
        tracker = job.get("_sampling_progress", {})
        samples = tracker.get("samples", [])
        if tracker.get("node") != str(node) or tracker.get("max") != maximum or samples and value < samples[-1][0]:
            samples = []
        if not samples or value != samples[-1][0]:
            samples.append([value, time.monotonic()])
        job["_sampling_progress"] = {"node": str(node), "max": maximum, "samples": samples[-16:]}

    def apply_event(self, kind, data, client_id=None):
        """Map actual Comfy websocket events; public for isolated protocol tests."""
        if not isinstance(data, dict):
            return
        prompt_id = data.get("prompt_id")
        job = self.jobs.get(prompt_id)
        if job is None:
            if prompt_id and client_id and client_id.startswith("asset-library-"):
                self._early_events.append((kind, data, client_id))
            return
        if kind not in ("execution_start", "executing", "progress", "progress_state", "execution_cached", "executed", "execution_success", "execution_error", "execution_interrupted"):
            return
        if job.get("status") not in FINAL:
            job["started_at"] = job.get("started_at") or from_timestamp(data.get("timestamp")) or now_iso()
            if job.get("status") != "cancelling":
                job["status"] = "running"
        node = data.get("node")
        if kind == "executing":
            if (str(node) if node is not None else None) != job.get("node"):
                job.pop("_sampling_progress", None)
            job["node"] = str(node) if node is not None else None
            if node is None:
                job["_finish_seen"] = True
        elif kind in ("progress", "progress_state"):
            values = data
            if kind == "progress_state":
                node_states = data.get("nodes", {})
                samplers = job.get("sampler_nodes", [job.get("sampler_node")])
                values = next((node_states[key] for key in samplers if key in node_states and node_states[key].get("state") == "running"),
                              node_states.get(job.get("node")) or next((value for value in node_states.values() if value.get("state") == "running"), {}))
                node = values.get("node_id", node)
            try:
                value, maximum = float(values.get("value")), float(values.get("max"))
                if math.isfinite(value) and math.isfinite(maximum) and maximum > 0:
                    self._sampling_clock(job, node, value, maximum)
                    job.update(node=str(node) if node is not None else job.get("node"), value=value, max=maximum, step=value)
                    samplers = job.get("sampler_nodes", [job.get("sampler_node")])
                    if str(node) in samplers:
                        stage = samplers.index(str(node))
                        job.update(sampling_stage=stage + 1, sampling_stages=len(samplers))
                        job["progress"] = min(0.99, max(0, (stage + value / maximum) / len(samplers)))
            except (TypeError, ValueError, OverflowError):
                pass
        elif kind == "execution_error":
            job.update(status="failed", completed_at=now_iso(), error=str(data.get("exception_message", "生成失败。"))[:1000])
        elif kind == "execution_interrupted":
            job.update(status="cancelled", completed_at=now_iso(), error=None)
        elif kind == "execution_success":
            job["_finish_seen"] = True
        self._emit_event(kind, data)
        self._dirty = True

    async def _websocket(self, client_id):
        ready = self._ws_ready.setdefault(client_id, asyncio.Event())
        while not self._closed and (client_id == self.client_id and self._pending_submissions or any(job.get("status") in ACTIVE and job.get("client_id") == client_id for job in self.jobs.values())):
            try:
                async with aiohttp.ClientSession(trust_env=False) as session:
                    async with session.ws_connect(self.base_url + "/ws?" + urlencode({"clientId": client_id}), heartbeat=20,
                                                  max_msg_size=2 * 1024 * 1024,
                                                  timeout=aiohttp.ClientWSTimeout(ws_receive=35, ws_close=4)) as socket:
                        ready.set()
                        async for message in socket:
                            if message.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    event = json.loads(message.data)
                                    self.apply_event(event.get("type"), event.get("data"), client_id)
                                except (ValueError, TypeError):
                                    continue
                            # Binary previews are deliberately not retained in memory.
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                if self._closed:
                    break
                await asyncio.sleep(1)
            finally:
                ready.clear()

    def _start_tracking(self):
        if self._closed:
            return
        active = [job for job in self.jobs.values() if job.get("status") in ACTIVE]
        clients = {job.get("client_id") for job in active if isinstance(job.get("client_id"), str) and job["client_id"].startswith("asset-library-")}
        if self._pending_submissions:
            clients.add(self.client_id)
        for client in clients:
            task = self._ws_tasks.get(client)
            if task is None or task.done():
                self._ws_tasks[client] = asyncio.create_task(self._websocket(client))
        for client, task in list(self._ws_tasks.items()):
            if client not in clients:
                task.cancel()
                del self._ws_tasks[client]
        if active and (self._monitor_task is None or self._monitor_task.done()):
            self._monitor_task = asyncio.create_task(self._monitor())

    async def _monitor(self):
        try:
            while not self._closed and any(job.get("status") in ACTIVE for job in self.jobs.values()):
                self._start_tracking()
                try:
                    await self._sync_jobs()
                except GenerationError:
                    if self._dirty:
                        await self._persist()
                    self._flush_notifications()
                await asyncio.sleep(1.2)
        finally:
            for task in self._ws_tasks.values():
                task.cancel()
            self._ws_tasks.clear()

    async def jobs_list(self, after_seq=0):
        error = None
        try:
            await self._sync_jobs()
        except GenerationError as exception:
            error = str(exception)
            if self._dirty:
                await self._persist()
        self._start_tracking()
        try:
            sequence = max(0, int(after_seq))
        except (TypeError, ValueError):
            raise GenerationError("进度游标无效。")
        if sequence > self._sequence:
            sequence = 0
        jobs = [public_job(job) for job in sorted(self.jobs.values(), key=lambda value: value.get("created_at") or "", reverse=True)]
        result = {"jobs": jobs, "active_count": sum(job["status"] in ACTIVE for job in jobs),
                  "events": [event for event in self._events if event["seq"] > sequence], "next_seq": self._sequence}
        if error:
            result["error"] = error
        return result

    async def _enqueue(self, built, graph=None, workflow=None):
        if sum(job.get("status") in ACTIVE for job in self.jobs.values()) + self._pending_submissions >= 100:
            raise GenerationError("最多同时保留100个待完成任务。")
        graph, workflow = graph or built["prompt"], workflow or built["workflow"]
        # Establish our own websocket before POST; never reuse the browser SID.
        self._pending_submissions += 1
        self._start_tracking()
        ready = self._ws_ready.setdefault(self.client_id, asyncio.Event())
        try:
            try:
                await asyncio.wait_for(ready.wait(), timeout=1.5)
            except asyncio.TimeoutError:
                pass
            result = await self._request("POST", "/prompt", json={"prompt": graph, "client_id": self.client_id,
                           "extra_data": {"extra_pnginfo": {"workflow": workflow, "asset_library": built["settings"]}}})
        except BaseException:
            self._pending_submissions -= 1
            self._start_tracking()
            raise
        self._pending_submissions -= 1
        identifier = result.get("prompt_id")
        if not identifier:
            self._start_tracking()
            raise GenerationError("ComfyUI未返回任务编号。")
        job = new_job(identifier, built["settings"], graph, workflow, self.client_id, result.get("number"))
        job["_notify_on_completion"] = True
        self.jobs[identifier] = job
        for event in list(self._early_events):
            if event[1].get("prompt_id") == identifier:
                self.apply_event(*event)
                self._early_events.remove(event)
        self._dirty = True
        await self._persist()
        self._start_tracking()
        return {**public_job(job), "warnings": built.get("warnings", [])}

    async def submit(self, payload, references=None):
        built = await self.build(payload, references)
        if not built["ready"]:
            raise GenerationError("当前引擎尚未就绪：" + "、".join(built["missing"]), "engine_not_ready", {"missing": built["missing"]})
        return await self._enqueue(built)

    async def result(self, prompt_id):
        job = self._owned(prompt_id)
        if job is None:
            record = await self._request("GET", "/history/" + prompt_id, timeout=aiohttp.ClientTimeout(total=4))
            job = self._history_record(prompt_id, record.get(prompt_id, {}))
            if job is None:
                raise GenerationError("这不是资产库提交的任务，或记录已失效。")
        if job.get("status") in ACTIVE:
            try:
                await self._sync_jobs()
            except GenerationError:
                pass
            self._start_tracking()
        if self._dirty:
            await self._persist()
        return public_job(job)

    async def cancel(self, prompt_id):
        job = self._owned(prompt_id)
        if job is None:
            raise GenerationError("只能取消本资产库提交的任务。")
        queue = await self._request("GET", "/queue", timeout=aiohttp.ClientTimeout(total=4))
        if any(item[1] == prompt_id for item in queue.get("queue_running", [])):
            await self._request("POST", "/interrupt", json={"prompt_id": prompt_id})
            job["status"] = "cancelling"
        elif any(item[1] == prompt_id for item in queue.get("queue_pending", [])):
            await self._request("POST", "/queue", json={"delete": [prompt_id]})
            job.update(status="cancelled", completed_at=now_iso())
        else:
            return await self.result(prompt_id)
        job["_cancel_requested"], self._dirty = True, True
        await self._persist()
        return public_job(job)

    async def retry(self, prompt_id):
        job = self._owned(prompt_id)
        if not job:
            raise GenerationError("任务记录已失效，请重新选择素材生成。")
        if job.get("status") in ACTIVE:
            raise GenerationError("任务仍在执行或排队，请先取消。")
        if not job.get("_graph"):
            return await self.submit(job["settings"])
        settings, graph = copy.deepcopy(job["settings"]), copy.deepcopy(job["_graph"])
        settings["seed"] = secrets.randbits(53)
        for node in graph.values():
            stage_match = re.search(r"LoRA (\d+)", node.get("_meta", {}).get("title", ""))
            stage_seed = (settings["seed"] + (int(stage_match[1]) if stage_match else 0)) % (1 << 53)
            if node.get("class_type") == "KSampler":
                node["inputs"]["seed"] = stage_seed
            elif node.get("class_type") == "RandomNoise":
                node["inputs"]["noise_seed"] = stage_seed
            elif node.get("class_type") in ("BBAPIMultiReference", "BBAPIPromptToImage"):
                node["inputs"]["重新生成编号"] = settings["seed"] % 2147483647
        workflow = canvas_workflow(graph, {**self.schemas, **self.live_schemas})
        return await self._enqueue({"settings": settings, "prompt": graph, "workflow": workflow, "warnings": []})

    async def close(self):
        self._closed = True
        tasks = [task for task in [self._monitor_task, self._notification_task, *self._ws_tasks.values()] if task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._dirty:
            await self._persist()
