import assert from "node:assert/strict";
import test from "node:test";

import { RealtimeVoiceCallResources, RealtimeVoiceCallSession, RealtimeVoiceCallTurn, RealtimeVoicePlaybackQueue, RealtimeVoiceSession } from "../src/realtime-voice-client.js";

const tick = () => new Promise(resolve => setTimeout(resolve, 0));
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
};

class AudioElement extends EventTarget {
  constructor() {
    super();
    this.listeners = new Map();
    this.currentTime = 0;
    this.volume = 1;
    this.nextPlay = null;
  }

  addEventListener(name, listener, options) {
    this.listeners.set(name, listener);
    super.addEventListener(name, listener, options);
  }

  play() {
    const result = this.nextPlay || Promise.resolve();
    this.nextPlay = null;
    return result;
  }

  pause() {}
  removeAttribute(name) { if (name === "src") this.src = ""; }
  load() {}
}

function speechHeader(id) {
  return { delivery_id: id, text: "reply", binary_follows: true, byte_length: 4 };
}

function playbackFixture() {
  const audio = new AudioElement();
  const sent = [];
  const revoked = [];
  let sequence = 0;
  const queue = new RealtimeVoicePlaybackQueue({
    audioElement: audio, sendJson: frame => sent.push(frame),
    createObjectUrl: () => "blob:reply-" + ++sequence,
    revokeObjectUrl: url => revoked.push(url),
  });
  return { audio, queue, sent, revoked };
}

for (const event of ["ended", "error"]) {
  test(`late ${event} from stopped audio cannot finish its successor`, async () => {
    const { audio, queue, sent, revoked } = playbackFixture();
    try {
      queue.enqueue(speechHeader("old"), new ArrayBuffer(4));
      await tick();
      const staleEvent = audio.listeners.get(event);
      queue.enqueue(speechHeader("new"), new ArrayBuffer(4));
      queue.applyControl({ control_id: "stop-old", command_id: "stop", action: "stop", delivery_id: "old" });
      await tick();
      staleEvent();

      assert.equal(queue.current?.header.delivery_id, "new");
      assert.equal(sent.some(frame => frame.delivery_id === "new" &&
        ["client.playback.completed", "client.playback.failed"].includes(frame.type)), false);
      audio.dispatchEvent(new Event("ended"));
      assert.equal(sent.filter(frame => frame.type === "client.playback.completed" && frame.delivery_id === "new").length, 1);
      assert.deepEqual(revoked, ["blob:reply-1", "blob:reply-2"]);
    } finally {
      queue.close();
    }
  });
}

test("late play resolution after interrupt cannot acknowledge the old delivery", async () => {
  const { audio, queue, sent } = playbackFixture();
  const playing = deferred();
  audio.nextPlay = playing.promise;
  try {
    queue.enqueue(speechHeader("old"), new ArrayBuffer(4));
    queue.interrupt();
    queue.enqueue(speechHeader("new"), new ArrayBuffer(4));
    await tick();
    playing.resolve();
    await tick();

    assert.equal(queue.current?.header.delivery_id, "new");
    assert.deepEqual(sent.filter(frame => frame.type === "client.playback.started").map(frame => frame.delivery_id), ["new"]);
  } finally {
    playing.resolve();
    queue.close();
  }
});

function captureFixture(pauseAt) {
  const waiting = deferred();
  const entered = deferred();
  const contexts = [];
  const nodes = [];
  let stoppedTracks = 0;
  class AudioNode {
    connect() {}
    disconnect() {}
  }
  class AudioContext {
    constructor() {
      this.sampleRate = 16000;
      this.state = pauseAt === "resume" ? "suspended" : "running";
      this.closeCalls = 0;
      this.destination = new AudioNode();
      this.audioWorklet = { addModule: async () => {
        if (pauseAt === "module") { entered.resolve(); await waiting.promise; }
      } };
      contexts.push(this);
    }
    async resume() {
      if (pauseAt === "resume") { entered.resolve(); await waiting.promise; }
      if (this.state !== "closed") this.state = "running";
    }
    createMediaStreamSource() { return new AudioNode(); }
    createGain() { const node = new AudioNode(); node.gain = { value: 1 }; return node; }
    async close() { this.closeCalls += 1; this.state = "closed"; }
  }
  class AudioWorkletNode extends AudioNode {
    constructor() { super(); this.port = { postMessage() {} }; nodes.push(this); }
  }
  const scope = { AudioContext, AudioWorkletNode, setTimeout, clearTimeout };
  const mediaStream = { getTracks: () => [{ stop: () => { stoppedTracks += 1; } }] };
  return { scope, mediaStream, contexts, nodes, entered, waiting, stoppedTracks: () => stoppedTracks };
}

for (const kind of ["shared", "standalone"]) {
  for (const pauseAt of ["module", "resume"]) {
    test(`${kind} capture closed during ${pauseAt} cannot connect late nodes`, async () => {
      const fixture = captureFixture(pauseAt);
      const capture = kind === "shared"
        ? new RealtimeVoiceCallResources({ mediaStream: fixture.mediaStream, audioElement: new AudioElement() })
        : new RealtimeVoiceSession({ mediaStream: fixture.mediaStream, scope: fixture.scope, workletModuleUrl: "/pcm.js" });
      const start = kind === "shared"
        ? capture.ensureCaptureStarted({ scope: fixture.scope, workletModuleUrl: "/pcm.js" })
        : capture.startCapture();
      const settled = start.then(() => null, error => error);
      try {
        await fixture.entered.promise;
        if (kind === "shared") await capture.close();
        else capture.dispose();
        fixture.waiting.resolve();
        const error = await settled;

        assert.equal(error?.message, kind === "shared" ? "voice_call_resources_closed" : "voice_realtime_session_closed");
        assert.equal(fixture.nodes.length, 0);
        assert.equal(fixture.contexts[0].closeCalls, 1);
        assert.equal(kind === "shared" ? capture.captureContext : capture.audioContext, null);
        if (kind === "shared") assert.equal(fixture.stoppedTracks(), 1);
      } finally {
        fixture.waiting.resolve();
        await settled;
        if (kind === "shared") await capture.close();
        else { capture.dispose(); await capture.stopCapture(); }
      }
    });
  }
}

test("closed resources reject capture without allocating another AudioContext", async () => {
  const fixture = captureFixture();
  const resources = new RealtimeVoiceCallResources({ mediaStream: fixture.mediaStream, audioElement: new AudioElement() });
  await resources.close();
  await assert.rejects(resources.ensureCaptureStarted({ scope: fixture.scope, workletModuleUrl: "/pcm.js" }),
    /voice_call_resources_closed/);
  assert.equal(fixture.contexts.length, 0);
});

test("superseded standalone initialization releases only its own context", async () => {
  const fixture = captureFixture("module");
  const session = new RealtimeVoiceSession({ mediaStream: fixture.mediaStream, scope: fixture.scope, workletModuleUrl: "/pcm.js" });
  const old = session.startCapture().then(() => null, error => error);
  try {
    await fixture.entered.promise;
    const current = session.startCapture().then(() => null, error => error);
    fixture.waiting.resolve();
    assert.ok(await old instanceof Error);
    assert.equal(await current, null);
    assert.equal(session.audioContext, fixture.contexts[1]);
    assert.equal(fixture.contexts[0].closeCalls, 1);
    assert.equal(fixture.contexts[1].closeCalls, 0);
    assert.equal(fixture.nodes.length, 1);
  } finally {
    fixture.waiting.resolve();
    await old;
    session.dispose();
    await session.stopCapture();
  }
});

test("capture events queued before close cannot retain PCM afterwards", async () => {
  const fixture = captureFixture();
  const resources = new RealtimeVoiceCallResources({ mediaStream: fixture.mediaStream, audioElement: new AudioElement() });
  await resources.close();
  resources.handleCaptureMessage({ type: "pcm", frames: 2, buffer: new Float32Array([0.1, 0.2]).buffer });
  assert.equal(resources.idleCaptureFrameCount, 0);
  assert.equal(resources.idleCaptureFrames.length, 0);
});

function sessionFixture(kind) {
  const notifications = [];
  const timers = new Set();
  const sent = [];
  const released = [];
  let acquired = 0;
  let socketCloses = 0;
  const scope = {
    Blob, URL, performance,
    setTimeout(callback) { timers.add(callback); return callback; },
    clearTimeout(callback) { timers.delete(callback); },
  };
  const callbacks = Object.fromEntries(["onReady", "onFinal", "onTranscript", "onFailure", "onCallClosed", "onProtocolEvent"]
    .map(name => [name, payload => notifications.push({ name, payload })]));
  const audio = new AudioElement();
  const resources = {
    ensureCaptureStarted: async () => 16000,
    acquireCapture: async () => { acquired += 1; return 16000; },
    releaseCapture: async owner => { released.push(owner); },
    replayIdleCapture() {},
    createPlaybackQueue: options => new RealtimeVoicePlaybackQueue({ audioElement: audio, ...options }),
  };
  const session = kind === "call"
    ? new RealtimeVoiceCallSession({ scope, callbacks, callResources: resources })
    : new RealtimeVoiceSession({ scope, callbacks, audioElement: audio });
  session.socket = { readyState: 1, send: text => sent.push(JSON.parse(text)), close() { socketCloses += 1; } };
  return { session, resources, notifications, sent, timers, released, acquired: () => acquired, socketCloses: () => socketCloses };
}

for (const kind of ["single", "call"]) {
  test(`closed ${kind} session ignores late server events`, async () => {
    const { session, notifications } = sessionFixture(kind);
    if (kind === "call") await session.close();
    else session.dispose();
    for (const type of ["server.ready", "server.speech", "server.partial", "server.failed", "server.final"]) {
      session.handleServerEvent({ type, text: "late", terminal: true });
    }
    assert.deepEqual(notifications, []);
    assert.equal(session.pendingSpeechHeader, null);
    if (kind === "single") assert.equal(session.playbackQueue, null);
  });

  test(`late ${kind} Blob conversion after close cannot report another failure`, async () => {
    const { session, notifications } = sessionFixture(kind);
    if (kind === "single") session.handleServerEvent({ type: "server.ready" });
    else {
      session.createTurn({ voiceTurnId: "old", audioStreamId: "old-stream" });
      session.handleServerEvent({ type: "server.turn.ready", voice_turn_id: "old" });
    }
    session.handleServerEvent({ type: "server.speech", ...speechHeader("audio"), voice_turn_id: "old" });
    const decoding = deferred();
    const result = session.handleSocketMessage({ arrayBuffer: () => decoding.promise });
    if (kind === "call") await session.close();
    else session.dispose();
    const before = notifications.length;
    decoding.resolve(new ArrayBuffer(4));
    await result;
    await tick();
    assert.equal(notifications.length, before);
  });

  test(`${kind} Blob conversion failure becomes an observable terminal failure`, async () => {
    const { session, notifications } = sessionFixture(kind);
    try {
      await session.handleSocketMessage({ arrayBuffer: async () => { throw new Error("decode failed"); } });
      await tick();
      const failures = notifications.filter(event => event.name === "onFailure");
      assert.equal(failures.length, 1);
      assert.equal(failures[0].payload.terminal, true);
      assert.match(failures[0].payload.reason, /server_frame_decode_failed/);
    } finally {
      if (kind === "call") await session.close();
      else session.dispose();
    }
  });
}

test("server call closure settles readiness, releases turns and clears waiting timers", async () => {
  const fixture = sessionFixture("call");
  const { session, released, timers, sent, notifications } = fixture;
  const turn = session.createTurn({ voiceTurnId: "input", audioStreamId: "stream" });
  turn.captureAttached = true;
  session.currentTurn = turn;
  const queue = turn.createPlaybackQueue();
  let readyError = null;
  session.waitUntilReady().catch(error => { readyError = error; });
  session.handleServerEvent({ type: "server.call.closed", reason: "hangup" });
  await tick();

  assert.equal(session.closed, true);
  assert.equal(turn.closed, true);
  assert.equal(queue.closed, true);
  assert.equal(session.turns.size, 0);
  assert.deepEqual(released, [turn]);
  assert.ok(readyError instanceof Error);
  assert.equal(timers.size, 0);
  assert.equal(sent.length, 0);
  assert.equal(fixture.socketCloses(), 1);
  assert.equal(notifications.filter(event => event.name === "onCallClosed").length, 1);
});

for (const phase of ["initialization", "acquisition"]) {
  test(`turn cancelled during capture ${phase} cannot start its old input on the server`, async () => {
    const fixture = sessionFixture("call");
    const { session, resources, sent } = fixture;
    const waiting = deferred();
    const entered = deferred();
    const method = phase === "initialization" ? "ensureCaptureStarted" : "acquireCapture";
    const original = resources[method];
    resources[method] = async (...args) => { entered.resolve(); await waiting.promise; return original(...args); };
    session.ready = true;
    session.startPromise = Promise.resolve(session);
    const old = session.createTurn({ voiceTurnId: "old", audioStreamId: "old-stream" });
    const settled = old.start().then(() => null, error => error);
    try {
      await entered.promise;
      old.dispose();
      const next = session.createTurn({ voiceTurnId: "next", audioStreamId: "next-stream" });
      session.currentTurn = next;
      waiting.resolve();
      assert.ok(await settled instanceof Error);
      assert.equal(sent.some(frame => frame.type === "client.turn.start" && frame.voice_turn_id === "old"), false);
      assert.equal(session.currentTurn, next);
      if (phase === "initialization") assert.equal(fixture.acquired(), 0);
      else assert.deepEqual(fixture.released, [old]);
    } finally {
      waiting.resolve();
      await settled;
      await session.close();
    }
  });
}

for (const kind of ["single", "call-turn"]) {
  test(`${kind} fallback preserves WAV samples and rejects incomplete buffered audio`, async () => {
    const capture = kind === "single"
      ? new RealtimeVoiceSession({ scope: { Blob } })
      : new RealtimeVoiceCallTurn({ session: { scope: { Blob } } });
    capture.captureSampleRate = 16000;
    capture.fallbackPcmMaxMs = 1000;
    capture.retainFallbackPcmFrame(new Float32Array([1, -1, 0]).buffer, 3);
    const wav = new DataView(await capture.buildFallbackPcmBlob().arrayBuffer());
    assert.equal(wav.getUint32(24, true), 16000);
    assert.deepEqual([44, 46, 48].map(offset => wav.getInt16(offset, true)), [32767, -32768, 0]);
    capture.releaseFallbackPcm();
    capture.retainFallbackPcmFrame(new Float32Array(9000).buffer, 9000);
    capture.retainFallbackPcmFrame(new Float32Array(9000).buffer, 9000);
    assert.equal(capture.fallbackPcmFrameCount, 9000);
    assert.equal(capture.fallbackPcmDroppedFrames, 9000);
    assert.equal(capture.buildFallbackPcmBlob(), null);
    capture.releaseFallbackPcm();
    assert.equal(capture.buildFallbackPcmBlob(), null);
  });
}
