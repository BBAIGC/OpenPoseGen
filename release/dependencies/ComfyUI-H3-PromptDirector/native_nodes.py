"""Ready-only loading for the unchanged author-native H3 sampling graph.

This module does not implement conditioning, sampling, decoding or muxing.
Those operations remain visible ComfyUI core nodes in the native workflows.
"""
from __future__ import annotations

import json
from pathlib import Path

import folder_paths
import nodes

AUTHOR_MODEL = "minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors"
AUTHOR_CLIP = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
AUTHOR_VIDEO_VAE = "minimax_h3_video_vae_int8_convrot.safetensors"
AUTHOR_AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"


def _choices(category, default):
    values = folder_paths.get_filename_list(category)
    return (values, {"default": default})


class H3ReadyModelGate:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "分镜计划": ("STRING", {"forceInput": True}),
            "融合模型": _choices("diffusion_models", AUTHOR_MODEL),
            "文本编码器": _choices("text_encoders", AUTHOR_CLIP),
            "视频VAE": _choices("vae", AUTHOR_VIDEO_VAE),
            "音频VAE": _choices("vae", AUTHOR_AUDIO_VAE),
        }}

    RETURN_TYPES = ("MODEL", "CLIP", "VAE", "VAE", "STRING")
    RETURN_NAMES = ("原作者模型", "文本编码器", "视频VAE", "音频VAE", "加载说明")
    FUNCTION = "load"
    CATEGORY = "H3/作者原生4步"
    DESCRIPTION = "仅在整稿规划和语言模型卸载完成后加载原作者权重；采样与解码由图中原生节点完成。"

    def load(self, **kwargs):
        try:
            plan = json.loads(kwargs["分镜计划"])
        except (ValueError, TypeError) as exc:
            raise ValueError("分镜计划无效，作者原生链尚未加载模型。") from exc
        if not isinstance(plan, dict) or plan.get("status") != "ready" or plan.get("llm_released") is not True:
            raise ValueError("整稿规划或语言模型卸载尚未完成，视频权重不会加载。")
        filename = Path(str(kwargs["融合模型"]).replace("\\", "/")).name
        if filename != AUTHOR_MODEL:
            raise ValueError("此兼容流程固定使用原作者融合模型；基础模型的4/8/20步请使用另一套流程。")
        model = nodes.UNETLoader().load_unet(kwargs["融合模型"], "default")[0]
        clip = nodes.CLIPLoader().load_clip(kwargs["文本编码器"], "minimax", "default")[0]
        video_vae = nodes.VAELoader().load_vae(kwargs["视频VAE"])[0]
        audio_vae = nodes.VAELoader().load_vae(kwargs["音频VAE"])[0]
        report = ("作者原生4步：融合权重，不叠加外置Turbo LoRA。"
                  "后续图保持低显存分块4、SLA 0.9/64、shift 12/3、res_multistep/simple/4、BasicGuider。")
        return model, clip, video_vae, audio_vae, report


class H3NativeAudioPolicy:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"模型音轨": ("AUDIO",),
                             "音轨策略": ("STRING", {"forceInput": True})}}
    RETURN_TYPES = ("AUDIO",)
    RETURN_NAMES = ("原音轨或等长静音",)
    FUNCTION = "apply"
    CATEGORY = "H3/作者原生4步"

    def apply(self, **kwargs):
        audio, mode = kwargs["模型音轨"], kwargs["音轨策略"]
        if mode == "模型音频":
            return (audio,)
        if mode != "静音":
            raise ValueError("未知音轨策略；作者原生链只接受模型音频或静音。")
        import torch
        return ({**audio, "waveform": torch.zeros_like(audio["waveform"], device="cpu")},)


NATIVE_NODE_CLASS_MAPPINGS = {"H3ReadyModelGate": H3ReadyModelGate,
                             "H3NativeAudioPolicy": H3NativeAudioPolicy}
NATIVE_NODE_DISPLAY_NAME_MAPPINGS = {"H3ReadyModelGate": "作者模型加载门控",
                                    "H3NativeAudioPolicy": "音轨策略（默认原生直通）"}
