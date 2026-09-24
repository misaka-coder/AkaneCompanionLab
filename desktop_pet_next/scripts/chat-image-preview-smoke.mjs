import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { readChatImageBlob, createChatImagePreviews } from "../src/control-center/chat-image-preview.js";
import { renderChat } from "../src/control-center-v2/components/chat.js";

const bytes = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=", "base64");
const digest = data => crypto.subtle.digest("SHA-256", data).then(buffer => Buffer.from(buffer).toString("hex"));
const headers = { "Content-Type": "image/png", "X-Akane-Artifact-Mime": "image/png", "X-Akane-Artifact-Instance": "host",
  "X-Akane-Artifact-Size": String(bytes.length), "X-Akane-Artifact-Sha256": await digest(bytes) };
let request;
const options = { baseUrl: "http://localhost:12001/api/bots/b", instanceId: "host", profileUserId: "u", sessionId: "s",
  characterPackId: "reimu", handle: "img_1", itemType: "attachment",
  fetchImpl: async (url, init) => { request = { url, init }; return new Response(bytes, { headers }); } };
assert.equal((await readChatImageBlob(options)).size, bytes.length);
assert.equal(new URL(request.url).searchParams.get("character_pack_id"), "reimu");
assert.match(request.url, /\/api\/bots\/b\/desktop-pet\/workspace\/attachments\/img_1\/content/);
assert.equal(request.init.maxRedirections, 0);
for (const [field, value, reason] of [
  ["X-Akane-Artifact-Instance", "foreign", "instance_mismatch"], ["X-Akane-Artifact-Size", "9000000", "too_large"],
  ["Content-Type", "image/svg+xml", "format_unsupported"], ["X-Akane-Artifact-Mime", "image/jpeg", "format_mismatch"],
  ["X-Akane-Artifact-Size", "1", "size_mismatch"], ["X-Akane-Artifact-Sha256", "0".repeat(64), "integrity_mismatch"]]) {
  await assert.rejects(readChatImageBlob({ ...options, fetchImpl: async () => new Response(bytes, { headers: { ...headers, [field]: value } }) }), new RegExp(reason));
}
await assert.rejects(readChatImageBlob({ ...options, handle: "../../private" }), /invalid_artifact_handle/);

let scope = "a", resolveLoad, loads = 0, changes = 0;
const revoked = [], created = [];
const previews = createChatImagePreviews({ readScope: () => scope,
  load: async () => { loads += 1; return new Promise(resolve => { resolveLoad = resolve; }); }, changed: () => changes++,
  createUrl: () => { const url = `blob:preview-${created.length}`; created.push(url); return url; }, revokeUrl: url => revoked.push(url) });
const item = { itemType: "attachment", handle: "img_1" };
const first = previews.open(item);
await previews.open(item);
assert.equal(loads, 1, "duplicate click shares active read");
assert.equal(previews.snapshot()["attachment:img_1"].status, "loading");
scope = "b";
assert.deepEqual(previews.snapshot(), {});
resolveLoad(new Blob([bytes])); await first;
assert.equal(created.length, 0, "old scope read never creates a new URL");
const second = previews.open(item); resolveLoad(new Blob([bytes])); await second;
assert.equal(previews.snapshot()["attachment:img_1"].status, "ready");
previews.failed(item);
assert.equal(revoked.length, 1);
assert.equal(previews.snapshot()["attachment:img_1"].reason, "preview_decode_failed");
previews.dispose();
assert.ok(changes >= 4);

const card = { ...item, kind: "image", format: "png", title: 'test <image>', canOpen: true };
const state = { actionStates: {}, viewModel: { shell: { connected: true }, actions: {},
  chat: { messages: [{ id: "m", content: "fixture", attachments: [card] }], previews: { "attachment:img_1": { status: "ready", url: "blob:fixture" } } } } };
const html = renderChat(state);
assert.match(html, /<img src="blob:fixture" alt="test &lt;image&gt;"/);
assert.match(html, /title="查看原图"/);
state.viewModel.chat.previews["attachment:img_1"] = { status: "ready", url: "https://tracking.invalid/image" };
assert.ok(!renderChat(state).includes("tracking.invalid"));
state.viewModel.chat.previews["attachment:img_1"] = { status: "failed", reason: "preview_too_large" };
assert.match(renderChat(state), /超过 8MB/);

const bridgeSource = fs.readFileSync(new URL("../src/control-center-v2/bridge.js", import.meta.url), "utf8");
const cacheStart = bridgeSource.indexOf("  const imagePreviews = createChatImagePreviews(");
const cacheEnd = bridgeSource.indexOf("  const qqSetup", cacheStart);
const methodStart = bridgeSource.indexOf("  async function previewChatImage(");
const methodEnd = bridgeSource.indexOf("  return { start, refresh", methodStart);
let bridgeReads = 0;
const context = { createChatImagePreviews, publish() {},
  source: { backendUrl: "http://localhost:12001", botId: "a", profileUserId: "u", readChatImagePreview: async () => { bridgeReads++; return new Blob([bytes]); } },
  runtimeSnapshot: { state: { backendUrl: "http://localhost:12001", boundBotId: "b", profileUserId: "u", sessionId: "s", characterPackId: "reimu" } },
  publishedViewModel: { shell: { connected: true }, chat: { pendingAttachments: [card] } },
};
vm.createContext(context);
vm.runInContext(bridgeSource.slice(cacheStart, cacheEnd) + bridgeSource.slice(methodStart, methodEnd)
  + "globalThis.bridgePreviews = imagePreviews;", context);
await context.previewChatImage({ ...item, handle: "not-on-a-card" });
assert.equal(bridgeReads, 0, "arbitrary refs outside visible cards cannot load");
await context.previewChatImage(item);
assert.equal(bridgeReads, 0, "stale bound source cannot read from a different Bot");
assert.equal(context.bridgePreviews.snapshot()["attachment:img_1"].reason, "preview_scope_changed");
context.source.botId = "b";
await context.previewChatImage(item);
assert.equal(bridgeReads, 1);
context.bridgePreviews.dispose();

if (process.argv.includes("--transfer")) {
  const chunks = []; for await (const chunk of process.stdin) chunks.push(chunk);
  const real = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  const blob = await readChatImageBlob({ ...options, handle: real.handle, instanceId: real.instanceId,
    fetchImpl: async () => new Response(Buffer.from(real.body, "base64"), { headers: real.headers }) });
  assert.equal(blob.size, real.size);
  assert.equal(blob.type, "image/png");
}
console.log("chat image previews: real transfer contract, scoped URL lifecycle, duplicate reads, size/MIME/hash rejection, safe rendering and failure fallback passed");
