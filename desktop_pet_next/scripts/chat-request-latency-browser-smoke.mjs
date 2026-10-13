// Exercise the real chat controller while optional media requests stay pending.
import assert from "node:assert/strict";
import { createServer } from "vite";
const { chromium } = await import(process.env.AKANE_PLAYWRIGHT_MODULE || "playwright");
const server = await createServer({ server: { host: "127.0.0.1", port: 0 }, plugins: [{
  name: "isolated-chat-latency",
  configureServer(instance) {
    instance.middlewares.use((request, response, next) => {
      if (request.url !== "/__chat_latency.html") return next();
      response.setHeader("Content-Type", "text/html");
      response.end("<!doctype html><title>Chat admission smoke</title>");
    });
  }
}] });
let browser;
try {
  await server.listen();
  browser = await chromium.launch({ channel: process.env.AKANE_BROWSER_CHANNEL || "msedge", headless: true });
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(server.resolvedUrls.local[0] + "__chat_latency.html");
  const result = await page.evaluate(async () => {
    const { createChatController } = await import("/src/pet/chat.js");
    const never = new Promise(() => {}), rows = [];
    for (const scenario of ["ordinary", "play", "snapshot_failed", "explicit_lyrics", "changed_session"]) {
      let revision = 1, releaseLyrics, hydrationOptions;
      const requests = [];
      const readyLyrics = new Promise(resolve => { releaseLyrics = resolve; });
      const scope = { capture: () => revision, isCurrent: value => value === revision };
      const controller = createChatController({
        state: { sessionId: "isolated-latency" }, resourceState: {}, els: {}, scope,
        platform: { window, isTauriRuntime: true, backendFetch: async (url, init) => {
          requests.push({ at: performance.now(), payload: JSON.parse(init.body) });
          return new Response("", { status: 200 });
        } },
        lifecycle: { disposed: false, setTimeout: window.setTimeout.bind(window), clearTimeout: window.clearTimeout.bind(window) },
        perception: { latestDesktopScreenFramesForThink: async () => [] },
        character: { getCurrentCharacterPackId: () => "cecilia", buildCurrentVisual: () => ({}) },
        bubble: { buildClientCapabilities: () => ["speech_units_v1", "tool_actions"] },
        persistence: { getProfileUserId: () => "master" },
        backend: { buildBackendEndpointUrl: () => "/isolated-think", async *readNdjsonEvents() { yield { type: "turn_start" }; } },
        music: { buildDesktopMusicActivity: () => ({ status: "playing", title: "cached track" }) },
        systemMedia: {
          systemMediaLyrics: { status: "pending" },
          refreshSystemMediaSnapshot: () => scenario === "snapshot_failed" ? Promise.reject(new Error("synthetic offline")) : never,
          hydrateSystemMediaLyricsForTurn: async (_message, options) => {
            hydrationOptions = options;
            return options.waitForLyricsHydration ? readyLyrics : { waited: false, status: "background" };
          },
          shouldWaitForSystemMediaLyricsForTurn: (_message, options) => options.waitForLyricsHydration
        },
        care: { buildDesktopCareContext: () => null, getCareFeatureStatus: () => ({ enabled: false }) }
      });
      const token = controller.beginTurn("message");
      const started = performance.now();
      const waiting = ["explicit_lyrics", "changed_session"].includes(scenario);
      const stream = controller.sendThinkStream(scenario === "play" ? "播放吧" : "测试", token,
        waiting ? { waitForLyricsHydration: true } : {});
      const next = stream.next();
      if (waiting) {
        await new Promise(resolve => setTimeout(resolve, 25));
        if (requests.length) throw new Error("Explicit context wait bypassed");
        if (scenario === "changed_session") revision++;
        releaseLyrics({ waited: true, status: "ready" });
      }
      const first = await next;
      await stream.return();
      const elapsed = requests.length ? requests[0].at - started : null;
      if (!waiting && (requests.length !== 1 || elapsed > 100)) throw new Error(`${scenario}: local admission delayed ${elapsed}ms`);
      if (!waiting && hydrationOptions.waitForLyricsHydration !== false) throw new Error("Background lyrics unexpectedly waited");
      if (scenario === "changed_session" && (!first.done || requests.length)) throw new Error("Old session submitted after switching");
      if (scenario === "explicit_lyrics" && requests.length !== 1) throw new Error("Explicit context turn did not submit");
      rows.push({ scenario, local_request_ms: elapsed == null ? null : Math.round(elapsed * 1000) / 1000,
        requests: requests.length, cancelled: first.done });
    }
    return rows;
  });
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ status: "passed", rows: result, real_controller: true, provider_requests: 0 }, null, 2));
} finally {
  await browser?.close();
  await server.close();
}
