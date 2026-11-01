"""Disk-backed voice references for native MiniMax H3 RefDelta conditioning.

This is reference conditioning, not an independent TTS clone or a trained voice.
Library operations only decode short CPU audio and never execute the video graph.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
from pathlib import Path
import re
import threading
import uuid
import wave

import folder_paths

_LOCK = threading.RLock()
MAX_SECONDS = 15
MAX_BYTES = 50 * 1024 * 1024
GENDERS = ("男", "女", "未指定")


def _root():
    path = Path(folder_paths.get_user_directory()) / "h3_voice_library"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _library():
    path = _root() / "library.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("音色库格式无效。")
    return data


def _audio_path(identity):
    if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{32}", identity):
        raise ValueError("音色编号无效。")
    path = (_root() / (identity + ".wav")).resolve()
    if not path.is_relative_to(_root()) or not path.is_file():
        raise ValueError("参考音频不存在，请重新保存音色。")
    return path


def save_voice(content, name, gender):
    """Bound decode and persist only the first 15 seconds as standard WAV."""
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
        raise ValueError("音色名称应为1～60字。")
    if gender not in GENDERS:
        raise ValueError("请选择有效的音色性别。")
    if not content or len(content) > MAX_BYTES:
        raise ValueError("参考音频应小于50MB。")
    import av
    import numpy as np

    chunks = []
    count = 0
    rate = 32000
    resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
    with av.open(io.BytesIO(content)) as source:
        if not source.streams.audio:
            raise ValueError("文件中没有可用的音频。")
        for frame in source.decode(audio=0):
            for converted in resampler.resample(frame):
                samples = converted.to_ndarray().reshape(-1)
                samples = samples[:max(0, MAX_SECONDS * rate - count)]
                chunks.append(samples)
                count += len(samples)
            if count >= MAX_SECONDS * rate:
                break
        if count < MAX_SECONDS * rate:
            for converted in resampler.resample(None):
                samples = converted.to_ndarray().reshape(-1)[:MAX_SECONDS * rate - count]
                chunks.append(samples)
                count += len(samples)
    if count < 2 * rate:
        raise ValueError("参考音频至少需要2秒清晰人声，建议5～15秒。")
    pcm = np.concatenate(chunks).astype("<i2", copy=False)
    if not np.any(pcm):
        raise ValueError("参考音频为静音，请换一段清晰人声。")
    identity = uuid.uuid4().hex
    path = _root() / (identity + ".wav")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(pcm.tobytes())
    item = {"id": identity, "name": name.strip(), "gender": gender,
            "seconds": round(count / rate, 2), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    with _LOCK:
        entries = _library()
        entries.append(item)
        temporary = _root() / "library.json.tmp"
        temporary.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(_root() / "library.json")
    return item


def normalize_voice_settings(mode, assignments):
    if mode not in ("模型默认", "参考音色"):
        raise ValueError("音色模式无效。")
    if isinstance(assignments, str):
        try:
            assignments = json.loads(assignments)
        except (TypeError, ValueError) as exc:
            raise ValueError("角色音色分配无效。") from exc
    if not isinstance(assignments, list) or len(assignments) > 100:
        raise ValueError("角色音色分配应为不超过100项的列表。")
    if mode == "模型默认":
        return {"mode": mode, "assignments": []}
    bank = {item["id"]: item for item in _library()}
    selected = []
    seen = set()
    for row in assignments:
        if not isinstance(row, dict):
            raise ValueError("角色音色分配无效。")
        role, identity = row.get("role", ""), row.get("voice_id", "")
        if not isinstance(role, str) or not re.fullmatch(r"S[1-9]\d*|男生[1-9]\d*|女生[1-9]\d*", role):
            raise ValueError("角色应为S1、S2、男生1、女生1等。")
        if role in seen:
            raise ValueError("同一角色只能分配一个音色。")
        seen.add(role)
        if identity not in bank:
            raise ValueError("已选音色不存在，请重新选择。")
        _audio_path(identity)
        selected.append({"role": role, "voice_id": identity, **{key: bank[identity][key] for key in ("name", "gender", "sha256", "seconds")}})
    return {"mode": mode, "assignments": selected}


def _gender(description):
    text = str(description).lower()
    female = bool(re.search(r"女生|女性|女人|女孩|女声|\b(?:female|woman|girl|she|her)\b", text))
    male = bool(re.search(r"男生|男性|男人|男孩|男声|\b(?:male|man|boy|he|his)\b", text))
    return "女" if female and not male else "男" if male and not female else None


def select_scene_voices(settings, plan, scene):
    """Exact S IDs win; gender ordinals are stable across the whole plan."""
    if not settings or settings.get("mode") != "参考音色":
        return []
    assignments = {row["role"]: row for row in settings.get("assignments", [])}
    descriptions = {}
    for shot in plan.get("scenes", []):
        for line in shot.get("dialogues", []):
            speaker = line.get("speaker_id", line.get("speaker", "S1"))
            if not descriptions.get(speaker):
                descriptions[speaker] = line.get("speaker_description", "")
    counters = {"男": 0, "女": 0}
    aliases = {}
    for speaker in sorted(descriptions, key=lambda name: int(re.sub(r"\D", "", name) or 0)):
        gender = _gender(descriptions[speaker])
        if gender:
            counters[gender] += 1
            aliases[speaker] = f"{gender}生{counters[gender]}"
    selected = []
    for line in scene.get("dialogues", []):
        speaker = line.get("speaker_id", line.get("speaker", "S1"))
        row = assignments.get(speaker) or assignments.get(aliases.get(speaker))
        if row and not any(item["speaker_id"] == speaker for item in selected):
            selected.append({**row, "speaker_id": speaker})
    if len({row["voice_id"] for row in selected}) > 3:
        raise ValueError("H3每镜最多支持3个参考音色，请减少该镜的不同音色或拆分镜头。")
    durations = {row["voice_id"]: row.get("seconds", 0) for row in selected}
    if any(not isinstance(seconds, (int, float)) or not 2 <= seconds <= 15 for seconds in durations.values()):
        raise ValueError("参考音色需使用2～15秒的录音，请重新保存对应音色。")
    if sum(durations.values()) > 15.01:
        raise ValueError("同一镜头的参考录音总时长不能超过15秒，请使用更短的音色样本。")
    return selected


def voice_conditioning(clip, vae, audio_vae, prompt, width, height, length,
                       first_frame, last_frame, voices):
    """Native keyframes + native audio reference blocks, one text encoding."""
    from comfy_extras import nodes_minimax_h3 as native
    from comfy_extras.nodes_audio import load
    import node_helpers

    latent, frame_count = native._empty_av_latent(width, height, length)
    ref_items, refs, keyframes = [], [], []
    for image, index, crop in ((first_frame, 0, "disabled"), (last_frame, frame_count - 1, "center")):
        if image is not None:
            image = native._resize(image[:1], width, height, crop)
            ref_items.append({"type": "image", "data": image})
            keyframes.append({"resolved_frame_index": index, "latent": vae.encode(image)})
    slots = {}
    bindings = []
    for row in voices:
        identity = row["voice_id"]
        if identity not in slots:
            waveform, sample_rate = load(str(_audio_path(identity)))
            encoded, audio_t = native._encode_ref_audio(audio_vae, {"waveform": waveform.unsqueeze(0), "sample_rate": sample_rate})
            refs.append({"kind": "audio", "ref_audio_t": audio_t, "audio_latent": encoded})
            ref_items.append({"type": "audio"})
            slots[identity] = len(slots) + 1
        bindings.append(f"{row['speaker_id']}的音色参考<Audio {slots[identity]}>。")
    # The sample's words are never inserted into target dialogue.
    prompt = str(prompt)
    binding = "".join(bindings) + "参考音频仅用于音色，不复述参考录音内容。"
    if "overall_soundscape:" in prompt:
        position = prompt.index("overall_soundscape:")
        prompt = prompt[:position].rstrip() + "\n" + binding + "\n\n" + prompt[position:]
    else:
        prompt += "\n" + binding
    tokens = clip.tokenize(prompt, minimax_ref_items=ref_items)
    positive = clip.encode_from_tokens_scheduled(tokens)
    positive = node_helpers.conditioning_set_values(positive, {"minimax_keyframes": keyframes, "minimax_refs": refs})
    return positive, latent


class H3VoiceLibrary:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"模式": (["模型默认", "参考音色"], {"default": "模型默认"}),
                             "角色分配": ("STRING", {"default": "[]", "multiline": True})}}

    RETURN_TYPES = ("H3_VOICE_SETTINGS",)
    RETURN_NAMES = ("角色音色",)
    FUNCTION = "execute"
    CATEGORY = "H3/模型与采样"
    DESCRIPTION = "保存参考人声并按角色分配给H3 RefDelta模型；这是参考音色，不是独立语音克隆。默认模式不加载音频。"

    def execute(self, **kwargs):
        return (normalize_voice_settings(kwargs["模式"], kwargs["角色分配"]),)


def register_voice_routes():
    from aiohttp import web
    from server import PromptServer
    server = getattr(PromptServer, "instance", None)
    if server is None or getattr(server, "_h3_voice_routes", False):
        return
    server._h3_voice_routes = True

    @server.routes.get("/h3-director/voices")
    async def list_voices(request):
        return web.json_response({"voices": _library(), "max_seconds": MAX_SECONDS})

    @server.routes.get("/h3-director/voices/{identity}/audio")
    async def preview_voice(request):
        try:
            return web.FileResponse(_audio_path(request.match_info["identity"]), headers={"Content-Type": "audio/wav"})
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=404)

    @server.routes.post("/h3-director/voices")
    async def add_voice(request):
        try:
            reader = await request.multipart()
            fields = {}
            content = bytearray()
            async for part in reader:
                if part.name == "audio":
                    while chunk := await part.read_chunk():
                        content.extend(chunk)
                        if len(content) > MAX_BYTES:
                            raise ValueError("参考音频应小于50MB。")
                elif part.name in ("name", "gender"):
                    fields[part.name] = (await part.text())[:100]
            item = await asyncio.to_thread(save_voice, bytes(content), fields.get("name", ""), fields.get("gender", "未指定"))
            return web.json_response({"voice": item})
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)


VOICE_NODE_CLASS_MAPPINGS = {"H3VoiceLibrary": H3VoiceLibrary}
VOICE_NODE_DISPLAY_NAME_MAPPINGS = {"H3VoiceLibrary": "角色音色库"}
