"""Loopback-only local settings endpoints. No credentials are returned to the UI."""
from __future__ import annotations

import asyncio
import ipaddress
from urllib.parse import urlsplit

from aiohttp import web
from server import PromptServer

from .client import ImageClient, safe_message
from .nodes import make_connection, profile_store


def _local_request(request) -> bool:
    try:
        if not ipaddress.ip_address(request.remote).is_loopback:
            return False
        origin = request.headers.get("Origin")
        if origin and urlsplit(origin).netloc != request.host:
            return False
        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            return False
        return True
    except (ValueError, TypeError):
        return False


async def _payload(request) -> dict:
    if not _local_request(request):
        raise web.HTTPForbidden(text="私有配置仅允许从本机 ComfyUI 窗口访问。")
    if request.content_type != "application/json":
        raise web.HTTPUnsupportedMediaType()
    if request.content_length and request.content_length > 16 * 1024:
        raise web.HTTPRequestEntityTooLarge(max_size=16 * 1024, actual_size=request.content_length)
    raw = await request.content.read(16 * 1024 + 1)
    if len(raw) > 16 * 1024:
        raise web.HTTPRequestEntityTooLarge(max_size=16 * 1024, actual_size=len(raw))
    import json
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("参数格式不正确。")
    return value


@PromptServer.instance.routes.get("/bbapi-private/profiles")
async def public_profiles(request):
    if not _local_request(request):
        raise web.HTTPForbidden()
    return web.json_response({"profiles": profile_store().public_profiles(), "version": "1.1.0"})


@PromptServer.instance.routes.post("/bbapi-private/profiles")
async def save_profile(request):
    try:
        data = await _payload(request)
        await asyncio.to_thread(
            profile_store().save, str(data.get("profile", "")),
            str(data.get("base_url", "")), str(data.get("api_key", "")),
            str(data.get("default_model", "")),
        )
        return web.json_response({"saved": True, "profile": data["profile"]})
    except web.HTTPException:
        raise
    except Exception:
        return web.json_response({"error": "配置保存失败，请检查名称、接口地址和 API Key。"}, status=400)


@PromptServer.instance.routes.post("/bbapi-private/models")
async def list_models(request):
    try:
        data = await _payload(request)
        connection = make_connection(str(data.get("profile", "")), str(data.get("base_url", "")))
        models = await asyncio.to_thread(ImageClient(connection).models)
        return web.json_response({"models": models})
    except web.HTTPException:
        raise
    except Exception as error:
        return web.json_response({"error": safe_message(str(error))}, status=400)
