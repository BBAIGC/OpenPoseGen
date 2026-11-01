const widget = (node, name) => node?.widgets?.find(item => item.name === name);
const valuesOf = item => typeof item?.options?.values === 'function' ? item.options.values() : item?.options?.values;
const versionOf = name => /turbo/i.test(String(name)) ? 'turbo' : 'normal';
const familyOf = name => /krea/i.test(String(name)) ? 'krea' : /qwen|image2[._-]?1/i.test(String(name)) ? 'qwen' : '';
const element = (tag, className, text) => {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text != null) result.textContent = text;
    return result;
};

function installStyle() {
    if (document.getElementById('bba-image-sampling-style')) return;
    const sheet = element('style'); sheet.id = 'bba-image-sampling-style';
    sheet.textContent = `
    .bba-image-sampling{display:grid;gap:8px;min-width:0;margin:8px 0 12px;font:inherit;color:inherit}
    .bba-image-sampling *{box-sizing:border-box}.bba-image-sampling [hidden]{display:none!important}
    .bba-image-sampling .bba-sampling-row{display:grid;grid-template-columns:minmax(68px,34%) minmax(0,1fr);align-items:center;gap:8px;min-height:30px}
    .bba-image-sampling .bba-sampling-row>span{min-width:0;overflow-wrap:anywhere}
    .bba-image-sampling input,.bba-image-sampling select,.bba-image-sampling button{font:inherit;color:inherit;min-width:0;width:100%;min-height:30px;padding:4px 6px;border:1px solid var(--border-color,#555);border-radius:3px;background:var(--comfy-input-bg,#292929)}
    .bba-image-sampling .bba-sampling-versions{display:flex;gap:2px;min-width:0}
    .bba-image-sampling button{cursor:pointer}.bba-image-sampling button[aria-checked=true]{background:#657bc34d;border-color:#92a9ff}
    .bba-image-sampling button:disabled{opacity:.45;cursor:default}
    .bba-image-sampling input:focus-visible,.bba-image-sampling select:focus-visible,.bba-image-sampling button:focus-visible{outline:2px solid #9c81e7;outline-offset:2px}
    `;
    document.head.append(sheet);
}

// Model version is derived from the real filename. No serialized widget or
// backend schema position is added by this panel.
export function createImageSamplingControls({node, fields, modelChoices, onChange}) {
    installStyle();
    const root = element('div', 'bba-image-sampling');
    root.setAttribute('aria-label', '图像采样');
    let disposed = false, updating = false, lastModel = String(widget(node, fields.model)?.value || '');
    const controls = new Map(), versionButtons = new Map();
    const read = key => widget(node, fields[key])?.value;
    const snapshots = () => {
        node.properties ||= {};
        node.properties.codex_image_sampling_versions ||= {};
        return node.properties.codex_image_sampling_versions[fields.model] ||= {};
    };
    const capture = () => {
        const model = read('model');
        if (typeof model !== 'string' || !model) return;
        const current = {};
        for (const key of Object.keys(fields)) {
            const value = read(key);
            if (typeof value === 'string' || typeof value === 'number' && Number.isFinite(value)) current[key] = value;
        }
        snapshots()[versionOf(model)] = current;
    };
    const installed = () => {
        let candidates;
        try { candidates = modelChoices ? modelChoices() : valuesOf(widget(node, fields.model)); } catch { candidates = []; }
        const family = familyOf(read('model'));
        return [...new Set((Array.isArray(candidates) ? candidates : []).filter(name =>
            typeof name === 'string' && name && (!family || familyOf(name) === family)))];
    };
    const set = (key, value) => {
        const target = widget(node, fields[key]);
        if (!target || target.value === value) return;
        target.value = value;
        if (target.inputEl) target.inputEl.value = String(value);
        target.callback?.(value);
    };
    const changed = () => {
        capture(); lastModel = String(read('model') || '');
        node.graph?.setDirtyCanvas?.(true, true);
        onChange?.();
        refresh();
    };
    const chooseModel = (version, requested) => {
        if (disposed || updating) return;
        const candidates = installed().filter(name => versionOf(name) === version);
        if (!candidates.length) return;
        capture();
        const saved = snapshots()[version];
        const model = candidates.includes(requested) ? requested : candidates.includes(saved?.model) ? saved.model :
            candidates.find(name => /UC[-_]Q6/i.test(name)) || candidates[0];
        const previousVersion = versionOf(read('model'));
        updating = true;
        try {
            if (version !== previousVersion) {
                const defaults = {steps: version === 'turbo' ? 8 : 25, cfg: 1, sampler: 'euler', scheduler: 'simple'};
                const selected = saved || {...Object.fromEntries(Object.keys(fields).map(key => [key, read(key)])), ...defaults};
                set('model', model);
                for (const key of Object.keys(fields)) if (key !== 'model' && selected[key] != null) set(key, selected[key]);
            } else set('model', model);
        } finally { updating = false; }
        changed();
    };
    const row = (label, key, control) => {
        const wrapper = element('label', 'bba-sampling-row');
        wrapper.dataset.samplingField = key;
        wrapper.append(element('span', '', label), control); root.append(wrapper);
        control.setAttribute('aria-label', label);
        controls.set(key, control);
        return control;
    };
    const versions = element('div', 'bba-sampling-versions');
    versions.setAttribute('role', 'radiogroup'); versions.setAttribute('aria-label', '模型版本');
    row('模型版本', 'version', versions);
    for (const [version, title] of [['normal', '普通版'], ['turbo', 'Turbo']]) {
        const button = element('button', '', title); button.type = 'button'; button.setAttribute('role', 'radio');
        button.onclick = event => { event.preventDefault(); chooseModel(version); };
        versionButtons.set(version, button); versions.append(button);
    }
    versions.onkeydown = event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const available = [...versionButtons].filter(([, button]) => !button.hidden && !button.disabled);
        if (!available.length) return;
        const active = available.findIndex(([version]) => version === versionOf(read('model')));
        const index = event.key === 'Home' ? 0 : event.key === 'End' ? available.length - 1 :
            (active + (event.key === 'ArrowLeft' ? -1 : 1) + available.length) % available.length;
        chooseModel(available[index][0]); available[index][1].focus();
    };
    const model = row('模型文件', 'model', element('select'));
    model.onchange = () => chooseModel(versionOf(model.value), model.value);
    for (const [key, title] of [['steps', '步数'], ['cfg', 'CFG'], ['denoise', '降噪'], ['sampler', '采样器'], ['scheduler', '调度器'], ['seed', '种子']]) {
        if (!fields[key] || !widget(node, fields[key])) continue;
        const numeric = ['steps', 'cfg', 'denoise', 'seed'].includes(key);
        const control = row(title, key, element(numeric ? 'input' : 'select'));
        if (numeric) {
            control.type = 'number';
            const defaults = key === 'steps' ? {min: 1, max: 10000, step: 1} : key === 'denoise' ? {min: 0, max: 1, step: 0.01} :
                key === 'seed' ? {min: 0, max: 4294967295, step: 1} : {min: 0, max: 100, step: 0.1};
            const options = widget(node, fields[key]).options || {};
            for (const name of ['min', 'max', 'step']) control[name] = String(Number.isFinite(options[name]) ? options[name] : defaults[name]);
            control.onchange = () => {
                if (control.value === '' || !Number.isFinite(Number(control.value))) { refresh(); return; }
                let value = Number(control.value);
                value = Math.max(Number(control.min), Math.min(Number(control.max), value));
                if (key === 'steps' || key === 'seed') value = Math.round(value);
                set(key, value); changed();
            };
        } else control.onchange = () => { set(key, control.value); changed(); };
    }
    function syncOptions(control, choices, current) {
        const options = [...new Set(choices)];
        const signature = JSON.stringify([options, current]);
        if (control._samplingSignature !== signature) {
            control._samplingSignature = signature; control.replaceChildren();
            for (const choice of options) {
                const option = element('option', '', String(choice)); option.value = String(choice); control.append(option);
            }
            if (current != null && !options.includes(current)) {
                const option = element('option', '', `${current}（当前）`); option.value = String(current); option.disabled = true; control.append(option);
            }
        }
        if (document.activeElement !== control) control.value = String(current ?? '');
    }
    function refresh() {
        if (disposed || updating) return;
        const currentModel = String(read('model') || ''), currentVersion = versionOf(currentModel), candidates = installed();
        // External widget edits and workflow restore stay authoritative. Refresh
        // records them but never resets them to a version's default parameters.
        if (lastModel !== currentModel) lastModel = currentModel;
        capture();
        const knownVersions = new Set(candidates.map(versionOf)); knownVersions.add(currentVersion);
        for (const [version, button] of versionButtons) {
            button.hidden = familyOf(currentModel) === 'krea' && !knownVersions.has(version);
            button.disabled = !candidates.some(name => versionOf(name) === version);
            button.setAttribute('aria-checked', String(version === currentVersion));
            button.tabIndex = version === currentVersion ? 0 : -1;
        }
        syncOptions(model, candidates.filter(name => versionOf(name) === currentVersion), currentModel);
        for (const [key, control] of controls) {
            if (key === 'model' || key === 'version') continue;
            const value = read(key);
            if (control.tagName === 'SELECT') syncOptions(control, valuesOf(widget(node, fields[key])) || [], value);
            else if (document.activeElement !== control) control.value = String(value ?? '');
        }
    }
    refresh();
    return {element: root, refresh, dispose() {
        disposed = true;
        for (const control of [...controls.values(), ...versionButtons.values()]) { control.onchange = null; control.onclick = null; control.onkeydown = null; }
    }};
}
