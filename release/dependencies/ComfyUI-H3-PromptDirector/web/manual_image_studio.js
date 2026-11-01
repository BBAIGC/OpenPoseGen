import {hideNativeWidget} from './nodes2_dom_compat.js';
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { H3_STUDIO_VERSION } from "./version.js";

const TYPE = "H3ManualImageStudio";
const MAX_SHOTS = 100;
const settingsNames = {"图像模型":"model","文本编码器":"clip","VAE":"vae","步数":"steps","CFG":"cfg","采样器":"sampler","调度器":"scheduler","去噪":"denoise","缓存设备":"cache_device","缓存精度":"cache_dtype","编码器设备":"clip_device"};
const PHASES = {loading:"加载模型",encoding:"编码参考图",sampling:"正在采样",decoding:"解码图片",completed:"已完成"};
const STATES = {idle:"等待导入",pending:"等待生成",running:"生成中",completed:"已完成",failed:"失败",cancelled:"已取消",paused:"已暂停"};
const el = (tag, text = "", className = "") => { const value=document.createElement(tag);value.textContent=text;if(className)value.className=className;return value; };
const button = (text, action, title = text) => {const value=el("button",text);value.type="button";value.onclick=action;value.title=title;return value;};
const imageURL = value => value?.filename ? api.apiURL(`/view?${new URLSearchParams({filename:value.filename,subfolder:value.subfolder||"",type:value.type||"input"})}`) : "";
const refKey = value => `${value.type||"input"}/${value.subfolder||""}/${value.filename}`;
const clone = value => JSON.parse(JSON.stringify(value));
const graphLink = (graph, identity) => graph?.links?.get?.(identity) ?? graph?.links?.[identity];

function installStyle(){
    if(document.getElementById("h3-manual-images-style"))return;
    const style=el("style");style.id="h3-manual-images-style";style.textContent=`
    .h3-manual-images{display:flex;flex-direction:column;height:100%;min-height:0;overflow:hidden;padding:10px 12px;box-sizing:border-box;font:13px/1.5 system-ui,sans-serif;color:var(--fg-color,#ddd);background:var(--comfy-menu-bg,#222)}
    .h3-manual-images *{box-sizing:border-box}.h3-manual-images [hidden]{display:none!important}
    .h3-manual-images h3{margin:0;font-size:16px;font-weight:650}.h3-manual-images h4{margin:0;font-size:13px;font-weight:650}
    .h3-manual-images button,.h3-manual-images input,.h3-manual-images textarea,.h3-manual-images select{font:inherit;color:inherit;background:var(--comfy-input-bg,#292929);border:1px solid var(--border-color,#555);border-radius:3px;min-height:30px;padding:4px 7px}
    .h3-manual-images button{cursor:pointer;white-space:nowrap}.h3-manual-images button:disabled{opacity:.4;cursor:default}.h3-manual-images :focus-visible{outline:2px solid var(--fg-color,#bbb);outline-offset:2px}
    .h3-manual-images .mi-heading,.h3-manual-images .mi-row{display:flex;align-items:center;gap:7px;flex-wrap:wrap}.h3-manual-images .mi-heading{justify-content:space-between;margin:0 0 8px}.h3-manual-images .mi-row{margin:7px 0}.h3-manual-images .mi-grow{flex:1;min-width:0}
    .h3-manual-images .mi-muted{font-size:12px;opacity:.7}.h3-manual-images .mi-error{color:#e87474;white-space:pre-wrap;overflow-wrap:anywhere}.h3-manual-images .mi-section{padding-top:9px;margin-top:9px;border-top:1px solid var(--border-color,#555)}
    .h3-manual-images .mi-body{min-height:0;flex:1;overflow:auto;overscroll-behavior:contain;scrollbar-gutter:stable;padding-right:6px;padding-bottom:8px}.h3-manual-images .mi-body[aria-disabled=true]{opacity:.5;pointer-events:none}
    .h3-manual-images .mi-switch{display:flex;align-items:center;gap:7px;cursor:pointer}.h3-manual-images .mi-switch input{appearance:none;width:34px;height:18px;min-height:18px;padding:2px;border-radius:10px;background:#555;position:relative;flex:none;cursor:pointer}.h3-manual-images .mi-switch input:before{content:"";display:block;width:12px;height:12px;background:#eee;border-radius:50%;transition:transform .18s}.h3-manual-images .mi-switch input:checked{background:#aaa}.h3-manual-images .mi-switch input:checked:before{transform:translateX(15px);background:#222}
    .h3-manual-images .mi-references,.h3-manual-images .mi-results{display:flex;gap:7px;overflow-x:auto;padding:5px 2px 9px;overscroll-behavior:contain;min-height:76px}
    .h3-manual-images .mi-ref{position:relative;flex:0 0 70px;padding:3px;text-align:center;font-size:12px;white-space:normal;min-height:72px}.h3-manual-images .mi-ref img{display:block;width:62px;height:48px;object-fit:contain;margin:auto}.h3-manual-images .mi-ref[aria-pressed=true]{outline:2px solid var(--fg-color,#ccc);outline-offset:-1px;background:color-mix(in srgb,currentColor 12%,transparent)}
    .h3-manual-images .mi-queue{max-height:210px;overflow:auto;overscroll-behavior:contain}.h3-manual-images .mi-queue-item{display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--border-color,#444);padding:6px 2px;cursor:pointer}.h3-manual-images .mi-queue-item[aria-selected=true]{background:color-mix(in srgb,currentColor 10%,transparent)}.h3-manual-images .mi-queue-item strong{white-space:nowrap}.h3-manual-images .mi-queue-summary{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0;font-size:12px;opacity:.75}
    .h3-manual-images textarea{width:100%;height:135px;min-height:80px;resize:vertical;line-height:1.55;padding:7px}.h3-manual-images input[type=number]{width:75px}.h3-manual-images .mi-primary{font-weight:650;border-color:var(--fg-color,#aaa)}
    .h3-manual-images .mi-result{position:relative;flex:0 0 174px;min-width:0}.h3-manual-images .mi-result-frame{height:136px;width:100%;padding:0;display:flex;align-items:center;justify-content:center;overflow:hidden}.h3-manual-images .mi-result img{width:100%;height:100%;object-fit:contain}.h3-manual-images .mi-result.selected{outline:2px solid var(--fg-color,#ccc);outline-offset:2px}.h3-manual-images .mi-result.retained .mi-result-frame{border-color:#b29a54}.h3-manual-images .mi-result .mi-row{gap:4px;justify-content:space-between}.h3-manual-images .mi-result button{font-size:12px;padding:3px 5px}.h3-manual-images .mi-order{position:absolute;top:5px;left:5px;background:#222d;color:#fff;border-radius:2px;min-width:22px;padding:1px 5px;text-align:center}.h3-manual-images .mi-result.retained:after{content:"已保留";position:absolute;right:5px;top:5px;background:#222d;color:#e9d894;padding:1px 5px;font-size:11px;pointer-events:none}
    .h3-manual-images .mi-progress{width:100%;height:7px;accent-color:#aaa}.h3-manual-images .mi-status{flex:none;padding-top:8px;border-top:1px solid var(--border-color,#555);font-size:12px;min-height:30px;white-space:pre-wrap;overflow-wrap:anywhere}.h3-manual-images .mi-spinner{display:inline-block;width:13px;height:13px;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:mi-spin .8s linear infinite;margin-right:5px;vertical-align:middle}@keyframes mi-spin{to{transform:rotate(360deg)}}
    .h3-manual-images .mi-viewer{position:absolute;inset:5px;z-index:30;display:flex;flex-direction:column;padding:10px;background:var(--comfy-menu-bg,#222);border:1px solid var(--border-color,#777)}.h3-manual-images .mi-viewer img{width:100%;height:0;flex:1;min-height:0;object-fit:contain}.h3-manual-images .mi-viewer .mi-row{flex:none}.h3-manual-images{position:relative}
    `;document.head.append(style);
}

function hide(widget){if(!widget)return;hideNativeWidget(widget);widget.computeSize=()=>[0,-4];if(widget.inputEl)widget.inputEl.style.display="none";if(widget.element)widget.element.style.display="none";}

function setup(node){
    installStyle();
    const dataWidget=node.widgets?.find(item=>item.name==="任务数据");if(!dataWidget)return;
    hide(dataWidget);
    node.properties||={};
    const fresh=()=>({task_id:crypto.randomUUID().replaceAll("-",""),source_mode:"",shots:[],references:[],activeId:null,width:null,height:null,seed:42,image_settings:null,runSelection:[],regenerate:false});
    let data=fresh(),disposed=false,busy=false,selectedResults=[],lastSource=null,queueing=false,previewFocus=null,activePromptId=null,pendingPromptId=null;
    const root=el("div","","h3-manual-images"),heading=el("div","","mi-heading"),title=el("h3",`Image2.1 独立分镜生图 V${H3_STUDIO_VERSION}`),stateLabel=el("span","未连接","mi-muted");heading.append(title,stateLabel);
    const body=el("div","","mi-body"),status=el("div","从手动分镜工作台导入提示词。","mi-status");root.append(heading,body,status);
    const isolationLabel=el("label","","mi-switch"),isolation=el("input");isolation.type="checkbox";isolation.role="switch";isolationLabel.append(isolation,el("span","仅运行生图区"));body.append(isolationLabel);
    const refsSection=el("section","","mi-section"),refsHeading=el("div","","mi-heading"),refsCount=el("span","0 张","mi-muted"),applyRefs=button("应用到全部",()=>{const row=active();if(!row)return;for(const item of data.shots){item.references=clone(row.references);markEdited(item);}save();render();note("所选参考图已应用到全部分镜。");});refsHeading.append(el("h4","参考图"),refsCount,applyRefs);const references=el("div","","mi-references");refsSection.append(refsHeading,references);body.append(refsSection);
    const queueSection=el("section","","mi-section"),queueHeading=el("div","","mi-heading"),queueCount=el("span","0 项","mi-muted");queueHeading.append(el("h4","提示词队列"),queueCount);const queue=el("div","","mi-queue");queue.setAttribute("role","listbox");queueSection.append(queueHeading,queue);body.append(queueSection);
    const editorSection=el("section","","mi-section"),editorHeading=el("div","","mi-heading"),editorTitle=el("h4","当前提示词"),copy=button("复制",()=>copyText(prompt.value));editorHeading.append(editorTitle,copy);const prompt=el("textarea");prompt.setAttribute("aria-label","独立生图提示词");prompt.spellcheck=false;const snapshotNote=el("span","","mi-muted");editorSection.append(editorHeading,prompt,snapshotNote);body.append(editorSection);
    const sizeRow=el("div","","mi-row"),sizeSummary=el("span","跟随工作台","mi-muted mi-grow"),customSizeLabel=el("label","","mi-switch"),customSize=el("input");customSize.type="checkbox";customSize.setAttribute("aria-label","自定尺寸");customSizeLabel.append(customSize,el("span","自定尺寸"));sizeRow.append(sizeSummary,customSizeLabel);body.append(sizeRow);
    const dimensions=el("div","","mi-row"),width=el("input"),height=el("input"),seed=el("input");for(const input of [width,height]){input.type="number";input.min="64";input.max="2048";input.step="32";}width.setAttribute("aria-label","生图宽度");height.setAttribute("aria-label","生图高度");seed.type="number";seed.min="0";seed.max="4294967295";seed.setAttribute("aria-label","随机种子");dimensions.append(el("span","宽"),width,el("span","高"),height);const seedRow=el("div","","mi-row");seedRow.append(el("span","种子"),seed);body.append(dimensions,seedRow);
    const generateControls=el("div","","mi-row"),single=button("生成本镜",()=>run(false,false)),reroll=button("重抽本镜",()=>run(false,true)),batch=button("批量生成",()=>run(true,false));single.className="mi-primary";generateControls.append(single,reroll,batch);body.append(generateControls);
    const stopControls=el("div","","mi-row"),pause=button("本张完成后暂停",()=>control("pause")),cancel=button("立即停止",()=>control("cancel")),skip=button("跳过本镜",()=>{const row=active();if(!row)return;row.skip=!row.skip;save();render();}),next=button("下一镜",()=>{const index=data.shots.findIndex(row=>row.id===active()?.id);if(index+1<data.shots.length){data.activeId=data.shots[index+1].id;save();render();}});stopControls.append(pause,cancel,skip,next);body.append(stopControls);
    const progress=el("progress","","mi-progress");progress.max=100;progress.hidden=true;body.append(progress);
    const resultsSection=el("section","","mi-section"),resultsHeading=el("div","","mi-heading"),resultsCount=el("span","0 张","mi-muted"),showAll=el("input");showAll.type="checkbox";const allLabel=el("label","","mi-switch");allLabel.append(showAll,el("span","全部分镜"));resultsHeading.append(el("h4","候选图片"),resultsCount,allLabel);const results=el("div","","mi-results"),resultPaging=el("div","","mi-row"),previousPage=button("上一组",()=>{resultOffset=Math.max(0,resultOffset-48);renderResults();}),nextPage=button("下一组",()=>{resultOffset+=48;renderResults();}),pageNote=el("span","","mi-muted");resultPaging.append(previousPage,pageNote,nextPage);resultsSection.append(resultsHeading,results,resultPaging);body.append(resultsSection);
    const backfillRow=el("div","","mi-row"),backfill=button("按点选顺序回填",()=>backfillImages()),clearSelection=button("取消选择",()=>{selectedResults=[];renderResults();}),selectionNote=el("span","","mi-muted");backfillRow.append(backfill,clearSelection,selectionNote);body.append(backfillRow);
    const viewer=el("div","","mi-viewer");viewer.hidden=true;viewer.tabIndex=-1;const viewerControls=el("div","","mi-row"),viewerCaption=el("strong","","mi-grow"),viewerSave=button("保存图片",()=>download(viewer.dataset.url,viewer.dataset.name)),viewerClose=button("关闭",()=>closeViewer()),viewerImage=el("img");viewerControls.append(viewerCaption,viewerSave,viewerClose);viewer.append(viewerControls,viewerImage);root.append(viewer);
    let currentCandidates=[],resultOffset=0,lastProgressSignature="";

    function note(text,error=false){status.textContent=text;status.classList.toggle("mi-error",error);}
    function source(){return node.graph?.getNodeById(data.sourceNodeId??node.properties.h3_manual_source_id)??null;}
    function active(){return data.shots.find(row=>row.id===data.activeId)||data.shots[0]||null;}
    function sourceEnabled(){const owner=source();return !!owner?.h3ManualStoryboard?.isManualMode?.();}
    function save(){dataWidget.value=JSON.stringify(data);dataWidget.callback?.(dataWidget.value);node.graph?.setDirtyCanvas?.(true,true);}
    function read(){try{const value=JSON.parse(dataWidget.value||"{}");data={...fresh(),...value,shots:Array.isArray(value.shots)?value.shots:[],references:Array.isArray(value.references)?value.references:[]};}catch{data=fresh();}}
    function markEdited(row){row.retained=false;for(const item of row.candidates||[])item.stale=true;row.status="pending";}
    function currentReferences(){return active()?.references||data.references.slice(0,1);}
    function validReferences(items){return(Array.isArray(items)?items:[]).filter(item=>item&&typeof item.filename==="string").map(item=>({id:item.id,filename:item.filename,subfolder:item.subfolder||"",type:item.type||"input"}));}
    function snapshotSettings(){
        const owner=source();const link=owner?.inputs?.find(input=>input.type==="H3_IMAGE_SETTINGS")?.link;const upstream=graphLink(node.graph,link);const configuration=upstream?node.graph.getNodeById(upstream.origin_id):null;
        if(configuration){const settings={};for(const field of configuration.widgets||[]){const key=settingsNames[field.name];if(key)settings[key]=field.value;}if(Object.keys(settings).length)data.image_settings=settings;}
        if(owner&&data.dimensions_source!=="manual"){const canvas=owner.properties?.h3_director_preview?.canvas;if(Number.isInteger(canvas?.width)&&Number.isInteger(canvas?.height)){data.width=canvas.width;data.height=canvas.height;data.dimensions_source="source";}}
    }
    function discoverSource(){
        const input=node.inputs?.find(item=>item.type==="H3_MANUAL_SOURCE"),link=graphLink(node.graph,input?.link);if(link){data.sourceNodeId=link.origin_id;node.properties.h3_manual_source_id=link.origin_id;lastSource=link.origin_id;}
        if(!data.sourceNodeId&&node.properties.h3_manual_source_id)data.sourceNodeId=node.properties.h3_manual_source_id;
    }
    function setIsolation(enabled,restoreLinks=true){
        const graph=node.graph;if(!graph)return;
        if(enabled&&!sourceEnabled())throw new Error("请先将来源工作台切换到手动分镜图模式。");
        let receipt=node.properties.h3_manual_isolation;
        if(enabled){
            snapshotSettings();
            if(!receipt)receipt={modes:{},links:[]};
            for(const other of graph._nodes||[]){if(other===node)continue;if(!(String(other.id) in receipt.modes))receipt.modes[other.id]=other.mode??0;other.mode=2;}
            for(let index=0;index<(node.inputs?.length||0);index++){
                const input=node.inputs[index],link=graphLink(graph,input.link);if(!link)continue;
                if(!receipt.links.some(item=>item.origin_id===link.origin_id&&item.origin_slot===link.origin_slot&&item.target_slot===index))receipt.links.push({origin_id:link.origin_id,origin_slot:link.origin_slot,target_slot:index});
                node.disconnectInput(index);
            }
            node.properties.h3_manual_isolation=receipt;node.mode=0;isolation.checked=true;
        }else{
            if(receipt){for(const [identity,mode]of Object.entries(receipt.modes||{})){const other=graph.getNodeById(Number.isNaN(Number(identity))?identity:Number(identity));if(other?.mode===2)other.mode=mode;}
                if(restoreLinks)for(const link of receipt.links||[]){const origin=graph.getNodeById(link.origin_id);if(origin&&node.inputs?.[link.target_slot]?.link==null)origin.connect(link.origin_slot,node,link.target_slot);}
                delete node.properties.h3_manual_isolation;
            }
            isolation.checked=false;
        }
        save();graph.setDirtyCanvas?.(true,true);
    }
    isolation.onchange=()=>{try{setIsolation(isolation.checked);note(isolation.checked?"仅生图节点启用，参考图已保存在队列中。":"已恢复此前的节点状态与连线。");}catch(error){isolation.checked=false;note(error.message,true);}};

    function renderReferences(){
        references.replaceChildren();const chosen=new Set(currentReferences().map(refKey));refsCount.textContent=`已选 ${chosen.size} / ${data.references.length} 张`;
        if(!data.references.length){references.append(el("span","从左侧工作台导入参考图。","mi-muted"));return;}
        data.references.forEach((ref,index)=>{const pick=button(`图${index+1}`,()=>{if(busy)return;const row=active();if(!row)return;let next=row.references||[];const exists=next.some(item=>refKey(item)===refKey(ref));if(exists)next=next.filter(item=>refKey(item)!==refKey(ref));else{if(next.length>=16){note("每个分镜最多选择16张参考图。",true);return;}next=[...next,clone(ref)];}row.references=next;data.selectedReferenceKeys=next.map(refKey);markEdited(row);save();renderReferences();renderQueue();renderEditor();});pick.className="mi-ref";pick.setAttribute("aria-pressed",String(chosen.has(refKey(ref))));pick.setAttribute("aria-label",`参考图${index+1}`);pick.disabled=busy;const img=el("img");img.src=imageURL(ref);img.alt=`参考图${index+1}`;img.loading="lazy";pick.prepend(img);references.append(pick);});
    }
    function renderQueue(){
        queue.replaceChildren();queueCount.textContent=`${data.shots.length} 项`;
        if(!data.shots.length){queue.append(el("div","导入分镜提示词后开始。","mi-muted"));return;}
        for(const row of data.shots){const item=el("div","","mi-queue-item");item.tabIndex=0;item.role="option";item.setAttribute("aria-selected",String(active()?.id===row.id));const label=el("strong",row.id),summary=el("span",row.prompt,"mi-queue-summary"),state=el("span",row.retained?"已保留":row.skip?"已跳过":STATES[row.status]||"等待生成","mi-muted");item.append(label,summary,state);item.onclick=()=>{data.activeId=row.id;save();renderReferences();renderQueue();renderEditor();renderResults();};item.onkeydown=event=>{if(event.key==="Enter"||event.key===" "){event.preventDefault();item.click();}};queue.append(item);}
    }
    function renderEditor(){const row=active();editorSection.hidden=!row;customSize.checked=data.dimensions_source==="manual";dimensions.hidden=!customSize.checked;sizeSummary.textContent=`${customSize.checked?"自定尺寸":"跟随工作台"}${data.width&&data.height?` · ${data.width}×${data.height}`:""}`;if(!row)return;editorTitle.textContent=`${row.id}提示词`;if(document.activeElement!==prompt)prompt.value=row.prompt||"";prompt.readOnly=busy;snapshotNote.textContent=`已保存 ${row.references?.length||0} 张参考图；左侧停用后仍可使用。`;skip.textContent=row.skip?"取消跳过":"跳过本镜";width.value=data.width==null?"":String(data.width);height.value=data.height==null?"":String(data.height);seed.value=String(data.seed);}
    customSize.onchange=()=>{if(busy)return;data.dimensions_source=customSize.checked?"manual":"source";snapshotSettings();for(const row of data.shots)markEdited(row);save();renderEditor();renderQueue();};
    prompt.oninput=()=>{const row=active();if(!row||busy)return;row.prompt=prompt.value;markEdited(row);save();renderQueue();};
    for(const [input,key]of [[width,"width"],[height,"height"],[seed,"seed"]])input.onchange=()=>{const value=Number(input.value);if(!Number.isInteger(value)||(key==="seed"?value<0||value>4294967295:value<64||value>2048||value%32)){input.value=data[key]==null?"":String(data[key]);note(key==="seed"?"种子应为0～4294967295整数。":"宽高应为64～2048的32倍数。",true);return;}data[key]=value;if(key!=="seed")data.dimensions_source="manual";for(const row of data.shots)markEdited(row);save();renderQueue();};
    function allCandidates(){return data.shots.flatMap(row=>(row.candidates||[]).map(candidate=>({row,candidate})));}
    function renderResults(){
        results.replaceChildren();const row=active();currentCandidates=(showAll.checked?allCandidates():(row?.candidates||[]).map(candidate=>({row,candidate})));resultsCount.textContent=`${currentCandidates.length} 张`;
        const existing=new Set(allCandidates().map(item=>item.candidate.id));selectedResults=selectedResults.filter(id=>existing.has(id));
        if(!currentCandidates.length)results.append(el("div","生成后的候选图会排列在这里。","mi-muted"));
        resultOffset=Math.min(resultOffset,Math.max(0,Math.floor((currentCandidates.length-1)/48)*48));resultPaging.hidden=currentCandidates.length<=48;previousPage.disabled=resultOffset===0;nextPage.disabled=resultOffset+48>=currentCandidates.length;pageNote.textContent=`${resultOffset+1}～${Math.min(currentCandidates.length,resultOffset+48)} / ${currentCandidates.length}`;
        currentCandidates.slice(resultOffset,resultOffset+48).forEach(({row,candidate},index)=>{const card=el("article","","mi-result"),order=selectedResults.indexOf(candidate.id),selected=order>=0;card.classList.toggle("selected",selected);card.classList.toggle("retained",row.retained&&row.selected_candidate===candidate.id);const image=el("img");image.src=imageURL(candidate.image);image.alt=`${row.id}候选图${index+1+resultOffset}`;image.loading="lazy";const frame=button("",()=>{const current=selectedResults.indexOf(candidate.id);if(current>=0)selectedResults.splice(current,1);else selectedResults.push(candidate.id);renderResults();},"选择候选图；按点击顺序回填");frame.className="mi-result-frame";frame.append(image);if(selected)card.append(el("span",String(order+1),"mi-order"));const actions=el("div","","mi-row"),preview=button("预览",()=>openViewer(candidate,row.id)),keep=button(row.retained&&row.selected_candidate===candidate.id?"取消保留":"保留",()=>{row.retained=!(row.retained&&row.selected_candidate===candidate.id);row.selected_candidate=candidate.id;save();renderQueue();renderResults();}),fill=button("回填",()=>backfillImages([candidate.id]));keep.disabled=busy;actions.append(preview,keep,fill);card.append(frame,actions,el("span",`${row.id}${candidate.stale?" · 旧提示词":""} · 种子 ${candidate.seed??"—"}`,"mi-muted"));results.append(card);});
        selectionNote.textContent=selectedResults.length?`已点选 ${selectedResults.length} 张`:"点击图片选择回填顺序";backfill.disabled=!selectedResults.length||!sourceEnabled();clearSelection.disabled=!selectedResults.length;
    }
    showAll.onchange=()=>{resultOffset=0;renderResults();};
    function render(){
        discoverSource();const enabled=sourceEnabled();if(node.properties.h3_manual_last_source_enabled!==enabled){node.mode=enabled?0:2;node.properties.h3_manual_last_source_enabled=enabled;}if(!enabled)node.mode=2;if(!enabled&&node.properties.h3_manual_isolation){try{setIsolation(false);}catch{}node.mode=2;}
        body.setAttribute("aria-disabled",String(!enabled));stateLabel.textContent=enabled?(busy?"生成中":node.mode===2?"已停用":"已启用"):"手动模式下启用";isolation.checked=!!node.properties.h3_manual_isolation;
        renderReferences();renderQueue();renderEditor();renderResults();const row=active();single.disabled=reroll.disabled=batch.disabled=busy||queueing||!enabled||!row;reroll.disabled ||= !!row?.retained;pause.disabled=cancel.disabled=!busy;skip.disabled=busy||!row;applyRefs.disabled=busy||!row;for(const input of [width,height,seed,customSize])input.disabled=busy;progress.hidden=!busy;
    }
    function openViewer(candidate,label){previewFocus=document.activeElement;viewer.dataset.url=imageURL(candidate.image);viewer.dataset.name=candidate.image.filename;viewerImage.src=viewer.dataset.url;viewerImage.alt=`${label}候选图片`;viewerCaption.textContent=label;viewer.hidden=false;viewer.focus({preventScroll:true});}
    function closeViewer(){viewer.hidden=true;viewerImage.removeAttribute("src");previewFocus?.focus?.({preventScroll:true});}
    viewer.onkeydown=event=>{if(event.key==="Escape"){event.preventDefault();closeViewer();}};
    async function copyText(text){try{await navigator.clipboard.writeText(text);note("已复制完整提示词。");}catch(error){note(`复制失败：${error.message}`,true);}}
    async function download(url,name){try{const response=await fetch(url);if(!response.ok)throw new Error("图片读取失败");const blob=await response.blob();const local=URL.createObjectURL(blob),anchor=el("a");anchor.href=local;anchor.download=name||"分镜.png";anchor.click();setTimeout(()=>URL.revokeObjectURL(local),1000);}catch(error){note(error.message,true);}}
    async function backfillImages(identities=selectedResults){
        const owner=source();if(!owner?.h3ManualStoryboard?.setImages||!sourceEnabled()){note("请连接手动分镜工作台后回填。",true);return;}
        const all=new Map(allCandidates().map(item=>[item.candidate.id,item.candidate]));const images=identities.map(id=>all.get(id)?.image).filter(Boolean);if(!images.length)return;
        try{backfill.disabled=true;const result=await owner.h3ManualStoryboard.setImages(images);note(`已回填 ${result?.assigned??images.length} 张${result?.overflow?`，剩余 ${result.overflow} 张超出槽位`:""}。`);}catch(error){note(`回填失败：${error.message}`,true);}finally{renderResults();}
    }
    async function run(all,regenerate){
        if(busy||queueing)return;const row=active();if(!row)return;
        if(!sourceEnabled()){note("请先启用手动分镜图模式。",true);return;}
        const targets=all?data.shots.filter(item=>!item.skip&&!item.retained&&!(item.candidates||[]).some(candidate=>!candidate.stale)):[row];
        if(!targets.length){note("全部分镜已有图片或已保留，无需重复生成。");return;}
        snapshotSettings();if(![data.width,data.height].every(value=>Number.isInteger(value)&&value>=64&&value<=2048&&value%32===0)){note("尚未得到工作台的实际画幅，请重新导入已规划的分镜或填写宽高。",true);return;}
        for(const item of targets){if(!item.prompt.trim()||!item.references?.length){note(`${item.id}缺少提示词或参考图。`,true);return;}}
        if(!all&&row.retained){note("本镜已保留，请先取消保留再生成。",true);return;}
        try{queueing=true;setIsolation(true);data.runSelection=targets.map(item=>item.id);data.regenerate=regenerate;data.stopAfterCurrent=false;data.request_id=crypto.randomUUID();save();render();note("正在提交独立生图任务…");const result=await app.queuePrompt(0,1);if(result===false)throw new Error("任务未提交，请查看ComfyUI错误提示。");busy=true;note("任务已提交，等待Image2.1开始。");}
        catch(error){busy=false;note(`提交失败：${error.message}`,true);}finally{queueing=false;render();}
    }
    async function control(action){try{const response=await api.fetchApi("/h3-director/manual-images/control",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({task_id:data.task_id,action})});const value=await response.json();if(!response.ok)throw new Error(value.error||"停止失败");note(!value.active?"当前任务尚未开始，可使用ComfyUI停止按钮取消排队。":action==="pause"?"本张完成后暂停，已有结果保留。":"正在停止当前图片，已有结果保留。");}catch(error){note(error.message,true);}}
    function applyProgress(event){
        if(disposed||String(event.node_id)!==String(node.id)||event.task_id!==data.task_id)return;
        const oldActive=active()?.id;const prior=new Map(data.shots.map(row=>[row.id,row]));
        for(const row of event.shots||[]){const previous=prior.get(row.id);if(previous){row.retained=previous.retained;row.skip=previous.skip;} }
        data.shots=event.shots||data.shots;if(oldActive)data.activeId=oldActive;busy=event.status==="running";
        const running=data.shots.find(row=>row.id===event.active_id);progress.value=Number(running?.progress)||0;
        const signature=JSON.stringify([event.status,event.active_id,data.shots.map(row=>[row.id,row.status,row.candidates?.length||0])]);if(signature!==lastProgressSignature){lastProgressSignature=signature;save();render();}const phrase=running?`${running.id} · ${PHASES[event.phase]||"生成中"}${running.progress?` ${Math.round(running.progress)}%`:""}`:STATES[event.status]||event.status;
        note(event.error||`${phrase} · 本批完成 ${event.completed||0} / ${event.total||0}`,!!event.error);
    }
    const onProgress=event=>applyProgress(event.detail||event);
    const onMode=event=>{if(String(event.detail?.sourceNodeId)===String(data.sourceNodeId)){if(!event.detail.enabled&&busy)control("cancel");render();}};
    api.addEventListener("h3-manual-image-progress",onProgress);window.addEventListener("h3-manual-mode-changed",onMode);document.addEventListener("h3-manual-mode-changed",onMode);
    const coreMatches=detail=>!!detail&&(activePromptId&&detail.prompt_id===activePromptId||String(detail.node_id??detail.node??"")===String(node.id));
    const onCoreStart=event=>{pendingPromptId=event.detail?.prompt_id||null;};
    const onCoreExecuting=event=>{const detail=event.detail||{};if(String(detail.node??detail.display_node??"")===String(node.id))activePromptId=detail.prompt_id||pendingPromptId;};
    const onCoreEnd=event=>{if(!busy||!coreMatches(event.detail))return;busy=false;render();restoreReceipt();};
    const onCoreError=event=>{if(coreMatches(event.detail)){busy=false;render();restoreReceipt();}};
    api.addEventListener("execution_start",onCoreStart);api.addEventListener("executing",onCoreExecuting);api.addEventListener("execution_interrupted",onCoreEnd);api.addEventListener("execution_success",onCoreEnd);api.addEventListener("execution_error",onCoreError);
    async function restoreReceipt(){if(!data.task_id)return;try{const response=await api.fetchApi(`/h3-director/manual-images/${encodeURIComponent(data.task_id)}`);if(!response.ok)return;const receipt=await response.json();if(receipt.status==="running")return;applyProgress({...receipt,node_id:String(node.id)});}catch{/* The serialized local draft remains available offline. */}}
    node.h3ManualImageStudio={
        setSourceEnabled(sourceNodeId,enabled){
            if(data.sourceNodeId!=null&&String(data.sourceNodeId)!==String(sourceNodeId))return;
            data.sourceNodeId=sourceNodeId;node.properties.h3_manual_source_id=sourceNodeId;
            if(!enabled&&busy)control("cancel");render();
        },
        importShots(payload){
            if(busy)throw new Error("请先停止独立生图，再导入新的提示词。");
            if(!payload||!Array.isArray(payload.shots))throw new Error("导入的提示词格式无效。");
            if(data.sourceNodeId!=null&&String(data.sourceNodeId)!==String(payload.sourceNodeId)&&data.shots.length)throw new Error("此生图节点已绑定另一工作台，请新建生图节点。");
            data.sourceNodeId=payload.sourceNodeId;node.properties.h3_manual_source_id=payload.sourceNodeId;data.source_mode="手动分镜图";lastSource=payload.sourceNodeId;
            const refs=validReferences(payload.references);data.references=clone(refs);const chosenKeys=new Set(data.selectedReferenceKeys||[]);let selectedRefs=refs.filter(ref=>chosenKeys.has(refKey(ref)));if(!selectedRefs.length)selectedRefs=refs.slice(0,1);const existing=new Map(data.shots.map(row=>[row.id,row]));let count=0;
            for(const incoming of payload.shots){if(!incoming?.id||typeof incoming.prompt!=="string")continue;const previous=existing.get(incoming.id);if(!previous&&data.shots.length>=MAX_SHOTS)throw new Error("最多导入100个分镜。");const row=previous||{id:incoming.id,index:incoming.index,candidates:[],retained:false,status:"pending"};const changed=row.prompt!==incoming.prompt||JSON.stringify(row.references)!==JSON.stringify(selectedRefs);row.prompt=incoming.prompt;row.index=incoming.index;row.references=clone(selectedRefs);row.skip=false;if(changed)markEdited(row);if(!previous)data.shots.push(row);data.activeId=row.id;count++;}
            if(payload.width&&payload.height){data.width=payload.width;data.height=payload.height;data.dimensions_source="source";}if(payload.image_settings)data.image_settings=clone(payload.image_settings);snapshotSettings();save();render();note(`已导入 ${count} 个提示词，保存 ${selectedRefs.length} 张参考图。`);return count;
        },
        isBusy:()=>busy,
        restoreIsolation:()=>setIsolation(false),
    };
    const widget=node.addDOMWidget("独立生图工作区","h3-manual-images",root,{serialize:false,hideOnZoom:false,canvasOnly:false,hideInPanel:true});
    widget.computeLayoutSize=()=>({minWidth:470,minHeight:500,maxHeight:Infinity});
    node.size=[Math.max(490,node.size?.[0]||0),Math.max(980,node.size?.[1]||0)];
    root.addEventListener("pointerdown",event=>event.stopPropagation());root.addEventListener("keydown",event=>event.stopPropagation());root.addEventListener("wheel",event=>event.stopPropagation(),{passive:true});
    const configured=node.onConfigure;node.onConfigure=function(configuration){const firstTemplate=!Object.prototype.hasOwnProperty.call(configuration?.properties||{},"h3_manual_last_source_enabled");const result=configured?.apply(this,arguments);if(firstTemplate)delete node.properties.h3_manual_last_source_enabled;hide(dataWidget);read();queueMicrotask(()=>{if(!disposed){discoverSource();render();restoreReceipt();}});return result;};
    const connectionsChanged=node.onConnectionsChange;node.onConnectionsChange=function(){const result=connectionsChanged?.apply(this,arguments);queueMicrotask(()=>{if(!disposed){discoverSource();render();}});return result;};
    const executed=node.onExecuted;node.onExecuted=function(output){const result=executed?.apply(this,arguments);const event=output?.h3_manual_images?.[0];if(event)applyProgress(event);return result;};
    const removed=node.onRemoved;node.onRemoved=function(){disposed=true;if(busy)control("cancel");try{setIsolation(false,false);}catch{}closeViewer();api.removeEventListener("h3-manual-image-progress",onProgress);api.removeEventListener("execution_start",onCoreStart);api.removeEventListener("executing",onCoreExecuting);api.removeEventListener("execution_interrupted",onCoreEnd);api.removeEventListener("execution_success",onCoreEnd);api.removeEventListener("execution_error",onCoreError);window.removeEventListener("h3-manual-mode-changed",onMode);document.removeEventListener("h3-manual-mode-changed",onMode);delete node.h3ManualImageStudio;return removed?.apply(this,arguments);};
    read();discoverSource();render();
}

app.registerExtension({name:"H3.Director.ManualImageStudio",async beforeRegisterNodeDef(nodeType,nodeData){if(nodeData.name!==TYPE)return;const created=nodeType.prototype.onNodeCreated;nodeType.prototype.onNodeCreated=function(){const result=created?.apply(this,arguments);setup(this);return result;};}});
