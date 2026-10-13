import { invoke } from "@tauri-apps/api/core";
import { emit, emitTo, listen } from "@tauri-apps/api/event";
import { fetch as tauriFetch } from "@tauri-apps/plugin-http";
import { createDesktopConversationFetch } from "../desktop-conversation-transport.js";
import { SETTINGS_COMMAND_EVENT, SETTINGS_SNAPSHOT_EVENT } from "../control-center/event-bridge.js";

const WORKSPACE_REFRESH_EVENT = "akane-next-workspace-refresh";

export function createWorkspaceHost() {
  return {
    document, window, invoke,
    fetch: createDesktopConversationFetch({ fetch: tauriFetch, invoke }),
    now: Date.now,
    setTimeout: (callback, delay) => window.setTimeout(callback, delay),
    clearTimeout: timer => window.clearTimeout(timer),
    confirm: message => window.confirm(message),
    copy: value => navigator.clipboard.writeText(value),
    loadState,
    sendCommand,
    listenSnapshots: callback => listen(SETTINGS_SNAPSHOT_EVENT, event => callback(event.payload)),
    listenRefresh: callback => listen(WORKSPACE_REFRESH_EVENT, event => callback(event.payload)),
    async notifyRefresh() {
      try { await emit(WORKSPACE_REFRESH_EVENT, { t: Date.now(), source: "workspace" }); }
      catch { /* Main window notifications are optional. */ }
    },
    async close() {
      try { await invoke("close_window"); }
      catch { window.close(); }
    }
  };
}

async function loadState() {
  const [persistedState, launchBinding] = await Promise.all([
    invoke("load_pet_state"), invoke("get_client_launch_binding")
  ]);
  const state = { ...persistedState,
    backendUrl: launchBinding?.hasBackendOverride ? launchBinding.backendUrl : persistedState?.backendUrl };
  const expected = String(launchBinding?.instanceId || "").trim();
  if (!expected || expected !== String(state.instanceId || "").trim()) throw Error("client_instance_binding_mismatch");
  return state;
}

async function sendCommand(command, value = null, { isCurrent = () => true } = {}) {
  const payload = { command, value };
  if (!isCurrent()) throw new DOMException("Workspace scope changed", "AbortError");
  try { await emitTo("main", SETTINGS_COMMAND_EVENT, payload); }
  catch {
    if (!isCurrent()) throw new DOMException("Workspace scope changed", "AbortError");
    await emit(SETTINGS_COMMAND_EVENT, payload);
  }
}
