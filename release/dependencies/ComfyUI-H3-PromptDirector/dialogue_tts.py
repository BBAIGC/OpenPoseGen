"""Offline dialogue synthesis using Windows voices or already installed Qwen weights."""
from __future__ import annotations
import asyncio
import copy
import hashlib
import importlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import uuid
import wave
import folder_paths

SAMPLE_RATE = 32000
QWEN_VOICES = ("Aiden", "Dylan", "Eric", "Ono_anna", "Ryan", "Serena", "Sohee", "Uncle_fu", "Vivian")
_LOCK = threading.RLock()
_JOBS = {}
_ACTIVE = None
_VOICES = None
_SAPI_SCRIPT = r'''param([string]$RequestFile)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Speech
$request=Get-Content -LiteralPath $RequestFile -Raw -Encoding UTF8 | ConvertFrom-Json
$synth=New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
 if ($request.action -eq 'voices') {
  $voices=@($synth.GetInstalledVoices() | Where-Object Enabled | ForEach-Object {
   @{id=$_.VoiceInfo.Name;label=$_.VoiceInfo.Name;culture=$_.VoiceInfo.Culture.Name;gender=$_.VoiceInfo.Gender.ToString();engine='system'}
  })
  @{voices=$voices} | ConvertTo-Json -Depth 4 -Compress
 } elseif ($request.action -eq 'speak') {
  $synth.SelectVoice([string]$request.voice)
  $format=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(32000,[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,[System.Speech.AudioFormat.AudioChannel]::Mono)
  $synth.SetOutputToWaveFile([string]$request.output,$format)
  $synth.Rate=0
  $synth.Speak([string]$request.text)
  @{ok=$true} | ConvertTo-Json -Compress
 } else { throw 'Unknown action' }
} finally { $synth.Dispose() }
'''

class TTSBusy(RuntimeError):
    pass

def _root():
    root = (Path(folder_paths.get_input_directory()) / "h3_dialogue_tts").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root

def _sapi(request):
    """Pass fixed script plus a JSON filename, never interpolate spoken text in a shell."""
    shell = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    if os.name != "nt" or not shell.is_file():
        raise RuntimeError("本机没有Windows系统配音组件。")
    root = (Path(folder_paths.get_temp_directory()) / "h3_dialogue_tts_requests").resolve()
    root.mkdir(parents=True, exist_ok=True)
    script = root / "system_speech.ps1"
    if not script.is_file() or script.read_text(encoding="utf-8-sig") != _SAPI_SCRIPT:
        script.write_text(_SAPI_SCRIPT, encoding="utf-8-sig")
    data = root / f"request_{uuid.uuid4().hex}.json"
    data.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8-sig")
    try:
        done = subprocess.run([str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                               "-File", str(script), "-RequestFile", str(data)], capture_output=True,
                              encoding="utf-8", errors="replace", timeout=120,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if done.returncode:
            raise RuntimeError("系统配音失败：" + done.stderr.strip()[-1200:])
        return json.loads(done.stdout.lstrip("\ufeff").strip())
    finally:
        data.unlink(missing_ok=True)

def system_voices():
    global _VOICES
    with _LOCK:
        if _VOICES is not None:
            return copy.deepcopy(_VOICES)
    voices = _sapi({"action": "voices"}).get("voices", [])
    voices.sort(key=lambda row: (not str(row.get("culture", "")).lower().startswith("zh"), row["id"]))
    with _LOCK:
        _VOICES = voices
    return copy.deepcopy(voices)

def _qwen_preflight():
    plugin = Path(__file__).resolve().parent.parent / "ComfyUI-Qwen-TTS"
    if not (plugin / "nodes.py").is_file():
        return {"ready": False, "message": "未安装ComfyUI-Qwen-TTS插件。"}
    roots = [Path(folder_paths.models_dir) / "qwen-tts"]
    try:
        roots.extend(Path(path) for path in folder_paths.get_folder_paths("qwen-tts"))
    except (KeyError, AttributeError):
        pass
    for root in dict.fromkeys(roots):
        if not root.is_dir():
            continue
        tokenizer = next((p for p in root.iterdir() if p.is_dir() and "tokenizer" in p.name.lower()
                          and (p / "config.json").is_file() and any(p.glob("*.safetensors"))), None)
        for size in ("0.6B", "1.7B"):
            model = next((p for p in root.iterdir() if p.is_dir() and size.lower() in p.name.lower()
                          and "customvoice" in p.name.lower() and (p / "config.json").is_file()
                          and any(p.glob("*.safetensors")) and not list(p.glob("*.partial*"))), None)
            if model and tokenizer:
                missing = [m for m in ("torch", "torchaudio", "transformers", "accelerate", "soundfile", "librosa", "sox")
                           if importlib.util.find_spec(m) is None]
                if missing:
                    return {"ready": False, "message": "Qwen配音缺少依赖：" + "、".join(missing)}
                return {"ready": True, "message": "本机Qwen3-TTS预设音色可用。", "model": str(model),
                        "tokenizer": str(tokenizer), "model_choice": size, "plugin": str(plugin)}
    return {"ready": False, "message": "未找到完整本机Qwen3-TTS CustomVoice及Tokenizer权重。"}

def voice_options():
    try:
        voices = system_voices()
        system = {"id": "system", "label": "系统离线配音", "available": bool(voices), "message": "Windows本机音色，速度快。"}
    except Exception as exc:
        voices, system = [], {"id": "system", "label": "系统离线配音", "available": False, "message": str(exc)}
    qwen = _qwen_preflight()
    voices.extend({"id": voice, "label": voice, "engine": "local-qwen"} for voice in QWEN_VOICES)
    return {"ok": True, "engines": [system, {"id": "local-qwen", "label": "Qwen3-TTS本地配音", "available": qwen["ready"], "message": qwen["message"]}],
            "voices": voices, "preflight": {"ready": system["available"], "message": system["message"]}}

def normalize_request(body):
    if not isinstance(body, dict):
        raise ValueError("配音请求无效。")
    engine, device, total = body.get("engine", "system"), body.get("device", "auto"), body.get("total_seconds")
    if engine not in ("system", "local-qwen") or device not in ("auto", "cpu", "cuda"):
        raise ValueError("请选择有效配音引擎和设备。")
    if isinstance(total, bool) or not isinstance(total, (int, float)) or not math.isfinite(total) or not 0 < total <= 1500:
        raise ValueError("配音总时长应为0～1500秒。")
    rows = body.get("lines")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise ValueError("配音对白应为1～100句。")
    voices = system_voices() if engine == "system" else [{"id": voice} for voice in QWEN_VOICES]
    available = {voice["id"] for voice in voices}
    default = voices[0]["id"] if engine == "system" and voices else "Vivian"
    normalized, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("对白记录无效。")
        identity, text, speaker = row.get("dialogue_id", row.get("id")), row.get("text"), row.get("speaker", "S1")
        if not isinstance(identity, str) or not re.fullmatch(r"D[1-9]\d*", identity) or identity in seen:
            raise ValueError("对白编号重复或无效。")
        if not isinstance(text, str) or not text.strip() or len(text) > 1000 or "<" in text or ">" in text:
            raise ValueError("对白应为1～1000字原文，不包含提示词标签。")
        if not isinstance(speaker, str) or not re.fullmatch(r"S[1-9]\d*", speaker):
            raise ValueError("说话人编号应为S1、S2等。")
        start, end, speed = row.get("start"), row.get("end"), row.get("speed", 1)
        if (any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in (start, end, speed))
                or not 0 <= start < end <= total or not 0.5 <= speed <= 2):
            raise ValueError(f"{identity}的时间或语速无效。")
        voice = row.get("voice") or default
        if voice not in available:
            raise ValueError(f"{identity}音色不存在，请重新选择。")
        normalized.append({"dialogue_id": identity, "speaker": speaker, "text": text, "voice": voice,
                           "start": start, "end": end, "speed": speed, "status": "pending"})
        seen.add(identity)
    selected = body.get("dialogue_ids", body.get("indices"))
    if selected is not None:
        if not isinstance(selected, list) or not selected or any(key not in seen for key in selected):
            raise ValueError("所选对白编号无效。")
        normalized = [row for row in normalized if row["dialogue_id"] in selected]
    return {"engine": engine, "device": device, "total_seconds": total, "lines": normalized}

def _cache_key(row, engine):
    return hashlib.sha256(json.dumps({"engine": engine, "version": 1, "text": row["text"], "voice": row["voice"]}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

def _metadata(path):
    import av
    import numpy as np
    frames, peak, rate = 0, 0.0, SAMPLE_RATE
    with av.open(str(path)) as source:
        if not source.streams.audio:
            raise ValueError("配音结果没有音轨。")
        for frame in source.decode(audio=0):
            rate, frames = frame.sample_rate, frames + frame.samples
            peak = max(peak, float(np.max(np.abs(frame.to_ndarray().astype(np.float32)))))
            if frames / rate > 180:
                raise ValueError("单句配音超过180秒，请拆分对白。")
    if not frames or peak == 0:
        raise ValueError("配音结果为空或全静音。")
    return {"audio": {"filename": path.name, "subfolder": "h3_dialogue_tts", "type": "input"}, "seconds": frames / rate, "sample_rate": rate}

def _qwen_module(preflight):
    import nodes
    cls = nodes.NODE_CLASS_MAPPINGS.get("FB_Qwen3TTSCustomVoice")
    if cls is not None:
        return cls, sys.modules[cls.__module__]
    name = "h3_installed_qwen_tts"
    if name not in sys.modules:
        package = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader=None, is_package=True))
        package.__path__ = [preflight["plugin"]]
        sys.modules[name] = package
    module = importlib.import_module(name + ".nodes")
    return module.CustomVoiceNode, module

def _qwen_synthesize(row, destination, preflight, device, max_new_tokens=2048):
    import torch
    import torchaudio
    cls, module = _qwen_module(preflight)
    download, checked = module.download_model_if_needed, module._MODELS_CHECKED
    module.download_model_if_needed = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("离线配音不会自动下载模型。"))
    module._MODELS_CHECKED = True
    try:
        audio = cls().generate(text=row["text"], speaker=row["voice"], model_choice=preflight["model_choice"], device=device,
            precision="fp32" if device == "cpu" else "bf16", language="Auto", seed=42, instruct="", max_new_tokens=max_new_tokens,
            attention="sdpa", unload_model_after_generate=False, custom_model_path=preflight["model"])[0]
        waveform = audio["waveform"].detach().cpu().float().mean(dim=1, keepdim=True)[0]
        if int(audio["sample_rate"]) != SAMPLE_RATE:
            waveform = torchaudio.functional.resample(waveform, int(audio["sample_rate"]), SAMPLE_RATE)
        pcm = waveform.clamp(-1, 1).mul(32767).round().short().numpy().astype("<i2")
        with wave.open(str(destination), "wb") as output:
            output.setnchannels(1); output.setsampwidth(2); output.setframerate(SAMPLE_RATE); output.writeframes(pcm.tobytes())
    finally:
        module.download_model_if_needed, module._MODELS_CHECKED = download, checked

def synthesize_line(row, engine="system", device="auto", preflight=None):
    key = _cache_key(row, engine)
    destination, cached = _root() / (key + ".wav"), False
    cached = destination.is_file()
    if not cached:
        temporary = destination.with_suffix(".partial.wav")
        try:
            if engine == "system":
                _sapi({"action": "speak", "voice": row["voice"], "text": row["text"], "output": str(temporary)})
            elif engine == "local-qwen":
                ready = preflight or _qwen_preflight()
                if not ready["ready"]:
                    raise RuntimeError(ready["message"])
                _qwen_synthesize(row, temporary, ready, device)
            else:
                raise ValueError("未知配音引擎。")
            _metadata(temporary)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    result = {**row, **_metadata(destination), "cache_key": key, "engine": engine, "cached": cached, "status": "completed"}
    duration = result["seconds"] / row.get("speed", 1)
    if duration > row["end"] - row["start"] + 0.02:
        result.update(timing_warning=f"配音占用{duration:.2f}秒，时间槽{row['end'] - row['start']:.2f}秒；请调整时间或语速。",
                      required_speed=round(result["seconds"] / (row["end"] - row["start"]), 4))
    return result

def _public(job):
    return {key: copy.deepcopy(value) for key, value in job.items() if not key.startswith("_")}

def job_status(identity):
    if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{32}", identity):
        raise ValueError("配音任务编号无效。")
    with _LOCK:
        if identity not in _JOBS:
            raise KeyError(identity)
        return {"ok": True, **_public(_JOBS[identity])}

def cancel_job(identity):
    job_status(identity)
    with _LOCK:
        _JOBS[identity]["_cancel"].set()
        _JOBS[identity]["cancel_requested"] = True
    return job_status(identity)

def _run_job(server, identity, options, preflight):
    global _ACTIVE
    job, guard, owned_models = _JOBS[identity], None, False
    with _LOCK:
        job["status"] = "running"
    try:
        if options["engine"] == "local-qwen":
            from .resource_control import release_resources
            guard = server.prompt_queue.mutex
            guard.acquire()
            running, waiting = server.prompt_queue.get_current_queue_volatile()
            if running or waiting:
                raise TTSBusy("视频工作流正在运行或排队，请先完成或取消后再配音。")
            release_resources(full=True, clear_execution=False)
            owned_models = True
        for index, row in enumerate(job["lines"]):
            if job["_cancel"].is_set():
                break
            with _LOCK:
                row["status"], job["current_dialogue_id"] = "running", row["dialogue_id"]
            result = synthesize_line(row, options["engine"], options["device"], preflight)
            with _LOCK:
                job["lines"][index], job["completed"] = result, index + 1
        with _LOCK:
            job["status"] = "cancelled" if job["_cancel"].is_set() else "completed"
    except Exception as exc:
        with _LOCK:
            job["status"], job["error"] = "failed", str(exc)
            for row in job["lines"]:
                if row["status"] == "running":
                    row.update(status="failed", error=str(exc))
    finally:
        if guard is not None:
            try:
                if owned_models:
                    from .resource_control import release_resources
                    if preflight and preflight["ready"]:
                        _, module = _qwen_module(preflight)
                        module.unload_cached_model()
                    release_resources(full=False)
            except Exception as exc:
                with _LOCK:
                    job.setdefault("cleanup_error", str(exc))
            finally:
                guard.release()
        with _LOCK:
            job["finished_at"], _ACTIVE = time.time(), None

def start_job(server, body):
    global _ACTIVE
    options = normalize_request(body)
    preflight = _qwen_preflight() if options["engine"] == "local-qwen" else None
    if preflight is not None:
        if not preflight["ready"]:
            raise RuntimeError(preflight["message"])
        with server.prompt_queue.mutex:
            running, waiting = server.prompt_queue.get_current_queue_volatile()
            if running or waiting:
                raise TTSBusy("视频工作流正在运行或排队，请先完成或取消后再配音。")
    with _LOCK:
        if _ACTIVE is not None:
            raise TTSBusy("已有配音任务正在执行，请等待或取消。")
        identity = uuid.uuid4().hex
        _JOBS[identity] = {"job_id": identity, "engine": options["engine"], "status": "queued", "lines": options["lines"],
                           "total_seconds": options["total_seconds"], "completed": 0, "created_at": time.time(), "_cancel": threading.Event()}
        _ACTIVE = identity
        while len(_JOBS) > 20:
            oldest = next((key for key, value in _JOBS.items() if key != identity and value["status"] not in ("queued", "running")), None)
            if oldest is None:
                break
            _JOBS.pop(oldest)
    threading.Thread(target=_run_job, args=(server, identity, options, preflight), daemon=True, name="h3-dialogue-tts").start()
    return job_status(identity)

def register_dialogue_tts_routes():
    from aiohttp import web
    from server import PromptServer
    from .resource_control import _loopback
    server = PromptServer.instance
    if server is None or getattr(server, "_h3_dialogue_tts_routes", False):
        return
    def allowed(request):
        origin = request.headers.get("Origin")
        return _loopback(request.remote) and (not origin or _loopback(urlsplit(origin).hostname))
    @server.routes.get("/h3-director/dialogue/tts/voices")
    @server.routes.get("/h3-director/dialogue/tts/options")
    async def voices(request):
        if not allowed(request):
            return web.json_response({"ok": False, "message": "配音仅允许本机操作。"}, status=403)
        return web.json_response(await asyncio.to_thread(voice_options))
    @server.routes.post("/h3-director/dialogue/tts")
    async def start(request):
        if not allowed(request):
            return web.json_response({"ok": False, "message": "配音仅允许本机操作。"}, status=403)
        try:
            return web.json_response(await asyncio.to_thread(start_job, server, await request.json()), status=202)
        except TTSBusy as exc:
            return web.json_response({"ok": False, "message": str(exc)}, status=409)
        except (ValueError, TypeError) as exc:
            return web.json_response({"ok": False, "message": str(exc)}, status=400)
        except RuntimeError as exc:
            return web.json_response({"ok": False, "message": str(exc)}, status=503)
    @server.routes.get("/h3-director/dialogue/tts/{job_id}")
    async def status(request):
        if not allowed(request):
            return web.json_response({"ok": False, "message": "配音仅允许本机操作。"}, status=403)
        try:
            return web.json_response(job_status(request.match_info["job_id"]))
        except (ValueError, KeyError):
            return web.json_response({"ok": False, "message": "配音任务不存在。"}, status=404)
    @server.routes.post("/h3-director/dialogue/tts/{job_id}/cancel")
    async def cancel(request):
        if not allowed(request):
            return web.json_response({"ok": False, "message": "配音仅允许本机操作。"}, status=403)
        try:
            return web.json_response(cancel_job(request.match_info["job_id"]))
        except (ValueError, KeyError):
            return web.json_response({"ok": False, "message": "配音任务不存在。"}, status=404)
    server._h3_dialogue_tts_routes = True
