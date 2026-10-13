import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm, { petRuntimeSource, petFunctionSource, petFunctions } from "./pet-production-functions.mjs";
import { segmentSpeechForDelivery, missingReplySegments, SpeechStageReplay } from "../src/speech-delivery.js";
import { SPEECH_UNITS_PROTOCOL, VoiceStageReplay, utteranceVoiceSegments, utteranceDeliveryEvents, voiceQueueItem, canMergeVoiceItems, normalizeUnitEmotion,
  continuousVoiceChunks, CONTINUOUS_VOICE_MAX_CHARS } from "../src/speech-units.js";

const source = petRuntimeSource();
const production = petFunctionSource;
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
    streamedReplyUnitEmotions: new Map(), streamedReplyUnitKeys: [], streamedReplySeenUnitKeys: new Set(),
    streamingTtsTurnToken: 1, streamingTtsText: "", streamingTtsSegmentKeys: new Set(),
    streamingTtsPendingShort: "", streamingTtsPendingShortKey: "", streamingTtsPendingShortTimer: 0,
    speechUnitPending: null, speechUnitPendingTimer: 0, speechUnitsTurnToken: 0, speechUnitsStreaming: false, speechUnitsMutedTurnToken: 0,
    bubbleKind: "thinking", bubbleToken: 1, bubbleTimer: 0, segmentTimer: 0,
    TTS_CHUNK_SOFT_LIMIT: 24, TTS_SHORT_SEGMENT_MAX_CHARS: 4, TTS_SHORT_SEGMENT_HOLD_MS: 420,
    CLIENT_SEGMENT_SOFT_LIMIT: 56, lastTurnSignature: "", lastTurnTextKey: "",
    segmentSpeechForDelivery, missingReplySegments, SpeechStageReplay,
    SPEECH_UNITS_PROTOCOL, VoiceStageReplay, utteranceVoiceSegments, utteranceDeliveryEvents, voiceQueueItem, canMergeVoiceItems, normalizeUnitEmotion,
    continuousVoiceChunks, CONTINUOUS_VOICE_MAX_CHARS,
    getActiveCharacterProfile: () => ({ voice: { provider: scope.voiceProvider || "gpt_sovits" } }),
    getProfileDefaultEmotion: () => "normal",
    sceneOwnsPlayback: () => false,
    isTauriRuntime: false,
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
    setPetEmotion: (value) => effects.push(["emotion", value]),
    setRuntimeStatus: (text) => effects.push(["status", text]),
    logTtsTiming: (event) => effects.push(["timing", event]),
    showBubbleText(text) { scope.bubbleKind = "reply"; scope.els.bubbleText.textContent = text; effects.push(["bubble", text]); },
    displayReplyBubbleText(text) { scope.els.bubbleText.textContent = text; effects.push(["bubble", text]); },
    scheduleBubbleReset() {},
    markTurnLatency() {}, markTurnLatencyOnce() {}, applyPayloadCareSnapshot() {}, applyPayloadEmotion() {},
    applyPayloadActivity() {}, applyPayloadFileDeliveries() {}, applyPayloadBrowserEvents() {},
    hideBubble() {}, clearLocalInteraction() {},
    formatError: (error) => error.message, friendlyErrorMessage: (message) => message,
    fetchTtsAudio(text, token, item) { const task = deferred(); requests.push({ ...task, text, item }); return task.promise; }
  };
  vm.createContext(scope);
  vm.runInContext([
    "shouldSyncReplyToTts", "queueStreamedReplySegment", "showNextStreamedReplySegment",
    "queueSpeechUnitText", "markSpeechUnitPresented", "applySpeechUnitEmotion",
    "processThinkStream", "resetStreamedReplySegments", "missingFinalReplySegments", "renderPayload",
    "splitSpeechText", "normalizeSegments", "showSpeechSegments", "queueLiveTtsPayloadItems", "removeStreamingTtsPrefix",
    "queueStreamedTtsSegment", "ensureStreamingTtsTurn", "bufferStreamingTtsSegment",
    "flushStreamingTtsPending", "clearStreamingTtsPending", "queueTtsItems", "runTtsQueue",
    "queueSpeechUnitAudio", "finishSpeechUnitAudio", "beginSpeechUnits", "finishSpeechUnits", "flushSpeechUnitAudio", "clearSpeechUnitAudio",
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

// Bilingual SSE uses spoken sentences, never the Chinese final fallback. One
// long say supplies several requests; final/final_ui do not enqueue them again.
{
  const h = harness();
  const utterances = [{ style: "calm", text: "回来啦。今天好吗？", say: "おかえりなさい。今日はどうですか？" },
    { style: "happy", text: "太好啦。", say: "よかったですね。" }];
  const spoken = utteranceVoiceSegments(utterances);
  const final = { speech: utterances.map(u => u.text).join(""), utterances, speech_protocol: SPEECH_UNITS_PROTOCOL };
  await h.scope.processThinkStream(events([
    { type: "speech_mode", speech_protocol: SPEECH_UNITS_PROTOCOL },
    { type: "speech_segment", text: "回来啦。", index: 0, speech_protocol: SPEECH_UNITS_PROTOCOL },
    ...spoken, { type: "assistant_stage_decision", has_tool_call: false },
    { type: "final_ui", payload: final }, { type: "final", payload: final }, { type: "stream_end" }
  ]), 1);
  assert.equal(h.requests.length, 1);
  await drainAudio(h); drainBubbles(h);
  assert.deepEqual(h.requests.map(r => r.text), spoken.map(e => e.text));
  assert.deepEqual(h.requests.map(r => r.item.voiceStyle), ["calm", "calm", "happy"]);
  assert.deepEqual(h.effects.filter(([kind]) => kind === "bubble").map(([, text]) => text), utterances.map(unit => unit.text));
}
// Short pieces wait briefly, but never merge across references. Cancellation
// drops the timer as well as pending synthesis; old audio cannot resume.
{
  const h = harness();
  h.scope.queueSpeechUnitAudio({ text: "うん。", style: "calm" }, 1);
  assert.equal(h.requests.length, 0);
  h.scope.queueSpeechUnitAudio({ text: "はい。", style: "happy" }, 1);
  assert.equal(h.requests[0].text, "うん。");
  h.scope.stopTts(); h.audio(0); await tick();
  assert.equal(h.plays.length, 0);
  assert.equal(h.timers.size, 0);
  h.scope.activeTurnToken = 2;
  h.scope.queueSpeechUnitAudio({ text: "古い音声です。", style: "calm" }, 1);
  assert.equal(h.requests.length, 1);
}
console.log("Bilingual production queues: style, punctuation, final replay and cancellation passed");

// Expressions are presentation cues. Neither fast model output nor a final
// replay may move the portrait ahead of the audio that owns the next unit.
{
  const h = harness(); h.scope.voiceProvider = "irodori";
  const utterances = [
    { emotion: "normal", style: "calm", text: "先认真解释。", say: "まず説明します。" },
    { emotion: "shy", style: "calm", text: "接着有些害羞。", say: "少し恥ずかしいです。" },
    { style: "happy", text: "仍然有些害羞。", say: "でも嬉しいです。" }
  ];
  const final = { emotion: "happy", utterances, speech: utterances.map(unit => unit.text).join(""), speech_protocol: SPEECH_UNITS_PROTOCOL };
  await h.scope.processThinkStream(events([
    { type: "ui", emotion: "happy" }, { type: "speech_mode", speech_protocol: SPEECH_UNITS_PROTOCOL },
    ...utteranceDeliveryEvents(utterances, final.emotion),
    { type: "assistant_stage_decision" }, { type: "final_ui", payload: final }, { type: "final", payload: final }
  ]), 1);
  assert.deepEqual(h.effects.filter(([kind]) => kind === "emotion"), [["emotion", "normal"]]);
  h.audio(0); await tick(); h.plays[0].resolve(); await tick();
  h.audio(1); await tick();
  assert.deepEqual(h.effects.filter(([kind]) => kind === "emotion"), [["emotion", "normal"]]);
  h.player.end(); await tick(); h.player.ended = false;
  h.plays[1].resolve(); await tick();
  assert.equal(h.effects.filter(([kind]) => kind === "emotion").at(-1)[1], "shy");
  h.audio(2); await tick(); h.player.end(); await tick(); h.player.ended = false;
  h.plays[2].resolve(); await tick(); h.player.end(); await tick();
  assert.equal(h.requests.length, 3, "Expressions do not add synthesis requests");
  assert.equal(h.effects.some(([kind, value]) => kind === "emotion" && value === "happy"), false);
}
// With voice off, the display queue owns expression changes and first-unit
// fallback. Final-only frames and empty utterances follow the same contract.
{
  const h = harness(); h.scope.state.voiceEnabled = false;
  const utterances = [
    { text: "先用顶层表情。", say: null },
    { emotion: "shy", text: "接着有些害羞。", say: null },
    { text: "延续上一组。", say: null }
  ];
  const final = { emotion: "normal", utterances, speech: utterances.map(unit => unit.text).join(""), speech_protocol: SPEECH_UNITS_PROTOCOL };
  await h.scope.processThinkStream(events([{ type: "final", payload: final }, { type: "final", payload: final }]), 1);
  assert.deepEqual(h.effects.filter(([kind]) => kind === "emotion"), [["emotion", "normal"]]);
  drainBubbles(h);
  assert.deepEqual(h.effects.filter(([kind]) => kind === "emotion").map(([, value]) => value), ["normal", "shy", "shy"]);
  assert.equal(h.requests.length, 0);
}
console.log("Speech-unit expressions: root priority, inheritance, actual playback and final replay passed");

// Irodori keeps a complete say together, starts at its completion marker, and
// retains a single lookahead request while the first expression plays.
{
  const h = harness(); h.scope.voiceProvider = "irodori";
  const utterances = [{ style: "温柔关心", text: "累了就休息吧。", say: "少し疲れているみたいですね。無理をしないで、ゆっくり休んでください。" },
    { style: "轻松地接着说", text: "一起喝茶吧。", say: "お茶にしましょう。私も一緒に休みます。" }];
  const spoken = utteranceVoiceSegments(utterances);
  for (const event of spoken.filter(event => event.unit_index === 0)) h.scope.queueSpeechUnitAudio(event, 1);
  assert.equal(h.requests.length, 0, "Wait for the expression rather than every punctuation mark");
  h.scope.finishSpeechUnitAudio({ unit_index: 99 }, 1);
  assert.equal(h.requests.length, 0, "An unrelated completion cannot flush this expression");
  h.scope.finishSpeechUnitAudio({ unit_index: 0 }, 1);
  assert.equal(h.requests[0].text, utterances[0].say);
  h.audio(0); await tick(); h.plays[0].resolve(); await tick();
  for (const event of spoken.filter(event => event.unit_index === 1)) h.scope.queueSpeechUnitAudio(event, 1);
  h.scope.finishSpeechUnitAudio({ unit_index: 1 }, 1);
  assert.equal(h.requests.length, 2, "Prepare the next expression during playback");
  assert.equal(h.requests[1].text, utterances[1].say);
  h.audio(1); await tick(); h.player.end(); await tick();
  h.player.ended = false; h.plays[1].resolve(); await tick(); h.player.end(); await tick();
  assert.equal(h.scope.ttsActive, false);
}
// Final-only and repeated final/SSE events use the same grouping without
// replaying audio, changing the display language or rewriting expression style.
{
  const h = harness(); h.scope.voiceProvider = "provider.tts.irodori.local";
  const utterances = [{ style: "很开心，笑着说", text: "今天很开心。陪你喝茶。", say: "今日はうれしいです。一緒にお茶を飲みましょう。" }];
  const final = { utterances, speech: utterances[0].text, speech_protocol: SPEECH_UNITS_PROTOCOL };
  await h.scope.processThinkStream(events([
    { type: "speech_mode", speech_protocol: SPEECH_UNITS_PROTOCOL },
    ...utteranceVoiceSegments(utterances), { type: "voice_unit_end", unit_index: 0 },
    { type: "assistant_stage_decision" }, { type: "final_ui", payload: final }, { type: "final", payload: final }
  ]), 1);
  await drainAudio(h); drainBubbles(h);
  assert.deepEqual(h.requests.map(request => request.text), [utterances[0].say]);
  assert.equal(h.requests[0].item.voiceStyle, utterances[0].style);
  assert.equal(h.scope.speechUnitPending, null);
  const fallback = harness(); fallback.scope.voiceProvider = "irodori";
  await fallback.scope.processThinkStream(events([{ type: "final", payload: final }, { type: "final", payload: final }]), 1);
  await drainAudio(fallback); drainBubbles(fallback);
  assert.deepEqual(fallback.requests.map(request => request.text), [utterances[0].say]);
}
// Stop and character/turn switches discard buffered words and late markers.
{
  const h = harness(); h.scope.voiceProvider = "irodori";
  h.scope.queueSpeechUnitAudio({ text: "前の会話です。", unit_index: 0, style: "轻松" }, 1);
  h.scope.stopTts(); h.scope.activeTurnToken = 2;
  h.scope.queueSpeechUnitAudio({ text: "新しい会話です。", unit_index: 0, style: "开心" }, 2);
  h.scope.finishSpeechUnitAudio({ unit_index: 0 }, 1);
  assert.equal(h.requests.length, 0);
  assert.equal(h.scope.speechUnitPending.text, "新しい会話です。");
  h.scope.finishSpeechUnitAudio({ unit_index: 0 }, 2);
  assert.equal(h.requests[0].text, "新しい会話です。");
  h.scope.stopTts(); h.audio(0); await tick();
  assert.equal(h.plays.length, 0);
}
for (const text of ["無理しないで。ゆっくり休みましょう。".repeat(20), "「" + "穏やかに話します。".repeat(30) + "」", "あ".repeat(365)]) {
  const h = harness(); h.scope.voiceProvider = "irodori";
  const chunks = h.scope.buildTtsQueueItems([text], { preserveSegments: true });
  assert.ok(chunks.every(chunk => chunk.length <= 120));
  assert.equal(chunks.join(""), text);
}
console.log("Irodori production delivery: complete expressions, single lookahead, replay, bounds and stale cancellation passed");

const pairedUnits = [
  { text: "累了就休息吧。慢慢来。", say: "無理をしないでください。ゆっくり休みましょう。", style: "温柔关切" },
  { text: "我陪你喝杯茶。", say: "一緒にお茶を飲みましょう。", style: "轻松" }
];
const pairedStream = units => [
  { type: "speech_mode", speech_protocol: SPEECH_UNITS_PROTOCOL },
  ...utteranceDeliveryEvents(units),
  { type: "final_ui", payload: { utterances: units, speech: units.map(unit => unit.text).join(""), speech_protocol: SPEECH_UNITS_PROTOCOL } },
  { type: "stream_end" }
];
const readingTick = h => {
  const entry = [...h.timers.entries()].find(([, timer]) => timer.ms === 2200);
  assert.ok(entry, "Text-only fallback must have a reading wait");
  h.timers.delete(entry[0]); entry[1].callback();
};
// Slow synthesis, an early-ready lookahead and delayed play() must never move
// the paired Chinese text ahead of actual Japanese playback.
{
  const h = harness(); h.scope.voiceProvider = "irodori";
  await h.scope.processThinkStream(events(pairedStream(pairedUnits)), 1);
  assert.equal(h.scope.els.bubbleText.textContent, pairedUnits[0].text);
  assert.equal(h.scope.segmentTimer, 0, "Reading timers cannot advance a spoken expression");
  assert.equal(h.requests.length, 1);
  h.audio(0); await tick();
  assert.equal(h.requests.length, 2);
  h.audio(1); await tick();
  assert.equal(h.scope.els.bubbleText.textContent, pairedUnits[0].text);
  h.plays[0].resolve(); await tick();
  assert.deepEqual(Array.from(h.scope.streamedReplySegments), [pairedUnits[1].text]);
  h.player.end(); await tick();
  assert.equal(h.scope.els.bubbleText.textContent, pairedUnits[0].text, "Next play promise has not settled");
  h.player.ended = false; h.plays[1].resolve(); await tick();
  assert.equal(h.scope.els.bubbleText.textContent, pairedUnits[1].text);
  assert.equal(h.scope.streamedReplySegments.length, 0);
  h.player.end(); await tick();
  assert.equal(h.scope.ttsActive, false);
}
// A long say keeps its whole translation across multiple synthesis blocks.
{
  const h = harness(); h.scope.voiceProvider = "irodori";
  const units = [{ ...pairedUnits[0], say: "ゆっくり休んでください。".repeat(15) }, pairedUnits[1]];
  await h.scope.processThinkStream(events(pairedStream(units)), 1);
  const chunks = continuousVoiceChunks(units[0].say);
  assert.ok(chunks.length > 1);
  for (let i = 0; i < chunks.length + 1; i += 1) {
    h.audio(i); await tick(); h.player.ended = false; h.plays[i].resolve(); await tick();
    assert.equal(h.scope.els.bubbleText.textContent, i < chunks.length ? units[0].text : units[1].text);
    h.player.end(); await tick();
  }
  assert.deepEqual(h.requests.map(request => request.text), [...chunks, units[1].say]);
}
// Null/missing/empty say stays in order without a fabricated speech request.
for (const say of [null, undefined, ""]) {
  const h = harness(); h.scope.voiceProvider = "irodori";
  const silent = { text: "（把热茶放到你手边）", say };
  await h.scope.processThinkStream(events(pairedStream([silent, pairedUnits[1]])), 1);
  assert.deepEqual(h.requests.map(request => request.text), [pairedUnits[1].say]);
  h.audio(0); await tick();
  assert.equal(h.plays.length, 0);
  assert.equal(h.scope.els.bubbleText.textContent, silent.text);
  readingTick(h); await tick(); h.plays[0].resolve(); await tick();
  assert.equal(h.scope.els.bubbleText.textContent, pairedUnits[1].text);
  h.player.end(); await tick(); assert.equal(h.scope.ttsActive, false);
}
// Failed speech remains readable before the next audio expression starts.
{
  const h = harness(); h.scope.voiceProvider = "irodori";
  await h.scope.processThinkStream(events(pairedStream(pairedUnits)), 1);
  h.requests[0].reject(new Error("provider failed")); await tick(); h.audio(1); await tick();
  assert.equal(h.scope.els.bubbleText.textContent, pairedUnits[0].text);
  assert.equal(h.plays.length, 0);
  readingTick(h); await tick(); h.plays[0].resolve(); await tick();
  assert.equal(h.scope.els.bubbleText.textContent, pairedUnits[1].text);
  h.player.end(); await tick(); assert.equal(h.scope.ttsActive, false);
}
// Muting consumes only unread translations; re-enabling waits until the next
// turn, so half a text-only reply cannot jump back into the audio queue.
{
  const h = harness(); h.scope.voiceProvider = "irodori";
  await h.scope.processThinkStream(events(pairedStream(pairedUnits)), 1);
  h.audio(0); await tick(); h.plays[0].resolve(); await tick();
  h.scope.setVoiceEnabled(false);
  assert.equal(h.scope.els.bubbleText.textContent, pairedUnits[1].text);
  h.scope.setVoiceEnabled(true);
  h.scope.queueSpeechUnitText("第三段文字。", 1, "1:1:2");
  h.scope.queueSpeechUnitAudio({ unit_index: 2, text: "三番目です。", display_text: "第三段文字。" }, 1);
  h.scope.finishSpeechUnitAudio({ unit_index: 2 }, 1);
  assert.equal(h.requests.length, 2);
  h.audio(1); await tick();
  drainBubbles(h);
  assert.equal(h.scope.els.bubbleText.textContent, "第三段文字。");
  assert.equal(h.plays.length, 1, "Late prefetched audio cannot restart after mute");
}
// Voice-disabled, final-only and repeated equal units remain readable once per
// occurrence; a cancelled prior turn cannot overwrite a new translation.
{
  const h = harness(); h.scope.voiceProvider = "irodori";
  await h.scope.processThinkStream(events([
    { type: "speech_mode", speech_protocol: SPEECH_UNITS_PROTOCOL },
    { type: "speech_unit_text", unit_index: 0, text: pairedUnits[0].text },
    { type: "speech_unit_text", unit_index: 1, text: pairedUnits[1].text },
    { type: "stream_error", message: "connection lost before say" }
  ]), 1);
  assert.equal(h.requests.length, 0);
  assert.equal(h.scope.els.bubbleText.textContent, pairedUnits[0].text);
  readingTick(h); await tick();
  assert.equal(h.scope.els.bubbleText.textContent, pairedUnits[1].text);
  readingTick(h); await tick(); assert.equal(h.scope.ttsActive, false);
  assert.equal(h.scope.streamedReplySegments.length, 0);
}
{
  const h = harness(); h.scope.voiceProvider = "irodori"; h.scope.state.voiceEnabled = false;
  await h.scope.processThinkStream(events(pairedStream([pairedUnits[0], pairedUnits[0]])), 1);
  drainBubbles(h);
  assert.deepEqual(h.effects.filter(([kind]) => kind === "bubble").map(([, text]) => text), [pairedUnits[0].text, pairedUnits[0].text]);
  assert.equal(h.requests.length, 0);
}
{
  const h = harness(); h.scope.voiceProvider = "irodori";
  await h.scope.processThinkStream(events(pairedStream(pairedUnits)), 1);
  h.scope.stopTts(); h.scope.activeTurnToken = 2;
  await h.scope.processThinkStream(events(pairedStream([pairedUnits[1]])), 2);
  h.audio(0); await tick();
  assert.equal(h.scope.els.bubbleText.textContent, pairedUnits[1].text);
  assert.equal(h.plays.length, 0);
  h.audio(1); await tick(); h.plays[0].resolve(); await tick(); h.player.end(); await tick();
  assert.equal(h.scope.streamedReplySegments.length, 0);
}
console.log("Bilingual bubble/audio synchronization: slow/prefetched playback, long say, silence, errors, mute, replay and stale turns passed");
