// Real entry point and DOM, with synthetic HTTP responses isolated from live services.
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { createServer } from "vite";

const { chromium } = await import(process.env.AKANE_PLAYWRIGHT_MODULE || "playwright");
const server = await createServer({ server: { host: "127.0.0.1", port: 0 }, plugins: [{
  name: "isolated-slow-chat-stream",
  configureServer(instance) {
    instance.middlewares.use((request, response, next) => {
      if (request.url !== "/__slow_chat") return next();
      response.setHeader("Content-Type", "application/x-ndjson");
      response.setHeader("Access-Control-Allow-Origin", "*");
      response.setHeader("Cache-Control", "no-store");
      const write = event => response.write(JSON.stringify(event) + "\n");
      write({ type: "stream_start", phase: "received" });
      const timers = [
        setTimeout(() => {
          write({ type: "turn_start" });
          write({ type: "turn_progress", phase: "waiting_response", elapsed_ms: 5000 });
        }, 400),
        setTimeout(() => write({ type: "turn_progress", phase: "retrying_model" }), 800),
        setTimeout(() => {
          write({ type: "final", payload: { speech: "等待阶段完成。" } });
          write({ type: "stream_end" });
          response.end();
        }, 1200),
      ];
      response.on("close", () => timers.forEach(clearTimeout));
    });
  },
}] });
let browser;
try {
  await server.listen();
  const origin = server.resolvedUrls.local[0];
  browser = await chromium.launch({ channel: process.env.AKANE_BROWSER_CHANNEL || "msedge", headless: true });
  const page = await browser.newPage({ viewport: { width: 420, height: 680 } });
  // Keep real DOM handlers and controller wiring; device and ASR boundaries are controlled.
  await page.addInitScript(() => {
    const requests = [], streams = [], recorders = [];
    window.__petVoiceSmoke = {
      requests, streams, recorders,
      resolve(index) {
        const track = { stops: 0, stop() { this.stops += 1; } };
        const stream = { track, getTracks: () => [track] };
        streams.push(stream);
        requests[index].resolve(stream);
      },
      deny(index) { requests[index].reject(new DOMException("denied", "NotAllowedError")); }
    };
    Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: {
      getUserMedia: () => new Promise((resolve, reject) => requests.push({ resolve, reject }))
    } });
    window.MediaRecorder = class extends EventTarget {
      static isTypeSupported(type) { return type === "audio/webm"; }
      constructor(stream, options) {
        super();
        this.stream = stream;
        this.mimeType = options?.mimeType || "audio/webm";
        this.state = "inactive";
        recorders.push(this);
      }
      start() { this.state = "recording"; }
      stop() {
        if (this.state === "inactive") throw new DOMException("inactive", "InvalidStateError");
        this.state = "inactive";
        queueMicrotask(() => {
          const data = new Event("dataavailable");
          data.data = new Blob(["synthetic voice".repeat(128)], { type: this.mimeType });
          this.dispatchEvent(data);
          this.dispatchEvent(new Event("stop"));
        });
      }
    };
  });
  const errors = [], requests = [];
  let releaseAsr, stopRequested = false;
  const asrReady = new Promise(resolve => { releaseAsr = resolve; });
  page.on("pageerror", error => errors.push(error.message));
  const json = body => ({ contentType: "application/json", body: JSON.stringify(body) });
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.origin === new URL(origin).origin) return route.continue();
    if (url.origin !== "http://127.0.0.1:9999") return route.abort();
    const body = route.request().headers()["content-type"]?.includes("application/json")
      ? route.request().postDataJSON() : null;
    requests.push({ path: url.pathname, body });
    if (url.pathname === "/health") return route.fulfill(json({ status: "ok", root_binding: "valid", instance_id: "local-default" }));
    if (url.pathname === "/api/bots") return route.fulfill(json({ defaultBotId: "local-default", bots: [{ botId: "local-default", available: true }] }));
    if (url.pathname.endsWith("/desktop-pet/health")) return route.fulfill(json({ status: "ok", tts: { enabled: false }, features: { care: { enabled: false } } }));
    if (url.pathname.endsWith("/resource-manifest")) return route.fulfill(json({ characters: { outfits: [{ id: "猫娘", emotions: [{ id: "正常", path: origin + "src/assets/characters/猫娘/正常.png" }] }] } }));
    if (url.pathname.endsWith("/sessions/ensure")) return route.fulfill(json({ session: {} }));
    if (url.pathname.endsWith("/workspace/summary")) return route.fulfill(json({ sections: { files: [], outputs: [] } }));
    if (url.pathname.endsWith("/think/stop")) {
      stopRequested = true;
      return route.fulfill(json({ ok: true }));
    }
    if (url.pathname.endsWith("/asr")) {
      await asrReady;
      return route.fulfill(json({ ok: true, text: "浏览器语音" }));
    }
    if (url.pathname.endsWith("/think")) {
      if (body.message === "等待阶段") return route.continue({ url: origin + "__slow_chat" });
      if (body.message === "失败请求") return route.fulfill({ status: 503, ...json({ detail: "smoke backend failure" }) });
      if (body.message === "慢请求") {
        stopRequested = false;
        await new Promise(resolve => setTimeout(resolve, 500));
        if (stopRequested) return route.fulfill({ contentType: "application/x-ndjson",
          body: [{ type: "turn_stopped" }, { type: "stream_end", status: "stopped" }]
            .map(event => JSON.stringify(event)).join("\n") + "\n" });
      }
      const text = body.message === "慢请求" ? "旧会话回复" : "模块连接正常。";
      const events = [{ type: "turn_start" }, { type: "speech_segment", index: 0, text },
        { type: "speech_segment", index: 0, text }, { type: "final", payload: { speech: text } }, { type: "stream_end" }];
      return route.fulfill({ contentType: "application/x-ndjson", body: events.map(event => JSON.stringify(event)).join("\n") + "\n" }).catch(() => {});
    }
    throw new Error(`Unmocked pet browser route: ${url.pathname}`);
  });

  const openInput = async () => {
    if (await page.locator("#chat-input").isVisible()) return;
    await page.locator("#pet-hitbox").dblclick();
    await page.locator("#chat-input").waitFor({ state: "visible" });
  };
  const send = async text => {
    await openInput();
    await page.locator("#chat-input").fill(text);
    await page.locator("#chat-input").press("Enter");
  };
  const ready = async () => {
    await page.waitForFunction(() => document.querySelector("#connection-status").textContent.includes("已连接"));
    await page.waitForFunction(() => document.querySelector("#pet-image").dataset.imageState === "ready");
  };
  await page.goto(origin);
  await ready();
  assert.equal(await page.locator("#chat-form").isVisible(), false);
  await send("验证流式回复");
  await page.waitForFunction(() => document.querySelector("#bubble-text").textContent === "模块连接正常。");
  assert.equal(requests.filter(item => item.path.endsWith("/think")).length, 1);

  await send("等待阶段");
  await page.waitForFunction(() => document.querySelector("#bubble-text").textContent.includes("消息已收到"));
  await page.waitForFunction(() => document.querySelector("#bubble-text").textContent.includes("等待模型回复")
    && document.querySelector("#bubble-text").textContent.includes("5 秒"));
  await page.waitForFunction(() => document.querySelector("#bubble-text").textContent.includes("正在重试"));
  await page.waitForFunction(() => document.querySelector("#bubble-text").textContent === "等待阶段完成。");

  await send("失败请求");
  await page.waitForFunction(() => document.querySelector("#bubble-text").textContent.includes("smoke backend failure"));
  await send("慢请求");
  await page.waitForFunction(() => document.querySelector("#runtime-status").textContent.includes("正在发送消息"));
  // Stopping work keeps the continuous conversation and waits for the host stop receipt.
  await page.locator("#stop-reply").evaluate(element => element.click());
  await page.waitForFunction(() => document.querySelector("#bubble-text").textContent.includes("已停止"));
  await page.waitForTimeout(650);
  assert.ok(!(await page.locator("#bubble-text").textContent()).includes("旧会话回复"));
  const sessionIds = requests.filter(item => item.path.endsWith("/think")).map(item => item.body.user_id);
  assert.ok(sessionIds[0]);
  assert.equal(new Set(sessionIds).size, 1);
  assert.equal(await page.locator("#new-session").count(), 0);

  await page.reload();
  await ready();

  await openInput();
  const voiceButton = page.locator("#voice-record-button");
  const voiceState = async text => page.waitForFunction(text => document.querySelector("#voice-record-button").textContent === text, text);
  const holdVoiceShortcut = async () => {
    await page.keyboard.down("Control");
    await page.keyboard.down("Shift");
    await page.keyboard.down("Space");
  };
  const releaseVoiceShortcut = async () => {
    await page.keyboard.up("Space");
    await page.keyboard.up("Shift");
    await page.keyboard.up("Control");
  };
  await holdVoiceShortcut();
  await voiceState("取消");
  assert.equal(await voiceButton.isEnabled(), true);
  await page.keyboard.press("Escape");
  await voiceState("麦");
  await releaseVoiceShortcut();
  await page.evaluate(() => window.__petVoiceSmoke.resolve(0));
  await page.waitForFunction(() => window.__petVoiceSmoke.streams[0]?.track.stops === 1);
  assert.equal(await page.evaluate(() => window.__petVoiceSmoke.recorders.length), 0);

  await holdVoiceShortcut();
  await voiceState("取消");
  await page.evaluate(() => window.__petVoiceSmoke.deny(1));
  await page.waitForFunction(() => document.querySelector("#bubble-text").textContent.includes("没有麦克风权限"));
  await voiceState("麦");
  await releaseVoiceShortcut();

  await holdVoiceShortcut();
  await voiceState("取消");
  await voiceButton.click();
  await voiceState("麦");
  await releaseVoiceShortcut();
  await page.evaluate(() => window.__petVoiceSmoke.resolve(2));
  await page.waitForFunction(() => window.__petVoiceSmoke.streams[1]?.track.stops === 1);
  assert.equal(await page.evaluate(() => window.__petVoiceSmoke.recorders.length), 0);

  await openInput();
  await holdVoiceShortcut();
  await voiceState("取消");
  await page.evaluate(() => window.__petVoiceSmoke.resolve(3));
  await voiceState("停");
  await page.waitForTimeout(750);
  await releaseVoiceShortcut();
  await voiceState("…");
  assert.equal(await voiceButton.isDisabled(), true);
  releaseAsr();
  await page.waitForFunction(() => document.querySelector("#bubble-text").textContent === "模块连接正常。");
  await voiceState("麦");
  assert.equal(await voiceButton.isEnabled(), true);
  assert.equal(requests.filter(item => item.path.endsWith("/asr")).length, 1);
  assert.equal(requests.filter(item => item.body?.message === "浏览器语音").length, 1);
  assert.equal(await page.evaluate(() => window.__petVoiceSmoke.streams[2].track.stops), 1);

  await send("重新进入");
  await page.waitForFunction(() => document.querySelector("#bubble-text").textContent === "模块连接正常。");
  assert.equal(requests.filter(item => item.body?.message === "重新进入").length, 1);
  // Exercise the real DOM/controller graph at the native-frame boundary too.
  // This second controller is not started and every device call is isolated.
  await page.evaluate(async () => {
    const { createPetApplication } = await import("/src/pet/application.js");
    const handlers = new Map();
    const platform = { window, document, navigator, MediaRecorder, isTauriRuntime: true,
      appWindow: null, backendFetch: async () => new Response('{"ok":true}'),
      invoke: async () => null, tauriCall: async () => null, listen: async (name, handler) => {
        handlers.set(name, handler); return () => handlers.delete(name);
      }, emit: async () => {}, emitTo: async () => {}, convertFileSrc: value => value };
    const app = createPetApplication({ platform, bundledCharacterAssets: {} });
    app.state.voiceEnabled = false;
    await app.controllers.plugins.registerPluginAgentEventBridge();
    const bridge = handlers.get("akane:plugin-agent-event-frame");
    window.__petBackgroundSmoke = { app, send(sequence, events, scope = {}) {
      bridge({ payload: { deliveryId: `browser-progress-${sequence}`, payload: { _desktop_turn_event: {
        stream_id: "browser-background", sequence, session_id: app.state.sessionId,
        profile_user_id: app.controllers.persistence.getProfileUserId(),
        character_pack_id: app.controllers.character.getCurrentCharacterPackId(), events, ...scope
      } } } });
    } };
    window.__petBackgroundSmoke.send(0, [{ type: "stream_start", phase: "resuming" }]);
  });
  await page.waitForFunction(() => document.querySelector("#bubble-text").textContent.includes("恢复任务"));
  await page.evaluate(() => window.__petBackgroundSmoke.send(1, [
    { type: "speech_segment", index: 0, text: "后台中途反馈已到达。" }
  ]));
  await page.waitForFunction(() => document.querySelector("#bubble-text").textContent === "后台中途反馈已到达。");
  assert.equal(await page.evaluate(() => window.__petBackgroundSmoke.app.controllers.chat.sending), true);
  await page.evaluate(() => window.__petBackgroundSmoke.send(2, [
    { type: "assistant_working", message: "工具仍在执行。" }
  ]));
  assert.equal(await page.locator("#bubble-text").textContent(), "后台中途反馈已到达。");
  await page.evaluate(() => window.__petBackgroundSmoke.send(3, [
    { type: "final", payload: { speech: "后台任务最终完成。" } }, { type: "stream_end", status: "ok" }
  ]));
  await page.waitForFunction(() => !window.__petBackgroundSmoke.app.controllers.chat.sending);
  await page.waitForFunction(() => document.querySelector("#bubble-text").textContent === "后台任务最终完成。");
  await page.evaluate(() => window.__petBackgroundSmoke.send(4, [
    { type: "final", payload: { speech: "迟到旧帧不应覆盖。" } }, { type: "stream_end", status: "ok" }
  ]));
  await page.waitForTimeout(100);
  assert.equal(await page.locator("#bubble-text").textContent(), "后台任务最终完成。");
  await page.evaluate(() => window.__petBackgroundSmoke.app.dispose());
  assert.deepEqual(errors, []);
  if (process.env.AKANE_PET_SMOKE_OUTPUT) {
    await mkdir(process.env.AKANE_PET_SMOKE_OUTPUT, { recursive: true });
    await page.screenshot({ path: resolve(process.env.AKANE_PET_SMOKE_OUTPUT, "pet-runtime.png") });
  }
  console.log("pet runtime browser smoke: real main entry, resources, foreground input/stream/abort, microphone/ASR, background recovery/preface/tool state/final/late-frame rejection; 0 page errors");
} finally {
  await browser?.close();
  await server.close();
}
