"""Explicit manual storyboard import, without contacting a language model."""
import json
from .static_prompt import static_text

MANUAL_EXAMPLE=json.dumps({'参考图':['图1'],'镜头':[
 {'秒数':8,'开场':'成年人物戴着帽子坐在桌前，杯子在桌上。','画面':'日间室内中景，人物与桌子保持参考图布局。','动作':'右手摘下帽子放到桌面，微笑说话。','结束':'帽子放在桌上，人物仍坐在桌前。','生图提示词':'成年人物戴帽子坐在桌前，杯子在桌上，白天室内中景。','对白':[{'原话':'下午好。','开始':1,'结束':3}]},
 {'秒数':8,'开场':'帽子放在桌上，人物仍坐在桌前。','画面':'与上一镜同一环境，人物面部近景。','动作':'人物轻轻点头，双手仍放在桌边。','结束':'人物点头后自然坐着，帽子和杯子位置不变。','生图提示词':'成年人物坐在桌前，帽子与杯子都在桌上，脸部近景。','对白':[]}]},ensure_ascii=False,indent=2)


class H3ManualPlan:
    @classmethod
    def INPUT_TYPES(cls):return {'required':{'手动分镜JSON':('STRING',{'default':MANUAL_EXAMPLE,'multiline':True}),
             '生成模式':(['AI生成分镜','手动分镜图','逐镜参考图','上一镜末帧','末帧高清续接'],),
             '声音模式':(['自动识别原稿','仅指定对白','无对白（环境音）','静音'],)}}
    RETURN_TYPES=('STRING','STRING','STRING');RETURN_NAMES=('导入优化计划','原始剧本','每镜秒数');FUNCTION='build';CATEGORY='H3/整稿与分镜'
    def build(self,手动分镜JSON,生成模式='AI生成分镜',声音模式='自动识别原稿'):
        from .planner import compile_storyboard
        from .plan_import import export_plan
        data=json.loads(手动分镜JSON);entries=data.get('镜头')
        if not isinstance(entries,list) or not 1<=len(entries)<=100:raise ValueError('手动计划应包含1～100个镜头。')
        refs=data.get('参考图',['图1'])
        if not isinstance(refs,list) or any(not isinstance(x,str) or not x.startswith('图') or not x[1:].isdigit() for x in refs):raise ValueError('参考图请填写图1、图2等编号。')
        scenes=[];lines=[];seconds=[]
        for i,entry in enumerate(entries):
            duration=entry['秒数']
            if type(duration) not in (int,float) or not 1<=duration<=15:raise ValueError('每镜秒数应为1～15。')
            seconds.append(float(duration));scene={}
            for source,target in [('开场','start_state'),('画面','visual'),('动作','action'),('结束','end_state'),('生图提示词','image_prompt')]:
                value=entry.get(source)
                if not isinstance(value,str) or not value.strip():raise ValueError(f'镜头{i+1}缺少{source}。')
                scene[target]=static_text(value) if target=='image_prompt' else value
            if i and scene['start_state']!=scenes[-1]['end_state']:raise ValueError(f'镜头{i+1}开场必须与上一镜结束状态一致。')
            scene.update(soundscape=entry.get('环境音','自然环境音。'),music=entry.get('配乐','N/A'),reference_ids=refs,dialogues=[])
            for row in entry.get('对白',[]) if 声音模式 not in ('静音','无对白（环境音）') else []:
                text=row['原话'];identifier=f'D{len(lines)+1}'
                if not isinstance(text,str) or not text.strip() or '<' in text or '>' in text:raise ValueError('对白请填写无控制标签的原话。')
                lines.append({'id':identifier,'text':text,'speaker':'S1','speaker_locked':False,'language':'Chinese','offscreen':False})
                scene['dialogues'].append({'id':identifier,'start':row['开始'],'end':row['结束'],'speaker_id':'S1','speaker_description':row.get('人物','成年人物'),'delivery':row.get('语气','自然说话')})
            scenes.append(scene)
        script=' '.join('@'+x for x in refs)+'\n'+ '\n'.join(scene['action'] for scene in scenes)
        compiled=compile_storyboard({'scenes':scenes,'continuity':'','constraints':''},script=script,count=len(scenes),seconds=seconds,dialogues=lines,voice_mode=声音模式)
        final_scenes=compiled['scenes']
        prompts=[x['prompt'] for x in compiled['compiled']]
        for i,scene in enumerate(final_scenes):
            scene['video_prompt']=prompts[i];scene['display_prompt']=f"@分镜{i+1} 开始 → @{'分镜'+str(i+2) if i+1<len(scenes) else '结束帧'} 结束"
        plan={'status':'ready','llm_released':True,'scene_count':len(scenes),'scene_seconds':seconds,'total_seconds':sum(seconds),'speech_mode':声音模式,'audio_mode':'静音' if 声音模式=='静音' else '模型音频','raw_script':script,'dialogue_whitelist':lines,'scenes':final_scenes,'compiled_prompts':[x['json'] for x in compiled['compiled']],'prompts':prompts,'final_image_prompt':static_text(scenes[-1]['end_state']),'prompt_format':'工作台格式'}
        payload=export_plan(plan,seconds,mode=生成模式,references=[{'id':x} for x in refs])
        return json.dumps(payload,ensure_ascii=False),script,','.join(f'{x:g}' for x in seconds)


NODE_CLASS_MAPPINGS={'H3ManualPlan':H3ManualPlan};NODE_DISPLAY_NAME_MAPPINGS={'H3ManualPlan':'手动分镜计划 · 不调用AI'}
