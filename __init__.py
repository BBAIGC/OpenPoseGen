"""ComfyUI bootstrap for the separately bounded asset service."""
from pathlib import Path
import asyncio
import json
import os
import subprocess
import threading
from urllib.request import urlopen

from aiohttp import web
from server import PromptServer

__version__ = '1.2.1'
WEB_DIRECTORY = './comfy_web'
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
_LOCK = threading.Lock()
_ROOT = Path(__file__).resolve().parent


def _running():
    try:
        with urlopen('http://127.0.0.1:8191/asset-library/api/bootstrap', timeout=2) as response:
            return json.loads(response.read(2048)).get('service') == 'bbaigc-asset-library'
    except (OSError, ValueError):
        return False


def _launch():
    with _LOCK:
        if _running():
            return {'started': False, 'url': 'http://127.0.0.1:8191/asset-library/'}
        launcher = _ROOT / '启动资产库.ps1'
        powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        result = subprocess.run([str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(launcher), '-NoBrowser'],
                                capture_output=True, timeout=18, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode or not _running():
            raise RuntimeError('资产库未启动，请查看本机资产目录中的日志。')
        return {'started': True, 'url': 'http://127.0.0.1:8191/asset-library/'}


@PromptServer.instance.routes.post('/asset-library/start')
async def start_assets(request):
    origin = request.headers.get('Origin')
    if request.remote not in ('127.0.0.1', '::1') or origin not in (None, 'http://127.0.0.1:8000', 'http://localhost:8000', 'http://[::1]:8000'):
        return web.json_response({'error': '仅允许本机打开资产库。'}, status=403)
    try:
        return web.json_response(await asyncio.to_thread(_launch))
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        return web.json_response({'error': str(error)}, status=503)
