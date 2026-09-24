import assert from "node:assert/strict";
import { createQqSetupController, isQqSetupAction, normalizeQqSetup, QQ_SETUP_ACTIONS as A } from "../src/control-center-v2/qq-setup.js";
import { createControlCenterViewModel } from "../src/control-center-v2/view-model.js";
import { renderSystem } from "../src/control-center-v2/components/system.js";
import { createInitialControlCenterState } from "../src/control-center-v2/store.js";
import { createBackendControlCenterSource } from "../src/control-center/data-sources.js";

const ready = { ok: true, status: "available", installed: true, configured: true, webuiReady: true, botQq: "123456789", token: "never-render", path: "never-render" };
const checked = { ok: true, status: "connected", payload: { botQq: "123456789" } };
let latest;
const controller = createQqSetupController({ onChange: (v) => { latest = v; } });
const calls = [];
const source = { async runAction(id) { calls.push(id); return id === A.check ? checked : ready; } };
controller.bind(source);
assert.equal((await controller.run(A.detect)).ok, true);
assert.deepEqual(calls, [A.detect, A.check]);
assert.equal(latest.connection.ok, true);
assert.ok(!JSON.stringify(latest).includes("never-render"));
assert.equal(latest.native.botQq, "123456789");

controller.bind({ async runAction(id) { return id === A.check ? { ...checked, payload: { botQq: "987654321" } } : ready; } });
assert.equal((await controller.run(A.detect)).ok, false);
assert.equal(latest.connection.reason, "local_qq_account_mismatch");
assert.equal(latest.connection.ok, false);

controller.bind({ async runAction() { return { ...ready, webuiReady: false, starting: true }; } });
await controller.run(A.start);
assert.equal(latest.connection, null);
assert.match(latest.notice, /尚未确认/);

let release;
controller.bind({ runAction() { return new Promise((resolve) => { release = resolve; }); } });
const old = controller.run(A.detect);
assert.equal((await controller.run(A.start)).status, "busy");
controller.bind(source);
release(ready);
assert.equal((await old).status, "stale");
assert.equal(latest.native, null);

controller.bind({ runAction() { return new Promise((resolve) => { release = resolve; }); } });
const stopped = controller.run(A.detect);
const snapshotBeforeStop = latest;
controller.stop();
release(ready);
assert.equal((await stopped).status, "stale");
assert.equal(latest, snapshotBeforeStop);

controller.bind({ async runAction() { throw new Error("secret-error-never-render"); } });
assert.equal((await controller.run(A.detect)).ok, false);
assert.ok(!JSON.stringify(latest).includes("secret-error"));
controller.bind({ async runAction() { return { ok: false, status: "cancelled" }; } });
assert.equal((await controller.run(A.select)).status, "cancelled");
assert.equal(latest.phase, "idle");

const raw = { sourceKind: "backend", backendUrl: "http://127.0.0.1:12001", fallbackReason: "backend-down",
  controlCenterV2: { liveSnapshotStatus: "connecting" }, qqSetupRuntime: { phase: "idle", native: ready } };
const vm = createControlCenterViewModel(raw);
assert.equal(vm.shell.connected, false);
assert.equal(vm.actions[A.detect].available, true);
assert.equal(vm.actions[A.openLogin].available, true);
const html = renderSystem({ ...createInitialControlCenterState(), viewModel: vm });
assert.match(html, /QQ 接入/);
assert.match(html, /打开扫码登录页/);
assert.ok(!html.includes("never-render"));
const remote = createControlCenterViewModel({ ...raw, backendUrl: "https://remote.invalid" });
assert.equal(remote.actions[A.start].available, false);
assert.equal(isQqSetupAction(A.check), false, "existing remote backend self-check must not enter local onboarding");
assert.equal(remote.actions[A.check], undefined, "local onboarding must not disable the existing self-check");
assert.equal(isQqSetupAction(A.detect), true);
assert.match(remote.qqSetup.detail, /远程/);
assert.equal(normalizeQqSetup({}, false, raw.backendUrl).supported, false);

const invokeCalls = [];
const production = createBackendControlCenterSource({ baseUrl: raw.backendUrl, botId: "local-qq-test",
  tauriBridge: { async invoke(name, args) { invokeCalls.push([name, args]); return ready; } },
  fetchImpl() { throw new Error("native actions must not call the backend"); } });
for (const id of [A.detect, A.select, A.start, A.openLogin, A.openFolder]) {
  assert.equal((await production.runAction(id, { botId: "wrong", backendUrl: "https://wrong.invalid", path: "untrusted" })).ok, true);
  const [name, args] = invokeCalls.at(-1);
  assert.equal(name, "local_qq_setup");
  assert.deepEqual(args.request, { action: id.slice("qq.setup.".length), backendUrl: raw.backendUrl, botId: "local-qq-test" });
}
console.log("QQ setup smoke passed: real action routing, account match, offline UI, stale/busy/cancel/failure and secret filtering");
