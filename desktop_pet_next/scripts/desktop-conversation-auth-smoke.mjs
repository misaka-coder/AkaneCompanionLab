import assert from "node:assert/strict";
import { createDesktopConversationFetch } from "../src/desktop-conversation-transport.js";
import { createPlatform } from "../src/pet/platform.js";

const endpoint = "http://127.0.0.1:9999/api/bots/one/think";
let issued = 0, now = 1_000;
const calls = [], sent = [];
let rejectIssue = false, rejectCredential = false;
let holdIssue = null;
const invoke = async (command, payload) => {
  calls.push({ command, payload });
  if (holdIssue) await holdIssue;
  if (rejectIssue) return { ok: false, reason: "instance_mismatch" };
  const token = `chat-${++issued}`;
  return { ok: true, body: JSON.stringify({ ok: true, token, expiresAt: now + 600_000 }) };
};
const transport = createDesktopConversationFetch({ invoke, now: () => now, fetch: async (input, init) => {
  sent.push({ input, init });
  if (rejectCredential) { rejectCredential = false; return new Response("expired", { status: 401 }); }
  return new Response("ok");
} });

await Promise.all([transport(endpoint, { method: "POST", body: "first" }), transport(endpoint, { method: "POST", body: "second" })]);
assert.equal(calls.length, 1);
assert.equal(sent.length, 2);
assert.equal(sent[0].init.headers.get("x-akane-desktop-chat-token"), "chat-1");
assert.equal(sent[0].init.headers.get("Authorization"), null);
assert.equal(calls[0].command, "backend_admin_request");
assert.ok(calls[0].payload.request.url.endsWith("/api/bots/one/control-center/desktop-chat-session"));

rejectCredential = true;
await transport(endpoint, { method: "POST", body: "retry" });
assert.equal(calls.length, 2);
assert.equal(sent.at(-1).init.headers.get("x-akane-desktop-chat-token"), "chat-2");
assert.equal(sent.at(-1).init.body, "retry");

now += 600_000;
await transport(endpoint, { method: "POST" });
assert.equal(calls.length, 3);
await transport(endpoint.replace("/one/", "/two/"), { method: "POST" });
assert.equal(calls.length, 4);
await transport(endpoint.replace("/think", "/desktop-pet/care/shop"), { method: "POST" });
assert.equal(calls.length, 4);
assert.equal(sent.at(-1).init.headers, undefined);

const protocols = await transport.websocketProtocols(endpoint.replace("http:", "ws:").replace("/think", "/voice/realtime"));
assert.match(protocols[0], /^akane\.desktop-chat\.chat-/);
assert.ok(!calls.at(-1).payload.request.url.includes("token"));

rejectIssue = true;
await assert.rejects(transport(endpoint.replace("/one/", "/failed/"), { method: "POST" }), /instance_mismatch/);
rejectIssue = false;
await transport(endpoint.replace("/one/", "/failed/"), { method: "POST" });

let finishIssue;
holdIssue = new Promise(resolve => { finishIssue = resolve; });
const cancel = new AbortController();
const before = sent.length;
const cancelled = transport(endpoint.replace("/one/", "/cancelled/"), { method: "POST", signal: cancel.signal });
cancel.abort();
finishIssue();
await assert.rejects(cancelled, error => error.name === "AbortError");
assert.equal(sent.length, before);
holdIssue = null;

// The first speech chunk is readable while the producer still holds the stream open.
let release;
const stream = new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode("first\n")); release = () => controller.close(); } });
const streamed = createDesktopConversationFetch({ invoke, now: () => now, fetch: async () => new Response(stream) });
const response = await streamed(endpoint, { method: "POST" });
const reader = response.body.getReader();
assert.equal(new TextDecoder().decode((await reader.read()).value), "first\n");
release();
assert.equal((await reader.read()).done, true);
now = Date.now();
const platform = createPlatform({ window: { __TAURI_INTERNALS__: {}, navigator: {} }, document: {},
  getCurrentWindow: () => ({}), invoke, tauriFetch: async (_url, init) => {
    assert.ok(init.headers.get("X-Akane-Desktop-Chat-Token"));
    return new Response("native platform connected");
  } });
assert.equal(await (await platform.backendFetch(endpoint, { method: "POST" })).text(), "native platform connected");
assert.match((await platform.voiceSocketProtocols(endpoint.replace("/think", "/voice/realtime")))[0], /^akane\.desktop-chat\./);
console.log("desktop conversation auth smoke: ok");
