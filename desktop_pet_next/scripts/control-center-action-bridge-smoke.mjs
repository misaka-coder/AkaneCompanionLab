import assert from "node:assert/strict";

import {
  CONTROL_CENTER_ACTIONS,
  CONTROL_CENTER_BRIDGED_ACTION_IDS,
  createControlCenterActionRouter,
  isControlCenterBridgedAction
} from "../src/control-center/action-router.js";
import {
  CONTROL_CENTER_ACTION_SURFACE_STATUS,
  CONTROL_CENTER_CLIENT_HANDLED_ACTION_IDS,
  getUncataloguedBridgedActionIds,
  listControlCenterActionSurfaces
} from "../src/control-center/action-surface-contract.js";
import {
  createControlCenterActionPayloadFromDataset,
  secondsFromIntervalLabel
} from "../src/control-center/action-helpers.js";
import { createControlCenterSnapshot } from "../src/control-center/data-adapter.js";
import * as mockData from "../src/control-center/mock-data.js";
import {
  buildMusicRuntimePatch,
  createBackendControlCenterSource,
  createMockControlCenterSource,
  createTauriControlCenterSource
} from "../src/control-center/data-sources.js";

const SETTINGS_COMMAND_EVENT = "akane-next-settings-command";

const emitLog = [];
const invokeLog = [];
const dataSource = createTauriControlCenterSource({
  tauriBridge: {
    emit: async (event, payload) => {
      emitLog.push({ event, payload });
    },
    invoke: async (command, payload) => {
      invokeLog.push({ command, payload });
    }
  }
});
const afterActionLog = [];
const router = createControlCenterActionRouter({
  dataSource,
  onAfterAction: (result) => {
    afterActionLog.push(result);
  }
});

const bridgedActionCases = [
  { id: CONTROL_CENTER_ACTIONS.chatNew, payload: {}, context: { source: "smoke" }, emit: "newSession" },
  { id: CONTROL_CENTER_ACTIONS.chatStop, payload: {}, context: {}, emit: "stopReply" },
  { id: CONTROL_CENTER_ACTIONS.workspaceOpen, payload: {}, context: {}, invoke: "open_workspace_window" },
  { id: CONTROL_CENTER_ACTIONS.voiceTest, payload: {}, context: {}, emit: "testTts" },
  { id: CONTROL_CENTER_ACTIONS.voiceStop, payload: {}, context: {}, emit: "stopTts" },
  {
    id: CONTROL_CENTER_ACTIONS.voiceSetTtsEnabled,
    payload: { value: false, settingId: "ttsEnabled" },
    context: {},
    emit: "setVoiceEnabled",
    value: false
  },
  {
    id: CONTROL_CENTER_ACTIONS.voiceSetAsrEnabled,
    payload: { value: true, settingId: "asrEnabled" },
    context: {},
    emit: "setVoiceInputEnabled",
    value: true
  },
  {
    id: CONTROL_CENTER_ACTIONS.voiceSetVolume,
    payload: { value: 0.7, percent: 70 },
    context: {},
    emit: "setVoiceVolume",
    value: 0.7
  },
  {
    id: CONTROL_CENTER_ACTIONS.voicePreviewPlay,
    payload: { text: "你好，Akane。", value: "你好，Akane。" },
    context: { source: "lab" },
    emit: "previewTts",
    value: "你好，Akane。"
  },
  {
    id: CONTROL_CENTER_ACTIONS.voiceSetSpeed,
    payload: { value: "1.00x", field: "speed" },
    context: { source: "lab" },
    emit: "setVoiceSpeed",
    value: "1.00x"
  },
  {
    id: CONTROL_CENTER_ACTIONS.voiceSetWakeWord,
    payload: { value: "Akane", field: "wakeWord" },
    context: { source: "lab" },
    emit: "setWakeWord",
    value: "Akane"
  },
  {
    id: CONTROL_CENTER_ACTIONS.voiceSetWakeSensitivity,
    payload: { value: "中等", field: "wakeSensitivity" },
    context: { source: "lab" },
    emit: "setWakeSensitivity",
    value: "中等"
  },
  { id: CONTROL_CENTER_ACTIONS.characterRefresh, payload: {}, context: {}, emit: "reloadResources" },
  {
    id: CONTROL_CENTER_ACTIONS.characterPreviewEmotion,
    payload: { value: "happy", emotionId: "happy" },
    context: {},
    emit: "previewEmotion",
    value: "happy"
  },
  {
    id: CONTROL_CENTER_ACTIONS.characterSetOutfit,
    payload: { value: "summer", outfitId: "summer" },
    context: { source: "lab" },
    emit: "setOutfit",
    value: "summer"
  },
  {
    id: CONTROL_CENTER_ACTIONS.characterSelectPack,
    payload: { value: "akane_default", packId: "akane_default" },
    context: { source: "lab" },
    emit: "setCharacterPack",
    value: "akane_default"
  },
  {
    id: CONTROL_CENTER_ACTIONS.characterOpenPackFolder,
    payload: {},
    context: {},
    invoke: "open_character_packs_folder"
  },
  {
    id: CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileAssignToCurrentCharacter,
    payload: {
      providerId: "provider.tts.gpt_sovits.local",
      voiceProfileId: "dania",
      characterPackId: "akane_sample"
    },
    context: { source: "lab" },
    invoke: "set_character_voice_profile",
    status: "assigned"
  },
  {
    id: CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileClearCurrentCharacter,
    payload: {
      providerId: "provider.tts.gpt_sovits.local",
      characterPackId: "akane_sample"
    },
    context: { source: "lab" },
    invoke: "clear_character_voice_profile",
    status: "cleared"
  },
  {
    id: CONTROL_CENTER_ACTIONS.perceptionDesktopContextSetEnabled,
    payload: { value: true, featureId: "activeWindow" },
    context: { source: "lab" },
    emit: "setDesktopContextEnabled",
    value: true
  },
  {
    id: CONTROL_CENTER_ACTIONS.perceptionClipboardContextSetEnabled,
    payload: { value: false, featureId: "clipboard" },
    context: { source: "lab" },
    emit: "setClipboardContextEnabled",
    value: false
  },
  {
    id: CONTROL_CENTER_ACTIONS.perceptionScreenVisionSetEnabled,
    payload: { value: true, featureId: "screen" },
    context: { source: "lab" },
    emit: "setScreenVisionEnabled",
    value: true
  },
  {
    id: CONTROL_CENTER_ACTIONS.perceptionScreenVisionSetIntervalSec,
    payload: { value: 25, label: "25 秒" },
    context: { source: "lab" },
    emit: "setScreenVisionIntervalSec",
    value: 25
  },
  {
    id: CONTROL_CENTER_ACTIONS.perceptionScreenVisionSetFrameCount,
    payload: { value: 4, frames: 4 },
    context: { source: "lab" },
    emit: "setScreenVisionFrameCount",
    value: 4
  },
  {
    id: CONTROL_CENTER_ACTIONS.perceptionScreenVisionClear,
    payload: {},
    context: { source: "lab" },
    emit: "clearScreenVision"
  },
  {
    id: CONTROL_CENTER_ACTIONS.perceptionProactiveWakeSetEnabled,
    payload: { value: true, featureId: "proactive" },
    context: { source: "lab" },
    emit: "setProactiveWakeEnabled",
    value: true
  },
  {
    id: CONTROL_CENTER_ACTIONS.perceptionProactiveWakeSetIntervalSec,
    payload: { value: 180, label: "3 分钟" },
    context: { source: "lab" },
    emit: "setProactiveWakeIntervalSec",
    value: 180
  },
  {
    id: CONTROL_CENTER_ACTIONS.perceptionRunDiagnostics,
    payload: { page: "perception" },
    context: { source: "lab" },
    emit: "requestSnapshot"
  },
  { id: CONTROL_CENTER_ACTIONS.advancedProbeClickThrough, payload: {}, context: {}, emit: "probeClickThrough" },
  { id: CONTROL_CENTER_ACTIONS.advancedResetWindow, payload: {}, context: {}, emit: "resetWindow" },
  {
    id: CONTROL_CENTER_ACTIONS.advancedToggleWebgl,
    payload: { value: false, settingId: "webgl" },
    context: {},
    emit: "toggleWebgl",
    value: false
  },
  {
    id: CONTROL_CENTER_ACTIONS.advancedSetHitTestEnabled,
    payload: { value: true, settingId: "hitTest" },
    context: {},
    emit: "setHitTestEnabled",
    value: true
  },
  {
    id: CONTROL_CENTER_ACTIONS.advancedSetHitboxOverlay,
    payload: { value: false, settingId: "hitbox" },
    context: {},
    emit: "setHitboxOverlay",
    value: false
  },
  { id: CONTROL_CENTER_ACTIONS.musicPrevious, payload: {}, context: {}, emit: "previousMusic" },
  { id: CONTROL_CENTER_ACTIONS.musicNext, payload: {}, context: {}, emit: "nextMusic" },
  { id: CONTROL_CENTER_ACTIONS.musicPause, payload: {}, context: {}, emit: "toggleMusic" },
  { id: CONTROL_CENTER_ACTIONS.musicStop, payload: {}, context: {}, emit: "stopMusic" },
  { id: CONTROL_CENTER_ACTIONS.musicClear, payload: {}, context: {}, emit: "clearMusicQueue" },
  {
    id: CONTROL_CENTER_ACTIONS.musicSeek,
    payload: { value: 42, seconds: 42, percent: 25 },
    context: { source: "lab" },
    emit: "seekMusic",
    value: 42
  },
  {
    id: CONTROL_CENTER_ACTIONS.musicSelectQueueItem,
    payload: { value: "track_starsWithYou", trackId: "track_starsWithYou", index: 3 },
    context: { source: "lab" },
    emit: "playMusicTrack",
    value: "track_starsWithYou"
  },
  {
    id: CONTROL_CENTER_ACTIONS.musicSetPlayMode,
    payload: { value: "列表循环", field: "playMode" },
    context: { source: "lab" },
    emit: "setMusicPlayMode",
    value: "列表循环"
  },
  {
    id: CONTROL_CENTER_ACTIONS.musicSetVolumeNormalization,
    payload: { value: false, field: "volumeNormalization" },
    context: { source: "lab" },
    emit: "setMusicVolumeNormalization",
    value: false
  },
  {
    id: CONTROL_CENTER_ACTIONS.musicPlayWorkspaceRecommendation,
    payload: { itemType: "generated", handle: "audio_001", title: "Starry Days", path: "C:/secret/song.mp3", cachedPath: "C:/secret/cache.mp3" },
    context: { source: "lab" },
    emit: "playWorkspaceAudio"
  }
];

for (const testCase of bridgedActionCases) {
  assert.equal(isControlCenterBridgedAction(testCase.id), true, `${testCase.id} should be bridged`);
  const result = await router.run(testCase.id, testCase.payload, testCase.context);
  assert.deepEqual(
    { ok: result.ok, status: result.status, actionId: result.actionId, refresh: result.refresh },
    { ok: true, status: testCase.status || "executed", actionId: testCase.id, refresh: true },
    `${testCase.id} should execute`
  );

  if (testCase.emit) {
    const entry = emitLog.find((item) => item.payload.command === testCase.emit);
    assert.ok(entry, `${testCase.id} should emit ${testCase.emit}`);
    assert.equal(entry.event, SETTINGS_COMMAND_EVENT);
    assert.equal(entry.payload.source, testCase.context.source || "control-center");
    if ("value" in testCase) {
      assert.equal(entry.payload.value, testCase.value, `${testCase.id} should preserve payload value`);
    }
  }

  if (testCase.invoke) {
    const entry = invokeLog.find((item) => item.command === testCase.invoke);
    assert.ok(entry, `${testCase.id} should invoke ${testCase.invoke}`);
    if (testCase.id === CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileAssignToCurrentCharacter) {
      assert.deepEqual(
        entry.payload,
        {
          request: {
            packId: "akane_sample",
            provider: "provider.tts.gpt_sovits.local",
            profileId: "dania",
            notes: "控制中心声线 dania"
          }
        },
        "character voice assignment should invoke with safe pack/profile fields"
      );
      const refreshEntry = emitLog.find((item) => item.payload.command === "refreshCharacterPacks");
      assert.ok(refreshEntry, "character voice assignment should refresh runtime character packs");
      assert.deepEqual(refreshEntry.payload.value, { selectPackId: "akane_sample", apply: true });
    }
    if (testCase.id === CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileClearCurrentCharacter) {
      assert.deepEqual(
        entry.payload,
        { packId: "akane_sample" },
        "character voice clear should invoke with only the safe pack id"
      );
      const refreshEntry = emitLog.find((item) => item.payload.command === "refreshCharacterPacks");
      assert.ok(refreshEntry, "character voice clear should refresh runtime character packs");
      assert.deepEqual(refreshEntry.payload.value, { selectPackId: "akane_sample", apply: true });
    }
  }
}

{
  const entry = emitLog.find((item) => item.payload.command === "playWorkspaceAudio");
  assert.ok(entry, "music.playWorkspaceRecommendation should emit playWorkspaceAudio");
  assert.deepEqual(
    entry.payload.value,
    { itemType: "generated", handle: "audio_001", title: "Starry Days" },
    "workspace recommendation playback should only emit safe workspace fields"
  );
  assert.equal("path" in entry.payload.value, false, "workspace recommendation payload must not include path");
  assert.equal("cachedPath" in entry.payload.value, false, "workspace recommendation payload must not include cachedPath");
}

// ---------- action surface contract ----------

const bridgedSurfaceIds = new Set(
  listControlCenterActionSurfaces(CONTROL_CENTER_ACTION_SURFACE_STATUS.bridged).map((surface) => surface.actionId)
);
const deferredSurfaces = listControlCenterActionSurfaces(CONTROL_CENTER_ACTION_SURFACE_STATUS.deferred);
assert.deepEqual(getUncataloguedBridgedActionIds(), [], "every bridged action id should be listed in the surface contract");
for (const actionId of CONTROL_CENTER_BRIDGED_ACTION_IDS) {
  assert.equal(bridgedSurfaceIds.has(actionId), true, `${actionId} should appear in the bridged surface contract`);
}
for (const surface of deferredSurfaces) {
  assert.equal(isControlCenterBridgedAction(surface.actionId), false, `${surface.actionId} should remain deferred`);
  assert.equal(typeof surface.reason, "string", `${surface.actionId} should document a deferred reason`);
  assert.notEqual(surface.reason.trim(), "", `${surface.actionId} should document a non-empty deferred reason`);
}

const directNotImplementedCases = [
  CONTROL_CENTER_ACTIONS.characterImportZip,
  CONTROL_CENTER_ACTIONS.characterApply,
  CONTROL_CENTER_ACTIONS.characterRestoreDefaults,
  "perception.clipboard.clear"
];

for (const actionId of directNotImplementedCases) {
  const result = await dataSource.runAction(actionId);
  assert.deepEqual(
    { ok: result.ok, status: result.status, actionId: result.actionId, refresh: result.refresh },
    { ok: false, status: "not-implemented", actionId, refresh: false },
    `${actionId} should remain not implemented at data-source boundary`
  );
}

for (const actionId of [...directNotImplementedCases]) {
  const result = await router.run(actionId);
  assert.equal(result.status, "not-implemented", `${actionId} should route to not-implemented`);
  assert.equal(result.refresh, false, `${actionId} should normalize to refresh:false`);
}

const labelCases = [
  ["30 秒", 30],
  ["1 分钟", 60],
  ["3 分钟", 180],
  ["5 分钟", 300],
  ["10 分钟", 600],
  ["", 0],
  ["abc", 0]
];

for (const [label, expected] of labelCases) {
  assert.equal(secondsFromIntervalLabel(label), expected, `${label} should parse to ${expected}`);
}

// ---------- shared data-* payload helper ----------

{
  const suggestionPayload = createControlCenterActionPayloadFromDataset(
    { payloadField: "action", payloadValue: "检查代码逻辑与异常处理", payloadIndex: "0" },
    "perception"
  );
  assert.equal(suggestionPayload.page, "perception", "payload helper should include page");
  assert.equal(suggestionPayload.action, "检查代码逻辑与异常处理", "payload helper should promote action field");
  assert.equal(suggestionPayload.index, 0, "payload helper should coerce payloadIndex to number");

  const abilityPayload = createControlCenterActionPayloadFromDataset(
    { payloadField: "label", payloadValue: "文件处理", payloadIndex: "0" },
    "advanced"
  );
  assert.equal(abilityPayload.label, "文件处理", "payload helper should promote label field");

  const selectPackPayload = createControlCenterActionPayloadFromDataset(
    { payloadField: "packId", payloadValue: "Akane Default", payloadPackId: "akane_default" },
    "character"
  );
  assert.equal(selectPackPayload.value, "Akane Default", "payload helper should keep display value");
  assert.equal(selectPackPayload.packId, "akane_default", "payload helper should prefer explicit packId");

  const expertPayload = createControlCenterActionPayloadFromDataset(
    { payloadField: "enabled", payloadValue: "true", payloadOptionId: "expert_devMode", payloadIndex: "0" },
    "advanced"
  );
  assert.equal(expertPayload.optionId, "expert_devMode", "payload helper should keep expert optionId");
  assert.equal(expertPayload.value, true, "payload helper should coerce boolean value");
  assert.equal(expertPayload.enabled, true, "payload helper should promote enabled field");

  const exitPayload = createControlCenterActionPayloadFromDataset(
    { payloadRequiresConfirmation: "true" },
    "advanced"
  );
  assert.equal(exitPayload.requiresConfirmation, true, "payload helper should coerce requiresConfirmation");

  const workspaceRecommendationPayload = createControlCenterActionPayloadFromDataset(
    { payloadItemType: "generated", payloadHandle: "audio_001", payloadTitle: "Starry Days" },
    "music"
  );
  assert.deepEqual(
    workspaceRecommendationPayload,
    { page: "music", itemType: "generated", handle: "audio_001", title: "Starry Days" },
    "payload helper should build workspace recommendation payload"
  );

  const providerPayload = createControlCenterActionPayloadFromDataset(
    { payloadProviderId: "provider.comfyui.local", payloadEndpoint: "http://127.0.0.1:8188" },
    "abilities"
  );
  assert.equal(providerPayload.providerId, "provider.comfyui.local", "payload helper should keep providerId");
  assert.equal(providerPayload.endpoint, "http://127.0.0.1:8188", "payload helper should keep provider endpoint");

  const workflowPayload = createControlCenterActionPayloadFromDataset(
    { payloadWorkflowId: "workflow.workshop.portrait.cutout" },
    "abilities"
  );
  assert.equal(workflowPayload.workflowId, "workflow.workshop.portrait.cutout", "payload helper should keep workflowId");

  const mcpPayload = createControlCenterActionPayloadFromDataset(
    { payloadServerId: "browser" },
    "abilities"
  );
  assert.equal(mcpPayload.serverId, "browser", "payload helper should keep MCP serverId");
}

assert.ok(afterActionLog.length >= bridgedActionCases.length, "onAfterAction should receive refresh results");

// at least the executed actions should have refresh:true; not-implemented results have refresh:false
const refreshTrueCount = afterActionLog.filter((result) => result.refresh === true).length;
assert.ok(refreshTrueCount >= bridgedActionCases.length, "executed bridged actions should request refresh");

// ---------- hardening: handler throws ----------

const THROWBACK_ID = "test.handler-throws";
const throwRouter = createControlCenterActionRouter({ dataSource });
throwRouter.register(THROWBACK_ID, () => {
  throw new Error("handler explosion");
});
const throwResult = await throwRouter.run(THROWBACK_ID);
assert.equal(throwResult.ok, false, "handler throw should produce ok:false");
assert.equal(throwResult.status, "failed", "handler throw should produce status:failed");
assert.equal(typeof throwResult.error, "string", "error should be a string");
assert.equal(throwResult.refresh, true, "handler throw should request refresh");

// ---------- hardening: dataSource.runAction throws ----------

const FAIL_DS = {
  kind: "mock",
  handlesAction() { return true; },
  async runAction() { throw new Error("data source kaboom"); }
};
const failDsRouter = createControlCenterActionRouter({ dataSource: FAIL_DS });
const failDsResult = await failDsRouter.run("any.action");
assert.equal(failDsResult.ok, false, "dataSource throw should produce ok:false");
assert.equal(failDsResult.status, "failed", "dataSource throw should produce status:failed");
assert.equal(typeof failDsResult.error, "string", "error should be a string");
assert.equal(failDsResult.refresh, true, "dataSource throw should request refresh");

// ---------- hardening: onAfterAction throws does not affect result ----------

const onAfterLog = [];
const oaRouter = createControlCenterActionRouter({
  dataSource,
  onAfterAction() { throw new Error("hook oops"); }
});
const oaResult = await oaRouter.run(CONTROL_CENTER_ACTIONS.chatNew);
assert.equal(oaResult.ok, true, "onAfterAction throw should not change result");
assert.equal(oaResult.status, "executed", "onAfterAction throw should not change status");

// ---------- hardening: refresh:false from handler is preserved ----------

const NOREFRESH_ID = "test.no-refresh";
const nrRouter = createControlCenterActionRouter({ dataSource });
nrRouter.register(NOREFRESH_ID, () => ({ ok: true, refresh: false }));
const nrResult = await nrRouter.run(NOREFRESH_ID);
assert.equal(nrResult.ok, true, "refresh:false should keep ok:true");
assert.equal(nrResult.refresh, false, "handler refresh:false should be preserved");

// ---------- window actions ----------

const windowLog = [];
const winDataSource = createTauriControlCenterSource({
  tauriBridge: {
    invoke: async (command) => { invokeLog.push({ command }); },
    window: {
      minimize: async () => { windowLog.push("minimize"); },
      toggleMaximize: async () => { windowLog.push("toggleMaximize"); }
    }
  }
});
const winRouter = createControlCenterActionRouter({ dataSource: winDataSource });

// window.close — uses tauriInvokeByActionId (close_window)
{
  assert.equal(isControlCenterBridgedAction(CONTROL_CENTER_ACTIONS.windowClose), true);
  const result = await winRouter.run(CONTROL_CENTER_ACTIONS.windowClose);
  assert.equal(result.status, "executed", "window.close should execute");
  assert.equal(result.refresh, true, "window.close should request refresh");
  const invokeEntry = invokeLog.find((e) => e.command === "close_window");
  assert.ok(invokeEntry, "window.close should invoke close_window");
}

// window.minimize — uses injected window.minimize
{
  assert.equal(isControlCenterBridgedAction(CONTROL_CENTER_ACTIONS.windowMinimize), true);
  const result = await winRouter.run(CONTROL_CENTER_ACTIONS.windowMinimize);
  assert.equal(result.status, "executed", "window.minimize should execute");
  assert.equal(result.actionId, CONTROL_CENTER_ACTIONS.windowMinimize, "window.minimize should keep actionId");
  assert.equal(result.refresh, true, "window.minimize should request refresh");
  assert.ok(windowLog.includes("minimize"), "window.minimize should call bridge window.minimize");
  const directResult = await winDataSource.runAction(CONTROL_CENTER_ACTIONS.windowMinimize);
  assert.equal(directResult.actionId, CONTROL_CENTER_ACTIONS.windowMinimize, "dataSource window.minimize should return actionId");
}

// window.maximize — uses injected window.toggleMaximize
{
  assert.equal(isControlCenterBridgedAction(CONTROL_CENTER_ACTIONS.windowMaximize), true);
  const result = await winRouter.run(CONTROL_CENTER_ACTIONS.windowMaximize);
  assert.equal(result.status, "executed", "window.maximize should execute");
  assert.equal(result.actionId, CONTROL_CENTER_ACTIONS.windowMaximize, "window.maximize should keep actionId");
  assert.equal(result.refresh, true, "window.maximize should request refresh");
  assert.ok(windowLog.includes("toggleMaximize"), "window.maximize should call bridge window.toggleMaximize");
  const directResult = await winDataSource.runAction(CONTROL_CENTER_ACTIONS.windowMaximize);
  assert.equal(directResult.actionId, CONTROL_CENTER_ACTIONS.windowMaximize, "dataSource window.maximize should return actionId");
}

// window.notify — NOT bridged, stays not-implemented
{
  const result = await dataSource.runAction(CONTROL_CENTER_ACTIONS.windowNotify);
  assert.equal(result.status, "not-implemented", "window.notify should be not-implemented");
  assert.equal(result.ok, false, "window.notify should be ok:false");
  assert.equal(result.refresh, false, "window.notify should not request refresh");
  // router.run normalizes not-implemented to refresh:false
  const routerResult = await router.run(CONTROL_CENTER_ACTIONS.windowNotify);
  assert.equal(routerResult.status, "not-implemented", "window.notify should route to not-implemented");
  assert.equal(routerResult.refresh, false, "window.notify should normalize to refresh:false");
}

// window actions are client-only; backend source must not POST them when Tauri is unavailable.
{
  let backendFetchCalled = false;
  const backendSource = createBackendControlCenterSource({
    fetchImpl: async () => {
      backendFetchCalled = true;
      return { ok: false, status: 500, headers: { get: () => "" } };
    }
  });
  const result = await backendSource.runAction(CONTROL_CENTER_ACTIONS.windowMinimize);
  assert.equal(result.status, "not-implemented", "backend window.minimize should be not-implemented without Tauri");
  assert.equal(result.refresh, false, "backend window.minimize should not request refresh");
  assert.equal(backendFetchCalled, false, "backend window.minimize should not call backend HTTP");
}

// character.previewEmotion is client-only, must not fall back to backend HTTP
{
  let backendFetchCalled = false;
  const backendSource = createBackendControlCenterSource({
    fetchImpl: async () => {
      backendFetchCalled = true;
      return { ok: false, status: 500, headers: { get: () => "" } };
    }
  });
  const result = await backendSource.runAction(CONTROL_CENTER_ACTIONS.characterPreviewEmotion, { value: "happy" });
  assert.equal(result.status, "not-implemented", "backend character.previewEmotion should be not-implemented without Tauri");
  assert.equal(result.refresh, false, "backend character.previewEmotion should not request refresh");
  assert.equal(backendFetchCalled, false, "backend character.previewEmotion should not call backend HTTP");
}

// Provider configuration actions are bridged to dedicated backend routes, not /control-center/actions.
{
  const fetchCalls = [];
  const backendSource = createBackendControlCenterSource({
    baseUrl: "http://provider-action-test",
    sessionId: "desktop",
    profileUserId: "master",
    fetchImpl: async (url, options = {}) => {
      fetchCalls.push({ url: String(url), options });
      return {
        ok: true,
        status: 200,
        headers: { get: () => "application/json" },
        json: async () => ({
          ok: true,
          status: String(url).includes("inspect-folder")
            ? "inspected"
            : String(url).includes("tts-test")
              ? "tts-test-ready"
              : String(url).includes("health-check")
                ? "ready"
                : "saved",
          providerId: "provider.comfyui.local",
          suggestedProfile: String(url).includes("inspect-folder")
            ? { voiceProfileId: "reimu_main", displayName: "Reimu Main", refAudioPath: "C:\\voices\\reimu_ref.wav", promptText: "参考文本" }
            : undefined,
          mediaType: "audio/wav",
          audioBase64: "d2F2",
          refresh: true
        })
      };
    }
  });

  const saveResult = await backendSource.runAction(CONTROL_CENTER_ACTIONS.abilitiesProviderConfigSave, {
    providerId: "provider.comfyui.local",
    enabled: true,
    endpoint: "http://127.0.0.1:8188/ui?token=secret",
    token: "must-not-send"
  });
  assert.equal(saveResult.status, "saved", "provider config save should hit provider config route");
  assert.equal(saveResult.refresh, true, "provider config save should request refresh");

  const healthResult = await backendSource.runAction(CONTROL_CENTER_ACTIONS.abilitiesProviderHealthCheck, {
    providerId: "provider.comfyui.local",
    endpoint: "http://127.0.0.1:8188"
  });
  assert.equal(healthResult.status, "ready", "provider health check should hit provider health route");

  const testResult = await backendSource.runAction(CONTROL_CENTER_ACTIONS.abilitiesProviderTtsTest, {
    providerId: "provider.tts.gpt_sovits.local",
    endpoint: "http://127.0.0.1:9880/ui?token=secret",
    text: "测试本地声线",
    voiceProfileId: "reimu_main",
    textLang: "zh",
    promptLang: "zh",
    mediaType: "wav",
    refAudioPath: "C:\\voices\\reimu_ref.wav",
    promptText: "参考文本",
    token: "must-not-send"
  });
  assert.equal(testResult.status, "tts-test-ready", "provider tts test should hit provider tts-test route");
  assert.equal(testResult.audioBase64, "d2F2", "provider tts test should return audio payload");

  const inspectResult = await backendSource.runAction(CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileInspectFolder, {
    providerId: "provider.tts.gpt_sovits.local",
    folderPath: "C:\\models\\reimu",
    token: "must-not-send"
  });
  assert.equal(inspectResult.status, "inspected", "provider voice profile inspect should hit provider inspect-folder route");
  assert.equal(inspectResult.suggestedProfile.voiceProfileId, "reimu_main", "provider inspect should return suggested profile");

  const voiceProfileResult = await backendSource.runAction(CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileSave, {
    providerId: "provider.tts.gpt_sovits.local",
    voiceProfileId: "reimu_main",
    displayName: "Reimu Main",
    voiceProfileEnabled: true,
    textLang: "zh",
    promptLang: "zh",
    mediaType: "wav",
    refAudioPath: "C:\\voices\\reimu_ref.wav",
    promptText: "参考文本",
    token: "must-not-send"
  });
  assert.equal(voiceProfileResult.status, "saved", "provider voice profile save should hit provider voice profile route");

  assert.equal(fetchCalls.length, 5, "provider save/check/test/inspect/profile should make five backend requests");
  assert.ok(fetchCalls[0].url.includes("/capabilities/providers/provider.comfyui.local/config"), "save should use provider config route");
  assert.ok(fetchCalls[1].url.includes("/capabilities/providers/provider.comfyui.local/health-check"), "health should use provider health route");
  assert.ok(fetchCalls[2].url.includes("/capabilities/providers/provider.tts.gpt_sovits.local/tts-test"), "tts test should use provider tts-test route");
  assert.ok(fetchCalls[3].url.includes("/capabilities/providers/provider.tts.gpt_sovits.local/voice-profiles/inspect-folder"), "voice profile inspect should use provider voice profile inspect route");
  assert.ok(fetchCalls[4].url.includes("/capabilities/providers/provider.tts.gpt_sovits.local/voice-profiles/reimu_main/config"), "voice profile save should use provider voice profile route");
  assert.equal(fetchCalls.some((call) => call.url.includes("/control-center/actions")), false, "provider actions must not use inert control-center action endpoint");
  const saveBody = JSON.parse(fetchCalls[0].options.body);
  assert.deepEqual(saveBody, { enabled: true, endpoint: "http://127.0.0.1:8188/ui?token=secret" }, "provider save should only send enabled and endpoint");
  assert.equal("token" in saveBody, false, "provider save must not forward arbitrary token fields");
  const testBody = JSON.parse(fetchCalls[2].options.body);
  assert.deepEqual(
    testBody,
    {
      endpoint: "http://127.0.0.1:9880/ui?token=secret",
      text: "测试本地声线",
      voiceProfileId: "reimu_main",
      textLang: "zh",
      promptLang: "zh",
      mediaType: "wav",
      refAudioPath: "C:\\voices\\reimu_ref.wav",
      promptText: "参考文本"
    },
    "provider tts test should only send endpoint/text/profile fields"
  );
  assert.equal("token" in testBody, false, "provider tts test must not forward arbitrary token fields");
  const inspectBody = JSON.parse(fetchCalls[3].options.body);
  assert.deepEqual(
    inspectBody,
    {
      folderPath: "C:\\models\\reimu"
    },
    "provider voice profile inspect should only send folderPath"
  );
  assert.equal("token" in inspectBody, false, "provider voice profile inspect must not forward arbitrary token fields");
  const voiceProfileBody = JSON.parse(fetchCalls[4].options.body);
  assert.deepEqual(
    voiceProfileBody,
    {
      enabled: true,
      displayName: "Reimu Main",
      textLang: "zh",
      promptLang: "zh",
      mediaType: "wav",
      refAudioPath: "C:\\voices\\reimu_ref.wav",
      promptText: "参考文本"
    },
    "provider voice profile save should only send voice profile config fields"
  );
  assert.equal("token" in voiceProfileBody, false, "provider voice profile save must not forward arbitrary token fields");
}

// MCP config/discovery actions are bridged to dedicated backend routes, not /control-center/actions.
{
  const fetchCalls = [];
  const backendSource = createBackendControlCenterSource({
    baseUrl: "http://mcp-action-test",
    sessionId: "desktop",
    profileUserId: "master",
    fetchImpl: async (url, options = {}) => {
      fetchCalls.push({ url: String(url), options });
      return {
        ok: true,
        status: 200,
        headers: { get: () => "application/json" },
        json: async () => ({
          ok: true,
          status: String(url).includes("discover") ? "discovered" : "saved",
          serverId: "browser",
          toolCount: 2,
          refresh: true
        })
      };
    }
  });

  const saveResult = await backendSource.runAction(CONTROL_CENTER_ACTIONS.abilitiesMcpConfigSave, {
    serverId: "browser",
    displayName: "Browser MCP",
    enabled: true,
    command: "C:\\mcp\\browser-mcp.exe",
    args: ["--profile", "akane"],
    cwd: "C:\\mcp",
    env: { MCP_MODE: "local" },
    token: "must-not-send"
  });
  assert.equal(saveResult.status, "saved", "MCP config save should hit MCP config route");
  assert.equal(saveResult.refresh, true, "MCP config save should request refresh");

  const discoverResult = await backendSource.runAction(CONTROL_CENTER_ACTIONS.abilitiesMcpDiscover, {
    serverId: "browser",
    token: "must-not-send"
  });
  assert.equal(discoverResult.status, "discovered", "MCP discover should hit MCP discover route");
  assert.equal(discoverResult.toolCount, 2, "MCP discover should return tool count");

  assert.equal(fetchCalls.length, 2, "MCP save/discover should make two backend requests");
  assert.ok(fetchCalls[0].url.includes("/capabilities/mcp-servers/browser/config"), "MCP save should use MCP config route");
  assert.ok(fetchCalls[1].url.includes("/capabilities/mcp-servers/browser/discover"), "MCP discover should use MCP discover route");
  assert.equal(fetchCalls.some((call) => call.url.includes("/control-center/actions")), false, "MCP actions must not use inert control-center action endpoint");
  const saveBody = JSON.parse(fetchCalls[0].options.body);
  assert.deepEqual(
    saveBody,
    {
      enabled: true,
      transport: "stdio",
      command: "C:\\mcp\\browser-mcp.exe",
      displayName: "Browser MCP",
      cwd: "C:\\mcp",
      args: ["--profile", "akane"],
      env: { MCP_MODE: "local" }
    },
    "MCP save should only send whitelisted config fields"
  );
  assert.equal("token" in saveBody, false, "MCP save must not forward arbitrary token fields");
  const discoverBody = JSON.parse(fetchCalls[1].options.body);
  assert.deepEqual(discoverBody, {}, "MCP discover should not send arbitrary payload fields");
}

// Approval policy save is bridged to its dedicated backend route, not /control-center/actions.
{
  const fetchCalls = [];
  const backendSource = createBackendControlCenterSource({
    baseUrl: "http://approval-policy-action-test",
    sessionId: "desktop",
    profileUserId: "master",
    fetchImpl: async (url, options = {}) => {
      fetchCalls.push({ url: String(url), options });
      return {
        ok: true,
        status: 200,
        headers: { get: () => "application/json" },
        json: async () => ({
          ok: true,
          status: "saved",
          approvalPolicy: {
            defaultMode: "trusted_auto_allow"
          },
          refresh: true
        })
      };
    }
  });

  const saveResult = await backendSource.runAction(CONTROL_CENTER_ACTIONS.abilitiesApprovalPolicySave, {
    defaultMode: "trusted_auto_allow",
    token: "must-not-send"
  });
  assert.equal(saveResult.status, "saved", "approval policy save should hit approval policy route");
  assert.equal(saveResult.refresh, true, "approval policy save should request refresh");
  assert.equal(fetchCalls.length, 1, "approval policy save should make one backend request");
  assert.ok(fetchCalls[0].url.includes("/capabilities/approval-policy"), "approval policy save should use approval policy route");
  assert.equal(fetchCalls[0].url.includes("/control-center/actions"), false, "approval policy save must not use inert control-center action endpoint");
  const saveBody = JSON.parse(fetchCalls[0].options.body);
  assert.deepEqual(saveBody, { defaultMode: "trusted_auto_allow" }, "approval policy save should only send defaultMode");
  assert.equal("token" in saveBody, false, "approval policy save must not forward arbitrary token fields");
}

// Workflow binding actions are bridged to dedicated backend routes, not /control-center/actions.
{
  const fetchCalls = [];
  const backendSource = createBackendControlCenterSource({
    baseUrl: "http://workflow-action-test",
    sessionId: "desktop",
    profileUserId: "master",
    fetchImpl: async (url, options = {}) => {
      fetchCalls.push({ url: String(url), options });
      return {
        ok: true,
        status: 200,
        headers: { get: () => "application/json" },
        json: async () => ({
          ok: true,
          status: String(url).includes("validate") ? "validated_config" : "saved",
          workflowId: "workflow.workshop.portrait.cutout",
          executionReady: false,
          refresh: true
        })
      };
    }
  });

  const saveResult = await backendSource.runAction(CONTROL_CENTER_ACTIONS.abilitiesWorkflowConfigSave, {
    workflowId: "workflow.workshop.portrait.cutout",
    enabled: true,
    workflowPath: "workflows/comfyui/portrait_cutout.json",
    inputImageSlot: "12.inputs.image",
    outputImageSlot: "20.inputs.filename_prefix",
    token: "must-not-send"
  });
  assert.equal(saveResult.status, "saved", "workflow config save should hit workflow config route");
  assert.equal(saveResult.refresh, true, "workflow config save should request refresh");

  const importResult = await backendSource.runAction(CONTROL_CENTER_ACTIONS.abilitiesWorkflowFileImport, {
    workflowId: "workflow.workshop.portrait.cutout",
    workflowPath: "workflows/comfyui/portrait_cutout.json",
    workflowJson: "{\"12\":{\"class_type\":\"LoadImage\",\"inputs\":{\"image\":\"old.png\"}}}",
    token: "must-not-send"
  });
  assert.equal(importResult.status, "saved", "workflow file import should hit workflow file route");

  const validateResult = await backendSource.runAction(CONTROL_CENTER_ACTIONS.abilitiesWorkflowValidate, {
    workflowId: "workflow.workshop.portrait.cutout"
  });
  assert.equal(validateResult.status, "validated_config", "workflow validate should hit workflow validate route");

  assert.equal(fetchCalls.length, 3, "workflow save/import/validate should make three backend requests");
  assert.ok(fetchCalls[0].url.includes("/capabilities/workflows/workflow.workshop.portrait.cutout/config"), "workflow save should use workflow config route");
  assert.ok(fetchCalls[1].url.includes("/capabilities/workflows/workflow.workshop.portrait.cutout/file"), "workflow file import should use workflow file route");
  assert.ok(fetchCalls[2].url.includes("/capabilities/workflows/workflow.workshop.portrait.cutout/validate"), "workflow validate should use workflow validate route");
  assert.equal(fetchCalls.some((call) => call.url.includes("/control-center/actions")), false, "workflow actions must not use inert control-center action endpoint");
  const saveBody = JSON.parse(fetchCalls[0].options.body);
  assert.deepEqual(
    saveBody,
    {
      enabled: true,
      workflowPath: "workflows/comfyui/portrait_cutout.json",
      slotMapping: {
        input_image_handle: "12.inputs.image",
        output_image_handle: "20.inputs.filename_prefix"
      }
    },
    "workflow save should only send enabled, workflowPath, and safe slot mapping"
  );
  assert.equal("token" in saveBody, false, "workflow save must not forward arbitrary token fields");
  const importBody = JSON.parse(fetchCalls[1].options.body);
  assert.deepEqual(
    importBody,
    {
      workflowPath: "workflows/comfyui/portrait_cutout.json",
      workflowJson: "{\"12\":{\"class_type\":\"LoadImage\",\"inputs\":{\"image\":\"old.png\"}}}"
    },
    "workflow file import should only send safe workflow path and JSON text"
  );
  assert.equal("token" in importBody, false, "workflow file import must not forward arbitrary token fields");
}

// ---------- deferred voice actions ----------

const deferredVoiceActionIds = [
  CONTROL_CENTER_ACTIONS.voiceSelectTtsVoice,
  CONTROL_CENTER_ACTIONS.voiceSelectAsrDevice,
  CONTROL_CENTER_ACTIONS.voiceSetAsrLanguage,
  CONTROL_CENTER_ACTIONS.voiceSetAsrSensitivity,
  CONTROL_CENTER_ACTIONS.voiceRecordsClear,
  CONTROL_CENTER_ACTIONS.voiceQueueClear
];

for (const actionId of deferredVoiceActionIds) {
  assert.equal(isControlCenterBridgedAction(actionId), false, `${actionId} should NOT be bridged`);
  const result = await router.run(actionId, { value: "test", field: "testField" }, { source: "smoke" });
  assert.equal(result.status, "not-implemented", `${actionId} should be not-implemented`);
  assert.equal(result.refresh, false, `${actionId} should have refresh:false`);
  assert.equal(result.ok, false, `${actionId} should have ok:false`);
}

// Verify deferred voice actions appear in the surface contract deferred list
const deferredSurfaceIds = new Set(deferredSurfaces.map((s) => s.actionId));
for (const actionId of deferredVoiceActionIds) {
  assert.equal(deferredSurfaceIds.has(actionId), true, `${actionId} should appear in deferred surfaces`);
}

// Voice mock-source payload preservation (simulates real UI data-* construction)
{
  const mockVoiceRouter = createControlCenterActionRouter({
    dataSource: createMockControlCenterSource(mockData)
  });
  const previewResult = await mockVoiceRouter.run(
    CONTROL_CENTER_ACTIONS.voicePreviewPlay,
    { text: mockData.voicePage.preview.text.join("\n") }
  );
  assert.equal(previewResult.status, "mocked", "voice.previewPlay mock should be mocked");
  assert.ok(Array.isArray(mockData.voicePage.preview.text), "voicePage.preview.text should be an array");
  assert.equal(typeof previewResult.payload.text, "string", "voice.previewPlay payload.text should be string");
  assert.ok(previewResult.payload.text.includes("你好"), "voice.previewPlay payload.text should include preview text");

  const wakeResult = await mockVoiceRouter.run(
    CONTROL_CENTER_ACTIONS.voiceSetWakeWord,
    { value: mockData.voicePage.wakeWord, field: "wakeWord" }
  );
  assert.equal(wakeResult.payload.value, "Akane", "voice.setWakeWord payload.value should be preserved");
  assert.equal(mockData.voicePage.wakeSensitivity, "中等", "voicePage.wakeSensitivity should be a stable field");
}

// ---------- deferred music actions ----------

const deferredMusicActionIds = [
  CONTROL_CENTER_ACTIONS.musicSetMood,
  CONTROL_CENTER_ACTIONS.musicRefreshRecommendations,
  CONTROL_CENTER_ACTIONS.musicSelectOutputDevice
];

for (const actionId of deferredMusicActionIds) {
  assert.equal(isControlCenterBridgedAction(actionId), false, `${actionId} should NOT be bridged`);
  const result = await router.run(actionId, { value: "test", field: "musicField" }, { source: "smoke" });
  assert.equal(result.status, "not-implemented", `${actionId} should be not-implemented`);
  assert.equal(result.refresh, false, `${actionId} should have refresh:false`);
  assert.equal(result.ok, false, `${actionId} should have ok:false`);
}

// Verify deferred music actions appear in the surface contract deferred list
for (const actionId of deferredMusicActionIds) {
  assert.equal(deferredSurfaceIds.has(actionId), true, `${actionId} should appear in deferred surfaces`);
}

// music.selectQueueItem payload preserves trackId and index
{
  const queuePayload = createControlCenterActionPayloadFromDataset(
    { payloadValue: "track_starsWithYou", payloadTrackId: "track_starsWithYou", payloadIndex: "3" },
    "music"
  );
  assert.equal(queuePayload.value, "track_starsWithYou", "music.selectQueueItem dataset should create payload.value");
  assert.equal(queuePayload.trackId, "track_starsWithYou", "music.selectQueueItem dataset should create payload.trackId");
  assert.equal(queuePayload.index, 3, "music.selectQueueItem dataset should create numeric payload.index");

  const mockMusicRouter = createControlCenterActionRouter({
    dataSource: createMockControlCenterSource(mockData)
  });
  const mockQueueResult = await mockMusicRouter.run(
    CONTROL_CENTER_ACTIONS.musicSelectQueueItem,
    { trackId: "track_starsWithYou", index: 3 }
  );
  assert.equal(mockQueueResult.payload.trackId, "track_starsWithYou", "music.selectQueueItem payload.trackId preserved");
  assert.equal(mockQueueResult.payload.index, 3, "music.selectQueueItem payload.index preserved");
  assert.equal(mockData.musicPage.currentPlayMode, "列表循环", "musicPage.currentPlayMode should be a stable field");
}

// existing bridged music actions still bridged and emit correctly
{
  const musicBridgedIds = [
    CONTROL_CENTER_ACTIONS.musicPrevious,
    CONTROL_CENTER_ACTIONS.musicNext,
    CONTROL_CENTER_ACTIONS.musicPause,
    CONTROL_CENTER_ACTIONS.musicStop,
    CONTROL_CENTER_ACTIONS.musicClear
  ];
  for (const actionId of musicBridgedIds) {
    assert.equal(isControlCenterBridgedAction(actionId), true, `${actionId} should remain bridged`);
  }
}

// music.refreshRecommendations payload source field
{
  const mockMusicRouter = createControlCenterActionRouter({
    dataSource: createMockControlCenterSource(mockData)
  });
  const refreshResult = await mockMusicRouter.run(
    CONTROL_CENTER_ACTIONS.musicRefreshRecommendations,
    { source: "recommendations" }
  );
  assert.equal(refreshResult.payload.source, "recommendations", "music.refreshRecommendations payload.source preserved");

  const playModeResult = await mockMusicRouter.run(
    CONTROL_CENTER_ACTIONS.musicSetPlayMode,
    { value: mockData.musicPage.currentPlayMode, field: "playMode" }
  );
  assert.equal(playModeResult.payload.value, "列表循环", "music.setPlayMode payload.value should come from mock data field");

  const outputResult = await mockMusicRouter.run(
    CONTROL_CENTER_ACTIONS.musicSelectOutputDevice,
    { value: mockData.musicPage.outputDevice, field: "outputDevice" }
  );
  assert.equal(outputResult.payload.value, "扬声器", "music.selectOutputDevice payload.value should come from mock data field");

  const normalizationResult = await mockMusicRouter.run(
    CONTROL_CENTER_ACTIONS.musicSetVolumeNormalization,
    { value: true, field: "volumeNormalization" }
  );
  assert.equal(normalizationResult.payload.value, true, "music.setVolumeNormalization payload.value should preserve boolean");
}

// playlist item id is preserved through adapter
const adapterSnapshot = createControlCenterSnapshot({
  ...mockData,
  sourceKind: "mock"
});
assert.equal(adapterSnapshot.pages.music.playlist[0].id, "track_clearSky", "playlist[0] id should be preserved through adapter");

// runtime playlist items without id get fallback, with id are preserved
const runtimeSnapshot = createControlCenterSnapshot({
  ...mockData,
  sourceKind: "backend",
  musicRuntime: {
    nowPlaying: { title: "custom", artist: "test" },
    playlist: [
      { title: "no id track", artist: "test" },
      { id: "custom-id", title: "with id", artist: "test" }
    ]
  }
});
const runtimePlaylist = runtimeSnapshot.pages.music.playlist;
assert.equal(runtimePlaylist.length, 2, "runtime playlist should have 2 items");
assert.equal(runtimePlaylist[0].id, "no id track", "playlist item without id should fall back to title");
assert.equal(runtimePlaylist[1].id, "custom-id", "playlist item with id should be preserved");

// Tauri music runtime queue must expose sourceId as the clickable queue item value.
const musicRuntimePatch = buildMusicRuntimePatch({
  musicSnapshot: {
    track: { sourceId: "local-1", displayName: "Runtime Track" },
    queue: [{ sourceId: "local-1", displayName: "Runtime Track" }],
    queueIndex: 0,
    progressSeconds: 0,
    durationSeconds: 60,
    playing: true
  },
  petState: { voiceVolume: 0.8 }
});
assert.equal(musicRuntimePatch.playlist[0].id, "local-1", "runtime playlist id should come from sourceId");
assert.equal(musicRuntimePatch.playlist[0].sourceId, "local-1", "runtime playlist should preserve sourceId");
assert.deepEqual(musicRuntimePatch.recommendations, [], "runtime music patch without recommendations should clear mock recommendations");

const musicRuntimeRecommendationPatch = buildMusicRuntimePatch({
  musicSnapshot: {
    track: { sourceId: "local-1", displayName: "Runtime Track" },
    queue: [{ sourceId: "local-1", displayName: "Runtime Track" }],
    queueIndex: 0,
    progressSeconds: 0,
    durationSeconds: 60,
    playing: true,
    recommendations: [
      {
        id: "local-2",
        sourceId: "local-2",
        title: "Next Runtime Track",
        durationSeconds: 123,
        durationLabel: "02:03",
        reason: "下一首",
        playable: true
      }
    ]
  },
  petState: { voiceVolume: 0.8 }
});
assert.equal(musicRuntimeRecommendationPatch.recommendations.length, 1, "runtime music recommendations should patch");
assert.equal(musicRuntimeRecommendationPatch.recommendations[0].sourceId, "local-2", "runtime music recommendation sourceId should patch");
assert.equal(musicRuntimeRecommendationPatch.recommendations[0].duration, "02:03", "runtime music recommendation duration label should patch");

// ---------- deferred character actions ----------

const deferredCharacterActionIds = [
  CONTROL_CENTER_ACTIONS.characterManageOutfits,
  CONTROL_CENTER_ACTIONS.characterMoreExpressions,
  CONTROL_CENTER_ACTIONS.characterResourceRepair
];

for (const actionId of deferredCharacterActionIds) {
  assert.equal(isControlCenterBridgedAction(actionId), false, `${actionId} should NOT be bridged`);
  const result = await router.run(actionId, { value: "test", field: "charField" }, { source: "smoke" });
  assert.equal(result.status, "not-implemented", `${actionId} should be not-implemented`);
  assert.equal(result.refresh, false, `${actionId} should have refresh:false`);
  assert.equal(result.ok, false, `${actionId} should have ok:false`);
}

// Verify deferred character actions appear in surface contract
for (const actionId of deferredCharacterActionIds) {
  assert.equal(deferredSurfaceIds.has(actionId), true, `${actionId} should appear in deferred surfaces`);
}

// Mock-source payload preservation
{
  const mockCharRouter = createControlCenterActionRouter({
    dataSource: createMockControlCenterSource(mockData)
  });
  const outfitResult = await mockCharRouter.run(
    CONTROL_CENTER_ACTIONS.characterSetOutfit,
    { value: "summer", outfitId: "summer" }
  );
  assert.equal(outfitResult.payload.outfitId, "summer", "character.setOutfit payload.outfitId preserved");
  assert.equal(outfitResult.payload.value, "summer", "character.setOutfit payload.value preserved");

  const selectResult = await mockCharRouter.run(
    CONTROL_CENTER_ACTIONS.characterSelectPack,
    { packId: "akane_default", value: "akane_default" }
  );
  assert.equal(selectResult.payload.packId, "akane_default", "character.selectPack payload.packId preserved");
}

// selectedPackId fallback from mock data
const charSnapshot = createControlCenterSnapshot({ ...mockData, sourceKind: "mock" }).pages.character;
assert.equal(charSnapshot.selectedPackId, "akane_default", "selectedPackId should come from mock data");

// Runtime warning actionId override
const warningSnapshot = createControlCenterSnapshot({
  ...mockData,
  sourceKind: "backend",
  characterRuntime: {
    warning: { actionId: "character.resourceRepair" }
  }
}).pages.character;
assert.equal(warningSnapshot.warning.actionId, "character.resourceRepair", "runtime warning actionId should be preserved");

// importZip/apply/restoreDefaults still not-implemented
const stillDeferred = [
  CONTROL_CENTER_ACTIONS.characterImportZip,
  CONTROL_CENTER_ACTIONS.characterApply,
  CONTROL_CENTER_ACTIONS.characterRestoreDefaults
];
for (const actionId of stillDeferred) {
  const result = await router.run(actionId, {}, { source: "smoke" });
  assert.equal(result.status, "not-implemented", `${actionId} should still be not-implemented`);
  assert.equal(result.refresh, false, `${actionId} should still have refresh:false`);
}

// ---------- deferred perception actions ----------

const deferredPerceptionActionIds = [
  CONTROL_CENTER_ACTIONS.perceptionPrivacyHelp,
  CONTROL_CENTER_ACTIONS.perceptionManagePermissions,
  CONTROL_CENTER_ACTIONS.perceptionClipboardClear,
  CONTROL_CENTER_ACTIONS.perceptionEventsViewAll,
  CONTROL_CENTER_ACTIONS.perceptionSuggestionRun
];

for (const actionId of deferredPerceptionActionIds) {
  assert.equal(isControlCenterBridgedAction(actionId), false, `${actionId} should NOT be bridged`);
  const result = await router.run(actionId, { page: "perception" }, { source: "smoke" });
  assert.equal(result.status, "not-implemented", `${actionId} should be not-implemented`);
  assert.equal(result.refresh, false, `${actionId} should have refresh:false`);
  assert.equal(result.ok, false, `${actionId} should have ok:false`);
}

for (const actionId of deferredPerceptionActionIds) {
  assert.equal(deferredSurfaceIds.has(actionId), true, `${actionId} should appear in deferred surfaces`);
}

// perception.clipboard.clear is NOT bridged
assert.equal(isControlCenterBridgedAction(CONTROL_CENTER_ACTIONS.perceptionClipboardClear), false, "perception.clipboard.clear should NOT be bridged");
// perception.screenVision.clear IS still bridged
assert.equal(isControlCenterBridgedAction(CONTROL_CENTER_ACTIONS.perceptionScreenVisionClear), true, "perception.screenVision.clear should remain bridged");

// Mock-source payload preservation
{
  const mockPerRouter = createControlCenterActionRouter({
    dataSource: createMockControlCenterSource(mockData)
  });
  const suggestionResult = await mockPerRouter.run(
    CONTROL_CENTER_ACTIONS.perceptionSuggestionRun,
    { action: "检查代码逻辑与异常处理", index: 0 }
  );
  assert.equal(suggestionResult.payload.action, "检查代码逻辑与异常处理", "suggestion payload.action preserved");
  assert.equal(suggestionResult.payload.index, 0, "suggestion payload.index preserved");

  const clipboardResult = await mockPerRouter.run(
    CONTROL_CENTER_ACTIONS.perceptionClipboardClear,
    { featureId: "clipboard" }
  );
  assert.equal(clipboardResult.payload.featureId, "clipboard", "clipboard.clear payload.featureId preserved");
}

// ---------- deferred abilities actions ----------

const deferredAbilitiesActionIds = [
  CONTROL_CENTER_ACTIONS.abilitiesQuickAction,
  CONTROL_CENTER_ACTIONS.abilitiesManageModules,
  CONTROL_CENTER_ACTIONS.abilitiesMoreWorkflows,
  CONTROL_CENTER_ACTIONS.abilitiesSafetyDetails,
  CONTROL_CENTER_ACTIONS.abilitiesLive2dOpenSettings
];

for (const actionId of deferredAbilitiesActionIds) {
  assert.equal(isControlCenterBridgedAction(actionId), false, `${actionId} should NOT be bridged`);
  const result = await router.run(actionId, { page: "abilities" }, { source: "smoke" });
  assert.equal(result.status, "not-implemented", `${actionId} should be not-implemented`);
  assert.equal(result.refresh, false, `${actionId} should have refresh:false`);
  assert.equal(result.ok, false, `${actionId} should have ok:false`);
}

for (const actionId of deferredAbilitiesActionIds) {
  assert.equal(deferredSurfaceIds.has(actionId), true, `${actionId} should appear in deferred surfaces`);
}

// Mock-source payload: abilities quick action
{
  const mockAbRouter = createControlCenterActionRouter({
    dataSource: createMockControlCenterSource(mockData)
  });
  const quickResult = await mockAbRouter.run(
    CONTROL_CENTER_ACTIONS.abilitiesQuickAction,
    { label: "创建文档", index: 0 }
  );
  assert.equal(quickResult.payload.label, "创建文档", "abilities quick action payload.label preserved");
  assert.equal(quickResult.payload.index, 0, "abilities quick action payload.index preserved");

  const providerMockResult = await mockAbRouter.run(
    CONTROL_CENTER_ACTIONS.abilitiesProviderConfigSave,
    { providerId: "provider.comfyui.local", endpoint: "http://127.0.0.1:8188", enabled: true }
  );
  assert.equal(providerMockResult.status, "not-implemented", "mock source must not fake provider config save");
  assert.equal(providerMockResult.refresh, false, "mock provider config save should not request refresh");

  const voiceProfileMockResult = await mockAbRouter.run(
    CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileSave,
    { providerId: "provider.tts.gpt_sovits.local", voiceProfileId: "reimu_main", enabled: true }
  );
  assert.equal(voiceProfileMockResult.status, "not-implemented", "mock source must not fake provider voice profile save");
  assert.equal(voiceProfileMockResult.refresh, false, "mock provider voice profile save should not request refresh");

  const voiceProfileInspectMockResult = await mockAbRouter.run(
    CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileInspectFolder,
    { providerId: "provider.tts.gpt_sovits.local", folderPath: "C:\\models\\reimu" }
  );
  assert.equal(voiceProfileInspectMockResult.status, "not-implemented", "mock source must not fake provider voice profile inspect");
  assert.equal(voiceProfileInspectMockResult.refresh, false, "mock provider voice profile inspect should not request refresh");

  const voiceProfileAssignMockResult = await mockAbRouter.run(
    CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileAssignToCurrentCharacter,
    { providerId: "provider.tts.gpt_sovits.local", voiceProfileId: "dania", characterPackId: "akane_sample" }
  );
  assert.equal(voiceProfileAssignMockResult.status, "not-implemented", "mock source must not fake character voice assignment");
  assert.equal(voiceProfileAssignMockResult.refresh, false, "mock character voice assignment should not request refresh");

  const voiceProfileClearMockResult = await mockAbRouter.run(
    CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileClearCurrentCharacter,
    { providerId: "provider.tts.gpt_sovits.local", characterPackId: "akane_sample" }
  );
  assert.equal(voiceProfileClearMockResult.status, "not-implemented", "mock source must not fake character voice clear");
  assert.equal(voiceProfileClearMockResult.refresh, false, "mock character voice clear should not request refresh");

  const workflowMockResult = await mockAbRouter.run(
    CONTROL_CENTER_ACTIONS.abilitiesWorkflowConfigSave,
    { workflowId: "workflow.workshop.portrait.cutout", workflowPath: "workflows/comfyui/portrait_cutout.json", enabled: true }
  );
  assert.equal(workflowMockResult.status, "not-implemented", "mock source must not fake workflow config save");
  assert.equal(workflowMockResult.refresh, false, "mock workflow config save should not request refresh");

  const mcpMockResult = await mockAbRouter.run(
    CONTROL_CENTER_ACTIONS.abilitiesMcpConfigSave,
    { serverId: "browser", command: "browser-mcp", enabled: true }
  );
  assert.equal(mcpMockResult.status, "not-implemented", "mock source must not fake MCP config save");
  assert.equal(mcpMockResult.refresh, false, "mock MCP config save should not request refresh");

  const approvalPolicyMockResult = await mockAbRouter.run(
    CONTROL_CENTER_ACTIONS.abilitiesApprovalPolicySave,
    { defaultMode: "trusted_auto_allow" }
  );
  assert.equal(approvalPolicyMockResult.status, "not-implemented", "mock source must not fake approval policy save");
  assert.equal(approvalPolicyMockResult.refresh, false, "mock approval policy save should not request refresh");
}

// ---------- deferred advanced + shell actions ----------

const deferredAdvancedActionIds = [
  CONTROL_CENTER_ACTIONS.advancedLogsClear,
  CONTROL_CENTER_ACTIONS.advancedExitPet,
  CONTROL_CENTER_ACTIONS.advancedExpertOption,
  CONTROL_CENTER_ACTIONS.advancedLive2dOpenStatus,
  CONTROL_CENTER_ACTIONS.advancedAbilityDetails
];

for (const actionId of deferredAdvancedActionIds) {
  assert.equal(isControlCenterBridgedAction(actionId), false, `${actionId} should NOT be bridged`);
  const result = await router.run(actionId, { page: "advanced" }, { source: "smoke" });
  assert.equal(result.status, "not-implemented", `${actionId} should be not-implemented`);
  assert.equal(result.refresh, false, `${actionId} should have refresh:false`);
  assert.equal(result.ok, false, `${actionId} should have ok:false`);
}

for (const actionId of deferredAdvancedActionIds) {
  assert.equal(deferredSurfaceIds.has(actionId), true, `${actionId} should appear in deferred surfaces`);
}

// window.notify still deferred (not bridged)
assert.equal(isControlCenterBridgedAction(CONTROL_CENTER_ACTIONS.windowNotify), false, "window.notify should remain deferred");
const notifyResult = await router.run(CONTROL_CENTER_ACTIONS.windowNotify, { page: "shell" }, { source: "smoke" });
assert.equal(notifyResult.status, "not-implemented", "window.notify should be not-implemented");
assert.equal(notifyResult.refresh, false, "window.notify should have refresh:false");

// advanced.exitPet is NOT window.close
assert.notEqual(CONTROL_CENTER_ACTIONS.advancedExitPet, CONTROL_CENTER_ACTIONS.windowClose, "advanced.exitPet should differ from window.close");

// Mock-source payload preservation
{
  const mockAdvRouter = createControlCenterActionRouter({
    dataSource: createMockControlCenterSource(mockData)
  });
  const expertResult = await mockAdvRouter.run(
    CONTROL_CENTER_ACTIONS.advancedExpertOption,
    { optionId: "expert_devMode", index: 0, value: false }
  );
  assert.equal(expertResult.payload.optionId, "expert_devMode", "expertOption payload.optionId preserved");
  assert.equal(expertResult.payload.index, 0, "expertOption payload.index preserved");
  assert.equal(expertResult.payload.value, false, "expertOption payload.value preserved");

  const abilityResult = await mockAdvRouter.run(
    CONTROL_CENTER_ACTIONS.advancedAbilityDetails,
    { label: "文件处理", index: 0 }
  );
  assert.equal(abilityResult.payload.label, "文件处理", "ability details payload.label preserved");
  assert.equal(abilityResult.payload.index, 0, "ability details payload.index preserved");

  const exitResult = await mockAdvRouter.run(
    CONTROL_CENTER_ACTIONS.advancedExitPet,
    { page: "advanced", requiresConfirmation: true }
  );
  assert.equal(exitResult.payload.requiresConfirmation, true, "exitPet payload.requiresConfirmation preserved");
}

// Adapter: operations[0]/[1] bridged, operations[2] exitPet deferred and not bridged
const advSnap = createControlCenterSnapshot({ ...mockData, sourceKind: "mock" }).pages.advanced;
assert.equal(advSnap.operations[0].actionId, "advanced.probeClickThrough", "operations[0] should be probeClickThrough");
assert.equal(advSnap.operations[1].actionId, "advanced.resetWindow", "operations[1] should be resetWindow");
assert.equal(advSnap.operations[2].actionId, "advanced.exitPet", "operations[2] should be exitPet");
assert.equal(isControlCenterBridgedAction(advSnap.operations[2].actionId), false, "operations[2] exitPet should NOT be bridged");

// expertOptions / abilityOverview have stable id
assert.ok(advSnap.expertOptions.every((item) => item.id), "expertOptions should all have id");
assert.ok(advSnap.abilityOverview.every((item) => item.id), "abilityOverview should all have id");

// existing bridged advanced core/operations still work
const advBridgeTestCases = [
  CONTROL_CENTER_ACTIONS.advancedProbeClickThrough,
  CONTROL_CENTER_ACTIONS.advancedResetWindow,
  CONTROL_CENTER_ACTIONS.advancedToggleWebgl,
  CONTROL_CENTER_ACTIONS.advancedSetHitTestEnabled,
  CONTROL_CENTER_ACTIONS.advancedSetHitboxOverlay
];
for (const actionId of advBridgeTestCases) {
  assert.equal(isControlCenterBridgedAction(actionId), true, `${actionId} should remain bridged`);
}

// ---------- advanced operations ----------

const advancedSnapshot = createControlCenterSnapshot({
  ...mockData,
  sourceKind: "mock"
}).pages.advanced;

assert.ok(Array.isArray(advancedSnapshot.coreSettings), "advanced.coreSettings should be an array");
assert.equal(advancedSnapshot.coreSettings[0].id, "webgl", "coreSettings[0] should be webgl");
assert.equal(advancedSnapshot.coreSettings[0].actionId, "advanced.toggleWebgl", "webgl should have actionId");
assert.equal(advancedSnapshot.coreSettings[1].id, "hitTest", "coreSettings[1] should be hitTest");
assert.equal(advancedSnapshot.coreSettings[1].actionId, "advanced.setHitTestEnabled", "hitTest should have actionId");
assert.equal(advancedSnapshot.coreSettings[2].id, "hitbox", "coreSettings[2] should be hitbox");
assert.equal(advancedSnapshot.coreSettings[2].actionId, "advanced.setHitboxOverlay", "hitbox should have actionId");
assert.ok(Array.isArray(advancedSnapshot.operations), "advanced.operations should be an array");
assert.equal(advancedSnapshot.operations[0].actionId, "advanced.probeClickThrough", "operations[0] should have actionId");
assert.equal(advancedSnapshot.operations[1].actionId, "advanced.resetWindow", "operations[1] should have actionId");
assert.equal(advancedSnapshot.operations[2].actionId, "advanced.exitPet", "operations[2] 退出 should have exitPet actionId");

const runtimeAdvancedSnapshot = createControlCenterSnapshot({
  ...mockData,
  sourceKind: "backend",
  advancedRuntime: {
    operations: [
      { title: "运行时窗口穿透", action: "执行", icon: "window", tone: "blue" },
      { title: "运行时重置窗口", action: "重置", icon: "refresh", tone: "blue" },
      { title: "运行时退出桌宠", action: "退出", icon: "alert", tone: "pink" }
    ]
  }
}).pages.advanced;

assert.equal(runtimeAdvancedSnapshot.operations[0].actionId, "advanced.probeClickThrough", "runtime operations[0] should get fallback actionId");
assert.equal(runtimeAdvancedSnapshot.operations[1].actionId, "advanced.resetWindow", "runtime operations[1] should get fallback actionId");
assert.equal(runtimeAdvancedSnapshot.operations[2].actionId, undefined, "runtime operations[2] should NOT get fallback actionId");

const runtimeAdvancedCoreSnapshot = createControlCenterSnapshot({
  ...mockData,
  sourceKind: "backend",
  advancedRuntime: {
    coreSettings: [
      { id: "hitTest", enabled: false },
      { id: "hitbox", enabled: false }
    ]
  }
}).pages.advanced;

assert.equal(runtimeAdvancedCoreSnapshot.coreSettings[0].actionId, "advanced.toggleWebgl", "runtime webgl actionId should stay bridged");
assert.equal(runtimeAdvancedCoreSnapshot.coreSettings[1].enabled, false, "runtime hitTest enabled should patch by id");
assert.equal(runtimeAdvancedCoreSnapshot.coreSettings[1].actionId, "advanced.setHitTestEnabled", "runtime hitTest actionId should stay bridged");
assert.equal(runtimeAdvancedCoreSnapshot.coreSettings[2].enabled, false, "runtime hitbox enabled should patch by id");
assert.equal(runtimeAdvancedCoreSnapshot.coreSettings[2].actionId, "advanced.setHitboxOverlay", "runtime hitbox actionId should stay bridged");

// ---------- character warning action ----------

const characterSnapshot = createControlCenterSnapshot({
  ...mockData,
  sourceKind: "mock"
}).pages.character;

assert.equal(characterSnapshot.warning.actionId, "character.refresh", "character warning should default to refresh action");

const customCharacterSnapshot = createControlCenterSnapshot({
  ...mockData,
  sourceKind: "backend",
  characterRuntime: {
    warning: {
      title: "运行时资源提示",
      action: "自定义动作",
      actionId: "custom.character.warning"
    }
  }
}).pages.character;

assert.equal(customCharacterSnapshot.warning.actionId, "custom.character.warning", "custom character warning actionId should be preserved");

// ---------- perception screen vision controls ----------

const perceptionSnapshot = createControlCenterSnapshot({
  ...mockData,
  sourceKind: "mock"
}).pages.perception;
const screenCard = perceptionSnapshot.featureCards.find((card) => card.id === "screen");
assert.equal(screenCard.frequency, "25 秒", "screen vision mock interval should match runtime constraints");
assert.equal(screenCard.frames, "4", "screen vision mock frame count should match runtime constraints");

const runtimePerceptionSnapshot = createControlCenterSnapshot({
  ...mockData,
  sourceKind: "backend",
  perceptionRuntime: {
    featureCards: [{ id: "screen", frequency: "60 秒", frames: "5" }]
  }
}).pages.perception;
const runtimeScreenCard = runtimePerceptionSnapshot.featureCards.find((card) => card.id === "screen");
assert.equal(runtimeScreenCard.frequency, "60 秒", "runtime screen interval should patch by id");
assert.equal(runtimeScreenCard.frames, "5", "runtime screen frame count should patch by id");

// ---------- voice basic settings ----------

const runtimeVoiceSnapshot = createControlCenterSnapshot({
  ...mockData,
  sourceKind: "backend",
  voiceRuntime: {
    tts: { enabled: false, volume: 70 },
    asr: { enabled: true }
  }
}).pages.voice;

assert.equal(runtimeVoiceSnapshot.tts.enabled, false, "runtime TTS enabled should patch");
assert.equal(runtimeVoiceSnapshot.tts.volume, 70, "runtime TTS volume should patch");
assert.equal(runtimeVoiceSnapshot.asr.enabled, true, "runtime ASR enabled should patch");

const runtimeVoiceContractSnapshot = createControlCenterSnapshot({
  ...mockData,
  sourceKind: "backend",
  voiceRuntime: {
    preview: { text: "单行试听文本" },
    wakeWord: "AkaneNext",
    wakeSensitivity: "高"
  }
}).pages.voice;

assert.equal(runtimeVoiceContractSnapshot.preview.text, "单行试听文本", "runtime voice preview text should allow string payload source");
assert.equal(runtimeVoiceContractSnapshot.wakeWord, "AkaneNext", "runtime wake word should patch");
assert.equal(runtimeVoiceContractSnapshot.wakeSensitivity, "高", "runtime wake sensitivity should patch");

// ---------- overview music controls ----------

const overviewSnapshot = createControlCenterSnapshot({
  ...mockData,
  sourceKind: "mock"
}).pages.overview;

assert.equal(overviewSnapshot.music.controls.length, 5, "overview music controls should have 5 items");
assert.equal(overviewSnapshot.music.controls[0].actionId, "music.previous", "controls[0] should be music.previous");
assert.equal(overviewSnapshot.music.controls[1].actionId, "music.next", "controls[1] should be music.next");
assert.equal(overviewSnapshot.music.controls[2].actionId, "music.pause", "controls[2] should be music.pause");
assert.equal(overviewSnapshot.music.controls[3].actionId, "music.stop", "controls[3] should be music.stop");
assert.equal(overviewSnapshot.music.controls[4].actionId, "music.clear", "controls[4] should be music.clear");
assert.equal(overviewSnapshot.music.controls[0].label, "上一首", "controls[0] label should be preserved");
assert.equal(overviewSnapshot.music.controls[4].label, "清空", "controls[4] label should be preserved");

// quickActions remain unchanged
assert.equal(overviewSnapshot.quickActions[0].commandId, "chat.new", "quickActions[0] should be chat.new");
assert.equal(overviewSnapshot.quickActions[1].commandId, "chat.stop", "quickActions[1] should be chat.stop");
assert.equal(overviewSnapshot.quickActions[2].commandId, "workspace.open", "quickActions[2] should be workspace.open");

// overview sensing toggles reuse the perception action bridge
assert.equal(overviewSnapshot.sense.toggles[0].id, "activeWindow", "overview sense[0] should identify activeWindow");
assert.equal(overviewSnapshot.sense.toggles[0].actionId, "perception.desktopContext.setEnabled", "overview sense[0] should bridge desktop context");
assert.equal(overviewSnapshot.sense.toggles[1].id, "clipboard", "overview sense[1] should identify clipboard");
assert.equal(overviewSnapshot.sense.toggles[1].actionId, "perception.clipboardContext.setEnabled", "overview sense[1] should bridge clipboard");
assert.equal(overviewSnapshot.sense.toggles[2].id, "screen", "overview sense[2] should identify screen vision");
assert.equal(overviewSnapshot.sense.toggles[2].actionId, "perception.screenVision.setEnabled", "overview sense[2] should bridge screen vision");
assert.equal(overviewSnapshot.sense.toggles[3].id, "proactive", "overview sense[3] should identify proactive wake");
assert.equal(overviewSnapshot.sense.toggles[3].actionId, "perception.proactiveWake.setEnabled", "overview sense[3] should bridge proactive wake");

const runtimeOverviewSenseSnapshot = createControlCenterSnapshot({
  ...mockData,
  sourceKind: "backend",
  overviewRuntime: {
    sense: {
      activeWindowEnabled: false,
      clipboardEnabled: true,
      screenVisionEnabled: false,
      proactiveWakeEnabled: true,
      note: "运行时感知提示"
    }
  }
}).pages.overview;

assert.equal(runtimeOverviewSenseSnapshot.sense.toggles[0].enabled, false, "overview sense activeWindow should patch from runtime");
assert.equal(runtimeOverviewSenseSnapshot.sense.toggles[1].enabled, true, "overview sense clipboard should patch from runtime");
assert.equal(runtimeOverviewSenseSnapshot.sense.toggles[2].enabled, false, "overview sense screen should patch from runtime");
assert.equal(runtimeOverviewSenseSnapshot.sense.toggles[3].enabled, true, "overview sense proactive should patch from runtime");
assert.equal(runtimeOverviewSenseSnapshot.sense.note, "运行时感知提示", "overview sense note should patch from runtime");

const customOverviewSenseSnapshot = createControlCenterSnapshot({
  ...mockData,
  overviewPage: {
    ...mockData.overviewPage,
    sense: {
      ...mockData.overviewPage.sense,
      toggles: [{ label: "自定义感知", id: "customSense", actionId: "custom.sense.toggle", enabled: false, icon: "eye" }]
    }
  },
  sourceKind: "mock"
}).pages.overview;

assert.equal(customOverviewSenseSnapshot.sense.toggles.length, 1, "custom overview sense should preserve row count");
assert.equal(customOverviewSenseSnapshot.sense.toggles[0].id, "customSense", "custom overview sense id should be preserved");
assert.equal(customOverviewSenseSnapshot.sense.toggles[0].actionId, "custom.sense.toggle", "custom overview sense actionId should be preserved");
assert.equal(customOverviewSenseSnapshot.sense.toggles[0].enabled, false, "custom overview sense enabled should be preserved");

// overview voice rows reuse the voice action bridge
assert.equal(overviewSnapshot.voice.rows[0].id, "ttsEnabled", "overview voice row[0] should identify TTS");
assert.equal(overviewSnapshot.voice.rows[0].actionId, "voice.setTtsEnabled", "overview voice row[0] should bridge TTS");
assert.equal(overviewSnapshot.voice.rows[1].id, "asrEnabled", "overview voice row[1] should identify ASR");
assert.equal(overviewSnapshot.voice.rows[1].actionId, "voice.setAsrEnabled", "overview voice row[1] should bridge ASR");

const runtimeOverviewVoiceSnapshot = createControlCenterSnapshot({
  ...mockData,
  sourceKind: "backend",
  overviewRuntime: {
    voice: {
      ttsEnabled: false,
      asrEnabled: true,
      status: "语音状态：运行时"
    }
  }
}).pages.overview;

assert.equal(runtimeOverviewVoiceSnapshot.voice.rows[0].enabled, false, "overview voice TTS should patch from runtime");
assert.equal(runtimeOverviewVoiceSnapshot.voice.rows[1].enabled, true, "overview voice ASR should patch from runtime");
assert.equal(runtimeOverviewVoiceSnapshot.voice.status, "语音状态：运行时", "overview voice status should patch from runtime");

const customOverviewVoiceSnapshot = createControlCenterSnapshot({
  ...mockData,
  overviewPage: {
    ...mockData.overviewPage,
    voice: {
      ...mockData.overviewPage.voice,
      rows: [{ label: "自定义语音", id: "customVoice", actionId: "custom.voice.toggle", enabled: true }]
    }
  },
  sourceKind: "mock"
}).pages.overview;

assert.equal(customOverviewVoiceSnapshot.voice.rows[0].id, "customVoice", "custom overview voice id should be preserved");
assert.equal(customOverviewVoiceSnapshot.voice.rows[0].actionId, "custom.voice.toggle", "custom overview voice actionId should be preserved");

// object input with custom actionId must be preserved
const customOverviewSnapshot = createControlCenterSnapshot({
  ...mockData,
  overviewPage: {
    ...mockData.overviewPage,
    music: {
      ...mockData.overviewPage.music,
      controls: [{ label: "自定义", actionId: "custom.action", icon: "custom-icon" }]
    }
  },
  sourceKind: "mock"
}).pages.overview;

assert.equal(customOverviewSnapshot.music.controls.length, 1, "custom controls should have 1 item");
assert.equal(customOverviewSnapshot.music.controls[0].actionId, "custom.action", "custom actionId should be preserved");
assert.equal(customOverviewSnapshot.music.controls[0].label, "自定义", "custom label should be preserved");
assert.equal(customOverviewSnapshot.music.controls[0].icon, "custom-icon", "custom control fields should be preserved");

const runtimeMusicContractSnapshot = createControlCenterSnapshot({
  ...mockData,
  sourceKind: "backend",
  musicRuntime: {
    currentPlayMode: "单曲循环",
    outputDevice: "耳机",
    volumeNormalization: false,
    recommendations: []
  }
}).pages.music;

assert.equal(runtimeMusicContractSnapshot.currentPlayMode, "单曲循环", "runtime music currentPlayMode should patch");
assert.equal(runtimeMusicContractSnapshot.outputDevice, "耳机", "runtime music outputDevice should patch");
assert.equal(runtimeMusicContractSnapshot.volumeNormalization, false, "runtime music volumeNormalization should patch");
assert.deepEqual(runtimeMusicContractSnapshot.recommendations, [], "runtime music empty recommendations should override mock recommendations");

// ---------- snapshot endpoint fallback ----------

// Valid snapshot: readSnapshot should use it (uses a fetch that returns all healthy data)
{
  let snapshotUrl = "";
  const okSource = createBackendControlCenterSource({
    baseUrl: "http://snapshot-test",
    sessionId: "desktop-session",
    profileUserId: "master-user",
    characterPackId: "akane_pack",
    outfit: "cat",
    emotion: "happy",
    fetchImpl: async (url) => {
      const requestUrl = String(url);
      if (requestUrl.includes("/control-center/snapshot")) {
        snapshotUrl = requestUrl;
      }
      if (requestUrl.includes("/capabilities")) {
        return { ok: false, status: 404, headers: { get: () => "" } };
      }
      return {
        ok: true,
        status: 200,
        headers: { get: () => "application/json" },
        json: async () => ({
          ok: true,
          status: "available",
          schemaVersion: 1,
          sourceKind: "backend",
          generatedAt: new Date().toISOString(),
          runtime: {
            health: { status: "ok", contracts: { desktop_pet: { tts: true } } },
            diagnostics: { status: "ok", capabilities: { tool_names: ["read_file"] }, runtime: { metrics: {} } },
            workspace: { counts: { files: 1, outputs: 0, tasks: 0 } },
            resourceManifest: { schema_version: 1, clients: { desktop_pet: {} }, characters: { outfits: [] } },
            metrics: "cpu_percent 12\nmemory_percent 38"
          }
        })
      };
    }
  });
  const result = await okSource.readSnapshot();
  assert.ok(result, "snapshot with valid runtime should return data");
  assert.ok(snapshotUrl.includes("/control-center/snapshot"), "snapshot endpoint should be requested first");
  assert.ok(snapshotUrl.includes("user_id=desktop-session"), "snapshot endpoint should receive session id");
  assert.ok(snapshotUrl.includes("real_user_id=master-user"), "snapshot endpoint should receive profile user id");
  assert.ok(snapshotUrl.includes("character_pack_id=akane_pack"), "snapshot endpoint should receive character pack id");
  assert.ok(snapshotUrl.includes("outfit=cat"), "snapshot endpoint should receive outfit");
  assert.ok(snapshotUrl.includes("emotion=happy"), "snapshot endpoint should receive emotion");
  assert.equal(result.sourceKind, "backend", "snapshot path should set sourceKind backend");
  assert.ok(result.overviewRuntime, "snapshot path should produce overviewRuntime");
  assert.equal(result.advancedRuntime.systemStrip.CPU.value, "12%", "snapshot metrics text should feed advanced CPU value");
  assert.equal(result.advancedRuntime.systemStrip["内存"].value, "38%", "snapshot metrics text should feed advanced memory value");
}

// Valid snapshot with wrapped field data: readSnapshot should unwrap data fields.
{
  const wrappedSource = createBackendControlCenterSource({
    baseUrl: "http://snapshot-wrapped-test",
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      headers: { get: () => "application/json" },
      json: async () => ({
        ok: true,
        status: "available",
        schemaVersion: 1,
        sourceKind: "backend",
        generatedAt: new Date().toISOString(),
        runtime: {
          health: { ok: true, data: { status: "ok" } },
          diagnostics: { ok: true, data: { status: "ok", capabilities: { tool_names: [] }, runtime: { metrics: {} } } },
          workspace: { ok: true, data: { counts: { files: 0, outputs: 0, tasks: 0 } } },
          resourceManifest: { ok: false, status: "unavailable" },
          metrics: { ok: true, data: "cpu_percent 7\nmemory_percent 9" }
        }
      })
    })
  });
  const result = await wrappedSource.readSnapshot();
  assert.ok(result, "snapshot with wrapped field data should return data");
  assert.equal(result.advancedRuntime.systemStrip.CPU.value, "7%", "wrapped metrics should feed advanced CPU value");
}

// Snapshot 404: should fall back to null (no individual endpoints succeed either)
{
  const emptySource = createBackendControlCenterSource({
    baseUrl: "http://snapshot-fallback-test",
    fetchImpl: async () => ({
      ok: false,
      status: 404,
      headers: { get: () => "" }
    })
  });
  const result = await emptySource.readSnapshot();
  assert.equal(result, null, "snapshot 404 should fall back and return null when all fallbacks also fail");
}

// Snapshot bad structure (runtime missing): fallback (also ensure individual endpoints fail)
{
  let snapshotAttempted = false;
  const badSource = createBackendControlCenterSource({
    baseUrl: "http://snapshot-bad-test",
    fetchImpl: async (url) => {
      if (url.includes("/control-center/snapshot")) {
        snapshotAttempted = true;
        return { ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => ({ ok: true, runtime: null }) };
      }
      // Make individual endpoints fail so fallback returns null
      return { ok: false, status: 404, headers: { get: () => "" } };
    }
  });
  const result = await badSource.readSnapshot();
  assert.ok(snapshotAttempted, "snapshot endpoint should be attempted");
  assert.equal(result, null, "snapshot with bad runtime should fall back");
}

// Snapshot bad contract metadata or missing runtime fields: fallback.
{
  const badContractSource = createBackendControlCenterSource({
    baseUrl: "http://snapshot-bad-contract-test",
    fetchImpl: async (url) => {
      if (url.includes("/control-center/snapshot")) {
        return {
          ok: true,
          status: 200,
          headers: { get: () => "application/json" },
          json: async () => ({
            ok: true,
            status: "available",
            schemaVersion: 1,
            sourceKind: "other",
            generatedAt: new Date().toISOString(),
            runtime: { health: { status: "ok" } }
          })
        };
      }
      return { ok: false, status: 404, headers: { get: () => "" } };
    }
  });
  const result = await badContractSource.readSnapshot();
  assert.equal(result, null, "snapshot with bad metadata or missing fields should fall back");
}

// Snapshot all-unavailable: should fall back (no sub-field has ok:true), individual endpoints also fail
{
  const allUnavailableResponse = {
    ok: true, status: 200, headers: { get: () => "application/json" },
    json: async () => ({
      ok: true,
      status: "available",
      schemaVersion: 1,
      sourceKind: "backend",
      generatedAt: new Date().toISOString(),
      runtime: {
        health: { ok: false, status: "unavailable" },
        diagnostics: { ok: false, status: "unavailable" },
        workspace: { ok: false, status: "unavailable" },
        resourceManifest: { ok: false, status: "unavailable" },
        metrics: { ok: false, status: "unavailable" }
      }
    })
  };
  const unavailableSource = createBackendControlCenterSource({
    baseUrl: "http://snapshot-unavail-test",
    fetchImpl: async (url) => {
      if (url.includes("/control-center/snapshot")) return allUnavailableResponse;
      return { ok: false, status: 404, headers: { get: () => "" } };
    }
  });
  const result = await unavailableSource.readSnapshot();
  assert.equal(result, null, "snapshot all-unavailable should fall back");
}

// ---------- character resources runtime patch ----------

{
  const runtimeResourcesSnapshot = createControlCenterSnapshot({
    ...mockData,
    sourceKind: "backend",
    characterRuntime: {
      resources: [
        { label: "动作资源", value: "45 / 50", tone: "blue" },
        { label: "表情资源", value: "12 / 12", tone: "green" },
        { label: "服装资源", value: "3 / 3", tone: "pink" },
        { label: "背景资源", value: "5 / 5", tone: "green" }
      ]
    }
  }).pages.character;

  assert.equal(runtimeResourcesSnapshot.resources.length, 4, "runtime character resources should have 4 items");
  assert.equal(runtimeResourcesSnapshot.resources[0].value, "45 / 50", "runtime character resources[0] should patch action resource count");
  assert.equal(runtimeResourcesSnapshot.resources[1].value, "12 / 12", "runtime character resources[1] should patch emotion resource count");
  assert.equal(runtimeResourcesSnapshot.resources[2].value, "3 / 3", "runtime character resources[2] should patch outfit resource count");
  assert.equal(runtimeResourcesSnapshot.resources[3].value, "5 / 5", "runtime character resources[3] should patch background resource count");
}

// ---------- character runtime consistency: pack selector, outfit, emotion payloads ----------

{
  // Runtime data with explicit selectedPackId, outfits, emotions
  const charConsistencySnapshot = createControlCenterSnapshot({
    ...mockData,
    sourceKind: "backend",
    characterRuntime: {
      selectedPackId: "runtime_pack",
      selectedPack: "Runtime Pack Display",
      outfits: [
        { id: "runtime_default", name: "Runtime Default", current: true, image: "happy" },
        { id: "runtime_alt", name: "Runtime Alt", image: "shy" }
      ],
      emotions: [
        { id: "runtime_happy", name: "Runtime Happy", current: true, image: "happy" },
        { id: "runtime_shy", name: "Runtime Shy", image: "shy" }
      ]
    }
  }).pages.character;

  // Pack selector payload uses runtime selectedPackId
  assert.equal(charConsistencySnapshot.selectedPackId, "runtime_pack", "char consistency: selectedPackId should be runtime_pack");
  const packDataset = { payloadField: "packId", payloadValue: charConsistencySnapshot.selectedPackId, payloadPackId: charConsistencySnapshot.selectedPackId };
  const packPayload = createControlCenterActionPayloadFromDataset(packDataset, "character");
  assert.equal(packPayload.value, "runtime_pack", "char consistency: pack selector payload.value should use runtime_pack");
  assert.equal(packPayload.packId, "runtime_pack", "char consistency: pack selector payload.packId should use runtime_pack");

  // Outfit tile data uses runtime outfit IDs (not mock "default")
  assert.equal(charConsistencySnapshot.outfits[0].id, "runtime_default", "char consistency: outfit[0] id should be runtime_default");
  assert.equal(charConsistencySnapshot.outfits[1].id, "runtime_alt", "char consistency: outfit[1] id should be runtime_alt");
  // Simulate state sync: active outfit picks runtime id
  const activeOutfit = charConsistencySnapshot.outfits.find((o) => o.current)?.id || charConsistencySnapshot.outfits[0]?.id;
  assert.equal(activeOutfit, "runtime_default", "char consistency: active outfit should be runtime_default");
  // Mock ID should NOT appear
  assert.equal(charConsistencySnapshot.outfits.some((o) => o.id === "default"), false, "char consistency: mock outfit id 'default' should not appear");

  // Emotion tile data uses runtime emotion IDs (not mock "smile")
  assert.equal(charConsistencySnapshot.emotions[0].id, "runtime_happy", "char consistency: emotion[0] id should be runtime_happy");
  assert.equal(charConsistencySnapshot.emotions[1].id, "runtime_shy", "char consistency: emotion[1] id should be runtime_shy");
  // Simulate state sync: active emotion picks runtime id
  const activeEmotion = charConsistencySnapshot.emotions.find((e) => e.current)?.id || charConsistencySnapshot.emotions[0]?.id;
  assert.equal(activeEmotion, "runtime_happy", "char consistency: active emotion should be runtime_happy");
  // Mock ID should NOT appear
  assert.equal(charConsistencySnapshot.emotions.some((e) => e.id === "smile"), false, "char consistency: mock emotion id 'smile' should not appear");
}

// ---------- abilities modules from tool names, no raw tool IDs ----------

{
  const toolNamesSnapshot = createControlCenterSnapshot({
    ...mockData,
    sourceKind: "backend",
    abilitiesRuntime: {
      modules: [
        { title: "文件处理", description: "读取与整理本地文件", permission: "受限访问", count: "3 项能力", tone: "blue", icon: "folder" },
        { title: "媒体工具", description: "处理音频与视频", permission: "多媒体操作", count: "5 项能力", tone: "green", icon: "play" }
      ]
    }
  }).pages.abilities;

  assert.equal(toolNamesSnapshot.modules.length, 2, "runtime abilities modules should have 2 items");
  assert.equal(toolNamesSnapshot.modules[0].title, "文件处理", "runtime modules[0] title should be user-friendly");
  assert.equal(toolNamesSnapshot.modules[1].title, "媒体工具", "runtime modules[1] title should be user-friendly");
  // Verify no raw tool IDs leak into module titles or descriptions
  const allModuleText = JSON.stringify(toolNamesSnapshot.modules);
  assert.equal(allModuleText.includes("read_file"), false, "runtime module text should not contain raw tool names like read_file");
}

// ---------- field degradation: one field unavailable, others still produce patches ----------

{
  const degradedSource = createBackendControlCenterSource({
    baseUrl: "http://degraded-test",
    sessionId: "degraded-session",
    fetchImpl: async (url) => {
      if (url.includes("/control-center/snapshot")) {
        return {
          ok: true, status: 200, headers: { get: () => "application/json" },
          json: async () => ({
            ok: true, schemaVersion: 1, sourceKind: "backend",
            generatedAt: new Date().toISOString(),
            runtime: {
              health: { ok: false, status: "unavailable" },
              diagnostics: { status: "ok", capabilities: { tool_names: ["read_file", "send_file"] }, runtime: { metrics: { request_duration_ms: 42 } } },
              workspace: { counts: { files: 3, outputs: 1, tasks: 0 } },
              resourceManifest: { schema_version: 1, clients: { desktop_pet: {} }, characters: { outfits: [] } },
              metrics: "cpu_percent 15\nmemory_percent 42"
            }
          })
        };
      }
      return { ok: false, status: 404, headers: { get: () => "" } };
    }
  });
  const degradedResult = await degradedSource.readSnapshot();
  assert.ok(degradedResult, "degraded snapshot (health unavailable) should still return data");

  // health unavailable → overview health should still have basic structure from other sources
  assert.ok(degradedResult.overviewRuntime?.health, "degraded snapshot should still produce overviewRuntime health");
  assert.ok(degradedResult.overviewRuntime?.health["CPU 占用"], "degraded overview CPU tile should exist");
  // diagnostics available → abilities runtime from diagnostics
  assert.ok(degradedResult.abilitiesRuntime?.overview, "degraded snapshot should still produce abilitiesRuntime from diagnostics");
  // metrics available → advanced runtime from metrics
  assert.equal(degradedResult.advancedRuntime.systemStrip.CPU.value, "15%", "degraded snapshot advanced CPU should come from metrics");
  assert.equal(degradedResult.advancedRuntime.systemStrip["内存"].value, "42%", "degraded snapshot advanced memory should come from metrics");
  // resourceManifest available → character runtime
  assert.ok(degradedResult.characterRuntime, "degraded snapshot should still produce characterRuntime from resourceManifest");
  // sourceKind is backend, not fallback mock
  assert.equal(degradedResult.sourceKind, "backend", "degraded snapshot should keep backend sourceKind");
}

// ---------- abilities modules from diagnostics tool names ----------

{
  const abilitiesFromTools = createControlCenterSnapshot({
    ...mockData,
    sourceKind: "backend",
    abilitiesRuntime: {
      modules: [
        { title: "文件处理", description: "文档读写与转换", permission: "读写文件", count: "4 项能力", tone: "blue", icon: "folder" },
        { title: "生成文件交付", description: "生成并交付文档", permission: "生成与导出", count: "2 项能力", tone: "purple", icon: "file" },
        { title: "媒体工具", description: "音视频播放与转写", permission: "多媒体操作", count: "3 项能力", tone: "green", icon: "play" },
        { title: "安全边界", description: "限制危险操作", permission: "安全与隔离", count: "2 项能力", tone: "pink", icon: "shield" }
      ]
    }
  }).pages.abilities;

  assert.equal(abilitiesFromTools.modules.length, 4, "abilities modules from tool names should have 4 items");
  // All module descriptions should be user-facing, not raw tool IDs
  for (const mod of abilitiesFromTools.modules) {
    assert.ok(!mod.description.includes("_"), `${mod.title} description should not contain raw identifiers`);
    assert.ok(typeof mod.title === "string" && mod.title.length > 0, "module title should be a non-empty string");
  }
}

// ---------- advanced metrics from metrics text ----------

{
  const metricsTextSnapshot = createControlCenterSnapshot({
    ...mockData,
    sourceKind: "backend",
    advancedRuntime: {
      // diagnostics.metrics must be an object keyed by label (as buildAdvancedRuntimePatch produces)
      diagnostics: {
        metrics: {
          "应用状态": { value: "已连接", tone: "green" },
          "后端健康": { value: "良好", tone: "green" },
          "内存占用": { value: "256 MB" }
        }
      },
      systemStrip: {
        "CPU": { value: "22%" },
        "内存": { value: "55%" }
      }
    }
  }).pages.advanced;

  // diagnostics.metrics: rows patched by label — matching labels get runtime values
  assert.equal(metricsTextSnapshot.diagnostics.metrics[0].value, "已连接", "advanced metrics[0] app status should be runtime-patched");
  assert.equal(metricsTextSnapshot.diagnostics.metrics[1].value, "良好", "advanced metrics[1] backend health should be runtime-patched");
  // "帧率 (FPS)" has no runtime patch → mock value passes through
  assert.equal(metricsTextSnapshot.diagnostics.metrics[2].value, "60", "advanced metrics[2] FPS should stay at mock value when runtime does not patch it");
  assert.equal(metricsTextSnapshot.diagnostics.metrics[3].value, "256 MB", "advanced metrics[3] memory should be runtime-patched by label");
  // systemStrip patched by label as well
  assert.equal(metricsTextSnapshot.systemStrip[1].value, "22%", "advanced systemStrip CPU should be runtime-patched");
  assert.equal(metricsTextSnapshot.systemStrip[2].value, "55%", "advanced systemStrip memory should be runtime-patched");
}

// ---------- client-handled actions (local UI handlers) ----------

const clientHandledActionIds = new Set(CONTROL_CENTER_CLIENT_HANDLED_ACTION_IDS);

// Client-handled action surface contract: all local UI handlers should be in the surface contract.
{
  const clientHandledSurfaces = listControlCenterActionSurfaces(CONTROL_CENTER_ACTION_SURFACE_STATUS.clientHandled);
  assert.equal(clientHandledSurfaces.length, CONTROL_CENTER_CLIENT_HANDLED_ACTION_IDS.length, "client-handled surface count should match catalog");
  for (const actionId of CONTROL_CENTER_CLIENT_HANDLED_ACTION_IDS) {
    const surface = clientHandledSurfaces.find((s) => s.actionId === actionId);
    assert.ok(surface, `${actionId} should appear in client-handled surfaces`);
    assert.equal(surface.bridged, false, `${actionId} should not be bridged`);
    assert.equal(typeof surface.description, "string", `${actionId} should have a description`);
  }
}

// Client-handled actions are NOT in bridged action ids
for (const actionId of CONTROL_CENTER_CLIENT_HANDLED_ACTION_IDS) {
  assert.equal(isControlCenterBridgedAction(actionId), false, `${actionId} should NOT be a bridged action`);
}

// Client-handled actions are NOT in deferred surfaces
{
  const deferredSurfaceIds = new Set(
    listControlCenterActionSurfaces(CONTROL_CENTER_ACTION_SURFACE_STATUS.deferred).map((s) => s.actionId)
  );
  for (const actionId of CONTROL_CENTER_CLIENT_HANDLED_ACTION_IDS) {
    assert.equal(deferredSurfaceIds.has(actionId), false, `${actionId} should NOT be in deferred surfaces`);
  }
}

// Client-handled actions through router with registered handlers: refresh:false, no emit/invoke
{
  const beforeEmitLen = emitLog.length;
  const beforeInvokeLen = invokeLog.length;
  const handlerLog = [];
  const chRouter = createControlCenterActionRouter({ dataSource });
  chRouter.registerHandlers({
    [CONTROL_CENTER_ACTIONS.perceptionActiveWindowDetails]: (payload) => {
      handlerLog.push({ actionId: CONTROL_CENTER_ACTIONS.perceptionActiveWindowDetails, payload });
      return { ok: true, refresh: false };
    },
    [CONTROL_CENTER_ACTIONS.abilitiesLogsViewAll]: (payload) => {
      handlerLog.push({ actionId: CONTROL_CENTER_ACTIONS.abilitiesLogsViewAll, payload });
      return { ok: true, refresh: false };
    },
    [CONTROL_CENTER_ACTIONS.abilitiesProviderConfigOpen]: (payload) => {
      handlerLog.push({ actionId: CONTROL_CENTER_ACTIONS.abilitiesProviderConfigOpen, payload });
      return { ok: true, refresh: false };
    },
    [CONTROL_CENTER_ACTIONS.abilitiesMcpConfigOpen]: (payload) => {
      handlerLog.push({ actionId: CONTROL_CENTER_ACTIONS.abilitiesMcpConfigOpen, payload });
      return { ok: true, refresh: false };
    },
    [CONTROL_CENTER_ACTIONS.abilitiesWorkflowConfigOpen]: (payload) => {
      handlerLog.push({ actionId: CONTROL_CENTER_ACTIONS.abilitiesWorkflowConfigOpen, payload });
      return { ok: true, refresh: false };
    },
    [CONTROL_CENTER_ACTIONS.advancedLogsMore]: (payload) => {
      handlerLog.push({ actionId: CONTROL_CENTER_ACTIONS.advancedLogsMore, payload });
      return { ok: true, refresh: false };
    }
  });

  const chCases = [
    { id: CONTROL_CENTER_ACTIONS.perceptionActiveWindowDetails, payload: { featureId: "activeWindow" } },
    { id: CONTROL_CENTER_ACTIONS.abilitiesLogsViewAll, payload: { page: "abilities" } },
    { id: CONTROL_CENTER_ACTIONS.abilitiesProviderConfigOpen, payload: { page: "abilities", providerId: "provider.comfyui.local" } },
    { id: CONTROL_CENTER_ACTIONS.abilitiesMcpConfigOpen, payload: { page: "abilities", serverId: "browser" } },
    { id: CONTROL_CENTER_ACTIONS.abilitiesWorkflowConfigOpen, payload: { page: "abilities", workflowId: "workflow.workshop.portrait.cutout" } },
    { id: CONTROL_CENTER_ACTIONS.advancedLogsMore, payload: { page: "advanced" } }
  ];

  for (const testCase of chCases) {
    const result = await chRouter.run(testCase.id, testCase.payload, { source: "smoke" });
    assert.equal(result.ok, true, `${testCase.id} should be handled locally`);
    assert.equal(result.refresh, false, `${testCase.id} should have refresh:false`);
    assert.notEqual(result.status, "not-implemented", `${testCase.id} should not be not-implemented`);
    assert.notEqual(result.status, "failed", `${testCase.id} should not be failed`);
  }

  assert.equal(handlerLog.length, chCases.length, "all client-handled handlers should be called");
  assert.equal(emitLog.length, beforeEmitLen, "client-handled actions should NOT emit");
  assert.equal(invokeLog.length, beforeInvokeLen, "client-handled actions should NOT invoke");
}

// Client-handled actions through dataSource directly: still not-implemented (no real boundary)
for (const actionId of CONTROL_CENTER_CLIENT_HANDLED_ACTION_IDS) {
  const result = await dataSource.runAction(actionId, {}, { source: "smoke" });
  assert.deepEqual(
    { ok: result.ok, status: result.status, actionId: result.actionId, refresh: result.refresh },
    { ok: false, status: "not-implemented", actionId, refresh: false },
    `${actionId} dataSource should remain not-implemented`
  );
}

// Client-handled action router without registered handler: should still work (not-implemented from dataSource)
{
  const bareRouter = createControlCenterActionRouter({ dataSource });
  for (const actionId of CONTROL_CENTER_CLIENT_HANDLED_ACTION_IDS) {
    const result = await bareRouter.run(actionId, {}, { source: "smoke" });
    assert.equal(result.status, "not-implemented", `${actionId} without handler should be not-implemented`);
    assert.equal(result.refresh, false, `${actionId} without handler should have refresh:false`);
  }
}

// ---------- display-only actions: voiceSetAsrSensitivity and musicSelectOutputDevice ----------

// These remain deferred in surface contract but do NOT have data-action-id in the UI.
// Data source still returns not-implemented.
{
  const displayOnlyIds = [
    CONTROL_CENTER_ACTIONS.voiceSetAsrSensitivity,
    CONTROL_CENTER_ACTIONS.musicSelectOutputDevice
  ];
  for (const actionId of displayOnlyIds) {
    assert.equal(isControlCenterBridgedAction(actionId), false, `${actionId} should NOT be bridged`);
    const result = await dataSource.runAction(actionId);
    assert.equal(result.status, "not-implemented", `${actionId} dataSource should be not-implemented`);
    // They remain in deferred surfaces
    const deferredIds = new Set(
      listControlCenterActionSurfaces(CONTROL_CENTER_ACTION_SURFACE_STATUS.deferred).map((s) => s.actionId)
    );
    assert.equal(deferredIds.has(actionId), true, `${actionId} should appear in deferred surfaces`);
  }
}

// ---------- all action IDs are covered by the surface contract ----------
//
// Every action ID constant in CONTROL_CENTER_ACTIONS must appear in the surface
// contract as either bridged, client-handled, or deferred. There must be no
// "unclassified" action IDs that have no documented status. Some action IDs
// intentionally appear on multiple pages, so duplicate surface entries are OK
// as long as they stay in a single status category.

{
  const allActionIds = Object.values(CONTROL_CENTER_ACTIONS);
  const allSurfaces = listControlCenterActionSurfaces();
  const allSurfaceIds = new Set(allSurfaces.map((s) => s.actionId));
  const unclassified = allActionIds.filter((id) => !allSurfaceIds.has(id));
  assert.equal(
    unclassified.length, 0,
    `all CONTROL_CENTER_ACTIONS must be in surface contract; unclassified: ${unclassified.join(", ")}`
  );

  for (const actionId of allActionIds) {
    const statuses = new Set(allSurfaces.filter((s) => s.actionId === actionId).map((s) => s.status));
    assert.equal(
      statuses.size,
      1,
      `${actionId} must map to exactly one surface status category; got ${Array.from(statuses).join(", ")}`
    );
  }
}

// ---------- forbidden actions remain not-implemented ----------

const forbiddenActionIds = [
  CONTROL_CENTER_ACTIONS.musicSetMood,
  CONTROL_CENTER_ACTIONS.musicRefreshRecommendations,
  CONTROL_CENTER_ACTIONS.advancedExitPet
];

for (const actionId of forbiddenActionIds) {
  assert.equal(isControlCenterBridgedAction(actionId), false, `${actionId} should NOT be bridged`);
  const dsResult = await dataSource.runAction(actionId, {}, { source: "smoke" });
  assert.equal(dsResult.status, "not-implemented", `${actionId} dataSource should be not-implemented`);
  assert.equal(dsResult.refresh, false, `${actionId} dataSource should have refresh:false`);
  const rResult = await router.run(actionId, {}, { source: "smoke" });
  assert.equal(rResult.status, "not-implemented", `${actionId} router should be not-implemented`);
  assert.equal(rResult.refresh, false, `${actionId} router should have refresh:false`);
}

// advanced.exitPet is NOT window.close and NOT closePet
assert.notEqual(
  CONTROL_CENTER_ACTIONS.advancedExitPet,
  CONTROL_CENTER_ACTIONS.windowClose,
  "advanced.exitPet must not equal window.close"
);
// Verify exitPet's dataSource runAction doesn't trigger close_window invoke
{
  const beforeExitInvokeLen = invokeLog.length;
  await dataSource.runAction(CONTROL_CENTER_ACTIONS.advancedExitPet);
  const closeWindowInvoke = invokeLog.slice(beforeExitInvokeLen).find((e) => e.command === "close_window");
  assert.equal(closeWindowInvoke, undefined, "advanced.exitPet must not invoke close_window");
}

console.log(
  `control-center action bridge smoke passed: ${CONTROL_CENTER_BRIDGED_ACTION_IDS.length} bridged action ids, ${bridgedActionCases.length} tauri bridge cases, ` +
    `${directNotImplementedCases.length} not-implemented checks, ${labelCases.length} interval labels, ` +
    "4 hardening checks, 4 window action checks, 3 voice setting checks, 1 character preview action check, 5 advanced action checks, " +
    `${deferredSurfaces.length} deferred surface checks, 2 character warning checks, 3 screen vision control checks, ` +
    "8 data-* payload helper checks, " +
    "6 overview music control checks, 9 overview voice checks, 17 overview sense checks, " +
    `${deferredVoiceActionIds.length} deferred voice action ids, ` +
    `${deferredMusicActionIds.length} deferred music action ids, 4 playlist id checks, 9 payload shape checks, 4 voice/music contract field checks, ` +
    `${deferredCharacterActionIds.length} deferred character action checks, 3 char payload/fallback checks, 3 still-deferred checks, ` +
    `${deferredPerceptionActionIds.length} deferred perception action ids, ${deferredAbilitiesActionIds.length} deferred abilities action checks, ` +
    "4 perception/abilities payload checks, " +
    "6 deferred advanced action checks, 1 window notify check, 1 exitPet-not-close check, " +
    "4 advanced payload checks, 4 advanced adapter checks, 5 still-bridged advanced checks, " +
    "2 snapshot valid path checks, 4 snapshot fallback checks, " +
    "4 character resources checks, 13 character consistency checks, 2 abilities modules checks, 4 field degradation checks, " +
    "4 abilities-from-tools checks, 4 advanced metrics checks, " +
    `${CONTROL_CENTER_CLIENT_HANDLED_ACTION_IDS.length} client-handled surface/router/dataSource checks, ` +
    "3 bare-router not-implemented, 2 display-only action checks, 3 forbidden action checks, 1 exitPet safety check"
);
