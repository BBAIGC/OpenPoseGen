import {app} from '/scripts/app.js';
const types=new Set(['BBAPIPromptToImage','BBAPIImageToImage','BBAPIMultiReference','BBAPIFolderBatch']);
const field=(n,name)=>n.widgets?.find(w=>w.name===name);
app.registerExtension({name:'BBAPI.OutputMode',beforeRegisterNodeDef(type,data){
 if(!types.has(data.name))return;
 const create=type.prototype.onNodeCreated;type.prototype.onNodeCreated=function(){const value=create?.apply(this,arguments),node=this,mode=field(this,'输出模式');if(!mode)return value;
  mode.hidden=true;mode.computeSize=()=>[0,-4];if(mode.inputEl)mode.inputEl.style.display='none';
  const root=document.createElement('div');root.style.cssText='display:flex;position:relative;padding:3px;gap:0;height:34px;box-sizing:border-box;background:var(--comfy-input-bg);border:1px solid var(--border-color);border-radius:4px';root.setAttribute('role','radiogroup');root.setAttribute('aria-label','生图输出模式');
  for(const property of ['height','min-height','max-height'])root.style.setProperty(property,'36px','important');root.style.setProperty('flex','0 0 36px','important');
  const indicator=document.createElement('span');indicator.style.cssText='position:absolute;left:3px;top:3px;bottom:3px;width:calc((100% - 6px)/2);background:#8885;pointer-events:none';root.append(indicator);const buttons=[];
  const render=()=>{indicator.style.transform=`translateX(${mode.value==='保存并预览'?100:0}%)`;for(const[val,b]of buttons)b.setAttribute('aria-checked',String(val===mode.value));};
  for(const val of ['预览（不保存）','保存并预览']){const b=document.createElement('button');b.type='button';b.textContent=val;b.setAttribute('role','radio');b.style.cssText='position:relative;flex:1;border:0;background:transparent;color:var(--input-text);font:12px sans-serif;cursor:pointer';b.onclick=()=>{mode.value=val;mode.callback?.(val);node.graph?.setDirtyCanvas(true,true);render();};root.append(b);buttons.push([val,b]);}
  node.addDOMWidget('输出模式切换','BBAPI_OUTPUT_MODE',root,{serialize:false,canvasOnly:false,hideInPanel:true,getMinHeight:()=>36,getMaxHeight:()=>36});
  if(field(node,'重新生成编号'))node.addWidget('button','重新生成一组',null,()=>{const w=field(node,'重新生成编号');w.value=Number(w.value||0)+1;w.callback?.(w.value);node.graph?.setDirtyCanvas(true,true);}, {serialize:false});
  const configured=node.onConfigure;node.onConfigure=function(saved){const out=configured?.apply(this,arguments);const selected=saved?.widgets_values_named?.['输出模式']??mode.value;mode.value=['预览（不保存）','保存并预览'].includes(selected)?selected:'保存并预览';render();return out;};
  const serialized=node.onSerialize;node.onSerialize=function(saved){const out=serialized?.apply(this,arguments);saved.widgets_values_named={...(saved.widgets_values_named||{}),'输出模式':mode.value};return out;};render();return value;
 };
}});
