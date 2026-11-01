"""Shared H3 canvas sizing and visible, sequential shot input selection.

These nodes keep one canvas for the entire film and adapt reference images
without stretching them. Shot previews are normal ComfyUI intermediate UI
results, so the actual next first frame appears as soon as that input is ready.
"""

from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path

import folder_paths
import nodes
import torch
import torch.nn.functional as F


SAMPLE_IMAGE = "freedidi_MiniMaxH3_Example_REF_Image.jpg"
ASPECTS = ("跟随图片", "16:9 横屏", "9:16 竖屏", "1:1 方形", "4:3 横屏", "3:4 竖屏", "自定义")
RESOLUTIONS = ("384P 快速", "480P", "720P", "1080P")
FIT_MODES = ("等比补边", "等比裁切")
SOURCE_MODES = ("自动接上一镜末帧", "使用原始参考图", "手动指定首帧")


def _align(value: float, *, limit: int = 2048) -> int:
    return min(limit, max(64, int(math.floor(float(value) / 32 + 0.5)) * 32))


def _image_shape(image) -> tuple[int, int]:
    if not torch.is_tensor(image) or image.ndim != 4 or image.shape[0] < 1:
        raise ValueError("请连接有效的首帧图片。")
    height, width = int(image.shape[1]), int(image.shape[2])
    if width < 1 or height < 1 or image.shape[3] not in (1, 3, 4):
        raise ValueError("首帧图片的宽高或颜色通道无效。")
    return width, height


def resolve_canvas(reference, aspect: str, resolution: str,
                   custom_width: int, custom_height: int) -> tuple[int, int, str]:
    source_width, source_height = _image_shape(reference)
    if aspect not in ASPECTS or resolution not in RESOLUTIONS:
        raise ValueError("请选择有效的画幅与生成档位。")
    if aspect == "自定义":
        if not 64 <= int(custom_width) <= 2048 or not 64 <= int(custom_height) <= 2048:
            raise ValueError("自定义宽高应在64～2048之间。")
        width, height = _align(custom_width), _align(custom_height)
        detail = f"自定义{custom_width}×{custom_height}，按H3的32倍数对齐"
    else:
        ratio = {
            "跟随图片": source_width / source_height,
            "16:9 横屏": 16 / 9, "9:16 竖屏": 9 / 16,
            "1:1 方形": 1.0, "4:3 横屏": 4 / 3, "3:4 竖屏": 3 / 4,
        }[aspect]
        nominal_short = {"384P 快速": 384, "480P": 480, "720P": 720, "1080P": 1080}[resolution]
        short = _align(nominal_short)
        raw_width, raw_height = ((short * ratio, short) if ratio >= 1 else (short, short / ratio))
        # Bound pathological reference ratios without letting one edge exceed
        # H3's exposed local-workflow limit. The effective size is reported.
        scale = min(1.0, 2048 / max(raw_width, raw_height))
        width, height = _align(raw_width * scale), _align(raw_height * scale)
        detail = f"{aspect}，{resolution}生成档位，32倍数对齐"
        if scale < 1:
            detail += "；因长边上限2048已降低尺寸"
    return width, height, detail


def resolve_text_canvas(aspect: str, resolution: str,
                        custom_width: int, custom_height: int) -> tuple[int, int, str]:
    """Resolve a text-only canvas without making or reading a reference image."""
    if aspect not in ASPECTS or resolution not in RESOLUTIONS:
        raise ValueError("请选择有效的画幅与生成档位。")
    if aspect == "跟随图片":
        raise ValueError("文生视频没有参考图，请选择明确比例或自定义画幅。")
    if aspect == "自定义":
        if (type(custom_width) is not int or type(custom_height) is not int or
                not 64 <= custom_width <= 2048 or not 64 <= custom_height <= 2048):
            raise ValueError("自定义宽高应为64～2048之间的整数。")
        return (_align(custom_width), _align(custom_height),
                f"自定义{custom_width}×{custom_height}，按H3的32倍数对齐")
    ratio = {"16:9 横屏": 16 / 9, "9:16 竖屏": 9 / 16, "1:1 方形": 1.0,
             "4:3 横屏": 4 / 3, "3:4 竖屏": 3 / 4}[aspect]
    short = _align({"384P 快速": 384, "480P": 480, "720P": 720, "1080P": 1080}[resolution])
    raw_width, raw_height = ((short * ratio, short) if ratio >= 1 else (short, short / ratio))
    scale = min(1.0, 2048 / max(raw_width, raw_height))
    width, height = _align(raw_width * scale), _align(raw_height * scale)
    detail = f"{aspect}，{resolution}生成档位，32倍数对齐"
    if scale < 1:
        detail += "；因长边上限2048已降低尺寸"
    return width, height, detail


def fit_reference(image, width: int, height: int, mode: str):
    """Return one RGB CPU image, preserving geometry by padding or cropping."""
    source_width, source_height = _image_shape(image)
    width, height = int(width), int(height)
    if width < 64 or height < 64 or width % 32 or height % 32:
        raise ValueError("全片宽高必须是至少64的32倍数，请使用全局画幅节点。")
    if mode not in FIT_MODES:
        raise ValueError("首帧适配方式应为等比补边或等比裁切。")
    pixels = image[:1].detach().to(device="cpu", dtype=torch.float32)
    if pixels.shape[-1] == 1:
        pixels = pixels.repeat(1, 1, 1, 3)
    elif pixels.shape[-1] == 4:
        alpha = pixels[..., 3:4].clamp(0, 1)
        pixels = pixels[..., :3] * alpha + 0.05 * (1 - alpha)
    pixels = pixels[..., :3].movedim(-1, 1)
    if mode == "等比补边":
        scale = min(width / source_width, height / source_height)
        scaled_width = min(width, max(1, round(source_width * scale)))
        scaled_height = min(height, max(1, round(source_height * scale)))
    else:
        scale = max(width / source_width, height / source_height)
        scaled_width = max(width, round(source_width * scale))
        scaled_height = max(height, round(source_height * scale))
    if (source_width, source_height) != (scaled_width, scaled_height):
        pixels = F.interpolate(pixels, size=(scaled_height, scaled_width), mode="bicubic",
                               align_corners=False, antialias=True)
    if mode == "等比补边":
        left = (width - scaled_width) // 2
        top = (height - scaled_height) // 2
        pixels = F.pad(pixels, (left, width - scaled_width - left,
                                top, height - scaled_height - top), value=0.05)
    else:
        left = (scaled_width - width) // 2
        top = (scaled_height - height) // 2
        pixels = pixels[..., top:top + height, left:left + width]
    return pixels.movedim(1, -1).clamp(0, 1).contiguous()


def last_video_frame(video):
    """Decode only the final frame window rather than the complete clip."""
    try:
        frame_rate = float(video.get_frame_rate())
        duration = float(video.get_duration())
        if not math.isfinite(frame_rate) or frame_rate <= 0 or not math.isfinite(duration) or duration <= 0:
            raise ValueError
        window = min(duration, 1 / frame_rate + 0.0001)
        tail = video.as_trimmed(start_time=max(0.0, duration - window),
                                duration=window, strict_duration=False)
        if tail is None:
            raise ValueError
        frames = tail.get_components().images
        _image_shape(frames)
        return frames[-1:].detach().to(device="cpu", dtype=torch.float32).contiguous()
    except Exception as exc:
        raise ValueError("无法读取上一镜末帧；请检查上一镜是否已完整生成。") from exc


def _manual_images():
    root = folder_paths.get_input_directory()
    if not os.path.isdir(root):
        return []
    files = [name for name in os.listdir(root) if os.path.isfile(os.path.join(root, name))]
    return sorted(folder_paths.filter_files_content_types(files, ["image"]))


class H3CanvasSettings:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "原参考图": ("IMAGE",),
            "画幅": (list(ASPECTS), {"default": "跟随图片", "tooltip": "一次选择，全片所有镜头共用。跟随图片会保留参考图横竖比例。"}),
            "生成档位": (list(RESOLUTIONS), {"default": "480P", "tooltip": "按短边选择生成尺寸，并对齐为32倍数。720P实际短边736，1080P实际短边1088；原生高分辨率会增加显存和耗时。"}),
            "自定义宽度": ("INT", {"default": 640, "min": 64, "max": 2048, "step": 32}),
            "自定义高度": ("INT", {"default": 384, "min": 64, "max": 2048, "step": 32}),
            "适配方式": (list(FIT_MODES), {"default": "等比补边", "tooltip": "补边保留全图，裁切填满画布；两种方式都不拉伸人物。"}),
        }}

    RETURN_TYPES = ("INT", "INT", "STRING", "STRING")
    RETURN_NAMES = ("全片宽度", "全片高度", "首帧适配方式", "画幅说明")
    FUNCTION = "resolve"
    CATEGORY = "H3/画幅与首帧"
    DESCRIPTION = "所有镜头共用一套画幅与生成尺寸，避免横竖混用到最后合片才报错。"

    def resolve(self, 原参考图, 画幅="跟随图片", 生成档位="480P", 自定义宽度=640,
                自定义高度=384, 适配方式="等比补边"):
        if 适配方式 not in FIT_MODES:
            raise ValueError("请选择有效的首帧适配方式。")
        width, height, detail = resolve_canvas(原参考图, 画幅, 生成档位, 自定义宽度, 自定义高度)
        source_width, source_height = _image_shape(原参考图)
        report = f"原图{source_width}×{source_height} → 全片统一{width}×{height}；{detail}；{适配方式}。"
        if width * height >= 1280 * 720:
            report += "原生高分辨率生成资源占用较高，16GB显卡建议先用480P短片验证。"
        return width, height, 适配方式, report


class H3ShotInput:
    @classmethod
    def INPUT_TYPES(cls):
        images = _manual_images()
        default = SAMPLE_IMAGE if SAMPLE_IMAGE in images else images[0] if images else ""
        return {"required": {
            "原参考图": ("IMAGE",),
            "宽度": ("INT", {"forceInput": True}),
            "高度": ("INT", {"forceInput": True}),
            "适配方式": ("STRING", {"forceInput": True}),
            "首帧来源": (list(SOURCE_MODES), {"default": "自动接上一镜末帧", "tooltip": "自动会在上一镜完成后接入其末帧；也可选原参考图或为当前镜头手动上传首帧。"}),
            "手动首帧": (images, {"default": default, "image_upload": True}),
        }, "optional": {
            "上一镜视频": ("VIDEO",),
            "上一镜末帧": ("IMAGE",),
        }, "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"}}

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("本镜实际首帧", "首帧来源说明")
    FUNCTION = "select"
    CATEGORY = "H3/画幅与首帧"
    OUTPUT_NODE = False
    DESCRIPTION = "显示本镜真正接入的首帧。上一镜视频为执行依赖，即使改用手动首帧也保持镜头顺序生成。"

    @classmethod
    def IS_CHANGED(cls, 原参考图=None, 宽度=None, 高度=None, 适配方式=None,
                   首帧来源="自动接上一镜末帧", 手动首帧="", **kwargs):
        if 首帧来源 != "手动指定首帧":
            return "not-manual"
        try:
            path = folder_paths.get_annotated_filepath(手动首帧)
            return hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except OSError:
            return "missing-manual"

    def select(self, 原参考图, 宽度, 高度, 适配方式, 首帧来源="自动接上一镜末帧",
               手动首帧="", 上一镜视频=None, 上一镜末帧=None, prompt=None, extra_pnginfo=None):
        if 首帧来源 not in SOURCE_MODES:
            raise ValueError("请选择有效的首帧来源。")
        if 上一镜视频 is not None:
            # Check before generating this next shot rather than after all
            # segments have finished at the final SaveVideo node.
            previous_size = tuple(int(value) for value in 上一镜视频.get_dimensions())
            if previous_size != (int(宽度), int(高度)):
                raise ValueError(f"上一镜为{previous_size[0]}×{previous_size[1]}，全片设置为{宽度}×{高度}。请统一全局画幅后重新运行，不能混合横竖镜头。")
        if 首帧来源 == "手动指定首帧":
            if not 手动首帧 or not folder_paths.exists_annotated_filepath(手动首帧):
                raise ValueError("当前镜头已选手动首帧，请先上传或选择一张有效图片。")
            reference = nodes.LoadImage().load_image(手动首帧)[0]
            origin = f"手动首帧：{手动首帧}"
        elif 首帧来源 == "自动接上一镜末帧" and 上一镜视频 is not None:
            reference = 上一镜末帧 if 上一镜末帧 is not None else last_video_frame(上一镜视频)
            origin = "自动接入上一镜末帧"
        else:
            reference = 原参考图
            origin = "原始参考图" if 首帧来源 != "自动接上一镜末帧" else "首镜无上一镜，使用原始参考图"
        original_width, original_height = _image_shape(reference)
        actual = fit_reference(reference, 宽度, 高度, 适配方式)
        report = f"{origin}；{original_width}×{original_height} → 本镜首帧{宽度}×{高度}；{适配方式}。"
        preview = nodes.PreviewImage().save_images(actual, filename_prefix="H3_本镜首帧",
                                                   prompt=prompt, extra_pnginfo=extra_pnginfo)
        return {"ui": preview["ui"], "result": (actual, report)}


SCENE_NODE_CLASS_MAPPINGS = {
    "H3CanvasSettings": H3CanvasSettings,
    "H3ShotInput": H3ShotInput,
}
SCENE_NODE_DISPLAY_NAME_MAPPINGS = {
    "H3CanvasSettings": "全片画幅与清晰度",
    "H3ShotInput": "本镜首帧与预览",
}
