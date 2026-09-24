import { escapeHtml } from "../dom.js";
import { renderActionButton } from "./action-button.js";
import { QQ_SETUP_ACTIONS } from "../qq-setup.js";

export function renderQqSetup(state) {
  const vm = state.viewModel;
  const setup = vm?.qqSetup;
  const native = setup?.native || {};
  const connection = setup?.connection;
  const row = (label, value, good = false) => `<article class="system-service${good ? " is-ready" : " is-warning"}"><i></i><span><strong>${escapeHtml(label)}</strong><small>${escapeHtml(value)}</small></span></article>`;
  return `<section class="system-panel glass-panel" aria-labelledby="qq-setup-title">
    <div class="system-panel-head"><div><p class="eyebrow">QQ CONNECTION</p><h3 id="qq-setup-title">QQ 接入 · 本地扫码</h3></div><span class="mini-chip">${native.botQq ? escapeHtml(native.botQq) : "当前 Bot"}</span></div>
    <p role="status" aria-live="polite">${escapeHtml(setup?.busy ? "正在检查或处理，请稍候…" : setup?.detail || "等待桌面连接信息。")}</p>
    <div class="system-service-list">
      ${row("1 · NapCat 安装", native.installed ? "已检测到完整的 Windows Shell 安装" : "尚未确认安装位置", native.installed)}
      ${row("2 · 扫码登录页", native.webuiReady ? "管理页可访问；请在页面内完成扫码" : native.starting ? "启动中，登录页尚未就绪" : "未就绪", native.webuiReady)}
      ${row("3 · QQ 连接", connection?.ok ? `QQ ${connection.actualQq} 在线，账号与当前 Bot 匹配` : connection?.actualQq ? `实际登录 QQ ${connection.actualQq}，尚未通过校验` : "未确认；扫码后点击重新检查", connection?.ok)}
    </div>
    <div class="system-recovery-actions">
      ${renderActionButton(state, vm, QQ_SETUP_ACTIONS.detect, "↻", "重新检查", "soft")}
      ${renderActionButton(state, vm, QQ_SETUP_ACTIONS.select, "▱", "选择 NapCat 目录", "soft")}
      ${renderActionButton(state, vm, QQ_SETUP_ACTIONS.start, "▷", "配置并启动 NapCat", "soft")}
      ${renderActionButton(state, vm, QQ_SETUP_ACTIONS.openLogin, "▦", "打开扫码登录页", "primary")}
    </div>
    <p class="system-recovery-note">仅使用你信任的现有 NapCat 安装。配置并启动会先备份、再更新 Akane 自己的两项连接；不下载软件，不终止已有 QQ。扫码由你完成，不自动发送测试消息。</p>
    <details class="system-details"><summary><span><strong>排查与连接详情</strong></span><i>⌄</i></summary>
      <p>${native.backendPort ? `本地后端端口 ${escapeHtml(native.backendPort)}；OneBot 端口 ${escapeHtml(native.onebotPort)}。` : "通过本地 QQ 启动入口打开桌宠后，可读取本次连接端口。"}</p>
      <p>${native.configured ? "所选 NapCat 的 Akane 连接配置匹配。" : "尚未确认 NapCat 连接配置匹配。"}在线自检不等于双向消息测试通过。</p>
      ${renderActionButton(state, vm, QQ_SETUP_ACTIONS.openFolder, "▱", "打开安装目录", "soft")}
    </details>
  </section>`;
}
