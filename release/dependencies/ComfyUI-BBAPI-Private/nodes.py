from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import time
import uuid
import re
import io
import hashlib

import folder_paths
import numpy as np
import torch
from PIL import Image, PngImagePlugin

from .client import (
    Connection, ImageClient, ItemResult, MAX_INPUT_BYTES, MAX_REFERENCES,
    MAX_PIXELS, decode_image, encode_reference,
)
from .secrets import ProfileStore, normalize_base_url

NODE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get(
    'COMFYUI_BBAPI_PRIVATE_CONFIG',
    str(Path(folder_paths.get_user_directory()) / 'bbapi_private_profiles.json'),
))


def profile_store() -> ProfileStore:
    return ProfileStore(CONFIG_PATH)


def make_connection(profile: str, base_url: str, timeout: int = 300,
                    default_model: str = '') -> Connection:
    value = profile_store().load(profile, base_url)
    return Connection(
        base_url=normalize_base_url(value['base_url']), api_key=value['api_key'],
        profile=profile.strip(), timeout=max(30, min(900, int(timeout))),
        default_model=default_model.strip() or value.get('default_model', ''),
    )


def _image_to_tensor(image: Image.Image) -> torch.Tensor:
    return torch.from_numpy(np.asarray(image.convert('RGB'), dtype=np.float32) / 255.0)[None, ...]


def _tensor_references(images, limit: int = MAX_REFERENCES) -> list[bytes]:
    if images is None:
        return []
    if len(images) > limit:
        raise ValueError(f'此入口最多支持 {limit} 张参考图片。')
    result, total = [], 0
    for image in images:
        height, width = image.shape[0:2]
        if height * width > MAX_PIXELS:
            raise ValueError('单张参考图超过 4000 万像素，请先缩小。')
        array = (image.detach().cpu().numpy() * 255).clip(0, 255).astype(np.uint8)
        data = encode_reference(Image.fromarray(array))
        total += len(data)
        if total > MAX_INPUT_BYTES:
            raise ValueError('参考图片总量超过 64MB，请缩小后重试。')
        result.append(data)
    return result


def _cancelled() -> bool:
    from comfy.model_management import processing_interrupted
    return bool(processing_interrupted())


def _report(model: str, results: list[ItemResult], started: float) -> str:
    success = [item for item in results if item.image is not None]
    failed = [item for item in results if item.image is None]
    lines = [f'模型：{model}', f'完成：{len(success)}/{len(results)}，总耗时：{time.monotonic() - started:.1f} 秒']
    if success:
        lines.append('成功序号：' + '、'.join(str(item.index + 1) for item in success))
        if all(item.file for item in success):
            lines.append('已自动保存到 output/BBAPI。')
    for item in results:
        if not item.error:
            continue
        suffix = f'；请求编号：{item.request_id}' if item.request_id else ''
        retry = f'；Retry-After：{item.retry_after}' if item.retry_after else ''
        label = '保存提示' if item.image is not None else f'失败（HTTP {item.status or "连接"}）'
        lines.append(f'第 {item.index + 1} 张{label}：{item.error}{suffix}{retry}')
    if failed:
        lines.append('失败项未自动重提。超时后请先核对站点记录，再单独重跑。')
    return '\n'.join(lines)


class BBAPIConnection:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {
            '配置名称': ('STRING', {'default': 'bbapi-main', 'multiline': False}),
            '接口地址': ('STRING', {'default': 'https://bbapi.ainivox.cn', 'multiline': False}),
            '默认模型': ('STRING', {'default': '', 'multiline': False,
                'tooltip': '填写该密钥分组开放的模型；生成节点留空时使用此模型。'}),
            '超时秒': ('INT', {'default': 300, 'min': 30, 'max': 900, 'step': 10, 'advanced': True}),
        }}

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float('nan')

    RETURN_TYPES = ('BBAPI_CONNECTION', 'STRING')
    RETURN_NAMES = ('连接', '状态')
    FUNCTION = 'connect'
    CATEGORY = 'BBAPI私有/连接'
    DESCRIPTION = 'API Key 在本节点密码框输入并保存，只保存在当前 Windows 用户的加密配置。'

    def connect(self, **kwargs):
        profile = kwargs['配置名称'].strip()
        connection = make_connection(profile, kwargs['接口地址'], kwargs['超时秒'], kwargs['默认模型'])
        return (connection, f'已使用本机配置「{profile}」。')


class BBAPIListModels:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'连接': ('BBAPI_CONNECTION',)}}

    RETURN_TYPES = ('STRING',)
    RETURN_NAMES = ('生图模型列表',)
    FUNCTION = 'list_models'
    CATEGORY = 'BBAPI私有/连接'

    def list_models(self, 连接):
        models = ImageClient(连接).models()
        return ('\n'.join(f"{item['id']}  {item.get('display_name', '')}".strip() for item in models),)


class BBAPIReferences:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'图片文件': ('STRING', {'default': '[]', 'multiline': False})}}

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float('nan')

    RETURN_TYPES = ('BBAPI_REFERENCES',)
    RETURN_NAMES = ('参考图',)
    FUNCTION = 'load'
    CATEGORY = 'BBAPI私有/输入'
    DESCRIPTION = '一次上传最多16张参考图，可逐张移除和调整顺序。工作流只保存输入文件名。'

    def load(self, 图片文件):
        try:
            names = json.loads(图片文件)
        except (ValueError, TypeError) as error:
            raise ValueError('参考图片列表无效，请重新选择图片。') from error
        if not isinstance(names, list) or len(names) > MAX_REFERENCES:
            raise ValueError('参考图片最多16张。')
        root = Path(folder_paths.get_input_directory()).resolve()
        images, total = [], 0
        for name in names:
            if not isinstance(name, str) or len(name) > 1000:
                raise ValueError('参考图片文件名无效。')
            path = (root / name).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError('参考图片不存在，请重新上传。')
            if path.stat().st_size > MAX_INPUT_BYTES:
                raise ValueError('单张参考图片超过64MB。')
            image = decode_image(path.read_bytes())
            encoded = encode_reference(image)
            total += len(encoded)
            if total > MAX_INPUT_BYTES:
                raise ValueError('参考图片总量超过64MB，请缩小或减少数量。')
            images.append(encoded)
        return (images,)


class _ImageBase:
    CATEGORY = 'BBAPI私有/生图'
    RETURN_TYPES = ('IMAGE', 'STRING', 'IMAGE', 'BBAPI_REFERENCES', 'IMAGE')
    OUTPUT_IS_LIST = (True, False, False, False, True)
    RETURN_NAMES = ('全部生成图', '报告', '第一张生成图', '生成图作参考', '输入参考图')
    FUNCTION = 'generate'
    OUTPUT_NODE = True
    DESCRIPTION = '每张仅提交一次；成功图片自动保存并按原序输出。限流或超时会报告，避免重复扣费。'

    @classmethod
    def _required(cls):
        return {
            '连接': ('BBAPI_CONNECTION',),
            '模型': ('STRING', {'default': '自动（2.5优先）', 'multiline': False,
                'tooltip': '留空使用连接节点的默认模型，也可填写该密钥允许的其他模型。'}),
            '提示词': ('STRING', {'default': '', 'multiline': True}),
            '分辨率': (['1K', '2K', '4K'],),
            '比例': (['1:1', '3:2', '2:3', '4:3', '3:4', '16:9', '9:16'] +
                    [f'{x}（{label}）' for x,label in [('1:1','方形'),('3:2','横'),('2:3','竖'),('4:3','横'),('3:4','竖'),('16:9','横'),('9:16','竖')]] +
                    [f'跟随参考图{i}' for i in range(1,17)],),
            '批量数量': ('INT', {'default': 1, 'min': 1, 'max': 16, 'step': 1}),
            '并发上限': ('INT', {'default': 2, 'min': 1, 'max': 16, 'step': 1,
                'tooltip': '最多16；首次建议1–2。站点或上游仍可能限制更低并发。'}),
            '质量': (['省费', '标准', '精细', '自动'], {'default': '省费', 'advanced': True}),
            '输出模式': (['预览（不保存）','保存并预览'],{'default':'保存并预览'}),
            '重新生成编号': ('INT',{'default':0,'min':0,'max':2147483647,'advanced':True,
                                  'tooltip':'相同输入复用已生成结果；要重新请求一组图片时递增此编号。'}),
        }

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float('nan')

    @staticmethod
    def _run(连接, 模型, 提示词, 分辨率, 比例, 质量, 批量数量, 并发上限, references=None,
             输出模式='保存并预览',重新生成编号=0):
        if not isinstance(连接, Connection):
            raise ValueError('请连接 BBAPI 私有连接节点。')
        model = 模型.strip() or 连接.default_model
        if model.startswith('自动'):
            available = [item['id'] for item in ImageClient(连接).models()]
            candidates = [x for x in available if re.match(r'^gpt-image-(?:2\.5|2)(?:-|$)', x, re.I)]
            candidates.sort(key=lambda x: (not x.startswith('gpt-image-2.5'), x != 连接.default_model, x))
            if not candidates:
                raise ValueError('此密钥未开放 GPT Image 2/2.5，自动模式不会降级，请读取模型列表后选择。')
            model = candidates[0]
        ratio = re.sub(r'（.*?）', '', 比例)
        if ratio.startswith('跟随参考图'):
            index = int(ratio.removeprefix('跟随参考图'))-1
            if not references or index >= len(references):
                raise ValueError('所选比例参考图尚未接入。')
            image = decode_image(references[index])
            ratio = f'{image.width}:{image.height}'
            if any(name in model.lower() for name in ('gemini','grok','nano-banana')):
                presets = ['1:1','3:2','2:3','4:3','3:4','16:9','9:16']
                ratio = min(presets,key=lambda value: abs(float(value.split(':')[0])/float(value.split(':')[1])-image.width/image.height))
        started = time.monotonic()
        run_id = datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
        if 输出模式 not in ('预览（不保存）','保存并预览'):raise ValueError('输出模式无效。')
        preview=输出模式=='预览（不保存）'
        subfolder = Path('BBAPI-preview' if preview else 'BBAPI') / datetime.now().strftime('%Y-%m-%d')
        output_dir = Path(folder_paths.get_temp_directory() if preview else folder_paths.get_output_directory()) / subfolder
        from comfy.utils import ProgressBar
        progress = ProgressBar(批量数量)

        def save(image, index, request_id):
            output_dir.mkdir(parents=True, exist_ok=True)
            filename = f'{run_id}-{index + 1:02}.png'
            metadata = PngImagePlugin.PngInfo()
            metadata.add_text('bbapi', json.dumps({
                'model': model, 'prompt': 提示词, 'resolution': 分辨率,
                'aspect_ratio': 比例, 'index': index + 1, 'request_id': request_id,
            }, ensure_ascii=False))
            image.save(output_dir / filename, format='PNG', pnginfo=metadata)
            return {'filename': filename, 'subfolder': subfolder.as_posix(), 'type': 'temp' if preview else 'output'}

        signature={'profile':连接.profile,'base_url':连接.base_url,'model':model,'prompt':提示词,
                   'workspace':str(Path(folder_paths.get_output_directory()).resolve()),
                   'resolution':分辨率,'ratio':ratio,'quality':质量,'batch':批量数量,'generation':重新生成编号,
                   'references':[hashlib.sha256(item).hexdigest() for item in references or []]}
        key=hashlib.sha256(json.dumps(signature,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        cache_dir=Path(folder_paths.get_user_directory())/'codex-cache'/'bbapi-results'/key
        receipt=cache_dir/'result.json';reused=False
        if receipt.is_file():
            metadata=json.loads(receipt.read_text(encoding='utf-8'));results=[]
            for index,item in enumerate(metadata.get('images',[])):
                path=cache_dir/item['file']
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
                    raise ValueError('已有生图缓存不可读取；核对结果后递增重新生成编号，不会自动重新扣费。')
                image=decode_image(path.read_bytes());results.append(ItemResult(index=index,image=image,file=save(image,index,'')))
            if len(results)!=批量数量:raise ValueError('已有结果缓存不完整；请核对后递增重新生成编号。')
            reused=True
        else:
            results = ImageClient(连接).run(
                model=model, prompt=提示词, resolution=分辨率, ratio=ratio,
                quality={'省费': 'low', '标准': 'medium', '精细': 'high', '自动': 'auto'}.get(质量, 质量),
                batch=批量数量, concurrency=并发上限, references=references or [],
                cancelled=_cancelled, save_image=save,
                on_progress=lambda current, total: progress.update_absolute(current, total),
            )
            if len(results)==批量数量 and all(item.image is not None for item in results):
                cache_dir.mkdir(parents=True,exist_ok=True);metadata={'images':[]}
                for index,item in enumerate(results):
                    raw=encode_reference(item.image);name=f'{index:02}.png';(cache_dir/name).write_bytes(raw)
                    metadata['images'].append({'file':name,'sha256':hashlib.sha256(raw).hexdigest()})
                temp=cache_dir/('result-'+uuid.uuid4().hex+'.tmp');temp.write_text(json.dumps(metadata),encoding='utf-8');temp.replace(receipt)
        image_list = [_image_to_tensor(item.image) for item in results if item.image is not None]
        report = _report(model, results, started)
        if preview:report=report.replace('已自动保存到 output/BBAPI。','仅临时预览，未保存到正式 output。')
        if reused:report+='\n复用已有结果；没有重新提交生图请求。'
        encoded = [encode_reference(item.image) for item in results if item.image is not None]
        return {'ui': {'images': [item.file for item in results if item.file], 'text': [report], 'bbapi_reference_count':[len(references or [])]},
                'result': (image_list, report, image_list[0] if image_list else None, encoded,
                           [_image_to_tensor(decode_image(value)) for value in references or []])}


class BBAPIPromptToImage(_ImageBase):
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': cls._required()}

    def generate(self, **kwargs):
        return self._run(**kwargs)


class BBAPIImageToImage(_ImageBase):
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': cls._required(), 'optional': {
            '参考图': ('BBAPI_REFERENCES',),
            '上游图片': ('IMAGE', {'tooltip': '可直接接任意图片节点；与上传面板二选一，单图入口仅用1张。'}),
        }}

    def generate(self, 参考图=None, 上游图片=None, **kwargs):
        if 参考图 and 上游图片 is not None:
            raise ValueError('上传参考图和上游图片请选择其中一个入口。')
        references = 参考图 or _tensor_references(上游图片, 1)
        if len(references) != 1:
            raise ValueError('图生图需要1张参考图；多张请使用多图参考节点。')
        return self._run(references=references, **kwargs)


class BBAPIMultiReference(_ImageBase):
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': cls._required(), 'optional': {
            '参考图': ('BBAPI_REFERENCES',),
            '上游图片批次': ('IMAGE', {'tooltip': '最多16张；上游的IMAGE批次也可直接作为参考图。'}),
        }}

    def generate(self, 参考图=None, 上游图片批次=None, **kwargs):
        if 参考图 and 上游图片批次 is not None:
            raise ValueError('上传参考图和上游图片批次请选择其中一个入口。')
        references = 参考图 or _tensor_references(上游图片批次)
        if not references:
            raise ValueError('请先上传或连接参考图片。')
        return self._run(references=references, **kwargs)


NODE_CLASS_MAPPINGS = {
    'BBAPIConnection': BBAPIConnection,
    'BBAPIListModels': BBAPIListModels,
    'BBAPIReferences': BBAPIReferences,
    'BBAPIPromptToImage': BBAPIPromptToImage,
    'BBAPIImageToImage': BBAPIImageToImage,
    'BBAPIMultiReference': BBAPIMultiReference,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    'BBAPIConnection': 'BBAPI 私有连接',
    'BBAPIListModels': 'BBAPI 生图模型列表',
    'BBAPIReferences': '上传参考图片',
    'BBAPIPromptToImage': 'BBAPI 文生图',
    'BBAPIImageToImage': 'BBAPI 图生图',
    'BBAPIMultiReference': 'BBAPI 多图参考',
}
