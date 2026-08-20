import { escapeHtml } from "../dom.js";
import { actionPhase } from "./action-button.js";

export function renderAbilities(state) {
  const vm = state.viewModel;
  const abilities = vm?.abilities;
  if (!abilities?.available) {
    return `
      <section class="empty-state glass-panel">
        <span aria-hidden="true">⌁</span>
        <h2>能力目录还没有同步</h2>
        <p>${escapeHtml(vm?.shell?.connected ? "后端已连接，但暂时没有返回能力目录。可以刷新后重试。" : "连接桌宠后，这里会显示真实能力、权限模式和配置状态。")}</p>
        <button class="action-button is-primary" type="button" data-refresh><span>↻</span><b>重新同步</b></button>
      </section>`;
  }

  const pending = ["pressed", "pending"].includes(actionPhase(state, "abilities.approvalPolicy.save"));
  const canChangePolicy = vm.actions["abilities.approvalPolicy.save"]?.available;
  return `
    <section class="ccv2-abilities" aria-labelledby="ccv2-page-title">
      <div class="abilities-heading">
        <div><p class="eyebrow">ABILITIES & PERMISSIONS</p><h2>她现在能做什么</h2><p>这里只展示真实注册的能力和当前权限策略；未接通的能力不会伪装成可用。</p></div>
        <button class="action-button" type="button" data-refresh><span>↻</span><b>刷新能力</b></button>
      </div>

      <section class="abilities-hero glass-panel">
        <div class="availability-ring" style="--availability:${Number(abilities.availability ?? 0)}%" aria-label="能力可用率 ${escapeHtml(String(abilities.availability ?? 0))}%">
          <strong>${escapeHtml(String(abilities.availability ?? "—"))}${abilities.availability == null ? "" : "%"}</strong><small>能力可用率</small>
        </div>
        <div class="abilities-hero-copy"><p class="eyebrow">LIVE CAPABILITY CATALOG</p><h3>${escapeHtml(abilities.note || "能力目录已同步")}</h3><p>模型只能调用宿主真实提供、并通过当前安全策略的能力。</p></div>
        <div class="abilities-stats">${abilities.stats.map((item) => `<span><strong>${escapeHtml(item.value)}</strong><small>${escapeHtml(item.label)}</small></span>`).join("")}</div>
      </section>

      <div class="abilities-layout">
        <div class="abilities-main">
          <section class="abilities-panel glass-panel">
            <div class="abilities-panel-head"><div><p class="eyebrow">CAPABILITY MODULES</p><h3>能力模块</h3></div><span class="mini-chip">${abilities.modules.length} 个模块</span></div>
            ${abilities.modules.length ? `<div class="ability-module-grid">${abilities.modules.map(renderModule).join("")}</div>` : renderInlineEmpty("当前没有可展示的能力模块")}
          </section>
          ${renderIntegrations(abilities)}
          ${renderCalls(abilities)}
        </div>

        <aside class="permissions-inspector">
          <section class="permissions-card glass-panel">
            <div class="abilities-panel-head"><div><p class="eyebrow">APPROVAL POLICY</p><h3>权限模式</h3></div><span class="policy-status">${escapeHtml(abilities.safetyStatus)}</span></div>
            <p class="policy-summary">${escapeHtml(abilities.policy.summary || "选择高风险能力在执行前是否需要逐次确认。")}</p>
            <div class="policy-options" role="group" aria-label="能力审批策略">
              ${abilities.policy.availableModes.map((mode) => renderPolicyOption(mode, abilities.policy.defaultMode, pending, canChangePolicy)).join("")}
            </div>
            <div class="hard-boundary-note"><i>✓</i><span><strong>硬安全边界始终保留</strong><small>完全访问不会跳过路径、密钥、URL 和本地边界校验。</small></span></div>
          </section>
          <section class="permissions-card glass-panel">
            <div class="abilities-panel-head"><div><p class="eyebrow">SAFETY STATUS</p><h3>当前保护状态</h3></div></div>
            ${abilities.safetyItems.length ? `<dl class="safety-list">${abilities.safetyItems.map((item) => `<div><dt>${escapeHtml(item.label)}</dt><dd>${escapeHtml(item.value)}</dd></div>`).join("")}</dl>` : renderInlineEmpty("安全状态尚未同步")}
          </section>
        </aside>
      </div>
    </section>`;
}

function renderPolicyOption(mode, currentMode, pending, available) {
  const selected = mode.id === currentMode;
  return `<button class="policy-option${selected ? " is-selected" : ""}" type="button" data-approval-mode="${escapeHtml(mode.id)}" aria-pressed="${selected ? "true" : "false"}"${selected || pending || !available ? " disabled" : ""}><span><strong>${escapeHtml(mode.label)}</strong><small>${escapeHtml(mode.summary)}</small></span><i>${selected ? "当前" : pending ? "保存中" : "选择"}</i></button>`;
}

function renderModule(item) {
  return `<article class="ability-module" data-tone="${escapeHtml(item.tone)}"><span class="ability-module-mark">${moduleGlyph(item.tone)}</span><div><strong>${escapeHtml(item.title)}</strong><p>${escapeHtml(item.description)}</p><small>${escapeHtml(item.permission)}</small></div><span class="module-state is-${escapeHtml(item.statusTone)}">${escapeHtml(item.statusLabel)}</span><em>${escapeHtml(item.count)}</em></article>`;
}

function renderIntegrations(abilities) {
  if (!abilities.integrations.length) return "";
  return `<section class="abilities-panel glass-panel"><div class="abilities-panel-head"><div><p class="eyebrow">LOCAL CONNECTIONS</p><h3>本地服务与扩展</h3></div><span class="mini-chip">只读状态</span></div><div class="integration-list">${abilities.integrations.map((item) => `<div class="integration-row"><i class="${item.ready ? "is-ready" : ""}"></i><span><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.group)}${item.detail ? ` · ${escapeHtml(item.detail)}` : ""}</small></span><em>${escapeHtml(item.status)}</em></div>`).join("")}</div></section>`;
}

function renderCalls(abilities) {
  if (!abilities.calls.length) return "";
  return `<section class="abilities-panel glass-panel"><div class="abilities-panel-head"><div><p class="eyebrow">RUNTIME STATUS</p><h3>最近状态</h3></div><span class="mini-chip">真实诊断</span></div><div class="ability-call-list">${abilities.calls.map((item) => `<div><time>${escapeHtml(item.time || "—")}</time><span><strong>${escapeHtml(item.module || "运行状态")}</strong><small>${escapeHtml(item.description)}</small></span><em>${escapeHtml(item.status || item.method)}</em></div>`).join("")}</div></section>`;
}

function renderInlineEmpty(label) {
  return `<div class="empty-inline"><span>${escapeHtml(label)}</span><small>刷新后仍为空时，请检查本地服务配置。</small></div>`;
}

function moduleGlyph(tone) {
  return { blue: "◌", orange: "▱", purple: "◇", green: "♪", pink: "✦" }[tone] || "·";
}
