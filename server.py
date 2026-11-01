"""Standalone local asset service; ComfyUI is contacted only for user actions."""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import mimetypes
import os
from pathlib import Path
import secrets
import shutil
import sys
from uuid import uuid4
from urllib.parse import urlsplit, urlencode

from aiohttp import ClientSession, ClientTimeout, FormData, web

from catalog_store import CatalogStore, atomic_json
from external_store import ExternalStore
from runtime_paths import discover

VERSION = '1.2.0'
ROOT = Path(__file__).resolve().parent
RUNTIME = discover()
COMFY = RUNTIME['comfy_url']
ORIGINS = {f'http://{host}:{port}' for host in ('127.0.0.1', 'localhost', '[::1]') for port in (8000, 8191)}
ORIGINS.add(COMFY)


def create_app(asset_root, port=8191):
    token = secrets.token_urlsafe(32)
    store = CatalogStore(asset_root)
    external = ExternalStore(asset_root)
    from provider import ProviderService
    provider = ProviderService(Path(asset_root))
    from release_manager import ReleaseManager
    releases = ReleaseManager(ROOT, Path(asset_root), VERSION)
    media_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='asset-media')
    media_slots = asyncio.Semaphore(2)
    media_pending = set()
    imports = asyncio.Lock()
    settings_lock = asyncio.Lock()
    result_slots = asyncio.Semaphore(2)
    generations = None
    languages = None

    @web.middleware
    async def guard(request, handler):
        if request.remote not in ('127.0.0.1', '::1') or request.host not in {f'{host}:{port}' for host in ('127.0.0.1', 'localhost', '[::1]')}:
            return web.json_response({'error': '仅允许本机访问资产库。'}, status=403)
        origin = request.headers.get('Origin')
        if origin and origin not in ORIGINS:
            return web.json_response({'error': '请求来源无效。'}, status=403)
        if request.method == 'OPTIONS':
            response = web.Response(status=204)
        elif request.method not in ('GET', 'HEAD') and not secrets.compare_digest(request.headers.get('X-Asset-Token', ''), token):
            response = web.json_response({'error': '资产库连接已过期，请刷新后重试。'}, status=401)
        else:
            try:
                response = await handler(request)
            except KeyError as error:
                response = web.json_response({'error': str(error).strip("'")}, status=404)
            except FileNotFoundError as error:
                response = web.json_response({'error': str(error)}, status=409)
            except (ValueError, TypeError, json.JSONDecodeError) as error:
                response = web.json_response({'error': str(error)}, status=400)
            except asyncio.TimeoutError:
                response = web.json_response({'error': 'ComfyUI 响应超时，请检查运行状态后重试。'}, status=504)
            except OSError as error:
                response = web.json_response({'error': f'本地文件或连接不可用：{error}'}, status=503)
            except RuntimeError as error:
                response = web.json_response({'error': str(error)}, status=409)
        if origin in ORIGINS:
            response.headers['Access-Control-Allow-Origin'] = origin
            response.headers['Vary'] = 'Origin'
            response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Asset-Token'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        if not request.path.startswith('/asset-library/api/media/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    app = web.Application(middlewares=[guard], client_max_size=24 * 1024 * 1024)
    routes = web.RouteTableDef()

    async def body(request):
        value = await request.json()
        if not isinstance(value, dict):
            raise ValueError('请求内容必须为对象。')
        return value

    def generation():
        nonlocal generations
        if generations is None:
            from generation import GenerationService
            def reference_path(identifier):
                return external.resolve(identifier) if identifier.startswith('external-') else store.original(identifier)
            generations = GenerationService(resolver=reference_path, base_url=COMFY, data_dir=store.root, on_completed=completed_folder, provider=provider)
        return generations

    def language():
        nonlocal languages
        if languages is None:
            from language_service import LanguageService
            languages = LanguageService(provider=provider)
        return languages

    async def completed_folder(job):
        if not job.get('images'):
            return
        descriptor = image_descriptor(job['images'][0])
        async with ClientSession(timeout=ClientTimeout(total=15)) as session:
            async with session.post(COMFY + '/studio-results/reveal', json=descriptor) as response:
                result = await response.json()
                if response.status != 200:
                    raise RuntimeError(result.get('message') or result.get('error') or '无法打开生成结果位置。')

    def image_descriptor(image):
        if not isinstance(image, dict) or image.get('type') not in ('output', 'temp'):
            raise ValueError('仅支持当前 ComfyUI 生成结果。')
        filename, subfolder = image.get('filename'), image.get('subfolder', '')
        if not isinstance(filename, str) or not isinstance(subfolder, str) or not filename or '/' in filename or '\\' in filename:
            raise ValueError('生成图片信息无效。')
        for part in (filename, subfolder):
            if '..' in Path(part).parts or Path(part).anchor or ':' in part or '\x00' in part:
                raise ValueError('生成图片路径无效。')
        if Path(filename).suffix.lower() not in ('.png', '.webp', '.jpg', '.jpeg'):
            raise ValueError('暂不支持这类生成文件。')
        return {'filename': filename, 'subfolder': subfolder, 'type': image['type']}

    def public_image(image):
        descriptor = image_descriptor(image)
        url = '/asset-library/api/result-image?' + urlencode(descriptor)
        metadata = image.get('metadata') or {}
        return {**image, **descriptor, 'url': url, 'preview_url': url + '&size=2048', 'thumbnail_url': url + '&size=320',
                **{key: image.get(key) or metadata.get(key) for key in ('width', 'height') if image.get(key) or metadata.get(key)}}

    def public_job(result):
        value = dict(result)
        value['images'] = [public_image(image) for image in result.get('images', [])]
        for image in value['images']:
            for key in ('width', 'height'):
                if key not in image and result.get('settings', {}).get(key):
                    image[key] = result['settings'][key]
        return value

    def read_settings():
        defaults = {'save_output': True, 'auto_open_folder': False, 'resolution': '2K', 'ratio': '3:4', 'detail_level': 3, 'background': 'auto',
                    'model_overrides': {}, 'lora_slots': [{'name': '', 'strength': 1.0, 'enabled': False} for _ in range(5)],
                    'seed_mode': 'random', 'seed': 42,
                    'language_config': {'provider': 'local', 'model': '', 'detail_level': 3, 'temperature': 0.3, 'top_p': 0.9, 'max_tokens': 2048, 'num_ctx': 8192}}
        path = store.root / 'settings.json'
        try:
            value = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
            defaults.update({key: item for key, item in value.items() if key in defaults})
        except (OSError, ValueError):
            pass
        return defaults

    async def generation_body(request):
        value = await body(request)
        value['original_prompt'] = str(value.get('prompt', ''))
        defaults = await asyncio.to_thread(read_settings)
        for key, item in defaults.items():
            value.setdefault(key, item)
        # Keep role constraints alongside the prompt, while respecting the user's text.
        from prompt_presets import build_role_guidance
        if not isinstance(value.get('asset_ids', []), list) or len(value.get('asset_ids', [])) > 16 or any(not isinstance(item, str) for item in value.get('asset_ids', [])):
            raise ValueError('素材引用列表无效，最多使用16张。')
        session = value.get('external_session', '')
        items = [await asyncio.to_thread(external.get, session, identifier) if identifier.startswith('external-')
                 else await asyncio.to_thread(store.item, identifier) for identifier in value.get('asset_ids', [])]
        guidance = build_role_guidance(value, items)
        if guidance:
            value['prompt'] = str(value.get('prompt', '')).rstrip() + '\n' + guidance
        if value.get('background') == 'preserve':
            value['prompt'] += '\n保留形象参考图的环境背景，仅根据目标人物位置统一重打光。'
        return value

    @routes.get('/asset-library/api/bootstrap')
    async def bootstrap(request):
        return web.json_response({'token': token, 'version': VERSION, 'service': 'bbaigc-asset-library'})

    @routes.get('/asset-library/api/about')
    async def about(request):
        return web.json_response(releases.about())

    @routes.get('/asset-library/api/public-assets/{name}')
    async def public_asset(request):
        name = request.match_info['name']
        if name not in ('wechat-contact.png', 'wechat-support.jpg', 'alipay-support.jpg'):
            raise web.HTTPNotFound()
        path = ROOT / 'docs' / 'assets' / name
        if not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={'Cache-Control': 'public, max-age=86400'})

    @routes.get('/asset-library/api/updates/check')
    async def update_check(request):
        return web.json_response(await releases.check())

    @routes.post('/asset-library/api/updates/install')
    async def update_install(request):
        return web.json_response(await releases.install())

    @routes.get('/asset-library/api/summary')
    async def summary(request):
        return web.json_response(await asyncio.to_thread(store.summary))

    @routes.get('/asset-library/api/assets')
    async def assets(request):
        query = request.query
        result = await asyncio.to_thread(store.page, query.get('kind', ''), query.get('category', ''), query.get('q', ''), query.get('offset', 0), query.get('limit', 48))
        return web.json_response(result)

    @routes.get('/asset-library/api/assets/{identifier}')
    async def asset(request):
        identifier = request.match_info['identifier']
        if identifier.startswith('external-'):
            item = await asyncio.to_thread(external.get, request.query.get('session', ''), identifier)
            return web.json_response(external.public(item))
        item = await asyncio.to_thread(store.item, identifier)
        return web.json_response(store.public(item))

    @routes.get('/asset-library/api/assets/{identifier}/position')
    async def asset_position(request):
        query = request.query
        result = await asyncio.to_thread(store.position, request.match_info['identifier'], query.get('kind', ''), query.get('category', ''), query.get('q', ''))
        return web.json_response(result)

    @routes.get('/asset-library/api/media/{identifier}/{variant:thumbnail|preview|original}')
    async def media(request):
        key = (request.match_info['identifier'], request.match_info['variant'])
        if len(media_pending) >= 48:
            return web.json_response({'error': '图片正在准备，请稍后重试。'}, status=429)
        task = asyncio.current_task()
        media_pending.add(task)
        try:
            async with media_slots:
                path = await asyncio.get_running_loop().run_in_executor(media_pool, store.media, *key)
            return web.FileResponse(path, headers={'Cache-Control': 'private, max-age=86400', 'Content-Type': mimetypes.guess_type(path.name)[0] or ('image/webp' if path.suffix == '.webp' else 'application/octet-stream')})
        finally:
            media_pending.discard(task)

    @routes.get('/asset-library/api/presets')
    async def presets(request):
        return web.json_response({'items': await asyncio.to_thread(store.presets)})

    @routes.put('/asset-library/api/presets')
    async def save_presets(request):
        return web.json_response({'items': await asyncio.to_thread(store.presets, (await body(request)).get('items'))})

    @routes.get('/asset-library/api/engines')
    async def engines(request):
        return web.json_response(await generation().engines())

    @routes.post('/asset-library/api/generate')
    async def generate(request):
        return web.json_response(await generation().submit(await generation_body(request)))

    @routes.post('/asset-library/api/workflow')
    async def workflow(request):
        return web.json_response(await generation().build(await generation_body(request)))

    @routes.get('/asset-library/api/dimensions')
    async def dimension_options(request):
        from generation import dimensions
        ratio = request.query.get('ratio', '3:4')
        return web.json_response({'ratio': ratio, 'options': [{'id': size, **dimensions(size, ratio)} for size in ('1K', '2K', '4K')]})

    @routes.get('/asset-library/api/settings')
    async def settings(request):
        return web.json_response(await asyncio.to_thread(read_settings))

    @routes.put('/asset-library/api/settings')
    async def settings_update(request):
        value = await body(request)
        from generation import dimensions
        async with settings_lock:
            data = await asyncio.to_thread(read_settings)
            for key in data:
                if key in value:
                    data[key] = value[key]
            if type(data['save_output']) is not bool or type(data['auto_open_folder']) is not bool or type(data['detail_level']) is not int or not 1 <= data['detail_level'] <= 5:
                raise ValueError('保存或提示词设置无效。')
            if not data['save_output']:
                data['auto_open_folder'] = False
            if data['background'] not in ('auto', 'preserve'):
                raise ValueError('背景选项无效。')
            slots = data['lora_slots']
            if not isinstance(slots, list) or len(slots) > 20:
                raise ValueError('最多配置20个LoRA槽位。')
            for slot in slots:
                if not isinstance(slot, dict) or not isinstance(slot.get('name'), str) or len(slot['name']) > 500 or type(slot.get('enabled')) is not bool or type(slot.get('strength')) not in (int, float) or not -2 <= slot['strength'] <= 2:
                    raise ValueError('LoRA设置无效。')
            overrides = data['model_overrides']
            if not isinstance(overrides, dict) or len(overrides) > 64:
                raise ValueError('模型采样设置无效。')
            for key, override in overrides.items():
                if not isinstance(key, str) or not isinstance(override, dict) or type(override.get('steps')) is not int or not 1 <= override['steps'] <= 100 or type(override.get('cfg')) not in (int, float) or not 0 <= override['cfg'] <= 20:
                    raise ValueError('步数或CFG设置无效。')
            if data['seed_mode'] not in ('random', 'fixed') or type(data['seed']) is not int or not 0 <= data['seed'] <= 0xffffffffffffffff:
                raise ValueError('随机种子设置无效。')
            if not isinstance(data['language_config'], dict):
                raise ValueError('语言模型设置无效。')
            from language_service import normalize as validate_language
            language_config = data['language_config']
            allowed_language = {'provider', 'model', 'detail_level', 'temperature', 'top_p', 'max_tokens', 'num_ctx'}
            if set(language_config) - allowed_language:
                raise ValueError('语言模型设置包含未知字段。')
            validated = validate_language({'prompt': '设置验证', **language_config, 'engine': language_config.get('provider', 'local')})
            data['language_config'] = {key: validated[key] for key in allowed_language if key != 'provider'}
            data['language_config']['provider'] = validated['engine']
            dimensions(data['resolution'], data['ratio'])
            await asyncio.to_thread(atomic_json, store.root / 'settings.json', data)
        return web.json_response(data)

    @routes.get('/asset-library/api/provider')
    async def provider_config(request):
        data = await asyncio.to_thread(provider.public_config)
        if urlsplit(data.get('base_url', '')).hostname == 'bbapi.ainivox.cn':
            data['promotion'] = {'models': ['gpt-image-2.5-flare', 'gpt-image-2.5-sunburst'],
                                 'text': 'GPT Image 2.5 · 活动参考价 ¥0.025/张',
                                 'note': '按所选分组实际计费', 'url': 'https://bbapi.ainivox.cn/creative'}
        return web.json_response(data)

    @routes.put('/asset-library/api/provider')
    async def provider_save(request):
        return web.json_response(await provider.save(await body(request)))

    @routes.post('/asset-library/api/provider/test')
    async def provider_test(request):
        data = await provider.test()
        return web.json_response({**data, 'ok': True, 'message': f"连接成功，已读取{len(data.get('models', []))}个图像模型"})

    @routes.get('/asset-library/api/model-config')
    async def model_config(request):
        return web.json_response(await generation().model_config())

    @routes.get('/asset-library/api/language/models')
    async def language_models(request):
        return web.json_response(await language().models())

    @routes.post('/asset-library/api/language/optimize')
    async def language_submit(request):
        value = await body(request)
        settings = await asyncio.to_thread(read_settings)
        value.setdefault('engine', value.get('provider') or settings['language_config'].get('provider', 'local'))
        for key, item in settings['language_config'].items():
            value.setdefault(key, item)
        return web.json_response(await language().submit(value))

    @routes.get('/asset-library/api/language/jobs/{identifier}')
    async def language_job(request):
        return web.json_response(await language().result(request.match_info['identifier']))

    @routes.post('/asset-library/api/language/jobs/{identifier}/cancel')
    async def language_cancel(request):
        return web.json_response(await language().cancel(request.match_info['identifier']))

    @routes.get('/asset-library/api/external')
    async def external_list(request):
        return web.json_response(await asyncio.to_thread(external.list, request.query.get('session', '')))

    @routes.put('/asset-library/api/external/{identifier}')
    async def external_move(request):
        value = await body(request)
        item = await asyncio.to_thread(external.move, value.get('session', ''), request.match_info['identifier'], value.get('kind'), value.get('index'))
        return web.json_response({'item': item, **item})

    @routes.delete('/asset-library/api/external/{identifier}')
    async def external_remove(request):
        return web.json_response(await asyncio.to_thread(external.remove, request.query.get('session', ''), request.match_info['identifier']))

    @routes.get('/asset-library/api/external/{identifier}/media/{variant:thumbnail|preview|original}')
    async def external_media(request):
        async with media_slots:
            path = await asyncio.get_running_loop().run_in_executor(media_pool, external.media, request.query.get('session', ''), request.match_info['identifier'], request.match_info['variant'])
        return web.FileResponse(path, headers={'Cache-Control': 'private, max-age=3600', 'Content-Type': mimetypes.guess_type(path.name)[0] or 'image/webp'})

    @routes.post('/asset-library/api/external/upload')
    async def external_upload(request):
        from tempfile import TemporaryDirectory
        reader = await request.multipart()
        token = request.query.get('session', '')
        kind = request.query.get('kind', 'identity')
        paths = []
        with TemporaryDirectory(prefix='asset-upload-') as temp:
            async for field in reader:
                if field.name == 'session':
                    token = await field.text()
                elif field.name == 'kind':
                    kind = await field.text()
                elif field.filename:
                    if len(paths) >= 16:
                        raise ValueError('一次最多导入16张图片。')
                    path = Path(temp) / (str(len(paths)) + '.upload')
                    size = 0
                    with path.open('wb') as stream:
                        while chunk := await field.read_chunk():
                            size += len(chunk)
                            if size > 64 * 1024 * 1024:
                                raise ValueError('单张图片不能超过64MiB。')
                            stream.write(chunk)
                    paths.append((path, Path(field.filename).name))
            if not paths:
                raise ValueError('请选择图片。')
            items = [await asyncio.to_thread(external.add, token, path, title, kind) for path, title in paths]
        return web.json_response({'session': token, 'items': items})

    @routes.post('/asset-library/api/external/import')
    async def external_import(request):
        from tempfile import TemporaryDirectory
        value = await body(request)
        token, kind = value.get('session', ''), value.get('kind', 'identity')
        external.session(token)
        images = value.get('images')
        if not isinstance(images, list) or not 1 <= len(images) <= 16:
            raise ValueError('请选择1～16张图片。')
        items = []
        async with ClientSession(timeout=ClientTimeout(total=45)) as client:
            with TemporaryDirectory(prefix='asset-comfy-import-') as temp:
                for number, image in enumerate(images):
                    if not isinstance(image, dict):
                        raise ValueError('外接图片信息无效。')
                    descriptor = image_descriptor(image) if image.get('type') != 'input' else image_descriptor({**image, 'type': 'temp'})
                    descriptor['type'] = image.get('type')
                    if descriptor['type'] not in ('input', 'output', 'temp'):
                        raise ValueError('图片来源无效。')
                    path = Path(temp) / (str(number) + '.upload')
                    async with client.get(COMFY + '/view', params=descriptor) as response:
                        if response.status != 200:
                            raise FileNotFoundError('画布图片暂不可读取。')
                        size = 0
                        with path.open('wb') as stream:
                            async for chunk in response.content.iter_chunked(65536):
                                size += len(chunk)
                                if size > 64 * 1024 * 1024:
                                    raise ValueError('单张图片不能超过64MiB。')
                                stream.write(chunk)
                    items.append(await asyncio.to_thread(external.add, token, path, descriptor['filename'], kind))
        return web.json_response({'session': token, 'items': items})

    @routes.get('/asset-library/api/jobs')
    async def jobs(request):
        result = await generation().jobs_list(after_seq=max(0, int(request.query.get('after_seq', '0'))))
        result['jobs'] = [public_job(job) for job in result.get('jobs', [])]
        return web.json_response(result)

    @routes.get('/asset-library/api/jobs/{identifier}')
    async def job(request):
        result = await generation().result(request.match_info['identifier'])
        result['id'] = result.get('prompt_id', request.match_info['identifier'])
        return web.json_response(public_job(result))

    @routes.post('/asset-library/api/jobs/{identifier}/cancel')
    async def cancel(request):
        return web.json_response(public_job(await generation().cancel(request.match_info['identifier'])))

    @routes.post('/asset-library/api/jobs/{identifier}/retry')
    async def retry(request):
        return web.json_response(await generation().retry(request.match_info['identifier']))

    async def cache_result(image):
        descriptor = image_descriptor(image)
        target = store.cache / 'results' / (hashlib_id(json.dumps(descriptor, sort_keys=True)) + Path(descriptor['filename']).suffix.lower())
        if target.is_file():
            await asyncio.to_thread(target.touch)
            return target
        async with result_slots:
            if target.is_file():
                return target
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + '-' + uuid4().hex + '.tmp')
            try:
                async with ClientSession(timeout=ClientTimeout(total=35)) as session:
                    async with session.get(COMFY + '/view', params=descriptor) as response:
                        if response.status != 200:
                            raise FileNotFoundError('生成结果文件暂不可读取。')
                        size = 0
                        with temporary.open('wb') as stream:
                            async for chunk in response.content.iter_chunked(65536):
                                size += len(chunk)
                                if size > 96 * 1024 * 1024:
                                    raise ValueError('生成结果超过96MiB，请从ComfyUI保存原文件。')
                                stream.write(chunk)
                def verify():
                    from PIL import Image
                    with Image.open(temporary) as preview:
                        preview.verify()
                    if not target.exists():
                        temporary.replace(target)
                await asyncio.to_thread(verify)
                await asyncio.to_thread(store.prune)
                return target
            finally:
                temporary.unlink(missing_ok=True)

    @routes.get('/asset-library/api/result-image')
    async def result_image(request):
        path = await cache_result(dict(request.query))
        edge = request.query.get('size')
        if edge:
            edge = min(2048, max(64, int(edge)))
            from PIL import Image, ImageOps
            derivative = path.with_name(path.stem + f'-{edge}.webp')
            if not derivative.is_file():
                def prepare():
                    with Image.open(path) as source:
                        source = ImageOps.exif_transpose(source)
                        source.thumbnail((edge, edge), Image.Resampling.LANCZOS)
                        temp = derivative.with_name(derivative.name + '-' + uuid4().hex + '.tmp')
                        try:
                            source.convert('RGB').save(temp, 'WEBP', quality=86)
                            if not derivative.exists():
                                temp.replace(derivative)
                        finally:
                            temp.unlink(missing_ok=True)
                async with media_slots:
                    await asyncio.get_running_loop().run_in_executor(media_pool, prepare)
            path = derivative
        await asyncio.to_thread(store.prune)
        return web.FileResponse(path, headers={'Cache-Control': 'private, max-age=3600', 'Content-Type': mimetypes.guess_type(path.name)[0] or ('image/webp' if path.suffix == '.webp' else 'application/octet-stream')})

    @routes.post('/asset-library/api/results/open-folder')
    async def result_folder(request):
        image = image_descriptor((await body(request)).get('image'))
        async with ClientSession(timeout=ClientTimeout(total=15)) as session:
            async with session.post(COMFY + '/studio-results/reveal', json=image) as response:
                result = await response.json()
                if response.status != 200:
                    raise RuntimeError(result.get('message') or result.get('error') or '无法打开结果位置。')
        return web.json_response({'opened': True})

    @routes.post('/asset-library/api/results/save')
    async def result_save(request):
        image = image_descriptor((await body(request)).get('image'))
        if image['type'] == 'output':
            return web.json_response({'image': public_image(image), 'saved': True})
        path = await cache_result(image)
        # This installation's fixed output root is never supplied by the browser.
        output = RUNTIME['output'].resolve()
        destination = output / '资产库' / ('saved-' + uuid4().hex[:12] + path.suffix)
        destination.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copy2, path, destination)
        from PIL import Image
        def actual_dimensions():
            with Image.open(destination) as output_image:
                return output_image.size
        width, height = await asyncio.to_thread(actual_dimensions)
        saved_image = {'filename': destination.name, 'subfolder': '资产库', 'type': 'output', 'width': width, 'height': height}
        if hasattr(generation(), 'mark_saved'):
            await generation().mark_saved(image, saved_image)
        return web.json_response({'image': public_image(saved_image), 'saved': True})

    @routes.post('/asset-library/api/import/{identifier}')
    async def import_image(request):
        identifier = request.match_info['identifier']
        if identifier.startswith('external-'):
            item = await asyncio.to_thread(external.get, request.query.get('session', ''), identifier)
            path = Path(item['path'])
        else:
            path = await asyncio.to_thread(store.original, identifier)
        # Use ComfyUI's upload API, which resolves the active installation input directory.
        async with imports:
            async with ClientSession(timeout=ClientTimeout(total=30)) as session:
                with path.open('rb') as stream:
                    form = FormData()
                    form.add_field('image', stream, filename='asset-' + hashlib_id(identifier) + path.suffix, content_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
                    form.add_field('type', 'input'); form.add_field('subfolder', '资产库'); form.add_field('overwrite', 'true')
                    async with session.post(COMFY + '/upload/image', data=form) as response:
                        data = await response.json()
                        if response.status != 200:
                            raise RuntimeError(data.get('error', 'ComfyUI 未能导入这张素材。'))
        from urllib.parse import urlencode
        data['url'] = COMFY + '/view?' + urlencode({'filename': data['name'], 'subfolder': data.get('subfolder', ''), 'type': 'input'})
        return web.json_response(data)

    @routes.post('/asset-library/api/speech')
    async def speech(request):
        reader = await request.multipart()
        audio = None
        filename = 'recording.webm'
        async for field in reader:
            if field.name == 'audio':
                filename = Path(field.filename or filename).name
                chunks, size = [], 0
                while chunk := await field.read_chunk():
                    size += len(chunk)
                    if size > 20 * 1024 * 1024:
                        raise ValueError('语音过长，请分段录制。')
                    chunks.append(chunk)
                audio = b''.join(chunks)
        if not audio:
            raise ValueError('请先录制语音。')
        form = FormData()
        form.add_field('audio', audio, filename=filename, content_type=mimetypes.guess_type(filename)[0] or 'audio/webm')
        form.add_field('language', 'auto'); form.add_field('normalize', 'true'); form.add_field('threads', '2')
        async with ClientSession(timeout=ClientTimeout(total=120)) as session:
            async with session.post(COMFY + '/studio-speech/transcribe', data=form) as response:
                result = await response.json()
                if response.status != 200:
                    raise RuntimeError(result.get('message') or result.get('error') or '语音识别失败。')
        return web.json_response(result)

    @routes.post('/asset-library/api/result-assets')
    async def result_asset(request):
        value = await body(request)
        image = value.get('image')
        if not isinstance(image, dict) or image.get('type') not in ('output', 'temp'):
            raise ValueError('仅可收录 ComfyUI 的生成结果。')
        filename, subfolder = image.get('filename'), image.get('subfolder', '')
        if not isinstance(filename, str) or not isinstance(subfolder, str) or not filename or '/' in filename or '\\' in filename:
            raise ValueError('生成图片信息无效。')
        if any('..' in Path(part).parts or Path(part).anchor or ':' in part or '\x00' in part for part in (filename, subfolder)):
            raise ValueError('生成图片路径无效。')
        suffix = Path(filename).suffix.lower()
        if suffix not in ('.png', '.webp', '.jpg', '.jpeg'):
            raise ValueError('暂不支持这类生成文件。')
        descriptor = {'filename': filename, 'subfolder': subfolder, 'type': image['type']}
        identifier = 'result-' + hashlib_id(json.dumps(descriptor, sort_keys=True))
        path = store.root / 'user-assets' / (identifier + suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '-' + uuid4().hex + '.tmp')
        try:
            async with ClientSession(timeout=ClientTimeout(total=30)) as session:
                async with session.get(COMFY + '/view', params=descriptor) as response:
                    if response.status != 200:
                        raise RuntimeError('生成图片暂不可读取。')
                    size = 0
                    with temporary.open('wb') as stream:
                        async for chunk in response.content.iter_chunked(65536):
                            size += len(chunk)
                            if size > 32 * 1024 * 1024:
                                raise ValueError('这张结果图过大，请先从画布保存。')
                            stream.write(chunk)
            def finish():
                from PIL import Image
                with Image.open(temporary) as preview:
                    preview.verify()
                temporary.replace(path)
                return store.add_user_asset(identifier, path, prompt=value.get('prompt', ''), kind=value.get('kind', 'identity'))
            result = await asyncio.to_thread(finish)
            return web.json_response({'item': result})
        finally:
            temporary.unlink(missing_ok=True)

    @routes.get('/asset-library/api/debug/resources')
    async def resources(request):
        return web.json_response({'media_workers': 2, 'pending_media': len(media_pending), 'max_pending_media': 48,
                                  'thumbnail_edge': 320, 'preview_edge': 2048, 'disk_cache_limit_mib': 512,
                                  'image_memory_cache': 0, 'catalog_loaded': store._stamp is not None})

    @routes.get('/asset-library/{path:.*}')
    async def static(request):
        rel = request.match_info['path'] or 'index.html'
        ui = (ROOT / 'web' / 'ui').resolve()
        path = (ui / rel).resolve()
        if not path.is_relative_to(ui) or not path.is_file() or path.suffix not in ('.html', '.js', '.css', '.svg'):
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={'Content-Security-Policy': "default-src 'self'; img-src 'self' blob: data: http://127.0.0.1:8000; media-src 'self' blob:; connect-src 'self' http://127.0.0.1:8000; script-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors http://127.0.0.1:8000 http://localhost:8000"})

    app.add_routes(routes)
    async def cleanup(application):
        for task in list(media_pending):
            task.cancel()
        media_pool.shutdown(wait=False, cancel_futures=True)
        if generations is not None and hasattr(generations, 'close'):
            await generations.close()
        if languages is not None:
            await languages.close()
        await asyncio.to_thread(external.close)
    app.on_cleanup.append(cleanup)
    return app


def hashlib_id(value):
    import hashlib
    return hashlib.sha256(value.encode()).hexdigest()[:20]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--assets', type=Path, default=RUNTIME['asset_root'])
    parser.add_argument('--port', type=int, default=RUNTIME['port'])
    args = parser.parse_args()
    web.run_app(create_app(args.assets, args.port), host='127.0.0.1', port=args.port, print=lambda message: print(message, flush=True), access_log=None)


if __name__ == '__main__':
    main()
