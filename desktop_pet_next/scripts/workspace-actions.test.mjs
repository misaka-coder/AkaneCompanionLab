import assert from "node:assert/strict";
import { test } from "node:test";
import { createWorkspaceFileActions } from "../src/workspace/files.js";
import { createWorkspaceItemsView } from "../src/workspace/items.js";
import { createProjectWorkspaces } from "../src/workspace/projects.js";
import { createWorkspaceTransport } from "../src/workspace/transport.js";
import { testView, deferred, tick, json, summary } from "./workspace-fixture.mjs";

function harness(options = {}) {
  const view = testView(), items = createWorkspaceItemsView(view);
  let version = 0, abort = new AbortController(), notifications = 0, refreshes = 0, confirmations = 0;
  const calls = [], requests = [], copies = [];
  const state = { instanceId: "host-a", hostId: "host-a", backendUrl: "http://127.0.0.1:9999", boundBotId: "bot-a",
    sessionId: "session-a", profileUserId: "owner", characterPackId: "akane" };
  const capture = () => ({ version, state: Object.freeze({ ...state }), signal: abort.signal });
  const isCurrent = ticket => ticket.version === version && !ticket.signal.aborted;
  const host = {
    copy: async value => copies.push(value), confirm: () => { confirmations++; return true; }, sendCommand: async () => {},
    notifyRefresh: async () => { notifications++; },
    invoke: async (command, args) => {
      calls.push({ command, args });
      if (options.invoke) return options.invoke(command, args);
      if (command === "verify_backend_instance") return { ok: true, instanceId: "host-a" };
      return { ok: true, result: { workspaces: [] } };
    },
    fetch: async (input, init) => { requests.push({ input, init }); return options.fetch ? options.fetch(input, init) : json({ ok: true }); }
  };
  const transport = createWorkspaceTransport({ ...host, isCurrent });
  const files = createWorkspaceFileActions({ host, transport, view, items, capture, isCurrent, isLoading: () => false,
    refreshWorkspace: async () => { refreshes++; } });
  const projects = createProjectWorkspaces({ host, view, capture, isCurrent });
  function switchScope(partial) {
    version++; abort.abort(); abort = new AbortController(); Object.assign(state, partial);
    files.reset(); projects.reset(); view.setStatus("new scope"); view.setAlert("");
  }
  return { host, view, items, transport, files, projects, capture, switchScope, state, calls, requests, copies,
    counts: () => ({ notifications, refreshes, confirmations }) };
}

for (const method of ["openWorkspaceItem", "revealWorkspaceItem", "exportWorkspaceItemToDesktop"]) {
  test(`${method} exposes a native rejection instead of reporting success`, async () => {
    const h = harness({ invoke: async () => ({ ok: false, reason: "artifact_size_rejected", path: "unused" }) });
    assert.equal((await h.files[method]({ handle: "att_a" })).status, "failed");
    assert.match(h.view.els.alert.textContent, /artifact_size_rejected/);
    assert.ok(!h.view.els.status.textContent.includes("已"));
  });
}

test("a delayed native path lookup cannot copy an old session's path after switching", async () => {
  const native = deferred(), h = harness({ invoke: () => native.promise });
  const pending = h.files.copyWorkspaceItemPath({ handle: "old" }); await tick();
  h.switchScope({ sessionId: "session-b" });
  native.resolve({ ok: true, local_path: "C:/old/file.txt" });
  assert.equal((await pending).status, "stale");
  assert.deepEqual(h.copies, []); assert.equal(h.view.els.status.textContent, "new scope");
});

test("workspace audio command failure keeps its cause and never reports playback", async () => {
  const h = harness(); h.host.sendCommand = async () => { throw Error("main window unavailable"); };
  assert.equal((await h.files.playWorkspaceAudio({ handle: "audio-a" })).status, "failed");
  assert.match(h.view.els.alert.textContent, /main window unavailable/);
});

test("duplicate clear-all clicks confirm and dispatch only once, then refresh once", async () => {
  const reply = deferred(), h = harness({ fetch: () => reply.promise });
  const pending = h.files.clearWorkspaceFiles(); await tick();
  assert.equal((await h.files.clearWorkspaceFiles()).status, "busy");
  assert.equal(h.requests.length, 1); assert.equal(h.counts().confirmations, 1);
  const body = JSON.parse(h.requests[0].init.body);
  assert.deepEqual(body, { action: "clear_files", user_id: "session-a", session_id: "session-a", real_user_id: "owner", character_pack_id: "akane" });
  assert.match(h.requests[0].input, /\/api\/bots\/bot-a\/desktop-pet\/workspace\/action$/);
  reply.resolve(json({ ok: true, managed: ["a"] }));
  assert.equal((await pending).status, "completed");
  assert.deepEqual(h.counts(), { notifications: 1, refreshes: 1, confirmations: 1 });
});

test("a backend action rejected with HTTP 200 retains its cause and never refreshes as a success", async () => {
  const h = harness({ fetch: async () => json({ ok: false, reason: "clear_rejected" }) });
  assert.equal((await h.files.clearWorkspaceItem({ handle: "att_a" })).status, "failed");
  assert.match(h.view.els.alert.textContent, /clear_rejected/);
  assert.equal(h.counts().notifications, 0); assert.equal(h.counts().refreshes, 0);
});

test("old clear completion cannot refresh a new character or replace its feedback", async () => {
  const reply = deferred(), h = harness({ fetch: () => reply.promise });
  const pending = h.files.clearWorkspaceItem({ handle: "old" }); await tick();
  h.switchScope({ characterPackId: "reimu" });
  reply.resolve(json({ ok: true }));
  assert.equal((await pending).status, "stale");
  assert.equal(h.counts().notifications, 0); assert.equal(h.counts().refreshes, 0);
  assert.equal(h.view.els.status.textContent, "new scope");
});

test("old native completion cannot release a new scope's file-operation lock", async () => {
  const first = deferred(), second = deferred(); let count = 0;
  const h = harness({ invoke: () => ++count === 1 ? first.promise : second.promise });
  h.items.renderPayload(summary("card"));
  const old = h.files.openWorkspaceItem({ handle: "old" }); await tick();
  h.switchScope({ sessionId: "new" });
  const current = h.files.openWorkspaceItem({ handle: "new" }); await tick();
  first.resolve({ ok: true }); await old;
  assert.equal((await h.files.revealWorkspaceItem({ handle: "new" })).status, "busy");
  assert.ok(h.view.els.content.querySelectorAll("button").every(button => button.disabled));
  second.resolve({ ok: true }); await current;
  assert.ok(h.view.els.content.querySelectorAll("button").every(button => !button.disabled));
});

test("new project feedback cannot be overwritten by an older file action in the same scope", async () => {
  const file = deferred();
  const h = harness({ invoke: command => command === "open_workspace_item" ? file.promise : { ok: true, result: { workspaces: [] } } });
  const old = h.files.openWorkspaceItem({ handle: "a" }); await tick();
  await h.projects.runProjectAction("select", "project-a");
  file.resolve({ ok: true }); await old;
  assert.equal(h.view.els.status.textContent, "已切换项目");
});

test("newer project reads win over both an earlier success and an earlier failure", async () => {
  for (const fails of [false, true]) {
    const old = deferred(); let count = 0;
    const h = harness({ invoke: () => ++count === 1 ? old.promise : { ok: true, result: {
      selected_workspace_id: "new", workspaces: [{ workspace_id: "new", display_name: "New project", selected: true }]
    } } });
    const previous = h.projects.refresh(); await tick(); await h.projects.refresh();
    if (fails) old.reject(Error("old failed")); else old.resolve({ ok: true, result: { workspaces: [] } });
    await previous;
    assert.match(h.view.els.projectSummary.textContent, /New project/);
    assert.ok(!h.view.els.projectList.textContent.includes("old failed"));
  }
});

test("project creation locks duplicate submits and preserves a replacement draft", async () => {
  const creation = deferred(); let count = 0;
  const h = harness({ invoke: (_command, args) => args.action === "create" ? (++count, creation.promise) : { ok: true, result: { workspaces: [] } } });
  h.view.els.projectDisplayName.value = " Original ";
  const pending = h.projects.createManagedProject(); await tick();
  assert.equal(h.view.els.projectDisplayName.disabled, true);
  assert.equal((await h.projects.createManagedProject()).status, "busy");
  h.view.els.projectDisplayName.value = "Replacement";
  creation.resolve({ ok: true }); await pending;
  assert.equal(count, 1); assert.equal(h.view.els.projectDisplayName.value, "Replacement");
  assert.equal(h.calls[0].args.display_name, "Original");
  assert.equal(h.view.els.projectDisplayName.disabled, false);
});

test("project mutation after a scope change does not clear the new draft or report its old success", async () => {
  const creation = deferred();
  const h = harness({ invoke: (_command, args) => args?.action === "create" ? creation.promise : { ok: true, result: { workspaces: [] } } });
  h.view.els.projectDisplayName.value = "Old";
  const pending = h.projects.createManagedProject(); await tick();
  h.switchScope({ boundBotId: "bot-b" }); h.view.els.projectDisplayName.value = "New";
  creation.resolve({ ok: true }); assert.equal((await pending).status, "stale");
  assert.equal(h.view.els.projectDisplayName.value, "New");
  assert.equal(h.view.els.status.textContent, "new scope");
  assert.equal(h.view.els.projectDisplayName.disabled, false);
});

test("cancelled project picker keeps its cancel status, and rejected roots retain the real explanation", async () => {
  const h = harness({ invoke: async () => ({ ok: false, status: "cancelled" }) });
  assert.equal((await h.projects.bindProjectDirectory()).status, "cancelled");
  assert.equal(h.view.els.status.textContent, "已取消选择目录");
  const rejected = harness({ invoke: async () => ({ ok: false, reason: "host_directory_protected" }) });
  assert.equal((await rejected.projects.bindProjectDirectory()).status, "failed");
  assert.match(rejected.view.els.alert.textContent, /不能把 Akane 源码/);
});

test("scope change while instance verification is pending prevents the old POST; new request shares verification", async () => {
  const binding = deferred(), h = harness({ invoke: () => binding.promise });
  const old = h.transport.postWorkspaceAction(h.capture(), { action: "clear_files" });
  const rejection = assert.rejects(old, { name: "AbortError" });
  h.switchScope({ sessionId: "session-b", characterPackId: "reimu" });
  const current = h.transport.postWorkspaceAction(h.capture(), { action: "clear_files" });
  binding.resolve({ ok: true, instanceId: "host-a" }); await Promise.all([rejection, current]);
  assert.equal(h.calls.length, 1); assert.equal(h.requests.length, 1);
  assert.equal(JSON.parse(h.requests[0].init.body).session_id, "session-b");
});

test("HTTP error bodies and native argument mapping preserve real causes and the captured target", async () => {
  const h = harness({ fetch: async () => new Response("offline", { status: 503 }) });
  await assert.rejects(h.transport.fetchWorkspaceSummary(h.capture()), /HTTP 503/);
  const detail = harness({ fetch: async () => new Response(JSON.stringify({ detail: "summary unavailable" }), { status: 500 }) });
  await assert.rejects(detail.transport.fetchWorkspaceSummary(detail.capture()), /summary unavailable/);
  await h.transport.invokeWorkspaceItemAction(h.capture(), { handle: "gen_a", item_type: "generated", title: "report", format: ".TXT" }, "save_desktop");
  assert.deepEqual(h.calls.at(-1).args, { handle: "gen_a", backendUrl: "http://127.0.0.1:9999", botId: "bot-a", itemType: "generated",
    action: "save_desktop", userId: "session-a", sessionId: "session-a", realUserId: "owner", fileName: "report.TXT" });
});

test("instance verification rejection never dispatches the workspace request", async () => {
  const h = harness({ invoke: async () => ({ ok: false, actualInstanceId: "other-host" }) });
  await assert.rejects(h.transport.fetchWorkspaceSummary(h.capture()), /后端实例不匹配/);
  assert.equal(h.requests.length, 0);
});
