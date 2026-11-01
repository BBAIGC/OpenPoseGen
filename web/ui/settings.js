import {request,isAbort} from './api.js';

const $=id=>document.getElementById(id);
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
export const RATIOS=['1:1','2:3','3:2','3:4','4:3','4:5','5:4','9:16','16:9','9:21','21:9','1:2','2:1'];
export const DETAIL_NAMES=['最简','简洁','适中','详细','精细'];

export function localDimensions(ratio){const[a,b]=String(ratio).split(':').map(Number),long=Math.max(a,b);return['1K','2K','4K'].map((id,index)=>{const side=[1024,2048,4096][index];return{id,width:Math.max(32,Math.round(side*a/long/32)*32),height:Math.max(32,Math.round(side*b/long/32)*32)};});}

export class GenerationSettings {
  constructor({onDetail,onToast}) {
    Object.assign(this,{onDetail,onToast});this.values={save_output:true,auto_open_folder:false,resolution:'2K',ratio:'3:4',detail_level:3,background:'auto'};this.options=localDimensions('3:4');this.dirty=new Set();this.timer=0;this.saving=false;this.saveAgain=false;this.dimensionEpoch=0;this.active=true;
    try{const pending=JSON.parse(sessionStorage.getItem('bbaigc-asset-settings-pending')||'null');if(pending){Object.assign(this.values,pending);for(const key of Object.keys(pending))this.dirty.add(key);this.normalize();this.options=localDimensions(this.values.ratio);}}catch{}
    $('detail-level').oninput=event=>{const value=Number(event.target.value);this.set('detail_level',value);this.onDetail?.(value);};
    $('background').onchange=event=>this.set('background',event.target.value);
    $('save-output').onclick=()=>this.set('save_output',!this.values.save_output);
    $('auto-open-folder').onclick=()=>{if(this.values.save_output)this.set('auto_open_folder',!this.values.auto_open_folder);};
    $('generation-settings-toggle').onclick=()=>{const menu=$('generation-settings-popover');menu.hidden=!menu.hidden;$('generation-settings-toggle').setAttribute('aria-expanded',String(!menu.hidden));if(!menu.hidden)this.positionRatios();};
    for(const ratio of RATIOS){const button=el('button','ratio-option'),symbol=el('i','ratio-symbol');this.symbol(symbol,ratio);button.append(symbol,el('span','',ratio));button.setAttribute('aria-label',ratio+' 比例');button.dataset.ratio=ratio;button.onclick=()=>this.set('ratio',ratio);$('ratio-menu').append(button);}
    document.addEventListener('pointerdown',event=>{if(!$('generation-settings-popover').contains(event.target)&&!$('generation-settings-toggle').contains(event.target))this.closeRatios();});
    document.addEventListener('keydown',event=>{if(event.key==='Escape')this.closeRatios();});
    this.render();
  }
  async load(){try{const data=await request('/settings');if(!this.active)return;const values=data.settings||data;for(const[key,value]of Object.entries(values))if(!this.dirty.has(key))this.values[key]=value;this.normalize();this.render();this.dimensions();if(this.dirty.size)this.save();}catch(error){if(!isAbort(error)&&error.status!==404)this.onToast?.(error.message,true);}}
  normalize(){if(!RATIOS.includes(this.values.ratio))this.values.ratio='3:4';if(!['1K','2K','4K'].includes(this.values.resolution))this.values.resolution='2K';this.values.detail_level=Math.max(1,Math.min(5,Number(this.values.detail_level)||3));this.values.save_output=this.values.save_output!==false;this.values.auto_open_folder=this.values.save_output&&this.values.auto_open_folder===true;this.values.background=this.values.background==='preserve'?'preserve':'auto';}
  set(key,value){this.values[key]=value;this.dirty.add(key);this.normalize();try{sessionStorage.setItem('bbaigc-asset-settings-pending',JSON.stringify(this.values));}catch{}this.render();if(key==='ratio')this.dimensions();clearTimeout(this.timer);this.timer=setTimeout(()=>this.save(),350);}
  async save(){if(this.saving){this.saveAgain=true;return;}this.saving=true;const snapshot=JSON.stringify(this.values);try{await request('/settings',{method:'PUT',body:{...this.values}});if(snapshot===JSON.stringify(this.values)){try{sessionStorage.removeItem('bbaigc-asset-settings-pending');}catch{}this.dirty.clear();}}catch(error){if(!isAbort(error))this.onToast?.(error.message,true);}finally{this.saving=false;if(this.saveAgain&&this.active){this.saveAgain=false;this.save();}}}
  async dimensions(){const epoch=++this.dimensionEpoch,ratio=this.values.ratio;this.options=localDimensions(ratio);this.renderSizes();try{const data=await request('/dimensions?ratio='+encodeURIComponent(ratio));if(epoch!==this.dimensionEpoch||!this.active)return;if(data.options?.length){this.options=data.options;this.renderSizes();}}catch(error){if(!isAbort(error)&&error.status!==404)this.onToast?.('尺寸暂按所选比例计算');}}
  render(){this.renderSizes();$('detail-level').value=this.values.detail_level;$('detail-label').textContent=DETAIL_NAMES[this.values.detail_level-1];$('background').value=this.values.background;$('save-output').setAttribute('aria-checked',String(this.values.save_output));$('auto-open-folder').setAttribute('aria-checked',String(this.values.auto_open_folder));$('auto-open-folder').disabled=!this.values.save_output;$('auto-open-folder').closest('label').classList.toggle('disabled',!this.values.save_output);for(const button of $('ratio-menu').querySelectorAll('button'))button.setAttribute('aria-pressed',String(button.dataset.ratio===this.values.ratio));this.renderSummary();}
  renderSummary(){const size=this.options.find(option=>option.id===this.values.resolution)||localDimensions(this.values.ratio).find(option=>option.id===this.values.resolution);const text=`${this.values.ratio} · ${this.values.resolution} · ${size.width}×${size.height} · ${this.values.background==='preserve'?'保留参考背景':'自动背景'}`;$('generation-settings-summary').textContent=text;$('generation-settings-toggle').title=text;}
  renderSizes(){const root=$('size-presets');root.replaceChildren();for(const option of this.options){const button=el('button','size-preset');button.setAttribute('aria-pressed',String(option.id===this.values.resolution));button.append(el('strong','',option.id),el('small','',`${option.width} × ${option.height}`));button.onclick=()=>this.set('resolution',option.id);root.append(button);}this.renderSummary();}
  symbol(node,ratio){const[a,b]=ratio.split(':').map(Number),scale=Math.min(17/a,17/b);node.style.width=Math.max(6,a*scale)+'px';node.style.height=Math.max(6,b*scale)+'px';}
  positionRatios(){const rect=$('generation-settings-toggle').getBoundingClientRect(),menu=$('generation-settings-popover');menu.style.left=Math.max(8,Math.min(innerWidth-menu.offsetWidth-8,rect.left))+'px';menu.style.top=Math.max(8,Math.min(rect.top-menu.offsetHeight-8,innerHeight-menu.offsetHeight-8))+'px';}
  closeRatios(){$('generation-settings-popover').hidden=true;$('generation-settings-toggle').setAttribute('aria-expanded','false');}
  payload(){const size=this.options.find(option=>option.id===this.values.resolution)||localDimensions(this.values.ratio).find(option=>option.id===this.values.resolution);return{...this.values,width:size.width,height:size.height};}
  restore(values){for(const key of['save_output','auto_open_folder','resolution','ratio','detail_level','background','model_overrides','lora_slots','seed_mode','seed','language_config'])if(values[key]!==undefined){this.values[key]=values[key];this.dirty.add(key);}this.normalize();this.options=localDimensions(this.values.ratio);this.render();this.dimensions();try{sessionStorage.setItem('bbaigc-asset-settings-pending',JSON.stringify(this.values));}catch{}clearTimeout(this.timer);this.timer=setTimeout(()=>this.save(),350);}
  suspend(){this.active=false;this.dimensionEpoch++;this.closeRatios();if(this.timer){clearTimeout(this.timer);this.timer=0;this.save();}}
  resume(){this.active=true;this.dimensions();}
}
