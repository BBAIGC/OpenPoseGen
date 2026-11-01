import {hideNativeWidget} from './nodes2_dom_compat.js';
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const FUSED_LABEL = "mystic07 · RefDelta r1024 Turbo8";
const RULE_NAMES = ["分镜规划规则", "环境与光照规则", "动作与状态规则", "多图与构图规则", "视频提示词规则", "生图提示词规则"];
const RULE_HINTS = {
    "分镜规划规则":"控制剧情如何按先后顺序拆分到各个镜头。",
    "环境与光照规则":"控制场景与光线的延续；默认以主参考图为准。",
    "动作与状态规则":"控制跨镜姿态和物件状态，避免重复已经完成的动作。",
    "多图与构图规则":"控制素材角色对应，以及各镜的景别、机位与构图。",
    "视频提示词规则":"控制视频动作与声音描述；对白仍以原稿提取结果为准。",
    "生图提示词规则":"控制静态分镜图，应只描述该镜开场的可见状态。",
};

function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    if (className) node.className = className;
    return node;
}

function styles() {
    if (document.getElementById("h3-voice-style")) return;
    const style = element("style"); style.id = "h3-voice-style";
    style.textContent = `
    .h3-voice {padding:8px 10px;box-sizing:border-box;height:100%;overflow:auto;font:13px/1.5 system-ui,sans-serif;color:var(--fg-color,#ddd);background:var(--comfy-menu-bg,#222)}
    .h3-voice *{box-sizing:border-box}.h3-voice .voice-row{display:flex;gap:6px;align-items:center;margin:7px 0}
    .h3-voice input,.h3-voice select,.h3-voice button{font:inherit;min-height:30px;min-width:0;color:inherit;border:1px solid var(--border-color,#555);border-radius:3px;background:var(--comfy-input-bg,#292929);padding:4px 6px}
    .h3-voice input,.h3-voice select{flex:1}.h3-voice button{cursor:pointer;flex:none}.h3-voice button:disabled{opacity:.5;cursor:default}
    .h3-voice .voice-role{max-width:100px}.h3-voice .voice-note{font-size:12px;opacity:.75}.h3-voice .voice-divider{border-top:1px solid var(--border-color,#555);margin-top:12px;padding-top:8px}
    .h3-voice .voice-error{color:#ed7777}.h3-rule-heading{font:600 14px/24px system-ui,sans-serif;color:var(--fg-color,#ddd);padding:0 6px;box-sizing:border-box;height:24px}
    .h3-rule-lockbar{display:flex;justify-content:flex-end;align-items:center;padding:0 6px;height:32px;box-sizing:border-box}
    .h3-rule-lockbar button{font:13px/1.4 system-ui,sans-serif;color:var(--fg-color,#ddd);background:var(--comfy-input-bg,#292929);border:1px solid var(--border-color,#555);border-radius:3px;padding:4px 12px;cursor:pointer}
    .h3-rule-locked{opacity:.72;filter:grayscale(1)}
    .h3-rules{box-sizing:border-box;display:flex;flex-direction:column;gap:10px;height:100%;min-height:0;padding:8px 10px 12px;overflow:auto;background:var(--comfy-menu-bg,#222);color:var(--fg-color,#ddd);font:13px/1.5 system-ui,sans-serif}
    .h3-rules *{box-sizing:border-box}.h3-rules .h3-rule-lockbar{flex:none;padding:0;height:32px}
    .h3-rules section{display:flex;flex-direction:column;gap:5px;flex:1 0 160px;min-height:160px}
    .h3-rules label{font-weight:600;font-size:14px;line-height:24px}
    .h3-rules textarea{display:block;width:100%;flex:1;min-height:120px;resize:vertical;border:1px solid var(--border-color,#555);border-radius:3px;padding:7px 8px;background:var(--comfy-input-bg,#292929);color:inherit;font:inherit;line-height:1.55;outline-offset:2px}
    `;
    document.head.append(style);
}

function hide(widget) {
    if (!widget) return;
    hideNativeWidget(widget);
    widget.computeSize = () => [0, -4];
    if (widget.inputEl) widget.inputEl.style.display = "none";
    if (widget.element) widget.element.style.display = "none";
}

function setupVoices(node) {
    styles();
    const modeWidget = node.widgets.find(widget => widget.name === "模式");
    const assignmentWidget = node.widgets.find(widget => widget.name === "角色分配");
    hide(modeWidget); hide(assignmentWidget);
    const root = element("div", "", "h3-voice");
    root.addEventListener("pointerdown", event => event.stopPropagation());
    root.addEventListener("keydown", event => event.stopPropagation());
    const modeRow = element("div", "", "voice-row"), mode = element("select");
    for (const text of ["模型默认", "参考音色"]) { const option = element("option", text); option.value = text; mode.append(option); }
    mode.setAttribute("aria-label", "音色模式");mode.title="模型默认由H3生成声音；参考音色使用所选录音引导角色音色。"; modeRow.append(element("strong", "音色模式"), mode);
    const hint = element("div", "音色参考仅支持RefDelta模型，每镜最多3种、合计15秒。", "voice-note");
    const assignmentRows = element("div");
    const addRow = element("button", "添加角色"); addRow.type = "button";addRow.title="新增一组角色与音色对应，可分别配置多个男生或女生。";
    const line = element("div", "", "voice-divider"); line.append(element("strong", "保存音色"));
    const nameRow = element("div", "", "voice-row"), name = element("input"), gender = element("select");
    name.placeholder = "自定义音色名称"; name.maxLength = 60; name.setAttribute("aria-label", "音色名称");name.title="给音色取一个容易辨认的名称，保存后可反复选用。";
    for (const text of ["男", "女", "未指定"]) { const option = element("option", text); option.value = text; gender.append(option); }
    gender.setAttribute("aria-label", "音色性别");gender.title="标记这段参考人声的性别，便于在音色库中选择。"; gender.style.maxWidth = "90px"; nameRow.append(name, gender);
    const fileRow = element("div", "", "voice-row"), choose = element("button", "上传人声"), save = element("button", "保存音色"), fileName = element("span", "未选择音频", "voice-note");
    choose.type = save.type = "button";choose.title="选择清晰单人录音，建议5～15秒；较长录音取前15秒。";save.title="将名称、性别与参考录音保存到音色库，不启动视频生成。";
    const file = element("input"); file.type = "file"; file.accept = "audio/*,.wav,.mp3,.flac,.ogg,.m4a"; file.hidden = true;
    choose.onclick = () => file.click();
    file.onchange = () => {fileName.textContent = file.files?.[0]?.name || "未选择音频";};
    fileRow.append(choose, fileName, save, file);
    const message = element("div", "建议5～15秒清晰人声，较长录音取前15秒。保存时无需运行工作流。", "voice-note");
    root.append(modeRow, hint, assignmentRows, addRow, line, nameRow, fileRow, message);
    let voices = [], assignments = [];
    const player = new Audio(); player.preload = "none";
    function readAssignments() {try {const value=JSON.parse(String(assignmentWidget.value||"[]"));return Array.isArray(value)?value:[];} catch {return [];} }
    function store() {
        assignmentWidget.value = JSON.stringify(assignments);
        assignmentWidget.callback?.(assignmentWidget.value);
        node.graph?.setDirtyCanvas?.(true,true);
    }
    function render() {
        mode.value = modeWidget.value || "模型默认";
        assignmentRows.replaceChildren();
        assignments.forEach((row, index) => {
            const group=element("div","","voice-row"),role=element("input"),voice=element("select"),preview=element("button","试听"),remove=element("button","移除");
            role.value=row.role||`S${index+1}`;role.className="voice-role";role.placeholder="S1 / 男生1";role.title="S1指定角色；男生1、女生1按剧本人物性别自动分配";role.setAttribute("aria-label",`角色${index+1}`);
            const blank=element("option","选择音色");blank.value="";voice.append(blank);
            for(const item of voices){const option=element("option",`${item.name} · ${item.gender}`);option.value=item.id;voice.append(option);}
            voice.value=row.voice_id||"";voice.setAttribute("aria-label",`${role.value}音色`);voice.title="选择这个角色使用的参考音色。";preview.type=remove.type="button";preview.title="试听当前选择的参考录音。";remove.title="移除这组角色分配，保留音色库中的录音。";
            preview.disabled=!row.voice_id;preview.onclick=()=>{player.pause();player.src=api.apiURL(`/h3-director/voices/${encodeURIComponent(row.voice_id)}/audio`);player.play().catch(error=>{message.textContent=error.message;});};
            role.onchange=()=>{row.role=role.value.trim();store();};voice.onchange=()=>{row.voice_id=voice.value;preview.disabled=!row.voice_id;store();};
            remove.onclick=()=>{assignments.splice(index,1);store();render();};
            group.append(role,voice,preview,remove);assignmentRows.append(group);
        });
        assignmentRows.style.opacity=mode.value==="参考音色"?"1":".6";
    }
    async function refresh() {
        try {const response=await api.fetchApi("/h3-director/voices");const data=await response.json();if(!response.ok)throw new Error(data.error||"读取音色库失败");voices=data.voices||[];render();}
        catch(error){message.textContent=error.message;message.classList.add("voice-error");}
    }
    mode.onchange=()=>{modeWidget.value=mode.value;modeWidget.callback?.(mode.value);render();};
    addRow.onclick=()=>{let ordinal=1;while(assignments.some(row=>row.role===`S${ordinal}`))ordinal++;assignments.push({role:`S${ordinal}`,voice_id:voices[0]?.id||""});store();render();};
    save.onclick=async()=>{
        const audio=file.files?.[0];
        if(!audio||!name.value.trim()){message.textContent="请填写名称并上传参考人声。";return;}
        save.disabled=true;message.classList.remove("voice-error");message.textContent="正在保存参考音色…";
        try{
            const data=new FormData();data.append("name",name.value.trim());data.append("gender",gender.value);data.append("audio",audio);
            const response=await api.fetchApi("/h3-director/voices",{method:"POST",body:data});const result=await response.json();
            if(!response.ok)throw new Error(result.error||"保存失败");
            message.textContent=`已保存“${result.voice.name}” · ${result.voice.seconds}秒`;
            await refresh();
        }catch(error){message.textContent=error.message;message.classList.add("voice-error");}
        finally{save.disabled=false;}
    };
    const widget=node.addDOMWidget("角色音色库界面","h3-voices",root,{serialize:false,hideOnZoom:false,canvasOnly:false,hideInPanel:true});
    widget.computeSize=()=>[440,440];
    node.size=[Math.max(460,node.size?.[0]||0),Math.max(490,node.size?.[1]||0)];
    const configured=node.onConfigure;
    node.onConfigure=function(){const result=configured?.apply(this,arguments);assignments=readAssignments();render();return result;};
    const removed=node.onRemoved;
    node.onRemoved=function(){player.pause();player.removeAttribute("src");player.load();return removed?.apply(this,arguments);};
    assignments=readAssignments();render();refresh();
}

function setupRuleTitles(node) {
    styles();
    node.properties ||= {};
    if(typeof node.properties.h3_prompt_rules_locked!=="boolean")node.properties.h3_prompt_rules_locked=false;
    const root=element("div","","h3-rules"),fields=[];
    root.addEventListener("pointerdown",event=>event.stopPropagation());
    root.addEventListener("keydown",event=>event.stopPropagation());
    const toolbar=element("div","","h3-rule-lockbar"),lock=element("button");lock.type="button";
    lock.title="锁定后仍可查看、选择和复制提示词";toolbar.append(lock);root.append(toolbar);
    for (const title of RULE_NAMES) {
        const field=node.widgets?.find(widget=>widget.name===title);
        if(!field)continue;
        hide(field);
        const section=element("section"),label=element("label",title),editor=element("textarea");
        editor.id=`h3-rule-${node.id??"new"}-${fields.length}-${Math.random().toString(36).slice(2,8)}`;
        label.htmlFor=editor.id;label.title=RULE_HINTS[title];editor.title=RULE_HINTS[title];editor.setAttribute("aria-label",title);editor.spellcheck=false;editor.value=String(field.value??"");
        editor.addEventListener("input",()=>{
            if(node.properties.h3_prompt_rules_locked){editor.value=String(field.value??"");return;}
            field.value=editor.value;field.callback?.(editor.value);node.graph?.setDirtyCanvas?.(true,true);
        });
        section.append(label,editor);root.append(section);fields.push({field,editor});
    }
    // One DOMWidget owns all editors. Native widget positions and serialization
    // stay unchanged on ComfyUI frontends which virtualize multiline widgets.
    const widget=node.addDOMWidget("提示词规则编辑器","h3-rules",root,{serialize:false,hideOnZoom:false,canvasOnly:false,hideInPanel:true});
    widget.computeSize=()=>[560,1180];
    function applyLock(){
        const locked=node.properties?.h3_prompt_rules_locked===true;
        lock.textContent=locked?"解锁规则":"锁定规则";lock.setAttribute("aria-pressed",String(locked));
        for(const{editor}of fields){
            editor.readOnly=locked;editor.setAttribute("aria-readonly",String(locked));editor.classList.toggle("h3-rule-locked",locked);
        }
    }
    lock.onclick=event=>{event.stopPropagation();node.properties.h3_prompt_rules_locked=!node.properties.h3_prompt_rules_locked;applyLock();node.graph?.setDirtyCanvas?.(true,true);};
    const configured=node.onConfigure;
    node.onConfigure=function(){const result=configured?.apply(this,arguments);for(const{field,editor}of fields){hide(field);editor.value=String(field.value??"");}applyLock();return result;};
    applyLock();
    node.setSize?.([Math.max(560,node.size?.[0]||0),Math.max(1280,node.size?.[1]||0)]);
}

app.registerExtension({
    name:"H3.Director.VoiceLibrary",
    async beforeRegisterNodeDef(nodeType,nodeData){
        if(nodeData.name==="H3VoiceLibrary"){
            const created=nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated=function(){const result=created?.apply(this,arguments);setupVoices(this);return result;};
        }else if(nodeData.name==="H3PromptRules"){
            const created=nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated=function(){const result=created?.apply(this,arguments);setupRuleTitles(this);return result;};
        }else if(nodeData.name==="H3VideoModelSettings"){
            const configured=nodeType.prototype.onConfigure;
            nodeType.prototype.onConfigure=function(){const result=configured?.apply(this,arguments);const field=this.widgets?.find(widget=>widget.name==="加速权重");if(field?.value==="原作者融合模型")field.value=FUSED_LABEL;return result;};
        }
    },
});
