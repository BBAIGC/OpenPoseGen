"""Explicit, queue-safe CPU/GPU resource release for the H3 director.

Internal callers own their model lifetimes. The HTTP operation additionally
holds ComfyUI's queue lock and refuses to touch an active or queued workflow.
No model files, outputs, history or other processes are removed.
"""
from __future__ import annotations

import asyncio
import gc
import ipaddress
import logging
import os
from pathlib import Path
import re
import sys
import threading
from urllib.parse import urlsplit

from aiohttp import web

log = logging.getLogger(__name__)
_cleaners = []
_cleaner_lock = threading.RLock()


def register_cleaner(callback):
    """Register an owned-weight-cache cleanup, without importing its owner."""
    with _cleaner_lock:
        if callback not in _cleaners:
            _cleaners.append(callback)


def _rss():
    try:
        import psutil
        return psutil.Process().memory_info().rss
    except (ImportError, OSError):
        return None


def _reset_idle_execution_caches():
    """Called only while the queue lock proves no execution can be active."""
    execution = sys.modules.get("execution")
    executor_type = getattr(execution, "PromptExecutor", None)
    if executor_type is None:
        return 0
    count = 0
    # Comfy's prompt worker owns its executor locally. Discover that exact type
    # instead of modifying core or retaining an executor in a global variable.
    for obj in gc.get_objects():
        if type(obj) is executor_type:
            obj.reset()
            count += 1
    return count


def release_resources(full=False, *, clear_execution=False):
    """Release owned model refs (full), offloaded models and allocator cache.

clear_execution is reserved for the guarded idle HTTP path. Sequence runners
must leave it false while executing. Dropping CPU weights makes the next run
reload them; an operating-system file cache may still remain reclaimable.
"""
    import comfy.model_management as mm

    before = _rss()
    failures = []
    if full:
        with _cleaner_lock:
            callbacks = tuple(_cleaners)
        for callback in callbacks:
            try:
                callback()
            except Exception as exc:
                log.exception("H3 owned cache cleanup failed")
                failures.append(type(exc).__name__)
    reset_count = _reset_idle_execution_caches() if clear_execution else 0
    mm.unload_all_models()
    collected = gc.collect()
    mm.soft_empty_cache()
    after = _rss()
    return {
        "ok": not failures, "full": bool(full),
        "execution_caches_reset": reset_count,
        "collected_objects": collected,
        "rss_before": before, "rss_after": after,
        "errors": failures,
        "message": "已释放模型和执行缓存，下次生成会重新加载权重。" if full
                   else "已卸载显存模型并回收空闲缓存。",
    }


def _loopback(host):
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _release_when_idle(server, full):
    queue = server.prompt_queue
    # The same lock is used by put/get. A new workflow cannot start between
    # checking the queue and releasing its previous executor's model refs.
    with queue.mutex:
        running, queued = queue.get_current_queue_volatile()
        if running or queued:
            return 409, {"ok": False, "message": "工作流正在运行或排队，请完成或取消后再清理。"}
        return 200, release_resources(full=full, clear_execution=full)


def _open_output(run_id=None):
    import folder_paths

    if run_id is not None and (not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id)):
        return 400, {"ok": False, "message": "任务编号无效。"}
    output_root = Path(folder_paths.get_output_directory()).resolve()
    directory = output_root / "H3分镜" / "工作台"
    if run_id is not None:
        directory /= run_id
    directory = directory.resolve()
    if not directory.is_relative_to(output_root) or not directory.is_dir():
        return 404, {"ok": False, "message": "保存目录不存在。"}
    try:
        os.startfile(str(directory))
    except (OSError, AttributeError):
        log.exception("Could not open H3 output folder")
        return 500, {"ok": False, "message": "无法打开保存目录，请按显示路径手动打开。", "path": str(directory)}
    return 200, {"ok": True, "path": str(directory)}


def register_routes():
    from server import PromptServer

    server = getattr(PromptServer, "instance", None)
    if server is None or getattr(server, "_h3_director_resource_routes", False):
        return

    @server.routes.post("/h3-director/release")
    async def release(request):
        origin = request.headers.get("Origin")
        if not _loopback(request.remote) or (origin and not _loopback(urlsplit(origin).hostname)):
            return web.json_response({"ok": False, "message": "资源清理仅允许本机操作。"}, status=403)
        try:
            body = await request.json()
        except (ValueError, TypeError):
            return web.json_response({"ok": False, "message": "请求格式无效。"}, status=400)
        if not isinstance(body, dict) or ("full" in body and type(body["full"]) is not bool):
            return web.json_response({"ok": False, "message": "清理选项无效。"}, status=400)
        status, result = await asyncio.to_thread(_release_when_idle, server, body.get("full", True))
        return web.json_response(result, status=status)

    @server.routes.post("/h3-director/open-output")
    async def open_output(request):
        origin = request.headers.get("Origin")
        if not _loopback(request.remote) or (origin and not _loopback(urlsplit(origin).hostname)):
            return web.json_response({"ok": False, "message": "保存目录仅允许本机打开。"}, status=403)
        try:
            body = await request.json()
        except (ValueError, TypeError):
            return web.json_response({"ok": False, "message": "请求格式无效。"}, status=400)
        if not isinstance(body, dict) or set(body) - {"run_id"}:
            return web.json_response({"ok": False, "message": "只接受任务编号，请勿传入文件路径。"}, status=400)
        status, result = await asyncio.to_thread(_open_output, body.get("run_id"))
        return web.json_response(result, status=status)

    server._h3_director_resource_routes = True

