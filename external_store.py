"""Bounded session-only reference files, separated from the permanent catalog."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import shutil
import threading
import time
from uuid import uuid4

from PIL import Image, ImageOps

KINDS = ('identity', 'outfit', 'pose')


class ExternalStore:
    def __init__(self, root):
        self.root = Path(root).resolve() / '.external-sessions'
        self.sessions = {}
        self._lock = threading.RLock()

    def directory(self, token):
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', token):
            raise ValueError('外接会话无效。')
        path = self.root / token
        for entry in (self.root, path):
            if entry.is_symlink() or (hasattr(entry, 'is_junction') and entry.is_junction()):
                raise ValueError('外接会话目录不能使用链接。')
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError('外接会话目录无效。')
        return path

    def session(self, token):
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', token):
            raise ValueError('外接会话无效，请重新打开资产库。')
        with self._lock:
            self.directory(token)
            self._expire()
            state = self.sessions.setdefault(token, {'items': [], 'touched': time.monotonic()})
            state['touched'] = time.monotonic()
            return state

    def _expire(self):
        expired = [key for key, value in self.sessions.items() if time.monotonic() - value['touched'] > 8 * 3600]
        for key in expired:
            self.sessions.pop(key, None)
            path = (self.root / key).resolve()
            if path.is_relative_to(self.root) and path.is_dir():
                shutil.rmtree(path)

    def public(self, item):
        value = {key: item[key] for key in ('id', 'title', 'kind', 'category', 'width', 'height', 'session')}
        from urllib.parse import urlencode
        query = urlencode({'session': item['session']})
        base = '/asset-library/api/external/' + item['id'] + '/media'
        return {**value, 'available': True, 'adult': False, 'tags': ['外接参考'], 'prompt': '', 'negative_prompt': '',
                'thumbnail_url': base + '/thumbnail?' + query, 'image_url': base + '/preview?' + query,
                'original_url': base + '/original?' + query, 'source_url': '', 'metadata': {'external': True}}

    def list(self, token):
        with self._lock:
            return {'session': token, 'items': [self.public(item) for item in self.session(token)['items']]}

    def get(self, token, identifier):
        with self._lock:
            item = next((item for item in self.session(token)['items'] if item['id'] == identifier), None)
            if not item:
                raise KeyError('外接参考已清空或不属于当前会话。')
            return dict(item)

    def resolve(self, identifier):
        with self._lock:
            for session in self.sessions.values():
                for item in session['items']:
                    if item['id'] == identifier:
                        return Path(item['path'])
        raise FileNotFoundError('外接参考已失效，请重新导入。')

    def add(self, token, source, title, kind='identity'):
        if kind not in KINDS:
            raise ValueError('参考类型无效。')
        state = self.session(token)
        source = Path(source)
        if source.stat().st_size > 64 * 1024 * 1024:
            raise ValueError('单张外接参考不能超过64MiB。')
        with Image.open(source) as image:
            if image.width * image.height > 80_000_000:
                raise ValueError('外接参考过大，请先缩小。')
            width, height = image.size
            suffix = {'PNG': '.png', 'JPEG': '.jpg', 'WEBP': '.webp'}.get(image.format)
            if suffix is None:
                raise ValueError('只支持 PNG、JPEG、WebP 图片。')
            image.verify()
        with source.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        with self._lock:
            duplicate = next((item for item in state['items'] if item['sha256'] == digest and item['kind'] == kind), None)
            if duplicate:
                return self.public(duplicate)
            if len(state['items']) >= 128:
                raise ValueError('当前会话最多保留128张外接参考。')
            directory = self.directory(token)
            directory.mkdir(parents=True, exist_ok=True)
            identifier = 'external-' + uuid4().hex
            target = directory / (identifier + suffix)
            shutil.copy2(source, target)
            item = {'id': identifier, 'title': Path(title).name[:160], 'kind': kind, 'category': '外接参考',
                    'path': str(target), 'width': width, 'height': height, 'session': token, 'sha256': digest}
            state['items'].append(item)
            return self.public(item)

    def move(self, token, identifier, kind, index=None):
        if kind not in KINDS:
            raise ValueError('参考类型无效。')
        if index is not None and (type(index) is not int or index < 0):
            raise ValueError('参考位置无效。')
        with self._lock:
            state = self.session(token)
            item = next((item for item in state['items'] if item['id'] == identifier), None)
            if item is None:
                raise KeyError('外接参考已失效。')
            item['kind'] = kind
            if index is not None:
                state['items'].remove(item)
                positions = [i for i, value in enumerate(state['items']) if value['kind'] == kind]
                where = positions[index] if index < len(positions) else (positions[-1] + 1 if positions else len(state['items']))
                state['items'].insert(where, item)
            return self.public(item)

    def remove(self, token, identifier):
        with self._lock:
            state = self.session(token)
            found = self.get(token, identifier)
            state['items'] = [item for item in state['items'] if item['id'] != identifier]
            Path(found['path']).unlink(missing_ok=True)
            for variant in ('thumbnail', 'preview'):
                Path(found['path']).with_suffix('.' + variant + '.webp').unlink(missing_ok=True)
        return {'removed': True}

    def media(self, token, identifier, variant):
        self.directory(token)
        item = self.get(token, identifier)
        source = Path(item['path'])
        if variant == 'original':
            return source
        edge = 320 if variant == 'thumbnail' else 2048
        target = source.with_suffix('.' + variant + '.webp')
        with self._lock:
            if not target.is_file():
                with Image.open(source) as image:
                    image.draft('RGB', (edge, edge))
                    image = ImageOps.exif_transpose(image)
                    image.thumbnail((edge, edge), Image.Resampling.LANCZOS)
                    image.convert('RGB').save(target, 'WEBP', quality=86)
        return target

    def close(self):
        with self._lock:
            for token in list(self.sessions):
                path = (self.root / token).resolve()
                if path.is_relative_to(self.root) and path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
            self.sessions.clear()
