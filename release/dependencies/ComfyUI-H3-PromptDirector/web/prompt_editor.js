import { app } from '/scripts/app.js';
import { hideNativeWidget } from './nodes2_dom_compat.js';
import { attachSpeechInput } from './speech_input.js';

const field = (node, name) => node.widgets?.find(widget => widget.name === name);
const editors = new WeakMap();
function installStyles() {
  if (document.getElementById('bbaigc-large-prompt-style')) return;
  const style = document.createElement('style'); style.id = 'bbaigc-large-prompt-style';
  style.textContent = `.bbaigc-large-prompt{display:flex;flex-direction:column;gap:9px;padding:10px;box-sizing:border-box;width:100%;min-height:0;overflow:hidden;color:var(--input-text,#ddd);background:var(--comfy-input-bg,#242424);font:13px "Microsoft YaHei UI",sans-serif;overscroll-behavior:contain}.bbaigc-large-prompt textarea{display:block;flex:1 1 0;min-height:140px;width:100%;box-sizing:border-box;resize:none;overflow:auto;padding:10px;border:1px solid var(--border-color,#555);border-radius:3px;background:var(--comfy-input-bg,#242424);color:var(--input-text,#ddd);font:13px/1.7 "Microsoft YaHei UI",sans-serif;white-space:pre-wrap;overscroll-behavior:contain}.bbaigc-large-prompt textarea:focus{outline:1px solid #829bd5}.bbaigc-large-prompt>.codex-speech{flex:0 0 auto;height:auto;min-height:0;margin:0;padding-top:8px}.bbaigc-external-rules{display:flex;flex-direction:column;gap:7px;box-sizing:border-box;padding:6px 0;min-height:0;overflow:hidden}.bbaigc-external-rules>span{font:12px sans-serif;color:var(--input-text,#ddd)}.bbaigc-external-rules>textarea{flex:1 1 0;min-height:260px;resize:none;overflow:auto;width:100%;box-sizing:border-box;padding:9px;border:1px solid var(--border-color,#555);background:var(--comfy-input-bg,#242424);color:var(--input-text,#ddd);font:12px/1.6 "Microsoft YaHei UI",sans-serif;white-space:pre-wrap;overscroll-behavior:contain}`;
  document.head.append(style);
}

function restoreValue(node, saved) {
  const widget = field(node, 'value'); if (!widget) return;
  const named = saved?.widgets_values_named?.value;
  const positional = saved?.widgets_values?.[0];
  const value = typeof named === 'string' ? named : typeof positional === 'string' ? positional : widget.value;
  if (typeof value === 'string') widget.value = value;
}

function installLargePrompt(node) {
  if (node.properties?.bbaigc_prompt_editor !== true || editors.has(node)) return;
  const value = field(node, 'value'); if (!value) return;
  installStyles();
  hideNativeWidget(value); value.computeSize = () => [0, -4];
  if (value.inputEl) value.inputEl.style.display = 'none';
  if (value.element) value.element.hidden = true;
  const root = document.createElement('div'); root.className = 'bbaigc-large-prompt'; root.dataset.bbaigcPanel = 'true'; root.dataset.bbaigcBoundedPanel = 'true';
  const editor = document.createElement('textarea'); editor.setAttribute('aria-label', '原始提示词'); editor.placeholder = '输入原始提示词'; editor.value = typeof value.value === 'string' ? value.value : ''; root.append(editor);
  const life = new AbortController();
  let disposed = false, frame = null, requested = null;
  const sync = () => { if (!disposed && document.activeElement !== editor && typeof value.value === 'string' && editor.value !== value.value) editor.value = value.value; };
  const write = () => { if (disposed) return; value.value = editor.value; value.callback?.(editor.value); node.graph?.setDirtyCanvas?.(true, true); };
  editor.addEventListener('input', write, { signal: life.signal });
  for (const eventName of ['pointerdown', 'pointerup', 'keydown', 'keyup', 'dblclick']) root.addEventListener(eventName, event => event.stopPropagation(), { signal: life.signal });
  root.addEventListener('wheel', event => event.stopPropagation(), { passive: true, signal: life.signal });
  const speech = attachSpeechInput(node, root, { widgetName: 'value', editor, compact: false, onText: sync });
  const updateSize = () => {
    frame = null; if (disposed) return;
    const height = Math.max(280, Math.round(Number(requested?.[1] ?? node.size?.[1] ?? 700) - 82)); requested = null;
    const px = `${height}px`;
    if (root.style.height !== px) { root.style.setProperty('height', px, 'important'); root.style.setProperty('max-height', px, 'important'); }
    root.style.setProperty('min-height', '0', 'important'); root.style.setProperty('flex', '0 0 auto', 'important'); sync();
  };
  const schedule = size => { if (size) requested = [...size]; if (!disposed && frame == null) frame = requestAnimationFrame(updateSize); };
  const dom = node.addDOMWidget('原始提示词编辑器', 'BBAIGC_LARGE_PROMPT', root, { serialize: false, canvasOnly: false, hideInPanel: true, hideOnZoom: false, getMinHeight: () => 280, getMaxHeight: () => 10000 });
  dom.serialize = false; dom.serializeValue = () => undefined; dom.computeLayoutSize = () => ({ minWidth: 300, minHeight: 280, maxHeight: Infinity });
  const observer = new ResizeObserver(() => schedule()); observer.observe(root);
  const resize = node.onResize; node.onResize = function(size) { const result = resize?.apply(this, arguments); schedule(size); return result; };
  const setSize = node.setSize; if (typeof setSize === 'function') node.setSize = function(size) { const result = setSize.apply(this, arguments); schedule(size); return result; };
  const callback = value.callback; value.callback = function() { const result = callback?.apply(this, arguments); sync(); return result; };
  const removed = node.onRemoved; node.onRemoved = function() { disposed = true; life.abort(); observer.disconnect(); if (frame != null) cancelAnimationFrame(frame); speech.dispose(); editors.delete(node); return removed?.apply(this, arguments); };
  editors.set(node, { sync, schedule }); schedule();
}

app.registerExtension({
  name: 'BBAIGC.ExternalLargePrompt',
  beforeRegisterNodeDef(type, data) {
    if (data.name !== 'PrimitiveStringMultiline') return;
    const created = type.prototype.onNodeCreated;
    type.prototype.onNodeCreated = function() { const result = created?.apply(this, arguments); installLargePrompt(this); return result; };
    const configured = type.prototype.onConfigure;
    type.prototype.onConfigure = function(saved) {
      const result = configured?.apply(this, arguments);
      if (this.properties?.bbaigc_prompt_editor !== true) return result;
      restoreValue(this, saved); installLargePrompt(this); editors.get(this)?.sync(); editors.get(this)?.schedule(saved?.size); return result;
    };
    const serialized = type.prototype.onSerialize;
    type.prototype.onSerialize = function(saved) {
      const result = serialized?.apply(this, arguments);
      if (this.properties?.bbaigc_prompt_editor !== true) return result;
      const value = field(this, 'value')?.value;
      if (typeof value === 'string') { saved.widgets_values_named = { ...saved.widgets_values_named, value }; if (Array.isArray(saved.widgets_values)) saved.widgets_values[0] = value; }
      return result;
    };
  },
});
