"""Qwen 2.1 workbench adapters; native generation nodes remain in the graph."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
import re
import sys
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageOps
import torch

import folder_paths
import nodes

from .image_tools import (
    CodexImageCanvas, CodexImageOutput, CodexQwenImageLora, _cache_key, _cached_image, _call, _model_stamps,
    _outputs, _save_cached, load_tensor, tensor_path,
)
from .resource_control import release_resources
from .static_prompt import REPAIR_RULE


MODES = ['文生图', '图生图']
ROUTES = ['Qwen Image 2.1', 'Qwen Image 2.1 Turbo', 'MOSS', 'BBAPI', 'Z-Image', 'Krea 2 Turbo']


def _mode(value):
    if value not in MODES:
        raise ValueError('请选择文生图或图生图。')
    return value


def _library(value):
    try:
        entries = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise ValueError('参考图库数据无效，请重新选择图片。') from exc
    if not isinstance(entries, list) or len(entries) > 16:
        raise ValueError('参考图库最多支持16张图片。')
    seen = set()
    result = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('参考图片条目无效。')
        identity = entry.get('id')
        if not isinstance(identity, str) or not re.fullmatch(r'ref[1-9]\d{0,8}', identity) or identity in seen:
            raise ValueError('参考图片编号无效或重复，请重新选择图片。')
        name = entry.get('name', '')
        if not isinstance(name, str) or len(name) > 160:
            raise ValueError('参考图片名称最多160字。')
        filename, subfolder = entry.get('filename', ''), entry.get('subfolder', '')
        if not isinstance(filename, str) or not filename or not isinstance(subfolder, str):
            raise ValueError('参考图片缺少有效文件名。')
        if entry.get('type', 'input') != 'input':
            raise ValueError('参考图库只接受已上传到输入目录的图片。')
        seen.add(identity)
        uid = entry.get('uid')
        if uid is not None and (not isinstance(uid, str) or len(uid) > 1024 or re.search(r'[\s\[\]<>]', uid)):
            raise ValueError('参考图片身份无效，请重新选择图片。')
        result.append({'id': identity, 'uid': uid or 'legacy-' + identity,
                       'name': name.strip() or f'参考图{identity[3:]}',
                       'filename': filename, 'subfolder': subfolder, 'type': 'input'})
    return result


def _input_path(entry):
    root = Path(folder_paths.get_input_directory()).resolve()
    parts = []
    for value in (entry['subfolder'], entry['filename']):
        normalized = value.replace('\\', '/')
        if normalized.startswith('/') or ':' in normalized or '\x00' in normalized or '..' in normalized.split('/'):
            raise ValueError('参考图片路径必须位于ComfyUI输入目录。')
        parts.append(normalized)
    path = root.joinpath(*parts).resolve()
    if not path.is_relative_to(root):
        raise ValueError('参考图片路径超出ComfyUI输入目录。')
    return path


def _image(value, label):
    if not torch.is_tensor(value) or value.ndim != 4 or len(value) != 1 or value.shape[-1] not in (3, 4):
        raise ValueError(f'{label}需要一张RGB或RGBA图片。')
    return value


def _file_image(entry):
    path = _input_path(entry)
    if not path.is_file():
        raise ValueError(f'未找到参考图片：{entry["name"]}。')
    try:
        with Image.open(path) as source:
            rgba = ImageOps.exif_transpose(source).convert('RGBA')
            canvas = Image.new('RGBA', rgba.size, 'white')
            canvas.alpha_composite(rgba)
            pixels = np.asarray(canvas.convert('RGB'), dtype=np.float32) / 255.0
    except (OSError, ValueError) as exc:
        raise ValueError(f'无法读取参考图片：{entry["name"]}。') from exc
    return torch.from_numpy(pixels)[None]


def _reference_stamp(entry):
    path = _input_path(entry)
    try:
        stat = path.stat()
        return str(path), stat.st_size, stat.st_mtime_ns
    except OSError:
        return str(path), None, None


@lru_cache(maxsize=128)
def _reference_dimensions(path, size, modified):
    """Read only image headers; file stamps invalidate same-name replacements."""
    if size is None:
        return None
    try:
        with Image.open(path) as source:
            width, height = source.size
            # PNG's override can decode all pixels to search for trailing EXIF.
            orientation = Image.Image.getexif(source).get(274, 1)
            if orientation in (5, 6, 7, 8):
                width, height = height, width
            return int(width), int(height)
    except (OSError, ValueError, SyntaxError):
        return None


def _reference_metadata(entries, kwargs, primary):
    records, sizes = [], {}
    for entry in entries:
        external = f'参考图{entry["id"][3:]}' in kwargs
        dimensions = None if external else _reference_dimensions(*_reference_stamp(entry))
        record = {**entry, 'external': external, 'missing': dimensions is None}
        if dimensions:
            sizes[entry['id']] = {'width': dimensions[0], 'height': dimensions[1]}
        records.append(record)
    identities = {entry['id'] for entry in entries}
    for number in range(1, 17):
        if f'参考图{number}' in kwargs and f'ref{number}' not in identities:
            records.append({'id': f'ref{number}', 'name': f'参考图{number}',
                            'type': 'external', 'external': True, 'missing': True})
    if len(records) > 16:
        raise ValueError('上传与外接参考图合计最多16张，请移除多余图片。')
    selected = primary if type(primary) is int and 1 <= primary <= len(records) else 1
    return {'images': [], 'labels': [entry['name'] for entry in records], 'entries': records,
            'image_slots': {}, 'reference_sizes': sizes, 'primary_slot': selected,
            'primary_id': records[selected - 1]['id'] if records else None}


def _choices(category, contains, preferred):
    names = [name for name in folder_paths.get_filename_list(category)
             if any(part in name.lower() for part in contains)]
    names.sort(key=lambda name: (name != preferred, name.lower()))
    return names or [f'未安装{preferred}']


class CodexStudioCanvas(CodexImageCanvas):
    @classmethod
    def VALIDATE_INPUTS(cls, 比例=None):
        if 比例 is None:
            return True
        presets = CodexImageCanvas.INPUT_TYPES()['required']['比例'][0]
        if 比例 in presets or isinstance(比例, str) and re.fullmatch(r'跟随参考图[1-9]\d{0,8}', 比例):
            return True
        return '请选择有效的画布比例或已启用的参考图。'

    CATEGORY = 'Codex/图像工作台'

    def resolve(self, 比例='跟随参考图', 参考图=None, 参考图集=None, **kwargs):
        valid = self.VALIDATE_INPUTS(比例)
        if valid is not True:
            raise ValueError(valid)
        match = re.fullmatch(r'跟随参考图([1-9]\d{0,8})', 比例)
        collection = 参考图集 or {}
        images = collection.get('images', [])
        metadata_identity = None
        if match:
            identity = 'ref' + match.group(1)
            slot = collection.get('image_slots', {}).get(identity)
            if type(slot) is not int or not 1 <= slot <= len(images):
                metadata_identity = identity
            else:
                source = images[slot - 1]
        elif 比例 == '跟随参考图' and images:
            slot = collection.get('primary_slot', 1)
            source = images[slot - 1] if type(slot) is int and 1 <= slot <= len(images) else images[0]
        elif 比例 == '跟随参考图' and collection.get('entries'):
            metadata_identity = collection.get('primary_id') or collection['entries'][0]['id']
        else:
            return super().resolve(比例=比例, 参考图=参考图, 参考图集=参考图集, **kwargs)
        if metadata_identity:
            dimensions = collection.get('reference_sizes', {}).get(metadata_identity)
            if not dimensions:
                entry = next((item for item in collection.get('entries', []) if item['id'] == metadata_identity), None)
                if entry and entry.get('external'):
                    raise ValueError('该外接图需图生图模式读取尺寸。')
                raise ValueError(f'比例所用的参考图{metadata_identity[3:]}已删除、缺失或未启用，请重新选择参考图。')
            source = SimpleNamespace(shape=(1, dimensions['height'], dimensions['width'], 3))
        return super().resolve(比例='跟随参考图', 参考图=source, 参考图集={'images': [source]}, **kwargs)


class CodexQwenMode:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'生成模式': (MODES, {'default': '文生图'}),
                             '模型路线': (ROUTES, {'default': ROUTES[0]})}}

    RETURN_TYPES = ('BOOLEAN', 'BOOLEAN', 'STRING', 'BOOLEAN', 'BOOLEAN', 'STRING', 'BOOLEAN', 'BOOLEAN')
    RETURN_NAMES = ('是否Turbo', '是否图生图', '生成模式', '是否MOSS', '是否BBAPI', '优化目标', '是否ZImage', '是否Krea')
    FUNCTION = 'select'
    CATEGORY = 'Codex/Qwen工作台'

    def select(self, 生成模式='文生图', 模型路线=ROUTES[0]):
        _mode(生成模式)
        if 模型路线 not in ROUTES:
            raise ValueError('请选择Qwen Image 2.1、Turbo、MOSS、BBAPI、Z-Image或Krea 2 Turbo路线。')
        target = ('MOSS-Image' if 模型路线 == 'MOSS' else 'GPT Image 2/2.5' if 模型路线 == 'BBAPI'
                  else 'Z-Image Turbo' if 模型路线 == 'Z-Image'
                  else 'Krea-2 Turbo' if 模型路线 == 'Krea 2 Turbo' else 'Image2.1')
        return (模型路线 == ROUTES[1], 生成模式 == '图生图', 生成模式,
                模型路线 == 'MOSS', 模型路线 == 'BBAPI', target, 模型路线 == 'Z-Image', 模型路线 == 'Krea 2 Turbo')


def _krea_weight(category, filename):
    # A string field deliberately avoids rejecting an unused optional branch at graph validation.
    if not isinstance(filename, str) or not filename.strip():
        raise ValueError('请填写Krea 2配套权重文件名。')
    path = folder_paths.get_full_path(category, filename)
    if not path or not Path(path).is_file():
        raise ValueError(f'未找到Krea 2权重：models/{category}/{filename}。请从Comfy-Org/Krea-2下载后刷新模型。')
    return filename


class CodexStudioKreaModel:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'图像模型': ('STRING', {'default': 'krea2_turbo_fp8_scaled.safetensors',
                                               'tooltip': 'Comfy-Org/Krea-2官方模型；放入models/diffusion_models。仅选中Krea路线才读取。'})}}

    RETURN_TYPES = ('MODEL',)
    RETURN_NAMES = ('Krea模型',)
    FUNCTION = 'load'
    CATEGORY = 'Codex/图像工作台'
    DESCRIPTION = 'Krea 2模型按需加载；未选中此路线时不要求安装对应权重。'

    @classmethod
    def IS_CHANGED(cls, 图像模型='krea2_turbo_fp8_scaled.safetensors'):
        return _cache_key('krea-model-v1', _model_stamps([('diffusion_models', 图像模型)]))

    def load(self, 图像模型='krea2_turbo_fp8_scaled.safetensors'):
        clip_types = nodes.CLIPLoader.INPUT_TYPES()['required']['type'][0]
        if 'krea2' not in clip_types:
            raise RuntimeError('当前ComfyUI核心不支持Krea 2，请先更新到含krea2编码器的版本。')
        return nodes.UNETLoader().load_unet(_krea_weight('diffusion_models', 图像模型), 'default')


class CodexStudioKreaGenerate:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {
            '模型': ('MODEL',),
            '提示词': ('STRING', {'default': '', 'multiline': True}),
            '生成模式': ('STRING', {'default': '文生图', 'forceInput': True}),
            '文本编码器': ('STRING', {'default': 'qwen3vl_4b_fp8_scaled.safetensors'}),
            'VAE': ('STRING', {'default': 'qwen_image_vae.safetensors'}),
            '宽度': ('INT', {'default': 1024, 'min': 64, 'max': 4096, 'step': 32}),
            '高度': ('INT', {'default': 1024, 'min': 64, 'max': 4096, 'step': 32}),
            '种子': ('INT', {'default': 42, 'min': 0, 'max': 0xffffffffffffffff}),
            '步数': ('INT', {'default': 8, 'min': 1, 'max': 100}),
            'CFG': ('FLOAT', {'default': 1.0, 'min': 0.0, 'max': 30.0, 'step': 0.1}),
            '采样器': (list(nodes.KSampler.INPUT_TYPES()['required']['sampler_name'][0]), {'default': 'euler'}),
            '调度器': (list(nodes.KSampler.INPUT_TYPES()['required']['scheduler'][0]), {'default': 'simple'}),
            '图生图降噪': ('FLOAT', {'default': 0.65, 'min': 0.0, 'max': 1.0, 'step': 0.01,
                                      'tooltip': '单张主图潜空间重绘。越低越接近原图，越高改动越大；不提供多图指令编辑。'}),
            '主参考图编号': ('INT', {'default': 1, 'min': 1, 'max': 16}),
        }, 'optional': {'参考图集': ('CODEX_REFERENCES', {'lazy': True})}}

    RETURN_TYPES = ('IMAGE',)
    RETURN_NAMES = ('Krea生成图',)
    FUNCTION = 'generate'
    CATEGORY = 'Codex/图像工作台'
    DESCRIPTION = '官方Krea 2 Turbo文生图链；图生图采用单张主参考图VAE重绘，不等同于多图编辑或风格参考LoRA。'

    def check_lazy_status(self, 生成模式='文生图', **kwargs):
        return ['参考图集'] if _mode(生成模式) == '图生图' and '参考图集' in kwargs and kwargs['参考图集'] is None else []

    @classmethod
    def IS_CHANGED(cls, 文本编码器='qwen3vl_4b_fp8_scaled.safetensors', VAE='qwen_image_vae.safetensors', **kwargs):
        return _cache_key('krea-generate-v1', _model_stamps([('text_encoders', 文本编码器), ('vae', VAE)]))

    def generate(self, 模型, 提示词='', 生成模式='文生图', 文本编码器='qwen3vl_4b_fp8_scaled.safetensors',
                 VAE='qwen_image_vae.safetensors', 宽度=1024, 高度=1024, 种子=42, 步数=8, CFG=1.0,
                 采样器='euler', 调度器='simple', 图生图降噪=0.65, 主参考图编号=1, 参考图集=None):
        _mode(生成模式)
        if not isinstance(提示词, str) or not 提示词.strip():
            raise ValueError('请填写Krea 2生图提示词。')
        if any(type(value) is not int or not 64 <= value <= 4096 or value % 8 for value in (宽度, 高度)):
            raise ValueError('Krea 2宽高需为64～4096之间的8的倍数。')
        if not 0 <= float(图生图降噪) <= 1:
            raise ValueError('Krea 2图生图降噪应在0～1之间。')
        prompt = CodexStudioMossPrompt().prepare(提示词, 生成模式, 主参考图编号, 参考图集, 'Krea 2')[0]
        encoder_name = _krea_weight('text_encoders', 文本编码器)
        vae_name = _krea_weight('vae', VAE)
        clip = nodes.CLIPLoader().load_clip(encoder_name, 'krea2', 'default')[0]
        vae = nodes.VAELoader().load_vae(vae_name)[0]
        positive = nodes.CLIPTextEncode().encode(clip, prompt)[0]
        negative = _call('ConditioningZeroOut', conditioning=positive)[0]
        if 生成模式 == '图生图':
            image = _image(参考图集['images'][主参考图编号 - 1], 'Krea主参考图')[:, :, :, :3]
            from comfy.utils import common_upscale
            image = common_upscale(image.movedim(-1, 1), 宽度, 高度, 'lanczos', 'disabled').movedim(1, -1)
            latent = nodes.VAEEncode().encode(vae, image)[0]
            denoise = float(图生图降噪)
        else:
            latent = nodes.EmptyLatentImage().generate(宽度, 高度, 1)[0]
            denoise = 1.0
        samples = nodes.KSampler().sample(模型, 种子, 步数, CFG, 采样器, 调度器,
                                         positive, negative, latent, denoise=denoise)[0]
        return nodes.VAEDecode().decode(vae, samples)


class CodexStudioLora(CodexQwenImageLora):
    @classmethod
    def INPUT_TYPES(cls):
        schema = super().INPUT_TYPES()
        schema['required']['LoRA文件'][1]['tooltip'] = '请选择匹配当前生成模型基座的LoRA；不同模型族的LoRA不能混用。'
        return schema

    CATEGORY = 'Codex/图像工作台'

    def apply(self, *args, **kwargs):
        try:
            return super().apply(*args, **kwargs)
        except ValueError:
            raise ValueError('此LoRA无法应用到当前模型，请检查LoRA基座、模型版本和权重格式。') from None


class CodexStudioDenoise:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'生成模式': ('STRING', {'default': '文生图', 'forceInput': True}),
                             '图生图降噪': ('FLOAT', {'default': 0.65, 'min': 0.0, 'max': 1.0, 'step': 0.01})}}

    RETURN_TYPES = ('FLOAT',)
    RETURN_NAMES = ('本次降噪',)
    FUNCTION = 'resolve'
    CATEGORY = 'Codex/图像工作台'

    def resolve(self, 生成模式='文生图', 图生图降噪=0.65):
        if _mode(生成模式) == '文生图':
            return (1.0,)
        value = float(图生图降噪)
        if not 0 <= value <= 1:
            raise ValueError('图生图降噪应在0～1之间。')
        return (value,)


class CodexStudioMossPrompt:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'提示词': ('STRING', {'default': '', 'multiline': True}),
                             '生成模式': ('STRING', {'default': '文生图', 'forceInput': True}),
                             '主参考图编号': ('INT', {'default': 1, 'min': 1, 'max': 16})},
                'optional': {'参考图集': ('CODEX_REFERENCES', {'lazy': True}),
                             '生成模型名': ('STRING', {'default': 'MOSS'})}}

    RETURN_TYPES = ('STRING',)
    RETURN_NAMES = ('MOSS提示词',)
    FUNCTION = 'prepare'
    CATEGORY = 'Codex/图像工作台'

    def check_lazy_status(self, 生成模式='文生图', **kwargs):
        return ['参考图集'] if _mode(生成模式) == '图生图' and '参考图集' in kwargs and kwargs['参考图集'] is None else []

    def prepare(self, 提示词='', 生成模式='文生图', 主参考图编号=1, 参考图集=None, 生成模型名='MOSS'):
        if _mode(生成模式) == '文生图':
            return (提示词,)
        images = (参考图集 or {}).get('images', [])
        if type(主参考图编号) is not int or not 1 <= 主参考图编号 <= len(images):
            raise ValueError(f'{生成模型名}图生图需要选择一张已接入的主参考图。')
        referred = [int(number) for number in re.findall(r'<image(\d+)>', 提示词, flags=re.I)]
        if any(number != 主参考图编号 for number in referred):
            raise ValueError(f'{生成模型名}图生图仅重绘所选主参考图；多图编辑请切换Qwen Image 2.1或BBAPI。')
        return (re.sub(r'<image\d+>', '输入图像', 提示词, flags=re.I),)


def _bbapi_safe_text(value, connection):
    text = str(value)
    secret = getattr(connection, 'api_key', '')
    if isinstance(secret, str) and secret:
        text = text.replace(secret, '[已隐藏]')
    text = re.sub(r'(?:Bearer\s+|sk-)[A-Za-z0-9_.-]{8,}', '[已隐藏]', text, flags=re.I)
    return re.sub(r'https?://\S+', '[链接已隐藏]', text)


class CodexStudioBBAPI:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'连接': ('BBAPI_CONNECTION',),
                             '提示词': ('STRING', {'default': '', 'multiline': True}),
                             '生成模式': ('STRING', {'default': '文生图', 'forceInput': True}),
                             '分辨率': (['1K', '2K', '4K'], {'default': '1K'}),
                             '宽度': ('INT', {'default': 1024, 'min': 16, 'max': 16384, 'step': 16}),
                             '高度': ('INT', {'default': 1024, 'min': 16, 'max': 16384, 'step': 16}),
                             '模型': ('STRING', {'default': '自动（2.5优先）'}),
                             '质量': (['省费', '标准', '精细', '自动'], {'default': '省费'}),
                             '重新生成编号': ('INT', {'default': 0, 'min': 0, 'max': 2147483647})},
                'optional': {'参考图集': ('CODEX_REFERENCES', {'lazy': True})}}

    RETURN_TYPES = ('IMAGE', 'STRING')
    RETURN_NAMES = ('BBAPI生成图', 'BBAPI报告')
    FUNCTION = 'generate'
    CATEGORY = 'Codex/图像工作台'
    OUTPUT_NODE = False
    DESCRIPTION = '每次固定生成一张、并发一张；相同输入复用BBAPI已有结果，递增重新生成编号才请求新图。'

    def check_lazy_status(self, 生成模式='文生图', **kwargs):
        return ['参考图集'] if _mode(生成模式) == '图生图' and '参考图集' in kwargs and kwargs['参考图集'] is None else []

    def generate(self, 连接, 提示词='', 生成模式='文生图', 分辨率='1K', 宽度=1024, 高度=1024,
                 模型='自动（2.5优先）', 质量='省费', 重新生成编号=0, 参考图集=None):
        _mode(生成模式)
        if not isinstance(提示词, str) or not 提示词.strip():
            raise ValueError('请填写BBAPI生图提示词。')
        if type(宽度) is not int or type(高度) is not int or not 16 <= 宽度 <= 16384 or not 16 <= 高度 <= 16384:
            raise ValueError('画布宽高应在16～16384之间。')
        if 分辨率 not in ('1K', '2K', '4K') or 质量 not in ('省费', '标准', '精细', '自动'):
            raise ValueError('请选择有效的BBAPI分辨率与质量。')
        if type(重新生成编号) is not int or not 0 <= 重新生成编号 <= 2147483647:
            raise ValueError('重新生成编号应为非负整数。')
        backend = nodes.NODE_CLASS_MAPPINGS.get('BBAPIMultiReference')
        module = sys.modules.get(getattr(backend, '__module__', ''))
        encode = getattr(module, '_tensor_references', None)
        if backend is None or not callable(getattr(backend, '_run', None)) or not callable(encode):
            raise RuntimeError('缺少已安装的BBAPI私有生图节点，请检查节点加载状态。')
        references = []
        if 生成模式 == '图生图':
            images = (参考图集 or {}).get('images', [])
            if not 1 <= len(images) <= 16:
                raise ValueError('BBAPI图生图需要1～16张参考图。')
            for position, image in enumerate(images, 1):
                references.extend(encode(_image(image, f'参考图{position}'), 1))
            if sum(map(len, references)) > 64 * 1024 * 1024:
                raise ValueError('参考图片合计超过64MB，请减少或缩小图片。')
        prompt = re.sub(r'<image(\d+)>', lambda match: f'参考图{match.group(1)}', 提示词, flags=re.I)
        try:
            result = backend._run(连接=连接, 模型=模型, 提示词=prompt, 分辨率=分辨率,
                                  比例=f'{宽度}:{高度}', 质量=质量, 批量数量=1, 并发上限=1,
                                  references=references, 输出模式='预览（不保存）', 重新生成编号=重新生成编号)
        except Exception as exc:
            raise RuntimeError(_bbapi_safe_text(exc, 连接)) from None
        values = _outputs(result)
        report = _bbapi_safe_text(values[1], 连接)
        image = values[2]
        if image is None:
            raise RuntimeError(report or 'BBAPI没有返回图片；本次未自动重新提交。')
        _image(image, 'BBAPI结果')
        ui = dict(result.get('ui', {})) if isinstance(result, dict) else {}
        ui['text'] = [report]
        return {'ui': ui, 'result': (image, report)}


class CodexQwenStudioReferences:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'参考图库': ('STRING', {'default': '[]'}),
                             '生成模式': ('STRING', {'default': '文生图', 'forceInput': True})},
                'optional': {**{f'参考图{i}': ('IMAGE', {'lazy': True}) for i in range(1, 17)},
                             '对比参考图编号': ('INT', {'default': 1, 'min': 1, 'max': 16})}}

    RETURN_TYPES = ('CODEX_REFERENCES', 'IMAGE')
    RETURN_NAMES = ('参考图集合', '所选参考图')
    FUNCTION = 'collect'
    CATEGORY = 'Codex/Qwen工作台'

    @classmethod
    def IS_CHANGED(cls, 参考图库='[]', 生成模式='文生图', **kwargs):
        _mode(生成模式)
        stamps = []
        for entry in _library(参考图库):
            stamps.append([entry['id'], *_reference_stamp(entry)])
        return _cache_key(生成模式, 参考图库, stamps)

    def check_lazy_status(self, 生成模式='文生图', **kwargs):
        if _mode(生成模式) == '文生图':
            return []
        return [f'参考图{i}' for i in range(1, 17)
                if f'参考图{i}' in kwargs and kwargs[f'参考图{i}'] is None]

    def collect(self, 参考图库='[]', 生成模式='文生图', 对比参考图编号=1, **kwargs):
        if _mode(生成模式) == '文生图':
            return (_reference_metadata(_library(参考图库), kwargs, 对比参考图编号), None)
        entries = _library(参考图库)
        known = {entry['id'] for entry in entries}
        for i in range(1, 17):
            if kwargs.get(f'参考图{i}') is not None and f'ref{i}' not in known:
                entries.append({'id': f'ref{i}', 'name': f'参考图{i}', 'type': 'external'})
        if len(entries) > 16:
            raise ValueError('上传与外接参考图合计最多16张，请移除多余图片。')
        if not entries:
            raise ValueError('图生图需要至少一张参考图；可上传图片或连接参考图输入。')
        images, labels, records, slots = [], [], [], {}
        for position, entry in enumerate(entries, 1):
            number = int(entry['id'][3:])
            external = kwargs.get(f'参考图{number}')
            value = _image(external, f'参考图{number}') if external is not None else _file_image(entry)
            images.append(value)
            labels.append(entry['name'])
            records.append({**entry, 'slot': position, 'external': external is not None})
            slots[entry['id']] = position
        if type(对比参考图编号) is not int or not 1 <= 对比参考图编号 <= len(images):
            raise ValueError('请选择已启用的对比参考图编号。')
        return ({'images': images, 'labels': labels, 'entries': records, 'image_slots': slots,
                 'primary_slot': 对比参考图编号, 'primary_id': records[对比参考图编号 - 1]['id']},
                images[对比参考图编号 - 1])


class CodexQwenStudioEncoder:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'clip': ('CLIP',), 'vae': ('VAE',),
                             'prompt': ('STRING', {'default': '', 'multiline': True}),
                             'negative_prompt': ('STRING', {'default': '', 'multiline': True}),
                             'resolution': ('INT', {'default': 1024, 'min': 0, 'max': 4096, 'step': 32}),
                             '生成模式': ('STRING', {'default': '文生图', 'forceInput': True})},
                'optional': {'参考图集': ('CODEX_REFERENCES', {'lazy': True})}}

    RETURN_TYPES = ('CONDITIONING', 'CONDITIONING', 'LATENT')
    RETURN_NAMES = ('正面条件', '负面条件', '参考尺寸空潜空间')
    FUNCTION = 'encode'
    CATEGORY = 'Codex/Qwen工作台'

    def check_lazy_status(self, 生成模式='文生图', **kwargs):
        return ['参考图集'] if _mode(生成模式) == '图生图' and '参考图集' in kwargs and kwargs['参考图集'] is None else []

    def encode(self, clip, vae, prompt='', negative_prompt='', resolution=1024, 生成模式='文生图', 参考图集=None):
        images = (参考图集 or {}).get('images', []) if _mode(生成模式) == '图生图' else []
        if 生成模式 == '图生图' and not 1 <= len(images) <= 16:
            raise ValueError('图生图需要1～16张参考图。')
        cls = nodes.NODE_CLASS_MAPPINGS.get('TextEncodeQwenImage21')
        if cls is None:
            raise RuntimeError('缺少本机TextEncodeQwenImage21节点。')
        return _outputs(cls.execute(clip=clip, vae=vae, prompt=prompt, negative_prompt=negative_prompt,
                                    resolution=resolution, images={f'image_{i}': image for i, image in enumerate(images, 1)}))


FINISH_INPUT_SIZES = ['原图', '1K', '2K', '3K', '4K', '自定义']


def resize_input(image, 输入尺寸='1K', 输入长边=1024):
    """Bound pre-processing input; the selected upscaler still applies its full factor."""
    if 输入尺寸 not in FINISH_INPUT_SIZES:
        raise ValueError('请选择原图、1K、2K、3K、4K或自定义输入尺寸。')
    if 输入尺寸 == '原图':
        return image
    if 输入尺寸 == '自定义' and (type(输入长边) is not int or not 256 <= 输入长边 <= 4096 or 输入长边 % 32):
        raise ValueError('处理前输入长边应为256～4096的32倍数。')
    limit = {'1K': 1024, '2K': 2048, '3K': 3072, '4K': 4096}.get(输入尺寸, 输入长边)
    if not torch.is_tensor(image) or image.ndim != 4 or min(image.shape[1:3]) < 1:
        raise ValueError('处理前输入需要有效IMAGE。')
    height, width = map(int, image.shape[1:3])
    if max(width, height) <= limit:
        return image
    scale = limit / max(width, height)

    def aligned(value, original):
        size = max(1, int(value * scale))
        if size >= 32:
            size = max(32, size // 32 * 32)
        return min(original, limit, size)

    target_width, target_height = aligned(width, width), aligned(height, height)
    from comfy.utils import common_upscale
    return common_upscale(image.movedim(-1, 1), target_width, target_height,
                          'lanczos', 'disabled').movedim(1, -1)


def input_dimensions(original, prepared, **extra):
    return {'original_width': int(original.shape[2]), 'original_height': int(original.shape[1]),
            'width': int(prepared.shape[2]), 'height': int(prepared.shape[1]), **extra}


class CodexQwenStudioRepair:
    @classmethod
    def INPUT_TYPES(cls):
        samplers = list(nodes.KSampler.INPUT_TYPES()['required']['sampler_name'][0])
        schedulers = list(nodes.KSampler.INPUT_TYPES()['required']['scheduler'][0])
        sampling = lambda steps: {
            '步数': ('INT', {'default': steps, 'min': 1, 'max': 100}),
            'CFG': ('FLOAT', {'default': 1.0, 'min': 0.0, 'max': 30.0, 'step': 0.1}),
            '采样器': (samplers, {'default': 'euler' if 'euler' in samplers else samplers[0]}),
            '调度器': (schedulers, {'default': 'simple' if 'simple' in schedulers else schedulers[0]}),
            '降噪': ('FLOAT', {'default': 0.3, 'min': 0.0, 'max': 1.0, 'step': 0.01,
                            'tooltip': '从原图潜空间低降噪修复，建议0.15～0.4；越高改动越大，0仅调整输出尺寸。'}),
        }
        optional = {
            'Image2配置': ('H3_IMAGE_SETTINGS', {'lazy': True}),
            '使用外接配置': ('BOOLEAN', {'default': False, 'tooltip': '关闭时使用修复独立参数；启用时面板调整连接的图像模型配置，模型和降噪均以外接配置为准。'}),
            'Image2模型': ('STRING', {'default': 'qwen-image-2.1-UC-Q6_K.gguf'}),
            'Image2编码器': ('STRING', {'default': 'qwen3vl_8b_int8_convrot.safetensors'}),
            'Image2VAE': ('STRING', {'default': 'qwen_image_2.1_vae_bf16.safetensors'}),
            **{'Image2'+name: spec for name, spec in sampling(25).items()},
            'Image2缓存设备': (['auto', 'gpu', 'cpu', 'off'], {'default': 'auto'}),
            'Image2缓存精度': (['default', 'int8', 'int4'], {'default': 'default'}),
            'Image2编码器设备': (['default', 'cpu'], {'default': 'default'}),
            'Krea模型': ('STRING', {'default': 'krea2_turbo_fp8_scaled.safetensors'}),
            'Krea编码器': ('STRING', {'default': 'qwen3vl_4b_fp8_scaled.safetensors'}),
            'KreaVAE': ('STRING', {'default': 'qwen_image_vae.safetensors'}),
            **{'Krea'+name: spec for name, spec in sampling(8).items()},
            'Krea编码器设备': (['default', 'cpu'], {'default': 'default'}),
            '负面提示词': ('STRING', {'default': '', 'multiline': True}),
            '输入尺寸': (FINISH_INPUT_SIZES, {'default': '2K', 'tooltip': '修复前仅等比缩小超过上限的输入；小图不会放大。'}),
            '输入长边': ('INT', {'default': 2048, 'min': 256, 'max': 4096, 'step': 32}),
            '输出尺寸': (['跟随输入', '原图', '1K', '2K', '自定义'], {'default': '跟随输入',
                       'tooltip': '跟随输入采用限长边后的尺寸；原图采用最初输入尺寸；1K/2K按原比例设置修复画布。'}),
            '输出长边': ('INT', {'default': 1024, 'min': 256, 'max': 4096, 'step': 32}),
            'DLSS倍率': ([1, 2], {'default': 2, 'tooltip': '先以所选倍率保真增强，再按修复输出尺寸等比适配；不会追加VOSR或扩散重绘。'}),
            'DLSS强度': ('FLOAT', {'default': 1.0, 'min': 0.0, 'max': 2.0, 'step': 0.05}),
            'DLSS色调': ('FLOAT', {'default': 1.0, 'min': 0.0, 'max': 2.0, 'step': 0.05}),
            'DLSS结构': ('FLOAT', {'default': 1.5, 'min': 0.0, 'max': 2.0, 'step': 0.05}),
            'DLSS皮肤': ('FLOAT', {'default': -1.0, 'min': -1.0, 'max': 2.0, 'step': 0.05}),
            'DLSS细节': ('FLOAT', {'default': 1.0, 'min': 0.0, 'max': 2.0, 'step': 0.05}),
            'DLSS色彩': ('FLOAT', {'default': 1.0, 'min': 0.0, 'max': 1.0, 'step': 0.05}),
        }
        return {'required': {'图像': ('IMAGE',), '启用修复': ('BOOLEAN', {'default': False}),
                             '修复引擎': (['Image2.1', 'Krea 2', 'DLSS5'], {'default': 'Image2.1'}),
                             '修复规则': ('STRING', {'default': REPAIR_RULE, 'multiline': True}),
                             '补充要求': ('STRING', {'default': '', 'multiline': True}),
                             '种子': ('INT', {'default': 42, 'min': 0, 'max': 2**32 - 1})}, 'optional': optional}

    RETURN_TYPES = ('IMAGE', 'BOOLEAN')
    RETURN_NAMES = ('修复或直通图像', '修复已启用')
    FUNCTION = 'repair'
    CATEGORY = 'Codex/Qwen工作台'
    DESCRIPTION = 'Image2.1低降噪编辑、Krea 2低降噪重绘或DLSS5保真增强。DLSS5不使用扩散模型或修复提示词；关闭时不读取或加载修复权重。'

    def check_lazy_status(self, 启用修复=False, 修复引擎='Image2.1', 使用外接配置=False, **kwargs):
        return ['Image2配置'] if (启用修复 and 修复引擎 == 'Image2.1' and 使用外接配置 and
                                  'Image2配置' in kwargs and kwargs['Image2配置'] is None) else []

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        if not kwargs.get('启用修复'):
            return _cache_key('qwen-studio-repair-v4-disabled')
        engine = kwargs.get('修复引擎', 'Image2.1')
        if engine == 'DLSS5':
            from .reference_upscale import _identity
            return _cache_key('qwen-studio-repair-dlss-v1', kwargs,
                              _identity(Path(__file__).with_name('isolated_dlss.py'), True),
                              _identity(Path(__file__).with_name('dlss_worker.py'), True))
        if engine == 'Image2.1' and kwargs.get('使用外接配置'):
            from .image_bridge import normalize_settings
            cfg = normalize_settings(kwargs.get('Image2配置'))
            names = [('unet' if cfg['model'].lower().endswith('.gguf') else 'diffusion_models', cfg['model']),
                     ('text_encoders', cfg['clip']), ('vae', cfg['vae'])]
        else:
            prefix = 'Krea' if engine == 'Krea 2' else 'Image2'
            defaults = ('krea2_turbo_fp8_scaled.safetensors', 'qwen3vl_4b_fp8_scaled.safetensors', 'qwen_image_vae.safetensors') if prefix == 'Krea' else ('qwen-image-2.1-UC-Q6_K.gguf', 'qwen3vl_8b_int8_convrot.safetensors', 'qwen_image_2.1_vae_bf16.safetensors')
            model, clip, vae = [kwargs.get(prefix+name, default) for name, default in zip(('模型', '编码器', 'VAE'), defaults)]
            names = [('unet' if model.lower().endswith('.gguf') else 'diffusion_models', model), ('text_encoders', clip), ('vae', vae)]
        return _cache_key('qwen-studio-repair-v4', kwargs, _model_stamps(names))

    def repair(self, 图像, 启用修复=False, 修复引擎='Image2.1', 修复规则=REPAIR_RULE, 补充要求='', 种子=42,
               Image2配置=None, 使用外接配置=False, Image2模型='qwen-image-2.1-UC-Q6_K.gguf',
               Image2编码器='qwen3vl_8b_int8_convrot.safetensors', Image2VAE='qwen_image_2.1_vae_bf16.safetensors',
               Image2步数=25, Image2CFG=1.0, Image2采样器='euler', Image2调度器='simple', Image2降噪=0.3,
               Image2缓存设备='auto', Image2缓存精度='default', Image2编码器设备='default',
               Krea模型='krea2_turbo_fp8_scaled.safetensors', Krea编码器='qwen3vl_4b_fp8_scaled.safetensors',
               KreaVAE='qwen_image_vae.safetensors', Krea步数=8, KreaCFG=1.0, Krea采样器='euler',
               Krea调度器='simple', Krea降噪=0.3, Krea编码器设备='default', 负面提示词='',
               输入尺寸='2K', 输入长边=2048, 输出尺寸='跟随输入', 输出长边=1024,
               DLSS倍率=2, DLSS强度=1.0, DLSS色调=1.0, DLSS结构=1.5,
               DLSS皮肤=-1.0, DLSS细节=1.0, DLSS色彩=1.0, **legacy):
        if not 启用修复:
            return (图像, False)
        _image(图像, '修复输入')
        if 修复引擎 not in ('Image2.1', 'Krea 2', 'DLSS5'):
            raise ValueError('请选择Image2.1、Krea 2或DLSS5修复引擎；MOSS修复已移除。')
        original = 图像
        prepared = resize_input(图像, 输入尺寸, 输入长边)
        dimensions = input_dimensions(original, prepared)
        width, height = self._canvas(original, prepared, 输出尺寸, 输出长边)
        dimensions.update(output_width=width, output_height=height, output_size=输出尺寸)
        if 修复引擎 == 'DLSS5':
            return self._dlss_repair(prepared, width, height, dimensions,
                                     DLSS倍率, DLSS强度, DLSS色调, DLSS结构,
                                     DLSS皮肤, DLSS细节, DLSS色彩)
        from .image_bridge import normalize_settings
        if 修复引擎 == 'Image2.1':
            cfg = normalize_settings(Image2配置 if 使用外接配置 else {
                'model': Image2模型, 'clip': Image2编码器, 'vae': Image2VAE, 'steps': Image2步数, 'cfg': Image2CFG,
                'sampler': Image2采样器, 'scheduler': Image2调度器, 'denoise': Image2降噪,
                'cache_device': Image2缓存设备, 'cache_dtype': Image2缓存精度, 'clip_device': Image2编码器设备})
            # Retain the prepared reference budget even when requesting a smaller output.
            cfg['reference_long_edge'] = max(32, min(4096, ((max(prepared.shape[1:3])+31)//32)*32))
            names = [('unet' if cfg['model'].lower().endswith('.gguf') else 'diffusion_models', cfg['model']),
                     ('text_encoders', cfg['clip']), ('vae', cfg['vae'])]
        else:
            cfg = {'model': Krea模型, 'clip': Krea编码器, 'vae': KreaVAE, 'steps': Krea步数, 'cfg': KreaCFG,
                   'sampler': Krea采样器, 'scheduler': Krea调度器, 'denoise': Krea降噪, 'clip_device': Krea编码器设备}
            names = [('diffusion_models', Krea模型), ('text_encoders', Krea编码器), ('vae', KreaVAE)]
        import math
        if (type(cfg['steps']) is not int or not 1 <= cfg['steps'] <= 100 or
                not math.isfinite(float(cfg['cfg'])) or not 0 <= float(cfg['cfg']) <= 30 or
                not math.isfinite(float(cfg['denoise'])) or not 0 <= float(cfg['denoise']) <= 1):
            raise ValueError('修复步数需为1～100，CFG为0～30，降噪为0～1。')
        if cfg['sampler'] not in nodes.KSampler.INPUT_TYPES()['required']['sampler_name'][0] or cfg['scheduler'] not in nodes.KSampler.INPUT_TYPES()['required']['scheduler'][0]:
            raise ValueError('请选择本机支持的修复采样器与调度器。')
        dimensions.update(engine=修复引擎, denoise=float(cfg['denoise']))
        rgb = prepared[:, :, :, :3]
        from comfy.utils import common_upscale
        if tuple(rgb.shape[1:3]) != (height, width):
            rgb = common_upscale(rgb.movedim(-1, 1), width, height, 'lanczos', 'disabled').movedim(1, -1)
        if float(cfg['denoise']) == 0:
            return self._preview(rgb, dimensions=dimensions)
        prompt = '\n'.join(text for text in (修复规则, 补充要求) if text.strip())
        if not prompt:
            raise ValueError('请填写修复规则或补充要求。')
        settings = {'engine': 修复引擎, 'rules': 修复规则, 'extra': 补充要求, 'negative': 负面提示词,
                    'seed': 种子, 'config': cfg, 'input': [输入尺寸, 输入长边], 'output': [width, height]}
        key = _cache_key('qwen-studio-repair-v5', prepared, settings, _model_stamps(names))
        cached = _cached_image(key)
        if cached:
            return self._preview(load_tensor(cached), dimensions=dimensions)
        if 修复引擎 == 'Image2.1':
            from .image_bridge import generate_reference
            path = tensor_path(prepared)
            try:
                result = generate_reference(prompt, str(path), width, height, seed=种子, settings=cfg,
                                            init_from_reference=True, negative_prompt=负面提示词)
                image = load_tensor(result)
            finally:
                path.unlink(missing_ok=True)
        else:
            for category, name in names:
                _krea_weight(category, name)
            if cfg['clip_device'] not in ('default', 'cpu'):
                raise ValueError('请选择默认或CPU编码器设备。')
            clip_types = nodes.CLIPLoader.INPUT_TYPES()['required']['type'][0]
            if 'krea2' not in clip_types:
                raise RuntimeError('当前ComfyUI核心不支持Krea 2，请先更新核心。')
            handles = {}
            release_resources(full=True)
            try:
                handles['model'] = nodes.UNETLoader().load_unet(cfg['model'], 'default')[0]
                handles['clip'] = nodes.CLIPLoader().load_clip(cfg['clip'], 'krea2', cfg['clip_device'])[0]
                handles['vae'] = nodes.VAELoader().load_vae(cfg['vae'])[0]
                positive = nodes.CLIPTextEncode().encode(handles['clip'], prompt)[0]
                negative = nodes.CLIPTextEncode().encode(handles['clip'], 负面提示词)[0] if 负面提示词.strip() else _call('ConditioningZeroOut', conditioning=positive)[0]
                latent = nodes.VAEEncode().encode(handles['vae'], rgb)[0]
                samples = nodes.KSampler().sample(handles['model'], 种子, cfg['steps'], cfg['cfg'], cfg['sampler'],
                                                  cfg['scheduler'], positive, negative, latent, denoise=cfg['denoise'])[0]
                image = nodes.VAEDecode().decode(handles['vae'], samples)[0]
                del positive, negative, latent, samples
            finally:
                handles.clear()
                release_resources(full=True)
        if tuple(image.shape[1:3]) != (height, width):
            raise RuntimeError(f'修复输出尺寸异常：预期{width}×{height}，实际{image.shape[2]}×{image.shape[1]}。')
        return self._preview(load_tensor(_save_cached(image, key)), dimensions=dimensions)

    @classmethod
    def _dlss_repair(cls, image, width, height, dimensions, scale, intensity, tone, structure, skin, detail, color):
        import inspect
        import math
        import uuid
        import comfy.model_management as mm
        from comfy.utils import common_upscale
        from .reference_upscale import _node, _identity, _size, DLSS_MODES
        from .isolated_dlss import run_dlss
        if type(scale) is not int or scale not in (1, 2):
            raise ValueError('DLSS5修复增强倍率请选择1或2。')
        for label, value, low, high in (('强度', intensity, 0, 2), ('色调', tone, 0, 2),
                                        ('结构', structure, 0, 2), ('皮肤', skin, -1, 2),
                                        ('细节', detail, 0, 2), ('色彩', color, 0, 1)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f'DLSS5{label}需为{low}～{high}的有限数值。')
        node = _node('RH_DLSS5Enhance')
        plugin = Path(inspect.getfile(node)).parent
        module = sys.modules.get(node.__module__)
        common = getattr(module, 'common', None)
        if common is None:
            raise RuntimeError('DLSS5接口不兼容，请检查已安装的DLSS5节点。')
        runtime = Path(common.resolve_runtime_dir(''))
        common.check_runtime_files(runtime, scale)
        files = sorted(runtime.glob('*.dll')) + sorted((runtime / 'caller').glob('*.dll'))
        files.append(plugin / 'native/bin/dlss5nr_bridge.dll')
        implementation = [_identity(inspect.getfile(node), True),
                          _identity(Path(__file__).with_name('isolated_dlss.py'), True),
                          _identity(Path(__file__).with_name('dlss_worker.py'), True)]
        implementation += [_identity(path, True) for path in sorted((plugin / 'dlss5nr').glob('*.py'))]
        implementation += [_identity(path) for path in files]
        kwargs = {'upscaling_mode': DLSS_MODES[str(scale)], 'style': 'default', 'preset': 0,
                  'intensity': float(intensity), 'tone': float(tone), 'structure': float(structure),
                  'skin': float(skin), 'auto_mask': 'on', 'motion': 'auto', 'scene_change_threshold': 0.24,
                  'batch_mode': 'temporal sequence', 'warmup_frames': 1, 'keep_audio': 'on',
                  'backend': 'windows-bridge', 'channel_order': 'auto', 'hdr': 'off',
                  'global_tone': -1.0, 'detail': float(detail), 'color': float(color), 'runtime_dir': '', 'wine_prefix': ''}
        key = _cache_key('qwen-studio-repair-dlss-v1', image,
                         {'engine':'DLSS5','config':kwargs,'output':[width,height]}, implementation)
        dimensions.update(engine='DLSS5', enhancement_scale=scale, diffusion=False,
                          enhancement_width=int(image.shape[2])*scale, enhancement_height=int(image.shape[1])*scale)
        cached = _cached_image(key)
        if cached:
            dimensions['cache_hit'] = True
            return cls._preview(load_tensor(cached), dimensions=dimensions)
        source = tensor_path(image[:, :, :, :3])
        destination = source.with_name(source.stem + '-dlss-' + uuid.uuid4().hex + '.png')
        enhanced = None
        release_resources(full=True)
        try:
            run_dlss(source, destination, kwargs, plugin, mm.throw_exception_if_processing_interrupted)
            expected = (int(image.shape[2])*scale, int(image.shape[1])*scale)
            actual = _size(destination)
            if actual != expected:
                raise RuntimeError(f'DLSS5增强尺寸异常：预期{expected[0]}×{expected[1]}，实际{actual[0]}×{actual[1]}。')
            enhanced = load_tensor(destination)
            if not bool(torch.isfinite(enhanced).all()):
                raise RuntimeError('DLSS5增强返回无效像素，未保存结果。')
            if tuple(enhanced.shape[1:3]) != (height, width):
                enhanced = common_upscale(enhanced.movedim(-1,1), width, height, 'lanczos', 'disabled').movedim(1,-1)
            if tuple(enhanced.shape[1:3]) != (height, width):
                raise RuntimeError('DLSS5修复输出尺寸不符，未保存结果。')
            dimensions['cache_hit'] = False
            return cls._preview(load_tensor(_save_cached(enhanced, key)), dimensions=dimensions)
        finally:
            source.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)
            enhanced = None
            release_resources(full=True)

    @staticmethod
    def _canvas(original, prepared, size, edge):
        if size not in ('跟随输入', '原图', '1K', '2K', '自定义'):
            raise ValueError('请选择跟随输入、原图、1K、2K或自定义修复输出尺寸。')
        height, width = map(int, (original if size == '原图' else prepared).shape[1:3])
        if size in ('1K', '2K', '自定义'):
            if size == '自定义' and (type(edge) is not int or not 256 <= edge <= 4096 or edge % 32):
                raise ValueError('修复输出长边应为256～4096的32倍数。')
            limit = {'1K': 1024, '2K': 2048}.get(size, edge)
            scale = limit / max(width, height)
            width, height = width*scale, height*scale
        width, height = [max(64, round(value/32)*32) for value in (width, height)]
        if max(width, height) > 4096:
            raise ValueError('修复输出最长边最多4096；请选择1K、2K或自定义输出尺寸。')
        return width, height

    @staticmethod
    def _preview(image, enabled=True, dimensions=None):
        result = CodexImageOutput().execute(image, '预览（不保存）', 'Qwen工作台/修复预览')
        ui = {'images': result['ui']['images']}
        if dimensions is not None:
            ui['codex_input_dimensions'] = [dimensions]
        return {'ui': ui, 'result': (image, enabled)}


NODE_CLASS_MAPPINGS = {cls.__name__: cls for cls in
                       (CodexQwenMode, CodexQwenStudioReferences, CodexQwenStudioEncoder, CodexQwenStudioRepair,
                        CodexStudioLora, CodexStudioDenoise, CodexStudioMossPrompt, CodexStudioBBAPI,
                        CodexStudioCanvas, CodexStudioKreaModel, CodexStudioKreaGenerate)}
NODE_DISPLAY_NAME_MAPPINGS = {
    'CodexQwenMode': '生成模式与模型路线',
    'CodexQwenStudioReferences': '参考图库',
    'CodexQwenStudioEncoder': 'Qwen文生图与多图编辑编码',
    'CodexQwenStudioRepair': '修复 · Image2.1 / Krea 2 / DLSS5',
    'CodexStudioLora': '通用LoRA · 匹配当前模型基座',
    'CodexStudioDenoise': '生成模式降噪',
    'CodexStudioMossPrompt': 'MOSS主参考图重绘提示词',
    'CodexStudioBBAPI': 'BBAPI文生图与多图编辑',
    'CodexStudioCanvas': '画布比例与参考图尺寸',
    'CodexStudioKreaModel': 'Krea 2 Turbo · 按需加载模型',
    'CodexStudioKreaGenerate': 'Krea 2 Turbo · 文生图 / 单图重绘',
}
