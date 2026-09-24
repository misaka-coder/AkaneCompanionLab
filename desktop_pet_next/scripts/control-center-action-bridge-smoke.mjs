import assert from "node:assert/strict";

import {
  CONTROL_CENTER_ACTIONS,
  CONTROL_CENTER_BRIDGED_ACTION_IDS,
  createControlCenterActionRouter,
  createNotImplementedActionResult,
  isControlCenterBridgedAction,
} from "../src/control-center/action-router.js";
import { createBackendControlCenterSource } from "../src/control-center/data-sources.js";
import { SETTINGS_COMMAND_EVENT } from "../src/control-center/event-bridge.js";

function jsonResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => "application/json" },
    async json() { return body; },
  };
}

const emitLog = [];
const invokeLog = [];
const windowLog = [];
const source = createBackendControlCenterSource({
  baseUrl: "http://control-center-action-smoke",
  characterPackId: "akane_default",
  tauriBridge: {
    async emit(event, payload) {
      emitLog.push({ event, payload });
    },
    async invoke(command, payload) {
      invokeLog.push({ command, payload });
      if (command === "pick_local_plugin_path") {
        return {
          ok: true,
          status: "selected",
          kind: payload.kind,
          path: payload.kind === "source" ? "C:/plugins/sample" : "C:/plugins/sample.whl",
        };
      }
      if (command === "set_character_voice_profile" || command === "clear_character_voice_profile") {
        return { profile: { identity: { name: "Akane" } } };
      }
      return {};
    },
    window: {
      async minimize() { windowLog.push("minimize"); },
      async toggleMaximize() { windowLog.push("toggleMaximize"); },
    },
  },
});

const afterActionLog = [];
const router = createControlCenterActionRouter({
  dataSource: source,
  onAfterAction(result) { afterActionLog.push(result); },
});

for (const actionId of CONTROL_CENTER_BRIDGED_ACTION_IDS) {
  assert.equal(isControlCenterBridgedAction(actionId), true, `${actionId} should be bridged`);
  assert.equal(source.handlesAction(actionId), true, `${actionId} should be handled by the production source`);
}
assert.equal(new Set(CONTROL_CENTER_BRIDGED_ACTION_IDS).size, CONTROL_CENTER_BRIDGED_ACTION_IDS.length);

const settingsCases = [
  [CONTROL_CENTER_ACTIONS.settingsSelectBot, "setBoundBot", { value: "personal" }],
  [CONTROL_CENTER_ACTIONS.chatNew, "newSession", {}],
  [CONTROL_CENTER_ACTIONS.chatSend, "sendChatMessage", { text: "hello" }],
  [CONTROL_CENTER_ACTIONS.chatStop, "stopReply", {}],
  [CONTROL_CENTER_ACTIONS.voiceTest, "testTts", {}],
  [CONTROL_CENTER_ACTIONS.voiceStop, "stopTts", {}],
  [CONTROL_CENTER_ACTIONS.voiceSetTtsEnabled, "setVoiceEnabled", { value: true }],
  [CONTROL_CENTER_ACTIONS.voiceSetAsrEnabled, "setVoiceInputEnabled", { value: true }],
  [CONTROL_CENTER_ACTIONS.voiceSetVolume, "setVoiceVolume", { value: 80 }],
  [CONTROL_CENTER_ACTIONS.voicePreviewPlay, "previewTts", { text: "试听" }],
  [CONTROL_CENTER_ACTIONS.voiceSetSpeed, "setVoiceSpeed", { value: 1.1 }],
  [CONTROL_CENTER_ACTIONS.voiceSetWakeWord, "setWakeWord", { value: "Akane" }],
  [CONTROL_CENTER_ACTIONS.voiceSetWakeSensitivity, "setWakeSensitivity", { value: "medium" }],
  [CONTROL_CENTER_ACTIONS.characterRefresh, "reloadResources", {}],
  [CONTROL_CENTER_ACTIONS.characterPreviewEmotion, "previewEmotion", { value: "happy" }],
  [CONTROL_CENTER_ACTIONS.characterSelectPack, "setCharacterPack", { value: "akane_default" }],
  [CONTROL_CENTER_ACTIONS.characterSetOutfit, "setOutfit", { value: "default" }],
  [CONTROL_CENTER_ACTIONS.perceptionRunDiagnostics, "requestSnapshot", {}],
  [CONTROL_CENTER_ACTIONS.advancedResetWindow, "resetWindow", {}],
  [CONTROL_CENTER_ACTIONS.advancedSetHitTestEnabled, "setHitTestEnabled", { value: true }],
  [CONTROL_CENTER_ACTIONS.advancedSetHitboxOverlay, "setHitboxOverlay", { value: true }],
];

for (const [actionId, command, payload] of settingsCases) {
  const before = emitLog.length;
  const result = await router.run(actionId, payload, { source: "smoke" });
  assert.equal(result.ok, true, `${actionId} should execute`);
  assert.equal(result.refresh, true, `${actionId} should refresh`);
  assert.equal(emitLog.length, before + 1, `${actionId} should emit once`);
  assert.equal(emitLog.at(-1).event, SETTINGS_COMMAND_EVENT);
  assert.equal(emitLog.at(-1).payload.command, command);
}

for (const [actionId, action] of [
  [CONTROL_CENTER_ACTIONS.musicPrevious, "previous"],
  [CONTROL_CENTER_ACTIONS.musicNext, "next"],
  [CONTROL_CENTER_ACTIONS.musicTogglePlayback, "toggle"],
  [CONTROL_CENTER_ACTIONS.musicStop, "stop"],
]) {
  const result = await router.run(actionId, {}, { source: "smoke" });
  assert.equal(result.ok, true);
  assert.equal(emitLog.at(-1).payload.command, "controlActiveMusic");
  assert.equal(emitLog.at(-1).payload.action, action);
  assert.deepEqual(emitLog.at(-1).payload.value, { action });
}

const invalidWorkspaceAudio = await router.run(CONTROL_CENTER_ACTIONS.musicPlayWorkspaceRecommendation, {});
assert.equal(invalidWorkspaceAudio.status, "invalid-payload");
const validWorkspaceAudio = await router.run(CONTROL_CENTER_ACTIONS.musicPlayWorkspaceRecommendation, {
  itemType: "generated",
  handle: "artifact:music-1",
  title: "Song",
});
assert.equal(validWorkspaceAudio.ok, true);
assert.equal(emitLog.at(-1).payload.command, "playWorkspaceAudio");
assert.deepEqual(emitLog.at(-1).payload.value, {
  itemType: "generated",
  handle: "artifact:music-1",
  title: "Song",
});

for (const [actionId, command] of [
  [CONTROL_CENTER_ACTIONS.workspaceOpen, "open_workspace_window"],
  [CONTROL_CENTER_ACTIONS.characterOpenWorkshop, "open_workshop_window"],
  [CONTROL_CENTER_ACTIONS.characterOpenPackFolder, "open_character_packs_folder"],
  [CONTROL_CENTER_ACTIONS.abilitiesSkillsOpenFolder, "open_managed_skills_folder"],
  [CONTROL_CENTER_ACTIONS.windowClose, "close_window"],
]) {
  const result = await router.run(actionId);
  assert.equal(result.ok, true, `${actionId} should invoke Tauri`);
  assert.equal(invokeLog.at(-1).command, command);
}

const pickedPluginSource = await router.run(CONTROL_CENTER_ACTIONS.abilitiesPluginPickSource);
assert.equal(pickedPluginSource.ok, true);
assert.equal(pickedPluginSource.status, "selected");
assert.equal(pickedPluginSource.path, "C:/plugins/sample");
assert.equal(pickedPluginSource.refresh, false);
assert.deepEqual(invokeLog.at(-1), {
  command: "pick_local_plugin_path",
  payload: { kind: "source" },
});

const pickedPluginWheel = await router.run(CONTROL_CENTER_ACTIONS.abilitiesPluginPickWheel);
assert.equal(pickedPluginWheel.ok, true);
assert.equal(pickedPluginWheel.path, "C:/plugins/sample.whl");
assert.deepEqual(invokeLog.at(-1), {
  command: "pick_local_plugin_path",
  payload: { kind: "wheel" },
});

await router.run(CONTROL_CENTER_ACTIONS.windowMinimize);
await router.run(CONTROL_CENTER_ACTIONS.windowMaximize);
assert.deepEqual(windowLog, ["minimize", "toggleMaximize"]);

const assigned = await router.run(
  CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileAssignToCurrentCharacter,
  { voiceProfileId: "reimu", provider: "gpt_sovits" },
  { source: "smoke" },
);
assert.equal(assigned.status, "assigned");
assert.equal(invokeLog.at(-1).command, "set_character_voice_profile");
assert.equal(invokeLog.at(-1).payload.request.packId, "akane_default");
assert.equal(emitLog.at(-1).payload.command, "refreshCharacterPacks");

const cleared = await router.run(
  CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileClearCurrentCharacter,
  {},
  { source: "smoke" },
);
assert.equal(cleared.status, "cleared");
assert.equal(invokeLog.at(-1).command, "clear_character_voice_profile");

const unknown = await router.run("future.placeholder.action");
assert.deepEqual(unknown, {
  ok: false,
  status: "not-implemented",
  actionId: "future.placeholder.action",
  refresh: false,
});
assert.deepEqual(createNotImplementedActionResult(" x "), {
  ok: false,
  status: "not-implemented",
  actionId: "x",
  refresh: false,
});

const failingRouter = createControlCenterActionRouter({
  dataSource: source,
  handlers: {
    "test.failure"() { throw new Error("expected failure"); },
  },
});
const failure = await failingRouter.run("test.failure");
assert.equal(failure.ok, false);
assert.equal(failure.status, "failed");
assert.match(failure.error, /expected failure/);

const backendRequests = [];
const backendSource = createBackendControlCenterSource({
  baseUrl: "http://control-center-route-smoke",
  fetchImpl: async (url, options = {}) => {
    backendRequests.push({ url: String(url), options });
    return jsonResponse({ ok: true, status: "executed", refresh: true });
  },
});
const backendRouter = createControlCenterActionRouter({ dataSource: backendSource });

await backendRouter.run(CONTROL_CENTER_ACTIONS.abilitiesProviderHealthCheck, { providerId: "edge" });
assert.match(backendRequests.at(-1).url, /\/capabilities\/providers\/edge\/health-check/);
await backendRouter.run(CONTROL_CENTER_ACTIONS.abilitiesMcpRestart, { serverId: "github" });
assert.match(backendRequests.at(-1).url, /\/capabilities\/mcp-servers\/github\/restart/);
await backendRouter.run(CONTROL_CENTER_ACTIONS.abilitiesPluginDisable, { pluginId: "akane.sample.gentle-checkin" });
assert.match(backendRequests.at(-1).url, /\/admin\/plugins\/akane\.sample\.gentle-checkin\/enabled/);
assert.equal(backendRequests.at(-1).options.method, "POST");
assert.deepEqual(JSON.parse(backendRequests.at(-1).options.body), { enabled: false });
await backendRouter.run(CONTROL_CENTER_ACTIONS.abilitiesPluginRollback, { pluginId: "akane.sample.gentle-checkin" });
assert.match(backendRequests.at(-1).url, /\/admin\/plugins\/akane\.sample\.gentle-checkin\/rollback/);
assert.equal(backendRequests.at(-1).options.method, "POST");
assert.deepEqual(JSON.parse(backendRequests.at(-1).options.body), {});
await backendRouter.run(CONTROL_CENTER_ACTIONS.abilitiesPluginConnectionSave, {
  pluginId: "example.http-query", connectionName: "query_api", expectedRevision: 2, enabled: true,
  values: { endpoint: "https://example.test/query", timeout_seconds: 10 }, clearFields: []
});
assert.match(backendRequests.at(-1).url, /\/admin\/plugins\/example\.http-query\/connections\/query_api/);
assert.match(backendRequests.at(-1).url, /profile_user_id=master/);
assert.equal(backendRequests.at(-1).options.method, "PUT");
assert.deepEqual(JSON.parse(backendRequests.at(-1).options.body), {
  expected_revision: 2, enabled: true, values: { endpoint: "https://example.test/query", timeout_seconds: 10 }, clear_fields: []
});
await backendRouter.run(CONTROL_CENTER_ACTIONS.abilitiesPluginStageSource, { path: "C:/work/timer-plugin" });
assert.match(backendRequests.at(-1).url, /\/admin\/plugins\/stages\/source/);
assert.deepEqual(JSON.parse(backendRequests.at(-1).options.body), { source_path: "C:/work/timer-plugin" });
await backendRouter.run(CONTROL_CENTER_ACTIONS.abilitiesPluginStageWheel, { path: "C:/work/timer-plugin.whl" });
assert.match(backendRequests.at(-1).url, /\/admin\/plugins\/stages(?:\?|$)/);
assert.deepEqual(JSON.parse(backendRequests.at(-1).options.body), { wheel_path: "C:/work/timer-plugin.whl" });
await backendRouter.run(CONTROL_CENTER_ACTIONS.abilitiesPluginStageMarket, { pluginId: "akane.media-convert", digest: "a".repeat(64) });
assert.match(backendRequests.at(-1).url, /\/admin\/plugins\/market\/akane\.media-convert\/stage/);
assert.deepEqual(JSON.parse(backendRequests.at(-1).options.body), { digest: "a".repeat(64) });
const beforeInvalidMarket = backendRequests.length;
const invalidMarket = await backendRouter.run(CONTROL_CENTER_ACTIONS.abilitiesPluginStageMarket, { pluginId: "akane.media-convert" });
assert.equal(invalidMarket.ok, false);
assert.equal(backendRequests.length, beforeInvalidMarket);
await backendRouter.run(CONTROL_CENTER_ACTIONS.abilitiesPluginInstall, {
  stageId: "stage-review-1",
  approvedPermissions: ["agent.event.submit", "storage.write"]
});
assert.match(backendRequests.at(-1).url, /\/admin\/plugins\/stages\/stage-review-1\/install/);
assert.deepEqual(JSON.parse(backendRequests.at(-1).options.body), {
  approved_permissions: ["agent.event.submit", "storage.write"]
});
await backendRouter.run(CONTROL_CENTER_ACTIONS.abilitiesPluginDiscardStage, { stageId: "stage-review-1" });
assert.match(backendRequests.at(-1).url, /\/admin\/plugins\/stages\/stage-review-1(?:\?|$)/);
assert.equal(backendRequests.at(-1).options.method, "DELETE");
await backendRouter.run(CONTROL_CENTER_ACTIONS.abilitiesPluginUninstall, { pluginId: "akane.sample.gentle-checkin" });
assert.match(backendRequests.at(-1).url, /\/admin\/plugins\/akane\.sample\.gentle-checkin(?:\?|$)/);
assert.equal(backendRequests.at(-1).options.method, "DELETE");
await backendRouter.run(CONTROL_CENTER_ACTIONS.abilitiesWorkflowValidate, { workflowId: "portrait" });
assert.match(backendRequests.at(-1).url, /\/capabilities\/workflows\/portrait\/validate/);
await backendRouter.run(CONTROL_CENTER_ACTIONS.abilitiesApprovalPolicySave, { familyId: "ops", mode: "trusted_auto_allow" });
assert.match(backendRequests.at(-1).url, /\/capabilities\/approval-policy/);
await backendRouter.run(CONTROL_CENTER_ACTIONS.abilitiesApprovalRequestDecide, {
  requestId: "request-1",
  decision: "approved",
});
assert.match(backendRequests.at(-1).url, /\/capabilities\/approval-requests\/request-1\/decision/);

const failedPluginSource = createBackendControlCenterSource({
  baseUrl: "http://control-center-route-smoke",
  fetchImpl: async () => jsonResponse({
    ok: false,
    status: "activation_failed",
    reason: "plugin_probe_failed",
    rollback_status: "active"
  }, 409)
});
const failedPluginResult = await failedPluginSource.runAction(
  CONTROL_CENTER_ACTIONS.abilitiesPluginEnable,
  { pluginId: "akane.sample.gentle-checkin" }
);
assert.equal(failedPluginResult.ok, false);
assert.equal(failedPluginResult.refresh, true);
assert.equal(failedPluginResult.reason, "插件未能切换，宿主已恢复上一有效版本");

const changedStageSource = createBackendControlCenterSource({
  baseUrl: "http://control-center-route-smoke",
  fetchImpl: async () => jsonResponse({
    ok: false,
    status: "approval_required",
    reason: "plugin_permissions_not_approved"
  }, 409)
});
const changedStageResult = await changedStageSource.runAction(
  CONTROL_CENTER_ACTIONS.abilitiesPluginInstall,
  { stageId: "stage-review-1", approvedPermissions: ["storage.write"] }
);
assert.equal(changedStageResult.ok, false);
assert.equal(changedStageResult.reason, "候选权限已经变化，请重新审查后再安装");

const managementSource = createBackendControlCenterSource({
  baseUrl: "http://control-center-route-smoke",
  expectedInstanceId: "local-default",
  fetchImpl: async (url) => {
    if (String(url).includes("/health")) {
      return jsonResponse({ status: "ok", root_binding: "valid", instance_id: "local-default" });
    }
    return jsonResponse({
      status: "active",
      management: { status: "ready", supports: ["stage_source", "stage_wheel"] },
      artifacts: { status: "ready", stages: [{ stage_id: "stage-1" }] }
    });
  }
});
const managementSnapshot = await managementSource.readPluginManagement();
assert.equal(managementSnapshot.ok, true);
assert.equal(managementSnapshot.data.artifacts.stages[0].stage_id, "stage-1");

const marketRequests = [];
const marketSource = createBackendControlCenterSource({
  baseUrl: "http://control-center-market-smoke", expectedInstanceId: "local-default",
  fetchImpl: async (url) => {
    marketRequests.push(String(url));
    if (String(url).includes("/health")) return jsonResponse({ status: "ok", root_binding: "valid", instance_id: "local-default" });
    return jsonResponse({ ok: true, status: "ready", plugins: [{ plugin_id: "akane.media-convert", sha256: "a".repeat(64) }] });
  }
});
assert.equal((await marketSource.readPluginMarket()).data.plugins[0].plugin_id, "akane.media-convert");
assert.match(marketRequests.at(-1), /\/plugins\/market/);
const failedMarketSource = createBackendControlCenterSource({
  baseUrl: "http://control-center-market-smoke",
  fetchImpl: async () => jsonResponse({ ok: false, status: "conflict", reason: "market_selection_changed" }, 409)
});
const failedMarket = await failedMarketSource.runAction(CONTROL_CENTER_ACTIONS.abilitiesPluginStageMarket,
  { pluginId: "akane.media-convert", digest: "a".repeat(64) });
assert.equal(failedMarket.ok, false);
assert.match(failedMarket.reason, /市场版本已经变化/);

assert.ok(afterActionLog.length >= settingsCases.length);
console.log(
  `control-center action bridge smoke passed: ${CONTROL_CENTER_BRIDGED_ACTION_IDS.length} production actions, ` +
  `${emitLog.length} events, ${invokeLog.length} invokes, ${backendRequests.length} backend routes`,
);
