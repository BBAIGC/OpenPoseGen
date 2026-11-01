const H3_LINK_COLORS = {
    H3_TEXT_SETTINGS: "#ffcf40",
    H3_IMAGE_SETTINGS: "#30e6ff",
    H3_VIDEO_SETTINGS: "#6bff74",
    H3_PROMPT_RULES: "#ffad54",
};

export function colorH3Connections(app, node) {
    for (const canvasClass of new Set([app.canvas?.constructor, globalThis.LGraphCanvas, globalThis.LiteGraph?.LGraphCanvas])) {
        if (canvasClass?.link_type_colors) Object.assign(canvasClass.link_type_colors, H3_LINK_COLORS);
    }
    for (const slot of [...(node?.inputs || []), ...(node?.outputs || [])]) {
        const color = H3_LINK_COLORS[slot.type];
        if (color) slot.color_on = slot.color_off = color;
    }
    const links = node?.graph?.links;
    if (links) {
        const values = typeof links.values === "function" ? links.values() : Object.values(links);
        for (const link of values) {
            if (H3_LINK_COLORS[link?.type]) link.color = H3_LINK_COLORS[link.type];
        }
    }
    node?.graph?.setDirtyCanvas?.(true, true);
}
