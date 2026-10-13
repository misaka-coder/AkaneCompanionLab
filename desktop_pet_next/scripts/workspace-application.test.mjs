import assert from "node:assert/strict";
import { test } from "node:test";
import { deferred, tick, json, summary, workspaceHarness } from "./workspace-fixture.mjs";

test("initial settings failure exposes its cause and does not dispatch backend work; retry recovers", async () => {
  const h = workspaceHarness();
  h.host.loadState = async () => { throw Error("binding unavailable"); };
  await h.app.start();
  assert.match(h.view.els.alert.textContent, /读取设置失败：binding unavailable/);
  assert.equal(h.requests.length, 0); assert.equal(h.calls.length, 0);
  h.host.loadState = async () => ({ ...h.host.state });
  await h.app.refreshWorkspace();
  assert.equal(h.view.els.fileCount.textContent, "1");
  h.app.dispose();
});

test("a slow project listing does not block the file tray or publish after closing", async () => {
  const listing = deferred();
  const h = workspaceHarness({ invoke: command => command === "verify_backend_instance"
    ? { ok: true, instanceId: "host-a" } : listing.promise });
  await h.app.start();
  assert.equal(h.view.els.fileCount.textContent, "1");
  const before = h.view.els.projectList.textContent;
  h.app.dispose();
  listing.resolve({ ok: true, result: { workspaces: [{ workspace_id: "late", display_name: "late" }] } });
  await tick(); assert.equal(h.view.els.projectList.textContent, before);
});

for (const fails of [false, true]) test(`old summary ${fails ? "failure" : "success"} cannot overwrite a new session`, async () => {
  const old = deferred(); let count = 0;
  const h = workspaceHarness({ fetch: () => ++count === 1 ? old.promise : json(summary("new-session")) });
  const started = h.app.start(); await tick();
  h.publish({ sessionId: "session-b" });
  assert.equal(h.view.els.fileCount.textContent, "0");
  assert.equal(h.requests[0].init.signal.aborted, true);
  h.flushTimers(); await tick();
  assert.match(h.view.els.content.textContent, /new-session/);
  if (fails) old.reject(Error("old failure")); else old.resolve(json(summary("old-session")));
  await started;
  assert.match(h.view.els.content.textContent, /new-session/);
  assert.ok(!h.view.els.alert.textContent.includes("old failure"));
  assert.equal(h.view.els.refresh.disabled, false);
  h.app.dispose();
});

test("same session character switches, including A to B to A, do not revive an old request", async () => {
  const old = deferred(); let count = 0;
  const h = workspaceHarness({ fetch: () => ++count === 1 ? old.promise : json(summary(`fresh-${count}`)) });
  const started = h.app.start(); await tick();
  h.publish({ characterPackId: "reimu" }); h.flushTimers(); await tick();
  assert.match(h.view.els.content.textContent, /fresh-2/);
  h.publish({ characterPackId: "akane" });
  assert.equal(h.view.els.fileCount.textContent, "0");
  h.flushTimers(); await tick();
  old.resolve(json(summary("obsolete-akane"))); await started;
  assert.match(h.view.els.content.textContent, /fresh-3/);
  assert.ok(!h.view.els.content.textContent.includes("obsolete"));
  h.app.dispose();
});

test("latest live snapshot wins over a delayed bootstrap settings read", async () => {
  const load = deferred(), h = workspaceHarness();
  const persisted = { ...h.host.state };
  h.host.loadState = () => load.promise;
  const started = h.app.start(); await tick();
  h.publish({ sessionId: "live", characterPackId: "reimu" });
  load.resolve(persisted); await started;
  assert.equal(new URL(h.requests[0].input).searchParams.get("user_id"), "live");
  assert.equal(h.view.els.session.title, "live");
  h.app.dispose();
});

test("a snapshot during manual reload fences older persisted state even if identity is unchanged", async () => {
  const h = workspaceHarness(); await h.app.start();
  const load = deferred(); h.host.loadState = () => load.promise;
  const refreshed = h.app.refreshWorkspace(); await tick();
  h.publish();
  load.resolve({ ...h.host.state, sessionId: "outdated" }); await refreshed;
  assert.equal(h.view.els.session.title, "session-a");
  assert.equal(new URL(h.requests.at(-1).input).searchParams.get("user_id"), "session-a");
  h.app.dispose();
});

test("repeated same-scope refreshes keep one trailing refresh instead of dropping it", async () => {
  const old = deferred(); let count = 0;
  const h = workspaceHarness({ fetch: () => ++count === 1 ? old.promise : json(summary("tail")) });
  const started = h.app.start(); await tick();
  const first = h.app.refreshWorkspace({ reload: false }), second = h.app.refreshWorkspace({ reload: false });
  assert.equal(first, second); assert.equal(count, 1);
  old.resolve(json(summary("before"))); await Promise.all([started, first, second]);
  assert.equal(count, 2); assert.match(h.view.els.content.textContent, /tail/);
  h.app.dispose();
});

test("a queued retry survives settings-read failure", async () => {
  const load = deferred(), h = workspaceHarness(); let reads = 0;
  h.host.loadState = () => ++reads === 1 ? load.promise : { ...h.host.state };
  const started = h.app.start(); await tick();
  const retry = h.app.refreshWorkspace();
  load.reject(Error("temporary read failure"));
  await Promise.all([started, retry]);
  assert.equal(reads, 2); assert.equal(h.view.els.status.textContent, "已刷新");
  assert.equal(h.requests.length, 1); h.app.dispose();
});

for (const partial of [{ boundBotId: "bot-b" }, { profileUserId: "other-owner" }, { backendUrl: "http://127.0.0.1:12000" }]) {
  test(`binding change ${Object.keys(partial)[0]} clears cards and reads the captured new target`, async () => {
    const old = deferred(); let count = 0;
    const h = workspaceHarness({ fetch: () => ++count === 1 ? old.promise : json(summary("new-binding")) });
    const started = h.app.start(); await tick();
    h.publish(partial); assert.equal(h.view.els.fileCount.textContent, "0");
    h.flushTimers(); await tick();
    const target = new URL(h.requests.at(-1).input);
    assert.equal(target.origin, h.host.state.backendUrl);
    assert.ok(target.pathname.includes(`/api/bots/${h.host.state.boundBotId}/`));
    assert.equal(target.searchParams.get("real_user_id"), h.host.state.profileUserId);
    old.resolve(json(summary("old-binding"))); await started;
    assert.match(h.view.els.content.textContent, /new-binding/); h.app.dispose();
  });
}

test("foreign instance snapshots do not retarget the tray", async () => {
  const h = workspaceHarness(); await h.app.start();
  h.publish({ instanceId: "foreign", hostId: "foreign", sessionId: "other" });
  h.flushTimers(); await tick();
  assert.equal(h.requests.length, 1);
  assert.equal(h.view.els.session.title, "session-a");
  assert.match(h.view.els.alert.textContent, /已忽略来自实例 foreign/);
  h.app.dispose();
});

test("empty sessions show a waiting state while projects remain readable", async () => {
  const h = workspaceHarness(); h.host.state.sessionId = "";
  await h.app.start();
  assert.equal(h.requests.length, 0);
  assert.ok(h.calls.some(call => call.command === "manage_project_workspaces"));
  assert.equal(h.view.els.status.textContent, "等待会话");
  h.app.dispose();
});

test("dispose cancels timers and requests, removes UI/native listeners, and fences late results", async () => {
  const old = deferred(), h = workspaceHarness({ fetch: () => old.promise });
  const started = h.app.start(); await tick();
  h.publish({ sessionId: "new" });
  assert.equal(h.timers.size, 1);
  h.app.dispose();
  const text = h.view.els.content.textContent;
  assert.equal(h.timers.size, 0); assert.equal(h.callbacks.size, 0); assert.equal(h.refreshCallbacks.size, 0);
  for (const target of [...Object.values(h.view.els), h.view.document, h.host.window]) {
    assert.equal([...target.listeners.values()].flatMap(set => [...set]).length, 0);
  }
  old.resolve(json(summary("late"))); await started;
  assert.equal(h.view.els.content.textContent, text);
  await h.app.start(); assert.equal(h.callbacks.size, 0, "closed window applications are not restarted");
});

test("late native listener registration is immediately released after close", async () => {
  const registration = deferred(); let releases = 0;
  const h = workspaceHarness({ host: { listenSnapshots: () => registration.promise } });
  const started = h.app.start(); await tick(); h.app.dispose();
  registration.resolve(() => releases++); await started;
  assert.equal(releases, 1); assert.equal(h.refreshCallbacks.size, 0);
});

test("partial native listener failure cleans earlier registration and leaves manual refresh usable", async () => {
  const h = workspaceHarness({ host: { listenRefresh: async () => { throw Error("listen failed"); } } });
  await h.app.start();
  assert.equal(h.callbacks.size, 0);
  assert.match(h.view.els.alert.textContent, /窗口同步暂时不可用.*listen failed/);
  assert.equal(h.view.els.refresh.disabled, false);
  await h.app.refreshWorkspace(); assert.equal(h.requests.length, 2);
  h.app.dispose();
});
