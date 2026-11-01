import { app } from '/scripts/app.js';
import { api } from '/scripts/api.js';

const SERVICE = 'http://127.0.0.1:8191';
const SOURCE = 'bbaigc-asset-host';
const MIME = 'application/x-bbaigc-asset';
const EXTERNAL_SESSION = crypto.randomUUID();
let pendingExternal = null;
const patchedMenus = new Map();
let overlay = null, frame = null, layout = null, token = '', pendingOpen = null;
let drag = null, dropped = false, epoch = 0;
let panelWidth = Number(localStorage.getItem('bbaigc-asset-panel-width')) || 0;
let jobTimer = null, jobSeq = 0, jobPoll = null, nativeService = null, nativeJob = null;
let optimizing = false;
const requests = new Set();
let controller = new AbortController();

async function request(path, data, method = 'POST') {
  const abort = new AbortController();
  requests.add(abort);
  const timer = setTimeout(() => abort.abort(), 30000);
  try {
    for (let attempt = 0; attempt < 2; attempt++) {
      if (data !== undefined && !token) token = (await request('/bootstrap')).token;
      const response = await fetch(`${SERVICE}/asset-library/api${path}`, {
        method: data === undefined ? 'GET' : method,
        headers: data === undefined ? {} : { 'Content-Type': 'application/json', 'X-Asset-Token': token },
        body: data === undefined ? undefined : JSON.stringify(data), signal: abort.signal,
      });
      const value = await response.json();
      if (response.status === 401 && data !== undefined && !attempt) { token = ''; continue; }
      if (!response.ok) throw new Error(value.error || '资产库请求失败');
      return value;
    }
  } finally { clearTimeout(timer); requests.delete(abort); }
}

function send(type, values = {}) {
  frame?.contentWindow?.postMessage({ source: SOURCE, type, ...values }, SERVICE);
}

function workspaceBounds() {
  let left = 0, top = 0;
  for (const element of document.querySelectorAll('[data-testid="side-toolbar"],.sidebar-content-container')) {
    const r = element.getBoundingClientRect();
    if (r.width && r.height && r.left < innerWidth * .5) left = Math.max(left, r.right);
  }
  for (const element of document.querySelectorAll('[data-testid="top-menu-actionbars"],[data-testid="action-bar-card"],.actionbar-container,[data-testid="legacy-topbar-container"],.comfy-menu,[data-testid="topbar-workflow-tabs"],.workflow-tabs-container')) {
    const r = element.getBoundingClientRect();
    if (r.width && r.height && r.top >= 0 && r.top < innerHeight * .3 && r.height < 150) top = Math.max(top, r.bottom);
  }
  return { left, top, width: innerWidth - left, height: innerHeight - top };
}

function geometry() {
  if (!overlay) return;
  const bounds = workspaceBounds();
  const width = Math.min(bounds.width, Math.max(Math.min(440, bounds.width), panelWidth || bounds.width / 2));
  Object.assign(overlay.style, { left: `${bounds.left}px`, top: `${bounds.top}px`, width: `${width}px`, height: `${bounds.height}px` });
}

function resizeHandle() {
  const handle = document.createElement('div');
  handle.id = 'bbaigc-asset-resize';
  handle.setAttribute('role', 'separator'); handle.setAttribute('aria-label', '调整资产库宽度');
  handle.setAttribute('aria-orientation', 'vertical'); handle.tabIndex = 0;
  handle.style.cssText = 'position:absolute;right:0;top:0;bottom:0;width:7px;cursor:ew-resize;z-index:5;border-right:1px solid #6668';
  handle.onpointerdown = event => {
    event.preventDefault(); handle.setPointerCapture(event.pointerId);
    overlay.dataset.resizing = 'true';
  };
  handle.onpointermove = event => {
    if (!handle.hasPointerCapture(event.pointerId)) return;
    panelWidth = Math.max(440, Math.min(workspaceBounds().width, event.clientX - workspaceBounds().left));
    geometry();
  };
  const save = () => { localStorage.setItem('bbaigc-asset-panel-width', String(panelWidth)); if (overlay) delete overlay.dataset.resizing; };
  handle.onpointerup = event => { if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId); save(); };
  handle.onlostpointercapture = save;
  handle.ondblclick = () => { panelWidth = workspaceBounds().width; geometry(); save(); };
  handle.onkeydown = event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    panelWidth = event.key === 'Home' ? workspaceBounds().width / 2 : event.key === 'End' ? workspaceBounds().width : Math.max(440, (panelWidth || workspaceBounds().width / 2) + (event.key === 'ArrowLeft' ? -32 : 32));
    geometry(); save();
  };
  return handle;
}

function placeSidebar() {
  const button = document.querySelector('[data-testid="bbaigc-asset-library-tab-button"]');
  const workflow = document.querySelector('[data-testid="codex-workflow-library-tab-button"]');
  if (button && workflow && workflow.parentElement && workflow.previousElementSibling !== button) workflow.before(button);
}

async function progressService() {
  if (nativeService) return nativeService;
  try { nativeService = (await import('/rgthree/common/prompt_service.js')).SERVICE; } catch { /* native API events still work */ }
  return nativeService;
}

async function nativeProgress(result) {
  const jobs = result.jobs || [], localJobs = jobs.filter(job => job.queue !== 'cloud' && !String(job.id || job.prompt_id).startsWith('cloud-'));
  const active = localJobs.find(job => job.status === 'running');
  const cloudCount = result.cloud_active_count || 0;
  let cloudStatus = document.getElementById('openposegen-cloud-status');
  if (cloudCount && !active) {
    if (!cloudStatus) { cloudStatus = document.createElement('div'); cloudStatus.id = 'openposegen-cloud-status'; cloudStatus.style.cssText = 'position:fixed;left:0;right:0;top:0;height:18px;z-index:9998;border-top:3px solid #40b67e;background:#181d1a;color:#cfeadd;font:12px/15px system-ui;text-align:center;pointer-events:none'; document.body.append(cloudStatus); }
    cloudStatus.textContent = `姿绘库 · 云端 ${cloudCount} 项处理中`;
  } else cloudStatus?.remove();
  const service = await progressService();
  if (active && service) {
    const execution = service.getOrMakePrompt(active.id || active.prompt_id);
    if (!execution.totalNodes) {
      const output = Object.fromEntries(Object.entries(active.node_titles || {}).map(([id, title]) => [id, { _meta: { title } }]));
      execution.setPrompt({ output });
    }
    service.currentExecution = execution;
    nativeJob = active.id || active.prompt_id;
    if (active.node) execution.executing(String(active.node), active.value ?? active.step, active.max);
    service.lastQueueRemaining = result.active_count || 0;
  }
  if (result.next_seq < jobSeq) jobSeq = 0;
  for (const event of result.events || []) {
    if (event.seq <= jobSeq) continue;
    const data = event.data || {};
    if (!['execution_start', 'executing', 'progress', 'progress_state', 'execution_cached', 'executed', 'execution_error', 'execution_interrupted', 'execution_success'].includes(event.type)) continue;
    // Mirror the real backend events into the current UI without reusing its websocket SID.
    api.dispatchEvent(new CustomEvent(event.type, { detail: event.type === 'executing' ? data.node : data }));
  }
  jobSeq = Math.max(jobSeq, result.next_seq || 0);
  if (service) {
    const execution = service.currentExecution;
    if (active && execution?.id === (active.id || active.prompt_id)) {
      // Replayed cached/executing events can repeat IDs. Preserve real node
      // completions without letting the native bar count one node twice.
      execution.executedNodeIds = [...new Set(execution.executedNodeIds)].filter(id => String(id) !== String(active.node));
      if (execution.currentlyExecuting && active.node) {
        execution.currentlyExecuting.nodeId = String(active.node);
        execution.currentlyExecuting.nodeLabel = active.node_titles?.[active.node] || execution.currentlyExecuting.nodeLabel;
        execution.currentlyExecuting.pass = 1;
        execution.currentlyExecuting.step = active.value;
        execution.currentlyExecuting.maxSteps = active.max;
      }
    }
    service.lastQueueRemaining = result.active_count || 0;
    if (!active && nativeJob && service.currentExecution?.id === nativeJob) {
      service.currentExecution.executing(null); service.currentExecution = null; nativeJob = null;
      api.dispatchEvent(new CustomEvent('executing', { detail: null }));
    }
    service.dispatchProgressUpdate();
  }
}

function watchJobs() {
  if (jobPoll) return jobPoll;
  clearTimeout(jobTimer); jobTimer = null;
  jobPoll = (async () => {
    try {
      const data = await request(`/jobs?after_seq=${jobSeq}`);
      await nativeProgress(data);
      if (data.active_count) jobTimer = setTimeout(watchJobs, document.hidden ? 1800 : 800);
    } catch { if (overlay || nativeJob) jobTimer = setTimeout(watchJobs, 3000); }
  })().finally(() => { jobPoll = null; });
  return jobPoll;
}

function selected(value) {
  const button = document.querySelector('[data-testid="bbaigc-asset-library-tab-button"]');
  button?.classList.toggle('side-bar-button-selected', value);
  button?.setAttribute('aria-expanded', String(value));
}

function close() {
  epoch++;
  send('visibility', { visible: false });
  layout?.disconnect(); layout = null;
  const previous = overlay;
  overlay = frame = null; drag = null;
  selected(false);
  if (previous) {
    previous.style.pointerEvents = 'none'; previous.style.opacity = '0';
    // Destroy the iframe after its brief exit; no hidden gallery process stays alive.
    setTimeout(() => previous.remove(), matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 160);
  }
}

async function open() {
  if (overlay) { close(); return; }
  if (pendingOpen) return pendingOpen;
  window.__codexWorkflowLibrary?.closeLibrary?.();
  const current = ++epoch;
  overlay = document.createElement('section'); overlay.id = 'bbaigc-asset-library-overlay';
  overlay.setAttribute('aria-label', '姿绘库');
  overlay.style.cssText = 'position:fixed;z-index:1310;display:flex;background:var(--comfy-menu-bg,#181818);overflow:hidden;opacity:0;transition:opacity 160ms ease;border-right:1px solid #555;box-shadow:5px 0 18px #0003';
  const loading = document.createElement('div');
  loading.style.cssText = 'margin:auto;color:var(--input-text,#ccc);font:14px system-ui';
  loading.textContent = '正在打开资产库…';
  overlay.append(loading); document.body.append(overlay); geometry(); selected(true);
  requestAnimationFrame(() => { if (overlay) overlay.style.opacity = '1'; });
  layout = new ResizeObserver(geometry);
  for (const element of document.querySelectorAll('[data-testid="side-toolbar"],.sidebar-content-container,.workflow-tabs-container,#main-panel')) layout.observe(element);
  const launch = (async () => {
    try {
      try { await request('/bootstrap'); }
      catch {
        const response = await fetch('/asset-library/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
        if (!response.ok) throw new Error('请先运行资产库目录里的“启动资产库.ps1”。首次安装后端入口将在下次正常启动 ComfyUI 时加载。');
        await request('/bootstrap');
      }
      if (current !== epoch || !overlay) return;
      frame = document.createElement('iframe'); frame.title = '姿绘库';
      frame.allow = 'microphone; clipboard-read; clipboard-write';
      frame.style.cssText = 'border:0;display:block;width:100%;height:100%;background:#181818';
      frame.src = `${SERVICE}/asset-library/?parent_origin=${encodeURIComponent(location.origin)}&external_session=${EXTERNAL_SESSION}`;
      frame.onload = () => {
        send('visibility', { visible: !document.hidden });
        send('theme', { theme: getComputedStyle(document.documentElement).colorScheme === 'light' ? 'light' : 'dark' });
        if (pendingExternal) { send('external-imported', pendingExternal); pendingExternal = null; }
      };
      overlay.replaceChildren(frame, resizeHandle());
      void watchJobs();
    } catch (error) {
      if (current !== epoch || !overlay) return;
      loading.textContent = error.message;
      const dismiss = document.createElement('button'); dismiss.textContent = '关闭'; dismiss.onclick = close;
      loading.append(document.createElement('br'), dismiss);
    }
  })().finally(() => { if (pendingOpen === launch) pendingOpen = null; });
  pendingOpen = launch; return launch;
}

function position(event) {
  const canvas = app.canvas;
  if (event && canvas?.convertEventToCanvasOffset) return canvas.convertEventToCanvasOffset(event);
  if (event && canvas?.ds?.convertCanvasToOffset) return canvas.ds.convertCanvasToOffset([event.clientX, event.clientY]);
  return [-(canvas?.ds?.offset?.[0] || 0) + 100, -(canvas?.ds?.offset?.[1] || 0) + 100];
}

async function addImage(image, event) {
  if (!image || typeof (image.name || image.filename) !== 'string') throw new Error('图片信息无效。');
  const point = position(event);
  const existing = event ? app.graph?.getNodeOnPos?.(point[0], point[1]) : null;
  const reused = existing?.type === 'LoadImage';
  const node = reused ? existing : window.LiteGraph?.createNode?.('LoadImage');
  if (!node) throw new Error('ComfyUI 加载图像节点尚未就绪。');
  let descriptor = image;
  // Output images are uploaded to input before creating a native LoadImage node.
  if (image.type && image.type !== 'input') {
    const url = new URL('/view', location.origin);
    for (const key of ['filename', 'subfolder', 'type']) url.searchParams.set(key, image[key] || '');
    const response = await fetch(url); if (!response.ok) throw new Error('生成图片尚不可读取。');
    const form = new FormData(); form.append('image', await response.blob(), image.filename); form.append('subfolder', '资产库/生成结果');
    const upload = await fetch('/upload/image', { method: 'POST', body: form });
    if (!upload.ok) throw new Error('无法将生成结果送入画布。');
    descriptor = await upload.json();
  }
  const value = [descriptor.subfolder, descriptor.name || descriptor.filename].filter(Boolean).join('/');
  if (!reused) { node.pos = point; node.title = '资产库 · 参考图'; app.graph.add(node); }
  const widget = node.widgets?.find(widget => widget.name === 'image');
  if (widget) { widget.value = value; widget.callback?.(value); }
  const preview = new Image();
  preview.onload = () => { if (node.graph) { node.imgs = [preview]; node.setSize?.([320, 360]); node.setDirtyCanvas?.(true, true); } };
  preview.src = `/view?${new URLSearchParams({ filename: descriptor.name || descriptor.filename, subfolder: descriptor.subfolder || '', type: 'input' })}`;
  node.setDirtyCanvas?.(true, true); app.canvas?.selectNode?.(node);
  return node;
}

async function addAsset(id, event, session = EXTERNAL_SESSION) { return addImage(await request(`/import/${encodeURIComponent(id)}?session=${encodeURIComponent(session)}`, {}), event); }

function nodeImages(node) {
  const values = [];
  const widget = node.widgets?.find(value => value.name === 'image');
  if (node.type === 'LoadImage' && typeof widget?.value === 'string') {
    const value = widget.value.replace(/\s+\[(?:input|output|temp)\]$/, '').replaceAll('\\', '/');
    const parts = value.split('/');
    if (parts.at(-1)) values.push({ filename: parts.pop(), subfolder: parts.join('/'), type: 'input' });
  } else {
    for (const image of node.imgs || []) {
      try {
        const url = new URL(image.src, location.origin);
        if (url.origin !== location.origin || !url.pathname.endsWith('/view')) continue;
        const descriptor = Object.fromEntries(['filename', 'subfolder', 'type'].map(key => [key, url.searchParams.get(key) || (key === 'type' ? 'output' : '')]));
        if (descriptor.filename) values.push(descriptor);
      } catch { /* nodes without stored previews remain unavailable */ }
    }
  }
  return values;
}

async function importNodes(node) {
  const selection = Object.values(app.canvas?.selected_nodes || {});
  const nodes = selection.includes(node) ? selection : [node];
  const descriptors = nodes.flatMap(nodeImages);
  const images = [...new Map(descriptors.map(image => [JSON.stringify(image), image])).values()].slice(0, 16);
  if (!images.length) throw new Error('选中的节点没有可读取图片，请先加载或生成图片。');
  const result = await request('/external/import', { session: EXTERNAL_SESSION, images, kind: 'identity' });
  const value = { session: EXTERNAL_SESSION, items: result.items };
  if (frame) send('external-imported', value);
  else { pendingExternal = value; await open(); }
}

function installNodeMenus() {
  const types = window.LiteGraph?.registered_node_types || {};
  for (const [name, type] of Object.entries(types)) {
    if (!/(?:loadimage|saveimage|previewimage)/i.test(name) || patchedMenus.has(type.prototype)) continue;
    const prototype = type.prototype, previous = prototype.getExtraMenuOptions;
    const wrapper = function (canvas, options) {
      const result = previous?.apply(this, arguments);
      const list = Array.isArray(options) ? options : Array.isArray(result) ? result : null;
      if (list && !list.some(option => option?.content === '导入资产库外接')) {
        list.push(null, { content: '导入资产库外接', disabled: !nodeImages(this).length,
          callback: () => { void importNodes(this).catch(error => app.extensionManager?.toast?.add?.({ severity: 'error', summary: '资产库', detail: error.message, life: 5000 })); } });
      }
      return result;
    };
    prototype.getExtraMenuOptions = wrapper;
    patchedMenus.set(prototype, { previous, wrapper });
  }
}

async function message(event) {
  if (event.origin !== SERVICE || event.source !== frame?.contentWindow || event.data?.source !== 'bbaigc-asset-library') return;
  const data = event.data;
  const owner = frame;
  try {
    if (data.type === 'close') close();
    else if (data.type === 'optimization-state') {
      optimizing = !!data.active;
      const existing = document.getElementById('openposegen-optimization-status');
      if (!optimizing) existing?.remove();
      else if (!existing) {
        const indicator = document.createElement('div'); indicator.id = 'openposegen-optimization-status';
        indicator.setAttribute('role', 'status'); indicator.textContent = '姿绘库 · 正在优化提示词';
        indicator.style.cssText = 'position:fixed;left:0;right:0;top:0;height:18px;z-index:9999;border-top:3px solid #40b67e;background:#181d1a;color:#cfeadd;font:12px/15px system-ui;text-align:center;pointer-events:none';
        document.body.append(indicator);
      }
    }
    else if (data.type === 'jobs-changed') { void watchJobs(); }
    else if (data.type === 'add-to-canvas' || data.type === 'copy-result' || data.type === 'drop-on-canvas') {
      const bounds = frame.getBoundingClientRect();
      const point = data.type === 'drop-on-canvas' && Number.isFinite(data.x) && Number.isFinite(data.y)
        ? { clientX: bounds.left + Math.max(0, Math.min(bounds.width, data.x)), clientY: bounds.top + Math.max(0, Math.min(bounds.height, data.y)) } : undefined;
      dropped = data.type === 'drop-on-canvas';
      if (data.asset_id) await addAsset(data.asset_id, point, data.external_session || EXTERNAL_SESSION); else await addImage(data.image, point);
      if (owner === frame) {
        if (overlay) { overlay.style.opacity = '1'; overlay.style.pointerEvents = 'auto'; }
        send('notice', { message: '已加入画布' });
      }
    } else if (data.type === 'load-workflow') {
      if (!Array.isArray(data.workflow?.nodes)) throw new Error('工作流内容无效。');
      // A new temporary workflow preserves the user's currently open workflow.
      const workflowStore = app.extensionManager?.workflow;
      const temporary = await workflowStore?.createTemporary?.('资产库快速生成');
      const loaded = await app.loadGraphData(data.workflow, true, true, temporary, { deferWarnings: true });
      if (loaded === false) throw new Error('工作流尚未载入。');
      if (owner === frame) close();
    } else if (data.type === 'drag-start') {
      drag = data; dropped = false;
      if (overlay) { overlay.style.opacity = '0'; overlay.style.pointerEvents = 'auto'; }
    } else if (data.type === 'drag-end') {
      if (!dropped && overlay) { overlay.style.opacity = '1'; overlay.style.pointerEvents = 'auto'; }
      drag = null;
    }
  } catch (error) {
    if (owner === frame && overlay) { overlay.style.opacity = '1'; overlay.style.pointerEvents = 'auto'; }
    dropped = false; drag = null;
    send('notice', { message: error.message, error: true });
  }
}

function incoming(event) {
  const raw = event.dataTransfer?.getData(MIME);
  if (raw) { try { return JSON.parse(raw); } catch { return null; } }
  const text = event.dataTransfer?.getData('text/plain') || '';
  return text.startsWith('bbaigc-asset:') ? { id: text.slice(13) } : null;
}

export function install() {
  window.__bbaigcAssetLibrary?.dispose?.();
  controller = new AbortController();
  const style = document.createElement('style'); style.id = 'bbaigc-asset-icon-style';
  const svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><g fill="none" stroke="black" stroke-width="1.65" stroke-linejoin="round" stroke-linecap="round"><path d="M5 7V4h3M16 4h3v3M19 17v3h-3M8 20H5v-3"/><circle cx="12" cy="9" r="3"/><path d="M7.5 17c.4-2.5 2-4 4.5-4s4.1 1.5 4.5 4"/></g></svg>';
  style.textContent = `.bbaigc-asset-icon{display:inline-block;width:20px;height:20px;background:currentColor;mask:url("data:image/svg+xml,${encodeURIComponent(svg)}") center/contain no-repeat}`;
  document.head.append(style);
  const manager = app.extensionManager;
  manager.unregisterSidebarTab?.('bbaigc-asset-library');
  manager.registerSidebarTab?.({ id: 'bbaigc-asset-library', title: '姿绘库', label: '姿绘库', tooltip: 'OpenPoseGen · 姿绘库', icon: 'bbaigc-asset-icon', type: 'custom', render: element => { element.textContent = ''; }, onToggle: () => { void open(); return true; } });
  installNodeMenus();
  placeSidebar();
  const sidebarObserver = new MutationObserver(() => placeSidebar());
  const rail = document.querySelector('[data-testid="sidebar-top-group"]') || document.querySelector('[data-testid="side-toolbar"]');
  if (rail) sidebarObserver.observe(rail, { childList: true, subtree: true });
  window.addEventListener('message', message, { signal: controller.signal });
  document.addEventListener('pointerdown', event => { if (overlay && !overlay.contains(event.target)) send('dismiss-popovers'); }, { capture: true, signal: controller.signal });
  window.addEventListener('resize', geometry, { signal: controller.signal });
  document.addEventListener('visibilitychange', () => send('visibility', { visible: !document.hidden }), { signal: controller.signal });
  document.addEventListener('dragover', event => { if (event.dataTransfer?.types?.includes(MIME) || drag) { event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; } }, { capture: true, signal: controller.signal });
  document.addEventListener('drop', event => {
    const value = incoming(event) || (drag ? { id: drag.asset_id, image: drag.image } : null);
    if (!value || (!value.id && !value.image)) return;
    event.preventDefault(); event.stopImmediatePropagation(); dropped = true;
    void (value.id ? addAsset(value.id, event, value.external_session || EXTERNAL_SESSION) : addImage(value.image, event)).then(() => {
      if (overlay) { overlay.style.opacity = '1'; overlay.style.pointerEvents = 'auto'; }
      drag = null;
      send('notice', { message: '已加入画布' });
    }).catch(error => { if (overlay) { overlay.style.opacity = '1'; overlay.style.pointerEvents = 'auto'; } send('notice', { message: error.message, error: true }); });
  }, { capture: true, signal: controller.signal });
  window.__bbaigcAssetLibrary = { version: '1.2.1', open, close, addAsset, importNodes, watchJobs, session: EXTERNAL_SESSION,
    dispose() { close(); clearTimeout(jobTimer); document.getElementById('openposegen-optimization-status')?.remove(); sidebarObserver.disconnect(); controller.abort(); for (const request of requests) request.abort(); style.remove();
      for (const [prototype, { previous, wrapper }] of patchedMenus) if (prototype.getExtraMenuOptions === wrapper) prototype.getExtraMenuOptions = previous;
      patchedMenus.clear(); manager.unregisterSidebarTab?.('bbaigc-asset-library'); } };
}

if (!app.extensions.some(extension => extension.name === 'BBAIGC.AssetLibrary')) {
  app.registerExtension({ name: 'BBAIGC.AssetLibrary', setup: install, nodeCreated() { installNodeMenus(); } });
}
