import { app } from '/scripts/app.js';

const COMPARER = 'Image Comparer (rgthree)';
const NATIVE_NAME = 'bbaigc_image_compare_view';
const installed = new WeakSet();

function isComparer(node) {
  return node?.comfyClass === COMPARER || node?.type === COMPARER;
}

function mediaFromURL(value) {
  if (typeof value !== 'string' || !value) return null;
  try {
    const url = new URL(value, location.origin);
    if (url.origin !== location.origin || !/\/view$/.test(url.pathname)) return null;
    const filename = url.searchParams.get('filename');
    const type = url.searchParams.get('type') || 'output';
    if (!filename || !['input', 'output', 'temp'].includes(type)) return null;
    return { filename, type, subfolder: url.searchParams.get('subfolder') || '' };
  } catch { return null; }
}

function savedSides(node) {
  const widget = node.canvasWidget || node.widgets?.find(item => item.name === 'rgthree_comparer');
  const value = widget?.value;
  const images = Array.isArray(value) ? value : value?.images || [];
  const sides = { a_images: [], b_images: [] };
  for (const [index, item] of images.entries()) {
    const descriptor = mediaFromURL(typeof item === 'string' ? item : item?.url);
    if (!descriptor) continue;
    const name = typeof item === 'object' ? String(item?.name || '') : '';
    const side = name.startsWith('A') ? 'a_images' : name.startsWith('B') ? 'b_images' : index === 0 ? 'a_images' : 'b_images';
    sides[side].push(descriptor);
  }
  return sides;
}

function restoreComparisonOutputs(node, output) {
  if (!node.graph || node.id == null || node.id === -1) return;
  const previous = app.nodeOutputs?.[String(node.id)] || {};
  if (output && (Array.isArray(output.a_images) || Array.isArray(output.b_images))) return;
  if (!output && (previous.a_images?.length || previous.b_images?.length)) return;
  const sides = savedSides(node);
  if (!sides.a_images.length && !sides.b_images.length) return;
  // The native comparer reads nodeOutputStore, exposed by the existing
  // app.nodeOutputs setter. Restore only this node's safe local /view media;
  // no generation, file edits or fabricated second image are involved.
  app.nodeOutputs[String(node.id)] = { ...previous, ...(output || {}), ...sides };
}

function install(node) {
  if (!isComparer(node) || installed.has(node)) return;
  const legacy = node.canvasWidget || node.widgets?.find(widget => widget.name === 'rgthree_comparer');
  if (!legacy) return;
  installed.add(node);
  legacy.options ||= {};
  legacy.options.canvasOnly = true;
  legacy.options.hideInPanel = true;
  // A native Vue widget provides the maintained slider, equal stage sizing,
  // batch navigation, and single-image/empty states. The original custom
  // widget remains the sole persisted comparer widget and legacy renderer.
  node.addCustomWidget({
    name: NATIVE_NAME, type: 'imagecompare', value: [], serialize: false,
    options: { serialize: false, hideInPanel: true, surfaces: { canvas: 'never', vueNode: 'shown', panel: 'never' } },
    computeSize: () => [0, 0],
  });
  const configured = node.onConfigure;
  node.onConfigure = function() {
    const result = configured?.apply(this, arguments);
    restoreComparisonOutputs(this);
    return result;
  };
  const executed = node.onExecuted;
  node.onExecuted = function(output) {
    const result = executed?.apply(this, arguments);
    restoreComparisonOutputs(this, output);
    return result;
  };
  restoreComparisonOutputs(node);
}

app.registerExtension({
  name: 'BBAIGC.Nodes2ImageComparer',
  nodeCreated(node) {
    install(node);
    // rgthree invokes extension hooks from its base constructor, before its
    // comparer subclass creates canvasWidget. Retry after construction once.
    if (isComparer(node) && !installed.has(node)) queueMicrotask(() => install(node));
  },
  afterConfigureGraph() { for (const node of app.rootGraph?._nodes || app.graph?._nodes || []) install(node); },
  setup() { for (const node of app.rootGraph?._nodes || app.graph?._nodes || []) install(node); },
});
