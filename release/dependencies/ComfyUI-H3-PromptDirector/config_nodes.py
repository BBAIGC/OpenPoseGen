"""Connectable H3 settings. These nodes return metadata, never model weights."""
from __future__ import annotations

import math
from pathlib import Path

import folder_paths

from .prompt_nodes import local_models
from .planner import PREFERRED_MODEL
from .shot_nodes import (BASE_MODEL, TEXT_ENCODER, VIDEO_VAE, AUDIO_VAE,
                         TURBO_LORA_4STEP, TURBO_LORA)
from .native_nodes import AUTHOR_MODEL

IMAGE_MODEL = "qwen-image-2.1-UC-Q6_K.gguf"
IMAGE_CLIP = "qwen3vl_8b_int8_convrot.safetensors"
IMAGE_VAE = "qwen_image_2.1_vae_bf16.safetensors"
MISSING = "未找到兼容模型"
FUSED_MODEL_LABEL = "mystic07 · RefDelta r1024 Turbo8"


def _name(value):
    return Path(str(value).replace("\\", "/")).name.lower()


def _files(*categories, predicate=None):
    values = []
    for category in categories:
        try:
            values.extend(folder_paths.get_filename_list(category))
        except KeyError:
            continue
    return sorted({name for name in values if predicate is None or predicate(_name(name))})


def _choice(values, preferred, tooltip=""):
    options = list(values) or [MISSING]
    settings = {"default": preferred if preferred in options else options[0]}
    if tooltip:
        settings["tooltip"] = tooltip
    return options, settings


def _selected(value, values, label):
    if value not in values:
        raise ValueError(f"{label}未找到或不兼容，请选择本机已有的对应权重。")
    return value


def _number(value, minimum, maximum, label, integer=False):
    if isinstance(value, bool):
        raise ValueError(f"{label}应为有效数字。")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}应为有效数字。") from exc
    if not math.isfinite(result) or not minimum <= result <= maximum or integer and result != int(result):
        raise ValueError(f"{label}应在{minimum:g}～{maximum:g}之间。")
    return int(result) if integer else result


def _samplers():
    from comfy.samplers import KSampler
    return list(KSampler.SAMPLERS), list(KSampler.SCHEDULERS)


def _image_choices():
    return {
        "model": _files("unet_gguf", "unet", "diffusion_models", predicate=lambda name:
                        "qwen" in name and "2.1" in name and name.endswith((".gguf", ".safetensors"))),
        "clip": _files("text_encoders", predicate=lambda name:
                       "qwen3vl8b" in name.replace("_", "").replace("-", "") and
                       "minimax" not in name and name.endswith(".safetensors")),
        "vae": _files("vae", predicate=lambda name: "qwen" in name and "2.1" in name),
    }


def _video_choices():
    return {
        "基础模型": _files("diffusion_models", predicate=lambda name:
                         name.startswith("minimax_h3_fl2va") and name.endswith(".safetensors") and
                         not any(part in name for part in ("fused", "turbo", "refdelta"))),
        "原作者模型": _files("diffusion_models", predicate=lambda name: name == AUTHOR_MODEL.lower()),
        "文本编码器": _files("text_encoders", predicate=lambda name:
                          "minimax_h3" in name and "qwen3vl" in name and name.endswith(".safetensors")),
        "视频VAE": _files("vae", predicate=lambda name: name.startswith("minimax_h3_video_vae")),
        "音频VAE": _files("vae", predicate=lambda name: name.startswith("minimax_h3_audio_vae")),
        "4步LoRA": _files("loras", predicate=lambda name: "minimax_h3_fl2v_turbo_4step" in name),
        "8步LoRA": _files("loras", predicate=lambda name: "minimax_h3_fl2v_turbo_8step" in name),
    }


class H3TextModelSettings:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "模型": _choice(local_models(), PREFERRED_MODEL),
            "温度": ("FLOAT", {"default": 0.1, "min": 0.0, "max": 2.0, "step": 0.05}),
            "上下文长度": ("INT", {"default": 32768, "min": 4096, "max": 131072, "step": 1024}),
            "输出上限": ("INT", {"default": 10000, "min": 512, "max": 32768, "step": 256}),
            "top_p": ("FLOAT", {"default": 0.9, "min": 0.01, "max": 1.0, "step": 0.05}),
            "重复惩罚": ("FLOAT", {"default": 1.05, "min": 0.1, "max": 2.0, "step": 0.05}),
        }}

    RETURN_TYPES = ("H3_TEXT_SETTINGS",)
    RETURN_NAMES = ("文本模型配置",)
    FUNCTION = "execute"
    CATEGORY = "H3/模型与采样"
    DESCRIPTION = "选择本机千问与规划参数；连接工作台后生效，配置节点不加载模型。"

    def execute(self, **kwargs):
        settings = {
            "model": _selected(kwargs["模型"], local_models(), "千问模型"),
            "temperature": _number(kwargs["温度"], 0, 2, "温度"),
            "num_ctx": _number(kwargs["上下文长度"], 4096, 131072, "上下文长度", True),
            "num_predict": _number(kwargs["输出上限"], 512, 32768, "输出上限", True),
            "top_p": _number(kwargs["top_p"], 0.01, 1, "top_p"),
            "repeat_penalty": _number(kwargs["重复惩罚"], 0.1, 2, "重复惩罚"),
        }
        if settings["num_predict"] > settings["num_ctx"]:
            raise ValueError("输出上限不能大于上下文长度。")
        return (settings,)


class H3ImageModelSettings:
    @classmethod
    def VALIDATE_INPUTS(cls, **kwargs):
        # A disabled repair branch may connect this metadata node; only its
        # actual execute() validates the selected model files.
        return True

    @classmethod
    def INPUT_TYPES(cls):
        choices = _image_choices()
        samplers, schedulers = _samplers()
        return {"required": {
            "图像模型": _choice(choices["model"], IMAGE_MODEL, "仅显示Qwen-Image 2.1兼容的本机GGUF或safetensors权重。"),
            "文本编码器": _choice(choices["clip"], IMAGE_CLIP),
            "VAE": _choice(choices["vae"], IMAGE_VAE),
            "步数": ("INT", {"default": 25, "min": 1, "max": 100}),
            "CFG": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 30.0, "step": 0.1}),
            "采样器": _choice(samplers, "euler"),
            "调度器": _choice(schedulers, "simple"),
            "去噪": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.05}),
            "缓存设备": (["auto", "gpu", "cpu", "off"], {"default": "auto"}),
            "缓存精度": (["default", "int8", "int4"], {"default": "default"}),
            "编码器设备": (["default", "cpu"], {"default": "default"}),
        }, "optional": {
            "分镜图长边": ("INT", {"default": 2048, "min": 0, "max": 4096, "step": 32,
                                    "tooltip": "0跟随视频尺寸；较大值先生成更清晰的AI分镜原图，再适配视频尺寸。仅影响AI分镜及结束帧，不改变独立生图和末帧修复尺寸。"}),
            "上镜参考策略": (["完整参考（兼容）", "结构参考（原图细节优先）"], {"default": "完整参考（兼容）",
                            "tooltip": "结构参考限制上一张AI分镜的细节输入，原始素材继续提供身份、肤质及材质；仅影响AI分镜及结束帧。"}),
            "参考读取长边": ("INT", {"default": 0, "min": 0, "max": 4096, "step": 32,
                                     "tooltip": "0自动选择最高2K；仅缩小超出上限的参考图，不把小原图放大。与视频输出尺寸独立。"}),
        }}

    RETURN_TYPES = ("H3_IMAGE_SETTINGS",)
    RETURN_NAMES = ("图像模型配置",)
    FUNCTION = "execute"
    CATEGORY = "H3/模型与采样"
    DESCRIPTION = "配置Qwen-Image 2.1分镜生图和末帧修复；不切换其他模型族。"

    def execute(self, **kwargs):
        choices = _image_choices()
        samplers, schedulers = _samplers()
        result = {
            "model": _selected(kwargs["图像模型"], choices["model"], "Qwen-Image 2.1模型"),
            "clip": _selected(kwargs["文本编码器"], choices["clip"], "图像文本编码器"),
            "vae": _selected(kwargs["VAE"], choices["vae"], "图像VAE"),
            "steps": _number(kwargs["步数"], 1, 100, "图像步数", True),
            "cfg": _number(kwargs["CFG"], 0, 30, "CFG"),
            "sampler": _selected(kwargs["采样器"], samplers, "图像采样器"),
            "scheduler": _selected(kwargs["调度器"], schedulers, "图像调度器"),
            "denoise": _number(kwargs["去噪"], 0, 1, "去噪"),
            "cache_device": _selected(kwargs["缓存设备"], ["auto", "gpu", "cpu", "off"], "缓存设备"),
            "cache_dtype": _selected(kwargs["缓存精度"], ["default", "int8", "int4"], "缓存精度"),
            "clip_device": _selected(kwargs.get("编码器设备", "default"), ["default", "cpu"], "编码器设备"),
            "storyboard_long_edge": _number(kwargs.get("分镜图长边", 2048), 0, 4096, "分镜图长边", True),
            "reference_long_edge": _number(kwargs.get("参考读取长边", 0), 0, 4096, "参考读取长边", True),
            "previous_reference_policy": _selected(kwargs.get("上镜参考策略", "完整参考（兼容）"),
                                                   ["完整参考（兼容）", "结构参考（原图细节优先）"], "上镜参考策略"),
        }
        if result["storyboard_long_edge"] % 32 or result["reference_long_edge"] % 32:
            raise ValueError("分镜图长边与参考读取长边应为0～4096的32倍数。")
        return (result,)


class H3VideoModelSettings:
    @classmethod
    def INPUT_TYPES(cls):
        choices = _video_choices()
        samplers, schedulers = _samplers()
        defaults = {"基础模型": BASE_MODEL, "原作者模型": AUTHOR_MODEL,
                    "文本编码器": TEXT_ENCODER, "视频VAE": VIDEO_VAE, "音频VAE": AUDIO_VAE,
                    "4步LoRA": TURBO_LORA_4STEP, "8步LoRA": TURBO_LORA}
        optional_weights = {"基础模型", "原作者模型", "4步LoRA", "8步LoRA"}
        required = {key: _choice(choices[key] + (["未选用（无需下载）"] if key in optional_weights else []), default)
                    for key, default in defaults.items()}
        required["基础模型"][1]["tooltip"] = "仅支持未融合的MiniMax H3 FL2VA基础模型。"
        required["原作者模型"][1]["tooltip"] = "mystic07 RefDelta r1024 Turbo8融合权重；不叠加外置LoRA。"
        required.update({
            "启用加速LoRA": ("BOOLEAN", {"default": True}),
            "加速权重": ([FUSED_MODEL_LABEL, "独立LoRA"], {"default": FUSED_MODEL_LABEL}),
            "加速步数": ([4, 8], {"default": 4}),
            "标准步数": ("INT", {"default": 20, "min": 20, "max": 50}),
            "低显存": ("BOOLEAN", {"default": True}),
            "稀疏注意力": ("BOOLEAN", {"default": True}),
            "视频采样器": _choice(samplers, "res_multistep"),
            "视频调度器": _choice(schedulers, "simple"),
        })
        return {"required": required, "optional": {"角色音色": ("H3_VOICE_SETTINGS",),
                                                   "对白音轨": ("H3_DIALOGUE_TRACK",)}}

    RETURN_TYPES = ("H3_VIDEO_SETTINGS",)
    RETURN_NAMES = ("视频模型配置",)
    FUNCTION = "execute"
    CATEGORY = "H3/模型与采样"
    DESCRIPTION = "配置兼容的H3模型、LoRA与采样参数；关闭加速时使用未融合基础模型。"

    @classmethod
    def VALIDATE_INPUTS(cls, 加速权重=None, **kwargs):
        # An unused fusion/4-step/8-step weight may be absent on another machine.
        # Keep dropdowns, but defer file and scalar validation to execute's selected route.
        return 加速权重 in (FUSED_MODEL_LABEL, "原作者融合模型", "独立LoRA") or "加速权重无效。"

    def execute(self, **kwargs):
        choices = _video_choices()
        samplers, schedulers = _samplers()
        if not isinstance(kwargs["启用加速LoRA"], bool):
            raise ValueError("加速LoRA开关无效。")
        if kwargs["加速步数"] not in (4, 8):
            raise ValueError("加速步数只能为4或8。")
        fused = kwargs["启用加速LoRA"] and kwargs["加速权重"] in (FUSED_MODEL_LABEL, "原作者融合模型")
        needed = {"文本编码器", "视频VAE", "音频VAE", "原作者模型" if fused else "基础模型"}
        if kwargs["启用加速LoRA"] and not fused:
            needed.add(f'{int(kwargs["加速步数"])}步LoRA')
        result = {key: (_selected(kwargs.get(key), options, key) if key in needed else
                        kwargs.get(key) if kwargs.get(key) in options else None)
                  for key, options in choices.items()}
        result.update({
            "启用加速LoRA": kwargs["启用加速LoRA"],
            "加速权重": ("原作者融合模型" if kwargs["加速权重"] in (FUSED_MODEL_LABEL, "原作者融合模型") else
                       _selected(kwargs["加速权重"], ["独立LoRA"], "加速权重")),
            "加速步数": int(kwargs["加速步数"]),
            "标准步数": _number(kwargs["标准步数"], 20, 50, "标准步数", True),
            "低显存": bool(kwargs["低显存"]), "稀疏注意力": bool(kwargs["稀疏注意力"]),
            "视频采样器": _selected(kwargs["视频采样器"], samplers, "视频采样器"),
            "视频调度器": _selected(kwargs["视频调度器"], schedulers, "视频调度器"),
        })
        if kwargs.get("对白音轨") is not None:
            from .dialogue_audio import normalize_dialogue_track
            track = normalize_dialogue_track(kwargs["对白音轨"])
            if track and track["enabled"]:
                result["_dialogue_track"] = track
        voice = kwargs.get("角色音色")
        if voice is not None:
            if not isinstance(voice, dict):
                raise ValueError("角色音色配置无效。")
            if not result.get("_dialogue_track") and voice.get("mode") == "参考音色" and voice.get("assignments") and (
                    not result["启用加速LoRA"] or result["加速权重"] != "原作者融合模型"):
                raise ValueError("参考音色需要mystic07 RefDelta融合模型；当前基础模型路径请使用模型默认音色。")
            result["_voice_settings"] = voice
        return (result,)


CONFIG_NODE_CLASS_MAPPINGS = {cls.__name__: cls for cls in
                            (H3TextModelSettings, H3ImageModelSettings, H3VideoModelSettings)}
CONFIG_NODE_DISPLAY_NAME_MAPPINGS = {
    "H3TextModelSettings": "千问规划配置",
    "H3ImageModelSettings": "分镜图像模型配置",
    "H3VideoModelSettings": "H3视频模型配置",
}
