"""Bounded, file-backed dialogue audio for native H3 audio keyframe guidance."""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import math
from pathlib import Path
import threading

import folder_paths

MODE = "对白引导并使用原音轨"
MISSING = "未选择音频"
SAMPLE_RATE = 32000
MAX_SECONDS = 1500
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_CACHE_BYTES = 32 * 1024 * 1024
MAX_CACHE_ITEMS = 8
EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".aiff", ".aif"}
_LOCK = threading.RLock()
_CACHE = OrderedDict()
_CACHE_BYTES = 0
_HASH_CACHE = OrderedDict()


def _path(filename):
    if not isinstance(filename, str) or not filename or filename == MISSING:
        raise ValueError("请先上传或选择完整对白音频。")
    filename = filename.removesuffix(" [input]")
    root = Path(folder_paths.get_input_directory()).resolve()
    result = (root / filename).resolve()
    if not result.is_relative_to(root) or not result.is_file() or result.suffix.lower() not in EXTENSIONS:
        raise ValueError("对白音频不存在或格式无效，请重新选择输入目录中的音频。")
    if not 0 < result.stat().st_size <= MAX_FILE_BYTES:
        raise ValueError("对白音频应为不超过256MB的有效文件。")
    return result


def _file_hash(path):
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    with _LOCK:
        if key in _HASH_CACHE:
            _HASH_CACHE.move_to_end(key)
            return _HASH_CACHE[key]
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    value = digest.hexdigest()
    with _LOCK:
        _HASH_CACHE[key] = value
        while len(_HASH_CACHE) > 16:
            _HASH_CACHE.popitem(last=False)
    return value


def create_dialogue_track(filename, enabled=True):
    if not isinstance(enabled, bool):
        raise ValueError("对白音轨开关无效。")
    if not enabled:
        return {"enabled": False, "mode": MODE}
    import av
    path = _path(filename)
    with av.open(str(path)) as source:
        if not source.streams.audio:
            raise ValueError("所选文件没有可用音频。")
        stream = source.streams.audio[0]
        seconds = float(stream.duration * stream.time_base) if stream.duration is not None else (
            float(source.duration / av.time_base) if source.duration is not None else None)
    if seconds is None or not math.isfinite(seconds) or not 0 < seconds <= MAX_SECONDS:
        raise ValueError("对白音轨须有明确时长且不超过1500秒，请先导出为WAV或FLAC。")
    root = Path(folder_paths.get_input_directory()).resolve()
    return {"enabled": True, "mode": MODE, "filename": path.relative_to(root).as_posix(),
            "sha256": _file_hash(path), "seconds": seconds}


def normalize_dialogue_track(track):
    if track is None:
        return None
    if not isinstance(track, dict) or not isinstance(track.get("enabled"), bool) or track.get("mode") != MODE:
        raise ValueError("对白音轨配置无效，请重新连接对白音轨节点。")
    if not track["enabled"]:
        return {"enabled": False, "mode": MODE}
    seconds = track.get("seconds")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 0 < seconds <= MAX_SECONDS:
        raise ValueError("对白音轨时长无效。")
    path = _path(track.get("filename"))
    if _file_hash(path) != track.get("sha256"):
        raise ValueError("对白音频已修改，请重新运行对白音轨节点。")
    return {key: track[key] for key in ("enabled", "mode", "filename", "sha256", "seconds")}


def clear_dialogue_cache():
    global _CACHE_BYTES
    with _LOCK:
        _CACHE.clear()
        _HASH_CACHE.clear()
        _CACHE_BYTES = 0


def dialogue_segment(track, offset_seconds, frame_count, fps=24):
    """Decode only this shot's time window; pad missing source audio with silence."""
    import av
    import torch
    global _CACHE_BYTES
    track = normalize_dialogue_track(track)
    if not track or not track["enabled"]:
        return None
    if (isinstance(offset_seconds, bool) or not isinstance(offset_seconds, (int, float)) or
            not math.isfinite(offset_seconds) or offset_seconds < 0 or offset_seconds > MAX_SECONDS or
            isinstance(frame_count, bool) or not isinstance(frame_count, int) or
            isinstance(fps, bool) or not isinstance(fps, (int, float)) or not math.isfinite(fps) or fps <= 0 or
            not 0 < frame_count / fps <= 15):
        raise ValueError("对白音频裁剪时间无效。")
    start_sample = round(offset_seconds * SAMPLE_RATE)
    sample_count = round(frame_count / fps * SAMPLE_RATE)
    key = (track["sha256"], start_sample, sample_count)
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
            return {"waveform": cached.clone(), "sample_rate": SAMPLE_RATE}
    waveform = torch.zeros((1, 2, sample_count), dtype=torch.float32, device="cpu")
    if offset_seconds < track["seconds"]:
        path = _path(track["filename"])
        with av.open(str(path)) as source:
            if not source.streams.audio:
                raise ValueError("对白文件中没有可用音轨。")
            stream = source.streams.audio[0]
            origin = float((stream.start_time or 0) * stream.time_base)
            if offset_seconds:
                # Decode a short preroll so resampling filters have the same
                # history across adjacent cuts, rather than warming up at every shot.
                seek_seconds = max(0.0, offset_seconds - 0.25)
                source.seek(max(0, math.floor((origin + seek_seconds) / float(stream.time_base))), stream=stream, backward=True)
            resampler = av.AudioResampler(format="fltp", layout="stereo", rate=SAMPLE_RATE)
            decoded_samples = 0
            complete = False

            def consume(frame):
                nonlocal complete
                if frame.pts is None or frame.time_base is None:
                    raise ValueError("对白音频缺少时间戳，请先转换为WAV或FLAC。")
                position = round((float(frame.pts * frame.time_base) - origin) * SAMPLE_RATE)
                values = frame.to_ndarray()
                left, right = max(position, start_sample), min(position + values.shape[-1], start_sample + sample_count)
                if right > left:
                    waveform[0, :, left - start_sample:right - start_sample] = torch.from_numpy(
                        values[:, left - position:right - position].copy())
                if position + values.shape[-1] >= start_sample + sample_count:
                    complete = True

            for frame in source.decode(stream):
                decoded_samples += frame.samples
                # A malformed stream cannot turn a 15-second request into an unbounded decode.
                if decoded_samples / max(1, frame.sample_rate) > 45:
                    raise ValueError("对白音频时间轴异常，请重新导出为WAV或FLAC。")
                for converted in resampler.resample(frame):
                    consume(converted)
                if complete:
                    break
            if not complete:
                for converted in resampler.resample(None):
                    consume(converted)
    waveform = torch.nan_to_num(waveform).clamp_(-1, 1).contiguous()
    size = waveform.numel() * waveform.element_size()
    with _LOCK:
        # Replace under the same lock so concurrent identical decodes cannot double-count bytes.
        previous = _CACHE.pop(key, None)
        if previous is not None:
            _CACHE_BYTES -= previous.numel() * previous.element_size()
        while _CACHE and (_CACHE_BYTES + size > MAX_CACHE_BYTES or len(_CACHE) >= MAX_CACHE_ITEMS):
            _, removed = _CACHE.popitem(last=False)
            _CACHE_BYTES -= removed.numel() * removed.element_size()
        if size <= MAX_CACHE_BYTES:
            _CACHE[key] = waveform
            _CACHE_BYTES += size
    return {"waveform": waveform.clone(), "sample_rate": SAMPLE_RATE}


class H3DialogueAudio:
    @classmethod
    def INPUT_TYPES(cls):
        root = Path(folder_paths.get_input_directory())
        options = sorted(path.name for path in root.iterdir() if path.is_file() and path.suffix.lower() in EXTENSIONS) if root.is_dir() else []
        return {"required": {"启用": ("BOOLEAN", {"default": False}),
                             "对白文件": ([MISSING, *options], {"default": MISSING, "audio_upload": True})},
                "optional": {"配音数据": ("STRING", {"default": "{}"})}}

    RETURN_TYPES = ("H3_DIALOGUE_TRACK",)
    RETURN_NAMES = ("对白音轨",)
    FUNCTION = "execute"
    CATEGORY = "H3/声音"
    DESCRIPTION = "从剧本生成对白配音，或导入已有录音；逐句调整时间和语速，按镜头引导并用于成片音轨。"

    @classmethod
    def VALIDATE_INPUTS(cls, 启用, 对白文件):
        if not 启用:
            return True
        try:
            _path(对白文件)
            return True
        except ValueError as exc:
            return str(exc)

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return _file_hash(_path(kwargs.get("对白文件"))) if kwargs.get("启用", False) else "disabled"

    def execute(self, **kwargs):
        return (create_dialogue_track(kwargs.get("对白文件"), kwargs.get("启用", False)),)


DIALOGUE_NODE_CLASS_MAPPINGS = {"H3DialogueAudio": H3DialogueAudio}
DIALOGUE_NODE_DISPLAY_NAME_MAPPINGS = {"H3DialogueAudio": "对白配音"}
