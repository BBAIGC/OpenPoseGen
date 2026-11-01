"""Pure, local edits of an existing storyboard; never runs a language model."""
from __future__ import annotations

import copy
import json
import re

from .contract import H3_FIELDS, render_h3_prompt
from .environment_anchor import (normalize_environment_anchor, sanitize_environment_text,
                                 lock_environment_prompt, scene_environment_anchor)
from .reference_composition import (static_composition, infer_reference_roles, native_reference_prose)
from .prompt_formats import is_section_heading
from .motion_continuity import apply_motion_continuity

_SOURCE_REF = re.compile(r"@(图[1-9]\d*)(?!\d)")
_UI_REF = re.compile(r"@(?:图[1-9]\d*|分镜[1-9]\d*|首帧[1-9]\d*|结束帧)(?!\d)")
_DIALOGUE_BLOCK = re.compile(r"<d>.*?</d>", re.S)
_LABELS = {"开场": "start_state", "画面": "visual", "动作": "action", "结束": "end_state",
           "对白": "dialogue", "视频提示词": "video_prompt", "分镜图提示词": "image_prompt",
           "图片提示词": "image_prompt"}
_LABELS.update({"参考图": "references_text", "环境音": "soundscape", "配乐": "music"})
_FIELDS = (set(_LABELS.values()) - {"references_text"}) | {"video_text", "reference_ids"}
_STRUCTURE = {"visual", "action", "start_state", "end_state", "dialogue", "soundscape", "music"}


def _text(value, label, *, allow_empty=False):
    if not isinstance(value, str) or len(value) > 32000 or not allow_empty and not value.strip():
        raise ValueError(f"{label}为空、过长或不是文本。")
    return value.strip()


def _clean(value):
    # Quoted speech is authoritative: UI markers outside its <d> blocks must
    # not leak into the model protocol, but actual spoken words stay intact.
    parts = re.split(r"(<d>.*?</d>)", value, flags=re.S)
    return "".join(part if part.startswith("<d>") else _UI_REF.sub("", part)
                   for part in parts).strip()


def parse_optimized_script(text, scene_count):
    """Parse the workbench's Chinese overview without guessing missing fields."""
    text = _text(text, "优化稿", allow_empty=True)
    if not text:
        return {}
    heading = re.compile(r"(?m)^[ \t]*(?:镜头[ \t]*([1-9]\d*)(?=[ \t·:：\n]|$)[^\n]*|结束帧[ \t]*[：:]?)[ \t]*$")
    matches = list(heading.finditer(text))
    if not matches or text[:matches[0].start()].strip():
        raise ValueError("优化稿请保留“镜头1”等分镜标题，再修改各镜内容。")
    result = {}
    for position, match in enumerate(matches):
        key = match.group(1) or "结束帧"
        if key != "结束帧" and not 1 <= int(key) <= scene_count or key in result:
            raise ValueError(f"优化稿的镜头编号{key}重复或超出范围。")
        body = text[match.end():matches[position + 1].start() if position + 1 < len(matches) else len(text)].strip()
        if not body:
            raise ValueError(f"镜头{key}没有内容。")
        if key == "结束帧":
            result[key] = {"image_prompt": re.sub(r"^(?:分镜图提示词|图片提示词)[：:]\s*", "", body)}
            continue
        values, free, active = {}, [], None
        for line in body.splitlines():
            if is_section_heading(line):
                active = None
                continue
            if not line.strip():
                if active:
                    values[active] += "\n"
                continue
            # Generated boundary bindings are a display-only line. Source
            # @图 IDs are captured independently below as explicit bindings.
            binding_line = re.sub(r"（<Picture [1-9]\d*>）", "", line)
            if (re.fullmatch(r"\s*(?:@(?:图\d+|分镜\d+|首帧\d+|结束帧)|[→\-\s])+", binding_line) or
                    re.fullmatch(r"\s*@(?:图[1-9]\d*|分镜[1-9]\d*|首帧[1-9]\d*|结束帧)\s+开始"
                                 r"(?:\s*→\s*@(?:图[1-9]\d*|分镜[1-9]\d*|首帧[1-9]\d*|结束帧)\s+结束)?\s*", binding_line)):
                continue
            label = re.match(r"^\s*([^：:\n]{1,16})[：:]\s*(.*)$", line)
            if label:
                if label.group(1) not in _LABELS:
                    raise ValueError(f"镜头{key}包含无法识别的字段：{label.group(1)}。")
                active = _LABELS[label.group(1)]
                if active in values and active != "dialogue":
                    raise ValueError(f"镜头{key}的{label.group(1)}重复。")
                values[active] = (values[active] + "\n" if active in values else "") + label.group(2)
            elif active:
                values[active] += "\n" + line
            else:
                free.append(line)
        if free:
            if values:
                raise ValueError(f"镜头{key}存在未归类内容，请放入画面或动作字段。")
            values["video_text"] = "\n".join(free)
        if not values:
            raise ValueError(f"镜头{key}没有可编辑内容。")
        reference_text = values.pop("references_text", None)
        if reference_text is not None and _SOURCE_REF.sub("", reference_text).strip():
            raise ValueError(f"镜头{key}的参考图项只填写@图编号，主体描述放入画面字段。")
        refs = list(dict.fromkeys(_SOURCE_REF.findall(reference_text if reference_text is not None else body)))
        if refs or reference_text is not None:
            values["reference_ids"] = refs
        result[key] = values
    return result


def normalize_plan_edits(raw, scene_count):
    if not isinstance(scene_count, int) or not 1 <= scene_count <= 100:
        raise ValueError("分镜数量应为1～100。")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except ValueError as exc:
            raise ValueError("优化稿编辑数据不是有效JSON。") from exc
    if not isinstance(raw, dict):
        raise ValueError("优化稿编辑数据应为对象。")
    if any(key in raw for key in ("optimized_script", "scenes", "ending")):
        if set(raw) - {"optimized_script", "scenes", "ending"}:
            raise ValueError("优化稿编辑数据包含未知字段。")
        result = parse_optimized_script(raw.get("optimized_script", ""), scene_count)
        scenes = raw.get("scenes", {})
        if not isinstance(scenes, dict):
            raise ValueError("逐镜编辑数据应为对象。")
        for key, values in scenes.items():
            key = str(key)
            if not isinstance(values, dict):
                raise ValueError(f"镜头{key}的编辑内容应为对象。")
            result[key] = {**result.get(key, {}), **values}
        if "ending" in raw:
            if not isinstance(raw["ending"], dict):
                raise ValueError("结束帧编辑内容应为对象。")
            result["结束帧"] = {**result.get("结束帧", {}), **raw["ending"]}
    else:
        result = copy.deepcopy(raw)
    normalized = {}
    for key, values in result.items():
        key = str(key)
        if key != "结束帧" and (not re.fullmatch(r"[1-9]\d*", key) or not 1 <= int(key) <= scene_count):
            raise ValueError(f"编辑的镜头编号{key}超出范围。")
        allowed = {"image_prompt"} if key == "结束帧" else _FIELDS
        if not isinstance(values, dict) or set(values) - allowed:
            raise ValueError(f"镜头{key}的编辑字段无效。")
        converted = {}
        for field, value in values.items():
            if field == "reference_ids":
                if not isinstance(value, list) or any(not isinstance(item, str) or not re.fullmatch(r"图[1-9]\d*", item) for item in value):
                    raise ValueError(f"镜头{key}的参考图编号无效。")
                converted[field] = list(dict.fromkeys(value))
            elif field == "dialogue" and isinstance(value, list):
                converted[field] = [_text(item, f"镜头{key}对白") for item in value]
            else:
                converted[field] = _text(value, f"镜头{key}的{field}", allow_empty=field in ("dialogue", "soundscape", "music"))
        normalized[key] = converted
    return normalized


def _fields(plan, index):
    raw = plan["compiled_prompts"][index]
    try:
        result = json.loads(raw) if isinstance(raw, str) else copy.deepcopy(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"镜头{index + 1}的原提示词结构无效，请重新规划。") from exc
    if not isinstance(result, dict) or set(result) != set(H3_FIELDS) or any(not isinstance(result[key], str) for key in H3_FIELDS):
        raise ValueError(f"镜头{index + 1}缺少原生H3三字段，请重新规划。")
    return result


def _anchors(plan, fields):
    body = fields[H3_FIELDS[0]]
    values = {}
    for name, key, following in (("连续性", "continuity", "限制|本镜开始时已成立的状态|本镜结束状态|第\\d"),
                                 ("限制", "constraints", "本镜开始时已成立的状态|本镜结束状态|第\\d")):
        match = re.search(name + r"：(.*?)(?=\s+(?:" + following + r")|$)", body, re.S)
        values[key] = plan.get(key, match.group(1).strip() if match else "")
    return values


def _edit_dialogue(plan, scene, value, index):
    assigned = scene.get("dialogues", [])
    lines = {item["id"]: item for item in plan.get("dialogue_whitelist", [])}
    old_words = [lines[item["id"]]["text"] for item in assigned]
    if isinstance(value, str) and value == "；".join(old_words):
        return False
    if not assigned:
        if value in ("", []):
            return False
        raise ValueError(f"镜头{index+1}原来没有对白分配；请在原稿指定对白并重新规划，以确定说话人和时间。")
    if isinstance(value, list):
        words = value
    elif len(assigned) == 1:
        words = [value]
    else:
        words = value.splitlines() if "\n" in value else value.split("；")
    if len(words) != len(assigned) or any(not word.strip() for word in words):
        raise ValueError(f"镜头{index+1}请保留{len(assigned)}句对白，使用换行或原分隔符分句。")
    if any("<" in word or ">" in word for word in words):
        raise ValueError("对白请填写原话，不加入控制标签。")
    changed = words != old_words
    for item, word in zip(assigned, words):
        lines[item["id"]]["text"] = word
    return changed


def _speech_tail(body, has_dialogue):
    if has_dialogue:
        first_words = _DIALOGUE_BLOCK.search(body)
        if first_words:
            starts = list(re.finditer(r"\s第\d+(?:\.\d+)?至\d+(?:\.\d+)?秒，", body[:first_words.start()]))
            if starts:
                # Action prose can also contain a timed instruction; the
                # immediately preceding clock belongs to the first <d> line.
                return body[starts[-1].start():]
        return ""
    match = re.search(r"\s本镜人物不说话。", body)
    return body[match.start():] if match else ""


def apply_plan_edits(plan, edits, *, input_mode=None):
    if not isinstance(plan, dict) or not isinstance(plan.get("scenes"), list):
        raise ValueError("已有分镜计划无效，请先完成一次规划。")
    from .planner import normalize_input_mode, validate_text_only_references
    plan_mode = normalize_input_mode(plan.get("input_mode", "I2V"))
    input_mode = plan_mode if input_mode is None else normalize_input_mode(input_mode)
    if input_mode != plan_mode:
        raise ValueError("编辑计划的视频输入类型与当前选择不同。")
    text_only = input_mode == "T2VA"
    result = copy.deepcopy(plan)
    count = len(result["scenes"])
    if text_only and isinstance(edits, dict):
        validate_text_only_references(edits.get("optimized_script", ""))
    edits = normalize_plan_edits(edits, count)
    if text_only:
        validate_text_only_references(result.get("raw_script", ""))
        for values in edits.values():
            validate_text_only_references("\n".join(value for value in values.values() if isinstance(value, str)),
                                          values.get("reference_ids", []))
    if not edits:
        return result
    from .planner import _boundary_image_prompt, _state_facts, _stable_continuity, compile_storyboard
    changed = {}
    for key, values in edits.items():
        if key == "结束帧":
            continue
        index, scene = int(key)-1, result["scenes"][int(key)-1]
        environment = scene_environment_anchor(scene, result.get("environment_anchor"))
        end_environment = scene_environment_anchor(scene, environment, ending=True)
        meaningful = {}
        for field, value in values.items():
            if field == "dialogue":
                if _edit_dialogue(result, scene, value, index):
                    meaningful[field] = value
            elif field in ("video_text", "reference_ids") or value != scene.get(field):
                meaningful[field] = value
        if not meaningful:
            continue
        changed[index] = meaningful
        for field in _STRUCTURE - {"dialogue"}:
            if field in meaningful:
                scene[field] = _clean(meaningful[field])
        if "start_state" in meaningful:
            scene["start_facts"] = {"当前可见状态": scene["start_state"]}
        if "end_state" in meaningful:
            # A human free-text state edit supersedes the prior generated
            # ledger; do not keep contradicted old clothing/object facts.
            scene["end_facts"] = {"当前可见状态": scene["end_state"]}
        explicit = list(dict.fromkeys(ref for value in meaningful.values() if isinstance(value, str)
                                     for ref in _SOURCE_REF.findall(value)))
        if "reference_ids" in meaningful:
            scene["reference_ids"] = meaningful["reference_ids"]
        elif explicit:
            scene["reference_ids"] = explicit
        if "visual" in meaningful:
            scene["composition"] = static_composition(meaningful["visual"])
        if explicit:
            role_source = "\n".join(value for value in meaningful.values() if isinstance(value, str))
            scene["reference_roles"] = {**scene.get("reference_roles", {}),
                                        **infer_reference_roles(role_source, scene["reference_ids"])}
        if "image_prompt" in meaningful:
            scene["image_prompt"] = lock_environment_prompt(_clean(meaningful["image_prompt"]), environment)
    # A shared boundary has one state. An explicit edit of the following
    # opening wins; otherwise inherit the edited ending into that opening.
    for index in range(count-1):
        if "end_state" in changed.get(index, {}) and "start_state" not in changed.get(index+1, {}):
            following = result["scenes"][index+1]
            if following.get("start_state") != result["scenes"][index]["end_state"]:
                following["start_state"] = result["scenes"][index]["end_state"]
                following["start_facts"] = copy.deepcopy(result["scenes"][index].get("end_facts", {}))
                changed.setdefault(index+1, {})["start_state"] = following["start_state"]
    for index, values in sorted(changed.items()):
        scene = result["scenes"][index]
        environment = scene_environment_anchor(scene, result.get("environment_anchor"))
        end_environment = scene_environment_anchor(scene, environment, ending=True)
        fields = _fields(result, index)
        anchors = _anchors(result, fields)
        if _STRUCTURE.intersection(values):
            line_ids = {item["id"] for item in scene.get("dialogues", [])}
            compile_scene = copy.deepcopy(scene)
            compile_scene["environment_anchor"], compile_scene["end_environment_anchor"] = environment, end_environment
            compiled = compile_storyboard({**anchors, "environment_anchor": environment, "scenes": [compile_scene]}, script=result.get("raw_script", ""),
                                          count=1, seconds=[result["scene_seconds"][index]],
                                          dialogues=[line for line in result.get("dialogue_whitelist", []) if line["id"] in line_ids],
                                          voice_mode=result.get("speech_mode", "自动识别原稿"),
                                          scene_index_offset=index, total_scene_count=count, input_mode=input_mode)
            new_fields = compiled["compiled"][0]["fields"]
            # Ordinary visual edits preserve original sound and music exactly.
            fields[H3_FIELDS[0]] = new_fields[H3_FIELDS[0]]
            for field, native in (("soundscape", H3_FIELDS[1]), ("music", H3_FIELDS[2])):
                if field in values:
                    fields[native] = new_fields[native]
        authorized_speech = _DIALOGUE_BLOCK.findall(fields[H3_FIELDS[0]])
        if "video_text" in values:
            body = _clean(values["video_text"])
            tail = _speech_tail(fields[H3_FIELDS[0]], bool(scene.get("dialogues")))
            if not _DIALOGUE_BLOCK.search(body):
                body += tail
            fields[H3_FIELDS[0]] = body if "[Shot 1]" in body else "[Shot 1] " + body
            scene["video_text"] = values["video_text"]
        if "video_prompt" in values:
            supplied = _clean(values["video_prompt"])
            marker = re.compile(r"(?m)^(" + "|".join(H3_FIELDS) + r"):\s*")
            parts = list(marker.finditer(supplied))
            if parts:
                if len(parts) != 3 or {part.group(1) for part in parts} != set(H3_FIELDS):
                    raise ValueError(f"镜头{index+1}的视频提示词需要完整的H3三字段。")
                fields = {part.group(1): supplied[part.end():parts[i+1].start() if i+1 < len(parts) else len(supplied)].strip()
                          for i, part in enumerate(parts)}
            else:
                if not _DIALOGUE_BLOCK.search(supplied):
                    supplied += _speech_tail(fields[H3_FIELDS[0]], bool(scene.get("dialogues")))
                fields[H3_FIELDS[0]] = supplied if "[Shot 1]" in supplied else "[Shot 1] " + supplied
        if set(values) - {"image_prompt", "reference_ids"}:
            actual_speech = [block for field in H3_FIELDS for block in _DIALOGUE_BLOCK.findall(fields[field])]
            if actual_speech != authorized_speech:
                raise ValueError(f"镜头{index+1}的视频提示词对白与已确认对白不同；请在“对白”项修改台词，画面描述中不要另加发声内容。")
            fields[H3_FIELDS[0]] = "".join(
                part if part.startswith("<d>") else native_reference_prose(part)
                for part in re.split(r"(<d>.*?</d>)", fields[H3_FIELDS[0]], flags=re.S))
            fields[H3_FIELDS[0]] = lock_environment_prompt(fields[H3_FIELDS[0]], environment, end_environment)
            fields = apply_motion_continuity(fields, result.get("raw_script", ""), index, count)
            encoded = json.dumps(fields, ensure_ascii=False, indent=2)
            prompt = render_h3_prompt(encoded, input_mode=input_mode, duration_seconds=result["scene_seconds"][index])
            result["compiled_prompts"][index] = encoded
            result["prompts"][index] = scene["video_prompt"] = prompt
        if not text_only and {"start_state", "visual"}.intersection(values) and "image_prompt" not in values:
            composition = scene.get("composition") or static_composition(scene.get("visual", ""))
            scene["image_prompt"] = _boundary_image_prompt(
                scene.get("start_state", ""),
                anchors["continuity"], facts=scene.get("start_facts"), inherited=index > 0,
                environment_anchor=environment, composition=composition,
                reference_roles=scene.get("reference_roles"))
        if text_only:
            scene["reference_ids"] = []
            scene["display_prompt"] = f"开场：{scene.get('start_state', '')}\n收尾：{scene.get('end_state', '')}\n本镜：{scene.get('visual', '')} {scene.get('action', '')}"
        else:
            boundary = scene.get("boundary_refs", [f"分镜{index+1}", f"分镜{index+2}" if index+1<count else "结束帧"])
            scene["display_prompt"] = (f"@{boundary[0]} → @{boundary[1]}\n开场：{scene.get('start_state', '')}"
                                        f"\n收尾：{scene.get('end_state', '')}\n本镜：{scene.get('visual', '')} {scene.get('action', '')}")
    if not text_only and "end_state" in changed.get(count-1, {}):
        result["final_image_prompt"] = _boundary_image_prompt(result["scenes"][-1]["end_state"],
                                                                facts=result["scenes"][-1].get("end_facts"), inherited=True,
                                                                environment_anchor=scene_environment_anchor(result["scenes"][-1], result.get("environment_anchor"), ending=True),
                                                                composition=result["scenes"][-1].get("composition", ""),
                                                                reference_roles=result["scenes"][-1].get("reference_roles"))
    if not text_only and "结束帧" in edits and "image_prompt" in edits["结束帧"]:
        result["final_image_prompt"] = lock_environment_prompt(_clean(edits["结束帧"]["image_prompt"]),
                                                               scene_environment_anchor(result["scenes"][-1], result.get("environment_anchor"), ending=True))
    if "structured_scenes" in result:
        result["structured_scenes"] = copy.deepcopy(result["scenes"])
    return result
