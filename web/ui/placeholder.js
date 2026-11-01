const SVG_PATH=new URL('./assets/openai.svg',import.meta.url).href;
export function openAIPlaceholder(){const node=document.createElement('span');node.className='asset-image-fallback';node.setAttribute('aria-hidden','true');node.style.setProperty('--loading-symbol',`url("${SVG_PATH}")`);return node;}
