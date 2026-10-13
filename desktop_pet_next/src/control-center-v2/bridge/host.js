import { invoke } from "@tauri-apps/api/core";
import { emit, emitTo, listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { fetch as tauriFetch } from "@tauri-apps/plugin-http";
import { SETTINGS_COMMAND_EVENT, SETTINGS_SNAPSHOT_EVENT, createTargetedEventEmitter } from "../../control-center/event-bridge.js";
import { CONTROL_CENTER_SOURCE_KIND } from "../../control-center/data-sources.js";
import { bindInstanceStorage, getInstanceStorageItem } from "../../instance-storage.js";
import { formatError } from "./errors.js";

const DEFAULT_BACKEND_URL = "http://127.0.0.1:9999";

export function createControlCenterHost(options = {}) {
  const isTauri = options.isTauri ?? Boolean(window.__TAURI_INTERNALS__);
  return { isTauri, invoke, createSourceOptions: () => createSourceOptions({ isTauri }),
    requestSnapshot: () => emitMainEvent(SETTINGS_COMMAND_EVENT, { command: "requestSnapshot" }),
    listenSnapshots: callback => listen(SETTINGS_SNAPSHOT_EVENT, event => callback(event.payload)),
    listenFileDrops: callback => getCurrentWindow().onDragDropEvent(event => {
      if (event.payload.type === "drop") callback(event.payload.paths || []);
    }) };
}

export async function createSourceOptions({ isTauri }) {
  const params = new URLSearchParams(window.location.search);
  let petState = {};
  let availableCharacterPacks = [];

  if (isTauri) {
    try {
      const [persistedState, launchBinding] = await Promise.all([
        invoke("load_pet_state"),
        invoke("get_client_launch_binding")
      ]);
      petState = {
        ...(persistedState || {}),
        backendUrl: launchBinding?.hasBackendOverride ? launchBinding.backendUrl : persistedState?.backendUrl
      };
      const expectedInstanceId = String(launchBinding?.instanceId || "").trim();
      const actualInstanceId = String(petState.instanceId || "").trim();
      if (expectedInstanceId && expectedInstanceId !== actualInstanceId) {
        throw new Error("client_instance_binding_mismatch");
      }
      bindInstanceStorage(petState.hostId || petState.instanceId);
      try {
        availableCharacterPacks = await invoke("list_character_packs");
      } catch {
        availableCharacterPacks = [];
      }
    } catch (error) {
      throw new Error(`tauri_control_center_bootstrap_failed:${formatError(error)}`);
    }
  }

  const instanceId = String(petState.instanceId || "").trim();
  const hostId = String(petState.hostId || instanceId).trim();
  const boundBotId = String(petState.boundBotId || instanceId).trim();
  bindInstanceStorage(hostId || "local-default");

  return {
    kind: CONTROL_CENTER_SOURCE_KIND.backend,
    baseUrl: (isTauri ? petState.backendUrl : params.get("backend") || params.get("backend_url"))
      || getInstanceStorageItem("controlCenter.backendUrl", { legacyKey: "akane.controlCenter.backendUrl" })
      || DEFAULT_BACKEND_URL,
    expectedInstanceId: hostId,
    botId: boundBotId,
    fetchImpl: isTauri ? instanceBoundFetch : window.fetch.bind(window),
    sessionId: (isTauri ? petState.sessionId : params.get("session_id") || params.get("user_id"))
      || getInstanceStorageItem("controlCenter.sessionId", { legacyKey: "akane.controlCenter.sessionId" })
      || "control-center-v2",
    profileUserId: (isTauri ? petState.profileUserId : params.get("real_user_id") || params.get("profile_user_id"))
      || getInstanceStorageItem("controlCenter.profileUserId", { legacyKey: "akane.controlCenter.profileUserId" })
      || "master",
    characterPackId: (isTauri ? petState.characterPackId : params.get("character_pack_id")) || "",
    outfit: params.get("outfit") || petState.outfit || "",
    emotion: params.get("emotion") || petState.currentEmotion || "",
    petState,
    availableCharacterPacks,
    tauriBridge: isTauri ? { invoke, emit: emitMainEvent } : undefined
  };
}

async function instanceBoundFetch(input, init = {}) {
  const method = String(init?.method || "GET").trim().toUpperCase();
  const url = typeof input === "string" ? input : String(input?.url || input || "");
  const usesAdminProxy = shouldUseInstanceAdminProxy(url, method);
  if (!usesAdminProxy) return tauriFetch(input, init);
  const body = typeof init?.body === "string" ? init.body : "{}";
  const result = await invoke("backend_admin_request", { request: { url, method, body } });
  return new Response(String(result?.body || ""), {
    status: Number(result?.httpStatus || 502),
    headers: {
      "Content-Type": String(result?.contentType || "application/json"),
      "Cache-Control": "no-store"
    }
  });
}

export function shouldUseInstanceAdminProxy(rawUrl, rawMethod = "GET") {
  const method = String(rawMethod || "GET").trim().toUpperCase();
  if (method === "POST" || method === "PUT" || method === "DELETE") return true;
  if (method !== "GET") return false;
  try {
    const path = new URL(rawUrl).pathname;
    return path.startsWith("/admin/") || /^\/api\/bots\/[^/]+\/admin\//.test(path)
      || /^(?:\/api\/bots\/[^/]+)?\/control-center\/memory-settings(?:\/|$)/.test(path);
  } catch {
    return false;
  }
}

export const emitMainEvent = createTargetedEventEmitter({
  targetLabel: "main",
  eventName: SETTINGS_COMMAND_EVENT,
  emitTo,
  emit
});
