"""Bounded CPU SenseVoice engine, reusing the installed local ONNX weights."""
from __future__ import annotations
import gc
import io
import json
import os
from pathlib import Path
import re
import threading
import time
import wave

import kaldi_native_fbank as knf
import numpy as np
import onnxruntime as ort
import yaml
import folder_paths

LANGUAGES = {'auto': 0, 'zh': 3, 'en': 4, 'yue': 7, 'ja': 11, 'ko': 12}
_LOCK = threading.Lock()
_MODEL = None
_MODEL_KEY = None
def model_directory():
    configured = os.environ.get('BBAIGC_SENSEVOICE_MODEL_DIR') or os.environ.get('CODEX_SENSEVOICE_MODEL_DIR')
    path = Path(configured) if configured else Path(folder_paths.models_dir) / 'sensevoice-small'
    required = ('model.onnx', 'config.yaml', 'tokens.json')
    missing = [name for name in required if not (path / name).is_file()]
    if missing:
        raise RuntimeError(f'SenseVoice 模型文件不完整：{", ".join(missing)}。请放到 ComfyUI/models/sensevoice-small/，或设置 BBAIGC_SENSEVOICE_MODEL_DIR。')
    # Some ONNX exports include normalization in the graph (cmvn_file: null).
    # Only require an external CMVN file when this export explicitly uses one.
    config = yaml.safe_load((path / 'config.yaml').read_text(encoding='utf-8')) or {}
    cmvn = (config.get('frontend_conf') or {}).get('cmvn_file')
    if cmvn and not (path / str(cmvn)).is_file():
        raise RuntimeError(f'SenseVoice 缺少配置指定的归一化文件：{cmvn}，请补齐对应模型文件。')
    return path


def clean_text(raw):
    # Prompt input needs literal speech text, without emotion/music emoji tags.
    text = re.sub(r'<\|[^|]*\|>', '', str(raw))
    text = re.sub(r'</?s>|<unk>|<OOV>', '', text)
    return re.sub(r'\s+', ' ', text.replace('▁', ' ')).strip()


def _read_wav(payload):
    if not isinstance(payload, (bytes, bytearray)) or len(payload) > 16 * 1024 * 1024:
        raise ValueError('录音数据无效或超过16MB。')
    try:
        with wave.open(io.BytesIO(payload), 'rb') as reader:
            if reader.getsampwidth() != 2 or reader.getframerate() != 16000 or reader.getnchannels() != 1:
                raise ValueError('语音识别需要16kHz、16位单声道 WAV。')
            count = reader.getnframes()
            if not 1600 <= count <= 16000 * 300:
                raise ValueError('录音长度应在0.1秒至5分钟之间。')
            frames = reader.readframes(count)
    except (wave.Error, EOFError) as exc:
        raise ValueError('无法读取录音，请重新录制。') from exc
    if len(frames) != count * 2:
        raise ValueError('录音数据不完整。')
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

class WavFrontend:
    def __init__(
        self,
        cmvn_file=None,
        fs=16000,
        window="hamming",
        n_mels=80,
        frame_length=25,
        frame_shift=10,
        lfr_m=1,
        lfr_n=1,
        dither=1.0,
        **_,
    ):
        opts = knf.FbankOptions()
        opts.frame_opts.samp_freq = fs
        opts.frame_opts.dither = dither
        opts.frame_opts.window_type = window
        opts.frame_opts.frame_shift_ms = float(frame_shift)
        opts.frame_opts.frame_length_ms = float(frame_length)
        opts.mel_opts.num_bins = n_mels
        opts.energy_floor = 0
        opts.frame_opts.snip_edges = True
        opts.mel_opts.debug_mel = False
        self.opts = opts
        self.lfr_m = int(lfr_m)
        self.lfr_n = int(lfr_n)
        self.cmvn_file = cmvn_file
        self.cmvn = self._load_cmvn() if cmvn_file else None

    def fbank(self, waveform):
        scaled = waveform * (1 << 15)
        fbank = knf.OnlineFbank(self.opts)
        fbank.accept_waveform(self.opts.frame_opts.samp_freq, scaled.tolist())
        frames = fbank.num_frames_ready
        matrix = np.empty([frames, self.opts.mel_opts.num_bins], dtype=np.float32)
        for index in range(frames):
            matrix[index, :] = fbank.get_frame(index)
        return matrix, np.array(matrix.shape[0]).astype(np.int32)

    def lfr_cmvn(self, feat):
        current = feat
        if self.lfr_m != 1 or self.lfr_n != 1:
            current = self._apply_lfr(current, self.lfr_m, self.lfr_n)
        if self.cmvn is not None:
            current = self._apply_cmvn(current)
        return current, np.array(current.shape[0]).astype(np.int32)

    def _apply_cmvn(self, inputs):
        frame, dim = inputs.shape
        means = np.tile(self.cmvn[0:1, :dim], (frame, 1))
        vars_ = np.tile(self.cmvn[1:2, :dim], (frame, 1))
        return (inputs + means) * vars_

    @staticmethod
    def _apply_lfr(inputs, lfr_m, lfr_n):
        merged = []
        total = inputs.shape[0]
        total_lfr = int(np.ceil(total / lfr_n))
        left_padding = np.tile(inputs[0], ((lfr_m - 1) // 2, 1))
        padded = np.vstack((left_padding, inputs))
        total = total + (lfr_m - 1) // 2
        for index in range(total_lfr):
            start = index * lfr_n
            if lfr_m <= total - start:
                merged.append(padded[start : start + lfr_m].reshape(1, -1))
            else:
                num_padding = lfr_m - (total - start)
                frame = padded[start:].reshape(-1)
                for _ in range(num_padding):
                    frame = np.hstack((frame, padded[-1]))
                merged.append(frame)
        return np.vstack(merged).astype(np.float32)

    def _load_cmvn(self):
        with open(self.cmvn_file, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
        means_list = []
        vars_list = []
        for index, line in enumerate(lines):
            line_item = line.split()
            if not line_item:
                continue
            if line_item[0] == "<AddShift>":
                next_item = lines[index + 1].split()
                if next_item[0] == "<LearnRateCoef>":
                    means_list = next_item[3 : len(next_item) - 1]
            if line_item[0] == "<Rescale>":
                next_item = lines[index + 1].split()
                if next_item[0] == "<LearnRateCoef>":
                    vars_list = next_item[3 : len(next_item) - 1]
        means = np.array(means_list).astype(np.float64)
        vars_ = np.array(vars_list).astype(np.float64)
        return np.array([means, vars_])

class SenseVoiceModel:
    def __init__(self, path, threads=4):
        with (path / 'config.yaml').open('r', encoding='utf-8') as handle:
            config = yaml.safe_load(handle)
        with (path / 'tokens.json').open('r', encoding='utf-8') as handle:
            self.tokens = json.load(handle)
        frontend_conf = dict(config.get('frontend_conf') or {})
        cmvn = frontend_conf.get('cmvn_file')
        frontend_conf['cmvn_file'] = str(path / cmvn) if cmvn else None
        frontend_conf['dither'] = 0.0
        self.frontend = WavFrontend(**frontend_conf)
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.log_severity_level = 3
        self.session = ort.InferenceSession(str(path / 'model.onnx'), sess_options=options,
                                            providers=['CPUExecutionProvider'])

    def recognize(self, audio, language, normalize):
        features, _ = self.frontend.fbank(audio)
        if not len(features):
            return ''
        feat, length = self.frontend.lfr_cmvn(features)
        logits, lens = self.session.run(None, {
            'speech': np.expand_dims(feat.astype(np.float32), 0),
            'speech_lengths': np.array([int(length)], dtype=np.int32),
            'language': np.array([LANGUAGES[language]], dtype=np.int32),
            'textnorm': np.array([14 if normalize else 15], dtype=np.int32),
        })
        sequence = np.argmax(logits[0, :int(lens[0]), :], axis=-1)
        kept = sequence[np.concatenate(([True], sequence[1:] != sequence[:-1]))]
        raw = ''.join(self.tokens[int(index)] for index in kept if 0 < int(index) < len(self.tokens))
        if '<|nospeech|>' in raw and '<|Speech|>' not in raw:
            return ''
        return clean_text(raw)


def transcribe(wav_bytes, language='auto', normalize=True, threads=4):
    global _MODEL, _MODEL_KEY
    if language not in LANGUAGES:
        raise ValueError('请选择有效的识别语言。')
    if type(threads) is not int or not 1 <= threads <= 8:
        raise ValueError('识别线程数应在1～8之间。')
    if type(normalize) is not bool:
        raise ValueError('文本规范化选项无效。')
    audio = _read_wav(wav_bytes)
    started = time.monotonic()
    path = model_directory().resolve()
    key = (str(path), threads)
    with _LOCK:
        if _MODEL_KEY != key or _MODEL is None:
            # One retained CPU model only; thread/model changes replace it.
            _MODEL = None
            _MODEL_KEY = None
            model = SenseVoiceModel(path, threads)
            _MODEL = model
            _MODEL_KEY = key
        text = _MODEL.recognize(audio, language, normalize)
    return {'text': text, 'elapsed': round(time.monotonic() - started, 3),
            'model': 'SenseVoice Small', 'duration': round(len(audio) / 16000, 3)}


def unload_model():
    global _MODEL, _MODEL_KEY
    if not _LOCK.acquire(blocking=False):
        raise ValueError('正在识别，请完成后卸载')
    try:
        _MODEL = None
        _MODEL_KEY = None
        gc.collect()
    finally:
        _LOCK.release()
    return {'released': True, 'model': 'SenseVoice Small', 'message': '已卸载语音模型'}
