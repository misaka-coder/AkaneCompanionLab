// Production chat, bubble and TTS controllers, real DOM and HTMLAudioElement.
// Only model events and synthesis transport are controlled; no live chats.
import assert from "node:assert/strict";
import { createServer } from "vite";
const { chromium } = await import(process.env.AKANE_PLAYWRIGHT_MODULE || "playwright");
const frames = 28800;
const wav = Buffer.alloc(44 + frames * 2);
wav.write("RIFF"); wav.writeUInt32LE(wav.length - 8, 4); wav.write("WAVEfmt ", 8);
wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
wav.writeUInt32LE(48000, 24); wav.writeUInt32LE(96000, 28);
wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34); wav.write("data", 36); wav.writeUInt32LE(frames * 2, 40);
const server = await createServer({ server: { host: "127.0.0.1", port: 0 }, plugins: [{
  name: "isolated-bilingual-bubbles",
  configureServer(instance) {
    instance.middlewares.use((request, response, next) => {
      if (request.url !== "/__bilingual_bubble.html") return next();
      response.setHeader("Content-Type", "text/html");
      response.end('<!doctype html><audio id="player"></audio><div id="bubble"><span id="text"></span></div>');
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
  await page.goto(server.resolvedUrls.local[0] + "__bilingual_bubble.html");
  const result = await page.evaluate(async encoded => {
    const [{ createTtsController }, { createBubbleController }, { createChatController }, profiles, unitsApi] = await Promise.all([
      import("/src/pet/tts.js"), import("/src/pet/bubble.js"), import("/src/pet/chat.js"),
      import("/src/character-profile.js"), import("/src/speech-units.js")
    ]);
    profiles.setRuntimeCharacterPacks([{ id: "bubble-smoke", profile: { identity: { id: "bubble-smoke" }, voice: { provider: "irodori" } } }]);
    profiles.selectCharacterPack("bubble-smoke", { persist: false });
    const controllers = {};
    const port = name => new Proxy({}, { get(_target, key) {
      const value = controllers[name]?.[key];
      return typeof value === "function" ? value.bind(controllers[name]) : value;
    } });
    const requests = [], statuses = [], played = [], snapshots = [];
    const state = { voiceEnabled: true, voiceVolume: .85 };
    const els = { voicePlayer: document.querySelector("#player"), bubble: document.querySelector("#bubble"), bubbleText: document.querySelector("#text") };
    const player = els.voicePlayer;
    const bytes = Uint8Array.from(atob(encoded), char => char.charCodeAt(0));
    let releaseFirst, releaseSecondPlay, releaseOld, playCalls = 0;
    const realPlay = player.play.bind(player);
    player.play = async () => {
      playCalls += 1;
      if (playCalls === 2) await new Promise(resolve => { releaseSecondPlay = resolve; });
      return realPlay();
    };
    player.addEventListener("playing", () => played.push(player.src));
    const common = {
      state, resourceState: { health: "online", tts: { enabled: true } }, els,
      lifecycle: { disposed: false, setTimeout: (...args) => window.setTimeout(...args), clearTimeout: (...args) => window.clearTimeout(...args) },
      platform: { window, isTauriRuntime: false, async backendFetch(_url, init) {
        const body = JSON.parse(init.body); requests.push(body);
        if (requests.length === 1) await new Promise(resolve => { releaseFirst = resolve; });
        if (body.text === "前の返事です。") await new Promise(resolve => { releaseOld = resolve; });
        return new Response(bytes, { headers: { "Content-Type": "audio/wav" } });
      } },
      scope: { capture: () => ({}), isCurrent: () => true },
      chat: port("chat"), bubble: port("bubble"), tts: port("tts"),
      visualRenderer: { setMotion() {} },
      character: { buildBackendCharacterContext: () => ({ character_pack_id: "cecilia" }), setPetEmotion() {} },
      care: { applyAuthoritativeCareSnapshot() {} }, attachments: { applyPayloadFileDeliveries() {}, applyPayloadBrowserEvents() {} },
      interaction: { clearLocalInteraction() {} }, window: { scheduleNativeHitTestSync() {} },
      status: { setRuntimeStatus: text => statuses.push(text), updateActivityControls() {} },
      backend: { buildBackendEndpointUrl: () => "/isolated-tts" }, perception: { proactiveWakeRunning: false },
      voice: { voiceInputState: "idle", hasRealtimeVoiceCallPlayback: () => false },
      settings: { scheduleSettingsSnapshot() {} }, music: { scheduleMusicEmotionRestore() {} }
    };
    controllers.bubble = createBubbleController(common);
    controllers.tts = createTtsController(common);
    controllers.chat = createChatController(common);
    const wait = async predicate => {
      const deadline = performance.now() + 5000;
      while (!predicate()) {
        if (performance.now() > deadline) throw new Error("Browser sync timed out: " + JSON.stringify({ requests, statuses, played: played.length, playCalls, text: els.bubbleText.textContent }));
        await new Promise(resolve => setTimeout(resolve, 10));
      }
    };
    const check = (phase, text) => {
      snapshots.push({ phase, text: els.bubbleText.textContent });
      if (els.bubbleText.textContent !== text) throw new Error(`Bubble advanced during ${phase}`);
      if (!els.bubble.classList.contains("visible")) throw new Error(`Bubble hidden during ${phase}`);
    };
    const units = [
      { text: "累了就休息吧。慢慢来。", say: "無理をしないでください。ゆっくり休みましょう。", style: "温柔关切" },
      { text: "我陪你喝杯茶。", say: "一緒にお茶を飲みましょう。", style: "轻松" }
    ];
    async function* stream(utterances) {
      yield { type: "speech_mode", speech_protocol: unitsApi.SPEECH_UNITS_PROTOCOL };
      yield* unitsApi.utteranceDeliveryEvents(utterances);
      const payload = { utterances, speech: utterances.map(unit => unit.text).join(""), speech_protocol: unitsApi.SPEECH_UNITS_PROTOCOL };
      yield { type: "final_ui", payload }; yield { type: "final", payload }; yield { type: "stream_end" };
    }
    const turn = controllers.chat.beginTurn("test");
    await controllers.chat.processThinkStream(stream(units), turn); controllers.chat.endTurn(turn);
    check("slow-synthesis", units[0].text);
    if (requests.length !== 1) throw new Error("Unexpected parallel preparation");
    releaseFirst();
    await wait(() => played.length === 1 && requests.length === 2);
    check("next-audio-ready-current-still-playing", units[0].text);
    await wait(() => Boolean(releaseSecondPlay));
    check("next-play-not-started", units[0].text);
    releaseSecondPlay();
    await wait(() => played.length === 2 && els.bubbleText.textContent === units[1].text);
    check("second-audio-started", units[1].text);
    await wait(() => !controllers.tts.ttsActive);
    if (controllers.bubble.streamedReplySegments.length) throw new Error("Consumed translations left behind");
    // Stop + a new turn also rejects late model transport and prepared audio.
    const oldTurn = controllers.chat.beginTurn("old");
    await controllers.chat.processThinkStream(stream([{ text: "旧回复。", say: "前の返事です。" }]), oldTurn);
    await wait(() => Boolean(releaseOld));
    controllers.tts.stopTts();
    const newTurn = controllers.chat.beginTurn("new");
    await controllers.chat.processThinkStream(stream([{ text: "新的回复。", say: "新しい返事です。" }]), newTurn);
    releaseOld();
    await wait(() => played.length === 3 && !controllers.tts.ttsActive);
    check("cancelled-old-turn", "新的回复。");
    controllers.chat.endTurn(newTurn); controllers.tts.stopTts(); controllers.bubble.hideBubble();
    const recoveredUnits = [
      { text: "恢复前已经说完的句子。", say: "最初の返事です。", style: "calm" },
      { text: "恢复后补充的句子。", say: "続きの返事です。", style: "happy" }
    ];
    async function* recoveryStream() {
      for (const unit of recoveredUnits) {
        yield { type: "speech_mode", speech_protocol: unitsApi.SPEECH_UNITS_PROTOCOL };
        yield* unitsApi.utteranceDeliveryEvents([unit]);
      }
      const payload = { utterances: recoveredUnits, speech: recoveredUnits.map(unit => unit.text).join(""),
        speech_protocol: unitsApi.SPEECH_UNITS_PROTOCOL };
      yield { type: "final_ui", payload }; yield { type: "final", payload }; yield { type: "stream_end" };
    }
    const recoveryTurn = controllers.chat.beginTurn("recovery");
    await controllers.chat.processThinkStream(recoveryStream(), recoveryTurn);
    await wait(() => played.length === 5 && !controllers.tts.ttsActive);
    check("recovery-canonical-final-does-not-replay", recoveredUnits[1].text);
    controllers.chat.endTurn(recoveryTurn); controllers.tts.stopTts(); controllers.bubble.hideBubble();
    return { snapshots, requests: requests.map(request => request.text), played: played.length,
      failed: statuses.some(text => text.startsWith("语音播放失败")) };
  }, wav.toString("base64"));
  assert.equal(result.failed, false);
  assert.equal(result.played, 5);
  assert.equal(result.requests.length, 6);
  assert.deepEqual(result.requests.slice(-2), ["最初の返事です。", "続きの返事です。"]);
  assert.equal(errors.length, 0, errors.join("\n"));
  console.log("Bilingual real browser/chat/bubble/audio: waiting, ready lookahead, actual start, final dedup and stale cancellation passed");
} finally {
  await browser?.close();
  await server.close();
}
