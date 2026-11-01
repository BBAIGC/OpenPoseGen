"""Independent local speech input; recording does not require graph execution."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
import hashlib
import io
from pathlib import Path
import shutil
import subprocess
import threading
import uuid
import wave
from urllib.parse import urlsplit

from aiohttp import web
import folder_paths

from .resource_control import _loopback

LANGUAGES = {'自动': 'auto', '中文': 'zh', '英文': 'en', '粤语': 'yue', '日语': 'ja', '韩语': 'ko'}
_cache = OrderedDict()
_lock = threading.Lock()


def wav_audio(data):
    if not data or len(data) > 32 * 1024 * 1024:
        raise ValueError('请选择不超过32MB、五分钟以内的音频。')
    try:
        with wave.open(io.BytesIO(data), 'rb') as audio:
            seconds = audio.getnframes() / audio.getframerate()
            if seconds > 300:
                raise ValueError('每次识别最多五分钟，请分段录音。')
            if audio.getnchannels() == 1 and audio.getsampwidth() == 2 and audio.getframerate() == 16000:
                return data, seconds
    except (wave.Error, EOFError):
        pass
    executable = shutil.which('ffmpeg')
    if not executable:
        raise ValueError('请使用WAV音频；本机未找到音频转换组件。')
    result = subprocess.run([executable, '-hide_banner', '-loglevel', 'error', '-i', 'pipe:0',
                             '-t', '301', '-f', 'wav', '-ac', '1', '-ar', '16000', '-acodec', 'pcm_s16le', 'pipe:1'],
                            input=data, capture_output=True, timeout=45,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError('无法读取该音频，请改用WAV或重新录音。')
    # ffmpeg streaming WAV uses an unknown data size; decode and rewrite a finite WAV.
    import soundfile as sf
    samples, rate = sf.read(io.BytesIO(result.stdout), dtype='int16')
    seconds = len(samples) / rate
    if not .1 <= seconds <= 300:
        raise ValueError('请录制0.1秒至五分钟的音频。')
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000); audio.writeframes(samples.tobytes())
    return output.getvalue(), seconds


def recognize(data, language='自动', normalize=True, threads=4):
    if language not in LANGUAGES or type(normalize) is not bool or type(threads) is not int or not 1 <= threads <= 8:
        raise ValueError('识别参数无效。')
    data, seconds = wav_audio(data)
    key = hashlib.sha256(data + f'{language}|{normalize}|{threads}'.encode()).hexdigest()
    if not _lock.acquire(blocking=False):
        raise ValueError('正在识别另一段音频，请稍后再试。')
    try:
        cached = _cache.get(key)
        if cached:
            _cache.move_to_end(key)
            return {**cached, 'cached': True}, data
        from .speech_engine import transcribe
        result = transcribe(data, language=LANGUAGES[language], normalize=normalize, threads=threads)
        result.update(input_seconds=round(seconds, 2), cached=False,
                      elapsed_seconds=result.get('elapsed_seconds', result.get('elapsed', 0)))
        _cache[key] = dict(result)
        while len(_cache) > 32:
            _cache.popitem(last=False)
        return result, data
    finally:
        _lock.release()


def audio_path(filename):
    root = Path(folder_paths.get_input_directory()).resolve()
    if not isinstance(filename, str) or ':' in filename or '..' in filename.replace('\\', '/').split('/'):
        raise ValueError('请选择已上传的音频。')
    path = (root / filename).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('音频文件不存在或过大，请重新上传。')
    return path


class CodexSpeechInput:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {
            '启用识别': ('BOOLEAN', {'default': False}),
            '识别文字': ('STRING', {'default': '', 'multiline': True}),
            '音频文件': ('STRING', {'default': ''}),
            '识别语言': (list(LANGUAGES), {'default': '自动'}),
            '数字规范': ('BOOLEAN', {'default': True, 'tooltip': '将口语数字和日期整理为文字中的数字格式。'}),
            'CPU线程': ('INT', {'default': 4, 'min': 1, 'max': 8}),
        }, 'optional': {'外接音频': ('AUDIO', {'lazy': True}),
                        '运行前卸载模型': ('BOOLEAN', {'default': True})}}

    RETURN_TYPES = ('STRING',)
    RETURN_NAMES = ('识别文本',)
    FUNCTION = 'execute'
    # Recording is an independent HTTP action. An unconnected speech tool must
    # never become an execution root of an otherwise unrelated workflow.
    OUTPUT_NODE = False
    CATEGORY = 'Codex/语音输入'
    DESCRIPTION = '本机SenseVoice语音输入。不接连线也可录音、识别和复制；文本输出可接提示词输入。'

    def check_lazy_status(self, 启用识别=False, **kwargs):
        return ['外接音频'] if 启用识别 and '外接音频' in kwargs and kwargs['外接音频'] is None else []

    def execute(self, 启用识别=False, 识别文字='', 音频文件='', 识别语言='自动', 数字规范=True, CPU线程=4, 外接音频=None, 运行前卸载模型=True):
        def release():
            if 运行前卸载模型:
                import sys
                engine = sys.modules.get(__package__ + '.speech_engine')
                if engine is not None:
                    engine.unload_model()
        if not 启用识别:
            release()
            return {'ui': {'text': [识别文字]}, 'result': ('',)}
        if 外接音频 is None and 识别文字.strip():
            release()
            return {'ui': {'text': [识别文字]}, 'result': (识别文字,)}
        if 外接音频 is not None:
            import numpy as np
            import soundfile as sf
            waveform = 外接音频['waveform'].detach().cpu().float().numpy()
            if waveform.ndim == 3: waveform = waveform[0]
            if waveform.ndim == 2: waveform = waveform.mean(axis=0)
            if len(waveform) / int(外接音频['sample_rate']) > 300:
                raise ValueError('外接音频最多五分钟。')
            stream = io.BytesIO(); sf.write(stream, np.clip(waveform, -1, 1), int(外接音频['sample_rate']), format='WAV', subtype='PCM_16')
            data = stream.getvalue()
        elif 音频文件:
            data = audio_path(音频文件).read_bytes()
        else:
            return {'ui': {'text': [''], 'speech_status': ['请录音或上传音频']}, 'result': ('',)}
        result, _ = recognize(data, 识别语言, 数字规范, CPU线程)
        release()
        return {'ui': {'text': [result['text']], 'speech_stats': [result]}, 'result': (result['text'],)}


def register_speech_routes():
    from server import PromptServer
    server = getattr(PromptServer, 'instance', None)
    if server is None or getattr(server, '_codex_speech_routes', False): return

    @server.routes.post('/studio-speech/transcribe')
    async def transcribe_audio(request):
        origin = request.headers.get('Origin')
        if not _loopback(request.remote) or (origin and not _loopback(urlsplit(origin).hostname)):
            return web.json_response({'error': '仅允许本机语音识别。'}, status=403)
        try:
            reader = await request.multipart(); data = bytearray(); options = {}
            async for part in reader:
                if part.name == 'audio':
                    while True:
                        chunk = await part.read_chunk()
                        if not chunk: break
                        data.extend(chunk)
                        if len(data) > 32 * 1024 * 1024: raise ValueError('音频不能超过32MB。')
                elif part.name in ('language', 'normalize', 'threads'):
                    options[part.name] = await part.text()
            language = options.get('language', '自动')
            language = next((name for name, code in LANGUAGES.items() if code == language), language)
            normalize = options.get('normalize', 'true') == 'true'
            result, wav = await asyncio.to_thread(recognize, bytes(data), language, normalize, int(options.get('threads', 4)))
            directory = Path(folder_paths.get_input_directory()) / 'studio-speech'; directory.mkdir(parents=True, exist_ok=True)
            name = uuid.uuid4().hex + '.wav'; (directory / name).write_bytes(wav)
            return web.json_response({**result, 'audio_file': 'studio-speech/' + name})
        except (ValueError, TypeError) as exc:
            return web.json_response({'error': str(exc)}, status=400)
        except (RuntimeError, ImportError) as exc:
            import logging
            logging.getLogger(__name__).exception('SenseVoice 本机模型初始化或识别失败')
            return web.json_response({'error': str(exc)}, status=500)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('SenseVoice 识别异常')
            return web.json_response({'error': '本机语音识别失败，请检查SenseVoice模型和依赖。'}, status=500)

    @server.routes.post('/studio-speech/unload')
    async def unload_audio_model(request):
        origin = request.headers.get('Origin')
        if not _loopback(request.remote) or (origin and not _loopback(urlsplit(origin).hostname)):
            return web.json_response({'error': '仅允许本机卸载语音模型。'}, status=403)
        try:
            from .speech_engine import unload_model
            result = await asyncio.to_thread(unload_model)
            return web.json_response(result)
        except ValueError as exc:
            return web.json_response({'error': str(exc)}, status=409)

    server._codex_speech_routes = True


NODE_CLASS_MAPPINGS = {'CodexSpeechInput': CodexSpeechInput}
NODE_DISPLAY_NAME_MAPPINGS = {'CodexSpeechInput': '语音输入 · SenseVoice'}
