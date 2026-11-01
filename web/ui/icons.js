const paths={
  library:'<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="12" cy="8" r="2.5"/><path d="M6.5 19v-1.5a5.5 5.5 0 0 1 11 0V19"/>',
  identity:'<circle cx="12" cy="7" r="3.4"/><path d="M5 21v-3.4a7 7 0 0 1 14 0V21M8.3 13.2 12 16l3.7-2.8"/>',
  outfit:'<path d="M10 5.5a2 2 0 1 1 3 1.7L12 8v2l8 5a1.7 1.7 0 0 1-.9 3H4.9a1.7 1.7 0 0 1-.9-3l8-5"/>',
  pose:'<circle cx="13.5" cy="4.5" r="2"/><path d="m12.8 7.5-2.6 5 4.8 2.2-1.3 6M11.5 10l-5-1.5L4 5M11.2 11l5.3-2L20 5.5M10.2 12.5l-3.5 4L4 21"/>',
  chevron:'<path d="m9 5 7 7-7 7"/>',
  close:'<path d="m7 7 10 10M17 7 7 17"/>',
  check:'<path d="m6.5 12 3.5 3.5 7.5-8"/>'
  ,quick:'<path d="m13 2-8 12h6l-1 8 9-13h-7z"/>'
  ,generate:'<path d="m12 3 2.2 6.8L21 12l-6.8 2.2L12 21l-2.2-6.8L3 12l6.8-2.2zM20 3v4M18 5h4"/>'
};
export function icon(name,className=''){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');if(className)svg.setAttribute('class',className);svg.innerHTML=paths[name]||paths.library;return svg;}
