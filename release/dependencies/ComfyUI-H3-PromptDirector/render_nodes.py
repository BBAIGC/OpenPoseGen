"""Chinese controls and globally consistent, independently timed H3 shots."""
from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import uuid

import folder_paths
import nodes

from .shot_nodes import (
    AUDIO_VAE, BASE_MODEL, TEXT_ENCODER, TURBO_LORA, TURBO_LORA_4STEP,
    VIDEO_VAE, H3GenerateShot, H3VideoModelBundle, _combo,
)


def _plan(text):
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ValueError("分镜计划无效，请先运行整稿规划。") from exc
    if data.get("status") != "ready" or data.get("llm_released") is not True:
        raise ValueError("整稿规划尚未完成或语言模型尚未卸载。")
    durations = data.get("scene_seconds")
    if not isinstance(durations, list) or len(durations) not in (1, 3):
        raise ValueError("分镜计划必须包含每个镜头的独立时长。")
    if any(not isinstance(s, (int, float)) or not math.isfinite(s) or not 1 <= s <= 15 for s in durations):
        raise ValueError("每个镜头时长应为1～15秒。")
    return data


class H3ShotSettings:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {f"镜头{i}秒数": ("FLOAT", {
            "default": 10.0, "min": 1.0, "max": 15.0, "step": 0.5,
            "tooltip": "独立设置该镜头时长，同时用于原稿拆镜和视频生成。",
        }) for i in (1, 2, 3)}}

    RETURN_TYPES = ("FLOAT", "FLOAT", "FLOAT")
    RETURN_NAMES = ("镜头1秒数", "镜头2秒数", "镜头3秒数")
    FUNCTION = "execute"
    CATEGORY = "H3/整稿与分镜"

    def execute(self, **kwargs):
        result = tuple(float(kwargs[f"镜头{i}秒数"]) for i in (1, 2, 3))
        if any(not math.isfinite(s) or not 1 <= s <= 15 for s in result):
            raise ValueError("每个镜头时长应为1～15秒。")
        return result


class H3AccelerationSwitch:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"启用加速LoRA": ("BOOLEAN", {
            "default": True, "label_on": "配套4/8步", "label_off": "原版标准采样",
        })}}
    RETURN_TYPES = ("BOOLEAN",)
    RETURN_NAMES = ("启用加速",)
    FUNCTION = "execute"
    CATEGORY = "H3/整稿与分镜"

    def execute(self, **kwargs):
        return (bool(kwargs["启用加速LoRA"]),)


class H3EightSteps:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"基础步数": ("INT", {"forceInput": True, "default": 4})}}
    RETURN_TYPES = ("INT",)
    RETURN_NAMES = ("加速步数",)
    FUNCTION = "execute"
    CATEGORY = "H3/整稿与分镜"
    DESCRIPTION = "开启此组使用8步及配套LoRA；旁路此组使用输入的4步及配套LoRA。"

    def execute(self, **kwargs):
        return (8,)


class H3SamplingControl:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "启用加速": ("BOOLEAN", {"forceInput": True}),
            "加速步数": ("INT", {"forceInput": True}),
            "标准步数": ("INT", {"default": 20, "min": 20, "max": 50}),
        }}
    RETURN_TYPES = ("INT",)
    RETURN_NAMES = ("采样步数",)
    FUNCTION = "execute"
    CATEGORY = "H3/整稿与分镜"

    def execute(self, **kwargs):
        if kwargs["启用加速"]:
            value = int(kwargs["加速步数"])
            if value not in (4, 8):
                raise ValueError("加速模式只支持对应LoRA的4步或8步。")
            return (value,)
        return (max(20, int(kwargs["标准步数"])),)


class H3ModelLoader:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "分镜计划": ("STRING", {"forceInput": True}),
            "宽度": ("INT", {"forceInput": True}),
            "高度": ("INT", {"forceInput": True}),
            "采样档": ("INT", {"forceInput": True}),
            "启用加速": ("BOOLEAN", {"forceInput": True}),
            "基础模型": _combo("diffusion_models", BASE_MODEL),
            "文本编码器": _combo("text_encoders", TEXT_ENCODER),
            "视频VAE": _combo("vae", VIDEO_VAE),
            "音频VAE": _combo("vae", AUDIO_VAE),
            "低显存": ("BOOLEAN", {"default": True}),
            "4步LoRA": _combo("loras", TURBO_LORA_4STEP),
            "8步LoRA": _combo("loras", TURBO_LORA),
        }}
    RETURN_TYPES = ("H3_VIDEO_BUNDLE",)
    RETURN_NAMES = ("视频模型",)
    FUNCTION = "execute"
    CATEGORY = "H3/整稿与分镜"

    def execute(self, **kwargs):
        plan = _plan(kwargs["分镜计划"])
        width, height = int(kwargs["宽度"]), int(kwargs["高度"])
        if width < 64 or height < 64 or width % 32 or height % 32:
            raise ValueError("全局宽高必须是至少64的32倍数。")
        # Every render receives this same immutable canvas; a manual cross-shot
        # override is rejected before sampling, not after three videos finish.
        plan["canvas"] = {"width": width, "height": height}
        bundle = H3VideoModelBundle().load(
            ready_plan=json.dumps(plan, ensure_ascii=False),
            unet_name=kwargs["基础模型"], clip_name=kwargs["文本编码器"],
            video_vae_name=kwargs["视频VAE"], audio_vae_name=kwargs["音频VAE"],
            turbo_enabled=kwargs["启用加速"], accelerated_steps=kwargs["采样档"],
            turbo_lora=kwargs["8步LoRA"], turbo_lora_4step=kwargs["4步LoRA"],
            low_vram=kwargs["低显存"], sparse_attention=False,
        )[0]
        bundle["canvas"] = plan["canvas"]
        return (bundle,)


class H3RenderShot:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "视频模型": ("H3_VIDEO_BUNDLE",),
            "提示词": ("STRING", {"forceInput": True}),
            "宽度": ("INT", {"forceInput": True}),
            "高度": ("INT", {"forceInput": True}),
            "镜头秒数": ("FLOAT", {"forceInput": True}),
            "采样步数": ("INT", {"forceInput": True}),
            "随机种子": ("INT", {"default": 42, "min": 0, "max": 2**32-1}),
            "音轨策略": ("STRING", {"forceInput": True}),
            "镜头序号": ("INT", {"default": 1, "min": 1, "max": 3}),
        }, "optional": {"首帧": ("IMAGE",), "上一镜视频": ("VIDEO",)},
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"}}
    RETURN_TYPES = ("VIDEO", "IMAGE", "STRING")
    RETURN_NAMES = ("镜头视频", "末帧", "生成记录")
    FUNCTION = "execute"
    CATEGORY = "H3/整稿与分镜"

    def execute(self, **kwargs):
        from .output_metadata import execution_metadata
        with execution_metadata(prompt=kwargs.pop("prompt", None), extra_pnginfo=kwargs.pop("extra_pnginfo", None)):
            return self._execute(**kwargs)

    def _execute(self, **kwargs):
        from comfy_api.latest import InputImpl
        bundle = kwargs["视频模型"]
        plan = _plan(bundle["ready_plan"])
        index = int(kwargs["镜头序号"]) - 1
        if not 0 <= index < len(plan["scene_seconds"]):
            raise ValueError("镜头序号与整稿规划不符。")
        seconds = float(kwargs["镜头秒数"])
        if not math.isclose(seconds, plan["scene_seconds"][index], abs_tol=1e-6):
            raise ValueError("镜头时长与规划不一致；请统一使用“每镜时长”的同一个输出。")
        width, height = int(kwargs["宽度"]), int(kwargs["高度"])
        canvas = bundle["canvas"]
        if (width, height) != (canvas["width"], canvas["height"]):
            raise ValueError("镜头画幅与全局设置不一致；尚未开始该镜生成。")
        previous = kwargs.get("上一镜视频")
        if index and previous is None:
            raise ValueError("后续镜头必须连接上一镜视频，以保证顺序生成。")
        if previous is not None and tuple(previous.get_dimensions()) != (width, height):
            raise ValueError("上一镜尺寸与全局画幅不一致，请使用同一全局设置重新生成。")
        image = kwargs.get("首帧")
        if image is not None and tuple(image.shape[1:3]) != (height, width):
            raise ValueError("接入首帧没有按全局画幅适配，请连接“镜头首帧”的图像输出。")
        selected = dict(bundle)
        selected_plan = dict(plan)
        selected_plan["seconds_per_scene"] = seconds
        selected["ready_plan"] = json.dumps(selected_plan, ensure_ascii=False)
        result = H3GenerateShot().generate(
            bundle=selected, prompt=kwargs["提示词"], width=width, height=height,
            seconds=seconds, steps=kwargs["采样步数"], seed=kwargs["随机种子"],
            audio_mode=kwargs["音轨策略"], continue_previous=False,
            first_frame=image, previous_video=previous,
        )
        video, last_frame, raw_report = result
        report = json.loads(raw_report)
        source = Path(report["temporary_video"])
        subfolder = "H3分镜/单镜片段"
        destination_dir = Path(folder_paths.get_output_directory()) / subfolder
        destination_dir.mkdir(parents=True, exist_ok=True)
        filename = f"镜头{index+1}_{uuid.uuid4().hex[:12]}.mp4"
        destination = destination_dir / filename
        shutil.copyfile(source, destination)
        video = InputImpl.VideoFromFile(str(destination))
        report.update(shot_index=index + 1, width=width, height=height,
                      saved_video=str(destination))
        text = json.dumps(report, ensure_ascii=False)
        return {"ui": {"images": [{"filename": filename, "subfolder": subfolder, "type": "output"}],
                       "animated": [True], "text": [text]},
                "result": (video, last_frame, text)}


class H3JoinVideos:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"镜头1": ("VIDEO",)}, "optional": {
            "镜头2": ("VIDEO",), "镜头3": ("VIDEO",)}}
    RETURN_TYPES = ("VIDEO",)
    RETURN_NAMES = ("完整视频",)
    FUNCTION = "execute"
    CATEGORY = "H3/整稿与分镜"

    def execute(self, **kwargs):
        from comfy_extras.nodes_video import ConcatenateVideo
        videos = [kwargs[key] for key in ("镜头1", "镜头2", "镜头3") if kwargs.get(key) is not None]
        dimensions = [tuple(video.get_dimensions()) for video in videos]
        if len(set(dimensions)) != 1:
            raise ValueError(f"镜头画幅不一致，未执行合片：{dimensions}。")
        fps = [float(video.get_frame_rate()) for video in videos]
        if max(fps) - min(fps) > 0.001:
            raise ValueError("镜头帧率不一致，未执行合片。")
        return ConcatenateVideo.execute(
            videos={f"video{i}": [video] for i, video in enumerate(videos)}, codec=["auto"]).result


RENDER_NODE_CLASS_MAPPINGS = {cls.__name__: cls for cls in (
    H3ShotSettings, H3AccelerationSwitch, H3EightSteps, H3SamplingControl,
    H3ModelLoader, H3RenderShot, H3JoinVideos,
)}
RENDER_NODE_DISPLAY_NAME_MAPPINGS = {
    "H3ShotSettings": "每镜时长", "H3AccelerationSwitch": "加速 LoRA 开关",
    "H3EightSteps": "8步采样", "H3SamplingControl": "实际采样步数",
    "H3ModelLoader": "H3 视频模型", "H3RenderShot": "镜头生成与预览",
    "H3JoinVideos": "按顺序合片",
}
