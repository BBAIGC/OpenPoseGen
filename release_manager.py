"""Read-only update discovery and explicit, verified code-only update staging."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile
import time
import stat
from urllib.parse import urlsplit
from uuid import uuid4
from zipfile import ZipFile

from aiohttp import ClientSession, ClientTimeout

REPOSITORY = 'https://github.com/BBAIGC/OpenPoseGen'
API = 'https://api.github.com/repos/BBAIGC/OpenPoseGen'


def version_tuple(value):
    try:
        return tuple(int(part) for part in str(value).lstrip('vV').split('.')[:3])
    except ValueError:
        return (0,)


class ReleaseManager:
    def __init__(self, root, data_root, version):
        self.root, self.data_root, self.version = Path(root), Path(data_root), version
        self.latest = None
        self.checked_at = 0
        self.installing = False

    def about(self):
        latest = self.latest or {}
        return {'name': 'OpenPoseGen', 'display_name': '姿绘库', 'version': self.version,
                'repository': REPOSITORY, 'latest_version': latest.get('version', self.version),
                'update_available': version_tuple(latest.get('version')) > version_tuple(self.version),
                'release_url': latest.get('release_url', REPOSITORY + '/releases'),
                'asset_download_url': None,
                'contacts': [{'name': '微信交流', 'image': '/asset-library/api/public-assets/wechat-contact.png'},
                             {'name': '微信支持', 'image': '/asset-library/api/public-assets/wechat-support.jpg'},
                             {'name': '支付宝支持', 'image': '/asset-library/api/public-assets/alipay-support.jpg'}],
                'sources': [{'name': 'RuiC-PoseAtlas · 素材原作者', 'url': 'https://github.com/HRuiCcc/RuiC-PoseAtlas'},
                            {'name': 'Anhao · 中文资料整理', 'url': 'https://github.com/anhao/ai-pose-gallery-zh'}]}

    async def check(self):
        if self.latest and time.monotonic() - self.checked_at < 300:
            return self.about()
        async with ClientSession(timeout=ClientTimeout(total=18)) as session:
            async with session.get(API + '/releases/latest', headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'OpenPoseGen-Updater'}) as response:
                if response.status == 404:
                    return {**self.about(), 'message': '当前尚无可用的新发布版本。'}
                if response.status != 200:
                    raise RuntimeError('暂时无法读取更新信息，请稍后重试。')
                release = await response.json()
            descriptor = next((asset for asset in release.get('assets', []) if asset['name'] == 'release.json'), None)
            self.latest = {'version': str(release.get('tag_name', '')).lstrip('vV'), 'release_url': release['html_url'], 'assets': release.get('assets', [])}
            if descriptor:
                url = descriptor['browser_download_url']
                self._official(url)
                async with session.get(url) as response:
                    if response.status != 200:
                        raise RuntimeError('无法读取更新清单。')
                    if response.content_length and response.content_length > 1024 * 1024:
                        raise RuntimeError('更新清单过大。')
                    self.latest['manifest'] = json.loads((await response.content.read(1024 * 1024)).decode('utf-8-sig'))
        self.checked_at = time.monotonic()
        return self.about()

    @staticmethod
    def _official(url):
        value = urlsplit(url)
        if value.scheme != 'https' or value.hostname != 'github.com' or not value.path.startswith('/BBAIGC/OpenPoseGen/releases/download/'):
            raise ValueError('更新来源无效。')

    async def install(self):
        if self.installing:
            raise ValueError('更新正在处理，请稍候。')
        await self.check()
        if not self.about()['update_available']:
            return {**self.about(), 'updated': False, 'message': '当前已经是最新版本。'}
        latest = self.latest
        manifest = latest.get('manifest') or {}
        package = manifest.get('package') or manifest
        url = package.get('url') or package.get('download_url')
        digest = package.get('sha256')
        if not url:
            asset = next((item for item in latest.get('assets', []) if item['name'].endswith('.zip')), None)
            url = asset.get('browser_download_url') if asset else None
        if not isinstance(url, str) or not isinstance(digest, str) or len(digest) != 64:
            raise ValueError('该版本缺少完整校验清单，请从仓库手动安装。')
        self._official(url)
        self.installing = True
        try:
            update_root = self.data_root / '.updates' / uuid4().hex
            update_root.mkdir(parents=True, exist_ok=True)
            archive = update_root / 'update.zip'
            async with ClientSession(timeout=ClientTimeout(total=180)) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        raise RuntimeError('下载更新失败。')
                    size = 0
                    with archive.open('wb') as stream:
                        async for chunk in response.content.iter_chunked(131072):
                            size += len(chunk)
                            if size > 128 * 1024 * 1024:
                                raise ValueError('更新包过大。')
                            stream.write(chunk)
            def prepare():
                with archive.open('rb') as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != digest.lower():
                        raise ValueError('更新包校验失败，未替换任何代码。')
                with ZipFile(archive) as source:
                    members = source.infolist()
                    if len(members) > 12000 or sum(member.file_size for member in members) > 256 * 1024 * 1024:
                        raise ValueError('更新包解压规模无效。')
                    for member in members:
                        path = PurePosixPath(member.filename)
                        mode = member.external_attr >> 16
                        if path.is_absolute() or '..' in path.parts or ':' in member.filename or '\\' in member.filename or stat.S_ISLNK(mode) or member.file_size > 64 * 1024 * 1024:
                            raise ValueError('更新包路径无效。')
                    source.extractall(update_root / 'package')
                return update_root / 'package'
            directory = await asyncio.to_thread(prepare)
            installer = directory / 'install' / 'installer.py'
            if not installer.is_file():
                candidates = list(directory.glob('*/install/installer.py'))
                installer = candidates[0] if len(candidates) == 1 else installer
            if not installer.is_file():
                raise ValueError('更新包缺少安装入口，未替换现有代码。')
            # The installer is part of the user's configured repository release.
            # It performs its own hash/conflict checks and preserves runtime data.
            from runtime_paths import discover
            runtime = discover()
            process = await asyncio.create_subprocess_exec(sys.executable, str(installer), '--comfy-base', str(runtime['comfy_base']),
                    '--comfy-core', str(runtime['comfy_core']), '--python', runtime['python'], '--asset-root', str(runtime['asset_root']),
                    '--models', 'none', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            output, _ = await asyncio.wait_for(process.communicate(), timeout=120)
            if process.returncode:
                raise RuntimeError('自动安装未完成，请从仓库查看安装说明；原素材与配置已保留。')
            return {**self.about(), 'updated': True, 'restart_required': True, 'message': '代码已更新，请关闭资产库服务后重新打开；ComfyUI按需正常重启。'}
        finally:
            self.installing = False
