import assert from "node:assert/strict";
import test from "node:test";
import * as facade from "../src/control-center-v2/bridge.js";
import { createControlCenterRuntime } from "../src/control-center-v2/bridge/runtime.js";
import { createBridgeSubscriptions } from "../src/control-center-v2/bridge/subscriptions.js";
import { createBridgeSnapshots } from "../src/control-center-v2/bridge/snapshots.js";

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const flush = () => new Promise(setImmediate);
const snapshot = name => ({ sourceKind: "backend", backendUrl: "http://localhost:9999", characterRuntime: { displayName: name } });
const session = (name, bot = "main", pack = "akane", id = "shared") => ({ bot_id: bot,
  session: { session_id: id, character_pack_id: pack }, messages: [{ source_id: name, seq_no: 10, role: "assistant", content: name }],
  message_page: { has_more: true, next_before_seq: 10 } });
const source = (name = "main", extra = {}) => ({ backendUrl: "http://localhost:9999", botId: "main",
  profileUserId: "master", sessionId: "shared", characterPackId: "akane",
  readInitialState: () => ({ ...snapshot(name), fallbackReason: "bootstrap" }),
  readSnapshot: async () => snapshot(name), readChatSession: async () => session(name),
  runAction: async () => ({ ok: false, reason: "fixture_unavailable" }), ...extra });

function runtimeFixture(t, { native = false, serializeAttachments } = {}) {
  let currentSource = source(), created = 0;
  const runtimeListeners = new Set(), dropListeners = new Set(), published = [];
  const host = { isTauri: native, invoke: async () => ({ ok: false, reason: "fixture_unavailable" }),
    createSourceOptions: async () => currentSource, requestSnapshot: async () => {},
    listenSnapshots: async callback => { runtimeListeners.add(callback); return () => runtimeListeners.delete(callback); },
    listenFileDrops: async callback => { dropListeners.add(callback); return () => dropListeners.delete(callback); } };
  const bridge = createControlCenterRuntime({ host, serializeAttachments,
    createDataSource(options) { created++; return options; }, createRouter: ({ dataSource }) => ({ run: (...args) => dataSource.runAction(...args) }) });
  bridge.subscribe(vm => published.push(vm)); t.after(() => bridge.stop());
  return { bridge, host, published, runtimeListeners, dropListeners,
    get created() { return created; }, get source() { return currentSource; }, bind(next) { currentSource = next; },
    emit(state = {}, settingsCommandResult = {}) { for (const callback of runtimeListeners) callback({ state, settingsCommandResult }); } };
}

test("the public bridge facade retains its two exports", () => {
  assert.deepEqual(Object.keys(facade).sort(), ["createControlCenterBridge", "shouldUseInstanceAdminProxy"]);
});

for (const failure of [false, true]) test(`a replaced required snapshot ${failure ? "failure" : "success"} cannot enter the new source`, async t => {
  const f = runtimeFixture(t), old = deferred();
  f.bind(source("old", { readSnapshot: () => old.promise })); const first = f.bridge.start(); await flush();
  f.bind(source("new")); await f.bridge.start(); const count = f.published.length;
  if (failure) old.reject(Error("old_offline")); else old.resolve(snapshot("old"));
  assert.equal(await first, null); assert.equal(f.published.length, count);
  assert.equal(f.published.at(-1).chat.messages[0].content, "new");
});

test("closing during bootstrap prevents data source creation and all late publication", async t => {
  const f = runtimeFixture(t), bootstrap = deferred(); f.host.createSourceOptions = () => bootstrap.promise;
  const started = f.bridge.start(); f.bridge.stop(); bootstrap.resolve(source());
  assert.equal(await started, null); assert.equal(f.created, 0); assert.equal(f.published.length, 0);
});

test("a newer start owns bootstrap even if the old one fails later", async t => {
  const f = runtimeFixture(t), old = deferred(); let calls = 0;
  f.host.createSourceOptions = () => ++calls === 1 ? old.promise : Promise.resolve(source("new"));
  const first = f.bridge.start(); await f.bridge.start(); old.reject(Error("old_bootstrap_failed"));
  assert.equal(await first, null); assert.equal(f.created, 1); assert.equal(f.published.at(-1).chat.messages[0].content, "new");
});

test("current bootstrap failures retain their real reason", async t => {
  const f = runtimeFixture(t); f.host.createSourceOptions = async () => { throw Error("client_instance_binding_mismatch"); };
  await assert.rejects(f.bridge.start(), /client_instance_binding_mismatch/); assert.equal(f.created, 0);
});

test("repeated native starts leave one snapshot listener and one file-drop listener", async t => {
  const f = runtimeFixture(t, { native: true }); let dropped = 0; f.bridge.onFileDrop(() => dropped++);
  await f.bridge.start(); await f.bridge.start();
  assert.equal(f.runtimeListeners.size, 1); assert.equal(f.dropListeners.size, 1);
  for (const callback of f.dropListeners) callback(["fixture.txt"]); assert.equal(dropped, 1);
  f.bridge.stop(); assert.equal(f.runtimeListeners.size, 0); assert.equal(f.dropListeners.size, 0);
});

for (const listener of ["listenSnapshots", "listenFileDrops"]) test(`a late ${listener} disposer is released after close`, async t => {
  const f = runtimeFixture(t, { native: true }), registration = deferred(); let disposed = 0;
  f.host[listener] = () => registration.promise;
  const started = f.bridge.start(); await flush(); f.bridge.stop(); const count = f.published.length;
  registration.resolve(() => disposed++); assert.equal(await started, null);
  assert.equal(disposed, 1); assert.equal(f.published.length, count); assert.equal(f.runtimeListeners.size, 0);
});

test("an old listener registration failure cannot dispose the new lifetime's listeners", async () => {
  const old = deferred(); let calls = 0, active = 0, failures = 0;
  const subscriptions = createBridgeSubscriptions({ host: { isTauri: true,
    listenSnapshots: async () => { active++; return () => active--; }, requestSnapshot: async () => {},
    listenFileDrops: () => ++calls === 1 ? old.promise : Promise.resolve(() => {}) },
    onRuntime() {}, onFileDrop() {}, onUnavailable() { failures++; } });
  const first = subscriptions.connect(subscriptions.begin()); await flush();
  await subscriptions.connect(subscriptions.begin()); old.reject(Error("old_registration_failed")); await first;
  assert.equal(active, 1); assert.equal(failures, 0); subscriptions.stop(); assert.equal(active, 0);
});

test("a current subscription failure releases its partial listeners and stays observable", async () => {
  let active = 0, reason = "";
  const subscriptions = createBridgeSubscriptions({ host: { isTauri: true,
    listenSnapshots: async () => { active++; return () => active--; }, requestSnapshot: async () => { throw Error("event_delivery_failed"); } },
    onRuntime() {}, onFileDrop() {}, onUnavailable(value) { reason = value; } });
  await subscriptions.connect(subscriptions.begin()); assert.equal(active, 0); assert.equal(reason, "event_delivery_failed"); subscriptions.stop();
});

test("subscribing a second listener delivers its cached state without waking the first", async t => {
  const f = runtimeFixture(t); await f.bridge.start(); const count = f.published.length; let received = 0;
  f.bridge.subscribe(() => received++); assert.equal(received, 1); assert.equal(f.published.length, count);
});

test("new session refresh can finish while the previous session's tail remains pending", async t => {
  const f = runtimeFixture(t, { native: true }), old = deferred(); let snapshots = 0;
  f.bind(source("main", { readSnapshot: async () => { snapshots++; return snapshot("main"); },
    readChatSession: options => options.sessionId === "shared" ? old.promise : Promise.resolve(session("new", "main", "akane", "next")) }));
  const started = f.bridge.start(); await flush();
  f.emit({ sessionId: "next" }); await f.bridge.refresh();
  assert.equal(snapshots, 2); assert.equal(f.published.at(-1).chat.messages[0].content, "new");
  old.resolve(session("old")); await started; assert.equal(f.published.at(-1).chat.messages[0].content, "new");
});

test("required snapshot failure keeps its reason and can reconnect", async t => {
  const f = runtimeFixture(t); f.source.readSnapshot = async () => null; f.source.getFallbackReason = () => "snapshot_offline";
  await assert.rejects(f.bridge.start(), /snapshot_offline/); assert.equal(f.published.at(-1).shell.connected, false);
  f.source.readSnapshot = async () => snapshot("online"); await f.bridge.refresh(); assert.equal(f.published.at(-1).shell.connected, true);
});

test("optional catalog failures are independent, honest and do not block the main snapshot", async () => {
  let raw = {}, epoch = 1; const market = deferred();
  const requestSource = source("main", { readPluginCatalog: async () => { throw Error("catalog_offline"); },
    readPluginManagement: async () => ({ ok: true, data: { ready: true } }), readPluginMarket: () => market.promise });
  const snapshots = createBridgeSnapshots({ captureBinding: () => ({ source: requestSource, epoch }),
    isCurrentBinding: ticket => ticket.epoch === epoch, readSnapshot: () => raw, writeSnapshot: value => { raw = value; },
    publish() {}, readViewModel: () => raw, refreshChatSession: async () => {} });
  await snapshots.refresh(); await flush();
  assert.equal(raw.pluginRuntime.reason, "catalog_offline"); assert.equal(raw.pluginManagementRuntime.status, "available");
  assert.equal(raw.pluginMarketRuntime.status, "loading"); epoch++; snapshots.reset(); const before = raw;
  market.resolve({ ok: true, data: { foreign: true } }); await flush(); assert.equal(raw, before);
});

test("late browser attachment serialization cannot dispatch into a new character", async t => {
  const serialized = deferred(), f = runtimeFixture(t, { native: true, serializeAttachments: () => serialized.promise });
  let dispatched = 0; f.source.runAction = async () => { dispatched++; return { ok: true }; };
  await f.bridge.start(); dispatched = 0;
  const action = f.bridge.runAction("chat.attach", { browserFiles: [{}] });
  f.emit({ characterPackId: "reimu" }); serialized.resolve([{ name: "fixture.txt" }]);
  assert.equal((await action).status, "stale"); assert.equal(dispatched, 0);
});

test("closing resolves a long native confirmation without performing later refresh", async t => {
  const f = runtimeFixture(t, { native: true }); f.source.runAction = async () => ({ ok: true });
  await f.bridge.start(); const action = f.bridge.runAction("chat.attach", { files: [] }); await flush();
  f.bridge.stop(); assert.equal((await action).status, "stale");
});

test("observed native failure preserves the operation's original reason", async t => {
  const f = runtimeFixture(t, { native: true });
  f.source.runAction = async (action, payload) => {
    if (action !== "character.refresh") return { ok: false, reason: "fixture_unavailable" };
    f.emit({}, { command: "reloadResources", operationId: payload.operationId, status: "failed", ok: false, reason: "resource_reload_failed" });
    return { ok: true, status: "dispatched" };
  };
  await f.bridge.start(); const result = await f.bridge.runAction("character.refresh");
  assert.equal(result.ok, false); assert.equal(result.observed, true); assert.equal(result.reason, "resource_reload_failed");
});

test("Bot switching rebinds only after its own acknowledgement and a matching persisted binding", async t => {
  const f = runtimeFixture(t, { native: true });
  f.source.runAction = async (action, payload) => {
    if (action !== "settings.selectBot") return { ok: false, reason: "fixture_unavailable" };
    f.bind(source("next", { botId: "next", readChatSession: async () => session("next", "next") }));
    f.emit({ boundBotId: "next" }, { command: "setBoundBot", operationId: payload.operationId, status: "completed", ok: true });
    return { ok: true };
  };
  await f.bridge.start(); const before = f.created;
  const result = await f.bridge.runAction("settings.selectBot", { value: "next" });
  assert.equal(result.ok, true); assert.equal(result.observed, true); assert.equal(f.created, before + 1);
  assert.equal(f.published.at(-1).chat.messages[0].content, "next");
});

test("a delayed Bot rebind cannot replace a newer runtime selection", async t => {
  const f = runtimeFixture(t, { native: true }), binding = deferred();
  f.source.runAction = async (action, payload) => {
    if (action !== "settings.selectBot") return { ok: false, reason: "fixture_unavailable" };
    f.emit({ boundBotId: "next" }, { command: "setBoundBot", operationId: payload.operationId, status: "completed", ok: true });
    return { ok: true };
  };
  await f.bridge.start(); f.host.createSourceOptions = () => binding.promise;
  const action = f.bridge.runAction("settings.selectBot", { value: "next" }); await flush();
  f.emit({ boundBotId: "third" }); binding.resolve({ ...f.source, botId: "next" });
  assert.equal((await action).status, "stale"); assert.equal(f.created, 1);
});

test("a native acknowledgement cannot rebind to a conflicting persisted Bot", async t => {
  const f = runtimeFixture(t, { native: true });
  f.source.runAction = async (action, payload) => {
    if (action !== "settings.selectBot") return { ok: false, reason: "fixture_unavailable" };
    f.emit({ boundBotId: "next" }, { command: "setBoundBot", operationId: payload.operationId, status: "completed", ok: true });
    return { ok: true };
  };
  await f.bridge.start(); assert.equal((await f.bridge.runAction("settings.selectBot", { value: "next" })).status, "stale");
  assert.equal(f.created, 1);
});

test("closing during a required snapshot discards late failures without publishing", async t => {
  const f = runtimeFixture(t), pending = deferred(); f.source.readSnapshot = () => pending.promise;
  const started = f.bridge.start(); await flush(); f.bridge.stop(); const count = f.published.length;
  pending.reject(Error("late_offline")); assert.equal(await started, null); assert.equal(f.published.length, count);
});

test("required snapshots share the current binding's pending read", async () => {
  const pending = deferred(); let reads = 0, raw = {};
  const requestSource = source("main", { readSnapshot: () => { reads++; return pending.promise; } });
  const snapshots = createBridgeSnapshots({ captureBinding: () => ({ source: requestSource }), isCurrentBinding: () => true,
    readSnapshot: () => raw, writeSnapshot: value => { raw = value; }, publish() {}, readViewModel: () => raw, refreshChatSession: async () => {} });
  const first = snapshots.refresh(), second = snapshots.refresh(); assert.equal(reads, 1);
  pending.resolve(snapshot("main")); await Promise.all([first, second]); assert.equal(reads, 1);
});

test("a new lifetime rejects an old model read even when reusing the same source object", async () => {
  const oldModel = deferred(); let epoch = 1, models = 0, raw = {};
  const requestSource = source("main", { readModelService: () => ++models === 1 ? oldModel.promise : Promise.resolve({ marker: "new" }) });
  const snapshots = createBridgeSnapshots({ captureBinding: () => ({ source: requestSource, epoch }), isCurrentBinding: ticket => ticket.epoch === epoch,
    readSnapshot: () => raw, writeSnapshot: value => { raw = value; }, publish() {}, readViewModel: () => raw, refreshChatSession: async () => {} });
  const first = snapshots.refresh(); await flush(); epoch++; snapshots.reset(); await snapshots.refresh();
  oldModel.resolve({ marker: "old" }); assert.equal(await first, null); assert.equal(raw.modelRuntime.marker, "new");
});

test("an old dispatched action cannot trigger refresh after a rebind", async t => {
  const f = runtimeFixture(t), old = deferred(); let snapshots = 0;
  f.source.runAction = () => old.promise; await f.bridge.start();
  const action = f.bridge.runAction("abilities.plugin.enable", { pluginId: "fixture" });
  f.bind(source("new", { readSnapshot: async () => { snapshots++; return snapshot("new"); } })); await f.bridge.start();
  old.resolve({ ok: true, refresh: true }); assert.equal((await action).status, "stale"); assert.equal(snapshots, 1);
});

test("backend actions cannot target the old Bot while runtime already displays another", async t => {
  const f = runtimeFixture(t, { native: true }); await f.bridge.start(); let dispatched = 0;
  f.source.runAction = async () => { dispatched++; return { ok: true }; }; f.emit({ boundBotId: "next" });
  assert.equal((await f.bridge.runAction("abilities.plugin.enable", { pluginId: "fixture" })).status, "stale");
  assert.equal(dispatched, 0);
});
