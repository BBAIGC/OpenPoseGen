import {request, isAbort} from './api.js';
import {openAIPlaceholder} from './placeholder.js';

const PAGE = 48, MAX_PAGES = 4, MAX_CARDS = 96;
export const KIND_NAMES = {identity:'形象',outfit:'服装',pose:'姿态'};

function el(tag, className, text) {
  const node = document.createElement(tag); if (className) node.className = className;
  if (text !== undefined) node.textContent = text; return node;
}

export class VirtualGallery {
  constructor(root, {horizontal = false, kind = 'identity', media, getSize, selected, onSelect, onPreview, onContext, onStatus, onScroll}) {
    Object.assign(this, {root, horizontal, kind, media, getSize, selected, onSelect, onPreview, onContext, onStatus});
    this.canvas = root.querySelector('.virtual-canvas'); this.window = el('div','virtual-window'); this.canvas.append(this.window);
    this.empty = root.querySelector('.gallery-empty'); this.total = 0; this.category = ''; this.query = '';
    this.pages = new Map(); this.pending = new Map(); this.cards = new Map(); this.generation = 0;
    this.active = true; this.hasLoaded = false; this.raf = 0; this.displayed = [];this.windowStart=-1;this.windowEnd=-1;
    root.addEventListener('scroll', () => {this.schedule();this.media.reprioritize?.();onScroll?.(this);}, {passive:true});
    this.resize = new ResizeObserver(() => this.schedule()); this.resize.observe(root);
    root.addEventListener('click', event => {
      if(event.target.closest('.image-retry'))return;
      const card = event.target.closest('[data-asset-id]'); if (!card) return;
      const asset = this.assetById(card.dataset.assetId); if (!asset) return;
      if (event.target.closest('.card-more')) { event.stopPropagation(); this.onContext(asset, event); return; }
      this.onSelect(asset, event);
    });
    root.addEventListener('dblclick', event => {
      const card = event.target.closest('[data-asset-id]'); const asset = card && this.assetById(card.dataset.assetId);
      if (asset) this.onPreview(asset, this.visibleAssets(), {highResolution:true});
    });
    root.addEventListener('contextmenu', event => {
      const card = event.target.closest('[data-asset-id]'); const asset = card && this.assetById(card.dataset.assetId);
      if (asset) { event.preventDefault(); this.onContext(asset, event); }
    });
    root.addEventListener('keydown', event => {
      const card = event.target.closest('[data-asset-id]'); const asset = card && this.assetById(card.dataset.assetId);
      if (asset && event.key === 'Enter') { event.preventDefault(); this.onPreview(asset, this.visibleAssets()); }
      else if (asset && event.key === ' ') { event.preventDefault(); this.onSelect(asset, event); }
    });
    root.addEventListener('dragstart', event => {
      const card = event.target.closest('[data-asset-id]'); const asset = card && this.assetById(card.dataset.assetId);
      if (!asset || !event.dataTransfer) return;
      event.dataTransfer.effectAllowed = 'copy';
      event.dataTransfer.setData('application/x-bbaigc-asset', JSON.stringify({id:asset.id,title:asset.title,thumbnail_url:asset.thumbnail_url}));
      event.dataTransfer.setData('text/plain','bbaigc-asset:' + asset.id);
      if (asset.image_url) event.dataTransfer.setData('text/uri-list', new URL(asset.image_url, location.href).href);
      card.classList.add('dragging');
    });
    root.addEventListener('dragend', () => { root.querySelectorAll('.dragging').forEach(node => node.classList.remove('dragging')); });
  }
  reset({kind = this.kind, category = '', query = '', total = 0, restore = false} = {}) {
    this.generation++; for (const controller of this.pending.values()) controller.abort(); this.pending.clear();
    Object.assign(this,{kind, category, query, total}); this.pages.clear(); this.hasLoaded = false;
    this.releaseCards();this.windowStart=this.windowEnd=-1; if (!restore) this.root[this.horizontal ? 'scrollLeft':'scrollTop'] = 0;
    this.schedule();
  }
  schedule() {
    if (!this.active || this.raf) return;
    this.raf = requestAnimationFrame(() => { this.raf = 0; this.render(); });
  }
  metrics() {
    if (this.horizontal) {
      const height = Math.max(66, this.root.clientHeight - 34);
      const size = Math.max(80, Math.min(this.getSize(), height * .75));
      return {columns:1, stride:size+11, width:size, cardHeight:this.root.clientHeight-9, offset:this.root.scrollLeft, viewport:this.root.clientWidth};
    }
    const width = this.root.clientWidth - 36, min = this.getSize();
    const columns = Math.max(1,Math.floor((width+12)/(min+12)));
    const cardWidth = (width-(columns-1)*12)/columns;
    return {columns,stride:cardWidth*1.18+44,width:cardWidth,cardHeight:cardWidth*1.18+31,offset:this.root.scrollTop,viewport:this.root.clientHeight};
  }
  render() {
    if (!this.active || this.root.hidden || !this.root.clientWidth || !this.root.clientHeight) return;
    const m=this.metrics(), virtualTotal=this.hasLoaded?this.total:Math.max(this.total,PAGE);
    let start,end;
    if (this.horizontal) {
      const neededStart=Math.max(0,Math.floor(Math.max(0,m.offset-m.viewport*.65)/m.stride)),neededEnd=Math.min(virtualTotal,Math.ceil((m.offset+m.viewport*1.65)/m.stride));
      if(this.windowStart>=0&&neededStart>=this.windowStart&&neededEnd<=this.windowEnd){start=this.windowStart;end=Math.min(virtualTotal,this.windowEnd);}else{start=Math.max(0,neededStart-2);end=Math.min(virtualTotal,start+Math.min(MAX_CARDS,neededEnd-start+3));this.windowStart=start;this.windowEnd=end;}
      this.canvas.style.width=Math.max(0,virtualTotal*m.stride-11)+'px';
      this.window.style.left=(start*m.stride)+'px'; this.window.style.top='0';
    } else {
      const rows=Math.ceil(virtualTotal/m.columns);
      const neededRow=Math.max(0,Math.floor(Math.max(0,m.offset-100)/m.stride)),neededEnd=Math.min(virtualTotal,Math.ceil((m.offset+m.viewport+100)/m.stride)*m.columns),maxRows=Math.max(1,Math.floor(MAX_CARDS/m.columns));
      if(this.windowStart>=0&&neededRow*m.columns>=this.windowStart&&neededEnd<=this.windowEnd&&this.windowColumns===m.columns){start=this.windowStart;end=Math.min(virtualTotal,this.windowEnd);}else{start=Math.max(0,neededRow-1)*m.columns;end=Math.min(virtualTotal,start+Math.min(maxRows,Math.ceil((m.viewport+200)/m.stride)+3)*m.columns);this.windowStart=start;this.windowEnd=end;this.windowColumns=m.columns;}
      const firstRow=Math.floor(start/m.columns);
      this.canvas.style.height=(rows*m.stride+20)+'px';
      this.window.style.top=(firstRow*m.stride)+'px'; this.window.style.left='0';
      this.window.style.gridTemplateColumns=`repeat(${m.columns},minmax(0,1fr))`;
    }
    if (this.empty) this.empty.hidden=!(this.hasLoaded && this.total===0);
    if (this.hasLoaded && this.total===0) { this.releaseCards(); return; }
    const next = new Map(), displayed=[];
    for (let index=start; index<end; index++) {
      const asset=this.itemAt(index), key=asset?.id || 'skeleton:'+index;
      let card=this.cards.get(key);
      if (!card) card=asset?this.createCard(asset):this.createSkeleton();
      card.style.height=m.cardHeight+'px'; if(this.horizontal) card.style.width=m.width+'px';
      if(asset) {card.classList.toggle('selected',this.selected().has(asset.id));card.setAttribute('aria-selected',String(this.selected().has(asset.id)));displayed.push(asset);}
      next.set(key,card);
      const expectedPosition=index-start;if(this.window.children[expectedPosition]!==card)this.window.insertBefore(card,this.window.children[expectedPosition]||null);
    }
    for(const[key,card]of this.cards)if(!next.has(key)){this.media.releaseTree(card);card.remove();}
    this.cards=next; this.displayed=displayed;
    const firstPage=Math.floor(start/PAGE),lastPage=Math.floor(Math.max(start,end-1)/PAGE);
    for(const[page,controller]of this.pending)if(page<firstPage||page>lastPage){controller.abort();this.pending.delete(page);}
    for(let page=firstPage;page<=lastPage;page++)this.loadPage(page);
    const center=(firstPage+lastPage)/2;
    while(this.pages.size>MAX_PAGES){const page=[...this.pages.keys()].sort((a,b)=>Math.abs(b-center)-Math.abs(a-center))[0];this.pages.delete(page);}
  }
  itemAt(index) { return this.pages.get(Math.floor(index/PAGE))?.[index%PAGE]; }
  assetById(id) { return this.displayed.find(item=>item.id===id); }
  visibleAssets() { return this.displayed.slice(); }
  async loadPage(page) {
    if(this.pages.has(page)||this.pending.has(page)||!this.active||page*PAGE>=this.total&&this.hasLoaded)return;
    const generation=this.generation,controller=new AbortController();this.pending.set(page,controller);
    const query=new URLSearchParams({kind:this.kind,category:this.category,q:this.query,offset:String(page*PAGE),limit:String(PAGE)});
    try{
      const data=await request('/assets?'+query,{signal:controller.signal});
      if(generation!==this.generation||!this.active)return;
      this.pages.set(page,data.items||[]);this.total=Number(data.total)||0;this.hasLoaded=true;
      this.onStatus?.(this.total,this);this.schedule();
    }catch(error){if(!isAbort(error))this.onStatus?.(null,this,error);}
    finally{if(this.pending.get(page)===controller)this.pending.delete(page);}
  }
  createCard(asset) {
    const card=el('article','asset-card');card.dataset.assetId=asset.id;card.tabIndex=0;card.draggable=true;card.setAttribute('role','option');card.setAttribute('aria-label',asset.title);
    const media=el('div','asset-media'),fallback=openAIPlaceholder(),image=el('img');image.alt=asset.title;media.append(fallback,image);
    const selected=el('span','card-select');selected.setAttribute('aria-hidden','true');selected.innerHTML='<svg viewBox="0 0 24 24"><path d="m6.5 12 3.5 3.5 7.5-8"/></svg>';
    const more=el('button','card-more','⋯');more.title='更多操作';more.setAttribute('aria-label',asset.title+'，更多操作');
    media.append(selected,more);if(asset.available===false)media.append(el('span','asset-pending','原图下载中'));
    const info=el('div','asset-info');info.append(el('span','asset-title',asset.title),el('span','asset-type',asset.category||''));card.append(media,info);
    this.media.observe(image,asset.thumbnail_url||asset.image_url,{retain:true});return card;
  }
  createSkeleton() {const card=el('div','asset-card asset-skeleton');card.setAttribute('aria-hidden','true');const surface=el('div','asset-media');surface.append(openAIPlaceholder());card.append(surface,el('div','asset-info'));card.lastChild.append(el('span','asset-title'));return card;}
  updateSelection() {for(const[id,card]of this.cards){const value=this.selected().has(id);card.classList.toggle('selected',value);card.setAttribute('aria-selected',String(value));}}
  scrollToIndex(index,{behavior='smooth'}={}){const m=this.metrics(),offset=this.horizontal?index*m.stride-(this.root.clientWidth-m.width)/2:Math.floor(index/m.columns)*m.stride-(this.root.clientHeight-m.cardHeight)/2,extent=this.horizontal?this.root.clientWidth:this.root.clientHeight,property=this.horizontal?'left':'top';if(behavior==='smooth'&&Math.abs(offset-m.offset)>extent*5)this.root.scrollTo({[property]:Math.max(0,offset-extent*.6),behavior:'instant'});this.root.scrollTo({[property]:Math.max(0,offset),behavior});this.schedule();}
  releaseCards() {this.media.releaseTree(this.window);this.window.replaceChildren();this.cards.clear();this.displayed=[];this.windowStart=this.windowEnd=-1;}
  suspend(){this.active=false;this.generation++;if(this.raf)cancelAnimationFrame(this.raf);this.raf=0;for(const c of this.pending.values())c.abort();this.pending.clear();this.resize.disconnect();this.releaseCards();}
  resume(){this.active=true;this.resize.observe(this.root);this.schedule();}
  stats(){return{cards:this.cards.size,pages:this.pages.size,pending:this.pending.size};}
}
