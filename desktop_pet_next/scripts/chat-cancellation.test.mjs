import assert from "node:assert/strict";
import { after, test } from "node:test";
import { loadPetRuntime } from "./pet-runtime-harness.mjs";
import { createDesktopConversationFetch } from "../src/desktop-conversation-transport.js";

const runtime = await loadPetRuntime();
const { createChatController } = await runtime.load("/src/pet/chat.js");
const { createBackendController } = await runtime.load("/src/pet/backend.js");
after(() => runtime.close());

function harness({ fetch, frames, lyrics } = {}) {
  const statuses = [], requests = [], timers = new Map();
  let revision = 1, elapsed = 0;
  const window = { performance, localStorage: { getItem: () => null } };
  const scope = { capture: () => revision, isCurrent: value => value === revision };
  const state = { sessionId: "isolated" };
  const lifecycle = { disposed: false,
    setTimeout(fn, ms) { const id = Symbol(); timers.set(id, { fn, ms, due: elapsed + ms }); return id; },
    clearTimeout(id) { timers.delete(id); } };
  const platform = { window, isTauriRuntime: true, backendFetch: async (url, init) => {
    requests.push({ url, init });
    return fetch ? fetch(url, init) : new Response('{"type":"turn_start"}\n');
  } };
  const backend = createBackendController({ platform, state });
  const chat = createChatController({ state, resourceState: {}, els: {}, scope, platform, lifecycle,
    perception: { latestDesktopScreenFramesForThink: () => frames || Promise.resolve([]) },
    character: { getCurrentCharacterPackId: () => "test", buildCurrentVisual: () => ({}) },
    bubble: { buildClientCapabilities: () => [], resetStreamedReplySegments() {}, showThinking() {}, showBubbleText() {}, hideBubble() {} },
    persistence: { getProfileUserId: () => "master" },
    backend: { buildBackendEndpointUrl: () => "http://127.0.0.1:9999/think", readNdjsonEvents: backend.readNdjsonEvents },
    music: { buildDesktopMusicActivity: () => ({}) },
    systemMedia: { systemMediaLyrics: {}, refreshSystemMediaSnapshot: async () => ({}),
      hydrateSystemMediaLyricsForTurn: () => lyrics || Promise.resolve({}),
      shouldWaitForSystemMediaLyricsForTurn: (_message, options) => options.waitForLyricsHydration === true },
    care: { buildDesktopCareContext: () => null, getCareFeatureStatus: () => ({ enabled: false }) },
    status: { setRuntimeStatus: text => statuses.push(text) },
    tts: { flushSpeechUnitAudio() {}, flushStreamingTtsPending() {} }
  });
  return { chat, backend, statuses, requests, timers, switchSession() { revision++; },
    advance(ms) {
      elapsed += ms;
      for (const [id, timer] of [...timers]) {
        if (timer.due <= elapsed) { timers.delete(id); timer.fn(); }
      }
    }
  };
}

test("native chat passes its cancellation signal through the live body reader", { timeout: 1000 }, async () => {
  let writer;
  const body = new ReadableStream({ start(controller) {
    writer = controller;
    controller.enqueue(new TextEncoder().encode('{"type":"turn_start"}\n'));
  } });
  const h = harness({ fetch: (_url, init) => {
    assert.ok(init.signal instanceof AbortSignal);
    init.signal.addEventListener("abort", () => writer.error(init.signal.reason), { once: true });
    return new Response(body);
  } });
  const stream = h.chat.sendThinkStream("test", h.chat.beginTurn("user"));
  assert.equal((await stream.next()).value.type, "turn_start");
  h.chat.thinkController.abort();
  await assert.rejects(stream.next(), error => error.name === "AbortError");
  assert.equal(h.chat.thinkController, null);
  assert.equal(h.timers.size, 0);
  assert.equal(body.locked, false);
});

test("ending consumption cancels an open response and releases its reader", async () => {
  let cancelled = false;
  const body = new ReadableStream({ start(controller) {
    controller.enqueue(new TextEncoder().encode('{"type":"stream_start"}\n'));
  }, cancel() { cancelled = true; } });
  const h = harness({ fetch: () => new Response(body) });
  const stream = h.chat.sendThinkStream("test", h.chat.beginTurn("user"));
  await stream.next();
  await stream.return();
  assert.equal(cancelled, true);
  assert.equal(body.locked, false);
  assert.equal(h.timers.size, 0);
});

test("tool events and heartbeats renew the idle timeout throughout a long turn", async () => {
  let writer;
  const body = new ReadableStream({ start(controller) { writer = controller; } });
  const h = harness({ fetch: (_url, init) => {
    init.signal.addEventListener("abort", () => writer.error(init.signal.reason), { once: true });
    return new Response(body);
  } });
  const stream = h.chat.sendThinkStream("long coding task", h.chat.beginTurn("user"));
  let previousTimer;
  for (const type of ["stream_start", "turn_start", "assistant_working", "turn_progress", "turn_progress", "final"]) {
    h.advance(90_000); // Nine minutes in total, with continuous observable work.
    writer.enqueue(new TextEncoder().encode(JSON.stringify({ type }) + "\n"));
    assert.equal((await stream.next()).value.type, type);
    assert.equal(h.chat.thinkController.signal.aborted, false);
    assert.equal(h.timers.size, 1);
    const [timerId, timer] = [...h.timers][0];
    assert.equal(timer.ms, 300_000);
    assert.notEqual(timerId, previousTimer);
    if (previousTimer) assert.equal(h.timers.has(previousTimer), false);
    previousTimer = timerId;
  }
  writer.close();
  assert.equal((await stream.next()).done, true);
  assert.equal(h.timers.size, 0);
});

test("a silent stream still times out and releases its resources", async () => {
  let writer;
  const body = new ReadableStream({ start(controller) {
    writer = controller;
    controller.enqueue(new TextEncoder().encode('{"type":"turn_start"}\n'));
  } });
  const h = harness({ fetch: (_url, init) => {
    init.signal.addEventListener("abort", () => writer.error(init.signal.reason), { once: true });
    return new Response(body);
  } });
  const stream = h.chat.sendThinkStream("test", h.chat.beginTurn("user"));
  await stream.next();
  [...h.timers.values()][0].fn();
  await assert.rejects(stream.next(), error => error.name === "TimeoutError");
  assert.equal(h.chat.thinkController, null);
  assert.equal(h.timers.size, 0);
  assert.equal(body.locked, false);
});

for (const phase of ["frames", "lyrics"]) {
  test(`cancelling during ${phase} preparation does not submit a late message`, { timeout: 1000 }, async () => {
    const h = harness({ [phase]: new Promise(() => {}) });
    const stream = h.chat.sendThinkStream("test", h.chat.beginTurn("user"), { waitForLyricsHydration: true });
    const pending = stream.next();
    h.chat.thinkController.abort();
    await assert.rejects(pending, error => error.name === "AbortError");
    assert.equal(h.requests.length, 0);
    assert.equal(h.chat.thinkController, null);
    assert.equal(h.timers.size, 0);
  });
}

test("credential wait cancels immediately while another waiter can still send", { timeout: 1000 }, async () => {
  let release, issued = 0;
  const ready = new Promise(resolve => { release = resolve; });
  const sent = [];
  const fetch = createDesktopConversationFetch({ invoke: async () => {
    issued++;
    await ready;
    return { ok: true, body: JSON.stringify({ ok: true, token: "isolated", expiresAt: Date.now() + 600_000 }) };
  }, fetch: async (_input, init) => { sent.push(init.body); return new Response("ok"); } });
  const endpoint = "http://127.0.0.1:9999/think";
  const abort = new AbortController();
  const first = fetch(endpoint, { method: "POST", body: "cancelled", signal: abort.signal });
  const second = fetch(endpoint, { method: "POST", body: "current" });
  abort.abort();
  await assert.rejects(first, error => error.name === "AbortError");
  assert.deepEqual(sent, []);
  release();
  await second;
  assert.equal(issued, 1);
  assert.deepEqual(sent, ["current"]);
});

test("receipt, model wait and retries remain distinct during a slow reply", async () => {
  const h = harness();
  const token = h.chat.beginTurn("user");
  async function* events() {
    yield { type: "stream_start" };
    yield { type: "turn_start" };
    yield { type: "turn_progress", phase: "waiting_response", elapsed_ms: 5000 };
    yield { type: "turn_progress", phase: "retrying_model" };
    yield { type: "turn_progress", phase: "waiting_response", elapsed_ms: 15000 };
  }
  assert.equal(await h.chat.processThinkStream(events(), token), false);
  assert.ok(h.statuses.some(text => text.includes("消息已收到")));
  assert.ok(h.statuses.some(text => text.includes("等待模型回复") && text.includes("5 秒")));
  assert.ok(h.statuses.at(-1).includes("正在重试") && h.statuses.at(-1).includes("15 秒"));
  const before = h.statuses.length;
  h.switchSession();
  await h.chat.processThinkStream(events(), token);
  assert.equal(h.statuses.length, before);
});
