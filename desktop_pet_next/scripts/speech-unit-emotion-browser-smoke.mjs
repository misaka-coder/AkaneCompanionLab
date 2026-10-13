// Real chat, bubble, audio and portrait renderer; transport supplies fixed
// model events and WAV only. This never opens a Bot turn or sends QQ messages.
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { createServer } from "vite";
const { chromium } = await import(process.env.AKANE_PLAYWRIGHT_MODULE || "playwright");
const wav = Buffer.alloc(44 + 24000 * 2);
wav.write("RIFF"); wav.writeUInt32LE(wav.length - 8, 4); wav.write("WAVEfmt ", 8);
wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
wav.writeUInt32LE(48000, 24); wav.writeUInt32LE(96000, 28);
wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34); wav.write("data", 36); wav.writeUInt32LE(wav.length - 44, 40);
const server = await createServer({ server: { host: "127.0.0.1", port: 0 }, plugins: [{
  name: "isolated-unit-expressions",
  configureServer(instance) {
    instance.middlewares.use((request, response, next) => {
      if (request.url !== "/__unit_expression.html") return next();
      response.setHeader("Content-Type", "text/html");
      response.end('<!doctype html><main id="stage"><img id="portrait" width="160" height="160"></main><audio id="player"></audio><div id="bubble"><span id="text"></span></div>');
    });
  }
}] });
let browser;
try {
  await server.listen();
  browser = await chromium.launch({ channel: process.env.AKANE_BROWSER_CHANNEL || "msedge", headless: true,
    args: ["--autoplay-policy=no-user-gesture-required"] });
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(server.resolvedUrls.local[0] + "__unit_expression.html");
  const result = await page.evaluate(async encoded => {
    const [ttsApi, bubbleApi, chatApi, profiles, unitsApi, rendererApi] = await Promise.all([
      import("/src/pet/tts.js"), import("/src/pet/bubble.js"), import("/src/pet/chat.js"),
      import("/src/character-profile.js"), import("/src/speech-units.js"), import("/src/visual-renderer.js")
    ]);
    profiles.setRuntimeCharacterPacks([{ id: "expression-smoke", profile: { identity: { id: "expression-smoke" }, voice: { provider: "irodori" } } }]);
    profiles.selectCharacterPack("expression-smoke", { persist: false });
    const controllers = {}, requests = [], expressions = [], played = [];
    const port = name => new Proxy({}, { get(_target, key) {
      const value = controllers[name]?.[key];
      return typeof value === "function" ? value.bind(controllers[name]) : value;
    } });
    const els = { voicePlayer: document.querySelector("#player"), bubble: document.querySelector("#bubble"),
      bubbleText: document.querySelector("#text") };
    const portrait = document.querySelector("#portrait");
    const renderer = rendererApi.createVisualRenderer({ image: portrait, stage: document.querySelector("#stage") });
    const state = { voiceEnabled: true, voiceVolume: .7, currentEmotion: "thinking" };
    const bytes = Uint8Array.from(atob(encoded), char => char.charCodeAt(0));
    let releaseFirst, releaseSecondPlay, releaseOld, playCalls = 0;
    const realPlay = els.voicePlayer.play.bind(els.voicePlayer);
    els.voicePlayer.play = async () => {
      if (++playCalls === 2) await new Promise(resolve => { releaseSecondPlay = resolve; });
      return realPlay();
    };
    els.voicePlayer.addEventListener("playing", () => played.push(state.currentEmotion));
    const colors = { normal: "#71b7ae", shy: "#eda6bc", happy: "#efc979" };
    const common = {
      state, resourceState: { health: "online", tts: { enabled: true } }, els, visualRenderer: renderer,
      lifecycle: { disposed: false, setTimeout: (...args) => window.setTimeout(...args), clearTimeout: (...args) => window.clearTimeout(...args) },
      platform: { window, isTauriRuntime: false, async backendFetch(_url, init) {
        const body = JSON.parse(init.body); requests.push(body);
        if (requests.length === 1) await new Promise(resolve => { releaseFirst = resolve; });
        if (body.text === "前の返事です。") await new Promise(resolve => { releaseOld = resolve; });
        return new Response(bytes, { headers: { "Content-Type": "audio/wav" } });
      } },
      scope: { capture: () => ({}), isCurrent: () => true },
      chat: port("chat"), bubble: port("bubble"), tts: port("tts"),
      character: { buildBackendCharacterContext: () => ({ character_pack_id: "cecilia" }), setPetEmotion(emotion) {
        if (!colors[emotion]) throw new Error("Invalid fixture expression: " + emotion);
        expressions.push(emotion); state.currentEmotion = emotion;
        const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="160"><rect width="160" height="160" fill="' + colors[emotion] + '"/><text x="20" y="85">' + emotion + '</text></svg>';
        renderer.setExpression({ id: emotion, url: "data:image/svg+xml," + encodeURIComponent(svg) });
      } },
      care: { applyAuthoritativeCareSnapshot() {} }, attachments: { applyPayloadFileDeliveries() {}, applyPayloadBrowserEvents() {} },
      interaction: { clearLocalInteraction() {} }, window: { scheduleNativeHitTestSync() {} },
      status: { setRuntimeStatus() {}, updateActivityControls() {} },
      backend: { buildBackendEndpointUrl: () => "/isolated-tts" }, perception: { proactiveWakeRunning: false },
      voice: { voiceInputState: "idle", hasRealtimeVoiceCallPlayback: () => false },
      settings: { scheduleSettingsSnapshot() {} }, music: { scheduleMusicEmotionRestore() {} }
    };
    controllers.bubble = bubbleApi.createBubbleController(common);
    controllers.tts = ttsApi.createTtsController(common);
    controllers.chat = chatApi.createChatController(common);
    const wait = async predicate => {
      const deadline = performance.now() + 8000;
      while (!predicate()) {
        if (performance.now() > deadline) throw new Error("Expression browser sync timed out");
        await new Promise(resolve => setTimeout(resolve, 10));
      }
    };
    async function* stream(utterances, emotion = "happy", finalOnly = false) {
      const payload = { emotion, utterances, speech: utterances.map(unit => unit.text).join(""), speech_protocol: unitsApi.SPEECH_UNITS_PROTOCOL };
      yield { type: "ui", emotion };
      if (!finalOnly) {
        yield { type: "speech_mode", speech_protocol: unitsApi.SPEECH_UNITS_PROTOCOL, emotion };
        yield* unitsApi.utteranceDeliveryEvents(utterances, emotion);
        yield { type: "assistant_stage_decision" };
      }
      yield { type: "final_ui", payload }; yield { type: "final", payload }; yield { type: "stream_end" };
    }
    const units = [
      { emotion: "normal", text: "先认真解释。", say: "まず説明します。", style: "落ち着いた声で説明する。" },
      { emotion: "shy", text: "接着有些害羞。", say: "少し恥ずかしいです。", style: "落ち着いた声で説明する。" }
    ];
    const turn = controllers.chat.beginTurn("test");
    await controllers.chat.processThinkStream(stream(units), turn);
    await wait(() => portrait.dataset.emotion === "normal");
    assertExpression("normal", units[0].text);
    if (expressions.includes("happy")) throw new Error("Root expression flashed before the first group");
    releaseFirst();
    await wait(() => played.length === 1 && requests.length === 2);
    assertExpression("normal", units[0].text);
    await wait(() => Boolean(releaseSecondPlay));
    assertExpression("normal", units[0].text);
    releaseSecondPlay();
    await wait(() => played.length === 2 && portrait.dataset.emotion === "shy");
    assertExpression("shy", units[1].text);
    await wait(() => !controllers.tts.ttsActive);
    if (requests.length !== 2 || new Set(requests.map(request => request.voice_style)).size !== 1) throw new Error("Visual emotion changed voice selection");
    controllers.chat.endTurn(turn);
    state.voiceEnabled = false;
    const textTurn = controllers.chat.beginTurn("text");
    const silent = [{ text: "先用顶层表情。", say: null }, { emotion: "shy", text: "接着有些害羞。", say: null }, { text: "延续上一组。", say: null }];
    await controllers.chat.processThinkStream(stream(silent, "normal", true), textTurn);
    await wait(() => portrait.dataset.emotion === "normal");
    assertExpression("normal", silent[0].text);
    await wait(() => els.bubbleText.textContent === silent[1].text && portrait.dataset.emotion === "shy");
    await wait(() => els.bubbleText.textContent === silent[2].text);
    assertExpression("shy", silent[2].text);
    if (requests.length !== 2) throw new Error("Silent/text mode requested synthesis");
    controllers.chat.endTurn(textTurn);
    state.voiceEnabled = true;
    const oldTurn = controllers.chat.beginTurn("old");
    await controllers.chat.processThinkStream(stream([{ emotion: "shy", text: "旧回复。", say: "前の返事です。" }]), oldTurn);
    await wait(() => Boolean(releaseOld));
    controllers.tts.stopTts();
    const newTurn = controllers.chat.beginTurn("new");
    await controllers.chat.processThinkStream(stream([{ emotion: "normal", text: "新的回复。", say: "新しい返事です。" }]), newTurn);
    releaseOld();
    await wait(() => played.length === 3 && !controllers.tts.ttsActive && portrait.dataset.emotion === "normal");
    assertExpression("normal", "新的回复。");
    controllers.chat.endTurn(newTurn);
    controllers.bubble.restoreLatestReply({ latest_final_json: { emotion: "happy", utterances: units,
      speech: units.map(unit => unit.text).join(""), speech_protocol: unitsApi.SPEECH_UNITS_PROTOCOL } });
    await wait(() => portrait.dataset.emotion === "shy");
    if (state.currentEmotion !== "shy" || requests.length !== 4) throw new Error("Restore replayed synthesis or lost the last unit expression");
    controllers.tts.stopTts(); controllers.bubble.hideBubble();
    return { expressions, requests: requests.length, played: played.length, restored: state.currentEmotion };
    function assertExpression(emotion, text) {
      if (state.currentEmotion !== emotion || portrait.dataset.emotion !== emotion || els.bubbleText.textContent !== text) throw new Error("Expression/text got out of sync");
    }
  }, wav.toString("base64"));
  assert.equal(errors.length, 0, errors.join("\n"));
  assert.equal(result.restored, "shy");
  await mkdir(new URL("../../output/speech_unit_emotion_20261006/", import.meta.url), { recursive: true });
  await page.screenshot({ path: new URL("../../output/speech_unit_emotion_20261006/browser.png", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"), fullPage: true });
  console.log("Real browser expressions: first/root priority, playback timing, text-only inheritance, stale cancellation and restore passed", JSON.stringify(result));
} finally {
  await browser?.close();
  await server.close();
}
