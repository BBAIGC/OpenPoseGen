import {KIND_NAMES} from './gallery.js';

const create=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};

export class ReferenceEditor {
  constructor(root, menu, {getReferences, media, onChange,onReferenceClick,onPasteText}) {
    Object.assign(this,{root,menu,getReferences,media,onChange,onReferenceClick,onPasteText});this.range=null;this.mentionRange=null;this.options=[];this.index=0;this.composing=false;
    root.addEventListener('input',()=>{if(!this.composing){this.showMentions();this.changed();}});
    root.addEventListener('compositionstart',()=>{this.composing=true;menu.hidden=true;});
    root.addEventListener('compositionend',()=>{this.composing=false;this.showMentions();this.changed();});
    root.addEventListener('keyup',()=>this.capture());root.addEventListener('pointerup',()=>this.capture());root.addEventListener('focus',()=>this.capture());
    root.addEventListener('click',event=>{const chip=event.target.closest('.prompt-chip');if(chip){const asset=this.getReferences().find(item=>item.id===chip.dataset.assetId);if(asset)this.onReferenceClick?.(asset);}});
    root.addEventListener('keydown',event=>{
      if(this.composing||event.isComposing||menu.hidden)return;
      if(['ArrowUp','ArrowDown'].includes(event.key)){event.preventDefault();this.index=(this.index+(event.key==='ArrowDown'?1:-1)+this.options.length)%this.options.length;this.drawMentions();}
      else if(['Enter','Tab'].includes(event.key)){event.preventDefault();this.choose(this.options[this.index]);}
      else if(event.key==='Escape'){event.preventDefault();menu.hidden=true;}
    });
    root.addEventListener('paste',event=>{event.preventDefault();const text=event.clipboardData?.getData('text/plain')||'';if(!this.onPasteText?.(text))this.insertText(text);});
    root.addEventListener('copy',event=>{const selection=window.getSelection();if(!selection?.rangeCount||selection.isCollapsed)return;const container=document.createElement('div');container.append(selection.getRangeAt(0).cloneContents());event.clipboardData?.setData('text/plain',this.read(container));event.preventDefault();});
    root.addEventListener('blur',()=>setTimeout(()=>{if(!menu.contains(document.activeElement))menu.hidden=true;},100));
    menu.addEventListener('pointerdown',event=>event.preventDefault());
  }
  read(root=this.root) {
    let result='';const refs=[...this.getReferences()];
    for(const child of root.childNodes){
      if(child.nodeType===Node.TEXT_NODE)result+=child.textContent;
      else if(child.classList?.contains('prompt-chip')){const index=refs.findIndex(item=>item.id===child.dataset.assetId);result+=index>=0?'@图'+(index+1):'@已移除';}
      else if(child.nodeName==='BR')result+='\n';
      else{if(['DIV','P'].includes(child.nodeName)&&result&&!result.endsWith('\n'))result+='\n';result+=this.read(child);}
    }
    return result.replace(/\u200b/g,'');
  }
  snapshot(){const result=[];const visit=root=>{for(const node of root.childNodes){if(node.nodeType===Node.TEXT_NODE)result.push(node.textContent);else if(node.classList?.contains('prompt-chip'))result.push({id:node.dataset.assetId,role:node.dataset.role||undefined});else if(node.nodeName==='BR')result.push('\n');else{if(['DIV','P'].includes(node.nodeName)&&result.length&&result.at(-1)!=='\n')result.push('\n');visit(node);}}};visit(this.root);return result;}
  readNode(node){if(node.nodeType===Node.TEXT_NODE)return node.textContent;if(node.nodeName==='BR')return '\n';const box=document.createElement('div');box.append(node.cloneNode(true));return this.read(box);}
  restore(snapshot){this.media.releaseTree(this.root);this.root.replaceChildren();for(const value of snapshot||[]){if(typeof value==='string')this.root.append(document.createTextNode(value));else{const asset=this.getReferences().find(item=>item.id===value.id);if(asset){const chip=this.chip(asset);if(value.role)chip.dataset.role=value.role;this.root.append(chip);}}}this.reconcile();}
  chip(asset){const chip=create('span','prompt-chip');chip.contentEditable='false';chip.dataset.assetId=asset.id;chip.setAttribute('role','img');chip.title=asset.title;const image=create('img'),label=create('span');image.alt='';chip.append(image,label);this.updateChip(chip,asset);this.media.observe(image,asset.thumbnail_url||asset.image_url);return chip;}
  updateChip(chip,asset){const refs=this.getReferences(),index=refs.findIndex(item=>item.id===asset.id);chip.querySelector('span').textContent=`@图${index+1} · ${asset.title}`;chip.setAttribute('aria-label',`图${index+1}，${asset.title}`);}
  bindNumericReferences(){
    const refs=this.getReferences(),walker=document.createTreeWalker(this.root,NodeFilter.SHOW_TEXT),nodes=[];
    while(walker.nextNode())if(!walker.currentNode.parentElement.closest('.prompt-chip'))nodes.push(walker.currentNode);
    let changed=false;
    for(const node of nodes){const pattern=/@图([1-9]\d*)/g;let match,last=0,found=false;const fragment=document.createDocumentFragment();while((match=pattern.exec(node.textContent))){const asset=refs[Number(match[1])-1];if(!asset)continue;fragment.append(document.createTextNode(node.textContent.slice(last,match.index)),this.chip(asset));last=pattern.lastIndex;found=true;}if(found){fragment.append(document.createTextNode(node.textContent.slice(last)));node.replaceWith(fragment);changed=true;}}
    if(changed)this.changed();
  }
  reconcile(){
    const roleKind={'@形象':'identity','@服装':'outfit','@姿态':'pose'};
    for(const chip of this.root.querySelectorAll('.prompt-chip')){
      const asset=chip.dataset.role?this.getReferences().find(item=>item.kind===roleKind[chip.dataset.role]):this.getReferences().find(item=>item.id===chip.dataset.assetId);
      if(asset){if(chip.dataset.assetId!==asset.id){chip.dataset.assetId=asset.id;this.media.observe(chip.querySelector('img'),asset.thumbnail_url||asset.image_url);}this.updateChip(chip,asset);}
      else{this.media.releaseTree(chip);if(chip.dataset.role)chip.replaceWith(document.createTextNode(chip.dataset.role));else chip.remove();}
    }
    const walker=document.createTreeWalker(this.root,NodeFilter.SHOW_TEXT),nodes=[];while(walker.nextNode())if(!walker.currentNode.parentElement.closest('.prompt-chip'))nodes.push(walker.currentNode);
    for(const node of nodes){if(!/@形象|@服装|@姿态/.test(node.textContent))continue;const parts=node.textContent.split(/(@形象|@服装|@姿态)/g),fragment=document.createDocumentFragment();let changed=false;for(const part of parts){const asset=roleKind[part]&&this.getReferences().find(item=>item.kind===roleKind[part]);if(asset){const chip=this.chip(asset);chip.dataset.role=part;fragment.append(chip);changed=true;}else fragment.append(document.createTextNode(part));}if(changed)node.replaceWith(fragment);}
    this.changed();
  }
  capture(){const selection=window.getSelection();if(selection?.rangeCount&&this.root.contains(selection.anchorNode))this.range=selection.getRangeAt(0).cloneRange();}
  caret(){this.root.focus({preventScroll:true});const selection=window.getSelection();let range=this.range;if(!range||!this.root.contains(range.commonAncestorContainer)){range=document.createRange();range.selectNodeContents(this.root);range.collapse(false);}selection.removeAllRanges();selection.addRange(range);return range;}
  insert(asset,{replaceMention=false}={}){const range=replaceMention&&this.mentionRange?this.mentionRange:this.caret();range.deleteContents();const chip=this.chip(asset),space=document.createTextNode(' '),fragment=document.createDocumentFragment();fragment.append(chip,space);range.insertNode(fragment);range.setStartAfter(space);range.collapse(true);const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);this.range=range.cloneRange();this.menu.hidden=true;this.root.focus({preventScroll:true});this.changed();}
  insertText(text){if(!text)return;const range=this.caret();range.deleteContents();const refs=this.getReferences(),fragment=document.createDocumentFragment(),normalized=String(text).replace(/<image([1-9]\d*)>|参考图([1-9]\d*)/gi,(_,a,b)=>'@图'+(a||b));let tail=null;for(const part of normalized.split(/(@形象|@服装|@姿态|@图\d+)/g)){const number=part.match(/^@图(\d+)$/),kind={'@形象':'identity','@服装':'outfit','@姿态':'pose'}[part],asset=number?refs[Number(number[1])-1]:kind?refs.find(item=>item.kind===kind):null;tail=asset?this.chip(asset):document.createTextNode(part);if(asset&&kind)tail.dataset.role=part;fragment.append(tail);}range.insertNode(fragment);if(tail)range.setStartAfter(tail);range.collapse(true);const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);this.range=range.cloneRange();this.changed();}
  createSpeechAnchor(){
    const range=this.caret().cloneRange(),marker=document.createElement('span');marker.dataset.speechAnchor='true';marker.contentEditable='false';marker.setAttribute('aria-hidden','true');marker.style.cssText='display:inline;width:0;overflow:hidden;line-height:0';range.deleteContents();range.insertNode(marker);
    return{marker,dispose:()=>marker.remove()};
  }
  insertTranscript(text,anchor){
    const marker=anchor?.marker;if(!text||!marker?.isConnected||!this.root.contains(marker))return false;
    const previous=marker.previousSibling?.textContent||'',next=marker.nextSibling?.textContent||'',left=/[A-Za-z0-9]$/.test(previous)&&/^[A-Za-z0-9]/.test(text)?' ':'',right=/[A-Za-z0-9]$/.test(text)&&/^[A-Za-z0-9]/.test(next)?' ':'';
    const node=document.createTextNode(left+text+right);marker.before(node);const selection=window.getSelection();if(document.activeElement===this.root&&selection?.isCollapsed){const range=document.createRange();range.setStartAfter(node);range.collapse(true);selection.removeAllRanges();selection.addRange(range);this.range=range.cloneRange();}this.changed();return true;
  }
  setPreset(text){this.media.releaseTree(this.root);this.root.replaceChildren();const refs=this.getReferences();const normalized=String(text||'').replace(/<image([1-9]\d*)>|参考图([1-9]\d*)/gi,(_,a,b)=>'@图'+(a||b));const parts=normalized.split(/(@形象|@服装|@姿态|@图\d+)/g);for(const part of parts){let asset;if(/^@图\d+$/.test(part))asset=refs[Number(part.slice(2))-1];else if(part==='@形象')asset=refs.find(item=>item.kind==='identity');else if(part==='@服装')asset=refs.find(item=>item.kind==='outfit');else if(part==='@姿态')asset=refs.find(item=>item.kind==='pose');if(asset){const chip=this.chip(asset);if(['@形象','@服装','@姿态'].includes(part))chip.dataset.role=part;this.root.append(chip);}else this.root.append(document.createTextNode(part));}this.range=null;this.changed();}
  showMentions(){this.capture();const selection=window.getSelection();if(!selection?.rangeCount||!selection.isCollapsed||selection.anchorNode?.nodeType!==Node.TEXT_NODE){this.menu.hidden=true;return;}const node=selection.anchorNode,text=node.textContent.slice(0,selection.anchorOffset),match=text.match(/@([^@\s]*)$/);if(!match){this.menu.hidden=true;return;}const query=match[1].toLowerCase();this.options=this.getReferences().filter((item,index)=>`${item.title} ${KIND_NAMES[item.kind]} 图${index+1}`.toLowerCase().includes(query));if(!this.options.length){this.menu.hidden=true;return;}const range=document.createRange();range.setStart(node,match.index);range.setEnd(node,selection.anchorOffset);this.mentionRange=range;this.index=Math.min(this.index,this.options.length-1);this.drawMentions();}
  drawMentions(){this.media.releaseTree(this.menu);this.menu.replaceChildren();this.options.forEach((asset,index)=>{const button=create('button','mention-option');button.type='button';button.setAttribute('role','option');button.setAttribute('aria-selected',String(index===this.index));const image=create('img');image.alt='';button.append(image,create('span','',asset.title),create('small','',KIND_NAMES[asset.kind]));button.onclick=()=>this.choose(asset);this.menu.append(button);this.media.observe(image,asset.thumbnail_url||asset.image_url);});this.menu.hidden=false;}
  choose(asset){if(asset)this.insert(asset,{replaceMention:true});}
  changed(){this.capture();this.onChange?.(this.read());}
  suspend(){this.menu.hidden=true;this.media.releaseTree(this.menu);this.menu.replaceChildren();this.range=null;this.mentionRange=null;}
}
