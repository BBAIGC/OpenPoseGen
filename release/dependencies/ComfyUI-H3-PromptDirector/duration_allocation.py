"""Validate and quantize a language-model duration allocation without retries."""
from __future__ import annotations

import math
import re

DURATION_MODES = ("手动设置", "AI自动分配")


def allocation_target(count, total):
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 100:
        raise ValueError("分镜数量应为1～100。")
    try:
        if isinstance(total, bool):
            raise ValueError
        total = float(total)
    except (TypeError, ValueError) as exc:
        raise ValueError("自动分配需要填写有效的总时长。") from exc
    if (not math.isfinite(total) or not count <= total <= 15*count or
            not math.isclose(total*2, round(total*2), abs_tol=1e-8)):
        raise ValueError(f"{count}镜的总时长应在{count}～{15*count}秒之间，并以0.5秒为单位。")
    return total


def normalize_allocation(raw, count, total):
    total = allocation_target(count, total)
    if not isinstance(raw, dict):
        raise ValueError("千问返回的时长分配无效。")
    source = raw.get("scene_seconds")
    if not isinstance(source, list) or len(source) != count:
        raise ValueError("千问分配的镜头数量不正确。")
    values = []
    for value in source:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("千问返回的镜头时长不是有效数字。")
        value = float(value)
        if not math.isfinite(value) or value <= 0 or value > 1500:
            raise ValueError("千问返回的镜头时长超出可处理范围。")
        values.append(value)
    # Preserve the model's relative allocation, then project onto the bounded
    # half-second lattice. No equal-split heuristic substitutes for its choice.
    scale = total/sum(values)
    desired = [value*scale*2 for value in values]
    units = [min(30, max(2, math.floor(value))) for value in desired]
    target = round(total*2)
    while sum(units) < target:
        candidates = [index for index, value in enumerate(units) if value < 30]
        index = max(candidates, key=lambda item: (desired[item]-units[item], -item))
        units[index] += 1
    while sum(units) > target:
        candidates = [index for index, value in enumerate(units) if value > 2]
        index = max(candidates, key=lambda item: (units[item]-desired[item], -item))
        units[index] -= 1
    result = [value/2 for value in units]
    reasons = raw.get("reasons", [])
    if reasons is not None and (not isinstance(reasons, list) or len(reasons) not in (0, count) or
                                any(not isinstance(value, str) or len(value) > 500 for value in reasons)):
        raise ValueError("千问的时长分配理由格式无效。")
    return {"mode": "AI自动分配", "source": "qwen", "requested_total": total,
            "scene_seconds": result, "raw_scene_seconds": values,
            "adjusted": any(not math.isclose(before, after, abs_tol=1e-8) for before, after in zip(values, result)),
            "reasons": reasons or []}


def allocation_request(script, count, total, dialogues):
    total = allocation_target(count, total)
    schema = {"type": "object", "properties": {
        "scene_seconds": {"type": "array", "minItems": count, "maxItems": count,
                          "items": {"type": "number", "minimum": 1, "maximum": 15, "multipleOf": 0.5}},
        "reasons": {"type": "array", "minItems": count, "maxItems": count, "items": {"type": "string"}},
    }, "required": ["scene_seconds", "reasons"], "additionalProperties": False}
    system = f"""你是视频分镜时长规划师。通读完整剧本与逐字对白，按剧情先后给恰好{count}镜分配时长。
总长必须为{total:g}秒；每镜1～15秒，0.5秒一档，数组和必须等于总长。
分配要依据每镜动作复杂度、运动所需时间、对白字数与语速、停顿以及镜头节奏，不要习惯性平均分。
保持事件与对白原顺序，不增删剧情，不改写对白。长对白和复杂动作应获得足够时间，静态停顿可以较短。
先把对白及其触发动作安排到整片的剧情节点，再分配时长；不要开头几镜说完所有话，后面留下连续长段重复姿态。
对白少于镜头数时允许无对白镜头，按叙事作用留出动作、反应与停顿；不为填满每镜而新增、拆改或重复台词。
原稿明确的静默段落、时间点、对白与动作的因果关系优先，不能为均匀分布而拆散；一镜尽量聚焦一个主要动作。
reasons同时说明对应事件或对白节点，长静默镜头说明原稿中的叙事用途，而非笼统写保持氛围。
只返回schema JSON。reasons用每镜一句简短中文说明分配依据，不复述整篇剧本，不添加对白。"""
    payload = {"full_script": script, "scene_count": count, "total_seconds": total,
               "minimum_seconds": 1, "maximum_seconds": 15, "step_seconds": 0.5,
               "dialogue_whitelist": dialogues}
    return system, payload, schema
