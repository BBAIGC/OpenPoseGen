export function imageModels(entries){
 return (Array.isArray(entries)?entries:[]).filter(item=>item&&typeof item.id==='string'&&/image|imagen|flux|dall.?e|banana|grok-imagine/i.test(item.id)&&!/embedding|vision-preview|transcri|audio|video/i.test(item.id));
}
export function modelFamily(model){const id=String(model||'').toLowerCase();return id.includes('gpt-image')?'GPT Image':id.includes('grok')?'Grok':/gemini|banana|imagen/.test(id)?'Gemini':'其他';}
export function cloudCapabilities(model){
 const family=modelFamily(model);
 return {family,resolutions:family==='Grok'?['1K','2K']:['1K','2K','4K'],quality:family==='GPT Image',
  note:family==='GPT Image'?'按所选比例请求图像尺寸；支持质量档位。':family==='Grok'?'当前通道支持1K、2K和画幅比例。':family==='Gemini'?'按通道支持的分辨率和比例请求图像。':'参数通过兼容图像接口提交，支持范围以该通道为准。'};
}
