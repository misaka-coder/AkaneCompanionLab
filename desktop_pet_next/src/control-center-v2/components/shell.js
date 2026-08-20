import { escapeHtml, initial, safeStyleUrl } from "../dom.js";
import { renderOverview } from "./overview.js";

export function renderControlCenterShell(root, state) {
  const vm = state.viewModel;
  const character = vm?.character;
  const avatarStyle = safeStyleUrl(character?.visuals?.avatar);
  const connected = vm?.shell?.connectionStatus || state.phase;
  root.innerHTML = `
    <main class="ccv2-shell" data-connection="${escapeHtml(connected)}">
      <aside class="ccv2-rail">
        <div class="ccv2-brand"><span class="brand-mark">A</span><span><strong>桌宠控制中心</strong><small>V2 候选实现</small></span></div>
        <nav aria-label="控制中心导航"><button class="nav-item is-active" type="button"><span aria-hidden="true">⌂</span><b>总览</b></button></nav>
        <div class="rail-spacer"></div>
        <div class="migration-note"><i></i><span><strong>真实数据模式</strong><small>没有演示状态和假按钮</small></span></div>
        <div class="rail-character">
          ${avatarStyle ? `<span class="rail-avatar" style="--avatar-image:${avatarStyle}"></span>` : `<span class="rail-avatar rail-avatar-fallback">${initial(character?.displayName)}</span>`}
          <span><strong>${escapeHtml(character?.displayName || "等待角色")}</strong><small>${escapeHtml(vm?.shell?.connectionLabel || "正在连接")}</small></span>
        </div>
      </aside>
      <section class="ccv2-surface">
        <header class="ccv2-topbar">
          <div><p class="eyebrow">DESKTOP COMPANION</p><h1 id="ccv2-page-title">今天想让她做什么？</h1></div>
          <div class="topbar-actions">
            <span class="connection-chip" title="${escapeHtml(vm?.shell?.connectionDetail || "")}"><i></i><span>${escapeHtml(vm?.shell?.connectionDetail || vm?.shell?.connectionLabel || "正在连接")}</span></span>
            <button class="round-button${state.phase === "refreshing" ? " is-spinning" : ""}" type="button" data-refresh aria-label="刷新真实状态" title="刷新真实状态">↻</button>
          </div>
        </header>
        <div class="ccv2-scroll">${renderOverview(state)}</div>
        <div class="toast-region" aria-live="polite">${renderToast(state)}</div>
      </section>
    </main>`;
}

function renderToast(state) {
  const entries = Object.values(state.actionStates).filter((item) => item?.phase === "failed" || item?.phase === "unknown");
  const latest = entries.at(-1);
  return latest ? `<div class="toast is-${escapeHtml(latest.phase)}"><strong>${escapeHtml(latest.label)}</strong>${latest.detail ? `<span>${escapeHtml(latest.detail)}</span>` : ""}</div>` : "";
}
