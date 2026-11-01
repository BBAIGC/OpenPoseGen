"""Source-derived asset categories; pose angles stay on the asset itself.

RuiC's duo collection contains explicit adult source categories. Keep that
collection under 18+; the source's solo "sexy" labels alone are not an adult
classification. This is metadata organization, without image recognition.
"""
from __future__ import annotations

from urllib.parse import quote

VERSION = "1.1.0"
POSE_SERIES = {
    "full_sexy": "全身单人・性感系",
    "full_std": "全身单人・标准系",
    "upper": "上半身单人",
    "duo": "双人缠绵",
}
ADULT_POSE_SERIES = frozenset({"duo"})
IDENTITY_SECTIONS = {"face": "脸部", "styling": "造型"}
HAIR_LENGTHS = {"short": "短发", "medium": "中发", "semilong": "中长发", "long": "长发"}


def _label(value, fallback=""):
    if isinstance(value, dict):
        value = value.get("zh") or value.get("en") or value.get("ja")
    return str(value).strip() if value is not None else fallback


def _key(value):
    return quote(str(value), safe="-_.")


def category_path(item):
    """Return ordered nodes; each parent's count includes all descendants.

    Keys use stable source IDs, never angle IDs or display names for pose
    groups. Levels start at one. Unknown/user assets retain a simple source
    category instead of guessing a pose series or an adult classification.
    """
    if not isinstance(item, dict):
        return []
    kind = item.get("kind")
    if kind not in {"pose", "outfit", "identity"}:
        return []
    metadata = item.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    result = []

    def add(key, title, adult=False):
        if title:
            result.append({"key": key, "title": title, "adult": bool(adult), "level": len(result) + 1})

    if kind == "pose":
        series = metadata.get("series")
        if not isinstance(series, str) or not series:
            category = _label(item.get("category"), "其他姿态") or "其他姿态"
            add("pose/category/" + _key(category), category)
            return result
        adult = series in ADULT_POSE_SERIES
        if adult:
            add("pose/adult", "18+", True)
        series_key = "pose/series/" + _key(series)
        add(series_key, _label(metadata.get("series_names"), POSE_SERIES.get(series, series)), adult)
        category = _label(item.get("category"))
        if category:
            add(series_key + "/category/" + _key(category), category, adult)
        group_id = metadata.get("upstream_id") or metadata.get("pose_id") or metadata.get("group_id")
        if isinstance(group_id, str) and group_id:
            title = _label(metadata.get("names"), _label(metadata.get("number"), group_id))
            add(series_key + "/group/" + _key(group_id), title or group_id, adult)
        return result

    if kind == "identity":
        section = metadata.get("sec")
        category = _label(item.get("category"), "其他形象") or "其他形象"
        section_title = _label(metadata.get("section_names"), IDENTITY_SECTIONS.get(section, category))
        section_key = "identity/section/" + _key(section or category)
        add(section_key, section_title)
        hair = metadata.get("hair")
        if isinstance(hair, str) and hair:
            add(section_key + "/hair/" + _key(hair), _label(metadata.get("hair_names"), HAIR_LENGTHS.get(hair, hair)))
        return result

    category = _label(item.get("category"), "其他服装") or "其他服装"
    add("outfit/category/" + _key(metadata.get("cat") or category),
        _label(metadata.get("category_names"), category))
    return result


def category_items(asset_items):
    """Aggregate flat summary nodes with parent keys and descendant counts."""
    nodes = {}
    source = asset_items.values() if isinstance(asset_items, dict) else asset_items
    for item in source:
        parent = None
        for node in category_path(item):
            key = (item["kind"], node["key"])
            if key not in nodes:
                nodes[key] = {"kind": item["kind"], **node, "count": 0}
                if parent is not None:
                    nodes[key]["parent"] = parent
            nodes[key]["count"] += 1
            parent = node["key"]
    return list(nodes.values())
