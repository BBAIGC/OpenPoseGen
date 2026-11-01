"""Independent, bounded BBAPI jobs; no ComfyUI queue or model-loading calls."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime, timezone
import hashlib
import inspect
import io
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from uuid import uuid4

from PIL import Image, ImageOps, PngImagePlugin

from generation import GenerationError, canvas_workflow, cloud_api_prompt, normalize

ACTIVE = {"queued", "running", "cancelling"}
MAX_INPUT_BYTES = 64 * 1024 * 1024
SUBFOLDER = "姿绘库-API"


def now():
    return datetime.now(timezone.utc).isoformat()


def safe_text(text, secret=""):
    value = str(text)
    if secret:
        value = value.replace(secret, "[已隐藏]")
    value = re.sub(r"(?:Bearer\s+|sk-)[A-Za-z0-9_.-]{8,}", "[已隐藏]", value, flags=re.I)
    value = re.sub(r"https?://\S+", "[链接已隐藏]", value)
    return value[:600]


def error_kind(status, message):
    text = str(message).lower()
    if status == 402 or any(word in text for word in ("insufficient_quota", "quota_exceeded", "insufficient_balance", "balance", "credit", "余额", "额度不足")):
        return "balance"
    if any(word in text for word in ("content_policy", "content_filter", "moderation", "policy_violation", "safety", "内容审核")):
        return "policy"
    if status in (401, 403):
        return "auth"
    if status == 429:
        return "rate_limit"
    if status in (408, 504) or any(word in text for word in ("timeout", "timed out", "超时")):
        return "timeout"
    return "upstream"


def _safe_path(root, relative):
    path = Path(relative)
    if path.anchor or ".." in path.parts or ":" in str(path) or "\x00" in str(path):
        raise ValueError("云端结果保存路径无效。")
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise ValueError("云端结果保存目录不能是链接。")
    if not current.resolve().is_relative_to(root.resolve()):
        raise ValueError("云端结果保存路径越界。")
    return current


class CloudJobs:
    def __init__(self, provider, resolver, data_dir, output_root, temp_root, on_completed=None, max_concurrency=16, input_root=None):
        if type(max_concurrency) is not int or not 1 <= max_concurrency <= 16:
            raise ValueError("云端并发须为1～16。")
        self.provider, self.resolver = provider, resolver
        self.path = Path(data_dir) / "cloud-jobs.json"
        self.output_root, self.temp_root = Path(output_root).resolve(), Path(temp_root).resolve()
        self.input_root = Path(input_root).resolve() if input_root is not None else self.output_root.parent / "input"
        self._schemas = json.loads((Path(__file__).parent / "workflows/node-schema.json").read_text(encoding="utf-8"))
        self.on_completed = on_completed
        self.max_concurrency = max_concurrency
        self.jobs = OrderedDict()
        self._tasks, self._flags = {}, {}
        self._pool = ThreadPoolExecutor(max_workers=max_concurrency, thread_name_prefix="asset-cloud")
        self._slots = asyncio.Semaphore(max_concurrency)
        self._encoding = threading.BoundedSemaphore(2)
        self._save_lock = asyncio.Lock()
        self._closed = False
        if self.path.is_file():
            saved = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if not isinstance(saved, dict) or saved.get("version") != 1:
                raise ValueError("云端任务记录格式无效，已保留原文件。")
            for job in saved.get("jobs", [])[-100:]:
                if not isinstance(job, dict) or not isinstance(job.get("id"), str) or not job["id"].startswith("cloud-"):
                    continue
                if job.get("status") in ACTIVE:
                    job.update(status="failed", completed_at=now(), error="服务已重启，未自动重新提交云端请求；请先核对站点记录。", error_type="interrupted")
                self.jobs[job["id"]] = job

    def _public(self, job):
        result = copy.deepcopy({key: value for key, value in job.items() if not key.startswith("_")})
        start, end = result.get("started_at"), result.get("completed_at")
        try:
            stopped = datetime.fromisoformat(end) if end else datetime.now(timezone.utc)
            result["elapsed_seconds"] = round(max(0, (stopped - datetime.fromisoformat(start)).total_seconds()), 1) if start else 0
        except (TypeError, ValueError):
            result["elapsed_seconds"] = 0
        result.update(remaining_seconds=None, remaining_scope=None, estimated=False, step=None, value=None, max=None)
        return result

    def _trim(self):
        while len(self.jobs) > 100:
            old = next((key for key, value in self.jobs.items() if value.get("status") not in ACTIVE and key not in self._tasks), None)
            if old is None:
                raise GenerationError("最多同时保留100个云端任务。")
            self.jobs.pop(old)

    async def _persist(self):
        async with self._save_lock:
            self._trim()
            snapshot = {"version": 1, "jobs": copy.deepcopy(list(self.jobs.values()))}
            def write():
                self.path.parent.mkdir(parents=True, exist_ok=True)
                descriptor, filename = tempfile.mkstemp(prefix=".cloud-jobs-", suffix=".tmp", dir=self.path.parent)
                try:
                    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                        json.dump(snapshot, stream, ensure_ascii=False, separators=(",", ":"))
                        stream.flush()
                    os.replace(filename, self.path)
                finally:
                    if os.path.exists(filename):
                        os.unlink(filename)
            await asyncio.to_thread(write)

    async def submit(self, payload):
        if self._closed:
            raise GenerationError("云端任务服务已关闭，请重新打开资产库。")
        if not isinstance(payload, dict) or not str(payload.get("engine", "")).startswith("bbapi:"):
            raise GenerationError("请选择云端图像模型。")
        if len(self._tasks) >= 100 or sum(job.get("status") in ACTIVE for job in self.jobs.values()) >= 100:
            raise GenerationError("云端队列最多100个任务，请等待已有任务完成。")
        model = copy.deepcopy(self.provider.model(payload["engine"]))
        settings = normalize(payload, cloud_model=model)
        quality = payload.get("quality", "auto")
        if quality not in ("auto", "low", "medium", "high"):
            raise GenerationError("云端图片质量设置无效。")
        settings["quality"] = quality
        connection = self.provider.public_config()
        if not connection.get("configured") or connection.get("connection_status") != "connected":
            raise GenerationError("请先保存密钥并测试云端模型连接。")
        profile, base = connection.get("profile"), connection.get("base_url")
        if not isinstance(profile, str) or not isinstance(base, str):
            raise GenerationError("云端连接配置无效。")
        identifier = "cloud-" + uuid4().hex
        job = {"id": identifier, "prompt_id": identifier, "name": settings.get("name") or "BBAPI · " + model.get("name", model["id"]),
               "engine": settings["engine"], "status": "queued", "progress": None, "created_at": now(), "started_at": None,
               "completed_at": None, "images": [], "error": None, "error_type": None, "settings": settings,
               "provider": "bbapi", "queue": "cloud", "node": None, "node_count": 0, "node_titles": {},
               "_connection": {"profile": profile, "base_url": base, "model": model["id"]}}
        self.jobs[identifier] = job
        flag = self._flags[identifier] = threading.Event()
        await self._persist()
        self._tasks[identifier] = asyncio.create_task(self._run(identifier, flag), name=identifier)
        return self._public(job)

    def _references(self, job, flag):
        values, total, encoded = [], 0, 0
        ids = job["settings"]["asset_ids"]
        if len(ids) > 16:
            raise GenerationError("云端最多使用16张参考图。")
        with self._encoding:
            for identifier in ids:
                if flag.is_set() or self._closed:
                    raise InterruptedError("云端任务已取消。")
                if self.resolver is None:
                    raise GenerationError("参考图解析器尚未配置。")
                path = Path(self.resolver(identifier))
                if not path.is_file():
                    raise GenerationError("参考图已失效，请重新选择。")
                total += path.stat().st_size
                if total > MAX_INPUT_BYTES:
                    raise GenerationError("云端参考图总量不能超过64MiB。")
                with Image.open(path) as source:
                    if source.width * source.height > 80_000_000:
                        raise GenerationError("参考图超过8000万像素，请先缩小。")
                    image = ImageOps.exif_transpose(source).convert("RGB")
                    try:
                        buffer = io.BytesIO()
                        image.save(buffer, "PNG")
                        content = buffer.getvalue()
                    finally:
                        image.close()
                encoded += len(content)
                if encoded > MAX_INPUT_BYTES:
                    raise GenerationError("编码后的云端参考图总量不能超过64MiB。")
                values.append(content)
        return values

    def _workflow_metadata(self, job, references):
        """Archive the exact transmitted references and a key-free native graph."""
        descriptors = []
        subfolder = "OpenPoseGen-cloud-refs"
        directory = _safe_path(self.input_root, subfolder)
        if references:
            directory.mkdir(parents=True, exist_ok=True)
        for content in references:
            digest = hashlib.sha256(content).hexdigest()
            name = digest + ".png"
            target = _safe_path(self.input_root, subfolder + "/" + name)
            if target.exists():
                with target.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                        raise GenerationError("云端原始参考存档冲突，未替换已有文件。")
            else:
                descriptor, temporary = tempfile.mkstemp(prefix=".cloud-ref-", suffix=".tmp", dir=directory)
                try:
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(content)
                    os.replace(temporary, target)
                finally:
                    Path(temporary).unlink(missing_ok=True)
            descriptors.append({"filename": name, "subfolder": subfolder, "type": "input"})
        frozen = job["_connection"]
        graph = cloud_api_prompt(job["settings"], descriptors, {"profile": frozen["profile"], "base_url": frozen["base_url"]})
        quality = {"auto": "自动", "low": "省费", "medium": "标准", "high": "精细"}[job["settings"]["quality"]]
        for node in graph.values():
            if node["class_type"] in ("BBAPIMultiReference", "BBAPIPromptToImage"):
                node["inputs"]["质量"] = quality
        job["_graph"] = graph
        job["_workflow"] = canvas_workflow(graph, self._schemas)

    def _save_image(self, image, job, flag):
        if flag.is_set() or self._closed:
            raise InterruptedError("已取消本地保存；远端若已接收仍可能计费。")
        settings = job["settings"]
        root = self.output_root if settings["save_output"] else self.temp_root
        directory = _safe_path(root, SUBFOLDER)
        directory.mkdir(parents=True, exist_ok=True)
        filename = "cloud-" + uuid4().hex + ".png"
        destination = _safe_path(root, SUBFOLDER + "/" + filename)
        descriptor, temporary = tempfile.mkstemp(prefix=".cloud-image-", suffix=".tmp", dir=directory)
        os.close(descriptor)
        try:
            with self._encoding:
                if flag.is_set() or self._closed:
                    raise InterruptedError("云端任务已取消。")
                clean = image.convert("RGB")
                try:
                    clean.info.clear()
                    metadata = PngImagePlugin.PngInfo()
                    metadata.add_text("prompt", json.dumps(job["_graph"], ensure_ascii=False, separators=(",", ":")))
                    metadata.add_text("workflow", json.dumps(job["_workflow"], ensure_ascii=False, separators=(",", ":")))
                    metadata.add_text("openposegen", json.dumps({"version": 1, "engine": settings["engine"], "settings": settings}, ensure_ascii=False, separators=(",", ":")))
                    clean.save(temporary, "PNG", pnginfo=metadata)
                    width, height = clean.size
                finally:
                    clean.close()
            if flag.is_set() or self._closed:
                raise InterruptedError("云端任务已取消。")
            os.replace(temporary, destination)
            return {"filename": filename, "subfolder": SUBFOLDER, "type": "output" if settings["save_output"] else "temp",
                    "width": width, "height": height, "url": "", "queue": "cloud"}
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _execute(self, job, flag):
        secret = ""
        try:
            if flag.is_set() or self._closed:
                raise InterruptedError("任务已取消，尚未发送云端生成请求。")
            frozen = job["_connection"]
            credentials = self.provider.store.load(frozen["profile"], frozen["base_url"])
            secret = credentials["api_key"]
            if credentials.get("base_url", "").rstrip("/") != frozen["base_url"].rstrip("/"):
                raise GenerationError("排队任务的服务地址已修改，请重新确认后提交。")
            connection = self.provider.client.Connection(base_url=frozen["base_url"], api_key=secret, profile=frozen["profile"],
                                                         timeout=300, default_model=frozen["model"])
            references = self._references(job, flag)
            self._workflow_metadata(job, references)
            if flag.is_set() or self._closed:
                raise InterruptedError("任务已取消，尚未发送云端生成请求。")
            validate = getattr(self.provider.client, "validate_references", None)
            if validate:
                validate(frozen["model"], references, job["settings"]["resolution"])
            result = self.provider.client.ImageClient(connection).generate_one(
                index=0, model=frozen["model"], prompt=job["settings"]["prompt"], resolution=job["settings"]["resolution"],
                ratio=job["settings"]["ratio"], quality=job["settings"]["quality"], references=references,
                cancelled=lambda: flag.is_set() or self._closed,
                save_image=lambda image, index, request_id: self._save_image(image, job, flag))
            try:
                message = getattr(result, "error", "")
                status = int(getattr(result, "status", 0) or 0)
                file = getattr(result, "file", None)
                image = getattr(result, "image", None)
                if not message and not file and image is not None:
                    file = self._save_image(image, job, flag)
                if message or not file:
                    return {"_graph": job["_graph"], "_workflow": job["_workflow"],
                            "error": safe_text(message or "接口未返回可用图片；未自动重提，请核对站点记录。", secret),
                            "error_type": error_kind(status, message), "http_status": status,
                            "request_id": safe_text(getattr(result, "request_id", ""), secret)}
                return {"_graph": job["_graph"], "_workflow": job["_workflow"], "images": [file], "http_status": status,
                        "request_id": safe_text(getattr(result, "request_id", ""), secret)}
            finally:
                image = getattr(result, "image", None)
                if image is not None:
                    image.close()
        except InterruptedError:
            return {"cancelled": True}
        except Exception as error:
            message = safe_text(error, secret)
            return {"error": message or "云端请求失败；未自动重新提交。", "error_type": error_kind(getattr(error, "status", 0), message)}

    async def _run(self, identifier, flag):
        job = self.jobs[identifier]
        try:
            async with self._slots:
                if flag.is_set() or self._closed:
                    return
                job.update(status="running", started_at=now())
                await self._persist()
                # Cancellation leaves the actual network worker in its occupied
                # slot until it returns, so repeated cancel cannot exceed 16.
                result = await asyncio.get_running_loop().run_in_executor(self._pool, self._execute, copy.deepcopy(job), flag)
                if flag.is_set() or self._closed:
                    return
                if result.get("cancelled"):
                    job.update(status="cancelled", completed_at=now())
                elif result.get("error"):
                    job.update(status="failed", completed_at=now(), progress=None, **result)
                else:
                    job.update(status="completed", completed_at=now(), progress=1.0, **result)
                await self._persist()
                if job["status"] == "completed" and job["settings"].get("auto_open_folder") and self.on_completed:
                    try:
                        if inspect.iscoroutinefunction(self.on_completed):
                            await self.on_completed(self._public(job))
                        else:
                            value = await asyncio.to_thread(self.on_completed, self._public(job))
                            if inspect.isawaitable(value):
                                await value
                    except Exception as error:
                        job["folder_error"] = safe_text(error)
                        await self._persist()
        except asyncio.CancelledError:
            pass
        except Exception as error:
            if not flag.is_set() and not self._closed:
                job.update(status="failed", completed_at=now(), progress=None, error=safe_text(error), error_type="local")
                await self._persist()
        finally:
            self._tasks.pop(identifier, None)
            self._flags.pop(identifier, None)

    async def jobs_list(self, after_seq=0):
        return {"jobs": [self._public(job) for job in reversed(self.jobs.values())],
                "active_count": sum(job.get("status") in ACTIVE for job in self.jobs.values()),
                "events": [], "next_seq": 0, "queue": "cloud", "max_concurrency": self.max_concurrency}

    def _owned(self, identifier):
        if not isinstance(identifier, str) or not identifier.startswith("cloud-") or identifier not in self.jobs:
            raise GenerationError("云端任务不存在或记录已失效。")
        return self.jobs[identifier]

    async def result(self, identifier):
        return self._public(self._owned(identifier))

    async def cancel(self, identifier):
        job = self._owned(identifier)
        if job.get("status") not in ACTIVE:
            return self._public(job)
        flag = self._flags.get(identifier)
        if flag:
            flag.set()
        queued = job["status"] == "queued"
        job.update(status="cancelled", completed_at=now(), progress=None,
                   cancellation_note="任务尚未提交，已取消。" if queued else "已取消本地等待；远端若已接收仍可能计费，请核对站点记录。")
        if queued and identifier in self._tasks:
            self._tasks[identifier].cancel()
            self._tasks.pop(identifier, None)
            self._flags.pop(identifier, None)
        await self._persist()
        return self._public(job)

    async def retry(self, identifier):
        job = self._owned(identifier)
        if job.get("status") in ACTIVE:
            raise GenerationError("云端任务仍在运行，请先取消或等待结束。")
        return await self.submit(copy.deepcopy(job["settings"]))

    async def mark_saved(self, original_image, new_image):
        key = lambda image: tuple(str(image.get(field, "")).replace("\\", "/") for field in ("filename", "subfolder", "type"))
        updated = 0
        for job in self.jobs.values():
            for index, image in enumerate(job.get("images", [])):
                if key(image) == key(original_image):
                    job["images"][index] = {**image, **new_image}
                    updated += 1
        if updated:
            await self._persist()
        return {"updated": updated, "image": new_image}

    async def close(self):
        if self._closed:
            return
        self._closed = True
        tasks = list(self._tasks.values())
        for identifier, flag in self._flags.items():
            flag.set()
            job = self.jobs.get(identifier)
            if job and job.get("status") in ACTIVE:
                job.update(status="failed", completed_at=now(), progress=None, error_type="interrupted",
                           error="本地服务已关闭，云端请求未自动重提；远端若已接收仍可能计费，请先核对记录。")
        await self._persist()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._pool.shutdown(wait=False, cancel_futures=True)
