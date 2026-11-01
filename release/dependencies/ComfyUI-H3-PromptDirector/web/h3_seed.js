const MAX_SEED = 0xffffffff;

function seedWidget(node) {
    return node?.widgets?.find(widget => widget?.name === '视频种子');
}

function uint32(value) {
    if (typeof value !== 'number' && typeof value !== 'string') return null;
    if (typeof value === 'string' && !value.trim()) return null;
    const number = Number(value);
    return Number.isInteger(number) && number >= 0 && number <= MAX_SEED ? number : null;
}

function randomSeed() {
    try {
        if (globalThis.crypto?.getRandomValues) {
            return globalThis.crypto.getRandomValues(new Uint32Array(1))[0];
        }
    } catch {
        // A restricted browser context may expose crypto but reject its use.
    }
    return Math.floor(Math.random() * (MAX_SEED + 1));
}

export function seedMode(node) {
    return node?.properties?.h3_seed_mode === 'fixed' ? 'fixed' : 'random';
}

export function setSeedMode(node, mode) {
    const next = mode === 'random' ? 'random' : 'fixed';
    if (node) {
        node.properties ||= {};
        node.properties.h3_seed_mode = next;
    }
    return next;
}

export function currentH3Seed(node) {
    const widget = seedWidget(node);
    if (!widget || widget.value === -1 || widget.value === '-1') {
        return uint32(node?.widgets?.find(item => item?.name === '随机种子')?.value);
    }
    return uint32(widget.value);
}

export function setH3Seed(node, seed) {
    const next = uint32(seed);
    if (next === null) throw new RangeError('种子必须是0～4294967295之间的整数。');
    const widget = seedWidget(node);
    if (!widget) return false;
    const changed = uint32(widget.value) !== next;
    widget.value = next;
    if (widget.inputEl) widget.inputEl.value = String(next);
    node.properties ||= {};
    node.properties.h3_video_seed = next;
    return changed;
}

export function imageSeedMode(node) {
    return node?.properties?.h3_image_seed_mode === 'fixed' ? 'fixed' : 'random';
}

export function setImageSeedMode(node, mode) {
    const next = mode === 'random' ? 'random' : 'fixed';
    if (node) {
        node.properties ||= {};
        node.properties.h3_image_seed_mode = next;
    }
    return next;
}

export function currentH3ImageSeed(node) {
    return uint32(node?.widgets?.find(widget => widget?.name === '随机种子')?.value);
}

function distinctSeed(seed, randomUint32) {
    const draw = randomUint32 || randomSeed;
    let next = uint32(draw());
    if (next === null || next === seed) next = uint32(draw());
    return next === null || next === seed ? (seed + 1) >>> 0 : next;
}

export function randomH3SceneSeed(seed, randomUint32) {
    const previous = uint32(seed);
    if (previous === null) throw new RangeError('单镜视频种子无效。');
    return distinctSeed(previous, randomUint32);
}

export function prepareH3ImageSeed(node, {resumeRun = '', hasSnapshot = false, randomUint32} = {}) {
    const mode = imageSeedMode(node);
    const resumeWidget = node?.widgets?.find(widget => widget?.name === '继续任务');
    const resumed = Boolean(resumeRun || resumeWidget?.value || hasSnapshot);
    const widget = node?.widgets?.find(item => item?.name === '随机种子');
    let seed = currentH3ImageSeed(node);
    let changed = false;
    if (mode === 'random' && !resumed && widget && seed !== null) {
        const next = distinctSeed(seed, randomUint32);
        changed = seed !== next;
        widget.value = next;
        if (widget.inputEl) widget.inputEl.value = String(next);
        seed = next;
    }
    if (node && seed !== null) {
        node.properties ||= {};
        node.properties.h3_last_image_seed = seed;
    }
    return {seed, changed, mode, resumed};
}

export function prepareH3Seed(node, {resumeRun = '', hasSnapshot = false, randomUint32} = {}) {
    const mode = seedMode(node);
    const resumeWidget = node?.widgets?.find(widget => widget?.name === '继续任务');
    const resumed = Boolean(resumeRun || resumeWidget?.value || hasSnapshot);
    const widget = seedWidget(node);
    const inherited = widget?.value === -1 || widget?.value === '-1';
    const firstVideoOverride = inherited && uint32(node?.properties?.h3_last_video_seed) === null;
    let seed = currentH3Seed(node);
    let changed = false;
    if (mode === 'random' && widget && (!resumed || firstVideoOverride) && seed !== null) {
        changed = setH3Seed(node, distinctSeed(seed, randomUint32));
        seed = currentH3Seed(node);
    }
    // Inheritance is not an independent video seed. Keep its history empty so
    // an existing run can opt into random video sampling once without replanning.
    if (node && uint32(widget?.value) !== null) {
        node.properties ||= {};
        node.properties.h3_last_video_seed = seed;
    }
    return {seed, changed, mode, resumed};
}
