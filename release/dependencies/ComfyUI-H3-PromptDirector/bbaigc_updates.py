"""Bounded release checks and opt-in workflow updates from fixed BBAIGC repositories."""
from __future__ import annotations
import asyncio
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid
from urllib.request import Request, urlopen
from aiohttp import web
import folder_paths
from .resource_control import _loopback

PRODUCTS = {
    'h3': ('小陈 MiniMax H3 多分镜工作台', 'ComfyUI-BBAIGC-MiniMax-H3'),
    'image': ('BBAIGC 生图工作台', 'ComfyUI-BBAIGC-Image-Studio'),
    'bbapi': ('BBAIGC BBAPI 生图工作台', 'ComfyUI-BBAIGC-BBAPI'),
}
_CACHE = {}
_LOCK = threading.Lock()

def _download(repo, path, limit=8 * 1024 * 1024):
    if not re.fullmatch(r'[A-Za-z0-9_./\-]+', path) or '..' in path.split('/'):
        raise ValueError('发布文件路径无效')
    request = Request(f'https://raw.githubusercontent.com/BBAIGC/{repo}/main/{path}', headers={'User-Agent': 'BBAIGC-ComfyUI'})
    with urlopen(request, timeout=10) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('发布文件超过大小限制')
    return data

def _manifest(identity, refresh=False):
    if identity not in PRODUCTS:
        raise ValueError('未知工作流')
    with _LOCK:
        cached = _CACHE.get(identity)
    if cached and not refresh and time.monotonic() - cached[0] < 900:
        return cached[1]
    value = json.loads(_download(PRODUCTS[identity][1], 'release.json', 65536))
    if value.get('id') != identity or not re.fullmatch(r'\d+\.\d+\.\d+', value.get('version', '')):
        raise ValueError('版本清单无效')
    with _LOCK:
        _CACHE[identity] = (time.monotonic(), value)
    return value

def _workflow_root():
    return Path(folder_paths.get_user_directory()) / 'default' / 'workflows' / 'BBAIGC'

def _workflow_target(identity):
    target = _workflow_root() / (identity + '.json')
    if identity != 'h3':
        return target
    root = _workflow_root().parent
    name = '小陈H3多分镜工作台_Codex.json'
    preferred = root / '麻瓜迪迪-MiniMaxH3' / '提示词优化与多分镜' / name
    if preferred.is_file():
        return preferred
    matches = list(root.rglob(name)) if root.is_dir() else []
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError('存在多个H3固定入口，请先明确要更新的工作流，未新增副本。')
    return target

def _installed():
    for path in (_registry_path(), Path(__file__).parent / 'installed_versions.json'):
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(value, dict):
                return value
        except (OSError, ValueError):
            pass
    return {}

def _registry_path():
    # Runtime state must not dirty a Git checkout and block its next update.
    return Path(folder_paths.get_user_directory()) / 'default' / 'bbaigc' / 'installed_versions.json'

def _local_workflow_version(identity):
    target = _workflow_target(identity)
    recorded = None
    if target.is_file():
        recorded = _installed().get(identity)
        candidates = [target]
    else:
        root = _workflow_root().parent
        pattern = {'h3': 'H3多分镜小陈工作台*.json', 'image': '生图工作台*.json',
                   'bbapi': 'BBAPI生图工作台*.json'}[identity]
        candidates = list(root.rglob(pattern)) if root.is_dir() else []
    versions = []
    for path in candidates:
        try:
            extra = json.loads(path.read_text(encoding='utf-8')).get('extra', {})
            version = extra.get('bbaigc_version') or extra.get('workflowVersion') or extra.get('codex_version')
            if _version(version) == (0, 0, 0):
                match = re.search(r'[Vv](\d+\.\d+\.\d+)', path.stem)
                version = match.group(1) if match else None
            if _version(version) != (0, 0, 0):
                versions.append(version)
        except (OSError, ValueError, AttributeError):
            pass
    return max(versions, key=_version) if versions else recorded if _version(recorded) != (0, 0, 0) else None

def _node_versions():
    result = {}
    try:
        source = (Path(__file__).parent / 'web' / 'version.js').read_text(encoding='utf-8')
        match = re.search(r'H3_STUDIO_VERSION\s*=\s*[\'"](\d+\.\d+\.\d+)', source)
        if match:
            result['core'] = match.group(1)
    except OSError:
        pass
    for directory in ('ComfyUI-BBAIGC-BBAPI', 'ComfyUI-BBAPI-Private'):
        try:
            source = (Path(__file__).resolve().parent.parent / directory / 'pyproject.toml').read_text(encoding='utf-8')
            match = re.search(r'^version\s*=\s*[\'"](\d+\.\d+\.\d+)', source, re.M)
            if match:
                result['bbapi'] = match.group(1)
                break
        except OSError:
            pass
    return result

def _version(value):
    return tuple(int(part) for part in value.split('.')) if isinstance(value, str) and re.fullmatch(r'\d+\.\d+\.\d+', value) else (0, 0, 0)

def check(identity):
    local_nodes = _node_versions()
    result = {'id': identity, 'name': PRODUCTS[identity][0], 'local_version': _local_workflow_version(identity),
              'local_node_versions': local_nodes}
    try:
        manifest = _manifest(identity)
        result.update(version=manifest['version'], notes=manifest.get('notes', ''), update_available=_version(manifest['version']) > _version(result['local_version']))
        wanted = manifest.get('node_versions', {})
        wanted = wanted if isinstance(wanted, dict) else {}
        result.update(node_versions=wanted, node_update_available=any(
            _version(version) > _version(local_nodes.get(name)) for name, version in wanted.items()))
    except Exception:
        result.update(error='暂时无法连接发布仓库，可稍后重试', update_available=False)
    return result

@contextmanager
def _idle_queue():
    from server import PromptServer
    queue = PromptServer.instance.prompt_queue
    with queue.mutex:
        if queue.get_tasks_remaining():
            raise ValueError('当前仍有运行或排队任务，请完成后再更新。')
        yield

def install(identity):
    manifest = _manifest(identity, refresh=True)
    repo = PRODUCTS[identity][1]
    payload = _download(repo, manifest['workflow'])
    if hashlib.sha256(payload).hexdigest() != manifest['sha256']:
        raise ValueError('工作流校验失败，未修改本地文件')
    workflow = json.loads(payload)
    if not isinstance(workflow.get('nodes'), list) or not isinstance(workflow.get('links'), list):
        raise ValueError('工作流结构无效')
    target = _workflow_target(identity)
    registry = _registry_path()
    token = uuid.uuid4().hex
    temporary = target.with_name(target.name + '.' + token + '.tmp')
    pending = registry.with_name(registry.name + '.' + token + '.tmp')
    # Downloading can take time. Recheck and hold the queue mutex only for local changes.
    with _idle_queue():
        target.parent.mkdir(parents=True, exist_ok=True)
        registry.parent.mkdir(parents=True, exist_ok=True)
        versions = _installed()
        versions[identity] = manifest['version']
        try:
            temporary.write_bytes(payload)
            pending.write_text(json.dumps(versions, ensure_ascii=False, indent=2), encoding='utf-8')
            if target.exists():
                backups = Path(folder_paths.get_user_directory()) / 'default' / 'bbaigc-backups'
                backups.mkdir(parents=True, exist_ok=True)
                (backups / f'{identity}-{time.time_ns()}.json').write_bytes(target.read_bytes())
            temporary.replace(target)
            pending.replace(registry)
        finally:
            temporary.unlink(missing_ok=True)
            pending.unlink(missing_ok=True)
    return {'message': '工作流已更新，原文件已备份。请从 BBAIGC 文件夹打开；节点升级请运行仓库安装脚本后重启。', 'version': manifest['version']}

def update_nodes(identity):
    if identity not in PRODUCTS:
        raise ValueError('未知工作流')
    custom = Path(__file__).resolve().parent.parent
    repos = [PRODUCTS['h3'][1]]
    if identity in ('image', 'bbapi'):
        repos.append(PRODUCTS['bbapi'][1])
    prepared = []
    for repo in repos:
        candidates = ([Path(__file__).resolve().parent, custom / repo] if repo == PRODUCTS['h3'][1]
                      else [custom / repo, custom / 'ComfyUI-BBAPI-Private'])
        def git(*args):
            return subprocess.run(['git', '-C', str(target), *args], check=True, capture_output=True, text=True,
                                  timeout=60, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout.strip()
        target = None
        has_git = False
        for candidate in dict.fromkeys(candidates):
            if not (candidate / '.git').exists():
                continue
            has_git = True
            target = candidate
            origin = git('remote', 'get-url', 'origin').rstrip('/').removesuffix('.git')
            if origin in (f'https://github.com/BBAIGC/{repo}', f'git@github.com:BBAIGC/{repo}'):
                break
            target = None
        if target is None:
            raise ValueError('节点仓库来源不匹配，未修改文件。' if has_git else
                             '节点为非 Git 安装，请先按仓库说明运行安装脚本；工作流仍可单独更新。')
        if git('status', '--porcelain', '--untracked-files=no'):
            raise ValueError('节点包含本地修改，请先保存或合并修改后再更新。')
        if git('branch', '--show-current') != 'main':
            raise ValueError('节点不在main分支，请先切回main后再更新。')
        # Fetch uses the fixed release URL, independent of local remote configuration.
        git('fetch', '--no-tags', f'https://github.com/BBAIGC/{repo}.git', 'main')
        prepared.append((repo, target))
    with _idle_queue():
        for repo, target in prepared:
            if git('status', '--porcelain', '--untracked-files=no'):
                raise ValueError('下载期间节点出现本地修改，未覆盖文件。')
        for repo, target in prepared:
            git('branch', f'bbaigc-backup-{time.time_ns()}', 'HEAD')
            git('merge', '--ff-only', 'FETCH_HEAD')
    return [repo for repo, _ in prepared]

def register_update_routes():
    try:
        from server import PromptServer
        routes = PromptServer.instance.routes
    except Exception:
        return

    @routes.get('/bbaigc/updates')
    async def updates(request):
        if not _loopback(request.remote):
            return web.json_response({'error': '仅支持本机操作'}, status=403)
        values = await asyncio.gather(*(asyncio.to_thread(check, identity) for identity in PRODUCTS))
        return web.json_response({'products': values})

    @routes.post('/bbaigc/update')
    async def update(request):
        if not _loopback(request.remote):
            return web.json_response({'error': '仅支持本机操作'}, status=403)
        origin = request.headers.get('Origin', '')
        if origin:
            from urllib.parse import urlsplit
            parsed = urlsplit(origin)
            if parsed.scheme not in ('http', 'https') or parsed.netloc != request.host:
                return web.json_response({'error': '来源不匹配'}, status=403)
        try:
            data = await request.json()
            async with _UPDATE_LOCK:
                from server import PromptServer
                if PromptServer.instance.prompt_queue.get_tasks_remaining():
                    raise ValueError('当前仍有运行或排队任务，请完成后再更新。')
                if data.get('include_nodes'):
                    if data.get('id') not in PRODUCTS:
                        raise ValueError('未知工作流')
                    await asyncio.to_thread(update_nodes, data['id'])
                value = await asyncio.to_thread(install, data.get('id'))
                if data.get('include_nodes'):
                    value['message'] = '节点与工作流已更新。请在方便时重启 ComfyUI，打开 BBAIGC 文件夹中的新版工作流。'
            return web.json_response(value)
        except Exception as error:
            return web.json_response({'error': str(error)}, status=400)

_UPDATE_LOCK = asyncio.Lock()
