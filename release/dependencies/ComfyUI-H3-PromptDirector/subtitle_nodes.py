"""Optional local speech recognition, editable timed captions and separate export."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import uuid
import asyncio
import gc
import threading
import ipaddress
import numpy as np
import folder_paths
_EXPORT_LOCK=threading.Lock()


def validate_lines(lines,duration):
    if not isinstance(lines,list) or len(lines)>1000:raise ValueError('字幕应为最多1000条的列表。')
    clean=[]
    for row in lines:
        if not isinstance(row,dict):raise ValueError('每条字幕应包含开始、结束和文字。')
        start,end=row.get('start'),row.get('end');text=row.get('text')
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in (start,end)) or not 0<=start<end<=duration+0.05:
            raise ValueError('字幕起止时间超出视频，或结束早于开始。')
        if not isinstance(text,str) or not text.strip() or len(text)>500:raise ValueError('每条字幕应为1～500字。')
        clean.append({'start':float(start),'end':float(end),'text':text.strip()})
    clean.sort(key=lambda row:row['start'])
    if any(row['start']<previous['end']-1e-6 for previous,row in zip(clean,clean[1:])):
        raise ValueError('字幕时间存在重叠，请调整上一条结束或下一条开始；不会自动改写编辑时间。')
    return clean


def srt_text(lines):
    def stamp(v):
        ms=round(v*1000);return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'
    return '\n\n'.join(f"{i+1}\n{stamp(row['start'])} --> {stamp(row['end'])}\n{row['text']}" for i,row in enumerate(lines))+'\n'


def ass_text(lines,width,height,font='Microsoft YaHei',size=36,color='#ffffff',outline=2,x=50,y=90):
    if not re.fullmatch(r'#[0-9a-fA-F]{6}',color):raise ValueError('字幕颜色应为#RRGGBB。')
    if font not in ('Microsoft YaHei','SimHei','SimSun','Arial'):raise ValueError('字体不在支持列表。')
    if not 8<=size<=150 or not 0<=outline<=10 or not 0<=x<=100 or not 0<=y<=100:raise ValueError('字幕样式参数超出范围。')
    rgb=color[1:];bgr=rgb[4:6]+rgb[2:4]+rgb[0:2]
    header=f'''[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,{font},{size},&H00{bgr},&H00{bgr},&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,{outline},0,2,12,12,12,1
[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
'''
    def stamp(v):
        ticks=round(v*100);return f'{ticks//360000}:{ticks//6000%60:02}:{ticks//100%60:02}.{ticks%100:02}'
    result=[]
    for row in lines:
        text=row['text'].replace('\\','＼').replace('{','｛').replace('}','｝').replace('\r','').replace('\n',r'\N')
        result.append(f"Dialogue: 0,{stamp(row['start'])},{stamp(row['end'])},Default,,0,0,0,,{{\\pos({round(width*x/100)},{round(height*y/100)})}}{text}\n")
    return header+''.join(result)


def _source(record):
    root=(Path(folder_paths.get_output_directory())/'H3分镜'/'工作台').resolve()
    p=Path(record).resolve()
    if not p.is_relative_to(root) or p.name!='分镜任务.json' or not p.is_file():raise ValueError('请把工作台“任务记录”连接到字幕节点。')
    data=json.loads(p.read_text(encoding='utf-8'));video=Path(data.get('final_path','')).resolve()
    if not data.get('final_path'):return None,data
    if not video.is_relative_to(p.parent) or not video.is_file():raise ValueError('完整视频尚未生成。')
    return video,data


def _recognize(path,progress,language='自动'):
    import av
    from .subtitle_timing import caption_lines
    model_dir=Path(folder_paths.models_dir)/'whisper'/'base'
    if not (model_dir/'model.safetensors').is_file():raise RuntimeError('本地语音识别模型未就绪：models/whisper/base。')
    progress('recognizing','正在识别声音与时间点')
    import torch
    from transformers import pipeline
    reader=None
    try:
        # Decode bounded chunks; a full-length movie is never copied to GPU.
        reader=pipeline('automatic-speech-recognition',model=str(model_dir),device=-1,torch_dtype=torch.float32)
        with av.open(str(path)) as video:
            if not video.streams.audio:return []
            total_seconds=float(video.duration/av.time_base) if video.duration else None
            resampler=av.AudioResampler(format='fltp',layout='mono',rate=16000)
            chunks=[];count=0;offset=None;result=[]
            media_start=float(video.start_time/av.time_base) if video.start_time is not None else 0.0
            def transcribe(audio,at):
                if not len(audio) or float(np.sqrt(np.mean(audio*audio)))<0.002:return
                generation={'task':'transcribe'}
                if language!='自动':generation['language']={'中文':'zh','英语':'en'}[language]
                output=reader({'raw':audio,'sampling_rate':16000},return_timestamps='word',generate_kwargs=generation,chunk_length_s=30)
                result.extend(caption_lines(output.get('chunks',[]),len(audio)/16000,offset=at,source='word'))
                done=at+len(audio)/16000
                progress('recognizing',f'已识别 {min(done,total_seconds):.1f} / {total_seconds:.1f} 秒' if total_seconds else f'已识别 {done:.1f} 秒')
            def consume(sample):
                nonlocal chunks,count,offset
                values=sample.to_ndarray().reshape(-1)
                at=sample.time
                at=float(at)-media_start if type(at) in (int,float) and math.isfinite(at) else (offset or 0.0)+count/16000
                if at<0:
                    skip=min(len(values),round(-at*16000));values=values[skip:];at+=skip/16000
                if not len(values):return
                at=max(0.0,at)
                if count and abs(at-(offset+count/16000))>0.08:
                    transcribe(np.concatenate(chunks),offset);chunks=[];count=0
                if not count:offset=at
                chunks.append(values);count+=len(values)
                if count>=16000*30:
                    transcribe(np.concatenate(chunks),offset);offset+=count/16000;chunks=[];count=0
            for frame in video.decode(audio=0):
                from comfy import model_management as mm
                mm.throw_exception_if_processing_interrupted()
                for sample in resampler.resample(frame):consume(sample)
            for sample in resampler.resample(None):consume(sample)
            if chunks:transcribe(np.concatenate(chunks),offset)
        return result
    finally:reader=None;gc.collect()


class H3Subtitles:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'启用字幕':('BOOLEAN',{'default':False}),
            '执行步骤':(['识别并保存字幕视频','仅识别字幕','使用编辑字幕导出'],),
            '识别语言':(['自动','中文','英语'],),
            '字体':(['Microsoft YaHei','SimHei','SimSun','Arial'],),
            '字号':('INT',{'default':36,'min':8,'max':150}),
            '颜色':('STRING',{'default':'#ffffff'}),'黑色描边':('FLOAT',{'default':2,'min':0,'max':10,'step':0.5}),
            '横向位置':('FLOAT',{'default':50,'min':0,'max':100,'step':1}),
            '纵向位置':('FLOAT',{'default':90,'min':0,'max':100,'step':1}),
            '编辑字幕':('STRING',{'default':'{}','multiline':True})},
            'optional':{'任务记录':('STRING',{'forceInput':True,'lazy':True})},'hidden':{'unique_id':'UNIQUE_ID'}}
    RETURN_TYPES=('VIDEO','STRING');RETURN_NAMES=('字幕视频','字幕文件');FUNCTION='execute';CATEGORY='H3/字幕';OUTPUT_NODE=True
    def check_lazy_status(self,启用字幕=False,任务记录=None,**kwargs):return ['任务记录'] if 启用字幕 and 任务记录 is None else []
    def execute(self,启用字幕=False,执行步骤='识别并保存字幕视频',识别语言='自动',字体='Microsoft YaHei',字号=36,颜色='#ffffff',黑色描边=2,横向位置=50,纵向位置=90,编辑字幕='{}',任务记录=None,unique_id=None):
        if not 启用字幕:return {'ui':{'text':['字幕已关闭']},'result':(None,'')}
        from comfy_api.latest import InputImpl
        from server import PromptServer
        from .audio_timeline import _ffmpeg
        def progress(stage,message):
            if PromptServer.instance:PromptServer.instance.send_sync('h3-subtitle-progress',{'node_id':str(unique_id),'stage':stage,'message':message})
        try:
            source,manifest=_source(任务记录)
            if source is None:
                progress('waiting','等待工作台完整成片；字幕将在成片之后处理')
                return {'ui':{'text':['等待完整成片']},'result':(None,'')}
            import av
            with av.open(str(source)) as container:
                stream=container.streams.video[0];w,h=stream.width,stream.height
                duration=float(container.duration/av.time_base) if container.duration else sum(manifest['scene_seconds'])
            digest=hashlib.sha256()
            with source.open('rb') as stream:
                for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
            sha=digest.hexdigest()
            draft=json.loads(编辑字幕 or '{}')
            if not isinstance(draft,dict):raise ValueError('字幕编辑数据应为对象。')
            cache=source.with_suffix('.subtitles.json')
            from .subtitle_timing import TIMING_VERSION,caption_lines
            cached=json.loads(cache.read_text(encoding='utf-8')) if cache.is_file() else {}
            cache_matches=cached.get('source_sha256')==sha and cached.get('language','自动')==识别语言
            draft_matches=draft.get('source_sha256')==sha and (draft.get('language','自动')==识别语言 or 执行步骤=='使用编辑字幕导出')
            # Explicit edits keep their exact seconds. Unedited old segment
            # caches are recognized once with word timing; style-only changes
            # reuse the corrected cache and do not reload the recognizer.
            draft_edited=draft_matches and (执行步骤=='使用编辑字幕导出' or not cache_matches or draft.get('lines')!=cached.get('lines'))
            timing_source=[]
            if draft_edited or draft_matches and draft.get('timing_version')==TIMING_VERSION:
                lines=validate_lines(draft.get('lines'),duration);timing_source=['edited'] if draft_edited else draft.get('timing_source',['word'])
            elif 执行步骤=='使用编辑字幕导出':raise ValueError('编辑字幕不属于当前视频，请先识别当前成片。')
            elif cache_matches and cached.get('timing_version')==TIMING_VERSION:
                lines=validate_lines(cached['lines'],duration);timing_source=cached.get('timing_source',['word'])
            else:
                recognized=caption_lines(_recognize(source,progress,识别语言),duration,source='word')
                timing_source=list(dict.fromkeys(row.get('timing_source','word') for row in recognized))
                lines=validate_lines(recognized,duration)
            payload={'source_sha256':sha,'run_id':manifest['run_id'],'lines':lines,'width':w,'height':h,'duration':duration,'language':识别语言,
                     'timing_version':TIMING_VERSION,'timing_source':timing_source}
            cache.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
            srt=source.with_suffix('.srt');srt.write_text(srt_text(lines),encoding='utf-8-sig')
            ass=source.with_suffix('.ass');ass.write_text(ass_text(lines,w,h,字体,字号,颜色,黑色描边,横向位置,纵向位置),encoding='utf-8-sig')
            output=source
            if 执行步骤!='仅识别字幕' and lines:
                progress('rendering','正在合成字幕视频')
                output=source.with_name(source.stem+'_字幕_'+uuid.uuid4().hex[:6]+'.mp4');partial=output.with_suffix('.partial.mp4')
                filter_path=ass.as_posix().replace(':',r'\:').replace("'",r"\'")
                # Retain the source execution snapshot as MP4 mdta tags and put
                # moov first so ComfyUI's bounded metadata reader can find it.
                try:_ffmpeg(['-i',source,'-map_metadata','0','-vf',f"ass=filename='{filter_path}'",'-c:v','libx264','-crf','18','-preset','fast','-c:a','copy','-movflags','use_metadata_tags+faststart',partial],timeout=1800);partial.replace(output)
                finally:partial.unlink(missing_ok=True)
            relative=output.relative_to(Path(folder_paths.get_output_directory()).resolve())
            descriptor={'filename':relative.name,'subfolder':relative.parent.as_posix(),'type':'output'}
            progress('completed','字幕已保存；原成片保留')
            return {'ui':{'h3_subtitles':[payload],'videos':[descriptor],'text':[str(srt)]},'result':(InputImpl.VideoFromFile(str(output)),str(srt))}
        except BaseException as error:
            progress('failed',str(error));raise


NODE_CLASS_MAPPINGS={'H3Subtitles':H3Subtitles}
NODE_DISPLAY_NAME_MAPPINGS={'H3Subtitles':'字幕 · 识别、编辑与位置预览'}


def register_subtitle_routes():
    from server import PromptServer
    from aiohttp import web
    server=PromptServer.instance
    if server is None or getattr(server,'_codex_subtitle_routes',False):return
    @server.routes.post('/h3-director/subtitles/export')
    async def export(request):
        try:
            if not ipaddress.ip_address(request.remote).is_loopback:raise web.HTTPForbidden()
            if request.headers.get('Origin') and request.headers['Origin'].split('://',1)[-1].rstrip('/')!=request.host:raise web.HTTPForbidden()
            if request.content_length and request.content_length>1024*1024:raise ValueError('字幕编辑数据过大。')
            raw=await request.content.read(1024*1024+1)
            if len(raw)>1024*1024:raise ValueError('字幕编辑数据过大。')
            data=json.loads(raw);run=data.get('run_id','')
            if not re.fullmatch(r'[0-9a-f]{32}',run):raise ValueError('请先生成一次完整成片，或连接已有工作台任务。')
            with server.prompt_queue.mutex:
                running,queued=server.prompt_queue.get_current_queue_volatile()
                if running or queued:return web.json_response({'error':'请等待当前队列结束；单独更新字幕不会重跑生图或视频。'},status=409)
            allowed={'执行步骤','识别语言','字体','字号','颜色','黑色描边','横向位置','纵向位置','编辑字幕'}
            params={key:value for key,value in data.get('settings',{}).items() if key in allowed}
            record=Path(folder_paths.get_output_directory())/'H3分镜'/'工作台'/run/'分镜任务.json'
            def work():
                if not _EXPORT_LOCK.acquire(blocking=False):raise ValueError('已有字幕任务正在处理。')
                try:
                    from comfy import model_management as mm
                    mm.interrupt_current_processing(False)
                    result=H3Subtitles().execute(启用字幕=True,任务记录=str(record),unique_id=data.get('node_id'),**params)
                    return result.get('ui',{})
                finally:_EXPORT_LOCK.release()
            return web.json_response(await asyncio.to_thread(work))
        except web.HTTPException:raise
        except Exception as error:return web.json_response({'error':str(error)},status=400)
    server._codex_subtitle_routes=True
