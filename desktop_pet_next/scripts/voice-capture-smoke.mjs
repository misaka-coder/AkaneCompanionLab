import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { captureMicrophone } from "../src/voice-capture.js";

const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const tick = () => new Promise((resolve) => setImmediate(resolve));
const source = readFileSync(new URL("../src/main.js", import.meta.url), "utf8");
function production(name) {
  const match = new RegExp(`(?:async )?function ${name}\\(`).exec(source);
  assert.ok(match, name);
  const next = /\n(?:async )?function \w+\(/.exec(source.slice(match.index + 1));
  return source.slice(match.index, next ? match.index + 1 + next.index : undefined);
}
function harness({ realtime = true, timeoutMs = 15000 } = {}) {
  const requests = [], effects = [];
  const button = { classList: { toggle() {} } };
  const scope = {
    AbortController, MediaRecorder: class {},
    navigator: { mediaDevices: { getUserMedia() {
      const request = deferred(); requests.push(request); return request.promise;
    } } },
    captureMicrophone: (options) => captureMicrophone({ ...options, timeoutMs }),
    state: { voiceInputEnabled: true, voiceEnabled: true },
    els: { voiceRecordButton: button, voicePlayer: {} },
    voiceInputState: "idle", voiceInputToken: 0, voiceCaptureController: null,
    realtimeVoiceCall: null, realtimeVoiceTurn: null, voiceRecorder: null, asrController: null,
    voiceStream: null, voiceChunks: [], voiceStartedAt: 0,
    canUseRealtimeVoice: () => realtime, isReplyActive: () => false,
    showBubbleText: (text) => effects.push(["bubble", text]),
    showError: (text) => effects.push(["error", text]),
    describeVoiceError: (error) => error.message,
    setRuntimeStatus: (text) => effects.push(["status", text]),
    closeRealtimeVoiceTurn() {},
    setVoiceInputState: (value) => { scope.voiceInputState = value; scope.updateVoiceRecordButton(); },
    // A cancelled acquisition must never advance to session creation or TTS stop.
    generateSessionId() { throw new Error("Late microphone started a call"); },
    selectVoiceMimeType() { throw new Error("Late microphone started recording"); },
    stopTts() { throw new Error("Late microphone stopped newer audio"); }
  };
  vm.createContext(scope);
  vm.runInContext([
    "startVoiceRecording", "startRealtimeVoiceCall", "cancelVoiceRecording",
    "toggleVoiceRecording", "updateVoiceRecordButton", "cleanupVoiceRecorder"
  ].map(production).join("\n"), scope);
  return { scope, requests, effects, button };
}

for (const realtime of [true, false]) {
  const h = harness({ realtime });
  const pending = h.scope.toggleVoiceRecording();
  await tick();
  assert.equal(h.scope.voiceInputState, "opening");
  assert.equal(h.button.disabled, false);
  assert.equal(h.button.title, "取消打开麦克风");
  // A duplicate shortcut/start cannot acquire another device.
  await (realtime ? h.scope.startRealtimeVoiceCall() : h.scope.startVoiceRecording());
  assert.equal(h.requests.length, 1);
  await h.scope.toggleVoiceRecording();
  await pending;
  assert.equal(h.scope.voiceInputState, "idle");
  const next = h.scope.toggleVoiceRecording();
  await tick();
  const openingController = h.scope.voiceCaptureController;
  let stopped = 0;
  h.requests[0].resolve({ getTracks: () => [{ stop: () => stopped++ }] });
  await tick();
  assert.equal(stopped, 1, "Late stream is released");
  assert.equal(h.scope.voiceInputState, "opening", "Old result cannot reset new opening state");
  assert.equal(h.scope.voiceCaptureController, openingController);
  // sendMessage / character / session changes all use this cancellation handler.
  await h.scope.cancelVoiceRecording();
  await next;
  h.requests[1].reject(new Error("late permission rejection"));
  await tick();
  assert.equal(h.effects.some(([kind]) => kind === "error"), false);

  const slow = harness({ realtime, timeoutMs: 5 });
  await slow.scope.toggleVoiceRecording();
  assert.equal(slow.scope.voiceInputState, "idle");
  assert.equal(slow.button.disabled, false);
  assert.ok(slow.effects.some(([kind, text]) => kind === "error" && text.includes("超时")));
  slow.requests[0].resolve({ getTracks: () => [{ stop: () => stopped++ }] });
  await tick();
  assert.equal(stopped, 2, "Timed-out stream is also released");
}

const stream = { getTracks: () => [] };
assert.equal(await captureMicrophone({ getUserMedia: async () => stream }), stream);
await assert.rejects(captureMicrophone({ getUserMedia: async () => {
  throw Object.assign(new Error("denied"), { name: "NotAllowedError" });
} }), { name: "NotAllowedError" });
console.log("voice capture cancellation smoke: ok");
