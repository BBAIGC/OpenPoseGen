import { app } from '/scripts/app.js';

const TYPES = new Set(['Fast Groups Muter (rgthree)', 'Fast Groups Bypasser (rgthree)']);
const instances = new WeakMap();

function isStageController(node) {
  return TYPES.has(node?.type) || TYPES.has(node?.comfyClass);
}

function isGroupRow(widget) {
  return widget?.name === 'RGTHREE_TOGGLE_AND_NAV' && widget.group && typeof widget.doModeChange === 'function';
}

function detachViews(node, state) {
  for (const view of state.views.values()) {
    const index = node.widgets.indexOf(view);
    if (index >= 0) node.widgets.splice(index, 1);
  }
}

function syncViews(node, state) {
  if (state.removed) return;
  const rows = node.widgets.filter(isGroupRow);
  for (const [row, view] of state.views) {
    if (rows.includes(row)) continue;
    // Remove the concrete widget through LiteGraph so its Vue store entry is
    // disposed as well, even when the legacy refresh removed the source row.
    if (!node.widgets.includes(view)) node.widgets.push(view);
    node.removeWidget(view);
    state.views.delete(row);
  }
  for (const row of rows) {
    row.options ||= {};
    row.options.canvasOnly = true;
    row.options.hideInPanel = true;
    let view = state.views.get(row);
    if (!view) {
      view = node.addCustomWidget({
        name: `bbaigc_stage_${state.serial++}`,
        label: String(row.group.title || row.label || '阶段'),
        type: 'boolean', value: Boolean(row.toggled), serialize: false,
        options: { serialize: false, on: '启用', off: '关闭', hideInPanel: true,
          surfaces: { canvas: 'never', vueNode: 'shown', panel: 'never' } },
        computeSize: () => [0, -4],
        callback(value) {
          // Delegate to rgthree's real group implementation, including modeOff
          // (mute/bypass), all subgraph members and toggleRestriction rules.
          runLegacy(node, state, () => row.doModeChange(Boolean(value)));
          node.graph?.setDirtyCanvas?.(true, true);
        },
      });
      state.views.set(row, view);
    } else if (!node.widgets.includes(view)) node.widgets.push(view);
    const label = String(row.group.title || row.label || '阶段');
    if (view.label !== label) view.label = label;
    const value = Boolean(row.toggled);
    if (view.value !== value) view.value = value;
  }
}

function runLegacy(node, state, callback) {
  if (state.busy || state.removed) return callback();
  state.busy = true;
  detachViews(node, state);
  try { return callback(); }
  finally { syncViews(node, state);state.busy = false; }
}

function install(node) {
  if (!isStageController(node) || instances.has(node) || typeof node.refreshWidgets !== 'function') return;
  const state = { views: new Map(), serial: 0, busy: false, removed: false };
  instances.set(node, state);
  const refresh = node.refreshWidgets;
  node.refreshWidgets = function() {
    return runLegacy(this, state, () => refresh.apply(this, arguments));
  };
  const action = node.handleAction;
  if (typeof action === 'function') node.handleAction = function() {
    return runLegacy(this, state, () => action.apply(this, arguments));
  };
  const removed = node.onRemoved;
  node.onRemoved = function() {
    state.removed = true;state.views.clear();instances.delete(this);
    return removed?.apply(this, arguments);
  };
  // Keep rgthree's original serialize_widgets=false and original custom rows.
  // New native views never enter workflow JSON or a generation API prompt.
  syncViews(node, state);
}

app.registerExtension({
  name: 'BBAIGC.Nodes2StageControls',
  nodeCreated(node) {
    install(node);
    if (isStageController(node) && !instances.has(node)) queueMicrotask(() => install(node));
  },
  loadedGraphNode: install,
  afterConfigureGraph() { for (const node of app.rootGraph?._nodes || app.graph?._nodes || []) install(node); },
  setup() { for (const node of app.rootGraph?._nodes || app.graph?._nodes || []) install(node); },
});
