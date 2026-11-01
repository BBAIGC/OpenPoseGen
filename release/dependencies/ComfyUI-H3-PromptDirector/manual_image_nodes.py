"""Independent, disk-backed Image2.1 storyboard candidates.

Only metadata crosses the source link. Imported references remain input files,
so running this output node never requires executing the upstream video graph.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
import time
import uuid

import folder_paths

from .image_bridge import generate_reference, normalize_settings, validate_image_backend

MAX_SHOTS = 100
MAX_CANDIDATES = 24
_LOCK = threading.Lock()
_ACTIVE = {}


def _root():
    root = Path(folder_paths.get_output_directory()).resolve() / "H3分镜" / "独立生图"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _task_id(value):
    value = str(value or "")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{8,80}", value):
        raise ValueError("独立生图任务编号无效，请重新导入提示词。")
    return value


def _image_path(item):
    if not isinstance(item, dict) or item.get("type", "input") != "input":
        raise ValueError("请使用已经导入到本机的参考图。")
    filename, subfolder = item.get("filename"), item.get("subfolder", "")
    if not isinstance(filename, str) or not filename or not isinstance(subfolder, str):
        raise ValueError("参考图文件信息不完整。")
    root = Path(folder_paths.get_input_directory()).resolve()
    path = (root / subfolder / filename).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"参考图不存在或路径无效：{Path(filename).name}。")
    return path


def _preview(path):
    root = Path(folder_paths.get_output_directory()).resolve()
    path = Path(path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("候选图片不在ComfyUI输出目录内。")
    relative = path.relative_to(root)
    return {"filename": relative.name, "subfolder": relative.parent.as_posix(), "type": "output"}


def _candidate_exists(candidate):
    item = candidate.get("image", {}) if isinstance(candidate, dict) else {}
    if item.get("type") != "output" or not isinstance(item.get("filename"), str):
        return False
    root = Path(folder_paths.get_output_directory()).resolve()
    path = (root / str(item.get("subfolder", "")) / item["filename"]).resolve()
    return path.is_relative_to(root) and path.is_file()


def _save(data):
    path = _root() / (_task_id(data["task_id"]) + ".json")
    temporary = path.with_suffix(".partial.json")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _read(task_id):
    path = _root() / (_task_id(task_id) + ".json")
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (ValueError, OSError):
        return None


def _emit(data, node_id, phase=None):
    from server import PromptServer
    event = {key: data.get(key) for key in ("task_id", "status", "shots", "active_id", "error", "completed", "total")}
    event.update(node_id=str(node_id), phase=phase or data.get("phase", ""))
    server = getattr(PromptServer, "instance", None)
    if server is not None:
        server.send_sync("h3-manual-image-progress", event)
    return event


def _request(raw, source=None, image_settings=None):
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError) as exc:
        raise ValueError("独立生图任务格式无效，请重新导入提示词。") from exc
    if not isinstance(data, dict):
        raise ValueError("独立生图任务格式应为对象。")
    source = source if isinstance(source, dict) else {}
    if source and source.get("mode") != "手动分镜图":
        raise ValueError("独立生图节点只在手动分镜图模式启用。")
    if data.get("source_mode") != "手动分镜图" and not source:
        raise ValueError("请从手动分镜工作台导入提示词。")
    task_id = _task_id(data.get("task_id"))
    shots = data.get("shots", [])
    if not isinstance(shots, list) or not 1 <= len(shots) <= MAX_SHOTS:
        raise ValueError("请先导入1～100个分镜提示词。")
    settings = normalize_settings(image_settings or data.get("image_settings") or source.get("image_settings"))
    dimensions = []
    for key in ("width", "height"):
        value = data.get(key) if data.get("dimensions_source") == "manual" else source.get(key, data.get(key))
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value != int(value):
            raise ValueError("独立生图宽高无效。")
        value = int(value)
        if not 64 <= value <= 2048 or value % 32:
            raise ValueError("独立生图宽高应为64～2048的32倍数。")
        dimensions.append(value)
    ids = set()
    clean = []
    for row in shots:
        if not isinstance(row, dict):
            raise ValueError("分镜队列项格式无效。")
        identity = str(row.get("id", ""))
        prompt = row.get("prompt", "")
        if not identity or len(identity) > 100 or identity in ids or not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("分镜编号重复或提示词为空，请检查队列。")
        ids.add(identity)
        references = row.get("references", data.get("references", []))
        if not isinstance(references, list) or not 1 <= len(references) <= 16:
            raise ValueError(f"{identity}需要选择1～16张参考图。")
        refs = []
        for ref in references:
            _image_path(ref)
            refs.append({key: ref.get(key, "input" if key == "type" else "")
                         for key in ("filename", "subfolder", "type")})
        fingerprint = hashlib.sha256(json.dumps({"prompt": prompt, "references": refs,
            "width": dimensions[0], "height": dimensions[1], "settings": settings},
            sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        candidates = [entry for entry in row.get("candidates", []) if _candidate_exists(entry)]
        clean.append({"id": identity, "index": row.get("index", len(clean) + 1), "prompt": prompt,
                      "references": refs, "fingerprint": fingerprint, "candidates": candidates[-MAX_CANDIDATES:],
                      "retained": bool(row.get("retained")), "selected_candidate": row.get("selected_candidate"),
                      "skip": bool(row.get("skip")), "status": "pending"})
    selection = data.get("runSelection", [])
    if not isinstance(selection, list):
        raise ValueError("生成范围无效。")
    selection = set(map(str, selection)) if selection else ids
    seed = data.get("seed", 42)
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**32 - 1:
        raise ValueError("随机种子应为0～4294967295的整数。")
    return {"task_id": task_id, "shots": clean, "image_settings": settings,
            "width": dimensions[0], "height": dimensions[1], "seed": seed,
            "selection": selection, "regenerate": bool(data.get("regenerate")),
            "stop_after_current": bool(data.get("stopAfterCurrent"))}


def _merge_previous(request, previous):
    """The disk receipt survives global cancellation and stale serialized UI."""
    old = {row.get("id"): row for row in (previous or {}).get("shots", [])}
    for row in request["shots"]:
        prior = old.get(row["id"])
        if prior and prior.get("fingerprint") == row["fingerprint"]:
            known = {entry.get("id"): entry for entry in prior.get("candidates", []) if _candidate_exists(entry)}
            known.update({entry.get("id"): entry for entry in row["candidates"]})
            row["candidates"] = list(known.values())[-MAX_CANDIDATES:]
        elif prior:
            # Results from an edited prompt remain visible, but no longer count
            # as a completed result for the current prompt/reference selection.
            known = {entry.get("id"): {**entry, "stale": True} for entry in prior.get("candidates", []) if _candidate_exists(entry)}
            for entry in row["candidates"]:
                known[entry.get("id")] = {**entry, "stale": True}
            row["candidates"] = list(known.values())[-MAX_CANDIDATES:]
            row["retained"] = False


class H3ManualImageStudio:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"任务数据": ("STRING", {"default": "{}", "multiline": True})},
                "optional": {"分镜来源": ("H3_MANUAL_SOURCE",), "图像模型配置": ("H3_IMAGE_SETTINGS",)},
                "hidden": {"unique_id": "UNIQUE_ID", "prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"}}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("候选记录",)
    FUNCTION = "execute"
    OUTPUT_NODE = True
    CATEGORY = "H3/整稿与分镜"
    DESCRIPTION = "独立Image2.1候选生图；支持单张、批量、停止后保留结果和回填分镜。"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def execute(self, **kwargs):
        from .output_metadata import execution_metadata
        with execution_metadata(kwargs.get('prompt'), kwargs.get('extra_pnginfo')):
            return self._execute(**kwargs)

    def _execute(self, **kwargs):
        import comfy.model_management as mm
        raw = kwargs.get("任务数据", "{}")
        try:
            empty = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            empty = None
        if isinstance(empty, dict) and not empty.get("shots"):
            # A connected output node participates in the first planning run.
            # No imported queue is a harmless idle state, not a failed plan.
            event = {"node_id": str(kwargs.get("unique_id", "")), "task_id": empty.get("task_id", ""),
                     "status": "idle", "shots": [], "active_id": None, "error": "", "completed": 0, "total": 0}
            return {"ui": {"h3_manual_images": [event]}, "result": (json.dumps(event, ensure_ascii=False),)}
        request = _request(kwargs.get("任务数据", "{}"), kwargs.get("分镜来源"), kwargs.get("图像模型配置"))
        if not _LOCK.acquire(blocking=False):
            raise RuntimeError("已有独立生图任务正在运行，请先停止或等待完成。")
        node_id = str(kwargs.get("unique_id", ""))
        data = {"task_id": request["task_id"], "shots": request["shots"], "status": "running",
                "active_id": None, "error": "", "completed": 0, "total": 0}
        control = {"pause": request["stop_after_current"], "cancel": False, "node_id": node_id}
        try:
            from .output_metadata import save_workflow_snapshot
            workflow_path = save_workflow_snapshot(_root(), request['task_id'] + '_' + uuid.uuid4().hex[:12])
            if workflow_path:
                data['workflow_snapshot'] = workflow_path
            _merge_previous(request, _read(request["task_id"]))
            targets = [row for row in data["shots"] if row["id"] in request["selection"] and not row["skip"]]
            targets = [row for row in targets if not row["retained"] and
                       (request["regenerate"] or not any(not item.get("stale") for item in row["candidates"]))]
            data["total"] = len(targets)
            _ACTIVE[request["task_id"]] = control
            if targets:
                validate_image_backend(request["image_settings"])
            _save(data)
            _emit(data, node_id)
            for ordinal, row in enumerate(targets):
                if control["cancel"] or (control["pause"] and ordinal > 0):
                    data["status"] = "paused"
                    break
                mm.throw_exception_if_processing_interrupted()
                data["active_id"] = row["id"]
                row.update(status="running", error="", progress=0)
                _save(data)
                _emit(data, node_id, "loading")
                def progress(event):
                    row["progress"] = event.get("percent", row.get("progress", 0))
                    row["phase"] = event.get("phase", "")
                    _emit(data, node_id, row["phase"])
                try:
                    paths = [_image_path(ref) for ref in row["references"]]
                    candidate_number = len(row["candidates"])
                    seed = (request["seed"] + ordinal + candidate_number * 1009) % 2**32
                    output = generate_reference(row["prompt"], paths[0], request["width"], request["height"],
                                                seed=seed, reference_paths=paths[1:], settings=request["image_settings"],
                                                progress_callback=progress)
                    from .output_metadata import current_metadata, save_png
                    if current_metadata():
                        from PIL import Image
                        output_path = Path(output)
                        temporary = output_path.with_name(output_path.stem + '.metadata.png')
                        try:
                            with Image.open(output_path) as image:
                                save_png(image, temporary)
                            os.replace(temporary, output_path)
                        finally:
                            temporary.unlink(missing_ok=True)
                    candidate = {"id": uuid.uuid4().hex, "image": _preview(output), "created_at": time.time(),
                                 "seed": seed, "stale": False}
                    row["candidates"] = (row["candidates"] + [candidate])[-MAX_CANDIDATES:]
                    row.update(status="completed", progress=100, selected_candidate=candidate["id"])
                    data["completed"] += 1
                except Exception as exc:
                    row.update(status="failed", error=str(exc))
                    data["error"] = f"{row['id']}：{exc}"
                _save(data)
                _emit(data, node_id)
                if control["pause"]:
                    data["status"] = "paused"
                    break
            else:
                data["status"] = "failed" if any(row.get("status") == "failed" for row in targets) else "completed"
            data["active_id"] = None
            _save(data)
            event = _emit(data, node_id)
            return {"ui": {"h3_manual_images": [event]}, "result": (json.dumps(data, ensure_ascii=False),)}
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # InterruptProcessingException is a BaseException on ComfyUI 1.55.
            interrupted = "interrupt" in type(exc).__name__.lower() or control["cancel"]
            data.update(status="cancelled" if interrupted else "failed", error="" if interrupted else str(exc))
            for row in data["shots"]:
                if row.get("status") == "running":
                    row["status"] = "cancelled" if interrupted else "failed"
                    row["error"] = "" if interrupted else str(exc)
            data["active_id"] = None
            _save(data)
            _emit(data, node_id)
            raise
        finally:
            _ACTIVE.pop(request["task_id"], None)
            _LOCK.release()


def register_manual_image_routes():
    from aiohttp import web
    from server import PromptServer
    server = getattr(PromptServer, "instance", None)
    if server is None or getattr(server, "_h3_manual_image_routes", False):
        return
    server._h3_manual_image_routes = True

    @server.routes.get("/h3-director/manual-images/{identity}")
    async def read_manual_images(request):
        try:
            data = _read(request.match_info["identity"])
            if data is None:
                return web.json_response({"error": "尚无保存的候选图。"}, status=404)
            return web.json_response(data)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)

    @server.routes.post("/h3-director/manual-images/control")
    async def control_manual_images(request):
        try:
            body = await request.json()
            identity = _task_id(body.get("task_id"))
            action = body.get("action")
            if action not in ("pause", "cancel", "continue"):
                raise ValueError("停止方式无效。")
            control = _ACTIVE.get(identity)
            if control is None:
                return web.json_response({"active": False})
            if action == "cancel":
                control["cancel"] = True
                import comfy.model_management as mm
                mm.interrupt_current_processing(True)
            else:
                control["pause"] = action == "pause"
            return web.json_response({"active": True, "action": action})
        except (ValueError, TypeError) as exc:
            return web.json_response({"error": str(exc)}, status=400)


MANUAL_IMAGE_NODE_CLASS_MAPPINGS = {"H3ManualImageStudio": H3ManualImageStudio}
MANUAL_IMAGE_NODE_DISPLAY_NAME_MAPPINGS = {"H3ManualImageStudio": "Image2.1 独立分镜生图"}
