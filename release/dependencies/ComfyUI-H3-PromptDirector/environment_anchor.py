"""Reference-grounded environment locks, independent of changing prop facts."""
from __future__ import annotations

import copy
import re

ENVIRONMENT_KEYS = ("location", "time_of_day", "light_source", "color_temperature", "exposure", "background")
_LABELS = {"location": "地点", "time_of_day": "时段", "light_source": "光源",
           "color_temperature": "色温", "exposure": "曝光", "background": "背景"}
_DAY = re.compile(r"白天|日间|清晨|早晨|早上|上午|中午|下午|\bdaytime\b|\bmorning\b|\bafternoon\b", re.I)
_NIGHT = re.compile(r"夜晚|晚上|夜间|深夜|黑夜|午夜|\bnight(?:time)?\b|\bmidnight\b", re.I)
_NATURAL = re.compile(r"窗(?:外|边|户)?(?:的)?(?:日光|阳光|自然光)|窗光|日光|阳光|\b(?:sunlight|daylight)\b", re.I)
_ARTIFICIAL = re.compile(r"台灯|顶灯|壁灯|吊灯|室内灯|\b(?:desk lamp|table lamp|ceiling light)\b", re.I)
_BLOCK = re.compile(r"(?:\n?【环境一致性】).*?【环境约束结束】", re.S)
_D = re.compile(r"(<d>.*?</d>)", re.S)
# Do not promote a vision model's guessed grading into a persistent setting.
# The pixels remain authoritative unless the source explicitly requests it.
_COLOR_REQUEST = re.compile(r"暖(?:色|光|台灯|灯)|冷(?:色|光)|色温|色调|调色|金色(?:光|阳光)|\b(?:warm|cool|golden|color temperature|grading)\b", re.I)
_EXPOSURE_REQUEST = re.compile(r"曝光|过曝|欠曝|\bexposure\b", re.I)
_COLOR_PROSE = re.compile(
    r"(?:温暖|柔和|柔暖|金黄|橙黄|黄色|橙色|暖色|冷色|暖|冷)(?:的)?(?:环境)?(?:光线|灯光|光照|光|色调|色温|滤镜)(?:照明|照亮)?|"
    r"(?:柔和|自然)?(?:的)?(?:高|低)?曝光(?:感|效果)?|"
    r"\b(?:(?:soft|gentle|natural)\s+)?(?:warm|cool|golden|yellow|orange)(?:\s+(?:ambient|indoor|cinematic))?\s+(?:lighting|light|glow|tones?|color cast|filter)\b|"
    r"\b(?:bright|soft|natural|high|low|cinematic)\s+exposure\b", re.I)


def normalize_environment_anchor(value):
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - set(ENVIRONMENT_KEYS):
        raise ValueError("环境锚点格式无效。")
    result = {}
    for key, text in value.items():
        if not isinstance(text, str) or len(text) > 500 or re.search(r"</?(?:d|think)\b", text, re.I):
            raise ValueError("环境锚点内容无效。")
        text = text.strip()
        if text and text not in ("未知", "不确定", "无法确定", "unknown", "N/A"):
            result[key] = text
    return result


def environment_schema():
    return {"type": "object", "properties": {key: {"type": "string"} for key in ENVIRONMENT_KEYS},
            "additionalProperties": False,
            "description": "地点和背景只记录可见事实；光线、色温和曝光默认空字符串，交由原参考图保留，不猜测或补写。只有原稿明确指定的变化才填写。"}


def _time_class(text):
    day, night = bool(_DAY.search(text)), bool(_NIGHT.search(text))
    return "day" if day and not night else "night" if night and not day else None


def initial_environment_anchor(value, script=""):
    anchor = normalize_environment_anchor(value)
    # An explicit opening scene in the user's own prose overrides the image.
    # Later "then move to..." instructions belong to per-scene changes.
    opening = re.split(r"然后|随后|接着|最后|转到|切换到|变成|变为|改为|等到|到了", script, maxsplit=1)[0]
    opening = re.sub(r'“[^”]*”|「[^」]*」|『[^』]*』|‘[^’]*’|"[^"\n]*"', "", opening)
    positive = " ".join(part for part in re.split(r"[，,。；;\n]", opening)
                        if not re.search(r"不要|禁止|避免|不得|无需|不加|不偏|没有|保持原图|以.*参考图", part))
    if not _COLOR_REQUEST.search(positive):
        anchor.pop("color_temperature", None)
    if not _EXPOSURE_REQUEST.search(positive):
        anchor.pop("exposure", None)
    if not re.search(r"光|灯|\b(?:light|sun|lamp)\b", positive, re.I):
        anchor.pop("light_source", None)
    for match in re.finditer(_DAY.pattern + "|" + _NIGHT.pattern, opening, re.I):
        prefix, suffix = opening[max(0, match.start()-16):match.start()], opening[match.end():match.end()+16]
        if re.search(r"不要|禁止|避免|不得|不是|并非|无需", prefix):
            continue
        if (re.search(r"场景|时间|时段|设定|画面|地点|镜头|^", prefix) and
                re.search(r"客厅|室内|房间|卧室|厨房|餐厅|街道|户外|花园|办公室|场景|拍摄|光|的|[，,。；;：:]|^$", suffix)):
            anchor["time_of_day"] = match.group(0)
            break
    return anchor


def apply_environment_change(anchor, change, script):
    current = normalize_environment_anchor(anchor)
    if not change:
        return current
    if not isinstance(change, dict) or set(change) - {"changes", "source_quote"}:
        raise ValueError("环境变更记录无效。")
    quote = change.get("source_quote", "")
    updates = normalize_environment_anchor(change.get("changes"))
    if not isinstance(quote, str) or not quote or quote not in script or not updates:
        return current
    if re.search(r"(?:不要|不许|禁止|不得|避免|不能|无需|不再)[^。；;\n]{0,20}"
                 r"(?:转到|切到|切换|转场|移到|变为|变成|改为|天黑|入夜|天亮|夜晚|晚上|灯|光)", quote):
        return current
    # An arbitrary source quote (e.g. "pick up the cup") cannot authorize a
    # day/night switch. The quote must itself express an environment change.
    change_intent = re.search(r"转到|切到|切换到|转场|移到|走到|进入|离开|来到|变为|变成|改为|天黑|入夜|天亮|日出|日落|打开.*灯|关闭.*灯|关上.*灯|开启.*灯|调(?:亮|暗)|\b(?:switch|move|enter|turn on|turn off|sunset|sunrise)\b", quote, re.I)
    if not change_intent:
        return current
    if "time_of_day" in updates:
        time_class = _time_class(updates["time_of_day"])
        if time_class == "day" and not (_DAY.search(quote) or re.search(r"天亮|日出|sunrise", quote, re.I)):
            updates.pop("time_of_day")
        elif time_class == "night" and not (_NIGHT.search(quote) or re.search(r"天黑|入夜|日落|sunset", quote, re.I)):
            updates.pop("time_of_day")
    return {**current, **updates}


def environment_text(anchor):
    values = normalize_environment_anchor(anchor)
    detail = "；".join(f"{_LABELS[key]}：{values[key]}" for key in ENVIRONMENT_KEYS if key in values)
    return ("【环境一致性】环境、光线与色彩严格保留原参考图。" +
            (detail + "。" if detail else "") + "【环境约束结束】")


def sanitize_environment_text(text, anchor, *, environment_only=False):
    """Remove explicit opposing time/light phrases, retaining object actions."""
    values = normalize_environment_anchor(anchor)
    text = _BLOCK.sub("", str(text))
    time_class = _time_class(values.get("time_of_day", ""))
    forbidden_time = _NIGHT if time_class == "day" else _DAY if time_class == "night" else None
    source = values.get("light_source", "")
    natural, artificial = bool(_NATURAL.search(source)), bool(_ARTIFICIAL.search(source))
    parts = []
    for chunk in _D.split(text):
        if chunk.startswith("<d>"):
            parts.append(chunk)
            continue
        if environment_only:
            # Legacy continuity was an unstructured second source of guessed
            # illumination. Preserve its identity/material clauses only.
            clauses = re.split(r"(?<=[，,。；;\n])", chunk)
            retained = []
            for clause in clauses:
                described = re.search(r"白天|夜晚|夜间|晚上|早晨|上午|下午|光照|光源|暖光|冷光|灯光|色温|曝光|日光|阳光|窗光|台灯|顶灯|壁灯|吊灯|背景|客厅|室内|房间|卧室|厨房|街道|户外|\b(?:night|daytime|lighting|sunlight|background)\b", clause, re.I)
                if not described:
                    retained.append(clause)
                    continue
                identity = re.search(r"人物|身份|脸部|五官|材质|木纹|面部|家具", clause)
                if identity and identity.end() <= described.start():
                    # "Same person and warm indoor light" contains useful
                    # identity prose before the guessed lighting. Keep it.
                    retained.append(clause[:described.start()].rstrip(" ，,和与及的") + "。")
            chunk = "".join(retained)
        else:
            def grading(match):
                phrase = match.group(0)
                if _EXPOSURE_REQUEST.search(phrase):
                    return phrase if values.get("exposure") else ""
                requested_color = values.get("color_temperature", "") + values.get("light_source", "")
                warm = re.search(r"暖|金黄|橙|黄色|\b(?:warm|golden|yellow|orange)\b", phrase, re.I)
                cool = re.search(r"冷|\bcool\b", phrase, re.I)
                if warm and not re.search(r"暖|金黄|橙|黄色|\b(?:warm|golden|yellow|orange)\b", requested_color, re.I):
                    return ""
                if cool and not re.search(r"冷|\bcool\b", requested_color, re.I):
                    return ""
                return phrase
            chunk = _COLOR_PROSE.sub(grading, chunk)
            if forbidden_time:
                chunk = forbidden_time.sub("", chunk)
            elif time_class is None:
                # Unknown is a reference lock, not permission for a per-scene
                # LLM guess. Explicit user opening/change instructions have
                # already populated time_of_day before reaching this helper.
                chunk = _NIGHT.sub("", _DAY.sub("", chunk))
            if natural and not artificial:
                chunk = re.sub(r"(?:仅有|只有|完全由|改成|改为)?\s*" + _ARTIFICIAL.pattern + r"(?:照明|照亮)?", "", chunk, flags=re.I)
            if artificial and not natural:
                chunk = _NATURAL.sub("", chunk)
        parts.append(chunk)
    return "".join(parts).strip()


def lock_environment_prompt(prompt, anchor, end_anchor=None):
    """Idempotent on native H3 or image prose; speech <d> text is untouched."""
    start = normalize_environment_anchor(anchor)
    end = normalize_environment_anchor(end_anchor) if end_anchor is not None else start
    transitioning = end != start
    body = _BLOCK.sub("", str(prompt)).strip() if transitioning else sanitize_environment_text(prompt, start)
    lock = (environment_text(start) if not transitioning else
            "【环境一致性】本镜开场环境：" + "；".join(f"{_LABELS[key]}：{value}" for key, value in start.items()) +
            "。仅按原稿明确指定的变化过渡，结束环境：" + "；".join(f"{_LABELS[key]}：{value}" for key, value in end.items()) +
            "。其余环境跟随主参考图。【环境约束结束】")
    marker = "overall_soundscape:"
    if marker in body:
        index = body.index(marker)
        return body[:index].rstrip() + "\n" + lock + "\n\n" + body[index:]
    return body.rstrip() + "\n" + lock


def scene_environment_anchor(scene, fallback=None, *, ending=False):
    key = "end_environment_anchor" if ending else "environment_anchor"
    return copy.deepcopy(normalize_environment_anchor(scene.get(key, fallback)))
