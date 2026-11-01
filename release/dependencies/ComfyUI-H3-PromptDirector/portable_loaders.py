"""Defer optional model-file checks until the selected branch actually executes."""
from __future__ import annotations
import nodes
import copy

class _Deferred:
    CATEGORY = 'BBAIGC/按需模型'
    FUNCTION = 'load'
    TARGET = ''

    @classmethod
    def VALIDATE_INPUTS(cls, **kwargs):
        # Keep the native dropdown UI while postponing literal filename enums.
        # Link types are still validated by ComfyUI; load() checks selected files.
        return True

    @classmethod
    def schema(cls, fallback):
        target = nodes.NODE_CLASS_MAPPINGS.get(cls.TARGET)
        return copy.deepcopy(target.INPUT_TYPES()) if target is not None else fallback

    def load(self, **kwargs):
        target = nodes.NODE_CLASS_MAPPINGS.get(self.TARGET)
        if target is None:
            raise RuntimeError(f'当前所选路线缺少 {self.TARGET} 节点，请按 MODELS.md 安装对应插件或更新 ComfyUI。')
        schemas = target.INPUT_TYPES()
        for group in ('required', 'optional'):
            for name, schema in schemas.get(group, {}).items():
                choices = schema[0]
                if name in kwargs and isinstance(choices, list) and kwargs[name] not in choices:
                    raise ValueError(f'{self.TARGET}.{name} 的值 {kwargs[name]!r} 不可用。请检查模型是否放入正确目录并刷新模型列表。')
        instance = target()
        return getattr(instance, instance.FUNCTION)(**kwargs)

class CodexDeferredUNETLoader(_Deferred):
    TARGET = 'UNETLoader'
    RETURN_TYPES = ('MODEL',)
    @classmethod
    def INPUT_TYPES(cls):
        return cls.schema({'required': {'unet_name': (['未安装生成模型'],), 'weight_dtype': (['default'],)}})

class CodexDeferredCLIPLoader(_Deferred):
    TARGET = 'CLIPLoader'
    RETURN_TYPES = ('CLIP',)
    @classmethod
    def INPUT_TYPES(cls):
        return cls.schema({'required': {'clip_name': (['未安装编码器'],), 'type': (['qwen_image'],)},
                           'optional': {'device': (['default', 'cpu'],)}})

class CodexDeferredVAELoader(_Deferred):
    TARGET = 'VAELoader'
    RETURN_TYPES = ('VAE',)
    @classmethod
    def INPUT_TYPES(cls):
        return cls.schema({'required': {'vae_name': (['未安装VAE'],)}})

class CodexDeferredGGUFLoader(_Deferred):
    TARGET = 'UnetLoaderGGUF'
    RETURN_TYPES = ('MODEL',)
    @classmethod
    def INPUT_TYPES(cls):
        return cls.schema({'required': {'unet_name': (['未安装GGUF模型'],)}})

NODE_CLASS_MAPPINGS = {cls.__name__: cls for cls in (
    CodexDeferredUNETLoader, CodexDeferredCLIPLoader, CodexDeferredVAELoader, CodexDeferredGGUFLoader)}
NODE_DISPLAY_NAME_MAPPINGS = {
    'CodexDeferredUNETLoader': '按需加载生成模型', 'CodexDeferredCLIPLoader': '按需加载编码器',
    'CodexDeferredVAELoader': '按需加载 VAE', 'CodexDeferredGGUFLoader': '按需加载 GGUF 模型'}
