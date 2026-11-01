"""Prompt guidance for internal boundaries of an explicitly continuous take.

This does not blend clips, create motion conditioning, or change frame clocks.
"""
from __future__ import annotations

import re


_CONTINUOUS = re.compile(
    r"一镜到底|长镜头|(?:一个|单个)?连续镜头|连续拍摄|"
    r"不(?:要)?切镜|不(?:要)?切换镜头|无剪辑|无切镜|"
    r"\b(?:single|one)[ -](?:continuous[ -])?(?:take|shot)\b|"
    r"\b(?:unbroken|continuous)[ -](?:take|shot)\b|\bno[ -]cuts\b",
    re.IGNORECASE,
)
_NEGATED = re.compile(
    r"(?:不要|不要求|不需要|不必|无需|避免|取消|并非|不是)\s*"
    r"(?:一镜到底|长镜头|(?:一个|单个)?连续镜头|连续拍摄)|"
    r"\b(?:not|avoid|without)\s+(?:a\s+)?(?:single|one|continuous|unbroken)"
    r"[ -](?:continuous[ -])?(?:take|shot)\b",
    re.IGNORECASE,
)


def is_continuous_take(raw_script):
    """Enable guidance only from the user's explicit continuous-take request."""
    if not isinstance(raw_script, str):
        return False
    return bool(_CONTINUOUS.search(_NEGATED.sub("", raw_script)))


def motion_continuity_note(raw_script, scene_index, scene_count, language="zh"):
    """Return one neutral video-body note; scene_index is zero based.

    Explicit story pauses and final holds remain authoritative. No new action,
    time, dialogue, editing effect, or technical speaker label is introduced.
    """
    if (type(scene_count) is not int or type(scene_index) is not int
            or scene_count < 1 or not 0 <= scene_index < scene_count):
        raise ValueError("分镜索引或总数无效。")
    if language not in ("zh", "en"):
        raise ValueError("连续动作说明语言应为zh或en。")
    if scene_count == 1 or not is_continuous_take(raw_script):
        return ""
    if scene_index == scene_count - 1:
        if language == "en":
            return (
                "This is the final segment of one continuous take. Continue the existing action and camera "
                "movement naturally from the preceding segment, without a fresh start. Preserve any pause "
                "or final hold explicitly requested by the original script; otherwise let the existing "
                "movement continue through the final frame rather than stopping early."
            )
        return (
            "本段是同一次连续拍摄的最后一段，承接前段原有动作和运镜，不重新起势。"
            "保留原剧本明确要求的停顿或最终停留；未要求停留时，原有运动自然持续至最后一帧，"
            "不要因片段即将结束而提前停住。"
        )
    if language == "en":
        return (
            "This segment is part of one continuous take. Its ending state is an instantaneous frame "
            "within the ongoing action, not a cue to settle into a held pose. Carry the action and camera "
            "movement already requested by the script through the boundary, with their direction and pace "
            "continuing into the next segment. Begin from the preceding instant without restarting the "
            "action. Preserve explicit story pauses and intentional stillness; introduce no extra action."
        )
    return (
        "本段属于同一次连续拍摄。结束状态是进行中动作的瞬间画面，不表示提前完成动作并保持姿势。"
        "原剧本已有的动作与运镜自然穿过分段边界，方向和节奏衔接下一段；"
        "开场承接上一瞬间，不重新起势。保留剧情明确要求的停顿与静止，不额外添加动作。"
    )


def apply_motion_continuity(fields, raw_script, scene_index, scene_count):
    """Return native video fields with one idempotent, non-spoken motion note.

    Global indices are mandatory: a planning batch's last clip is not necessarily
    the full video's last clip. Existing exact helper notes are removed outside
    dialogue blocks before inserting the current note; spoken text is untouched.
    """
    note = motion_continuity_note(raw_script, scene_index, scene_count)
    result = dict(fields)
    field = ("integrated_multimodal_description" if "integrated_multimodal_description" in result
             else "detailed_description" if "detailed_description" in result else None)
    if field is None:
        raise ValueError("视频描述字段缺失，无法添加连续动作说明。")
    body = result[field]
    if not isinstance(body, str):
        raise ValueError("视频描述字段必须是文本。")
    generated_notes = [motion_continuity_note("一镜到底", index, 2, language)
                       for language in ("zh", "en") for index in (0, 1)]
    parts = re.split(r"(<d>.*?</d>)", body, flags=re.S)
    for index, part in enumerate(parts):
        if part.startswith("<d>"):
            continue
        for previous in generated_notes:
            part = part.replace(previous, "")
        parts[index] = part
    body = "".join(parts).strip()
    if note:
        opening = re.match(r"\[Shot 1\]\s*", body)
        if opening:
            body = "[Shot 1] " + note + " " + body[opening.end():].lstrip()
        else:
            body = note + " " + body
    result[field] = body
    return result
