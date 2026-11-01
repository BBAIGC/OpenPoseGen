"""Pure export preparation and loopback-only progress/plan file endpoints."""
import copy
import hashlib
import ipaddress
import json
import re
from aiohttp import web
from .plan_import import parse_import_payload,export_plan,_validate_plan


async def _read_json(request):
    raw=bytearray()
    async for chunk in request.content.iter_chunked(64*1024):
        raw.extend(chunk)
        if len(raw)>8*1024*1024:raise ValueError('分镜文件超过8MB。')
    value=json.loads(raw)
    if not isinstance(value,dict):raise ValueError('分镜文件应为JSON对象。')
    return value


def prepare_export(payload,edits=None,overrides=None):
    from .plan_edits import apply_plan_edits
    value=parse_import_payload(payload)
    if value is None:raise ValueError('请先完成或导入分镜计划。')
    plan=value['plan'];edit=copy.deepcopy(edits or {})
    if not isinstance(plan.get('raw_script',''),str) or len(plan.get('raw_script',''))>12000:
        raise ValueError('原始剧本应为不超过12000字的文本。')
    if value.get('optimized_script'):edit.setdefault('optimized_script',value['optimized_script'])
    if overrides:
        edit.setdefault('scenes',{})
        for key,changes in overrides.items():edit['scenes'][str(key)]={**edit['scenes'].get(str(key),{}),**changes}
    plan=apply_plan_edits(plan,edit)
    refs=value.get('references',[])
    input_mode=plan.get('input_mode','I2V');text_only=input_mode=='T2VA'
    _validate_plan(plan,value['durations'],refs,input_mode=input_mode)
    # The exported overview must describe the merged plan. Re-import applies
    # this text, so retaining the old overview would undo individual edits.
    from .studio_nodes import _optimized_script
    text=edit.get('optimized_script','')
    bindings={}
    for block in re.split(r'(?m)(?=^镜头\s*[1-9]\d*\b)',text):
        heading=re.match(r'^镜头\s*([1-9]\d*)',block)
        binding=re.search(r'(?m)^(@[^\n]+\s开始(?:[^\n]*\s结束)?)\s*$',block)
        if heading and binding:bindings[int(heading[1])]=binding[1]
    scenes=[{'seconds':seconds,'display_prompt':'' if text_only else bindings.get(i+1,f'@分镜{i+1} 开始 → @{"分镜"+str(i+2) if i+1<len(plan["scenes"]) else "结束帧"} 结束')}
            for i,seconds in enumerate(value['durations'])]
    result=export_plan(plan,value['durations'],mode=value.get('mode'),references=refs,optimized_script=_optimized_script(plan,scenes))
    result['source_script']=plan.get('raw_script','')
    if value.get('reference_files') and not text_only:result['reference_files']=value['reference_files']
    if value.get('canvas_settings'):result['canvas_settings']=value['canvas_settings']
    if text_only:result['video_input_source']='文生视频'
    return result


def register_plan_file_routes():
    from server import PromptServer
    server=PromptServer.instance
    if server is None or getattr(server,'_h3_plan_file_routes',False):return
    def local(request):
        if not ipaddress.ip_address(request.remote).is_loopback:raise web.HTTPForbidden()
        origin=request.headers.get('Origin')
        if origin and origin.split('://',1)[-1].rstrip('/')!=request.host:raise web.HTTPForbidden()
    @server.routes.post('/h3-director/plan/export')
    async def export(request):
        local(request)
        try:
            data=await _read_json(request)
            return web.json_response(prepare_export(data.get('payload'),data.get('edits'),data.get('overrides')))
        except (ValueError,KeyError,TypeError) as error:return web.json_response({'error':str(error)},status=400)
    @server.routes.post('/h3-director/plan/import')
    async def import_file(request):
        local(request)
        try:
            data=await _read_json(request);payload=parse_import_payload(data.get('payload'))
            if payload is None:raise ValueError('文件没有完整分镜计划。')
            from .studio_nodes import resolve_references,_optimized_script
            from .plan_import import import_plan
            files=payload.get('reference_files') or data.get('references',[])
            input_mode=payload['plan'].get('input_mode','I2V');text_only=input_mode=='T2VA'
            if text_only and files:raise ValueError('文生视频计划不能导入参考图片。')
            inputs=[]
            for entry in files:
                if entry.get('type','input')!='input':raise ValueError('参考图片请先上传到当前ComfyUI输入目录。')
                name='/'.join(str(entry.get(key,'')).strip('/') for key in ('subfolder','filename') if entry.get(key))
                inputs.append({'id':entry['id'],'filename':name})
            references=[] if text_only else resolve_references(json.dumps(inputs))
            plan=import_plan(payload,count=len(payload['durations']),durations=payload['durations'],references=references,mode=payload.get('mode','AI生成分镜'),voice_mode=payload['plan']['speech_mode'],prompt_format=payload.get('prompt_format',payload['plan'].get('prompt_format','工作台格式')),input_mode=input_mode)
            if not payload.get('optimized_script'):
                scenes=[{'seconds':seconds,'display_prompt':'' if text_only else scene.get('display_prompt',f'@分镜{i+1} 开始')} for i,(scene,seconds) in enumerate(zip(plan['scenes'],payload['durations']))]
                payload['optimized_script']=_optimized_script(plan,scenes)
            payload['plan']=plan;payload['source_script']=plan.get('raw_script','')
            return web.json_response({'payload':payload,'references':inputs})
        except (ValueError,KeyError,TypeError,OSError) as error:return web.json_response({'error':str(error)},status=400)
    @server.routes.get('/h3-director/progress')
    async def progress(request):
        local(request)
        from .progress_state import get_state
        from .studio_nodes import _manifest_path,_snapshot_event
        run=request.query.get('run_id');node=request.query.get('node_id');prompt=request.query.get('prompt_id')
        if run and not re.fullmatch(r'[0-9a-f]{32}',run):return web.json_response({'error':'任务编号无效。'},status=400)
        event=get_state(run,node,prompt)
        if event is None and run:
            path=_manifest_path(run)
            if path.is_file():
                data=json.loads(path.read_text(encoding='utf-8'))
                if (not prompt or not data.get('prompt_id') or data.get('prompt_id')==prompt) and (not data.get('node_id') or str(data['node_id'])==node):
                    event=_snapshot_event(data,data.get('node_id') or node)
        return web.json_response({'snapshot':event},headers={'Cache-Control':'no-store'})
    server._h3_plan_file_routes=True
