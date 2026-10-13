import assert from "node:assert/strict";
import test, { after } from "node:test";
import { createLifecycle } from "../src/pet/lifecycle.js";
import { deferred, loadPetRuntime, tick } from "./pet-runtime-harness.mjs";

const runtime = await loadPetRuntime();
const { createVoiceController } = await runtime.load("/src/pet/voice.js");
const voiceClasses = await runtime.load("/src/realtime-voice-client.js");
after(() => runtime.close());

function fixture({ getUserMedia, fetch, realtime = false } = {}) {
  const recorders = [];
  const streams = [];
  const effects = [];
  const requests = [];
  class Recorder extends EventTarget {
    static isTypeSupported(type) { return type === "audio/ogg;codecs=opus"; }
    constructor(stream, options) {
      super();
      this.stream = stream;
      this.options = options;
      this.mimeType = options?.mimeType || "audio/webm";
      this.state = "inactive";
      recorders.push(this);
    }
    start() { this.state = "recording"; }
    stop() {
      if (this.state === "inactive") throw new Error("already stopped");
      this.state = "inactive";
      if (this.autoComplete) queueMicrotask(() => this.complete());
    }
    data(text) {
      const event = new Event("dataavailable");
      event.data = new Blob([text], { type: this.mimeType });
      this.dispatchEvent(event);
    }
    complete() { this.dispatchEvent(new Event("stop")); }
    fail() { this.dispatchEvent(new Event("error")); }
  }
  const stream = () => {
    const track = { stops: 0, stop() { this.stops += 1; } };
    const value = { track, getTracks: () => [track] };
    streams.push(value);
    return value;
  };
  const element = () => ({
    classList: { toggle() {}, contains: () => false },
    hidden: true, value: "", scrollHeight: 40, pause() {}, load() {}, removeAttribute() {}
  });
  const els = {
    voiceRecordButton: element(), chatForm: element(), chatInput: element(),
    bubble: element(), bubbleText: element(), voicePlayer: realtime ? element() : null
  };
  const state = {
    voiceInputEnabled: true, voiceEnabled: realtime, sessionId: "test-session",
    voiceVolume: 0.5, currentEmotion: "normal"
  };
  const lifecycle = createLifecycle(globalThis);
  const platform = {
    window: realtime ? {
      WebSocket() {}, AudioContext() {}, AudioWorkletNode() {}, Blob, URL,
      setTimeout, clearTimeout
    } : globalThis,
    navigator: { mediaDevices: { getUserMedia: getUserMedia || (async () => stream()) } },
    MediaRecorder: Recorder, isTauriRuntime: false,
    backendFetch: async (url, init) => {
      requests.push({ url, init });
      return fetch ? fetch(url, init) : new Response(JSON.stringify({ ok: true, text: "识别结果" }));
    }
  };
  const voice = createVoiceController({
    state, els, platform, lifecycle,
    tts: { cancelTtsPrewarm() {}, stopTts() {}, scheduleTtsPrewarm() {} },
    bubble: {
      streamedReplySegments: [], setPetMotion: value => effects.push(["motion", value]),
      showBubbleText: text => effects.push(["bubble", text]),
      showError: text => effects.push(["error", text]), showThinking() {},
      displayReplyBubbleText: text => effects.push(["reply", text]), scheduleVoiceBubbleReset() {},
      applyPayloadEmotion: value => effects.push(["emotion", value])
    },
    chat: {
      isReplyActive: () => false, isTurnActive: () => false, interruptReply: () => false,
      isAbortLike: error => error?.name === "AbortError", setVoiceSending() {},
      sendMessage: async text => effects.push(["send", text])
    },
    persistence: { scheduleSave() {}, getProfileUserId: () => "test-owner", DEFAULT_STATE: {} },
    status: { setRuntimeStatus: text => effects.push(["status", text]), updateActivityControls() {} },
    settings: { scheduleSettingsSnapshot() {} },
    character: {
      setPetEmotion() {}, setTransientEmotion() {}, resolveEmotionEntry: id => ({ id }),
      getCurrentCharacterPackId: () => "test-pack"
    },
    backend: { buildBackendEndpointUrl: (_, pathname) => "http://127.0.0.1" + pathname },
    music: { setRestingPetEmotion() {}, scheduleMusicEmotionRestore() {} },
    input: { scheduleChatInputAutoHide() {}, setChatInputText: text => effects.push(["input", text]) }
  });
  const stop = (elapsed = 1000) => {
    const now = Date.now;
    Date.now = () => now() + elapsed;
    try { return voice.stopVoiceRecording(); }
    finally { Date.now = now; }
  };
  return {
    voice, state, els, lifecycle, platform, recorders, streams, effects, requests, stream, stop,
    async close() {
      for (const recorder of recorders) {
        recorder.autoComplete = true;
        if (recorder.state === "inactive") recorder.complete();
      }
      await voice.cancelVoiceRecording();
      lifecycle.dispose();
    }
  };
}

function mockRealtimeStart(t) {
  const single = voiceClasses.RealtimeVoiceSession.prototype;
  const callTurn = voiceClasses.RealtimeVoiceCallTurn.prototype;
  const oldSingleStart = single.start;
  const oldCallStart = callTurn.start;
  single.start = callTurn.start = async function () {
    this.callbacks.onReady?.();
    return true;
  };
  t.after(() => { single.start = oldSingleStart; callTurn.start = oldCallStart; });
}

async function commitCallInput(t, turn) {
  const prototype = voiceClasses.RealtimeVoiceCallTurn.prototype;
  const finish = prototype.finishInput;
  prototype.finishInput = async function () {
    this.callbacks.onFinal({ input_state: "finalized", response: { status: "started" } });
  };
  t.after(() => { prototype.finishInput = finish; });
  turn.recorder.autoComplete = true;
  turn.detector.onEndpoint({ action: "commit" });
  await turn.endpointTask;
  // The flow opens overlap listening after the server response has started.
  turn.session.callbacks.onFinal({ input_state: "committed", response: { status: "started" } });
  await new Promise(resolve => setTimeout(resolve, 10));
}

for (const outcome of ["stop", "error", "short"]) {
  test(`late recording ${outcome} cannot clear or submit a successor`, async t => {
    const f = fixture();
    t.after(() => f.close());
    await f.voice.startVoiceRecording();
    const old = f.recorders[0];
    old.data("old".repeat(600));
    const stopping = f.stop(outcome === "short" ? 0 : 1000);
    await f.voice.cancelVoiceRecording();
    await f.voice.startVoiceRecording();
    const before = f.effects.length;
    if (outcome === "error") old.fail();
    else old.complete();
    await stopping;

    assert.equal(f.voice.voiceInputState, "recording");
    assert.equal(f.streams[1].track.stops, 0);
    assert.equal(f.requests.length, 0);
    assert.deepEqual(f.effects.slice(before), []);
  });
}

test("late data from a cancelled recorder cannot enter the next recording", async t => {
  const f = fixture();
  t.after(() => f.close());
  await f.voice.startVoiceRecording();
  const old = f.recorders[0];
  await f.voice.cancelVoiceRecording();
  await f.voice.startVoiceRecording();
  old.data("stale".repeat(500));
  const current = f.recorders[1];
  const text = "new".repeat(600);
  current.data(text);
  const stopping = f.stop();
  current.complete();
  await stopping;
  assert.equal(f.requests.length, 1);
  assert.equal(await f.requests[0].init.body.get("file").text(), text);
  assert.deepEqual(f.effects.filter(([kind]) => kind === "send"), [["send", "识别结果"]]);
});

test("MIME selection uses the injected recorder supported by this platform", async t => {
  const f = fixture();
  t.after(() => f.close());
  await f.voice.startVoiceRecording();
  assert.equal(f.recorders[0].options?.mimeType, "audio/ogg;codecs=opus");
});

test("duplicate opening and cancellation release a late microphone", async t => {
  const pending = deferred();
  const f = fixture({ getUserMedia: () => pending.promise });
  t.after(() => f.close());
  const opening = f.voice.startVoiceRecording();
  await f.voice.startVoiceRecording();
  await tick();
  assert.equal(f.voice.voiceInputState, "opening");
  await f.voice.cancelVoiceRecording();
  const late = f.stream();
  pending.resolve(late);
  await opening;
  await tick();
  assert.equal(late.track.stops, 1);
  assert.equal(f.recorders.length, 0);
  assert.equal(f.voice.voiceInputState, "idle");
});

test("cancelled ASR ignores a late successful response", async t => {
  const pending = deferred();
  const f = fixture({ fetch: () => pending.promise });
  t.after(() => f.close());
  await f.voice.startVoiceRecording();
  f.recorders[0].data("old".repeat(600));
  const stopping = f.stop();
  f.recorders[0].complete();
  await tick();
  assert.equal(f.voice.voiceInputState, "processing");
  await f.voice.cancelVoiceRecording();
  assert.equal(f.requests[0].init.signal.aborted, true);
  await f.voice.startVoiceRecording();
  const before = f.effects.length;
  pending.resolve(new Response(JSON.stringify({ ok: true, text: "旧结果" })));
  await stopping;
  assert.equal(f.voice.voiceInputState, "recording");
  assert.deepEqual(f.effects.slice(before), []);
});

test("microphone permission failure is visible and leaves input idle", async t => {
  const f = fixture({ getUserMedia: async () => { throw Object.assign(new Error("denied"), { name: "NotAllowedError" }); } });
  t.after(() => f.close());
  await f.voice.startVoiceRecording();
  assert.equal(f.voice.voiceInputState, "idle");
  assert.deepEqual(f.effects.filter(([kind]) => kind === "error"), [["error", "没有麦克风权限"]]);
  assert.equal(f.requests.length, 0);
});

test("empty audio is rejected without an ASR request", async t => {
  const f = fixture();
  t.after(() => f.close());
  await f.voice.startVoiceRecording();
  const stopping = f.stop();
  f.recorders[0].complete();
  await stopping;
  assert.equal(f.voice.voiceInputState, "idle");
  assert.equal(f.requests.length, 0);
  assert.deepEqual(f.effects.filter(([kind]) => kind === "error"), [["error", "录音太短啦，我没听清。"]]);
});

test("single-turn speech has a segment buffer before the first audio header", async t => {
  mockRealtimeStart(t);
  const f = fixture({ realtime: true });
  t.after(() => f.close());
  await f.voice.startVoiceRecording();
  const turn = f.voice.realtimeVoiceTurn;
  assert.ok(turn);
  assert.doesNotThrow(() => turn.session.callbacks.onPlaybackEnqueued({ delivery_id: "first", ordinal: 0, text: "你好。" }));
  assert.equal(turn.replyBubbleText, "你好。");
  assert.equal(turn.replySegments.size, 1);
});

test("cancelled call failure cannot submit fallback or stop a successor", async t => {
  mockRealtimeStart(t);
  const f = fixture({ realtime: true });
  t.after(() => f.close());
  await f.voice.toggleVoiceRecording();
  const oldCall = f.voice.realtimeVoiceCall;
  const oldTurn = oldCall.inputTurn;
  oldTurn.detector.speechStarted = true;
  oldTurn.recorder.data("old".repeat(600));
  oldTurn.session.callbacks.onFailure({ terminal: true, committed: false, reason: "provider_failed" });
  const failure = oldTurn.failureTask;
  const stopping = f.voice.cancelVoiceRecording();
  const opening = f.voice.toggleVoiceRecording();
  await tick();
  oldTurn.recorder.complete();
  await tick();
  for (const recorder of f.recorders) {
    recorder.autoComplete = true;
    if (recorder.state === "inactive") recorder.complete();
  }
  await stopping;
  await opening;
  for (const recorder of f.recorders) recorder.autoComplete = true;
  await failure;
  assert.equal(f.requests.length, 0);
  assert.ok(f.voice.realtimeVoiceCall?.active);
  assert.notEqual(f.voice.realtimeVoiceCall, oldCall);
});

test("new call waits for old microphone cleanup and ignores its closing notice", async t => {
  mockRealtimeStart(t);
  const f = fixture({ realtime: true });
  t.after(() => f.close());
  await f.voice.toggleVoiceRecording();
  const oldCall = f.voice.realtimeVoiceCall;
  const oldRecorder = oldCall.inputTurn.recorder;
  const stopping = f.voice.stopRealtimeVoiceCall({ notice: true });
  const opening = f.voice.toggleVoiceRecording();
  await tick();
  const waiting = { state: f.voice.voiceInputState, streams: f.streams.length };
  oldRecorder.complete();
  await stopping;
  await opening;
  assert.deepEqual(waiting, { state: "opening", streams: 1 });
  assert.equal(f.voice.voiceInputState, "recording");
  assert.ok(f.voice.realtimeVoiceCall?.active);
  assert.equal(f.streams.at(-1).track.stops, 0);
  assert.equal(f.effects.some(([kind, text]) => kind === "bubble" && text === "语音通话已结束。"), false);
});

test("transport cleanup failure still closes microphone resources and reports its cause", async t => {
  mockRealtimeStart(t);
  const f = fixture({ realtime: true });
  t.after(() => f.close());
  await f.voice.toggleVoiceRecording();
  const call = f.voice.realtimeVoiceCall;
  call.inputTurn.recorder.autoComplete = true;
  call.transport.close = async () => { throw new Error("close failed"); };
  let failed;
  try { await f.voice.stopRealtimeVoiceCall(); }
  catch (error) { failed = error; }
  assert.equal(call.resources.closed, true);
  assert.equal(f.streams[0].track.stops, 1);
  assert.equal(f.voice.realtimeVoiceCall, null);
  assert.equal(f.voice.voiceInputState, "idle");
  assert.equal(failed, undefined);
  assert.ok(f.effects.some(([kind, text]) => kind === "error" && text.includes("close failed")));
});

for (const trigger of ["passive", "server-close", "transport-failure"]) {
  test(`late ${trigger} error cannot overwrite the next microphone opening`, async t => {
    mockRealtimeStart(t);
    const pending = deferred();
    t.after(() => pending.resolve());
    const f = fixture({ realtime: true });
    t.after(() => f.close());
    await f.voice.toggleVoiceRecording();
    const call = f.voice.realtimeVoiceCall;
    const turn = call.inputTurn;
    turn.recorder.autoComplete = true;
    const close = call.resources.close.bind(call.resources);
    call.resources.close = async reason => { await close(reason); await pending.promise; };
    if (trigger === "passive") {
      turn.session.callbacks.onFailure({ terminal: true, committed: false, message: "old passive error" });
    } else if (trigger === "server-close") {
      call.transport.callbacks.onCallClosed();
    } else {
      call.inputTurn = null;
      call.transport.callbacks.onFailure({ terminal: true, message: "old transport error" });
    }
    await tick();
    assert.ok(call.stopTask);
    const opening = f.voice.toggleVoiceRecording();
    const before = f.effects.length;
    pending.resolve();
    await call.stopTask;
    await turn.failureTask;
    await opening;
    assert.ok(f.voice.realtimeVoiceCall?.active);
    assert.deepEqual(f.effects.slice(before).filter(([kind]) => kind === "error"), []);
  });
}

for (const phase of ["opening", "recording", "processing"]) {
  test(`repeated input enable preserves the active ${phase} operation`, async t => {
    const pending = deferred();
    const f = fixture({
      getUserMedia: phase === "opening" ? () => pending.promise : undefined,
      fetch: phase === "processing" ? () => pending.promise : undefined
    });
    let action;
    t.after(async () => {
      await f.voice.cancelVoiceRecording();
      pending.resolve(phase === "opening" ? f.stream() : new Response(JSON.stringify({ ok: true, text: "late" })));
      await action;
      await f.close();
    });
    action = f.voice.startVoiceRecording();
    if (phase === "opening") await tick();
    else {
      await action;
      if (phase === "processing") {
        f.recorders[0].data("input".repeat(500));
        action = f.stop();
        f.recorders[0].complete();
        await tick();
      }
    }
    assert.equal(f.voice.voiceInputState, phase);
    const before = f.effects.length;
    f.voice.setVoiceInputEnabled(true);
    assert.equal(f.voice.voiceInputState, phase);
    assert.deepEqual(f.effects.slice(before), []);
  });
}

test("old response completion cannot replace a newer playing reply", async t => {
  mockRealtimeStart(t);
  const f = fixture({ realtime: true });
  t.after(() => f.close());
  await f.voice.toggleVoiceRecording();
  const call = f.voice.realtimeVoiceCall;
  const old = call.inputTurn;
  await commitCallInput(t, old);
  const current = call.inputTurn;
  assert.ok(current && current !== old);
  old.session.callbacks.onPlaybackStarted({ delivery_id: "old", ordinal: 0, text: "旧回复" });
  current.session.callbacks.onPlaybackStarted({ delivery_id: "new", ordinal: 0, text: "新回复" });
  const before = f.effects.length;
  old.session.callbacks.onResponseCompleted({ speech: "旧回复全文" });
  assert.equal(old.closed, true);
  assert.equal(current.closed, false);
  assert.equal(call.presentingTurn, current);
  assert.deepEqual(f.effects.slice(before).filter(([kind]) => kind === "reply" || kind === "bubble"), []);
});

test("realtime unit expressions wait for their playback and override overall presentation", async t => {
  mockRealtimeStart(t);
  const f = fixture({ realtime: true });
  t.after(() => f.close());
  await f.voice.toggleVoiceRecording();
  const turn = f.voice.realtimeVoiceCall.inputTurn;
  turn.session.callbacks.onPresentation({ presentation: { emotion: "happy" } });
  turn.session.callbacks.onPlaybackEnqueued({ delivery_id: "first", ordinal: 0, text: "先认真说明。", emotion: "normal" });
  turn.session.callbacks.onPlaybackEnqueued({ delivery_id: "second", ordinal: 1, text: "接着有些害羞。", emotion: "shy" });
  assert.deepEqual(f.effects.filter(([kind]) => kind === "emotion").map(([, value]) => value.emotion), ["normal"]);
  turn.session.callbacks.onPlaybackStarted({ delivery_id: "first", ordinal: 0, text: "先认真说明。", emotion: "normal" });
  turn.session.callbacks.onPlaybackStarted({ delivery_id: "second", ordinal: 1, text: "接着有些害羞。", emotion: "shy" });
  turn.session.callbacks.onPresentation({ presentation: { emotion: "happy" } });
  assert.deepEqual(f.effects.filter(([kind]) => kind === "emotion").map(([, value]) => value.emotion), ["normal", "normal", "shy"]);
});

test("queued reply text waits for playback and duplicate headers keep one segment", async t => {
  mockRealtimeStart(t);
  const f = fixture({ realtime: true });
  t.after(() => f.close());
  await f.voice.toggleVoiceRecording();
  const call = f.voice.realtimeVoiceCall;
  const old = call.inputTurn;
  await commitCallInput(t, old);
  const current = call.inputTurn;
  old.session.callbacks.onPlaybackStarted({ delivery_id: "old", ordinal: 0, text: "旧回复" });
  const header = { delivery_id: "new", ordinal: 0, text: "新回复" };
  const before = f.effects.length;
  current.session.callbacks.onPlaybackEnqueued(header);
  current.session.callbacks.onPlaybackEnqueued(header);
  assert.equal(current.replySegments.size, 1);
  assert.equal(current.hasShownSpeech, false);
  assert.deepEqual(f.effects.slice(before).filter(([kind]) => kind === "reply" || kind === "bubble"), []);
  current.session.callbacks.onPlaybackStarted(header);
  assert.equal(current.replySegments.size, 1);
  assert.equal(current.hasShownSpeech, true);
  assert.equal(f.effects.filter(([kind]) => kind === "reply" || kind === "bubble").at(-1)[1], "新回复");
});

test("old final arriving after the newer reply ends cannot rewind its bubble", async t => {
  mockRealtimeStart(t);
  const f = fixture({ realtime: true });
  t.after(() => f.close());
  await f.voice.toggleVoiceRecording();
  const call = f.voice.realtimeVoiceCall;
  const old = call.inputTurn;
  await commitCallInput(t, old);
  const current = call.inputTurn;
  old.session.callbacks.onPlaybackStarted({ delivery_id: "old", ordinal: 0, text: "旧回复" });
  current.session.callbacks.onPlaybackStarted({ delivery_id: "new", ordinal: 0, text: "新回复" });
  current.session.callbacks.onResponseCompleted({ speech: "新回复全文" });
  assert.equal(call.presentingTurn, null);
  const before = f.effects.length;
  old.session.callbacks.onResponseCompleted({ speech: "旧回复全文" });
  assert.equal(old.closed, true);
  assert.deepEqual(f.effects.slice(before).filter(([kind]) => kind === "reply" || kind === "bubble"), []);
});

test("voice facade preserves its public methods and live readonly getters", async t => {
  const f = fixture();
  t.after(() => f.close());
  const getters = ["voiceInputState", "realtimeVoiceTurn", "realtimeVoiceCall", "asrController"];
  const methods = [
    "setVoiceEnabled", "setVoiceInputEnabled", "setVoiceVolume",
    "toggleVoiceRecording", "startVoiceRecording", "stopVoiceRecording", "cancelVoiceRecording", "canUseRealtimeVoice",
    "getRealtimeVoiceCallTurns", "hasRealtimeVoiceCallPlayback", "hasRealtimeVoiceCallCommittedTurn", "hasRealtimeVoiceCallFallbackPending",
    "cancelRealtimeVoiceCallResponse", "stopRealtimeVoiceCall", "closeRealtimeVoiceTurn", "readBackendErrorMessage",
    "setVoiceInputState", "updateVoiceRecordButton", "cleanupVoiceRecorder"
  ];
  assert.deepEqual(Object.keys(f.voice).sort(), [...getters, ...methods].sort());
  for (const method of methods) assert.equal(typeof f.voice[method], "function");
  for (const getter of getters) {
    assert.equal(typeof Object.getOwnPropertyDescriptor(f.voice, getter).get, "function");
    assert.throws(() => { f.voice[getter] = "replacement"; }, TypeError);
  }
  assert.equal(f.streams.length, 0);
  assert.equal(f.recorders.length, 0);
  assert.equal(f.requests.length, 0);
  await f.voice.startVoiceRecording();
  assert.equal(f.voice.voiceInputState, "recording");
});
