import { invoke } from "@tauri-apps/api/core";
import { emit, emitTo, listen } from "@tauri-apps/api/event";
import { fetch as tauriFetch } from "@tauri-apps/plugin-http";

import { createControlCenterActionRouter } from "../control-center/action-router.js";
import {
  buildCharacterRuntimePatchFromSettingsSnapshot,
  buildMusicRuntimePatch,
  CONTROL_CENTER_SOURCE_KIND,
  createControlCenterDataSource,
  createControlCenterRuntimeSnapshot
} from "../control-center/data-sources.js";
import { bindInstanceStorage, getInstanceStorageItem } from "../instance-storage.js";
import {
  createControlCenterViewModel,
  isObservedActionConfirmation
} from "./view-model.js";

const DEFAULT_BACKEND_URL = "http://127.0.0.1:9999";
const SETTINGS_COMMAND_EVENT = "akane-next-settings-command";
const SETTINGS_SNAPSHOT_EVENT = "akane-next-settings-snapshot";
const OBSERVED_ACTION_IDS = new Set([
  "chat.new",
  "chat.stop",
  "music.pause",
  "character.selectPack",
  "character.setOutfit",
  "character.previewEmotion",
  "character.refresh"
]);
const ACTION_CONFIRM_TIMEOUT_MS = 1800;
const CHARACTER_SWITCH_CONFIRM_TIMEOUT_MS = 4000;

export function createControlCenterBridge(options = {}) {
  const isTauri = options.isTauri ?? Boolean(window.__TAURI_INTERNALS__);
  const listeners = new Set();
  let source = null;
  let actionRouter = null;
  let rawSnapshot = null;
  let runtimeSnapshot = null;
  let disposeRuntimeListener = null;
  let refreshPromise = null;
  let liveSnapshotStatus = isTauri ? "connecting" : "not-applicable";
  let liveSnapshotError = "";

  function publish() {
    if (!rawSnapshot) return;
    const viewModel = createControlCenterViewModel(withBridgeStatus(
      withLiveRuntime(rawSnapshot, runtimeSnapshot),
      liveSnapshotStatus,
      liveSnapshotError
    ), runtimeSnapshot);
    for (const listener of listeners) listener(viewModel);
  }

  async function start() {
    const sourceOptions = await createSourceOptions({ isTauri });
    source = createControlCenterDataSource(sourceOptions);
    actionRouter = createControlCenterActionRouter({ dataSource: source });

    if (isTauri) {
      try {
        disposeRuntimeListener = await listen(SETTINGS_SNAPSHOT_EVENT, (event) => {
          runtimeSnapshot = event.payload && typeof event.payload === "object" ? event.payload : null;
          liveSnapshotStatus = runtimeSnapshot ? "connected" : "connecting";
          liveSnapshotError = "";
          publish();
        });
        await emitMainEvent({ command: "requestSnapshot" });
      } catch (error) {
        disposeRuntimeListener?.();
        disposeRuntimeListener = null;
        liveSnapshotStatus = "unavailable";
        liveSnapshotError = formatError(error);
      }
    }

    return refresh();
  }

  async function refresh() {
    if (refreshPromise) return refreshPromise;
    refreshPromise = (async () => {
      if (!source || typeof source.readSnapshot !== "function") {
        throw new Error("control_center_snapshot_source_unavailable");
      }
      const next = await source.readSnapshot();
      if (!next) {
        const reason = source.getFallbackReason?.() || source.fallbackReason || "snapshot_unavailable";
        throw new Error(String(reason));
      }
      rawSnapshot = createControlCenterRuntimeSnapshot(next);
      publish();
      return createControlCenterViewModel(withBridgeStatus(
        withLiveRuntime(rawSnapshot, runtimeSnapshot),
        liveSnapshotStatus,
        liveSnapshotError
      ), runtimeSnapshot);
    })();
    try {
      return await refreshPromise;
    } finally {
      refreshPromise = null;
    }
  }

  async function runAction(actionId, payload = {}) {
    if (!actionRouter) {
      return { ok: false, status: "not-available", actionId, reason: "control_center_bridge_not_started" };
    }
    const beforeRuntimeSnapshot = runtimeSnapshot;
    let result = await actionRouter.run(actionId, payload, { source: "control-center-v2" });
    if (result.ok && OBSERVED_ACTION_IDS.has(actionId)) {
      const confirmed = await waitForRuntimeConfirmation(actionId, payload, beforeRuntimeSnapshot, () => runtimeSnapshot);
      result = confirmed
        ? { ...result, status: "completed", observed: true }
        : {
            ...result,
            ok: false,
            status: "execution_unknown",
            reason: "runtime_state_not_confirmed",
            observed: false,
            refresh: false
          };
    }
    if (result.refresh) {
      if (isTauri) {
        try {
          await emitMainEvent({ command: "requestSnapshot" });
        } catch {
          // Backend refresh below still runs; event delivery is an optional live-state accelerator.
        }
      }
      try {
        await refresh();
      } catch {
        // Preserve the last trustworthy snapshot; the action result remains authoritative.
      }
    }
    return result;
  }

  function subscribe(listener) {
    listeners.add(listener);
    if (rawSnapshot) publish();
    return () => listeners.delete(listener);
  }

  function stop() {
    disposeRuntimeListener?.();
    disposeRuntimeListener = null;
    listeners.clear();
  }

  return { start, refresh, runAction, subscribe, stop };
}

async function waitForRuntimeConfirmation(actionId, payload, beforeSnapshot, readCurrentSnapshot) {
  const startedAt = Date.now();
  const timeoutMs = actionId === "character.selectPack" ? CHARACTER_SWITCH_CONFIRM_TIMEOUT_MS : ACTION_CONFIRM_TIMEOUT_MS;
  while (Date.now() - startedAt < timeoutMs) {
    const current = readCurrentSnapshot();
    if (current !== beforeSnapshot && isObservedActionConfirmation(actionId, beforeSnapshot, current, payload)) {
      return true;
    }
    await new Promise((resolve) => window.setTimeout(resolve, 60));
  }
  return false;
}

async function createSourceOptions({ isTauri }) {
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
  if (hostId) bindInstanceStorage(hostId);

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

function formatError(error) {
  return error instanceof Error ? error.message : String(error || "unknown");
}

async function instanceBoundFetch(input, init = {}) {
  const method = String(init?.method || "GET").trim().toUpperCase();
  if (method !== "POST") return tauriFetch(input, init);
  const url = typeof input === "string" ? input : String(input?.url || input || "");
  const body = typeof init?.body === "string" ? init.body : "{}";
  const result = await invoke("backend_admin_request", { request: { url, body } });
  return new Response(String(result?.body || ""), {
    status: Number(result?.httpStatus || 502),
    headers: {
      "Content-Type": String(result?.contentType || "application/json"),
      "Cache-Control": "no-store"
    }
  });
}

async function emitMainEvent(payload) {
  try {
    await emitTo("main", SETTINGS_COMMAND_EVENT, payload);
  } catch {
    await emit(SETTINGS_COMMAND_EVENT, payload);
  }
}

function withLiveRuntime(rawSnapshot, runtimeSnapshot) {
  if (!runtimeSnapshot) return rawSnapshot;
  const musicRuntime = runtimeSnapshot.music ? buildMusicRuntimePatch({
    musicSnapshot: runtimeSnapshot.music,
    petState: runtimeSnapshot.state || {}
  }) : null;
  const characterPatch = buildCharacterRuntimePatchFromSettingsSnapshot(runtimeSnapshot);
  return {
    ...rawSnapshot,
    ...(musicRuntime ? { musicRuntime } : {}),
    ...(characterPatch ? { characterRuntime: { ...rawSnapshot.characterRuntime, ...characterPatch } } : {})
  };
}

function withBridgeStatus(rawSnapshot, status, error) {
  return {
    ...rawSnapshot,
    controlCenterV2: {
      liveSnapshotStatus: status,
      ...(error ? { liveSnapshotError: error } : {})
    }
  };
}
