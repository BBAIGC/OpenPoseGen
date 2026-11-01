import {request,isAbort} from './api.js';
import {openAIPlaceholder} from './placeholder.js';

const $=id=>document.getElementById(id);
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const ACTIVE=new Set(['queued','pending','running']);
const LABELS={queued:'等待中',pending:'准备中',running:'生成中',completed:'已完成',failed:'失败',cancelled:'已取消'};
const time=value=>Number.isFinite(value)?(value<60?Math.floor(value)+'秒':Math.floor(value/60)+'分'+Math.floor(value%60)+'秒'):null;
const percentage=value=>Number.isFinite(value)?Math.round(Math.max(0,Math.min(1,value))*100):null;
const created=job=>{const raw=job.created_at??job.created??0;return Number.isFinite(Number(raw))?Number(raw):Date.parse(raw)||0;};

export class JobPanel {
  constructor({media,onPreview,onCopy,onRestoreJob,onCanvas,onDrag,onToast,onChange,onMutation}) {
    Object.assign(this,{media,onPreview,onCopy,onRestoreJob,onCanvas,onDrag,onToast,onChange,onMutation});
    this.jobs=[];this.active=true;this.timer=0;this.loading=false;this.controller=null;this.resultSignature='';this.collapsed=false;try{this.collapsed=sessionStorage.getItem('bbaigc-results-collapsed')==='true';}catch{}
    $('jobs-toggle').onclick=()=>this.toggleDropdown();$('results-toggle').onclick=()=>this.toggleResults();
    document.addEventListener('pointerdown',event=>{if(!$('jobs-dropdown').contains(event.target)&&!$('jobs-toggle').contains(event.target))this.closeDropdown();if(!$('result-actions-menu').contains(event.target)&&!event.target.closest('.result-more'))$('result-actions-menu').hidden=true;});
    document.addEventListener('keydown',event=>{if(event.key==='Escape'){this.closeDropdown();$('result-actions-menu').hidden=true;}});
    this.resize=()=>{if(!$('jobs-dropdown').hidden)this.positionDropdown();};window.addEventListener('resize',this.resize);this.render();
  }
  async refresh() {
    clearTimeout(this.timer);this.timer=0;if(!this.active||this.loading)return;
    this.loading=true;const controller=new AbortController();this.controller=controller;
    try{
      const data=await request('/jobs',{signal:controller.signal});if(!this.active||controller.signal.aborted)return;
      this.jobs=(data.jobs||[]).slice(-50).reverse();
      // Accept either server ordering; latest creation timestamps win when present.
      if(this.jobs.some(job=>job.created_at||job.created))this.jobs.sort((a,b)=>created(b)-created(a));
      this.render();this.onChange?.(this.jobs);
    }catch(error){if(!isAbort(error)&&this.active){$('jobs-toggle').title='任务状态暂不可用';$('results-state').textContent='无法读取任务，稍后自动重试';}}
    finally{if(this.controller===controller){this.controller=null;this.loading=false;if(this.active)this.timer=setTimeout(()=>this.refresh(),this.jobs.some(job=>ACTIVE.has(job.status))?1200:5000);}}
  }
  accept(data) {
    const id=data.prompt_id||data.id;if(!id)return;
    const item={...data,id,prompt_id:id,status:data.status||'queued'};
    this.jobs=this.jobs.filter(job=>(job.id||job.prompt_id)!==id);this.jobs.unshift(item);this.render();this.refresh();
  }
  render(){this.renderRing();if(!$('jobs-dropdown').hidden)this.renderQueue();this.renderResults();}
  renderRing(){
    const active=this.jobs.filter(job=>ACTIVE.has(job.status)),running=active.find(job=>job.status==='running'),progress=running?percentage(running.progress):null;
    const ring=$('jobs-toggle').querySelector('.job-ring-progress'),circumference=2*Math.PI*14;
    ring.style.strokeDasharray=String(circumference);ring.style.strokeDashoffset=progress===null?(active.length?circumference*.73:circumference):circumference*(1-progress/100);
    $('jobs-toggle').classList.toggle('indeterminate',!!active.length&&progress===null);$('jobs-toggle').classList.toggle('has-active',!!active.length);
    $('jobs-ring-label').textContent=active.length?(progress===null?'…':progress+'%'):'✓';
    $('jobs-active-count').hidden=!active.length;$('jobs-active-count').textContent=String(active.length);
    $('jobs-toggle').title=active.length?`${active.length} 个任务${progress===null?'':` · ${progress}%`}`:'生成任务';
    $('jobs-toggle').setAttribute('aria-label',active.length?`${active.length} 个生成任务${progress===null?'':`，当前 ${progress}%`}`:'查看生成任务');
  }
  toggleDropdown(){const open=$('jobs-dropdown').hidden;$('jobs-dropdown').hidden=!open;$('jobs-toggle').setAttribute('aria-expanded',String(open));if(open){this.renderQueue();this.positionDropdown();this.refresh();}}
  closeDropdown(){this.media.releaseTree($('jobs-list'));$('jobs-list').replaceChildren();$('jobs-dropdown').hidden=true;$('jobs-toggle').setAttribute('aria-expanded','false');}
  positionDropdown(){const rect=$('jobs-toggle').getBoundingClientRect(),menu=$('jobs-dropdown');menu.style.right=Math.max(8,innerWidth-rect.right)+'px';menu.style.top=Math.min(innerHeight-160,rect.bottom+7)+'px';menu.style.maxHeight=Math.max(120,innerHeight-rect.bottom-20)+'px';}
  renderQueue(){
    const list=$('jobs-list'),preserved=new Map([...list.querySelectorAll('.queue-job')].map(row=>[row.dataset.id,row.querySelector('.queue-results')]));list.replaceChildren();const active=this.jobs.filter(job=>ACTIVE.has(job.status)).length;$('jobs-summary').textContent=active?`${active} 项进行中`:`${this.jobs.length} 项`;
    if(!this.jobs.length){list.append(el('p','queue-empty','暂无生成任务'));return;}
    for(const job of this.jobs.slice(0,30)){
      const id=job.id||job.prompt_id,row=el('article','queue-job'),head=el('div','queue-job-head'),name=el('strong','',job.name||job.settings?.name||(job.engine_name||job.settings?.engine_name||job.engine||'生成')+' · '+String(id||'').slice(-6)),status=el('span','queue-job-status',LABELS[job.status]||job.status),sub=el('div','queue-job-sub');
      row.dataset.id=id;row.dataset.status=job.status;name.title=name.textContent;head.append(name,status);const percent=percentage(job.progress);if(percent!==null)sub.append(el('span','queue-percent',percent+'%'));const elapsed=time(job.elapsed_seconds);if(elapsed)sub.append(el('span','',`已用 ${elapsed}`));const remaining=time(job.remaining_seconds);if(remaining)sub.append(el('span','',job.remaining_scope==='sampling'?`采样约 ${remaining}`:`预计剩余 ${remaining}`));
      if(job.error){const error=el('span','queue-job-error',job.error);error.title=job.error;sub.append(error);}
      row.append(head,sub);
      const previous=preserved.get(String(id));preserved.delete(String(id));
      if(job.status==='completed'&&job.images?.length){const signature=JSON.stringify(job.images.slice(0,3).map(image=>image.thumbnail_url||image.url)),thumbnails=previous?.dataset.signature===signature?previous:el('div','queue-results');if(thumbnails!==previous){if(previous)this.media.releaseTree(previous);thumbnails.dataset.signature=signature;for(const image of job.images.slice(0,3)){const preview=el('button','queue-result-preview'),img=el('img');preview.type='button';preview.setAttribute('aria-label','预览 '+(image.filename||'生成结果'));img.alt=image.filename||'生成结果';preview.append(openAIPlaceholder(),img);preview.onclick=()=>this.onPreview(image,job);thumbnails.append(preview);this.media.observe(img,image.thumbnail_url||image.url);} }row.append(thumbnails);}else if(previous)this.media.releaseTree(previous);
      if(ACTIVE.has(job.status)&&job.can_cancel!==false&&job.owned!==false){const cancel=el('button','queue-cancel');cancel.setAttribute('aria-label','取消任务 '+name.textContent);cancel.title='取消此任务';cancel.append(iconCross());cancel.onclick=async()=>{cancel.disabled=true;try{await request('/jobs/'+encodeURIComponent(id)+'/cancel',{method:'POST',body:{}});this.onMutation?.(id);this.refresh();}catch(error){this.onToast(error.message,true);}finally{cancel.disabled=false;}};row.append(cancel);}
      if(percent!==null&&ACTIVE.has(job.status)){const track=el('span','queue-track'),fill=el('i');fill.style.width=percent+'%';track.append(fill);row.append(track);}list.append(row);
    }
    for(const thumbnail of preserved.values())if(thumbnail)this.media.releaseTree(thumbnail);
  }
  renderResults(){
    const active=job=>ACTIVE.has(job.status),jobs=this.jobs.filter(job=>active(job)||job.images?.length).sort((a,b)=>Number(active(b))-Number(active(a))||created(b)-created(a)),entries=jobs.flatMap(job=>(job.images?.length?job.images:[null]).slice(0,3).map((image,index)=>({job,image,key:(job.id||job.prompt_id)+':'+index}))).slice(0,24),root=$('results-scroll');
    $('results-panel').hidden=false;$('results-count').textContent=String(entries.length);$('results-state').textContent='';this.applyResultCollapse();root.hidden=this.collapsed;
    if(!this.resultNodes)this.resultNodes=new Map();
    if(!this.active||this.collapsed){this.media.releaseTree(root);root.replaceChildren();this.resultNodes.clear();return;}
    if(!entries.length){if(!root.querySelector('.result-empty')){this.media.releaseTree(root);root.replaceChildren(el('span','result-empty','暂无结果'));}this.resultNodes.clear();return;}
    root.querySelector('.result-empty')?.remove();const next=new Map();
    for(const{image,job,key}of entries){const signature=JSON.stringify([job.status,job.progress,job.error,image?.url,image?.thumbnail_url,image?.available,image?.updated_at,image?.thumbnail_ready,image?.width,image?.height,job.settings?.width,job.settings?.height]),previous=this.resultNodes.get(key);let card=previous?.signature===signature?previous.card:null;if(!card){if(previous){this.media.releaseTree(previous.card);previous.card.remove();}card=this.createResultCard(job,image);card.dataset.jobId=String(job.id||job.prompt_id);root.append(card);const img=card.querySelector('img');if(img)this.media.observe(img,image.thumbnail_url||image.url,{retain:true});}const position=next.size;if(root.children[position]!==card)root.insertBefore(card,root.children[position]||null);next.set(key,{card,signature});}
    for(const[key,value]of this.resultNodes)if(!next.has(key)){this.media.releaseTree(value.card);value.card.remove();}this.resultNodes=next;
  }
  createResultCard(job,image){
    const card=el('article','result-card'),parts=String(job.settings?.ratio||'1:1').split(':').map(Number),fallbackRatio=parts[0]>0&&parts[1]>0?parts[0]/parts[1]:1,width=Number(image?.width||job.settings?.width)||Math.round(1024*fallbackRatio),height=Number(image?.height||job.settings?.height)||1024,ratio=width/height;card.style.setProperty('--result-ratio',String(ratio));card.style.width=(72*ratio)+'px';
    if(!image){const stage=el('div','result-pending'),ring=el('span','result-pending-ring'),label=el('span','result-pending-label',job.status==='running'?'生成中':'等候'),progress=percentage(job.progress);stage.style.aspectRatio=String(ratio);ring.innerHTML='<svg viewBox="0 0 36 36"><circle class="pending-track" cx="18" cy="18" r="14"/><circle class="pending-fill" cx="18" cy="18" r="14"/></svg>';if(progress===null)ring.classList.add('indeterminate');else{const fill=ring.querySelector('.pending-fill'),c=2*Math.PI*14;fill.style.strokeDasharray=String(c);fill.style.strokeDashoffset=String(c*(1-progress/100));ring.append(el('small','',progress+'%'));}stage.append(ring,label);card.append(stage,el('div','result-pending-name',job.name||job.engine_name||'生成任务'));return card;}
    const preview=el('button','result-preview'),img=el('img'),info=el('div','result-info'),size=el('span','result-size'),actions=el('div','result-card-actions');preview.type='button';preview.style.aspectRatio=String(ratio);preview.setAttribute('aria-label','预览 '+(image.filename||'生成结果'));preview.append(openAIPlaceholder(),img);img.alt=image.filename||'生成结果';if(width&&height)size.textContent=`${width} × ${height}`;info.append(size);preview.onclick=()=>this.onPreview(image,job);preview.draggable=true;preview.addEventListener('dragstart',event=>{event.dataTransfer.setData('application/x-bbaigc-result',JSON.stringify(image));event.dataTransfer.setData('text/uri-list',new URL(image.url,location.href).href);event.dataTransfer.setData('text/plain',new URL(image.url,location.href).href);this.onDrag?.('drag-start',{image});});preview.addEventListener('dragend',()=>this.onDrag?.('drag-end'));card.append(preview,info,actions);const menuActions=[];
    const action=(label,callback,inline=false)=>{if(!inline){menuActions.push({label,callback});return;}const button=el('button','button',label);button.title=label;button.onclick=async()=>{button.disabled=true;try{await callback();}catch(error){this.onToast(error.message,true);}finally{button.disabled=false;}};actions.append(button);};
    action('重新生成',()=>this.onRestoreJob(job),true);action('加入画布',()=>this.onCanvas(image));action('复制图片',()=>this.onCopy(image));action('打开所在文件夹',async()=>{await request('/results/open-folder',{method:'POST',body:{image}});});if(image.type==='temp'||job.settings?.save_output===false&&image.type!=='output')action('保存图片',async()=>{const data=await request('/results/save',{method:'POST',body:{image}});if(data.image)Object.assign(image,data.image);this.renderResults();this.onToast('已保存到输出目录');});
    const more=el('button','result-more icon-button');more.title='结果操作';more.setAttribute('aria-label','结果操作');more.innerHTML='<svg viewBox="0 0 24 24"><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></svg>';more.onclick=event=>this.openResultMenu(menuActions,event);actions.append(more);preview.addEventListener('contextmenu',event=>{event.preventDefault();this.openResultMenu(menuActions,event);});return card;
  }
  applyResultCollapse(){const panel=$('results-panel');panel.closest('.composer-top').classList.toggle('results-collapsed',this.collapsed);panel.classList.toggle('is-collapsed',this.collapsed);$('results-toggle').setAttribute('aria-expanded',String(!this.collapsed));$('results-toggle').title=this.collapsed?'向左展开生成结果':'向右收起生成结果';$('results-toggle').setAttribute('aria-label',this.collapsed?'向左展开生成结果':'向右收起生成结果');$('results-chevron').innerHTML=this.collapsed?'<svg viewBox="0 0 24 24"><path d="m15 5-7 7 7 7"/></svg>':'<svg viewBox="0 0 24 24"><path d="m9 5 7 7-7 7"/></svg>';}
  toggleResults(){this.collapsed=!this.collapsed;try{sessionStorage.setItem('bbaigc-results-collapsed',String(this.collapsed));}catch{}this.resultSignature='';this.renderResults();this.media.reprioritize?.();}
  openResultMenu(actions,event){const menu=$('result-actions-menu');menu.replaceChildren();for(const{label,callback}of actions){const button=el('button','',label);button.setAttribute('role','menuitem');button.onclick=async()=>{menu.hidden=true;try{await callback();}catch(error){this.onToast(error.message,true);}};menu.append(button);}menu.hidden=false;const rect=event.currentTarget?.getBoundingClientRect?.(),x=event.clientX||rect?.left||0,y=event.clientY||rect?.bottom||0;menu.style.left=Math.max(6,Math.min(innerWidth-menu.offsetWidth-6,x))+'px';menu.style.top=Math.max(6,Math.min(innerHeight-menu.offsetHeight-6,y))+'px';}
  suspend(){this.active=false;clearTimeout(this.timer);this.timer=0;this.controller?.abort();this.controller=null;this.loading=false;this.closeDropdown();$('result-actions-menu').hidden=true;this.media.releaseTree($('results-scroll'));$('results-scroll').replaceChildren();this.resultSignature='';this.resultNodes?.clear();}
  resume(){this.active=true;this.resultSignature='';this.render();this.refresh();}
  stats(){return{jobs:this.jobs.length,active:this.jobs.filter(job=>ACTIVE.has(job.status)).length,results:this.jobs.reduce((sum,job)=>sum+(job.images?.length||0),0),polling:!!this.timer};}
}

function iconCross(){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.innerHTML='<path d="m7 7 10 10M17 7 7 17"/>';return svg;}
