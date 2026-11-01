import {app} from '/scripts/app.js';
import {api} from '/scripts/api.js';

const OWN=/^(?:H3|Codex|BBAPI)/;
const REPOS={h3:'ComfyUI-BBAIGC-MiniMax-H3',image:'ComfyUI-BBAIGC-Image-Studio',bbapi:'ComfyUI-BBAIGC-BBAPI'};
const element=(tag,text='')=>Object.assign(document.createElement(tag),{textContent:text});
function legacyIdBadgeWidth(node,ctx){
 // Mixlab's manager fallback draws this badge in onDrawForeground, outside
 // LiteGraph's badge layout. Match its actual text and font before reserving
 // room; source and author badges then keep their normal hit-test rectangles.
 if(!node.badge_enabled||typeof node.getNickname!=='function'||node.flags?.collapsed||node.constructor.title_mode===window.LiteGraph?.NO_TITLE)return 0;
 let nickname=node.getNickname();
 if(nickname==='ComfyUI')nickname='🦊';
 if(nickname?.length>25)nickname=nickname.substring(0,23)+'..';
 const text=`#${node.id} ${nickname||''}`;
 ctx.save();ctx.font='12px sans-serif';
 try{return ctx.measureText(text).width+12;}finally{ctx.restore();}
}
function reserveIdBadgeSpace(node){
 if(node._bbaigcIdBadgeSpacingInstalled||typeof node.drawBadges!=='function')return;
 node._bbaigcIdBadgeSpacingInstalled=true;
 const spacer=new window.LGraphBadge({text:''});
 let reservedWidth=0;
 spacer.getWidth=()=>reservedWidth;
 spacer.draw=()=>{};
 const draw=node.drawBadges;
 node.drawBadges=function(ctx){
  const badges=this.badges;
  if(!Array.isArray(badges))return draw.apply(this,arguments);
  const index=badges.indexOf(spacer);if(index>=0)badges.splice(index,1);
  reservedWidth=legacyIdBadgeWidth(this,ctx);
  if(reservedWidth>0)badges.push(spacer);
  return draw.apply(this,arguments);
 };
}
function brand(node){
 if(!OWN.test(node.comfyClass||node.type||''))return;
 node.properties||={};node.properties.bbaigc_author='BBAIGC';
 if(window.LGraphBadge&&Array.isArray(node.badges)&&!node._bbaigcBadge){
  node._bbaigcBadge=new window.LGraphBadge({text:'BBAIGC',fgColor:'#f0ce87',bgColor:'#29251c',fontSize:12,cornerRadius:3});
  node.badges.push(node._bbaigcBadge);
 }
 if(node._bbaigcBadge)reserveIdBadgeSpace(node);
}
function zoom(event){
 if(!event.ctrlKey&&!event.metaKey)return;
 const target=event.target instanceof Element?event.target:null;
 if(!target?.closest('.h3-studio,.qws-panel,.qws-controls,.codex-controls,.codex-control-style,.h3-manual-studio,.h3-manual-images,.h3-dialogue-studio,.codex-speech,[data-bbaigc-panel]'))return;
 const ds=app.canvas?.ds;if(!ds?.changeScale||!event.deltaY)return;
 event.preventDefault();event.stopImmediatePropagation();
 const unit=event.deltaMode===1?18:event.deltaMode===2?600:1;
 const scale=ds.scale*Math.exp(-Math.max(-160,Math.min(160,event.deltaY*unit))*.0015);
 ds.changeScale(Math.max(ds.min_scale??0.1,Math.min(ds.max_scale??10,scale)),[event.clientX,event.clientY]);
 app.canvas.setDirty(true,true);
}
async function updates(){
 if(document.querySelector('#bbaigc-updates'))return;
 const dialog=element('dialog');dialog.id='bbaigc-updates';
 Object.assign(dialog.style,{width:'640px',maxWidth:'90vw',background:'#202020',color:'#eee',border:'1px solid #555',borderRadius:'10px',padding:'22px'});
 const title=element('h2','BBAIGC 工作流更新'),body=element('div','正在检查版本…'),close=element('button','关闭');
 close.onclick=()=>dialog.close();dialog.append(title,body,close);document.body.append(dialog);dialog.onclose=()=>dialog.remove();dialog.showModal();
 try{
  const response=await api.fetchApi('/bbaigc/updates');if(!response.ok)throw Error('请在当前任务结束后重启 ComfyUI，以启用更新服务');
  const data=await response.json();body.replaceChildren();
  for(const item of data.products){
   const row=element('div');Object.assign(row.style,{padding:'14px 0',borderTop:'1px solid #444'});
   const version=element('strong',`${item.name} · 本地 ${item.local_version||'未安装'} → 最新 ${item.version||'暂未获取'}`);row.append(version);
   const note=element('p',item.error||item.notes||'已是最新版本');row.append(note);
   const link=element('a','查看仓库');link.href=`https://github.com/BBAIGC/${REPOS[item.id]}`;link.target='_blank';link.rel='noopener';row.append(link);
   if(item.version&&!item.error){
    const localNodes=item.local_node_versions||{},wantedNodes=item.node_versions||{};
    if(Object.keys(wantedNodes).length)row.append(element('p',Object.entries(wantedNodes).map(([name,value])=>`${name==='core'?'共享节点':'BBAPI节点'} ${localNodes[name]||'未安装'} → ${value}`).join(' · ')));
    const install=element('button',item.update_available?'更新工作流':'重新安装工作流');install.style.marginLeft='16px';row.append(install);
    const nodes=element('button','更新节点与工作流');nodes.style.marginLeft='8px';row.append(nodes);
    const perform=async includeNodes=>{install.disabled=nodes.disabled=true;note.textContent=includeNodes?'正在检查本地改动并更新…':'正在下载并备份原文件…';try{
     const response=await api.fetchApi('/bbaigc/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:item.id,include_nodes:includeNodes})});
     const result=await response.json();if(!response.ok)throw Error(result.error||'更新失败');note.textContent=result.message;
     item.local_version=result.version||item.version;item.update_available=false;version.textContent=`${item.name} · 本地 ${item.local_version} → 最新 ${item.version}`;install.textContent='重新安装工作流';
     if(includeNodes)nodes.textContent='节点与工作流已更新';
    }catch(error){note.textContent=error.message;}finally{install.disabled=nodes.disabled=false;}};
    install.onclick=()=>perform(false);nodes.onclick=()=>perform(true);
   }
   body.append(row);
  }
 }catch(error){body.textContent=error.message;}
}
app.registerExtension({name:'BBAIGC.BrandAndUpdates',nodeCreated:brand,async setup(){
 document.addEventListener('wheel',zoom,{capture:true,passive:false});
 for(const node of app.graph?._nodes||[])brand(node);
 const button=element('button','BBAIGC · 检查更新');button.title='查看三套工作流版本并选择更新';button.onclick=updates;
 Object.assign(button.style,{position:'fixed',right:'18px',bottom:'56px',zIndex:'100',fontSize:'12px',padding:'7px 11px',background:'#28251d',color:'#ebca88',border:'1px solid #665535',borderRadius:'5px',cursor:'pointer'});
 document.body.append(button);
 // A failed network request is quiet; local workflows remain usable offline.
 setTimeout(async()=>{try{const response=await api.fetchApi('/bbaigc/updates');if(!response.ok)return;const data=await response.json();const count=data.products.filter(item=>item.update_available||item.node_update_available).length;if(count)button.textContent=`BBAIGC · ${count} 项可更新`;}catch{}},10000);
}});
