import {app} from '/scripts/app.js';
import {api} from '/scripts/api.js';

const keys=['generated','repaired','upscaled'];
const labels={generated:'生成原图',repaired:'修复结果',upscaled:'超分结果'};
const widget=(node,name)=>node?.widgets?.find(item=>item.name===name);
const validImage=image=>typeof image?.filename==='string'&&image.filename.length>0&&['input','output','temp'].includes(image.type||'temp');
const el=(tag,className,text)=>{const item=document.createElement(tag);if(className)item.className=className;if(text)item.textContent=text;return item;};
function styles(){
    if(document.getElementById('studio-result-actions-style'))return;
    const style=el('style');style.id='studio-result-actions-style';style.textContent=`
    .qws-results-actions{display:flex;align-items:center;flex-wrap:wrap;gap:9px;padding:0 0 10px;margin:0 0 5px;border-bottom:1px solid var(--border-color,#444);font:12px/1.4 sans-serif}
    .qws-results-actions .qws-action-status{flex-basis:100%;color:#aaa;min-height:0}.qws-action-status:empty{display:none}.qws-action-status[data-error=true]{color:#efb1a9}
    .qws-result-action-switch{display:inline-flex!important;align-items:center;gap:6px;border:0!important;background:transparent!important;padding:2px!important;min-height:26px!important;font-size:12px!important;white-space:nowrap}
    .qws-result-action-switch>i{position:relative;display:block;width:29px;height:16px;background:#555;border-radius:9px;flex:0 0 auto;transition:background .18s}.qws-result-action-switch>i:before{content:'';position:absolute;left:2px;top:2px;width:12px;height:12px;background:#eee;border-radius:50%;transition:transform .18s}
    .qws-result-action-switch[aria-checked=true]>i{background:#647fe0}.qws-result-action-switch[aria-checked=true]>i:before{transform:translateX(13px)}.qws-stage-head>.qws-reveal-button{font-size:12px;white-space:nowrap}
    @media(prefers-reduced-motion:reduce){.qws-result-action-switch>i,.qws-result-action-switch>i:before{transition:none}}
    `;document.head.append(style);
}

export function installResultActions(node,root,stages){
    node._codexResultActionsCleanup?.();styles();
    const controller=new AbortController(),signal=controller.signal,descriptors=new Map(),stageButtons=new Map(),seenReveals=new Set(),releasedPrompts=new Set();
    let disposed=false,activePromptId=null,running=false,ownRun=false,finishTimer=null,releasing=false,failed=false;
    node.properties||={};node.properties.codex_release_after_run??=false;node.properties.codex_auto_reveal||={};
    const toolbar=el('div','qws-results-actions'),release=el('button','','立即释放模型'),status=el('span','qws-action-status');release.type='button';
    const dirty=()=>node.graph?.setDirtyCanvas?.(true,true);
    function note(text,error=false){if(disposed)return;status.textContent=text||'';status.dataset.error=String(error);}
    function toggle(label,read,write){const button=el('button','qws-result-action-switch');button.type='button';button.setAttribute('role','switch');button.setAttribute('aria-label',label);button.append(el('i'),el('span','',label));const refresh=()=>button.setAttribute('aria-checked',String(!!read()));button.onclick=()=>{write(!read());refresh();dirty();};refresh();return button;}
    const autoRelease=toggle('完成后卸载模型',()=>node.properties.codex_release_after_run,value=>node.properties.codex_release_after_run=value);
    toolbar.append(release,autoRelease,status);root.prepend(toolbar);
    async function request(path,body){
        if(!['127.0.0.1','localhost','::1','[::1]'].includes(location.hostname))throw Error('此操作仅可在本机工作台使用。');
        const response=await api.fetchApi(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal,credentials:'same-origin',redirect:'error'});
        let result={};try{result=await response.json();}catch{}
        if(!response.ok)throw Error(result.error||result.message||`操作失败（${response.status}）`);return result;
    }
    function stageEnabled(key){if(key==='generated')return true;const target=node.graph?.getNodeById(node.properties?.[key==='repaired'?'codex_repair_node':'codex_upscale_node']);return key==='repaired'?!!widget(target,'启用修复')?.value:!!widget(target,'启用超分')?.value;}
    const outputId=key=>node.properties?.codex_stage_output_nodes?.[key]??node.properties?.codex_stage_nodes?.[key];
    const output=nodeId=>app.nodeOutputs?.[String(nodeId)]||{};
    function currentImage(key){const current=stages?.get(key)?.current;return validImage(current)?current:descriptors.get(key);}
    function refresh(){release.disabled=running||releasing;release.title=running?'当前任务结束后可释放模型':'释放空闲的本地模型缓存';for(const[key,button]of stageButtons)button.disabled=!validImage(currentImage(key));}
    function remember(key,image){if(validImage(image))descriptors.set(key,image);refresh();}
    async function reveal(key,image,automatic=false){
        if(!validImage(image)||automatic&&(!stageEnabled(key)||!running||activePromptId==null))return;
        const identity=JSON.stringify([activePromptId,key,image.type,image.subfolder,image.filename]);
        if(automatic){if(seenReveals.has(identity))return;seenReveals.add(identity);if(seenReveals.size>128)seenReveals.delete(seenReveals.values().next().value);}
        try{await request('/studio-results/reveal',{filename:image.filename,subfolder:image.subfolder||'',type:image.type||'temp'});note(`已打开${labels[key]}所在位置。`);}catch(error){if(error.name!=='AbortError')note(error.message,true);}
    }
    for(const key of keys){
        const view=stages?.get(key),section=view?.section||root.querySelectorAll('.qws-stage')[keys.indexOf(key)],heading=section?.querySelector('.qws-stage-head');if(!heading)continue;
        const open=el('button','qws-reveal-button','打开位置');open.type='button';open.setAttribute('aria-label',`打开${labels[key]}所在位置`);open.onclick=()=>reveal(key,currentImage(key));
        const auto=toggle('完成后打开',()=>node.properties.codex_auto_reveal[key],value=>node.properties.codex_auto_reveal[key]=value);auto.setAttribute('aria-label',`${labels[key]}完成后打开`);heading.append(open,auto);stageButtons.set(key,open);
        const cached=output(outputId(key)).images?.find(validImage);if(cached)remember(key,cached);
    }
    async function releaseModels(automatic=false,promptId=null){
        if(releasing||disposed)return;releasing=true;refresh();note(automatic?'队列空闲后释放模型…':'正在释放空闲模型…');
        try{const result=await request('/studio-results/release',automatic?{wait_for_idle:true,prompt_id:promptId}:{});note(result.message||(result.released?'模型已卸载。':result.waiting?'队列空闲后释放。':'释放操作已完成。'));}catch(error){if(error.name!=='AbortError')note(error.message,true);}finally{releasing=false;refresh();}
    }
    release.onclick=()=>releaseModels(false);
    const matching=detail=>activePromptId==null||detail?.prompt_id==null||String(detail.prompt_id)===activePromptId;
    const on=(name,callback)=>api.addEventListener(name,event=>callback(event.detail||{}),{signal});
    on('execution_start',detail=>{if(running&&detail.prompt_id!=null&&String(detail.prompt_id)===activePromptId)return;activePromptId=detail.prompt_id==null?null:String(detail.prompt_id);running=true;ownRun=false;failed=false;if(finishTimer)clearTimeout(finishTimer);finishTimer=null;descriptors.clear();note('');refresh();});
    on('executed',detail=>{
        if(!matching(detail))return;
        for(const key of keys)if(String(detail.node)===String(outputId(key))){const image=detail.output?.images?.find(validImage);if(image){ownRun=true;remember(key,image);if(node.properties.codex_auto_reveal[key])reveal(key,image,true);}}
        if(String(detail.node)===String(node.id)){ownRun=true;for(const key of keys){const image=detail.output?.[key]?.find(validImage);if(image)remember(key,image);}}
    });
    on('execution_cached',detail=>{if(!matching(detail))return;const ids=new Set((detail.nodes||[]).map(String));for(const key of keys)if(ids.has(String(outputId(key)))){ownRun=true;const image=output(outputId(key)).images?.find(validImage);if(image)remember(key,image);}});
    function finish(detail,error=false){
        if(!matching(detail))return;if(error)failed=true;running=false;refresh();if(finishTimer)clearTimeout(finishTimer);
        finishTimer=setTimeout(()=>{finishTimer=null;if(disposed||failed||running||!ownRun||!node.properties.codex_release_after_run||activePromptId==null||releasedPrompts.has(activePromptId))return;releasedPrompts.add(activePromptId);if(releasedPrompts.size>32)releasedPrompts.delete(releasedPrompts.values().next().value);releaseModels(true,activePromptId);},75);
    }
    on('execution_success',detail=>finish(detail));on('executing',detail=>{if(detail.node==null)finish(detail);});on('execution_error',detail=>finish(detail,true));on('execution_interrupted',detail=>finish(detail,true));
    const observer=new MutationObserver(refresh);observer.observe(root,{subtree:true,attributes:true,attributeFilter:['src','hidden']});refresh();
    const cleanup=()=>{if(disposed)return;disposed=true;controller.abort();observer.disconnect();if(finishTimer)clearTimeout(finishTimer);toolbar.remove();for(const button of stageButtons.values()){const heading=button.parentElement;button.remove();heading?.querySelector('.qws-result-action-switch')?.remove();}if(node._codexResultActionsCleanup===cleanup)delete node._codexResultActionsCleanup;};node._codexResultActionsCleanup=cleanup;return cleanup;
}
