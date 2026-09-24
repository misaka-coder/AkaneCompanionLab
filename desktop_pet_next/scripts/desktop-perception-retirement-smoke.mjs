import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { normalizeScreenObservationSettings } from "../src/screen-observation.js";
import { CONTROL_CENTER_ACTIONS } from "../src/control-center/action-router.js";
import { createControlCenterViewModel } from "../src/control-center-v2/view-model.js";
import { renderPerception } from "../src/control-center-v2/components/perception.js";

const source = fs.readFileSync(new URL("../src/main.js", import.meta.url), "utf8");
function production(name) {
  const match = source.match(new RegExp(`^(?:async )?function\\*? ${name}\\([^]*?^}`, "m"));
  assert.ok(match, `production function ${name} exists`);
  return match[0];
}

// Real state migration and save payload: old preferences cannot return on restart.
const context = {
  DEFAULT_STATE: { scale: 1, opacity: 1, voiceVolume: 0.8 }, SCALE_MIN: 0.5, SCALE_MAX: 2,
  LOCAL_DEFAULT_INSTANCE_ID: "test-host", PROFILE_USER_ID: "test-user",
  clamp: (n, min, max) => Math.max(min, Math.min(max, n)), isLegacyWindowSize: () => false,
  normalizeBackendUrl: v => v, normalizeCharacterPackId: v => v,
  normalizeCharacterRuntimeStates: v => v || {}, generateSessionId: () => "test-session",
  normalizeScreenObservationSettings, normalizeProactiveWakeIntervalSec: () => 30,
  cloneCareState: () => null, normalizeMusicPlayMode: () => "ordered",
  normalizeBooleanSetting: v => Boolean(v),
};
vm.createContext(context);
vm.runInContext(production("normalizeState"), context);
const old = { desktopContextEnabled: true, clipboardContextEnabled: true, screenVisionMode: "summary",
  screenVisionIntervalSec: 25, screenVisionEnabled: false, screenVisionSampleIntervalSec: 0.5,
  screenVisionWindowSec: 120, screenVisionMaxEdge: 1920, screenVisionPacking: "frames" };
const migrated = context.normalizeState(old);
for (const key of ["desktopContextEnabled", "clipboardContextEnabled", "screenVisionMode", "screenVisionIntervalSec"]) {
  assert.ok(!(key in migrated), key);
}
assert.equal(old.clipboardContextEnabled, true, "migration does not mutate its input");
assert.equal(migrated.screenVisionSampleIntervalSec, 0.5);
assert.equal(migrated.screenVisionPacking, "frames");
const saved = [];
Object.assign(context, { state: migrated, isTauriRuntime: true, applyWindowGeometryToState() {},
  persistCurrentCharacterRuntimeState() {}, buildCharacterRuntimeSnapshot: () => ({}),
  invoke: async (command, payload) => { if (command === "save_pet_state") saved.push(payload.state); return {}; },
  setStatus: message => assert.fail(message), formatError: String });
vm.runInContext(production("saveNow"), context);
await context.saveNow();
assert.equal(saved.length, 1);
assert.ok(!("clipboardContextEnabled" in saved[0]));
assert.equal(saved[0].screenVisionWindowSec, 120);
assert.equal(context.normalizeState(saved[0]).screenVisionPacking, "frames");

// Stale settings windows get an explicit unsupported acknowledgement, not success.
Object.assign(context, { lastSettingsCommandResult: null, scheduleSettingsSnapshot() {}, setRuntimeStatus() {} });
vm.runInContext(production("handleSettingsCommand"), context);
for (const command of ["setDesktopContextEnabled", "setClipboardContextEnabled"]) {
  await context.handleSettingsCommand({ command, value: true, operationId: command });
  assert.equal(context.lastSettingsCommandResult.ok, false);
  assert.equal(context.lastSettingsCommandResult.status, "not-supported");
}
assert.ok(!Object.values(CONTROL_CENTER_ACTIONS).includes("perception.clipboardContext.setEnabled"));

// Execute the actual request builder. Any hidden OS read fails this test.
const requests = [];
const frames = [{ captured_at: 123, data_url: "data:image/jpeg;base64,synthetic" }];
Object.assign(context, {
  AbortController, window: { setTimeout: () => 1, clearTimeout() {} }, THINK_TIMEOUT_MS: 1000,
  thinkController: null, markTurnLatency() {}, hydrateSystemMediaLyricsForTurn: async () => ({}),
  shouldWaitForSystemMediaLyricsForTurn: () => false, isTurnActive: () => true,
  screenObservationScope: () => "test-scope", screenObservation: { revision: 1, snapshot: () => frames },
  packScreenObservation: async value => value,
  attachDesktopCareContext: value => value, getProfileUserId: () => "test-user",
  createDesktopSourceMessageId: () => "test-message", CLIENT_MODE: "desktop_pet",
  getCurrentCharacterPackId: () => "test-character", BASE_CAPABILITIES: ["speech_segments", "file_drop"],
  AUDIO_PLAYBACK_CAPABILITY: "audio_playback", buildCurrentVisual: () => ({}),
  buildDesktopMusicActivity: () => null, buildDesktopCareContext: () => ({}), getCareFeatureStatus: () => ({}),
  buildBackendEndpointUrl: () => "synthetic://think",
  backendFetch: async (_url, init) => { requests.push(JSON.parse(init.body)); return { ok: true, status: 200 }; },
  readNdjsonEvents: async function* () { yield { type: "stream_end" }; },
  tauriCall: () => assert.fail("implicit native read"),
  navigator: { clipboard: { readText: () => assert.fail("implicit clipboard read") } },
});
vm.runInContext(["buildClientCapabilities", "latestDesktopScreenFramesForThink", "sendThinkStream"].map(production).join("\n"), context);
for (const screenVisionEnabled of [false, true, false]) {
  context.state = { ...migrated, desktopContextEnabled: true, clipboardContextEnabled: true, screenVisionEnabled };
  for await (const event of context.sendThinkStream("看看当前窗口和剪贴板", 1, { attachmentIds: ["manual-paste-file"] })) {
    assert.equal(event.type, "stream_end");
  }
  const request = requests.at(-1);
  assert.ok(!("desktop_context" in request));
  assert.ok(!request.client_capabilities.includes("desktop_context"));
  assert.deepEqual(request.desktop_screen_frames, screenVisionEnabled ? frames : []);
  assert.deepEqual(request.current_attachment_ids, ["manual-paste-file"]);
  assert.equal(request.message, "看看当前窗口和剪贴板");
}
context.isTurnActive = () => false;
for await (const _ of context.sendThinkStream("cancelled", 2)) assert.fail("cancelled turn yielded");
assert.equal(requests.length, 3);

const html = renderPerception({ viewModel: createControlCenterViewModel({}), actionStates: {} });
assert.match(html, /不会自动读取剪贴板或附带窗口标题/);
assert.match(html, /独立于屏幕共享/);
assert.match(html, /手动粘贴文字、图片和文件/);
assert.doesNotMatch(html, /perception\.clipboardContext|perception\.desktopContext/);
assert.doesNotMatch(source, /navigator\.clipboard\.readText|collectDesktopContextForTurn|lastDesktopForeground/);
console.log("desktop perception retirement: migrated state/save, retired commands, actual turn requests, screen toggle, cancellation and rendered boundary passed");
