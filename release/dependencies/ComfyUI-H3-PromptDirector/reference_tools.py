"""Ordered, lazy reference slots shared by Qwen editing and canvas sizing."""
from __future__ import annotations
import torch
import nodes


class CodexReferenceInputs:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'启用参考图数量':('INT',{'default':1,'min':0,'max':16})},
                'optional':{**{f'参考图{i}':('IMAGE',{'lazy':True}) for i in range(1,17)},
                            '对比参考图编号':('INT',{'default':1,'min':1,'max':16})}}
    RETURN_TYPES=('CODEX_REFERENCES','IMAGE')
    RETURN_NAMES=('参考图集合','所选参考图')
    FUNCTION='collect'
    CATEGORY='Codex/图像工具'

    def check_lazy_status(self,启用参考图数量=1,**kwargs):
        return [f'参考图{i}' for i in range(1,启用参考图数量+1)
                if f'参考图{i}' in kwargs and kwargs[f'参考图{i}'] is None]

    def collect(self,启用参考图数量=1,对比参考图编号=1,**kwargs):
        if type(启用参考图数量) is not int or not 0<=启用参考图数量<=16:
            raise ValueError('启用参考图数量应为0～16。')
        images=[]
        for index in range(1,启用参考图数量+1):
            image=kwargs.get(f'参考图{index}')
            if not torch.is_tensor(image) or image.ndim!=4 or len(image)!=1:
                raise ValueError(f'请把一张IMAGE连接到参考图{index}，或降低启用数量。')
            images.append(image)
        if images and not 1<=对比参考图编号<=len(images):raise ValueError('所选对比参考图未启用，请选择已启用的编号。')
        return ({'images':images,'labels':[f'参考图{i+1}' for i in range(len(images))]},images[对比参考图编号-1] if images else None)


class CodexQwenReferenceEncoder:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'clip':('CLIP',),'vae':('VAE',),
                'prompt':('STRING',{'multiline':True}),
                'negative_prompt':('STRING',{'default':'','multiline':True}),
                'resolution':('INT',{'default':1024,'min':0,'max':4096,'step':32})},
                'optional':{'参考图集':('CODEX_REFERENCES',)}}
    RETURN_TYPES=('CONDITIONING','CONDITIONING','LATENT')
    RETURN_NAMES=('正面条件','负面条件','原图尺寸潜空间')
    FUNCTION='encode'
    CATEGORY='Codex/图像工具'

    def encode(self,clip,vae,prompt,negative_prompt='',resolution=1024,参考图集=None):
        images=(参考图集 or {}).get('images',[])
        if not 1<=len(images)<=16:raise ValueError('多图编辑需要启用1～16张参考图。')
        cls=nodes.NODE_CLASS_MAPPINGS.get('TextEncodeQwenImage21')
        if cls is None:raise RuntimeError('缺少本机TextEncodeQwenImage21节点。')
        result=cls.execute(clip=clip,vae=vae,prompt=prompt,negative_prompt=negative_prompt,
                           resolution=resolution,images={f'image_{i+1}':image for i,image in enumerate(images)})
        if hasattr(result,'result'):return result.result
        return result['result'] if isinstance(result,dict) and 'result' in result else result


class CodexQwenCanvasLatent:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'width':('INT',{'default':1024,'min':64,'max':4096,'step':32}),
                            'height':('INT',{'default':1024,'min':64,'max':4096,'step':32}),
                            'batch_size':('INT',{'default':1,'min':1,'max':16})}}
    RETURN_TYPES=('LATENT',)
    FUNCTION='create'
    CATEGORY='Codex/图像工具'
    def create(self,width=1024,height=1024,batch_size=1):
        if width%32 or height%32 or not 64<=width<=4096 or not 64<=height<=4096:
            raise ValueError('Qwen画布宽高应为64～4096的32倍数。')
        from comfy import model_management as mm
        return ({'samples':torch.zeros((batch_size,64,height//16,width//16),device=mm.intermediate_device())},)


NODE_CLASS_MAPPINGS={cls.__name__:cls for cls in (CodexReferenceInputs,CodexQwenReferenceEncoder,CodexQwenCanvasLatent)}
NODE_DISPLAY_NAME_MAPPINGS={'CodexReferenceInputs':'参考图接口 · 启用数量0～16',
                          'CodexQwenReferenceEncoder':'Qwen Image2.1 · 有序多图编码',
                          'CodexQwenCanvasLatent':'Qwen Image2.1 · 指定画布潜空间'}
