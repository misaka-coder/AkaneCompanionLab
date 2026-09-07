// Exercise the existing desktop consumer with a real backend-produced file event.
// Native OS opening is a transport double; this does not claim audible playback.
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const event = JSON.parse(fs.readFileSync(0, "utf8"));
const source = fs.readFileSync(new URL("../src/main.js", import.meta.url), "utf8");
const start = source.indexOf("async function handleDesktopFileDeliveryEvent(event)");
const end = source.indexOf("function normalizeSegments(value)", start);
assert(start >= 0 && end > start);
const calls = [], statuses = [], bubbles = [];
let refreshes = 0, nativeResult = {};
const scope = vm.createContext({
  desktopFileDeliveryHandled: new Set(),
  state: { sessionId: "session", backendUrl: "http://127.0.0.1:12001", boundBotId: "bot-a" },
  getProfileUserId: () => "owner",
  notifyWorkspaceRefresh: async () => { refreshes += 1; },
  tauriCall: async (...args) => { calls.push(args); return nativeResult; },
  setRuntimeStatus: (...args) => statuses.push(args),
  showBubbleText: (...args) => bubbles.push(args),
});
vm.runInContext(source.slice(start, end), scope);
await scope.handleDesktopFileDeliveryEvent({ ...event, send_to_user: false });
assert.equal(refreshes, 0);
assert.equal(calls.length, 0);

await scope.handleDesktopFileDeliveryEvent({ ...event, delivery_action: "", desktop_delivery: {} });
assert.equal(refreshes, 1);
assert.equal(calls.length, 0);
assert.match(statuses.at(-1)[0], /文件已放到手边/);

await scope.handleDesktopFileDeliveryEvent(event);
assert.equal(calls.length, 1);
assert.equal(calls[0][0], "open_workspace_item");
assert.equal(calls[0][1].handle, event.file.handle);
assert.equal(calls[0][1].itemType, "generated");
assert.equal(calls[0][1].realUserId, "owner");
assert.equal(calls[0][1].sessionId, "session");
assert.equal(calls[0][1].botId, "bot-a");
assert.equal(calls[0][1].backendUrl, "http://127.0.0.1:12001");
assert(!Object.hasOwn(calls[0][1], "path"));
assert(calls[0][1].fileName.endsWith("." + (process.argv[2] || "mp3")));
assert.equal(bubbles.at(-1)[1].kind, "status");
await scope.handleDesktopFileDeliveryEvent(event);
assert.equal(calls.length, 1, "duplicate completion must not open twice");

nativeResult = null;
const failed = { ...event, file: { ...event.file, absolute_path: "", path: "", file_path: "", generated_id: "generated::failure", source_id: "generated::failure", handle: "gen_failure" } };
await scope.handleDesktopFileDeliveryEvent(failed);
assert.equal(calls.length, 2);
assert.equal(statuses.at(-1)[1].mode, "error");
assert.equal(bubbles.at(-1)[1].kind, "error");
assert.match(bubbles.at(-1)[0], /失败/);
console.log("media plugin desktop handoff smoke passed (native transport doubled)");
