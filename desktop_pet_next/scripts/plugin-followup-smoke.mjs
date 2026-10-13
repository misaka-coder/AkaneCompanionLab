// Feed a real backend-produced file-only frame to the existing desktop renderer.
import assert from "node:assert/strict";
import fs from "node:fs";
import vm, { petFunctions } from "./pet-production-functions.mjs";

const frame = JSON.parse(fs.readFileSync(0, "utf8"));
const effects = [], statuses = [];
let refreshes = 0;
const context = vm.createContext({
  desktopFileDeliveryHandled: new Set(),
  state: { sessionId: "session", backendUrl: "http://127.0.0.1:12001", boundBotId: "test" },
  getProfileUserId: () => "owner",
  notifyWorkspaceRefresh: async () => { refreshes += 1; },
  tauriCall: async () => { throw Error("no native action was requested"); },
  applyPayloadCareSnapshot() {}, applyPayloadBrowserEvents() {},
  setPetEmotion: () => effects.push("emotion"),
  showSpeechSegments: () => effects.push("reply"), showBubbleText: () => effects.push("bubble"),
  queueLiveTtsPayloadItems: () => effects.push("tts"),
  setRuntimeStatus: (...args) => statuses.push(args),
  normalizeSegments: (value) => value || [],
});
vm.runInContext(petFunctions([
  "renderPayload", "applyPayloadEmotion", "applyPayloadFileDeliveries", "handleDesktopFileDeliveryEvent"
]), context);
assert.equal(context.renderPayload(frame), false);
await new Promise(setImmediate);
assert.equal(refreshes, 1);
assert.match(statuses.at(-1)[0], /文件已放到手边/);
assert.deepEqual(effects, []);
context.renderPayload(frame);
await new Promise(setImmediate);
assert.equal(refreshes, 1, "duplicate completion must not deliver twice");
assert.equal(context.renderPayload({ speech: "", _deliberate_silence: true }), false);
assert.deepEqual(effects, []);
console.log("plugin followup desktop smoke passed: workspace=1, reply/TTS/emotion/native actions=0");
