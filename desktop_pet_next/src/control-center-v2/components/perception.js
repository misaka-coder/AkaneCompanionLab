import { observationDuration, observationConfigurationIssue } from "../../screen-observation.js";
import { escapeHtml } from "../dom.js";
import { actionPhase, renderActionButton } from "./action-button.js";

export function renderPerception(state) {
  const vm = state.viewModel;
  const perception = vm?.perception;
  if (!perception) return "";
  const status = {
    off: "未共享屏幕", starting: "等待共享授权", watching: "正在采样",
    waiting: "等待新画面", error: "采集失败", unsupported: "当前环境不支持屏幕共享"
  }[perception.status] || perception.status;
  return `<section class="system-panel glass-panel" aria-label="桌面观察与主动陪伴">
    <div class="system-panel-head"><div><p class="eyebrow">SCREEN & COMPANIONSHIP</p><h3>桌面观察与主动陪伴</h3></div><span class="mini-chip">${escapeHtml(status)}</span></div>
    <p class="system-recovery-note">连续画面直接交给视觉模型，以当前角色回应，不采集系统音频。看视频时尽量共享视频所在窗口，避免视频只占整张截图的一小角；对白只能通过可读字幕理解。若提示需要点击授权，请在桌宠弹出的菜单点击「屏幕共享」。画面会在对话或观察评估时发送给视觉服务。</p>
    <p class="perception-boundary-note">不会自动读取剪贴板或附带窗口标题。按需读取窗口的桌面工具独立于屏幕共享，受绑定电脑与能力权限控制；关闭「看屏幕」只停止画面采样。手动粘贴文字、图片和文件仍可正常使用。</p>
    ${!perception.available ? '<p role="status">等待桌宠实时连接，暂时不能修改观察设置。</p>' : ""}
    ${observationConfigurationIssue(perception) ? `<p role="alert">${escapeHtml(observationConfigurationIssue(perception))}</p>` : ""}
    ${Object.entries(state.actionStates || {}).filter(([id, item]) => id.startsWith("perception.") && item.phase === "failed").map(([, item]) => `<p class="form-message" role="alert">${escapeHtml(item.detail || item.label || "设置未能保存，请重试")}</p>`).join("")}
    ${perception.preparation === "preparing" ? '<p role="status">正在准备观察能力，期间继续采样，准备完成后再取最新画面发起请求。</p>' : ""}
    ${perception.preparationError ? `<p role="status">${escapeHtml(perception.preparationError)}</p>` : ""}
    ${perception.error ? `<p class="form-message" role="alert">${escapeHtml(perception.error)}</p>` : ""}
    <div class="system-setting-list">
      ${toggle(state, "perception.screenVision.setEnabled", "看屏幕", "只开启采样，不代表每次都搭话", perception.screenVisionEnabled)}
      ${toggle(state, "perception.proactiveWake.setEnabled", "适度主动", "由模型决定开口或安静；关闭后仍可正常对话", perception.proactiveWakeEnabled)}
    </div>
    <div class="model-form-grid">
    ${numberInput(state, "perception.screenVision.setSampleIntervalSec", "采样间隔", perception.screenVisionSampleIntervalSec, 0.5, 30, 0.1, "秒")}
    ${numberInput(state, "perception.screenVision.setWindowSec", "每张拼图观察窗口", perception.screenVisionWindowSec, 5, 300, 1, "秒")}
    ${numberInput(state, "perception.screenVision.setFrameCount", "每张拼图代表帧（多图模式为每轮帧数）", perception.screenVisionFrameCount, 1, 5, 1, "张")}
    ${numberInput(state, "perception.screenVision.setMaxEdge", "单帧最长边", perception.screenVisionMaxEdge, 640, 1920, 1, "像素")}
    ${choice(state, "perception.screenVision.setPacking", "画面组织方式", perception.screenVisionPacking, [["contact_sheet", "纵向时间拼图（从上到下）"], ["frames", "独立时间帧（细节更清晰）"]])}
    ${numberInput(state, "perception.proactiveWake.setIntervalSec", "主动评估最短间隔", perception.proactiveWakeIntervalSec, 15, 600, 1, "秒")}
    ${numberInput(state, "perception.screenVision.setSheetCount", "每次请求拼图数", perception.screenVisionSheetCount, 1, 4, 1, "张", perception.screenVisionPacking !== "contact_sheet")}
    </div>
    <p class="system-recovery-note">${perception.screenVisionPacking === "contact_sheet" ? `累计覆盖约 ${observationDuration(perception)} 秒，最多 ${perception.screenVisionSheetCount} 张纵向时间拼图一起请求。最新采样已在最后一格，不重复附图。多张拼图须先攒满时长；实际请求间隔还受评估间隔、上一轮回复和用户交互影响。` : "按时间顺序发送独立代表帧，每张图紧邻对应时间说明；适合视频和小字细节，拼图数量在此模式下不生效。"} 长窗口最多保留约 120 个时间采样，避免内存持续增长。数值可直接输入，离开输入框后保存。</p>
    <p class="system-recovery-note">当前可用 ${perception.bufferedFrames} 张代表帧${perception.evaluating ? " · 正在评估是否开口" : ""}。观察时间窗内均匀取样并保留最新画面；帧数、清晰度和请求频率越高，通常调用开销越大。</p>
    ${renderActionButton(state, vm, "perception.screenVision.clear", "↻", "清空最近画面", "soft")}
  </section>`;
}

function toggle(state, action, title, detail, enabled) {
  const pending = ["pressed", "pending"].includes(actionPhase(state, action));
  const available = state.viewModel.actions[action]?.available;
  return `<article class="system-setting"><span><strong>${title}</strong><small>${detail}</small></span><button class="voice-toggle${enabled ? " is-on" : ""}" type="button" data-action="${action}" data-action-value="${!enabled}" data-action-value-type="boolean" aria-pressed="${enabled}" ${!available || pending ? "disabled" : ""}><i></i><span>${pending ? "等待确认" : enabled ? "已开启" : "已关闭"}</span></button></article>`;
}

function choice(state, action, title, value, values, unit = "") {
  const pending = ["pressed", "pending"].includes(actionPhase(state, action));
  const available = state.viewModel.actions[action]?.available;
  const options = values.map((item) => Array.isArray(item) ? item : [item, `${item} ${unit}`]);
  if (!options.some(([key]) => String(key) === String(value))) options.push([value, `${value} ${unit}`]);
  return `<label class="${action.endsWith("setPacking") ? "is-wide" : ""}"><span>${title}${pending ? " · 保存中" : ""}</span><select aria-label="${title}" data-perception-choice="${action}" data-value-type="${typeof value}" ${!available || pending ? "disabled" : ""}>${options.map(([key, label]) => `<option value="${escapeHtml(key)}" ${String(key) === String(value) ? "selected" : ""}>${escapeHtml(label)}</option>`).join("")}</select></label>`;
}

function numberInput(state, action, title, value, min, max, step, unit, inactive = false) {
  const pending = ["pressed", "pending"].includes(actionPhase(state, action));
  const available = state.viewModel.actions[action]?.available;
  return `<label><span>${title}（${unit}）${pending ? " · 保存中" : ""}</span><input type="number" aria-label="${title}" data-perception-choice="${action}" data-value-type="number" min="${min}" max="${max}" step="${step}" value="${escapeHtml(value)}" ${!available || pending || inactive ? "disabled" : ""}><small>${min}–${max} ${unit}</small></label>`;
}
