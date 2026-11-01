"""Self-contained storyboard clipboard plans, locally verified without an LLM."""
from __future__ import annotations

import copy
import json
import math
import re
import hashlib
from pathlib import Path

from .contract import H3_FIELDS, render_h3_prompt

SCHEMA = "h3-plan/1"
MAX_BYTES = 8 * 1024 * 1024
_PLAN_KEYS = {"status", "llm_released", "scene_count", "scene_seconds", "seconds_per_scene", "total_seconds",
              "speech_mode", "audio_mode", "raw_script", "prompt_rules", "duration_mode", "environment_anchor",
              "dialogue_whitelist", "dialogues_detected_from_source", "scenes", "structured_scenes",
              "final_image_prompt", "generated_reference_ids", "compiled_prompts", "prompts", "continuity", "constraints"}
_PLAN_KEYS.update({"prompt_format", "dialogue_pacing"})
_PLAN_KEYS.add("input_mode")
_RUNTIME_KEYS = {"path", "filename", "subfolder", "run_id", "node_id", "image", "end_image", "video", "report",
                 "output_directory", "image_path", "end_image_path", "video_path", "tail_path", "final_path",
                 "generated_image_path", "repaired_image_path", "source_images", "effective_settings"}


def _portable(value, depth=0):
    if depth > 16:
        raise ValueError("导入计划层级过深。")
    if isinstance(value, dict):
        return {str(key): _portable(item, depth + 1) for key, item in value.items()
                if str(key) not in _RUNTIME_KEYS and not str(key).endswith("_path")}
    if isinstance(value, list):
        if len(value) > 2000:
            raise ValueError("导入计划列表过长。")
        return [_portable(item, depth + 1) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        if isinstance(value, str) and len(value) > 250000 or isinstance(value, float) and not math.isfinite(value):
            raise ValueError("导入计划文本过长或含无效数字。")
        return value
    raise ValueError("导入计划必须是可复制的纯文本数据。")


def export_plan(plan, durations, *, mode=None, references=(), optimized_script=""):
    if not isinstance(plan, dict) or not plan.get("scenes"):
        return None
    text_only = plan.get("input_mode") == "T2VA"
    if text_only and references:
        raise ValueError("文生视频计划不能包含参考图片。")
    portable = _portable({key: plan[key] for key in _PLAN_KEYS if key in plan})
    result = {"schema": SCHEMA, "plan": portable, "durations": list(durations),
              "prompt_format": portable.get("prompt_format", "工作台格式"),
              'source_script':portable.get('raw_script',''),
              "references": [{"id": item["id"]} for item in references if isinstance(item, dict) and "id" in item]}
    files=[]
    for item in references:
        image=item.get('image',{})
        if image.get('filename') and image.get('type','input')=='input':
            files.append({'id':item['id'],**{key:image.get(key,'') for key in ('filename','subfolder')},'type':'input',**({'sha256':item['sha256']} if item.get('sha256') else {})})
    if files:result['reference_files']=files
    if mode:
        result["mode"] = mode
    if text_only:
        result.update(mode="文生视频", input_mode="T2VA")
    if optimized_script:
        result["optimized_script"] = optimized_script
    return result


def parse_import_payload(raw):
    if raw in (None, "", "{}", {}):
        return None
    if isinstance(raw, str):
        if len(raw.encode("utf-8")) > MAX_BYTES:
            raise ValueError("导入优化计划超过8MB，请减少镜头内容。")
        try:
            raw = json.loads(raw)
        except (ValueError, RecursionError) as exc:
            raise ValueError("这段文字缺少完整分镜计划；请使用总览的复制按钮后粘贴，或导入完整计划JSON。") from exc
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA or not isinstance(raw.get("plan"), dict):
        raise ValueError("导入内容缺少完整计划信息，请从AI剧本优化总览复制。")
    try:
        encoded = json.dumps(raw, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("导入优化计划含无效数据。") from exc
    if len(encoded.encode("utf-8")) > MAX_BYTES:
        raise ValueError("导入优化计划超过8MB，请减少镜头内容。")
    return copy.deepcopy(raw)


def _durations(values, count):
    if (not isinstance(values, list) or len(values) != count or any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or
            not 1 <= value <= 15 for value in values)):
        raise ValueError("导入计划的镜头数量或每镜时长无效。")
    return [float(value) for value in values]


def _validate_plan(plan, durations, references, *, input_mode="I2V"):
    """Check both compiled text and authoritative speaker/timing assignments."""
    from .planner import (VOICE_MODES, compile_storyboard, validate_prompt_dialogue,
                          normalize_input_mode, validate_text_only_references)
    input_mode = normalize_input_mode(input_mode)
    text_only = input_mode == "T2VA"
    if text_only:
        validate_text_only_references(plan.get("raw_script", ""), references)
        if plan.get("generated_reference_ids"):
            raise ValueError("文生视频计划不能包含生成分镜图片绑定。")
    count = len(durations)
    if not isinstance(plan.get("scenes"), list) or len(plan["scenes"]) != count:
        raise ValueError("导入计划的分镜数量与当前设置不一致。")
    if plan.get("speech_mode") not in VOICE_MODES or plan.get("audio_mode") not in ("静音", "模型音频"):
        raise ValueError("导入计划缺少有效声音模式。")
    if _durations(plan.get("scene_seconds"), count) != durations:
        raise ValueError("导入计划内部时长不一致。")
    lines = plan.get("dialogue_whitelist")
    if not isinstance(lines, list) or len(lines) > 2000:
        raise ValueError("导入计划缺少对白白名单。")
    known_lines = set()
    for line in lines:
        if (not isinstance(line, dict) or not isinstance(line.get("id"), str) or not re.fullmatch(r"D[1-9]\d*", line["id"]) or
                line["id"] in known_lines or not isinstance(line.get("text"), str) or not line["text"].strip() or
                not isinstance(line.get("speaker"), str) or not re.fullmatch(r"S[1-9]\d*", line["speaker"]) or
                not isinstance(line.get("language"), str) or not line["language"].strip() or
                not isinstance(line.get("offscreen"), bool)):
            raise ValueError("导入计划的对白、说话人或语言信息不完整。")
        known_lines.add(line["id"])
    reference_ids = {item["id"] for item in references}
    compiled = plan.get("compiled_prompts")
    prompts = plan.get("prompts")
    if not isinstance(compiled, list) or not isinstance(prompts, list) or len(compiled) != count or len(prompts) != count:
        raise ValueError("导入计划缺少完整的原生视频提示词。")
    for index, scene in enumerate(plan["scenes"]):
        required = ("visual", "action", "start_state", "end_state") + (() if text_only else ("image_prompt",))
        if not isinstance(scene, dict) or any(not isinstance(scene.get(key), str) or not scene[key].strip()
                                               for key in required):
            raise ValueError(f"导入镜头{index + 1}缺少画面、动作、开场、结束或分镜图提示词。")
        refs = scene.get("reference_ids", [])
        if not isinstance(refs, list) or any(not isinstance(identity, str) or identity not in reference_ids for identity in refs):
            raise ValueError(f"导入镜头{index + 1}引用的参考图尚未上传。")
        if text_only:
            validate_text_only_references("\n".join(str(scene.get(key, "")) for key in required),
                                          refs or scene.get("reference_roles", {}))
            scene.setdefault("image_prompt", "")
        fields = json.loads(compiled[index]) if isinstance(compiled[index], str) else compiled[index]
        if not isinstance(fields, dict) or set(fields) != set(H3_FIELDS):
            raise ValueError(f"导入镜头{index + 1}缺少原生H3三字段。")
        rendered = render_h3_prompt(fields, input_mode=input_mode, duration_seconds=durations[index])
        if not isinstance(prompts[index], str) or not prompts[index].strip():
            raise ValueError(f"导入镜头{index + 1}缺少完整视频提示词。")
        validate_prompt_dialogue(rendered, lines, scene.get("dialogues", []))
        validate_prompt_dialogue(prompts[index], lines, scene.get("dialogues", []))
        if scene.get("video_prompt"):
            validate_prompt_dialogue(scene["video_prompt"], lines, scene.get("dialogues", []))
        if text_only:
            for text in (rendered, prompts[index], scene.get("video_prompt", "")):
                validate_text_only_references(text)
    # This compiler is pure: verifies local clocks, speaker identities, dialogue
    # coverage and order, without opening the LLM client or decoding references.
    compile_storyboard({"continuity": plan.get("continuity", ""), "constraints": plan.get("constraints", ""),
                        "environment_anchor": plan.get("environment_anchor", {}), "scenes": plan["scenes"]},
                       script=plan.get("raw_script", ""), count=count, seconds=durations,
                       dialogues=lines, voice_mode=plan["speech_mode"], input_mode=input_mode)
    if not text_only and (not isinstance(plan.get("final_image_prompt"), str) or not plan["final_image_prompt"].strip()):
        raise ValueError("导入计划缺少结束帧提示词。")


def import_plan(raw, *, count, durations, references, mode, voice_mode, duration_mode="手动设置", target_total_seconds=None,
                prompt_format="工作台格式", input_mode="I2V"):
    payload = parse_import_payload(raw)
    if payload is None:
        return None
    from .planner import normalize_input_mode, validate_text_only_references
    input_mode = normalize_input_mode(input_mode)
    imported_input = normalize_input_mode(payload["plan"].get("input_mode", "I2V"))
    if imported_input != input_mode or ("input_mode" in payload and normalize_input_mode(payload["input_mode"]) != input_mode):
        raise ValueError("导入计划的视频输入类型与当前选择不同，请切换到对应的图生或文生视频。")
    if input_mode == "T2VA":
        if mode != "文生视频" or payload.get("mode") != "文生视频":
            raise ValueError("文生视频需要对应的文生计划，不能沿用图片分镜计划。")
        if payload.get("reference_files"):
            raise ValueError("文生视频计划不能包含参考图片文件。")
        validate_text_only_references(payload["plan"].get("raw_script", ""), references)
    if 'source_script' in payload and payload['source_script']!=payload['plan'].get('raw_script',''):
        raise ValueError('文件中的原始剧本与分镜计划原稿不一致，请重新导出完整计划。')
    bindings={item['id']:item for item in references}
    for entry in payload.get('reference_files',[]):
        if not isinstance(entry,dict):raise ValueError('参考图文件信息无效。')
        expected=entry.get('sha256');bound=bindings.get(entry.get('id'))
        if expected and bound and bound.get('path'):
            digest=hashlib.sha256()
            with Path(bound['path']).open('rb') as stream:
                for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
            if digest.hexdigest()!=expected:raise ValueError(f"{entry['id']}与导出时的图片内容不同，请补回对应参考图。")
    from .prompt_formats import normalize_prompt_format
    expected_format = normalize_prompt_format(prompt_format)
    imported_format = normalize_prompt_format(payload.get("prompt_format", payload["plan"].get("prompt_format")))
    if (imported_format != expected_format or
            normalize_prompt_format(payload["plan"].get("prompt_format", imported_format)) != imported_format):
        raise ValueError("导入计划的提示词格式与当前选择不同，请先切换到复制时的格式。")
    if payload.get("mode") and payload["mode"] != mode:
        raise ValueError("导入计划的生成模式与当前模式不同，请先切换为复制时的模式。")
    exported_references = payload.get("references")
    if exported_references is not None:
        if (not isinstance(exported_references, list) or any(not isinstance(item, dict) or
                not isinstance(item.get("id"), str) or not re.fullmatch(r"图[1-9]\d*", item["id"])
                for item in exported_references) or
                len({item["id"] for item in exported_references}) != len(exported_references) or
                {item["id"] for item in exported_references} != {item["id"] for item in references}):
            raise ValueError("导入计划的参考图编号与当前图库不一致，请先补齐对应参考图。")
    actual = _durations(payload.get("durations"), count)
    if duration_mode == "AI自动分配":
        if target_total_seconds is None or not math.isclose(sum(actual), target_total_seconds, abs_tol=1e-6):
            raise ValueError("导入计划的总秒数与当前目标总秒数不一致。")
    elif any(not math.isclose(old, new, abs_tol=1e-6) for old, new in zip(_durations(durations, count), actual)):
        raise ValueError("导入计划的每镜秒数与当前设置不一致。")
    portable = _portable({key: payload["plan"][key] for key in _PLAN_KEYS if key in payload["plan"]})
    portable["prompt_format"] = imported_format
    if portable.get("speech_mode") != voice_mode:
        raise ValueError("导入计划的声音模式与当前设置不同，请先选择复制时的声音模式。")
    try:
        _validate_plan(portable, actual, references, input_mode=input_mode)
        text = payload.get("optimized_script")
        if text is not None:
            if not isinstance(text, str) or not text.strip():
                raise ValueError("粘贴的优化总览为空。")
            from .plan_edits import apply_plan_edits
            portable = apply_plan_edits(portable, {"optimized_script": text}, input_mode=input_mode)
            _validate_plan(portable, actual, references, input_mode=input_mode)
    except (KeyError, TypeError, IndexError, json.JSONDecodeError) as exc:
        raise ValueError("导入计划的原生字段、说话人或对白时间不完整，请重新从总览复制。") from exc
    portable.update(status="ready", llm_released=True, scene_count=count, scene_seconds=actual,
                    total_seconds=sum(actual), seconds_per_scene=actual[0] if len(set(actual)) == 1 else None)
    return portable
