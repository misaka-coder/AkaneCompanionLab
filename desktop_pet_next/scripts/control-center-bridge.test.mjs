import assert from "node:assert/strict";
import test from "node:test";
import { createRuntimeConfirmation, createActionOperationId } from "../src/control-center-v2/bridge/confirmation.js";
import { createBridgeChat } from "../src/control-center-v2/bridge/chat.js";

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const tail = (label = "tail", pack = "akane", bot = "main") => ({ bot_id: bot,
  session: { session_id: "shared", character_pack_id: pack },
  messages: [{ source_id: label, seq_no: 10, content: label }], message_page: { has_more: true, next_before_seq: 10 } });
function chatFixture() {
  let source = { backendUrl: "http://localhost:9999", botId: "main", profileUserId: "master",
    sessionId: "shared", characterPackId: "akane", readChatSession: async () => tail() };
  let runtime = { state: {} }, snapshot = {}, active = true, publications = 0;
  const timers = new Map(); let sequence = 0;
  const chat = createBridgeChat({ readSource: () => source, readRuntime: () => runtime,
    readSnapshot: () => snapshot, updateSnapshot: update => { snapshot = update(snapshot); },
    isActive: () => active, publish: () => publications++,
    schedule(fn) { const id = ++sequence; timers.set(id, fn); return id; }, cancel: id => timers.delete(id) });
  return { chat, timers, get source() { return source; }, get snapshot() { return snapshot; },
    get publications() { return publications; },
    bind(next) { source = next; chat.synchronize(); },
    live(state, command = {}) { runtime = { state, settingsCommandResult: command }; chat.synchronize(); },
    stop() { active = false; chat.stop(); } };
}

function confirmationFixture() {
  let time = 0, snapshot = {}, sequence = 0;
  const timers = new Map();
  const confirmation = createRuntimeConfirmation({ readSnapshot: () => snapshot, now: () => time,
    schedule(fn) { const id = ++sequence; timers.set(id, fn); return id; }, cancel: id => timers.delete(id) });
  return { confirmation, timers, setSnapshot(value) { snapshot = value; },
    async tick(ms = 60) { time += ms; const jobs = [...timers.values()]; timers.clear(); jobs.forEach(fn => fn()); await Promise.resolve(); } };
}

test("confirmation accepts its own failed acknowledgement without inventing success", async () => {
  const f = confirmationFixture();
  const waiting = f.confirmation.wait("chat.send", { operationId: "mine" }, {});
  const snapshot = { settingsCommandResult: { command: "sendChatMessage", operationId: "mine", status: "failed", ok: false, reason: "backend_offline" } };
  f.setSnapshot(snapshot); await f.tick();
  assert.equal(await waiting, snapshot);
  assert.equal(f.timers.size, 0);
});

test("a foreign acknowledgement cannot satisfy an observed action", async () => {
  const f = confirmationFixture();
  const waiting = f.confirmation.wait("chat.send", { operationId: "mine" }, {});
  f.setSnapshot({ settingsCommandResult: { command: "sendChatMessage", operationId: "other", status: "accepted", ok: true } });
  await f.tick(); assert.equal(f.timers.size, 1);
  f.confirmation.stop(); assert.equal(await waiting, null); assert.equal(f.timers.size, 0);
});

test("closing cancels concurrent long attachment and audio waits immediately", async () => {
  const f = confirmationFixture();
  const waiting = [f.confirmation.wait("chat.attach", {}, {}), f.confirmation.wait("chat.playAttachment", {}, {})];
  assert.equal(f.timers.size, 2);
  f.confirmation.stop(); assert.deepEqual(await Promise.all(waiting), [null, null]);
  assert.equal(f.timers.size, 0);
});

test("confirmation stops when its request owner changes", async () => {
  const f = confirmationFixture(); let current = true;
  const waiting = f.confirmation.wait("chat.attach", {}, {}, () => current);
  current = false; await f.tick(); assert.equal(await waiting, null);
});

test("standard confirmation expires while attachment retains its longer window", async () => {
  const f = confirmationFixture();
  const standard = f.confirmation.wait("character.refresh", {}, {});
  const attachment = f.confirmation.wait("chat.attach", {}, {});
  await f.tick(1800); assert.equal(await standard, null); assert.equal(f.timers.size, 1);
  f.confirmation.stop(); assert.equal(await attachment, null);
});

test("operation IDs remain distinct for repeated actions in the same millisecond", () => {
  const values = Array.from({ length: 30 }, () => createActionOperationId("chat.send"));
  assert.equal(new Set(values).size, 30); assert.match(values[0], /^chat-send-/);
});

test("a slow old Bot chat read cannot block or overwrite the same session ID in a new Bot", async () => {
  const f = chatFixture(), old = deferred();
  f.source.readChatSession = () => old.promise;
  const first = f.chat.refresh();
  f.bind({ ...f.source, botId: "next", readChatSession: async () => tail("new", "akane", "next") });
  await f.chat.refresh(); assert.equal(f.snapshot.chatSession.messages[0].content, "new");
  old.resolve(tail("old")); await first;
  assert.equal(f.snapshot.chatSession.bot_id, "next");
  assert.equal(f.snapshot.chatSession.messages[0].content, "new");
});

for (const failure of ["empty", "exception"]) test(`old chat ${failure} cannot mark a new character's session failed`, async () => {
  const f = chatFixture(), old = deferred();
  f.source.readChatSession = () => old.promise;
  const first = f.chat.refresh();
  f.live({ characterPackId: "reimu" }); f.source.readChatSession = async () => tail("new", "reimu");
  await f.chat.refresh(); const published = f.publications;
  if (failure === "empty") old.resolve(null); else old.reject(Error("old_failure"));
  await first;
  assert.equal(f.snapshot.chatRuntime.status, "available"); assert.equal(f.publications, published);
});

test("A to B to A scope transitions invalidate the first A read", async () => {
  const f = chatFixture(), old = deferred();
  f.source.readChatSession = () => old.promise;
  const first = f.chat.refresh();
  f.live({ sessionId: "other" }); f.live({ sessionId: "shared" });
  f.source.readChatSession = async () => tail("new-A"); await f.chat.refresh();
  old.resolve(tail("old-A")); await first;
  assert.deepEqual(f.snapshot.chatSession.messages.map(row => row.content), ["new-A"]);
});

test("paging scopes have independent locks and old settlement cannot release the new lock", async () => {
  const f = chatFixture(), old = deferred(), next = deferred(); let calls = 0;
  f.source.readChatHistoryPage = () => { calls++; return calls === 1 ? old.promise : next.promise; };
  await f.chat.refresh(); const first = f.chat.loadOlderChatMessages();
  f.live({ characterPackId: "reimu" });
  assert.equal(f.snapshot.chatSession, undefined); assert.equal(f.snapshot.chatRuntime.status, "switching");
  f.source.readChatSession = async () => tail("new", "reimu");
  await f.chat.refresh(); const second = f.chat.loadOlderChatMessages(); assert.equal(calls, 2);
  old.resolve({ messages: [{ source_id: "wrong", seq_no: 1 }], message_page: { has_more: false } });
  assert.equal((await first).status, "stale");
  const duplicate = f.chat.loadOlderChatMessages(); assert.equal(calls, 2);
  next.resolve({ messages: [{ source_id: "older", seq_no: 1 }], message_page: { has_more: false } });
  assert.equal((await second).count, 1); assert.equal((await duplicate).count, 1);
  assert.deepEqual(f.snapshot.chatSession.messages.map(row => row.source_id), ["older", "new"]);
});

test("paging fails with its original reason and can be retried", async () => {
  const f = chatFixture(); await f.chat.refresh();
  f.source.readChatHistoryPage = async () => { throw Error("read_offline"); };
  assert.equal((await f.chat.loadOlderChatMessages()).reason, "read_offline");
  f.source.readChatHistoryPage = async () => ({ messages: [], message_page: { has_more: false } });
  assert.equal((await f.chat.loadOlderChatMessages()).ok, true);
});

test("mismatched session data and a foreign paging pack are rejected before merging", async () => {
  const f = chatFixture(); f.source.readChatSession = async () => tail("wrong", "reimu");
  await f.chat.refresh(); assert.equal(f.snapshot.chatRuntime.reason, "chat_session_scope_mismatch");
  f.source.readChatSession = async () => tail(); await f.chat.refresh();
  let reads = 0; f.source.readChatHistoryPage = async () => { reads++; return {}; };
  assert.equal((await f.chat.loadOlderChatMessages({ characterPackId: "reimu" })).status, "stale"); assert.equal(reads, 0);
});

test("profile or backend changes discard old reads even when Bot, pack and session match", async () => {
  for (const state of [{ profileUserId: "another" }, { backendUrl: "http://localhost:12001" }]) {
    const f = chatFixture(), old = deferred(); f.source.readChatSession = () => old.promise;
    const first = f.chat.refresh(); f.live(state); const publications = f.publications;
    old.resolve(tail()); await first; assert.equal(f.snapshot.chatSession, undefined); assert.equal(f.publications, publications);
    await f.chat.refresh(); assert.equal(f.publications, publications);
  }
});

test("closing cancels debounce timers and rejects late chat publications", async () => {
  const f = chatFixture(), read = deferred(); f.source.readChatSession = () => read.promise;
  const first = f.chat.refresh(); f.chat.schedule(); assert.equal(f.timers.size, 1);
  f.stop(); const publications = f.publications; read.resolve(tail()); await first;
  assert.equal(f.timers.size, 0); assert.equal(f.publications, publications);
  assert.equal((await f.chat.loadOlderChatMessages()).status, "not-available");
});

test("two own acknowledgements without timestamps both schedule a chat refresh", async () => {
  const f = chatFixture(); let reads = 0;
  f.source.readChatSession = async () => { reads++; return tail(); };
  for (const operationId of ["first", "second"]) {
    f.live({}, { command: "sendChatMessage", operationId, status: "accepted" }); f.chat.schedule();
    const jobs = [...f.timers.values()]; f.timers.clear(); jobs.forEach(fn => fn());
    await new Promise(setImmediate);
  }
  assert.equal(reads, 2); f.stop();
});
