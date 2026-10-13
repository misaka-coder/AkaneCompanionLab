import { botScopedPath } from "../bot-routing.js";

const PROFILE_USER_ID = "master";
const SUMMARY_LIMIT = 24;

export function normalizeBackendUrl(url) {
  return String(url || "").trim().replace(/\/+$/, "") || "http://127.0.0.1:9999";
}

export function createWorkspaceTransport({ invoke, fetch, now = Date.now, isCurrent }) {
  const bindings = new Map();

  function assertCurrent(ticket) {
    if (!isCurrent(ticket) || ticket.signal?.aborted) throw new DOMException("Workspace scope changed", "AbortError");
  }

  async function workspaceFetch(ticket, input, init = {}) {
    assertCurrent(ticket);
    const state = ticket.state;
    const backendUrl = normalizeBackendUrl(state.backendUrl);
    const instanceId = String(state.hostId || state.instanceId || "").trim();
    if (!instanceId) throw Error("实例还没准备好");
    const key = JSON.stringify([instanceId, backendUrl]);
    let entry = bindings.get(key);
    if (!entry || (!entry.pending && now() - entry.verifiedAt >= 3000)) {
      entry = { verifiedAt: 0 };
      entry.pending = (async () => {
        const binding = await invoke("verify_backend_instance", { backendUrl });
        if (!binding?.ok || String(binding.instanceId || "") !== instanceId) {
          const actual = String(binding?.actualInstanceId || "").trim();
          throw Error(actual ? `后端实例不匹配：当前 ${instanceId}，目标 ${actual}` : `无法验证实例 ${instanceId} 的后端身份`);
        }
        entry.verifiedAt = now();
      })();
      bindings.set(key, entry);
    }
    try { await entry.pending; }
    catch (error) { if (bindings.get(key) === entry) bindings.delete(key); throw error; }
    finally { delete entry.pending; }
    assertCurrent(ticket);
    return fetch(input, { ...init, signal: ticket.signal });
  }

  async function fetchWorkspaceSummary(ticket) {
    const state = ticket.state;
    const query = new URLSearchParams({
      user_id: String(state.sessionId || ""), real_user_id: String(state.profileUserId || PROFILE_USER_ID),
      limit: String(SUMMARY_LIMIT), t: String(now())
    });
    const response = await workspaceFetch(ticket,
      `${normalizeBackendUrl(state.backendUrl)}${botScopedPath(state?.boundBotId || state.instanceId, "/desktop-pet/workspace/summary")}?${query}`,
      { method: "GET", cache: "no-store", connectTimeout: 5000 });
    const result = await response.json().catch(error => { if (response.ok) throw error; return null; });
    if (!response.ok || result?.ok === false) throw Error(extractWorkspaceError(result) || `HTTP ${response.status}`);
    if (!result || typeof result !== "object" || Array.isArray(result)) throw Error("workspace_summary_response_invalid");
    return result;
  }

  async function postWorkspaceAction(ticket, payload) {
    const state = ticket.state;
    const sessionId = String(state.sessionId || "").trim();
    if (!sessionId) throw Error("会话还没准备好");
    const response = await workspaceFetch(ticket,
      `${normalizeBackendUrl(state.backendUrl)}${botScopedPath(state?.boundBotId || state.instanceId, "/desktop-pet/workspace/action")}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, cache: "no-store",
        body: JSON.stringify({ ...payload, user_id: sessionId, session_id: sessionId,
          real_user_id: state.profileUserId || PROFILE_USER_ID, character_pack_id: String(state.characterPackId || "").trim() }),
        connectTimeout: 10000
      });
    const result = await response.json().catch(() => null);
    if (!response.ok || !result?.ok) throw Error(extractWorkspaceError(result) || `HTTP ${response.status}`);
    return result;
  }

  async function invokeWorkspaceItemAction(ticket, item, action) {
    assertCurrent(ticket);
    const state = ticket.state;
    const handle = String(item?.handle || item?.id || "").trim();
    if (!handle) throw Error("没有可定位的编号");
    const sessionId = String(state.sessionId || "").trim();
    if (!sessionId) throw Error("会话还没准备好");
    const result = await invoke("open_workspace_item", {
      handle, backendUrl: normalizeBackendUrl(state.backendUrl), botId: state.boundBotId || state.instanceId,
      itemType: workspaceRouteType(item), action: String(action || "").trim(),
      userId: sessionId, sessionId, realUserId: String(state.profileUserId || PROFILE_USER_ID),
      fileName: buildWorkspaceExportFileName(item)
    });
    if (!result?.ok) throw Error(extractWorkspaceError(result) || "workspace_file_action_failed");
    return result;
  }

  return { fetchWorkspaceSummary, postWorkspaceAction, invokeWorkspaceItemAction };
}

function workspaceRouteType(item) {
  const type = String(item?.item_type || item?.type || "").trim().toLowerCase();
  return type === "generated" || type === "output" ? "generated" : "attachments";
}

function buildWorkspaceExportFileName(item) {
  const title = String(item?.title || item?.handle || item?.id || "akane-output").trim();
  const format = String(item?.format || "").trim().replace(/^\.+/, "");
  if (!format || title.toLowerCase().endsWith(`.${format.toLowerCase()}`)) return title;
  return `${title}.${format}`;
}

export function extractWorkspaceError(payload) {
  if (!payload || typeof payload !== "object") return "";
  const detail = payload.detail;
  if (typeof detail === "string" && detail.trim()) return detail.trim();
  for (const key of ["message", "error", "reason"]) {
    const value = String(payload[key] || "").trim();
    if (value) return value;
  }
  return "";
}
