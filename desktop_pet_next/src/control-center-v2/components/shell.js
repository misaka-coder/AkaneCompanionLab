import { escapeHtml, initial, safeStyleUrl } from "../dom.js";
import { renderCharacterAppearance } from "./appearance.js";
import { renderAbilities } from "./abilities.js";
import { renderChat } from "./chat.js";
import { renderOverview } from "./overview.js";
import { renderModelService } from "./model.js";
import { renderVoice } from "./voice.js";
import { renderSystem } from "./system.js";

export function renderControlCenterShell(root, state) {
  const vm = state.viewModel;
  const character = vm?.character;
  const avatarStyle = safeStyleUrl(character?.visuals?.avatar);
  const connected = vm?.shell?.connectionStatus || state.phase;
  const activePage = state.activePage || "overview";
  const pageTitle = activePage === "appearance"
    ? "把这里变成她的空间"
    : activePage === "abilities"
      ? "能力清楚，权限也清楚"
    : activePage === "voice"
      ? "声音顺手，状态也诚实"
    : activePage === "model"
      ? "模型连得上，配置也说得清"
    : activePage === "system"
      ? "看得懂，也修得动"
    : activePage === "chat"
      ? "聊过的话，都留在这里"
      : "今天想让她做什么？";
  root.innerHTML = `
    <main class="ccv2-shell" data-connection="${escapeHtml(connected)}">
      <aside class="ccv2-rail">
        <div class="ccv2-brand"><span class="brand-mark">A</span><span><strong>桌宠控制中心</strong><small>真实运行设置</small></span></div>
        <nav aria-label="控制中心导航">
          ${renderNavItem("overview", "⌂", "总览", activePage)}
          ${renderNavItem("chat", "◌", "聊天", activePage)}
          ${renderNavItem("appearance", "✦", "角色与外观", activePage)}
          ${renderNavItem("voice", "♪", "语音与唤醒", activePage)}
          ${renderNavItem("model", "◇", "模型服务", activePage)}
          ${renderNavItem("abilities", "⌁", "能力与权限", activePage)}
          ${renderNavItem("system", "⚙", "系统与诊断", activePage)}
        </nav>
        <div class="rail-spacer"></div>
        <div class="migration-note"><i></i><span><strong>真实数据模式</strong><small>没有演示状态和假按钮</small></span></div>
        <div class="rail-character">
          ${avatarStyle ? `<span class="rail-avatar" style="--avatar-image:${avatarStyle}"></span>` : `<span class="rail-avatar rail-avatar-fallback">${initial(character?.displayName)}</span>`}
          <span><strong>${escapeHtml(character?.displayName || "等待角色")}</strong><small>${escapeHtml(vm?.shell?.connectionLabel || "正在连接")}</small></span>
        </div>
      </aside>
      <section class="ccv2-surface">
        <header class="ccv2-topbar" data-tauri-drag-region>
          <div data-tauri-drag-region><p class="eyebrow" data-tauri-drag-region>DESKTOP COMPANION</p><h1 id="ccv2-page-title" data-tauri-drag-region>${pageTitle}</h1></div>
          <div class="topbar-actions">
            <span class="connection-chip" title="${escapeHtml(vm?.shell?.connectionDetail || "")}"><i></i><span>${escapeHtml(vm?.shell?.connectionDetail || vm?.shell?.connectionLabel || "正在连接")}</span></span>
            <button class="round-button${state.phase === "refreshing" ? " is-spinning" : ""}" type="button" data-refresh aria-label="刷新真实状态" title="刷新真实状态">↻</button>
            <span class="window-actions" aria-label="窗口操作">
              ${renderWindowButton(state, "window.minimize", "最小化", "−")}
              ${renderWindowButton(state, "window.maximize", "最大化或还原", "□")}
              ${renderWindowButton(state, "window.close", "关闭", "×", " is-close")}
            </span>
          </div>
        </header>
        <div class="ccv2-scroll${activePage === "chat" ? " is-chat" : ""}">${renderPage(activePage, state)}</div>
        <div class="toast-region" aria-live="polite">${renderToast(state)}</div>
      </section>
    </main>`;
}

function renderPage(activePage, state) {
  if (activePage === "chat") return renderChat(state);
  if (activePage === "appearance") return renderCharacterAppearance(state);
  if (activePage === "voice") return renderVoice(state);
  if (activePage === "model") return renderModelService(state);
  if (activePage === "abilities") return renderAbilities(state);
  if (activePage === "system") return renderSystem(state);
  return renderOverview(state);
}

function renderNavItem(pageId, icon, label, activePage) {
  const active = pageId === activePage;
  return `<button class="nav-item${active ? " is-active" : ""}" type="button" data-page="${pageId}" aria-label="${label}" title="${label}"${active ? ' aria-current="page"' : ""}><span aria-hidden="true">${icon}</span><b>${label}</b></button>`;
}

function renderWindowButton(state, actionId, label, icon, className = "") {
  const actionState = state.actionStates[actionId];
  const pending = actionState?.phase === "pressed" || actionState?.phase === "pending";
  return `<button class="window-button${className}${pending ? " is-pending" : ""}" type="button" data-action="${actionId}" aria-label="${label}" title="${label}"${pending ? " disabled" : ""}>${icon}</button>`;
}

function renderToast(state) {
  const entries = Object.values(state.actionStates).filter((item) => item?.phase === "failed" || item?.phase === "unknown");
  const latest = state.presentationNotice || entries.at(-1);
  return latest ? `<div class="toast is-${escapeHtml(latest.phase)}"><strong>${escapeHtml(latest.label)}</strong>${latest.detail ? `<span>${escapeHtml(latest.detail)}</span>` : ""}</div>` : "";
}
