import assert from "node:assert/strict";
import { createToolExposureController } from "../src/control-center-v2/tool-exposure.js";
import { renderToolExposure } from "../src/control-center-v2/components/abilities.js";
import { createBackendControlCenterSource } from "../src/control-center/data-sources.js";

const tick = () => new Promise(resolve => setTimeout(resolve, 0));
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const data = (session = "a", revision = 0) => ({ ok: true, status: "restored", boundarySupported: true,
  backend: "memcore", scope: { sessionId: session },
  preferences: { revision, defaultMode: "resident", searchEnabled: true, toolModes: {} },
  tools: [{ id: "demo.alpha", name: "Alpha", description: "Exact purpose", source: "宿主工具",
    targetMode: "resident", currentMode: "resident", nativeContractValid: true, executionAvailable: true }] });
const reads = [], saves = [];
const source = { backendUrl: "http://backend-a", botId: "bot", profileUserId: "owner",
  readToolExposure(scope) { const d = deferred(); reads.push({ ...d, scope }); return d.promise; },
  saveToolExposure(payload, scope) { const d = deferred(); saves.push({ ...d, payload, scope }); return d.promise; } };
const controller = createToolExposureController();
controller.bind(source, { sessionId: "a", characterPackId: "one" });
assert.equal(controller.snapshot().phase, "loading");
controller.bind(source, { sessionId: "b", characterPackId: "two" });
reads[0].resolve(data("a")); await tick();
assert.equal(controller.snapshot().data, null, "old session read cannot overwrite the new scope");
reads[1].resolve(data("b")); await tick();
assert.equal(controller.snapshot().phase, "ready");
const saving = controller.save({ id: "demo.alpha", mode: "on_demand" });
assert.equal(controller.snapshot().phase, "saving");
assert.deepEqual(saves[0].payload.toolModes, { "demo.alpha": "on_demand" });
assert.equal((await controller.save({ searchEnabled: false })).status, "not_ready");
await controller.refresh();
assert.equal(reads.length, 2, "background refresh cannot supersede an in-flight save");
controller.bind(source, { sessionId: "c", characterPackId: "two" });
saves[0].resolve({ ok: true, state: data("b", 1) });
assert.equal((await saving).status, "scope_changed");
assert.equal(controller.snapshot().data, null, "late save does not publish into another session");
reads[2].resolve(data("c", 2)); await tick();
const conflicted = controller.save({ source: "宿主工具", mode: "on_demand" });
saves[1].resolve({ ok: false, reason: "tool_exposure_revision_conflict" }); await conflicted;
assert.equal(controller.snapshot().phase, "failed");
assert.match(renderToolExposure(controller.snapshot()), /别处修改/);
const refreshing = controller.refresh(); reads[3].resolve(data("c", 3)); await refreshing;
assert.equal(controller.snapshot().data.preferences.revision, 3);
const rejected = controller.save({ searchEnabled: false });
saves[2].resolve({ ok: false, reason: "tool_exposure_save_failed" }); await rejected;
assert.equal(controller.snapshot().data.preferences.searchEnabled, true, "no optimistic success on failed persistence");
assert.match(renderToolExposure({ phase: "ready", data: { ...data(), backend: "dual", boundarySupported: false } }), /dual 模式不支持按 MemCore/);
assert.match(renderToolExposure({ phase: "ready", data: { ...data(), tools: [] } }), /当前没有模型可见/);
const awaiting = data();
awaiting.searchPending = true;
awaiting.tools[0] = { ...awaiting.tools[0], currentMode: "not_published", pending: true, pendingReason: "next_request" };
const awaitingHtml = renderToolExposure({ phase: "ready", data: awaiting });
assert.match(awaitingHtml, /待下一轮生效/);
assert.match(awaitingHtml, /待下一轮发布/);
assert.doesNotMatch(awaitingHtml, /待压缩合并/);
const upgraded = data();
upgraded.tools[0] = { ...upgraded.tools[0], pending: true, pendingReason: "compaction", nativeContractValid: false };
assert.match(renderToolExposure({ phase: "ready", data: upgraded }), /声明待压缩合并/);
assert.match(renderToolExposure({ phase: "ready", data: upgraded }), /契约已失效/);
controller.stop();

const requests = [];
const backend = createBackendControlCenterSource({ baseUrl: "http://backend.test", sessionId: "default",
  profileUserId: "owner", botId: "bot", fetchImpl: async (url, options = {}) => {
    requests.push({ url, options });
    const body = new URL(url).pathname === "/health"
      ? { status: "ok", root_binding: "valid", instance_id: "test-host" } : data();
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  } });
await backend.readToolExposure({ sessionId: "active", characterPackId: "character" });
await backend.saveToolExposure(data().preferences, { sessionId: "active", characterPackId: "character" });
const sent = requests.filter(item => new URL(item.url).pathname.includes("tool-exposure"));
assert.equal(sent.length, 2);
assert.equal(new URL(sent[0].url).pathname, "/api/bots/bot/capabilities/tool-exposure");
assert.equal(new URL(sent[1].url).searchParams.get("real_user_id"), "owner");
assert.equal(new URL(sent[1].url).searchParams.get("user_id"), "active");
assert.equal(new URL(sent[1].url).searchParams.get("character_pack_id"), "character");
assert.equal(JSON.parse(sent[1].options.body).revision, 0);
console.log("tool exposure scope/save/render/bridge smoke passed");
