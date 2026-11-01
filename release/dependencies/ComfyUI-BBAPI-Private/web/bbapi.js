import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js";

const generators = new Set(["BBAPIPromptToImage", "BBAPIImageToImage", "BBAPIMultiReference"]);
const el = (tag, props = {}) => Object.assign(document.createElement(tag), props);
const field = (node, name) => node.widgets?.find((widget) => widget.name === name);
const value = (node, name) => field(node, name)?.value ?? "";

function button(text, action) {
  const element = el("button", { type: "button", textContent: text });
  Object.assign(element.style, { padding: "5px 8px", cursor: "pointer", color: "var(--input-text)",
    background: "var(--comfy-input-bg)", border: "1px solid var(--border-color)", borderRadius: "4px" });
  element.addEventListener("click", action);
  return element;
}
function panel() {
  const element = el("div");
  Object.assign(element.style, { padding: "7px", boxSizing: "border-box", display: "flex",
    flexDirection: "column", gap: "6px", color: "var(--input-text)", overflow: "auto",
    font: "12px sans-serif", background: "var(--comfy-input-bg)" });
  return element;
}
function dom(node, name, element, minHeight) {
  const widget = node.addDOMWidget(name, "BBAPI_PRIVATE_UI", element, {
    serialize: false, getMinHeight: () => minHeight, getMaxHeight: () => minHeight,
    hideOnZoom: true,
  });
  widget.serialize = false;
  widget.serializeValue = () => undefined;
  return widget;
}
async function localPost(path, body) {
  const response = await api.fetchApi(path, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `请求失败（${response.status}）`);
  return payload;
}
function profileControls(node) {
  const box = panel();
  const password = el("input", { type: "password", placeholder: "输入 API Key 后保存到本机", autocomplete: "off" });
  password.setAttribute("aria-label", "API Key");
  Object.assign(password.style, { padding: "7px", width: "100%", boxSizing: "border-box",
    border: "1px solid var(--border-color)", borderRadius: "4px",
    background: "var(--comfy-input-bg)", color: "var(--input-text)" });
  const status = el("div", { textContent: "尚未检查本机配置" });
  status.style.cssText = "line-height:1.5;overflow-wrap:anywhere";
  const row = el("div");
  row.style.cssText = "display:flex;gap:6px;flex-wrap:wrap";
  const profile = () => ({ profile: String(value(node, "配置名称")), base_url: String(value(node, "接口地址")),
    default_model: String(value(node, "默认模型")) });
  const save = button("保存密钥", async () => {
    if (!password.value.trim()) { status.textContent = "请先输入 API Key。"; return; }
    save.disabled = true;
    try {
      await localPost("/bbapi-private/profiles", { ...profile(), api_key: password.value.trim() });
      password.value = "";
      status.textContent = `「${profile().profile}」已加密保存；工作流只保存配置名称。`;
    } catch (error) { status.textContent = error.message; }
    finally { save.disabled = false; }
    if (!password.value) models.click();
  });
  const choices = el("select");
  choices.style.cssText = "width:100%;padding:5px;background:var(--comfy-input-bg);color:var(--input-text)";
  choices.append(el("option", { textContent: "读取后选择默认生图模型", value: "" }));
  choices.addEventListener("change", () => {
    const widget = field(node, "默认模型");
    if (widget && choices.value) {
      widget.value = choices.value;
      widget.callback?.(choices.value);
      node.graph?.setDirtyCanvas(true, true);
    }
  });
  const models = button("读取生图模型", async () => {
    models.disabled = true;
    status.textContent = "正在读取模型目录…";
    try {
      const payload = await localPost("/bbapi-private/models", profile());
      window.dispatchEvent(new CustomEvent('bbapi-models',{detail:payload.models||[]}));
      choices.replaceChildren(el("option", { textContent: "选择默认生图模型", value: "" }));
      for (const item of payload.models || []) {
        choices.append(el("option", { value: item.id, textContent: item.display_name ? `${item.display_name} · ${item.id}` : item.id }));
      }
      choices.value = String(value(node, "默认模型"));
      status.textContent = `已读取 ${payload.models?.length || 0} 个生图模型；此操作不生成图片。`;
    } catch (error) { status.textContent = error.message; }
    finally { models.disabled = false; }
  });
  row.append(save, models);
  const website=el('a',{textContent:'BBAPI Key 获取',href:'https://bbapi.ainivox.cn',target:'_blank',rel:'noopener noreferrer'});
  website.style.cssText='color:#98baff;text-decoration:underline';
  const offer=el('span',{textContent:'GPT2.5模型低至0.025每张'});
  box.append(website,offer,password,row,choices,status);
  dom(node, "本机密钥配置", box, 180);
  // Password text remains in this closure, never in a node property or widget value.
  const oldRemoved = node.onRemoved;
  node.onRemoved = function () { password.value = ""; return oldRemoved?.apply(this, arguments); };
  api.fetchApi("/bbapi-private/profiles").then(async (response) => {
    if (!response.ok) return;
    const payload = await response.json();
    const match = (payload.profiles || []).find((item) => item.name === value(node, "配置名称"));
    status.textContent = match ? `「${match.name}」已有本机密钥。` : "尚未保存此配置，请输入 API Key 后保存。";
    if(match)models.click();
  }).catch(() => { status.textContent = "配置尚未加载，可先保存密钥。"; });
}
function referenceControls(node) {
  const widget = field(node, "图片文件");
  if (!widget) return;
  widget.hidden = true;
  widget.computeSize = () => [0, -4];
  if (widget.inputEl) widget.inputEl.style.display = "none";
  const box = panel(), row = el("div"), grid = el("div"), status = el("div");
  row.style.cssText = "display:flex;gap:6px;flex-wrap:wrap";
  grid.style.cssText = "display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:5px;min-height:70px";
  const picker = el("input", { type: "file", multiple: true, accept: "image/png,image/jpeg,image/webp,image/bmp" });
  picker.style.display = "none";
  const names = () => {
    try { const parsed = JSON.parse(widget.value || "[]"); return Array.isArray(parsed) ? parsed : []; }
    catch { return []; }
  };
  const write = (items) => {
    widget.value = JSON.stringify(items);
    widget.callback?.(widget.value);
    render(); node.graph?.setDirtyCanvas(true, true);
  };
  const render = () => {
    const items = names(); grid.replaceChildren();
    items.forEach((name, index) => {
      const tile = el("div");
      tile.style.cssText = "min-width:0;border:1px solid var(--border-color);padding:3px";
      const image = el("img", { alt: `参考图 ${index + 1}` });
      const slash = name.lastIndexOf("/");
      image.src = api.apiURL(`/view?${new URLSearchParams({type:"input",filename:name.slice(slash+1),subfolder:slash<0?"":name.slice(0,slash)})}`);
      image.style.cssText = "width:100%;height:60px;object-fit:contain";
      const actions = el("div"); actions.style.cssText = "display:flex;justify-content:center;gap:2px";
      const left = button("‹", () => {
        if (index > 0) { [items[index-1],items[index]]=[items[index],items[index-1]]; write(items); }
      });
      left.disabled = index === 0; left.setAttribute("aria-label", "参考图前移");
      const remove = button("×", () => { items.splice(index, 1); write(items); });
      remove.setAttribute("aria-label", "移除参考图");
      actions.append(left, remove); tile.append(el("div", { textContent: String(index + 1) }), image, actions); grid.append(tile);
    });
    status.textContent = `${items.length}/16 张 · 按序作为参考图`;
  };
  const upload = async (files) => {
    const selected = Array.from(files).filter((file) => file.type.startsWith("image/"));
    if (names().length + selected.length > 16) { status.textContent = "最多16张，请先移除多余图片。"; return; }
    if (selected.reduce((sum, file) => sum + file.size, 0) > 64 * 1024 * 1024) {
      status.textContent = "本次上传超过64MB，请缩小或减少图片。"; return;
    }
    add.disabled = true;
    try {
      for (const file of selected) {
        const data = new FormData(); data.append("image", file); data.append("subfolder", "bbapi-references");
        const response = await api.fetchApi("/upload/image", { method: "POST", body: data });
        if (!response.ok) throw new Error(`图片上传失败（${response.status}）`);
        const result = await response.json(); const name = result.subfolder ? `${result.subfolder}/${result.name}` : result.name;
        write([...names(), name]);
      }
    } catch (error) { status.textContent = error.message; }
    finally { add.disabled = false; picker.value = ""; }
  };
  const add = button("上传图片", () => picker.click());
  row.append(add, button("清空", () => write([])));
  picker.addEventListener("change", () => upload(picker.files));
  box.addEventListener("dragover", (event) => { event.preventDefault(); event.stopPropagation(); });
  box.addEventListener("drop", (event) => {
    event.preventDefault(); event.stopPropagation(); if (event.dataTransfer?.files) upload(event.dataTransfer.files);
  });
  box.append(row, status, grid, picker); dom(node, "参考图片上传", box, 300);
  const oldConfigure = node.onConfigure;
  node.onConfigure = function () { const result = oldConfigure?.apply(this, arguments); requestAnimationFrame(render); return result; };
  render();
}
function reportControls(node) {
  const report = el("textarea", { readOnly: true, placeholder: "运行后显示成功数量、保存位置和失败详情" });
  report.style.cssText = "width:100%;box-sizing:border-box;resize:none;background:var(--comfy-input-bg);color:var(--input-text);font:12px/1.5 sans-serif;border:1px solid var(--border-color);padding:7px";
  dom(node, "运行报告", report, 112);
  const oldExecuted = node.onExecuted;
  node.onExecuted = function (message) {
    const result = oldExecuted?.apply(this, arguments); report.value = (message.text || []).join("\n");this._bbapiReferenceCount=message.bbapi_reference_count?.[0];return result;
  };
}
app.registerExtension({
  name: "BBAPI.Private.Local",
  beforeRegisterNodeDef(nodeType, nodeData) {
    if (!["BBAPIConnection", "BBAPIReferences", ...generators].includes(nodeData.name)) return;
    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const result = created?.apply(this, arguments);
      if (nodeData.name === "BBAPIConnection") profileControls(this);
      else if (nodeData.name === "BBAPIReferences") referenceControls(this);
      else reportControls(this);
      return result;
    };
  },
});
