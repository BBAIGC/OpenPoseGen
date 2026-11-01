"""Keep source roles and static composition separate from changing state."""
from __future__ import annotations

import re

_SOURCE = re.compile(r"@?(?:素材)?图([1-9]\d*)(?!\d)")
_CAMERA = re.compile(
    r"(?:大远景|远景|全景|中景|近景|特写|俯视|俯拍|仰视|仰拍|平视|鸟瞰|正面|背面|侧面|侧视|"
    r"三分之[一二]侧面|第一人称|POV|过肩|低机位|高机位|固定机位|手持机位|"
    r"广角|长焦|浅景深|深景深|前景|背景|画面(?:左侧|右侧|中央|上方|下方)|"
    r"\b(?:wide shot|close[- ]?up|medium shot|overhead|eye[- ]level|low[- ]angle|high[- ]angle)\b)", re.I)
_DYNAMIC = re.compile(r"穿上|脱下|戴上|摘下|拿起|放下|重新|再次|随后|然后|接着|最后|开始走|走向|走到")


def source_reference_labels(text):
    return re.sub(r"@(图[1-9]\d*)(?!\d)", r"素材\1", text)


def native_reference_prose(text):
    """H3 gets only its real boundary images; source labels are image-only."""
    return re.sub(r"@?(?:素材)?图[1-9]\d*(?!\d)(?:中|里)?(?:的)?", "参考画面中的", text)


def static_composition(visual, proposed=""):
    """An explicit static field wins; legacy visual contributes camera terms."""
    if proposed is not None and (not isinstance(proposed, str) or len(proposed) > 2000):
        raise ValueError("静态构图字段无效。")
    if proposed and proposed.strip():
        parts = re.split(r"(?<=[，,。；;\n])", proposed)
        result = "".join(part for part in parts if not _DYNAMIC.search(part)).strip()
        if result:
            return source_reference_labels(result)
    # Copying all legacy visual prose would restore obsolete outfits/props.
    # Its distinct camera terms are enough to preserve a shot's composition.
    terms = list(dict.fromkeys(match.group(0) for match in _CAMERA.finditer(visual)))
    return "，".join(terms)


def normalize_reference_roles(roles, allowed_ids):
    if roles is None:
        return {}
    if not isinstance(roles, dict) or len(roles) > 16:
        raise ValueError("素材角色对应表无效。")
    result = {}
    allowed = set(allowed_ids)
    for identity, role in roles.items():
        if not isinstance(identity, str) or identity not in allowed:
            raise ValueError("素材角色对应表引用了未绑定的图片。")
        if not isinstance(role, str) or not role.strip() or len(role) > 1000:
            raise ValueError("素材角色描述无效。")
        result[identity] = source_reference_labels(role.strip())
    return result


def infer_reference_roles(script, allowed_ids):
    """Keep explicit '@图N的角色' phrases without inventing visual content."""
    allowed = set(allowed_ids)
    result = {}
    pattern = re.compile(r"@(图[1-9]\d*)(?!\d)(?:中|里)?(?:的)?([^@，,。；;\n]{0,80})")
    for match in pattern.finditer(script):
        identity = match.group(1)
        if identity not in allowed:
            continue
        role = re.split(r"与|和|一起|共同|并且|以及|作为|用于|负责", match.group(2), maxsplit=1)[0].strip()
        if role:
            result[identity] = role
    return result


def reference_roles_text(roles):
    return "；".join(f"素材{identity}提供：{role}" for identity, role in roles.items())
