import assert from "node:assert/strict";
import { test } from "node:test";
import * as facade from "../src/control-center-v2/view-model.js";

const { createControlCenterViewModel: project, isObservedActionConfirmation: confirms } = facade;
const raw = (extra = {}) => ({
  sourceKind: "backend", generatedAt: "2026-10-03T00:00:00Z", backendUrl: "http://127.0.0.1:9999",
  controlCenterV2: { liveSnapshotStatus: "connected" }, ...extra
});
const plugin = (extra = {}) => ({
  plugin_id: "sample.plugin", manageable: true, enabled: false, runtime_status: "disabled", ...extra
});
const catalog = (extra = {}) => ({ status: "available", data: { ok: true, plugins: [plugin()], ...extra } });
const provider = (extra = {}) => ({
  id: "tts", adapter: "gpt_sovits", actionsEnabled: true, voiceProfiles: [], ...extra
});
function freeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) freeze(child);
  return Object.freeze(value);
}

test("the facade retains its four public exports and projects frozen inputs without mutations", () => {
  assert.deepEqual(Object.keys(facade).sort(), [
    "createControlCenterViewModel", "isObservedActionConfirmation", "normalizeActionPresentation", "observedActionOutcome"
  ]);
  const input = freeze(raw({ pluginRuntime: catalog(), abilitiesRuntime: { providers: [provider()] } }));
  const live = freeze({ state: { sessionId: "current", characterPackId: "akane" }, active: {} });
  const before = structuredClone([input, live]);
  assert.deepEqual(project(input, live), project(input, live));
  assert.deepEqual([input, live], before);
});

test("failed or unconfirmed plugin catalogs retain their cause and cannot enable mutation buttons", () => {
  for (const ok of [false, undefined]) {
    const vm = project(raw({ pluginRuntime: catalog({ ok, reason: "catalog_rejected" }) }));
    assert.equal(vm.abilities.plugins.available, false);
    assert.equal(vm.abilities.plugins.reason, "catalog_rejected");
    assert.equal(vm.abilities.plugins.entries[0].actionsEnabled, false);
    assert.equal(vm.actions["abilities.plugin.enable"].available, false);
  }
});

test("confirmed plugin catalogs preserve enabled, disabled and pending activation behavior", () => {
  const vm = project(raw({ pluginRuntime: catalog({ plugins: [
    plugin(), plugin({ plugin_id: "active", enabled: true, runtime_status: "active", rollback_available: true }),
    plugin({ plugin_id: "pending", pending_activation: true })
  ] }) }));
  assert.equal(vm.abilities.plugins.available, true);
  assert.equal(vm.actions["abilities.plugin.enable"].available, true);
  assert.equal(vm.actions["abilities.plugin.disable"].available, true);
  assert.equal(vm.actions["abilities.plugin.rollback"].available, true);
  assert.equal(vm.abilities.plugins.entries[2].actionsEnabled, false);
});

test("native plugin pickers are restricted to actual loopback hosts", () => {
  for (const backendUrl of ["http://localhost:9999", "http://127.0.0.1:9999", "http://127.2.3.4", "http://[::1]:9999"]) {
    assert.equal(project(raw({ backendUrl })).abilities.plugins.localPickerAvailable, true, backendUrl);
  }
  for (const backendUrl of ["https://127.example.invalid", "https://127.0.0.1.example.invalid", "https://localhost.example.invalid", "https://remote.invalid", "invalid"]) {
    assert.equal(project(raw({ backendUrl })).abilities.plugins.localPickerAvailable, false, backendUrl);
  }
  assert.equal(project(raw({ controlCenterV2: { liveSnapshotStatus: "not-applicable" } })).abilities.plugins.localPickerAvailable, false);
});

test("voice audition requires a usable profile on the same actionable provider", () => {
  const providers = [provider(), provider({ id: "disabled", actionsEnabled: false, voiceProfiles: [{ id: "hidden", enabled: true }] })];
  const vm = project(raw({ abilitiesRuntime: { providers } }));
  assert.equal(vm.actions["abilities.provider.voiceProfile.save"].available, true);
  assert.equal(vm.actions["abilities.provider.ttsTest"].available, false);
  providers[0].voiceProfiles.push({ id: "audition", enabled: true });
  assert.equal(project(raw({ abilitiesRuntime: { providers } })).actions["abilities.provider.ttsTest"].available, true);
});

test("integration readiness follows explicit status and never matches negative readiness labels", () => {
  for (const statusLabel of ["不可用", "尚未就绪", "未就绪", "服务不可用"]) {
    const vm = project(raw({ abilitiesRuntime: { workflows: [{ id: "workflow", title: "Task", statusLabel }] } }));
    assert.equal(vm.abilities.integrations[0].ready, false, statusLabel);
  }
  const vm = project(raw({ abilitiesRuntime: { providers: [
    provider({ status: "unavailable", statusLabel: "可用" }), provider({ id: "ready", status: "ready", statusLabel: "已就绪" })
  ], workflows: [{ id: "ready-workflow", statusLabel: "已就绪" }] } }));
  assert.deepEqual(vm.abilities.integrations.map(item => item.ready), [false, true, true]);
});

test("plugin connection projections remove private values and retain only configured-field indicators", () => {
  const vm = project(raw({ pluginRuntime: catalog({ plugins: [plugin({ contributions: { connections: [{
    name: "api", private_fields: ["token"], schema: { type: "object", properties: { token: { type: "string" }, endpoint: { type: "string" } } }
  }] }, connection_configs: [{ connection: { name: "api" }, values: { token: "synthetic-secret", endpoint: "https://example.invalid" },
    configured_private_fields: ["token", "unknown"] }] })] }) }));
  const config = vm.abilities.plugins.entries[0].connectionConfigs[0];
  assert.deepEqual(config.values, { endpoint: "https://example.invalid" });
  assert.deepEqual(config.configuredPrivateFields, ["token"]);
  assert.doesNotMatch(JSON.stringify(vm.abilities.plugins), /synthetic-secret/);
});

test("disconnected projections keep unavailable capabilities and an honest initial setup state", () => {
  const vm = project({ pluginRuntime: catalog(), abilitiesRuntime: { providers: [provider()] } });
  assert.equal(vm.shell.connected, false);
  assert.equal(vm.abilities.available, false);
  assert.equal(vm.abilities.plugins.entries[0].actionsEnabled, false);
  assert.equal(vm.actions["abilities.provider.config.save"].available, false);
  assert.equal(vm.setup.coreComplete, false);
  assert.equal(vm.activity.phase, "waiting");
});

const session = (extra = {}) => ({
  bot_id: "main", session: { session_id: "old", character_pack_id: "akane", message_count: 120, display_title: "Old chat" },
  messages: [{ role: "assistant", content: "Old reply", seq_no: 80 }],
  message_page: { has_more: true, next_before_seq: 80 }, latest_final_json: { status: "completed", content: "old outcome" },
  workspace_outputs: [{ handle: "old-file", title: "Old file" }], ...extra
});
const live = (extra = {}) => ({
  state: { sessionId: "old", boundBotId: "main", characterPackId: "akane" }, active: {}, ...extra
});

test("switching sessions hides the old identity, count, history cursor and structured outcome", () => {
  const vm = project(raw({ chatSession: session(), chatRuntime: { status: "available" } }),
    live({ state: { sessionId: "new", boundBotId: "main", characterPackId: "akane" } }));
  assert.equal(vm.chat.matchesCurrentSession, false);
  assert.equal(vm.chat.sessionId, "new");
  assert.equal(vm.chat.title, "正在切换会话");
  assert.deepEqual(vm.chat.messages, []);
  assert.deepEqual(vm.chat.outputs, []);
  assert.equal(vm.chat.latestOutcome, null);
  assert.equal(vm.chat.totalCount, 0);
  assert.deepEqual(vm.chat.history, { pageKnown: false, hasMore: false, nextBeforeSeq: 0 });
  assert.equal(vm.chat.loadStatus, "switching");
});

test("a reused session id cannot retain history from a different bot or character", () => {
  for (const overrides of [{ boundBotId: "other-bot" }, { characterPackId: "other-character" }]) {
    const vm = project(raw({ chatSession: session() }), live({ state: { ...live().state, ...overrides } }));
    assert.equal(vm.chat.matchesCurrentSession, false);
    assert.equal(vm.chat.totalCount, 0);
    assert.deepEqual(vm.chat.history, { pageKnown: false, hasMore: false, nextBeforeSeq: 0 });
    assert.deepEqual(vm.chat.outputs, []);
  }
});

test("fresh matching chat data restores the current title, attachments, count and paging", () => {
  const vm = project(raw({ chatSession: session({ session: { sessionId: "current", character_pack_id: "akane", displayTitle: "New chat", message_count: 9 },
    messages: [{ role: "user", content: "", attachments: [{ handle: "file", item_type: "attachment", title: "Photo" }] },
      { role: "assistant", content: "Current reply", seq_no: 9 }, { role: "system", content: "hidden" }] }) }),
    live({ state: { sessionId: "current", boundBotId: "main", characterPackId: "akane" } }));
  assert.equal(vm.chat.matchesCurrentSession, true);
  assert.equal(vm.chat.sessionId, "current");
  assert.equal(vm.chat.title, "New chat");
  assert.equal(vm.chat.messages.length, 2);
  assert.equal(vm.chat.messages[0].attachments[0].handle, "file");
  assert.equal(vm.chat.totalCount, 9);
  assert.equal(vm.chat.history.hasMore, true);
  assert.equal(vm.chat.outputs[0].itemType, "generated");
});

test("missing live volume does not mute the backend volume, while an explicit zero remains valid", () => {
  for (const voiceVolume of [null, "", "   ", false, {}, "invalid"]) {
    const vm = project(raw({ voiceRuntime: { tts: { volume: 63 } } }), live({ state: { voiceVolume } }));
    assert.equal(vm.voice.tts.volume, 63, `invalid live volume ${JSON.stringify(voiceVolume)}`);
  }
  assert.equal(project(raw({ voiceRuntime: { tts: { volume: 63 } } }), live({ state: { voiceVolume: 0 } })).voice.tts.volume, 0);
  assert.equal(project(raw(), live({ state: { voiceVolume: "0.45" } })).voice.tts.volume, 45);
});

test("activity keeps its established priority and exposes the wake evaluation only once", () => {
  const activity = active => project(raw(), live({ active, runtimeMode: "tool-running" })).activity;
  assert.equal(activity({ proactiveWakeRunning: true, sending: true, speaking: true }).phase, "observing");
  assert.equal(activity({ sending: true, speaking: true }).phase, "using_tool");
  assert.equal(activity({ speaking: true }).phase, "delivering");
  assert.equal(activity({ voiceInput: "opening" }).label, "正在打开麦克风");
  assert.equal(activity({ voiceInput: "processing" }).label, "正在识别语音");
  assert.equal(activity({}).phase, "idle");
});

test("character assets retain allowed URLs and reject local paths and script URLs", () => {
  for (const image of ["javascript:alert(1)", "file:///C:/private/image.png", "C:/private/image.png", "//remote.invalid/image", "data:text/html,hello"]) {
    assert.equal(project(raw({ characterRuntime: { hero: image, emotions: [{ image }] } })).character.visuals.portrait, "");
    assert.equal(project(raw({ characterRuntime: { hero: image } })).character.visuals.hero, "");
  }
  for (const image of ["/assets/pet.png", "https://example.invalid/pet.png", "asset://localhost/pet.png", "blob:https://example.invalid/id", "data:image/png;base64,AA=="]) {
    assert.equal(project(raw(), { currentExpression: { image } }).character.visuals.portrait, image);
  }
});

test("system diagnostics preserve degraded providers, unavailable live state and deduplicated warnings", () => {
  const vm = project(raw({ controlCenterV2: { liveSnapshotStatus: "unavailable" },
    characterRuntime: { warning: { headline: "资源缺失", body: "资源缺失" } },
    voiceRuntime: { tts: { providerStatus: { status: "unavailable", activeProviderName: "TTS", reason: "provider_offline" } } } }));
  assert.equal(vm.system.overallTone, "danger");
  assert.equal(vm.system.controlsAvailable, false);
  assert.equal(vm.system.issues.filter(item => item.title === "资源缺失").length, 1);
  assert.equal(vm.system.issues.some(item => item.detail === "provider_offline"), true);
  assert.equal(vm.shell.connectionDetail, "实时状态同步不可用");
});

test("a missing numeric state cannot confirm a zero-volume setting", () => {
  for (const voiceVolume of [null, "", "   ", false, {}]) {
    assert.equal(confirms("voice.setVolume", {}, { state: { voiceVolume } }, { value: 0 }), false);
  }
  assert.equal(confirms("voice.setVolume", {}, { state: { voiceVolume: 0 } }, { value: 0 }), true);
  assert.equal(confirms("voice.setVolume", {}, { state: { voiceVolume: "0.4501" } }, { value: 0.45 }), true);
});

test("matching settings values cannot confirm a different operation or an unrelated command", () => {
  const after = { state: { voiceVolume: 0.5 }, settingsCommandResult: { command: "setVoiceVolume", operationId: "old", ok: true, status: "completed" } };
  assert.equal(confirms("voice.setVolume", {}, after, { value: 0.5, operationId: "new" }), false);
  after.settingsCommandResult.operationId = "new";
  after.settingsCommandResult.command = "setVoiceSpeed";
  assert.equal(confirms("voice.setVolume", {}, after, { value: 0.5, operationId: "new" }), false);
  after.settingsCommandResult.command = "setVoiceVolume";
  assert.equal(confirms("voice.setVolume", {}, after, { value: 0.5, operationId: "new" }), true);
  assert.equal(confirms("voice.setVolume", {}, after, { value: 0.6, operationId: "new" }), false);
});

test("refresh operations require their own acknowledgement and expose a failed reload cause", () => {
  assert.equal(confirms("character.refresh", {}, { active: {} }, { operationId: "refresh" }), false);
  assert.equal(confirms("perception.runDiagnostics", {}, { active: {} }, { operationId: "diagnostics" }), false);
  const settingsCommandResult = { command: "reloadResources", operationId: "refresh", ok: false, status: "failed", reason: "resource_reload_failed" };
  assert.equal(confirms("character.refresh", {}, { settingsCommandResult }, { operationId: "refresh" }), true);
  assert.deepEqual(facade.observedActionOutcome("character.refresh", { operationId: "refresh" }, { settingsCommandResult }), {
    ok: false, status: "failed", reason: "resource_reload_failed"
  });
  settingsCommandResult.ok = true;
  settingsCommandResult.status = "completed";
  assert.equal(confirms("character.refresh", {}, { settingsCommandResult }, { operationId: "refresh" }), true);
});

test("pending music acknowledgements are not terminal, while an accepted chat request is observed", () => {
  const settingsCommandResult = { command: "controlActiveMusic", operationId: "music", ok: true, status: "pending" };
  assert.equal(facade.observedActionOutcome("music.next", { operationId: "music" }, { settingsCommandResult }), null);
  assert.equal(confirms("music.next", {}, { settingsCommandResult }, { operationId: "music" }), false);
  assert.equal(confirms("chat.send", {}, { settingsCommandResult: {
    command: "sendChatMessage", operationId: "chat", ok: true, status: "accepted"
  } }, { operationId: "chat" }), true);
});

test("legacy music navigation observes track identity changes without playback toggles", () => {
  const before = { active: { musicPlaying: true }, music: { track: { sourceId: "one", displayName: "Track one" } } };
  const after = { active: { musicPlaying: true }, music: { track: { sourceId: "two", displayName: "Track two" } } };
  assert.equal(confirms("music.next", before, after), true);
  assert.equal(confirms("music.next", after, structuredClone(after)), false);
});

test("legacy music confirmation follows the selected system target even with an old local queue", () => {
  const before = { active: { musicPlaying: false, musicPaused: false }, music: { control: { target: "system" },
    track: { displayName: "Old local track" }, systemMedia: { trackKey: "system", playbackStatus: "paused", isPlaying: false } } };
  const after = structuredClone(before);
  after.music.systemMedia.playbackStatus = "playing";
  after.music.systemMedia.isPlaying = true;
  assert.equal(confirms("music.togglePlayback", before, after), true);
  const unchanged = structuredClone(before);
  unchanged.music.track.displayName = "Different local track";
  assert.equal(confirms("music.next", before, unchanged), false);
});

test("normalized character voice aliases can enable clearing a current assignment", () => {
  const vm = project(raw({ characterRuntime: { selectedPackId: "akane", voice: { profile_id: "main-voice" } } }));
  assert.equal(vm.character.voice.profileId, "main-voice");
  assert.equal(vm.actions["abilities.provider.voiceProfile.clearCurrentCharacter"].available, true);
});

test("an unmapped observed action cannot report confirmation from an arbitrary snapshot", () => {
  assert.equal(confirms("unknown.action", {}, {}), false);
});

test("action feedback distinguishes submitted work and explicit failure from completion", () => {
  for (const status of ["accepted", "queued", "pending", "running"]) {
    assert.notEqual(facade.normalizeActionPresentation({ ok: true, status }).label, "已完成", status);
  }
  const failed = facade.normalizeActionPresentation({ ok: true, status: "failed", reason: "operation_rejected" });
  assert.equal(failed.phase, "failed");
  assert.equal(failed.detail, "operation_rejected");
  assert.equal(facade.normalizeActionPresentation({ ok: true, status: "completed" }).phase, "confirmed");
  assert.equal(facade.normalizeActionPresentation({ ok: false, status: "execution_unknown", reason: "runtime_state_not_confirmed" }).phase, "unknown");
});

test("reselecting a current pack or outfit confirms only its own completed command", () => {
  for (const [action, command, field] of [["character.selectPack", "setCharacterPack", "characterPackId"], ["character.setOutfit", "setOutfit", "outfit"]]) {
    const before = { state: { [field]: "current" } };
    const after = { state: { [field]: "current" }, settingsCommandResult: { command, operationId: "select", ok: true, status: "completed" } };
    assert.equal(confirms(action, before, after, { value: "current", operationId: "select" }), true);
    assert.equal(confirms(action, before, { state: after.state }, { value: "current", operationId: "select" }), false);
  }
});

test("a malformed command acknowledgement cannot fabricate success or failure", () => {
  for (const ok of [undefined, null, "true", 1]) {
    const settingsCommandResult = { command: "controlActiveMusic", operationId: "music", status: "completed", ok };
    assert.equal(facade.observedActionOutcome("music.next", { operationId: "music" }, { settingsCommandResult }), null);
    assert.equal(confirms("music.next", {}, { settingsCommandResult }, { operationId: "music" }), false);
  }
});
