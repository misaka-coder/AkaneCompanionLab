import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { ScreenObservationBuffer, packScreenObservation, normalizeScreenObservationSettings, SCREEN_OBSERVATION_COMMANDS } from "../src/screen-observation.js";
import { createControlCenterViewModel, isObservedActionConfirmation } from "../src/control-center-v2/view-model.js";
import { renderPerception } from "../src/control-center-v2/components/perception.js";

const config = normalizeScreenObservationSettings({});
const buffer = new ScreenObservationBuffer();
const frame = (captured_at) => ({ captured_at, width: 1280, height: 720, data_url: `data:image/jpeg;base64,${captured_at}` });
for (let time = 100; time <= 140; time += 2) buffer.push(frame(time), "alice/session-1", config);
const samples = buffer.snapshot("alice/session-1", config, 140000);
assert.deepEqual(samples.map((item) => item.captured_at), [110, 120, 130, 140]);
assert.equal(buffer.snapshot("alice/session-1", config, 150000).length, 0);
assert.equal(buffer.snapshot("bob/session-1", config, 140000).length, 0);
buffer.push(frame(142), "bob/session-2", config);
assert.equal(buffer.frames.length, 1);
assert.deepEqual(buffer.snapshot("bob/session-2", config, 142000).map((item) => item.captured_at), [142]);
const revision = buffer.revision;
buffer.clear();
assert.ok(buffer.revision > revision);
assert.equal(buffer.frames.length, 0);
assert.equal(normalizeScreenObservationSettings({ screenVisionSampleIntervalSec: 0.5 }).screenVisionSampleIntervalSec, 0.5);
assert.equal(normalizeScreenObservationSettings({ screenVisionFrameCount: 100 }).screenVisionFrameCount, 5);

const drawings = [], labels = [];
const canvas = { getContext: () => ({ fillRect() {}, drawImage: (...args) => drawings.push(args), fillText: (text) => labels.push(text) }), toDataURL: () => "data:image/jpeg;base64,sheet" };
const packed = await packScreenObservation(samples, config, { createCanvas: () => canvas, loadImage: async (url) => url });
assert.equal(drawings.length, 4);
assert.deepEqual(packed[0].frame_times, [110, 120, 130, 140]);
assert.deepEqual(packed[0].layout, { columns: 2, rows: 2 });
assert.equal(packed[1].data_url, samples.at(-1).data_url);
assert.equal(labels[0], "#1  -30.0s");
assert.equal(labels[3], "#4  0.0s");
assert.equal((await packScreenObservation(samples, { ...config, screenVisionPacking: "frames" })).length, 4);
assert.equal((await packScreenObservation([], config)).length, 0);
await assert.rejects(packScreenObservation(samples, config, { loadImage: async () => { throw Error("decode failed"); } }), /decode failed/);

for (const [id, { command, field }] of Object.entries(SCREEN_OBSERVATION_COMMANDS)) {
  const value = field?.endsWith("Enabled") ? true : field === "screenVisionPacking" ? "frames" : 4;
  const after = { state: { [field]: value }, active: { screenVisionBufferRevision: 2 }, settingsCommandResult: { command, operationId: "op-1", ok: true, status: "succeeded" } };
  assert.equal(isObservedActionConfirmation(id, {}, after, { value, operationId: "op-1" }), true);
  assert.equal(isObservedActionConfirmation(id, {}, after, { value, operationId: "old-op" }), false);
  assert.equal(isObservedActionConfirmation(id, {}, { ...after, settingsCommandResult: {} }, { value, operationId: "op-1" }), false);
}
const offline = { viewModel: createControlCenterViewModel({}), actionStates: {} };
assert.match(renderPerception(offline), /等待桌宠实时连接/);
assert.match(renderPerception(offline), /disabled/);

const centerSource = fs.readFileSync(new URL("../src/control-center-v2/index.js", import.meta.url), "utf8");
const guardSource = centerSource.slice(centerSource.indexOf("function perceptionRenderScope("), centerSource.indexOf("  captureCapabilityUiState();", centerSource.indexOf("function render(state)")));
const guardContext = { document: { activeElement: { matches: () => true } }, renderedPerceptionScope: "" };
vm.createContext(guardContext);
vm.runInContext(guardSource + '\nthrow Error("render continued");\n}', guardContext);
const editState = { activePage: "system", viewModel: { shell: { connected: true, instanceLabel: "test" }, bots: { activeId: "bot-1" }, character: { packId: "alice" }, chat: { sessionId: "session-1" }, perception: { available: true } } };
guardContext.renderedPerceptionScope = guardContext.perceptionRenderScope(editState);
assert.doesNotThrow(() => guardContext.render(editState));
assert.throws(() => guardContext.render({ ...editState, viewModel: { ...editState.viewModel, character: { packId: "bob" } } }), /render continued/);
guardContext.renderedPerceptionScope = guardContext.perceptionRenderScope(editState);
assert.throws(() => guardContext.render({ ...editState, viewModel: { ...editState.viewModel, shell: { connected: false } } }), /render continued/);

// Run production stream/render functions, replacing only UI effects with spies.
const source = fs.readFileSync(new URL("../src/main.js", import.meta.url), "utf8");
const commands = { proactiveWakeRunning: true, sending: true, lastSettingsCommandResult: null,
  scheduleSettingsSnapshot() {}, messages: [], sendMessage: (text) => commands.messages.push(text),
  interruptReply: () => { commands.proactiveWakeRunning = false; commands.sending = false; } };
vm.createContext(commands);
vm.runInContext(source.slice(source.indexOf("async function handleSettingsCommand("), source.indexOf("function reportSettingsCommandFailure(")), commands);
await commands.handleSettingsCommand({ command: "sendChatMessage", value: "先回答我", operationId: "user-1" });
assert.equal(commands.lastSettingsCommandResult.status, "accepted");
assert.equal(commands.proactiveWakeRunning, false);
assert.deepEqual(commands.messages, ["先回答我"]);
const observingVm = createControlCenterViewModel({ sourceKind: "backend", controlCenterV2: { liveSnapshotStatus: "connected" } },
  { state: { instanceId: "test" }, active: { sending: true, proactiveWakeRunning: true } });
assert.equal(observingVm.actions["chat.send"].available, true);
assert.equal(observingVm.activity.phase, "observing");
const streamFunction = source.slice(source.indexOf("async function processThinkStream("), source.indexOf("function renderPayload("));
const renderStart = source.indexOf("function renderPayload(");
const renderEnd = source.indexOf("\nfunction ", renderStart + 10);
const effects = [];
const context = {
  bubbleKind: "none", streamedReplySegmentCounts: new Map(),
  isTurnActive: () => true, resetStreamedReplySegments() {}, markTurnLatency() {}, markTurnLatencyOnce() {},
  showThinking: () => effects.push("thinking"), applyPayloadEmotion: () => effects.push("emotion"),
  applyPayloadCareSnapshot: () => effects.push("care"), applyPayloadActivity: () => effects.push("activity"),
  applyPayloadFileDeliveries() {}, applyPayloadBrowserEvents() {},
  queueStreamedReplySegment: (text) => { if (!text) return false; effects.push("bubble:" + text); return true; },
  queueStreamedTtsSegment: (text) => { if (text) effects.push("tts:" + text); },
  flushStreamingTtsPending() {}, splitSpeechText: (text) => [text], normalizeSegments: (items) => items || [],
  missingFinalReplySegments: (items) => items,
  lastTurnSignature: "", lastTurnTextKey: "", buildSpeechTextKey: (s) => s,
  showSpeechSegments: () => effects.push("speech"), setRuntimeStatus() {}, queueLiveTtsPayloadItems() {}
};
vm.createContext(context);
vm.runInContext(streamFunction + source.slice(renderStart, renderEnd), context);
async function* silent() {
  yield { type: "turn_start" };
  yield { type: "ui", emotion: "happy" };
  yield { type: "final", payload: { speech: "", _deliberate_silence: true } };
  yield { type: "stream_end" };
}
assert.equal(await context.processThinkStream(silent(), 1, { backgroundObservation: true }), false);
assert.deepEqual(effects, []);
assert.equal(context.renderPayload({ _deliberate_silence: true, speech: "", emotion: "happy" }), false);
assert.deepEqual(effects, []);
async function* speaking() {
  yield { type: "turn_start" };
  yield { type: "final", payload: { speech: "刚才那一下躲得漂亮。", emotion: "happy" } };
}
assert.equal(await context.processThinkStream(speaking(), 1, { backgroundObservation: true }), true);
assert.ok(effects.includes("bubble:刚才那一下躲得漂亮。"));
assert.ok(effects.includes("tts:刚才那一下躲得漂亮。"));
assert.ok(effects.includes("emotion"));
assert.ok(!effects.includes("thinking"));

// Late permission grants after disabling are stopped, never adopted.
const captureFunction = source.slice(source.indexOf("async function ensureScreenVisionCapture("), source.indexOf("async function latestDesktopScreenFramesForThink("));
let resolveGrant;
let stopped = 0, prompts = 0;
const capture = {
  screenVisionStream: null, screenVisionVideo: null, screenVisionCapturePending: null,
  screenVisionCaptureRevision: 0, screenVisionStatus: "off", screenVisionError: "",
  state: { screenVisionEnabled: true }, screenObservation: new ScreenObservationBuffer(),
  navigator: { mediaDevices: { getDisplayMedia: () => { prompts += 1; return new Promise((resolve) => { resolveGrant = resolve; }); } } },
  formatError: (error) => error.message, scheduleSave() {}, scheduleSettingsSnapshot() {}, screenObservationScope: () => "scope"
};
vm.createContext(capture);
vm.runInContext(captureFunction, capture);
capture.showScreenCaptureActivationPrompt = () => {};
const pending = capture.ensureScreenVisionCapture();
const duplicate = capture.ensureScreenVisionCapture();
assert.equal(prompts, 1);
capture.state.screenVisionEnabled = false;
capture.screenVisionCaptureRevision += 1;
resolveGrant({ getTracks: () => [{ stop: () => stopped++ }] });
assert.equal(await pending, false);
assert.equal(await duplicate, false);
assert.equal(stopped, 1);
assert.equal(capture.screenVisionStream, null);
capture.state.screenVisionEnabled = true;
capture.navigator.mediaDevices.getDisplayMedia = async () => { throw Error("permission denied"); };
await capture.captureScreenVisionFrame();
assert.equal(capture.state.screenVisionEnabled, false);
assert.equal(capture.screenVisionStatus, "error");
assert.equal(capture.screenVisionError, "permission denied");
capture.state.screenVisionEnabled = true;
capture.navigator.mediaDevices.getDisplayMedia = async () => { throw Object.assign(Error("activation required"), { name: "InvalidStateError" }); };
await capture.captureScreenVisionFrame();
assert.match(capture.screenVisionError, /快捷菜单/);
assert.equal(capture.state.screenVisionEnabled, false);
const clickOrder = [];
const clickContext = { state: { screenVisionEnabled: false }, els: { screenShare: { addEventListener: (_name, callback) => { clickContext.onClick = callback; } } },
  setScreenVisionEnabled: () => { clickOrder.push("capture"); return Promise.resolve(); }, closeMenu: () => clickOrder.push("close") };
vm.createContext(clickContext);
vm.runInContext(source.slice(source.indexOf('  els.screenShare.addEventListener("click"'), source.indexOf('  els.openWorkshop.addEventListener("click"')), clickContext);
clickContext.onClick();
assert.deepEqual(clickOrder, ["capture", "close"]);
console.log("screen observation smoke: temporal packing, scope isolation, command acknowledgements, silent/speaking UI+TTS paths, permission races passed");
