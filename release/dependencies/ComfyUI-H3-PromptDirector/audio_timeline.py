"""Model-free dialogue mixing and lossless-video remuxing for saved H3 runs."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from urllib.parse import urlsplit

import folder_paths

SAMPLE_RATE = 32000
MAX_SECONDS = 1500
MAX_LINES = 500
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_MEDIA_BYTES = 256 * 1024 * 1024
_ROUTE_LIMIT = asyncio.Semaphore(2)


class TimelineError(ValueError):
    def __init__(self, message, **details):
        super().__init__(message)
        self.details = details


def _number(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise TimelineError(f"{name}应为有效数值。")
    if not minimum <= value <= maximum:
        raise TimelineError(f"{name}应为{minimum:g}～{maximum:g}。")
    return float(value)


def _input_root():
    return Path(folder_paths.get_input_directory()).resolve()


def _input_audio(item):
    if not isinstance(item, dict) or item.get("type", "input") != "input":
        raise TimelineError("请使用已上传到本机的音频。")
    name, subfolder = item.get("filename"), item.get("subfolder", "")
    if not isinstance(name, str) or not name or not isinstance(subfolder, str):
        raise TimelineError("音频文件信息不完整。")
    root = _input_root()
    path = (root / subfolder / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise TimelineError("音频文件不存在或路径无效。")
    if path.stat().st_size > MAX_MEDIA_BYTES:
        raise TimelineError("音轨文件不能超过256MB。")
    return path


def _metadata(path, kind="input"):
    root = _input_root() if kind == "input" else Path(folder_paths.get_output_directory()).resolve()
    path = Path(path).resolve()
    if not path.is_relative_to(root):
        raise TimelineError("媒体文件不在ComfyUI数据目录中。")
    relative = path.relative_to(root)
    return {"filename": relative.name, "subfolder": relative.parent.as_posix(), "type": kind}


def _probe(path):
    import av
    try:
        with av.open(str(path)) as container:
            streams = []
            for stream in container.streams:
                duration = float(stream.duration * stream.time_base) if stream.duration is not None and stream.time_base else None
                streams.append({"type": stream.type, "duration": duration, "codec": stream.codec_context.name,
                                "sample_rate": getattr(stream.codec_context, "sample_rate", None),
                                "frames": stream.frames, "time_base": str(stream.time_base),
                                "rate": str(stream.average_rate) if stream.type == "video" else None})
            duration = float(container.duration / av.time_base) if container.duration is not None else None
            return {"duration": duration, "streams": streams}
    except (OSError, ValueError, av.error.FFmpegError) as exc:
        raise TimelineError("无法读取媒体文件，请使用有效的音视频文件。") from exc


def _audio_seconds(path):
    info = _probe(path)
    stream = next((stream for stream in info["streams"] if stream["type"] == "audio"), None)
    if stream is None:
        raise TimelineError("文件中没有可用音轨。")
    seconds = stream["duration"] or info["duration"]
    if seconds is None or not 0 < seconds <= MAX_SECONDS:
        raise TimelineError("无法确定音频时长，或时长超过25分钟。")
    return seconds


def _ffmpeg(arguments, timeout=240):
    import imageio_ffmpeg
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *map(str, arguments)]
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    except subprocess.TimeoutExpired as exc:
        raise TimelineError("音轨处理超时，请缩短音频或稍后重试。") from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()[-1200:]
        raise TimelineError("音轨处理失败。" + (" " + detail if detail else ""))


def _write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _normalize_lines(payload):
    if not isinstance(payload, dict):
        raise TimelineError("对白时间轴格式无效。")
    total = _number(payload.get("total_seconds"), "总时长", 1 / SAMPLE_RATE, MAX_SECONDS)
    sample_count = round(total * SAMPLE_RATE)
    total = sample_count / SAMPLE_RATE
    lines = payload.get("lines")
    if not isinstance(lines, list) or not 1 <= len(lines) <= MAX_LINES:
        raise TimelineError("请提供1～500句对白。")
    result, identities = [], set()
    for index, item in enumerate(lines):
        if not isinstance(item, dict):
            raise TimelineError("对白条目格式无效。")
        identity = str(item.get("id", item.get("dialogue_id", index + 1)))
        if identity in identities or len(identity) > 100:
            raise TimelineError("对白编号重复或无效。")
        identities.add(identity)
        path = _input_audio(item.get("file") or item.get("audio"))
        original_seconds = _audio_seconds(path)
        start = _number(item.get("start", 0), f"{identity}开始时间", 0, total)
        start_sample = round(start * SAMPLE_RATE)
        start = start_sample / SAMPLE_RATE
        speed = _number(item.get("speed", 1), f"{identity}语速", 0.5, 2)
        volume = _number(item.get("volume", 1), f"{identity}音量", 0, 2)
        slot_end = _number(item.get("slot_end", item.get("end", total)), f"{identity}结束边界", start, total)
        duration = original_seconds / speed
        if duration > slot_end - start + 1 / SAMPLE_RATE:
            needed = original_seconds / (slot_end - start) if slot_end > start else None
            if payload.get("auto_fit") is True and needed is not None and 0.5 <= needed <= 2:
                speed, duration = needed, slot_end - start
            else:
                raise TimelineError(f"{identity}音频超出该句时间范围，请调整开始时间、语速或镜头时长。",
                                    line_id=identity, required_speed=round(needed, 4) if needed else None,
                                    audio_seconds=original_seconds, available_seconds=slot_end - start)
        result.append({"id": identity, "path": path, "file": _metadata(path), "start": start,
                       "start_sample": start_sample, "end": start + duration, "slot_end": slot_end,
                       "speed": speed, "volume": volume, "source_seconds": original_seconds})
    budget = max(30.0, total * 4)
    if sum(row["source_seconds"] for row in result) > budget or sum(row["source_seconds"] / row["speed"] for row in result) > budget:
        raise TimelineError("对白音频总量超过时间轴处理上限，请减少重复或重叠的长音频。")
    return total, sample_count, result


def _mix_group(lines, destination, sample_count, temporary):
    args, filters = [], []
    for index, row in enumerate(lines):
        args.extend(("-i", row["path"]))
        filters.append(f"[{index}:a:0]aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,"
                       f"atempo={row['speed']:.12g},volume={row['volume']:.12g},adelay={row['start_sample']}S:all=1[a{index}]")
    labels = "".join(f"[a{index}]" for index in range(len(lines)))
    filters.append(f"{labels}amix=inputs={len(lines)}:normalize=0:dropout_transition=0,"
                   f"apad=whole_len={sample_count},atrim=end_sample={sample_count},asetpts=N/SR/TB[out]")
    script = temporary / f"filter-{uuid.uuid4().hex}.txt"
    script.write_text(";\n".join(filters), encoding="utf-8")
    _ffmpeg([*args, "-filter_complex_script", script, "-map", "[out]", "-ar", SAMPLE_RATE,
             "-ac", "2", "-c:a", "pcm_s16le", destination])


def mix_dialogue(payload):
    total, sample_count, lines = _normalize_lines(payload)
    task = str(payload.get("task_id", ""))
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", task):
        raise TimelineError("混音任务编号无效。")
    directory = _input_root() / "h3_dialogue_timeline"
    directory.mkdir(parents=True, exist_ok=True)
    name = f"h3_dialogue_{hashlib.sha256(task.encode()).hexdigest()[:12]}_{uuid.uuid4().hex[:12]}"
    destination = directory / (name + ".wav")
    with tempfile.TemporaryDirectory(prefix="h3-audio-mix-") as scratch:
        temporary = Path(scratch)
        prepared = []
        for index, row in enumerate(lines):
            line_audio = temporary / f"line-{index}.wav"
            _ffmpeg(["-i", row["path"], "-map", "0:a:0", "-vn", "-af",
                     f"aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,atempo={row['speed']:.12g},volume={row['volume']:.12g}",
                     "-ar", SAMPLE_RATE, "-ac", "2", "-c:a", "pcm_s16le", line_audio])
            # atempo works in overlap windows; its actual sample count can
            # differ slightly from source_duration/speed. Validate the saved
            # result before final trim so a word can never be silently cut.
            actual = _audio_seconds(line_audio)
            if actual > row["slot_end"] - row["start"] + 1 / SAMPLE_RATE:
                required = row["speed"] * actual / (row["slot_end"] - row["start"]) if row["slot_end"] > row["start"] else None
                raise TimelineError(f"{row['id']}调速后音频超出时间范围，请稍增语速或延长对白区间。",
                                    line_id=row["id"], required_speed=round(required, 4) if required else None,
                                    audio_seconds=actual, available_seconds=row["slot_end"] - row["start"])
            row["end"] = row["start"] + actual
            prepared.append({**row, "path": line_audio, "speed": 1.0, "volume": 1.0})
        partial = temporary / "mixed.wav"
        if len(prepared) <= 24:
            _mix_group(prepared, partial, sample_count, temporary)
        else:
            groups = []
            prepared.sort(key=lambda row: row["start_sample"])
            for offset in range(0, len(prepared), 24):
                chunk = prepared[offset:offset + 24]
                chunk_start = min(row["start_sample"] for row in chunk)
                chunk_end = max(math.ceil(row["end"] * SAMPLE_RATE) for row in chunk)
                chunk_lines = [{**row, "start_sample": row["start_sample"] - chunk_start} for row in chunk]
                group = temporary / f"group-{offset}.wav"
                _mix_group(chunk_lines, group, chunk_end - chunk_start, temporary)
                groups.append({"path": group, "speed": 1.0, "volume": 1.0, "start_sample": chunk_start})
            _mix_group(groups, partial, sample_count, temporary)
        shutil.copyfile(partial, destination.with_suffix(".partial.wav"))
        os.replace(destination.with_suffix(".partial.wav"), destination)
    receipt = {"task_id": task, "audio": _metadata(destination), "seconds": total, "sample_rate": SAMPLE_RATE,
               "channels": 2, "samples": sample_count, "created_at": time.time(),
               "lines": [{key: value for key, value in row.items() if key != "path"} for row in lines]}
    _write_json(destination.with_suffix(".json"), receipt)
    return receipt


def save_uploaded_audio(content, filename="audio.wav"):
    if not content or len(content) > MAX_UPLOAD_BYTES:
        raise TimelineError("请上传不超过100MB的音频。")
    extension = Path(str(filename)).suffix.lower()
    if extension not in (".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac", ".opus", ".mp4", ".webm"):
        raise TimelineError("不支持此音频格式。")
    directory = _input_root() / "h3_dialogue_upload"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / ("dialogue_" + uuid.uuid4().hex + ".wav")
    with tempfile.TemporaryDirectory(prefix="h3-audio-upload-") as scratch:
        source = Path(scratch) / ("source" + extension)
        source.write_bytes(content)
        seconds = _audio_seconds(source)
        partial = Path(scratch) / "converted.wav"
        _ffmpeg(["-i", source, "-map", "0:a:0", "-vn", "-ar", SAMPLE_RATE, "-ac", "2", "-c:a", "pcm_s16le", partial])
        shutil.copyfile(partial, destination)
    return {"audio": _metadata(destination), "seconds": seconds, "sample_rate": SAMPLE_RATE, "channels": 2}


def _manifest(run_id):
    if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise TimelineError("工作台任务编号无效。")
    root = Path(folder_paths.get_output_directory()).resolve()
    directory = (root / "H3分镜" / "工作台" / run_id).resolve()
    if not directory.is_relative_to(root):
        raise TimelineError("任务路径无效。")
    path = directory / "分镜任务.json"
    if not path.is_file():
        raise TimelineError("未找到工作台任务记录。")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("run_id") != run_id:
        raise TimelineError("工作台任务记录不匹配。")
    if value.get("status") in ("running", "generating", "planning", "joining"):
        raise TimelineError("该任务正在生成，请完成或停止后再调整成片音轨。")
    video = value.get("final_video") or {}
    final = value.get("final_path")
    if not final and video.get("type") == "output":
        final = root / str(video.get("subfolder", "")) / str(video.get("filename", ""))
    final = Path(final or "").resolve()
    if not final.is_relative_to(directory) or not final.is_file() or final.suffix.lower() != ".mp4":
        raise TimelineError("该任务尚无可调整音轨的完整MP4成片。")
    return path, value, final


@contextmanager
def _studio_guard():
    from .studio_nodes import _RUN_LOCK
    if not _RUN_LOCK.acquire(blocking=False):
        raise TimelineError("工作台仍在生成，请停止或等待完成后再保存音轨。")
    try:
        yield
    finally:
        _RUN_LOCK.release()


def mux_dialogue(payload):
    if not isinstance(payload, dict):
        raise TimelineError("成片音轨参数无效。")
    track = _input_audio(payload.get("audio") or payload.get("track"))
    replace = payload.get("replace", payload.get("overwrite", False))
    if not isinstance(replace, bool):
        raise TimelineError("覆盖保存参数应为开关。")
    with _studio_guard():
        manifest_path, manifest, original = _manifest(payload.get("run_id"))
        info = _probe(original)
        video_stream = next((stream for stream in info["streams"] if stream["type"] == "video"), None)
        if video_stream is None:
            raise TimelineError("原成片中没有视频画面。")
        duration = video_stream["duration"] or info["duration"]
        if duration is None or not 0 < duration <= MAX_SECONDS:
            raise TimelineError("原成片时长无效或超过25分钟。")
        track_seconds = _audio_seconds(track)
        if abs(track_seconds - duration) > max(1 / SAMPLE_RATE, 0.002):
            raise TimelineError("音轨总时长与成片不一致，请按成片时长重新混音。", video_seconds=duration, audio_seconds=track_seconds)
        stamp = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        destination = original.with_name(original.stem + f"_配音_{stamp}.mp4")
        partial = destination.with_suffix(".partial.mp4")
        try:
            _ffmpeg(["-i", original, "-i", track, "-map", "0:v:0", "-map", "1:a:0", "-map_metadata", "0",
                     "-c:v", "copy", "-c:a", "aac", "-ar", SAMPLE_RATE, "-ac", "2", "-b:a", "192k",
                     "-t", f"{duration:.12g}", "-movflags", "use_metadata_tags+faststart", partial])
            new_info = _probe(partial)
            output_video = next((stream for stream in new_info["streams"] if stream["type"] == "video"), None)
            if output_video is None or output_video["codec"] != video_stream["codec"]:
                raise TimelineError("成片封装校验失败，原文件已保留。")
            os.replace(partial, destination)
            record = {"created_at": time.time(), "audio": _metadata(track), "video": _metadata(destination, "output"),
                      "seconds": duration, "replace": replace, "video_codec": video_stream["codec"], "video_reencoded": False}
            if replace:
                backup = original.with_suffix(original.suffix + ".bak")
                if not backup.exists():
                    backup_partial = backup.with_suffix(backup.suffix + ".partial")
                    shutil.copyfile(original, backup_partial)
                    os.replace(backup_partial, backup)
                record["backup"] = _metadata(backup, "output")
                # Extract the original soundtrack once, independently of future
                # replacement tracks. A silent source retains its MP4 backup.
                original_audio = original.with_name(original.stem + "_原音轨.bak.wav")
                if not original_audio.exists() and any(stream["type"] == "audio" for stream in info["streams"]):
                    _ffmpeg(["-i", backup, "-map", "0:a:0", "-vn", "-ar", SAMPLE_RATE, "-ac", "2", "-c:a", "pcm_s16le", original_audio])
                replacement = original.with_suffix(".audio-replace.mp4")
                shutil.copyfile(destination, replacement)
                os.replace(replacement, original)
                manifest.update(final_path=str(original), final_video=_metadata(original, "output"))
                record["saved_video"] = _metadata(original, "output")
            manifest.setdefault("audio_versions", []).append(record)
            manifest["audio_versions"] = manifest["audio_versions"][-100:]
            _write_json(manifest_path, manifest)
            _write_json(destination.with_suffix(".audio.json"), record)
            return {"run_id": manifest["run_id"], "video": record.get("saved_video", record["video"]),
                    "version_video": record["video"], "seconds": duration, "replaced": replace,
                    "backup": record.get("backup"), "video_reencoded": False, "final_video": manifest.get("final_video")}
        finally:
            partial.unlink(missing_ok=True)


def _local_request(request):
    from .resource_control import _loopback
    try:
        origin = request.headers.get("Origin")
        return bool(_loopback(request.remote) and (not origin or _loopback(urlsplit(origin).hostname)))
    except ValueError:
        return False


def register_audio_timeline_routes():
    from aiohttp import web
    from server import PromptServer
    server = getattr(PromptServer, "instance", None)
    if server is None or getattr(server, "_h3_audio_timeline_routes", False):
        return
    server._h3_audio_timeline_routes = True

    async def respond(function, payload):
        try:
            async with _ROUTE_LIMIT:
                value = await asyncio.to_thread(function, payload)
            return web.json_response(value)
        except TimelineError as exc:
            return web.json_response({"error": str(exc), **exc.details}, status=400)
        except (ValueError, OSError) as exc:
            return web.json_response({"error": str(exc)}, status=400)

    @server.routes.post("/h3-director/dialogue/mix")
    async def mix_route(request):
        if not _local_request(request):
            return web.json_response({"error": "对白音轨仅允许本机操作。"}, status=403)
        return await respond(mix_dialogue, await request.json())

    @server.routes.post("/h3-director/dialogue/mux")
    async def mux_route(request):
        if not _local_request(request):
            return web.json_response({"error": "成片音轨仅允许本机保存。"}, status=403)
        return await respond(mux_dialogue, await request.json())

    @server.routes.post("/h3-director/dialogue/upload")
    async def upload_route(request):
        if not _local_request(request):
            return web.json_response({"error": "对白音频仅允许本机上传。"}, status=403)
        try:
            reader = await request.multipart()
            content = bytearray()
            filename = "audio.wav"
            async for part in reader:
                if part.name != "audio":
                    continue
                filename = part.filename or filename
                while chunk := await part.read_chunk():
                    content.extend(chunk)
                    if len(content) > MAX_UPLOAD_BYTES:
                        raise TimelineError("音频文件不能超过100MB。")
            async with _ROUTE_LIMIT:
                value = await asyncio.to_thread(save_uploaded_audio, bytes(content), filename)
            return web.json_response(value)
        except TimelineError as exc:
            return web.json_response({"error": str(exc), **exc.details}, status=400)
