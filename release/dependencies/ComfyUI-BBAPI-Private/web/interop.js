import {app} from '/scripts/app.js';
const kinds=new Set(['BBAPIPromptToImage','BBAPIImageToImage','BBAPIMultiReference','BBAPIFolderBatch']);
const ratios={'1:1':'方形','3:2':'横','2:3':'竖','4:3':'横','3:4':'竖','16:9':'横','9:16':'竖'};
let catalog=[];
window.addEventListener('bbapi-models',event=>{catalog=(event.detail||[]).map(item=>item.id);});
function setup(node,type){
 if(type==='BBAPIReferenceInputs'){
  const count=node.widgets?.find(w=>w.name==='参考图数量');
  const update=()=>{const n=Math.max(1,Math.min(16,Number(count?.value)||1));
   for(let i=node.inputs.length-1;i>=0;i--)if(/^参考图\d+$/.test(node.inputs[i].name)&&Number(node.inputs[i].name.slice(3))>n)node.removeInput(i);
   for(let i=1;i<=n;i++)if(!node.inputs.some(x=>x.name===`参考图${i}`))node.addInput(`参考图${i}`,'IMAGE');
   node.setSize([Math.max(285,node.size[0]),node.computeSize()[1]]);node.graph?.setDirtyCanvas(true,true);
  };const callback=count?.callback;if(count)count.callback=function(){callback?.apply(this,arguments);update();};
  const configure=node.onConfigure;node.onConfigure=function(){configure?.apply(this,arguments);update();};update();
 }
 if(kinds.has(type)){
  const model=node.widgets?.find(w=>w.name==='模型');if(model){model.type='combo';model.options||={};model.options.values=()=>[...new Set(['自动（2.5优先）',model.value,...catalog].filter(Boolean))];}
  const ratio=node.widgets?.find(w=>w.name==='比例');
  const normalize=()=>{if(ratio&&ratios[ratio.value])ratio.value=`${ratio.value}（${ratios[ratio.value]}）`;};
  if(ratio){ratio.options.values=()=>{let count=type==='BBAPIPromptToImage'?0:1;const port=node.inputs?.find(input=>input.name==='参考图'||input.name==='固定参考图');const link=node.graph?.links?.[port?.link],source=node.graph?.getNodeById(link?.origin_id);if(source?.type==='BBAPIReferenceInputs')count=Number(fieldValue(source,'参考图数量'))||1;else if(source?.type==='BBAPIReferences'){try{count=JSON.parse(fieldValue(source,'图片文件')||'[]').length;}catch{}}else if(node._bbapiReferenceCount)count=node._bbapiReferenceCount;if(type==='BBAPIFolderBatch')count=Math.min(16,count+1);return [...Object.entries(ratios).map(([r,t])=>`${r}（${t}）`),...Array.from({length:Math.max(0,Math.min(16,count))},(_,i)=>`跟随参考图${i+1}`)];};normalize();}
  const configure=node.onConfigure;node.onConfigure=function(){configure?.apply(this,arguments);normalize();};
 }
}
function fieldValue(node,name){return node.widgets?.find(w=>w.name===name)?.value;}
app.registerExtension({name:'BBAPI.NativeInterop',beforeRegisterNodeDef(type,data){
 if(!kinds.has(data.name)&&data.name!=='BBAPIReferenceInputs')return;
 const created=type.prototype.onNodeCreated;type.prototype.onNodeCreated=function(){const result=created?.apply(this,arguments);setup(this,data.name);return result;};
}});
