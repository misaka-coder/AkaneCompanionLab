import assert from "node:assert/strict";
import test from "node:test";
import { createMemorySettingsController } from "../src/control-center-v2/memory-settings.js";
import { renderMemorySettings } from "../src/control-center-v2/components/memory-settings.js";
import { createMemorySettingMethods } from "../src/control-center/data-source/memory-settings.js";

const shared = "OWNER_PRIVATE_SESSION_SHARED", mirror = "OWNER_PRIVATE_GROUP_MEMORY_MIRROR";
const state = () => ({ ok: true, botId: "one", characterPackId: "cecilia", processId: 10,
  qqAvailable: true, groupMemoryRunning: false, groupNamesAvailable: true,
  settings: [shared, mirror].map(key => ({ key, current: false, desired: false, pendingRestart: false })),
  groups: [{ groupId: "8700", name: "伙伴们", enabled: false, hasCharacterMemory: true }] });
const tick = () => new Promise(setImmediate);
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const source = (extra = {}) => ({ backendUrl: "http://127.0.0.1:12001", botId: "one",
  readMemorySettings: async () => ({ ...state(), botId: extra.botId || "one" }), updateSetting: async (key, value) => ({ ok: true,
    key, current: false, desired: value, pendingRestart: value }),
  saveMemoryReceiver: async (groupId, enabled) => ({ ok: true, groupId, enabled }), ...extra });

test("saved restart flags retain actual running state and can be reverted without restart", async () => {
  const c = createMemorySettingsController(); c.bind(source()); await tick();
  await c.run(`memory.setting.${mirror}`, { value: true });
  let row = c.snapshot().data.settings.find(row => row.key === mirror);
  assert.equal(row.current, false); assert.equal(row.desired, true); assert.equal(row.pendingRestart, true);
  await c.run(`memory.setting.${mirror}`, { value: false });
  row = c.snapshot().data.settings.find(row => row.key === mirror);
  assert.equal(row.pendingRestart, false);
});

test("failed save retains acknowledged consent and releases the button for retry", async () => {
  const c = createMemorySettingsController(); const s = source({ saveMemoryReceiver: async () => ({ ok: false,
    reason: "private_memory_preference_save_failed" }) });
  c.bind(s); await tick(); await c.run("memory.group.8700", { value: true });
  assert.equal(c.snapshot().data.groups[0].enabled, false);
  assert.equal(c.snapshot().error, "private_memory_preference_save_failed");
  assert.equal(c.snapshot().phase, "ready");
  s.saveMemoryReceiver = source().saveMemoryReceiver;
  assert.equal((await c.run("memory.group.8700", { value: true })).ok, true);
});

test("late read and save cannot overwrite a new Bot or an A to B to A selection", async () => {
  const pending = deferred(), c = createMemorySettingsController();
  const first = source({ readMemorySettings: () => pending.promise }), second = source({ botId: "two" });
  c.bind(first); c.bind(second); await tick();
  const latest = c.snapshot().data;
  pending.resolve({ ...state(), groups: [] }); await tick(); assert.equal(c.snapshot().data, latest);
  const save = deferred(); first.readMemorySettings = async () => state(); first.saveMemoryReceiver = () => save.promise;
  c.bind(first); await tick(); const request = c.run("memory.group.8700", { value: true });
  c.bind(second); c.bind(first); await tick();
  save.resolve({ ok: true, enabled: true });
  assert.equal((await request).status, "stale"); assert.equal(c.snapshot().data.groups[0].enabled, false);
});

test("duplicate saves and refresh during a pending save cannot replace the acknowledgement", async () => {
  const pending = deferred(), c = createMemorySettingsController(); let saves = 0;
  c.bind(source({ saveMemoryReceiver: () => { saves++; return pending.promise; } })); await tick();
  const request = c.run("memory.group.8700", { value: true });
  assert.equal((await c.run("memory.group.8700", { value: true })).ok, false);
  assert.equal((await c.refresh()).status, "busy"); assert.equal(saves, 1);
  pending.resolve({ ok: true, enabled: true }); await request;
  assert.equal(c.snapshot().data.groups[0].enabled, true);
});

test("restart verifies newly applied settings and preserves failure reasons", async () => {
  let applied = false, fail = false;
  const c = createMemorySettingsController({ desktop: true, invoke: async (_, { request }) => {
    if (request.action === "detect") return { ok: true };
    if (fail) return { ok: false, reason: "backend_restart_wait_timeout" };
    applied = true; return { ok: true, status: "completed", observed: true };
  } });
  c.bind(source({ readMemorySettings: async () => ({ ...state(), settings: [{ key: mirror,
    current: applied, desired: true, pendingRestart: !applied }] }) })); await tick();
  assert.equal(c.snapshot().restartAvailable, true);
  fail = true; assert.equal((await c.run("memory.restart")).ok, false);
  assert.equal(c.snapshot().data.settings[0].pendingRestart, true);
  assert.equal(c.snapshot().error, "backend_restart_wait_timeout");
  fail = false; assert.equal((await c.run("memory.restart")).ok, true);
  assert.equal(c.snapshot().data.settings[0].pendingRestart, false);
});

test("restart handoff without confirmed application never reports settings applied", async () => {
  const c = createMemorySettingsController({ desktop: true, invoke: async () => ({ ok: true }) });
  c.bind(source({ readMemorySettings: async () => ({ ...state(), settings: [{ key: mirror,
    current: false, desired: true, pendingRestart: true }] }) })); await tick();
  const result = await c.run("memory.restart");
  assert.equal(result.ok, false); assert.equal(result.reason, "settings_apply_not_confirmed");
});

test("ordinary publications do not reread groups, and closing discards a pending request", async () => {
  const pending = deferred(), c = createMemorySettingsController(); let reads = 0;
  const s = source({ readMemorySettings: () => { reads++; return pending.promise; } });
  for (let i = 0; i < 30; i++) c.bind(s, { characterPackId: "cecilia" });
  assert.equal(reads, 1); c.stop(); pending.resolve(state()); await tick();
  assert.equal(c.snapshot().data, null);
});

test("foreign Bot or character metadata cannot populate the controls", async () => {
  for (const data of [{ ...state(), botId: "foreign" }, { ...state(), characterPackId: "reimu" }]) {
    const c = createMemorySettingsController();
    c.bind(source({ readMemorySettings: async () => data }), { characterPackId: "cecilia" });
    await tick();
    assert.equal(c.snapshot().phase, "failed");
    assert.equal(c.snapshot().error, "memory_settings_scope_mismatch");
    assert.equal(c.snapshot().data, null);
  }
});

test("group name is escaped and filtered, offline, empty and unavailable restart are explicit", () => {
  const data = state(); data.groups[0].name = '<img src=x onerror="alert(1)">';
  data.settings[1].pendingRestart = true; data.groupNamesAvailable = false;
  const vm = { system: { memorySettings: { data, phase: "ready", restartAvailable: false } } };
  const html = renderMemorySettings({ viewModel: vm });
  assert.ok(!html.includes('<img src=x')); assert.ok(html.includes("&lt;img"));
  assert.ok(html.includes("待重启")); assert.ok(html.includes("请使用本地启动入口重启"));
  assert.ok(html.includes("QQ 群名称暂不可用"));
  assert.ok(renderMemorySettings({ viewModel: vm, memoryGroupFilter: "unmatched" }).includes("没有匹配的群"));
});

test("data source targets the bound Bot and rejects a failed HTTP save even if body says ok", async () => {
  const calls = [];
  const methods = createMemorySettingMethods({ botBaseUrl: "http://127.0.0.1:12001/api/bots/one",
    ensureVerifiedBackend: async () => true,
    fetchImpl: async (url, init) => { calls.push({ url, init }); return new Response(JSON.stringify({ ok: true }),
      { status: 503, headers: { "Content-Type": "application/json" } }); } });
  const result = await methods.saveMemoryReceiver("8700", true, { characterPackId: "cecilia" });
  assert.equal(result.ok, false);
  assert.equal(new URL(calls[0].url).pathname, "/api/bots/one/control-center/memory-settings/groups/8700");
  assert.deepEqual(JSON.parse(calls[0].init.body), { enabled: true, characterPackId: "cecilia" });
});
