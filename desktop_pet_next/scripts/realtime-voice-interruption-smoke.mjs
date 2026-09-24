import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

// Execute the actual main-window handlers with observational UI/transport ports.
const source = readFileSync(new URL("../src/main.js", import.meta.url), "utf8");
function handler(name, nextName) {
  const start = source.indexOf(`function ${name}(`);
  const next = new RegExp(`\\n(?:async )?function ${nextName}\\(`).exec(source.slice(start));
  const end = next ? start + next.index : -1;
  assert.ok(start >= 0 && end > start, `${name} handler boundary`);
  return source.slice(start, end);
}
const endpointStart = source.indexOf("async function handleRealtimeVoiceCallEndpoint(");
const endpointEnd = source.indexOf("\nfunction showFirstRealtimeVoicePlaybackText(", endpointStart);
assert.ok(endpointStart >= 0 && endpointEnd > endpointStart);
const effects = [];
const scope = {
  state: { currentEmotion: "normal" },
  window: { clearTimeout() {} },
  isActiveRealtimeCallTurn: (turn) => !turn.closed && turn.call.turns.has(turn),
  isActiveRealtimeVoiceCall: (call) => call.active,
  clearRealtimeVoiceFinalWatchdog() {},
  refreshRealtimeVoiceCallSending() {},
  stopRealtimeCallSafetyRecorder: async () => new Blob(["recording"]),
  setRuntimeStatus: (text, detail) => effects.push(["status", text, detail.mode]),
  setVoiceInputState: (state) => effects.push(["voice", state]),
  setPetEmotion: (emotion) => effects.push(["emotion", emotion]),
  setPetMotion: (motion) => effects.push(["motion", motion]),
  setRestingPetEmotion: () => effects.push(["emotion", "normal"]),
  applyPayloadEmotion: (payload) => effects.push(["replyEmotion", payload.emotion]),
  applyPayloadActivity: (payload) => effects.push(["activity", payload.activity.action]),
  showStartedRealtimeVoicePlaybackText: (turn) => { turn.hasShownSpeech = true; },
  showBubbleText: (text) => effects.push(["bubble", text]),
  finishRealtimeVoiceTurn: (turn) => { turn.closed = true; },
  showThinking: () => effects.push(["thinking"]),
  showError: (text) => effects.push(["error", text]),
  resolveEmotionEntry: (id) => ({ id }),
  updateActivityControls() {},
  scheduleSettingsSnapshot() {},
  hasVisibleRealtimeVoiceReply: (call, except) => [...call.turns].some(
    (turn) => turn !== except && turn.hasShownSpeech && turn.playbackActive
  )
};
vm.createContext(scope);
vm.runInContext([
  source.slice(endpointStart, endpointEnd),
  handler("applyRealtimeVoicePresentation", "buildRealtimeVoiceCallCallbacks"),
  handler("buildRealtimeVoiceCallCallbacks", "isActiveRealtimeVoiceCall"),
  handler("buildRealtimeVoiceCallbacks", "markRealtimeVoiceFailure"),
  handler("finishRealtimeVoiceCallTurn", "cancelRealtimeVoiceCallResponse")
].join("\n"), scope);

const call = {
  active: true, turns: new Set(),
  flow: { acceptEndpoint: () => true, markResponding() {}, allowOverlapListening() {}, complete() {} },
  reconnect: { reset() {} }
};
const oldTurn = { call, playbackActive: true, responseActive: true, hasShownSpeech: true, closed: false };
oldTurn.session = { cancel: async () => { throw new Error("Endpoint must not cancel the old reply"); } };
const turn = {
  call, closed: false, committed: false, endpointAccepted: false,
  session: {
    flushAndStopCapture: async () => {}, releaseFallbackPcm() {},
    dispose: () => effects.push(["dispose", "input"]),
    finishInput: async () => {
      effects.push(["finishInput"]);
      callbacks.onFinal({ input_state: "committed", disposition: "interaction" });
    }
  }
};
call.turns.add(oldTurn);
call.turns.add(turn);
call.inputTurn = turn;
const callbacks = scope.buildRealtimeVoiceCallCallbacks(turn);
await scope.handleRealtimeVoiceCallEndpoint(turn, { action: "commit" });
assert.equal(turn.committed, true);
assert.equal(oldTurn.closed, false);
assert.equal(effects.some(([kind]) => kind === "thinking" || kind === "error"), false);
callbacks.onInteraction({ state: "committed", disposition: "interaction" });
assert.equal(turn.closed, true);
assert.equal(oldTurn.closed, false);
assert.equal(oldTurn.playbackActive, true);
assert.equal(call.turns.size, 1);
assert.ok(effects.some(([kind, value]) => kind === "motion" && value === "speaking"));
assert.equal(effects.some(([kind, value]) => kind === "emotion" && value === "listening"), false);
const afterTerminal = effects.length;
callbacks.onInteraction({ state: "committed", disposition: "interaction" });
assert.equal(effects.length, afterTerminal, "Repeated settlement must be inert");

oldTurn.session = { dispose() {}, releaseFallbackPcm() {} };
call.turns.add({ call, closed: false, responseActive: true, playbackActive: false });
scope.buildRealtimeVoiceCallCallbacks(oldTurn).onResponseFailed({ state: "cancelled" });
assert.equal(effects.some(([kind]) => kind === "error"), false, "Semantic cancellation is not a failed reply");
assert.deepEqual(effects.filter(([kind]) => kind === "motion").at(-1), ["motion", "thinking"]);

const presentation = { presentation: { emotion: "happy", activity: { action: "pause" } } };
const speaking = { call, closed: false };
call.turns.add(speaking);
const speakingCallbacks = scope.buildRealtimeVoiceCallCallbacks(speaking);
const beforePresentation = effects.length;
speakingCallbacks.onPresentation(presentation);
assert.equal(effects.length, beforePresentation, "Queued audio must not apply actions yet");
speakingCallbacks.onPlaybackStarted({});
assert.equal(effects.filter(([kind]) => kind === "activity").length, 1);
assert.equal(effects.filter(([kind]) => kind === "replyEmotion").length, 1);
speakingCallbacks.onPresentation(presentation);
speakingCallbacks.onPlaybackStarted({});
assert.equal(effects.filter(([kind]) => kind === "activity").length, 1, "Later segments must not replay actions");

const stale = { call, closed: false, hasShownSpeech: true };
call.turns.add(stale);
scope.buildRealtimeVoiceCallCallbacks(stale).onPresentation(presentation);
assert.equal(effects.filter(([kind]) => kind === "activity").length, 1, "Old reply cannot override the speaking reply");
speaking.closed = true;
const afterClose = effects.length;
speakingCallbacks.onPresentation(presentation);
speakingCallbacks.onPlaybackStarted({});
assert.equal(effects.length, afterClose, "Closed turns ignore late UI and audio");

const legacy = { closed: false };
scope.realtimeVoiceTurn = legacy;
const legacyCallbacks = scope.buildRealtimeVoiceCallbacks(legacy);
legacyCallbacks.onPlaybackStarted({});
legacyCallbacks.onPresentation(presentation);
legacyCallbacks.onPresentation(presentation);
assert.equal(effects.filter(([kind]) => kind === "activity").length, 2, "Legacy protocol applies one action");
scope.realtimeVoiceTurn = {};
const afterSwitch = effects.length;
legacyCallbacks.onPlaybackStarted({});
assert.equal(effects.length, afterSwitch, "Switching turns fences old callbacks");
const textOnly = { closed: false };
scope.realtimeVoiceTurn = textOnly;
const textCallbacks = scope.buildRealtimeVoiceCallbacks(textOnly);
textCallbacks.onPresentation(presentation);
assert.equal(effects.length, afterSwitch);
textCallbacks.onResponseCompleted({ speech: "好的。", delivery_status: "text_only" });
assert.equal(effects.filter(([kind]) => kind === "activity").length, 3);
assert.equal(effects.filter(([kind]) => kind === "bubble").at(-1)[1], "好的。");
const afterTextOnly = effects.length;
textCallbacks.onResponseCompleted({ speech: "好的。", delivery_status: "text_only" });
assert.equal(effects.length, afterTextOnly);
console.log("realtime voice interruption smoke: ok");
