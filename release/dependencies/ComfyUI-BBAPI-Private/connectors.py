"""Ordered native IMAGE inputs and resumable sequential folder requests."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import os
from .nodes import _ImageBase, _tensor_references, _image_to_tensor, _cancelled
from .client import decode_image, encode_reference
import folder_paths


class BBAPIReferenceInputs:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'参考图数量': ('INT', {'default': 1, 'min': 1, 'max': 16})},
                'optional': {f'参考图{i}': ('IMAGE', {'lazy': True}) for i in range(1,17)}}
    RETURN_TYPES = ('BBAPI_REFERENCES','IMAGE')
    RETURN_NAMES = ('参考图集合','参考图逐张')
    OUTPUT_IS_LIST = (False,True)
    FUNCTION = 'collect'
    CATEGORY = 'BBAPI私有/输入'

    def check_lazy_status(self, 参考图数量=1, **kwargs):
        return [f'参考图{i}' for i in range(1,参考图数量+1) if f'参考图{i}' in kwargs and kwargs[f'参考图{i}'] is None]

    def collect(self, 参考图数量=1, **kwargs):
        encoded, tensors = [], []
        for i in range(1,参考图数量+1):
            image = kwargs.get(f'参考图{i}')
            if image is None or len(image) != 1:
                raise ValueError(f'参考图{i}请连接一张原生IMAGE图片；编号对应端口顺序。')
            encoded.extend(_tensor_references(image,1));tensors.append(image)
        return encoded,tensors


class BBAPIReferenceSelect:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'参考图': ('BBAPI_REFERENCES',), '编号': ('INT', {'default':1,'min':1,'max':16})}}
    RETURN_TYPES = ('IMAGE',)
    RETURN_NAMES = ('所选参考图',)
    FUNCTION = 'select'
    CATEGORY = 'BBAPI私有/输入'
    def select(self, 参考图, 编号=1):
        if not 1 <= 编号 <= len(参考图):raise ValueError('该编号参考图不存在。')
        return (_image_to_tensor(decode_image(参考图[编号-1])),)


class BBAPIFolderBatch(_ImageBase):
    RETURN_TYPES = ('IMAGE','STRING')
    RETURN_NAMES = ('生成图逐张','批处理报告')
    OUTPUT_IS_LIST = (True,False)
    @classmethod
    def INPUT_TYPES(cls):
        fields = cls._required()
        fields.pop('批量数量');fields.pop('并发上限')
        fields.update({'文件夹':('STRING',{'default':''}),
                       '最多处理':('INT',{'default':100,'min':1,'max':500}),
                       '继续已有结果':('BOOLEAN',{'default':True}),
                       '重试未确认请求':('BOOLEAN',{'default':False,'tooltip':'仅核对站点确认未扣费后开启，避免重复提交。'})})
        return {'required':fields,'optional':{'固定参考图':('BBAPI_REFERENCES',)}}
    def generate(self, 文件夹, 最多处理=100, 继续已有结果=True, 重试未确认请求=False, 固定参考图=None, **kwargs):
        root=Path(文件夹).expanduser().resolve()
        if not root.is_dir():raise ValueError('请选择存在的图片文件夹。')
        fixed=固定参考图 or []
        if len(fixed)>15:raise ValueError('固定参考最多15张，参考图1留给当前处理图片。')
        files=sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() in {'.png','.jpg','.jpeg','.webp','.bmp'})[:最多处理]
        if not files:raise ValueError('文件夹内没有可处理的图片。')
        directory=Path(folder_paths.get_output_directory())/'BBAPI'/'folder-batches';directory.mkdir(parents=True,exist_ok=True)
        settings={key:value for key,value in kwargs.items() if key!='连接'}
        settings.update(profile=kwargs['连接'].profile,base_url=kwargs['连接'].base_url,default_model=kwargs['连接'].default_model,
                        fixed=[hashlib.sha256(value).hexdigest() for value in fixed])
        outputs, reports=[],[]
        from comfy.utils import ProgressBar
        progress=ProgressBar(len(files))
        for index,path in enumerate(files):
            if _cancelled():raise InterruptedError('已停止批处理，完成的结果已保存。')
            data=path.read_bytes();source=decode_image(data)
            key=hashlib.sha256(data+json.dumps(settings,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
            record=directory/(key+'.json');cached=directory/(key+'.png')
            if 继续已有结果 and record.is_file():
                previous=json.loads(record.read_text(encoding='utf-8'))
                if previous.get('status')=='completed' and cached.is_file() and hashlib.sha256(cached.read_bytes()).hexdigest()==previous.get('sha256'):
                    outputs.append(_image_to_tensor(decode_image(cached.read_bytes())));reports.append(f'{path.name}：复用');progress.update_absolute(index+1,len(files));continue
                if previous.get('status')=='submitted' and not 重试未确认请求:
                    raise RuntimeError(f'{path.name} 上次请求状态未确认，请先核对站点；本次没有自动重提。')
            def store(value):
                temp=record.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8');os.replace(temp,record)
            store({'status':'submitted','source':path.name})
            prompt=kwargs['提示词']
            if fixed:prompt='参考图1是当前要处理的图片；参考图2及之后为固定素材，按指定角色使用。\n'+prompt
            try:result=self._run(**{**kwargs,'提示词':prompt},批量数量=1,并发上限=1,references=[encode_reference(source),*fixed])
            except ValueError:
                store({'status':'failed_validation','source':path.name});raise
            if not result['result'][0]:raise RuntimeError(result['result'][1])
            tensor=result['result'][0][0];raw=result['result'][3][0]
            cached.write_bytes(raw);store({'status':'completed','source':path.name,'sha256':hashlib.sha256(raw).hexdigest()})
            outputs.append(tensor);reports.append(f'{path.name}：完成');progress.update_absolute(index+1,len(files))
        return {'ui':{'text':['\n'.join(reports)]},'result':(outputs,'\n'.join(reports))}


NODE_CLASS_MAPPINGS={'BBAPIReferenceInputs':BBAPIReferenceInputs,'BBAPIReferenceSelect':BBAPIReferenceSelect,'BBAPIFolderBatch':BBAPIFolderBatch}
NODE_DISPLAY_NAME_MAPPINGS={'BBAPIReferenceInputs':'参考图接口 · 按需展开1～16','BBAPIReferenceSelect':'参考图编号选择 · 对比输出','BBAPIFolderBatch':'BBAPI 文件夹逐张处理'}
