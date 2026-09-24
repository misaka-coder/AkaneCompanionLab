// Feed a real backend-produced file-only frame to the existing desktop renderer.
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const frame = JSON.parse(fs.readFileSync(0, "utf8"));
const source = fs.readFileSync(new URL("../src/main.js", import.meta.url), "utf8");
function declaration(name) {
  const start = source.indexOf(`function ${name}(`);
  assert(start >= 0, name);
  const end = source.indexOf("\nfunction ", start + 10);
  return source.slice(start, end);
}
const fileStart = source.indexOf("async function handleDesktopFileDeliveryEvent(event)");
const fileEnd = source.indexOf("function normalizeSegments(value)", fileStart);
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
vm.runInContext(["renderPayload", "applyPayloadEmotion", "applyPayloadActivity", "applyPayloadFileDeliveries"]
  .map(declaration).join("\n") + source.slice(fileStart, fileEnd), context);
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
