"""Editable six-section planning view, separate from the FL2VA wire format.

H3's official six-field protocol is Ref2VA-only. This workbench uses I2VA /
FL2VA: its structured plan can be reviewed in six sections, but the real text
encoder still receives the validated base-mode three fields.
"""
from __future__ import annotations

import re

PROMPT_FORMATS = ("工作台格式", "H3六段式")
SECTION_LABELS = {
    "subject_definitions": "主体定义",
    "summary": "概要",
    "retention_analysis": "保留分析",
    "detailed_description": "详细描述",
    "overall_soundscape": "整体声景",
    "non_diegetic_music": "画外配乐",
}


def normalize_prompt_format(value=None):
    value = PROMPT_FORMATS[0] if value is None else value
    if value not in PROMPT_FORMATS:
        raise ValueError("提示词格式应为工作台格式或H3六段式。")
    return value


def video_prompt_for_format(prompt, value=None):
    if normalize_prompt_format(value) != '工作台格式':
        return prompt
    return ''.join(part if part.startswith('<d>') else re.sub(r'(?<![A-Za-z0-9])S[1-9]\d*(?![A-Za-z0-9])', '该人物',re.sub(r'[（(]\s*S[1-9]\d*\s*[）)]', '', part))
                   for part in re.split(r'(<d>.*?</d>)',str(prompt),flags=re.S))


def is_section_heading(line):
    return any(line.strip() in (f"{label}（{key}）：", f"{label}（{key}）:",
                                f"{key}:", f"{label}：")
               for key, label in SECTION_LABELS.items())


def _section(key):
    return f"{SECTION_LABELS[key]}（{key}）："


def render_six_section_scene(scene, current, index, dialogue):
    """Keep source assets and actual per-call video image slots distinct."""
    binding = str(current.get("display_prompt", "")).split("\n", 1)[0]
    tokens = list(re.finditer(r"@(?:图[1-9]\d*|分镜[1-9]\d*|首帧[1-9]\d*|结束帧)(?!\d)", binding))
    # Picture numbers are local to this clip, never global storyboard IDs.
    for slot, token in reversed(list(enumerate(tokens[:2], 1))):
        binding = binding[:token.end()] + f"（<Picture {slot}>）" + binding[token.end():]
    refs = list(dict.fromkeys(scene.get("reference_ids", [])))
    lines = [f"镜头{index + 1} · {current['seconds']:g}秒"]
    if binding.startswith("@"):
        lines.append(binding)
    lines += [_section("subject_definitions"), "参考图：" + " ".join(f"@{identity}" for identity in refs),
              _section("summary"), f"画面：{scene.get('visual', '')}",
              _section("retention_analysis"), f"开场：{scene.get('start_state', '')}",
              f"结束：{scene.get('end_state', '')}", _section("detailed_description"),
              f"动作：{scene.get('action', '')}"]
    words = [dialogue.get(item.get("id"), "") for item in scene.get("dialogues", [])]
    if any(words):
        lines.append("对白：" + "；".join(word for word in words if word))
    lines += [_section("overall_soundscape"), f"环境音：{scene.get('soundscape', '')}",
              _section("non_diegetic_music"), f"配乐：{scene.get('music', '')}"]
    return "\n".join(lines)
