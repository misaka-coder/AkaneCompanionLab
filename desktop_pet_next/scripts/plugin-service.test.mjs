import assert from "node:assert/strict";
import { test } from "node:test";
import { createPluginServiceClient } from "../src/pet/plugin-service.js";
import { createRuntimeScope } from "../src/pet/scope.js";
import { botScopedPath } from "../src/bot-routing.js";

function setup(invoke = async () => ({ ok: true, body: '{"ok":true,"content":{"version":1}}' })) {
  const state = { instanceId: "local", backendUrl: "http://localhost:12001", boundBotId: "second-bot",
    profileUserId: "master", sessionId: "desktop-client", characterPackId: "cecilia" };
  const requests = [];
  const client = createPluginServiceClient({ state, scope: createRuntimeScope(state),
    persistence: { getProfileUserId: () => state.profileUserId },
    backend: { buildBackendEndpointUrl: (_name, path, query) => {
      const url = new URL(botScopedPath(state.boundBotId, path), state.backendUrl);
      url.search = new URLSearchParams(query).toString();
      return url.toString();
    } },
    platform: { invoke: async (command, args) => { requests.push({ command, ...args }); return invoke(); } },
  });
  return { client, state, requests, binding: client.bind("example.game.service.board.v1.control") };
}

test("program calls use the client session, current character and Bot through the admin bridge", async () => {
  const h = setup();
  assert.deepEqual(await h.client.invoke(h.binding, { action: "state" }), { version: 1 });
  const url = new URL(h.requests[0].request.url);
  assert.equal(url.pathname, "/api/bots/second-bot/admin/plugins/capabilities/example.game.service.board.v1.control/invoke");
  assert.equal(url.searchParams.get("user_id"), "desktop-client");
  assert.equal(url.searchParams.get("real_user_id"), "master");
  assert.equal(url.searchParams.get("character_pack_id"), "cecilia");
  assert.equal(h.requests[0].command, "backend_admin_request");
});

test("a stale binding does not send another operation", async () => {
  const h = setup();
  h.state.characterPackId = "reimu";
  await assert.rejects(h.client.invoke(h.binding, {}), { reason: "request_scope_changed" });
  assert.equal(h.requests.length, 0);
});

test("an old response cannot be accepted after a session switch", async () => {
  let finish;
  const h = setup(() => new Promise(resolve => { finish = resolve; }));
  const pending = h.client.invoke(h.binding, {});
  h.state.sessionId = "new-client";
  finish({ ok: true, body: '{"ok":true,"content":{"version":5}}' });
  await assert.rejects(pending, { reason: "request_scope_changed" });
});

test("domain failure keeps its reason and current state", async () => {
  const h = setup(async () => ({ ok: true, body: JSON.stringify({ ok: false, reason: "stale",
    content: { state: { version: 8 }, message: "盘面已更新" } }) }));
  await assert.rejects(h.client.invoke(h.binding, {}), error => error.reason === "stale"
    && error.content.state.version === 8 && error.message === "盘面已更新");
});

test("missing acknowledgement and invalid JSON cannot be mistaken for success", async () => {
  for (const body of ['{"content":{}}', "not json", "null"]) {
    const h = setup(async () => ({ ok: true, body }));
    await assert.rejects(h.client.invoke(h.binding, {}));
  }
});
