"""Local H3 prompt planning. Dialogue is compiled separately from LLM output."""
from __future__ import annotations

import base64
import gc
import io
import json
import http.client
import logging
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request

from .contract import (
    SPEECH_MODES,
    build_optimization_request,
    compile_h3_prompts,
    render_h3_prompt,
)

log = logging.getLogger(__name__)
_LOCK = threading.Lock()
_URL = "http://127.0.0.1:11435"
_PREFERRED = "uncensored-qwen3.5:4b-aggressive"


def local_models():
    root = Path(os.environ.get("OLLAMA_MODELS", str(Path.home() / ".ollama" / "models")))
    manifests = root / "manifests" / "registry.ollama.ai" / "library"
    names = []
    if manifests.is_dir():
        for model in manifests.iterdir():
            if model.is_dir() and any(name in model.name.lower() for name in ('qwen','gemma4')):
                names.extend(f"{model.name}:{p.name}" for p in model.iterdir() if p.is_file())
    return sorted(set(names), key=lambda name: (name != _PREFERRED, name))


class LocalOllama:
    """A loopback-only, on-demand session, independent of the user's 11434."""

    def __init__(self):
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.process = None
        self.log_file = None
        self.progress = None
        self.cancel_check = None

    def request(self, endpoint, data=None, timeout=10):
        if endpoint == '/api/chat' and isinstance(data, dict) and data.get('stream'):
            return self._stream_chat(data, timeout)
        request = urllib.request.Request(
            _URL + endpoint,
            data=None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="GET" if data is None else "POST",
        )
        try:
            with self.opener.open(request, timeout=timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"本地语言模型请求失败（{exc.code}）：{detail}") from exc

    def _stream_chat(self, data, first_token_timeout):
        """Read bounded NDJSON while the execution thread remains cancellable."""
        first_wait = max(600, min(900, float(first_token_timeout)))
        total_limit = min(3600, max(900, int(data.get('options', {}).get('num_predict', 4096)) * .5 + 300))
        connection = http.client.HTTPConnection('127.0.0.1', 11435, timeout=10)
        finished, aborted = threading.Event(), threading.Event()
        state = {'fragments': 0, 'output_tokens': 0, 'received': False}
        outcome = {}
        started = time.monotonic()

        def stop():
            aborted.set()
            transport = getattr(connection, 'sock', None)
            if transport is not None:
                try:
                    transport.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            connection.close()

        def receive():
            response = None
            try:
                connection.connect()
                if aborted.is_set():
                    return
                connection.sock.settimeout(first_wait)
                payload = json.dumps(data, ensure_ascii=False).encode('utf-8')
                connection.request('POST', '/api/chat', body=payload, headers={'Content-Type': 'application/json'})
                response = connection.getresponse()
                if response.status != 200:
                    raise RuntimeError(f'本地语言模型请求失败（HTTP {response.status}）')
                content, thinking, message, size, final = [], [], {}, 0, None
                while not aborted.is_set():
                    raw = response.readline(1024 * 1024 + 1)
                    if not raw:
                        break
                    size += len(raw)
                    if len(raw) > 1024 * 1024 or size > 8 * 1024 * 1024:
                        raise RuntimeError('语言模型输出超过安全长度，请缩短本批规划后重试。')
                    if not raw.strip():
                        continue
                    item = json.loads(raw)
                    if item.get('error'):
                        raise RuntimeError('本地语言模型报告推理失败，请检查Ollama日志。')
                    state['received'] = True
                    part = item.get('message') or {}
                    message.update({key: value for key, value in part.items() if key not in ('content', 'thinking')})
                    if part.get('content'):
                        content.append(str(part['content']))
                        state['fragments'] += 1
                    if part.get('thinking'):
                        thinking.append(str(part['thinking']))
                    if item.get('eval_count') is not None:
                        state['output_tokens'] = int(item['eval_count'])
                    # Once data starts flowing, only inactivity is a read timeout.
                    transport = connection.sock or getattr(getattr(response.fp, 'raw', None), '_sock', None)
                    if transport is not None:
                        transport.settimeout(120)
                    if item.get('done'):
                        final = item
                        break
                if aborted.is_set():
                    return
                if final is None:
                    raise RuntimeError('语言模型连接在完整结果返回前关闭，未使用不完整分镜。')
                message['content'] = ''.join(content)
                if thinking:
                    message['thinking'] = ''.join(thinking)
                final['message'] = message
                outcome['result'] = final
            except Exception as error:
                if not aborted.is_set():
                    outcome['error'] = error
            finally:
                if response is not None:
                    response.close()
                connection.close()
                finished.set()

        worker = threading.Thread(target=receive, name='bbaigc-ollama-stream', daemon=True)
        worker.start()
        last_progress = -1.0
        try:
            while not finished.wait(.25):
                if callable(self.cancel_check):
                    self.cancel_check()
                elapsed = time.monotonic() - started
                if elapsed > total_limit:
                    raise RuntimeError(f'本批语言模型规划超过{int(total_limit / 60)}分钟上限，请减少镜头数、上下文或输出上限后重试。')
                if callable(self.progress) and elapsed - last_progress >= 1:
                    last_progress = elapsed
                    self.progress({'phase': '正在规划分镜' if state['received'] else '模型加载与首段推理',
                                   'elapsed_seconds': int(elapsed), 'output_fragments': state['fragments'],
                                   'output_tokens': state['output_tokens']})
            if callable(self.cancel_check):
                self.cancel_check()
            error = outcome.get('error')
            if error is not None:
                if isinstance(error, (TimeoutError, socket.timeout)):
                    phase = '模型加载或首段推理超过10分钟' if not state['received'] else '连续120秒没有返回新内容'
                    raise RuntimeError(f'本地语言模型超时：{phase}。请检查Ollama状态和当前上下文配置；未启动视频生成。') from error
                raise error
            if 'result' not in outcome:
                raise RuntimeError('本地语言模型请求未完成。')
            return outcome['result']
        finally:
            stop()
            worker.join(timeout=1)

    def start(self):
        try:
            self.request("/api/version", timeout=2)
            return
        except (OSError, urllib.error.URLError):
            pass
        executable = shutil.which("ollama")
        if not executable:
            candidate = Path.home() / "AppData/Local/Programs/Ollama/ollama.exe"
            executable = str(candidate) if candidate.is_file() else None
        if not executable:
            raise RuntimeError("未找到本地 Ollama，请先安装并导入千问模型。")
        env = os.environ.copy()
        env.update(OLLAMA_HOST="127.0.0.1:11435", OLLAMA_KEEP_ALIVE="0",
                   OLLAMA_MAX_LOADED_MODELS="1", OLLAMA_NUM_PARALLEL="1")
        log_path = Path(tempfile.gettempdir()) / "comfy-h3-prompt-ollama.log"
        self.log_file = log_path.open("ab", buffering=0)
        self.process = subprocess.Popen(
            [executable, "serve"], env=env, stdin=subprocess.DEVNULL,
            stdout=self.log_file, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"本地语言模型服务未启动，请查看 {log_path}")
            try:
                self.request("/api/version", timeout=2)
                return
            except (OSError, urllib.error.URLError):
                time.sleep(0.25)
        raise RuntimeError("本地语言模型启动超时；尚未开始视频生成。")

    def unload(self, model):
        self.request("/api/generate", {"model": model, "stream": False, "keep_alive": 0}, timeout=30)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            # This private session must be completely empty before handing off the GPU.
            active = self.request("/api/ps", timeout=5).get("models", [])
            if not active:
                return {"model": model, "loaded_models_after": 0, "confirmed": True}
            time.sleep(0.25)
        raise RuntimeError("语言模型尚未卸载，已停止视频生成，避免显存重叠。")

    def close_owned(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.log_file:
            self.log_file.close()


def _reference_jpeg(image):
    if image is None:
        return None
    import numpy as np
    from PIL import Image
    array = (image[0].detach().cpu().numpy().clip(0, 1) * 255).astype(np.uint8)
    picture = Image.fromarray(array[:, :, :3])
    picture.thumbnail((512, 512))
    buffer = io.BytesIO()
    picture.save(buffer, format="JPEG", quality=85)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _release_existing_private_model():
    """Disabling optimization must not leave a previous private LLM in VRAM."""
    client = LocalOllama()
    try:
        models = client.request("/api/ps", timeout=2).get("models", [])
    except (OSError, urllib.error.URLError):
        return {"confirmed": True, "loaded_models_after": 0, "optimizer_disabled": True}
    for model in models:
        client.request("/api/generate", {"model": model["name"], "stream": False,
                                        "keep_alive": 0}, timeout=30)
    if models:
        receipt = client.unload(models[-1]["name"])
        receipt["optimizer_disabled"] = True
        return receipt
    return {"confirmed": True, "loaded_models_after": 0, "optimizer_disabled": True}


def optimize_locally(*, model_name, system, user, seed, reference_image=None):
    if model_name not in local_models():
        raise ValueError("所选千问模型未在本地找到；此流程不会自动下载模型。")
    client = LocalOllama()
    receipt = None
    start = time.monotonic()
    # Ollama's allocations are separate from ComfyUI, so release Comfy weights first.
    import comfy.model_management as mm
    client.cancel_check = mm.throw_exception_if_processing_interrupted
    mm.unload_all_models()
    mm.soft_empty_cache()
    try:
        client.start()
        content = {"role": "user", "content": user}
        encoded_image = _reference_jpeg(reference_image)
        if encoded_image:
            content["images"] = [encoded_image]
        try:
            result = client.request("/api/chat", {
                "model": model_name, "stream": True, "think": False,
                "keep_alive": 0, "format": "json",
                "messages": [{"role": "system", "content": system}, content],
                "options": {"temperature": 0.1, "seed": int(seed), "num_ctx": 8192,
                            "num_predict": 2304, "repeat_penalty": 1.05},
            }, timeout=180)
            raw = result.get("message", {}).get("content", "").strip()
            if not raw or result.get("done_reason") == "length":
                raise RuntimeError("提示词优化结果为空或超出长度，已停止视频生成。")
        finally:
            # An unload failure is deliberately fatal; no ready token may escape it.
            receipt = client.unload(model_name)
        return raw, receipt, round(time.monotonic() - start, 2)
    finally:
        client.close_owned()
        gc.collect()


class H3LocalPromptOptimizer:
    @classmethod
    def INPUT_TYPES(cls):
        models = local_models() or [_PREFERRED]
        text = lambda default="": ("STRING", {"multiline": True, "default": default})
        return {"required": {
            "enabled": ("BOOLEAN", {"default": True}),
            "model_name": (models, {"default": _PREFERRED if _PREFERRED in models else models[0]}),
            "scene_count": ("INT", {"default": 3, "min": 1, "max": 3}),
            "story": text(), "continuity": text(),
            "scene_1": text(), "scene_2": text(), "scene_3": text(),
            "dialogue_1": text(), "dialogue_2": text(), "dialogue_3": text(),
            "speech_mode": (list(SPEECH_MODES), {"default": "无对白（环境音）"}),
            "environment": text(), "music": text(),
            "seed": ("INT", {"default": 42, "min": 0, "max": 2**32 - 1}),
        }, "optional": {"reference_image": ("IMAGE",)}}

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("已卸载的分镜计划", "镜头1提示词", "镜头2提示词", "镜头3提示词", "音轨策略", "优化结果")
    FUNCTION = "execute"
    CATEGORY = "H3/提示词与分镜"

    def execute(self, enabled, model_name, scene_count, story, continuity,
                scene_1, scene_2, scene_3, dialogue_1, dialogue_2, dialogue_3,
                speech_mode, environment, music, seed, reference_image=None):
        if scene_count not in (1, 3):
            raise ValueError("请选择1个镜头或3个镜头。")
        scenes = [scene_1.strip(), scene_2.strip(), scene_3.strip()][:scene_count]
        dialogues = [dialogue_1, dialogue_2, dialogue_3][:scene_count]
        if any(not scene for scene in scenes):
            raise ValueError("请填写每个启用镜头的画面和动作描述。")
        request_args = dict(story=story, continuity=continuity,
                            scene_descriptions=scenes, exact_dialogues=dialogues,
                            speech_mode=speech_mode, environment=environment,
                            music=music, duration_seconds=10.0)
        system, user = build_optimization_request(**request_args)
        if enabled:
            with _LOCK:
                raw, receipt, elapsed = optimize_locally(
                    model_name=model_name, system=system, user=user,
                    seed=seed, reference_image=reference_image)
        else:
            raw = json.dumps({"scenes": [{"visual": scene, "action": "Follow the requested visible actions in their original order."} for scene in scenes]}, ensure_ascii=False)
            with _LOCK:
                receipt = _release_existing_private_model()
            elapsed = 0
        structured = compile_h3_prompts(
            raw, scene_descriptions=scenes, exact_dialogues=dialogues,
            speech_mode=speech_mode, environment=environment, music=music,
            continuity=continuity, input_mode="I2V", duration_seconds=10.0)
        rendered = [render_h3_prompt(item, input_mode="I2V", duration_seconds=10.0) for item in structured]
        audio_mode = "静音" if speech_mode == "静音" else "模型音频"
        plan = {"status": "ready", "llm_released": bool(receipt["confirmed"]),
                "scene_count": scene_count, "speech_mode": speech_mode,
                "audio_mode": audio_mode, "model": model_name if enabled else None,
                "unload_receipt": receipt, "compiled_prompts": structured,
                "prompts": rendered, "seconds_per_scene": 10.0}
        report = (f"{scene_count}个镜头，优化耗时{elapsed}秒。语言模型已卸载。\n"
                  "台词从输入原样编译，不使用语言模型改写的对白。\n\n" +
                  "\n\n".join(f"镜头{i+1}\n{prompt}" for i, prompt in enumerate(rendered)))
        log.info("H3 prompt plan ready: scenes=%s, Ollama active models=%s", scene_count, receipt["loaded_models_after"])
        padded = rendered + [""] * (3 - len(rendered))
        return (json.dumps(plan, ensure_ascii=False), *padded, audio_mode, report)


class H3TurboToggle:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"enabled": ("BOOLEAN", {"default": True,
                                    "label_on": "加速LoRA已启用", "label_off": "原版20步"})}}
    RETURN_TYPES = ("BOOLEAN",)
    RETURN_NAMES = ("启用加速LoRA",)
    FUNCTION = "execute"
    CATEGORY = "H3/提示词与分镜"

    def execute(self, enabled):
        return (bool(enabled),)


PROMPT_NODE_CLASS_MAPPINGS = {
    "H3LocalPromptOptimizer": H3LocalPromptOptimizer,
    "H3TurboToggle": H3TurboToggle,
}
PROMPT_NODE_DISPLAY_NAME_MAPPINGS = {
    "H3LocalPromptOptimizer": "H3 本地提示词优化", "H3TurboToggle": "加速 LoRA 开关",
}
