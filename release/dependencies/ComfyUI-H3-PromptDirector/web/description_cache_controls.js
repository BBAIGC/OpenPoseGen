const field=(node,name)=>node.widgets?.find(widget=>widget.name===name);
const EPOCH='清除缓存批次';
const DESCRIBE_FIELDS={
 CodexReferenceDescribe:['模型','前置规则','补充要求','读取长边','输出上限','上下文','温度','top_p',EPOCH],
 CodexJoyCaptionDescribe:['模型','显存模式','保持加载','system_prompt','user_query','max_new_tokens','temperature','top_p','top_k',EPOCH],
};

export function normalizeDescriptionCacheEpoch(value){
 if(value&&typeof value==='object'&&!Array.isArray(value)&&Object.hasOwn(value,'__value__'))return normalizeDescriptionCacheEpoch(value.__value__);
 if(Array.isArray(value))return value.length===1?normalizeDescriptionCacheEpoch(value[0]):0;
 if(typeof value==='string'&&/^\d+$/.test(value))value=Number(value);
 return typeof value==='number'&&Number.isSafeInteger(value)&&value>=0&&value<=2147483647?value:0;
}

function positionalValues(node,saved){
 const names=DESCRIBE_FIELDS[node.type];if(!names)return [];
 const values=Array.isArray(saved?.widgets_values)?saved.widgets_values:[];
 const epochIndex=names.length-1;
 // AlekPet appends one [saveAs,clearText] widget per multiline editor. An old
 // save has those pairs directly after the previous eight business widgets.
 // Insert the newly introduced epoch before them; never consume a speech pair
 // as a model parameter or shift the following speech preferences.
 if(Array.isArray(values[epochIndex])&&values[epochIndex].length===2&&values[epochIndex].every(value=>typeof value==='boolean')){
  values.splice(epochIndex,0,0);
 }
 return values;
}

export function repairDescriptionCacheValues(node,saved){
 const names=DESCRIBE_FIELDS[node.type];if(!names)return;
 const values=positionalValues(node,saved),named=saved?.widgets_values_named;
 for(const[index,name]of names.entries()){
  const widget=field(node,name);if(!widget)continue;
  if(name===EPOCH){
   const candidate=named&&Object.hasOwn(named,name)?named[name]:values.length>index?values[index]:widget.value;
   widget.value=normalizeDescriptionCacheEpoch(candidate);
  }else{
   const candidate=named&&Object.hasOwn(named,name)?named[name]:values[index];
   if(candidate!==undefined&&!Array.isArray(candidate))widget.value=candidate;
  }
  if(widget.inputEl)widget.inputEl.value=String(widget.value??'');
 }
}

export function serializeDescriptionCacheValues(node,saved){
 const names=DESCRIBE_FIELDS[node.type];if(!names)return;
 const epoch=field(node,EPOCH);if(epoch)epoch.value=normalizeDescriptionCacheEpoch(epoch.value);
 const named=Object.fromEntries(names.map(name=>[name,field(node,name)?.value]));
 // Keep extension widgets serialized after the named, stable business block.
 const extras=(node.widgets||[]).filter(widget=>!names.includes(widget.name)&&widget.serialize!==false).map(widget=>widget.value);
 saved.widgets_values_named={...saved.widgets_values_named,...named};
 saved.widgets_values=[...names.map(name=>named[name]),...extras];
}

export function installDescriptionCacheSerialization(node){
 if(!DESCRIBE_FIELDS[node.type]||node._codexDescriptionCacheSerialization)return;
 node._codexDescriptionCacheSerialization=true;
 const epoch=field(node,EPOCH);
 if(epoch){epoch.value=normalizeDescriptionCacheEpoch(epoch.value);epoch.serializeValue=()=>{epoch.value=normalizeDescriptionCacheEpoch(epoch.value);return epoch.value;};}
 const configured=node.onConfigure;
 node.onConfigure=function(saved){
  positionalValues(this,saved);
  const result=configured?.apply(this,arguments);
  repairDescriptionCacheValues(this,saved);
  if(result&&typeof result.then==='function')result.then(()=>repairDescriptionCacheValues(this,saved));
  return result;
 };
 const serialized=node.onSerialize;
 node.onSerialize=function(saved){const result=serialized?.apply(this,arguments);serializeDescriptionCacheValues(this,saved);return result;};
}

export function sanitizeDescriptionCacheGraph(graph){
 for(const node of graph?._nodes||[]){
  if(DESCRIBE_FIELDS[node.type]){const epoch=field(node,EPOCH);if(epoch)epoch.value=normalizeDescriptionCacheEpoch(epoch.value);}
  if(node.subgraph)sanitizeDescriptionCacheGraph(node.subgraph);
 }
}

export function sanitizeDescriptionCachePrompt(result,graph){
 const output=result?.output||result?.prompt;
 for(const[id,node]of Object.entries(output||{})){
  if(!DESCRIBE_FIELDS[node?.class_type]||!node.inputs)continue;
  const owner=graph?.getNodeById?.(id);
  const connected=owner?.inputs?.find(input=>input.name===EPOCH)?.link!=null;
  const value=node.inputs[EPOCH];
  const apiLink=Array.isArray(value)&&value.length===2&&typeof value[0]==='string'&&Number.isInteger(value[1])&&value[1]>=0;
  if(!connected&&!apiLink)node.inputs[EPOCH]=normalizeDescriptionCacheEpoch(value);
 }
 const workflows=[result?.workflow,result?.workflow?.definitions?.subgraphs].flat().filter(Boolean);
 for(const workflow of workflows)for(const node of workflow.nodes||[]){
  const names=DESCRIBE_FIELDS[node.type];if(!names)continue;
  const values=positionalValues(node,node),epochIndex=names.length-1;
  const named=node.widgets_values_named;
  const epoch=normalizeDescriptionCacheEpoch(named&&Object.hasOwn(named,EPOCH)?named[EPOCH]:values[epochIndex]);
  if(Array.isArray(node.widgets_values))node.widgets_values[epochIndex]=epoch;
  if(named)named[EPOCH]=epoch;
 }
 return result;
}

export function installDescriptionCachePromptGuard(app){
 if(app._codexDescriptionCachePromptGuard||typeof app.graphToPrompt!=='function')return;
 app._codexDescriptionCachePromptGuard=true;
 const previous=app.graphToPrompt;
 app.graphToPrompt=async function(){
  sanitizeDescriptionCacheGraph(this.rootGraph||this.graph);
  const result=await previous.apply(this,arguments);
  sanitizeDescriptionCacheGraph(this.rootGraph||this.graph);
  return sanitizeDescriptionCachePrompt(result,this.rootGraph||this.graph);
 };
}

export function setDescriptionCachePending(control,ids,enabled){
 control.properties||={};
 const previous=control.properties.codex_description_cache_previous||{};
 for(const id of ids){
  const widget=field(control.graph?.getNodeById(id),'清除缓存批次');if(!widget)continue;
  if(enabled){previous[id]=normalizeDescriptionCacheEpoch(widget.value);widget.value=Math.min(2147483647,previous[id]+1);}
  else if(Object.hasOwn(previous,id))widget.value=normalizeDescriptionCacheEpoch(previous[id]);
  widget.callback?.(widget.value);
 }
 control.properties.codex_description_cache_pending=Boolean(enabled);
 if(enabled)control.properties.codex_description_cache_previous=previous;
 else delete control.properties.codex_description_cache_previous;
 control.graph?.setDirtyCanvas(true,true);
}

export function finishDescriptionCache(graph,id,message){
 const receipt=message?.codex_description_cache?.[0];
 const target=graph?.getNodeById(id),epoch=field(target,'清除缓存批次');
 if(!receipt||!epoch||Number(receipt.epoch)!==Number(epoch.value))return;
 for(const node of graph._nodes||[]){
  if(node.type!=='CodexWorkflowControls'||!node.properties?.codex_description_cache_pending)continue;
  let map;try{map=JSON.parse(field(node,'控件映射')?.value||'{}');}catch{continue;}
  if(!(map.description_cache_nodes||[]).some(value=>String(value)===String(id)))continue;
  node.properties.codex_description_cache_pending=false;
  delete node.properties.codex_description_cache_previous;
 }
 graph.setDirtyCanvas?.(true,true);
}

export function appendDescriptionCacheControl(node,map,root,updates){
 const ids=map.description_cache_nodes;if(!Array.isArray(ids)||!ids.length)return;
 const label=document.createElement('label'),name=document.createElement('span'),button=document.createElement('button');
 name.textContent='清除反推缓存';label.style.cssText='display:grid;grid-template-columns:110px 140px;gap:10px;align-items:center;justify-content:space-between;min-height:30px';
 button.type='button';button.className='codex-control-toggle';button.setAttribute('role','switch');button.setAttribute('aria-label','清除反推缓存');button.title='开启后，下次反推重新读取图片；完成后自动关闭。';
 button.innerHTML='<span class="codex-control-toggle-track"><span class="codex-control-toggle-thumb"></span></span><span class="codex-control-toggle-text"></span>';
 const refresh=()=>{const pending=Boolean(node.properties?.codex_description_cache_pending);button.setAttribute('aria-checked',String(pending));button.classList.toggle('is-on',pending);button.querySelector('.codex-control-toggle-text').textContent=pending?'待重推':'关闭';};
 button.onclick=()=>{setDescriptionCachePending(node,ids,!node.properties?.codex_description_cache_pending);refresh();};
 label.append(name,button);root.append(label);updates.push(refresh);refresh();
}
