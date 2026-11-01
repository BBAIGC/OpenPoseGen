export const VOSR_SCALES = [1, 2, 3, 4];
export const DLSS_SCALES = [1, 1.5, 1.724, 2, 3];
export const UPSCALE_DEFAULTS = {
    enabled:false, algorithm:"VOSR2", total_scale:2, vosr_scale:2, dlss_scale:1,
    vosr_model:"VOSR2", vosr_dtype:"bf16", seed:42, vosr_color_alignment:"wavelet",
    vosr_tile_size:512, vosr_tile_overlap:64, vosr_vae_tile_size:1024, vosr_vae_tile_overlap:128,
    dlss_style:"natural", dlss_preset:0, dlss_intensity:1, dlss_tone:1,
    dlss_structure:1.5, dlss_skin:-1, dlss_auto_mask:"on", dlss_global_tone:-1,
    dlss_detail:1, dlss_color:1,
};
const equalScale = (a,b) => Math.abs(Number(a)-Number(b)) < 0.000001;
export const scaleLabel = value => `${Number(Number(value).toFixed(3))}×`;
export function scalePairs(algorithm) {
    const dlss = algorithm === "DLSS5→VOSR2" ? DLSS_SCALES : [1];
    return VOSR_SCALES.flatMap(vosr => dlss.map(scale => ({vosr,dlss:scale,total:Number((vosr*scale).toFixed(3))})));
}
export function totalScales(algorithm) {
    return [...new Set(scalePairs(algorithm).map(pair=>pair.total))].sort((a,b)=>a-b);
}
export function readUpscaleSettings(raw) {
    try {
        const parsed=typeof raw==="string"?JSON.parse(raw||"{}"):raw;
        if(!parsed||Array.isArray(parsed)||typeof parsed!=="object")throw Error();
        return {settings:{...UPSCALE_DEFAULTS,...parsed},error:""};
    } catch {return {settings:{...UPSCALE_DEFAULTS},error:"衔接超分配置不是有效JSON，请重新选择参数。"};}
}
export function changeUpscaleSetting(settings,key,value) {
    const next={...settings,[key]:value};
    const pairs=scalePairs(next.algorithm);
    if(key==="total_scale"||key==="algorithm") {
        const matches=pairs.filter(pair=>equalScale(pair.total,next.total_scale));
        matches.sort((a,b)=>(a.vosr===settings.vosr_scale?0:1)-(b.vosr===settings.vosr_scale?0:1)||
            Math.abs(a.dlss-settings.dlss_scale)-Math.abs(b.dlss-settings.dlss_scale));
        if(matches.length){next.vosr_scale=matches[0].vosr;next.dlss_scale=matches[0].dlss;}
        else if(next.algorithm==="VOSR2")next.dlss_scale=1;
    } else if(key==="vosr_scale"||key==="dlss_scale") {
        const match=pairs.find(pair=>equalScale(pair.total,next.total_scale)&&
            (key==="vosr_scale"?equalScale(pair.vosr,value):equalScale(pair.dlss,value)));
        if(match){next.vosr_scale=match.vosr;next.dlss_scale=match.dlss;}
    }
    return next;
}
export function validateUpscaleSettings(settings) {
    if(typeof settings.enabled!=="boolean")return "衔接超分启用状态无效，请重新设置开关。";
    if(!["VOSR2","DLSS5→VOSR2"].includes(settings.algorithm))return "请选择有效的衔接超分算法。";
    if(!VOSR_SCALES.includes(settings.vosr_scale)||!DLSS_SCALES.includes(settings.dlss_scale)||
        !scalePairs(settings.algorithm).some(pair=>equalScale(pair.total,settings.total_scale)&&equalScale(pair.vosr,settings.vosr_scale)&&equalScale(pair.dlss,settings.dlss_scale)))
        return "阶段倍率无法组成当前总倍率，请调整总倍率或阶段倍率。";
    const choices={vosr_model:["VOSR2"],vosr_dtype:["default","fp16","bf16"],vosr_color_alignment:["wavelet","adain","none"],dlss_style:["default","natural","cinematic"],dlss_auto_mask:["on","off"]};
    for(const[key,values]of Object.entries(choices))if(!values.includes(settings[key]))return "超分参数选项无效，请检查高级参数。";
    const ranges={seed:[0,Number.MAX_SAFE_INTEGER,1],vosr_tile_size:[0,4096,64],vosr_tile_overlap:[0,512,8],vosr_vae_tile_size:[0,8192,64],vosr_vae_tile_overlap:[0,512,8],dlss_preset:[0,9,1],dlss_intensity:[0,2],dlss_tone:[0,2],dlss_structure:[0,2],dlss_skin:[-1,2],dlss_global_tone:[-1,2],dlss_detail:[0,2],dlss_color:[0,1]};
    for(const[key,[min,max,step]]of Object.entries(ranges))if(typeof settings[key]!=="number"||!Number.isFinite(settings[key])||settings[key]<min||settings[key]>max||step&&!equalScale(settings[key]/step,Math.round(settings[key]/step)))return "超分参数超出有效范围，请检查高级参数。";
    for(const[tile,overlap]of[[settings.vosr_tile_size,settings.vosr_tile_overlap],[settings.vosr_vae_tile_size,settings.vosr_vae_tile_overlap]])if(tile>0&&overlap>=tile)return "分块重叠必须小于对应分块尺寸。";
    return "";
}

export function createReferenceUpscalePanel({read,write,isBusy,isActive,dimensions}) {
    const el=(tag,className,text)=>{const node=document.createElement(tag);if(className)node.className=className;if(text!=null)node.textContent=text;return node;};
    const panel=el("div","h3-reference-upscale");
    const header=el("div","h3-row"),toggleLabel=el("label","h3-plan-toggle"),toggle=el("input");toggle.type="checkbox";toggle.setAttribute("role","switch");toggle.setAttribute("aria-label","启用衔接参考超分");toggleLabel.append(toggle,el("span","","启用"));header.append(el("strong","h3-grow","衔接参考超分"),toggleLabel);
    const common=el("div","h3-config h3-upscale-common"),controls=new Map(),fields=new Map();
    const advanced=el("details","h3-upscale-advanced"),advancedGrid=el("div","h3-config");advanced.append(el("summary","","超分高级参数"),advancedGrid);
    function field(key,label,options={},parent=common){
        const wrap=el("label","h3-field");wrap.append(el("span","",label));const control=el(options.choices?"select":"input");
        if(options.choices)for(const choice of options.choices)control.append(new Option(Array.isArray(choice)?choice[1]:String(choice),String(Array.isArray(choice)?choice[0]:choice)));
        else{control.type="number";control.min=String(options.min??0);control.max=String(options.max??2);control.step=String(options.step??0.05);}
        control.setAttribute("aria-label",label);if(options.title)control.title=options.title;
        control.onchange=()=>{if(isBusy())return;const parsed=readUpscaleSettings(read());const next=changeUpscaleSetting(parsed.settings,key,options.choices&&!options.numeric?control.value:Number(control.value));write(JSON.stringify(next));render();};
        controls.set(key,control);fields.set(key,wrap);wrap.append(control);parent.append(wrap);return control;
    }
    field("algorithm","算法",{choices:["VOSR2","DLSS5→VOSR2"]});
    field("total_scale","总倍率",{choices:[],numeric:true});
    field("dlss_scale","DLSS倍率",{choices:DLSS_SCALES.map(scale=>[scale,scaleLabel(scale)]),numeric:true});
    field("vosr_scale","VOSR倍率",{choices:VOSR_SCALES.map(scale=>[scale,scaleLabel(scale)]),numeric:true,title:"工作台提供1、2、3、4倍档位。"});
    field("vosr_model","VOSR模型",{choices:["VOSR2"]},advancedGrid);
    field("vosr_dtype","计算精度",{choices:[["default","默认"],["fp16","FP16"],["bf16","BF16"]]},advancedGrid);
    field("seed","超分种子",{min:0,max:Number.MAX_SAFE_INTEGER,step:1,title:"界面使用JavaScript可精确表示的非负整数。"},advancedGrid);
    field("vosr_color_alignment","色彩对齐",{choices:[["wavelet","小波"],["adain","AdaIN"],["none","关闭"]]},advancedGrid);
    for(const[key,label,min,max,step]of[["vosr_tile_size","图像分块",0,4096,64],["vosr_tile_overlap","图像重叠",0,512,8],["vosr_vae_tile_size","VAE分块",0,8192,64],["vosr_vae_tile_overlap","VAE重叠",0,512,8]])field(key,label,{min,max,step},advancedGrid);
    field("dlss_style","DLSS风格",{choices:[["default","默认"],["natural","自然"],["cinematic","电影"]]},advancedGrid);
    field("dlss_preset","DLSS预设",{min:0,max:9,step:1},advancedGrid);
    for(const[key,label,min,max]of[["dlss_intensity","修复强度",0,2],["dlss_tone","局部色调",0,2],["dlss_structure","结构",0,2],["dlss_skin","肤色",-1,2],["dlss_global_tone","整体色调",-1,2],["dlss_detail","细节",0,2],["dlss_color","色彩",0,1]])field(key,label,{min,max,step:0.05},advancedGrid);
    field("dlss_auto_mask","自动遮罩",{choices:[["on","开启"],["off","关闭"]]},advancedGrid);
    const note=el("div","h3-muted h3-upscale-note"),size=el("div","h3-muted h3-upscale-size"),error=el("div","h3-upscale-error");error.setAttribute("role","alert");
    panel.append(header,common,note,size,error,advanced);
    toggle.onchange=()=>{if(isBusy())return;const parsed=readUpscaleSettings(read());write(JSON.stringify({...parsed.settings,enabled:toggle.checked}));render();};
    function validation(){const parsed=readUpscaleSettings(read());return isActive()?(parsed.error||(parsed.settings.enabled?validateUpscaleSettings(parsed.settings):"")):"";}
    function render(){
        const parsed=readUpscaleSettings(read()),settings=parsed.settings,active=isActive(),busy=isBusy();panel.hidden=!active;toggle.checked=!!settings.enabled;toggle.disabled=busy;toggle.setAttribute("aria-checked",String(toggle.checked));
        const total=controls.get("total_scale"),totals=totalScales(settings.algorithm);total.replaceChildren(...totals.map(value=>new Option(scaleLabel(value),String(value))));if(!totals.some(value=>equalScale(value,settings.total_scale)))total.append(new Option(`${scaleLabel(settings.total_scale)}（待调整）`,String(settings.total_scale)));
        const dual=settings.algorithm==="DLSS5→VOSR2";
        for(const[key,control]of controls){control.value=String(settings[key]);control.disabled=busy||!settings.enabled;fields.get(key).hidden=key.startsWith("dlss_")&&!dual||key==="vosr_scale"&&!dual;}
        common.hidden=!settings.enabled;advanced.hidden=!settings.enabled;size.hidden=!settings.enabled;note.hidden=!settings.enabled;
        note.textContent="修复图完整参考，原始素材保留";
        const dims=dimensions?.(settings),input=dims?.input,output=dims?.output;
        size.textContent=input?.length===2?`${dims.estimatedInput?"预计输入":"输入"} ${input[0]} × ${input[1]} → 预计 ${output?.[0]??Math.round(input[0]*settings.total_scale)} × ${output?.[1]??Math.round(input[1]*settings.total_scale)} · ${scaleLabel(settings.total_scale)}（取整可能相差数像素）`:"生成分镜图后显示输入与预计超分尺寸。";
        const message=validation();error.textContent=message;error.hidden=!message;for(const key of["total_scale","vosr_scale","dlss_scale"])controls.get(key).setAttribute("aria-invalid",String(!!message));
    }
    return {element:panel,render,validate:validation,controls,toggle};
}
