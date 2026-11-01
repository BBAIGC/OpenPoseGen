"""Execution-local ComfyUI metadata for newly generated H3 assets."""

from contextlib import contextmanager
from contextvars import ContextVar
from collections import OrderedDict
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from threading import Lock
from uuid import uuid4


_METADATA = ContextVar("h3_output_metadata", default=None)
_PROMPT_RECEIPTS = OrderedDict()
_RECEIPT_LOCK = Lock()


def _ancestors(prompt, identity):
    if not isinstance(prompt, dict):
        return {}
    pending = [str(identity)] if identity is not None else list(prompt)
    found = {}
    while pending and len(found) < 512:
        key = str(pending.pop(0))
        node = prompt.get(key)
        if key in found or not isinstance(node, dict):
            continue
        inputs = node.get('inputs', {})
        if not isinstance(inputs, dict):
            continue
        found[key] = {'class_type': node.get('class_type'), 'inputs': inputs}
        for value in inputs.values():
            if (isinstance(value, (list, tuple)) and len(value) == 2 and
                    isinstance(value[0], (str, int)) and type(value[1]) is int and
                    str(value[0]) in prompt):
                pending.append(str(value[0]))
    return {} if pending else found


def _receipt_key(prompt, identity):
    nodes = _ancestors(prompt, identity)
    if str(identity) not in nodes:
        return None
    try:
        payload = json.dumps(nodes, sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return None
    return str(identity), hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _remember(prompt, identity, value):
    key = _receipt_key(prompt, identity)
    if key is None:
        return
    with _RECEIPT_LOCK:
        _PROMPT_RECEIPTS[key] = value
        _PROMPT_RECEIPTS.move_to_end(key)
        while len(_PROMPT_RECEIPTS) > 256:
            _PROMPT_RECEIPTS.popitem(last=False)


def _receipt(prompt, identity):
    key = _receipt_key(prompt, identity)
    with _RECEIPT_LOCK:
        saved = _PROMPT_RECEIPTS.get(key)
        return dict(saved) if saved else None


def remember_source_prompt(prompt, unique_id, original):
    """Keep the actual source, including a resolved external text input."""
    _remember(prompt, unique_id, {'original': str(original), 'kind': 'image'})


def remember_prompt_result(prompt, unique_id, source, text, optimized=True):
    """Record execution results by their exact upstream API inputs, never by last node id."""
    original = str(source or '')
    nodes = _ancestors(prompt, unique_id)
    frozen = nodes.get(str(unique_id), {}).get('inputs', {}).get('固定优化结果') is True
    if not frozen:
        for identity, node in nodes.items():
            if node.get('class_type') == 'CodexQwenStudioPrompt':
                saved = _receipt(prompt, identity)
                if saved is not None:
                    original = saved['original']
                    break
    _remember(prompt, unique_id, {'version': 1, 'kind': 'image', 'original': original,
                                 'optimized': str(text) if optimized else ''})
    return original


def enrich_prompt_metadata(prompt, metadata, unique_id=None):
    """Attach only verified results belonging to this output's upstream graph."""
    nodes = _ancestors(prompt, unique_id)
    for kind in ('CodexPromptOptimize', 'CodexQwenStudioPrompt'):
        for identity, node in nodes.items():
            if node.get('class_type') != kind:
                continue
            saved = _receipt(prompt, identity)
            inputs = node.get('inputs', {})
            if (saved is None and kind == 'CodexPromptOptimize' and
                    inputs.get('固定优化结果') is True and
                    isinstance(inputs.get('固定优化文本'), str) and inputs['固定优化文本'].strip()):
                saved = {'version': 1, 'kind': 'image',
                         'original': str(inputs.get('固定原始文本') or ''),
                         'optimized': inputs['固定优化文本']}
            if saved is not None:
                metadata['codex_prompts'] = {'version': 1, 'optimized': '', **saved}
                return metadata
    return metadata


def set_story_prompt_metadata(original, optimized, scenes):
    metadata = _METADATA.get()
    if not isinstance(metadata, dict) or _disabled():
        return
    metadata['codex_prompts'] = {
        'version': 1, 'kind': 'h3', 'original': str(original or ''),
        'optimized': str(optimized or ''),
        'image_prompts': [str(scene.get('image_prompt') or '') for scene in scenes],
        'video_prompts': [str(scene.get('prompt') or '') for scene in scenes],
    }


def _disabled():
    try:
        from comfy.cli_args import args
        return bool(getattr(args, "disable_metadata", False))
    except ImportError:
        return False


def _json_copy(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _prompt_snapshot(prompt):
    # ComfyUI mutates its execution prompt with per-node cache receipts. Those
    # may contain NaN for IS_CHANGED nodes and are not graph input parameters.
    if isinstance(prompt, dict):
        prompt = {
            identity: {key: value for key, value in node.items() if key != "is_changed"}
            if isinstance(node, dict) else node
            for identity, node in prompt.items()
        }
    return _json_copy(prompt)


@contextmanager
def execution_metadata(prompt=None, extra_pnginfo=None):
    """Scope a JSON snapshot; omitted values inherit an outer execution.

    Internal shot helpers omit hidden inputs, so they preserve their parent
    context. No metadata is reconstructed from saved workflow files or assets.
    """
    metadata = None
    if not _disabled():
        metadata = _json_copy(_METADATA.get()) or {}
        if isinstance(extra_pnginfo, dict):
            for key, value in extra_pnginfo.items():
                if not isinstance(key, str):
                    continue
                try:
                    metadata[key] = _json_copy(value)
                except (TypeError, ValueError, OverflowError):
                    metadata.pop(key, None)
        if prompt is not None:
            try:
                metadata["prompt"] = _prompt_snapshot(prompt)
            except (TypeError, ValueError, OverflowError):
                metadata.pop("prompt", None)
        metadata = metadata or None
    token = _METADATA.set(metadata)
    try:
        yield
    finally:
        _METADATA.reset(token)


def current_metadata():
    """Return a detached snapshot, or None when absent/disabled."""
    if _disabled():
        return None
    return _json_copy(_METADATA.get())


def save_png(image, destination):
    """Save pixels unchanged, adding standard PNG text metadata if present."""
    metadata = current_metadata()
    if metadata:
        from PIL.PngImagePlugin import PngInfo

        info = PngInfo()
        for key, value in metadata.items():
            info.add_text(key, json.dumps(value, ensure_ascii=False, allow_nan=False))
        image.save(destination, format="PNG", pnginfo=info)
    else:
        image.save(destination, format="PNG")
    return str(destination)


def _graph_workflow(workflow):
    if not isinstance(workflow, dict):
        return False
    nodes, links = workflow.get("nodes"), workflow.get("links")
    if not isinstance(nodes, list) or not isinstance(links, list):
        return False
    ids = set()
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("type"), str) or not node["type"]:
            return False
        identity = node.get("id")
        if isinstance(identity, bool) or not isinstance(identity, (int, str)) or identity in ids:
            return False
        ids.add(identity)
    return all(isinstance(link, (list, dict)) for link in links)


def _same_workflow(destination, workflow):
    try:
        return json.loads(destination.read_text(encoding="utf-8")) == workflow
    except (OSError, ValueError):
        return False


def save_workflow_snapshot(directory, identity):
    """Atomically publish an importable graph without replacing other payloads."""
    metadata = current_metadata()
    workflow = metadata.get("workflow") if metadata else None
    if not _graph_workflow(workflow):
        return None

    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(identity or "run")).strip("_")[:96] or "run"
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    destination = root / ("工作流_" + safe + ".json")
    if _same_workflow(destination, workflow):
        return str(destination)
    payload = json.dumps(workflow, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=root, prefix=".h3-workflow-", suffix=".tmp",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        while True:
            try:
                # A hard-link publishes the complete file and cannot overwrite
                # an existing name, including when another execution races us.
                os.link(temporary, destination)
                return str(destination)
            except FileExistsError:
                if _same_workflow(destination, workflow):
                    return str(destination)
                destination = root / ("工作流_" + safe + "_" + uuid4().hex[:12] + ".json")
            except OSError:
                if os.name != "nt":
                    raise
                # Windows rename is atomic and refuses an existing destination;
                # this also supports filesystems that cannot create hard links.
                try:
                    os.rename(temporary, destination)
                    temporary = None
                    return str(destination)
                except FileExistsError:
                    if _same_workflow(destination, workflow):
                        return str(destination)
                    destination = root / ("工作流_" + safe + "_" + uuid4().hex[:12] + ".json")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
