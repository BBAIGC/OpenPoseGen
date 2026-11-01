export function fileKind(file){const name=String(file?.name||'').toLowerCase();return /\.(txt|md|markdown|text)$/.test(name)?'script':name.endsWith('.json')?'plan':'';}
export function decodeScript(buffer){
 const bytes=new Uint8Array(buffer);let text;
 if(bytes[0]===255&&bytes[1]===254)text=new TextDecoder('utf-16le').decode(bytes);
 else if(bytes[0]===254&&bytes[1]===255)text=new TextDecoder('utf-16be').decode(bytes);
 else{try{text=new TextDecoder('utf-8',{fatal:true}).decode(bytes);}catch{text=new TextDecoder('gb18030',{fatal:true}).decode(bytes);}}
 text=text.replace(/^\uFEFF/,'');if(!text.trim())throw Error('剧本文件为空。');if(text.length>12000)throw Error('原始剧本最多12000字，请缩短后导入。');return text;
}
export function downloadPlan(payload){
 const blob=new Blob([JSON.stringify(payload,null,2)],{type:'application/json;charset=utf-8'}),url=URL.createObjectURL(blob),link=document.createElement('a');
 const stamp=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',dateStyle:'short',timeStyle:'medium'}).format(new Date()).replace(/\D/g,'');
 link.href=url;link.download=`H3分镜计划-${stamp}.json`;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
export function updateIdentity(previous,incoming,armed=false){
 const same=previous?.run_id===incoming.run_id;
 const changed=!!(same&&previous?.execution_id&&incoming.execution_id&&previous.execution_id!==incoming.execution_id);
 const obsolete=!!(same&&!changed&&incoming.execution_id&&previous?.execution_id===incoming.execution_id&&Number.isFinite(previous.progress_seq)&&Number.isFinite(incoming.progress_seq)&&incoming.progress_seq<previous.progress_seq);
 return {same,changed,obsolete,newAttempt:changed||armed&&['planning','planned'].includes(incoming.stage)};
}
