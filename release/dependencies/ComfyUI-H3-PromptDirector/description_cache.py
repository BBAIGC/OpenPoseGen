"""Persistent reverse-prompt text cache; downstream image settings never enter it."""
from __future__ import annotations
import json
import re
from pathlib import Path
import threading
import uuid
import folder_paths

_LOCK = threading.RLock()
_EPOCH_INPUT = '清除缓存批次'
_DESCRIBE_CLASSES = {'CodexReferenceDescribe', 'CodexJoyCaptionDescribe'}


def normalize_epoch(value=0):
    """Recover the known old widget tail without accepting arbitrary arrays as epochs."""
    for _ in range(5):
        if isinstance(value, dict) and set(value) == {'__value__'}:
            value = value['__value__']
        elif isinstance(value, (list, tuple)) and len(value) == 1:
            value = value[0]
        else:
            break
    if value is None or type(value) is bool:
        return 0
    if isinstance(value, (list, tuple)) and len(value) == 2 and type(value[0]) is bool and type(value[1]) is bool and list(value) == [False, True]:
        return 0
    if isinstance(value, str) and re.fullmatch(r'[0-9]+', value.strip()):
        value = int(value.strip())
    if type(value) is int and 0 <= value <= 2147483647:
        return value
    raise ValueError('清除缓存批次必须是0～2147483647的整数；仅兼容旧控件值[False, True]。')


def normalize_prompt_epochs(json_data):
    """Comfy validates list literals as links before custom validators run."""
    prompt = json_data.get('prompt') if isinstance(json_data, dict) else None
    if not isinstance(prompt, dict):
        return json_data
    for node in prompt.values():
        if not isinstance(node, dict) or node.get('class_type') not in _DESCRIBE_CLASSES:
            continue
        inputs = node.get('inputs')
        if not isinstance(inputs, dict) or _EPOCH_INPUT not in inputs:
            continue
        value = inputs[_EPOCH_INPUT]
        # Real links retain normal ComfyUI type/range and dependency validation.
        if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and type(value[1]) in (int, float):
            continue
        try:
            inputs[_EPOCH_INPUT] = normalize_epoch(value)
        except ValueError:
            # Preserve failure: an invalid scalar message reaches the core's
            # invalid_input_type path rather than crashing its list-as-link walk.
            inputs[_EPOCH_INPUT] = '无效清除缓存批次：' + repr(value)[:160]
    return json_data


def register_epoch_normalizer():
    from server import PromptServer
    instance = PromptServer.instance
    if not getattr(instance, '_codex_description_epoch_normalizer', False):
        instance.add_on_prompt_handler(normalize_prompt_epochs)
        instance._codex_description_epoch_normalizer = True


def cache_directory():
    get_user = getattr(folder_paths, 'get_user_directory', None)
    if get_user:
        return Path(get_user()) / 'default' / 'codex-description-cache'
    return Path(folder_paths.get_output_directory()) / 'codex-description-cache'


def read_description(key, legacy_key=None):
    paths = [cache_directory() / (key + '.json')]
    if legacy_key:
        paths.append(Path(folder_paths.get_temp_directory()) / 'codex-description-cache' / (legacy_key + '.json'))
    with _LOCK:
        for index, path in enumerate(paths):
            try:
                if path.stat().st_size > 1024 * 1024:
                    continue
                text = json.loads(path.read_text(encoding='utf-8')).get('text')
            except (OSError, ValueError, AttributeError):
                continue
            if isinstance(text, str) and text.strip():
                if index:
                    write_description(key, text)
                return text
    return None


def write_description(key, text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('反推结果为空，未覆盖已保存的提示词。')
    with _LOCK:
        directory = cache_directory()
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / (key + '.json')
        temporary = directory / (key + '.' + uuid.uuid4().hex + '.tmp')
        try:
            temporary.write_text(json.dumps({'text': text}, ensure_ascii=False), encoding='utf-8')
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


def description_result(text, epoch, hit):
    return {'ui': {'codex_description_cache': [{'epoch': normalize_epoch(epoch), 'cache_hit': bool(hit)}]}, 'result': (text,)}
