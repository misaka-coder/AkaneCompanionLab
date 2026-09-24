import { escapeHtml } from "../dom.js";

export function renderActionButton(state, viewModel, actionId, icon, label, tone = "soft", options = {}) {
  const action = viewModel?.actions?.[actionId] || { available: false, reason: "当前不可用" };
  const actionState = state.actionStates[actionId];
  const pending = actionState?.phase === "pressed" || actionState?.phase === "pending";
  const displayLabel = pending ? "处理中…" : actionState?.phase === "confirmed" ? actionState.label : label;
  const title = action.available ? actionState?.detail || label : action.reason;
  const value = String(options.value ?? "").trim();
  const valueAttribute = value ? ` data-action-value="${escapeHtml(value)}"` : "";
  const extraClass = options.className ? ` ${escapeHtml(options.className)}` : "";
  return `<button class="action-button is-${escapeHtml(tone)}${pending ? " is-pending" : ""}${extraClass}" type="button" data-action="${escapeHtml(actionId)}"${valueAttribute} title="${escapeHtml(title)}" ${action.available && !pending ? "" : "disabled"}><span aria-hidden="true">${escapeHtml(icon)}</span><b>${escapeHtml(displayLabel)}</b></button>`;
}

export function actionPhase(state, actionId) {
  return state.actionStates[actionId]?.phase || "idle";
}
