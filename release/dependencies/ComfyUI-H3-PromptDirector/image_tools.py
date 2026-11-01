"""Reusable native-image canvas, description, generation and optional finishing."""
from __future__ import annotations
import base64
import io
import json
import hashlib
import re
from pathlib import Path
import tempfile
import uuid
import numpy as np
from PIL import Image
import torch
import folder_paths
import nodes
from .static_prompt import DESCRIPTION_RULE,REPAIR_RULE,static_text
from .resource_control import release_resources
from .description_cache import read_description, write_description, description_result, normalize_epoch


def _cache_key(*values):
    def normalize(value):
        if torch.is_tensor(value):
            tensor=value.detach().cpu().contiguous()
            return {'tensor_sha256':hashlib.sha256(tensor.view(torch.uint8).numpy().tobytes()).hexdigest(),
                    'shape':list(tensor.shape),'dtype':str(tensor.dtype)}
        if isinstance(value,dict):return {str(key):normalize(item) for key,item in sorted(value.items())}
        if isinstance(value,(list,tuple)):return [normalize(item) for item in value]
        return value
    return hashlib.sha256(json.dumps(normalize(values),ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()


def _cached_image(key):
    path=Path(folder_paths.get_temp_directory())/'codex-image-cache'/f'{key}.png'
    return path if path.is_file() else None


def _save_cached(image,key):
    directory=Path(folder_paths.get_temp_directory())/'codex-image-cache';directory.mkdir(parents=True,exist_ok=True)
    path=directory/f'{key}.png';temp=directory/(key+'.'+uuid.uuid4().hex+'.partial.png')
    Image.fromarray((image[0,:,:,:3].detach().cpu().clamp(0,1).numpy()*255).round().astype('uint8')).save(temp);temp.replace(path);return path


def _model_stamps(names):
    stamps=[]
    for category,name in names:
        try:path=folder_paths.get_full_path(category,name)
        except (AttributeError,KeyError):path=None
        if path and Path(path).is_file():
            stat=Path(path).stat();stamps.append([category,name,stat.st_size,stat.st_mtime_ns])
        else:stamps.append([category,name,None])
    return stamps


def _outputs(result):
    if hasattr(result,'result'):return result.result
    return result['result'] if isinstance(result,dict) and 'result' in result else result


def _call(name,**kwargs):
    cls=nodes.NODE_CLASS_MAPPINGS.get(name)
    if cls is None:raise RuntimeError(f'缺少本机节点{name}，未开始生成。')
    instance=cls();method=getattr(instance,getattr(cls,'FUNCTION','execute'))
    return _outputs(method(**kwargs))


def tensor_path(image):
    if image is None or image.ndim!=4 or len(image)!=1:raise ValueError('此入口每次处理一张图片；IMAGE列表可由Comfy逐张调度。')
    p=Path(folder_paths.get_temp_directory())/('codex-source-'+uuid.uuid4().hex+'.png')
    Image.fromarray((image[0,:,:,:3].detach().cpu().clamp(0,1).numpy()*255).round().astype('uint8')).save(p)
    return p


def load_tensor(path):
    with Image.open(path) as image:return torch.from_numpy(np.array(image.convert('RGB'),dtype=np.float32)/255)[None]


class CodexImageCanvas:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'比例':(['跟随参考图']+[f'跟随参考图{i}' for i in range(1,17)]+['1:1 方形','3:2 横','2:3 竖','4:3 横','3:4 竖','16:9 横','9:16 竖','自定义宽高'],),
                            '尺寸基准':(['最长边','最短边'],{'default':'最长边'}),
                            '长边':('INT',{'default':1024,'min':256,'max':4096,'step':32,'display_name':'目标边像素'}),
                            '自定义宽度':('INT',{'default':1024,'min':64,'max':4096,'step':32}),
                            '自定义高度':('INT',{'default':1024,'min':64,'max':4096,'step':32})},'optional':{'参考图':('IMAGE',),'参考图集':('CODEX_REFERENCES',),
                            '最长边上限':('INT',{'default':4096,'min':256,'max':4096,'step':32})}}
    RETURN_TYPES=('INT','INT','STRING');RETURN_NAMES=('宽度','高度','尺寸说明');FUNCTION='resolve';CATEGORY='Codex/图像工具'
    def resolve(self,比例='跟随参考图',尺寸基准='最长边',长边=1024,自定义宽度=1024,自定义高度=1024,参考图=None,参考图集=None,最长边上限=4096):
        if 比例=='自定义宽高':w,h=自定义宽度,自定义高度
        else:
            if 比例.startswith('跟随参考图'):
                index=int(比例.removeprefix('跟随参考图') or '1')-1
                images=(参考图集 or {}).get('images',[])
                source=images[index] if 0<=index<len(images) else 参考图 if index==0 else None
                if source is None:raise ValueError(f'比例所用的参考图{index+1}未启用或未接入。')
                r=source.shape[2]/source.shape[1]
            else:a,b=比例.split()[0].split(':');r=int(a)/int(b)
            if 尺寸基准=='最短边':
                w,h=(长边*r,长边) if r>=1 else (长边,长边/r)
            else:w,h=(长边,长边/r) if r>=1 else (长边*r,长边)
        scale=min(1,最长边上限/max(w,h))
        w,h=[max(64,min(4096,round(x*scale/32)*32)) for x in (w,h)]
        note=f'{w} × {h} · 32像素对齐'+(' · 已按最长边上限等比缩小' if scale<1 else '')
        return {'ui':{'codex_dimensions':[{'width':w,'height':h}]},'result':(w,h,note)}


class CodexReferenceDescribe:
    @classmethod
    def VALIDATE_INPUTS(cls,清除缓存批次=0):
        try:normalize_epoch(清除缓存批次)
        except ValueError as exc:return str(exc)
        return True

    @classmethod
    def INPUT_TYPES(cls):
        from .prompt_nodes import local_models
        models=[x for x in local_models() if any(k in x.lower() for k in ('qwen3.5','qwen3.8','qwen3.6','qwen3-vl','gemma4'))]
        return {'required':{'参考图':('IMAGE',),'模型':(models or ['本机无视觉模型'],),
             '前置规则':('STRING',{'default':DESCRIPTION_RULE,'multiline':True}),
             '补充要求':('STRING',{'default':'','multiline':True}),
             '读取长边':('INT',{'default':1024,'min':256,'max':2048,'step':32}),
             '输出上限':('INT',{'default':1024,'min':128,'max':8192}),
             '上下文':('INT',{'default':8192,'min':4096,'max':65536,'step':1024}),
             '温度':('FLOAT',{'default':0.15,'min':0,'max':1,'step':0.05}),
             'top_p':('FLOAT',{'default':0.9,'min':0.1,'max':1,'step':0.05})},
             'optional':{'清除缓存批次':('INT',{'default':0,'min':0,'max':2147483647})}}
    RETURN_TYPES=('STRING',);RETURN_NAMES=('静态生图提示词',);FUNCTION='describe';CATEGORY='Codex/图像工具'
    def describe(self,参考图,模型,前置规则,补充要求,读取长边,输出上限,上下文,温度,top_p,清除缓存批次=0):
        from .prompt_nodes import LocalOllama
        清除缓存批次=normalize_epoch(清除缓存批次)
        legacy_key=_cache_key('describe-v2',参考图,模型,前置规则,补充要求,读取长边,输出上限,上下文,温度,top_p)
        key=_cache_key('describe-v3',参考图,模型,前置规则,补充要求 or '准确描述这张静态图片。',读取长边,输出上限,上下文,温度,top_p,int(清除缓存批次))
        text=read_description(key,legacy_key if not 清除缓存批次 else None)
        if text is not None:return description_result(text,清除缓存批次,True)
        p=tensor_path(参考图);buffer=io.BytesIO()
        try:
            with Image.open(p) as image:image.thumbnail((读取长边,读取长边));image.save(buffer,format='JPEG',quality=92)
            release_resources(full=True);client=LocalOllama();client.start()
            try:
                response=client.request('/api/chat',{'model':模型,'stream':False,'think':False,'keep_alive':0,
                    'messages':[{'role':'system','content':前置规则},{'role':'user','content':补充要求 or '准确描述这张静态图片。','images':[base64.b64encode(buffer.getvalue()).decode()]}],
                    'options':{'temperature':温度,'top_p':top_p,'num_ctx':上下文,'num_predict':输出上限}},timeout=600)
                text=response.get('message',{}).get('content','').strip()
                if not text or response.get('done_reason')=='length':raise ValueError('反推结果为空或已截断，请增加输出上限。')
                text=static_text(text);write_description(key,text)
                return description_result(text,清除缓存批次,False)
            finally:client.unload(模型);client.close_owned()
        finally:p.unlink(missing_ok=True)


class CodexJoyCaptionDescribe:
    @classmethod
    def VALIDATE_INPUTS(cls,清除缓存批次=0):
        try:normalize_epoch(清除缓存批次)
        except ValueError as exc:return str(exc)
        return True

    @classmethod
    def INPUT_TYPES(cls):
        loader=nodes.NODE_CLASS_MAPPINGS.get('JJC_DownloadAndLoadJoyCaptionModel')
        modes=loader.INPUT_TYPES()['required']['memory_mode'][0] if loader else ['Maximum Savings (4-bit)']
        return {'required':{'参考图':('IMAGE',),'模型':('STRING',{'default':'fancyfeast/llama-joycaption-beta-one-hf-llava'}),
            '显存模式':(modes,),'保持加载':('BOOLEAN',{'default':False}),
            'system_prompt':('STRING',{'default':'Describe the visible image accurately.','multiline':True}),
            'user_query':('STRING',{'default':DESCRIPTION_RULE,'multiline':True}),
            'max_new_tokens':('INT',{'default':768,'min':1,'max':2048}),
            'temperature':('FLOAT',{'default':0.35,'min':0,'max':2,'step':0.05}),
            'top_p':('FLOAT',{'default':0.9,'min':0,'max':1,'step':0.01}),
            'top_k':('INT',{'default':0,'min':0,'max':100})},
            'optional':{'清除缓存批次':('INT',{'default':0,'min':0,'max':2147483647})}}
    RETURN_TYPES=('STRING',);RETURN_NAMES=('静态生图提示词',);FUNCTION='describe';CATEGORY='Codex/图像工具'
    def describe(self,参考图,模型,显存模式,保持加载,system_prompt,user_query,max_new_tokens,temperature,top_p,top_k,清除缓存批次=0):
        清除缓存批次=normalize_epoch(清除缓存批次)
        key=_cache_key('joy-describe-v1',参考图,模型,显存模式,保持加载,system_prompt,user_query,max_new_tokens,temperature,top_p,top_k,int(清除缓存批次))
        text=read_description(key)
        if text is not None:return description_result(text,清除缓存批次,True)
        # The predictor is constructed only after the persistent text cache misses.
        model=_call('JJC_DownloadAndLoadJoyCaptionModel',model=模型,memory_mode=显存模式,keep_loaded=保持加载)[0]
        text=_call('JJC_JoyCaption_Custom',model=model,image=参考图,system_prompt=system_prompt,user_query=user_query,
                   max_new_tokens=max_new_tokens,temperature=temperature,top_p=top_p,top_k=top_k)[0]
        write_description(key,text)
        return description_result(text,清除缓存批次,False)


class CodexReversePromptSave:
    @classmethod
    def INPUT_TYPES(cls):
        original=nodes.NODE_CLASS_MAPPINGS.get('SaveText|pysssss')
        schema=original.INPUT_TYPES() if original else {'required':{'root_dir':(['output'],),'file':('STRING',{'default':'prompt-cache/zimage.txt'}),'append':(['overwrite','append','new only'],),'insert':('BOOLEAN',{'default':False}),'text':('STRING',{'forceInput':True})}}
        schema={**schema,'required':dict(schema['required'])}
        schema['required']['text']=('STRING',{'forceInput':True,'lazy':True})
        schema['optional']={'使用手动提示词':('BOOLEAN',{'default':False})}
        return schema
    RETURN_TYPES=('STRING',);FUNCTION='save';CATEGORY='Codex/图像工具'
    def check_lazy_status(self,root_dir,file,append,insert,text=None,使用手动提示词=False):
        return [] if 使用手动提示词 or text is not None else ['text']
    def save(self,root_dir,file,append,insert,text=None,使用手动提示词=False):
        if 使用手动提示词:
            try:return _call('LoadText|pysssss',root_dir=root_dir,file=file)
            except (OSError,ValueError,KeyError):return ('',)
        if text is None:raise ValueError('没有收到反推提示词，未覆盖原缓存。')
        return _call('SaveText|pysssss',root_dir=root_dir,file=file,append=append,insert=insert,text=text)


class CodexImageGenerate:
    @classmethod
    def IS_CHANGED(cls,**kwargs):
        return _cache_key('prompt-only-v3',kwargs.get('提示词',''),kwargs.get('生成路线'),kwargs.get('宽度'),kwargs.get('高度'),kwargs.get('种子'),kwargs.get('步数'),kwargs.get('引导'),kwargs.get('采样器'),kwargs.get('调度器'),kwargs.get('Image2配置'))
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'提示词':('STRING',{'multiline':True}),
              '生成路线':(['Z-Image Turbo','Image2.1'],),
              '宽度':('INT',{'default':1024,'min':64,'max':4096,'step':32}),
              '高度':('INT',{'default':1024,'min':64,'max':4096,'step':32}),
              '种子':('INT',{'default':42,'min':0,'max':2**32-1}),
              '步数':('INT',{'default':20,'min':1,'max':100}),
              '引导':('FLOAT',{'default':1,'min':0,'max':20,'step':0.1}),
              '采样器':(['res_multistep','euler','dpmpp_2m'],),
              '调度器':(['simple','normal','beta'],)},
              'optional':{'参考图':('IMAGE',),'Image2配置':('H3_IMAGE_SETTINGS',)}}
    RETURN_TYPES=('IMAGE',);RETURN_NAMES=('生成图',);FUNCTION='generate';CATEGORY='Codex/图像工具'
    def generate(self,提示词,生成路线,宽度,高度,种子,步数,引导,采样器,调度器,参考图=None,Image2配置=None):
        宽度,高度=[max(64,min(4096,round(value/32)*32)) for value in (宽度,高度)]
        model_names=[('diffusion_models','z_image_turbo_bf16.safetensors'),('text_encoders','qwen_3_4b.safetensors'),('vae','ae.safetensors')]
        if 生成路线=='Image2.1':
            from .image_bridge import normalize_settings
            cfg=normalize_settings(Image2配置)
            model_names=[('unet_gguf' if cfg['model'].endswith('.gguf') else 'diffusion_models',cfg['model']),('text_encoders',cfg['clip']),('vae',cfg['vae'])]
        key=_cache_key('generate-prompt-only-v3',提示词,生成路线,宽度,高度,种子,步数,引导,采样器,调度器,Image2配置,_model_stamps(model_names))
        cached=_cached_image(key)
        if cached:return (load_tensor(cached),)
        if 生成路线=='Image2.1':
            from .image_bridge import generate_reference,normalize_settings
            config=normalize_settings(Image2配置);config.update(steps=步数,cfg=引导,sampler=采样器,scheduler=调度器,denoise=1.0)
            # This dedicated reverse-prompt generator is text-to-image. Reference
            # pixels belong to description/canvas sizing, never edit conditioning.
            result=generate_reference(提示词,None,宽度,高度,seed=种子,settings=config,
                                      text_only=True,init_from_reference=False)
            image=load_tensor(result);return (load_tensor(_save_cached(image,key)),)
        handles={}
        try:
            handles['model']=nodes.UNETLoader().load_unet('z_image_turbo_bf16.safetensors','default')[0]
            handles['clip']=nodes.CLIPLoader().load_clip('qwen_3_4b.safetensors','lumina2','default')[0]
            handles['vae']=nodes.VAELoader().load_vae('ae.safetensors')[0]
            positive=nodes.CLIPTextEncode().encode(handles['clip'],提示词)[0]
            negative=_call('ConditioningZeroOut',conditioning=positive)[0]
            model=_call('ModelSamplingAuraFlow',model=handles['model'],shift=3)[0]
            latent=_call('EmptySD3LatentImage',width=宽度,height=高度,batch_size=1)[0]
            samples=nodes.KSampler().sample(model,种子,步数,引导,采样器,调度器,positive,negative,latent,denoise=1)[0]
            image=nodes.VAEDecode().decode(handles['vae'],samples)[0];return (load_tensor(_save_cached(image,key)),)
        finally:handles.clear();release_resources(full=True)


class CodexImageRepair:
    @classmethod
    def IS_CHANGED(cls,**kwargs):return _cache_key('repair',kwargs.get('图像'),kwargs.get('启用修复'),kwargs.get('修复规则'),kwargs.get('补充要求'),kwargs.get('种子'),kwargs.get('目标宽度'),kwargs.get('目标高度'),kwargs.get('图像模型配置'))
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'图像':('IMAGE',),'启用修复':('BOOLEAN',{'default':False}),
                '修复规则':('STRING',{'default':REPAIR_RULE,'multiline':True}),
                '补充要求':('STRING',{'default':'','multiline':True}),
                '种子':('INT',{'default':42,'min':0,'max':2**32-1})},
                'optional':{'图像模型配置':('H3_IMAGE_SETTINGS',),
                            '目标宽度':('INT',{'default':0,'min':0,'max':4096,'step':32,'tooltip':'0跟随输入；可外接比例与尺寸节点的宽度。'}),
                            '目标高度':('INT',{'default':0,'min':0,'max':4096,'step':32,'tooltip':'0跟随输入；只设置一边时另一边按原图比例计算。'})}}
    RETURN_TYPES=('IMAGE',);RETURN_NAMES=('修复或直通图像',);FUNCTION='repair';CATEGORY='Codex/图像工具'
    def repair(self,图像,启用修复=False,修复规则=REPAIR_RULE,补充要求='',种子=42,图像模型配置=None,目标宽度=0,目标高度=0):
        if not 启用修复:return (图像,)
        key=_cache_key('repair',图像,启用修复,修复规则,补充要求,种子,目标宽度,目标高度,图像模型配置);cached=_cached_image(key)
        if cached:return self._preview(load_tensor(cached))
        from .image_bridge import generate_reference
        p=tensor_path(图像);h,w=map(int,图像.shape[1:3]);ratio=w/h
        w,h=(目标宽度,目标高度) if 目标宽度 and 目标高度 else (目标宽度,目标宽度/ratio) if 目标宽度 else (目标高度*ratio,目标高度) if 目标高度 else (w,h)
        w,h=[max(64,round(x/32)*32) for x in (w,h)]
        try:
            result=generate_reference(修复规则+'\n'+补充要求,str(p),w,h,seed=种子,settings=图像模型配置)
            image=load_tensor(result);return self._preview(load_tensor(_save_cached(image,key)))
        finally:p.unlink(missing_ok=True)

    @staticmethod
    def _preview(image):
        # Keep this stage visible before downstream upscaling, without adding an output root.
        preview=CodexImageOutput().execute(image,'预览（不保存）','Codex/修复预览')
        return {'ui':{'images':preview['ui']['images']},'result':(image,)}


class CodexImageUpscale:
    @classmethod
    def VALIDATE_INPUTS(cls,超分路线=None,总倍率=None,DLSS倍率=None,VOSR倍率=None,**kwargs):
        if any(value is None for value in (超分路线,总倍率,DLSS倍率,VOSR倍率)):return True
        if 超分路线=='关闭':return True
        if 超分路线 in ('SeedVR2','VOSR2'):
            return True if 总倍率 in (1,2,3,4) else '单模型路线请选择整数倍率。'
        from decimal import Decimal
        return True if Decimal(str(总倍率))==Decimal(str(DLSS倍率))*Decimal(str(VOSR倍率)) else 'DLSS×VOSR无法组成总倍率，请调整后再运行。'
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'图像':('IMAGE',),'超分路线':(['关闭','SeedVR2','VOSR2','DLSS5→VOSR2'],),
                '总倍率':(sorted({round(a*b,3) for a in (1,2,3,4) for b in (1,1.5,1.724,2,3)}),{'default':2}),'DLSS倍率':([1,1.5,1.724,2,3],{'default':1}),
                'VOSR倍率':([1,2,3,4],{'default':2}),
                '种子':('INT',{'default':42,'min':0,'max':2**32-1}),
                '色彩校正':(['wavelet','adain','none'],),
                '图像分块':('INT',{'default':512,'min':64,'max':4096,'step':64}),
                '分块重叠':('INT',{'default':64,'min':0,'max':512,'step':8}),
                'VAE分块':('INT',{'default':1024,'min':64,'max':8192,'step':64}),
                'VAE重叠':('INT',{'default':128,'min':0,'max':512,'step':8}),
                'Seed模型':([p.name for p in (Path(folder_paths.models_dir)/'SEEDVR2').glob('seedvr2*.safetensors')] or ['未安装SeedVR2模型'],),
                '输入噪声':('FLOAT',{'default':0,'min':0,'max':1,'step':0.001}),
                '潜空间噪声':('FLOAT',{'default':0,'min':0,'max':1,'step':0.001}),
                '交换层数':('INT',{'default':0,'min':0,'max':36}),
                'DLSS强度':('FLOAT',{'default':1,'min':0,'max':2,'step':0.05}),
                'DLSS结构':('FLOAT',{'default':1.5,'min':0,'max':2,'step':0.05}),
                'DLSS色彩':('FLOAT',{'default':1,'min':0,'max':1,'step':0.05})}}
    RETURN_TYPES=('IMAGE',);RETURN_NAMES=('超分或直通图像',);FUNCTION='upscale';CATEGORY='Codex/图像工具'
    def upscale(self,图像,超分路线='关闭',总倍率=2,DLSS倍率=1,VOSR倍率=2,种子=42,色彩校正='wavelet',图像分块=512,分块重叠=64,VAE分块=1024,VAE重叠=128,Seed模型='seedvr2_ema_3b_fp8_e4m3fn.safetensors',输入噪声=0,潜空间噪声=0,交换层数=0,DLSS强度=1,DLSS结构=1.5,DLSS色彩=1):
        if 超分路线=='关闭':return (图像,)
        if 超分路线 in ('SeedVR2','VOSR2') and 总倍率 not in (1,2,3,4):raise ValueError('单模型路线请选择1～4的整数倍率。')
        if 超分路线!='SeedVR2':
            from .reference_upscale import upscale_reference
            p=tensor_path(图像)
            try:
                settings={'enabled':True,'algorithm':超分路线,'total_scale':总倍率,
                     'dlss_scale':DLSS倍率 if 超分路线!='VOSR2' else 1,'vosr_scale':VOSR倍率 if 超分路线!='VOSR2' else 总倍率,
                     'seed':种子,'vosr_color_alignment':色彩校正,'vosr_tile_size':图像分块,'vosr_tile_overlap':分块重叠,
                     'vosr_vae_tile_size':VAE分块,'vosr_vae_tile_overlap':VAE重叠,
                     'dlss_intensity':DLSS强度,'dlss_structure':DLSS结构,'dlss_color':DLSS色彩}
                result=upscale_reference(p,settings,Path(folder_paths.get_output_directory())/'Codex超分缓存')
                return (load_tensor(result['path']),)
            finally:p.unlink(missing_ok=True)
        dit=vae=None
        try:
            release_resources(full=True)
            if not (Path(folder_paths.models_dir)/'SEEDVR2'/Seed模型).is_file():raise ValueError('所选SeedVR2模型未安装，不会自动下载。')
            dit=_call('SeedVR2LoadDiTModel',model=Seed模型,device='cuda:0',blocks_to_swap=交换层数,swap_io_components=False,offload_device='cpu',cache_model=False,attention_mode='sdpa')[0]
            vae=_call('SeedVR2LoadVAEModel',model='ema_vae_fp16.safetensors',device='cuda:0',encode_tiled=True,encode_tile_size=VAE分块,encode_tile_overlap=VAE重叠,decode_tiled=True,decode_tile_size=VAE分块,decode_tile_overlap=VAE重叠,offload_device='cpu',cache_model=False)[0]
            return (_call('SeedVR2VideoUpscaler',image=图像,dit=dit,vae=vae,seed=种子,resolution=min(图像.shape[1:3])*总倍率,max_resolution=8192,batch_size=1,uniform_batch_size=False,color_correction=色彩校正,input_noise_scale=输入噪声,latent_noise_scale=潜空间噪声,offload_device='cpu',enable_debug=False)[0],)
        finally:dit=vae=None;release_resources(full=True)


class CodexWorkflowControls:
    @classmethod
    def INPUT_TYPES(cls):return {'required':{'控件映射':('STRING',{'default':'{}'})}}
    RETURN_TYPES=('STRING',);FUNCTION='execute';CATEGORY='Codex/图像工具'
    def execute(self,控件映射='{}'):return (控件映射,)


class CodexImageOutput:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'图像':('IMAGE',),'输出模式':(['预览（不保存）','保存并预览'],{'default':'保存并预览'}),
                            '文件名前缀':('STRING',{'default':'Codex/生成结果'})},
                'optional':{'格式':(['png','jpg','webp'],{'default':'png'}),'压缩质量':('INT',{'default':95,'min':1,'max':100})},
                'hidden':{'prompt':'PROMPT','extra_pnginfo':'EXTRA_PNGINFO','unique_id':'UNIQUE_ID'}}
    RETURN_TYPES=('IMAGE','STRING');RETURN_NAMES=('图像','输出报告');FUNCTION='execute';OUTPUT_NODE=True;CATEGORY='Codex/输出'
    def execute(self,图像,输出模式='保存并预览',文件名前缀='Codex/生成结果',格式='png',压缩质量=95,prompt=None,extra_pnginfo=None,unique_id=None):
        if 图像 is None or getattr(图像,'ndim',0)!=4:raise ValueError('输出节点需要IMAGE。')
        import folder_paths
        import time
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo
        from comfy.cli_args import args
        metadata={}
        if not args.disable_metadata:
            if prompt is not None:metadata['prompt']=prompt
            if extra_pnginfo is not None:metadata.update(extra_pnginfo)
            from .output_metadata import enrich_prompt_metadata
            enrich_prompt_metadata(prompt, metadata, unique_id)
        if 输出模式 not in ('预览（不保存）','保存并预览') or 格式 not in ('png','jpg','webp'):raise ValueError('请选择有效的输出模式及格式。')
        target_root=(Path(folder_paths.get_output_directory()) if 输出模式=='保存并预览' else Path(folder_paths.get_temp_directory())).resolve()
        target_root.mkdir(parents=True,exist_ok=True)
        prefix=Path(str(文件名前缀 or 'Codex/生成结果').replace('\\','/'))
        if prefix.is_absolute() or '..' in prefix.parts or ':' in str(prefix):raise ValueError('文件名前缀只能使用输出目录内的相对路径。')
        directory=(target_root/('codex-preview' if 输出模式=='预览（不保存）' else '')/prefix.parent).resolve()
        if not directory.is_relative_to(target_root):raise ValueError('保存位置超出输出目录。')
        directory.mkdir(parents=True,exist_ok=True)
        items=[]
        for index in range(len(图像)):
            name=f'{prefix.name}_{time.strftime("%Y%m%d-%H%M%S")}_{uuid.uuid4().hex[:6]}_{index+1:02}.{格式}';path=(directory/name).resolve()
            array=(图像[index].detach().cpu().clamp(0,1).numpy()*255).round().astype('uint8')
            if array.shape[-1]==1:array=array[:,:,0]
            image=Image.fromarray(array)
            if 格式=='jpg':image=image.convert('RGB')
            save_options={'quality':压缩质量} if 格式!='png' else {}
            if metadata and 格式=='png':
                pnginfo=PngInfo()
                for key,value in metadata.items():pnginfo.add_text(str(key),json.dumps(value,ensure_ascii=False))
                save_options['pnginfo']=pnginfo
            elif metadata and 格式=='webp':
                # ComfyUI reads WebP's ASCII EXIF entries as "key:JSON".
                exif=Image.Exif()
                keys=[key for key in ('prompt','workflow','codex_prompts') if key in metadata]
                keys.extend(key for key in metadata if key not in keys)
                for tag,key in zip((0x010F,0x0110,0x0131),keys):
                    value=metadata[key]
                    payload=str(key)+':'+json.dumps(value,ensure_ascii=True)
                    exif[tag]=payload
                save_options['exif']=exif.tobytes()
            image.save(path,**save_options)
            relative=path.relative_to(target_root);items.append({'filename':relative.name,'subfolder':relative.parent.as_posix() if relative.parent!=Path('.') else '', 'type':'output' if 输出模式=='保存并预览' else 'temp','width':image.width,'height':image.height})
        report=('已保存并预览' if 输出模式=='保存并预览' else '仅临时预览，未写入正式output')+f'：{len(items)}张'
        return {'ui':{'images':items,'text':[report]},'result':(图像,report)}


QWEN_LORA_OFF='不使用 LoRA'


def _qwen_lora_choices():
    """Filenames are user-defined; architecture compatibility is not a name filter."""
    try:
        names=folder_paths.get_filename_list('loras')
    except Exception:
        names=[]
    return [QWEN_LORA_OFF,*sorted(dict.fromkeys(names),key=str.casefold)]


class CodexQwenImageLora:
    """Optional model-only Qwen Image LoRA patch with a real off switch.

    GGUF patching is experimental in ComfyUI-GGUF; an incompatible file fails clearly when
    enabled, while the disabled path returns the original model without loading a LoRA.
    """
    def __init__(self):
        self._loader=None

    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{
            '模型':('MODEL',),
            '启用LoRA':('BOOLEAN',{'default':False}),
            'LoRA文件':(_qwen_lora_choices(),{'default':QWEN_LORA_OFF,'tooltip':'必须匹配当前Qwen Image 2.1基座；文件名可自定义，Flux、Z-Image、H3等其他模型的LoRA不能混用。'}),
            'LoRA强度':('FLOAT',{'default':0.75,'min':-2,'max':2,'step':0.01}),
        }}

    RETURN_TYPES=('MODEL',);RETURN_NAMES=('LoRA模型',);FUNCTION='apply';CATEGORY='Codex/图像工具'

    def apply(self,模型,启用LoRA=False,LoRA文件=QWEN_LORA_OFF,LoRA强度=0.75):
        if not 启用LoRA or LoRA文件==QWEN_LORA_OFF or float(LoRA强度)==0:
            return (模型,)
        try:
            if self._loader is None:
                loader=getattr(nodes,'LoraLoaderModelOnly',None) or nodes.NODE_CLASS_MAPPINGS.get('LoraLoaderModelOnly')
                if loader is None:raise RuntimeError('缺少ComfyUI原生LoraLoaderModelOnly节点。')
                self._loader=loader()
            patched=self._loader.load_lora_model_only(模型,LoRA文件,float(LoRA强度))[0]
            before=getattr(模型,'patches',None);after=getattr(patched,'patches',None)
            if isinstance(before,dict) and isinstance(after,dict) and sum(map(len,after.values()))<=sum(map(len,before.values())):
                raise ValueError('LoRA中没有匹配当前模型的权重。')
        except Exception as exc:
            raise ValueError('此文件无法应用到当前Qwen Image 2.1 GGUF模型；请确认它是同一基座的Qwen Image LoRA。') from exc
        return (patched,)


class CodexKreaImageLora(CodexQwenImageLora):
    @classmethod
    def INPUT_TYPES(cls):
        schema=super().INPUT_TYPES()
        schema['required']['LoRA文件'][1]['tooltip']='必须匹配当前Krea基座；文件名可自定义，其他模型族的LoRA不能混用。'
        return schema

    def apply(self,*args,**kwargs):
        try:return super().apply(*args,**kwargs)
        except ValueError as exc:raise ValueError('此文件无法应用到当前Krea模型；请确认LoRA基座和版本。') from exc


class CodexMossImageLora(CodexQwenImageLora):
    @classmethod
    def INPUT_TYPES(cls):
        schema=super().INPUT_TYPES()
        schema['required']['LoRA文件'][1]['tooltip']='必须匹配当前MOSS-Image基座；文件名可自定义，Qwen、Krea、Flux等其他模型族的LoRA不能混用。'
        return schema

    def apply(self,*args,**kwargs):
        try:return super().apply(*args,**kwargs)
        except ValueError as exc:raise ValueError('此文件无法应用到当前MOSS-Image模型；请确认LoRA基座和版本。') from exc


NODE_CLASS_MAPPINGS={cls.__name__:cls for cls in (CodexImageCanvas,CodexReferenceDescribe,CodexJoyCaptionDescribe,CodexReversePromptSave,CodexImageGenerate,CodexImageRepair,CodexImageUpscale,CodexWorkflowControls,CodexImageOutput,CodexQwenImageLora,CodexKreaImageLora,CodexMossImageLora)}
NODE_DISPLAY_NAME_MAPPINGS={'CodexImageCanvas':'比例与尺寸 · 跟随参考图','CodexReferenceDescribe':'视觉反推 · 可见事实与修改','CodexImageGenerate':'生成模型 · Z-Image / Image2.1','CodexImageRepair':'可选修复 · 保持身份与衣物','CodexImageUpscale':'超分 · SeedVR2 / VOSR2 / DLSS5'}
NODE_DISPLAY_NAME_MAPPINGS.update({'CodexJoyCaptionDescribe':'视觉反推 · JoyCaption','CodexReversePromptSave':'保存反推提示词 · 手动词直通'})
NODE_DISPLAY_NAME_MAPPINGS['CodexWorkflowControls']='统一控制 · 与各阶段同步'
NODE_DISPLAY_NAME_MAPPINGS['CodexImageOutput']='输出模式 · 预览 / 保存并预览'
NODE_DISPLAY_NAME_MAPPINGS['CodexQwenImageLora']='Qwen Image 2.1 LoRA · 可选实验'
NODE_DISPLAY_NAME_MAPPINGS['CodexKreaImageLora']='Krea LoRA · 可选'
NODE_DISPLAY_NAME_MAPPINGS['CodexMossImageLora']='MOSS-Image LoRA · 可选'
