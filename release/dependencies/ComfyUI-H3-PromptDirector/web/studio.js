import {hideNativeWidget} from './nodes2_dom_compat.js';
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { H3_STUDIO_VERSION } from "./version.js";
import { normalizeReferenceMentions, editorPlainText, editorClipboardSelection } from "./reference_utils.js";
import {reconcileH3References,h3ReferenceTokens} from "./h3_reference_bindings.js";
import { colorH3Connections } from "./link_colors.js";
import { createReferenceUpscalePanel } from "./reference_upscale.js";
import {fileKind,decodeScript,downloadPlan,updateIdentity} from './plan_files.js';
import {attachSpeechInput} from './speech_input.js';
import {createImageSamplingControls} from './image_sampling_controls.js';
import {seedMode,setSeedMode,prepareH3Seed,currentH3Seed,setH3Seed,imageSeedMode,setImageSeedMode,currentH3ImageSeed,prepareH3ImageSeed,randomH3SceneSeed} from './h3_seed.js';

const TYPE = "H3StoryboardStudio";
const CONFIG_TYPES=new Set(["H3TextModelSettings","H3ImageModelSettings","H3VideoModelSettings"]);
const MAX_SCENES = 100;
const MAX_REFERENCES = 101;
const MODES = ["逐镜参考图", "上一镜末帧", "AI生成分镜", "手动分镜图", "末帧高清续接"];
const MODE_LABELS = ["逐镜生成", "末帧续接", "AI分镜", "手动分镜", "高清续接"];
const PROMPT_FORMATS = ["工作台格式", "H3六段式"];
const VIDEO_INPUTS = ['图生视频','文生视频'];
const VIDEO_ONLY_FIELDS=new Set(["启用加速LoRA","加速权重","加速步数","标准步数","基础模型","原作者模型","文本编码器","视频VAE","音频VAE","视频采样器","视频调度器","稀疏注意力","低显存","完成后","视频种子"]);
const STATUS = { pending: "等待中", waiting: "等待中", running: "生成中", generating: "生成中", completed: "已完成", done: "已完成", ready: "已就绪", prepared: "分镜图已就绪", failed: "失败", error: "失败", cancelled: "已取消", interrupted: "已中断", "等待":"等待中", "准备图片":"准备图片", "图片就绪":"图片已就绪", "修复末帧":"修复末帧", "生成中":"生成中", "完成":"已完成", "失败":"失败", "已取消":"已取消" };
const STAGES = { planning: "规划分镜", planned:"剧本已优化", manual_waiting:"等待手动分镜图片", images: "生成分镜图", image: "生成分镜图", images_ready:"分镜图已就绪", end_image:"生成结束图", repair:"修复末帧", restoring: "修复衔接帧", loading: "加载视频模型", video: "生成视频", joining: "合成完整视频", cleanup: "释放模型", prepared: "分镜图已就绪", completed: "已完成", done: "已完成" };

function installStyle() {
    if (document.getElementById("h3-director-style")) return;
    const style = document.createElement("style");
    style.id = "h3-director-style";
    style.textContent = `
      .h3-studio {position:relative;box-sizing:border-box;padding:10px 14px;color:var(--fg-color,#ddd);background:var(--comfy-menu-bg,#222);font:14px/1.5 system-ui,sans-serif;display:flex;flex-direction:column;overflow:hidden;height:100%;min-height:0;contain:layout style}
      .h3-studio *, .h3-studio *:before {box-sizing:border-box}
      .h3-studio[data-file-drop=true]:after {content:'释放文件导入';position:absolute;inset:8px;z-index:35;display:grid;place-items:center;background:#222b;border:2px dashed #c3af77;font-size:22px;pointer-events:none}
      .h3-studio section {padding:10px 0;border-top:1px solid var(--border-color,#555)}
      .h3-studio section:first-child {border-top:0;padding-top:0}
      .h3-studio .h3-row {display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px}
      .h3-studio .h3-duration-mode.h3-video-input {width:100%;max-width:none;flex-basis:auto;margin:0 0 10px;height:36px}
      .h3-studio .h3-video-input button {font-size:14px}
      .h3-studio[data-text-video=true] .h3-text-video-hidden {display:none!important}
      .h3-studio .h3-seed-control {display:grid;grid-template-columns:64px minmax(0,1fr);gap:8px;align-items:center;margin:8px 0}
      .h3-studio .h3-seed-control input {width:100%;min-width:0}
      .h3-studio .h3-seed-control .h3-duration-mode {width:100%;max-width:none;grid-column:2}
      .h3-studio .h3-image-sampling {min-width:0;margin:8px 0;padding:8px 0 0;border:0;border-top:1px solid var(--border-color,#555)}
      .h3-studio .h3-image-sampling>legend {padding:0 8px 0 0;font-weight:600}
      .h3-studio .h3-row:last-child {margin-bottom:0}
      .h3-studio strong {font-weight:600}
      .h3-studio button,.h3-studio select,.h3-studio input,.h3-studio textarea {font:inherit;color:inherit;border:1px solid var(--border-color,#555);border-radius:4px;background:var(--comfy-input-bg,#292929)}
      .h3-studio button {padding:5px 9px;cursor:pointer;min-height:30px}
      .h3-studio button:hover {filter:brightness(1.15)}
      .h3-studio button:disabled {opacity:.5;cursor:default}
      .h3-studio button:focus-visible,.h3-studio textarea:focus,.h3-studio select:focus,.h3-studio input:focus,.h3-studio [contenteditable=true]:focus {outline:2px solid var(--fg-color,#aaa);outline-offset:1px}
      .h3-studio select,.h3-studio input {padding:5px 7px;min-height:30px}
      .h3-studio .h3-muted {opacity:.7;font-size:12px}
      .h3-studio .h3-grow {flex:1;min-width:0}
      .h3-studio .h3-refs {display:flex;gap:8px;overflow-x:auto;padding:1px 0 6px;min-height:82px;align-items:flex-start}
      .h3-studio .h3-ref {position:relative;flex:0 0 72px;text-align:center}
      .h3-studio .h3-ref-pick {display:block;width:72px;padding:3px;background:transparent}
      .h3-studio .h3-ref img {display:block;width:64px;height:56px;object-fit:contain}
      .h3-studio .h3-remove {position:absolute;right:-3px;top:-4px;padding:0;width:21px;min-height:21px;border-radius:50%;line-height:18px}
      .h3-studio .h3-editor {position:relative}
      .h3-studio textarea {display:block;width:100%;height:150px;resize:vertical;padding:9px;line-height:1.55}
      .h3-studio .h3-rich-editor {display:block;white-space:pre-wrap;overflow-wrap:anywhere;min-height:142px;max-height:330px;overflow:auto;width:100%;padding:10px;line-height:2.35;border:1px solid var(--border-color,#555);border-radius:4px;background:var(--comfy-input-bg,#292929);outline:none}
      .h3-studio .h3-rich-editor[aria-disabled=true] {opacity:.7}
      .h3-studio .h3-inline-ref {display:inline-flex;align-items:center;vertical-align:middle;gap:5px;padding:2px 5px;margin:0 2px;line-height:1.25;border:1px solid var(--border-color,#777);border-radius:4px;white-space:nowrap;user-select:all;background:var(--comfy-menu-bg,#222)}
      .h3-studio .h3-inline-ref img {display:inline-block;object-fit:contain;width:34px;height:29px;pointer-events:none}
      .h3-studio .h3-ref-placeholder {display:inline-flex;align-items:center;justify-content:center;width:34px;height:29px;border:1px dashed var(--border-color,#777);opacity:.6}
      .h3-studio .h3-inline-ref[data-missing=true] {text-decoration:line-through;opacity:.65}
      .h3-studio .h3-inline-ref[data-phase] {position:relative;padding-right:18px}
      .h3-studio .h3-ref-state {position:absolute;right:2px;top:1px;width:13px;height:13px;display:flex;align-items:center;justify-content:center;font-size:12px;line-height:1;pointer-events:none}
      .h3-studio .h3-ref-state .h3-spinner {margin:0;width:11px;height:11px;border-width:2px;color:var(--fg-color,#ddd)}
      .h3-studio .h3-ref-state[hidden] {display:none}
      .h3-studio [data-error=true] {outline:2px solid #e74c4c;outline-offset:2px}
      .h3-studio .h3-failure {display:flex;gap:8px;align-items:flex-start;flex-wrap:wrap;margin:7px 0;padding:7px 9px;border-left:3px solid #e34848;background:#c33a3a16;color:#e05a5a;font-size:13px;line-height:1.5}
      .h3-studio .h3-failure[hidden] {display:none}
      .h3-studio .h3-failure-text {flex:1;min-width:150px;white-space:pre-wrap;overflow-wrap:anywhere}
      .h3-studio .h3-modes {display:flex;position:relative;isolation:isolate;min-width:0;width:100%;padding:3px;border:1px solid var(--border-color,#555);border-radius:5px;background:var(--comfy-input-bg,#292929)}
      .h3-studio .h3-mode-indicator {position:absolute;top:3px;bottom:3px;left:3px;width:calc((100% - 6px)/var(--h3-mode-count,5));background:color-mix(in srgb,currentColor 18%,transparent);border-radius:3px;transform:translateX(100%);transition:transform 220ms cubic-bezier(.2,.85,.2,1);z-index:-1;pointer-events:none}
      .h3-studio .h3-mode {flex:1;min-width:0;border:0;background:transparent;padding:7px 5px;white-space:nowrap}
      .h3-studio .h3-mode[aria-checked=true] {font-weight:650}
      .h3-studio .h3-optimized {height:160px;min-height:110px;max-height:400px;font-size:13px;line-height:1.6;resize:vertical}
      .h3-studio .h3-optimized:empty:before {content:"规划后显示优化剧本与全部分镜";opacity:.5;pointer-events:none}
      .h3-studio .h3-models {display:grid;grid-template-columns:1fr;gap:3px;margin-top:8px;font-size:12px;overflow-wrap:anywhere}
      .h3-studio .h3-model-line {display:flex;gap:8px;align-items:baseline}
      .h3-studio .h3-model-line strong {flex:0 0 42px}
      .h3-studio .h3-layout-bar {position:sticky;top:-10px;z-index:7;display:flex;align-items:center;gap:6px;padding:0 0 8px;background:var(--comfy-menu-bg,#222)}
      .h3-studio .h3-layout-bar button[aria-pressed=true] {font-weight:650;outline:1px solid currentColor;outline-offset:-1px}
      .h3-studio .h3-workspace {display:grid;grid-template-columns:minmax(0,1fr);gap:12px}
      .h3-studio .h3-column {min-width:0}
      .h3-studio[data-layout=horizontal] .h3-workspace {grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:20px}
      .h3-studio[data-layout=horizontal] .h3-preview {height:220px}
      .h3-studio[data-layout=horizontal] .h3-optimized {height:195px}
      .h3-studio[data-layout=horizontal][data-ai-images=true] .h3-workspace {grid-template-columns:minmax(0,1fr) minmax(0,1fr) 400px}
      .h3-studio .h3-image-column[hidden] {display:none}
      .h3-studio .h3-image-summary {margin:3px 0 8px;font-size:12px}
      .h3-studio .h3-image-total {display:block;width:100%;height:8px;accent-color:#888;margin:5px 0 9px}
      .h3-studio .h3-image-list {max-height:850px;min-height:0;overflow-y:auto;overflow-x:hidden;scrollbar-gutter:stable;scrollbar-width:auto;scrollbar-color:#888 var(--comfy-input-bg,#292929);padding:3px 5px 16px 3px;overscroll-behavior:contain}
      .h3-studio .h3-image-list::-webkit-scrollbar {width:10px}
      .h3-studio .h3-image-list::-webkit-scrollbar-thumb {background:#888;border-radius:4px}
      .h3-studio .h3-image-job {border-top:1px solid var(--border-color,#555);padding:8px 0;margin:0}
      .h3-studio .h3-image-job>summary {display:grid;grid-template-columns:65px minmax(0,1fr);gap:8px;font-size:13px;opacity:1;list-style:none}
      .h3-studio .h3-image-job>summary::-webkit-details-marker {display:none}
      .h3-studio .h3-job-thumb {display:flex;width:64px;height:56px;align-items:center;justify-content:center;overflow:hidden;border:1px solid var(--border-color,#555)}
      .h3-studio .h3-job-thumb img {width:100%;height:100%;object-fit:contain;cursor:zoom-in}
      .h3-studio .h3-job-thumb [hidden] {display:none}
      .h3-studio .h3-job-head {display:flex;justify-content:space-between;gap:8px;align-items:baseline}
      .h3-studio .h3-job-progress {display:block;width:100%;height:6px;accent-color:#888;margin:5px 0}
      .h3-studio .h3-job-progress[hidden],.h3-studio .h3-image-total[hidden] {display:none}
      .h3-studio .h3-job-inserted,.h3-studio .h3-job-fraction {font-size:12px;opacity:.75}
      .h3-studio .h3-job-prompt {font:inherit;font-size:12px;line-height:1.5;white-space:pre-wrap;overflow-wrap:anywhere;max-height:220px;overflow:auto;margin:9px 0}
      .h3-studio textarea.h3-job-prompt {height:125px;min-height:75px;width:100%;resize:vertical}
      .h3-studio .h3-job-error {white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;margin-top:5px}
      .h3-studio .h3-preview-mode[aria-pressed=true] {font-weight:650;outline:1px solid currentColor;outline-offset:-1px}
      .h3-studio .h3-all-scenes {display:flex;gap:6px;align-items:flex-start;min-width:0;max-width:100%;width:100%;overflow-x:auto;overflow-y:hidden;margin:8px 0;padding:3px 3px 10px;scrollbar-width:auto;scrollbar-color:#888 var(--comfy-input-bg,#292929)}
      .h3-studio .h3-all-scenes::-webkit-scrollbar {height:10px}
      .h3-studio .h3-all-scenes::-webkit-scrollbar-thumb {background:#888;border-radius:4px}
      .h3-studio .h3-all-scroll[hidden] {display:none}
      .h3-studio .h3-single-preview {display:grid;grid-template-columns:minmax(0,2fr) minmax(0,1fr);gap:6px;margin-top:7px}
      .h3-studio .h3-single-preview>.h3-preview {height:220px;min-width:0;margin-top:0}
      .h3-studio .h3-all-scenes[hidden],.h3-studio .h3-single-preview[hidden] {display:none}
      .h3-studio .h3-scene-row {flex:0 0 auto;min-width:0;width:var(--h3-media-width,auto);border-top:1px solid var(--border-color,#555);padding:8px 0}
      .h3-studio .h3-scene-card-head {display:flex;flex-wrap:wrap;gap:4px;align-items:center;justify-content:space-between;min-height:26px;margin-bottom:6px}
      .h3-studio .h3-scene-card-head strong {white-space:nowrap;font-size:12px}
      .h3-studio .h3-scene-card-foot {display:flex;flex-wrap:wrap;gap:4px;align-items:center;justify-content:space-between;margin-top:6px;min-height:30px}
      .h3-studio .h3-scene-card-foot button {font-size:12px;padding:3px 7px;white-space:nowrap}
      .h3-studio .h3-scene-frame {min-width:0;display:flex;align-items:center;justify-content:center;position:relative;height:220px;background:var(--comfy-input-bg,#292929);border:1px solid var(--border-color,#555);overflow:hidden}
      .h3-studio .h3-scene-frame img,.h3-studio .h3-scene-frame video {width:100%;height:100%;object-fit:contain}
      .h3-studio .h3-scene-frame [hidden] {display:none}
      .h3-studio .h3-scene-cover {position:absolute;inset:0;display:flex;flex-direction:column;gap:9px;align-items:center;justify-content:center;padding:12px 10px 62px;background:#0005;color:#fff;font-size:13px;pointer-events:none}
      .h3-studio .h3-scene-play {position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:38px;height:38px;min-height:38px;padding:0;border-radius:50%;background:#222c;color:#fff;box-shadow:0 1px 5px #0006;z-index:2;font-size:18px}
      .h3-studio .h3-scene-play[data-playing=true] {opacity:0;transition:opacity 140ms ease}
      .h3-studio .h3-scene-frame:hover .h3-scene-play[data-playing=true],.h3-studio .h3-scene-play:focus-visible {opacity:1}
      .h3-studio .h3-scene-row-status {font-size:12px;opacity:.75}
      .h3-studio .h3-time {display:inline-block;background:#f2cf55;color:#29230c;border:1px solid #d4b33b;border-radius:3px;padding:1px 5px;font-size:12px;line-height:1.5;font-variant-numeric:tabular-nums;white-space:nowrap;font-weight:600}
      .h3-studio .h3-time[hidden] {display:none}
      .h3-studio .h3-time-row {display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin:8px 0}
      .h3-studio .h3-output-path {flex:1;min-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;user-select:text}
      .h3-studio .h3-plan-toggle {display:inline-flex;gap:6px;align-items:center;font-size:13px;white-space:nowrap}
      .h3-studio .h3-plan-toggle input {width:17px;height:17px;min-height:17px;accent-color:#888}
      .h3-studio .h3-plan-toggle input[role=switch] {appearance:none;position:relative;width:34px;height:18px;min-height:18px;padding:0;border:1px solid #777;border-radius:10px;background:#555;cursor:pointer;transition:background 180ms ease}
      .h3-studio .h3-plan-toggle input[role=switch]:before {content:"";position:absolute;top:2px;left:2px;width:12px;height:12px;border-radius:50%;background:#eee;transition:transform 180ms ease}
      .h3-studio .h3-plan-toggle input[role=switch]:checked {background:#999}
      .h3-studio .h3-plan-toggle input[role=switch]:checked:before {transform:translateX(15px);background:#222}
      .h3-studio .h3-plan-toggle input[role=switch]:disabled {opacity:.5;cursor:default}
      .h3-studio .h3-regeneration {display:flex;gap:5px;align-items:center;flex-wrap:wrap;margin:8px 0}
      .h3-studio .h3-regeneration[hidden] {display:none}
      .h3-studio .h3-regeneration button[aria-pressed=true] {outline:1px solid currentColor;outline-offset:-1px;font-weight:650}
      .h3-studio [data-image-choice=true] {border:1px solid #e2bd38!important;box-shadow:0 0 0 2px #f1cc46,0 0 10px #f2ce4c66}
      .h3-studio [data-image-selectable=true] {cursor:pointer}
      .h3-studio .h3-regeneration-status {font-size:12px;opacity:.75;margin-left:auto}
      .h3-studio .h3-picker {position:absolute;left:8px;right:8px;bottom:calc(100% + 5px);z-index:5;max-height:200px;overflow:auto;border:1px solid var(--border-color,#555);padding:6px;background:var(--comfy-menu-bg,#222);box-shadow:0 3px 14px #0005}
      .h3-studio .h3-picker[hidden] {display:none}
      .h3-studio .h3-choice {display:flex;width:100%;gap:9px;align-items:center;border:0;border-radius:0;padding:5px;text-align:left}
      .h3-studio .h3-choice[aria-selected=true] {outline:1px solid var(--fg-color,#aaa);outline-offset:-1px}
      .h3-studio .h3-choice img {width:48px;height:38px;object-fit:contain;flex:none}
      .h3-studio .h3-durations {display:flex;gap:8px;overflow-x:auto;overflow-y:hidden;max-width:100%;padding:2px 0 7px;overscroll-behavior:contain;scrollbar-width:thin}.h3-studio .h3-duration {flex:none;white-space:nowrap}
      .h3-studio .h3-duration-mode {display:flex;flex:0 0 210px;position:relative;isolation:isolate;width:210px;max-width:100%;height:30px;padding:2px;border:1px solid var(--border-color,#555);border-radius:5px;background:var(--comfy-input-bg,#292929);margin-bottom:0}
      .h3-studio .h3-duration-mode button {flex:1;min-width:0;min-height:24px;background:transparent;border:0;padding:2px 5px;font-size:12px;z-index:1}
      .h3-studio .h3-duration-mode button[aria-checked=true] {font-weight:650}
      .h3-studio .h3-duration-indicator {position:absolute;top:3px;bottom:3px;left:3px;width:calc((100% - 6px)/2);background:color-mix(in srgb,currentColor 18%,transparent);border-radius:3px;transform:translateX(0);transition:transform 220ms cubic-bezier(.2,.85,.2,1);pointer-events:none}
      .h3-studio .h3-total-duration {display:flex;gap:7px;align-items:center;margin-bottom:7px}
      .h3-studio .h3-total-duration[hidden] {display:none}
      .h3-studio .h3-total-duration input {width:88px}
      .h3-studio .h3-duration {display:flex;gap:5px;align-items:center}
      .h3-studio .h3-duration input {width:55px;min-width:45px}
      .h3-studio .h3-duration button {padding:0 4px;min-height:25px}
      .h3-studio .h3-preview {display:flex;justify-content:center;align-items:center;min-height:155px;height:190px;background:var(--comfy-input-bg,#292929);border:1px solid var(--border-color,#555);margin-top:7px;overflow:hidden}
      .h3-studio .h3-preview img,.h3-studio .h3-preview video {display:block;width:100%;height:100%;object-fit:contain}
      .h3-studio .h3-preview [hidden] {display:none}
      .h3-studio .h3-frame {display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;min-width:0;width:50%;height:100%;padding:5px}
      .h3-studio .h3-frame:only-child {width:100%}
      .h3-studio .h3-frame img {min-height:0;flex:1;cursor:zoom-in}
      .h3-studio .h3-frame-label {font-size:12px;opacity:.8;flex:none}
      .h3-studio .h3-final {margin-top:9px}
      .h3-studio .h3-final video {display:block;width:auto;height:auto;max-width:100%;max-height:400px;object-fit:contain;margin:5px auto 0}
      .h3-studio .h3-final video[hidden] {display:none}
      .h3-studio .h3-final-placeholder {margin-top:5px;opacity:.75;font-size:12px}
      .h3-studio .h3-final-placeholder[hidden] {display:none}
      .h3-studio .h3-spinner {display:inline-block;width:11px;height:11px;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:h3-spin 1s linear infinite;margin-right:5px}
      .h3-studio .h3-state {white-space:pre-wrap;overflow-wrap:anywhere;min-height:19px}
      .h3-studio details {margin-top:8px}
      .h3-studio summary {cursor:pointer;font-size:12px;opacity:.8}
      .h3-studio .h3-prompt {max-height:170px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;margin:6px 0;font-size:12px}
      .h3-studio .h3-prompt-editor {height:98px;margin:5px 0;font-size:12px}
      .h3-studio .h3-config {display:grid;grid-template-columns:minmax(0,1fr);gap:6px 12px;margin-top:8px}
      .h3-studio .h3-field {display:flex;gap:6px;align-items:center;min-width:0}
      .h3-studio .h3-field[hidden] {display:none}
      .h3-studio .h3-field>span {flex:0 0 90px;font-size:12px}
      .h3-studio .h3-field input:not([type=checkbox]),.h3-studio .h3-field select {width:auto;max-width:100%;min-width:0;flex:1}.h3-studio .h3-field select {max-width:260px}
      .h3-studio .h3-field input[type=checkbox] {min-height:18px;width:18px;height:18px;accent-color:#888}
      .h3-studio .h3-aspects {display:flex;flex-wrap:wrap;gap:5px;margin-top:10px}
      .h3-studio .h3-aspect[aria-pressed=true] {font-weight:650;outline:1px solid currentColor;outline-offset:-1px}
      .h3-studio .h3-aspect-note {font-size:12px;opacity:.75;margin-top:6px;min-height:18px}
      .h3-studio .h3-image-sizing {padding-top:8px;border-top:1px solid var(--border-color,#555)}
      .h3-studio .h3-image-sizing-note {font-size:11px;opacity:.7;margin:5px 0 8px}
      .h3-studio .h3-reference-upscale {border-top:1px solid var(--border-color,#555);margin-top:10px;padding-top:9px}
      .h3-studio .h3-reference-upscale[hidden] {display:none}
      .h3-studio .h3-reference-upscale .h3-config {grid-template-columns:repeat(2,minmax(0,1fr));gap:5px 9px}
      .h3-studio .h3-reference-upscale .h3-field>span {flex:0 0 68px;white-space:normal;line-height:1.3}
      .h3-studio .h3-reference-upscale .h3-field select,.h3-studio .h3-reference-upscale .h3-field input {min-width:40px;width:75px;max-width:none;padding:4px;font-size:12px}
      .h3-studio .h3-upscale-advanced {margin-top:7px}
      .h3-studio .h3-upscale-note,.h3-studio .h3-upscale-size {font-size:11px;margin-top:5px}
      .h3-studio .h3-upscale-error,.h3-studio .h3-image-sizing-error {color:#ff8d8d;font-size:12px;margin-top:5px}
      .h3-studio .h3-reference-upscale [aria-invalid=true],.h3-studio .h3-image-sizing [aria-invalid=true] {border-color:#ef7777}
      @container (max-width:365px) {.h3-studio .h3-reference-upscale .h3-config{grid-template-columns:minmax(0,1fr)}}

      .h3-studio .h3-studio-title {font-size:28px;line-height:1.25;font-weight:750;color:#e7bd55;letter-spacing:.3px;margin-right:auto}
      .h3-studio .h3-studio-title small {font-size:17px;font-weight:600;white-space:nowrap;margin-left:10px;color:inherit;opacity:.9}
      .h3-studio .h3-layout-bar {flex-wrap:wrap;position:relative;top:0;padding:4px 0 13px}
      .h3-studio .h3-workspace {gap:0!important}
      .h3-studio[data-layout=horizontal] .h3-workspace {grid-template-columns:minmax(360px,var(--h3-source-width,360px)) 14px minmax(350px,1fr)}
      .h3-studio[data-layout=horizontal][data-ai-images=true] .h3-workspace {grid-template-columns:minmax(360px,var(--h3-source-width,360px)) 14px minmax(350px,1fr) 14px minmax(270px,var(--h3-image-width,380px))}
      .h3-studio[data-layout=vertical] .h3-column {margin-bottom:16px}
      .h3-studio .h3-splitter {position:relative;cursor:col-resize;touch-action:none;outline:none;min-height:100%;align-self:stretch}
      .h3-studio .h3-splitter:before {content:"";position:absolute;width:1px;left:50%;top:0;bottom:0;background:var(--border-color,#555);transition:width 150ms,background 150ms}
      .h3-studio .h3-splitter:hover:before,.h3-studio .h3-splitter:focus-visible:before,.h3-studio .h3-splitter[data-dragging=true]:before {width:3px;background:#d4b45f}
      .h3-studio[data-layout=vertical] .h3-splitter,.h3-studio .h3-splitter[hidden] {display:none}
      .h3-studio .h3-refs {position:relative;min-height:110px;outline:none;transition:box-shadow 160ms,background 160ms,border-color 160ms}
      .h3-studio .h3-refs[data-empty=true] {border:2px dashed var(--border-color,#777);min-height:150px;align-items:center;justify-content:center;padding:16px}
      .h3-studio .h3-ref-empty {display:flex;align-items:center;justify-content:center;flex-direction:column;gap:10px;text-align:center;width:100%;font-size:20px;line-height:1.5}
      .h3-studio .h3-ref-empty button {font-size:14px}
      .h3-studio .h3-ref-drop {display:none;position:absolute;inset:0;align-items:center;justify-content:center;z-index:3;background:#7778;backdrop-filter:blur(2px);font-size:21px;font-weight:650;pointer-events:none}
      .h3-studio .h3-refs[data-dragging=true] {border-color:#e7c764;box-shadow:inset 0 0 0 2px #e7c764,0 0 15px #e7c76488;background:#7774}
      .h3-studio .h3-refs[data-dragging=true] .h3-ref-drop {display:flex}
      .h3-studio .h3-overview-editor {position:relative}
      .h3-studio .h3-overview-editor .h3-optimized {height:195px;min-height:195px;max-height:none;resize:none;padding-top:42px;padding-bottom:42px;transition:height 320ms cubic-bezier(.22,.8,.23,1)}
      .h3-studio .h3-overview-editor .h3-dialogues {line-height:1.9;font-size:14px}
      .h3-studio .h3-overview-editor [hidden] {display:none}
      .h3-studio .h3-overview-tool {position:absolute;z-index:3;right:8px;font-size:12px;min-height:26px;padding:2px 8px;background:var(--comfy-input-bg,#292929)}
      .h3-studio .h3-dialogue-toggle {top:8px}
      .h3-studio .h3-overview-expand {bottom:8px}
      .h3-studio[data-overview-expanded=true] .h3-overview-editor .h3-optimized {height:var(--h3-overview-height,650px)}
      .h3-studio[data-overview-expanded=true] .h3-result-column {max-height:var(--h3-result-height,800px);overflow:hidden}
      .h3-studio .h3-job-head {flex-wrap:wrap;align-items:center;gap:5px}
      .h3-studio .h3-job-head>strong {margin-right:auto}
      .h3-studio .h3-job-head button {font-size:12px;padding:3px 6px;min-height:26px}
      .h3-studio .h3-job-heading {display:grid;grid-template-columns:65px minmax(0,1fr);gap:8px;font-size:13px;cursor:pointer}
      .h3-studio .h3-job-body {display:grid;grid-template-rows:0fr;opacity:0;transition:grid-template-rows 280ms cubic-bezier(.22,.8,.23,1),opacity 180ms}
      .h3-studio .h3-job-body>div {overflow:hidden;min-height:0}
      .h3-studio .h3-image-job[data-expanded=true] .h3-job-body {grid-template-rows:1fr;opacity:1}
      .h3-studio textarea.h3-job-prompt {height:330px;max-height:430px;min-height:150px}
      .h3-studio .h3-final-placeholder {display:flex;align-items:center;justify-content:center;min-height:180px;border:1px dashed var(--border-color,#555);font-size:15px;text-align:center;margin:8px auto;max-width:100%;width:320px}
      .h3-studio .h3-final-placeholder[hidden] {display:none}
      .h3-studio .h3-edge {position:absolute;z-index:12;touch-action:none}
      .h3-studio .h3-edge[data-edge=n],.h3-studio .h3-edge[data-edge=s] {left:5px;right:5px;height:5px;cursor:ns-resize}
      .h3-studio .h3-edge[data-edge=n] {top:0}.h3-studio .h3-edge[data-edge=s] {bottom:0}
      .h3-studio .h3-edge[data-edge=w],.h3-studio .h3-edge[data-edge=e] {top:5px;bottom:5px;width:5px;cursor:ew-resize}
      .h3-studio .h3-edge[data-edge=w] {left:0}.h3-studio .h3-edge[data-edge=e] {right:0}
      .h3-studio .h3-edge:hover {background:#d4b45f66}
      @media (prefers-reduced-motion:reduce) {.h3-studio .h3-overview-editor .h3-optimized,.h3-studio .h3-job-body{transition:none}}

      .h3-studio .h3-reference-hint {font-size:12px;font-weight:400;opacity:.7;white-space:normal}
      .h3-studio .h3-refs {height:110px;transition:height 320ms cubic-bezier(.22,.8,.23,1),box-shadow 160ms,background 160ms}
      .h3-studio .h3-refs[data-empty=true] {height:150px}
      .h3-studio .h3-refs[data-expanded=true] {height:var(--h3-reference-height,470px);align-items:center}
      .h3-studio .h3-refs[data-expanded=true] .h3-ref {flex-basis:256px}
      .h3-studio .h3-refs[data-expanded=true] .h3-ref-pick {width:256px}
      .h3-studio .h3-refs[data-expanded=true] .h3-ref img {width:246px;height:var(--h3-reference-image-height,290px);transition:width 320ms,height 320ms}
      .h3-studio .h3-field.h3-plan-toggle input[role=switch] {width:34px;height:18px;min-height:18px;flex:none}
      .h3-studio .h3-image-viewer {position:absolute;left:0;right:0;z-index:40;background:color-mix(in srgb,var(--comfy-menu-bg,#222) 70%,transparent);backdrop-filter:blur(14px);display:flex;align-items:center;justify-content:center;outline:none}
      .h3-studio .h3-image-viewer[hidden] {display:none}
      .h3-studio .h3-image-viewer img {display:block;max-width:calc(100% - 130px);max-height:calc(100% - 100px);object-fit:contain;box-shadow:0 3px 28px #0005}
      .h3-studio .h3-viewer-close {position:absolute;right:18px;top:16px;font-size:24px;width:42px;height:42px;padding:0}
      .h3-studio .h3-viewer-prev,.h3-studio .h3-viewer-next {position:absolute;top:50%;transform:translateY(-50%);font-size:44px;width:50px;height:76px;padding:0;background:transparent;border:0}
      .h3-studio .h3-viewer-prev {left:10px}.h3-studio .h3-viewer-next {right:10px}
      .h3-studio .h3-viewer-caption {position:absolute;left:70px;right:70px;bottom:20px;text-align:center;font-size:15px}
      @media (prefers-reduced-motion:reduce) {.h3-studio .h3-refs,.h3-studio .h3-refs img{transition:none}}
      .h3-studio .h3-refs[data-reference-error=true] {border:2px dashed #e45858;animation:h3-reference-flash 650ms ease 2}.h3-studio .h3-reference-error {color:#e45858;opacity:1}
      @keyframes h3-reference-flash {50% {box-shadow:inset 0 0 0 2px #e45858,0 0 18px #e4585866}}

      .h3-studio .h3-aspects {position:relative}
      .h3-studio .h3-aspect-picker {position:absolute;left:0;top:calc(100% + 5px);z-index:15;display:grid;grid-template-columns:minmax(170px,1fr);max-height:300px;min-width:240px;max-width:100%;overflow:auto;border:1px solid var(--border-color,#666);background:var(--comfy-menu-bg,#222);padding:5px;box-shadow:0 6px 20px #0005}
      .h3-studio .h3-aspect-picker[hidden] {display:none}
      .h3-studio .h3-aspect-choice {display:grid;grid-template-columns:52px 1fr;gap:3px 9px;align-items:center;text-align:left;border:0;border-radius:0;padding:5px;background:transparent}
      .h3-studio .h3-aspect-choice[aria-pressed=true] {outline:1px solid currentColor;outline-offset:-1px}
      .h3-studio .h3-aspect-choice img {grid-row:span 2;width:52px;height:48px;object-fit:contain}

      .h3-studio .h3-layout-bar {flex:none;margin:0;min-height:42px}
      .h3-studio .h3-workspace {flex:1;min-height:0;overflow:hidden;align-items:stretch}
      .h3-studio .h3-column {height:100%;min-height:0;overflow-y:auto;overflow-x:hidden;overscroll-behavior:contain;scrollbar-gutter:stable;scrollbar-width:auto;scrollbar-color:#888 var(--comfy-input-bg,#292929);padding:0 6px 16px 2px}
      .h3-studio .h3-column::-webkit-scrollbar {width:10px}
      .h3-studio .h3-column::-webkit-scrollbar-thumb {background:#888;border-radius:4px}
      .h3-studio .h3-source-column {container-type:inline-size}
      .h3-studio .h3-basic-config {grid-template-columns:repeat(2,minmax(0,1fr));gap:5px 9px}
      .h3-studio .h3-basic-config .h3-field {gap:4px;font-size:12px}
      .h3-studio .h3-basic-config .h3-field>span {flex:0 0 54px;white-space:normal;overflow:visible;line-height:1.35}
      .h3-studio .h3-basic-config .h3-field select,.h3-studio .h3-basic-config .h3-field input:not([type=checkbox]) {width:90px;min-width:50px;max-width:130px;padding:4px;font-size:12px}
      .h3-studio .h3-advanced-config .h3-field {flex-wrap:wrap;align-items:baseline;gap:3px 6px}
      .h3-studio .h3-advanced-config .h3-field>span {flex:0 0 90px;white-space:normal}
      .h3-studio .h3-advanced-config .h3-field select,.h3-studio .h3-advanced-config .h3-field input:not([type=checkbox]) {width:100%;max-width:none;min-width:0;flex:1 1 180px;font-size:12px}
      @container (max-width:365px) {.h3-studio .h3-basic-config {grid-template-columns:minmax(0,1fr)}.h3-studio .h3-basic-config .h3-field>span{flex-basis:70px}.h3-studio .h3-basic-config .h3-field select,.h3-studio .h3-basic-config .h3-field input:not([type=checkbox]){max-width:none}}
      .h3-studio .h3-image-list {max-height:none;overflow-y:visible;padding-right:0;scrollbar-gutter:auto}
      .h3-studio .h3-output-row {align-items:flex-start;gap:5px;margin-top:8px}
      .h3-studio .h3-output-path {flex:1 1 220px;min-width:120px;max-width:100%;white-space:normal;overflow-wrap:anywhere;line-height:1.5;overflow:visible}
      .h3-studio .h3-time-row {min-height:24px;flex:none}
      .h3-studio .h3-edge[data-edge=s] {height:9px;bottom:0;background:var(--comfy-menu-bg,#222);border-top:1px solid var(--border-color,#555)}
      .h3-studio .h3-resize-grip {display:block;position:absolute;left:50%;top:-10px;transform:translateX(-50%);width:40px;text-align:center;font-size:24px;line-height:18px;color:var(--fg-color,#ddd);letter-spacing:3px}
      .h3-studio[data-layout=vertical] .h3-workspace {overflow-y:auto;display:block}
      .h3-studio[data-layout=vertical] .h3-column {height:auto;overflow:visible;max-height:none}
      .h3-studio[data-overview-expanded=true] .h3-result-column {overflow-y:auto;max-height:none}
      .h3-studio[data-overview-expanded=true] .h3-result-column>.h3-preview-section,.h3-studio[data-overview-expanded=true] .h3-result-column>.h3-release-section {display:none}
      .h3-studio[data-compact-height=true] .h3-scene-frame,.h3-studio[data-compact-height=true] .h3-single-preview>.h3-preview {height:160px;min-height:160px}
      .h3-studio[data-compact-height=true] .h3-final-placeholder {min-height:120px}
      .h3-studio [data-error-flash=true] {animation:h3-error-flash 750ms ease 2}
      @keyframes h3-error-flash {50% {box-shadow:inset 0 0 0 2px #e45858,0 0 12px #e4585877}}

      .h3-studio .h3-job-heading {grid-template-columns:minmax(84px,30%) minmax(0,1fr);align-items:stretch;gap:9px}
      .h3-studio .h3-job-description {min-width:0}
      .h3-studio .h3-job-thumb {position:relative;width:100%;height:auto;min-height:110px;align-self:stretch}
      .h3-studio .h3-job-thumb img {position:absolute;inset:0;width:100%;height:100%;object-fit:contain}
      .h3-studio .h3-image-job[data-expanded=true] .h3-job-thumb {height:150px;align-self:start}
      .h3-studio .h3-job-head {min-width:0}
      .h3-studio .h3-job-head>strong,.h3-studio .h3-job-state {overflow-wrap:anywhere;white-space:normal}
      .h3-studio .h3-job-inserted {color:color-mix(in srgb,currentColor 45%,#82ad56);font-weight:550;opacity:1;margin-top:3px}
      .h3-studio .h3-image-job[data-expanded=true] .h3-job-inserted {display:none}
      .h3-studio textarea.h3-job-prompt {display:block;width:100%;height:36px;min-height:0;max-height:760px;margin:5px 0 0;padding:0;border:0;border-radius:0;background:transparent;line-height:1.5;font-size:12px;resize:none;overflow:hidden;transition:height 300ms cubic-bezier(.22,.8,.23,1),padding 180ms,border-color 180ms;cursor:pointer}
      .h3-studio .h3-image-job[data-expanded=true] textarea.h3-job-prompt {padding:8px;border:1px solid var(--border-color,#555);background:var(--comfy-input-bg,#292929);cursor:text}
      .h3-studio .h3-job-body .h3-row {margin-top:7px}
      @media (prefers-reduced-motion:reduce) {.h3-studio textarea.h3-job-prompt{transition:none}}

      .h3-studio .h3-result-column {container-type:inline-size}
      .h3-studio .h3-preview-deck {display:grid;grid-template-columns:minmax(0,1fr) 260px;gap:12px;align-items:start;min-width:0}
      .h3-studio .h3-shot-panel {min-width:0;border-top:1px solid var(--border-color,#555);border-bottom:1px solid var(--border-color,#555);padding:8px 0}
      .h3-studio .h3-shot-panel .h3-all-scenes {width:100%;min-width:0;max-width:100%;overflow-x:auto}
      .h3-studio .h3-final-column {min-width:0;border-left:1px solid var(--border-color,#555);padding-left:12px;align-self:start}
      .h3-studio .h3-final {margin-top:0}
      .h3-studio .h3-final .h3-row {gap:5px}
      .h3-studio .h3-final .h3-plan-toggle {font-size:12px}
      .h3-studio .h3-final-placeholder {width:100%;min-height:140px;height:140px;margin:7px 0;font-size:13px}
      .h3-studio[data-compact-height=true] .h3-final-placeholder {min-height:140px}
      .h3-studio .h3-final video {max-height:320px;max-width:100%}
      .h3-studio .h3-final-column .h3-time-row {gap:4px;margin:6px 0}
      .h3-studio .h3-final-column .h3-time {font-size:11px;padding:1px 4px}
      .h3-studio .h3-final-column .h3-output-row {gap:5px}
      .h3-studio .h3-final-column .h3-output-row button {font-size:12px;padding:4px 6px;min-height:28px;order:0}
      .h3-studio .h3-final-column .h3-output-path {order:1;flex:1 1 100%;font-size:11px;line-height:1.5}
      .h3-studio .h3-source-column {display:flex;flex-direction:column;overflow:hidden;padding-bottom:10px;scrollbar-gutter:auto}
      .h3-studio .h3-source-scroll {flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;scrollbar-gutter:stable;scrollbar-width:auto;scrollbar-color:#888 var(--comfy-input-bg,#292929);padding-right:5px;padding-bottom:10px}
      .h3-studio .h3-source-scroll::-webkit-scrollbar {width:10px}
      .h3-studio .h3-source-scroll::-webkit-scrollbar-thumb {background:#888;border-radius:4px}
      .h3-studio .h3-source-column>.h3-release-section {flex:none;min-height:36px;padding:6px 0 0;background:var(--comfy-menu-bg,#222);border-top:1px solid var(--border-color,#555)}
      .h3-studio .h3-source-column>.h3-release-section button {font-size:12px;min-height:28px;padding:4px 8px}
      .h3-studio .h3-advanced-config {grid-template-columns:repeat(2,minmax(0,1fr));gap:5px 9px}
      .h3-studio .h3-advanced-config .h3-field {flex-wrap:nowrap;font-size:12px;gap:4px;align-items:center}
      .h3-studio .h3-advanced-config .h3-field>span {flex:0 0 54px;white-space:normal;line-height:1.35}
      .h3-studio .h3-advanced-config .h3-field select,.h3-studio .h3-advanced-config .h3-field input:not([type=checkbox]) {flex:1;min-width:50px;width:90px;max-width:130px;padding:4px;font-size:12px}
      .h3-studio .h3-advanced-config .h3-field-wide {grid-column:1/-1}
      .h3-studio .h3-advanced-config .h3-field-wide select,.h3-studio .h3-advanced-config .h3-field-wide input:not([type=checkbox]) {width:100%;max-width:none;min-width:0}
      @container (max-width:590px) {.h3-studio .h3-preview-deck {grid-template-columns:minmax(0,1fr)}.h3-studio .h3-final-column {border-left:0;border-top:1px solid var(--border-color,#555);padding-left:0;padding-top:8px}.h3-studio .h3-final-column .h3-final-placeholder {max-width:300px}}
      @container (max-width:365px) {.h3-studio .h3-advanced-config {grid-template-columns:minmax(0,1fr)}.h3-studio .h3-advanced-config .h3-field select,.h3-studio .h3-advanced-config .h3-field input:not([type=checkbox]) {max-width:none}}
      .h3-studio[data-layout=vertical] .h3-source-column {display:block;overflow:visible}
      .h3-studio[data-layout=vertical] .h3-source-scroll {overflow:visible}

      .h3-studio .h3-final-column {margin-top:var(--h3-final-top-offset,0px)}
      .h3-studio .h3-final-heading {min-height:26px;margin:0;padding:0;align-items:center;flex-wrap:nowrap}
      .h3-studio .h3-final-heading strong {font-size:12px;white-space:nowrap}
      .h3-studio .h3-final-media {position:relative;display:flex;align-items:center;justify-content:center;width:var(--h3-final-frame-width,124px);height:var(--h3-final-frame-height,220px);max-width:100%;margin-top:var(--h3-final-frame-gap,6px);margin-bottom:7px;background:var(--comfy-input-bg,#292929);border:1px solid var(--border-color,#555);overflow:hidden}
      .h3-studio .h3-final-media .h3-final-placeholder,.h3-studio[data-compact-height=true] .h3-final-media .h3-final-placeholder {position:absolute;inset:0;width:100%;height:100%;min-height:0;max-width:none;margin:0;border:0;padding:5px;white-space:pre-line;font-size:12px}
      .h3-studio .h3-final-media video {display:block;width:100%;height:100%;max-width:none;max-height:none;object-fit:contain;margin:0}
      .h3-studio .h3-final-media video[hidden] {display:none}
      .h3-studio .h3-final-column>.h3-plan-toggle {margin:1px 0 5px;font-size:12px}
      @container (max-width:590px) {.h3-studio .h3-final-column {margin-top:0}.h3-studio .h3-final-media {margin-top:6px}}

      .h3-studio .h3-preview-deck {grid-template-columns:minmax(0,1fr) var(--h3-final-column-width,260px)}
      .h3-studio .h3-scene-frame,.h3-studio .h3-single-preview>.h3-preview,.h3-studio[data-compact-height=true] .h3-scene-frame,.h3-studio[data-compact-height=true] .h3-single-preview>.h3-preview {height:var(--h3-preview-frame-height,220px);min-height:0}
      @container (max-width:590px) {.h3-studio .h3-preview-deck {grid-template-columns:minmax(0,1fr)}}

      .h3-studio .h3-result-column {padding-right:0;scrollbar-gutter:auto}
      .h3-studio .h3-preview-section {border-top:0;padding-top:0}
      .h3-studio .h3-preview-deck {border-top:1px solid var(--border-color,#555);border-bottom:1px solid var(--border-color,#555)}
      .h3-studio .h3-shot-panel {border-top:0;border-bottom:0}
      .h3-studio .h3-final-column {display:flex;flex-direction:column;align-items:stretch;align-self:stretch;margin-top:0}
      .h3-studio .h3-final-column>.h3-final {flex:none;margin-top:var(--h3-final-top-offset,0px)}
      @container (max-width:590px) {.h3-studio .h3-final-column>.h3-final {margin-top:0}}

      .h3-studio .h3-source-column .h3-advanced-config .h3-field {grid-column:auto}
      .h3-studio .h3-source-column .h3-advanced-config .h3-field>span {flex-basis:54px}
      .h3-studio .h3-source-column .h3-advanced-config .h3-field select,.h3-studio .h3-source-column .h3-advanced-config .h3-field input:not([type=checkbox]) {width:90px;min-width:50px;max-width:130px}
      .h3-studio .h3-source-column .h3-refs:not([data-expanded=true]) {height:82px;min-height:82px}
      .h3-studio .h3-source-column .h3-refs[data-empty=true]:not([data-expanded=true]) {height:110px;min-height:110px}
      .h3-studio .h3-source-column[data-compact-settings=true] section {padding-top:7px;padding-bottom:7px}
      .h3-studio .h3-source-column[data-compact-settings=true] .h3-row {margin-bottom:5px}
      .h3-studio .h3-source-column[data-compact-settings=true] .h3-rich-editor {min-height:120px;line-height:2.1}
      .h3-studio .h3-source-column[data-compact-settings=true] .h3-config {gap:4px 9px;margin-top:6px}
      .h3-studio .h3-source-column[data-compact-settings=true] .h3-models {font-size:11px;gap:2px;margin-top:5px}
      .h3-studio .h3-source-column[data-compact-settings=true] .h3-aspects {margin-top:6px}
      .h3-studio .h3-preview-deck {position:relative}
      .h3-studio .h3-result-column {padding-left:0;padding-right:0}
      @container (max-width:365px) {.h3-studio .h3-source-column .h3-advanced-config .h3-field select,.h3-studio .h3-source-column .h3-advanced-config .h3-field input:not([type=checkbox]) {max-width:none}}
      .h3-studio .h3-deck-bridge {position:absolute;height:1px;background:var(--border-color,#555);pointer-events:none;z-index:8}
      .h3-studio[data-layout=vertical] .h3-deck-bridge {display:none}

      .h3-studio .h3-result-column {scrollbar-gutter:stable}
      .h3-studio .h3-shot-panel {padding-left:8px;padding-right:8px}
      .h3-studio .h3-final-column {padding-left:12px;padding-right:12px}
      .h3-studio .h3-all-scenes {padding-left:3px;padding-right:8px}
      .h3-studio .h3-image-list {padding-right:9px}
      .h3-studio .h3-scene-card-head {height:var(--h3-scene-heading-height,auto);min-height:26px;align-content:start;overflow:visible;margin-bottom:0}
      .h3-studio .h3-scene-card-head strong {white-space:normal}
      .h3-studio .h3-scene-row {display:grid;grid-template-rows:var(--h3-scene-heading-height,auto) var(--h3-preview-frame-height,220px) auto;row-gap:6px;align-content:start}
      .h3-studio .h3-scene-card-foot {display:grid;grid-template-rows:18px 26px;gap:4px;min-height:48px;margin-top:0;align-content:start;justify-content:stretch}
      .h3-studio .h3-scene-card-foot .h3-scene-row-status {white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      .h3-studio .h3-scene-card-foot button {justify-self:start;max-width:100%;overflow:hidden;text-overflow:ellipsis}
      .h3-studio .h3-optimized-bar>strong {flex:1 1 160px;min-width:140px;white-space:nowrap}
      .h3-studio .h3-final-tools {display:flex;flex-direction:column;gap:4px;padding-top:8px;min-width:0}
      .h3-studio .h3-final-tools .h3-output-row,.h3-studio .h3-final-tools .h3-time-row {margin:0;gap:4px}
      .h3-studio .h3-final-tools .h3-output-row button {flex:1;white-space:nowrap}
      .h3-studio .h3-final-tools .h3-output-path {overflow:hidden;text-overflow:ellipsis;white-space:nowrap;line-height:1.3;min-height:0}
      .h3-studio .h3-final-tools>.h3-plan-toggle {font-size:11px;white-space:normal}
      .h3-studio .h3-final-column>.h3-final {margin-top:var(--h3-final-top-offset,0px);width:100%}
      .h3-studio .h3-final-media {width:var(--h3-final-frame-width,100%);max-width:100%;margin-left:auto;margin-right:auto}
      @container (max-width:590px) {.h3-studio .h3-preview-deck{grid-template-columns:minmax(0,1fr) var(--h3-final-column-width,155px)}.h3-studio .h3-final-column{border-top:0;border-left:1px solid var(--border-color,#555);padding:0 12px}.h3-studio .h3-final-column>.h3-final{margin-top:var(--h3-final-top-offset,0px)}}

      .h3-studio .h3-scene-regenerate {position:absolute;left:50%;bottom:12px;transform:translateX(-50%);z-index:4;max-width:calc(100% - 16px);padding:5px 8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:12px;line-height:1.4;color:#f5df94;background:#252525e8;border-color:#b8a265;opacity:0;pointer-events:none;transition:opacity 140ms ease}
      .h3-studio .h3-scene-frame:hover .h3-scene-regenerate,.h3-studio .h3-scene-regenerate:focus-visible,.h3-studio .h3-scene-regenerate[aria-pressed=true] {opacity:1;pointer-events:auto}
      .h3-studio .h3-scene-regenerate[aria-pressed=true] {background:#6a5827ed;border-color:#f2cf55;color:#fff1bc}
      .h3-studio .h3-scene-regenerate[hidden] {display:none}
      .h3-studio .h3-manual-bar {gap:6px;padding:7px 0;border-top:1px solid var(--border-color,#555);margin:0}
      .h3-studio .h3-manual-bar[hidden] {display:none}
      .h3-studio .h3-manual-status {font-size:12px;line-height:1.45;flex:1;min-width:140px}
      .h3-studio .h3-scene-row[data-manual=true] {position:relative;transition:transform 180ms ease,opacity 140ms ease}
      .h3-studio .h3-scene-row[data-manual-selected=true] .h3-scene-frame {outline:2px solid #b8a265;outline-offset:-2px}
      .h3-studio .h3-scene-row[data-manual=true] .h3-scene-frame {cursor:pointer}
      .h3-studio .h3-scene-row[data-drag-source=true] {opacity:.28}
      .h3-studio .h3-scene-row[data-drop-side=before]:before,.h3-studio .h3-scene-row[data-drop-side=after]:after {content:"";position:absolute;top:32px;bottom:38px;width:3px;background:#d9bf76;z-index:8;box-shadow:0 0 8px #d9bf7660}
      .h3-studio .h3-scene-row[data-drop-side=before]:before {left:-4px}
      .h3-studio .h3-scene-row[data-drop-side=after]:after {right:-4px}
      .h3-studio .h3-scene-row[data-shift=left] {transform:translateX(-11px)}
      .h3-studio .h3-scene-row[data-shift=right] {transform:translateX(11px)}
      .h3-studio .h3-scene-remove {position:absolute;right:5px;top:5px;z-index:6;width:24px;min-height:24px;padding:0;background:#222c;color:#fff;font-size:18px}
      .h3-studio .h3-scene-remove[hidden] {display:none}
      .h3-studio .h3-all-scenes[data-file-drag=true] {outline:2px solid #b8a265;outline-offset:-2px;background:#8882}
      .h3-studio .h3-scene-row[data-manual=true] .h3-scene-card-foot {align-items:center;gap:5px}
      .h3-studio .h3-manual-connected {font-size:12px;white-space:normal}
      .h3-studio .h3-modes {display:flex;flex-wrap:nowrap;gap:0;padding:3px}
      .h3-studio .h3-modes .h3-mode {font-size:12px;padding:6px 3px;min-width:0}
      .h3-studio .h3-modes .h3-mode-indicator {display:block}
      .h3-studio .h3-image-job[data-manual-drop=true] {outline:2px solid #b8a265;outline-offset:-2px;background:#8882}
      @media(prefers-reduced-motion:reduce){.h3-studio .h3-scene-row[data-manual=true]{transition:none}}
      @media (prefers-reduced-motion:reduce) {.h3-studio .h3-scene-regenerate{transition:none}}
      @keyframes h3-spin {to {transform:rotate(360deg)}}
      @media (prefers-reduced-motion:reduce) {.h3-studio .h3-spinner{animation:none}.h3-studio .h3-mode-indicator,.h3-studio .h3-duration-indicator{transition:none}.h3-studio .h3-plan-toggle input[role=switch],.h3-studio .h3-plan-toggle input[role=switch]:before{transition:none}}
    `;
    document.head.append(style);
}

function el(tag, className, text) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text != null) result.textContent = text;
    return result;
}

async function copyPlainText(text) {
    try {
        await navigator.clipboard.writeText(text);
    } catch {
        const previousFocus = document.activeElement;
        const selection = window.getSelection();
        const ranges = selection ? Array.from({length: selection.rangeCount}, (_, index) => selection.getRangeAt(index).cloneRange()) : [];
        const field = document.createElement("textarea");
        field.value = text;
        field.setAttribute("readonly", "");
        field.style.cssText = "position:fixed;left:-10000px;top:0;opacity:0";
        document.body.append(field);
        field.select();
        try {
            if (!document.execCommand("copy")) throw new Error("复制失败，请展开后选择提示词复制。");
        } finally {
            field.remove();
            previousFocus?.focus?.({preventScroll:true});
            if (selection && ranges.length) {
                selection.removeAllRanges();
                ranges.forEach(range => {try {selection.addRange(range);} catch { /* Replaced content no longer owns a range. */ }});
            }
        }
    }
}

function mediaURL(item) {
    if (!item?.filename) return "";
    return api.apiURL(`/view?${new URLSearchParams({filename:item.filename, subfolder:item.subfolder || "", type:item.type || "output"})}`);
}

function referenceURL(item) {
    const parts = String(item.filename || "").replaceAll("\\", "/").split("/");
    return mediaURL({filename:parts.pop(), subfolder:parts.join("/"), type:"input"});
}

function stopVideo(video) {
    video.pause();
    video.removeAttribute("src");
    video.load();
}

function setMedia(element, url) {
    if (element.dataset.h3src === url) return;
    if (element.tagName === "VIDEO") stopVideo(element);
    element.dataset.h3src = url;
    if (url) element.src = url;
    else element.removeAttribute("src");
    element.hidden = !url;
}

function formatElapsed(timing){
    if(!timing||typeof timing!=="object")return "—";
    let seconds=Math.max(0,Number(timing.elapsed_seconds)||0);
    if(timing.status==="running"&&Number.isFinite(timing.started_at))seconds+=Math.max(0,Date.now()/1000-timing.started_at);
    seconds=Math.floor(seconds);const hours=Math.floor(seconds/3600),minutes=Math.floor(seconds%3600/60),rest=seconds%60;
    return hours?`${hours}:${String(minutes).padStart(2,"0")}:${String(rest).padStart(2,"0")}`:`${minutes}:${String(rest).padStart(2,"0")}`;
}
function setTimingBadge(badge,label,timing){const value=formatElapsed(timing);const text=`${label} ${value}`;if(badge.textContent!==text)badge.textContent=text;}

function hiddenWidget(widget) {
    if (!widget) return;
    hideNativeWidget(widget);
    widget.computeSize = () => [0, -4];
    if (widget.inputEl) widget.inputEl.style.display = "none";
    if (widget.element) widget.element.style.display = "none";
}

const NATIVE_PREVIEW_WIDGETS=new Set(["$$canvas-image-preview","$$comfy_animation_preview","video-preview"]);
function suppressNativePreviews(node){
    node.hideOutputImages=true;
    for(let index=(node.widgets?.length||0)-1;index>=0;index--){const widget=node.widgets[index];if(!NATIVE_PREVIEW_WIDGETS.has(widget.name))continue;widget.onRemove?.();node.widgets.splice(index,1);}
    node.imgs=undefined;node.imageIndex=null;
    if(node.videoContainer){for(const video of node.videoContainer.querySelectorAll?.("video")||[])stopVideo(video);node.videoContainer=undefined;}
}
function isolateNativeAssetDrawing(node){
    if(node._h3AssetDrawingIsolated)return;
    node._h3AssetDrawingIsolated=true;
    const previous=node.onDrawBackground;
    // This is Comfy's generated handler, whose only work for ordinary nodes is
    // loading the standard asset gallery. The Studio renders those assets itself.
    const nativeOnly=typeof previous==="function"&&/updatePreviews\(this/.test(Function.prototype.toString.call(previous))&&/SubgraphNode/.test(Function.prototype.toString.call(previous));
    node.onDrawBackground=function(){
        suppressNativePreviews(this);
        if(nativeOnly||!previous)return;
        // Preserve a third-party background chain while keeping Comfy's loader
        // cache in sync with the unchanged, public asset metadata.
        const output=app.nodeOutputs?.[String(this.id)];if(output)this.images=output.images;
        const previews=app.nodePreviewImages?.[String(this.id)];if(previews)this.preview=previews;
        try{return previous.apply(this,arguments);}finally{suppressNativePreviews(this);}
    };
}

function editorText(container) {
    return editorPlainText(container);
}

function restoreAddedWidgetValues(node,saved,imageConfig=false){
    const specs=imageConfig?{
        "分镜图长边":{fallback:2048,property:"h3_image_long_edge",valid:value=>Number.isInteger(value)&&value>=0&&value<=4096&&value%32===0},
        "参考读取长边":{fallback:0,property:"h3_reference_long_edge",valid:value=>Number.isInteger(value)&&value>=0&&value<=4096&&value%32===0},
        "上镜参考策略":{fallback:"完整参考（兼容）",valid:value=>["完整参考（兼容）","结构参考（原图细节优先）"].includes(value)},
    }:{
        "视频输入":{fallback:'图生视频',property:'h3_video_input',valid:value=>VIDEO_INPUTS.includes(value)},
        "视频种子":{fallback:-1,property:"h3_video_seed",valid:value=>Number.isInteger(value)&&value>=-1&&value<=4294967295},
        "生图后生成视频":{fallback:false,property:"h3_auto_video",valid:value=>typeof value==='boolean'},
        "视频短边上限":{fallback:0,property:"h3_video_short_edge",valid:value=>Number.isInteger(value)&&value>=0&&value<=1088&&value%32===0},
        "提示词格式":{fallback:PROMPT_FORMATS[0],property:"h3_prompt_format",valid:value=>PROMPT_FORMATS.includes(value)},
        "衔接超分配置":{fallback:"{}",property:"h3_reference_upscale",valid:value=>{if(typeof value!=="string")return false;try{const parsed=JSON.parse(value);return parsed&&typeof parsed==="object"&&!Array.isArray(parsed);}catch{return false;}}},
        "分镜图长边":{fallback:2048,property:"h3_image_long_edge",valid:value=>Number.isInteger(value)&&value>=0&&value<=4096&&value%32===0},
        "参考读取长边":{fallback:0,property:"h3_reference_long_edge",valid:value=>Number.isInteger(value)&&value>=0&&value<=4096&&value%32===0},
    };
    for(const[name,spec]of Object.entries(specs)){
        const widget=node.widgets?.find(item=>item.name===name);if(!widget)continue;
        const named=saved?.widgets_values_named?.[name],property=saved?.properties?.[spec.property];
        const position=(saved?.inputs||[]).filter(input=>input.widget).findIndex(input=>(input.widget?.name||input.name)===name);
        const positional=position>=0?saved?.widgets_values?.[position]:undefined;
        widget.value=spec.valid(named)?named:spec.valid(property)?property:spec.valid(positional)?positional:spec.fallback;
        if(widget.inputEl)widget.inputEl.value=String(widget.value);
    }
}

function createScriptEditor(getReferences,options={}) {
    const editor = el("div","h3-rich-editor");
    editor.contentEditable = options.readOnly?"false":"true"; editor.spellcheck = false;editor.tabIndex=0;
    if(options.readOnly)editor.setAttribute("aria-readonly","true");
    editor.setAttribute("role","textbox"); editor.setAttribute("aria-multiline","true");
    editor.setAttribute("aria-label","完整剧本，图1、参考图2或image 3自动关联参考图");
    let savedSelection = [0,0];
    function selection() {
        const selected = window.getSelection();
        if (!selected?.rangeCount) return savedSelection;
        const range = selected.getRangeAt(0);
        if (!editor.contains(range.startContainer) || !editor.contains(range.endContainer)) return savedSelection;
        const before = document.createRange(); before.selectNodeContents(editor); before.setEnd(range.startContainer,range.startOffset);
        const through = document.createRange(); through.selectNodeContents(editor); through.setEnd(range.endContainer,range.endOffset);
        savedSelection = [editorText(before.cloneContents()).length,editorText(through.cloneContents()).length];
        return savedSelection;
    }
    function caret(offset) {
        let remaining = Math.max(0,offset);
        for (const child of editor.childNodes) {
            const size = editorText(child).length;
            if (remaining <= size) {
                if (child.nodeType === Node.TEXT_NODE) return [child,Math.min(remaining,child.length)];
                const index = Array.from(editor.childNodes).indexOf(child);
                return [editor,index+(remaining>0?1:0)];
            }
            remaining -= size;
        }
        return [editor,editor.childNodes.length];
    }
    function setSelection(start,end=start) {
        const a=caret(start),b=caret(end);
        const range=document.createRange();range.setStart(...a);range.setEnd(...b);
        const selected=window.getSelection();selected.removeAllRanges();selected.addRange(range);
        savedSelection=[start,end];
    }
    function render(text,restore=null) {
        const fragment=document.createDocumentFragment();
        const references=new Map(getReferences().map(reference=>[reference.id,reference]));
        let position=0;
        const matches=options.generated?[...String(text).matchAll(/@(图[1-9]\d*|分镜[1-9]\d*|首帧[1-9]\d*|结束帧)/g)].map(match=>({start:match.index,end:match.index+match[0].length,token:match[0],id:match[1],reference:references.get(match[1])})):h3ReferenceTokens(text,options.getBindings?.(),getReferences());
        for (const match of matches) {
            if(match.start>position)fragment.append(document.createTextNode(text.slice(position,match.start)));
            const identity=match.id||`图${match.number}`,reference=match.reference;
            const chip=el("span","h3-inline-ref");chip.contentEditable="false";chip.dataset.h3Reference=identity;chip.dataset.qwsReferenceText=match.token;
            chip.dataset.h3Start=String(match.start);chip.dataset.h3End=String(match.end);if(match.uid)chip.dataset.h3Uid=match.uid;
            chip.setAttribute("role",match.missing?"button":"img");chip.setAttribute("aria-label",match.missing?"未引用，点击重新选择图片":`参考图 ${identity}`);if(match.missing)chip.tabIndex=0;
            if(reference&&(reference.filename||reference.media?.filename)){const image=el("img");image.src=reference.media?mediaURL(reference.media):referenceURL(reference);image.alt="";image.draggable=false;chip.append(image);}
            else if(reference?.pending){chip.dataset.pending="true";chip.append(el("span","h3-ref-placeholder","…"));chip.title="等待生成";}
            else{chip.dataset.missing="true";chip.title="未引用，点击重新选择图片";}
            chip.append(el("span","",match.missing?"未引用":identity));fragment.append(chip);
            position=match.end;
        }
        if(position<text.length)fragment.append(document.createTextNode(text.slice(position)));
        if(!fragment.childNodes.length)fragment.append(document.createTextNode(""));
        editor.replaceChildren(fragment);
        if(options.generated)editor.refreshReferenceStates?.();
        if(restore)setSelection(...restore);
    }
    Object.defineProperties(editor,{
        value:{get:()=>editorText(editor),set:next=>{const active=document.activeElement===editor;const range=active?selection():null;render(String(next),range);}},
        selectionStart:{get:()=>selection()[0]},selectionEnd:{get:()=>selection()[1]},
        disabled:{get:()=>editor.contentEditable!=="true",set:next=>{editor.contentEditable=next?"false":"true";editor.setAttribute("aria-disabled",String(!!next));}},
    });
    editor.setSelectionRange=setSelection;
    editor.setRangeText=(text,start,end)=>{const current=editor.value;const next=current.slice(0,start)+text+current.slice(end);render(next,[start+text.length,start+text.length]);};
    editor.refreshReferences=()=>{const active=document.activeElement===editor;render(editor.value,active?selection():null);};
    editor.refreshReferenceStates=()=>{
        if(!options.generated)return;
        const refs=new Map(getReferences().map(reference=>[reference.id,reference]));
        for(const chip of editor.querySelectorAll('[data-h3-reference]')){
            const reference=refs.get(chip.dataset.h3Reference),phase=reference?.phase||"",status=reference?.status||"";
            const busy=phase==="image"||phase==="video",failed=["失败","failed","error"].includes(status),cancelled=["已取消","cancelled","interrupted"].includes(status);
            chip.dataset.phase=phase||"idle";chip.dataset.referenceStatus=status;if(reference?.pending)chip.dataset.pending="true";else delete chip.dataset.pending;
            const percent=phase==="image"&&Number.isFinite(reference?.percent)?` ${Math.round(Math.max(0,Math.min(100,reference.percent))*10)/10}%`:"";
            const description=`${reference?.id||chip.dataset.h3Reference} · ${phase==="image"?"生图中":phase==="video"?"视频生成中":failed?"失败":cancelled?"已取消":reference?.pending?"待生成":"已就绪"}${percent}`;
            chip.title=description;chip.setAttribute("aria-label",description);
            let overlay=chip.querySelector('.h3-ref-state');
            if(!overlay){overlay=el("span","h3-ref-state");overlay.setAttribute("aria-hidden","true");overlay.contentEditable="false";chip.append(overlay);}
            const token=busy?"busy":failed?"failed":cancelled?"cancelled":"idle";
            if(overlay.dataset.state!==token){overlay.dataset.state=token;overlay.replaceChildren();if(busy)overlay.append(el("span","h3-spinner"));else if(failed||cancelled)overlay.textContent=failed?"!":"×";}
            overlay.hidden=!busy&&!failed&&!cancelled;
        }
    };
    editor.addEventListener("keyup",selection);
    editor.addEventListener("mouseup",selection);
    editor.addEventListener("blur",selection);
    editor.addEventListener("beforeinput",event=>{if(event.inputType?.startsWith("format"))event.preventDefault();});
    if(options.readOnly)editor.addEventListener("keydown",event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==="a"){event.preventDefault();const range=document.createRange();range.selectNodeContents(editor);const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);}});
    function plainClipboard(event,cut=false) {
        if(cut&&!editor.isContentEditable)return;
        const selected=editorClipboardSelection(editor,window.getSelection());
        if(!selected||!event.clipboardData)return;
        event.clipboardData.setData("text/plain",selected.text);event.preventDefault();
        if(cut){selected.range.deleteContents();editor.dispatchEvent(new InputEvent("input",{bubbles:true,inputType:"deleteByCut"}));}
    }
    editor.addEventListener("copy",event=>plainClipboard(event));editor.addEventListener("cut",event=>plainClipboard(event,true));
    return editor;
}

function setupStudio(node,nodeData) {
    if (node._h3Studio) return;
    installStyle();
    const widgets = new Map((node.widgets || []).map(widget => [widget.name, widget]));
    const configControls = new Map();
    function updateControlTitle(control){const help=control.dataset.h3Help;if(help)control.title=control.type==="checkbox"?help:`${help}\n当前值：${control.value}`;}
    const configFields=new Map();
    const configPorts={text:"文本模型配置",image:"图像模型配置",video:"视频模型配置",rules:"提示词规则"};
    function configSource(kind){
        const input=node.inputs?.find(input=>input.name===configPorts[kind]);
        if(input?.link==null)return null;
        const link=node.graph?.links?.get?.(input.link) ?? node.graph?.links?.[input.link];
        return link?node.graph?.getNodeById?.(link.origin_id ?? link[1]):null;
    }
    function upstreamWidget(name){
        if(name==="语言模型")return configSource("text")?.widgets?.find(widget=>["模型","model","语言模型"].includes(widget.name));
        if(["分镜图长边","参考读取长边"].includes(name))return configSource("image")?.widgets?.find(widget=>widget.name===name);
        return configSource("video")?.widgets?.find(widget=>widget.name===name);
    }
    const propertyFields={"画幅参考图":"h3_aspect_reference","导入优化计划":"h3_imported_plan","提示词格式":"h3_prompt_format","衔接超分配置":"h3_reference_upscale","分镜图长边":"h3_image_long_edge","参考读取长边":"h3_reference_long_edge","视频短边上限":"h3_video_short_edge","生图后生成视频":"h3_auto_video"};
    const value = (name, fallback) => upstreamWidget(name)?.value ?? widgets.get(name)?.value ?? node.properties?.[propertyFields[name]] ?? fallback;
    function isTextVideo(){return (widgets.get('视频输入')?.value??node.properties?.h3_video_input??'图生视频')==='文生视频';}
    let disposed = false;
    let syncing = false;
    let references = [];
    let referenceBindings=node.properties?.h3_reference_bindings||null;
    let durations = [];
    let durationDisplaySignature="";
    let snapshot = null;
    let currentRun = null;
    let activePromptId=node.properties?.h3_active_prompt_id||null;
    let pendingExecutionStart=null;
    let queueCheckPromise=null;
    let lastQueueCheck=0;
    let activeSince=0;
    let allowFreshExecution=false;
    const terminalRuns=new Map();
    if(node.properties?.h3_core_terminal?.run_id)terminalRuns.set(node.properties.h3_core_terminal.run_id,node.properties.h3_core_terminal.status);
    let draftChanged=!!node.properties?.h3_draft_changed;
    let lastPlanRender="";
    let lastPlanBindings="";
    let regenerationApplied=false;
    const oldRuns = new Set((node.properties?.h3_invalidated_runs||[]).filter(run=>typeof run==="string").slice(-16));
    let selected = 0;
    let followProgress = true;
    let pickerIndex = 0;
    let pickerMatches = [];
    let atRange = null;
    let uploading = false;
    let manualUploading = false;
    let manualDrag = null;
    let manualModeSignature = "";
    let editedScene = -1;
    let queueing = false;
    let submissionArmed=false,progressFetch=null,lastRecoveredKey='';
    let lastProgressFetch=0;
    let connectionSignature="";
    let connectionData=null;
    let previewMode=node.properties?.h3_director_preview_mode==="single"?"single":"all";
    let activeLayout=node.properties?.h3_director_layout==="vertical"?"vertical":"horizontal";
    let failureLocationMounted=false;
    let previewFrameHeight=220;
    const controller = new AbortController();
    const root = el("div", "h3-studio");
    root.tabIndex=0;root.setAttribute("aria-label","小陈 MiniMax H3 多分镜工作台");
    root.addEventListener("pointerdown",event=>{if(!event.target.closest("button,input,textarea,select,[contenteditable=true],a,video"))root.focus({preventScroll:true});});
    root.addEventListener("pointerdown", event => event.stopPropagation());
    root.title="内容区滚动 · 空白处滚轮缩放画布";
    function workspaceWheel(event){
        const horizontal=event.target.closest?.(".h3-all-scenes,.h3-durations,.h3-refs");
        if(horizontal){event.preventDefault();event.stopPropagation();horizontal.scrollLeft+=event.deltaX||event.deltaY;if(horizontal===allScenes){followProgress=false;updateSceneScroll();}return;}
        const safe=event.target.closest?.("textarea,input,select,[contenteditable],.h3-job-prompt,.h3-optimized,.h3-source-scroll,.h3-image-list,.h3-image-column,.h3-aspect-picker,.h3-picker");
        if(safe){
            event.preventDefault();event.stopPropagation();
            let current=event.target instanceof Element?event.target:safe,scroller=null;
            while(current&&current!==root){const css=getComputedStyle(current);if(/auto|scroll/.test(css.overflowY)&&current.scrollHeight>current.clientHeight+1){scroller=current;break;}current=current.parentElement;}
            if(scroller){const unit=event.deltaMode===1?18:event.deltaMode===2?scroller.clientHeight:1;scroller.scrollTop+=event.deltaY*unit;if(event.deltaX)scroller.scrollLeft+=event.deltaX*unit;}
            return;
        }
        event.preventDefault();event.stopPropagation();
        if(typeof app.canvas?.processMouseWheel==="function")app.canvas.processMouseWheel(event);
        else if(app.canvas?.canvas)app.canvas.canvas.dispatchEvent(new WheelEvent("wheel",{bubbles:true,cancelable:true,clientX:event.clientX,clientY:event.clientY,deltaX:event.deltaX,deltaY:event.deltaY,deltaZ:event.deltaZ,deltaMode:event.deltaMode,ctrlKey:event.ctrlKey,metaKey:event.metaKey,shiftKey:event.shiftKey,altKey:event.altKey}));
    }
    root.addEventListener("wheel",workspaceWheel,{capture:true,passive:false,signal:controller.signal});
    root.addEventListener("keydown",event=>{const editing=event.target.closest("input,textarea,select,[contenteditable=true]");if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==="m"&&!editing)return;event.stopPropagation();});

    function write(name, next) {
        if(name==='视频输入'){node.properties||={};node.properties.h3_video_input=next;}
        if(name==='视频种子'){node.properties||={};node.properties.h3_video_seed=next;}
        if(name==='生图后生成视频'){node.properties||={};node.properties.h3_auto_video=!!next;}
        if(name==="画幅参考图"){node.properties||={};node.properties.h3_aspect_reference=String(next||"图1");}
        if(name==="手动分镜图片"){node.properties||={};node.properties.h3_manual_images=String(next||"[]");}
        if(name==="导入优化计划"){node.properties||={};node.properties.h3_imported_plan=String(next||"{}");}
        if(name==="提示词格式"){node.properties||={};node.properties.h3_prompt_format=PROMPT_FORMATS.includes(next)?next:PROMPT_FORMATS[0];}
        if(["衔接超分配置","分镜图长边","参考读取长边"].includes(name)){node.properties||={};node.properties[propertyFields[name]]=next;}
        const widget = widgets.get(name);
        if (!widget) {node.graph?.setDirtyCanvas?.(true,true);return;}
        syncing = true;
        widget.value = next;
        if (widget.inputEl) widget.inputEl.value = String(next);
        try {widget.callback?.(next);} finally {syncing = false;}
        node.graph?.setDirtyCanvas?.(true, true);
    }
    function invalidateSource() {
        if (currentRun) oldRuns.add(currentRun);
        if(oldRuns.size>16)oldRuns.delete(oldRuns.keys().next().value);node.properties||={};node.properties.h3_invalidated_runs=Array.from(oldRuns);
        currentRun = null; snapshot = null; selected = 0; editedScene = -1; followProgress = true;
        write("继续任务",""); write("重做镜头",0); write("分镜覆盖","{}");
        write("计划编辑","{}");write("生图重做范围","保留现有图片");write("重生图片","[]");lastPlanRender="";regenerationApplied=false;
        if (node.properties){delete node.properties.h3_director_preview;delete node.properties.h3_plan_edit_draft;}
        draftChanged=false;if(node.properties)delete node.properties.h3_draft_changed;
        actionState.textContent = "";
        renderPreview();
    }
    function bindDraftReferences(text=textarea.value,removedIds=[],previousEntries=references){
        if(isTextVideo()){write('剧本',String(text));return {references,script:String(text),bindings:referenceBindings};}
        const raw=String(text),start=textarea.selectionStart,end=textarea.selectionEnd,active=document.activeElement===textarea;
        const result=reconcileH3References(references,raw,referenceBindings,{previousEntries,removedIds});
        references=result.references;referenceBindings=result.bindings;node.properties||={};node.properties.h3_reference_bindings=referenceBindings;node.properties.h3_ref_serial=references.length;
        if(raw!==result.script){
            const offset=position=>normalizeReferenceMentions(raw.slice(0,position),101).script.length;
            textarea.value=result.script;if(active)textarea.setSelectionRange(Math.min(result.script.length,offset(start)),Math.min(result.script.length,offset(end)));
        }else textarea.refreshReferences();
        write("参考图集",JSON.stringify(references));write("剧本",result.script);
        const unresolved=h3ReferenceTokens(result.script,referenceBindings,references).filter(item=>item.missing);
        if(unresolved.length){textarea.setAttribute("aria-invalid","true");uploadState.textContent="有未引用的图片，请点击红色引用重新选择，或删除该引用。";uploadState.classList.add("h3-reference-error");uploadState.dataset.referenceError="true";}
        return result;
    }
    function scriptChanged(){
        preflightFailure=null;
        if(typeof node.properties?.h3_optimized_import_text==="string"){delete node.properties.h3_optimized_import_text;write("导入优化计划","{}");}
        delete gallery.dataset.referenceError;textarea.removeAttribute("aria-invalid");uploadState.classList.remove("h3-reference-error");if(uploadState.dataset.referenceError){uploadState.textContent="";delete uploadState.dataset.referenceError;}
        bindDraftReferences();
        write("剧本",textarea.value);
        if(["running","generating"].includes(snapshot?.status)){
            draftChanged=true;node.properties||={};node.properties.h3_draft_changed=true;
            actionState.textContent="原稿已修改，下次运行生效";continueButton.disabled=true;redoButton.disabled=true;
        }else invalidateSource();
    }
    function finishRunningTiming(timing,status){
        if(!timing||timing.status!=="running")return;
        let elapsed=Math.max(0,Number(timing.elapsed_seconds)||0);
        if(Number.isFinite(timing.started_at))elapsed+=Math.max(0,Date.now()/1000-timing.started_at);
        timing.elapsed_seconds=elapsed;timing.status=status;timing.ended_at=Date.now()/1000;
    }
    function settleSnapshot(status,message=""){
        if((!snapshot||!["running","generating"].includes(snapshot.status))&&status==="failed"){
            snapshot={node_id:String(node.id),run_id:currentRun,status:"running",stage:"settings",scene_index:0,scene_count:durations.length,scenes:[],timings:{}};
        }
        if(!snapshot||!["running","generating"].includes(snapshot.status))return;
        const run=snapshot.run_id;terminalRuns.set(run,status);if(terminalRuns.size>12)terminalRuns.delete(terminalRuns.keys().next().value);
        if(status==="failed"&&!snapshot.failure_context)snapshot.failure_context={stage:snapshot.stage,scene_index:snapshot.scene_index||0,image_id:snapshot.image_jobs?.find(job=>["加载模型","编码参考","采样中","解码中","失败"].includes(job.status))?.id||null,message:message||snapshot.error||"任务失败"};
        snapshot.status=status;snapshot.stage=status;if(message)snapshot.error=message;
        const interrupted=["running","generating","生成中","准备图片","修复末帧","加载模型","编码参考","采样中","解码中"];
        for(const timing of Object.values(snapshot.timings||{}))finishRunningTiming(timing,status);
        for(const scene of snapshot.scenes||[]){finishRunningTiming(scene.video_timing,status);if(interrupted.includes(scene.status))scene.status=scene.video?"完成":status==="failed"?"失败":"已取消";}
        for(const job of snapshot.image_jobs||[]){finishRunningTiming(job.timing,status);if(interrupted.includes(job.status))job.status=job.image?"完成":status==="failed"?"失败":"已取消";}
        node.properties||={};node.properties.h3_core_terminal={run_id:run,status};node.properties.h3_director_preview=snapshot;
        renderPreview();maybeAutoOpenOutput(false);
    }
    async function reconcileExecutionQueue(reason="idle"){
        if(disposed||!snapshot||!["running","generating"].includes(snapshot.status))return;
        if(queueCheckPromise)return queueCheckPromise;
        if(Date.now()-lastQueueCheck<900)return;
        lastQueueCheck=Date.now();const run=currentRun;
        queueCheckPromise=(async()=>{
            try{
                const response=await api.fetchApi("/queue",{signal:controller.signal});if(!response.ok)return;
                const data=await response.json();if(disposed||currentRun!==run||!["running","generating"].includes(snapshot?.status))return;
                const items=[...(data.queue_running||[]),...(data.queue_pending||[])];
                const match=items.find(item=>(item[1]??item.prompt_id)===activePromptId)||items.find(item=>(item[2]??item.prompt??{})[String(node.id)]?.class_type===TYPE);
                if(match){activePromptId=match[1]??match.prompt_id;node.properties.h3_active_prompt_id=activePromptId;return;}
                if(reason==="newrun"&&Date.now()-activeSince<900)return;
                await recoverLiveProgress(true);if(!['running','generating'].includes(snapshot?.status))return;
                settleSnapshot(snapshot.final_video?"completed":"cancelled",snapshot.final_video?"":"任务已停止，可继续编辑后重新运行。");
            }catch(error){if(error.name!=="AbortError")return;}
            finally{queueCheckPromise=null;}
        })();return queueCheckPromise;
    }
    function coreEventMatches(data){return !!data&&(activePromptId&&data.prompt_id===activePromptId||!activePromptId&&String(data.node_id??data.node??"")===String(node.id));}
    function armExecution(){submissionArmed=true;allowFreshExecution=true;activePromptId=null;pendingExecutionStart=null;lastRecoveredKey='';if(currentRun){terminalRuns.delete(currentRun);oldRuns.delete(currentRun);}if(node.properties)delete node.properties.h3_core_terminal;}
    async function recoverLiveProgress(force=false){
        if(disposed||progressFetch||!currentRun&&!submissionArmed)return;
        if(!force&&!submissionArmed&&!['running','generating'].includes(snapshot?.status))return;
        if(!force&&Date.now()-lastProgressFetch<2000)return;lastProgressFetch=Date.now();
        progressFetch=(async()=>{try{const query=new URLSearchParams({node_id:String(node.id)});if(currentRun)query.set('run_id',currentRun);if(activePromptId)query.set('prompt_id',activePromptId);const response=await api.fetchApi('/h3-director/progress?'+query,{signal:controller.signal,cache:'no-store'});if(!response.ok)return;const data=(await response.json()).snapshot;if(!data)return;if(submissionArmed&&data.execution_id===snapshot?.execution_id&&!['running','generating'].includes(data.status))return;const key=JSON.stringify([data.run_id,data.execution_id,data.progress_seq,data.status]);if(key===lastRecoveredKey)return;lastRecoveredKey=key;applySnapshot(data,false);}catch{}finally{progressFetch=null;}})();return progressFetch;
    }
    function writeSetting(name,next) {
        preflightFailure=null;
        const upstream=upstreamWidget(name);
        if(upstream){upstream.value=next;upstream.callback?.(next);node.graph?.setDirtyCanvas?.(true,true);}
        write(name,next);
        if (!["执行范围","保留分镜计划","生图重做范围","重生图片","计划编辑"].includes(name)) {
            if(VIDEO_ONLY_FIELDS.has(name)&&canKeepVideoPlan())markVideoSettingsChanged(name!=="完成后");
            else if(["running","generating"].includes(snapshot?.status)){draftChanged=true;node.properties||={};node.properties.h3_draft_changed=true;actionState.textContent="参数已修改，下次运行生效";continueButton.disabled=true;redoButton.disabled=true;}
            else invalidateSource();
        }
        if(name==="保留分镜计划")renderPlanReuse();
        refreshModelConnections(false);
    }
    function canKeepVideoPlan(){return !!value("保留分镜计划",false)&&!!snapshot?.run_id&&!draftChanged&&(snapshot.plan_available??!!snapshot.scenes?.some(scene=>scene.prompt||scene.video_prompt));}
    function markVideoSettingsChanged(needsRender=true){
        if(!snapshot)return;
        snapshot.video_settings_changed=needsRender||!!snapshot.video_settings_changed;
        node.properties||={};node.properties.h3_director_preview=snapshot;
        write("继续任务",snapshot.run_id);
        actionState.textContent=needsRender?"视频参数已更新，继续运行将保留分镜计划和图片，重新生成视频。":"结束后资源设置已更新。";
        renderPlanReuse();renderAllScenes();
    }
    function writeImageSetting(name,next){
        const reusable=!draftChanged&&!planHasEdits()&&!Array.from(imageJobViews.values()).some(view=>view.editing)&&snapshot?.plan_export?exportedPlanData():null;
        const keep=reusable&&!importedPlanError(reusable)?reusable:null;
        if(node.properties)delete node.properties.h3_optimized_import_text;write("导入优化计划","{}");
        writeSetting(name,next);
        if(keep)acceptOptimizedPaste(keep.optimized_script,optimizedClipboardHTML(keep.optimized_script,keep));
        renderImageSizing();referenceUpscale.render();
    }

    const referenceSection = el("section");
    const referenceBar = el("div", "h3-row");
    const referenceTitle=el("strong","h3-grow","参考图 ");referenceTitle.append(el("span","h3-reference-hint","（图1、参考图2、image 3 自动识别，也可用 @）"));referenceBar.append(referenceTitle);
    const referenceCount = el("span", "h3-muted");
    const uploadButton = el("button", "", "添加图片");
    const fileInput = el("input");
    fileInput.type = "file"; fileInput.accept = "image/*"; fileInput.multiple = true; fileInput.hidden = true;
    uploadButton.onclick = () => fileInput.click();
    const referenceExpand=el("button","","展开 ↕");referenceExpand.type="button";referenceExpand.setAttribute("aria-expanded","false");referenceBar.append(referenceCount,referenceExpand,uploadButton,fileInput);
    const gallery = el("div", "h3-refs");
    referenceExpand.onclick=()=>{const expanded=gallery.dataset.expanded!=="true";gallery.dataset.expanded=String(expanded);referenceExpand.textContent=expanded?"收起 ↥":"展开 ↕";referenceExpand.setAttribute("aria-expanded",String(expanded));updateOverviewHeight();};
    gallery.tabIndex=0;gallery.setAttribute("role","group");gallery.setAttribute("aria-label","参考图库，可粘贴或拖入图片");
    const uploadState = el("div", "h3-muted h3-state");
    referenceSection.append(referenceBar, gallery, uploadState);

    const scriptSection = el("section");
    const scriptBar = el("div", "h3-row");
    scriptBar.append(el("strong", "h3-grow", "原始剧本"), el("span", "h3-muted", "图1／image 1 自动识别 · Ctrl+V 粘贴图片"));
    const editor = el("div", "h3-editor");
    const textarea = createScriptEditor(()=>isTextVideo()?[]:references,{getBindings:()=>isTextVideo()?null:referenceBindings});
    const importScript=el('button','','导入剧本'),scriptFile=el('input');importScript.type='button';scriptFile.type='file';scriptFile.accept='.txt,.md,.markdown,.text';scriptFile.hidden=true;importScript.onclick=()=>scriptFile.click();scriptFile.onchange=()=>readWorkspaceFile(scriptFile.files[0]).finally(()=>{scriptFile.value='';});scriptBar.append(importScript,scriptFile);
    const picker = el("div", "h3-picker");
    picker.setAttribute("role", "listbox"); picker.setAttribute("aria-label", "选择参考图"); picker.hidden = true;
    editor.append(picker, textarea);
    scriptSection.append(scriptBar, editor);
    const scriptSpeech=attachSpeechInput(node,editor,{widgetName:'剧本',editor:textarea,compact:true});
    controller.signal.addEventListener('abort',()=>scriptSpeech.dispose(),{once:true});

    let dialogueOnly=false;let overviewExpanded=false;let preflightFailure=null;
    const optimizedSection=el("section");const optimizedBar=el("div","h3-row h3-optimized-bar");
    const failureBox=el("div","h3-failure");failureBox.hidden=true;failureBox.setAttribute("role","alert");
    const failureText=el("span","h3-failure-text"),locateFailure=el("button","","定位错误");locateFailure.type="button";failureBox.append(failureText,locateFailure);
    let failureTarget=null;let failureRelatedTargets=[];const errorElements=new Set();
    locateFailure.onclick=()=>{if(!failureTarget)return;failureTarget.scrollIntoView({block:"nearest",inline:"nearest",behavior:"auto"});failureTarget.tabIndex=-1;failureTarget.focus({preventScroll:true});for(const target of[failureTarget,...failureRelatedTargets]){delete target.dataset.errorFlash;void target.offsetWidth;target.dataset.errorFlash="true";}};
    const planningTime=el("span","h3-time");
    const promptFormatBar=el("div","h3-duration-mode h3-prompt-format");promptFormatBar.setAttribute("role","radiogroup");promptFormatBar.setAttribute("aria-label","提示词格式");
    promptFormatBar.title="选择剧本整理结构；H3六段式参考官方全参考结构，当前首尾帧模型仍使用原生三字段视频提示词。切换后下次运行重新规划。";
    const promptFormatIndicator=el("span","h3-duration-indicator");promptFormatBar.append(promptFormatIndicator);
    const promptFormatButtons=new Map();
    for(const format of PROMPT_FORMATS){
        const button=el("button","",format==="工作台格式"?"当前模式":format);button.type="button";button.setAttribute("role","radio");button.setAttribute("aria-label",format);button.title=promptFormatBar.title;
        button.onclick=()=>{
            if(value("提示词格式",PROMPT_FORMATS[0])===format||["running","generating"].includes(snapshot?.status))return;
            if(node.properties)delete node.properties.h3_optimized_import_text;write("导入优化计划","{}");
            writeSetting("提示词格式",format);renderPromptFormat();
        };
        button.onkeydown=event=>{if(!["ArrowLeft","ArrowRight","Home","End"].includes(event.key))return;event.preventDefault();const next=event.key==="Home"?PROMPT_FORMATS[0]:event.key==="End"?PROMPT_FORMATS[1]:PROMPT_FORMATS[1-PROMPT_FORMATS.indexOf(format)];promptFormatButtons.get(next).focus();promptFormatButtons.get(next).click();};
        promptFormatButtons.set(format,button);promptFormatBar.append(button);
    }
    function renderPromptFormat(){const selected=value("提示词格式",PROMPT_FORMATS[0]),busy=["running","generating"].includes(snapshot?.status);promptFormatIndicator.style.transform=`translateX(${PROMPT_FORMATS.indexOf(selected)===1?100:0}%)`;for(const[format,button]of promptFormatButtons){const active=format===selected;button.setAttribute("aria-checked",String(active));button.tabIndex=active?0:-1;button.disabled=busy;}}
    const copyOptimized=el("button","","复制");
    const pasteOptimized=el("button","","粘贴优化稿");pasteOptimized.type="button";pasteOptimized.title="从总览复制完整优化稿后粘贴，直接使用已有分镜计划。";
    const keepPlanLabel=el("label","h3-plan-toggle");const keepPlanToggle=el("input");keepPlanToggle.type="checkbox";keepPlanToggle.setAttribute("role","switch");keepPlanToggle.setAttribute("aria-label","保留分镜计划");keepPlanLabel.append(keepPlanToggle,el("span","","保留分镜计划"));
    const optimizedScript=createScriptEditor(optimizationReferences,{readOnly:true,generated:true});optimizedScript.classList.add("h3-optimized");optimizedScript.setAttribute("aria-label","AI剧本优化总览");
    const overviewEditor=el("div","h3-overview-editor");const dialogueView=el("div","h3-rich-editor h3-optimized h3-dialogues");dialogueView.tabIndex=0;dialogueView.hidden=true;dialogueView.setAttribute("aria-label","最终发声对白");
    const dialogueToggle=el("button","h3-overview-tool h3-dialogue-toggle","查看对白"),overviewExpand=el("button","h3-overview-tool h3-overview-expand","展开 ↕");dialogueToggle.type=overviewExpand.type="button";
    dialogueToggle.setAttribute("aria-pressed","false");overviewExpand.setAttribute("aria-expanded","false");overviewEditor.append(optimizedScript,dialogueView,dialogueToggle,overviewExpand);
    dialogueToggle.onclick=()=>{dialogueOnly=!dialogueOnly;optimizedScript.hidden=dialogueOnly;dialogueView.hidden=!dialogueOnly;dialogueToggle.textContent=dialogueOnly?"返回总览":"查看对白";dialogueToggle.setAttribute("aria-pressed",String(dialogueOnly));renderDialogueView();};
    overviewExpand.onclick=()=>{overviewExpanded=!overviewExpanded;root.dataset.overviewExpanded=String(overviewExpanded);overviewExpand.textContent=overviewExpanded?"收起 ↥":"展开 ↕";overviewExpand.setAttribute("aria-expanded",String(overviewExpanded));if(overviewExpanded)resultColumn.scrollTop=0;updateOverviewHeight();};
    copyOptimized.onclick=copyOptimizedPlan;
    pasteOptimized.onclick=pasteOptimizedPlan;
    const regenerationBar=el("div","h3-regeneration");regenerationBar.setAttribute("role","group");regenerationBar.setAttribute("aria-label","生图重做范围");regenerationBar.hidden=true;
    const regenerationButtons=new Map();for(const[raw,label]of[["保留现有图片","保留当前图"],["选择重生","选择重生"],["全部重生","全部重生"]]){const button=el("button","",label);button.type="button";button.onclick=()=>{write("生图重做范围",raw);if(raw!=="选择重生")write("重生图片","[]");renderPlanReuse();};regenerationButtons.set(raw,button);regenerationBar.append(button);}
    const regenerationStatus=el("span","h3-regeneration-status");regenerationBar.append(regenerationStatus);
    const exportJSON=el('button','','导出JSON'),importJSON=el('button','','导入JSON'),planFile=el('input');exportJSON.type=importJSON.type='button';planFile.type='file';planFile.accept='.json';planFile.hidden=true;exportJSON.onclick=exportPlanFile;importJSON.onclick=()=>planFile.click();planFile.onchange=()=>readWorkspaceFile(planFile.files[0]).finally(()=>{planFile.value='';});
    optimizedBar.append(el("strong","h3-grow","AI剧本优化总览"),promptFormatBar,keepPlanLabel,planningTime,pasteOptimized,copyOptimized,exportJSON,importJSON,planFile);optimizedSection.append(optimizedBar,overviewEditor);
    keepPlanToggle.onchange=()=>{
        write("保留分镜计划",keepPlanToggle.checked);node.properties||={};
        if(!keepPlanToggle.checked){
            if(snapshot?.run_id)node.properties.h3_plan_edit_draft={run_id:snapshot.run_id,edit:planEditData(),overrides:readOverrides(),display:optimizedScript.value};
            write("继续任务","");write("计划编辑","{}");write("分镜覆盖","{}");write("生图重做范围","保留现有图片");write("重生图片","[]");
        }else{
            const saved=node.properties.h3_plan_edit_draft;
            if(saved&&snapshot?.run_id&&saved.run_id===snapshot.run_id&&!draftChanged){write("计划编辑",JSON.stringify(saved.edit||{}));write("分镜覆盖",JSON.stringify(saved.overrides||{}));}
        }
        renderPlanReuse();
    };
    optimizedScript.addEventListener("input",()=>{
        if(typeof node.properties?.h3_optimized_import_text==="string"){
            node.properties.h3_optimized_import_text=optimizedScript.value;
            const imported=importedPlanData();if(imported){imported.optimized_script=optimizedScript.value;write("导入优化计划",JSON.stringify(imported));}
            lastPlanRender=optimizedScript.value;return;
        }
        if(optimizedScript.disabled||!value("保留分镜计划",false))return;
        const edited=planEditData();edited.optimized_script=optimizedScript.value;write("计划编辑",JSON.stringify(edited));lastPlanRender=optimizedScript.value;renderPlanReuse();
    });
    optimizedScript.addEventListener("paste",event=>{
        if(optimizedScript.disabled)return;event.preventDefault();event.stopPropagation();
        const text=event.clipboardData?.getData("text/plain")||"",html=event.clipboardData?.getData("text/html")||"";
        acceptOptimizedPaste(text,html);
    });
    optimizedScript.addEventListener("copy",event=>{
        const selection=editorClipboardSelection(optimizedScript,window.getSelection());if(!selection||!event.clipboardData)return;
        const text=selection.text,full=text.trim()===optimizedScript.value.trim();
        event.clipboardData.setData("text/plain",text);event.clipboardData.setData("text/html",optimizedClipboardHTML(text,full?exportedPlanData():null,!full));event.preventDefault();
    });
    optimizedScript.addEventListener("blur",()=>{if(!disposed&&root.isConnected)renderOptimizedPlan();});

    const settingsSection = el("section");
    const videoInputBar=el('div','h3-duration-mode h3-video-input'),videoInputIndicator=el('span','h3-duration-indicator'),videoInputButtons=new Map();
    videoInputBar.setAttribute('role','radiogroup');videoInputBar.setAttribute('aria-label','视频输入');videoInputBar.append(videoInputIndicator);
    for(const mode of VIDEO_INPUTS){const button=el('button','',mode);button.type='button';button.setAttribute('role','radio');button.onclick=()=>switchVideoInput(mode);button.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const target=event.key==='Home'?VIDEO_INPUTS[0]:event.key==='End'?VIDEO_INPUTS[1]:mode===VIDEO_INPUTS[0]?VIDEO_INPUTS[1]:VIDEO_INPUTS[0];videoInputButtons.get(target).click();videoInputButtons.get(target).focus();};videoInputButtons.set(mode,button);videoInputBar.append(button);}
    const modeRow = el("div", "h3-row");
    const modeSelect = el("div", "h3-modes");modeSelect.setAttribute("role","radiogroup");modeSelect.setAttribute("aria-label","参考图模式");
    modeSelect.style.setProperty("--h3-mode-count",String(MODES.length));
    const modeIndicator=el("span","h3-mode-indicator");modeSelect.append(modeIndicator);
    let activeMode=MODES[1];
    const modeButtons=MODES.map((mode,index)=>{const button=el("button","h3-mode",MODE_LABELS[index]||mode);button.type="button";button.setAttribute("role","radio");button.setAttribute("aria-label",mode);
        button.onclick=()=>{modeSelect.value=mode;write("生成模式",mode);if(mode==="手动分镜图")write("保留分镜计划",true);invalidateSource();renderMode();};
        button.onkeydown=event=>{let next=index;if(["ArrowRight","ArrowDown"].includes(event.key))next=(index+1)%MODES.length;else if(["ArrowLeft","ArrowUp"].includes(event.key))next=(index+MODES.length-1)%MODES.length;else if(event.key==="Home")next=0;else if(event.key==="End")next=MODES.length-1;else return;event.preventDefault();modeButtons[next].focus();modeButtons[next].click();};
        modeSelect.append(button);return button;});
    Object.defineProperties(modeSelect,{value:{get:()=>activeMode,set:next=>{activeMode=MODES.includes(next)?next:MODES[1];const selectedIndex=MODES.indexOf(activeMode);modeIndicator.style.transform=`translateX(${selectedIndex*100}%)`;modeButtons.forEach((button,index)=>{button.setAttribute("aria-checked",String(index===selectedIndex));button.tabIndex=index===selectedIndex?0:-1;});}},disabled:{set:next=>modeButtons.forEach(button=>button.disabled=!!next),get:()=>modeButtons[0].disabled}});
    const addScene = el("button", "", "添加镜头");
    addScene.onclick = () => {
        if (durations.length >= MAX_SCENES) return;
        durations.push(durations.at(-1) || 10);
        saveDurations(); renderDurations(); renderSceneChooser();
    };
    modeRow.append(modeSelect);const durationBar=el("div","h3-row");durationBar.append(el("strong","h3-grow","每镜时长"),addScene);
    const durationModeBar=el("div","h3-duration-mode");durationModeBar.setAttribute("role","radiogroup");durationModeBar.setAttribute("aria-label","时长模式");
    const durationIndicator=el("span","h3-duration-indicator");durationModeBar.append(durationIndicator);
    const durationModeButtons=new Map();for(const mode of["手动设置","AI自动分配"]){const button=el("button","",mode);button.type="button";button.setAttribute("role","radio");button.onclick=()=>{if(value("时长模式","手动设置")===mode)return;if(mode==="AI自动分配")write("目标总秒数",durations.reduce((sum,seconds)=>sum+seconds,0));write("时长模式",mode);invalidateSource();renderDurations();renderSceneChooser();};button.onkeydown=event=>{if(!["ArrowLeft","ArrowRight","Home","End"].includes(event.key))return;event.preventDefault();const next=event.key==="Home"?"手动设置":event.key==="End"?"AI自动分配":mode==="手动设置"?"AI自动分配":"手动设置";durationModeButtons.get(next).focus();durationModeButtons.get(next).click();};durationModeButtons.set(mode,button);durationModeBar.append(button);}
    durationBar.insertBefore(durationModeBar,addScene);
    const targetDurationRow=el("label","h3-total-duration");const targetDuration=el("input");targetDuration.type="number";targetDuration.step="0.5";targetDuration.setAttribute("aria-label","目标总秒数");targetDurationRow.append(el("span","","总时长"),targetDuration,el("span","h3-muted","秒"));
    targetDuration.onchange=()=>{preflightFailure=null;const candidate=Number(targetDuration.value);write("目标总秒数",Math.max(durations.length,Math.min(15*durations.length,Number.isFinite(candidate)?candidate:durations.length)));invalidateSource();renderDurations();renderSceneChooser();};
    const modeHint = el("div", "h3-muted");
    const durationGrid = el("div", "h3-durations");
    const configDetails = el("details");
    node.properties||={};if(node.properties.h3_advanced_compact_version!==H3_STUDIO_VERSION){node.properties.h3_advanced_open=true;node.properties.h3_advanced_compact_version=H3_STUDIO_VERSION;}
    configDetails.open=node.properties?.h3_advanced_open??true;
    configDetails.addEventListener("toggle",()=>{node.properties||={};node.properties.h3_advanced_open=configDetails.open;});
    const basicGrid=el("div","h3-config h3-basic-config");const configGrid = el("div", "h3-config h3-advanced-config");
    const imageSeedRow=el('div','h3-seed-control'),imageSeedInput=el('input'),imageSeedModes=el('div','h3-duration-mode'),imageSeedIndicator=el('span','h3-duration-indicator'),imageSeedButtons=new Map();
    imageSeedInput.type='number';imageSeedInput.min='0';imageSeedInput.max='4294967295';imageSeedInput.step='1';imageSeedInput.setAttribute('aria-label','分镜种子');
    imageSeedModes.setAttribute('role','radiogroup');imageSeedModes.setAttribute('aria-label','分镜种子模式');imageSeedModes.title='随机在新任务首次提交时抽取；续跑保留分镜种子，不改变独立视频种子。';imageSeedModes.append(imageSeedIndicator);
    for(const[mode,label]of [['fixed','固定'],['random','随机']]){const button=el('button','',label);button.type='button';button.setAttribute('role','radio');button.onclick=()=>{setImageSeedMode(node,mode);node.graph?.setDirtyCanvas?.(true,true);renderImageSeed();};imageSeedButtons.set(mode,button);imageSeedModes.append(button);}
    imageSeedModes.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const mode=event.key==='Home'?'fixed':event.key==='End'?'random':imageSeedMode(node)==='fixed'?'random':'fixed';if(!imageSeedButtons.get(mode).disabled){imageSeedButtons.get(mode).click();imageSeedButtons.get(mode).focus();}};
    imageSeedInput.onchange=()=>{const next=Number(imageSeedInput.value);if(imageSeedInput.value===''||!Number.isSafeInteger(next)||next<0||next>4294967295){imageSeedInput.value=String(currentH3ImageSeed(node)??42);return;}if(seedMode(node)==='fixed'&&currentH3Seed(node)!==null)setH3Seed(node,currentH3Seed(node));setImageSeedMode(node,'fixed');writeSetting('随机种子',next);renderImageSeed();renderSeed();};
    imageSeedRow.append(el('span','','分镜种子'),imageSeedInput,imageSeedModes);
    function renderImageSeed(){const busy=queueing||['running','generating'].includes(snapshot?.status),mode=imageSeedMode(node);if(document.activeElement!==imageSeedInput)imageSeedInput.value=String(currentH3ImageSeed(node)??42);imageSeedInput.disabled=busy;imageSeedIndicator.style.transform=`translateX(${mode==='random'?100:0}%)`;for(const[key,button]of imageSeedButtons){button.setAttribute('aria-checked',String(key===mode));button.tabIndex=key===mode?0:-1;button.disabled=busy;}}
    const seedRow=el('div','h3-seed-control'),seedInput=el('input'),seedModes=el('div','h3-duration-mode'),seedIndicator=el('span','h3-duration-indicator'),seedButtons=new Map();
    seedInput.type='number';seedInput.min='0';seedInput.max='4294967295';seedInput.step='1';seedInput.setAttribute('aria-label','视频种子');
    seedModes.setAttribute('role','radiogroup');seedModes.setAttribute('aria-label','视频种子模式');seedModes.title='仅影响视频；固定沿用当前视频种子，随机为新视频任务抽取。分镜图与规划种子保持不变。';seedModes.append(seedIndicator);
    for(const[mode,label]of [['fixed','固定'],['random','随机']]){const button=el('button','',label);button.type='button';button.setAttribute('role','radio');button.onclick=()=>{if(mode==='fixed'&&currentH3Seed(node)!==null)setH3Seed(node,currentH3Seed(node));setSeedMode(node,mode);node.graph?.setDirtyCanvas?.(true,true);renderSeed();};seedButtons.set(mode,button);seedModes.append(button);}
    seedModes.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const mode=event.key==='Home'?'fixed':event.key==='End'?'random':seedMode(node)==='fixed'?'random':'fixed';if(!seedButtons.get(mode).disabled){seedButtons.get(mode).click();seedButtons.get(mode).focus();}};
    seedInput.onchange=()=>{const next=Number(seedInput.value);if(seedInput.value===''||!Number.isSafeInteger(next)||next<0||next>4294967295){seedInput.value=String(currentH3Seed(node)??42);renderSeed();return;}setSeedMode(node,'fixed');writeSetting('视频种子',next);renderSeed();};
    seedRow.append(el('span','','视频种子'),seedInput,seedModes);
    function renderSeed(){const busy=queueing||['running','generating'].includes(snapshot?.status)||!widgets.has('视频种子'),mode=seedMode(node);if(document.activeElement!==seedInput)seedInput.value=String(currentH3Seed(node)??42);seedInput.disabled=busy;seedIndicator.style.transform=`translateX(${mode==='random'?100:0}%)`;for(const[key,button]of seedButtons){button.setAttribute('aria-checked',String(key===mode));button.tabIndex=key===mode?0:-1;button.disabled=busy;}}
    function prepareSeed(){const options={resumeRun:value('继续任务',''),hasSnapshot:!!(value('保留分镜计划',false)&&(snapshot?.run_id||currentRun)||importedPlanData())};if(seedMode(node)==='fixed'&&currentH3Seed(node)!==null)setH3Seed(node,currentH3Seed(node));const imageResult=prepareH3ImageSeed(node,options),result=prepareH3Seed(node,options);if(result.changed&&canKeepVideoPlan())markVideoSettingsChanged(true);if(imageResult.changed||result.changed)node.graph?.setDirtyCanvas?.(true,true);renderImageSeed();renderSeed();return result;}
    const aspectChoices=["跟随图片","9:16 竖屏","16:9 横屏","1:1 方形","3:4 竖屏","4:3 横屏","自定义"];
    const aspectRow=el("div","h3-aspects");aspectRow.setAttribute("role","group");aspectRow.setAttribute("aria-label","视频画幅预设");
    const aspectButtons=new Map();for(const aspect of aspectChoices){const button=el("button","h3-aspect",aspect);button.type="button";button.setAttribute("aria-label",aspect);button.onclick=()=>{if(value("画幅","跟随图片")!==aspect)writeSetting("画幅",aspect);if(aspect==="跟随图片"&&references.length>1){aspectPicker.hidden=!aspectPicker.hidden;if(!aspectPicker.hidden)renderAspectPicker();}else aspectPicker.hidden=true;renderAspectSettings();};aspectButtons.set(aspect,button);aspectRow.append(button);}
    const aspectPicker=el("div","h3-aspect-picker");aspectPicker.hidden=true;aspectPicker.setAttribute("role","group");aspectPicker.setAttribute("aria-label","选择尺寸基准图片");aspectRow.append(aspectPicker);
    const aspectImageSizes=new Map();
    function aspectReference(){
        const filename=node.properties?.h3_aspect_ref_filename;
        const reference=filename?(references.find(item=>item.filename===filename)||references[0]):(references.find(item=>item.id===value("画幅参考图","图1"))||references[0]);
        if(reference){node.properties||={};node.properties.h3_aspect_ref_filename=reference.filename;if(value("画幅参考图","图1")!==reference.id)write("画幅参考图",reference.id);}
        return reference;
    }
    function renderAspectPicker(){
        const selected=aspectReference();aspectPicker.replaceChildren();
        for(const reference of references){const button=el("button","h3-aspect-choice"),picture=el("img"),label=el("span","",reference.id),dimensions=el("span","h3-muted","读取尺寸…");button.type="button";button.setAttribute("aria-pressed",String(reference.filename===selected?.filename));picture.alt="";picture.loading="lazy";picture.decoding="async";
            const known=aspectImageSizes.get(reference.filename);if(known)dimensions.textContent=known;
            picture.onload=()=>{if(disposed)return;const size=`${picture.naturalWidth} × ${picture.naturalHeight}`;aspectImageSizes.set(reference.filename,size);dimensions.textContent=size;};picture.onerror=()=>{dimensions.textContent="尺寸暂不可用";};picture.src=referenceURL(reference);button.append(picture,label,dimensions);
            button.onclick=()=>{node.properties||={};node.properties.h3_aspect_ref_filename=reference.filename;writeSetting("画幅参考图",reference.id);aspectPicker.hidden=true;renderAspectSettings();};aspectPicker.append(button);
        }
    }
    document.addEventListener("pointerdown",event=>{if(!aspectRow.contains(event.target))aspectPicker.hidden=true;},{signal:controller.signal});
    const aspectNote=el("div","h3-aspect-note");aspectNote.setAttribute("aria-live","polite");
    gallery.addEventListener('load',event=>{if(event.target instanceof HTMLImageElement)renderAspectSettings();},true);
    const videoScaleRow=el('label','h3-field'),videoScale=el('input'),videoScaleValue=el('span','h3-muted');videoScale.type='range';videoScale.min='64';videoScale.max='1088';videoScale.step='32';videoScale.setAttribute('aria-label','视频尺寸按32像素缩小');videoScaleRow.append(el('span','','尺寸缩放'),videoScale,videoScaleValue);
    videoScale.oninput=()=>{const short=Number(videoScale.value),ratio=Number(videoScale.dataset.ratio)||1;const dims=ratio>=1?[Math.round(short*ratio/32)*32,short]:[short,Math.round(short/ratio/32)*32];videoScaleValue.textContent=`${dims[0]} × ${dims[1]}`;};
    videoScale.onchange=()=>writeSetting('视频短边上限',Number(videoScale.value));
    const imageSampling=el("fieldset","h3-image-sampling"),imageSamplingBody=el("div");
    imageSampling.append(el("legend","","Image2.1 生图参数"),imageSamplingBody,imageSeedRow);
    const imageSamplingFields={model:"图像模型",steps:"步数",cfg:"CFG",denoise:"去噪",sampler:"采样器",scheduler:"调度器"};
    let imageSamplingSource=null,imageSamplingControls=null;
    function refreshImageSampling(source=configSource("image")){
        if(source!==imageSamplingSource){
            imageSamplingControls?.dispose();imageSamplingControls=null;imageSamplingSource=source;
            imageSamplingBody.replaceChildren();
        }
        const supported=source&&Object.values(imageSamplingFields).every(name=>source.widgets?.some(widget=>widget.name===name));
        if(!imageSamplingControls&&supported){
            imageSamplingControls=createImageSamplingControls({node:source,fields:imageSamplingFields,onChange(){preflightFailure=null;refreshModelConnections(true);}});
            imageSamplingBody.replaceChildren(imageSamplingControls.element);
        }else if(!supported){
            imageSamplingBody.textContent=source?"连接的图像配置不支持参数编辑":"请连接图像模型配置节点";
        }
        imageSamplingControls?.refresh();
        imageSampling.disabled=["running","generating"].includes(snapshot?.status)||queueing;
    }
    const imageSizing=el("div","h3-config h3-image-sizing"),imageSizeControls=new Map();
    const sizePresets=[[0,"跟随视频"],[1024,"1K"],[1536,"1.5K"],[2048,"2K"],[3072,"3K"],[4096,"4K"]];
    for(const name of["分镜图长边","参考读取长边"]){const label=el("label","h3-field"),control=el("select");label.append(el("span","",name==="分镜图长边"?"分镜输出长边":"参考图读取上限"),control);control.setAttribute("aria-label",name);control.title=name==="分镜图长边"?"分镜原图默认长边2K；生成视频时再按视频档位适配，不改变视频分辨率。":"仅限制送入模型的参考图大小，不决定分镜输出尺寸，也不会把小素材放大。";for(const[size,text]of sizePresets)control.append(new Option(size===0&&name==="参考读取长边"?"自动（最高2K）":text,String(size)));if(name==="分镜图长边")control.append(new Option("自定义","custom"));control.onchange=()=>{if(control.value==="custom"){imageCustomRow.hidden=false;imageCustomSize.focus();return;}writeImageSetting(name,Number(control.value));};imageSizeControls.set(name,control);configFields.set(name,label);imageSizing.append(label);}
    const imageCustomRow=el("label","h3-field"),imageCustomSize=el("input");imageCustomSize.type="number";imageCustomSize.min="32";imageCustomSize.max="4096";imageCustomSize.step="32";imageCustomSize.setAttribute("aria-label","自定义分镜图长边");imageCustomRow.append(el("span","","自定义长边"),imageCustomSize);imageCustomSize.onchange=()=>writeImageSetting("分镜图长边",Number(imageCustomSize.value));imageSizing.append(imageCustomRow);
    const imageSizingNote=el("div","h3-image-sizing-note","分镜默认2K；视频按比例和所选档位生成。"),imageSizingError=el("div","h3-image-sizing-error");imageSizingError.setAttribute("role","alert");
    function imageSizeError(){const edge=Number(value("分镜图长边",0)),readEdge=Number(value("参考读取长边",0));return !Number.isInteger(edge)||edge!==0&&(edge<32||edge>4096||edge%32)?"分镜图长边应为32～4096之间的32倍数，或选择跟随视频。":![0,1024,1536,2048,3072,4096].includes(readEdge)?"请选择有效的参考读取长边。":"";}
    function renderImageSizing(){const busy=["running","generating"].includes(snapshot?.status)||queueing,edge=Number(value("分镜图长边",0)),known=sizePresets.some(([size])=>size===edge);for(const[name,control]of imageSizeControls){const current=Number(value(name,0));control.value=name==="分镜图长边"&&!known?"custom":String(current);control.disabled=busy;}imageCustomRow.hidden=known;imageCustomSize.value=String(edge||1024);imageCustomSize.disabled=busy;const error=imageSizeError();imageSizingError.textContent=error;imageSizingError.hidden=!error;for(const control of[...imageSizeControls.values(),imageCustomSize])control.setAttribute("aria-invalid",String(!!error));}
    function referenceUpscaleDimensions(){
        const receipt=snapshot?.reference_upscale_receipt,dimensions=receipt?.dimensions||receipt?.expected_dimensions;
        if(Array.isArray(dimensions?.input)&&dimensions.input.every(value=>value>0))return {input:dimensions.input,output:dimensions.output};
        const source=snapshot?.image_jobs?.find(job=>/^分镜\d+$/.test(job.id)&&Number(job.reference_receipt?.width)>0&&Number(job.reference_receipt?.height)>0)?.reference_receipt;
        if(source)return {input:[Number(source.width),Number(source.height)]};
        const ratio=sceneMediaRatio(null),canvas=snapshot?.canvas;let edge=Number(value("分镜图长边",0));
        if(!edge)edge=canvas?.width&&canvas?.height?Math.max(canvas.width,canvas.height):({"384P":384,"480P":480,"720P":720,"1080P":1080}[value("生成档位","480P")]||480)*Math.max(ratio,1/ratio);
        return {input:[Math.max(32,Math.round((ratio>=1?edge:edge*ratio)/32)*32),Math.max(32,Math.round((ratio>=1?edge/ratio:edge)/32)*32)],estimatedInput:true};
    }
    const referenceUpscale=createReferenceUpscalePanel({read:()=>value("衔接超分配置","{}"),write:next=>writeImageSetting("衔接超分配置",next),isBusy:()=>["running","generating"].includes(snapshot?.status)||queueing,isActive:()=>!isTextVideo()&&modeSelect.value==="AI生成分镜",dimensions:referenceUpscaleDimensions});
    configDetails.append(el("summary", "", "高级设置与备用模型"),configGrid);
    const connectionLines=new Map();const modelConnections=el("div","h3-models");
    for(const[kind,label]of[["text","文本"],["image","图像"],["video","视频"],["rules","规则"]]){const row=el("div","h3-model-line");const name=el("span","h3-grow");row.append(el("strong","",label),name);connectionLines.set(kind,name);modelConnections.append(row);}
    const autoVideoRow=el('label','h3-plan-toggle'),autoVideoToggle=el('input');autoVideoToggle.type='checkbox';autoVideoToggle.setAttribute('role','switch');autoVideoToggle.setAttribute('aria-label','生图后生成视频');autoVideoRow.append(autoVideoToggle,el('span','','生图后生成视频'));autoVideoRow.title='仅AI分镜：保留计划首轮仍先停在剧本。关闭后生图完成停一次，再点继续可生成视频。';autoVideoToggle.onchange=()=>write('生图后生成视频',autoVideoToggle.checked);
    settingsSection.append(modeRow, modeHint, durationBar,targetDurationRow,durationGrid,aspectRow,aspectNote,videoScaleRow,imageSampling,imageSizing,imageSizingNote,imageSizingError,basicGrid,seedRow,autoVideoRow,referenceUpscale.element,modelConnections,configDetails);

    const previewSection = el("section","h3-preview-section");
    const previewBar = el("div", "h3-row");
    const previewViewBar=el("div","h3-row");const previewViewButtons=new Map();
    const previewModeSlider=el('div','h3-duration-mode'),previewModeIndicator=el('span','h3-duration-indicator');previewModeSlider.setAttribute('role','radiogroup');previewModeSlider.setAttribute('aria-label','预览范围');previewModeSlider.append(previewModeIndicator);previewViewBar.append(previewModeSlider);
    for(const[kind,label]of[["single","单镜"],["all","全部分镜"]]){const button=el("button","h3-preview-mode",label);button.type="button";button.setAttribute('role','radio');button.onclick=()=>setPreviewMode(kind);previewViewButtons.set(kind,button);previewModeSlider.append(button);}
    const previousButton = el("button", "", "上一镜");
    const nextButton = el("button", "", "下一镜");
    const sceneChooser = el("select", "h3-grow"); sceneChooser.setAttribute("aria-label", "选择预览镜头");
    const followButton = el("button", "", "跟随进度");
    followButton.onclick = () => {followProgress = true; if (snapshot?.scene_index > 0) selected = snapshot.scene_index - 1; renderPreview();};
    previousButton.onclick = () => {followProgress = false; selected = Math.max(0, selected - 1); renderPreview();};
    nextButton.onclick = () => {followProgress = false; selected = Math.min(sceneCount() - 1, selected + 1); renderPreview();};
    sceneChooser.onchange = () => {followProgress = false; selected = Number(sceneChooser.value); renderPreview();};
    previewBar.append(previousButton, sceneChooser, nextButton, followButton);
    const progressState = el("div", "h3-state", "尚未开始");
    progressState.setAttribute("aria-live", "polite");
    const currentVideoTime=el("span","h3-time");previewBar.append(currentVideoTime);
    const imageBox = el("div", "h3-preview");
    const image = el("img"); image.alt = "当前镜头的参考图"; image.hidden = true;
    const imagePlaceholder = el("span", "h3-muted", "分镜图片");
    const firstFrameBox = el("div", "h3-frame");
    const firstFrameLabel = el("span", "h3-frame-label", "开始帧");
    firstFrameBox.append(firstFrameLabel, image, imagePlaceholder);
    const endFrameBox = el("div", "h3-frame");
    const endFrameLabel = el("span", "h3-frame-label", "结束帧");
    const endImage = el("img"); endImage.alt = "当前镜头的结束帧"; endImage.hidden = true;
    const endImagePlaceholder = el("span", "h3-muted", "等待结束帧");
    endFrameBox.append(endFrameLabel,endImage,endImagePlaceholder); endFrameBox.hidden = true;
    imageBox.append(firstFrameBox,endFrameBox);
    image.ondblclick = () => {if (image.src && !image.hidden)openImageViewer(sceneImageItems,`分镜${selected+1}`);};
    image.style.cursor = "zoom-in";
    endImage.ondblclick = () => {if (endImage.src && !endImage.hidden)openImageViewer(()=>[{id:"结束帧",url:endImage.src}],"结束帧");};
    const videoBox = el("div", "h3-preview");
    const video = el("video"); video.controls = true; video.preload = "metadata"; video.playsInline = true; video.hidden = true;
    const videoPlaceholder = el("span", "h3-muted", "镜头视频"); videoBox.append(video, videoPlaceholder);
    const singlePreview=el("div","h3-single-preview");singlePreview.append(imageBox,videoBox);
    const allScenes=el("div","h3-all-scenes");allScenes.setAttribute("aria-label","全部分镜预览");allScenes.hidden=true;
    allScenes.tabIndex=0;
    const manualBar=el("div","h3-row h3-manual-bar");manualBar.hidden=true;
    const manualChoose=el("button","","选择图片"),manualClear=el("button","","清空本图"),manualClearAll=el("button","","清空分镜图"),manualStatus=el("span","h3-muted h3-manual-status","选择槽位后粘贴，或拖入多张图片。");
    manualChoose.type=manualClear.type=manualClearAll.type="button";manualChoose.onclick=()=>{delete manualFileInput.dataset.targetSlot;manualFileInput.click();};manualClear.onclick=()=>clearManualImage(selected);manualClearAll.onclick=()=>clearStoryboardImages();
    const manualFileInput=el("input");manualFileInput.type="file";manualFileInput.accept="image/*";manualFileInput.multiple=true;manualFileInput.hidden=true;
    manualFileInput.onchange=()=>{const target=manualFileInput.dataset.targetSlot;delete manualFileInput.dataset.targetSlot;uploadManualFiles(manualFileInput.files,target==null?selected:Number(target));};
    manualBar.append(manualChoose,manualClear,manualClearAll,manualStatus,manualFileInput);
    const allScrollBar=el("div","h3-row h3-all-scroll");allScrollBar.hidden=true;
    const scrollPrevious=el("button","","←"),scrollNext=el("button","","→");scrollPrevious.type="button";scrollNext.type="button";
    scrollPrevious.setAttribute("aria-label","向左滚动分镜");scrollNext.setAttribute("aria-label","向右滚动分镜");allScrollBar.append(scrollPrevious,el("span","h3-muted h3-grow","左右滚动查看分镜"),scrollNext);
    function updateSceneScroll(){const overflow=allScenes.scrollWidth>allScenes.clientWidth+2;allScrollBar.hidden=previewMode!=="all"||!overflow;scrollPrevious.disabled=allScenes.scrollLeft<=1;scrollNext.disabled=allScenes.scrollLeft>=allScenes.scrollWidth-allScenes.clientWidth-1;}
    const moveSceneScroll=direction=>{followProgress=false;allScenes.scrollBy({left:direction*Math.max(230,allScenes.clientWidth*.8),behavior:matchMedia("(prefers-reduced-motion:reduce)").matches?"auto":"smooth"});};
    scrollPrevious.onclick=()=>moveSceneScroll(-1);scrollNext.onclick=()=>moveSceneScroll(1);
    allScenes.addEventListener("scroll",updateSceneScroll,{passive:true,signal:controller.signal});
    const allSceneRows=new Map();const allSceneVideo=el("video");allSceneVideo.controls=true;allSceneVideo.playsInline=true;allSceneVideo.preload="none";
    let activeAllScene=null;
    const updateAllScenePlayback=()=>{const view=allSceneRows.get(activeAllScene);if(!view)return;const playing=!allSceneVideo.paused&&!allSceneVideo.ended;view.play.hidden=false;view.play.textContent=playing?"Ⅱ":"▶";view.play.dataset.playing=String(playing);view.play.setAttribute("aria-label",`${playing?"暂停":"播放"}镜头${view.index+1}视频`);view.play.title=playing?"暂停":"播放视频";if(view.regenerate)view.regenerate.hidden=playing||modeSelect.value!=="AI生成分镜"||!sceneAt(view.index)?.image;};
    for(const name of["play","pause","ended"])allSceneVideo.addEventListener(name,updateAllScenePlayback,{signal:controller.signal});
    allSceneVideo.addEventListener("loadedmetadata",()=>{const view=allSceneRows.get(activeAllScene);if(view){view.mediaRatio=allSceneVideo.videoWidth/allSceneVideo.videoHeight;sizeSceneMedia(view);}}, {signal:controller.signal});
    const promptDetails = el("details"); const promptText = el("div", "h3-prompt");
    const referenceBindingLabel = el("div", "h3-muted");
    const videoPromptEditor = el("textarea", "h3-prompt-editor"); videoPromptEditor.setAttribute("aria-label", "本镜视频提示词");
    const imagePromptEditor = el("textarea", "h3-prompt-editor"); imagePromptEditor.setAttribute("aria-label", "本镜分镜图提示词");
    promptText.append(referenceBindingLabel, el("label", "", "视频提示词"), videoPromptEditor, el("label", "", "分镜图提示词"), imagePromptEditor);
    promptDetails.append(el("summary", "", "编辑本镜提示词"), promptText);
    const actionRow = el("div", "h3-row");
    const continueButton = el("button", "", "继续生成视频");
    const redoButton = el("button", "", "重做本镜");
    redoButton.title='随机模式更换本镜的视频种子；固定模式沿用本镜种子。已完成的其他独立镜头保留。末帧续接时，后续依赖镜头也需重做。';
    const actionState = el("span", "h3-muted h3-grow");
    actionRow.append(continueButton, redoButton, actionState);
    const finalBox = el("div", "h3-final");
    const finalVideo = el("video"); finalVideo.controls = true; finalVideo.preload = "metadata"; finalVideo.playsInline = true;
    function sizeFinalVideo(){scheduleWorkspaceFit();}
    function setPixelStyle(element,name,next){const value=`${Math.round(next)}px`;if(element.style.getPropertyValue(name)!==value)element.style.setProperty(name,value);}
    function alignFinalMedia(){
        if(!root.isConnected||overviewExpanded)return;
        const first=Array.from(allSceneRows.values())[0],isAll=previewMode==="all",frame=isAll?first?.player:imageBox,head=isAll?first?.row.querySelector(".h3-scene-card-head"):null;
        const finalRatio=finalVideo.videoWidth&&finalVideo.videoHeight?finalVideo.videoWidth/finalVideo.videoHeight:sceneMediaRatio(null);
        const frameHeight=previewFrameHeight,width=Math.max(1,Math.min(Math.round(frameHeight*finalRatio),finalColumn.clientWidth-25));
        setPixelStyle(finalColumn,"--h3-final-frame-height",frameHeight);setPixelStyle(finalColumn,"--h3-final-frame-width",width);
        const scale=root.getBoundingClientRect().width/root.offsetWidth||1,columnTop=finalColumn.getBoundingClientRect().top,frameRect=frame?.getBoundingClientRect();if(!frameRect?.height)return;
        setPixelStyle(finalHeading,"height",head?.offsetHeight||26);
        setPixelStyle(finalColumn,"--h3-final-frame-gap",6);
        const toolsHeight=finalTools.getBoundingClientRect().height/scale;
        setPixelStyle(finalColumn,"--h3-final-top-offset",Math.max(0,(frameRect.top-columnTop)/scale-toolsHeight-finalHeading.offsetHeight-6));
        alignDeckBridges();
    }
    finalVideo.addEventListener("loadedmetadata",sizeFinalVideo);
    const finalPlaceholder=el("div","h3-final-placeholder","待合片");finalPlaceholder.setAttribute("aria-live","polite");
    const finalHeading=el("div","h3-row");const autoOpenLabel=el("label","h3-plan-toggle"),autoOpenToggle=el("input");autoOpenToggle.type="checkbox";autoOpenToggle.setAttribute("role","switch");autoOpenToggle.checked=!!node.properties?.h3_auto_open_output;autoOpenToggle.onchange=()=>{node.properties||={};node.properties.h3_auto_open_output=autoOpenToggle.checked;autoOpenToggle.setAttribute("aria-checked",String(autoOpenToggle.checked));};autoOpenLabel.append(autoOpenToggle,el("span","","完成后打开文件夹"));finalHeading.classList.add("h3-final-heading");finalHeading.append(el("strong","h3-grow","成片"));const finalMedia=el("div","h3-final-media");finalMedia.append(finalPlaceholder,finalVideo);finalBox.append(finalHeading,finalMedia);
    const resultTimingRow=el("div","h3-time-row");const videoTotalTime=el("span","h3-time"),joiningTime=el("span","h3-time"),taskTotalTime=el("span","h3-time");resultTimingRow.append(videoTotalTime,joiningTime,taskTotalTime);
    const outputRow=el("div","h3-row h3-output-row");const openOutput=el("button","","打开保存文件夹");openOutput.type="button";
    const copyOutput=el("button","","复制路径");copyOutput.type="button";copyOutput.hidden=true;
    const outputPath=el("span","h3-output-path");outputPath.setAttribute("aria-live","polite");let knownOutputPath="";
    function showOutputPath(path){knownOutputPath=String(path||"");outputPath.textContent=knownOutputPath?`已保存：${knownOutputPath}`:"";outputPath.title=knownOutputPath;copyOutput.hidden=!knownOutputPath;}
    copyOutput.onclick=async()=>{try{await copyPlainText(knownOutputPath);copyOutput.textContent="已复制";}catch(error){outputPath.textContent=error.message||"复制失败";}};
    openOutput.onclick=async()=>{
        openOutput.disabled=true;
        try{
            const payload=snapshot?.run_id?{run_id:snapshot.run_id}:{};
            const response=await api.fetchApi("/h3-director/open-output",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload),signal:controller.signal});
            const result=await response.json();if(disposed)return;
            if(!response.ok||result.ok===false)throw Error(result.error||result.message||`打开失败（${response.status}）`);
            if(result.path)showOutputPath(result.path);
        }catch(error){if(!disposed){outputPath.textContent=error.message||"无法打开保存文件夹";outputPath.title=knownOutputPath;}}
        finally{if(!disposed)openOutput.disabled=false;}
    };
    outputRow.append(openOutput,outputPath,copyOutput);
    const previewDeck=el("div","h3-preview-deck"),shotPanel=el("div","h3-shot-panel"),finalColumn=el("div","h3-final-column");
    shotPanel.append(previewViewBar,previewBar,progressState,singlePreview,manualBar,allScrollBar,allScenes,promptDetails,actionRow);
    const finalTools=el("div","h3-final-tools");finalTools.append(outputRow,resultTimingRow,autoOpenLabel);finalColumn.append(finalTools,finalBox);previewDeck.append(shotPanel,finalColumn);previewSection.append(previewDeck);

    const releaseSection = el("section","h3-release-section");
    const releaseRow = el("div", "h3-row");
    const releaseButton = el("button", "", "释放模型与缓存");
    const releaseState = el("div", "h3-muted h3-grow"); releaseState.setAttribute("aria-live", "polite");
    releaseRow.append(releaseButton, releaseState); releaseSection.append(releaseRow);
    const imageColumn=el("div","h3-column h3-image-column");imageColumn.hidden=true;
    const imageSection=el("section");const imageHeading=el("div","h3-row");const imagesTotalTime=el("span","h3-time");const importAllImages=el("button","","全部导入 →");importAllImages.type="button";importAllImages.hidden=true;importAllImages.onclick=()=>importManualPrompts();imageHeading.append(el("strong","h3-grow","分镜生图"),imagesTotalTime,importAllImages);
    const imageSummaryState=el("div","h3-image-summary","等待规划");imageSummaryState.setAttribute("aria-live","polite");
    const imageTotalProgress=el("progress","h3-image-total");imageTotalProgress.max=100;imageTotalProgress.hidden=true;imageTotalProgress.setAttribute("aria-label","分镜图片完成比例");
    const imageList=el("div","h3-image-list");const imageEmpty=el("div","h3-muted","规划后逐张显示生图提示词与进度");
    imageSection.append(imageHeading,regenerationBar,imageSummaryState,imageTotalProgress,imageList,imageEmpty);imageColumn.append(imageSection);
    const imageJobViews=new Map();
    const layoutBar=el("div","h3-layout-bar");layoutBar.setAttribute("role","group");layoutBar.setAttribute("aria-label","工作台布局");
    const versionLabel=el("strong","h3-studio-title","小陈 MiniMax H3 多分镜工作台");versionLabel.append(el("small","",`V${H3_STUDIO_VERSION}`));layoutBar.append(versionLabel);
    const layoutButtons=new Map();for(const[mode,label]of[["horizontal","横向"],["vertical","纵向"]]){const button=el("button","",label);button.type="button";button.onclick=()=>applyLayout(mode);layoutButtons.set(mode,button);layoutBar.append(button);}
    const workspace=el("div","h3-workspace");const sourceColumn=el("div","h3-column h3-source-column");const resultColumn=el("div","h3-column h3-result-column");
    const sourceSplitter=makeSplitter("source"),imageSplitter=makeSplitter("image");
    const deckBridgeTop=el("span","h3-deck-bridge"),deckBridgeBottom=el("span","h3-deck-bridge");imageSplitter.append(deckBridgeTop,deckBridgeBottom);
    const sourceScroll=el("div","h3-source-scroll");sourceScroll.title="滚轮滚动设置，也可拖动右侧滚动条。";sourceScroll.append(videoInputBar,referenceSection,scriptSection,settingsSection);sourceColumn.append(sourceScroll,releaseSection);resultColumn.append(optimizedSection,previewSection);workspace.append(sourceColumn,sourceSplitter,resultColumn,imageSplitter,imageColumn);root.append(layoutBar,workspace);
    for(const column of[sourceColumn,resultColumn,imageColumn])column.title="内容区滚动 · 空白处滚轮缩放画布";
    function panelHeight(){return 440;}
    function layoutMinimumSize(){return [activeLayout==="horizontal"?(imagePlanningMode()?1200:760):560,540];}
    function updateOverviewHeight(){
        if(!root.isConnected)return;const available=Math.max(280,workspace.clientHeight||root.clientHeight-layoutBar.offsetHeight-26);
        root.style.setProperty("--h3-reference-height",`${Math.max(280,Math.min(600,available*.5))}px`);root.style.setProperty("--h3-reference-image-height",`${Math.max(190,Math.min(420,available*.5-70))}px`);
        root.style.setProperty("--h3-result-height",`${available}px`);root.style.setProperty("--h3-overview-height",`${Math.max(280,available-optimizedBar.offsetHeight-20)}px`);
    }
    function updateColumnWidths(){
        const width=workspace.clientWidth||Math.max(700,(node.size?.[0]||1600)-50),ai=imagePlanningMode();
        const saved=node.properties?.h3_column_widths||{};const imageWidth=Math.max(270,Math.min(Number(saved.image)||380,width*.38));
        const sourceWidth=Math.max(360,Math.min(Number(saved.source)||360,width-(ai?imageWidth+28:14)-350));
        workspace.style.setProperty("--h3-source-width",`${sourceWidth}px`);workspace.style.setProperty("--h3-image-width",`${imageWidth}px`);
    }
    let workspaceFitFrame=0,workspaceFitRunning=false;
    function scheduleWorkspaceFit(){if(disposed||workspaceFitFrame)return;workspaceFitFrame=requestAnimationFrame(()=>{workspaceFitFrame=0;fitWorkspaceHeight();});}
    function fitWorkspaceHeight(){if(disposed||workspaceFitRunning||!root.isConnected||node.flags?.collapsed)return;workspaceFitRunning=true;try{root.dataset.compactHeight=String(workspace.clientHeight<760);sourceColumn.dataset.compactSettings=String(workspace.clientHeight<1160);updateOverviewHeight();updateColumnWidths();fitPreviewFrame();alignSceneHeaders();updateSceneScroll();alignFinalMedia();alignDeckBridges();for(const view of imageJobViews.values())if(view.entry.open)sizeImagePrompt(view);if(!failureLocationMounted){failureLocationMounted=true;renderFailure();}}finally{workspaceFitRunning=false;}}
    function alignSceneHeaders(){if(previewMode!=="all"||!allSceneRows.size)return;root.style.removeProperty("--h3-scene-heading-height");let height=26;for(const view of allSceneRows.values()){const head=view.row.querySelector(".h3-scene-card-head");height=Math.max(height,head.scrollHeight,head.getBoundingClientRect().height/(root.getBoundingClientRect().width/root.offsetWidth||1));}setPixelStyle(root,"--h3-scene-heading-height",Math.ceil(height));}
    function fitPreviewFrame(){
        if(!root.isConnected||overviewExpanded)return;
        const columnHeight=Math.round(resultColumn.clientHeight),columnWidth=Math.round(resultColumn.clientWidth);if(!columnHeight||!columnWidth)return;
        const stacked=columnWidth<590;
        // Width depends on the fixed viewport only. Media size never feeds back
        // into the column width or the height budget.
        const toolbar=previewViewBar.offsetHeight+previewBar.offsetHeight+progressState.offsetHeight+(manualBar.hidden?0:manualBar.offsetHeight)+38;
        const shotFixed=toolbar+50+44+promptDetails.offsetHeight+actionRow.offsetHeight+38;
        const finalFixed=toolbar+50+46;
        const available=columnHeight-optimizedSection.offsetHeight-Math.max(shotFixed,finalFixed)-24;
        const finalRatio=finalVideo.videoWidth&&finalVideo.videoHeight?finalVideo.videoWidth/finalVideo.videoHeight:sceneMediaRatio(null);
        const maxFinalWidth=Math.max(80,columnWidth*.48-25);
        const height=Math.round(Math.max(120,Math.min(900,available,maxFinalWidth/finalRatio)));
        const finalWidth=Math.round(Math.max(155,Math.min(columnWidth*.48,height*finalRatio+25)));
        setPixelStyle(previewDeck,"--h3-final-column-width",finalWidth);
        if(Math.abs(height-previewFrameHeight)<2)return;
        previewFrameHeight=height;setPixelStyle(root,"--h3-preview-frame-height",height);
        for(const view of allSceneRows.values())setPixelStyle(view.row,"--h3-media-width",Math.max(1,height*sceneMediaRatio(view)));
    }
    function alignDeckBridges(){
        if(!root.isConnected||imageSplitter.hidden||activeLayout!=="horizontal")return;
        const scale=root.getBoundingClientRect().width/root.offsetWidth||1,deck=previewDeck.getBoundingClientRect(),split=imageSplitter.getBoundingClientRect();
        const left=(deck.right-split.left)/scale,width=(split.width/2-deck.right+split.left)/scale;
        for(const[bridge,y]of[[deckBridgeTop,deck.top],[deckBridgeBottom,deck.bottom-1*scale]]){bridge.style.left=`${left}px`;bridge.style.width=`${Math.max(0,width)}px`;bridge.style.top=`${(y-split.top)/scale}px`;}
    }
    function rememberPanelHeight(height){node.properties||={};node.properties.h3_panel_height=Math.max(440,Number(height)||440);}
    function makeSplitter(kind){
        const split=el("div","h3-splitter");split.tabIndex=0;split.setAttribute("role","separator");split.setAttribute("aria-orientation","vertical");split.setAttribute("aria-label",kind==="source"?"调整原稿区宽度":"调整生图区宽度");
        const change=next=>{node.properties||={};node.properties.h3_column_widths={...node.properties.h3_column_widths,[kind]:Math.max(kind==="source"?360:280,next)};updateColumnWidths();node.graph?.setDirtyCanvas?.(true,true);};
        split.addEventListener("keydown",event=>{if(!["ArrowLeft","ArrowRight"].includes(event.key))return;event.preventDefault();const current=kind==="source"?sourceColumn.clientWidth:imageColumn.clientWidth;change(current+(event.key==="ArrowRight"?1:-1)*(kind==="source"?1:-1)*20);},{signal:controller.signal});
        split.addEventListener("pointerdown",event=>{event.preventDefault();event.stopPropagation();const initial=kind==="source"?sourceColumn.clientWidth:imageColumn.clientWidth,start=event.clientX,scale=root.getBoundingClientRect().width/root.offsetWidth||1;split.setPointerCapture(event.pointerId);split.dataset.dragging="true";const move=event=>change(initial+(event.clientX-start)/scale*(kind==="source"?1:-1));const finish=()=>{delete split.dataset.dragging;split.removeEventListener("pointermove",move);split.removeEventListener("pointerup",finish);split.removeEventListener("pointercancel",finish);};split.addEventListener("pointermove",move);split.addEventListener("pointerup",finish);split.addEventListener("pointercancel",finish);},{signal:controller.signal});
        return split;
    }
    for(const edge of["n","e","s","w"]){
        const handle=el("div","h3-edge");handle.dataset.edge=edge;handle.setAttribute("aria-hidden","true");handle.title=edge==="s"?"拖动底边调整工作台高度":"拖动边缘调整工作台大小";if(edge==="s"){const grip=el("span","h3-resize-grip","···");handle.append(grip);}root.append(handle);
        handle.addEventListener("pointerdown",event=>{event.preventDefault();event.stopPropagation();const initialSize=[...node.size],initialPos=[...node.pos],start=[event.clientX,event.clientY],scale=root.getBoundingClientRect().width/root.offsetWidth||1;handle.setPointerCapture(event.pointerId);const move=event=>{const dx=(event.clientX-start[0])/scale,dy=(event.clientY-start[1])/scale,min=layoutMinimumSize();const width=Math.max(min[0],initialSize[0]+(edge==="e"?dx:edge==="w"?-dx:0)),height=Math.max(min[1],initialSize[1]+(edge==="s"?dy:edge==="n"?-dy:0));if(edge==="w")node.pos[0]=initialPos[0]+initialSize[0]-width;if(edge==="n")node.pos[1]=initialPos[1]+initialSize[1]-height;node.properties||={};rememberPanelHeight(height-100);node.setSize([width,height]);node.graph?.setDirtyCanvas?.(true,true);};const finish=()=>{handle.removeEventListener("pointermove",move);handle.removeEventListener("pointerup",finish);handle.removeEventListener("pointercancel",finish);};handle.addEventListener("pointermove",move);handle.addEventListener("pointerup",finish);handle.addEventListener("pointercancel",finish);},{signal:controller.signal});
    }
    function applyLayout(mode){
        activeLayout=mode==="vertical"?"vertical":"horizontal";root.dataset.layout=activeLayout;const ai=imagePlanningMode();root.dataset.aiImages=String(ai);imageColumn.hidden=!ai;imageSplitter.hidden=!ai;node.properties||={};node.properties.h3_director_layout=activeLayout;
        for(const[kind,button]of layoutButtons)button.setAttribute("aria-pressed",String(kind===activeLayout));
        const min=layoutMinimumSize();node.setSize([Math.max(min[0],node.size?.[0]||1600),Math.max(540,node.size?.[1]||1100)]);node.graph?.setDirtyCanvas?.(true,true);requestAnimationFrame(fitWorkspaceHeight);
    }

    let viewerItems=()=>[],viewerIndex=0,viewerPreviousFocus=null;
    const imageViewer=el("div","h3-image-viewer");imageViewer.hidden=true;imageViewer.tabIndex=-1;imageViewer.setAttribute("role","dialog");imageViewer.setAttribute("aria-modal","true");imageViewer.setAttribute("aria-label","图片预览");
    const viewerImage=el("img"),viewerCaption=el("div","h3-viewer-caption"),viewerClose=el("button","h3-viewer-close","×"),viewerPrevious=el("button","h3-viewer-prev","‹"),viewerNext=el("button","h3-viewer-next","›");viewerImage.alt="";viewerImage.decoding="async";viewerClose.setAttribute("aria-label","关闭图片预览");viewerPrevious.setAttribute("aria-label","上一张图片");viewerNext.setAttribute("aria-label","下一张图片");for(const button of[viewerClose,viewerPrevious,viewerNext])button.type="button";imageViewer.append(viewerImage,viewerCaption,viewerClose,viewerPrevious,viewerNext);root.append(imageViewer);
    function sceneImageItems(){return Array.from({length:sceneCount()},(_,index)=>({id:`分镜${index+1}`,url:mediaURL(isManualMode()?manualImages()[index]:sceneAt(index)?.original_image||sceneAt(index)?.image)}));}
    function currentViewerItems(){return viewerItems().filter(item=>item.url);}
    function renderImageViewer(){const items=currentViewerItems();if(!items.length){closeImageViewer();return;}viewerIndex=Math.max(0,Math.min(items.length-1,viewerIndex));const item=items[viewerIndex];if(viewerImage.getAttribute("src")!==item.url)viewerImage.src=item.url;viewerImage.alt=item.id;viewerCaption.textContent=`${item.id} · ${viewerIndex+1} / ${items.length}${viewerImage.complete&&viewerImage.naturalWidth?' · '+viewerImage.naturalWidth+' × '+viewerImage.naturalHeight+' px':''}`;viewerPrevious.disabled=viewerIndex===0;viewerNext.disabled=viewerIndex===items.length-1;}
    viewerImage.onload=renderImageViewer;
    function openImageViewer(items,identity){viewerItems=items;const list=currentViewerItems();if(!list.length)return;viewerPreviousFocus=document.activeElement;viewerIndex=Math.max(0,list.findIndex(item=>item.id===identity));imageViewer.style.top=`${root.scrollTop}px`;imageViewer.style.height=`${root.clientHeight}px`;imageViewer.hidden=false;renderImageViewer();imageViewer.focus({preventScroll:true});}
    function closeImageViewer(){imageViewer.hidden=true;viewerImage.removeAttribute("src");viewerItems=()=>[];viewerPreviousFocus?.focus?.({preventScroll:true});}
    viewerClose.onclick=closeImageViewer;viewerPrevious.onclick=()=>{viewerIndex--;renderImageViewer();};viewerNext.onclick=()=>{viewerIndex++;renderImageViewer();};
    imageViewer.addEventListener("pointerdown",event=>{event.stopPropagation();});imageViewer.addEventListener("keydown",event=>{if(event.key==="Escape"){event.preventDefault();closeImageViewer();}else if(event.key==="ArrowLeft"||event.key==="ArrowRight"){event.preventDefault();viewerIndex+=event.key==="ArrowLeft"?-1:1;renderImageViewer();}else if(event.key==="Tab"){event.preventDefault();const buttons=[viewerClose,viewerPrevious,viewerNext].filter(button=>!button.disabled);const index=buttons.indexOf(document.activeElement);buttons[(index+(event.shiftKey?-1:1)+buttons.length)%buttons.length].focus();}});
    function maybeAutoOpenOutput(persisted){const run=snapshot?.run_id;if(persisted||!run||!snapshot.final_video||!["completed","done"].includes(snapshot.status)||!node.properties?.h3_auto_open_output||node.properties.h3_auto_opened_run===run)return;node.properties.h3_auto_opened_run=run;openOutput.onclick();}
    function prepareReferences(){
        if(disposed||node.mode===2||node.mode===4)return true;
        if(isManualMode())write("保留分镜计划",true);
        const textVideo=isTextVideo();if(textVideo)textVideoWeights();
        const sizingError=textVideo?'':imageSizeError(),upscaleError=textVideo?'':referenceUpscale.validate();
        if(sizingError||upscaleError){const target=sizingError?imageSizing:referenceUpscale.element;preflightFailure={stage:"settings",field:sizingError?"分镜图长边":"衔接超分配置",message:sizingError||upscaleError};renderImageSizing();referenceUpscale.render();renderFailure();target.scrollIntoView({block:"nearest",inline:"nearest"});return false;}
        if(typeof node.properties?.h3_optimized_import_text==="string"){
            const imported=importedPlanData(),error=importedPlanError(imported);
            if(error){preflightFailure={stage:"settings",field:"导入优化计划",message:error};actionState.textContent=error;renderFailure();return false;}
            if(["running","generating"].includes(snapshot?.status)){actionState.textContent="当前任务仍在运行，请停止或等待完成后使用粘贴稿。";return false;}
            imported.optimized_script=node.properties.h3_optimized_import_text;write("导入优化计划",JSON.stringify(imported));write("保留分镜计划",true);write("继续任务","");write("重做镜头",0);write("计划编辑","{}");write("分镜覆盖","{}");write("重生图片","[]");write("生图重做范围","保留现有图片");node.properties.h3_import_submitted=true;preflightFailure=null;renderFailure();return true;
        }
        preflightFailure=null;
        if(textVideo&&!textarea.value.trim()){preflightFailure={stage:'settings',field:'剧本',message:'请先填写剧本。'};renderFailure();textarea.focus();return false;}
        if(textVideo&&/@(?:图|参考图)\s*\d+|<Picture\s+\d+>/i.test(textarea.value)){preflightFailure={stage:'settings',field:'剧本',message:'文生视频不使用图片引用，请将图号改为人物、动作和场景描述。'};renderFailure();textarea.focus();return false;}
        if(textVideo&&value('画幅','跟随图片')==='跟随图片'){preflightFailure={stage:'settings',field:'画幅',message:'文生视频请选择明确画幅或自定义尺寸。'};renderFailure();return false;}
        if(!textVideo&&!references.length){preflightFailure={stage:"settings",field:"参考图集",message:"请先添加参考图",suggestion:"点击参考图区的添加按钮，或拖入、粘贴图片。"};renderFailure();gallery.scrollIntoView({block:"nearest",inline:"nearest"});return false;}
        const result=textVideo?{changed:false,valid:true,script:textarea.value}:normalizeReferenceMentions(textarea.value,references.length);
        if(result.changed){textarea.value=result.script;bindDraftReferences();renderMentions();if(!["running","generating"].includes(snapshot?.status))invalidateSource();}
        if(result.valid){delete gallery.dataset.referenceError;textarea.removeAttribute("aria-invalid");uploadState.classList.remove("h3-reference-error");if(uploadState.dataset.referenceError){uploadState.textContent="";delete uploadState.dataset.referenceError;}
            if(value("时长模式","手动设置")==="AI自动分配"){
                const total=Number(targetDuration.value),count=durations.length;
                if(!Number.isFinite(total)||total<count||total>15*count||Math.abs(total*2-Math.round(total*2))>1e-7){preflightFailure={stage:"settings",field:"目标总秒数",message:`${count}个镜头的目标总时长需为${count}～${15*count}秒，按0.5秒填写。`,suggestion:"调整总时长后重新运行。每镜时长范围为1～15秒。"};renderFailure();targetDuration.focus();targetDuration.scrollIntoView({block:"nearest",inline:"nearest"});return false;}
            }
            if(value("保留分镜计划",false)&&planCanResume())write("继续任务",snapshot.run_id);
            renderFailure();return true;}

        gallery.dataset.referenceError="true";uploadState.textContent=result.error;uploadState.classList.add("h3-reference-error");uploadState.dataset.referenceError="true";textarea.setAttribute("aria-invalid","true");gallery.scrollIntoView({block:"nearest",inline:"nearest"});textarea.focus({preventScroll:true});setTimeout(()=>{if(!disposed)delete gallery.dataset.referenceError;},1600);return false;
    }
    function saveReferences(removedIds=[]) {
        preflightFailure=null;
        bindDraftReferences(textarea.value,removedIds);
        aspectReference();aspectPicker.hidden=true;
        invalidateSource();
    }
    function saveDurations() {
        preflightFailure=null;
        write("分镜数量", durations.length);
        write("每镜秒数", durations.join(","));
        if(value("时长模式","手动设置")==="AI自动分配"){const target=Number(value("目标总秒数",30)),bounded=Math.max(durations.length,Math.min(durations.length*15,target));if(target!==bounded)write("目标总秒数",bounded);}
        invalidateSource();
    }
    function sceneCount() {return Math.max(durations.length, snapshot?.scene_count || 0, snapshot?.scenes?.length || 0, 1);}
    function preserveInputDraft(mode){
        node.properties||={};node.properties.h3_video_input_drafts||={};
        node.properties.h3_video_input_drafts[mode]={fields:Object.fromEntries(['分镜覆盖','计划编辑','导入优化计划','重生图片','生图重做范围','保留分镜计划'].map(name=>[name,value(name)])),optimized:node.properties.h3_optimized_import_text,planDraft:node.properties.h3_plan_edit_draft};
    }
    function textVideoWeights(restore=false){
        node.properties||={};const backups=node.properties.h3_text_video_weights||{};
        if(restore){for(const[id,prior]of Object.entries(backups)){const source=id==='self'?node:node.graph?.getNodeById?.(Number(id))??node.graph?.getNodeById?.(id);const widget=source?.widgets?.find(item=>item.name==='加速权重');if(widget&&widget.value==='独立LoRA'){widget.value=prior;if(widget.inputEl)widget.inputEl.value=String(prior);}}delete node.properties.h3_text_video_weights;return;}
        if(!isTextVideo())return;
        for(const source of new Set([node,configSource('video')].filter(Boolean))){const widget=source.widgets?.find(item=>item.name==='加速权重');if(!widget)continue;const id=source===node?'self':String(source.id);if(!(id in backups))backups[id]=widget.value;if(widget.value!=='独立LoRA'){widget.value='独立LoRA';if(widget.inputEl)widget.inputEl.value='独立LoRA';source.graph?.setDirtyCanvas?.(true,true);}}
        node.properties.h3_text_video_weights=backups;
    }
    function switchVideoInput(next){
        if(!VIDEO_INPUTS.includes(next)||queueing||['running','generating'].includes(snapshot?.status))return;
        const previous=isTextVideo()?'文生视频':'图生视频';if(previous===next)return;
        preserveInputDraft(previous);node.properties||={};
        if(next==='文生视频'){node.properties.h3_image_video_aspect=value('画幅','跟随图片');node.properties.h3_image_video_mode=modeSelect.value;write('画幅',node.properties.h3_text_video_aspect||'16:9 横屏');}
        else{node.properties.h3_text_video_aspect=value('画幅','16:9 横屏');textVideoWeights(true);write('画幅',node.properties.h3_image_video_aspect||'跟随图片');}
        write('视频输入',next);connectionSignature='';connectionData=null;invalidateSource();
        delete node.properties.h3_optimized_import_text;write('导入优化计划','{}');
        const draft=node.properties.h3_video_input_drafts[next];if(draft){for(const[name,nextValue]of Object.entries(draft.fields||{}))if(nextValue!==undefined)write(name,nextValue);if(typeof draft.optimized==='string')node.properties.h3_optimized_import_text=draft.optimized;if(draft.planDraft!==undefined)node.properties.h3_plan_edit_draft=draft.planDraft;}
        write('继续任务','');write('重做镜头',0);textVideoWeights();if(!isTextVideo())renderReferences();renderMode();renderPreview();refreshModelConnections(false);node.graph?.setDirtyCanvas?.(true,true);
    }
    function renderVideoInput(){
        const text=isTextVideo(),busy=queueing||['running','generating'].includes(snapshot?.status);root.dataset.textVideo=String(text);
        videoInputIndicator.style.transform=`translateX(${text?100:0}%)`;for(const[mode,button]of videoInputButtons){button.setAttribute('aria-checked',String((mode==='文生视频')===text));button.tabIndex=((mode==='文生视频')===text)?0:-1;button.disabled=busy||!widgets.has('视频输入');}
        for(const element of[referenceSection,modeRow,modeHint,imageSampling,imageSizing,imageSizingNote,imageSizingError,imageSeedRow,autoVideoRow,referenceUpscale.element])element.classList.add('h3-text-video-hidden');
        for(const name of['镜头衔接','末帧处理','适配方式','执行范围','原作者模型'])configFields.get(name)?.classList.add('h3-text-video-hidden');
        scriptBar.querySelector('.h3-muted')?.classList.add('h3-text-video-hidden');
        textarea.title=text?'输入人物、场景、动作与对白。':'图1、参考图2或image 3自动关联；也可用@选择参考图。';scriptBar.title=text?'原始视频剧本':'直接输入图号自动关联，也支持粘贴参考图。';
        connectionLines.get('image')?.parentElement?.classList.add('h3-text-video-hidden');
        const weight=configControls.get('加速权重');if(weight){for(const option of weight.options||[])option.disabled=text&&option.value!=='独立LoRA';weight.disabled=busy||text;}
        aspectButtons.get('跟随图片').hidden=text;if(text)aspectPicker.hidden=true;
    }
    function isManualMode(){return !isTextVideo()&&modeSelect.value==="手动分镜图";}
    function aiImageEditingMode(){return !isTextVideo()&&modeSelect.value==="AI生成分镜"&&!!value("保留分镜计划",false)&&!!snapshot?.run_id&&!draftChanged;}
    function imageEditingMode(){return isManualMode()||aiImageEditingMode();}
    function imageEditSlots(){try{const raw=JSON.parse(String(value("手动分镜图片","[]")));return Array.isArray(raw)?raw.slice(0,sceneCount()+1):[];}catch{return[];}}
    function storyboardImage(index){
        if(isManualMode())return manualImages()[index]||null;
        const entry=imageEditSlots()[index];
        if(entry&&snapshot?.run_id&&entry._run_id===snapshot.run_id&&snapshot.image_edit_receipts?.[String(index)]!==entry._edit_id)return entry.clear?null:normalizeManualImage(entry);
        return index===sceneCount()?snapshot?.scenes?.at(-1)?.end_image||null:sceneAt(index)?.image||null;
    }
    function imagePlanningMode(){return !isTextVideo()&&["AI生成分镜","手动分镜图","末帧高清续接"].includes(modeSelect.value);}
    function manualImages(){
        try{const raw=JSON.parse(String(value("手动分镜图片",node.properties?.h3_manual_images||"[]")));return Array.isArray(raw)?Array.from({length:sceneCount()+1},(_,index)=>normalizeManualImage(raw[index])):[];}catch{return[];}
    }
    function normalizeManualImage(item){
        if(!item||typeof item.filename!=="string"||!item.filename.trim())return null;
        const parts=item.filename.replaceAll("\\","/").split("/");const filename=parts.pop(),subfolder=[item.subfolder||"",...parts].filter(Boolean).join("/");
        return {filename,subfolder,type:item.type||"input"};
    }
    function manualCanEdit(){return imageEditingMode()&&!manualUploading&&!queueing&&!["running","generating"].includes(snapshot?.status);}
    function manualNotice(text){manualStatus.textContent=text;manualStatus.title=text;}
    function persistManualImages(images,changed){
        if(aiImageEditingMode()){
            const edits=imageEditSlots();
            for(const index of changed){edits[index]={...(images[index]||{clear:true}),_run_id:snapshot.run_id,_edit_id:globalThis.crypto?.randomUUID?.()||`${Date.now()}-${Math.random().toString(36).slice(2)}`};}
            write("手动分镜图片",JSON.stringify(edits));write("继续任务",snapshot.run_id);
            const ids=selectedImageIds();for(const index of changed)ids.delete(index===sceneCount()?"结束帧":`分镜${index+1}`);write("重生图片",JSON.stringify([...ids]));if(!ids.size)write("生图重做范围","保留现有图片");
            for(const index of changed){const scene=sceneAt(index);if(scene){scene.image=images[index]||null;scene.original_image=images[index]||null;scene.video=null;scene.end_frame=null;scene.status=images[index]?"图片就绪":"等待";}const previous=sceneAt(index-1);if(previous&&value("镜头衔接")==="首尾帧约束"){previous.video=null;previous.end_frame=null;previous.end_image=images[index]||null;previous.status=previous.image?"图片就绪":"等待";}const job=snapshot.image_jobs?.find(job=>job.id===(index===sceneCount()?"结束帧":`分镜${index+1}`));if(job){job.image=images[index]||null;job.original_image=images[index]||null;job.inserted=!!images[index];job.status=images[index]?"完成":"等待";}}
            snapshot.final_video=null;snapshot.status="planned";snapshot.stage="planned";node.properties||={};node.properties.h3_director_preview=snapshot;
            renderPreview();node.graph?.setDirtyCanvas?.(true,true);return;
        }
        write("手动分镜图片",JSON.stringify(images.slice(0,sceneCount()+1)));write("保留分镜计划",true);
        if(snapshot?.run_id){
            write("继续任务",snapshot.run_id);
            for(const index of changed){
                const scene=sceneAt(index);if(scene){scene.image=images[index]||null;scene.video=null;scene.end_frame=null;scene.status=images[index]?"图片就绪":"等待";}
                const previous=sceneAt(index-1);if(previous&&value("镜头衔接")==="首尾帧约束"){previous.video=null;previous.end_frame=null;previous.end_image=images[index]||null;previous.status=previous.image?"图片就绪":"等待";}
                const job=snapshot.image_jobs?.find(job=>job.id===`分镜${index+1}`);if(job){job.image=images[index]||null;job.inserted=!!images[index];job.status=images[index]?"完成":"等待";}
                for(const binding of snapshot.reference_bindings||[])if([`分镜${index+1}`,`首帧${index+1}`].includes(binding.id))binding.image=images[index]||null;
            }
            snapshot.final_video=null;snapshot.status="planned";snapshot.stage=images.slice(0,sceneCount()).every(Boolean)?"planned":"manual_waiting";snapshot.scene_index=0;
            node.properties||={};node.properties.h3_director_preview=snapshot;
        }
        renderPreview();node.graph?.setDirtyCanvas?.(true,true);
    }
    async function inputManualImage(item){
        const image=normalizeManualImage(item);if(!image)throw Error("图片信息无效。");
        if(image.type==="input")return image;
        const response=await api.fetchApi(`/view?${new URLSearchParams(image)}`,{signal:controller.signal});if(!response.ok)throw Error(`读取候选图片失败（${response.status}）`);
        const blob=await response.blob();if(!blob.type.startsWith("image/"))throw Error("候选结果不是图片。");
        return uploadManualFile(new File([blob],image.filename,{type:blob.type}));
    }
    async function uploadManualFile(file){
        if(file.size>40*1024*1024)throw Error("单张图片不能超过40MB。");
        const form=new FormData();form.append("image",file);form.append("type","input");form.append("overwrite","false");
        const response=await api.fetchApi("/upload/image",{method:"POST",body:form,signal:controller.signal});if(!response.ok)throw Error(`上传图片失败（${response.status}）`);
        const uploaded=await response.json();if(!uploaded.name)throw Error("上传结果缺少文件名。");return {filename:uploaded.name,subfolder:uploaded.subfolder||"",type:"input"};
    }
    async function setManualImages(incoming,startIndex=selected){
        if(!manualCanEdit()){manualNotice("请保留分镜计划，并在任务暂停或结束后放入图片。");return {assigned:0,overflow:incoming?.length||0};}
        const received=Array.from(incoming||[]),items=received.slice(0,MAX_REFERENCES),maximum=sceneCount()+(value("镜头衔接")==="首尾帧约束"?1:0),start=Math.max(0,Math.min(maximum-1,Number(startIndex)||0)),remaining=maximum-start;
        if(!items.length)return {assigned:0,overflow:0};
        const accepted=items.slice(0,remaining),images=isManualMode()?manualImages():Array.from({length:sceneCount()+1},(_,i)=>storyboardImage(i)),changed=[];manualUploading=true;manualChoose.disabled=true;manualClear.disabled=true;
        try{
            for(const item of accepted){manualNotice(`正在放入分镜${start+changed.length+1} · ${changed.length+1}/${accepted.length}`);const image=item instanceof File?await uploadManualFile(item):await inputManualImage(item);if(disposed)break;const index=start+changed.length;images[index]=image;changed.push(index);}
        }catch(error){if(!disposed)manualNotice(`${error.message||"放入图片失败"}${changed.length?`，前${changed.length}张已保留。`:""}`);}
        finally{manualUploading=false;manualFileInput.value="";if(!disposed){if(changed.length)persistManualImages(images,changed);else renderManualControls();}}
        const overflow=received.length-changed.length;
        if(!disposed&&changed.length)manualNotice(`已放入分镜${start+1}${changed.length>1?`～${start+changed.length}`:""}${overflow?`；${overflow}张未放入，请增加镜头或选择更前的槽位。`:"。"}`);
        return {assigned:changed.length,overflow};
    }
    function uploadManualFiles(files,start=selected){const images=Array.from(files||[]).filter(file=>file.type?.startsWith("image/")||/\.(png|jpe?g|webp|bmp|gif|avif)$/i.test(file.name||""));if(!images.length){manualNotice("请选择图片文件。");return Promise.resolve({assigned:0,overflow:0});}return setManualImages(images,start);}
    function clearManualImage(index){if(!manualCanEdit())return;const images=Array.from({length:sceneCount()+1},(_,i)=>storyboardImage(i));if(!images[index])return;if(!globalThis.confirm(`清空分镜${index+1}的图片？剧本、提示词和分镜计划会保留，相关视频需重新生成。`))return;images[index]=null;persistManualImages(images,[index]);manualNotice(`已清空分镜${index+1}，提示词保留。`);}
    function clearStoryboardImages(){if(!manualCanEdit())return;const images=Array.from({length:sceneCount()+1},(_,i)=>storyboardImage(i));const changed=images.map((item,index)=>item?index:-1).filter(index=>index>=0);if(!changed.length)return;if(!globalThis.confirm("清空全部分镜图片？剧本、提示词和分镜计划会保留，已有视频需要重新生成。"))return;persistManualImages(images.map(()=>null),changed);manualNotice("分镜图片已清空，剧本与计划保留。");}
    function chooseManualSlot(index){selected=index;followProgress=false;node.properties||={};node.properties.h3_manual_slot=index;renderPreview();manualNotice(`已选择分镜${index+1}，可选择、拖入或粘贴多张图片。`);}
    function renderManualControls(){
        manualBar.hidden=!imageEditingMode();manualChoose.disabled=!manualCanEdit();manualClear.disabled=!manualCanEdit()||!storyboardImage(selected);manualClearAll.disabled=!manualCanEdit()||!Array.from({length:sceneCount()+1},(_,i)=>storyboardImage(i)).some(Boolean);manualFileInput.disabled=!manualCanEdit();
        manualChoose.textContent=`${isManualMode()?"选择":"替换"}图片 · ${selected===sceneCount()?"结束帧":`分镜${selected+1}`}`;
        importAllImages.hidden=!isManualMode();importAllImages.disabled=!snapshot?.image_jobs?.some(job=>job.prompt);
    }
    function manualTarget(){
        for(const output of node.outputs||[])for(const linkId of output.links||[]){const link=node.graph?.links?.get?.(linkId)??node.graph?.links?.[linkId];const target=node.graph?.getNodeById?.(link?.target_id??link?.[3]);if(target?.type==="H3ManualImageStudio")return target;}
        return node.graph?._nodes?.find(target=>target.type==="H3ManualImageStudio"&&String(target.properties?.h3_manual_source_id)===String(node.id))||null;
    }
    async function importManualPrompts(job=null){
        if(!isManualMode())return;const target=manualTarget();
        if(!target?.h3ManualImageStudio?.importShots){imageSummaryState.textContent="请先将“分镜生图”连接到右侧独立生图节点。";return;}
        const jobs=job?[job]:(snapshot?.image_jobs||[]).filter(item=>/^分镜\d+$/.test(item.id));const shots=jobs.filter(item=>String(effectiveImagePrompt(item)).trim()).map(item=>({id:item.id,index:Number(item.index)||Number(item.id.match(/\d+/)?.[0])||1,prompt:String(effectiveImagePrompt(item))}));
        if(!shots.length){imageSummaryState.textContent="请先运行一次，完成分镜剧本规划。";return;}
        const canvas=snapshot?.canvas||{},payload={sourceNodeId:node.id,shots,references:references.map(item=>({...item})),width:canvas.width??canvas["宽度"],height:canvas.height??canvas["高度"],image_settings:snapshot?.effective_settings?.image};
        try{await target.h3ManualImageStudio.importShots(payload);if(!disposed)imageSummaryState.textContent=`已导入${shots.length}条提示词及参考图。`;}catch(error){if(!disposed)imageSummaryState.textContent=error.message||"导入失败。";}
    }
    function notifyManualMode(){
        const enabled=isManualMode(),signature=`${node.id}:${enabled}`;manualTarget()?.h3ManualImageStudio?.setSourceEnabled?.(node.id,enabled);
        if(signature===manualModeSignature)return;manualModeSignature=signature;document.dispatchEvent(new CustomEvent("h3-manual-mode-changed",{detail:{sourceNodeId:node.id,enabled}}));
    }
    function clearManualDrag(){manualDrag=null;delete allScenes.dataset.fileDrag;for(const view of allSceneRows.values()){delete view.row.dataset.dragSource;delete view.row.dataset.dropSide;delete view.row.dataset.shift;}}
    function previewManualInsertion(index,after){
        for(const view of allSceneRows.values()){delete view.row.dataset.dropSide;delete view.row.dataset.shift;}
        const target=allSceneRows.get(index);if(!target)return;target.row.dataset.dropSide=after?"after":"before";
        if(!manualDrag)return;const insertion=index+(after?1:0);manualDrag.insertion=insertion;
        for(const[position,view]of allSceneRows)if(position!==manualDrag.from){if(insertion>position&&position>manualDrag.from)view.row.dataset.shift="left";else if(insertion<=position&&position<manualDrag.from)view.row.dataset.shift="right";}
    }
    function moveManualImage(from,insertion){
        if(!manualCanEdit())return;const images=manualImages().slice(0,sceneCount());if(!images[from])return;const destination=insertion>from?insertion-1:insertion;if(destination===from)return;
        const previous=images.map(item=>JSON.stringify(item));const [image]=images.splice(from,1);images.splice(destination,0,image);
        const changed=images.map((item,index)=>JSON.stringify(item)!==previous[index]?index:-1).filter(index=>index>=0);selected=destination;persistManualImages(images,changed);manualNotice(`图片已移至分镜${destination+1}，提示词顺序保持不变。`);
    }
    function consumeManualDrop(event,index){
        const files=Array.from(event.dataTransfer?.files||[]);if(files.length){uploadManualFiles(files,index);return;}
        for(const type of["application/x-h3-images","application/x-h3-image"]){try{const raw=event.dataTransfer?.getData(type);if(!raw)continue;const parsed=JSON.parse(raw),images=Array.isArray(parsed)?parsed:parsed.images||[parsed];setManualImages(images,index);return;}catch{}}
        manualNotice("请拖入图片文件，或使用右侧的导回按钮。");
    }
    allScenes.addEventListener("dragover",event=>{
        if(!manualCanEdit())return;const types=Array.from(event.dataTransfer?.types||[]);if(!manualDrag&&!types.some(type=>["Files","application/x-h3-images","application/x-h3-image"].includes(type)))return;
        event.preventDefault();event.stopPropagation();allScenes.dataset.fileDrag="true";event.dataTransfer.dropEffect=manualDrag?"move":"copy";
        if(manualDrag)previewManualInsertion(sceneCount()-1,true);
    },{signal:controller.signal});
    allScenes.addEventListener("dragleave",event=>{if(!allScenes.contains(event.relatedTarget)){delete allScenes.dataset.fileDrag;for(const view of allSceneRows.values()){delete view.row.dataset.dropSide;delete view.row.dataset.shift;}}},{signal:controller.signal});
    allScenes.addEventListener("drop",event=>{
        if(!manualCanEdit())return;event.preventDefault();event.stopPropagation();if(manualDrag){const {from,insertion}=manualDrag;clearManualDrag();moveManualImage(from,insertion);return;}clearManualDrag();consumeManualDrop(event,selected);
    },{signal:controller.signal});
    function displayedDuration(index){if(value("时长模式","手动设置")!=="AI自动分配")return sceneAt(index)?.seconds??durations[index];if(snapshot?.duration_mode!=="AI自动分配"||!Array.isArray(snapshot.scene_seconds))return null;return snapshot.scene_seconds[index]??null;}
    function planEditData(){try{const data=JSON.parse(String(value("计划编辑","{}")));return data&&typeof data==="object"&&!Array.isArray(data)?data:{};}catch{return{};}}
    function selectedImageIds(){try{const ids=JSON.parse(String(value("重生图片","[]")));return new Set(Array.isArray(ids)?ids.map(String):[]);}catch{return new Set();}}
    function effectiveImagePrompt(job){const data=planEditData();return job.id==="结束帧"?data.ending?.image_prompt??job.prompt??"":data.scenes?.[String(job.index)]?.image_prompt??job.prompt??"";}
    function importedPlanData(){
        try{const data=JSON.parse(String(value("导入优化计划","{}")));return data?.schema==="h3-plan/1"&&data.plan&&Array.isArray(data.durations)?data:null;}catch{return null;}
    }
    function exportedPlanData(){
        const data=typeof node.properties?.h3_optimized_import_text==="string"?importedPlanData():snapshot?.plan_export||importedPlanData();if(data?.schema!=="h3-plan/1"||!data.plan||!Array.isArray(data.durations))return null;
        return {...data,...(isTextVideo()?{input_mode:'T2VA',mode:'文生视频'}:{}),source_script:data.plan.raw_script??textarea.value,reference_files:isTextVideo()?[]:data.reference_files||references.map(item=>({...item,type:'input'})),canvas_settings:Object.fromEntries(['视频输入','画幅','画幅参考图','生成档位','视频短边上限','适配方式','镜头衔接','分镜图长边','参考读取长边','生图后生成视频'].map(name=>[name,name==='视频输入'?(isTextVideo()?'文生视频':'图生视频'):value(name)])),prompt_format:data.prompt_format??data.plan.prompt_format??PROMPT_FORMATS[0],optimized_script:optimizedScript.value};
    }
    async function localPlanRequest(path,body){
        const response=await api.fetchApi(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:controller.signal});const data=await response.json();if(!response.ok)throw Error(data.error||'分镜文件处理失败。');return data;
    }
    async function exportPlanFile(){
        if(draftChanged){actionState.textContent='原始剧本或规划条件已改变，请先完成对应规划，再导出一致的原稿与分镜。';return;}
        const payload=exportedPlanData();if(!payload){actionState.textContent='请先完成或导入分镜计划。';return;}
        exportJSON.disabled=true;
        try{const result=await localPlanRequest('/h3-director/plan/export',{payload,edits:planEditData(),overrides:readOverrides()});downloadPlan(result);actionState.textContent='已导出原始剧本、完整分镜与生图提示词。';}
        catch(error){actionState.textContent=error.message;}
        finally{exportJSON.disabled=false;}
    }
    async function readWorkspaceFile(file){
        if(!file)return;delete root.dataset.fileDrop;
        try{
            const kind=fileKind(file);if(!kind)throw Error('支持TXT/MD剧本或本工作台导出的JSON分镜。');
            if(file.size>(kind==='plan'?8*1024*1024:256*1024))throw Error('文件过大：剧本最多12000字，分镜JSON最多8MB。');
            const buffer=await file.arrayBuffer();
            if(kind==='script'){textarea.value=decodeScript(buffer);scriptChanged();actionState.textContent=`已读取剧本：${file.name}`;return;}
            if(queueing||['running','generating'].includes(snapshot?.status))throw Error('请先停止当前任务，再导入另一套分镜。');
            const payload=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(buffer).replace(/^\uFEFF/,''));
            const inputError=planInputError(payload);if(inputError)throw Error(inputError);
            const result=await localPlanRequest('/h3-director/plan/import',{payload,references:isTextVideo()?[]:references,input_mode:isTextVideo()?'T2VA':'I2V'});const imported=result.payload;
            if(!isTextVideo()&&!MODES.includes(imported.mode||'AI生成分镜'))throw Error('文件中的生成模式不受支持。');
            for(const[name,next]of Object.entries(imported.canvas_settings||{})){if(!['画幅','画幅参考图','生成档位','视频短边上限','适配方式','镜头衔接','分镜图长边','参考读取长边','生图后生成视频'].includes(name)||next===undefined)continue;const upstream=upstreamWidget(name);if(upstream){upstream.value=next;upstream.callback?.(next);}write(name,next);}
            if(!isTextVideo()){references=result.references;write('参考图集',JSON.stringify(references));write('生成模式',imported.mode||'AI生成分镜');}write('剧本',imported.source_script??imported.plan.raw_script??'');write('声音模式',imported.plan.speech_mode);write('提示词格式',imported.prompt_format??imported.plan.prompt_format??PROMPT_FORMATS[0]);write('分镜数量',imported.durations.length);write('时长模式','手动设置');write('每镜秒数',imported.durations.join(','));write('目标总秒数',imported.durations.reduce((a,b)=>a+b,0));write('执行范围','完整生成');
            syncFromWidgets();acceptOptimizedPaste(imported.optimized_script,optimizedClipboardHTML(imported.optimized_script,imported));
            if(preflightFailure)throw Error(preflightFailure.message);
            actionState.textContent=isTextVideo()?'完整计划已读取，点击运行直接生成视频。':'完整计划已读取，原始剧本已恢复；点击运行直接生图，不再调用AI优化。';
        }catch(error){actionState.textContent=error.message||'文件无法读取。';}
    }
    root.addEventListener('dragover',event=>{if(!Array.from(event.dataTransfer?.types||[]).includes('Files'))return;event.preventDefault();root.dataset.fileDrop='true';},{capture:true,signal:controller.signal});
    root.addEventListener('dragleave',event=>{if(!root.contains(event.relatedTarget))delete root.dataset.fileDrop;},{signal:controller.signal});
    root.addEventListener('drop',event=>{delete root.dataset.fileDrop;const files=Array.from(event.dataTransfer?.files||[]),supported=files.filter(file=>fileKind(file));if(!supported.length)return;event.preventDefault();event.stopImmediatePropagation();if(files.length!==1){actionState.textContent='每次拖入一份剧本或分镜JSON。';return;}readWorkspaceFile(files[0]);},{capture:true,signal:controller.signal});
    function optimizedClipboardHTML(text,metadata,partial=false){
        const container=document.createElement("div");if(metadata)container.setAttribute("data-h3-plan",JSON.stringify(metadata));else if(partial)container.setAttribute("data-h3-plan-partial","true");container.textContent=text;return container.outerHTML;
    }
    async function copyOptimizedPlan(){
        const text=optimizedScript.value,metadata=exportedPlanData();if(!text)return;
        try{
            const Item=window.ClipboardItem;if(metadata&&Item&&navigator.clipboard?.write){await navigator.clipboard.write([new Item({"text/plain":new Blob([text],{type:"text/plain"}),"text/html":new Blob([optimizedClipboardHTML(text,metadata)],{type:"text/html"})})]);copyOptimized.textContent="已复制";return;}
            optimizedScript.focus();const range=document.createRange();range.selectNodeContents(optimizedScript);const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
            if(document.execCommand?.("copy")){copyOptimized.textContent="已复制";return;}
            await navigator.clipboard.writeText(text);copyOptimized.textContent="已复制文字";actionState.textContent="当前浏览器仅复制了文字；跨任务复用请完整复制总览。";
        }catch(error){copyOptimized.textContent="复制失败";actionState.textContent=error.message||"请选中完整总览后按Ctrl+C。";}
    }
    async function pasteOptimizedPlan(){
        optimizedScript.disabled=false;optimizedScript.focus();
        try{
            if(navigator.clipboard?.read){const items=await navigator.clipboard.read();let text="",html="";for(const item of items){if(item.types.includes("text/plain"))text=await(await item.getType("text/plain")).text();if(item.types.includes("text/html"))html=await(await item.getType("text/html")).text();}acceptOptimizedPaste(text,html);return;}
            if(navigator.clipboard?.readText){acceptOptimizedPaste(await navigator.clipboard.readText(),"");return;}
        }catch{}
        actionState.textContent="请在总览中按Ctrl+V粘贴已优化的完整分镜稿。";
    }
    function planInputError(data){const mode=data?.plan?.input_mode??data?.input_mode??'I2V';if(data?.input_mode&&data?.plan?.input_mode&&data.input_mode!==data.plan.input_mode)return '优化稿的视频输入格式不一致。';const text=mode==='T2VA'||mode==='T2V';return text!==isTextVideo()?`优化稿为${text?'文生视频':'图生视频'}，请先切换到相同视频输入。`:'';}
    function importedPlanError(data){
        if(!data)return "请使用总览的复制按钮保留完整分镜数据，再粘贴优化稿。";
        if(data.schema!=="h3-plan/1"||!data.plan||!Array.isArray(data.plan.scenes)||!Array.isArray(data.durations)||data.durations.length<1||data.durations.length>MAX_SCENES)return "优化稿分镜数据不完整，请重新复制完整总览。";
        const inputError=planInputError(data);if(inputError)return inputError;
        const format=data.prompt_format??data.plan.prompt_format??PROMPT_FORMATS[0];if(format!==value("提示词格式",PROMPT_FORMATS[0]))return `优化稿使用“${format}”，请先切换到相同提示词格式。`;
        if(!isTextVideo()&&data.mode&&data.mode!==modeSelect.value)return `优化稿使用“${data.mode}”，请先切换到相同模式。`;
        if(data.plan.speech_mode&&data.plan.speech_mode!==value("声音模式","自动识别原稿"))return `优化稿声音模式为“${data.plan.speech_mode}”，请先使用相同声音模式。`;
        if(data.durations.length!==durations.length)return `优化稿包含${data.durations.length}镜，当前为${durations.length}镜，请先调整镜头数量。`;
        if(value("时长模式","手动设置")==="AI自动分配"){
            const total=data.durations.reduce((sum,seconds)=>sum+Number(seconds),0);if(!Number.isFinite(total)||Math.abs(total-Number(value("目标总秒数",30)))>0.01)return "优化稿总时长与当前设置不一致，请调整总时长。";
        }else if(data.durations.some((seconds,index)=>!Number.isFinite(Number(seconds))||Math.abs(Number(seconds)-durations[index])>0.01))return "优化稿每镜时长与当前设置不一致，请调整每镜时长。";
        if(isTextVideo())return (data.references?.length||data.reference_files?.length)?'文生视频优化稿不应包含图片参考。':'';
        const exported=(data.references||[]).map(item=>typeof item==="string"?item:item?.id).filter(Boolean),current=references.map(item=>item.id);
        if(JSON.stringify(exported)!==JSON.stringify(current))return `优化稿需要${exported.join("、")||"相应参考图"}，请先添加并对应参考图片。`;
        return "";
    }
    function acceptOptimizedPaste(text,html){
        const body=String(text||"");if(!body.trim()){actionState.textContent="剪贴板中没有优化稿。";return;}
        let metadata=null,error="";
        try{const parsed=new window.DOMParser().parseFromString(String(html||""),"text/html"),holder=parsed.querySelector("[data-h3-plan]");if(holder){const raw=holder.getAttribute("data-h3-plan");if(raw.length>2*1024*1024)throw Error("分镜数据过大。");metadata=JSON.parse(raw);}else if(parsed.querySelector("[data-h3-plan-partial]"))error="只复制了部分总览，请完整复制优化稿后再粘贴。";}catch(failure){error=failure.message||"分镜数据无法读取。";}
        node.properties||={};node.properties.h3_optimized_import_text=body;write("导入优化计划",metadata?JSON.stringify({...metadata,optimized_script:body}):"{}");write("保留分镜计划",true);
        const busy=["running","generating"].includes(snapshot?.status);
        if(busy){draftChanged=true;node.properties.h3_draft_changed=true;optimizedScript.value=body;lastPlanRender=body;actionState.textContent="优化稿已保留为下一次草稿，当前任务继续。";renderPlanReuse();return;}
        if(currentRun){oldRuns.add(currentRun);node.properties.h3_invalidated_runs=Array.from(oldRuns).slice(-16);}currentRun=null;snapshot=null;write("继续任务","");write("重做镜头",0);write("分镜覆盖","{}");write("计划编辑","{}");write("重生图片","[]");write("生图重做范围","保留现有图片");draftChanged=false;delete node.properties.h3_draft_changed;delete node.properties.h3_director_preview;
        optimizedScript.value=body;lastPlanRender=body;error=error||importedPlanError(metadata);
        preflightFailure=error?{stage:"settings",field:"导入优化计划",message:error,suggestion:"保留完整优化稿与分镜数据后可直接运行，无需再次AI优化。"}:null;
        actionState.textContent=error||"已导入完整优化稿，下次运行直接使用现有分镜计划。";renderPreview();optimizedScript.focus();
    }
    function planHasEdits(){return Object.keys(planEditData()).length>0||Object.keys(readOverrides()).length>0||selectedImageIds().size>0;}
    function planCanResume(){const available=snapshot?.plan_available??!!(snapshot?.optimized_script&&snapshot?.scenes?.some(scene=>scene.prompt||scene.video_prompt)),text=snapshot?.mode==='文生视频'||snapshot?.input_mode==='T2VA'||snapshot?.video_input==='文生视频';return text===isTextVideo()&&!!value("保留分镜计划",false)&&!!snapshot?.run_id&&available&&!draftChanged;}
    function imagePromptCanEdit(){return !!snapshot?.run_id&&!!value('保留分镜计划',false)&&(snapshot.plan_available??!!snapshot.scenes?.some(scene=>scene.prompt||scene.video_prompt))&&!draftChanged;}
    function toggleImageChoice(identity){
        if(modeSelect.value!=="AI生成分镜"||value("生图重做范围")!=="选择重生"||!planHasEdits()||["running","generating"].includes(snapshot?.status))return;
        const ids=selectedImageIds();if(ids.has(identity))ids.delete(identity);else ids.add(identity);write("重生图片",JSON.stringify(Array.from(ids)));renderPlanReuse();
    }
    function sceneRegenerationEligibility(index){
        const scene=sceneAt(index),available=snapshot?.plan_available??!!snapshot?.scenes?.some(item=>item.prompt||item.video_prompt);
        if(modeSelect.value!=="AI生成分镜")return {allowed:false,reason:"当前模式不自动重生分镜图。"};
        if(!snapshot?.run_id||!available)return {allowed:false,reason:"当前任务没有可复用的分镜计划。"};
        if(draftChanged)return {allowed:false,reason:"原稿或参数已修改，请先重新规划。"};
        if(queueing||["running","generating"].includes(snapshot.status))return {allowed:false,reason:"任务正在运行，请停止或等待结束后再标记。"};
        if(!scene?.image)return {allowed:false,reason:"此图尚未生成，继续运行时会补齐。"};
        return {allowed:true,reason:"仅标记此图，点击继续生成后重生；其他未选图片保留。"};
    }
    function toggleSceneRegeneration(index){
        if(!sceneRegenerationEligibility(index).allowed)return;
        const id=`分镜${index+1}`,ids=selectedImageIds();if(ids.has(id))ids.delete(id);else ids.add(id);
        write("保留分镜计划",true);write("继续任务",snapshot.run_id);write("生图重做范围",ids.size?"选择重生":"保留现有图片");write("重生图片",JSON.stringify(Array.from(ids)));
        renderPlanReuse();
    }
    function renderPlanReuse(){
        const keep=!!value("保留分镜计划",false),busy=["running","generating"].includes(snapshot?.status),ready=planCanResume();
        keepPlanToggle.checked=keep;keepPlanToggle.setAttribute("aria-checked",String(keep));keepPlanToggle.disabled=busy||isManualMode();
        autoVideoToggle.checked=!!value('生图后生成视频',false);autoVideoToggle.disabled=busy||modeSelect.value!=='AI生成分镜';exportJSON.disabled=!exportedPlanData();
        const editable=!!(keep&&ready||!snapshot?.run_id||typeof node.properties?.h3_optimized_import_text==="string");optimizedScript.disabled=!editable;optimizedScript.setAttribute("aria-readonly",String(!editable));
        if(ready)write("继续任务",snapshot.run_id);else if(!keep)write("继续任务","");
        const edited=modeSelect.value==="AI生成分镜"&&keep&&ready&&planHasEdits();regenerationBar.hidden=!edited;
        const range=value("生图重做范围","保留现有图片"),selection=edited&&range==="选择重生",ids=selectedImageIds();
        for(const[name,button]of regenerationButtons){button.setAttribute("aria-pressed",String(name===range));button.disabled=busy;}
        regenerationStatus.textContent=selection?`已选 ${ids.size} 张`:range==="全部重生"?"重新生成全部分镜图":"保留已生成图片";
        for(const[id,view]of imageJobViews){view.entry.dataset.imageSelectable=String(selection&&!busy);view.entry.dataset.imageChoice=String(selection&&ids.has(id));view.prompt.readOnly=!view.editing||!imagePromptCanEdit();if(!view.editing&&document.activeElement!==view.prompt)view.prompt.value=effectiveImagePrompt(view.job);sizeImagePrompt(view);view.editPrompt.disabled=!imagePromptCanEdit();view.editPrompt.textContent=view.editing?"保存修改":"编辑提示词";view.saveState.textContent=view.savedChange?(isManualMode()?"已保存":ids.has(id)&&range==="选择重生"?"已保存 · 待重生":""):"";}
        for(const[index,view]of allSceneRows){const id=`分镜${index+1}`,marked=selection&&ids.has(id),eligible=sceneRegenerationEligibility(index);view.row.dataset.imageSelectable=String(selection&&!busy);view.row.dataset.imageChoice=String(marked);
            if(view.regenerate){const playing=activeAllScene===index&&!allSceneVideo.paused&&!allSceneVideo.ended;view.regenerate.hidden=playing||modeSelect.value!=="AI生成分镜"||!sceneAt(index)?.image;view.regenerate.disabled=!eligible.allowed;view.regenerate.textContent=marked?"↻ 已标记重生":"↻ 标记此图重生";view.regenerate.setAttribute("aria-pressed",String(marked));view.regenerate.title=marked?"已标记此图重生，再次点击取消；不会立即提交任务。":eligible.reason;}
        }
    }
    function renderMode() {
        renderVideoInput();
        root.dataset.manualMode=String(isManualMode());
        if(isManualMode()){write("保留分镜计划",true);if(manualModeSignature!==`${node.id}:true`){previewMode="all";node.properties||={};node.properties.h3_director_preview_mode="all";}}
        renderManualControls();notifyManualMode();
        modeHint.textContent = ({"逐镜参考图":"按顺序作为镜头边界图，最后一张可作结束帧。", "上一镜末帧":"第一镜使用参考图，后续接上一镜末帧。", "AI生成分镜":"自动生成边界图并引用 · 每张最多16张参考"})[modeSelect.value] || "";
        if(isManualMode())modeHint.textContent="先规划剧本与提示词，放入分镜图片后继续视频。";
        if(modeSelect.value==="末帧高清续接")modeHint.textContent="每镜末帧高清修复后续接下一镜。";
        applyLayout(activeLayout);
    }
    function renderAspectSettings(){
        const aspect=value("画幅","跟随图片"),busy=["running","generating"].includes(snapshot?.status);
        const reference=isTextVideo()?null:aspectReference();
        for(const[name,button]of aspectButtons){button.setAttribute("aria-pressed",String(name===aspect));button.disabled=busy&&name!=="跟随图片";if(name==="跟随图片"){button.textContent=`跟随${reference?.id||"图片"}${references.length>1?" ▾":""}`;button.title=references.length>1?"选择用于确定输出尺寸的参考图；不改变人物、环境与主参考图。":"按唯一参考图确定输出尺寸。";}}
        for(const name of["自定义宽度","自定义高度"]){const field=configFields.get(name);if(field)field.hidden=aspect!=="自定义";}
        const canvas=snapshot?.canvas;const width=canvas?.width??canvas?.["宽度"],height=canvas?.height??canvas?.["高度"];
        const sourceImage=Array.from(gallery.querySelectorAll('img')).find(img=>img.alt===reference?.id)||gallery.querySelector('img');const presets={'16:9 横屏':16/9,'9:16 竖屏':9/16,'1:1 方形':1,'4:3 横屏':4/3,'3:4 竖屏':3/4};const ratio=presets[aspect]||(sourceImage?.naturalWidth&&sourceImage?.naturalHeight?sourceImage.naturalWidth/sourceImage.naturalHeight:width/height||9/16);
        const short={'384P 快速':384,'480P':480,'720P':736,'1080P':1088}[value('生成档位','480P')]||480;
        const maxShort=Math.max(64,Math.floor(Math.min(short,2048/Math.max(ratio,1/ratio))/32)*32);videoScale.min=String(Math.min(256,maxShort));videoScale.max=String(maxShort);videoScale.dataset.ratio=String(ratio);videoScale.value=String(Math.min(maxShort,Number(value('视频短边上限',0))||maxShort));videoScale.disabled=busy;
        const target=Number(videoScale.value),estimated=ratio>=1?[Math.round(target*ratio/32)*32,target]:[target,Math.round(target/ratio/32)*32];videoScaleValue.textContent=`${estimated[0]} × ${estimated[1]}`;
        aspectNote.textContent=Number.isFinite(width)&&Number.isFinite(height)?`${width} × ${height} · 视频尺寸 · 已按32倍数对齐`:`预计 ${estimated[0]} × ${estimated[1]} · 视频尺寸`;
    }
    function refreshModelConnections(invalidate=true){
        if(disposed)return;
        textVideoWeights();
        notifyManualMode();
        colorH3Connections(app,node);
        const sourceData={};
        for(const kind of Object.keys(configPorts)){
            const source=configSource(kind);const settings=Object.fromEntries((source?.widgets||[]).filter(widget=>["string","boolean","number"].includes(typeof widget.value)).map(widget=>[widget.name,widget.value]));
            sourceData[kind]={source,settings};
        }
        const signature=JSON.stringify(Object.fromEntries(Object.entries(sourceData).map(([kind,{source,settings}])=>[kind,{id:source?.id??null,settings}])));
        const busy=["running","generating"].includes(snapshot?.status);
        const configuring=!!app.configuringGraph||!!node.graph?._is_configuring;
        if(connectionSignature&&signature!==connectionSignature&&invalidate&&!configuring&&busy)return;
        if(connectionSignature&&signature!==connectionSignature&&invalidate&&!configuring){
            const changed=[];for(const[kind,entry]of Object.entries(sourceData)){const prior=connectionData?.[kind];if(prior?.id!==(entry.source?.id??null))changed.push({kind,name:"__connection"});for(const name of new Set([...Object.keys(prior?.settings||{}),...Object.keys(entry.settings)]))if(JSON.stringify(prior?.settings?.[name])!==JSON.stringify(entry.settings[name]))changed.push({kind,name});}
            if(changed.length&&changed.every(change=>change.kind==="video"&&VIDEO_ONLY_FIELDS.has(change.name))&&canKeepVideoPlan())markVideoSettingsChanged(changed.some(change=>change.name!=="完成后"));else invalidateSource();
        }
        connectionSignature=signature;
        connectionData=Object.fromEntries(Object.entries(sourceData).map(([kind,{source,settings}])=>[kind,{id:source?.id??null,settings:{...settings}}]));
        for(const[name,widget]of widgets){const upstream=upstreamWidget(name);if(upstream&&widget.value!==upstream.value)write(name,upstream.value);}
        for(const[name,control]of configControls){if(document.activeElement===control&&control.type!=="checkbox")continue;if(control.type==="checkbox"){control.checked=!!value(name);control.setAttribute("aria-checked",String(control.checked));}else control.value=String(value(name,""));updateControlTitle(control);}
        for(const[kind,{source,settings}]of Object.entries(sourceData)){
            const effective=snapshot?.effective_settings?.[kind]||{};
            let model=kind==="text"?(settings["模型"]??settings.model??effective.model??value("语言模型","")):kind==="image"?(settings["图像模型"]??settings.model??effective.model):((settings["启用加速LoRA"]&&settings["加速权重"]==="原作者融合模型")?settings["原作者模型"]:(settings["基础模型"]??effective["基础模型"]));
            if(kind==="video"&&!source)model=value("启用加速LoRA")&&value("加速权重")==="原作者融合模型"?value("原作者模型"):value("基础模型");
            if(kind==="rules")model=source?"六类提示词规则":"默认提示词规则";
            connectionLines.get(kind).textContent=`${source?"已连接":"工作台"} · ${model||"默认模型"}${kind==='video'&&isTextVideo()?` · 文生 · ${value('启用加速LoRA')?'独立LoRA':'基础模型'}`:''}`;
            connectionLines.get(kind).title=String(model||"");
        }
        if(!isTextVideo()){refreshImageSampling(sourceData.image?.source);renderImageSizing();referenceUpscale.render();}renderImageSeed();renderSeed();renderVideoInput();
    }
    function insertReference(reference) {
        const from = atRange?.[0] ?? textarea.selectionStart ?? textarea.value.length;
        const to = atRange?.[1] ?? textarea.selectionEnd ?? from;
        const token = `@${reference.id} `;
        textarea.setRangeText(token, from, to, "end");
        scriptChanged(); picker.hidden = true; atRange = null;
        textarea.focus(); renderMentions();
    }
    function renderReferences() {
        gallery.replaceChildren();
        referenceCount.textContent = `${references.length} / ${MAX_REFERENCES}`;
        uploadButton.disabled = uploading || references.length >= MAX_REFERENCES;
        gallery.dataset.empty=String(!references.length);
        if (!references.length) {const empty=el("div","h3-ref-empty");const add=el("button","","点击添加图片");add.type="button";add.disabled=uploadButton.disabled;add.onclick=()=>fileInput.click();empty.append(el("span","","拖放图片 / Ctrl+V 粘贴"),add);gallery.append(empty);}
        const drop=el("div","h3-ref-drop","释放鼠标，添加参考图");gallery.append(drop);
        references.forEach(reference => {
            const card = el("div", "h3-ref");
            const pick = el("button", "h3-ref-pick"); pick.title = `单击插入 @${reference.id}；右键查看大图，可左右切换。`;
            const thumbnail = el("img"); thumbnail.src = referenceURL(reference); thumbnail.alt = reference.id; thumbnail.loading = "lazy";
            pick.append(thumbnail, el("span", "", reference.id));
            pick.onclick = () => {atRange = null; insertReference(reference);};
            pick.oncontextmenu=event=>{event.preventDefault();event.stopPropagation();openImageViewer(()=>references.map(item=>({id:item.id,url:referenceURL(item)})),reference.id);};
            const remove = el("button", "h3-remove", "×"); remove.setAttribute("aria-label", `移除${reference.id}`);
            remove.onclick = () => {
                bindDraftReferences();references = references.filter(item => item.uid !== reference.uid);saveReferences([reference.id]);renderReferences();renderMentions();
                uploadState.textContent=references.length?"图片编号与剧本引用已更新。":"参考图已清空。";
            };
            card.append(pick, remove); gallery.append(card);
        });
    }
    function renderMentions() {
        textarea.refreshReferences();
    }
    function updatePicker() {
        if(isTextVideo()){picker.hidden=true;atRange=null;return;}
        const position = textarea.selectionStart;
        const prefix = textarea.value.slice(0, position);
        const found = prefix.match(/@([^\s@，。；！？、]*)$/);
        if (!found) {picker.hidden = true; atRange = null; return;}
        atRange = [position - found[0].length, position];
        const needle = found[1].toLowerCase();
        pickerMatches = references.filter(reference => !needle || reference.id.toLowerCase().includes(needle) || reference.filename.toLowerCase().includes(needle));
        pickerIndex = Math.min(pickerIndex, Math.max(0, pickerMatches.length - 1));
        picker.replaceChildren(); picker.hidden = false;
        if (!pickerMatches.length) picker.append(el("span", "h3-muted", references.length ? "没有匹配的图片" : "请先添加参考图"));
        pickerMatches.forEach((reference, index) => {
            const button = el("button", "h3-choice"); button.setAttribute("role", "option"); button.setAttribute("aria-selected", String(index === pickerIndex));
            const thumbnail = el("img"); thumbnail.src = referenceURL(reference); thumbnail.alt = ""; thumbnail.loading = "lazy";
            button.append(thumbnail, el("span", "", `@${reference.id} · ${reference.filename.split(/[\\/]/).at(-1)}`));
            button.onmousedown = event => event.preventDefault();
            button.onclick = () => insertReference(reference);
            picker.append(button);
        });
    }
    let composingScript=false;
    textarea.addEventListener("compositionstart",()=>{composingScript=true;});
    textarea.addEventListener("compositionend",()=>{composingScript=false;scriptChanged();pickerIndex=0;updatePicker();});
    textarea.addEventListener("input",event=>{if(composingScript||event.isComposing){write("剧本",textarea.value);return;}scriptChanged();pickerIndex=0;updatePicker();});
    function repairReferenceChip(chip){
        const start=Number(chip.dataset.h3Start),end=Number(chip.dataset.h3End);textarea.focus();textarea.setSelectionRange(start,end);
        atRange=[start,end];pickerMatches=[...references];pickerIndex=0;picker.replaceChildren();picker.hidden=false;
        if(!references.length)picker.append(el("span","h3-muted","请先添加参考图"));
        for(const reference of pickerMatches){const button=el("button","h3-choice"),image=el("img");image.src=referenceURL(reference);image.alt="";button.setAttribute("role","option");button.append(image,el("span","",reference.id));button.onmousedown=event=>event.preventDefault();button.onclick=()=>insertReference(reference);picker.append(button);}
    }
    textarea.addEventListener("click",event=>{const chip=event.target.closest?.('[data-h3-reference][data-missing=true]');if(chip){event.preventDefault();repairReferenceChip(chip);}else updatePicker();});
    textarea.addEventListener("keydown",event=>{if((event.key==='Enter'||event.key===' ')&&event.target.matches?.('[data-h3-reference][data-missing=true]')){event.preventDefault();repairReferenceChip(event.target);}});
    textarea.addEventListener("keydown", event => {
        if (picker.hidden) return;
        if (event.key === "Escape") {event.preventDefault(); picker.hidden = true; atRange = null;}
        else if ((event.key === "ArrowDown" || event.key === "ArrowUp") && pickerMatches.length) {
            event.preventDefault(); pickerIndex = (pickerIndex + (event.key === "ArrowDown" ? 1 : -1) + pickerMatches.length) % pickerMatches.length; updatePicker();
        } else if ((event.key === "Enter" || event.key === "Tab") && pickerMatches.length) {
            event.preventDefault(); insertReference(pickerMatches[pickerIndex]);
        }
    });
    textarea.addEventListener("blur", () => {setTimeout(() => {if (!disposed&&!picker.contains(document.activeElement)) picker.hidden = true;}, 120);});

    async function uploadFiles(files) {
        if(isTextVideo())return [];
        if (uploading || disposed || ["running","generating"].includes(snapshot?.status)) return [];
        const images = Array.from(files).filter(file => file.type.startsWith("image/") || /\.(png|jpe?g|webp|bmp|gif|avif)$/i.test(file.name));
        const remaining = MAX_REFERENCES - references.length;
        const accepted = images.slice(0, remaining);
        if (!accepted.length) {uploadState.textContent = remaining ? "请选择图片文件。" : "参考图已达到101张上限。"; return [];}
        uploadButton.disabled = true; uploading = true;
        const added=[];
        let number = references.length;
        try {
            for (let index = 0; index < accepted.length; index++) {
                if (disposed) return;
                uploadState.textContent = `正在添加图片 ${index + 1} / ${accepted.length}`;
                const data = new FormData(); data.append("image", accepted[index]); data.append("type", "input"); data.append("overwrite", "false");
                const response = await api.fetchApi("/upload/image", {method:"POST", body:data, signal:controller.signal});
                if (!response.ok) throw new Error(`上传失败（${response.status}）`);
                const uploaded = await response.json();
                if (disposed) return;
                if (!uploaded.name) throw new Error("上传结果缺少文件名");
                const reference={id:`图${++number}`, filename:uploaded.subfolder ? `${uploaded.subfolder}/${uploaded.name}` : uploaded.name};references.push(reference);added.push(reference);
                node.properties ||= {}; node.properties.h3_ref_serial = number;
                saveReferences(); renderReferences();
            }
            uploadState.textContent = images.length > remaining ? `已添加${accepted.length}张，达到101张上限。` : "";
            renderMentions(); renderPreview();
        } catch (error) {if (!disposed) uploadState.textContent = error.message || "图片上传失败。";}
        finally {uploading = false; if (!disposed) {uploadButton.disabled = references.length >= MAX_REFERENCES; fileInput.value = "";}}
        return added;
    }
    fileInput.onchange = () => uploadFiles(fileInput.files);
    gallery.addEventListener("dragenter",event=>{if(!Array.from(event.dataTransfer?.types||[]).includes("Files"))return;event.preventDefault();gallery.dataset.dragging="true";});
    gallery.addEventListener("dragover",event=>{if(!Array.from(event.dataTransfer?.types||[]).includes("Files"))return;event.preventDefault();event.stopPropagation();event.dataTransfer.dropEffect="copy";gallery.dataset.dragging="true";});
    gallery.addEventListener("dragleave",event=>{if(!gallery.contains(event.relatedTarget))delete gallery.dataset.dragging;});
    gallery.addEventListener("drop",event=>{event.preventDefault();event.stopPropagation();delete gallery.dataset.dragging;uploadFiles(event.dataTransfer.files);});
    root.addEventListener("paste",event=>{
        if(imageEditingMode()&&!referenceSection.contains(event.target)&&!scriptSection.contains(event.target)&&!optimizedSection.contains(event.target)&&!event.target.closest("textarea,input,[contenteditable=true]")){
            const imageItems=Array.from(event.clipboardData?.items||[]).filter(item=>item.kind==="file"&&item.type.startsWith("image/")).map(item=>item.getAsFile()).filter(Boolean);
            const images=imageItems.length?imageItems:Array.from(event.clipboardData?.files||[]).filter(file=>file.type?.startsWith("image/")||/\.(png|jpe?g|webp|bmp|gif|avif)$/i.test(file.name||""));
            let transferred=[];try{const raw=event.clipboardData?.getData("application/x-h3-images");if(raw){const parsed=JSON.parse(raw);transferred=Array.isArray(parsed)?parsed:parsed.images||[];}}catch{}
            if(images.length||transferred.length){event.preventDefault();event.stopPropagation();if(images.length)uploadManualFiles(images,selected);else setManualImages(transferred,selected);return;}
        }
        if(optimizedSection.contains(event.target)||event.target.closest("textarea,input")&&!scriptSection.contains(event.target))return;
        const images=Array.from(event.clipboardData?.items||[]).filter(item=>item.kind==="file"&&item.type.startsWith("image/")).map(item=>item.getAsFile()).filter(Boolean);
        if(images.length){
            event.preventDefault();event.stopPropagation();
            const inScript=textarea.contains(event.target);const range=inScript?[textarea.selectionStart,textarea.selectionEnd]:null;
            uploadFiles(images).then(added=>{if(disposed||!inScript||!added?.length)return;atRange=range;added.forEach(reference=>insertReference(reference));});
        }else if(textarea.contains(event.target)){
            if(textarea.disabled)return;event.preventDefault();event.stopPropagation();const text=event.clipboardData?.getData("text/plain")||"";
            textarea.setRangeText(text,textarea.selectionStart,textarea.selectionEnd);scriptChanged();picker.hidden=true;atRange=null;
        }
    });
    document.addEventListener("paste",event=>{
        if(root.contains(document.activeElement)||!app.canvas?.selected_nodes?.[node.id]||document.activeElement?.closest?.("input,textarea,select,[contenteditable=true]"))return;
        const images=Array.from(event.clipboardData?.items||[]).filter(item=>item.kind==="file"&&item.type.startsWith("image/")).map(item=>item.getAsFile()).filter(Boolean);
        if(images.length){event.preventDefault();event.stopPropagation();uploadFiles(images);}
    },{signal:controller.signal});
    textarea.addEventListener("dragover",event=>{event.preventDefault();event.stopPropagation();});
    textarea.addEventListener("drop",event=>{
        event.preventDefault();event.stopPropagation();
        if(event.dataTransfer.files.length){uploadFiles(event.dataTransfer.files).then(added=>added?.forEach(reference=>{if(!disposed)insertReference(reference);}));}
        else{const text=event.dataTransfer.getData("text/plain");textarea.setRangeText(text,textarea.selectionStart,textarea.selectionEnd);scriptChanged();}
    });

    function renderDurations() {
        const automatic=value("时长模式","手动设置")==="AI自动分配",busy=["running","generating"].includes(snapshot?.status);
        const actual=automatic&&snapshot?.duration_mode==="AI自动分配"&&Array.isArray(snapshot.scene_seconds)?snapshot.scene_seconds:null;
        for(const[mode,button]of durationModeButtons){const active=mode===(automatic?"AI自动分配":"手动设置");button.setAttribute("aria-checked",String(active));button.tabIndex=active?0:-1;button.disabled=busy;}
        durationIndicator.style.transform=`translateX(${automatic?100:0}%)`;
        targetDurationRow.hidden=!automatic;targetDuration.min=String(durations.length);targetDuration.max=String(15*durations.length);targetDuration.value=String(value("目标总秒数",30));targetDuration.disabled=busy;
        durationDisplaySignature=JSON.stringify([automatic,durations,actual]);
        durationGrid.replaceChildren();
        addScene.disabled = durations.length >= MAX_SCENES;
        durations.forEach((seconds, index) => {
            const row = el("label", "h3-duration");
            const input = el("input"); input.type = "number"; input.min = "1"; input.max = "15"; input.step = "0.5"; input.value = String(automatic?actual?.[index]??"":seconds);input.readOnly=automatic;
            if(automatic&&!Number.isFinite(actual?.[index])){input.type="text";input.value="未分配";input.style.width="88px";}
            input.setAttribute("aria-label", `镜头${index+1}秒数`);input.disabled=busy;
            input.onchange = () => {
                const candidate = Number(input.value);
                durations[index] = Number.isFinite(candidate) ? Math.max(1, Math.min(15, candidate)) : 10;
                input.value = String(durations[index]); saveDurations(); renderSceneChooser();
            };
            const remove = el("button", "", "×"); remove.type = "button"; remove.title = `删除镜头${index+1}`; remove.disabled =busy||durations.length <= 1;
            remove.onclick = event => {event.preventDefault(); const images=isManualMode()?manualImages():null;durations.splice(index,1);if(images){images.splice(index,1);write('手动分镜图片',JSON.stringify(images));}saveDurations(); renderDurations(); renderSceneChooser();};
            row.append(el("span", "", `${index+1}`), input, el("span", "h3-muted", automatic&&!actual?"":"秒"), remove); durationGrid.append(row);
        });
    }
    function sceneAt(index) {
        return snapshot?.scenes?.find(scene => Number(scene.index) === index + 1) || snapshot?.scenes?.[index];
    }
    function optimizationReferences(){
        const result=references.map(reference=>({...reference}));
        const count=snapshot?.scene_count||snapshot?.scenes?.length||durations.length;
        for(let index=0;index<count;index++){const scene=sceneAt(index);result.push({id:`分镜${index+1}`,media:scene?.image,pending:!scene?.image},{id:`首帧${index+1}`,media:scene?.image,pending:!scene?.image});}
        const ending=snapshot?.end_image||sceneAt(count-1)?.end_image;
        result.push({id:"结束帧",media:ending,pending:!ending});
        const identities=new Map(result.map(reference=>[reference.id,reference]));
        for(const reference of snapshot?.reference_bindings||[]){if(reference?.id)identities.set(reference.id,{id:reference.id,media:reference.image,pending:!reference.image});}
        const jobs=new Map((snapshot?.image_jobs||[]).map(job=>[job.id,job]));
        const imageBusy=["加载模型","编码参考","采样中","解码中","准备图片","running","generating"];
        const videoBusy=["生成中","running","generating"];
        for(const reference of identities.values()){
            const job=jobs.get(reference.id);const found=reference.id.match(/^(?:分镜|首帧)([1-9]\d*)$/);const scene=found?sceneAt(Number(found[1])-1):reference.id==="结束帧"?sceneAt(count-1):null;
            if(job){reference.status=job.status;reference.percent=Number.isFinite(job.percent)?job.percent:null;if(imageBusy.includes(job.status))reference.phase="image";}
            if(!job&&["图像生成中","图片生成中"].includes(scene?.status)){reference.phase="image";reference.status=scene.status;}
            if(!job&&["失败","已取消"].includes(scene?.status))reference.status=scene.status;
            if(!reference.phase&&scene&&!scene.video&&videoBusy.includes(scene.status)&&!["失败","已取消"].includes(job?.status)){reference.phase="video";reference.status=scene.status;}
            if(!reference.status)reference.status=reference.pending?"等待":"就绪";
        }
        return Array.from(identities.values());
    }
    function renderSceneChooser() {
        const count = sceneCount(); selected = Math.max(0,Math.min(count-1,selected));
        sceneChooser.replaceChildren();
        for (let index = 0; index < count; index++) {
            const scene = sceneAt(index);
            const seconds = displayedDuration(index);
            const option = new Option(`镜头${index+1}${seconds ? ` · ${seconds}秒` : ""} · ${STATUS[scene?.status] || "等待中"}`, String(index));
            sceneChooser.append(option);
        }
        sceneChooser.value = String(selected);
        previousButton.disabled = selected <= 0; nextButton.disabled = selected >= count - 1;
    }
    function renderPreview() {
        renderSceneChooser();
        const scene = sceneAt(selected);
        let imageItem = imageEditingMode()?storyboardImage(selected):scene?.image;
        let endImageItem = imageEditingMode()?storyboardImage(selected+1):scene?.end_image;
        if (!isTextVideo()&&!imageItem && modeSelect.value === "逐镜参考图" && references[selected]) {
            const parts = references[selected].filename.replaceAll("\\", "/").split("/");
            imageItem = {filename:parts.pop(), subfolder:parts.join("/"), type:"input"};
        } else if (!isTextVideo()&&!imageItem && !selected && references[0]&&!imageEditingMode()) {
            const parts = references[0].filename.replaceAll("\\", "/").split("/");
            imageItem = {filename:parts.pop(), subfolder:parts.join("/"), type:"input"};
        }
        const constrained = !isTextVideo()&&!["上一镜末帧","末帧高清续接"].includes(modeSelect.value) && value("镜头衔接","首尾帧约束") === "首尾帧约束";
        if (!endImageItem && constrained && modeSelect.value === "逐镜参考图" && references[selected+1]) {
            const parts = references[selected+1].filename.replaceAll("\\", "/").split("/");
            endImageItem = {filename:parts.pop(),subfolder:parts.join("/"),type:"input"};
        }
        const imageURL = mediaURL(imageItem); setMedia(image,imageURL); imagePlaceholder.hidden = !!imageURL;
        const endImageURL = mediaURL(endImageItem); setMedia(endImage,endImageURL); endImagePlaceholder.hidden = !!endImageURL;
        endFrameBox.hidden = !constrained && !endImageURL;
        imageBox.hidden=isTextVideo();referenceBindingLabel.hidden=isTextVideo();imagePromptEditor.hidden=isTextVideo();imagePromptEditor.previousElementSibling.hidden=isTextVideo();
        firstFrameBox.style.width = endFrameBox.hidden ? "100%" : "50%";
        const bindings = Array.from(String(scene?.display_prompt || "").matchAll(/@(?:(?:分镜|首帧|图)\d+|结束帧)/g),match=>match[0]);
        const firstBinding = bindings[0] || (["AI生成分镜","手动分镜图"].includes(modeSelect.value) ? `@分镜${selected+1}` : modeSelect.value === "逐镜参考图" ? `@${references[selected]?.id || `图${selected+1}`}` : "");
        const endBinding = bindings[1] || (["AI生成分镜","手动分镜图"].includes(modeSelect.value) ? `@分镜${selected+2}` : modeSelect.value === "逐镜参考图" && references[selected+1] ? `@${references[selected+1].id}` : "");
        firstFrameLabel.textContent = `开始帧${firstBinding ? ` ${firstBinding}` : ""}`;
        endFrameLabel.textContent = `结束帧${endBinding ? ` ${endBinding}` : ""}`;
        referenceBindingLabel.textContent = firstBinding ? `引用：${firstBinding}${constrained && endBinding ? ` → ${endBinding}` : ""}` : "";
        const videoURL = previewMode==="single"?mediaURL(scene?.video):""; setMedia(video,videoURL); videoPlaceholder.hidden = !!videoURL;
        const busy = !!snapshot && ["running","generating"].includes(snapshot.status);
        renderOptimizedPlan();
        renderPromptFormat();
        copyOptimized.disabled=!optimizedScript.value;
        progressState.replaceChildren();
        if (busy) progressState.append(el("span", "h3-spinner"));
        const planning=snapshot?.stage==='planning'&&snapshot?.planning_progress;
        const planningText=planning?`${planning.phase} · 已等待${Math.max(0,Math.floor(planning.elapsed_seconds||0))}秒${planning.output_fragments?` · 已返回${Math.floor(planning.output_fragments)}段`:''}`:null;
        const text = snapshot ? `${planningText || STAGES[snapshot.stage] || STATUS[snapshot.status] || "处理中"}${snapshot.scene_index ? ` · 镜头${snapshot.scene_index} / ${sceneCount()}` : ""}${snapshot.error ? `：${snapshot.error}` : ""}` : "尚未开始";
        progressState.append(document.createTextNode(text));
        if (!videoURL && scene) {
            videoPlaceholder.textContent = STATUS[scene.status] || "等待生成";
            if (scene.error) videoPlaceholder.textContent += `：${scene.error}`;
        }
        const overrides = readOverrides()[String(selected+1)] || {};
        if (editedScene !== selected || !promptDetails.contains(document.activeElement)) {
            videoPromptEditor.value = overrides.video_prompt ?? scene?.prompt ?? scene?.video_prompt ?? "";
            imagePromptEditor.value = overrides.image_prompt ?? scene?.image_prompt ?? "";
            editedScene = selected;
        }
        videoPromptEditor.disabled = !snapshot?.run_id || !!busy&&!value('保留分镜计划',false);
        imagePromptEditor.disabled = !snapshot?.run_id || !!busy&&!value('保留分镜计划',false);
        const finalURL = mediaURL(snapshot?.final_video);
        finalBox.hidden=false;setMedia(finalVideo,finalURL);finalPlaceholder.hidden=!!finalURL;
        sizeFinalVideo();
        finalPlaceholder.textContent=snapshot?.stage==="joining"?"正在合片":snapshot?.scenes?.length&&snapshot.scenes.every(item=>item.video)?"等待合片":"待合片\n等待各镜头完成";
        releaseButton.disabled = !!busy;
        textarea.disabled = false; modeSelect.disabled = busy;
        uploadButton.disabled = busy || uploading || references.length >= MAX_REFERENCES;
        fileInput.disabled = busy;
        addScene.disabled = busy || durations.length >= MAX_SCENES;
        durationGrid.querySelectorAll("input,button").forEach(control => {control.disabled = busy || (control.tagName === "BUTTON" && durations.length <= 1);});
        for(const button of durationModeButtons.values())button.disabled=busy;targetDuration.disabled=busy;
        const automatic=value("时长模式","手动设置")==="AI自动分配";const actual=automatic&&snapshot?.duration_mode==="AI自动分配"&&Array.isArray(snapshot.scene_seconds)?snapshot.scene_seconds:null;
        if(JSON.stringify([automatic,durations,actual])!==durationDisplaySignature)renderDurations();
        gallery.querySelectorAll("button").forEach(control => {control.disabled = busy;});
        for (const control of configControls.values()) control.disabled = busy;
        continueButton.disabled = !snapshot?.run_id || !!busy || queueing||draftChanged;
        redoButton.disabled = !snapshot?.run_id || !scene || !!busy || queueing||draftChanged;
        if(draftChanged)actionState.textContent="原稿已修改，下次运行生效";
        continueButton.hidden = !snapshot || snapshot.status === "completed" || snapshot.status === "done";
        renderImageJobs();
        renderManualControls();
        renderAspectSettings();
        if(!isTextVideo()){renderImageSizing();referenceUpscale.render();}renderVideoInput();
        renderAllScenes();
        renderTimings();
        const directory=String(snapshot?.output_directory||"");if(directory!==knownOutputPath){showOutputPath(directory);copyOutput.textContent="复制路径";}
        renderPlanReuse();
        renderFailure();
        requestAnimationFrame(fitWorkspaceHeight);
    }
    function renderFailure(){
        for(const element of errorElements){delete element.dataset.error;delete element.dataset.errorFlash;element.removeAttribute("aria-invalid");}errorElements.clear();failureTarget=null;failureRelatedTargets=[];
        if(!preflightFailure&&snapshot?.status!=="failed"){failureBox.hidden=true;return;}
        const context={...(preflightFailure||snapshot?.failure_context||{})};
        const legacyTiming=[context.message,snapshot?.error].filter(Boolean).join("\n").match(/镜头\s*(\d+)\s*的?对白时间(?:超出|超过)镜头时长/);
        if(legacyTiming){context.scene_index=Number(legacyTiming[1]);context.field="对白时间";context.stage="planning";}
        const stage=String(context.stage||snapshot?.stage||""),index=Number(context.scene_index??snapshot?.scene_index)||0,field=String(context.field||"");
        let title="任务失败",target=progressState;
        if(["planning","planned"].includes(stage)){title=index?`镜头${index}分镜规划失败`:"分镜规划失败";target=optimizedSection;}
        else if(["images","image","image_progress","end_image","repair","restoring"].includes(stage)){
            const id=context.image_id||(stage==="end_image"?"结束帧":index?`分镜${index}`:null);title=id?`${id}生图失败`:"分镜生图失败";target=imageJobViews.get(id)?.entry||imageSection;
        }else if(["video","loading","rendering"].includes(stage)){title=index?`镜头${index}视频失败`:"视频生成失败";target=previewMode==="all"?allSceneRows.get(Math.max(0,index-1))?.row||previewSection:previewSection;}
        else if(["joining","join"].includes(stage)){title="合片失败";target=finalBox;}
        else if(["settings","configuration","preflight"].includes(stage))title="配置需要调整";
        const targets={"参考图集":gallery,"参考图":gallery,"目标总秒数":targetDuration,"每镜秒数":durationGrid,"时长模式":durationModeBar,"提示词格式":promptFormatBar,"衔接超分配置":referenceUpscale.element,"分镜图长边":imageSizing,"参考读取长边":imageSizing,"视频模型配置":connectionLines.get("video"),"文本模型配置":connectionLines.get("text"),"图像模型配置":imageSampling,"提示词规则":connectionLines.get("rules"),"剧本":textarea,"completed_actions":optimizedSection,"对白时间":optimizedSection};
        const contextTarget=typeof context.target==="string"?context.target:"";
        target=targets[field]||configControls.get(field)||targets[contextTarget]||({references:gallery,durations:durationGrid,video:connectionLines.get("video"),planning:optimizedSection,overview:optimizedSection,script:textarea})[contextTarget]||target;
        if(!target?.isConnected||target.closest('[hidden]'))target=progressState;
        const detail=String(context.message||snapshot?.error||"请检查当前任务后重试。"),message=legacyTiming?`规划结果未通过对白时间校验：${detail}`:detail,suggestion=String(context.suggestion||"").trim();
        if(legacyTiming&&index>0){const durationInput=durationGrid.querySelector(`[aria-label="镜头${index}秒数"]`),sceneView=allSceneRows.get(index-1)?.row;failureRelatedTargets=[durationInput,sceneView].filter(Boolean);for(const related of failureRelatedTargets){related.dataset.error="true";related.dataset.errorFlash="true";errorElements.add(related);}}
        failureText.textContent=`${title}${field?`（${field}）`:""}：${message}${suggestion?`\n建议：${suggestion}`:""}`;failureBox.title=context.exception_type?String(context.exception_type):"";failureBox.hidden=false;
        target.dataset.error="true";target.setAttribute("aria-invalid","true");errorElements.add(target);failureTarget=target;
        if(target===gallery)referenceSection.append(failureBox);
        else if(target===textarea)scriptSection.append(failureBox);
        else if(target===optimizedSection)optimizedSection.insertBefore(failureBox,overviewEditor);
        else if(target===imageSection||target.classList.contains("h3-image-job"))imageSection.insertBefore(failureBox,imageSummaryState);
        else if(target===finalBox)finalBox.insertBefore(failureBox,finalMedia);
        else if(settingsSection.contains(target))settingsSection.append(failureBox);
        else shotPanel.insertBefore(failureBox,progressState);
    }
    function setPreviewMode(mode){
        const next=mode==="all"?"all":"single";
        if(previewMode!==next){releaseAllScenePlayer();stopVideo(video);delete video.dataset.h3src;}
        previewMode=next;node.properties||={};node.properties.h3_director_preview_mode=previewMode;
        previewModeIndicator.style.transform=`translateX(${previewMode==='all'?100:0}%)`;for(const[kind,button]of previewViewButtons)button.setAttribute('aria-checked',String(kind===previewMode));
        for(const[kind,button]of previewViewButtons)button.setAttribute("aria-pressed",String(kind===previewMode));
        previewModeIndicator.style.transform=`translateX(${previewMode==="all"?100:0}%)`;singlePreview.hidden=previewMode!=="single";allScenes.hidden=previewMode!=="all";
        if(previewMode==="single"){const url=mediaURL(sceneAt(selected)?.video);setMedia(video,url);videoPlaceholder.hidden=!!url;}
        renderAllScenes();
    }
    function renderOptimizedPlan(){
        const stored=node.properties?.h3_plan_edit_draft;
        const inactiveDraft=!value("保留分镜计划",false)&&stored&&snapshot?.run_id&&stored.run_id===snapshot.run_id?stored.display:undefined;
        const text=node.properties?.h3_optimized_import_text??planEditData().optimized_script??inactiveDraft??snapshot?.optimized_script??((snapshot?.scenes||[]).map(scene=>`镜头${scene.index}${scene.seconds?` · ${scene.seconds}秒`:""}\n${scene.display_prompt||scene.prompt||scene.video_prompt||""}${scene.image_prompt?`\n分镜图：${scene.image_prompt}`:""}`).join("\n\n"));
        const signature=JSON.stringify(optimizationReferences().map(ref=>[ref.id,ref.media?mediaURL(ref.media):ref.filename?referenceURL(ref):"",!!ref.pending]));
        if(document.activeElement!==optimizedScript&&(text!==lastPlanRender||optimizedScript.value!==text||signature!==lastPlanBindings)){optimizedScript.value=text;lastPlanRender=text;lastPlanBindings=signature;}
        optimizedScript.refreshReferenceStates();renderDialogueView();
    }
    function renderDialogueView(){
        const lines=[];for(const scene of snapshot?.scenes||[]){const spoken=Array.isArray(scene.spoken_dialogues)?scene.spoken_dialogues:[];if(!spoken.length)continue;lines.push(`镜头${scene.index}`);for(const item of spoken){if(!item||typeof item.text!=="string"||!item.text.trim())continue;lines.push(`${item.speaker||item.role||"说话人"}：${item.text.trim()}`);}lines.push("");}
        const text=lines.join("\n").trim()||(!snapshot?.plan_available?"规划后显示最终发声对白":"本剧本没有安排人物对白");if(dialogueView.textContent!==text)dialogueView.textContent=text;
    }
    function releaseAllScenePlayer(){
        const previous=allSceneRows.get(activeAllScene);
        stopVideo(allSceneVideo);delete allSceneVideo.dataset.h3src;allSceneVideo.removeAttribute("poster");allSceneVideo.remove();activeAllScene=null;
        if(previous){previous.picture.hidden=!previous.picture.dataset.h3src;previous.waiting.hidden=!!previous.picture.dataset.h3src;previous.play.hidden=!sceneAt(previous.index)?.video;previous.play.textContent="▶";previous.play.dataset.playing="false";previous.play.setAttribute("aria-label",`播放镜头${previous.index+1}视频`);previous.play.title="播放视频";}
    }
    function sceneMediaRatio(view){
        const canvas=snapshot?.canvas;const explicit=Number(canvas?.width)>0&&Number(canvas?.height)>0?Number(canvas.width)/Number(canvas.height):null;
        if(explicit&&modeSelect.value==='AI生成分镜')return explicit;
        const aspect=value("画幅","跟随图片"),preset={"9:16 竖屏":9/16,"16:9 横屏":16/9,"1:1 方形":1,"3:4 竖屏":3/4,"4:3 横屏":4/3};
        const reference=aspectReference();const source=Array.from(gallery.querySelectorAll("img")).find(image=>image.alt===reference?.id)||gallery.querySelector("img");
        const custom=aspect==="自定义"?Number(value("自定义宽度"))/Number(value("自定义高度")):null;
        return view?.mediaRatio||explicit||(view?.picture?.naturalWidth&&view?.picture?.naturalHeight?view.picture.naturalWidth/view.picture.naturalHeight:null)||preset[aspect]||(custom>0?custom:null)||(source?.naturalWidth&&source?.naturalHeight?source.naturalWidth/source.naturalHeight:null)||9/16;
    }
    function sizeSceneMedia(view){
        const ratio=sceneMediaRatio(view);setPixelStyle(view.row,"--h3-media-width",Math.max(1,previewFrameHeight*ratio));scheduleWorkspaceFit();
    }
    function createSceneRow(index){
        const row=el("div","h3-scene-row");row.dataset.sceneIndex=String(index+1);const bar=el("div","h3-scene-card-head");const title=el("strong"),timing=el("span","h3-time"),imageTiming=el("span","h3-time");imageTiming.hidden=true;bar.append(title,imageTiming,timing);
        const player=el("div","h3-scene-frame");const picture=el("img");picture.loading="lazy";picture.alt=`镜头${index+1}分镜图`;picture.hidden=true;
        const waiting=el("span","h3-muted","等待分镜图"),play=el("button","h3-scene-play","▶");play.type="button";play.hidden=true;play.setAttribute("aria-label",`播放镜头${index+1}视频`);play.title="播放视频";
        const overlay=el("div","h3-scene-cover");overlay.hidden=true;const spinner=el("span","h3-spinner");spinner.setAttribute("aria-hidden","true");const overlayText=el("span","","生成中");overlay.append(spinner,overlayText);player.append(picture,waiting,play,overlay);
        const foot=el("div","h3-scene-card-foot");const status=el("span","h3-scene-row-status h3-grow"),inspect=el("button","","查看/编辑");inspect.type="button";
        inspect.onclick=()=>{if(isManualMode()){chooseManualSlot(index);manualFileInput.click();return;}selected=index;followProgress=false;setPreviewMode("single");renderPreview();promptDetails.open=true;};
        const replace=el("button","","替换图片");replace.type="button";replace.onclick=()=>{if(!manualCanEdit())return;chooseManualSlot(index);manualFileInput.dataset.targetSlot=String(index);manualFileInput.click();};foot.append(status,replace,inspect);
        picture.ondblclick=event=>{event.preventDefault();event.stopPropagation();if(!picture.hidden)openImageViewer(sceneImageItems,`分镜${index+1}`);};
        const regenerate=el("button","h3-scene-regenerate","↻ 标记此图重生");regenerate.type="button";regenerate.hidden=true;regenerate.setAttribute("aria-label",`标记分镜${index+1}重生`);regenerate.setAttribute("aria-pressed","false");regenerate.onclick=event=>{event.preventDefault();event.stopPropagation();toggleSceneRegeneration(index);};player.append(regenerate);
        const remove=el("button","h3-scene-remove","×");remove.type="button";remove.hidden=true;remove.setAttribute("aria-label",`清空分镜${index+1}图片`);remove.title="只删除此图，保留提示词";remove.onclick=event=>{event.preventDefault();event.stopPropagation();clearManualImage(index);};player.append(remove);
        const view={row,title,status,timing,imageTiming,player,picture,overlay,overlayText,play,waiting,regenerate,inspect,replace,remove,index};
        picture.addEventListener("load",()=>sizeSceneMedia(view),{signal:controller.signal});
        row.addEventListener("click",event=>{if(event.target.closest("button,video,input,textarea,summary,a"))return;if(imageEditingMode()&&value("生图重做范围")!=="选择重生"){chooseManualSlot(index);allScenes.focus({preventScroll:true});}else toggleImageChoice(`分镜${index+1}`);});
        picture.draggable=false;
        player.addEventListener("dragstart",event=>{
            if(!isManualMode()||!manualCanEdit()||!manualImages()[index]||event.target.closest("button,video")){event.preventDefault();return;}
            manualDrag={from:index,insertion:index};row.dataset.dragSource="true";event.dataTransfer.effectAllowed="move";event.dataTransfer.setData("application/x-h3-storyboard-slot",JSON.stringify({sourceNodeId:node.id,index}));
            const rect=picture.getBoundingClientRect();if(rect.width&&rect.height)event.dataTransfer.setDragImage(picture,rect.width/2,rect.height/2);
        },{signal:controller.signal});
        player.addEventListener("dragend",clearManualDrag,{signal:controller.signal});
        row.addEventListener("dragover",event=>{
            if(!manualCanEdit())return;const types=Array.from(event.dataTransfer?.types||[]);if(!manualDrag&&!types.some(type=>["Files","application/x-h3-images","application/x-h3-image"].includes(type)))return;
            event.preventDefault();event.stopPropagation();event.dataTransfer.dropEffect=manualDrag?"move":"copy";const rect=row.getBoundingClientRect();previewManualInsertion(index,event.clientX>=rect.left+rect.width/2);
        },{signal:controller.signal});
        row.addEventListener("drop",event=>{
            if(!manualCanEdit())return;event.preventDefault();event.stopPropagation();
            if(manualDrag){const {from,insertion}=manualDrag;clearManualDrag();moveManualImage(from,insertion);return;}
            clearManualDrag();selected=index;consumeManualDrop(event,index);
        },{signal:controller.signal});
        play.onclick=()=>{const scene=sceneAt(index),url=mediaURL(scene?.video);if(!url)return;if(activeAllScene===index){if(allSceneVideo.paused||allSceneVideo.ended)allSceneVideo.play().catch(()=>{});else allSceneVideo.pause();updateAllScenePlayback();return;}releaseAllScenePlayer();activeAllScene=index;const poster=mediaURL(scene?.image)||picture.dataset.h3src;if(poster)allSceneVideo.poster=poster;player.insertBefore(allSceneVideo,play);allSceneVideo.src=url;allSceneVideo.dataset.h3src=url;picture.hidden=true;waiting.hidden=true;overlay.hidden=true;updateAllScenePlayback();allSceneVideo.play().catch(()=>{});};
        row.append(bar,player,foot);allScenes.append(row);allSceneRows.set(index,view);return view;
    }
    function renderAllScenes(){
        for(const[kind,button]of previewViewButtons)button.setAttribute("aria-pressed",String(kind===previewMode));
        previewModeIndicator.style.transform=`translateX(${previewMode==="all"?100:0}%)`;singlePreview.hidden=previewMode!=="single";allScenes.hidden=previewMode!=="all";
        if(previewMode!=="all")return;
        if(video.getAttribute("src")){stopVideo(video);delete video.dataset.h3src;}
        const count=sceneCount();for(const[index,view]of allSceneRows)if(index>=count){if(activeAllScene===index)releaseAllScenePlayer();view.row.remove();allSceneRows.delete(index);}
        for(let index=0;index<count;index++){
            const scene=sceneAt(index),view=allSceneRows.get(index)||createSceneRow(index);
            const seconds=displayedDuration(index);view.title.textContent=`镜头${index+1}${seconds?` · ${seconds}秒`:""}`;
            const status=snapshot?.video_settings_changed&&!["running","generating"].includes(snapshot.status)?"视频待重做":STATUS[scene?.status]||scene?.status||"等待中";view.status.textContent=status;
            renderSceneTimings(index,view);
            const manual=isManualMode(),manualImage=storyboardImage(index);let first=imageEditingMode()?manualImage:scene?.image;
            const reference=index=>{const ref=references[index];if(!ref)return null;const parts=ref.filename.replaceAll("\\","/").split("/");return{filename:parts.pop(),subfolder:parts.join("/"),type:"input"};};
            if(!isTextVideo()&&!first&&!manual&&!aiImageEditingMode()&&(modeSelect.value==="逐镜参考图"||!index))first=reference(modeSelect.value==="逐镜参考图"?index:0);
            const imageURL=mediaURL(first),url=mediaURL(scene?.video);setMedia(view.picture,imageURL);
            if(activeAllScene===index&&allSceneVideo.dataset.h3src!==url)releaseAllScenePlayer();
            const playing=activeAllScene===index&&!!url;
            view.picture.hidden=playing||!imageURL;view.waiting.hidden=playing||!!imageURL;view.waiting.textContent=url?"视频已完成":isTextVideo()?'等待视频生成':"等待分镜图";view.play.hidden=!url;
            if(playing)updateAllScenePlayback();else{view.play.textContent="▶";view.play.dataset.playing="false";view.play.setAttribute("aria-label",`播放镜头${index+1}视频`);view.play.title="播放视频";}
            const busy=["running","generating","生成中","准备图片","修复末帧"].includes(scene?.status)&&!url;
            view.overlay.hidden=!busy;view.overlayText.textContent=status;
            view.row.dataset.manual=String(imageEditingMode());view.row.dataset.manualSelected=String(imageEditingMode()&&selected===index);view.player.draggable=manual&&manualCanEdit()&&!!manualImage;
            view.inspect.textContent=manual?"选择图片":"查看/编辑";view.inspect.disabled=manual&&!manualCanEdit();view.replace.hidden=!aiImageEditingMode();view.replace.disabled=!manualCanEdit();view.remove.hidden=!imageEditingMode()||!manualImage;view.remove.disabled=!manualCanEdit();
            if(manual&&!imageURL&&!url)view.waiting.textContent="点击选中 · 拖入 / 粘贴图片";
            if(playing&&imageURL)allSceneVideo.poster=imageURL;
            sizeSceneMedia(view);
        }
        if(followProgress&&snapshot?.scene_index>0){const active=allSceneRows.get(Math.min(count-1,snapshot.scene_index-1));if(active){const left=active.row.offsetLeft-allScenes.offsetLeft,right=left+active.row.offsetWidth;if(left<allScenes.scrollLeft)allScenes.scrollLeft=left;else if(right>allScenes.scrollLeft+allScenes.clientWidth)allScenes.scrollLeft=right-allScenes.clientWidth;}}
        scheduleWorkspaceFit();
        renderPlanReuse();
    }
    function renderTimings(){
        const timings=snapshot?.timings||{};
        setTimingBadge(planningTime,"规划",timings.planning);setTimingBadge(imagesTotalTime,"生图",timings.images);
        setTimingBadge(currentVideoTime,"本镜",sceneAt(selected)?.video_timing);
        setTimingBadge(videoTotalTime,"视频生成",timings.video);setTimingBadge(joiningTime,"合片",timings.joining);setTimingBadge(taskTotalTime,"总耗时",timings.total);
        for(const view of imageJobViews.values())setTimingBadge(view.timing,"耗时",view.job?.timing);
        if(previewMode==="all")for(const[index,view]of allSceneRows)renderSceneTimings(index,view);
        if(previewMode==="all")scheduleWorkspaceFit();
    }
    function renderSceneTimings(index,view){
        const scene=sceneAt(index),job=snapshot?.image_jobs?.find(job=>job.id===`分镜${index+1}`)||snapshot?.image_jobs?.find(job=>job.kind==="tail_repair"&&Number(job.index)===index+1);
        view.imageTiming.hidden=!job?.timing;setTimingBadge(view.imageTiming,"生图",job?.timing);
        view.timing.hidden=!scene?.video_timing;setTimingBadge(view.timing,"视频",scene?.video_timing);
    }
    function sizeImagePrompt(view){
        const {entry,prompt}=view,expanded=entry.open;
        if(view.promptMeasureExpanded===expanded&&view.promptMeasureText===prompt.value&&view.promptMeasureWidth===prompt.clientWidth)return;
        view.promptMeasureExpanded=expanded;view.promptMeasureText=prompt.value;view.promptMeasureWidth=prompt.clientWidth;
        if(!expanded){prompt.style.height="36px";prompt.style.overflowY="hidden";prompt.scrollTop=0;return;}
        const previous=prompt.style.height||"36px",transition=prompt.style.transition;prompt.style.transition="none";prompt.style.height="1px";
        const natural=Math.max(36,prompt.scrollHeight+2);prompt.style.height=previous;void prompt.offsetHeight;prompt.style.transition=transition;
        prompt.style.height=`${Math.min(760,natural)}px`;prompt.style.overflowY=natural>760?"auto":"hidden";
    }
    function imageJobView(job){
        const entry=el("div","h3-image-job");entry.dataset.imageJob=job.id;
        Object.defineProperty(entry,"open",{get:()=>entry.dataset.expanded==="true",set:next=>{entry.dataset.expanded=String(!!next);expand.textContent=next?"收起":"展开";expand.setAttribute("aria-expanded",String(!!next));sizeImagePrompt(view);}});
        const heading=el("div","h3-job-heading");const thumb=el("span","h3-job-thumb");const picture=el("img");picture.alt=`${job.id}分镜图`;picture.loading="lazy";picture.hidden=true;
        const placeholder=el("span","h3-muted","待生成");thumb.append(picture,placeholder);
        picture.onclick=event=>{if(picture.hidden)return;event.preventDefault();event.stopPropagation();if(value("生图重做范围")==="选择重生"&&planHasEdits())toggleImageChoice(job.id);};picture.ondblclick=event=>{event.preventDefault();event.stopPropagation();if(!picture.hidden)openImageViewer(()=>Array.from(imageJobViews.values()).map(view=>({id:view.job.id,url:mediaURL(view.job.original_image||view.job.image)})),job.id);};
        const description=el("div","h3-job-description");const top=el("div","h3-job-head");const title=el("strong","",job.id);const state=el("span","h3-job-state");const copy=el("button","h3-job-copy","复制提示词");copy.type="button";copy.setAttribute("aria-label",`复制${job.id}生图提示词`);const expand=el("button","h3-job-expand","展开");expand.type="button";expand.setAttribute("aria-expanded","false");expand.setAttribute("aria-label",`展开${job.id}完整提示词`);expand.title="展开查看完整提示词，点击其他位置收起；生成过程中也可查看。";const transfer=el("button","","导入 →");transfer.type="button";transfer.setAttribute("aria-label",`导入${job.id}提示词到独立生图`);transfer.onclick=event=>{event.preventDefault();event.stopPropagation();importManualPrompts(view.job);};top.append(title,state,copy,expand,transfer);
        const timing=el("span","h3-time");const fraction=el("div","h3-job-fraction");const progress=el("progress","h3-job-progress");progress.max=100;progress.hidden=true;progress.setAttribute("aria-label",`${job.id}采样进度`);
        const inserted=el("div","h3-job-inserted");description.append(top,timing,fraction,progress,inserted);heading.append(thumb,description);
        const prompt=el("textarea","h3-job-prompt");prompt.readOnly=true;prompt.setAttribute("aria-label",`${job.id}生图提示词`);const edit=el("button","","定位并编辑本镜");const error=el("div","h3-job-error");error.hidden=true;
        const editPrompt=el("button","","编辑提示词");editPrompt.type="button";editPrompt.setAttribute("aria-label",`编辑${job.id}生图提示词`);editPrompt.title="编辑后点击保存，将此图选为待重生；运行中可展开查看，结束后再修改。";const saveState=el("span","h3-muted");const promptActions=el("div","h3-row");
        const replace=el("button","","替换图片"),clear=el("button","","清空本图");replace.type=clear.type="button";replace.onclick=()=>{if(!manualCanEdit())return;manualFileInput.dataset.targetSlot=String((Number(view.job.index)||1)-1);manualFileInput.click();};clear.onclick=()=>clearManualImage((Number(view.job.index)||1)-1);promptActions.append(editPrompt,replace,clear,saveState);
        const view={entry,title,state,copy,timing,picture,placeholder,fraction,progress,inserted,prompt,edit,editPrompt,replace,clear,saveState,error,transfer,job,editing:false,savedChange:false};
        editPrompt.onclick=()=>{
            if(!imagePromptCanEdit())return;
            entry.open=true;
            if(!view.editing){view.editing=true;prompt.readOnly=false;prompt.value=effectiveImagePrompt(view.job);renderPlanReuse();prompt.focus();return;}
            const next=prompt.value.trim(),previous=String(effectiveImagePrompt(view.job)).trim();view.editing=false;
            if(next&&next!==previous){
                write("保留分镜计划",true);write("继续任务",snapshot.run_id);
                const data=planEditData();if(view.job.id==="结束帧")data.ending={...data.ending,image_prompt:next};else{data.scenes||={};data.scenes[String(view.job.index)]={...data.scenes[String(view.job.index)],image_prompt:next};}write("计划编辑",JSON.stringify(data));
                if(!isManualMode()){const ids=selectedImageIds();ids.add(view.job.id);write("生图重做范围","选择重生");write("重生图片",JSON.stringify(Array.from(ids)));}view.savedChange=true;
            }
            renderPlanReuse();
        };
        entry.addEventListener("click",event=>{if(event.target.closest("button,video,input,textarea,a"))return;if(imageEditingMode()&&/^分镜\d+$/.test(view.job.id)&&value("生图重做范围")!=="选择重生"){event.preventDefault();chooseManualSlot((Number(view.job.index)||1)-1);return;}if(value("生图重做范围")!=="选择重生"||!planHasEdits())return;event.preventDefault();toggleImageChoice(view.job.id);});
        entry.addEventListener("dragover",event=>{if(!manualCanEdit()||!/^分镜\d+$/.test(view.job.id)||manualDrag)return;event.preventDefault();event.stopPropagation();entry.dataset.manualDrop="true";event.dataTransfer.dropEffect="copy";},{signal:controller.signal});
        entry.addEventListener("dragleave",event=>{if(!entry.contains(event.relatedTarget))delete entry.dataset.manualDrop;},{signal:controller.signal});
        entry.addEventListener("drop",event=>{if(!manualCanEdit()||manualDrag||!/^分镜\d+$/.test(view.job.id))return;event.preventDefault();event.stopPropagation();delete entry.dataset.manualDrop;selected=(Number(view.job.index)||1)-1;consumeManualDrop(event,selected);},{signal:controller.signal});
        copy.addEventListener("pointerdown",event=>event.stopPropagation());
        copy.onclick=async event=>{
            event.preventDefault();event.stopPropagation();
            const text=String(view.editing?view.prompt.value:effectiveImagePrompt(view.job));
            if(!text)return;
            copy.disabled=true;
            try{await copyPlainText(text);if(!disposed)copy.textContent="已复制";}
            catch(failure){if(!disposed){copy.textContent="复制失败";copy.title=failure.message||"复制失败，请展开后手动复制。";}}
            finally{if(!disposed)copy.disabled=!String(view.job.prompt||"");}
        };
        edit.onclick=()=>{
            if(isManualMode()){chooseManualSlot((Number(view.job.index)||1)-1);manualFileInput.click();return;}
            const count=snapshot?.scene_count||durations.length;
            selected=Math.max(0,Math.min(count-1,(Number(view.job.index)||1)-1));followProgress=false;renderPreview();promptDetails.open=true;
            if(!imagePromptEditor.disabled)imagePromptEditor.focus({preventScroll:true});
            promptDetails.scrollIntoView({block:"nearest",inline:"nearest",behavior:"auto"});
        };
        const body=el("div","h3-job-body"),bodyInner=el("div");bodyInner.append(promptActions,edit,error);body.append(bodyInner);description.append(prompt,body);entry.append(heading);
        prompt.onclick=event=>{event.stopPropagation();if(!entry.open)entry.open=true;};prompt.addEventListener("input",()=>sizeImagePrompt(view));
        expand.onclick=event=>{event.preventDefault();event.stopPropagation();entry.open=!entry.open;};
        heading.onclick=event=>{if(event.target.closest("button,img,textarea")||value("生图重做范围")==="选择重生")return;entry.open=!entry.open;};
        imageList.append(entry);imageJobViews.set(job.id,view);return view;
    }
    document.addEventListener("pointerdown",event=>{for(const view of imageJobViews.values())if(view.entry.open&&!view.entry.contains(event.target))view.entry.open=false;},{capture:true,signal:controller.signal});
    function renderImageJobs(changedIds=null){
        const ai=imagePlanningMode();imageColumn.hidden=!ai;
        const manual=isManualMode(),slots=Array.from({length:sceneCount()+1},(_,index)=>storyboardImage(index));
        const jobs=Array.isArray(snapshot?.image_jobs)?snapshot.image_jobs.slice(0,MAX_REFERENCES):[];
        const ids=new Set(jobs.map(job=>job.id));
        for(const[id,view]of imageJobViews){if(!ids.has(id)){view.entry.remove();imageJobViews.delete(id);}}
        imageEmpty.hidden=!!jobs.length;
        const summary=snapshot?.image_summary;
        if(summary&&Number.isFinite(summary.completed)&&Number.isFinite(summary.total)){
            const percent=Number.isFinite(summary.percent)?Math.max(0,Math.min(100,summary.percent)):null;
            imageSummaryState.textContent=`已完成 ${summary.completed} / ${summary.total}${percent===null?"":` · ${Math.round(percent*10)/10}%`}`;
            imageTotalProgress.hidden=percent===null;if(percent!==null)imageTotalProgress.value=percent;
        }else{imageSummaryState.textContent="等待规划";imageTotalProgress.hidden=true;}
        if(manual){const filled=slots.slice(0,sceneCount()).filter(Boolean).length;imageSummaryState.textContent=`分镜图片 ${filled} / ${sceneCount()} · 提示词可导入右侧生图`;imageTotalProgress.hidden=true;}
        for(const job of jobs){
            if(!job?.id||changedIds&&!changedIds.has(job.id)&&imageJobViews.has(job.id))continue;
            const view=imageJobViews.get(job.id)||imageJobView(job);view.job=job;
            view.state.textContent=job.status||"等待";
            setTimingBadge(view.timing,"耗时",job.timing);
            const actualSteps=Number.isFinite(job.value)&&Number.isFinite(job.total)&&job.total>0;
            const percent=actualSteps&&Number.isFinite(job.percent)?Math.max(0,Math.min(100,job.percent)):null;
            const sampled=["采样中","解码中","完成","失败","已取消"].includes(job.status);
            view.fraction.textContent=sampled&&percent!==null?`${Math.round(percent*10)/10}% · ${job.value} / ${job.total}步`:"";
            view.progress.hidden=!sampled||percent===null;if(percent!==null)view.progress.value=percent;
            const jobImage=imageEditingMode()?slots[(Number(job.index)||1)-1]:job.image;const url=mediaURL(jobImage);setMedia(view.picture,url);view.placeholder.hidden=!!url;view.placeholder.textContent=manual?"待放入":"待生成";
            view.inserted.textContent=manual?(jobImage?"图片已就绪":"等待选择、拖入或粘贴图片"):(job.inserted?"已自动插入分镜与总览":job.image?"图片已生成，等待插入":"待生成");
            view.transfer.hidden=!manual;view.transfer.disabled=!String(effectiveImagePrompt(job)).trim();
            const text=String(effectiveImagePrompt(job));if(!view.editing&&view.prompt.value!==text&&document.activeElement!==view.prompt){view.prompt.value=text;sizeImagePrompt(view);view.copy.textContent="复制提示词";view.copy.removeAttribute("title");}
            view.copy.disabled=!text;
            view.edit.textContent=manual?"选择图片":job.id==="结束帧"?"定位末镜":"定位并编辑本镜";view.edit.disabled=manual?!manualCanEdit():!snapshot?.run_id;
            view.replace.hidden=view.clear.hidden=!imageEditingMode();view.replace.disabled=!manualCanEdit();view.clear.disabled=!manualCanEdit()||!jobImage;
            view.error.hidden=!job.error;view.error.textContent=job.error?String(job.error):"";
        }
        renderPlanReuse();
    }
    function applyImageProgress(data){
        if(disposed||!data?.job?.id||String(data.node_id)!==String(node.id)||data.run_id!==currentRun||!snapshot||oldRuns.has(data.run_id))return;
        if(data.execution_id&&snapshot.execution_id&&data.execution_id!==snapshot.execution_id)return;
        if(Number.isFinite(data.progress_seq)&&Number.isFinite(snapshot.progress_seq)&&data.progress_seq<snapshot.progress_seq)return;
        if(Number.isFinite(data.progress_seq))snapshot.progress_seq=data.progress_seq;
        if(terminalRuns.has(data.run_id)&&["等待","加载模型","编码参考","采样中","解码中"].includes(data.job.status))return;
        const jobs=Array.isArray(snapshot.image_jobs)?snapshot.image_jobs.slice():[];
        const index=jobs.findIndex(job=>job.id===data.job.id);const previous=index>=0?jobs[index]:{};
        if(previous.status==="完成"&&data.job.status!=="完成")return;
        const job={...previous,...data.job};if(job.status!=="失败"&&data.job.error==null)delete job.error;
        if(index>=0)jobs[index]=job;else if(jobs.length<MAX_REFERENCES)jobs.push(job);else return;
        snapshot.image_jobs=jobs;if(data.image_summary)snapshot.image_summary={...snapshot.image_summary,...data.image_summary};
        if(job.image&&job.inserted&&(mediaURL(previous.image)!==mediaURL(job.image)||!previous.inserted)){
            const bindings=Array.isArray(snapshot.reference_bindings)?snapshot.reference_bindings.slice():[];
            const position=bindings.findIndex(binding=>binding.id===job.id);const binding={id:job.id,image:job.image,status:"就绪"};
            if(position>=0)bindings[position]=binding;else bindings.push(binding);snapshot.reference_bindings=bindings;
            const sceneIndex=Math.max(0,Math.min((snapshot.scene_count||durations.length)-1,(Number(job.index)||1)-1));
            const scene=snapshot.scenes?.find(item=>Number(item.index)===sceneIndex+1);
            if(scene){if(job.id==="结束帧")scene.end_image=job.image;else scene.image=job.image;}
            renderOptimizedPlan();renderAllScenes();
        }
        node.properties||={};node.properties.h3_director_preview=snapshot;
        renderImageJobs(new Set([job.id]));
        renderTimings();
        optimizedScript.refreshReferenceStates();renderDialogueView();
    }
    function readOverrides() {
        try {const raw = JSON.parse(String(value("分镜覆盖","{}"))); return raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};} catch {return {};}
    }
    function savePromptEdits() {
        const overrides = readOverrides();
        overrides[String(selected+1)] = {...overrides[String(selected+1)], video_prompt:videoPromptEditor.value, image_prompt:imagePromptEditor.value};
        write("分镜覆盖",JSON.stringify(overrides));
        if(value("保留分镜计划",false)&&planCanResume()){
            const data=planEditData();data.scenes||={};data.scenes[String(selected+1)]={...data.scenes[String(selected+1)],video_prompt:videoPromptEditor.value,image_prompt:imagePromptEditor.value};write("计划编辑",JSON.stringify(data));
        }
        renderPlanReuse();if(["running","generating"].includes(snapshot?.status))actionState.textContent="修改已保留，停止后再次继续时应用；当前采样保持已提交内容。";
    }
    videoPromptEditor.addEventListener("input",savePromptEdits);
    imagePromptEditor.addEventListener("input",savePromptEdits);
    async function queueSaved(redo) {
        if (!snapshot?.run_id || disposed || queueing||draftChanged) return;
        const originalOverrides=String(value('分镜覆盖','{}'));let changedSceneSeed=false;
        if(redo&&currentH3Seed(node)!==null){
            // Materialize an inherited base before queue preparation so rerolling
            // one shot never rerolls the global seed or invalidates other videos.
            setH3Seed(node,currentH3Seed(node));
            if(seedMode(node)==='random'){
                const overrides=readOverrides(),key=String(selected+1),scene=sceneAt(selected);
                const previous=overrides[key]?.video_seed??scene?.video_seed??((currentH3Seed(node)+selected)>>>0);
                overrides[key]={...(overrides[key]||{}),video_seed:randomH3SceneSeed(previous)};
                write('分镜覆盖',JSON.stringify(overrides));changedSceneSeed=true;
            }
        }
        queueing = true; renderPreview(); actionState.textContent = redo ? `正在提交镜头${selected+1}…` : "正在提交继续生成…";
        write("继续任务",snapshot.run_id);
        write("重做镜头",redo ? selected+1 : 0);
        write("执行范围","完整生成");
        try {
            const result = await app.queuePrompt(0,1);
            if (result === false) throw new Error("提交失败，请查看队列错误。");
            if (!disposed) actionState.textContent = "已提交";
        } catch (error) {if(changedSceneSeed)write('分镜覆盖',originalOverrides);if (!disposed) actionState.textContent = error.message || "提交失败。";}
        finally {
            write("继续任务",planCanResume()?snapshot.run_id:""); write("重做镜头",0);
            queueing = false; if (!disposed) renderPreview();
        }
    }
    continueButton.onclick = () => queueSaved(false);
    redoButton.onclick = () => queueSaved(true);
    function applySnapshot(data, persisted = false) {
        if (disposed || !data || typeof data !== "object") return;
        if (data.node_id != null && String(data.node_id) !== String(node.id)) return;
        if(!persisted&&!data.run_id)return;
        const textInput=data.mode==='文生视频'||data.input_mode==='T2VA'||data.video_input==='文生视频';if(textInput!==isTextVideo())return;
        if(!isTextVideo()&&data.mode&&data.mode!==value("生成模式",MODES[1]))return;
        if((data.prompt_format??data.plan_export?.prompt_format??PROMPT_FORMATS[0])!==value("提示词格式",PROMPT_FORMATS[0]))return;
        if(!persisted&&data.prompt_id&&activePromptId&&data.prompt_id!==activePromptId&&!submissionArmed)return;
        const identity=updateIdentity(snapshot,data,submissionArmed);if(identity.obsolete)return;
        const run = data.run_id || currentRun;
        if (run && oldRuns.has(run)) return;
        if(identity.newAttempt){terminalRuns.delete(run);if(node.properties)delete node.properties.h3_core_terminal;}
        const blocked=terminalRuns.get(run);
        if(blocked&&allowFreshExecution&&["running","generating"].includes(data.status)&&(data.stage==="planning"||data.scene_index===0)){terminalRuns.delete(run);allowFreshExecution=false;}
        if(terminalRuns.has(run)&&["running","generating"].includes(data.status))return;
        const freshRun=run!==currentRun;
        if(freshRun&&data.run_id&&node.properties?.h3_import_submitted){delete node.properties.h3_optimized_import_text;delete node.properties.h3_import_submitted;}
        if(!persisted&&run!==currentRun&&(data.stage==="planning"||data.scene_index===0)){
            draftChanged=false;if(node.properties)delete node.properties.h3_draft_changed;actionState.textContent="";
        }
        if (currentRun && run !== currentRun) {
            if (!persisted && !(data.scene_index === 0 || data.stage === "planning"||data.prompt_id&&data.prompt_id===activePromptId)) return;
            oldRuns.add(currentRun);
            if (oldRuns.size > 16) oldRuns.delete(oldRuns.values().next().value);
            node.properties||={};node.properties.h3_invalidated_runs=Array.from(oldRuns);
            snapshot = null; selected = 0; followProgress = true;
        }
        currentRun = run;
        if(data.prompt_id){activePromptId=data.prompt_id;node.properties||={};node.properties.h3_active_prompt_id=activePromptId;}
        if(['running','generating'].includes(data.status))submissionArmed=false;
        if(freshRun&&["running","generating"].includes(data.status)){
            activeSince=Date.now();activePromptId=data.prompt_id||activePromptId||null;
            node.properties||={};node.properties.h3_active_prompt_id=activePromptId;
            if(!blocked)delete node.properties.h3_core_terminal;
        }
        const incoming = {...data};
        if(freshRun&&!Object.prototype.hasOwnProperty.call(incoming,"failure_context"))incoming.failure_context=null;
        if(freshRun&&!Object.prototype.hasOwnProperty.call(incoming,"error"))incoming.error="";
        if(data.regeneration_applied===true){if(!persisted&&!regenerationApplied){write("生图重做范围","保留现有图片");write("重生图片","[]");}regenerationApplied=true;}
        else if(data.regeneration_applied===false)regenerationApplied=false;
        if (!incoming.scenes && snapshot?.scenes) incoming.scenes = snapshot.scenes;
        snapshot = {...snapshot, ...incoming};
        maybeAutoOpenOutput(persisted);
        if(terminalRuns.has(run)&&!snapshot.final_video&&!['running','generating'].includes(snapshot.status))snapshot.status=terminalRuns.get(run);
        if (followProgress && snapshot.scene_index > 0) selected = snapshot.scene_index-1;
        node.properties ||= {};
        node.properties.h3_director_preview = snapshot;
        renderPreview();refreshModelConnections(false);
        if(persisted&&["running","generating"].includes(snapshot.status))setTimeout(()=>{if(!disposed)reconcileExecutionQueue("restore");},150);
    }
    function syncFromWidgets() {
        if (disposed || syncing) return;
        node.title=`小陈 MiniMax H3 多分镜工作台 V${H3_STUDIO_VERSION}`;
        node.properties||={};node.properties.h3_director_version=H3_STUDIO_VERSION;
        configDetails.open=node.properties.h3_advanced_open??true;
        try {
            const raw = JSON.parse(String(value("参考图集", "[]")));
            references = Array.isArray(raw) ? raw.filter(item => item?.id && item?.filename).slice(0,MAX_REFERENCES) : [];
        } catch {references = [];}
        referenceBindings=node.properties.h3_reference_bindings||referenceBindings;
        const reconciled=isTextVideo()?{references,script:String(value('剧本','')),bindings:referenceBindings}:reconcileH3References(references,String(value("剧本","")),referenceBindings);
        references=reconciled.references;referenceBindings=reconciled.bindings;node.properties.h3_reference_bindings=referenceBindings;node.properties.h3_ref_serial=references.length;
        if(!isTextVideo())write("参考图集",JSON.stringify(references));write("剧本",reconciled.script);
        textarea.value = reconciled.script;
        if(!isTextVideo()&&h3ReferenceTokens(reconciled.script,referenceBindings,references).some(item=>item.missing)){textarea.setAttribute("aria-invalid","true");uploadState.textContent="有未引用的图片，请点击红色引用重新选择，或删除该引用。";uploadState.classList.add("h3-reference-error");uploadState.dataset.referenceError="true";}
        modeSelect.value = MODES.includes(value("生成模式")) ? value("生成模式") : MODES[1];
        const imported=importedPlanData();if(imported&&!snapshot?.run_id&&typeof node.properties.h3_optimized_import_text!=="string"&&typeof imported.optimized_script==="string")node.properties.h3_optimized_import_text=imported.optimized_script;
        const count = Math.min(MAX_SCENES,Math.max(1,Number(value("分镜数量",3)) || 3));
        const rawDurations = String(value("每镜秒数","10,10,10")).split(/[,，\s]+/).filter(Boolean).map(Number);
        durations = Array.from({length:count},(_,index) => {
            const seconds = rawDurations[index] ?? rawDurations.at(-1) ?? 10;
            return Number.isFinite(seconds) ? Math.max(1,Math.min(15,seconds)) : 10;
        });
        for (const [name,control] of configControls) {
            if (control.type === "checkbox") {control.checked = !!value(name);control.setAttribute("aria-checked",String(control.checked));}
            else control.value = String(value(name,""));
            updateControlTitle(control);
        }
        if(!isTextVideo())renderReferences(); renderMentions(); renderMode(); renderDurations(); renderPreview();refreshModelConnections(false);
    }
    releaseButton.onclick = async () => {
        releaseButton.disabled = true; releaseState.textContent = "正在释放…";
        try {
            const response = await api.fetchApi("/h3-director/release", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({full:true}), signal:controller.signal});
            const result = await response.json();
            if (disposed) return;
            if (!response.ok || result.ok === false) throw new Error(result.error || result.message || `释放失败（${response.status}）`);
            releaseState.textContent = result.message || "已释放模型与缓存，下次生成会重新加载。";
        } catch (error) {if (!disposed) releaseState.textContent = error.message || "释放失败。";}
        finally {if (!disposed) releaseButton.disabled = ["running","generating"].includes(snapshot?.status);}
    };

    const privateFields = new Set(["剧本", "参考图集", "生成模式", "分镜数量", "每镜秒数", "继续任务", "重做镜头", "分镜覆盖","保留分镜计划","计划编辑","生图重做范围","重生图片","时长模式","目标总秒数","画幅参考图","手动分镜图片","导入优化计划","提示词格式","衔接超分配置","分镜图长边","参考读取长边","视频短边上限","生图后生成视频"]);
    const visibleBasics=new Set(["镜头衔接","画幅","生成档位","自定义宽度","自定义高度","启用加速LoRA","加速权重","加速步数","完成后","执行范围"]);
    for (const [name,widget] of widgets) {
        hiddenWidget(widget);
        if (!widget) continue;
        const callback = widget.callback;
        widget.callback = function () {const result = callback?.apply(this,arguments); if (!syncing) syncFromWidgets(); return result;};
        if (privateFields.has(name)||name==='视频种子'||name==='随机种子'||name==='视频输入') continue;
        const field = el("label", "h3-field"); field.append(el("span", "", ({"生成档位":"档位","镜头衔接":"衔接","启用加速LoRA":"加速","加速权重":"权重","加速步数":"步数","执行范围":"范围","自定义宽度":"宽度","自定义高度":"高度","文本编码器":"文本编码","原作者模型":"融合模型","视频采样器":"采样器","视频调度器":"调度器","稀疏注意力":"稀疏注意","随机种子":"分镜生图种子"})[name]||name));field.title=name;
        const declaration = nodeData?.input?.required?.[name] || nodeData?.input?.optional?.[name];
        const fieldOptions = declaration?.[1] || widget.options || {};
        let choices = Array.isArray(declaration?.[0]) ? declaration[0] : widget.options?.values;
        if (typeof choices === "function") choices = choices();
        let control;
        if (Array.isArray(choices)) {
            control = el("select"); choices.forEach(choice => control.append(new Option(String(choice),String(choice))));
            control.onchange = () => {writeSetting(name,choices.find(choice => String(choice) === control.value) ?? control.value);};
        } else if (typeof widget.value === "boolean") {
            control = el("input"); control.type = "checkbox"; control.checked = widget.value;control.setAttribute("role","switch");control.setAttribute("aria-checked",String(control.checked));field.classList.add("h3-plan-toggle");
            control.onchange = () => {control.setAttribute("aria-checked",String(control.checked));writeSetting(name,control.checked);};
        } else if (typeof widget.value === "number") {
            control = el("input"); control.type = "number";
            if (fieldOptions.min != null) control.min = String(fieldOptions.min);
            if (fieldOptions.max != null) control.max = String(fieldOptions.max);
            control.step = String(fieldOptions.step ?? 1);
            control.onchange = () => {
                let candidate = Number(control.value);
                if (!Number.isFinite(candidate)) candidate = Number(widget.value) || 0;
                if (fieldOptions.min != null) candidate = Math.max(fieldOptions.min,candidate);
                if (fieldOptions.max != null) candidate = Math.min(fieldOptions.max,candidate);
                writeSetting(name,candidate); control.value = String(candidate);
            };
        } else {
            control = el("input"); control.type = "text"; control.onchange = () => writeSetting(name,control.value);
        }
        control.setAttribute("aria-label",name); control.title = ({
            "生成档位":"控制生成分辨率；较高档位需要更多显存和生成时间。",
            "镜头衔接":"逐镜参考图与AI分镜模式中，选择是否用下一张图约束本镜结束画面。",
            "启用加速LoRA":"启用加速配置以降低生成步数；关闭后使用标准模型与步数。",
            "加速权重":"选择加速模型或LoRA；实际模型名称与外部视频配置节点保持一致。",
            "加速步数":"启用加速配置时使用的采样步数。",
            "标准步数":"关闭加速配置后使用的采样步数。",
            "完成后":"选择生成结束后保留或释放模型；保留模型可减少下次加载时间。",
            "执行范围":"选择本次只准备分镜图，还是继续生成视频。",
            "声音模式":"选择视频是否生成声音；外部角色音色节点可提供指定人物的参考音色。"
        })[name] || name;
        control.dataset.h3Help=control.title;
        if (control.type !== "checkbox") control.value = String(widget.value ?? "");
        updateControlTitle(control);

        configControls.set(name,control);configFields.set(name,field); field.append(control);if(name!=="画幅")(visibleBasics.has(name)?basicGrid:configGrid).append(field);
    }
    for(const name of["生成档位","镜头衔接","启用加速LoRA","加速权重","加速步数","执行范围","完成后","自定义宽度","自定义高度"]){const field=configFields.get(name);if(field)basicGrid.append(field);}
    // This workspace owns one live DOM tree. Comfy's parameter inspector also
    // mounts DOM widgets and writes their measured width back to the widget;
    // rendering this tree there would shrink the canvas to the sidebar width.
    const panel = node.addDOMWidget("h3_director_studio", "h3-studio", root, {serialize:false, hideOnZoom:false,canvasOnly:false,hideInPanel:true,getMinHeight:panelHeight});
    panel.serialize = false;
    // Older inspector sessions may have left an explicit sidebar width behind.
    // With no override Comfy derives this from the owning node on every draw.
    panel.width=undefined;
    // A fixed computeSize makes LiteGraph reserve a fixed-height widget and
    // leaves new node space empty. Let its DOM-widget layout distribute all
    // available height instead; the minimum stays independent of saved size.
    panel.computeSize=undefined;
    panel.computeLayoutSize=()=>({minWidth:0,minHeight:panelHeight(),maxHeight:undefined});
    // LiteGraph only reads widget.computeSize()[1]. The node itself must also
    // expose the workspace width, including during select/auto-resize passes.
    const originalComputeSize=node.computeSize;
    node.computeSize=function(){
        const result=originalComputeSize.apply(this,arguments);const minimum=layoutMinimumSize();
        result[0]=Math.max(Number(result[0])||0,minimum[0]);result[1]=Math.max(Number(result[1])||0,minimum[1]);return result;
    };
    const originalOnResize=node.onResize;
    node.onResize=function(size){
        const minimum=layoutMinimumSize();size[0]=Math.max(Number(size[0])||0,minimum[0]);size[1]=Math.max(Number(size[1])||0,minimum[1]);
        node.properties||={};rememberPanelHeight(size[1]-100);
        const result=originalOnResize?.apply(this,arguments);
        size[0]=Math.max(Number(size[0])||0,minimum[0]);size[1]=Math.max(Number(size[1])||0,minimum[1]);return result;
    };
    const progressListener = event => applySnapshot(event.detail);
    api.addEventListener("h3-director-progress",progressListener);
    const imageProgressListener=event=>applyImageProgress(event.detail);
    api.addEventListener("h3-director-image-progress",imageProgressListener);
    const coreListeners={
        execution_start:event=>{pendingExecutionStart=event.detail?.prompt_id||null;if(submissionArmed&&node.graph===app.graph)activePromptId=pendingExecutionStart;},
        executing:event=>{
            const data=event.detail||{};
            if(String(data.node??data.display_node??"")===String(node.id)&&(data.prompt_id||pendingExecutionStart)){const identity=data.prompt_id||pendingExecutionStart;allowFreshExecution=allowFreshExecution||!!activePromptId&&activePromptId!==identity;activePromptId=identity;node.properties||={};node.properties.h3_active_prompt_id=activePromptId;}
            else if(data.node===null&&(!data.prompt_id||coreEventMatches(data)))reconcileExecutionQueue("executing_end");
        },
        execution_interrupted:event=>{if(coreEventMatches(event.detail))settleSnapshot("cancelled","生成已取消，可编辑后重新运行。");},
        execution_error:event=>{if(coreEventMatches(event.detail))settleSnapshot("failed",event.detail?.exception_message||"生成失败，可编辑后重试。");},
        execution_success:event=>{if(coreEventMatches(event.detail)){if(snapshot?.final_video)settleSnapshot("completed");else reconcileExecutionQueue("success");}},
        status:event=>{const remaining=event.detail?.status?.exec_info?.queue_remaining??event.detail?.exec_info?.queue_remaining;if(remaining===0)reconcileExecutionQueue("idle");},
        reconnected:()=>{recoverLiveProgress(true);reconcileExecutionQueue("reconnected");},
    };
    for(const[name,listener]of Object.entries(coreListeners))api.addEventListener(name,listener);
    document.addEventListener("visibilitychange",()=>{if(!document.hidden){recoverLiveProgress(true);reconcileExecutionQueue("visible");}},{signal:controller.signal});
    const sceneResizeObserver=new ResizeObserver(scheduleWorkspaceFit);sceneResizeObserver.observe(resultColumn);
    const workspaceResizeObserver=new ResizeObserver(scheduleWorkspaceFit);workspaceResizeObserver.observe(root);workspaceResizeObserver.observe(imageColumn);
    const connectionTimer=setInterval(()=>{if(disposed||!root.isConnected||document.hidden||node.flags?.collapsed)return;const rect=root.getBoundingClientRect();if(rect.width&&rect.height&&rect.right>0&&rect.bottom>0&&rect.left<innerWidth&&rect.top<innerHeight){refreshModelConnections(true);renderTimings();recoverLiveProgress();}},1000);
    node._h3Studio = {
        sync:syncFromWidgets,
        prepareReferences,prepareSeed,armExecution,
        connectionsChanged(){refreshModelConnections(true);},
        restoreLayout(){applyLayout(node.properties?.h3_director_layout||"horizontal");if(node.properties?.h3_director_preview)applySnapshot(node.properties.h3_director_preview,true);setPreviewMode(node.properties?.h3_director_preview_mode||"all");recoverLiveProgress(true);},
        receive(message) {
            let data = message?.h3_storyboard;
            if (Array.isArray(data)) data = data.at(-1);
            try {if (typeof data === "string") data = JSON.parse(data);} catch {return;}
            applySnapshot(data,false);
        },
        dispose() {
            disposed = true;if(workspaceFitFrame)cancelAnimationFrame(workspaceFitFrame);clearInterval(connectionTimer);controller.abort(); api.removeEventListener("h3-director-progress",progressListener);
            imageSamplingControls?.dispose();imageSamplingControls=null;imageSamplingSource=null;
            sceneResizeObserver.disconnect();
            workspaceResizeObserver.disconnect();
            api.removeEventListener("h3-director-image-progress",imageProgressListener);
            for(const[name,listener]of Object.entries(coreListeners))api.removeEventListener(name,listener);
            node.computeSize=originalComputeSize;node.onResize=originalOnResize;
            clearManualDrag();stopVideo(video); stopVideo(finalVideo);stopVideo(allSceneVideo);root.remove();delete node.h3ManualStoryboard; delete node._h3Studio;
        },
    };
    node.h3ManualStoryboard={setImages:setManualImages,getReferences:()=>references.map(item=>({...item})),isManualMode,refresh:()=>{renderPreview();notifyManualMode();},getSelectedSlot:()=>selected};
    const keepPlanHelp="首次运行只规划；编辑后继续运行复用计划，不再调用AI。原稿、参考图或时长变化后需重新规划。";
    keepPlanLabel.title=keepPlanHelp;keepPlanToggle.title=keepPlanHelp;
    for(const[mode,button]of modeButtons.map((button,index)=>[MODES[index],button]))button.title=mode+"："+({
        "逐镜参考图":"按参考图顺序作为镜头边界，最后一张可作结束帧；不自动生成分镜图。",
        "上一镜末帧":"第一镜使用参考图，后续镜头接上一镜的结束帧。",
        "AI生成分镜":"先优化剧本，再逐张生成分镜图用于视频；每张最多16张参考。",
        "手动分镜图":"先规划剧本与提示词；支持选择、粘贴、拖入分镜图，或导到独立生图节点后回填。",
        "末帧高清续接":"前一镜结束后，用Image2.1保留状态与色彩修复末帧，再生成下一镜。"
    })[mode];
    referenceTitle.title="单击图片插入引用，右键打开大图；可拖入图片或按Ctrl+V粘贴。";
    referenceExpand.title="展开参考图区并放大缩略图，再次点击收起；运行中也可使用。";
    textarea.title="图1、参考图2、image 3、ref 1会自动绑定；也支持@选择。红色未引用可点击重新选择。运行中的修改用于下次任务。";
    scriptBar.title="直接输入图1、参考图2或image 3即可自动关联，也可用@或单击图片插入。";
    optimizedScript.title="查看完整剧本与分镜；可粘贴已优化的完整总览直接复用计划，滚轮可滚动内容。";
    overviewExpand.title="展开到当前工作区底部以查看更多内容，再次点击收起；运行中也可使用。";
    dialogueToggle.title="切换查看最终视频会发声的台词与说话人，再次点击返回完整总览。";
    autoOpenLabel.title="当前任务完整视频生成成功后自动打开保存文件夹，每个任务只打开一次。";autoOpenToggle.title=autoOpenLabel.title;
    durationModeButtons.get("手动设置").title="分别设置各镜秒数；切换AI分配后仍保留这些手动值。";
    durationModeButtons.get("AI自动分配").title="按目标总时长分配每镜秒数，规划前显示未分配；切回手动恢复原值。";
    targetDuration.title="AI按此总时长分配镜头，每镜1至15秒。";
    syncFromWidgets();
    if (node.properties?.h3_director_preview) applySnapshot(node.properties.h3_director_preview,true);
    applyLayout(activeLayout);
}

app.registerExtension({
    name:"H3.PromptDirector.StoryboardStudio",
    setup(){
        if(app._h3ReferenceQueueGuard)return;app._h3ReferenceQueueGuard=true;const queuePrompt=app.queuePrompt;
        app.queuePrompt=async function(){const nodes=(app.graph?._nodes||[]).filter(node=>node.type===TYPE&&node.mode!==2&&node.mode!==4);for(const node of nodes)if(node._h3Studio?.prepareReferences()===false)return false;for(const node of nodes){node._h3Studio?.prepareSeed();node._h3Studio?.armExecution();}return queuePrompt.apply(this,arguments);};
    },
    beforeRegisterNodeDef(nodeType,nodeData) {
        if(CONFIG_TYPES.has(nodeData.name)){
            const compact=node=>{const computed=node.computeSize?.();if(computed?.[1]>0)node.setSize([node.size?.[0]||computed[0],computed[1]]);colorH3Connections(app,node);};
            const created=nodeType.prototype.onNodeCreated;nodeType.prototype.onNodeCreated=function(){const result=created?.apply(this,arguments);compact(this);return result;};
            const configured=nodeType.prototype.onConfigure;nodeType.prototype.onConfigure=function(){const result=configured?.apply(this,arguments);if(nodeData.name==="H3ImageModelSettings")restoreAddedWidgetValues(this,arguments[0],true);compact(this);return result;};
            return;
        }
        if (nodeData.name !== TYPE) return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {const result = created?.apply(this,arguments);suppressNativePreviews(this);isolateNativeAssetDrawing(this);setupStudio(this,nodeData); return result;};
        const configured = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {const result = configured?.apply(this,arguments);restoreAddedWidgetValues(this,arguments[0]);suppressNativePreviews(this);this._h3Studio?.sync();this._h3Studio?.restoreLayout(); return result;};
        const connected=nodeType.prototype.onConnectionsChange;
        nodeType.prototype.onConnectionsChange=function(){const result=connected?.apply(this,arguments);this._h3Studio?.connectionsChanged();return result;};
        const executed = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {const coreMessage={...message};delete coreMessage.images;delete coreMessage.videos;delete coreMessage.animated;try{return executed?.call(this,coreMessage,...Array.from(arguments).slice(1));}finally{this._h3Studio?.receive(message);}};
        const removed = nodeType.prototype.onRemoved;
        nodeType.prototype.onRemoved = function () {this._h3Studio?.dispose(); return removed?.apply(this,arguments);};
    },
});

