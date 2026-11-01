"""Optional text-only prompt refinement; disabled means verbatim passthrough."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import uuid

DEFAULT_RULES=('将用户输入整理为目标生图模型能准确执行的提示词，只输出最终提示词。'
 '保留主体、身份、数量、动作、表情、衣物覆盖、物体关系、画面左右、环境、构图、光照和明确限制。'
 '参考图1、图2、@图1等来源编号必须保持，不互换角色或添加未要求的人物、物件、剧情。'
 '用户中文输入默认输出中文，指定要出现在图上的文字逐字保留。'
 '去掉重复措辞和不适用于该模型的标签，但不删除关键需求；看不清或未给出的细节不要编造。'
 '不要输出分析、标题、解释、对白时间轴、帧率、S编号或视频镜头运动。')

LEGACY_TARGET_RULE='Z-Image：用完整、具体、紧凑的静态视觉描述；Image2.1：清楚说明要修改什么、修改成什么，以及哪些必须保持。'
TARGET_RULES={
    'Z-Image Turbo':'使用完整自然语言描述一张静态画面，准确串联主体、动作表情、空间关系、构图、光照和材质。将修改意图落实为最终画面状态，不输出改图操作步骤；不堆叠重复标签。',
    'Image2.1':'文生图要求组织为完整静态画面；有编辑要求时清楚表达修改对象、修改后的状态和应保持的内容。多图编号与对象一一对应，准确描述位置、接触及遮挡，不互换参考图来源。',
    'GPT Image 2/2.5':'使用清楚的自然语言描述目标画面；编辑时区分修改项与保留项，多图时明确各图提供的对象。逐字保留图中文字及排版要求，只生成一个可直接使用的版本。',
    'Krea-2 Turbo':'用完整自然语言描述一张静态画面，准确表达主体及其动作、构图、材质、光照、色彩和用户指定风格。保持用户给出的媒介与可见文字，不编造道具或改写LoRA触发词，只输出当前目标的一份提示词。',
    'MOSS-Image':'用完整、准确的自然语言描述一张静态画面，明确各主体及动作、数量、前后左右关系、构图和布局。逐字保留用户指定的画面文字、风格名称和LoRA触发词；按已有信息表达材质、光照与环境，不编造细节，只输出当前目标的一份提示词。',
}
DETAIL_LEVELS={
    1:('精简','用紧凑准确的句子表达完整要求，只省略重复和赘述，不删除任何明确需求。'),
    2:('简洁','简洁交代全部关键对象、动作、关系与限制，必要的身份、图号和画面细节不能省略。'),
    3:('标准','完整组织用户提供的主体特征、动作表情、空间位置、构图、环境、光照材质及限制，明确复杂关系。'),
    4:('详细','按主体与图号、姿态动作、空间关系、环境构图、光照材质、编辑边界逐项展开已有信息，明确左右、接触和遮挡；有编辑要求时分别写清修改与保留项。不凭空补全未给出的内容，不靠重复句子增加篇幅。'),
    5:('极详细','逐项核对并完整展开用户已经提供的信息：每个主体与来源图号、身份和外观、姿态表情、前后左右与接触遮挡、环境构图、光照材质、修改项和保留边界。将已有的复杂关系解释清楚，消除含混指代，不合并掉明确需求；只在有依据时展开，不编造细节，不为达到字数重复或增添内容。'),
}
PROMPT_REVISION=5
OUTPUT_LANGUAGES=('跟随输入','中文','英文')
OUTPUT_SCHEMA={'type':'object','properties':{'prompt':{'type':'string'}},'required':['prompt'],'additionalProperties':False}


def output_language(source,selection='跟随输入'):
    if selection not in OUTPUT_LANGUAGES:raise ValueError('请选择跟随输入、中文或英文。')
    if selection!='跟随输入':return selection
    source=str(source)
    # Explicit instructions about the prompt language take precedence over its script.
    explicit=re.findall(r'(?:用|使用)(中文|英文)(?:输出|撰写|写出|生成)(?:提示词|prompt|正文)|(?:提示词|prompt|正文)(?:请)?(?:用|使用|输出为|写成)(中文|英文)',source,re.I)
    if explicit:return next(value for value in explicit[-1] if value)
    english=re.search(r'\b(?:write|output|generate)\s+(?:the\s+|a\s+)?(?:final\s+)?prompt\s+in\s+(English|Chinese)\b',source,re.I)
    if english:return '中文' if english[1].lower()=='chinese' else '英文'
    if re.search(r'[\u3400-\u9fff]',source):return '中文'
    return '英文' if re.search(r'[A-Za-z]',source) else '中文'


def _language_content(text,source):
    text=re.sub(r'<[^>]+>|@?(?:参考图|图|ref)\d+',' ',text,flags=re.I)
    # Literal text in the image and explicitly named LoRA triggers keep their language.
    literals=[]
    for pattern in (r'"([^"\n]+)"',r'“([^”\n]+)”',r'「([^」\n]+)」',r"'([^'\n]+)'",
                    r'(?:LoRA\s*)?(?:触发词|trigger(?:\s+words?)?)\s*[:：=]\s*([^\n;；。]+)'):
        literals.extend(re.findall(pattern,str(source),flags=re.I))
    for literal in sorted(set(literals),key=len,reverse=True):
        if literal.strip():text=text.replace(literal,' ')
    return re.sub(r'https?://\S+|\b\S+\.(?:safetensors|gguf|png|jpe?g|webp)\b',' ',text,flags=re.I)


def language_mismatch(text,language,source=''):
    prose=_language_content(text,source)
    cjk=len(re.findall(r'[\u3400-\u9fff]',prose))
    words=len(re.findall(r'[A-Za-z]+(?:[’\'-][A-Za-z]+)*',prose))
    letters=len(re.findall(r'[A-Za-z]',prose))
    if language=='中文':return words>=4 and cjk==0 or words>=12 and letters>cjk*2
    return cjk>=8 and cjk>words*2


def optimization_system(target,rules,detail,language=None):
    if target not in TARGET_RULES:raise ValueError('请选择有效的目标生图模型。')
    if type(detail) is not int or detail not in DETAIL_LEVELS:raise ValueError('详细程度应为1～5。')
    common=str(rules).replace(LEGACY_TARGET_RULE,'').strip()
    label,description=DETAIL_LEVELS[detail]
    language_rule=(f'本次提示词正文必须使用{language}。用户前置规则和原稿中与本项冲突的语言偏好，以本次输出语言为准；'
                   'LoRA触发词、来源图号、专有名称和用户指定的图中文字逐字保留，不翻译这些字面内容。\n') if language else ''
    return (f'本次唯一目标模型：{target}。只为这个模型优化一份可直接使用的最终提示词。\n'
            f'{common}\n目标模型写法：{TARGET_RULES[target]}\n详细程度：{label}。{description}\n'
            f'{language_rule}'
            '所有等级都必须保留原始输入中的明确需求、身份、图号、数量、动作、空间关系、限制和图中文字；'
            '只展开已有依据的信息，不为增加篇幅编造外观、剧情、物件或环境。'
            '输入中的<imageN>是固定图片身份标记，必须逐字保留所有已引用标记，不增删、不重编号。'
            '只返回一个JSON对象，唯一字段prompt的值是最终提示词正文。'
            '正文不含模型名标题、分版本结果、备选方案、分析或解释。')


def preserve_image_slots(source, output):
    """The model may rewrite prose, but cannot rename or silently drop image identities."""
    slots = {int(value) for value in re.findall(r'<image([1-9]\d*)>', str(source), flags=re.I)}
    if not slots:
        return output
    found = set()
    pattern = r'<image([1-9]\d*)>|\bimage\s*([1-9]\d*)\b|@?(?:参考图|图)\s*([1-9]\d*)(?!\d)'
    def replace(match):
        slot = int(next(value for value in match.groups() if value))
        if slot not in slots:
            raise ValueError(f'优化结果出现原稿未引用的图片槽位{slot}，未传给生图。请重新优化。')
        found.add(slot)
        return f'<image{slot}>'
    text = re.sub(pattern, replace, str(output), flags=re.I)
    if found != slots:
        raise ValueError('优化结果遗漏原稿的参考图片，未传给生图。请重新优化或关闭优化后使用原文。')
    return text


def _validate_budget(predict,context):
    if type(predict) is not int or not 128<=predict<=8192:raise ValueError('输出上限应为128～8192 token的整数。')
    if type(context) is not int or not 4096<=context<=65536:raise ValueError('上下文应为4096～65536 token的整数。')
    if predict+256>context:raise ValueError('上下文需为系统规则和原稿预留空间，请提高上下文或降低输出上限（单位均为token）。')


def _prompt_stats(text,language,response=None,cached=False):
    response=response or {}
    def count(name):
        value=response.get(name)
        return value if type(value) is int and value>=0 else None
    return {'characters':len(text),'bytes':len(text.encode('utf-8')),
            'output_tokens':count('eval_count'),'prompt_tokens':count('prompt_eval_count'),
            'model_load_ns':count('load_duration'),'prompt_eval_ns':count('prompt_eval_duration'),
            'output_eval_ns':count('eval_duration'),
            'language':language,'cached':cached,'truncated':False}


def _prompt_result(text,stats):
    return {'ui':{'text':[text],'codex_prompt_stats':[stats]},'result':(text,)}


def final_prompt(content,target):
    text=re.sub(r'<think>.*?</think>','',str(content),flags=re.S).strip()
    text=re.sub(r'^```(?:json)?\s*|\s*```$','',text,flags=re.I).strip()
    try:
        decoded=json.loads(text)
        parsed=True
    except json.JSONDecodeError:
        if text.startswith(('{','[')):raise ValueError('优化结果JSON无效，请重新优化。')
        parsed=False
    if parsed:
        if not isinstance(decoded,dict) or set(decoded)!={'prompt'} or not isinstance(decoded['prompt'],str):
            raise ValueError('优化结果格式无效，需要一份最终提示词。')
        text=decoded['prompt'].strip()
    # Some local model templates ignore JSON formatting. Keep only the selected labelled section.
    heading=re.compile(r'(?im)^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*)?(?P<name>Z[- ]?Image(?:[ \t]+Turbo)?|(?:Qwen[- ]?)?Image[ \t]*2\.1|GPT[ \t]*Image[ \t]*2(?:/2\.5|\.5)?|Krea[- ]?2(?:[ \t]+Turbo)?|MOSS[- ]?Image)(?:[ \t]*(?:模型|版本|提示词|prompt))?(?:\*\*)?[ \t]*(?:[:：](?:\*\*)?[ \t]*|$)')
    sections=list(heading.finditer(text))
    if sections:
        family='z' if target=='Z-Image Turbo' else 'gpt' if target=='GPT Image 2/2.5' else 'krea' if target.startswith('Krea') else 'moss' if target=='MOSS-Image' else 'image'
        selected=[]
        for index,match in enumerate(sections):
            name=match['name'].lower();kind='z' if name.startswith('z') else 'gpt' if name.startswith('gpt') else 'krea' if name.startswith('krea') else 'moss' if name.startswith('moss') else 'image'
            if kind==family:
                selected.append(text[match.end():sections[index+1].start() if index+1<len(sections) else len(text)].strip())
        if not selected:raise ValueError('优化结果与选中的目标模型不符。')
        text=selected[0]
    if not text:raise ValueError('提示词优化结果为空。')
    return text


class CodexPromptOptimize:
    @classmethod
    def INPUT_TYPES(cls):
        from .prompt_nodes import local_models
        models=[name for name in local_models() if 'qwen' in name.lower()]
        return {'required':{'原始提示词':('STRING',{'default':'','multiline':True,'lazy':True}),
            '启用优化':('BOOLEAN',{'default':False}),
            '目标模型':(['Z-Image Turbo','Image2.1','GPT Image 2/2.5','Krea-2 Turbo','MOSS-Image'],{'default':'Image2.1'}),
            '语言模型':(models or ['未安装千问模型'],),
            '前置规则':('STRING',{'default':DEFAULT_RULES,'multiline':True,'lazy':True}),
            '输出上限':('INT',{'default':1536,'min':128,'max':8192,'display_name':'输出上限（token）','tooltip':'这是最多生成的token数，不是字数或字节数；1536是上限，不要求每次写满。复杂原稿可提高到8192，并相应提高上下文。'}),
            '上下文':('INT',{'default':8192,'min':4096,'max':65536,'step':1024}),
            '温度':('FLOAT',{'default':0.15,'min':0,'max':1,'step':0.05}),
            'top_p':('FLOAT',{'default':0.9,'min':0.1,'max':1,'step':0.05})},
            'optional':{'详细程度':('INT',{'default':3,'min':1,'max':5,'step':1,'display':'slider'}),
                        '输出语言':(list(OUTPUT_LANGUAGES),{'default':'跟随输入','tooltip':'跟随输入时中文原稿默认中文；也可明确选择中文或英文。图中文字与LoRA触发词保持原样。'}),
                        '种子':('INT',{'default':42,'min':0,'max':2**32-1,'tooltip':'控制本机提示词优化的随机性；0也是固定种子，相同种子和输入可复用缓存。'}),
                        '固定优化结果':('BOOLEAN',{'default':False}),
                        '固定优化文本':('STRING',{'default':'','multiline':True}),
                        '固定原始文本':('STRING',{'default':'','multiline':True})},
                'hidden':{'prompt':'PROMPT','unique_id':'UNIQUE_ID'}}
    RETURN_TYPES=('STRING',)
    RETURN_NAMES=('最终生图提示词',)
    FUNCTION='optimize'
    CATEGORY='Codex/图像工具'

    def check_lazy_status(self, **kwargs):
        if kwargs.get('固定优化结果'):
            return []
        required=['原始提示词']
        if kwargs.get('启用优化'):
            required.append('前置规则')
        return [name for name in required if name in kwargs and kwargs[name] is None]

    def optimize(self,原始提示词=None,启用优化=False,目标模型='Image2.1',语言模型='',前置规则=DEFAULT_RULES,
                 输出上限=1536,上下文=8192,温度=.15,top_p=.9,详细程度=3,输出语言='跟随输入',种子=42,
                 固定优化结果=False,固定优化文本='',固定原始文本='',prompt=None,unique_id=None):
        def finish(text,stats):
            from .output_metadata import remember_prompt_result
            source=固定原始文本 if 固定优化结果 else 原始提示词 or ''
            recorded_source=remember_prompt_result(prompt,unique_id,source,text,optimized=bool(stats.get('optimized')))
            result=_prompt_result(text,stats)
            result['ui']['codex_prompt_source']=[recorded_source if isinstance(recorded_source,str) else source]
            return result
        if 固定优化结果:
            if not isinstance(固定优化文本,str) or not 固定优化文本.strip():
                raise ValueError('尚无可固定的优化结果，请先完成一次提示词优化。')
            text=固定优化文本
            stats={**_prompt_stats(text,output_language(text),cached=True),
                   'optimized':True,'frozen':True,'model_load_ns':0,'prompt_eval_ns':0,'output_eval_ns':0}
            return finish(text,stats)
        原始提示词='' if 原始提示词 is None else 原始提示词
        if type(种子) is not int or not 0<=种子<=2**32-1:raise ValueError('提示词优化种子应为0～4294967295的整数。')
        language=output_language(原始提示词,输出语言)
        if not 启用优化:return finish(原始提示词,{**_prompt_stats(原始提示词,language),'seed':种子,'optimized':False,'frozen':False})
        if not 原始提示词.strip():raise ValueError('请填写原始提示词。')
        _validate_budget(输出上限,上下文)
        system=optimization_system(目标模型,前置规则,详细程度,language)
        from .prompt_nodes import LocalOllama,local_models
        from .resource_control import release_resources
        if 语言模型 not in local_models():raise ValueError('请选择本机已安装的千问模型。')
        settings={'revision':PROMPT_REVISION,'detail':详细程度,'source':原始提示词,'target':目标模型,'model':语言模型,'rules':system,
                  'predict':输出上限,'context':上下文,'temperature':温度,'top_p':top_p,
                  'language_selection':输出语言,'language':language,'seed':种子}
        key=hashlib.sha256(json.dumps(settings,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        import folder_paths
        directory=Path(folder_paths.get_user_directory())/'codex-cache'/'prompt-optimize'
        cached=directory/(key+'.json')
        if cached.is_file():
            saved=json.loads(cached.read_text(encoding='utf-8'));text=saved.get('text')
            if isinstance(text,str) and text.strip() and not language_mismatch(text,language,原始提示词):
                stats=saved.get('stats') if isinstance(saved.get('stats'),dict) else _prompt_stats(text,language)
                stats={**stats,'cached':True,'truncated':False,'language':language,'seed':种子,
                       'optimized':True,'frozen':False,'model_load_ns':0,'prompt_eval_ns':0,'output_eval_ns':0}
                return finish(preserve_image_slots(原始提示词,text),stats)
        release_resources(full=True)
        client=LocalOllama()
        try:
            client.start()
            try:
                messages=[{'role':'system','content':system},
                          {'role':'user','content':json.dumps({'目标模型':目标模型,'输出语言':language,'原始提示词':原始提示词},ensure_ascii=False)}]
                # Keep the same model only for a possible immediate language correction.
                # The finally block unloads it before downstream image encoding starts.
                payload={'model':语言模型,'stream':False,'think':False,'keep_alive':30,'format':OUTPUT_SCHEMA,
                    'messages':messages,
                    'options':{'temperature':温度,'top_p':top_p,'num_ctx':上下文,'num_predict':输出上限,'seed':种子}}
                correction_passes=0
                response=client.request('/api/chat',payload,timeout=600)
                if response.get('done_reason') in ('length','max_tokens'):raise ValueError('提示词优化达到输出token上限而被截断，请提高输出上限，并为上下文预留输入空间。')
                text=final_prompt(response.get('message',{}).get('content',''),目标模型)
                if language_mismatch(text,language,原始提示词):
                    correction_passes=1
                    # One local correction only; never silently pass an English body to a Chinese request.
                    correction={'role':'user','content':f'上一份正文没有遵守{language}输出要求。请只纠正正文语言为{language}，不扩写、不删减需求，不改变图号、LoRA触发词和图中文字；只返回相同结构的prompt JSON。'}
                    payload['messages']=[*messages,{'role':'assistant','content':json.dumps({'prompt':text},ensure_ascii=False)},correction]
                    response=client.request('/api/chat',payload,timeout=600)
                    if response.get('done_reason') in ('length','max_tokens'):raise ValueError('提示词语言修正达到输出token上限而被截断，请提高输出上限后重试。')
                    text=final_prompt(response.get('message',{}).get('content',''),目标模型)
                    if language_mismatch(text,language,原始提示词):raise ValueError(f'本机语言模型两次未遵守{language}输出要求，未将错误语言的正文传给生图。请检查前置规则或明确选择输出语言后重试。')
                text=preserve_image_slots(原始提示词,text)
                stats={**_prompt_stats(text,language,response),'seed':种子,'correction_passes':correction_passes,'optimized':True,'frozen':False}
                directory.mkdir(parents=True,exist_ok=True)
                temp=directory/(key+'.'+uuid.uuid4().hex+'.tmp')
                temp.write_text(json.dumps({'text':text,'stats':stats},ensure_ascii=False),encoding='utf-8');temp.replace(cached)
                return finish(text,stats)
            finally:client.unload(语言模型)
        finally:client.close_owned()


NODE_CLASS_MAPPINGS={'CodexPromptOptimize':CodexPromptOptimize}
NODE_DISPLAY_NAME_MAPPINGS={'CodexPromptOptimize':'千问提示词优化 · 可关闭直通'}
