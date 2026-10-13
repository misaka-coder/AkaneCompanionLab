// Real browser/controller/audio player with an isolated synthesis transport.
import assert from "node:assert/strict";
import { createServer } from "vite";
const { chromium } = await import(process.env.AKANE_PLAYWRIGHT_MODULE || "playwright");
const wav = Buffer.alloc(44 + 9600);
wav.write("RIFF"); wav.writeUInt32LE(wav.length - 8, 4); wav.write("WAVEfmt ", 8);
wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
wav.writeUInt32LE(48000, 24); wav.writeUInt32LE(96000, 28);
wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34); wav.write("data", 36); wav.writeUInt32LE(9600, 40);
const server = await createServer({ server: { host: "127.0.0.1", port: 0 }, plugins: [{
  name: "isolated-irodori-delivery",
  configureServer(instance) {
    instance.middlewares.use((request, response, next) => {
      if (request.url !== "/__irodori_delivery.html") return next();
      response.setHeader("Content-Type", "text/html");
      response.end('<!doctype html><audio id="player"></audio>');
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
  await page.goto(server.resolvedUrls.local[0] + "__irodori_delivery.html");
  const result = await page.evaluate(async encoded => {
    const { createTtsController } = await import("/src/pet/tts.js");
    const profiles = await import("/src/character-profile.js");
    const { utteranceVoiceSegments } = await import("/src/speech-units.js");
    profiles.setRuntimeCharacterPacks([{ id: "irodori-smoke", profile: { identity: { id: "irodori-smoke" }, voice: { provider: "irodori", profile_id: "speaker" } } }]);
    profiles.selectCharacterPack("irodori-smoke", { persist: false });
    const requests = [], statuses = [], played = [];
    let activeTurn = 1, releaseSlow;
    const player = document.querySelector("#player");
    const bubbleText = { textContent: "" };
    player.addEventListener("playing", () => played.push(player.src));
    const bytes = Uint8Array.from(atob(encoded), char => char.charCodeAt(0));
    const controller = createTtsController({
      state: { voiceEnabled: true, voiceVolume: .85 }, resourceState: { health: "online", tts: { enabled: true } },
      els: { voicePlayer: player, bubbleText }, lifecycle: {
        setTimeout: (...args) => window.setTimeout(...args), clearTimeout: (...args) => window.clearTimeout(...args)
      },
      platform: { window, isTauriRuntime: false, async backendFetch(url, init) {
        const body = JSON.parse(init.body); requests.push(body);
        if (body.text === "遅い返事です。") await new Promise(resolve => { releaseSlow = resolve; });
        return new Response(bytes, { headers: { "Content-Type": "audio/wav" } });
      } },
      chat: { isTurnActive: token => token === activeTurn, sending: true },
      bubble: { setPetMotion() {}, replyDisplayActive: false, shouldSyncReplyToTts: () => false,
        showBubbleText: text => { bubbleText.textContent = text; } },
      perception: { proactiveWakeRunning: false }, voice: { voiceInputState: "idle" },
      character: { buildBackendCharacterContext: () => ({ character_pack_id: "cecilia" }) },
      backend: { buildBackendEndpointUrl: () => "/isolated-tts" },
      status: { setRuntimeStatus: text => statuses.push(text), updateActivityControls() {} },
      settings: { scheduleSettingsSnapshot() {} }, music: { scheduleMusicEmotionRestore() {} }
    });
    const wait = async predicate => {
      const deadline = performance.now() + 5000;
      while (!predicate()) {
        if (performance.now() > deadline) throw new Error("Browser delivery timed out: " + JSON.stringify({
          requests, statuses, played: played.length, active: controller.ttsActive,
          paused: player.paused, ended: player.ended, mediaError: player.error?.message
        }));
        await new Promise(resolve => setTimeout(resolve, 10));
      }
    };
    const say = "少し疲れているみたいですね。無理をしないで、ゆっくり休んでください。";
    for (const event of utteranceVoiceSegments([{ text: "累了就休息吧。", say, style: "温柔关切" }])) {
      controller.queueSpeechUnitAudio(event, 1);
    }
    if (requests.length) throw new Error("Expression was split before say completion");
    controller.finishSpeechUnitAudio({ unit_index: 0 }, 1);
    await wait(() => played.length === 1 && !controller.ttsActive);
    controller.finishSpeechUnitAudio({ unit_index: 0 }, 1);
    if (requests.length !== 1 || requests[0].text !== say) throw new Error("Complete expression/replay mismatch");
    if (requests[0].voice_style !== "温柔关切") throw new Error("Expression intent changed");
    controller.queueSpeechUnitAudio({ text: "遅い返事です。", unit_index: 1, style: "轻松" }, 1);
    controller.finishSpeechUnitAudio({ unit_index: 1 }, 1);
    await wait(() => Boolean(releaseSlow));
    controller.stopTts(); activeTurn = 2;
    controller.queueSpeechUnitAudio({ text: "新しい返事です。", unit_index: 0, style: "开心" }, 2);
    controller.finishSpeechUnitAudio({ unit_index: 0 }, 1);
    if (requests.length !== 2) throw new Error("Stale turn flushed new speech");
    releaseSlow(); await new Promise(resolve => setTimeout(resolve, 50));
    if (played.length !== 1) throw new Error("Cancelled audio resumed");
    controller.finishSpeechUnitAudio({ unit_index: 0 }, 2);
    await wait(() => played.length === 2 && !controller.ttsActive);
    controller.stopTts();
    return { requests: requests.map(item => item.text), played: played.length, decoded_seconds: player.duration,
      failed: statuses.some(text => text.startsWith("语音播放失败")) };
  }, wav.toString("base64"));
  assert.equal(result.failed, false);
  assert.equal(result.played, 2);
  assert.equal(errors.length, 0, errors.join("\n"));
  console.log("Irodori browser/audio: intact expression, preserved intent, completion, stop and stale turn passed");
} finally {
  await browser?.close();
  await server.close();
}
