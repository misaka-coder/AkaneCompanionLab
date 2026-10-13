// Run the production entry and DOM against isolated native/HTTP fixtures.
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { createServer } from "vite";

const { chromium } = await import(process.env.AKANE_PLAYWRIGHT_MODULE || "playwright");
const native = {
  "@tauri-apps/api/core": "export const invoke = (...args) => window.__workspaceSmoke.invoke(...args);",
  "@tauri-apps/api/event": "export const listen = (...args) => window.__workspaceSmoke.listen(...args); export const emit = (...args) => window.__workspaceSmoke.emit(...args); export const emitTo = (target, ...args) => window.__workspaceSmoke.emitTo(target, ...args);",
  "@tauri-apps/plugin-http": "export const fetch = (...args) => window.fetch(...args);"
};
const server = await createServer({ server: { host: "127.0.0.1", port: 0 },
  optimizeDeps: { exclude: Object.keys(native) },
  plugins: [{ name: "isolated-workspace-native", enforce: "pre",
    resolveId(id) { if (Object.hasOwn(native, id)) return "\0workspace-smoke:" + id; },
    load(id) { if (id.startsWith("\0workspace-smoke:")) return native[id.slice(17)]; }
  }]
});
let browser, page;
try {
  await server.listen();
  const origin = server.resolvedUrls.local[0];
  browser = await chromium.launch({ channel: process.env.AKANE_BROWSER_CHANNEL || "msedge", headless: true });
  page = await browser.newPage({ viewport: { width: 1200, height: 1000 } });
  const errors = [], unexpected = [], requests = [];
  page.on("pageerror", error => { errors.push(error.message); console.error("workspace page error:", error.message); });
  await page.addInitScript(() => {
    const listeners = new Map();
    const state = { instanceId: "fixture-host", hostId: "fixture-host", backendUrl: "http://127.0.0.1:9999",
      boundBotId: "bot-a", sessionId: "one", profileUserId: "master", characterPackId: "akane" };
    const music = { playing: true, queueIndex: 0, queueCount: 2, hasNext: true, track: { sourceId: "song-a", displayName: "Music fixture" },
      queue: [{ sourceId: "song-a", displayName: "Music fixture" }, { sourceId: "song-b", displayName: "Second song" }], currentLyric: { text: "Fixture lyric" } };
    const projects = [{ workspace_id: "project-a", display_name: "Project A", root_kind: "managed", available: true, selected: true }];
    const publish = () => {
      for (const callback of listeners.get("akane-next-settings-snapshot") || []) callback({ payload: { state: structuredClone(state), music } });
    };
    window.__workspaceSmoke = {
      state, calls: [], commands: [], projects, closed: 0,
      publish, switchTo(partial) { Object.assign(state, partial); publish(); },
      listenerCount() { return [...listeners.values()].reduce((count, callbacks) => count + callbacks.size, 0); },
      async invoke(command, args) {
        this.calls.push({ command, args });
        if (command === "load_pet_state") return structuredClone(state);
        if (command === "get_client_launch_binding") return { instanceId: state.instanceId };
        if (command === "verify_backend_instance") return { ok: true, instanceId: state.instanceId };
        if (command === "backend_admin_request") return { ok: true,
          body: JSON.stringify({ ok: true, token: "isolated-session", expiresAt: Date.now() + 300_000 }) };
        if (command === "manage_project_workspaces") {
          if (args.action === "create") {
            for (const project of projects) project.selected = false;
            projects.push({ workspace_id: "created", display_name: args.display_name, root_kind: "managed", available: true, selected: true });
          }
          return { ok: true, result: { workspaces: structuredClone(projects), selected_workspace_id: projects.find(project => project.selected)?.workspace_id } };
        }
        if (command === "bind_project_directory") return { ok: false, status: "cancelled" };
        if (command === "open_workspace_item") return this.fileResult || { ok: true, local_path: "C:/fixture/note.txt", path: "C:/fixture/export.txt" };
        if (command === "close_window") { this.closed++; return; }
        throw Error("Unexpected native workspace command: " + command);
      },
      async listen(event, callback) {
        if (!listeners.has(event)) listeners.set(event, new Set());
        listeners.get(event).add(callback);
        return () => listeners.get(event).delete(callback);
      },
      async emit(event, payload) {
        if (payload?.command === this.commandFailure) throw Error("music_fixture_failure");
        if (payload?.command === "requestSnapshot") { publish(); return; }
        if (payload?.command) { this.commands.push(payload); return; }
        for (const callback of listeners.get(event) || []) callback({ payload });
      },
      async emitTo(_target, event, payload) {
        if (payload?.command === this.holdCommand) {
          await new Promise((_resolve, reject) => { this.rejectCommand = () => reject(Error("late targeted failure")); });
        }
        return this.emit(event, payload);
      }
    };
  });
  let offline = false, empty = false, trayLabel = "Input fixture";
  let holdSummary = false, summaryHeld, releaseSummary, holdAction = false, actionHeld, releaseAction;
  function holdNextSummary() {
    holdSummary = true;
    return new Promise(resolve => { summaryHeld = resolve; });
  }
  const json = payload => ({ contentType: "application/json", body: JSON.stringify(payload) });
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.origin === new URL(origin).origin) return route.continue();
    if (url.origin !== "http://127.0.0.1:9999") { unexpected.push(url.pathname); return route.abort(); }
    const path = url.pathname.replace(/^\/api\/bots\/[^/]+/, "");
    requests.push({ url: url.href, method: route.request().method(), body: route.request().postDataJSON() });
    if (offline) return route.fulfill({ status: 503, ...json({ reason: "fixture_offline" }) });
    if (path === "/desktop-pet/workspace/summary") {
      const payload = { sections: empty ? {} : {
      files: [{ id: "same", handle: "att_one", title: trayLabel, item_type: "attachment", kind: "text", can_open: true, can_clear: true }],
      outputs: [{ id: "same", handle: "gen_one", title: "Output fixture", item_type: "generated", kind: "audio", can_open: true, can_clear: true }]
      } };
      if (holdSummary) {
        holdSummary = false;
        const wait = new Promise(resolve => { releaseSummary = resolve; }); summaryHeld(); await wait;
      }
      try { return await route.fulfill(json(payload)); }
      catch { /* The production scope aborts held old requests. */ }
      return;
    }
    if (path === "/desktop-pet/workspace/action") {
      if (holdAction) {
        holdAction = false;
        const wait = new Promise(resolve => { releaseAction = resolve; }); actionHeld(); await wait;
      }
      return route.fulfill(json({ ok: true, managed: [] }));
    }
    unexpected.push(path); return route.abort();
  });

  await page.goto(new URL("workspace.html", origin).href);
  await page.locator("#workspace-summary").filter({ hasText: "1 文件 · 1 成果" }).waitFor();
  assert.match(await page.locator("#workspace-music").innerText(), /Music fixture · 1\/2/);
  assert.match(await page.locator("#workspace-music").innerText(), /Fixture lyric/);
  await page.locator('[data-key="file:same"] [data-action="open-file"]').click();
  await page.waitForFunction(() => window.__workspaceSmoke.calls.some(call => call.command === "open_workspace_item"));
  await page.locator('[data-key="output:same"] [data-action="open-file"]').click();
  const opened = await page.evaluate(() => window.__workspaceSmoke.calls.filter(call => call.command === "open_workspace_item"));
  assert.deepEqual(opened.map(call => [call.args.handle, call.args.itemType]), [["att_one", "attachments"], ["gen_one", "generated"]]);
  await page.locator('[data-music-action="next"]').click();
  await page.waitForFunction(() => window.__workspaceSmoke.commands.some(command => command.command === "controlActiveMusic" && command.value.action === "next"));
  await page.evaluate(() => { window.__workspaceSmoke.commandFailure = "controlActiveMusic"; });
  await page.locator('[data-music-action="toggle"]').click();
  await page.locator("#workspace-status").filter({ hasText: "music_fixture_failure" }).waitFor();
  await page.evaluate(() => { delete window.__workspaceSmoke.commandFailure; });
  await page.locator("#project-display-name").fill("New fixture project");
  await page.locator('#create-project-form button[type="submit"]').click();
  await page.locator("#project-workspace-summary").filter({ hasText: "New fixture project" }).waitFor();
  assert.equal(await page.locator("#project-display-name").inputValue(), "");
  await page.locator("#bind-project-directory").click();
  await page.locator("#workspace-status").filter({ hasText: "已取消选择目录" }).waitFor();

  offline = true;
  await page.locator("#refresh-workspace").click();
  await page.locator("#workspace-status").filter({ hasText: "刷新失败" }).waitFor();
  assert.equal(await page.locator("#clear-workspace-files").isDisabled(), true);
  offline = false; empty = true;
  await page.keyboard.press("F5");
  await page.locator("#workspace-content").filter({ hasText: "现在手边还很清爽" }).waitFor();
  empty = false;
  await page.keyboard.press("Control+r");
  await page.locator("#workspace-summary").filter({ hasText: "1 文件 · 1 成果" }).waitFor();

  await page.evaluate(() => { window.__workspaceSmoke.fileResult = { ok: false, reason: "artifact_size_rejected" }; });
  await page.locator('[data-key="file:same"] [data-action="open-file"]').click();
  await page.locator("#workspace-alert").filter({ hasText: "artifact_size_rejected" }).waitFor();
  assert.ok(!(await page.locator("#workspace-status").innerText()).includes("已打开"));
  await page.evaluate(() => { delete window.__workspaceSmoke.fileResult; });

  for (const [partial, label] of [[{ sessionId: "two" }, "New session fixture"], [{ characterPackId: "reimu" }, "New role fixture"]]) {
    const held = holdNextSummary(); await page.keyboard.press("F5"); await held;
    trayLabel = label;
    const remaining = await page.evaluate(partial => {
      window.__workspaceSmoke.switchTo(partial);
      return document.querySelectorAll(".workspace-item").length;
    }, partial);
    assert.equal(remaining, 0, "old cards disappear immediately on scope change");
    await page.locator('[data-key="file:same"] h3').filter({ hasText: label }).waitFor();
    releaseSummary();
    await page.waitForFunction(label => document.querySelector('[data-key="file:same"] h3')?.textContent === label, label);
    assert.equal(await page.locator("#workspace-status").innerText(), "已刷新");
  }
  assert.equal(new URL(requests.filter(request => request.method === "GET").at(-1).url).searchParams.get("user_id"), "two");

  let dialogs = 0;
  page.on("dialog", async dialog => { dialogs++; await dialog.accept(); });
  const actionPending = new Promise(resolve => { actionHeld = resolve; }); holdAction = true;
  const postsBefore = requests.filter(request => request.method === "POST").length;
  await page.locator("#clear-workspace-files").click(); await actionPending;
  assert.equal(await page.locator("#clear-workspace-files").isDisabled(), true);
  await page.evaluate(() => {
    const button = document.querySelector("#clear-workspace-files");
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  assert.equal(requests.filter(request => request.method === "POST").length, postsBefore + 1);
  assert.equal(dialogs, 1);
  const mutation = requests.filter(request => request.method === "POST").at(-1);
  assert.equal(mutation.body.session_id, "two"); assert.equal(mutation.body.character_pack_id, "reimu");
  releaseAction();
  await page.locator("#refresh-workspace:not([disabled])").waitFor();
  await page.locator("#clear-workspace-files:not([disabled])").waitFor();

  const output = resolve(".codex/workspace-browser-smoke");
  await mkdir(output, { recursive: true });
  await page.screenshot({ path: resolve(output, "workspace.png"), fullPage: true });
  const commandCount = await page.evaluate(() => {
    window.__workspaceSmoke.holdCommand = "controlActiveMusic";
    return window.__workspaceSmoke.commands.length;
  });
  await page.locator('[data-music-action="next"]').click();
  await page.waitForFunction(() => Boolean(window.__workspaceSmoke.rejectCommand));
  const closing = holdNextSummary(); await page.keyboard.press("F5"); await closing;
  await page.locator("#close-workspace").click();
  await page.waitForFunction(() => window.__workspaceSmoke.closed === 1);
  assert.equal(await page.evaluate(() => window.__workspaceSmoke.listenerCount()), 0);
  const closedText = await page.locator("#workspace-content").innerText();
  releaseSummary();
  await page.evaluate(() => window.__workspaceSmoke.rejectCommand());
  await page.evaluate(() => window.__workspaceSmoke.switchTo({ sessionId: "after-close" }));
  assert.equal(await page.locator("#workspace-content").innerText(), closedText);
  assert.equal(await page.evaluate(() => window.__workspaceSmoke.commands.length), commandCount, "closed commands never fall back to global emit");
  await page.reload();
  await page.locator("#workspace-summary").filter({ hasText: "1 文件 · 1 成果" }).waitFor();
  assert.equal(await page.evaluate(() => window.__workspaceSmoke.listenerCount()), 2);
  await page.keyboard.press("Escape");
  await page.waitForFunction(() => window.__workspaceSmoke.closed === 1);
  assert.equal(await page.evaluate(() => window.__workspaceSmoke.listenerCount()), 0);
  assert.equal(unexpected.length, 0, `unexpected fixture requests: ${unexpected.join(", ")}`);
  assert.deepEqual(errors, []);
  console.log("workspace browser: cards/music/projects, native rejection, failure/empty/retry, held session/character switches, duplicate clear guard, close cleanup and reentry passed; 0 page errors (isolated services)");
} catch (error) {
  console.error("workspace page state:", await page?.evaluate(() => ({
    status: document.querySelector("#workspace-status")?.textContent,
    alert: document.querySelector("#workspace-alert")?.textContent,
    summary: document.querySelector("#workspace-summary")?.textContent,
    commands: window.__workspaceSmoke?.calls.map(call => call.command)
  })));
  throw error;
} finally {
  await browser?.close();
  await server.close();
}
