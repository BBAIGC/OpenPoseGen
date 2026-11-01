"""Local image reveal and idle-only release for the image workbench."""
from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess
from urllib.parse import urlsplit

from aiohttp import web

from .resource_control import _loopback, _release_when_idle


def image_path(descriptor):
    import folder_paths
    if not isinstance(descriptor, dict) or descriptor.get('type') not in ('output', 'temp'):
        raise ValueError('只可定位本次输出或临时预览图片。')
    filename, subfolder = descriptor.get('filename'), descriptor.get('subfolder', '')
    if not isinstance(filename, str) or not filename or not isinstance(subfolder, str):
        raise ValueError('图片文件信息无效。')
    for value in (filename, subfolder):
        normalized = value.replace('\\', '/')
        if normalized.startswith('/') or ':' in normalized or '\x00' in normalized or '..' in normalized.split('/'):
            raise ValueError('图片必须位于当前输出目录。')
    if '/' in filename or '\\' in filename:
        raise ValueError('图片文件名不能包含目录。')
    root = Path(folder_paths.get_output_directory() if descriptor['type'] == 'output'
                else folder_paths.get_temp_directory()).resolve()
    path = (root / subfolder / filename).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp'):
        raise ValueError('图片文件不存在或不在输出目录中。')
    return path


def reveal_image(descriptor):
    path = image_path(descriptor)
    subprocess.Popen(['explorer.exe', '/select,', str(path)],
                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return {'ok': True, 'path': str(path)}


def register_result_routes():
    from server import PromptServer
    server = getattr(PromptServer, 'instance', None)
    if server is None or getattr(server, '_codex_studio_result_routes', False):
        return

    def local(request):
        origin = request.headers.get('Origin')
        return _loopback(request.remote) and (not origin or _loopback(urlsplit(origin).hostname))

    @server.routes.post('/studio-results/reveal')
    async def reveal(request):
        if not local(request):
            return web.json_response({'message': '仅允许本机打开图片位置。'}, status=403)
        try:
            body = await request.json()
            result = await asyncio.to_thread(reveal_image, body)
        except (ValueError, TypeError):
            return web.json_response({'message': '图片位置无效或文件已不存在。'}, status=400)
        except OSError:
            return web.json_response({'message': '无法打开资源管理器。'}, status=500)
        return web.json_response(result)

    @server.routes.post('/studio-results/release')
    async def release(request):
        if not local(request):
            return web.json_response({'message': '仅允许本机释放模型。'}, status=403)
        try:
            body = await request.json()
        except (ValueError, TypeError):
            return web.json_response({'message': '释放选项无效。'}, status=400)
        if not isinstance(body, dict) or set(body) - {'wait_for_idle', 'prompt_id'} or type(body.get('wait_for_idle', False)) is not bool:
            return web.json_response({'message': '释放选项无效。'}, status=400)
        attempts = 30 if body.get('wait_for_idle') else 1
        for attempt in range(attempts):
            status, result = await asyncio.to_thread(_release_when_idle, server, True)
            if status == 200:
                return web.json_response({**result, 'released': bool(result.get('ok'))})
            if attempt + 1 < attempts:
                await asyncio.sleep(1)
        return web.json_response({'ok': False, 'released': False, 'waiting': False,
                                  'message': '队列仍在运行，未卸载模型；空闲后可点立即释放。'},
                                 status=200 if body.get('wait_for_idle') else 409)

    server._codex_studio_result_routes = True
