import {hideNativeWidget} from './nodes2_dom_compat.js';
import {app} from '/scripts/app.js';
import {changeUpscaleSetting, scalePairs, totalScales} from './reference_upscale.js';
import {imageModels,modelFamily,cloudCapabilities} from './cloud_models.js';
import {createImageSamplingControls} from './image_sampling_controls.js';

const field = (node, name) => node?.widgets?.find(value => value.name === name);
const levels = ['精简', '简洁', '标准', '详细', '极详细'];
const profileNames = {steps:'步数', cfg:'CFG', sampler:'采样器', scheduler:'调度器', seed:'种子', sigmas:'Turbo 日程'};
const profileKinds = ['normal', 'turbo', 'moss', 'zimage', 'krea'];
const routeKinds = ['normal', 'turbo', 'moss', 'bbapi', 'zimage', 'krea'];
const routeNames = {normal:'普通版', turbo:'Turbo', moss:'MOSS', bbapi:'BBAPI', zimage:'Z-Image', krea:'Krea 2 Turbo'};
const schemaCache = new Map();
const repairWeightCache = new Map();
const canvasDimensionCache = new Map();
const canvasNames = ['比例','尺寸基准','长边','自定义宽度','自定义高度','最长边上限'];
const canvasTypes = new Set(['CodexStudioCanvas','CodexImageCanvas']);
const roundEven = value => {const low=Math.floor(value),fraction=value-low;return fraction===0.5?(low%2===0?low:low+1):Math.round(value);};
function resolveStudioDimensions({ratio,basis='最长边',edge=1024,width=1024,height=1024,limit=4096}) {
    let w=Number(width),h=Number(height);
    if(ratio!=null){const r=Number(ratio);if(!Number.isFinite(r)||r<=0)return null;[w,h]=basis==='最短边'?(r>=1?[edge*r,edge]:[edge,edge/r]):(r>=1?[edge,edge/r]:[edge*r,edge]);}
    if(!Number.isFinite(w)||!Number.isFinite(h)||w<=0||h<=0)return null;
    const scale=Math.min(1,Number(limit)/Math.max(w,h));
    return {width:Math.max(64,Math.min(4096,roundEven(w*scale/32)*32)),height:Math.max(64,Math.min(4096,roundEven(h*scale/32)*32)),limited:scale<1,rawLongest:Math.max(w,h)};
}
const el = (tag, className, text) => {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text != null) item.textContent = text;
    return item;
};
const valuesOf = widget => typeof widget?.options?.values === 'function' ? widget.options.values() : widget?.options?.values;
function style() {
    if (document.getElementById('qwen-studio-controls-style')) return;
    const sheet = el('style'); sheet.id = 'qwen-studio-controls-style';
    sheet.textContent = `
    .qws-controls{box-sizing:border-box;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0;width:100%;height:100%;min-height:0;overflow:auto;color:var(--input-text,#ddd);font:13px/1.4 sans-serif;container-type:inline-size}
    .qws-controls *{box-sizing:border-box}.qws-controls [hidden]{display:none!important}
    .qws-controls .qws-column{min-width:0;padding:10px 14px;overflow:auto;scrollbar-width:thin;border-right:1px solid var(--border-color,#444)}
    .qws-controls .qws-column:last-child{border-right:0}.qws-controls h3{font-size:15px;font-weight:600;margin:0 0 12px;padding:2px 0 10px;border-bottom:1px solid var(--border-color,#444)}
    .qws-controls .qws-row{display:grid;grid-template-columns:minmax(76px,34%) minmax(0,1fr);align-items:center;gap:8px;margin:0 0 9px;min-height:30px}
    .qws-controls .qws-row-label{min-width:0;overflow-wrap:anywhere}.qws-controls .qws-wide{grid-template-columns:1fr;gap:5px}
    .qws-controls input,.qws-controls select,.qws-controls button{font:inherit;color:inherit;min-width:0;max-width:100%;min-height:30px;background:var(--comfy-input-bg,#292929);border:1px solid var(--border-color,#555);border-radius:3px;padding:4px 6px}
    .qws-controls input,.qws-controls select{width:100%}.qws-controls input:focus-visible,.qws-controls select:focus-visible,.qws-controls button:focus-visible,.qws-controls summary:focus-visible{outline:2px solid #9c81e7;outline-offset:2px}
    .qws-controls button{cursor:pointer}.qws-controls button:disabled,.qws-controls input:disabled,.qws-controls select:disabled{opacity:.45;cursor:default}
    .qws-controls .qws-switch{display:flex;align-items:center;gap:9px;width:100%;height:31px;border:0;padding:3px 4px;background:transparent;text-align:left}
    .qws-switch-track{position:relative;display:block;flex:0 0 35px;height:19px;border-radius:10px;background:#555;transition:background .18s}
    .qws-switch-track:before{content:'';position:absolute;left:2px;top:2px;width:15px;height:15px;background:#ddd;border-radius:50%;transition:transform .18s}
    .qws-switch[aria-checked=true] .qws-switch-track{background:#647fe0}.qws-switch[aria-checked=true] .qws-switch-track:before{transform:translateX(16px);background:#fff}
    .qws-controls .qws-segments{position:relative;display:flex;isolation:isolate;width:100%;min-height:33px;padding:2px;border:1px solid var(--border-color,#555);border-radius:4px;background:var(--comfy-input-bg,#292929)}
    .qws-segments .qws-indicator{position:absolute;z-index:-1;top:3px;bottom:3px;left:3px;width:calc((100% - 6px)/var(--count));border-radius:2px;background:#657bc34d;transform:translateX(calc(var(--selected)*100%));transition:transform .2s cubic-bezier(.2,.8,.2,1)}
    .qws-controls .qws-segments button{flex:1;min-height:27px;line-height:1.25;font-size:12px;background:transparent;border:0;border-radius:2px;padding:3px 4px;overflow-wrap:anywhere}.qws-controls .qws-model-route button{overflow-wrap:normal;word-break:normal;min-height:34px;font-size:11px}
    .qws-controls .qws-segments button[aria-checked=true]{color:#eef1ff}.qws-controls .qws-detail{display:grid;grid-template-columns:minmax(0,1fr) 40px;gap:8px;align-items:center}
    .qws-controls .qws-source-grid{display:flex;flex-direction:column;gap:5px}.qws-controls .qws-segment-spacer{flex:1;min-width:0}.qws-controls .qws-add-lora{width:100%;margin:5px 0 10px}
    .qws-controls .qws-canvas-presets{margin-top:0;margin-bottom:10px}.qws-controls .qws-dimensions{color:#b5c6ec;font-size:12px;line-height:1.5;margin:5px 0 2px}.qws-controls .qws-canvas-presets button{white-space:nowrap}
    .qws-controls .qws-seed-tools{grid-column:1/-1;display:flex;align-items:center;gap:6px;min-width:0}.qws-seed-tools .qws-segments{flex:1;min-width:0}.qws-controls .qws-seed-tools>.qws-new-seed{flex:0 0 auto;font-size:12px;white-space:nowrap}
    .qws-controls input[type=range]{appearance:none;-webkit-appearance:none;height:24px;border:0;padding:0;background:transparent;cursor:pointer}
    .qws-controls input[type=range]::-webkit-slider-runnable-track{height:9px;border-radius:5px;background:linear-gradient(90deg,#5369db,#9a66ec,#bb87ed)}
    .qws-controls input[type=range]::-webkit-slider-thumb{appearance:none;-webkit-appearance:none;width:18px;height:18px;border-radius:50%;background:#fff;margin-top:-4.5px;border:0;box-shadow:0 1px 4px #0007}
    .qws-controls input[type=range]::-moz-range-track{height:9px;border-radius:5px;background:linear-gradient(90deg,#5369db,#9a66ec,#bb87ed)}
    .qws-controls input[type=range]::-moz-range-thumb{width:18px;height:18px;border-radius:50%;background:#fff;border:0}.qws-detail output{color:#bd9bff;font-size:12px;text-align:right}
    .qws-controls details{margin:9px 0 12px;border-top:1px solid var(--border-color,#444);padding-top:5px}
    .qws-controls summary{display:flex;align-items:center;justify-content:space-between;gap:8px;min-height:35px;list-style:none;cursor:pointer;user-select:none}
    .qws-controls summary::-webkit-details-marker{display:none}.qws-controls summary>span:first-child:before{content:'▸';margin-right:7px;color:#aaa}.qws-controls details[open]>summary>span:first-child:before{content:'▾'}
    .qws-controls summary .qws-switch{width:90px}.qws-controls .qws-body{padding-top:8px}.qws-controls .qws-subheading{font-size:12px;color:#aaa;border-top:1px solid var(--border-color,#444);padding-top:9px;margin:10px 0}
    .qws-controls .qws-use-profile{width:100%;margin-top:3px}.qws-controls .qws-profile-active{border-color:#788dde;color:#d9e1ff}.qws-controls .qws-status{font-size:12px;color:#aaa;margin:5px 0 9px}.qws-controls .qws-error{color:#efb1a9}
    .qws-controls[data-columns='2']{grid-template-columns:repeat(2,minmax(0,1fr))}.qws-controls[data-columns='2']>.qws-column:last-child{grid-column:1/-1;border-top:1px solid var(--border-color,#444)}
    .qws-controls[data-columns='1']{grid-template-columns:minmax(0,1fr)}.qws-controls[data-columns='1']>.qws-column{border-right:0;border-bottom:1px solid var(--border-color,#444)}
    @media(prefers-reduced-motion:reduce){.qws-indicator,.qws-switch-track,.qws-switch-track:before{transition:none}}
    `;
    document.head.append(sheet);
}

async function schema(type, refresh = false) {
    const saved = schemaCache.get(type);
    if (saved && !refresh) return saved;
    const pending = fetch(`/object_info/${encodeURIComponent(type)}`, {cache:'no-store', signal:AbortSignal.timeout(8000)})
        .then(response => {if (!response.ok) throw Error('读取模型列表失败');return response.json();})
        .then(result => result[type]);
    schemaCache.set(type, pending);
    try {const result = await pending;schemaCache.set(type, result);return result;}
    catch(error) {schemaCache.delete(type);throw error;}
}

function numericValue(raw, current, options = {}) {
    if (raw === '' || !Number.isFinite(Number(raw))) return current;
    let value = Number(raw);
    if (Number.isFinite(options.min)) value = Math.max(options.min, value);
    if (Number.isFinite(options.max)) value = Math.min(options.max, value);
    if (Number.isFinite(options.step) && options.step > 0) {
        const origin = Number.isFinite(options.min) ? options.min : 0;
        value = Number((origin + Math.round((value - origin) / options.step) * options.step).toPrecision(12));
    }
    if (Number.isFinite(options.max)) value = Math.min(options.max, value);
    return value;
}

function install(node) {
    style();
    const config = field(node, '控件映射');
    if (!config) return;
    hideNativeWidget(config); config.computeSize = () => [0, -4];
    if (config.inputEl) config.inputEl.style.display = 'none';
    const root = el('div', 'qws-controls');
    root.setAttribute('aria-label', '统一控制中心');
    root.addEventListener('pointerdown', event => {event.stopPropagation();app.canvas?.bringToFront?.(node);});
    root.addEventListener('keydown', event => event.stopPropagation());
    root.addEventListener('wheel', event => event.stopPropagation(), {passive:true});
    let disposed = false, mapping = {}, mappingKey = null, refreshers = [], bindings = [], slots = [], applying = false;
    let stateIdentity = null, profileSourceRefresh = () => {}, visibleLoras = 6;
    const samplingPanels = [];
    const canvasControllers = new Map();
    let seedQueuePending=false;
    const secretInputs = new Set();
    const callbackRestorers = [];
    const getTarget = item => node.graph?.getNodeById(item.node);
    const getField = item => field(getTarget(item), item.widget);
    const dirty = () => node.graph?.setDirtyCanvas?.(true, true);
    const route = () => {
        const spec = mapping.route;
        const selected = spec && getField(spec)?.value;
        if(Array.isArray(spec?.values)){const index=spec.values.indexOf(selected);if(index>=0&&index<routeKinds.length)return routeKinds[index];}
        else if(spec?.values&&typeof spec.values==='object'){
            if(routeKinds.includes(spec.values[selected]))return spec.values[selected];
            const mapped=routeKinds.find(kind=>spec.values[kind]===selected);if(mapped)return mapped;
        }
        if(selected===(spec?.krea_value||'Krea 2 Turbo'))return 'krea';
        if(selected===(spec?.zimage_value||'Z-Image'))return 'zimage';
        if(selected===(spec?.bbapi_value||'BBAPI'))return 'bbapi';
        if(selected===(spec?.moss_value||'MOSS'))return 'moss';
        return selected === (spec?.turbo_value || 'Qwen Image 2.1 Turbo') ? 'turbo' : 'normal';
    };
    function syncOptimizationTarget(){
        if(mapping.prompt_optimize_node==null)return;
        const selected=field(node.graph?.getNodeById(mapping.prompt_optimize_node),'目标模型');
        const target={normal:'Image2.1',turbo:'Image2.1',moss:'MOSS-Image',bbapi:'GPT Image 2/2.5',zimage:'Z-Image Turbo',krea:'Krea-2 Turbo'}[route()];
        if(selected&&selected.value!==target){selected.value=target;if(selected.inputEl)selected.inputEl.value=target;selected.callback?.(target);}
    }
    function watchRouteCallback(){
        const routeField=mapping.route&&getField(mapping.route);if(!routeField)return;
        const previous=routeField.callback;
        const watched=function(){const value=previous?.apply(this,arguments);syncOptimizationTarget();return value;};
        routeField.callback=watched;callbackRestorers.push(()=>{if(routeField.callback===watched)routeField.callback=previous;});
    }
    const condition = item => {
        const repair=getTarget(item);
        if(repair?.type==='CodexQwenStudioRepair'){
            const engine=field(repair,'修复引擎')?.value;
            if(item.widget.startsWith('Image2')&&engine!=='Image2.1')return false;
            if(item.widget.startsWith('Image2')&&field(repair,'使用外接配置')?.value===true)return false;
            if(item.widget.startsWith('Krea')&&engine!=='Krea 2')return false;
            if(item.widget.startsWith('DLSS')&&engine!=='DLSS5')return false;
            if(engine==='DLSS5'&&['修复规则','补充要求','负面提示词','种子','使用外接配置'].includes(item.widget))return false;
            if(item.widget==='使用外接配置'&&engine!=='Image2.1')return false;
            if(['输入长边','输出长边'].includes(item.widget)&&field(repair,item.widget==='输入长边'?'输入尺寸':'输出尺寸')?.value!=='自定义')return false;
        }
        if (item.profile_route && item.profile_route !== route()) return false;
        const specs = item.condition ? Array.isArray(item.condition) ? item.condition : [item.condition] : [];
        return specs.every(spec => {
            const value = getField(spec)?.value;
            if (Object.hasOwn(spec, 'not')) return value !== spec.not;
            return Array.isArray(spec.equals) ? spec.equals.includes(value) : value === spec.equals;
        });
    };
    const isSeedItem=item=>item.profile_key==='seed'||item.widget==='种子'&&getTarget(item)?.type==='CodexPromptOptimize';
    const seedKey=item=>`${item.node}:${item.widget}`;
    function seedMode(item){node.properties||={};node.properties.codex_seed_modes||={};return node.properties.codex_seed_modes[seedKey(item)]==='random'?'random':'fixed';}
    function nativeFixed(item){for(const widget of getTarget(item)?.widgets||[])if(widget.name==='control_after_generate'||widget.name===`${item.widget}_control_after_generate`){if(widget.value!=='fixed'){widget.value='fixed';widget.callback?.('fixed');}}}
    function setSeedMode(item,value){node.properties||={};node.properties.codex_seed_modes||={};node.properties.codex_seed_modes[seedKey(item)]=value==='random'?'random':'fixed';nativeFixed(item);dirty();}
    function newSeed(previous){const random=()=>{if(globalThis.crypto?.getRandomValues){const values=new Uint32Array(1);globalThis.crypto.getRandomValues(values);return values[0];}return Math.floor(Math.random()*4294967296);};let next=random();if(next===previous)next=random();return next===previous?(Number(previous)+1)>>>0:next;}
    function prepareQueuedSeeds(){
        if(seedQueuePending)return;seedQueuePending=true;Promise.resolve().then(()=>seedQueuePending=false);
        for(const item of mapping.fields||[]){
            if(!isSeedItem(item)||item.profile_route&&item.profile_route!==route())continue;
            const target=getTarget(item),seed=getField(item);if(!seed)continue;nativeFixed(item);
            if(target?.type==='CodexPromptOptimize'&&!field(target,'启用优化')?.value)continue;
            if(seedMode(item)!=='random')continue;seed.value=newSeed(seed.value);if(seed.inputEl)seed.inputEl.value=String(seed.value);seed.callback?.(seed.value);
        }
        syncProfiles();refresh();dirty();
    }
    function canvasPresets(parent,canvas){
        const id=String(canvas.id);if(canvasControllers.has(id))return;
        const row=el('div','qws-row qws-wide qws-canvas-presets'),label=el('span','qws-row-label','尺寸档位'),group=el('div','qws-segments'),indicator=el('span','qws-indicator'),note=el('div','qws-dimensions');
        group.setAttribute('role','radiogroup');group.setAttribute('aria-label','尺寸档位');group.style.setProperty('--count','5');group.append(indicator);note.setAttribute('role','status');row.append(label,group,note);parent.append(row);
        const choices=[[1024,'1K'],[2048,'2K'],[3072,'3K'],[4096,'4K'],['custom','自定义']],buttons=[];
        let applyingSize=false,lastKey='',loadKey='',pending=null,referenceRatio=null;
        const value=name=>field(canvas,name)?.value;
        const set=(name,next,callback=true)=>{const widget=field(canvas,name);if(!widget||widget.value===next)return false;widget.value=next;if(widget.inputEl&&document.activeElement!==widget.inputEl)widget.inputEl.value=String(next);if(callback)widget.callback?.(next);return true;};
        const linkById=id=>canvas.graph?.links?.get?.(id)??canvas.graph?.links?.get?.(String(id))??canvas.graph?.links?.get?.(Number(id))??canvas.graph?.links?.[id];
        function reference(){
            let source=canvas.graph?.getNodeById(canvas.properties?.codex_reference_node);
            if(!source){const input=canvas.inputs?.find(item=>item.name==='参考图集'),link=linkById(input?.link);source=canvas.graph?.getNodeById(link?.origin_id);}
            if(!source)return null;let entries=[];try{entries=JSON.parse(field(source,'参考图库')?.value||'[]');if(!Array.isArray(entries))entries=[];}catch{}
            const known=new Map(entries.map(entry=>[String(entry.id),entry]));
            for(const input of source.inputs||[]){const match=input.name?.match(/^参考图([1-9]\d*)$/);if(!match||input.link==null)continue;const link=linkById(input.link),owner=canvas.graph?.getNodeById(link?.origin_id),output=app.nodeOutputs?.[String(owner?.id)],image=output?.images?.[0]||owner?.imgs?.[0];if(image?.filename)known.set('ref'+match[1],{...image,id:'ref'+match[1]});else if(!known.has('ref'+match[1]))known.set('ref'+match[1],{id:'ref'+match[1],external:true});}
            const match=String(value('比例')).match(/^跟随参考图([1-9]\d*)$/);if(match)return known.get('ref'+match[1])||null;
            const input=source.inputs?.find(item=>item.name==='对比参考图编号'),link=linkById(input?.link),owner=canvas.graph?.getNodeById(link?.origin_id);const primary=Number(field(owner,'value')?.value??field(source,'对比参考图编号')?.value)||1;
            return [...known.values()][Math.max(0,primary-1)]||null;
        }
        function ratio(){
            const selected=String(value('比例'));
            if(selected==='自定义宽高')return null;
            const fixed=selected.match(/^(\d+):(\d+)/);if(fixed)return Number(fixed[1])/Number(fixed[2]);
            if(!selected.startsWith('跟随参考图'))return undefined;
            const entry=reference();if(!entry)return undefined;
            const width=Number(entry.width??entry.metadata?.width),height=Number(entry.height??entry.metadata?.height);if(width>0&&height>0)return width/height;
            if(!entry.filename)return undefined;
            const url='/view?'+new URLSearchParams({filename:entry.filename,subfolder:entry.subfolder||'',type:entry.type||'input'}),saved=canvasDimensionCache.get(url);
            if(saved?.ratio)return saved.ratio;
            if(saved?.failedAt&&Date.now()-saved.failedAt<10000)return undefined;
            if(loadKey!==url){loadKey=url;referenceRatio=null;}
            if(referenceRatio!=null)return referenceRatio;
            if(!pending){
                const request=saved?.pending||new Promise((resolve,reject)=>{const image=new Image();image.onload=()=>image.naturalWidth&&image.naturalHeight?resolve(image.naturalWidth/image.naturalHeight):reject(Error('图片尺寸不可用'));image.onerror=()=>reject(Error('图片尺寸不可用'));image.src=url;});
                canvasDimensionCache.set(url,{pending:request});pending=request;if(canvasDimensionCache.size>64)canvasDimensionCache.delete(canvasDimensionCache.keys().next().value);
                request.then(result=>{canvasDimensionCache.set(url,{ratio:result});if(!disposed&&loadKey===url){referenceRatio=result;lastKey='';update();refresh();}}).catch(()=>{canvasDimensionCache.set(url,{failedAt:Date.now()});}).finally(()=>{if(pending===request)pending=null;});
            }
            return undefined;
        }
        const configKey=()=>JSON.stringify(canvasNames.map(name=>value(name)));
        const dimensions=r=>resolveStudioDimensions({ratio:r,basis:value('尺寸基准'),edge:Number(value('长边')),width:value('自定义宽度'),height:value('自定义高度'),limit:value('最长边上限')});
        const cloudResolution=()=>field(node.graph?.getNodeById(mapping.bbapi_image_node||mapping.bbapi_generator_node),'分辨率');
        function update(){
            const key=configKey(),r=ratio(),custom=value('比例')==='自定义宽高',size=r===undefined?null:dimensions(r);
            if(!custom&&size&&key!==lastKey&&!applyingSize){applyingSize=true;let changed=false;try{changed=set('自定义宽度',size.width,false)||changed;changed=set('自定义高度',size.height,false)||changed;}finally{applyingSize=false;}if(changed)dirty();}
            lastKey=configKey();const requested=Number(value('长边')),savedPreset=canvas.properties?.codex_canvas_size_preset,explicitCustom=savedPreset==='custom'||custom&&savedPreset!==requested,selected=explicitCustom?'custom':choices.some(([edge])=>edge===requested)?requested:'custom';
            const cloud=route()==='bbapi',cloudPreset={'1K':1024,'2K':2048,'4K':4096}[cloudResolution()?.value],active=cloud&&cloudPreset?cloudPreset:selected;
            const index=choices.findIndex(([edge])=>edge===active);group.style.setProperty('--selected',String(Math.max(0,index)));buttons.forEach((button,i)=>{button.setAttribute('aria-checked',String(i===index));button.tabIndex=i===index?0:-1;button.disabled=cloud&&choices[i][0]===3072;button.title=button.disabled?'BBAPI支持1K、2K、4K':'';});
            note.textContent=cloud?`BBAPI ${cloudResolution()?.value||'请选1K、2K或4K'} · 实际尺寸由所选模型决定`:size?`预计 ${size.width} × ${size.height}${size.limited?' · 已按最长边上限缩小':''}`:'上传后自动计算宽高';
        }
        function selectPreset(selected){
            if(route()==='bbapi'&&selected===3072)return;
            if(route()==='bbapi'&&selected!=='custom'){const resolution=cloudResolution(),next={1024:'1K',2048:'2K',4096:'4K'}[selected];if(resolution&&next){resolution.value=next;resolution.callback?.(next);}}
            if(selected==='custom'){applyingSize=true;try{set('比例','自定义宽高');canvas.properties||={};canvas.properties.codex_canvas_size_preset='custom';}finally{applyingSize=false;}lastKey='';update();refresh();dirty();return;}
            const r=ratio();applyingSize=true;try{canvas.properties||={};delete canvas.properties.codex_canvas_size_preset;set('长边',selected);if(r!==undefined){const targetRatio=r==null?Number(value('自定义宽度'))/Number(value('自定义高度')):r;const desired=resolveStudioDimensions({ratio:targetRatio,basis:value('尺寸基准'),edge:selected,limit:4096});if(desired){const currentLimit=Number(value('最长边上限'));set('最长边上限',Math.min(4096,Math.max(currentLimit,Math.ceil(desired.rawLongest/32)*32)));set('自定义宽度',desired.width);set('自定义高度',desired.height);if(r===null){const nearest=choices.find(([edge])=>edge===selected);canvas.properties.codex_canvas_size_preset=nearest?.[0];}}}}finally{applyingSize=false;}lastKey='';update();refresh();dirty();
        }
        for(const[selected,title]of choices){const button=el('button','',title);button.type='button';button.setAttribute('role','radio');button.onclick=event=>{event.preventDefault();selectPreset(selected);};buttons.push(button);group.append(button);}
        group.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const index=Math.max(0,buttons.findIndex(button=>button.getAttribute('aria-checked')==='true'));let next=event.key==='Home'?0:event.key==='End'?choices.length-1:(index+(event.key==='ArrowRight'?1:-1)+choices.length)%choices.length;while(buttons[next].disabled)next=(next+(event.key==='ArrowLeft'?-1:1)+choices.length)%choices.length;selectPreset(choices[next][0]);buttons[next].focus();};
        for(const name of canvasNames){const widget=field(canvas,name);if(!widget)continue;const previous=widget.callback;const watched=function(){const result=previous?.apply(this,arguments);if(applyingSize)return result;applyingSize=true;try{if(name==='自定义宽度'||name==='自定义高度'){set('比例','自定义宽高');canvas.properties||={};canvas.properties.codex_canvas_size_preset='custom';}else if(name==='比例'||name==='长边'){canvas.properties||={};delete canvas.properties.codex_canvas_size_preset;}}finally{applyingSize=false;}lastKey='';update();return result;};widget.callback=watched;callbackRestorers.push(()=>{if(widget.callback===watched)widget.callback=previous;});}
        canvasControllers.set(id,{update});refreshers.push(update);update();
    }
    const state = () => {
        node.properties ||= {};
        let value = node.properties.codex_qwen_sampling_profiles;
        if (!value || value.version !== 1) value = node.properties.codex_qwen_sampling_profiles = {version:1, sources:{}, base:{}, slots:{}};
        value.sources ||= {};value.base ||= {};value.slots ||= {};
        for (const kind of profileKinds) {
            value.base[kind] ||= {};
            if (value.sources[kind] !== 'base' && !slots.includes(String(value.sources[kind]).replace('lora-', ''))) value.sources[kind] = 'base';
        }
        for (const binding of bindings) {
            const kind = binding.profile_route || 'normal', key = binding.profile_key;
            if (!Object.hasOwn(value.base[kind], key)) value.base[kind][key] = getField(binding)?.value;
            for (const slot of slots) {
                value.slots[slot] ||= {};value.slots[slot][kind] ||= {};
                if (!Object.hasOwn(value.slots[slot][kind], key)) value.slots[slot][kind][key] = value.base[kind][key];
            }
        }
        return value;
    };
    const selectedValues = (value, kind) => value.sources[kind] === 'base' ? value.base[kind] : value.slots[value.sources[kind].slice(5)][kind];
    function syncProfiles() {
        if (applying || !bindings.length) return;
        const value = state();
        for (const item of bindings) {
            const target = getField(item);
            if (target) selectedValues(value, item.profile_route || 'normal')[item.profile_key] = target.value;
        }
    }
    function applyProfile(source, kind = route(), capture = true) {
        if(!profileKinds.includes(kind))return;
        if (capture) syncProfiles();
        const value = state();value.sources[kind] = source;
        applying = true;
        try {
            const selected = selectedValues(value, kind);
            for (const item of bindings.filter(item => (item.profile_route || 'normal') === kind)) {
                const target = getField(item);
                if (target && Object.hasOwn(selected, item.profile_key)) {target.value = selected[item.profile_key];target.callback?.(target.value);}
            }
        } finally {applying = false;}
        profileSourceRefresh();refresh();dirty();
    }
    function syncVisibleLoras(){
        const requested=Number(node.properties?.codex_qwen_visible_loras);
        let count=Math.min(slots.length,Math.max(0,Number.isFinite(requested)?Math.floor(requested):6));
        for(const item of mapping.fields||[]){
            if(!item.slot&&item.section!=='lora')continue;
            if(getField(item)?.value!==true)continue;
            const slot=String(item.slot||String(item.label).match(/LoRA\s*(\d+)/i)?.[1]||1);
            count=Math.max(count,slots.indexOf(slot)+1);
        }
        for(const source of Object.values(state().sources)){if(source!=='base')count=Math.max(count,slots.indexOf(String(source).replace('lora-',''))+1);}
        visibleLoras=Math.min(slots.length,count);node.properties.codex_qwen_visible_loras=visibleLoras;
    }
    function write(item, next) {
        const target = getField(item);
        if (!target) return;
        target.value = next;
        if (target.inputEl) target.inputEl.value = String(next);
        target.callback?.(next);
        const owner=getTarget(item);
        if(owner?.type==='CodexQwenStudioUpscale'&&['超分路线','总倍率','DLSS倍率','VOSR倍率'].includes(item.widget)){
            const value=name=>field(owner,name)?.value;
            const keys={'超分路线':'algorithm','总倍率':'total_scale','DLSS倍率':'dlss_scale','VOSR倍率':'vosr_scale'};
            const config={algorithm:value('超分路线'),total_scale:Number(value('总倍率')),dlss_scale:Number(value('DLSS倍率')),vosr_scale:Number(value('VOSR倍率'))};
            let fixed=changeUpscaleSetting(config,keys[item.widget],next);
            if(fixed.algorithm==='DLSS5→VOSR2'){
                if(!scalePairs(fixed.algorithm).some(pair=>Math.abs(pair.total-fixed.total_scale)<1e-6&&pair.dlss===fixed.dlss_scale&&pair.vosr===fixed.vosr_scale)){
                    if(item.widget==='总倍率'||item.widget==='超分路线'){
                        const pair=scalePairs(fixed.algorithm).find(pair=>Math.abs(pair.total-fixed.total_scale)<1e-6)||scalePairs(fixed.algorithm).find(pair=>pair.total===2);
                        fixed={...fixed,total_scale:pair.total,dlss_scale:pair.dlss,vosr_scale:pair.vosr};
                    }else fixed.total_scale=Number((fixed.dlss_scale*fixed.vosr_scale).toFixed(3));
                }
            }else{fixed.total_scale=Math.max(1,Math.min(4,Math.round(fixed.total_scale)));fixed.dlss_scale=1;fixed.vosr_scale=fixed.total_scale;}
            for(const[name,key]of Object.entries(keys)){const w=field(owner,name);if(w)w.value=fixed[key];}
            const total=field(owner,'总倍率');if(total){total.options||={};total.options.values=fixed.algorithm==='DLSS5→VOSR2'?totalScales(fixed.algorithm):[1,2,3,4];}
        }
        syncProfiles();refresh();dirty();
    }
    function refresh() {syncOptimizationTarget();syncVisibleLoras();for (const update of refreshers) update();profileSourceRefresh();}
    function fold(parent, key, title) {
        const details = el('details'), summary = el('summary'), heading = el('span', '', title), body = el('div', 'qws-body');
        details.dataset.controlGroup = key;summary.append(heading);details.append(summary, body);parent.append(details);
        node.properties ||= {};node.properties.codex_qwen_folds ||= {};
        details.open = node.properties.codex_qwen_folds[key] === true;
        details.addEventListener('toggle', () => {node.properties.codex_qwen_folds[key] = details.open;dirty();});
        return {details, summary, body};
    }
    function bbapiPanel(parent){
        const connection=node.graph?.getNodeById(mapping.bbapi_connection_node);
        if(!connection)return null;
        const section=fold(parent,'bbapi-connection','BBAPI 连接');
        if(!Object.hasOwn(node.properties.codex_qwen_folds,'bbapi-connection'))section.details.open=true;
        const configFields=el('div'),password=el('input'),actions=el('div'),save=el('button','','保存密钥'),load=el('button','','刷新模型'),families=el('div','qws-segments'),models=el('div','qws-cloud-models'),status=el('div','qws-status','尚未检查本机配置'),capability=el('div','qws-status');
        section.fields=configFields;section.params=el('div');
        password.type='password';password.autocomplete='off';password.placeholder='输入 API Key';password.setAttribute('aria-label','BBAPI 密钥');secretInputs.add(password);
        save.type=load.type='button';actions.style.cssText='display:flex;gap:6px;margin:8px 0';actions.append(save,load);
        models.setAttribute('aria-label','BBAPI 生图模型');families.setAttribute('role','radiogroup');families.setAttribute('aria-label','云端模型类别');models.setAttribute('role','radiogroup');models.style.cssText='display:flex;flex-wrap:wrap;gap:5px;margin:8px 0';
        const website=el('a','','BBAPI Key 获取');website.href='https://bbapi.ainivox.cn';website.target='_blank';website.rel='noopener noreferrer';website.style.cssText='display:inline-block;margin:0 8px 9px 0;color:#98baff;text-decoration:underline';
        const offer=el('span','qws-status','GPT2.5模型低至0.025每张');
        const settings=el('details'),settingsTitle=el('summary','','连接设置');settings.append(settingsTitle,configFields,password,actions);
        section.body.append(website,offer,families,models,capability,section.params,settings,status);
        const profile=()=>({profile:String(field(connection,'配置名称')?.value||''),base_url:String(field(connection,'接口地址')?.value||''),default_model:String(field(connection,'默认模型')?.value||'')});
        const profileIdentity=()=>JSON.stringify([profile().profile,profile().base_url]);
        const localRequest=async(path,body)=>{
            if(!['127.0.0.1','localhost','::1','[::1]'].includes(globalThis.location?.hostname))throw Error('密钥配置仅可在本机页面操作。');
            const response=await fetch(path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:undefined,body:body?JSON.stringify(body):undefined,credentials:'same-origin',redirect:'error',cache:'no-store',signal:AbortSignal.timeout(30000)});
            const payload=await response.json();if(!response.ok)throw Error(payload.error||`请求失败（${response.status}）`);return payload;
        };
        let checkedProfile=null,busy=false,statusRevision=0,entries=[],selectedFamily='',lastAutoIdentity='',lastModel='';
        const generator=()=>node.graph?.getNodeById(mapping.bbapi_image_node||mapping.bbapi_generator_node)||(mapping.fields||[]).map(getTarget).find(target=>field(target,'模型')&&(field(target,'分辨率')||field(target,'质量')));
        const currentModel=()=>String(field(generator(),'模型')?.value||profile().default_model||'');
        const syncCapabilities=()=>{const model=currentModel(),caps=cloudCapabilities(model),owner=generator();capability.textContent=model?caps.note:'连接成功后自动读取可用图像模型';
            const resolution=field(owner,'分辨率');if(resolution){resolution.options||={};resolution.options.values=caps.resolutions;if(!caps.resolutions.includes(resolution.value))resolution.value=caps.resolutions.at(-1);}
            for(const row of section.body.querySelectorAll('.qws-row[data-node]')){if(row.dataset.node!==String(owner?.id))continue;const name=row.dataset.widget;row.hidden=name==='模型'||name==='质量'&&!caps.quality||!['模型','分辨率','宽度','高度','比例','质量','批量数量','并发上限','重新生成编号','输出模式'].includes(name);}
        };
        const selectModel=value=>{write({node:connection.id,widget:'默认模型'},value);const owner=generator();if(owner)write({node:owner.id,widget:'模型'},value);selectedFamily=modelFamily(value);renderModels();syncCapabilities();};
        const renderModels=()=>{const categories=[...new Set(entries.map(item=>modelFamily(item.id)))];if(!categories.includes(selectedFamily))selectedFamily=categories.includes(modelFamily(currentModel()))?modelFamily(currentModel()):categories[0]||'';
            families.replaceChildren(...categories.map(name=>{const button=el('button','',name);button.type='button';button.setAttribute('role','radio');button.setAttribute('aria-checked',String(name===selectedFamily));button.style.background=name===selectedFamily?'#657bc34d':'';button.onclick=()=>{selectedFamily=name;renderModels();};return button;}));
            models.replaceChildren(...entries.filter(item=>modelFamily(item.id)===selectedFamily).map(item=>{const button=el('button','',item.display_name||item.id);button.type='button';button.title=item.id;button.setAttribute('role','radio');button.setAttribute('aria-checked',String(item.id===currentModel()));button.style.cssText='font-size:12px;flex:1 1 120px;min-width:0;overflow-wrap:anywhere';if(item.id===currentModel())button.style.borderColor='#92a9ff';button.onclick=()=>selectModel(item.id);return button;}));
        };
        const loadModels=async(force=false)=>{const identity=profileIdentity();if(busy||!force&&lastAutoIdentity===identity)return;busy=true;lastAutoIdentity=identity;const revision=++statusRevision;save.disabled=load.disabled=true;status.textContent='正在读取生图模型…';
            try{const result=await localRequest('/bbapi-private/models',profile());if(disposed||revision!==statusRevision||identity!==profileIdentity())return;entries=imageModels(result.models);renderModels();if(entries.length&&!entries.some(item=>item.id===currentModel()))selectModel(entries[0].id);syncCapabilities();status.textContent=`已读取 ${entries.length} 个生图模型。`;}
            catch(error){if(!disposed&&revision===statusRevision)status.textContent=error.message||'读取生图模型失败，可手动刷新。';}
            finally{busy=false;save.disabled=load.disabled=false;}
        };
        const statusProfile=async()=>{const name=profile().profile,revision=++statusRevision;checkedProfile=profileIdentity();try{const result=await localRequest('/bbapi-private/profiles');if(disposed||profile().profile!==name||revision!==statusRevision)return;const saved=(result.profiles||[]).some(item=>item.name===name);status.textContent=saved?'此配置已保存本机密钥。':'尚未保存此配置的密钥。';settings.open=!saved;if(saved)await loadModels();}catch(error){if(!disposed&&revision===statusRevision)status.textContent=error.message||'配置暂时无法读取。';}};
        save.onclick=async()=>{
            if(busy)return;if(!password.value?.trim()){status.textContent='请先输入 API Key。';return;}
            busy=true;statusRevision++;save.disabled=load.disabled=true;
            try{await localRequest('/bbapi-private/profiles',{...profile(),api_key:password.value.trim()});password.value='';status.textContent='密钥已加密保存到本机。';checkedProfile=profileIdentity();lastAutoIdentity='';settings.open=false;}
            catch{status.textContent='密钥保存失败，请检查本机连接配置。';}
            finally{busy=false;save.disabled=load.disabled=false;await loadModels();}
        };
        load.onclick=()=>loadModels(true);
        section.sync=syncCapabilities;
        refreshers.push(()=>{section.details.hidden=route()!=='bbapi';if(route()==='bbapi'){if(checkedProfile!==profileIdentity()&&!busy)statusProfile();const model=currentModel();if(lastModel!==model){lastModel=model;renderModels();}syncCapabilities();}});
        return section;
    }
    function makeControl(parent, item, custom) {
        const target = getField(item);if (!target) return null;
        const display = item.display || (item.widget === '色彩校正' ? {wavelet:'小波', adain:'统计', none:'关闭'} : item.widget === '模型路线' ? {'Qwen Image 2.1':'Qwen 2.1','Qwen Image 2.1 Turbo':'Qwen Turbo',MOSS:'MOSS',BBAPI:'BBAPI','Z-Image':'Z-Image','Krea 2 Turbo':'Krea 2'} : {});
        const read = custom?.read || (() => getField(item)?.value);
        const commit = custom?.write || (value => write(item, value));
        const controlLabel=item.widget==='输出上限'&&getTarget(item)?.type==='CodexPromptOptimize'?'输出上限（token）':item.label || item.widget;
        const row = el('label', 'qws-row'), label = el('span', 'qws-row-label', controlLabel);
        const weightField=getTarget(item)?.type==='CodexQwenStudioRepair'&&/^(Image2|Krea)(模型|编码器|VAE)$/.test(item.widget);
        const weightKey=weightField?item.widget:null;
        const choices = () => weightField?(repairWeightCache.get(weightKey)||[String(read()||'')]):valuesOf(getField(item));
        if(weightField&&!repairWeightCache.has(weightKey)){
            repairWeightCache.set(weightKey,[String(read()||'')]);
            const model=item.widget.endsWith('模型'),encoder=item.widget.endsWith('编码器');
            const loaders=model?[['UNETLoader','unet_name'],['UnetLoaderGGUF','unet_name']]:encoder?[['CLIPLoader','clip_name']]:[['VAELoader','vae_name']];
            Promise.allSettled(loaders.map(async([type,key])=>{const data=await schema(type);return data?.input?.required?.[key]?.[0]||[];})).then(results=>{
                if(disposed)return;const found=results.filter(result=>result.status==='fulfilled').flatMap(result=>Array.isArray(result.value)?result.value:[]).filter(name=>typeof name==='string');
                const filtered=[...new Set(found)].filter(name=>model?(item.widget.startsWith('Krea')?/krea/i.test(name):/qwen[-_]?image[-_]?2[._-]?1/i.test(name)):/qwen/i.test(name));
                repairWeightCache.set(weightKey,filtered.length?filtered.sort((a,b)=>a.localeCompare(b)):[String(read()||'')]);refresh();
            });
        }
        let options = {...target.options, ...item}, current = read(), control, updateValue;
        row.append(label);row.dataset.widget = item.widget;row.dataset.node = String(item.node);
        if (typeof current === 'boolean') {
            control = el('button', 'qws-switch');control.type = 'button';control.setAttribute('role', 'switch');
            const track = el('span', 'qws-switch-track'), text = el('span');control.append(track,text);
            control.onclick = event => {event.preventDefault();commit(!read());refresh();};
            updateValue = () => {const enabled = !!read();control.setAttribute('aria-checked', String(enabled));text.textContent = enabled ? '启用' : '关闭';};
        } else if (item.widget === '详细程度' || item.detail) {
            control = el('div', 'qws-detail');const range = el('input'), output = el('output');
            range.type = 'range';range.min='1';range.max='5';range.step='1';range.setAttribute('aria-label', item.label || '提示词详细程度');
            range.oninput=()=>{commit(Math.max(1,Math.min(5,Math.round(Number(range.value)))));refresh();};
            control.append(range, output);updateValue=()=>{const value=Math.max(1,Math.min(5,Math.round(Number(read())||3)));if(document.activeElement!==range)range.value=String(value);output.textContent=levels[value-1];range.setAttribute('aria-valuetext',levels[value-1]);};
        } else if (Array.isArray(choices()) && (item.segment || (typeof target.options?.values!=='function' && choices().length >= 2 && choices().length <= 4 && !['总倍率','比例'].includes(item.widget) && !/模型|文件|lora|vae|clip|encoder/i.test(item.label+' '+item.widget)))) {
            control = el('div', 'qws-segments');control.setAttribute('role','radiogroup');control.setAttribute('aria-label',item.label || item.widget);
            if(item.widget==='模型路线')control.classList.add('qws-model-route');
            const indicator=el('span','qws-indicator');control.append(indicator);let key='', buttons=[];
            updateValue=()=>{
                const values=choices()||[], signature=JSON.stringify(values);
                if(signature!==key){key=signature;buttons.forEach(button=>button.remove());buttons=values.map(value=>{const button=el('button','',display[String(value)] ?? String(value));button.type='button';button.setAttribute('role','radio');button.onclick=event=>{event.preventDefault();commit(value);refresh();};control.append(button);return button;});control.style.setProperty('--count',String(Math.max(1,values.length)));}
                const index=Math.max(0,values.findIndex(value=>value===read()));control.style.setProperty('--selected',String(index));buttons.forEach((button,i)=>{button.setAttribute('aria-checked',String(i===index));button.tabIndex=i===index?0:-1;});
            };
            control.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const values=choices()||[],index=Math.max(0,values.indexOf(read())),next=event.key==='Home'?0:event.key==='End'?values.length-1:(index+(event.key==='ArrowRight'?1:-1)+values.length)%values.length;commit(values[next]);refresh();buttons[next]?.focus();};
            row.classList.add('qws-wide');
        } else if (Array.isArray(choices())) {
            control = el('select');let signature='';
            updateValue=(force=false)=>{const values=choices()||[],value=read(),next=JSON.stringify([values,value]);if(signature!==next&&(force||document.activeElement!==control)){signature=next;control.replaceChildren(...values.map(value=>new Option(display[String(value)]??String(value),String(value))));if(value!=null&&!values.includes(value)){const reference=getTarget(item)?.type==='CodexStudioCanvas'&&item.widget==='比例'&&/^跟随参考图/.test(String(value)),missing=new Option(`${value}（${reference?'参考图已移除':'未安装'}）`,String(value));missing.disabled=true;control.append(missing);}}if(force||document.activeElement!==control)control.value=String(value);};
            control.onchange=()=>{const selected=choices()?.find(value=>String(value)===control.value);if(selected!==undefined)commit(selected);refresh();};
            if(!custom&&!(getTarget(item)?.type==='CodexQwenStudioUpscale'&&item.widget==='总倍率'))control.addEventListener('focus',async()=>{try{if(typeof getField(item)?.options?.values==='function'){updateValue(true);return;}const data=await schema(getTarget(item)?.type,true),definition=data?.input?.required?.[item.widget]||data?.input?.optional?.[item.widget];if(Array.isArray(definition?.[0])){const w=getField(item);if(typeof w.options?.values!=='function'){w.options||={};w.options.values=definition[0];}}if(!disposed)updateValue(true);}catch{/* Keep the installed model list on a transient refresh error. */}} ,{once:true});
        } else {
            control = el('input');control.type = typeof current === 'number' ? 'number' : 'text';
            const updateOptions=()=>{if(control.type!=='number')return;for(const key of ['min','max','step'])if(Number.isFinite(options[key]))control[key]=String(options[key]);if(!control.step)control.step='any';};
            updateOptions();
            if(control.type==='number')schema(getTarget(item)?.type).then(data=>{const definition=data?.input?.required?.[item.widget]||data?.input?.optional?.[item.widget];options={...options,...definition?.[1],...item};if(definition?.[0]==='INT'&&!Object.hasOwn(definition?.[1]||{},'step'))options.step=1;updateOptions();}).catch(()=>{});
            control.onchange=()=>{const value=control.type==='number'?numericValue(control.value,read(),options):control.value;commit(value);control.value=String(value);refresh();};
            updateValue=()=>{if(document.activeElement!==control)control.value=String(read()??'');};
        }
        control.setAttribute('aria-label', controlLabel);if(item.tooltip)control.title=item.tooltip;
        if(item.widget==='输出上限'&&getTarget(item)?.type==='CodexPromptOptimize')control.title='限制语言模型最多输出多少 token；不是字数，也不要求填满。';
        if(canvasTypes.has(getTarget(item)?.type)&&['自定义宽度','自定义高度'].includes(item.widget))control.title='手动修改宽高后切换为自定义尺寸。';
        row.append(control);parent.append(row);
        if(!custom&&isSeedItem(item)){
            const tools=el('div','qws-seed-tools'),modes=el('div','qws-segments'),indicator=el('span','qws-indicator'),buttons=[];
            modes.setAttribute('role','radiogroup');modes.setAttribute('aria-label',`${controlLabel}模式`);modes.style.setProperty('--count','2');modes.append(indicator);
            for(const[value,title]of [['fixed','固定'],['random','每次随机']]){const button=el('button','',title);button.type='button';button.setAttribute('role','radio');button.onclick=event=>{event.preventDefault();setSeedMode(item,value);refresh();};buttons.push([value,button]);modes.append(button);}
            modes.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const value=event.key==='Home'?'fixed':event.key==='End'?'random':seedMode(item)==='random'?'fixed':'random';setSeedMode(item,value);refresh();buttons.find(([key])=>key===value)?.[1].focus();};
            const fresh=el('button','qws-new-seed','换种子');fresh.type='button';fresh.setAttribute('aria-label',`${controlLabel}换种子`);fresh.onclick=event=>{event.preventDefault();setSeedMode(item,'fixed');commit(newSeed(read()));refresh();};
            tools.append(modes,fresh);row.append(tools);const changed=control.onchange;control.onchange=function(){setSeedMode(item,'fixed');return changed?.apply(this,arguments);};
            refreshers.push(()=>{const selected=seedMode(item);indicator.style.transform=`translateX(${selected==='random'?100:0}%)`;buttons.forEach(([value,button])=>{button.setAttribute('aria-checked',String(selected===value));button.tabIndex=selected===value?0:-1;});});nativeFixed(item);
        }
        const update=()=>{row.hidden=!condition(item);updateValue();};refreshers.push(update);update();
        return {row,control};
    }
    function repairSamplingPanel(parent, repair) {
        const container=el('div');parent.append(container);
        let current=null,targetIdentity=null;
        const modelCandidates=(target,modelName,prefix)=>{
            const native=valuesOf(field(target,modelName));
            const key=prefix+'模型';
            if(Array.isArray(native))return native.filter(name=>typeof name==='string'&&(prefix==='Krea'?/krea.*turbo|turbo.*krea/i.test(name):/qwen[-_]?image[-_]?2[._-]?1/i.test(name)));
            if(!repairWeightCache.has(key)){
                repairWeightCache.set(key,[]);
                Promise.allSettled(['UNETLoader','UnetLoaderGGUF'].map(async type=>{const data=await schema(type);return data?.input?.required?.unet_name?.[0]||[];})).then(results=>{
                    if(disposed)return;
                    const names=results.filter(result=>result.status==='fulfilled').flatMap(result=>Array.isArray(result.value)?result.value:[]);
                    repairWeightCache.set(key,[...new Set(names.filter(name=>typeof name==='string'&&(prefix==='Krea'?/krea.*turbo|turbo.*krea/i.test(name):/qwen[-_]?image[-_]?2[._-]?1/i.test(name))))]);
                    refresh();
                });
            }
            return repairWeightCache.get(key)||[];
        };
        const update=()=>{
            const engine=field(repair,'修复引擎')?.value,external=engine==='Image2.1'&&field(repair,'使用外接配置')?.value===true;
            container.hidden=!['Image2.1','Krea 2'].includes(engine);
            let target=repair;
            if(external){
                const input=repair.inputs?.find(item=>item.name==='Image2配置');
                const links=repair.graph?.links,link=links?.get?.(input?.link)||links?.[input?.link];
                target=repair.graph?.getNodeById(link?.origin_id??link?.[1]);
            }
            const identity=container.hidden?'hidden':`${engine}:${external}:${target?.id??'missing'}`;
            if(targetIdentity!==identity){
                current?.dispose();current=null;targetIdentity=identity;container.replaceChildren();
                if(!container.hidden){
                    if(!target||external&&target.type!=='H3ImageModelSettings')container.append(el('div','qws-status','请连接图像模型配置。'));
                    else{
                        const prefix=engine==='Krea 2'?'Krea':'Image2';
                        const names=external?{model:'图像模型',steps:'步数',cfg:'CFG',denoise:'去噪',sampler:'采样器',scheduler:'调度器'}:
                            {model:prefix+'模型',steps:prefix+'步数',cfg:prefix+'CFG',denoise:prefix+'降噪',sampler:prefix+'采样器',scheduler:prefix+'调度器'};
                        if(external)container.append(el('div','qws-status','外接配置'));
                        current=createImageSamplingControls({node:target,fields:names,modelChoices:()=>modelCandidates(target,names.model,prefix),onChange:()=>{dirty();refresh();}});
                        container.append(current.element);
                    }
                }
            }
            current?.refresh();
        };
        samplingPanels.push({dispose:()=>current?.dispose()});refreshers.push(update);update();
    }
    function render() {
        let next;try {next=JSON.parse(config.value||'{}');}catch {root.replaceChildren(el('p','qws-error','控制配置读取失败'));return;}
        for(const panel of samplingPanels.splice(0))panel.dispose();
        for(const restore of callbackRestorers.splice(0))restore();
        for(const secret of secretInputs)secret.value='';secretInputs.clear();
        mapping=next;mappingKey=config.value;refreshers=[];bindings=[];slots=[];canvasControllers.clear();
        watchRouteCallback();syncOptimizationTarget();
        const fields=(mapping.fields||[]).filter(item=>getField(item));
        const repairs=new Set([...fields.map(getTarget),...(node.graph?._nodes||[])].filter(owner=>owner?.type==='CodexQwenStudioRepair'));
        for(const repair of repairs){
            for(const widget of repair.widgets||[]){
                const name=widget.name;
                if(name==='修复规则'||/control_after_generate$/.test(name)||!['string','number','boolean'].includes(typeof widget.value)||widget.options?.serialize===false)continue;
                if(!fields.some(item=>String(item.node)===String(repair.id)&&item.widget===name))
                    fields.push({node:repair.id,widget:name,label:name,section:'finish'});
            }
        }
        for(const owner of new Set(fields.map(getTarget).filter(owner=>owner?.type==='CodexQwenStudioUpscale'))){const total=field(owner,'总倍率');if(total){total.options||={};total.options.values=field(owner,'超分路线')?.value==='DLSS5→VOSR2'?totalScales('DLSS5→VOSR2'):[1,2,3,4];}}
        const seen=new Set();for(const item of fields){if(item.profile_key){const key=(item.profile_route||'normal')+':'+item.profile_key;if(!seen.has(key)){seen.add(key);bindings.push(item);}}if(item.slot||item.section==='lora'){const slot=String(item.slot||String(item.label).match(/LoRA\s*(\d+)/i)?.[1]||1);if(!slots.includes(slot))slots.push(slot);}}
        slots.sort((a,b)=>Number(a)-Number(b));
        root.replaceChildren();const columns={};
        for(const[key,title]of [['base','基础与生成'],['lora','LoRA'],['finish','修复与超分']]){const section=el('section','qws-column');section.dataset.section=key;section.append(el('h3','',title));columns[key]=section;root.append(section);}
        const cloudConnection=bbapiPanel(columns.base),cloudNodeIds=new Set([mapping.bbapi_connection_node,mapping.bbapi_image_node,mapping.bbapi_generator_node].filter(value=>value!=null).map(String));
        const folds=new Map();
        const targetParent=item=>{
            if(cloudConnection&&cloudNodeIds.has(String(item.node)))return String(item.node)===String(mapping.bbapi_connection_node)?cloudConnection.fields:cloudConnection.params;
            const section=columns[item.section]||columns.base;
            if(getTarget(item)?.type==='CodexQwenStudioRepair'&&!item.widget.startsWith('DLSS'))return columns.finish;
            if(item.slot||item.section==='lora'){const slot=String(item.slot||String(item.label).match(/LoRA\s*(\d+)/i)?.[1]||1),key=`lora-${slot}`;if(!folds.has(key))folds.set(key,fold(columns.lora,key,`LoRA ${slot}`));return folds.get(key).body;}
            if(item.group){const key=item.section+':'+item.group;if(!folds.has(key))folds.set(key,fold(section,key,item.group_title||item.group));return folds.get(key).body;}
            return section;
        };
        if(bindings.length&&slots.length){
            const label=el('div','qws-row qws-wide'),name=el('span','qws-row-label','采样来源'),select=el('div','qws-source-grid');
            let choices=[],buttons=[],rows=[],choiceCount=-1;
            select.setAttribute('aria-label','采样来源');select.setAttribute('role','radiogroup');
            select.onchange=()=>applyProfile(select.value);
            const buildChoices=()=>{
                if(choiceCount===visibleLoras)return;choiceCount=visibleLoras;choices=[['base','基础'],...slots.slice(0,visibleLoras).map(slot=>[`lora-${slot}`,`LoRA ${slot}`])];buttons=[];rows=[];select.replaceChildren();
                for(let offset=0;offset<choices.length;offset+=4){const row=el('div','qws-segments'),indicator=el('span','qws-indicator');row.style.setProperty('--count','4');row.append(indicator);rows.push({row,indicator,offset});
                    for(const[value,title]of choices.slice(offset,offset+4)){const button=el('button','',title);button.type='button';button.setAttribute('role','radio');button.onclick=event=>{event.preventDefault();select.value=value;select.onchange();};buttons.push(button);row.append(button);}
                    for(let padding=choices.slice(offset,offset+4).length;padding<4;padding++)row.append(el('span','qws-segment-spacer'));select.append(row);
                }
            };
            select.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(event.key))return;event.preventDefault();const index=Math.max(0,choices.findIndex(([value])=>value===select.value)),delta=event.key==='ArrowUp'?-4:event.key==='ArrowDown'?4:event.key==='ArrowRight'?1:-1,next=event.key==='Home'?0:event.key==='End'?choices.length-1:((index+delta)%choices.length+choices.length)%choices.length;select.value=choices[next][0];select.onchange();buttons[next]?.focus();};
            label.append(name,select);columns.lora.append(label);
            const note=el('div','qws-status'),match=el('div','qws-status','LoRA需匹配当前模型'),cloud=el('div','qws-status','BBAPI 使用所选云端模型，不应用本地 LoRA');columns.lora.append(note,match,cloud);
            profileSourceRefresh=()=>{const value=state(),kind=route(),local=kind!=='bbapi';label.hidden=note.hidden=match.hidden=!local;cloud.hidden=local;if(!local)return;buildChoices();select.value=value.sources[kind];const selected=Math.max(0,choices.findIndex(([key])=>key===select.value));for(const{row,indicator,offset}of rows){const active=selected>=offset&&selected<offset+4;indicator.hidden=!active;row.style.setProperty('--selected',String(active?selected-offset:0));}buttons.forEach((button,index)=>{button.setAttribute('aria-checked',String(index===selected));button.tabIndex=index===selected?0:-1;});note.textContent=`${routeNames[kind]} · ${value.sources[kind]==='base'?'基础采样':`LoRA ${value.sources[kind].slice(5)} 采样预设`}`;};
        }else profileSourceRefresh=()=>{};
        const repairSamplingWidgets=/^(?:Image2|Krea)(?:模型|步数|CFG|降噪|采样器|调度器)$/;
        const renderedRepairPanels=new Set();
        for(const item of fields){
            const owner=getTarget(item),isCanvas=canvasTypes.has(owner?.type),shown=isCanvas&&item.widget==='最长边上限'?{...item,group:'canvas-limits',group_title:'尺寸上限'}:item;
            if(owner?.type==='CodexQwenStudioRepair'&&repairSamplingWidgets.test(item.widget))continue;
            const parent=targetParent(shown),created=makeControl(parent,shown);
            if(created&&item.widget==='模型路线'&&cloudConnection)created.row.after(cloudConnection.details);
            if(item.widget==='生成模式'){
                const capability=el('div','qws-status');parent.append(capability);
                refreshers.push(()=>{const single=['moss','zimage','krea'].includes(route());capability.hidden=!single;capability.textContent=single?'图生图采用单张主图重绘；多图编辑请选择 Qwen 或 BBAPI。':'';});
            }
            if(created&&owner?.type==='CodexQwenStudioRepair'&&item.widget==='修复引擎'){
                const hint=el('div','qws-status');parent.append(hint);refreshers.push(()=>{const engine=field(owner,'修复引擎')?.value;hint.textContent=engine==='DLSS5'?'保真增强，不调用扩散模型或提示词。先增强，再按所选输出尺寸等比适配。':engine==='Krea 2'?'Turbo 原图重绘；降低降噪可减少纹理变化。':'原图重绘；提高降噪会加大纹理和构图变化。';});
                if(!renderedRepairPanels.has(owner)){repairSamplingPanel(parent,owner);renderedRepairPanels.add(owner);}
            }
            if(created&&['CodexQwenStudioRepair','CodexQwenStudioUpscale'].includes(owner?.type)&&item.widget==='输入尺寸'){
                const dimensions=el('div','qws-status');parent.append(dimensions);refreshers.push(()=>{const receipt=app.nodeOutputs?.[String(owner.id)]?.codex_input_dimensions?.[0];dimensions.textContent=receipt?`原图 ${receipt.original_width} × ${receipt.original_height} → 处理输入 ${receipt.width} × ${receipt.height}${receipt.output_width?` → 输出 ${receipt.output_width} × ${receipt.output_height}`:''}`:'输入尺寸仅限制长边，不会放大小图。';});
            }
            if(created&&isCanvas&&item.widget==='比例'&&canvasNames.every(name=>field(owner,name)))canvasPresets(parent,owner);
            if(created&&(item.slot||item.section==='lora')&&typeof getField(item)?.value==='boolean'){
                const slot=String(item.slot||String(item.label).match(/LoRA\s*(\d+)/i)?.[1]||1),heading=folds.get(`lora-${slot}`).summary;
                created.row.remove();const holder=el('span');holder.append(created.control);heading.append(holder);
                created.control.addEventListener('click',event=>event.stopPropagation());
                refreshers.push(()=>{holder.hidden=!condition(item);});
            }
        }
        if(bindings.length&&slots.length)for(const slot of slots){
            const parent=folds.get(`lora-${slot}`)?.body;if(!parent)continue;parent.append(el('div','qws-subheading','采样预设'));
            for(const binding of bindings){
                const kind=binding.profile_route||'normal',key=binding.profile_key;
                makeControl(parent,{...binding,label:profileNames[key]||binding.label}, {
                    read:()=>state().slots[slot][kind][key],
                    write:value=>{const current=state();current.slots[slot][kind][key]=value;if(current.sources[kind]===`lora-${slot}`){applying=true;try{const target=getField(binding);target.value=value;target.callback?.(value);}finally{applying=false;}}refresh();dirty();}
                });
            }
            const use=el('button','qws-use-profile','使用本槽设置');use.type='button';use.setAttribute('aria-label',`使用 LoRA ${slot} 采样预设`);use.onclick=()=>applyProfile(`lora-${slot}`);parent.append(use);refreshers.push(()=>use.classList.toggle('qws-profile-active',state().sources[route()]===`lora-${slot}`));
        }
        for(const[slotIndex,slot]of slots.entries()){const section=folds.get(`lora-${slot}`);if(section)refreshers.push(()=>{section.details.hidden=route()==='bbapi'||slotIndex>=visibleLoras;});}
        for(const[key,section]of folds){if(key.startsWith('lora-'))continue;refreshers.push(()=>{const rows=[...section.body.children].filter(child=>child.classList.contains('qws-row'));section.details.hidden=rows.length>0&&rows.every(row=>row.hidden);});}
        if(slots.length){const add=el('button','qws-add-lora','添加 LoRA');add.type='button';add.setAttribute('aria-label','添加 LoRA');add.onclick=()=>{if(route()==='bbapi'||visibleLoras>=slots.length)return;node.properties.codex_qwen_visible_loras=visibleLoras+1;refresh();dirty();};columns.lora.append(add);refreshers.push(()=>{add.hidden=route()==='bbapi'||visibleLoras>=slots.length;});}
        const current=state();if(stateIdentity!==current){stateIdentity=current;for(const kind of profileKinds)applyProfile(current.sources[kind],kind,false);}
        if(cloudConnection)refreshers.push(cloudConnection.sync);
        refresh();
    }
    const dom=node.addDOMWidget('统一控制','CODEX_QWEN_CONTROLS',root,{serialize:false,canvasOnly:false,hideInPanel:true,getMinHeight:()=>500});
    dom.beforeQueued=()=>{syncOptimizationTarget();prepareQueuedSeeds();};
    dom.computeSize = undefined;
    dom.computeLayoutSize = () => ({minWidth:0,minHeight:500,maxHeight:undefined});
    const observer=new ResizeObserver(entries=>{const width=entries[0]?.contentRect.width||1100;root.dataset.columns=width<520?'1':width<850?'2':'3';});observer.observe(root);
    const configured=node.onConfigure;node.onConfigure=function(){const result=configured?.apply(this,arguments);stateIdentity=null;requestAnimationFrame(render);return result;};
    const serialized=node.onSerialize;node.onSerialize=function(saved){syncOptimizationTarget();syncProfiles();const result=serialized?.apply(this,arguments);saved.properties||={};for(const key of ['codex_qwen_sampling_profiles','codex_qwen_folds','codex_seed_modes'])if(node.properties?.[key])saved.properties[key]=JSON.parse(JSON.stringify(node.properties[key]));saved.properties.codex_qwen_visible_loras=node.properties.codex_qwen_visible_loras;return result;};
    const timer=setInterval(()=>{if(disposed||!root.isConnected)return;if(mappingKey!==config.value||!refreshers.length)render();else{syncProfiles();refresh();}},600);
    const removed=node.onRemoved;node.onRemoved=function(){disposed=true;for(const panel of samplingPanels.splice(0))panel.dispose();for(const restore of callbackRestorers.splice(0))restore();for(const secret of secretInputs)secret.value='';secretInputs.clear();clearInterval(timer);observer.disconnect();return removed?.apply(this,arguments);};
    requestAnimationFrame(render);
}

app.registerExtension({name:'Codex.QwenStudioControls',beforeRegisterNodeDef(type,data){
    if(data.name!=='CodexQwenStudioControls')return;
    const created=type.prototype.onNodeCreated;
    type.prototype.onNodeCreated=function(){const result=created?.apply(this,arguments);install(this);return result;};
}});
