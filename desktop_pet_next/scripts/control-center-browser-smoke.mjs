// Real settings entry, bridge, store and DOM; native services and HTTP use isolated fixtures.
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { createServer } from "vite";

const { chromium } = await import(process.env.AKANE_PLAYWRIGHT_MODULE || "playwright");
const native = {
  "@tauri-apps/api/core": 'export const invoke = (command, args) => window.__ccSmoke.invoke(command, args);',
  "@tauri-apps/api/event": 'export const listen = (event, callback) => window.__ccSmoke.listen(event, callback); export const emit = (event, payload) => window.__ccSmoke.emit(event, payload); export const emitTo = (target, event, payload) => window.__ccSmoke.emit(event, payload);',
  "@tauri-apps/api/window": 'export const getCurrentWindow = () => ({ onDragDropEvent: async () => () => {}, minimize: async () => {}, toggleMaximize: async () => {} });',
  "@tauri-apps/plugin-http": 'export const fetch = (...args) => window.fetch(...args);'
};
const server = await createServer({
  server: { host: "127.0.0.1", port: 0 },
  optimizeDeps: { exclude: Object.keys(native) },
  plugins: [{ name: "isolated-control-center-native", enforce: "pre",
    resolveId(id) { if (Object.hasOwn(native, id)) return "\0cc-smoke:" + id; },
    load(id) { if (id.startsWith("\0cc-smoke:")) return native[id.slice(10)]; }
  }]
});
let browser;
try {
  await server.listen();
  const origin = server.resolvedUrls.local[0];
  browser = await chromium.launch({ channel: process.env.AKANE_BROWSER_CHANNEL || "msedge", headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.addInitScript(() => {
    const listeners = new Map();
    const state = { backendUrl: "http://127.0.0.1:9999", instanceId: "fixture-host", hostId: "fixture-host",
      boundBotId: "main", profileUserId: "master", sessionId: "old", characterPackId: "akane", voiceVolume: 0.63, voiceEnabled: true };
    const runtime = { state, active: {}, character: { packId: "akane", displayName: "Akane" }, resource: { health: "online" } };
    const publish = () => {
      for (const callback of listeners.get("akane-next-settings-snapshot") || []) callback({ payload: structuredClone(runtime) });
    };
    window.__TAURI_INTERNALS__ = {};
    window.__ccSmoke = {
      state, runtime, publish, pending: null, pendingSend: null,
      async invoke(command, args) {
        if (command === "load_pet_state") return structuredClone(state);
        if (command === "get_client_launch_binding") return { instanceId: state.instanceId };
        if (command === "list_character_packs") return [{ id: "akane", appName: "Akane" }, { id: "reimu", appName: "Reimu" }];
        if (command === "memory_backend_restart") {
          if (args.request.action === "detect") return { ok: true, status: "available" };
          return new Promise(resolve => { window.__ccSmoke.finishRestart = () => resolve({ ok: true, status: "completed", observed: true }); });
        }
        if (command === "local_qq_setup" || command === "get_computer_use_status" || command === "get_personal_browser_status") {
          return { ok: false, status: "unavailable", reason: "isolated_fixture" };
        }
        if (command === "backend_admin_request") {
          const request = args.request;
          const response = await fetch(request.url, { method: request.method,
            headers: { "Content-Type": "application/json" }, ...(request.method === "GET" ? {} : { body: request.body }) });
          return { httpStatus: response.status, body: await response.text(), contentType: "application/json" };
        }
        throw Error("Unexpected native fixture command: " + command);
      },
      async listen(event, callback) {
        if (!listeners.has(event)) listeners.set(event, new Set());
        listeners.get(event).add(callback);
        return () => listeners.get(event).delete(callback);
      },
      async emit(event, payload) {
        if (payload?.command === "requestSnapshot" && !payload.operationId) { publish(); return; }
        if (payload?.command === "sendChatMessage") { this.pendingSend = payload; return; }
        if (payload?.command === "reloadResources") {
          this.pending = payload;
          runtime.settingsCommandResult = { command: payload.command, operationId: "old-op", ok: true, status: "completed" };
          publish();
          return;
        }
        throw Error("Unexpected settings fixture command: " + payload?.command);
      },
      finishReload(ok) {
        const payload = this.pending;
        runtime.settingsCommandResult = { command: payload.command, operationId: payload.operationId, ok,
          status: ok ? "completed" : "failed", reason: ok ? "" : "resource_reload_failed" };
        this.pending = null;
        publish();
      },
      finishSend() {
        const payload = this.pendingSend;
        runtime.settingsCommandResult = { command: payload.command, operationId: payload.operationId, ok: true, status: "accepted" };
        this.pendingSend = null;
        publish();
      }
    };
  });
  const errors = [], unexpected = [];
  page.on("pageerror", error => errors.push(error.message));
  let offline = false, holdSession = false, releaseSession;
  let markSessionHeld;
  const sessionHeld = new Promise(resolve => { markSessionHeld = resolve; });
  let releaseHistory, markHistoryHeld, historyRequests = 0;
  const historyHeld = new Promise(resolve => { markHistoryHeld = resolve; });
  const json = data => ({ contentType: "application/json", body: JSON.stringify(data) });
  const memoryData = { ok: true, botId: "main", characterPackId: "akane", qqAvailable: true,
    groupMemoryRunning: false, groupNamesAvailable: true, processId: 10,
    settings: ["OWNER_PRIVATE_SESSION_SHARED", "OWNER_PRIVATE_GROUP_MEMORY_MIRROR"].map(key => ({ key,
      current: key.endsWith("SHARED"), desired: key.endsWith("SHARED"), pendingRestart: false })),
    groups: [{ groupId: "87001234", name: "87 群 · 伙伴们", enabled: false, hasCharacterMemory: true },
      { groupId: "42001234", name: "42 群", enabled: true, hasCharacterMemory: true },
      { groupId: "102701234", name: "1027 群", enabled: false, hasCharacterMemory: false }] };
  let failMemorySave = false;
  const modelRequests = [];
  let modelData = { status: "configured", providerId: "openai_compatible", protocol: "responses",
    baseUrl: "https://main.example/v1", chatModel: "main-model", chatReasoningEffort: "max", chatMaxOutputTokens: 8192,
    hasApiKey: true, useForVision: true, standaloneVision: false,
    subagent: { mode: "independent", providerId: "openai_compatible", protocol: "openai", baseUrl: "https://child.example/v1",
      model: "child-model", hasApiKey: true, reasoningEffort: "low", timeoutSeconds: 120 },
    providers: [{ id: "openai_compatible", label: "OpenAI compatible", protocol: "responses" }] };
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.origin === new URL(origin).origin) return route.continue();
    if (url.origin !== "http://127.0.0.1:9999") { unexpected.push(url.pathname); return route.abort(); }
    const path = url.pathname.replace(/^\/api\/bots\/[^/]+/, "");
    if (offline) return route.fulfill({ status: 503, ...json({ reason: "fixture_offline" }) });
    if (path === "/health") return route.fulfill(json({ status: "ok", root_binding: "valid", instance_id: "fixture-host" }));
    if (path === "/api/bots") return route.fulfill(json({ defaultBotId: "main", bots: [{ botId: "main", available: true }] }));
    if (path === "/control-center/snapshot") return route.fulfill(json({ ok: true,
      schemaVersion: 1, sourceKind: "backend", generatedAt: "2026-10-03T00:00:00Z", runtime: {
      health: { status: "ok", tts: { enabled: true } }, diagnostics: {},
      workspace: { sections: { files: [], outputs: [] } }, resourceManifest: {}, metrics: ""
    } }));
    if (path === "/capabilities") return route.fulfill(json({ ok: true, capabilities: [] }));
    if (path === "/capabilities/voice-profiles") return route.fulfill(json({ ok: true, voiceProfiles: [] }));
    if (path === "/capabilities/approval-requests") return route.fulfill(json({ ok: true, approvalRequests: [] }));
    if (path === "/plugins/catalog" || path === "/plugins/market") return route.fulfill(json({ ok: true, plugins: [] }));
    if (path === "/admin/plugins/status") return route.fulfill(json({ management: { status: "ready", supports: [] }, artifacts: { stages: [] } }));
    if (path === "/control-center/model-service") {
      if (route.request().method() === "POST") {
        const payload = route.request().postDataJSON();
        modelRequests.push({ operation: "save", payload });
        modelData = { ...modelData, ...payload, ok: true };
      }
      return route.fulfill(json(modelData));
    }
    if (path.startsWith("/control-center/model-service/")) {
      const operation = path.split("/").at(-1), payload = route.request().postDataJSON();
      modelRequests.push({ operation, payload });
      return route.fulfill(json(operation === "models"
        ? { ok: true, models: [payload.target === "vision" ? "vision-discovered" : "child-discovered"] }
        : { ok: true, status: "connected", message: payload.target === "vision" ? "red" : "OK" }));
    }
    if (path === "/control-center/settings-catalog") return route.fulfill(json({ ok: true, items: [] }));
    if (path === "/control-center/memory-settings") {
      return route.fulfill(json({ ...memoryData, characterPackId: url.searchParams.get("character_pack_id") || "akane" }));
    }
    if (path.startsWith("/control-center/settings-catalog/OWNER_PRIVATE_")) {
      const row = memoryData.settings.find(row => row.key === path.split("/").at(-1));
      row.desired = route.request().postDataJSON().value; row.pendingRestart = row.desired !== row.current;
      return route.fulfill(json({ ok: true, status: "saved", ...row }));
    }
    if (path.startsWith("/control-center/memory-settings/groups/")) {
      if (failMemorySave) return route.fulfill({ status: 500, ...json({ ok: false, reason: "private_memory_preference_save_failed" }) });
      const row = memoryData.groups.find(row => row.groupId === path.split("/").at(-1));
      row.enabled = route.request().postDataJSON().enabled;
      return route.fulfill(json({ ok: true, status: "applied", groupId: row.groupId, enabled: row.enabled }));
    }
    if (path === "/capabilities/tool-exposure") return route.fulfill(json({ ok: true, entries: [] }));
    if (path === "/desktop-pet/workspace/summary") return route.fulfill(json({ ok: true, sections: { outputs: [] } }));
    if (path === "/sessions/ensure") {
      const body = route.request().postDataJSON(), sessionId = body.session_id, pack = body.character_pack_id;
      if (holdSession) await new Promise(resolve => { releaseSession = resolve; markSessionHeld(); });
      const paging = ["old", "paging"].includes(sessionId) && pack === "akane";
      return route.fulfill(json({ session: { session_id: sessionId, character_pack_id: pack, message_count: paging ? 120 : 1 },
        messages: [{ role: "assistant", content: pack === "reimu" ? "Reimu reply" : sessionId === "old" ? "Old reply" : sessionId === "paging" ? "Paging tail" : "Fresh reply", seq_no: 90 }],
        message_page: { has_more: paging, next_before_seq: paging ? 90 : null } }));
    }
    if (path === "/sessions/messages") {
      historyRequests++;
      assert.equal(url.searchParams.get("session_id"), "paging");
      assert.equal(url.searchParams.get("character_pack_id"), "akane");
      await new Promise(resolve => { releaseHistory = resolve; markHistoryHeld(); });
      return route.fulfill(json({ messages: [{ role: "assistant", content: "Foreign older reply", seq_no: 1 }],
        message_page: { has_more: false, next_before_seq: null } }));
    }
    unexpected.push(path);
    return route.fulfill({ status: 404, ...json({ reason: "unhandled_fixture_route" }) });
  });
  const ready = () => page.waitForFunction(() => document.querySelector(".ccv2-shell")?.dataset.connection === "connected");
  await page.goto(origin + "control-center-lab.html?page=chat");
  await ready();
  await page.getByText("Old reply", { exact: true }).waitFor();
  const output = resolve(process.env.AKANE_BROWSER_SMOKE_OUTPUT || ".codex/control-center-browser-smoke");
  await mkdir(output, { recursive: true });
  for (const destination of ["overview", "appearance", "voice", "model", "abilities", "system", "chat"]) {
    await page.locator(`.nav-item[data-page="${destination}"]`).click();
    await page.locator(`.nav-item[data-page="${destination}"][aria-current="page"]`).waitFor();
    assert.ok(await page.locator(".ccv2-scroll").innerText());
  }

  // Ordinary snapshots preserve the current editor and selection; layout toggles remain observable.
  await page.locator('.nav-item[data-page="model"]').click();
  await page.locator('[data-model-toggle="standaloneVision"]').click();
  await page.locator('[data-model-field="visionBaseUrl"]').waitFor();
  const modelInput = page.locator('[data-model-field="chatModel"]');
  await modelInput.fill("typed-model");
  await page.evaluate(() => {
    window.__ccEditor = document.activeElement;
    window.__ccEditor.setSelectionRange(2, 5);
    window.__ccSmoke.publish();
  });
  assert.equal(await page.evaluate(() => document.activeElement === window.__ccEditor), true);
  assert.equal(await modelInput.inputValue(), "typed-model");
  assert.deepEqual(await modelInput.evaluate(input => [input.selectionStart, input.selectionEnd]), [2, 5]);
  // Use the real availability projection and click path, not only a controller
  // harness: the child test previously rendered as a disabled button.
  await page.locator('[data-model-field="chatReasoningEffort"]').selectOption("max");
  await page.locator('[data-subagent-field="reasoningEffort"]').selectOption("high");
  assert.equal(await page.locator('[data-model-field="chatReasoningEffort"]').inputValue(), "max");
  await page.locator('[data-action="model.subagent.test"]').click();
  await page.waitForFunction(() => !document.querySelector('[data-action="model.subagent.test"]').disabled);
  assert.equal(modelRequests.at(-1).payload.target, "subagent");
  assert.equal(modelRequests.at(-1).payload.chatReasoningEffort, "max");
  assert.equal(modelRequests.at(-1).payload.subagent.reasoningEffort, "high");
  await page.locator('[data-model-field="visionBaseUrl"]').fill("https://vision.example/v1");
  await page.locator('[data-action="model.vision.models"]').click();
  await page.waitForFunction(() => Boolean(document.querySelector('#ccv2-vision-model-options option')));
  assert.equal(await page.locator('#ccv2-vision-model-options option').first().getAttribute("value"), "vision-discovered");
  assert.equal(await modelInput.inputValue(), "typed-model");
  await page.locator('[data-action="model.vision.test"]').click();
  await page.waitForFunction(() => !document.querySelector('[data-action="model.vision.test"]').disabled);
  assert.equal(modelRequests.at(-1).payload.target, "vision");
  assert.equal(modelRequests.at(-1).payload.visionModel, "vision-discovered");
  await page.locator('[data-action="model.save"]').click();
  await page.waitForFunction(() => !document.querySelector('[data-action="model.save"]').disabled);
  assert.equal(modelData.chatReasoningEffort, "max");
  assert.equal(modelData.subagent.reasoningEffort, "high");
  await page.locator('[data-model-field="chatModel"]').scrollIntoViewIfNeeded();
  await page.screenshot({ path: resolve(output, "model.png") });
  await page.locator('[data-model-toggle="useForVision"]').scrollIntoViewIfNeeded();
  await page.screenshot({ path: resolve(output, "vision-model.png") });
  await page.locator('[data-subagent-field="model"]').scrollIntoViewIfNeeded();
  await page.screenshot({ path: resolve(output, "subagent-model.png") });
  await page.locator('.nav-item[data-page="chat"]').click();

  // A send acknowledgement clears the submitted text, not the next draft typed while waiting.
  await page.locator("[data-chat-input]").fill("First fixture message");
  await page.locator(".composer-send[type=submit]").click();
  await page.waitForFunction(() => Boolean(window.__ccSmoke.pendingSend));
  await page.locator("[data-chat-input]").fill("Next unsent draft");
  await page.evaluate(() => window.__ccSmoke.finishSend());
  await page.locator(".ccv2-brand").click();
  await page.waitForFunction(() => !document.querySelector(".composer-send[type=submit]").disabled);
  assert.equal(await page.locator("[data-chat-input]").inputValue(), "Next unsent draft");
  await page.locator(".composer-send[type=submit]").click();
  await page.waitForFunction(() => Boolean(window.__ccSmoke.pendingSend));
  await page.locator("[data-chat-input]").fill("Draft in old conversation");

  holdSession = true;
  await page.evaluate(() => { window.__ccSmoke.state.sessionId = "new"; window.__ccSmoke.publish(); });
  await page.getByRole("heading", { name: "正在切换会话", exact: true }).waitFor();
  assert.equal(await page.locator(".session-chip").innerText(), "0 条消息");
  assert.equal(await page.getByText("Old reply", { exact: true }).count(), 0);
  assert.equal(await page.locator("[data-chat-load-older]").count(), 0);
  assert.equal(await page.locator("[data-chat-input]").inputValue(), "");
  let sessionTimeout;
  try {
    await Promise.race([sessionHeld, new Promise((_, reject) => {
      sessionTimeout = setTimeout(() => reject(Error("new session request did not reach the fixture")), 10000);
    })]);
  } finally { clearTimeout(sessionTimeout); }
  holdSession = false;
  releaseSession();
  await page.getByText("Fresh reply", { exact: true }).waitFor();
  assert.equal(await page.locator(".session-chip").innerText(), "1 条消息");
  await page.locator("[data-chat-input]").fill("Draft in new conversation");
  await page.evaluate(() => window.__ccSmoke.finishSend());
  await page.waitForTimeout(180);
  assert.equal(await page.locator("[data-chat-input]").inputValue(), "Draft in new conversation");

  // An old paging response cannot enter the same session ID under another character.
  await page.evaluate(() => { window.__ccSmoke.state.sessionId = "paging"; window.__ccSmoke.publish(); });
  await page.getByText("Paging tail", { exact: true }).waitFor();
  await page.locator("[data-chat-load-older]").click();
  let historyTimeout;
  try {
    await Promise.race([historyHeld, new Promise((_, reject) => {
      historyTimeout = setTimeout(() => reject(Error("history request did not reach the fixture")), 10000);
    })]);
  } finally { clearTimeout(historyTimeout); }
  await page.locator(".chat-history-loader.is-loading").waitFor();
  assert.equal(await page.locator("[data-chat-load-older]").count(), 0);
  await page.evaluate(() => {
    window.__ccSmoke.state.characterPackId = "reimu";
    window.__ccSmoke.runtime.character = { packId: "reimu", displayName: "Reimu" };
    window.__ccSmoke.publish();
  });
  await page.getByText("Reimu reply", { exact: true }).waitFor();
  releaseHistory(); await page.waitForTimeout(180);
  await page.evaluate(() => window.__ccSmoke.publish());
  assert.equal(historyRequests, 1);
  assert.equal(await page.getByText("Foreign older reply", { exact: true }).count(), 0);
  assert.equal(await page.getByText("Paging tail", { exact: true }).count(), 0);
  assert.equal(await page.locator(".session-chip").innerText(), "1 条消息");
  assert.equal(await page.locator("[data-chat-load-older]").count(), 0);
  await page.screenshot({ path: resolve(output, "chat.png") });

  await page.locator('.nav-item[data-page="voice"]').click();
  assert.deepEqual(errors, [], "the real controller must not throw during page changes");
  await page.locator('.nav-item[data-page="voice"][aria-current="page"]').waitFor();
  await page.evaluate(() => { window.__ccSmoke.state.voiceVolume = null; window.__ccSmoke.publish(); });
  assert.equal(await page.locator('[data-voice-range-value="volume"]').innerText(), "63%");
  await page.evaluate(() => { window.__ccSmoke.state.voiceVolume = 0; window.__ccSmoke.publish(); });
  assert.equal(await page.locator('[data-voice-range-value="volume"]').innerText(), "0%");
  await page.screenshot({ path: resolve(output, "voice.png") });

  await page.locator('.nav-item[data-page="appearance"]').click();
  await page.locator('[data-action="character.refresh"]').click();
  await page.waitForFunction(() => Boolean(window.__ccSmoke.pending));
  await page.waitForTimeout(180);
  assert.equal(await page.locator('[data-action="character.refresh"]').isDisabled(), true, "an old command must not confirm a pending reload");
  await page.evaluate(() => window.__ccSmoke.finishReload(false));
  await page.locator(".toast").filter({ hasText: "resource_reload_failed" }).waitFor();
  await page.locator('[data-action="character.refresh"]').click();
  await page.waitForFunction(() => Boolean(window.__ccSmoke.pending));
  await page.evaluate(() => window.__ccSmoke.finishReload(true));
  await page.waitForFunction(() => !document.querySelector('[data-action="character.refresh"]').disabled);

  await page.locator('.nav-item[data-page="system"]').click();
  const mirrorToggle = page.locator('[data-action="memory.setting.OWNER_PRIVATE_GROUP_MEMORY_MIRROR"]');
  await mirrorToggle.waitFor();
  await page.waitForFunction(() => !document.querySelector('[data-action="memory.setting.OWNER_PRIVATE_GROUP_MEMORY_MIRROR"]').disabled);
  await mirrorToggle.click();
  await page.locator('.memory-state.is-pending').waitFor();
  assert.equal(await mirrorToggle.getAttribute("aria-checked"), "true");
  const groupToggle = page.locator('[data-action="memory.group.87001234"]');
  failMemorySave = true; await groupToggle.click();
  await page.getByText("群设置保存失败，原来的接收状态已保留。", { exact: true }).waitFor();
  assert.equal(await groupToggle.getAttribute("aria-checked"), "false");
  failMemorySave = false; await groupToggle.click();
  await page.waitForFunction(() => document.querySelector('[data-action="memory.group.87001234"]').getAttribute("aria-checked") === "true");
  const filter = page.locator('[data-memory-group-filter]');
  await filter.pressSequentially("87");
  assert.equal(await page.locator(".memory-group-row").count(), 1);
  assert.equal(await filter.inputValue(), "87");
  await filter.fill(""); await filter.blur();
  await page.locator('[data-action="memory.restart"]').click();
  await page.waitForFunction(() => typeof window.__ccSmoke.finishRestart === "function");
  assert.equal(await mirrorToggle.isDisabled(), true);
  for (const row of memoryData.settings) { row.current = row.desired; row.pendingRestart = false; }
  memoryData.groupMemoryRunning = true; memoryData.processId++;
  await page.evaluate(() => window.__ccSmoke.finishRestart());
  await page.getByText("后端已重启，设置已生效。", { exact: true }).waitFor();
  assert.equal(await page.locator('.memory-state.is-pending').count(), 0);
  await page.locator('.nav-item[data-page="voice"]').click();
  await page.locator('.nav-item[data-page="system"]').click();
  assert.equal(await groupToggle.getAttribute("aria-checked"), "true");
  await page.locator('.memory-settings').scrollIntoViewIfNeeded();
  await page.locator('.memory-settings').screenshot({ path: resolve(output, "memory-settings-panel.png") });
  await page.screenshot({ path: resolve(output, "memory-settings.png") });
  await page.setViewportSize({ width: 900, height: 900 });
  await page.screenshot({ path: resolve(output, "memory-settings-compact.png") });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.setViewportSize({ width: 1440, height: 900 });

  offline = true;
  await page.reload();
  await page.waitForFunction(() => document.querySelector(".ccv2-shell")?.dataset.connection === "failed");
  offline = false;
  await page.locator(".topbar-actions [data-refresh]").click();
  await ready();
  await page.reload();
  await ready();
  assert.equal(await page.locator(".nav-item").count(), 7);
  assert.deepEqual(unexpected, []);
  assert.deepEqual(errors, []);
  console.log("control-center browser smoke passed: real entry and 7 pages, separate main/child reasoning, enabled child API test, vision probe selection and API target, saved form, focused model editor and vision toggle, send/draft races, delayed session switch, old paging under another character, volume fallback/zero, stale reload acknowledgement, memory settings pending/restart, group consent save/failure/retry/search/re-entry, compact layout, offline/reconnect, 0 page errors");
} finally {
  await browser?.close();
  await server.close();
}
