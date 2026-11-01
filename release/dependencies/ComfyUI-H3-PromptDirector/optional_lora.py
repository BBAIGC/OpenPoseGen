"""Optional, route-aware LoRA slots for the existing Flux/Kontext workflow."""
from __future__ import annotations
import math
import folder_paths
import nodes

class CodexOptionalModelLora:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'模型': ('MODEL',), '启用LoRA': ('BOOLEAN', {'default': False}),
                             'LoRA文件': (['未选用'] + folder_paths.get_filename_list('loras'), {'default': '未选用'}),
                             'LoRA强度': ('FLOAT', {'default': 1.0, 'min': -4.0, 'max': 4.0, 'step': 0.05})},
                'optional': {'路线编号': ('INT', {'forceInput': True})}}

    RETURN_TYPES = ('MODEL',)
    RETURN_NAMES = ('模型',)
    FUNCTION = 'apply'
    CATEGORY = 'BBAIGC/LoRA'

    @classmethod
    def VALIDATE_INPUTS(cls, **kwargs):
        return True

    def apply(self, 模型, 启用LoRA=False, LoRA文件='未选用', LoRA强度=1.0, 路线编号=1):
        if not 启用LoRA or LoRA文件 == '未选用' or float(LoRA强度) == 0:
            return (模型,)
        if not math.isfinite(float(LoRA强度)) or not -4 <= float(LoRA强度) <= 4:
            raise ValueError('LoRA强度应在-4～4之间。')
        if LoRA文件 not in folder_paths.get_filename_list('loras'):
            raise ValueError('找不到所选LoRA，请放入models/loras后刷新列表。')
        if int(路线编号) == 2:
            cls = nodes.NODE_CLASS_MAPPINGS.get('NunchakuFluxLoraLoader')
            if cls is None:
                raise RuntimeError('Nunchaku路线缺少NunchakuFluxLoraLoader节点。')
            instance = cls()
            return getattr(instance, instance.FUNCTION)(model=模型, lora_name=LoRA文件, lora_strength=float(LoRA强度))
        return nodes.LoraLoaderModelOnly().load_lora_model_only(模型, LoRA文件, float(LoRA强度))

NODE_CLASS_MAPPINGS = {'CodexOptionalModelLora': CodexOptionalModelLora}
NODE_DISPLAY_NAME_MAPPINGS = {'CodexOptionalModelLora': '额外LoRA · 模型与强度'}
