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
    <p class="system-recovery-note">连续画面直接交给视觉模型，以当前角色回应，不采集系统音频。开启后选择共享区域；若提示需要点击授权，请在桌宠弹出的菜单点击「屏幕共享」。画面会在对话或观察评估时发送给视觉服务。</p>
    <p class="perception-boundary-note">不会自动读取剪贴板或附带窗口标题。按需读取窗口的桌面工具独立于屏幕共享，受绑定电脑与能力权限控制；关闭「看屏幕」只停止画面采样。手动粘贴文字、图片和文件仍可正常使用。</p>
    ${!perception.available ? '<p role="status">等待桌宠实时连接，暂时不能修改观察设置。</p>' : ""}
    ${perception.error ? `<p class="form-message" role="alert">${escapeHtml(perception.error)}</p>` : ""}
    <div class="system-setting-list">
      ${toggle(state, "perception.screenVision.setEnabled", "看屏幕", "只开启采样，不代表每次都搭话", perception.screenVisionEnabled)}
      ${toggle(state, "perception.proactiveWake.setEnabled", "适度主动", "由模型决定开口或安静；关闭后仍可正常对话", perception.proactiveWakeEnabled)}
    </div>
    <div class="model-form-grid">
    ${choice(state, "perception.screenVision.setSampleIntervalSec", "采样间隔", perception.screenVisionSampleIntervalSec, [0.5, 1, 2, 3, 5, 10, 30], "秒")}
    ${choice(state, "perception.screenVision.setWindowSec", "观察时间窗", perception.screenVisionWindowSec, [5, 10, 20, 30, 60, 120, 300], "秒")}
    ${choice(state, "perception.screenVision.setFrameCount", "每轮代表帧", perception.screenVisionFrameCount, [1, 2, 3, 4, 5], "张")}
    ${choice(state, "perception.screenVision.setMaxEdge", "单帧最长边", perception.screenVisionMaxEdge, [640, 960, 1280, 1600, 1920], "像素")}
    ${choice(state, "perception.screenVision.setPacking", "画面组织方式", perception.screenVisionPacking, [["contact_sheet", "时间拼图＋最新清晰帧"], ["frames", "按时间顺序发送多图"]])}
    ${choice(state, "perception.proactiveWake.setIntervalSec", "主动评估间隔", perception.proactiveWakeIntervalSec, [15, 30, 60, 120, 300, 600], "秒")}
    </div>
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
