import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { segmentSpeechForDelivery, missingReplySegments, SpeechStageReplay } from "../src/speech-delivery.js";

const source = readFileSync(new URL("../src/main.js", import.meta.url), "utf8");
function production(name) {
  const match = new RegExp(`(?:async )?function ${name}\\(`).exec(source);
  assert.ok(match, name);
  const next = /\n(?:async )?function \w+\(/.exec(source.slice(match.index + 1));
  return source.slice(match.index, next ? match.index + 1 + next.index : undefined);
}
const tick = () => new Promise((resolve) => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function harness() {
  const effects = [], requests = [], plays = [], timers = new Map();
  let nextTimer = 0;
  class Player extends EventTarget {
    paused = true; ended = false; duration = 0.1; currentTime = 0;
    play() { const task = deferred(); plays.push(task); this.paused = false; return task.promise; }
    pause() { this.paused = true; }
    removeAttribute() { this.src = ""; }
    load() {}
    end() { this.paused = true; this.ended = true; this.dispatchEvent(new Event("ended")); }
  }
  const player = new Player();
  const scope = {
    state: { voiceEnabled: true, voiceVolume: 0.85 }, resourceState: { tts: { enabled: true } },
    els: { voicePlayer: player, bubbleText: { textContent: "" } },
    ttsPlaybackToken: 0, ttsToken: 1, ttsQueue: [], ttsActive: false, ttsController: null, ttsObjectUrl: "",
    lastTtsSignature: "", resolveTtsWait: null, ttsPrefetch: null,
    activeTurnToken: 1, streamedReplyTurnToken: 1, streamedReplySegments: [],
    streamedReplyTexts: [], streamedReplySegmentActive: false,
    streamingTtsTurnToken: 1, streamingTtsText: "", streamingTtsSegmentKeys: new Set(),
    streamingTtsPendingShort: "", streamingTtsPendingShortKey: "", streamingTtsPendingShortTimer: 0,
    bubbleKind: "thinking", bubbleToken: 1, bubbleTimer: 0, segmentTimer: 0,
    TTS_CHUNK_SOFT_LIMIT: 24, TTS_SHORT_SEGMENT_MAX_CHARS: 4, TTS_SHORT_SEGMENT_HOLD_MS: 420,
    CLIENT_SEGMENT_SOFT_LIMIT: 56, lastTurnSignature: "", lastTurnTextKey: "",
    segmentSpeechForDelivery, missingReplySegments, SpeechStageReplay,
    window: {
      setTimeout(callback, ms) { timers.set(++nextTimer, { callback, ms }); return nextTimer; },
      clearTimeout(id) { timers.delete(id); }
    },
    URL: { revokeObjectURL: (url) => effects.push(["revoke", url]) }, performance,
    isTurnActive: (token) => token === scope.activeTurnToken,
    cancelTtsPrewarm() {}, scheduleTtsPrewarm() {}, closeRealtimeVoiceTurn() {},
    realtimeVoiceTurn: null, voiceInputState: "idle", stopRealtimeVoiceCall() {},
    scheduleSave() {}, scheduleSettingsSnapshot() {}, updateActivityControls() {}, scheduleMusicEmotionRestore() {},
    getSegmentDisplayDelay: () => 2200,
    setPetMotion: (value) => effects.push(["motion", value]),
    setRuntimeStatus: (text) => effects.push(["status", text]),
    logTtsTiming: (event) => effects.push(["timing", event]),
    showBubbleText(text) { scope.bubbleKind = "reply"; scope.els.bubbleText.textContent = text; effects.push(["bubble", text]); },
    displayReplyBubbleText(text) { scope.els.bubbleText.textContent = text; effects.push(["bubble", text]); },
    scheduleBubbleReset() {},
    markTurnLatency() {}, markTurnLatencyOnce() {}, applyPayloadCareSnapshot() {}, applyPayloadEmotion() {},
    applyPayloadActivity() {}, applyPayloadFileDeliveries() {}, applyPayloadBrowserEvents() {},
    hideBubble() {}, clearLocalInteraction() {},
    formatError: (error) => error.message, friendlyErrorMessage: (message) => message,
    fetchTtsAudio(text) { const task = deferred(); requests.push({ ...task, text }); return task.promise; }
  };
  vm.createContext(scope);
  vm.runInContext([
    "shouldSyncReplyToTts", "queueStreamedReplySegment", "showNextStreamedReplySegment",
    "processThinkStream", "resetStreamedReplySegments", "missingFinalReplySegments", "renderPayload",
    "splitSpeechText", "normalizeSegments", "showSpeechSegments", "queueLiveTtsPayloadItems", "removeStreamingTtsPrefix",
    "queueStreamedTtsSegment", "ensureStreamingTtsTurn", "bufferStreamingTtsSegment",
    "flushStreamingTtsPending", "clearStreamingTtsPending", "queueTtsItems", "runTtsQueue",
    "startNextTtsPrepare", "playPreparedTtsAudio", "showFailedTtsReply", "presentTtsReplyText", "waitForTtsAudio",
    "finishTtsWait", "stopTts", "stopTtsAudio", "cleanupTtsObjectUrl", "discardPreparedTtsAudio",
    "discardPendingTtsPrepare", "reportTtsError", "normalizeTtsText", "buildTtsQueueItems",
    "splitTtsTextForLatency", "buildSpeechTextKey", "setTtsActive", "setVoiceEnabled", "hasLocalTtsPlayback"
  ].map(production).join("\n"), scope);
  function audio(index) {
    const request = requests[index];
    request.resolve({ text: request.text, objectUrl: `blob:${index}`, requestMs: 5000 });
  }
  return { scope, player, effects, requests, plays, timers, audio };
}

// The production speech-segment path keeps later text behind actual playback,
// and pipelines only one next synthesis while the current audio plays.
{
  const h = harness();
  for (const [index, text] of ["第一句话准备好了。", "第二句话也准备好了。", "最后一句话。"].entries()) {
    h.scope.queueStreamedReplySegment(text, 1);
    h.scope.queueStreamedTtsSegment(text, 1, index);
  }
  assert.equal(h.scope.hasLocalTtsPlayback(), false, "Synthesis is not speaking");
  assert.equal(h.requests.length, 1);
  assert.equal(h.scope.els.bubbleText.textContent, "第一句话准备好了。");
  assert.equal(h.effects.some(([kind, value]) => kind === "motion" && value === "speaking"), false);
  h.audio(0); await tick();
  assert.equal(h.requests.length, 2);
  h.plays[0].resolve(); await tick();
  assert.equal(h.scope.hasLocalTtsPlayback(), true);
  h.audio(1); await tick();
  assert.equal(h.scope.els.bubbleText.textContent, "第一句话准备好了。");
  h.player.end(); await tick();
  h.player.ended = false;
  h.plays[1].resolve(); await tick();
  assert.equal(h.scope.els.bubbleText.textContent, "第二句话也准备好了。");
  h.audio(2); await tick(); h.player.end(); await tick();
  h.player.ended = false; h.plays[2].resolve(); await tick();
  assert.equal(h.scope.els.bubbleText.textContent, "最后一句话。");
  h.player.end(); await tick();
  assert.equal(h.scope.ttsActive, false);
  assert.equal(h.scope.hasLocalTtsPlayback(), false);
  assert.equal(h.timers.size, 0);
}

// A sentence arriving during playback starts synthesis before the current
// audio ends. Keep one lookahead, preserve order, and discard it on cancellation.
{
  const h = harness();
  const append = (text, index) => {
    h.scope.queueStreamedReplySegment(text, 1);
    h.scope.queueStreamedTtsSegment(text, 1, index);
  };
  append("第一句已经在播放。", 0);
  h.audio(0); await tick();
  h.plays[0].resolve(); await tick();
  assert.equal(h.scope.hasLocalTtsPlayback(), true);
  assert.equal(h.requests.length, 1);
  append("播放时才收到的第二句。", 1);
  assert.equal(h.requests.length, 2, "Late segment synthesis overlaps current playback");
  append("第三句需要继续排队。", 2);
  assert.equal(h.requests.length, 2, "Only one lookahead synthesis");
  h.audio(1); await tick();
  assert.equal(h.scope.els.bubbleText.textContent, "第一句已经在播放。");
  h.player.end(); await tick();
  assert.equal(h.requests.length, 3);
  h.player.ended = false;
  h.plays[1].resolve(); await tick();
  assert.equal(h.scope.els.bubbleText.textContent, "播放时才收到的第二句。");
  h.scope.stopTts();
  h.scope.queueTtsItems(["新一轮的回复。"], "new", { reply: true });
  const newPrefetch = h.scope.ttsPrefetch;
  h.audio(2); await tick();
  assert.ok(h.effects.some(([kind, url]) => kind === "revoke" && url === "blob:2"));
  assert.equal(h.scope.ttsPrefetch, newPrefetch, "Old cleanup preserves new owner");
  h.audio(3); await tick();
  h.player.ended = false;
  h.plays[2].resolve(); await tick();
  assert.equal(h.scope.els.bubbleText.textContent, "新一轮的回复。");
  h.player.end(); await tick();
  assert.equal(h.scope.ttsPrefetch, null);
  assert.equal(h.scope.ttsActive, false);
}

// Ended may precede play() resolution (very short buffered audio).
{
  const h = harness();
  const playback = h.scope.playPreparedTtsAudio({ text: "嗯。", objectUrl: "blob:short" }, 1, { reply: true });
  await tick(); h.player.end(); h.plays[0].resolve(); await playback;
  assert.equal(h.scope.resolveTtsWait, null);
  assert.equal(h.scope.els.bubbleText.textContent, "嗯。");
}

// A cancellation settles a never-resolving play() and late completion cannot
// clear the newer owner's audio URL, display, listeners or completion waiter.
{
  const h = harness();
  const old = h.scope.playPreparedTtsAudio({ text: "旧回复", objectUrl: "blob:old" }, 1, { reply: true });
  await tick(); h.scope.stopTts(); await old;
  const current = h.scope.playPreparedTtsAudio({ text: "新回复", objectUrl: "blob:new" }, h.scope.ttsToken, { reply: true });
  await tick(); h.plays[0].resolve(); await tick();
  assert.equal(h.scope.ttsObjectUrl, "blob:new");
  h.plays[1].resolve(); await tick();
  assert.equal(h.scope.els.bubbleText.textContent, "新回复");
  h.player.end(); await current;
  assert.equal(h.timers.size, 0);
}

// Synthesis failure stays readable, then the next segment can actually play.
{
  const h = harness();
  h.scope.queueTtsItems(["失败的这一句。", "后面还能继续。"], "reply", { append: true, preserveSegments: true, reply: true });
  h.requests[0].reject(new Error("provider unavailable")); await tick();
  assert.equal(h.scope.els.bubbleText.textContent, "失败的这一句。");
  h.audio(1);
  const reading = [...h.timers.values()].find((timer) => timer.ms === 2200);
  assert.ok(reading); reading.callback(); await tick();
  h.plays[0].resolve(); await tick();
  assert.equal(h.scope.els.bubbleText.textContent, "后面还能继续。");
  h.player.end(); await tick();
  assert.equal(h.scope.ttsActive, false);
}

// Output device error / never-started playback are visible failures, not hangs.
for (const fail of ["error", "timeout", "rejected"]) {
  const h = harness();
  const play = h.scope.playPreparedTtsAudio({ text: "测试", objectUrl: "blob:test" }, 1);
  const rejected = assert.rejects(play);
  await tick();
  if (fail === "error") h.player.dispatchEvent(new Event("error"));
  else if (fail === "timeout") [...h.timers.values()][0].callback();
  else h.plays[0].reject(new Error("autoplay denied"));
  await rejected;
  assert.equal(h.scope.resolveTtsWait, null);
  assert.equal(h.timers.size, 0);
}

// Muting during slow synthesis releases the audio wait and reveals text.
{
  const h = harness();
  h.scope.queueStreamedReplySegment("还可以继续读文字。", 1);
  h.scope.queueStreamedTtsSegment("还可以继续读文字。", 1, 0);
  h.scope.setVoiceEnabled(false);
  assert.equal(h.scope.els.bubbleText.textContent, "还可以继续读文字。");
  h.audio(0); await tick();
  assert.equal(h.plays.length, 0);
  assert.equal(h.scope.ttsActive, false);
}
console.log("TTS playback and bubble presentation smoke: ok");

// Long English/bracketed text stays one visible sentence while the existing
// short audio requests still prefetch and play independently.
{
  const h = harness();
  const sentence = "刚才[The quick brown fox jumps over the lazy dog and returns to the castle]这段挺有意思。";
  h.scope.queueStreamedReplySegment(sentence, 1);
  h.scope.queueStreamedTtsSegment(sentence, 1, 0);
  assert.ok(h.requests[0].text.length <= 24);
  h.audio(0); await tick(); h.plays[0].resolve(); await tick();
  h.audio(1); await tick(); h.player.end(); await tick();
  h.player.ended = false; h.plays[1].resolve(); await tick();
  assert.equal(h.scope.els.bubbleText.textContent, sentence);
  assert.deepEqual(h.effects.filter(([kind]) => kind === "bubble"), [["bubble", sentence]], "Preview and later audio blocks do not replay or split the bubble");
  h.scope.stopTts();
}

async function* events(items) { yield* items; }
async function drainAudio(h) {
  for (let i = 0; h.scope.ttsActive || h.scope.ttsQueue.length; i += 1) {
    assert.ok(i < 100, "Audio queue must finish");
    h.audio(i); await tick(); h.player.ended = false;
    h.plays[i].resolve(); await tick(); h.player.end(); await tick();
  }
}
function drainBubbles(h) {
  for (let i = 0; h.scope.segmentTimer; i += 1) {
    assert.ok(i < 100, "Bubble queue must finish");
    const timer = h.timers.get(h.scope.segmentTimer);
    h.timers.delete(h.scope.segmentTimer); timer.callback();
  }
}
// Actual SSE consumer + final renderer + delivery queues, with real stream
// boundaries differing from the 56-character final fallback.
for (const voice of [false, true]) {
  const h = harness(); h.scope.state.voiceEnabled = voice;
  const speech = "刚才[The quick brown fox jumps over the lazy dog and returns to the castle]这段挺有意思。";
  const longChinese = `${"这是很长的完整文字".repeat(8)}，然后才到句子的后半段。`;
  const final = { speech: speech + longChinese };
  await h.scope.processThinkStream(events([
    { type: "speech_segment", text: speech, index: 0 },
    { type: "speech_segment", text: speech, index: 0 },
    { type: "speech_segment", text: longChinese, index: 1 },
    { type: "final_ui", payload: final }, { type: "final", payload: final }, { type: "stream_end" }
  ]), 1);
  if (voice) await drainAudio(h); else drainBubbles(h);
  assert.deepEqual(h.effects.filter(([kind]) => kind === "bubble").map(([, text]) => text), [speech, longChinese]);
  assert.equal(h.requests.length > 0, voice);
}
// A complete final without segment events still preserves pairs, and repeated
// occurrences with distinct stream indices must each be delivered once.
for (const voice of [false, true]) {
  const h = harness(); h.scope.state.voiceEnabled = voice;
  const speech = '她说："看《书里[还有\'nested!\']》吧。"结束。';
  await h.scope.processThinkStream(events([{ type: "final", payload: { speech } }]), 1);
  if (voice) await drainAudio(h); else drainBubbles(h);
  assert.deepEqual(h.effects.filter(([kind]) => kind === "bubble").map(([, text]) => text), [speech]);
}
{
  const h = harness(); h.scope.state.voiceEnabled = false;
  await h.scope.processThinkStream(events([
    { type: "speech_segment", text: "好的。", index: 0 },
    { type: "speech_segment", text: "好的。", index: 1 },
    { type: "final", payload: { speech: "好的。好的。" } }
  ]), 1);
  drainBubbles(h);
  assert.deepEqual(h.effects.filter(([kind]) => kind === "bubble").map(([, text]) => text), ["好的。", "好的。"]);
}
console.log("Production SSE/final/audio/text delivery: duplicate events and resegmentation passed");
