import assert from "node:assert/strict";

import {
  CONTROL_CENTER_ACTIONS,
  createControlCenterActionRouter,
  isControlCenterBridgedAction
} from "../src/control-center/action-router.js";
import { secondsFromIntervalLabel } from "../src/control-center/action-helpers.js";
import {
  createBackendControlCenterSource,
  createTauriControlCenterSource
} from "../src/control-center/data-sources.js";

const SETTINGS_COMMAND_EVENT = "akane-next-settings-command";

const emitLog = [];
const invokeLog = [];
const dataSource = createTauriControlCenterSource({
  tauriBridge: {
    emit: async (event, payload) => {
      emitLog.push({ event, payload });
    },
    invoke: async (command, payload) => {
      invokeLog.push({ command, payload });
    }
  }
});
const afterActionLog = [];
const router = createControlCenterActionRouter({
  dataSource,
  onAfterAction: (result) => {
    afterActionLog.push(result);
  }
});

const bridgedActionCases = [
  { id: CONTROL_CENTER_ACTIONS.chatNew, payload: {}, context: { source: "smoke" }, emit: "newSession" },
  { id: CONTROL_CENTER_ACTIONS.chatStop, payload: {}, context: {}, emit: "stopReply" },
  { id: CONTROL_CENTER_ACTIONS.workspaceOpen, payload: {}, context: {}, invoke: "open_workspace_window" },
  { id: CONTROL_CENTER_ACTIONS.voiceTest, payload: {}, context: {}, emit: "testTts" },
  { id: CONTROL_CENTER_ACTIONS.voiceStop, payload: {}, context: {}, emit: "stopTts" },
  { id: CONTROL_CENTER_ACTIONS.characterRefresh, payload: {}, context: {}, emit: "reloadResources" },
  {
    id: CONTROL_CENTER_ACTIONS.characterOpenPackFolder,
    payload: {},
    context: {},
    invoke: "open_character_packs_folder"
  },
  {
    id: CONTROL_CENTER_ACTIONS.perceptionDesktopContextSetEnabled,
    payload: { value: true, featureId: "activeWindow" },
    context: { source: "lab" },
    emit: "setDesktopContextEnabled",
    value: true
  },
  {
    id: CONTROL_CENTER_ACTIONS.perceptionClipboardContextSetEnabled,
    payload: { value: false, featureId: "clipboard" },
    context: { source: "lab" },
    emit: "setClipboardContextEnabled",
    value: false
  },
  {
    id: CONTROL_CENTER_ACTIONS.perceptionScreenVisionSetEnabled,
    payload: { value: true, featureId: "screen" },
    context: { source: "lab" },
    emit: "setScreenVisionEnabled",
    value: true
  },
  {
    id: CONTROL_CENTER_ACTIONS.perceptionProactiveWakeSetEnabled,
    payload: { value: true, featureId: "proactive" },
    context: { source: "lab" },
    emit: "setProactiveWakeEnabled",
    value: true
  },
  {
    id: CONTROL_CENTER_ACTIONS.perceptionProactiveWakeSetIntervalSec,
    payload: { value: 180, label: "3 分钟" },
    context: { source: "lab" },
    emit: "setProactiveWakeIntervalSec",
    value: 180
  },
  { id: CONTROL_CENTER_ACTIONS.musicPrevious, payload: {}, context: {}, emit: "previousMusic" },
  { id: CONTROL_CENTER_ACTIONS.musicNext, payload: {}, context: {}, emit: "nextMusic" },
  { id: CONTROL_CENTER_ACTIONS.musicPause, payload: {}, context: {}, emit: "toggleMusic" },
  { id: CONTROL_CENTER_ACTIONS.musicStop, payload: {}, context: {}, emit: "stopMusic" },
  { id: CONTROL_CENTER_ACTIONS.musicClear, payload: {}, context: {}, emit: "clearMusicQueue" }
];

for (const testCase of bridgedActionCases) {
  assert.equal(isControlCenterBridgedAction(testCase.id), true, `${testCase.id} should be bridged`);
  const result = await router.run(testCase.id, testCase.payload, testCase.context);
  assert.deepEqual(
    { ok: result.ok, status: result.status, actionId: result.actionId, refresh: result.refresh },
    { ok: true, status: "executed", actionId: testCase.id, refresh: true },
    `${testCase.id} should execute`
  );

  if (testCase.emit) {
    const entry = emitLog.find((item) => item.payload.command === testCase.emit);
    assert.ok(entry, `${testCase.id} should emit ${testCase.emit}`);
    assert.equal(entry.event, SETTINGS_COMMAND_EVENT);
    assert.equal(entry.payload.source, testCase.context.source || "control-center");
    if ("value" in testCase) {
      assert.equal(entry.payload.value, testCase.value, `${testCase.id} should preserve payload value`);
    }
  }

  if (testCase.invoke) {
    const entry = invokeLog.find((item) => item.command === testCase.invoke);
    assert.ok(entry, `${testCase.id} should invoke ${testCase.invoke}`);
  }
}

const directNotImplementedCases = [
  CONTROL_CENTER_ACTIONS.characterImportZip,
  CONTROL_CENTER_ACTIONS.characterApply,
  CONTROL_CENTER_ACTIONS.characterRestoreDefaults,
  "perception.clipboard.clear"
];

for (const actionId of directNotImplementedCases) {
  const result = await dataSource.runAction(actionId);
  assert.deepEqual(
    { ok: result.ok, status: result.status, actionId: result.actionId, refresh: result.refresh },
    { ok: false, status: "not-implemented", actionId, refresh: false },
    `${actionId} should remain not implemented at data-source boundary`
  );
}

for (const actionId of [...directNotImplementedCases]) {
  const result = await router.run(actionId);
  assert.equal(result.status, "not-implemented", `${actionId} should route to not-implemented`);
  assert.equal(result.refresh, false, `${actionId} should normalize to refresh:false`);
}

const labelCases = [
  ["30 秒", 30],
  ["1 分钟", 60],
  ["3 分钟", 180],
  ["5 分钟", 300],
  ["10 分钟", 600],
  ["", 0],
  ["abc", 0]
];

for (const [label, expected] of labelCases) {
  assert.equal(secondsFromIntervalLabel(label), expected, `${label} should parse to ${expected}`);
}

assert.ok(afterActionLog.length >= bridgedActionCases.length, "onAfterAction should receive refresh results");

// at least the executed actions should have refresh:true; not-implemented results have refresh:false
const refreshTrueCount = afterActionLog.filter((result) => result.refresh === true).length;
assert.ok(refreshTrueCount >= bridgedActionCases.length, "executed bridged actions should request refresh");

// ---------- hardening: handler throws ----------

const THROWBACK_ID = "test.handler-throws";
const throwRouter = createControlCenterActionRouter({ dataSource });
throwRouter.register(THROWBACK_ID, () => {
  throw new Error("handler explosion");
});
const throwResult = await throwRouter.run(THROWBACK_ID);
assert.equal(throwResult.ok, false, "handler throw should produce ok:false");
assert.equal(throwResult.status, "failed", "handler throw should produce status:failed");
assert.equal(typeof throwResult.error, "string", "error should be a string");
assert.equal(throwResult.refresh, true, "handler throw should request refresh");

// ---------- hardening: dataSource.runAction throws ----------

const FAIL_DS = {
  kind: "mock",
  handlesAction() { return true; },
  async runAction() { throw new Error("data source kaboom"); }
};
const failDsRouter = createControlCenterActionRouter({ dataSource: FAIL_DS });
const failDsResult = await failDsRouter.run("any.action");
assert.equal(failDsResult.ok, false, "dataSource throw should produce ok:false");
assert.equal(failDsResult.status, "failed", "dataSource throw should produce status:failed");
assert.equal(typeof failDsResult.error, "string", "error should be a string");
assert.equal(failDsResult.refresh, true, "dataSource throw should request refresh");

// ---------- hardening: onAfterAction throws does not affect result ----------

const onAfterLog = [];
const oaRouter = createControlCenterActionRouter({
  dataSource,
  onAfterAction() { throw new Error("hook oops"); }
});
const oaResult = await oaRouter.run(CONTROL_CENTER_ACTIONS.chatNew);
assert.equal(oaResult.ok, true, "onAfterAction throw should not change result");
assert.equal(oaResult.status, "executed", "onAfterAction throw should not change status");

// ---------- hardening: refresh:false from handler is preserved ----------

const NOREFRESH_ID = "test.no-refresh";
const nrRouter = createControlCenterActionRouter({ dataSource });
nrRouter.register(NOREFRESH_ID, () => ({ ok: true, refresh: false }));
const nrResult = await nrRouter.run(NOREFRESH_ID);
assert.equal(nrResult.ok, true, "refresh:false should keep ok:true");
assert.equal(nrResult.refresh, false, "handler refresh:false should be preserved");

// ---------- window actions ----------

const windowLog = [];
const winDataSource = createTauriControlCenterSource({
  tauriBridge: {
    invoke: async (command) => { invokeLog.push({ command }); },
    window: {
      minimize: async () => { windowLog.push("minimize"); },
      toggleMaximize: async () => { windowLog.push("toggleMaximize"); }
    }
  }
});
const winRouter = createControlCenterActionRouter({ dataSource: winDataSource });

// window.close — uses tauriInvokeByActionId (close_window)
{
  assert.equal(isControlCenterBridgedAction(CONTROL_CENTER_ACTIONS.windowClose), true);
  const result = await winRouter.run(CONTROL_CENTER_ACTIONS.windowClose);
  assert.equal(result.status, "executed", "window.close should execute");
  assert.equal(result.refresh, true, "window.close should request refresh");
  const invokeEntry = invokeLog.find((e) => e.command === "close_window");
  assert.ok(invokeEntry, "window.close should invoke close_window");
}

// window.minimize — uses injected window.minimize
{
  assert.equal(isControlCenterBridgedAction(CONTROL_CENTER_ACTIONS.windowMinimize), true);
  const result = await winRouter.run(CONTROL_CENTER_ACTIONS.windowMinimize);
  assert.equal(result.status, "executed", "window.minimize should execute");
  assert.equal(result.actionId, CONTROL_CENTER_ACTIONS.windowMinimize, "window.minimize should keep actionId");
  assert.equal(result.refresh, true, "window.minimize should request refresh");
  assert.ok(windowLog.includes("minimize"), "window.minimize should call bridge window.minimize");
  const directResult = await winDataSource.runAction(CONTROL_CENTER_ACTIONS.windowMinimize);
  assert.equal(directResult.actionId, CONTROL_CENTER_ACTIONS.windowMinimize, "dataSource window.minimize should return actionId");
}

// window.maximize — uses injected window.toggleMaximize
{
  assert.equal(isControlCenterBridgedAction(CONTROL_CENTER_ACTIONS.windowMaximize), true);
  const result = await winRouter.run(CONTROL_CENTER_ACTIONS.windowMaximize);
  assert.equal(result.status, "executed", "window.maximize should execute");
  assert.equal(result.actionId, CONTROL_CENTER_ACTIONS.windowMaximize, "window.maximize should keep actionId");
  assert.equal(result.refresh, true, "window.maximize should request refresh");
  assert.ok(windowLog.includes("toggleMaximize"), "window.maximize should call bridge window.toggleMaximize");
  const directResult = await winDataSource.runAction(CONTROL_CENTER_ACTIONS.windowMaximize);
  assert.equal(directResult.actionId, CONTROL_CENTER_ACTIONS.windowMaximize, "dataSource window.maximize should return actionId");
}

// window.notify — NOT bridged, stays not-implemented
{
  const result = await dataSource.runAction(CONTROL_CENTER_ACTIONS.windowNotify);
  assert.equal(result.status, "not-implemented", "window.notify should be not-implemented");
  assert.equal(result.ok, false, "window.notify should be ok:false");
  assert.equal(result.refresh, false, "window.notify should not request refresh");
  // router.run normalizes not-implemented to refresh:false
  const routerResult = await router.run(CONTROL_CENTER_ACTIONS.windowNotify);
  assert.equal(routerResult.status, "not-implemented", "window.notify should route to not-implemented");
  assert.equal(routerResult.refresh, false, "window.notify should normalize to refresh:false");
}

// window actions are client-only; backend source must not POST them when Tauri is unavailable.
{
  let backendFetchCalled = false;
  const backendSource = createBackendControlCenterSource({
    fetchImpl: async () => {
      backendFetchCalled = true;
      return { ok: false, status: 500, headers: { get: () => "" } };
    }
  });
  const result = await backendSource.runAction(CONTROL_CENTER_ACTIONS.windowMinimize);
  assert.equal(result.status, "not-implemented", "backend window.minimize should be not-implemented without Tauri");
  assert.equal(result.refresh, false, "backend window.minimize should not request refresh");
  assert.equal(backendFetchCalled, false, "backend window.minimize should not call backend HTTP");
}

console.log(
  `control-center action bridge smoke passed: ${bridgedActionCases.length} bridged actions, ` +
    `${directNotImplementedCases.length} not-implemented checks, ${labelCases.length} interval labels, ` +
    "4 hardening checks, 4 window action checks"
);
