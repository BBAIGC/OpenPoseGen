"""Lazy disk index and bounded media preparation for the asset library."""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
from uuid import uuid4

from PIL import Image, ImageOps

KINDS = {'identity': '形象', 'outfit': '服装', 'pose': '姿态'}


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


class CatalogStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.catalog = self.root / 'catalog.json'
        self.user_catalog = self.root / 'user-assets.json'
        self.cache = self.root / '.asset-cache'
        self.db = self.cache / 'catalog.sqlite3'
        self._lock = threading.Lock()
        self._stamp = None
        self._checked = 0
        self._source = {}
        self._last_prune = 0
        self._prune_lock = threading.Lock()
        self._media_locks = [threading.Lock() for _ in range(16)]

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.db, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def ensure(self):
        # Catalog indexing only happens on the first actual library request.
        if time.monotonic() - self._checked < 5 and self.db.exists():
            return
        with self._lock:
            self.cache.mkdir(parents=True, exist_ok=True)
            stamp = (self.catalog.stat().st_mtime_ns, self.catalog.stat().st_size) if self.catalog.is_file() else (0, 0)
            stamp += (self.user_catalog.stat().st_mtime_ns, self.user_catalog.stat().st_size) if self.user_catalog.is_file() else (0, 0)
            from taxonomy import VERSION as TAXONOMY_VERSION, category_path
            stamp += (TAXONOMY_VERSION,)
            if self._stamp == stamp and self.db.exists():
                self._checked = time.monotonic()
                return
            with self._connect() as connection:
                connection.execute('CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, kind TEXT, category TEXT, title TEXT, search TEXT, payload TEXT)')
                connection.execute('CREATE INDEX IF NOT EXISTS asset_kind_category ON assets(kind, category, id)')
                connection.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)')
                connection.execute('CREATE TABLE IF NOT EXISTS categories (key TEXT PRIMARY KEY, kind TEXT, title TEXT, parent TEXT, level INTEGER, adult INTEGER)')
                connection.execute('CREATE TABLE IF NOT EXISTS asset_categories (asset_id TEXT, category_key TEXT, PRIMARY KEY(asset_id,category_key))')
                connection.execute('CREATE INDEX IF NOT EXISTS category_members ON asset_categories(category_key,asset_id)')
                saved = connection.execute("SELECT value FROM state WHERE key='catalog_stamp'").fetchone()
                if saved and json.loads(saved[0]) == list(stamp):
                    source = connection.execute("SELECT value FROM state WHERE key='source'").fetchone()
                    self._source = json.loads(source[0]) if source else {}
                else:
                    data = json.loads(self.catalog.read_text(encoding='utf-8-sig')) if stamp[0] else {'items': [], 'source': {}}
                    items = data.get('items', []) if isinstance(data, dict) else data
                    if self.user_catalog.is_file():
                        items.extend(json.loads(self.user_catalog.read_text(encoding='utf-8')))
                    connection.execute('DELETE FROM assets')
                    connection.execute('DELETE FROM categories')
                    connection.execute('DELETE FROM asset_categories')
                    for item in items:
                        if not isinstance(item, dict) or item.get('kind') not in KINDS or not item.get('id'):
                            continue
                        item['id'] = str(item['id'])
                        category = str(item.get('category') or '未分类')
                        title = str(item.get('title') or item['id'])
                        search = ' '.join([title, category, str(item.get('prompt', '')), ' '.join(map(str, item.get('tags', [])))])
                        connection.execute('INSERT OR REPLACE INTO assets VALUES(?,?,?,?,?,?)',
                                           (item['id'], item['kind'], category, title, search.casefold(), json.dumps(item, ensure_ascii=False)))
                        parent = None
                        for level, node in enumerate(category_path(item), 1):
                            connection.execute('INSERT OR REPLACE INTO categories VALUES(?,?,?,?,?,?)',
                                               (node['key'], item['kind'], node['title'], parent, level, int(node.get('adult', False))))
                            connection.execute('INSERT OR IGNORE INTO asset_categories VALUES(?,?)', (item['id'], node['key']))
                            parent = node['key']
                    self._source = data.get('source', {}) if isinstance(data, dict) else {}
                    connection.execute("INSERT OR REPLACE INTO state VALUES('catalog_stamp', ?)", (json.dumps(stamp),))
                    connection.execute("INSERT OR REPLACE INTO state VALUES('source', ?)", (json.dumps(self._source, ensure_ascii=False),))
            self._stamp, self._checked = stamp, time.monotonic()

    def path(self, value):
        if not isinstance(value, str) or not value or '\x00' in value:
            raise ValueError('素材路径无效。')
        relative = Path(value)
        if relative.anchor or '..' in relative.parts:
            raise ValueError('素材路径必须位于本地素材目录。')
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('素材路径越界。')
        return path

    def item(self, identifier):
        self.ensure()
        with self._connect() as connection:
            row = connection.execute('SELECT payload FROM assets WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise KeyError('素材不存在或目录仍在准备。')
        return json.loads(row[0])

    def original(self, identifier):
        path = self.path(self.item(identifier).get('image'))
        if not path.is_file():
            raise FileNotFoundError('这张素材的原图尚未下载完成，请稍后重试。')
        return path

    def add_user_asset(self, identifier, path, title='生成结果', prompt='', kind='identity'):
        if kind not in KINDS:
            raise ValueError('素材类型无效。')
        with Image.open(path) as image:
            width, height = image.size
            image.verify()
        item = {'id': identifier, 'title': str(title)[:100], 'kind': kind, 'category': '生成结果',
                'tags': ['生成结果'], 'prompt': str(prompt)[:20000], 'negative_prompt': '',
                'image': path.relative_to(self.root).as_posix(), 'thumbnail': path.relative_to(self.root).as_posix(),
                'source_url': '', 'metadata': {'width': width, 'height': height, 'prompt_source': 'user_generation'}}
        with self._lock:
            items = json.loads(self.user_catalog.read_text(encoding='utf-8')) if self.user_catalog.is_file() else []
            items = [entry for entry in items if entry.get('id') != identifier]
            items.append(item)
            atomic_json(self.user_catalog, items)
            self._checked = 0
        return self.public(item)

    def public(self, item, detail=True):
        value = dict(item)
        identifier = value['id']
        from urllib.parse import quote
        prefix = '/asset-library/api/media/' + quote(identifier, safe='')
        original = self.path(value['image']) if value.get('image') else None
        thumb = self.path(value.get('thumbnail') or value.get('image'))
        value['available'] = bool(original and original.is_file())
        def version(path):
            try:
                return str(path.stat().st_mtime_ns)
            except (OSError, AttributeError):
                return 'pending'
        value['thumbnail_url'] = prefix + '/thumbnail?v=' + version(thumb)
        value['image_url'] = prefix + '/preview?v=' + version(original if value['available'] else thumb)
        value['original_url'] = prefix + '/original'
        metadata = value.get('metadata') or {}
        value['adult'] = value.get('kind') == 'pose' and metadata.get('series') == 'duo'
        value['width'] = value.get('width', metadata.get('width', 0))
        value['height'] = value.get('height', metadata.get('height', 0))
        if not detail:
            value['metadata'] = {key: metadata[key] for key in ('width', 'height', 'angle', 'series', 'prompt_source', 'license') if key in metadata}
        value.pop('image', None)
        value.pop('thumbnail', None)
        return value

    def page(self, kind='', category='', query='', offset=0, limit=48):
        self.ensure()
        offset, limit = max(0, int(offset)), max(1, min(60, int(limit)))
        conditions, values = [], []
        if kind:
            if kind not in KINDS:
                raise ValueError('素材类型无效。')
            conditions.append('kind=?'); values.append(kind)
        if category:
            conditions.append('(category=? OR id IN (SELECT asset_id FROM asset_categories WHERE category_key=?))'); values.extend((category, category))
        elif kind == 'pose':
            conditions.append("id NOT IN (SELECT asset_id FROM asset_categories WHERE category_key='pose/adult')")
        if query:
            conditions.append("search LIKE ? ESCAPE '\\'")
            values.append('%' + query[:200].casefold().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
        where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
        with self._connect() as connection:
            total = connection.execute('SELECT COUNT(*) FROM assets' + where, values).fetchone()[0]
            rows = connection.execute("SELECT payload FROM assets" + where + " ORDER BY (id LIKE 'result-%'),id LIMIT ? OFFSET ?", [*values, limit, offset]).fetchall()
        return {'items': [self.public(json.loads(row[0]), detail=False) for row in rows], 'total': total,
                'next_offset': offset + len(rows) if offset + len(rows) < total else None}

    def position(self, identifier, kind='', category='', query=''):
        self.ensure()
        conditions, values = [], []
        if kind:
            if kind not in KINDS:
                raise ValueError('素材类型无效。')
            conditions.append('kind=?'); values.append(kind)
        if category:
            conditions.append('(category=? OR id IN (SELECT asset_id FROM asset_categories WHERE category_key=?))'); values.extend((category, category))
        elif kind == 'pose':
            conditions.append("id NOT IN (SELECT asset_id FROM asset_categories WHERE category_key='pose/adult')")
        if query:
            conditions.append("search LIKE ? ESCAPE '\\'")
            values.append('%' + query[:200].casefold().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
        where = ' WHERE ' + ' AND '.join(conditions) if conditions else ' WHERE 1=1'
        with self._connect() as connection:
            found = connection.execute('SELECT id FROM assets' + where + ' AND id=?', [*values, identifier]).fetchone()
            if not found:
                raise KeyError('当前分类中没有这张素材。')
            generated = int(identifier.startswith('result-'))
            offset = connection.execute("SELECT COUNT(*) FROM assets" + where + " AND ((id LIKE 'result-%')<? OR ((id LIKE 'result-%')=? AND id<?))", [*values, generated, generated, identifier]).fetchone()[0]
            total = connection.execute('SELECT COUNT(*) FROM assets' + where, values).fetchone()[0]
        return {'offset': offset, 'total': total, 'id': identifier}

    def summary(self):
        self.ensure()
        with self._connect() as connection:
            kind_rows = connection.execute('SELECT kind,COUNT(*) AS count FROM assets GROUP BY kind').fetchall()
            rows = connection.execute('SELECT c.*,COUNT(ac.asset_id) AS count FROM categories c JOIN asset_categories ac ON ac.category_key=c.key GROUP BY c.key ORDER BY c.kind,c.level,c.title').fetchall()
        counts = Counter({key: 0 for key in KINDS})
        categories = []
        for row in kind_rows:
            counts[row['kind']] = row['count']
        for row in rows:
            categories.append({'kind': row['kind'], 'key': row['key'], 'title': row['title'], 'count': row['count'],
                               'parent': row['parent'], 'level': row['level'], 'adult': bool(row['adult'])})
        counts['total'] = sum(counts.values())
        adult_count = next((node['count'] for node in categories if node['key'] == 'pose/adult'), 0)
        counts['pose_regular'] = counts['pose'] - adult_count
        source = dict(self._source)
        progress = self.root / 'download-progress.json'
        if progress.is_file():
            try:
                source.update(json.loads(progress.read_text(encoding='utf-8')))
            except (ValueError, OSError):
                pass
        return {'version': '1.2.0', 'counts': dict(counts), 'categories': categories,
                'source': source, 'presets': self.presets()}

    def presets(self, items=None):
        from prompt_presets import preset_items
        path = self.root / 'user-presets.json'
        if items is not None:
            if not isinstance(items, list) or len(items) > 30:
                raise ValueError('最多保存 30 个提示词预设。')
            valid = []
            for item in items:
                if not isinstance(item, dict) or not all(isinstance(item.get(key), str) for key in ('id', 'name', 'prompt')):
                    raise ValueError('预设格式无效。')
                valid.append({'id': item['id'][:80], 'name': item['name'][:60], 'prompt': item['prompt'][:16000]})
                variants = item.get('variants')
                if isinstance(variants, list) and len(variants) == 5 and all(isinstance(value, str) for value in variants):
                    valid[-1]['variants'] = [value[:16000] for value in variants]
                for key in ('scene', 'version'):
                    if isinstance(item.get(key), str):
                        valid[-1][key] = item[key][:100]
            with self._lock:
                atomic_json(path, valid)
            return preset_items(valid)
        try:
            return preset_items(json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None)
        except (ValueError, OSError):
            return preset_items()

    def media(self, identifier, variant):
        item = self.item(identifier)
        original = self.path(item['image'])
        thumb = self.path(item.get('thumbnail') or item['image'])
        if variant == 'original':
            return self.original(identifier)
        source = thumb if variant == 'thumbnail' and thumb.is_file() else original
        if not source.is_file():
            source = thumb
        if not source.is_file():
            raise FileNotFoundError('缩略图仍在下载，请稍后刷新。')
        size = 320 if variant == 'thumbnail' else 2048
        stamp = source.stat()
        key = hashlib.sha256(f'{source}:{stamp.st_mtime_ns}:{stamp.st_size}:{size}'.encode()).hexdigest()
        target = self.cache / 'media' / (key + '.webp')
        with self._media_locks[int(key[:2], 16) % len(self._media_locks)]:
            return self._prepare_media(source, target, size)

    def _prepare_media(self, source, target, size):
        if target.is_file():
            target.touch()
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.stem + '-' + uuid4().hex + '.tmp')
        try:
            with Image.open(source) as image:
                image.draft('RGB', (size, size))
                image = ImageOps.exif_transpose(image)
                image.thumbnail((size, size), Image.Resampling.LANCZOS)
                image.convert('RGB').save(temporary, 'WEBP', quality=83, method=3)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        self.prune()
        return target

    def prune(self):
        if time.monotonic() - self._last_prune < 60 or not self._prune_lock.acquire(blocking=False):
            return
        try:
            self._last_prune = time.monotonic()
            files = sorted([*(self.cache / 'media').glob('*.webp'), *(self.cache / 'results').glob('*')], key=lambda p: p.stat().st_mtime)
            files = [path for path in files if path.is_file() and path.suffix != '.tmp']
            total = sum(path.stat().st_size for path in files)
            while files and (len(files) > 4096 or total > 512 * 1024 * 1024):
                path = files.pop(0)
                total -= path.stat().st_size
                try:
                    path.unlink(missing_ok=True)
                except PermissionError:
                    # A FileResponse may still own the file on Windows.
                    continue
        finally:
            self._prune_lock.release()
