import {hideNativeWidget} from './nodes2_dom_compat.js';
import {app} from '/scripts/app.js';
const field=(node,name)=>node?.widgets?.find(item=>item.name===name);
const detailLabels=['精简','简洁','标准','详细','极详细'];
const detailLevel=value=>Math.max(1,Math.min(5,Math.round(Number(value)||3)));

function syncPromptTarget(node){
 if(node.properties?.codex_target_node==null)return;
 const target=node.graph?.getNodeById(node.properties.codex_target_node),value=field(target,'生成路线')?.value,selected=field(node,'目标模型');
 if(selected&&['Z-Image Turbo','Image2.1','GPT Image 2/2.5'].includes(value)&&selected.value!==value){selected.value=value;selected.callback?.(value);}
}

function style(){
 if(document.getElementById('codex-output-mode-style'))return;
 const sheet=document.createElement('style');sheet.id='codex-output-mode-style';
 sheet.textContent='.codex-output-mode{position:relative;display:flex;gap:0;padding:3px;border:1px solid var(--border-color,#555);border-radius:4px;background:var(--comfy-input-bg,#292929);height:34px;box-sizing:border-box}.codex-output-mode>span{position:absolute;left:3px;top:3px;bottom:3px;width:calc((100% - 6px)/2);background:#8885;border-radius:2px;transition:transform .18s;pointer-events:none}.codex-output-mode>button{position:relative;z-index:1;flex:1;border:0;background:transparent;color:var(--input-text,#ddd);font:12px sans-serif;cursor:pointer}.codex-output-mode>button:focus-visible{outline:1px solid currentColor}.codex-prompt-detail{display:flex;flex-direction:column;gap:7px;min-height:58px;padding:5px 3px;color:var(--input-text,#ddd);font:12px sans-serif;box-sizing:border-box}.codex-prompt-detail>div{display:flex;justify-content:space-between;align-items:center}.codex-detail-label{color:#bd9bff;white-space:nowrap}.codex-detail-range{display:block;flex-shrink:0;width:100%;min-width:0;height:24px;margin:0;padding:0;background:transparent;appearance:none;-webkit-appearance:none;cursor:pointer;accent-color:#9b6bff}.codex-detail-range::-webkit-slider-runnable-track{height:10px;border-radius:5px;background:linear-gradient(90deg,#5369db,#9a66ec,#b880ed)}.codex-detail-range::-webkit-slider-thumb{width:20px;height:20px;margin-top:-5px;border:0;border-radius:50%;background:#fff;box-shadow:0 1px 5px #29174780;appearance:none;-webkit-appearance:none}.codex-detail-range::-moz-range-track{height:10px;border-radius:5px;background:linear-gradient(90deg,#5369db,#9a66ec,#b880ed)}.codex-detail-range::-moz-range-thumb{width:20px;height:20px;border:0;border-radius:50%;background:#fff}.codex-detail-range:focus-visible{outline:1px solid #bd9bff;outline-offset:3px;border-radius:3px}@media(prefers-reduced-motion:reduce){.codex-output-mode>span{transition:none}}';document.head.append(sheet);
}

function outputMode(node){
 style();const mode=field(node,'输出模式');if(!mode)return;
 hideNativeWidget(mode);mode.computeSize=()=>[0,-4];if(mode.inputEl)mode.inputEl.style.display='none';
 const root=document.createElement('div');root.className='codex-output-mode';root.setAttribute('role','radiogroup');root.setAttribute('aria-label','输出模式');
 const indicator=document.createElement('span');root.append(indicator);const buttons=[];
 const render=()=>{const save=mode.value==='保存并预览';indicator.style.transform=`translateX(${save?100:0}%)`;for(const[value,button]of buttons){button.setAttribute('aria-checked',String(value===mode.value));button.tabIndex=value===mode.value?0:-1;}};
 for(const[value,label]of [['预览（不保存）','预览（不保存）'],['保存并预览','保存并预览']]){
  const button=document.createElement('button');button.type='button';button.textContent=label;button.setAttribute('role','radio');
  button.onclick=()=>{mode.value=value;mode.callback?.(value);node.graph?.setDirtyCanvas(true,true);render();};
  button.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const selected=event.key==='Home'?0:event.key==='End'?1:mode.value==='保存并预览'?0:1;buttons[selected][1].focus();buttons[selected][1].click();};
  buttons.push([value,button]);root.append(button);
 }
 node.addDOMWidget('输出模式切换','CODEX_OUTPUT_MODE',root,{serialize:false,canvasOnly:false,hideInPanel:true,getMinHeight:()=>36,getMaxHeight:()=>36});
 const configured=node.onConfigure;node.onConfigure=function(){const result=configured?.apply(this,arguments);render();return result;};render();
}

function promptPanel(node){
 style();const detail=field(node,'详细程度');let refreshDetail=()=>{};
 for(const name of ['固定优化文本','固定原始文本']){const frozenText=field(node,name);if(frozenText){hideNativeWidget(frozenText);frozenText.computeSize=()=>[0,-4];if(frozenText.inputEl)frozenText.inputEl.style.display='none';}}
 if(detail){
  hideNativeWidget(detail);detail.computeSize=()=>[0,-4];if(detail.inputEl)detail.inputEl.style.display='none';
  const root=document.createElement('div');root.className='codex-prompt-detail';const heading=document.createElement('div'),label=document.createElement('span'),level=document.createElement('output'),range=document.createElement('input');
  label.textContent='提示词详细程度';level.className='codex-detail-label';range.type='range';range.min='1';range.max='5';range.step='1';range.className='codex-detail-range';range.setAttribute('aria-label','提示词详细程度');heading.append(label,level);root.append(heading,range);
  refreshDetail=()=>{const value=detailLevel(detail.value);detail.value=value;range.value=String(value);level.textContent=detailLabels[value-1];range.setAttribute('aria-valuetext',detailLabels[value-1]);};
  const changed=detail.callback;detail.callback=function(){const value=changed?.apply(this,arguments);refreshDetail();return value;};
  range.oninput=()=>{detail.value=detailLevel(range.value);detail.callback?.(detail.value);node.graph?.setDirtyCanvas(true,true);};
  node.addDOMWidget('详细程度滑块','CODEX_PROMPT_DETAIL',root,{serialize:false,canvasOnly:false,hideInPanel:true,getMinHeight:()=>58,getMaxHeight:()=>58});refreshDetail();
 }
 const result=document.createElement('textarea');result.readOnly=true;result.placeholder='启用后显示优化结果；关闭时原文直通';result.setAttribute('aria-label','最终生图提示词');
 result.style.cssText='width:100%;height:110px;resize:vertical;box-sizing:border-box;background:var(--comfy-input-bg);color:var(--input-text);border:1px solid var(--border-color);padding:7px;white-space:pre-wrap';
 const resultOptions={serialize:false,canvasOnly:false,hideInPanel:true,getMinHeight:()=>110};
 const resultWidget=node.addDOMWidget('优化结果','CODEX_PROMPT_RESULT',result,resultOptions);
 let externalInstalled=false;
 const externalLayout=()=>{
  if(node.properties?.bbaigc_prompt_layout!=='external-large'||externalInstalled)return;
  externalInstalled=true;
  const source=field(node,'原始提示词');hideNativeWidget(source);if(source){source.computeSize=()=>[0,-4];if(source.inputEl)source.inputEl.style.display='none';if(source.element)source.element.hidden=true;}
  const rule=field(node,'前置规则');if(!rule)return;
  hideNativeWidget(rule);rule.computeSize=()=>[0,-4];if(rule.inputEl)rule.inputEl.style.display='none';if(rule.element)rule.element.hidden=true;
  const root=document.createElement('div'),label=document.createElement('span'),editor=document.createElement('textarea');root.className='bbaigc-external-rules';root.dataset.bbaigcPanel='true';label.textContent='前置规则';editor.setAttribute('aria-label','前置规则');editor.value=String(rule.value??'');root.append(label,editor);
  // This opt-in workspace keeps rules readable without assigning the entire
  // node height to the output preview. The native fields retain their values.
  root.style.cssText='display:flex;flex-direction:column;gap:7px;box-sizing:border-box;padding:6px 0;min-height:0;overflow:hidden';
  editor.style.cssText='flex:1 1 0;min-height:260px;resize:none;overflow:auto;width:100%;box-sizing:border-box;padding:9px;border:1px solid var(--border-color,#555);background:var(--comfy-input-bg,#242424);color:var(--input-text,#ddd);font:12px/1.6 sans-serif;white-space:pre-wrap;overscroll-behavior:contain';
  editor.oninput=()=>{rule.value=editor.value;rule.callback?.(editor.value);node.graph?.setDirtyCanvas(true,true);};
  for(const name of ['pointerdown','pointerup','keydown','keyup','dblclick','wheel'])root.addEventListener(name,event=>event.stopPropagation());
  const rules=node.addDOMWidget('前置规则编辑器','CODEX_PROMPT_RULES',root,{serialize:false,canvasOnly:false,hideInPanel:true,getMinHeight:()=>294,getMaxHeight:()=>400});rules.serialize=false;rules.serializeValue=()=>undefined;rules.computeLayoutSize=()=>({minWidth:300,minHeight:294,maxHeight:400});
  const rulesIndex=node.widgets.indexOf(rules),detailIndex=node.widgets.findIndex(widget=>widget.name==='详细程度滑块');if(rulesIndex>=0&&detailIndex>=0){node.widgets.splice(rulesIndex,1);node.widgets.splice(detailIndex,0,rules);}
  resultOptions.getMaxHeight=()=>170;resultWidget.computeLayoutSize=()=>({minWidth:300,minHeight:110,maxHeight:170});result.style.minHeight='110px';result.style.maxHeight='170px';result.style.resize='none';
  const callback=rule.callback;rule.callback=function(){const value=callback?.apply(this,arguments);if(document.activeElement!==editor&&editor.value!==String(rule.value??''))editor.value=String(rule.value??'');return value;};
 };
 const executed=node.onExecuted;node.onExecuted=function(message){const value=executed?.apply(this,arguments);result.value=(message.text||[]).join('\n');return value;};
 const configured=node.onConfigure;node.onConfigure=function(){const value=configured?.apply(this,arguments);refreshDetail();externalLayout();return value;};externalLayout();
 const timer=setInterval(()=>{if(!result.isConnected)return;syncPromptTarget(node);refreshDetail();},700);
 const removed=node.onRemoved;node.onRemoved=function(){clearInterval(timer);return removed?.apply(this,arguments);};
}

app.registerExtension({name:'Codex.OutputAndPrompt',beforeRegisterNodeDef(type,data){
 if(!['CodexImageOutput','CodexPromptOptimize','CodexImageCanvas'].includes(data.name))return;
 const serialized=type.prototype.onSerialize;
 type.prototype.onSerialize=function(saved){if(data.name==='CodexPromptOptimize')syncPromptTarget(this);const result=serialized?.apply(this,arguments);saved.widgets_values_named=Object.fromEntries((this.widgets||[]).filter(widget=>widget.options?.serialize!==false&&['string','number','boolean'].includes(typeof widget.value)).map(widget=>[widget.name,widget.value]));if(data.name==='CodexPromptOptimize'&&Array.isArray(saved.widgets_values)){const index=(this.widgets||[]).filter(widget=>widget.options?.serialize!==false).findIndex(widget=>widget.name==='目标模型');if(index>=0)saved.widgets_values[index]=field(this,'目标模型')?.value;}return result;};
 const configured=type.prototype.onConfigure;
 type.prototype.onConfigure=function(saved){
  const value=configured?.apply(this,arguments);
  for(const[name,next]of Object.entries(saved?.widgets_values_named||{})){const widget=field(this,name);if(widget){widget.value=next;if(widget.inputEl)widget.inputEl.value=String(next);}}
  if(data.name==='CodexImageCanvas'&&!saved?.widgets_values_named&&typeof saved?.widgets_values?.[1]==='number'){
   const keys=['比例','长边','自定义宽度','自定义高度'];for(const[index,key]of keys.entries()){const widget=field(this,key);if(widget&&saved.widgets_values[index]!=null)widget.value=saved.widgets_values[index];}if(field(this,'尺寸基准'))field(this,'尺寸基准').value='最长边';
  }
  return value;
 };
 const created=type.prototype.onNodeCreated;type.prototype.onNodeCreated=function(){const value=created?.apply(this,arguments);if(data.name==='CodexImageOutput')outputMode(this);if(data.name==='CodexPromptOptimize')promptPanel(this);return value;};
}});
