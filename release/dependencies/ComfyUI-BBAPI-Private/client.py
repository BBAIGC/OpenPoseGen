"""Bounded OpenAI-compatible image transport, with no automatic paid POST retry."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
import io
import json
import math
import re
import socket
import threading
import time
from typing import Callable
from urllib.parse import urljoin, urlsplit
import uuid

from PIL import Image, ImageOps
import requests

MAX_IMAGE_BYTES = 64 * 1024 * 1024
MAX_RESPONSE_BYTES = 92 * 1024 * 1024
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 40_000_000
MAX_REFERENCES = 16
MAX_BATCH = 16


@dataclass(repr=False, frozen=True)
class Connection:
    base_url: str
    api_key: str = field(repr=False)
    profile: str = ""
    timeout: int = 300
    default_model: str = ""


@dataclass
class ItemResult:
    index: int
    image: Image.Image | None = field(default=None, repr=False)
    error: str = ""
    status: int = 0
    request_id: str = ""
    retry_after: str = ""
    submission_id: str = ""
    elapsed: float = 0
    file: dict | None = None


class ImageRequestError(RuntimeError):
    def __init__(self, message: str, status: int = 0, request_id: str = "", retry_after: str = ""):
        super().__init__(message)
        self.status = status
        self.request_id = request_id
        self.retry_after = retry_after


def safe_message(message: str, secret: str = "") -> str:
    text = str(message)
    text = re.sub(r"(?:Bearer\s+|sk-)[A-Za-z0-9_.-]{8,}", "[已隐藏]", text, flags=re.I)
    if secret:
        text = text.replace(secret, "[已隐藏]")
    # Signed media links can themselves carry access credentials.
    text = re.sub(r"https?://\S+", "[链接已隐藏]", text)
    return text[:500]


def bounded_read(response, limit: int, deadline: float) -> bytes:
    if time.monotonic() >= deadline:
        raise ImageRequestError("等待图片超时；任务未自动重提，请先核对站点记录。")
    declared = response.headers.get("Content-Length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise ImageRequestError("返回的数据超过本地大小限制。")
    expired = threading.Event()

    def abort_read():
        # Gateways may keep an HTTP 200 alive with whitespace while an upstream
        # is stuck. A read timeout alone can then reset forever. Interrupt the
        # socket at the overall deadline; never submit a replacement POST.
        expired.set()
        try:
            raw_socket = response.raw._fp.fp.raw._sock
            raw_socket.shutdown(socket.SHUT_RDWR)
        except (AttributeError, OSError):
            pass
        close = getattr(response, "close", None)
        if close:
            try:
                close()
            except Exception:
                pass

    timer = threading.Timer(max(.001, deadline - time.monotonic()), abort_read)
    timer.daemon = True
    timer.start()
    chunks, total = [], 0
    try:
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if expired.is_set() or time.monotonic() > deadline:
                raise ImageRequestError("等待图片超时；任务未自动重提，请先核对站点记录。")
            if not chunk:
                continue
            total += len(chunk)
            if total > limit:
                raise ImageRequestError("返回的数据超过本地大小限制。")
            chunks.append(chunk)
        if expired.is_set():
            raise ImageRequestError("等待图片超时；任务未自动重提，请先核对站点记录。")
        return b"".join(chunks)
    except Exception as error:
        if expired.is_set():
            raise ImageRequestError("等待图片超时；任务未自动重提，请先核对站点记录。") from error
        raise
    finally:
        timer.cancel()


def decode_image(data: bytes) -> Image.Image:
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageRequestError("图片超过 64MB 限制。")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > MAX_PIXELS or max(image.size) > 16384:
                raise ImageRequestError("图片尺寸超过本地处理限制。")
            image.load()
            return ImageOps.exif_transpose(image).convert("RGB")
    except ImageRequestError:
        raise
    except Exception as error:
        raise ImageRequestError("接口返回的内容不是可读取的图片。") from error


def encode_reference(image: Image.Image) -> bytes:
    if image.width * image.height > MAX_PIXELS:
        raise ValueError("参考图片超过 4000 万像素，请先缩小。")
    image = ImageOps.exif_transpose(image)
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    data = buffer.getvalue()
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("单张参考图片超过 64MB，请先缩小。")
    return data


def output_size(model: str, resolution: str, ratio: str) -> str:
    lower = model.lower()
    if "gemini" in lower or "grok" in lower or "nano-banana" in lower:
        return resolution
    left, right = (float(x) for x in ratio.split(":"))
    aspect = left / right
    long_edge = {"1K": 1024, "2K": 2048, "4K": 4096}[resolution]
    width, height = ((long_edge, long_edge / aspect) if aspect >= 1
                     else (long_edge * aspect, long_edge))
    if re.match(r"^gpt-image-(?:2(?:-|$)|2\.5)", lower):
        if not 1 / 3 <= aspect <= 3:
            raise ValueError("此模型支持的比例为 1:3 至 3:1。")
        pixels = max(655_360, min(8_294_400, width * height))
        target_width = min(3840, math.sqrt(pixels * aspect))
        target_height = target_width / aspect
        best, best_score = None, float("inf")
        for candidate_width in range(16, 3841, 16):
            exact_height = candidate_width / aspect / 16
            for candidate_height in {math.floor(exact_height) * 16, math.ceil(exact_height) * 16}:
                area = candidate_width * candidate_height
                if candidate_height <= 0 or candidate_height > 3840 or not 655_360 <= area <= 8_294_400:
                    continue
                ratio_error = abs(candidate_width / candidate_height / aspect - 1)
                if ratio_error > .015:
                    continue
                score = ((candidate_width - target_width) / target_width) ** 2 + (
                    (candidate_height - target_height) / target_height
                ) ** 2 + ratio_error ** 2 * 4
                if score < best_score:
                    best, best_score = (candidate_width, candidate_height), score
        if best is None:
            raise ValueError("此模型不支持当前尺寸和比例。")
        return f"{best[0]}x{best[1]}"
    return f"{max(16, round(width / 16) * 16)}x{max(16, round(height / 16) * 16)}"


def validate_references(model: str, references: list[bytes], resolution: str) -> None:
    limit = MAX_REFERENCES
    lower = model.lower()
    if "gemini-2.5" in lower and "image" in lower:
        limit = 3
    elif "gemini-3" in lower and "image" in lower:
        limit = 14
    if len(references) > limit:
        raise ValueError(f"此模型最多支持 {limit} 张参考图片。")
    if sum(map(len, references)) > MAX_INPUT_BYTES:
        raise ValueError("参考图片总量超过 64MB，请缩小图片或减少数量。")
    if "grok" in lower and resolution == "4K":
        raise ValueError("当前 Grok 通道最高支持 2K。")


def _error_payload(data: bytes, default: str) -> str:
    try:
        value = json.loads(data)
        error = value.get("error") or value.get("message") or default
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or default)
        return str(error)
    except Exception:
        return default


class ImageClient:
    def __init__(self, connection: Connection, session_factory=requests.Session):
        self.connection = connection
        self.session_factory = session_factory

    def models(self) -> list[dict]:
        connection = self.connection
        with self.session_factory() as session:
            with session.get(
                connection.base_url + "/v1/models",
                headers={"Authorization": "Bearer " + connection.api_key},
                timeout=(8, 20), stream=True, allow_redirects=False,
            ) as response:
                data = bounded_read(response, 2 * 1024 * 1024, time.monotonic() + 25)
                if response.status_code != 200:
                    raise ImageRequestError(
                        safe_message(_error_payload(data, "读取模型失败。"), connection.api_key),
                        response.status_code,
                    )
        try:
            payload = json.loads(data)
        except (ValueError, TypeError) as error:
            raise ImageRequestError("模型接口未返回有效 JSON。") from error
        candidates = payload.get("data", [])
        return [item for item in candidates if isinstance(item, dict) and
                re.search(r"image|imagen|flux|dall.?e|banana|grok-imagine", item.get("id", ""), re.I)]

    def generate_one(self, index: int, model: str, prompt: str, resolution: str, ratio: str,
                     quality: str, references: list[bytes], cancelled: Callable[[], bool],
                     save_image: Callable[[Image.Image, int, str], dict] | None) -> ItemResult:
        started = time.monotonic()
        result = ItemResult(index)
        connection = self.connection
        try:
            if cancelled():
                raise ImageRequestError("任务已取消，尚未提交。")
            common = {"model": model, "prompt": prompt, "n": 1,
                      "size": output_size(model, resolution, ratio),
                      "quality": quality, "response_format": "b64_json", "output_format": "png"}
            if any(name in model.lower() for name in ('grok', 'gemini', 'nano-banana', 'imagen')):
                common.pop('quality', None)
            if not re.match(r"^gpt-image-(?:2(?:-|$)|2\.5)", model, re.I):
                common["aspect_ratio"] = ratio
            task_id = str(uuid.uuid4())
            result.submission_id = task_id
            headers = {"Authorization": "Bearer " + connection.api_key, "Idempotency-Key": task_id}
            deadline = started + connection.timeout
            with self.session_factory() as session:
                kwargs = {"headers": headers, "timeout": (10, connection.timeout),
                          "stream": True, "allow_redirects": False}
                if references:
                    path = "/v1/images/edits"
                    kwargs["data"] = common
                    kwargs["files"] = [("image", (f"reference-{n + 1:02}.png", content, "image/png"))
                                       for n, content in enumerate(references)]
                else:
                    path = "/v1/images/generations"
                    kwargs["json"] = common
                # Never retry a generation POST: a timeout can follow upstream acceptance.
                with session.post(connection.base_url + path, **kwargs) as response:
                    result.status = response.status_code
                    result.request_id = response.headers.get("x-request-id", "")[:160]
                    result.retry_after = response.headers.get("Retry-After", "")[:80]
                    data = bounded_read(response, MAX_RESPONSE_BYTES, deadline)
                    if response.status_code != 200:
                        message = _error_payload(data, f"接口返回 HTTP {response.status_code}。")
                        if response.status_code == 429:
                            message = "通道限流，本项未自动重提。建议降低并发，稍后核对记录再重跑。" + (
                                f" 建议等待 {result.retry_after}。" if result.retry_after else "")
                        raise ImageRequestError(message, response.status_code,
                                                result.request_id, result.retry_after)
                try:
                    payload = json.loads(data)
                except (ValueError, TypeError) as error:
                    raise ImageRequestError("接口未返回有效 JSON；请先核对站点记录。") from error
                if not isinstance(payload, dict):
                    raise ImageRequestError("接口返回格式不正确。")
                if payload.get("error") or payload.get("success") is False:
                    raise ImageRequestError(_error_payload(data, "上游返回了错误。"), result.status)
                entries = payload.get("data") or []
                if not isinstance(entries, list) or not entries:
                    raise ImageRequestError("接口未返回图片；本项未自动重提。")
                first = entries[0]
                if not isinstance(first, dict):
                    raise ImageRequestError("接口返回的图片格式不正确。")
                if first.get("b64_json"):
                    encoded = first["b64_json"]
                    if len(encoded) > MAX_RESPONSE_BYTES:
                        raise ImageRequestError("图片编码超过本地大小限制。")
                    image_bytes = base64.b64decode(encoded, validate=True)
                elif first.get("url"):
                    media_url = urljoin(connection.base_url + "/", first["url"])
                    parts = urlsplit(media_url)
                    if parts.scheme not in ("http", "https") or parts.username or parts.password:
                        raise ImageRequestError("返回的图片链接无效。")
                    remaining = max(1, min(45, deadline - time.monotonic()))
                    # Do not forward a gateway key to a third-party storage URL.
                    if parts.scheme != "https" and parts.hostname not in ("127.0.0.1", "localhost", "::1"):
                        raise ImageRequestError("图片下载链接需要 HTTPS。")
                    same_origin = (parts.scheme, parts.netloc) == (
                        urlsplit(connection.base_url).scheme, urlsplit(connection.base_url).netloc
                    )
                    media_headers = {"Authorization": headers["Authorization"]} if same_origin else {}
                    with session.get(media_url, headers=media_headers,
                                     timeout=(8, remaining), stream=True,
                                     allow_redirects=False) as media:
                        if media.status_code != 200:
                            raise ImageRequestError(f"图片下载失败（HTTP {media.status_code}）。")
                        image_bytes = bounded_read(media, MAX_IMAGE_BYTES, deadline)
                else:
                    raise ImageRequestError("接口未返回图片数据或下载链接。")
            result.image = decode_image(image_bytes)
            if save_image:
                try:
                    result.file = save_image(result.image, index, result.request_id)
                except Exception:
                    result.error = "图片已生成，但自动保存失败；可连接保存图像节点保存本项。"
        except Exception as error:
            result.error = safe_message(str(error), connection.api_key)
            if isinstance(error, requests.Timeout):
                result.error = "等待接口超时；任务可能已被接收，请先核对站点记录，勿直接重复提交。"
            elif isinstance(error, requests.RequestException):
                result.error = "接口连接失败；本项未自动重提，请先核对站点记录。"
            if isinstance(error, ImageRequestError):
                result.status = error.status or result.status
                result.request_id = error.request_id or result.request_id
                result.retry_after = error.retry_after or result.retry_after
        result.elapsed = round(time.monotonic() - started, 2)
        return result

    def run(self, *, model: str, prompt: str, resolution: str, ratio: str, quality: str,
            batch: int, concurrency: int, references: list[bytes] | None = None,
            cancelled: Callable[[], bool] = lambda: False,
            save_image: Callable[[Image.Image, int, str], dict] | None = None,
            on_progress: Callable[[int, int], None] | None = None) -> list[ItemResult]:
        if not 1 <= batch <= MAX_BATCH or not 1 <= concurrency <= MAX_BATCH:
            raise ValueError("批量与并发必须在 1–16 之间。")
        if not model.strip() or not prompt.strip():
            raise ValueError("模型和提示词不能为空。")
        if len(prompt) > 100_000 or len(model) > 200:
            raise ValueError("提示词或模型名称过长。")
        if quality not in ("low", "medium", "high", "auto"):
            raise ValueError("质量参数不正确。")
        if resolution not in ("1K", "2K", "4K"):
            raise ValueError("分辨率参数不正确。")
        if not re.fullmatch(r'[1-9]\d{0,5}:[1-9]\d{0,5}', ratio):
            raise ValueError("比例参数不正确。")
        output_size(model, resolution, ratio)
        references = references or []
        validate_references(model, references, resolution)
        results = []
        with ThreadPoolExecutor(max_workers=min(batch, concurrency),
                                thread_name_prefix="bbapi-image") as pool:
            futures = [pool.submit(self.generate_one, index, model, prompt, resolution, ratio,
                                   quality, references, cancelled, save_image)
                       for index in range(batch)]
            for future in as_completed(futures):
                results.append(future.result())
                if on_progress:
                    on_progress(len(results), batch)
        return sorted(results, key=lambda item: item.index)
