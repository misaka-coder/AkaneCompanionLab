import { invoke } from "@tauri-apps/api/core";
import { emit, emitTo, listen } from "@tauri-apps/api/event";
import { fetch as tauriFetch } from "@tauri-apps/plugin-http";

import { createControlCenterActionRouter } from "../control-center/action-router.js";
import {
  SETTINGS_COMMAND_EVENT,
  SETTINGS_SNAPSHOT_EVENT,
  createTargetedEventEmitter
} from "../control-center/event-bridge.js";
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
  isObservedActionConfirmation,
  observedActionOutcome
} from "./view-model.js";
import { modelServiceOperation, runModelServiceBridgeAction } from "./model-service.js";
import { chatSessionId, createTrailingAsyncRefresh, mergeChatSessions } from "./chat-history.js";

const DEFAULT_BACKEND_URL = "http://127.0.0.1:9999";
const OBSERVED_ACTION_IDS = new Set([
  "chat.new",
  "chat.send",
  "chat.stop",
  "music.previous",
  "music.next",
  "music.togglePlayback",
  "music.stop",
  "voice.test",
  "voice.stop",
  "voice.previewPlay",
  "voice.setTtsEnabled",
  "voice.setAsrEnabled",
  "voice.setVolume",
  "voice.setSpeed",
  "voice.setWakeWord",
  "voice.setWakeSensitivity",
  "perception.runDiagnostics",
  "advanced.setHitTestEnabled",
  "advanced.setHitboxOverlay",
  "settings.selectBot",
  "character.selectPack",
  "character.setOutfit",
  "character.previewEmotion",
  "character.refresh"
]);
const ACTION_CONFIRM_TIMEOUT_MS = 1800;
const MUSIC_PLAYBACK_CONFIRM_TIMEOUT_MS = 4000;
const CHARACTER_SWITCH_CONFIRM_TIMEOUT_MS = 4000;
const VOICE_PLAYBACK_CONFIRM_TIMEOUT_MS = 12000;
const MEDIA_CONTROL_ACTION_IDS = new Set([
  "music.previous",
  "music.next",
  "music.togglePlayback",
  "music.stop"
]);
let actionOperationSequence = 0;

export function createControlCenterBridge(options = {}) {
  const isTauri = options.isTauri ?? Boolean(window.__TAURI_INTERNALS__);
  const listeners = new Set();
  let source = null;
  let actionRouter = null;
  let rawSnapshot = null;
  let runtimeSnapshot = null;
  let disposeRuntimeListener = null;
  let refreshPromise = null;
  let chatHistoryLoadPromise = null;
  let chatRefreshTimer = 0;
  let lastChatRuntimeSignature = "";
  let liveSnapshotStatus = isTauri ? "connecting" : "not-applicable";
  let liveSnapshotError = "";
  const refreshChatSession = createTrailingAsyncRefresh(async () => {
    const requestedSessionId = liveChatSessionId();
    const chatSession = await readChatSessionForRuntime();
    if (!rawSnapshot) return null;
    if (!chatSession) {
      rawSnapshot = {
        ...rawSnapshot,
        chatRuntime: {
          status: "failed",
          reason: source?.getChatSessionError?.() || "chat-session-unavailable"
        }
      };
      publish();
      return null;
    }
    const latestSessionId = liveChatSessionId();
    if (requestedSessionId && latestSessionId && requestedSessionId !== latestSessionId) {
      return null;
    }
    if (requestedSessionId && chatSessionId(chatSession) !== requestedSessionId) {
      return null;
    }
    rawSnapshot = {
      ...rawSnapshot,
      chatSession: mergeChatSessions(rawSnapshot.chatSession, chatSession),
      chatRuntime: { status: "available", reason: "" }
    };
    publish();
    return chatSession;
  });

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
          scheduleChatRefresh();
        });
        await emitMainEvent(SETTINGS_COMMAND_EVENT, { command: "requestSnapshot" });
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
      const [next, modelRuntime, botCatalog] = await Promise.all([
        source.readSnapshot(),
        typeof source.readModelService === "function"
          ? source.readModelService().catch(() => null)
          : Promise.resolve(null),
        typeof source.readBotCatalog === "function"
          ? source.readBotCatalog().catch(() => null)
          : Promise.resolve(null)
      ]);
      if (!next) {
        const reason = source.getFallbackReason?.() || source.fallbackReason || "snapshot_unavailable";
        throw new Error(String(reason));
      }
      rawSnapshot = {
        ...createControlCenterRuntimeSnapshot(next),
        modelRuntime: modelRuntime || rawSnapshot?.modelRuntime || {},
        botCatalog: botCatalog || rawSnapshot?.botCatalog || {},
        ...(rawSnapshot?.chatSession ? { chatSession: rawSnapshot.chatSession } : {}),
        ...(rawSnapshot?.chatRuntime ? { chatRuntime: rawSnapshot.chatRuntime } : {})
      };
      publish();
      await refreshChatSession();
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
    const actionPayload = OBSERVED_ACTION_IDS.has(actionId)
      ? { ...payload, operationId: createActionOperationId(actionId) }
      : payload;
    let result = modelServiceOperation(actionId)
      ? await runModelServiceBridgeAction(source, actionId, actionPayload)
      : await actionRouter.run(actionId, actionPayload, { source: "control-center-v2" });
    if (result.ok && OBSERVED_ACTION_IDS.has(actionId)) {
      const observedSnapshot = await waitForRuntimeConfirmation(actionId, actionPayload, beforeRuntimeSnapshot, () => runtimeSnapshot);
      const actionOutcome = observedSnapshot ? observedActionOutcome(actionId, actionPayload, observedSnapshot) : null;
      result = observedSnapshot
        ? actionOutcome
          ? { ...result, ...actionOutcome, observed: true, refresh: actionId === "settings.selectBot" }
          : { ...result, status: "completed", observed: true, refresh: actionId === "settings.selectBot" }
        : {
            ...result,
            ok: false,
            status: "execution_unknown",
            reason: "runtime_state_not_confirmed",
            observed: false,
            refresh: false
          };
    }
    if (result.ok && actionId === "settings.selectBot") {
      const sourceOptions = await createSourceOptions({ isTauri });
      source = createControlCenterDataSource(sourceOptions);
      actionRouter = createControlCenterActionRouter({ dataSource: source });
    }
    if (result.refresh) {
      if (isTauri) {
        try {
          await emitMainEvent(SETTINGS_COMMAND_EVENT, { command: "requestSnapshot" });
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
    window.clearTimeout(chatRefreshTimer);
    chatRefreshTimer = 0;
    disposeRuntimeListener?.();
    disposeRuntimeListener = null;
    listeners.clear();
  }

  return { start, refresh, runAction, subscribe, stop, loadOlderChatMessages };

  function scheduleChatRefresh() {
    const state = runtimeSnapshot?.state && typeof runtimeSnapshot.state === "object" ? runtimeSnapshot.state : {};
    const active = runtimeSnapshot?.active && typeof runtimeSnapshot.active === "object" ? runtimeSnapshot.active : {};
    const commandResult = runtimeSnapshot?.settingsCommandResult && typeof runtimeSnapshot.settingsCommandResult === "object"
      ? runtimeSnapshot.settingsCommandResult
      : {};
    const signature = [
      state.sessionId || "",
      state.characterPackId || "",
      Boolean(active.sending),
      Boolean(active.replyDisplayActive),
      runtimeSnapshot?.runtimeMode || "",
      commandResult.command || "",
      commandResult.at || ""
    ].join(":");
    if (!signature || signature === lastChatRuntimeSignature) return;
    lastChatRuntimeSignature = signature;
    window.clearTimeout(chatRefreshTimer);
    chatRefreshTimer = window.setTimeout(() => {
      chatRefreshTimer = 0;
      void refreshChatSession();
    }, active.sending ? 260 : 80);
  }

  async function readChatSessionForRuntime() {
    if (!source || typeof source.readChatSession !== "function") return null;
    const liveState = runtimeSnapshot?.state && typeof runtimeSnapshot.state === "object" ? runtimeSnapshot.state : {};
    return source.readChatSession({
      sessionId: liveState.sessionId,
      characterPackId: liveState.characterPackId
    });
  }

  function liveChatSessionId() {
    return String(runtimeSnapshot?.state?.sessionId || "").trim();
  }

  async function loadOlderChatMessages(options = {}) {
    if (chatHistoryLoadPromise) return chatHistoryLoadPromise;
    if (!source || typeof source.readChatHistoryPage !== "function" || !rawSnapshot?.chatSession) {
      return { ok: false, status: "not-available", reason: "chat_history_paging_unavailable" };
    }
    const currentSession = rawSnapshot.chatSession;
    const sessionId = chatSessionId(currentSession);
    const characterPackId = String(
      options.characterPackId || runtimeSnapshot?.state?.characterPackId || currentSession?.session?.character_pack_id || ""
    ).trim();
    const beforeSeq = Number(options.beforeSeq || currentSession?.message_page?.next_before_seq);
    if (!sessionId || !Number.isFinite(beforeSeq) || beforeSeq <= 0) {
      return { ok: false, status: "no-more-history", reason: "chat_history_cursor_unavailable" };
    }

    chatHistoryLoadPromise = (async () => {
      const page = await source.readChatHistoryPage({
        sessionId,
        characterPackId,
        beforeSeq,
        limit: options.limit || 60
      });
      if (!page) return { ok: false, status: "failed", reason: "chat_history_request_failed" };
      if (!rawSnapshot?.chatSession || chatSessionId(rawSnapshot.chatSession) !== sessionId) {
        return { ok: false, status: "stale", reason: "chat_session_changed" };
      }
      const previousCount = Array.isArray(rawSnapshot.chatSession.messages) ? rawSnapshot.chatSession.messages.length : 0;
      rawSnapshot = {
        ...rawSnapshot,
        chatSession: mergeChatSessions(rawSnapshot.chatSession, {
          session: rawSnapshot.chatSession.session,
          messages: page.messages,
          message_page: page.message_page
        }, { preferIncomingPage: true })
      };
      const nextCount = Array.isArray(rawSnapshot.chatSession.messages) ? rawSnapshot.chatSession.messages.length : 0;
      publish();
      return {
        ok: true,
        status: "loaded",
        count: Math.max(0, nextCount - previousCount),
        hasMore: Boolean(rawSnapshot.chatSession.message_page?.has_more)
      };
    })();
    try {
      return await chatHistoryLoadPromise;
    } finally {
      chatHistoryLoadPromise = null;
    }
  }
}

async function waitForRuntimeConfirmation(actionId, payload, beforeSnapshot, readCurrentSnapshot) {
  const startedAt = Date.now();
  const timeoutMs = voicePlaybackAction(actionId)
    ? VOICE_PLAYBACK_CONFIRM_TIMEOUT_MS
    : MEDIA_CONTROL_ACTION_IDS.has(actionId)
      ? MUSIC_PLAYBACK_CONFIRM_TIMEOUT_MS
    : actionId === "character.selectPack"
      ? CHARACTER_SWITCH_CONFIRM_TIMEOUT_MS
      : ACTION_CONFIRM_TIMEOUT_MS;
  while (Date.now() - startedAt < timeoutMs) {
    const current = readCurrentSnapshot();
    if (current !== beforeSnapshot && isObservedActionConfirmation(actionId, beforeSnapshot, current, payload)) {
      return current;
    }
    await new Promise((resolve) => window.setTimeout(resolve, 60));
  }
  return null;
}

function createActionOperationId(actionId) {
  actionOperationSequence += 1;
  const action = String(actionId || "action").replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "").toLowerCase();
  return `${action || "action"}-${Date.now().toString(36)}-${actionOperationSequence.toString(36)}`;
}

function voicePlaybackAction(actionId) {
  return actionId === "voice.test" || actionId === "voice.previewPlay";
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

const emitMainEvent = createTargetedEventEmitter({
  targetLabel: "main",
  eventName: SETTINGS_COMMAND_EVENT,
  emitTo,
  emit
});

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
