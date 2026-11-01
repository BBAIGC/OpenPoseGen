import {app} from '/scripts/app.js';

// Connected widgets can be omitted from a legacy positional save. Keep the
// BBAPI adapter's parameters keyed by schema name so those omissions never
// move the cloud model into its resolution or quality fields.
const layoutVersion=1;
const fields={
    '提示词':{type:'string',fallback:''},
    '分辨率':{choices:['1K','2K','4K'],fallback:'1K'},
    '宽度':{type:'integer',min:16,max:16384,fallback:1024},
    '高度':{type:'integer',min:16,max:16384,fallback:1024},
    '模型':{type:'string',fallback:'自动（2.5优先）'},
    '质量':{choices:['省费','标准','精细','自动'],fallback:'省费'},
    '重新生成编号':{type:'integer',min:0,max:2147483647,fallback:0},
};
const names=Object.keys(fields);
const field=(node,name)=>node.widgets?.find(widget=>widget.name===name);
const misplacedModelWords=new Set([...fields['分辨率'].choices,...fields['质量'].choices]);
function valid(name,value){
    const spec=fields[name];
    if(spec.choices)return spec.choices.includes(value);
    if(spec.type==='integer')return Number.isSafeInteger(value)&&value>=spec.min&&value<=spec.max;
    return typeof value==='string';
}
export function repairBBAPIWidgetValues(node,saved){
    const named=saved?.widgets_values_named||{},positional=saved?.widgets_values;
    const positionalComplete=Array.isArray(positional)&&names.every((name,index)=>valid(name,positional[index]));
    const corruptLayout=!valid('分辨率',named['分辨率'])&&(!valid('质量',named['质量'])||!valid('重新生成编号',named['重新生成编号']));
    for(const[index,name]of names.entries()){
        const widget=field(node,name);if(!widget)continue;
        let value=named[name];
        if(!valid(name,value)&&positionalComplete)value=positional[index];
        if(!valid(name,value)&&!Object.hasOwn(named,name)&&valid(name,widget.value))value=widget.value;
        // Earlier four-value saves placed the only recoverable model string
        // in 分辨率. Preserve it when the actual model field is corrupted.
        if(name==='模型'){
            if(corruptLayout&&misplacedModelWords.has(value))value=undefined;
            if(!valid(name,value)&&typeof named['分辨率']==='string'&&!misplacedModelWords.has(named['分辨率']))value=named['分辨率'];
        }
        if(!valid(name,value))value=fields[name].fallback;
        widget.value=value;
        if(widget.inputEl)widget.inputEl.value=String(value);
    }
    node.properties||={};node.properties.codex_studio_widget_layout=layoutVersion;
}
function serialize(node,saved){
    const named={};
    for(const name of names){const widget=field(node,name);named[name]=valid(name,widget?.value)?widget.value:fields[name].fallback;}
    saved.widgets_values_named=named;
    saved.widgets_values=names.map(name=>named[name]);
    saved.properties||={};saved.properties.codex_studio_widget_layout=layoutVersion;
}
export function installBBAPIWidgetSerialization(node){
    if(node._codexBBAPISerializationInstalled)return;
    node._codexBBAPISerializationInstalled=true;
    const configured=node.onConfigure;
    node.onConfigure=function(saved){const result=configured?.apply(this,arguments);repairBBAPIWidgetValues(this,saved);return result;};
    const serialized=node.onSerialize;
    node.onSerialize=function(saved){serialized?.apply(this,arguments);serialize(this,saved);};
}
const singleReferenceNames=['提示词','主参考图编号','生成模型名'];
const singleReferenceValid=(name,value)=>name==='主参考图编号'?Number.isSafeInteger(value)&&value>=1&&value<=16:typeof value==='string';
const singleReferenceDefault=(node,name)=>name==='主参考图编号'?1:name==='生成模型名'?String(node.title||'').includes('Z-Image')?'Z-Image':'MOSS':'';
export function repairSingleReferenceWidgetValues(node,saved){
    const named=saved?.widgets_values_named||{},positional=saved?.widgets_values;
    const complete=Array.isArray(positional)&&singleReferenceNames.every((name,index)=>singleReferenceValid(name,positional[index]));
    for(const[index,name]of singleReferenceNames.entries()){
        const widget=field(node,name);if(!widget)continue;
        let value=named[name];
        if(!singleReferenceValid(name,value)&&complete)value=positional[index];
        if(!singleReferenceValid(name,value)&&!Object.hasOwn(named,name)&&singleReferenceValid(name,widget.value))value=widget.value;
        if(!singleReferenceValid(name,value))value=singleReferenceDefault(node,name);
        widget.value=value;if(widget.inputEl)widget.inputEl.value=String(value);
    }
}
export function installSingleReferenceWidgetSerialization(node){
    if(node._codexSingleReferenceSerializationInstalled)return;
    node._codexSingleReferenceSerializationInstalled=true;
    const configured=node.onConfigure;
    node.onConfigure=function(saved){const result=configured?.apply(this,arguments);repairSingleReferenceWidgetValues(this,saved);return result;};
    const serialized=node.onSerialize;
    node.onSerialize=function(saved){
        serialized?.apply(this,arguments);
        saved.widgets_values_named=Object.fromEntries(singleReferenceNames.map(name=>{const value=field(this,name)?.value;return[name,singleReferenceValid(name,value)?value:singleReferenceDefault(this,name)];}));
        saved.widgets_values=singleReferenceNames.map(name=>saved.widgets_values_named[name]);
    };
}
function finishingFields(data){
    const result=[];
    for(const group of ['required','optional'])for(const[name,definition]of Object.entries(data.input?.[group]||{})){
        const type=definition[0],options=definition[1]||{};
        if(options.forceInput||!Array.isArray(type)&&!['STRING','INT','FLOAT','BOOLEAN'].includes(type))continue;
        result.push({name,type,options});
    }
    return result;
}
function finishingValid(spec,value){
    if(Array.isArray(spec.type))return typeof value==='string'&&(spec.name!=='目标模型'&&/模型|编码器|VAE/i.test(spec.name)||spec.type.includes(value))||spec.type.includes(value);
    if(spec.type==='STRING')return typeof value==='string';
    if(spec.type==='BOOLEAN')return typeof value==='boolean';
    if(!Number.isFinite(value)||spec.type==='INT'&&!Number.isSafeInteger(value))return false;
    return (spec.options.min==null||value>=spec.options.min)&&(spec.options.max==null||value<=spec.options.max);
}
export function installFinishingWidgetSerialization(node,data){
    if(node._codexFinishingSerializationInstalled)return;
    node._codexFinishingSerializationInstalled=true;
    const specs=finishingFields(data),initial=Object.fromEntries(specs.map(spec=>[spec.name,field(node,spec.name)?.value]));
    const fallback=spec=>spec.name==='输入尺寸'?'1K':spec.name==='输入长边'?1024:spec.name==='输出语言'?'中文':finishingValid(spec,initial[spec.name])?initial[spec.name]:spec.options.default??(Array.isArray(spec.type)?spec.type[0]:spec.type==='STRING'?'':spec.type==='BOOLEAN'?false:0);
    const restore=saved=>{
        const named=saved?.widgets_values_named||{},array=saved?.widgets_values;
        const complete=Array.isArray(array)&&specs.every((spec,index)=>finishingValid(spec,array[index]));
        for(const[index,spec]of specs.entries()){
            const widget=field(node,spec.name);if(!widget)continue;
            let value=named[spec.name];
            if(!finishingValid(spec,value)&&complete)value=array[index];
            if(!finishingValid(spec,value)&&!Object.hasOwn(named,spec.name)&&finishingValid(spec,widget.value))value=widget.value;
            if(!finishingValid(spec,value))value=fallback(spec);
            widget.value=value;if(widget.inputEl)widget.inputEl.value=String(value);
        }
    };
    const configured=node.onConfigure;node.onConfigure=function(saved){const result=configured?.apply(this,arguments);restore(saved);return result;};
    const serialized=node.onSerialize;node.onSerialize=function(saved){
        serialized?.apply(this,arguments);
        saved.widgets_values_named=Object.fromEntries(specs.map(spec=>{const value=field(this,spec.name)?.value;return[spec.name,finishingValid(spec,value)?value:fallback(spec)];}));
        saved.widgets_values=specs.map(spec=>saved.widgets_values_named[spec.name]);
    };
}
app.registerExtension({
    name:'Codex.StudioSerialization',
    beforeRegisterNodeDef(type,data){
        if(!['CodexStudioBBAPI','CodexStudioMossPrompt','CodexStudioKreaGenerate','CodexQwenStudioRepair','CodexQwenStudioUpscale','CodexPromptOptimize','CodexStudioStageOutput','CodexImageOutput'].includes(data.name))return;
        const created=type.prototype.onNodeCreated;
        type.prototype.onNodeCreated=function(){const result=created?.apply(this,arguments);if(data.name==='CodexStudioBBAPI')installBBAPIWidgetSerialization(this);else if(data.name==='CodexStudioMossPrompt')installSingleReferenceWidgetSerialization(this);else installFinishingWidgetSerialization(this,data);return result;};
    },
});
