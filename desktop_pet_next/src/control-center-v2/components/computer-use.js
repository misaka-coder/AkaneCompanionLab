import { escapeHtml } from "../dom.js";

export function renderComputerUse(state) {
  const cu = state.viewModel?.computerUse || {};
  const status = { connecting: "正在连接本机", unavailable: cu.available ? "本机执行器未连接" : "需要桌面应用", idle: "等待任务选择窗口",
    selected: "已选择窗口", running: "正在操作", paused: "输入活动后，等待任务重新观察", stopped: "已明确停止", stopping: "正在停止" }[cu.status] || "状态尚未读取";
  const button = (action, title, tone = "soft") => `<button type="button" class="action-button is-${tone}" data-computer-use="${action}" ${!cu.available || (cu.busy && action !== "stop") ? "disabled" : ""}><b>${title}</b></button>`;
  const targets = [...(cu.targets || [])];
  const workflow = cu.workflow;
  const workflowLabel = workflow ? ({ running: "连续流程正在执行", awaiting_approval: "连续流程等待本步批准", paused: "连续流程已暂停", unknown: "本步输入结果待核实", completed: "连续流程已完成" }[workflow.state] || "连续流程准备中") : "";
  if (cu.target_id && !targets.some(target => target.id === cu.target_id)) targets.unshift({ id: cu.target_id, title: cu.target_title });
  return `<section class="system-panel glass-panel computer-use-panel" aria-labelledby="computer-use-title">
    <div class="system-panel-head"><div><p class="eyebrow">COMPUTER USE</p><h3 id="computer-use-title">电脑操作</h3></div><span class="mini-chip">本机 · ${cu.status === "unavailable" || cu.status === "connecting" ? "输入状态未知" : cu.input_enabled ? "输入已开启" : "输入已关闭"}</span></div>
    <p role="status" aria-live="polite">${escapeHtml(status)}${cu.window_title ? ` · ${escapeHtml(cu.window_title)}` : ""}</p>
    ${cu.preference_error ? `<p class="system-recovery-note" role="alert">${escapeHtml(cu.preference_error === "device_preferences_save_failed" ? "本次开关已应用，但未能保存；重启后可能恢复之前的选择。请重新设置。" : "未能读取已保存的开关，当前保持关闭；请重新设置。")}</p>` : ""}
    ${workflow ? `<p role="status" aria-live="polite">${escapeHtml(workflowLabel)} · ${Math.min(Number(workflow.next_step || 0), Number(workflow.total_steps || 0))}/${Number(workflow.total_steps || 0)} 步已核实</p>` : ""}
    <p class="system-recovery-note">开启后，Akane 可按你的任务选择窗口并操作。移动鼠标或敲键会暂停当前输入；输入停下后，Akane 可重新观察并继续，无需点击恢复。点击「立即停止」会保持停止，直到你解除。窗口读取与屏幕共享分别控制。</p>
    <div class="system-recovery-actions">${button(cu.input_enabled ? "disable" : "enable", cu.input_enabled ? "关闭电脑输入" : "允许电脑输入", "primary")}
      ${button("resume", "解除停止")}${button("stop", "立即停止", "danger")}${button("refresh", "刷新状态")}</div>
    ${cu.error ? `<p class="system-recovery-note" role="alert">操作未完成：${escapeHtml(({ desktop_input_disabled: "请先开启电脑输入", no_control_session: "当前没有可恢复的任务", desktop_busy: "当前步骤尚未结束，可随时停止" })[cu.error] || cu.error)}</p>` : ""}
    <p class="system-recovery-note">首次启动默认开启，之后记住你的选择。是否需要逐次确认由权限模式决定；完全访问仍尊重这里的关闭和「立即停止」。重启不会恢复旧任务或重复输入。</p>
    <label class="computer-use-target-label">桌面窗口范围
      <select data-computer-use-target ${!cu.available || cu.busy ? "disabled" : ""}>
        <option value="" ${!cu.target_id ? "selected" : ""}>按任务选择窗口</option>
        ${targets.map(target => `<option value="${escapeHtml(target.id)}" ${target.id === cu.target_id ? "selected" : ""}>${escapeHtml(target.title || target.application || "未命名窗口")}</option>`).join("")}
      </select>
    </label>
    <div class="system-recovery-actions">${button("refresh_targets", "读取窗口列表")}${button("set_target", "应用窗口范围")}</div>
    <p class="system-recovery-note">当前：${escapeHtml(cu.target_title || "按任务选择窗口")}。限定后仅允许该窗口及其所属弹窗。更改范围会先停止操作，确认后点击「解除停止」。个人 Chrome 的标签页由网页任务另行选择。</p>
    <h4>个人 Chrome</h4>
    <p role="status">${cu.chrome_enabled == null ? "连接状态未知" : cu.chrome_enabled ? (cu.chrome_connected ? "Chrome 已连接 · 按任务选择标签页" : cu.chrome_status === "connecting" ? "连接尚未完成，请查看 Chrome 是否有待确认提示" : cu.chrome_status === "connection_failed" ? "连接失败，请检查 Chrome 后重试" : "已允许连接 · 等待任务") : "连接已关闭"}</p>
    <p class="system-recovery-note">首次启动默认允许连接，之后记住你的选择。在 Chrome 打开 <code>chrome://inspect/#remote-debugging</code> 开启连接，并在 Chrome 提示时允许。Akane 将使用已有登录状态；断开连接会保留浏览器和标签页。网页输入还需开启上方的电脑输入。</p>
    <div class="system-recovery-actions">${button(cu.chrome_enabled ? "chrome_disable" : "chrome_enable", cu.chrome_enabled ? "断开并关闭 Chrome 连接" : "允许连接个人 Chrome")}</div>
  </section>`;
}
