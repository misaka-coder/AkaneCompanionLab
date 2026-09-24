import { escapeHtml } from "../dom.js";
import { actionPhase, renderActionButton } from "./action-button.js";
import { renderQqSetup } from "./qq-setup.js";
import { renderPerception } from "./perception.js";
import { renderComputerUse } from "./computer-use.js";

export function renderSystem(state) {
  const vm = state.viewModel;
  const system = vm?.system;
  if (!system?.available) {
    return `
      ${renderQqSetup(state)}
      ${renderPerception(state)}
      ${renderComputerUse(state)}
      <section class="empty-state glass-panel">
        <span aria-hidden="true">⌁</span>
        <h2>还没有可用的诊断数据</h2>
        <p>启动本地服务后，这里会显示真实连接、资源和运行指标。</p>
        <button class="action-button is-primary" type="button" data-refresh><span>↻</span><b>重新连接</b></button>
      </section>`;
  }

  return `
    <section class="ccv2-system" aria-labelledby="ccv2-page-title">
      <div class="system-heading">
        <div><p class="eyebrow">SYSTEM & DIAGNOSTICS</p><h2>先看结论，再处理问题</h2><p>只展示宿主真实提供的状态；没有事件来源时会明确留空，不生成看似真实的日志。</p></div>
        ${renderActionButton(state, vm, "perception.runDiagnostics", "↻", "重新检查", "primary")}
      </div>

      ${renderQqSetup(state)}
      <section class="system-health-hero glass-panel is-${escapeHtml(system.overallTone)}">
        <div class="system-health-mark"><span>${system.overallTone === "good" ? "✓" : system.overallTone === "warning" ? "!" : "×"}</span></div>
        <div><p class="eyebrow">CURRENT HEALTH</p><h3>${escapeHtml(system.overallLabel)}</h3><p>${escapeHtml(system.overallDetail)}</p></div>
        <div class="system-health-meta"><span><strong>${system.services.filter((item) => item.ready).length}/${system.services.length || 0}</strong><small>服务可用</small></span><span><strong>${system.issues.length}</strong><small>需要留意</small></span></div>
      </section>

      <div class="system-metric-grid">
        ${system.metrics.map((item) => `<article class="system-metric glass-panel is-${escapeHtml(item.tone)}"><span>${metricGlyph(item.label)}</span><div><small>${escapeHtml(item.label)}</small><strong>${escapeHtml(item.value)}</strong></div></article>`).join("")}
      </div>

      <div class="system-layout">
        <div class="system-main-column">
          ${renderPerception(state)}
          ${renderComputerUse(state)}
          <section class="system-panel glass-panel">
            <div class="system-panel-head"><div><p class="eyebrow">SERVICE CHECKS</p><h3>服务连接</h3></div><span class="mini-chip">最近一次读取</span></div>
            ${system.services.length ? `<div class="system-service-list">${system.services.map(renderService).join("")}</div>` : renderInlineEmpty("后端没有返回服务检查结果")}
          </section>

          <section class="system-panel glass-panel">
            <div class="system-panel-head"><div><p class="eyebrow">ISSUES</p><h3>需要处理的事项</h3></div><span class="mini-chip">${system.issues.length || "无"}</span></div>
            ${system.issues.length ? `<div class="system-issue-list">${system.issues.map(renderIssue).join("")}</div>` : `<div class="system-all-clear"><i>✓</i><span><strong>没有发现需要用户处理的问题</strong><small>这代表当前可见检查均正常，不代表宿主拥有完整日志监控。</small></span></div>`}
          </section>

        </div>

        <aside class="system-side-column">
          <section class="system-panel glass-panel">
            <div class="system-panel-head"><div><p class="eyebrow">QUICK RECOVERY</p><h3>常用恢复</h3></div></div>
            <div class="system-recovery-actions">
              ${renderActionButton(state, vm, "perception.runDiagnostics", "↻", "重新检查状态", "primary")}
              ${renderActionButton(state, vm, "workspace.open", "▱", "打开工作区", "soft")}
              ${renderActionButton(state, vm, "advanced.resetWindow", "□", "重置桌宠窗口", "soft")}
            </div>
            <p class="system-recovery-note">重置窗口只能确认请求已发出，请以桌宠窗口是否恢复为准。</p>
          </section>

          <section class="system-panel glass-panel">
            <div class="system-panel-head"><div><p class="eyebrow">INTERACTION DEBUG</p><h3>点击区域排查</h3></div></div>
            <div class="system-setting-list">${system.settings.map((item) => renderSetting(state, vm, item)).join("")}</div>
          </section>

          <details class="system-details glass-panel">
            <summary><span><small>TECHNICAL DETAILS</small><strong>技术详情</strong></span><i>⌄</i></summary>
            <dl>${system.details.map((item) => `<div><dt>${escapeHtml(item.label)}</dt><dd>${escapeHtml(item.value)}</dd></div>`).join("")}</dl>
            <p>不展示密钥、内部数据库位置、缓存路径或提示词正文。</p>
          </details>
        </aside>
      </div>
    </section>`;
}

function renderService(item) {
  return `<article class="system-service ${item.ready ? "is-ready" : "is-warning"}"><i></i><span><strong>${escapeHtml(item.label)}</strong><small>${escapeHtml(item.detail)}</small></span><em>${escapeHtml(item.statusLabel)}</em></article>`;
}

function renderIssue(item) {
  return `<article class="system-issue is-${escapeHtml(item.tone)}"><span>${item.tone === "danger" ? "×" : "!"}</span><div><strong>${escapeHtml(item.title)}</strong>${item.detail ? `<small>${escapeHtml(item.detail)}</small>` : ""}</div></article>`;
}

function renderSetting(state, vm, item) {
  const pending = ["pressed", "pending"].includes(actionPhase(state, item.actionId));
  const available = vm.actions[item.actionId]?.available;
  return `<article class="system-setting"><span><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.description)}</small></span><button class="voice-toggle${item.enabled ? " is-on" : ""}${pending ? " is-pending" : ""}" type="button" data-action="${escapeHtml(item.actionId)}" data-action-value="${item.enabled ? "false" : "true"}" data-action-value-type="boolean" aria-pressed="${item.enabled ? "true" : "false"}" ${available && !pending ? "" : "disabled"}><i></i><span>${pending ? "保存中" : item.enabled ? "已开启" : "已关闭"}</span></button></article>`;
}

function renderInlineEmpty(label) {
  return `<div class="empty-inline"><span>${escapeHtml(label)}</span><small>刷新后仍为空时，请检查本地服务。</small></div>`;
}

function metricGlyph(label) {
  if (/CPU/.test(label)) return "⌁";
  if (/内存/.test(label)) return "▥";
  if (/网络/.test(label)) return "⌁";
  return "●";
}
