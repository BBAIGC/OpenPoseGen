import {request,abortRequests,isAbort} from './api.js';
import {MediaPool} from './media.js';
import {VirtualGallery,KIND_NAMES} from './gallery.js';
import {ReferenceEditor} from './editor.js';
import {JobPanel} from './jobs.js';
import {GenerationSettings} from './settings.js';
import {StreamingSpeech} from './speech_stream.js';
import {icon} from './icons.js';
import {ProviderSettings} from './provider.js';
import {ModelConfig} from './model_config.js';
import {ExternalWorkspace} from './external.js';
import {AboutDialog} from './about.js';
import {LanguageOptimizer} from './language.js';

const $=id=>document.getElementById(id);
const make=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;};
const STORAGE='bbaigc-asset-library-v1';
const KINDS=['identity','outfit','pose'];
const PARENT_ORIGINS=new Set(['http://127.0.0.1:8000','http://localhost:8000','http://[::1]:8000']);
const candidate=new URLSearchParams(location.search).get('parent_origin');
const parentOrigin=PARENT_ORIGINS.has(candidate)?candidate:null;
const embedded=parentOrigin&&window.parent!==window;
let saved={};try{saved=JSON.parse(sessionStorage.getItem(STORAGE)||'{}');}catch{}
const state={active:true,mode:['combine','external'].includes(saved.mode)?saved.mode:'grid',kind:KINDS.includes(saved.kind)?saved.kind:'identity',category:saved.category||'',query:saved.query||'',size:Math.max(120,Math.min(240,Number(saved.size)||170)),quick:!!saved.quick,selected:new Map(((saved.mode==='external'?saved.externalSelected:saved.catalogSelected)||saved.selected||[]).slice(0,16).map(item=>[item.id,item])),rowCategories:saved.rowCategories||{},summary:null,engines:[],presets:[],engine:typeof saved.engine==='string'&&/^[a-z0-9_:.\/-]{1,180}$/i.test(saved.engine)?saved.engine:'qwen',preset:saved.preset||'',jobId:saved.jobId||null,job:null,viewer:null,viewerList:[],viewerIndex:0,busy:false,navCollapsed:!!saved.navCollapsed,expandedKind:saved.expandedKind===null?null:(saved.expandedKind||saved.kind||'identity'),expandedCategories:new Set(saved.expandedCategories||[])};
const media=new MediaPool();
const catalogDraft={selected:new Map((saved.catalogSelected||saved.selected||[]).map(item=>[item.id,item])),prompt:saved.catalogPrompt||saved.prompt||[],mode:saved.catalogMode==='grid'?'grid':'combine'};
const externalDraft={selected:new Map((saved.externalSelected||[]).map(item=>[item.id,item])),prompt:saved.externalPrompt||[]};
let summaryTimer=0,jobTimer=0,toastTimer=0,searchTimer=0,saveTimer=0,speechTimer=0,viewerController=null,viewerBlob=null,viewerGeneration=0;
const retiredViewerURLs=new Set();

const copyControllers=new Set();
const galleryOptions={media,getSize:()=>state.size,selected:()=>state.selected,onSelect:selectAsset,onPreview:openViewer,onContext:openMenu,onStatus:galleryStatus};
const grid=new VirtualGallery($('grid-scroller'),galleryOptions);
const rows=new Map();
for(const kind of KINDS){
  const section=make('section','asset-row'),heading=make('div','row-heading'),select=make('select'),count=make('span','row-count'),navigation=make('div','row-navigation'),previous=make('button','icon-button','‹'),next=make('button','icon-button','›'),scroller=make('div','row-scroller'),canvas=make('div','virtual-canvas');
  select.setAttribute('aria-label',KIND_NAMES[kind]+'分类');previous.setAttribute('aria-label','向左浏览'+KIND_NAMES[kind]);next.setAttribute('aria-label','向右浏览'+KIND_NAMES[kind]);
  heading.append(make('strong','',KIND_NAMES[kind]),count,select,navigation);navigation.append(previous,next);scroller.append(canvas);section.append(heading,scroller);$('combine-view').append(section);
  const track=make('div','row-scroll-track'),thumb=make('button','row-scroll-thumb');thumb.type='button';thumb.setAttribute('aria-label','滚动'+KIND_NAMES[kind]);track.append(thumb);section.append(track);
  const gallery=new VirtualGallery(scroller,{...galleryOptions,horizontal:true,kind,onScroll:()=>updateRowTrack(kind)});
  rows.set(kind,{section,select,count,gallery,track,thumb});
  select.onchange=()=>{state.rowCategories[kind]=select.value;gallery.reset({category:select.value,query:state.query,total:countFor(kind,select.value)});persist();};
  previous.onclick=()=>scroller.scrollBy({left:-scroller.clientWidth*.75,behavior:reducedMotion()?'instant':'smooth'});
  next.onclick=()=>scroller.scrollBy({left:scroller.clientWidth*.75,behavior:reducedMotion()?'instant':'smooth'});
  scroller.addEventListener('wheel',event=>{if(Math.abs(event.deltaY)>Math.abs(event.deltaX)&&!event.ctrlKey){event.preventDefault();scroller.scrollLeft+=event.deltaY;}},{passive:false});
  let drag=null;track.addEventListener('pointerdown',event=>{if(event.button!==0)return;const rect=track.getBoundingClientRect(),scrollMax=scroller.scrollWidth-scroller.clientWidth,thumbWidth=thumb.getBoundingClientRect().width;if(event.target===thumb){drag={id:event.pointerId,x:event.clientX,scroll:scroller.scrollLeft,scale:scrollMax/Math.max(1,rect.width-thumbWidth)};track.setPointerCapture(event.pointerId);}else{const target=(event.clientX-rect.left-thumbWidth/2)/Math.max(1,rect.width-thumbWidth)*scrollMax;scroller.scrollTo({left:Math.max(0,Math.min(scrollMax,target)),behavior:reducedMotion()?'instant':'smooth'});}event.preventDefault();});track.addEventListener('pointermove',event=>{if(drag)scroller.scrollLeft=drag.scroll+(event.clientX-drag.x)*drag.scale;});for(const event of['pointerup','pointercancel'])track.addEventListener(event,()=>{drag=null;});
  const trackResize=new ResizeObserver(()=>updateRowTrack(kind));trackResize.observe(scroller);trackResize.observe(track);
}
const editor=new ReferenceEditor($('prompt-editor'),$('mention-menu'),{getReferences:()=>[...state.selected.values()],media,onChange:()=>persist(),onReferenceClick:asset=>{if(state.mode==='grid')setMode('combine');if(state.mode==='external')external.locate(asset);else centerSelectedRows(asset.id);}});
editor.restore(state.mode==='external'?externalDraft.prompt:(saved.catalogPrompt||saved.prompt||[]));
const settings=new GenerationSettings({onDetail:()=>{const preset=state.presets.find(item=>item.id===state.preset);if(preset?.variants?.length)applyPreset(preset);},onToast:toast});
const jobs=new JobPanel({media,onPreview:(image,job)=>openViewer({id:'result:'+image.filename,title:image.filename||'生成结果',thumbnail_url:image.thumbnail_url||image.url,image_url:image.preview_url||image.url,original_url:image.url,available:true,kind:'result',result:image,width:image.width,height:image.height,prompt:job.settings?.prompt||''},[],{highResolution:false}),onCopy:image=>copyImage({image_url:image.url,original_url:image.url,available:true}),onRestoreJob:restoreJob,onCanvas:image=>{if(!host('add-to-canvas',{image}))toast('请从 ComfyUI 打开资产库后加入画布');},onDrag:host,onToast:toast,onChange:()=>{},onMutation:id=>host('jobs-changed',{prompt_id:id})});
const providerSettings=new ProviderSettings({onSaved:()=>loadEngines(),onToast:toast});
const modelConfig=new ModelConfig({settings,getEngine:()=>state.engine,getEngineInfo:()=>state.engines.find(item=>item.id===state.engine),onToast:toast});
const aboutDialog=new AboutDialog({getEngines:()=>state.engines});
const languageOptimizer=new LanguageOptimizer({settings,editor,getAssets:()=>[...state.selected.values()],getEngine:()=>state.engine,host,onToast:toast});modelConfig.onLanguageTab=()=>{languageOptimizer.render();languageOptimizer.load();};
const externalContainer=make('div','combine-view external-view');externalContainer.id='external-view';externalContainer.hidden=true;$('browser-area').append(externalContainer);
const external=new ExternalWorkspace({container:externalContainer,media,getSize:()=>state.size,getSelected:()=>state.selected,onSelect:selectAsset,onPreview:openViewer,onContext:openMenu,onToast:toast,onItems:(items,added=[])=>{const known=new Map(items.map(item=>[item.id,item]));for(const[id,item]of externalDraft.selected)if(known.has(id))externalDraft.selected.set(id,compactAsset(known.get(id)));else externalDraft.selected.delete(id);if(state.mode==='external'){for(const[id,item]of state.selected)if(known.has(id))state.selected.set(id,compactAsset(known.get(id)));else state.selected.delete(id);const limit=Math.min(16,state.engines.find(item=>item.id===state.engine)?.max_references||16);for(const item of added)if(state.selected.size<limit)state.selected.set(item.id,compactAsset(item));renderSelection();renderHeader();}}});

const speech=new StreamingSpeech({button:$('speech-button'),live:$('speech-live'),cancel:$('speech-cancel'),stop:$('speech-stop'),wave:$('speech-wave'),status:$('speech-state'),createAnchor:()=>editor.createSpeechAnchor(),onText:(text,anchor)=>editor.insertTranscript(text,anchor),onError:message=>toast(message,true)});
function taskName(){const preset=state.presets.find(item=>item.id===state.preset),engine=state.engines.find(item=>item.id===state.engine);return `${preset?.name||'自由描述'} · ${engine?.name||state.engine}`;}
function applyPreset(preset){const variants=preset.variants;const variant=Array.isArray(variants)?variants[Math.min(variants.length-1,settings.values.detail_level-1)]:null;editor.setPreset(typeof variant==='string'?variant:variant?.prompt||preset.prompt||'');}


function reducedMotion(){return matchMedia('(prefers-reduced-motion:reduce)').matches;}
function host(type,payload={}){if(!embedded)return false;window.parent.postMessage({source:'bbaigc-asset-library',type,...(state.mode==='external'?{external_session:external.session}:{}),...payload},parentOrigin);return true;}
function persist(immediate=false){
  clearTimeout(saveTimer);const write=()=>{try{sessionStorage.setItem(STORAGE,JSON.stringify({mode:state.mode,catalogSelected:[...(state.mode==='external'?catalogDraft.selected:state.selected).values()].map(compactAsset),externalSelected:[...(state.mode==='external'?state.selected:externalDraft.selected).values()].map(compactAsset),catalogPrompt:state.mode==='external'?catalogDraft.prompt:editor.snapshot(),externalPrompt:state.mode==='external'?editor.snapshot():externalDraft.prompt,catalogMode:state.mode==='external'?catalogDraft.mode:state.mode,kind:state.kind,category:state.category,query:state.query,size:state.size,quick:state.quick,selected:[...state.selected.values()].map(compactAsset),rowCategories:state.rowCategories,engine:state.engine,preset:state.preset,prompt:editor.snapshot(),jobId:state.jobId,navCollapsed:state.navCollapsed,expandedKind:state.expandedKind,expandedCategories:[...state.expandedCategories],scroll:{grid:$('grid-scroller').scrollTop,...Object.fromEntries([...rows].map(([kind,row])=>[kind,row.gallery.root.scrollLeft]))}}));}catch{}};
  if(immediate)write();else saveTimer=setTimeout(write,180);
}
function compactAsset(asset){return Object.fromEntries(['id','title','kind','category','adult','tags','prompt','negative_prompt','thumbnail_url','image_url','original_url','available','width','height'].filter(key=>asset[key]!==undefined).map(key=>[key,asset[key]]));}
function toast(message,error=false){if(!state.active)return;clearTimeout(toastTimer);$('toast').textContent=message;$('toast').classList.toggle('error',error);$('toast').hidden=false;toastTimer=setTimeout(()=>$('toast').hidden=true,error?5000:2600);}
function showError(error){if(isAbort(error)||!state.active)return;$('error-banner').querySelector('span').textContent=error.message||String(error);$('error-banner').hidden=false;}
function countFor(kind,category=''){if(!state.summary)return 0;if(!category)return(kind==='pose'?state.summary.counts?.pose_regular??state.summary.counts?.pose:state.summary.counts?.[kind])||0;return state.summary.categories?.find(item=>item.kind===kind&&item.key===category)?.count||0;}
function categories(kind){return (state.summary?.categories||[]).filter(item=>item.kind===kind);}
function galleryStatus(total,gallery,error){if(error){showError(error);return;}if(gallery===grid)$('asset-count').textContent=Number(total).toLocaleString()+' 项';else for(const[kind,row]of rows)if(row.gallery===gallery){row.count.textContent=Number(total).toLocaleString();requestAnimationFrame(()=>updateRowTrack(kind));}}
function updateRowTrack(kind){const row=rows.get(kind);if(!row)return;const scroller=row.gallery.root,width=row.track.clientWidth,total=Math.max(scroller.clientWidth,scroller.scrollWidth),thumbWidth=Math.max(36,width*scroller.clientWidth/Math.max(1,total)),max=total-scroller.clientWidth;row.thumb.style.width=Math.min(width,thumbWidth)+'px';row.thumb.style.transform=`translateX(${max>0?scroller.scrollLeft/max*Math.max(0,width-thumbWidth):0}px)`;row.track.classList.toggle('not-scrollable',max<=1);}

async function loadSummary({initial=false}={}){
  try{
    const summary=await request('/summary');if(!state.active)return;
    const first=!state.summary;state.summary=summary;
    if(Array.isArray(summary.presets)&&!state.presets.length){state.presets=summary.presets;renderPresets();}
    renderHeader();renderNavigation();renderSource();renderRowSelectors();
    if(first||initial)resetGalleries(true);
    $('error-banner').hidden=true;
  }catch(error){showError(error);}
}
function renderSource(){
  const source=state.summary?.source||{},total=source.total||state.summary?.counts?.total||0,downloaded=source.downloaded||0,thumbs=source.thumbnail_downloaded;
  $('source-state').textContent=source.name||'本地素材库';
  $('source-state').title=total?`${total.toLocaleString()} 项 · ${downloaded.toLocaleString()} 张原图已就绪`:'';
  $('download-state').textContent=total&&downloaded<total?`原图 ${downloaded.toLocaleString()} / ${total.toLocaleString()}`:(total?`${total.toLocaleString()} 项素材`:'');
  if(thumbs!==undefined&&total&&thumbs<total)$('download-state').title=`缩略图 ${thumbs.toLocaleString()} / ${total.toLocaleString()}`;
}
function renderNavigation(){
  const nav=$('category-nav');nav.replaceChildren();
  for(const kind of KINDS){
    const group=make('section','nav-group'),button=make('button','nav-kind'),expanded=state.expandedKind===kind;
    button.append(icon(kind,'kind-icon'),make('span','',KIND_NAMES[kind]),make('span','nav-count',Number(state.summary?.counts?.[kind]||0).toLocaleString()),icon('chevron','nav-chevron'));
    button.classList.toggle('active',kind===state.kind&&state.mode==='grid');button.setAttribute('aria-expanded',String(expanded));button.onclick=()=>{if(state.expandedKind===kind){state.expandedKind=null;renderNavigation();persist();}else{state.expandedKind=kind;navigate(kind,'');}};group.append(button);
    if(expanded){
      const sub=make('div','nav-subcategories'),all=make('button','nav-category');all.append(make('span','category-label',kind==='pose'?'常规姿态':'全部'+KIND_NAMES[kind]),make('span','nav-count',countFor(kind).toLocaleString()));all.classList.toggle('active',!state.category);all.onclick=()=>navigate(kind,'');sub.append(all);
      const items=categories(kind),byParent=new Map(),known=new Map(items.map(item=>[item.key,item]));for(const item of items){const parent=item.parent||'';if(!byParent.has(parent))byParent.set(parent,[]);byParent.get(parent).push(item);}
      const selected=known.get(state.category);let parent=selected?.parent,depth=0;while(parent&&depth++<8){state.expandedCategories.add(parent);parent=known.get(parent)?.parent;}
      const draw=(list,level=0)=>{for(const item of list){const line=make('div','nav-category-line'),child=make('button','nav-category'),children=byParent.get(item.key)||[],open=state.expandedCategories.has(item.key);line.style.setProperty('--category-level',String(Math.min(4,level)));child.append(make('span','category-label',item.title||item.key),make('span','nav-count',Number(item.count||0).toLocaleString()));child.title=item.title||item.key;child.classList.toggle('active',state.category===item.key);child.classList.toggle('adult-category',!!item.adult);child.onclick=()=>{if(children.length){if(open)state.expandedCategories.delete(item.key);else state.expandedCategories.add(item.key);}navigate(kind,item.key);};line.append(child);if(children.length){const toggle=make('button','category-expand');toggle.append(icon('chevron'));toggle.setAttribute('aria-expanded',String(open));toggle.setAttribute('aria-label',(open?'收起':'展开')+(item.title||item.key));toggle.onclick=()=>{if(open)state.expandedCategories.delete(item.key);else state.expandedCategories.add(item.key);renderNavigation();persist();};line.append(toggle);}sub.append(line);if(children.length&&open)draw(children,level+1);}};
      draw(items.filter(item=>!item.parent||!known.has(item.parent)));group.append(sub);
    }
    nav.append(group);
  }
}
function renderRowSelectors(){for(const[kind,row]of rows){const previous=state.rowCategories[kind]||'';row.select.replaceChildren(new Option(kind==='pose'?'常规姿态':'全部分类',''));for(const category of categories(kind))row.select.append(new Option(category.title||category.key,category.key));row.select.value=previous;row.count.textContent=countFor(kind,previous).toLocaleString();}}
function navigate(kind,category){state.kind=kind;state.category=category;if(state.mode!=='grid')setMode('grid');else{grid.reset({kind,category,query:state.query,total:countFor(kind,category)});renderHeader();renderNavigation();}persist();}
function resetGalleries(restore=false){grid.reset({kind:state.kind,category:state.category,query:state.query,total:countFor(state.kind,state.category),restore});for(const[kind,row]of rows)row.gallery.reset({category:state.rowCategories[kind]||'',query:state.query,total:countFor(kind,state.rowCategories[kind]||''),restore});if(restore&&saved.scroll){$('grid-scroller').scrollTop=saved.scroll.grid||0;for(const[kind,row]of rows)row.gallery.root.scrollLeft=saved.scroll[kind]||0;}setActiveGalleries();}
function setActiveGalleries(){if(state.mode==='external'){grid.suspend();for(const row of rows.values())row.gallery.suspend();if(state.active)external.show();return;}external.hide();if(state.mode==='grid'){for(const row of rows.values())row.gallery.suspend();if(state.active)grid.resume();}else{grid.suspend();if(state.active)for(const row of rows.values())row.gallery.resume();}}
function renderHeader(){
  const combine=state.mode==='combine',isExternal=state.mode==='external';$('page-title').textContent=isExternal?'外接参考':combine?'参考搭配':state.category?(categories(state.kind).find(c=>c.key===state.category)?.title||state.category):KIND_NAMES[state.kind];
  $('asset-count').textContent=isExternal?`${external.items.length} 项`:combine?`${(state.summary?.counts?.total||0).toLocaleString()} 项`:`${countFor(state.kind,state.category).toLocaleString()} 项`;
  $('view-switch').dataset.view=state.mode;$('view-grid').setAttribute('aria-pressed',String(state.mode==='grid'));$('view-combine').setAttribute('aria-pressed',String(combine));$('view-external').setAttribute('aria-pressed',String(isExternal));
  $('grid-scroller').hidden=state.mode!=='grid';$('combine-view').hidden=!combine;externalContainer.hidden=!isExternal;$('composer').hidden=state.mode==='grid';$('search').disabled=isExternal;
  $('search').value=state.query;$('thumbnail-size').value=state.size;$('engine').value=state.engine;
  document.body.classList.toggle('nav-collapsed',state.navCollapsed);$('expand-nav').hidden=!state.navCollapsed;
}
function setMode(mode){if(state.mode===mode)return;const previous=state.mode;if(previous==='external'){externalDraft.selected=new Map(state.selected);externalDraft.prompt=editor.snapshot();}else{catalogDraft.selected=new Map(state.selected);catalogDraft.prompt=editor.snapshot();catalogDraft.mode=previous;}if(mode==='external'){state.selected=new Map(externalDraft.selected);editor.restore(externalDraft.prompt);}else if(previous==='external'){state.selected=new Map(catalogDraft.selected);editor.restore(catalogDraft.prompt);}state.mode=mode;closeViewer();renderHeader();renderNavigation();renderSelection();setActiveGalleries();if(mode==='combine'){centerSelectedRows();}if(mode!=='grid'&&!editor.read()&&state.presets[0]&&state.preset!=='custom'){state.preset=state.presets[0].id;applyPreset(state.presets[0]);renderPresets();}persist();}

let centerEpoch=0;
async function centerSelectedRows(specificId=null){
  const epoch=++centerEpoch;
  for(const kind of KINDS){
    const asset=[...state.selected.values()].find(item=>item.kind===kind&&(!specificId||item.id===specificId)),row=rows.get(kind);if(!asset)continue;
    const fallbackCategory=kind==='pose'&&asset.adult?'pose/adult':'';let category=state.rowCategories[kind]||fallbackCategory,query=state.query;
    try{
      const position=async()=>{try{return await request('/assets/'+encodeURIComponent(asset.id)+'/position?'+new URLSearchParams({kind,category,q:query}));}catch(error){if(error.status===404)return{offset:null};throw error;}};
      let data=await position();if(data.offset===null||data.offset===undefined||data.offset<0){category=fallbackCategory;data=await position();}
      if((data.offset===null||data.offset===undefined||data.offset<0)&&query){query='';data=await position();}
      if(epoch!==centerEpoch||!state.active||state.mode!=='combine')return;if(!state.selected.has(asset.id)||!Number.isInteger(data.offset)||data.offset<0)continue;
      state.rowCategories[kind]=category;row.select.value=category;row.gallery.reset({kind,category,query,total:data.total||countFor(kind,category)});
      requestAnimationFrame(()=>{if(epoch===centerEpoch&&state.mode==='combine')row.gallery.scrollToIndex(data.offset,{behavior:reducedMotion()?'instant':'smooth'});});
    }catch(error){if(!isAbort(error)&&error.status!==404)toast('暂时无法定位已选素材');}
  }
}
function selectAsset(asset,event={}){
  editor.bindNumericReferences();
  const multi=!!(event.ctrlKey||event.metaKey||event.shiftKey),already=state.selected.has(asset.id);
  if(already){state.selected.delete(asset.id);}else{
    if(!multi){for(const[id,other]of state.selected)if(other.kind===asset.kind)state.selected.delete(id);}
    if(state.selected.size>=16){toast('最多同时选择 16 张参考图');return;}
    state.selected.set(asset.id,compactAsset(asset));
  }
  renderSelection();
}
function addReference(asset){if(!state.selected.has(asset.id)){if(state.selected.size>=16){toast('最多同时选择 16 张参考图');return false;}editor.bindNumericReferences();state.selected.set(asset.id,compactAsset(asset));renderSelection();}return true;}
function renderSelection(){
  grid.updateSelection();for(const row of rows.values())row.gallery.updateSelection();external.updateSelection();
  media.releaseTree($('references'));$('references').replaceChildren();
  const list=[...state.selected.values()];
  if(!list.length)$('references').append(make('span','references-empty','选择上方素材作为参考'));
  list.forEach((asset,index)=>{
    const card=make('div','reference'),image=make('img'),text=make('span','reference-text'),name=make('span','',asset.title),label=make('span','reference-label',`图${index+1} · ${KIND_NAMES[asset.kind]||'参考图'}`),remove=make('button','icon-button remove-reference','×');
    text.append(label,name);card.append(image,text,remove);card.title='点击插入 @图'+(index+1);text.onclick=()=>{editor.insert(asset);};image.onclick=()=>editor.insert(asset);remove.title='移除参考';remove.setAttribute('aria-label','移除'+asset.title);remove.onclick=()=>{editor.bindNumericReferences();state.selected.delete(asset.id);renderSelection();};$('references').append(card);media.observe(image,asset.thumbnail_url||asset.image_url);
  });
  $('selection-state').textContent=list.length?`已选 ${list.length} 项`:'未选择素材';$('clear-selection').disabled=!list.length;
  editor.reconcile();persist();
}

function openMenu(asset,event){
  const menu=$('context-menu');menu.replaceChildren();
  const actions=[['预览','空格',()=>openViewer(asset,currentAssets())],['选为参考','',()=>{addReference(asset);toast('已加入参考');}],['复制图片','',()=>copyImage(asset)],['复制参考描述','',()=>copyText(asset.prompt||'','已复制参考描述')],null,['加入画布','',()=>addToCanvas(asset)],...(state.mode==='external'?[['移除外接图片','',()=>external.remove(asset)]]:[])];
  for(const action of actions){if(!action){menu.append(make('div','menu-divider'));continue;}const button=make('button');button.setAttribute('role','menuitem');button.append(make('span','',action[0]),make('small','',action[1]));button.onclick=()=>{menu.hidden=true;Promise.resolve(action[2]()).catch(error=>toast(error.message,true));};menu.append(button);}
  menu.hidden=false;const x=event.clientX??event.target.getBoundingClientRect().right,y=event.clientY??event.target.getBoundingClientRect().bottom;menu.style.left=Math.min(Math.max(5,x),innerWidth-menu.offsetWidth-6)+'px';menu.style.top=Math.min(Math.max(5,y),innerHeight-menu.offsetHeight-6)+'px';
}
function currentAssets(){return state.mode==='external'?external.visibleAssets():state.mode==='grid'?grid.visibleAssets():[...rows.values()].flatMap(row=>row.gallery.visibleAssets());}
async function copyText(text,success){if(!text){toast('这项素材没有参考描述');return;}await navigator.clipboard.writeText(text);toast(success||'已复制');}
async function copyImage(asset){
  if(asset.available===false){const detail=await request('/assets/'+encodeURIComponent(asset.id)+(asset.id.startsWith('external-')?'?session='+encodeURIComponent(external.session):''));Object.assign(asset,detail.item||detail.asset||detail);if(asset.available===false){toast('原图仍在下载，可先查看预览');return;}}
  if(!navigator.clipboard?.write||!window.ClipboardItem)throw new Error('当前浏览器不支持复制图片，请使用“加入画布”');
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),30000);copyControllers.add(controller);
  try{
    const response=await fetch(asset.original_url||asset.image_url,{signal:controller.signal});if(!response.ok)throw new Error('原图暂不可用');
    const blob=await response.blob();if(!state.active||controller.signal.aborted)return;let png=blob;
    if(blob.type!=='image/png'){
      const bitmap=await createImageBitmap(blob);if(bitmap.width*bitmap.height>40000000){bitmap.close();throw new Error('图片过大，请使用“加入画布”');}
      const canvas=document.createElement('canvas');canvas.width=bitmap.width;canvas.height=bitmap.height;canvas.getContext('2d').drawImage(bitmap,0,0);bitmap.close();png=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));canvas.width=canvas.height=0;
    }
    if(!png)throw new Error('图片转换失败');await navigator.clipboard.write([new ClipboardItem({'image/png':png})]);toast('图片已复制，可粘贴到画布');
  }catch(error){if(isAbort(error))throw new Error('复制已取消，请重试');throw error;}finally{clearTimeout(timeout);copyControllers.delete(controller);}
}
async function addToCanvas(asset){if(!host('add-to-canvas',{asset_id:asset.id})){toast('请从 ComfyUI 左栏打开资产库后加入画布');return;}toast('正在加入画布');}
async function reuseResult(image,job){try{const data=await request('/result-assets',{method:'POST',body:{image,prompt:job?.settings?.prompt||editor.read(),kind:'identity'}});const asset=data.item||data.asset;if(!asset?.id)throw new Error('结果没有保存成功');if(addReference(asset)){editor.insert(asset);toast('生成结果已加入参考');}loadSummary();}catch(error){if(!isAbort(error))toast(error.message,true);}}

async function restoreJob(job){
  const source=job.settings||{},ids=Array.isArray(source.asset_ids)?source.asset_ids.slice(0,16):[],found=new Map();let next=0;
  try{
    if(source.external_session===external.session)await external.refresh();
    await Promise.all(Array.from({length:Math.min(4,ids.length)},async()=>{while(next<ids.length){const id=ids[next++];try{const item=id.startsWith('external-')?external.items.find(item=>item.id===id):await request('/assets/'+encodeURIComponent(id));if(item?.id)found.set(id,item);}catch(error){if(isAbort(error))throw error;}}}));
    if(!state.active)return;const targetMode=source.external_session===external.session?'external':'combine';if(state.mode!==targetMode)setMode(targetMode);const restored=ids.filter(id=>found.has(id));state.selected=new Map(restored.map(id=>[id,compactAsset(found.get(id))]));state.engine=source.engine||job.engine||'qwen';if(![...$('engine').options].some(option=>option.value===state.engine))$('engine').append(new Option(source.engine_name||job.engine_name||state.engine,state.engine));$('engine').value=state.engine;state.preset=source.prompt_preset||'';const model_overrides=source.model_overrides||((source.steps!==undefined||source.cfg!==undefined)?{...(settings.values.model_overrides||{}),[state.engine]:{steps:source.steps,cfg:source.cfg}}:undefined);settings.restore({...source,...(model_overrides?{model_overrides}:{} )});modelConfig.render();
    let prompt=source.original_prompt||source.prompt||'';prompt=String(prompt).replace(/@图(\d+)/g,(token,number)=>{const oldId=ids[Number(number)-1],index=restored.indexOf(oldId);return index>=0?'@图'+(index+1):'@缺失参考'+number;});
    renderSelection();editor.setPreset(prompt);renderPresets();renderEngine();renderHeader();if(state.mode==='combine')centerSelectedRows();else for(const item of state.selected.values())external.locate(item);persist(true);toast(restored.length<ids.length?'参数已恢复，部分参考图需重新选择':'参数与参考图已恢复，确认后点击生成');
  }catch(error){if(!isAbort(error))toast(error.message,true);}
}

function viewerBaseScale(){const asset=state.viewer?.asset,img=$('viewer-image'),width=Number(asset?.width)||img.naturalWidth,height=Number(asset?.height)||img.naturalHeight;return width>0&&height>0?Math.min(img.clientWidth/width,img.clientHeight/height):1;}
function viewerTransform(){const viewer=state.viewer;if(viewer){$('viewer-image').style.transform=`translate(${viewer.x}px,${viewer.y}px) scale(${viewer.scale})`;$('viewer-quality').textContent=viewer.highResolution?'高清预览':'缩略预览';$('viewer-zoom').textContent=Math.round(viewerBaseScale()*viewer.scale*100)+'%';}}
function fitViewer(){if(!state.viewer)return;Object.assign(state.viewer,{x:0,y:0,scale:1});viewerTransform();}
function closeViewer(){viewerGeneration++;viewerController?.abort();viewerController=null;if(viewerBlob)URL.revokeObjectURL(viewerBlob);for(const url of retiredViewerURLs)URL.revokeObjectURL(url);retiredViewerURLs.clear();viewerBlob=null;$('viewer-image').onload=null;$('viewer-image').removeAttribute('src');$('viewer-image').style.transform='';$('viewer').hidden=true;state.viewer=null;}
async function openViewer(asset,list,{highResolution=false}={}){
  closeViewer();const generation=++viewerGeneration;state.viewer={asset,x:0,y:0,scale:1,highResolution};state.viewerList=list?.length?list:[asset];state.viewerIndex=Math.max(0,state.viewerList.findIndex(item=>item.id===asset.id));
  $('viewer').hidden=false;$('viewer-title').textContent=asset.title;$('viewer-resolution').textContent=asset.width&&asset.height?`${asset.width} × ${asset.height}`:'';$('viewer-prompt').textContent=asset.prompt||'没有可用的参考描述';$('viewer-tags').replaceChildren();for(const tag of[...new Set([KIND_NAMES[asset.kind],asset.category,...(asset.tags||[])].filter(Boolean))].slice(0,18))$('viewer-tags').append(make('span','',tag));
  $('viewer-metadata').replaceChildren();const meta=make('dl','viewer-metadata');const values=[['类型',KIND_NAMES[asset.kind]||'生成结果'],['分类',asset.category],['原图',asset.available===false?'下载中':'已就绪']];for(const[key,value]of values)if(value){meta.append(make('dt','',key),make('dd','',value));}$('viewer-metadata').append(meta);
  $('viewer-loading').hidden=false;$('viewer-loading').textContent=asset.available===false?'正在载入缩略图预览':'正在载入预览';$('viewer-image').alt=asset.title;$('viewer-stage').focus({preventScroll:true});
  viewerController=new AbortController();
  try{
    const response=await fetch(highResolution?(asset.image_url||asset.thumbnail_url):(asset.thumbnail_url||asset.image_url),{signal:viewerController.signal,cache:'force-cache'});if(!response.ok)throw new Error('预览暂未就绪');const blob=await response.blob();if(generation!==viewerGeneration||!state.active)return;
    viewerBlob=URL.createObjectURL(blob);$('viewer-image').src=viewerBlob;$('viewer-image').onload=()=>{if(generation===viewerGeneration){$('viewer-loading').hidden=true;viewerTransform();if(!asset.width&&asset.kind!=='result')$('viewer-resolution').textContent=`${$('viewer-image').naturalWidth} × ${$('viewer-image').naturalHeight}`;}};
    if(!asset.metadata){request('/assets/'+encodeURIComponent(asset.id)+(asset.id.startsWith('external-')?'?session='+encodeURIComponent(external.session):'')).then(detail=>{if(generation!==viewerGeneration)return;Object.assign(asset,detail.item||detail.asset||detail);state.viewer.asset=asset;$('viewer-prompt').textContent=asset.prompt||'没有可用的参考描述';}).catch(()=>{});}
  }catch(error){if(!isAbort(error)&&generation===viewerGeneration){$('viewer-loading').textContent=error.message;}}
}
function nextViewer(direction){if(!state.viewerList.length)return;const index=(state.viewerIndex+direction+state.viewerList.length)%state.viewerList.length;openViewer(state.viewerList[index],state.viewerList);}
async function loadViewerHD(){const current=state.viewer;if(!current)return;if(current.highResolution){fitViewer();return;}const generation=++viewerGeneration;viewerController?.abort();viewerController=new AbortController();$('viewer-loading').hidden=false;$('viewer-loading').textContent='正在载入高清预览';try{const response=await fetch(current.asset.image_url||current.asset.thumbnail_url,{signal:viewerController.signal,cache:'force-cache'});if(!response.ok)throw new Error('高清预览暂不可用');const blob=await response.blob();if(generation!==viewerGeneration||state.viewer!==current||!state.active)return;const previous=viewerBlob;if(previous)retiredViewerURLs.add(previous);viewerBlob=URL.createObjectURL(blob);$('viewer-image').src=viewerBlob;$('viewer-image').onload=()=>{if(generation===viewerGeneration){$('viewer-loading').hidden=true;viewerTransform();}if(previous){URL.revokeObjectURL(previous);retiredViewerURLs.delete(previous);}};current.highResolution=true;}catch(error){if(!isAbort(error)&&generation===viewerGeneration)$('viewer-loading').textContent=error.message;}}

function renderPresets(){const previous=state.preset;$('preset').replaceChildren(new Option('选择预设',''));for(const item of state.presets)$('preset').append(new Option(item.name,item.id));$('preset').value=previous;}
function engineTooltip(engine){return[engine.model_name?'模型 '+engine.model_name:null,Number.isFinite(engine.steps)?engine.steps+'步':null,[engine.sampler,engine.scheduler].filter(Boolean).join('/'),engine.reference_note].filter(Boolean).join(' ｜ ');}
async function loadEngines(){try{const data=await request('/engines');if(!state.active)return;state.engines=data.engines||[];if(state.engines.length){$('engine').replaceChildren(...state.engines.map(engine=>{const option=new Option(engine.name||engine.id,engine.id);option.title=engineTooltip(engine);return option;}));if(!state.engines.some(engine=>engine.id===state.engine))state.engine=state.engines[0].id;$('engine').value=state.engine;}renderEngine();modelConfig.render();}catch(error){showError(error);}}
function renderEngine(){const engine=state.engines.find(item=>item.id===state.engine),reason=engine?.reason||engine?.missing?.join('、');$('engine').title=engine?engineTooltip(engine):'';$('engine-state').textContent=engine?(engine.ready?(engine.reference_mode==='contact_sheet'?'多图拼合参考 · 可能保留分区布局':'已就绪'):reason||'模型尚未就绪'):'正在检查生成环境';$('engine-state').classList.toggle('warning',!!engine&&!engine.ready);$('engine-state').title=reason||engine?.reference_note||'';$('generate').disabled=state.busy;}
function generationPayload(){
  const references=[...state.selected.values()],roles={},originalPrompt=editor.read().trim();let prompt=originalPrompt;if(/@缺失参考\d+/.test(prompt))throw new Error('部分原参考素材已不可用，请替换提示词中的缺失参考');
  const engine=state.engines.find(item=>item.id===state.engine);if(engine?.max_references&&references.length>engine.max_references)throw new Error(`${engine.name} 最多使用 ${engine.max_references} 张参考图`);
  for(const[kind,name]of Object.entries(KIND_NAMES)){const asset=references.find(item=>item.kind===kind);if(asset)roles[name]=asset.id;if(prompt.includes('@'+name)){if(!asset)throw new Error('请先选择'+name+'参考图');prompt=prompt.replaceAll('@'+name,'@图'+(references.indexOf(asset)+1));}}
  if(settings.values.background==='preserve')prompt+='\n保持参考图中一致的背景环境与照明。';
  const invalid=[...prompt.matchAll(/@图(\d+)/g)].find(match=>Number(match[1])<1||Number(match[1])>references.length);if(invalid)throw new Error(invalid[0]+' 没有对应的参考图');
  return{engine:state.engine,engine_name:engine?.name||state.engine,prompt,asset_ids:references.map(item=>item.id),asset_roles:roles,preset:'default',prompt_preset:state.preset||undefined,original_prompt:originalPrompt,...settings.payload(),...modelConfig.payload(),external_session:state.mode==='external'?external.session:undefined,name:taskName()};
}
async function generate(){
  if(state.busy)return;
  let payload;try{payload=generationPayload();}catch(error){toast(error.message,true);return;}if(!payload.prompt){toast('先写下提示词，或选择一个预设');$('prompt-editor').focus();return;}
  state.busy=true;renderEngine();
  try{const data=await request('/generate',{method:'POST',body:payload});const id=data.prompt_id||data.id;if(!id)throw new Error('服务没有返回任务编号');state.jobId=id;jobs.accept({...data,name:payload.name,engine:payload.engine,settings:payload});host('jobs-changed',{prompt_id:id});persist(true);toast('已加入生成队列，可继续选择下一组');}
  catch(error){if(!isAbort(error))toast(error.message,true);}
  finally{state.busy=false;renderEngine();}
}
async function loadWorkflow(){try{const data=await request('/workflow',{method:'POST',body:generationPayload()});if(!host('load-workflow',{workflow:data.workflow})){const blob=new Blob([JSON.stringify(data.workflow,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=make('a');a.href=url;a.download=`资产库-${state.engine}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);toast('工作流已下载，可拖入 ComfyUI');}}catch(error){toast(error.message,true);}}

function editPresets(){const select=$('preset-manage-list');select.replaceChildren(new Option('新建预设',''));for(const item of state.presets)select.append(new Option(item.name,item.id));select.value=state.preset;fillPresetForm();providerSettings.open('provider');}
function fillPresetForm(){const preset=state.presets.find(item=>item.id===$('preset-manage-list').value);$('preset-name').value=preset?.name||'';$('preset-prompt').value=preset?.prompt||'';$('preset-delete').disabled=!preset;}
async function savePreset(remove=false){const id=$('preset-manage-list').value,name=$('preset-name').value.trim(),prompt=$('preset-prompt').value.trim();if(!remove&&(!name||!prompt)){toast('填写预设名称与提示词');return;}const items=state.presets.filter(item=>item.id!==id);if(!remove)items.push({id:id||'preset-'+Date.now().toString(36),name,prompt});try{const data=await request('/presets',{method:'PUT',body:{items}});state.presets=data.items||items;state.preset=remove?'':items.at(-1).id;renderPresets();$('preset-dialog').close();persist();toast(remove?'预设已删除':'预设已保存');}catch(error){toast(error.message,true);}}

function suspend(){if(!state.active)return;persist(true);state.active=false;document.body.classList.add('suspended');clearTimeout(summaryTimer);clearTimeout(jobTimer);clearTimeout(searchTimer);clearTimeout(saveTimer);clearTimeout(toastTimer);speech.cancel();jobs.suspend();settings.suspend();modelConfig.suspend();languageOptimizer.suspend();aboutDialog.suspend();external.hide();providerSettings.dispose();if($('preset-dialog').open)$('preset-dialog').close();closeViewer();editor.suspend();grid.suspend();for(const row of rows.values())row.gallery.suspend();media.clear();abortRequests();for(const controller of copyControllers)controller.abort();copyControllers.clear();$('context-menu').hidden=true;$('toast').hidden=true;}
function resume(){if(state.active)return;state.active=true;document.body.classList.remove('suspended');media.resume();const prompt=editor.snapshot();renderSelection();editor.restore(prompt);setActiveGalleries();loadSummary();loadEngines();jobs.resume();settings.resume();scheduleSummary();}
function scheduleSummary(){clearTimeout(summaryTimer);if(!state.active)return;summaryTimer=setTimeout(async()=>{await loadSummary();scheduleSummary();},12000);}

$('view-grid').onclick=()=>setMode('grid');$('view-combine').onclick=()=>setMode('combine');$('view-external').onclick=()=>setMode('external');
$('collapse-nav').onclick=()=>{state.navCollapsed=true;renderHeader();persist();};$('expand-nav').onclick=()=>{state.navCollapsed=false;renderHeader();persist();};
$('refresh').onclick=()=>{saved.scroll=null;loadSummary({initial:true});loadEngines();};$('retry-load').onclick=()=>loadSummary({initial:true});
$('close-library').onclick=()=>{persist(true);if(!host('close'))window.close();};
$('thumbnail-size').oninput=event=>{state.size=Number(event.target.value);grid.schedule();for(const row of rows.values())row.gallery.schedule();external.sizeChanged();persist();};
$('search').oninput=event=>{centerEpoch++;state.query=event.target.value;clearTimeout(searchTimer);searchTimer=setTimeout(()=>{resetGalleries();persist();},220);};
$('clear-selection').onclick=()=>{editor.bindNumericReferences();state.selected.clear();renderSelection();};
$('engine').onchange=event=>{state.engine=event.target.value;renderEngine();modelConfig.render();persist();};
$('preset').onchange=event=>{state.preset=event.target.value;const preset=state.presets.find(item=>item.id===state.preset);if(preset)applyPreset(preset);persist();};
$('edit-presets').onclick=editPresets;$('preset-manage-list').onchange=fillPresetForm;$('preset-save').onclick=()=>savePreset();$('preset-delete').onclick=()=>savePreset(true);
$('generate').onclick=generate;$('workflow-button').onclick=loadWorkflow;
$('viewer-close').onclick=closeViewer;$('viewer-prev').onclick=()=>nextViewer(-1);$('viewer-next').onclick=()=>nextViewer(1);$('viewer-fit').onclick=fitViewer;$('viewer-original').onclick=()=>{if(!state.viewer)return;state.viewer.scale=Math.max(.25,Math.min(8,1/viewerBaseScale()));state.viewer.x=state.viewer.y=0;viewerTransform();};
$('viewer-select').onclick=()=>{if(state.viewer?.asset?.result){reuseResult(state.viewer.asset.result);return;}if(state.viewer?.asset&&addReference(state.viewer.asset))toast('已加入参考');};
$('viewer-copy-prompt').onclick=()=>copyText(state.viewer?.asset.prompt||'','已复制参考描述').catch(error=>toast(error.message,true));
$('viewer-copy-image').onclick=()=>{if(state.viewer)copyImage(state.viewer.asset).catch(error=>toast(error.message,true));};
$('viewer-add-canvas').onclick=()=>{if(state.viewer?.asset?.result){if(!host('add-to-canvas',{image:state.viewer.asset.result}))toast('请从 ComfyUI 打开资产库后加入画布');}else if(state.viewer)addToCanvas(state.viewer.asset);};
$('viewer-stage').addEventListener('wheel',event=>{if(!state.viewer)return;event.preventDefault();const rect=$('viewer-stage').getBoundingClientRect(),px=event.clientX-rect.left-rect.width/2,py=event.clientY-rect.top-rect.height/2,previous=state.viewer.scale,next=Math.max(.25,Math.min(8,previous*Math.exp(-event.deltaY*.0015))),factor=next/previous;state.viewer.x=px-(px-state.viewer.x)*factor;state.viewer.y=py-(py-state.viewer.y)*factor;state.viewer.scale=next;viewerTransform();},{passive:false});
let pointer=null;$('viewer-stage').addEventListener('pointerdown',event=>{if(!state.viewer||event.button!==0)return;event.preventDefault();pointer={id:event.pointerId,x:event.clientX,y:event.clientY,originX:state.viewer.x,originY:state.viewer.y};$('viewer-stage').setPointerCapture(event.pointerId);$('viewer-stage').classList.add('dragging');});
$('viewer-stage').addEventListener('pointermove',event=>{if(!pointer||!state.viewer)return;state.viewer.x=pointer.originX+event.clientX-pointer.x;state.viewer.y=pointer.originY+event.clientY-pointer.y;viewerTransform();});
for(const type of['pointerup','pointercancel'])$('viewer-stage').addEventListener(type,()=>{pointer=null;$('viewer-stage').classList.remove('dragging');});
$('viewer-stage').addEventListener('dblclick',loadViewerHD);$('viewer-hd').onclick=loadViewerHD;
document.addEventListener('pointerdown',event=>{if(!$('context-menu').contains(event.target)&&!event.target.closest('.card-more'))$('context-menu').hidden=true;});
document.addEventListener('keydown',event=>{
  if(!state.active||event.isComposing)return;const typing=event.target.closest('input,textarea,[contenteditable=true]');
  if(event.key==='Escape'){if(!$('viewer').hidden)closeViewer();else if(!$('context-menu').hidden)$('context-menu').hidden=true;else if(!$('mention-menu').hidden)$('mention-menu').hidden=true;else if(!$('preset-dialog').open)host('close');}
  else if(!typing&&!$('viewer').hidden&&['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();nextViewer(event.key==='ArrowRight'?1:-1);}
  else if(!typing&&event.key==='/'){event.preventDefault();$('search').focus();}
  else if((event.ctrlKey||event.metaKey)&&event.key==='Enter'&&typing){event.preventDefault();generate();}
  else if(!typing&&(event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='c'&&state.selected.size===1){event.preventDefault();copyImage([...state.selected.values()][0]).catch(error=>toast(error.message,true));}
});
let canvasDrag=null;
document.addEventListener('dragstart',event=>{const card=event.target.closest('[data-asset-id]');if(card){canvasDrag={asset_id:card.dataset.assetId};if(state.mode!=='external')host('drag-start',canvasDrag);}else if(event.dataTransfer?.types.includes('application/x-bbaigc-result')){try{canvasDrag={image:JSON.parse(event.dataTransfer.getData('application/x-bbaigc-result'))};}catch{canvasDrag=null;}}});
document.addEventListener('dragover',event=>{if(embedded&&canvasDrag&&!event.target.closest('.external-row')){event.preventDefault();event.stopImmediatePropagation();event.dataTransfer.dropEffect='copy';}},{capture:true});
document.addEventListener('drop',event=>{if(embedded&&canvasDrag&&!event.target.closest('.external-row')){event.preventDefault();event.stopImmediatePropagation();host('drop-on-canvas',{...canvasDrag,x:event.clientX,y:event.clientY});canvasDrag=null;}},{capture:true});
document.addEventListener('dragend',()=>{canvasDrag=null;host('drag-end');});
window.addEventListener('message',event=>{if(!embedded||event.origin!==parentOrigin||event.source!==window.parent||event.data?.source!=='bbaigc-asset-host')return;const data=event.data;if(data.type==='visibility'){if(data.visible)resume();else suspend();}else if(data.type==='dismiss-popovers'){modelConfig.close();settings.closeRatios();jobs.closeDropdown();$('context-menu').hidden=true;$('result-actions-menu').hidden=true;}else if(data.type==='theme'){document.documentElement.dataset.theme=data.theme==='light'?'light':'dark';}else if(data.type==='external-imported'&&(!data.session||data.session===external.session)){setMode('external');external.refresh(data.items||[]);}else if(data.type==='notice')toast(data.message||'',!!data.error);});
document.addEventListener('visibilitychange',()=>{if(document.hidden)suspend();else resume();});
window.addEventListener('pagehide',suspend);window.addEventListener('beforeunload',()=>persist(true));
for(const scroller of[$('grid-scroller'),...[...rows.values()].map(row=>row.gallery.root)])scroller.addEventListener('scroll',()=>persist(),{passive:true});

$('page-title').closest('.workspace').querySelectorAll('.brand-symbol').forEach(node=>node.replaceChildren(icon('library')));
document.querySelector('.brand-symbol')?.replaceChildren(icon('library'));
$('generate').querySelector('span')?.replaceWith(icon('generate'));
renderHeader();renderNavigation();renderSelection();renderEngine();
Promise.allSettled([loadSummary(),loadEngines(),request('/presets').then(data=>{if(state.active){state.presets=data.items||state.presets;renderPresets();}})]).then(()=>{if(!editor.read()&&!saved.prompt?.length&&state.selected.size&&state.presets[0]){state.preset=state.presets[0].id;applyPreset(state.presets[0]);renderPresets();}});
jobs.refresh();aboutDialog.load();Promise.allSettled([settings.load(),modelConfig.load(),languageOptimizer.load()]).then(()=>{modelConfig.render();languageOptimizer.render();});if(state.mode==='external')setActiveGalleries();scheduleSummary();
// Diagnostics are scalar counts; no image bodies or private prompts are retained here.
window.assetLibraryDiagnostics=()=>({active:state.active,mode:state.mode,media:media.stats(),grid:grid.stats(),rows:Object.fromEntries([...rows].map(([kind,row])=>[kind,row.gallery.stats()])),selected:state.selected.size,jobs:jobs.stats(),speech:speech.stats(),external:external.stats()});
