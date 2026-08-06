import assert from "node:assert/strict";

import {
  RealtimeVoiceCallResources,
  RealtimeVoiceCallSession,
  RealtimeVoicePlaybackQueue,
  RealtimeVoiceSession,
  buildVoiceWebSocketUrl,
  encodeFloat32PcmAsWav
} from "../src/realtime-voice-client.js";

class FakeBlob {
  constructor(parts, options = {}) {
    this.parts = parts;
    this.type = options.type || "";
    this.size = parts.reduce((total, part) => {
      if (part instanceof ArrayBuffer) return total + part.byteLength;
      if (ArrayBuffer.isView(part)) return total + part.byteLength;
      return total + Number(part?.size || 0);
    }, 0);
  }
}

class FakeAudioElement {
  constructor() {
    this.listeners = new Map();
    this.currentTime = 0;
    this.src = "";
    this.volume = 1;
    this.playCalls = 0;
    this.pauseCalls = 0;
  }

  addEventListener(type, listener, options = {}) {
    const records = this.listeners.get(type) || [];
    records.push({ listener, once: Boolean(options.once) });
    this.listeners.set(type, records);
  }

  removeEventListener(type, listener) {
    const records = this.listeners.get(type) || [];
    this.listeners.set(
      type,
      records.filter((record) => record.listener !== listener)
    );
  }

  async play() {
    this.playCalls += 1;
  }

  pause() {
    this.pauseCalls += 1;
  }

  removeAttribute(name) {
    if (name === "src") this.src = "";
  }

  load() {}

  dispatch(type) {
    const records = [...(this.listeners.get(type) || [])];
    for (const record of records) {
      record.listener();
      if (record.once) this.removeEventListener(type, record.listener);
    }
  }
}

class FakeAudioNode {
  constructor() {
    this.connectCalls = 0;
    this.disconnectCalls = 0;
  }

  connect() {
    this.connectCalls += 1;
  }

  disconnect() {
    this.disconnectCalls += 1;
  }
}

class FakeCapturePort {
  constructor({ acknowledge = true } = {}) {
    this.onmessage = null;
    this.messages = [];
    this.acknowledge = acknowledge;
  }

  postMessage(payload) {
    this.messages.push(payload);
    const type = String(payload?.type || "");
    if (!this.acknowledge) return;
    if (type === "flush") this.emit({ type: "flushed" });
    if (type === "reset") this.emit({ type: "reset" });
    if (type === "stop") this.emit({ type: "stopped" });
  }

  emit(data) {
    this.onmessage?.({ data });
  }

  emitPcm(values) {
    const frame = new Float32Array(values);
    this.emit({
      type: "pcm",
      frames: frame.length,
      buffer: frame.buffer
    });
  }
}

function buildFakeCaptureScope({ acknowledge = true } = {}) {
  const contexts = [];
  const workletNodes = [];

  class FakeAudioContext {
    constructor() {
      this.sampleRate = 16000;
      this.state = "running";
      this.closeCalls = 0;
      this.loadedModules = [];
      this.destination = new FakeAudioNode();
      this.audioWorklet = {
        addModule: async (url) => {
          this.loadedModules.push(url);
        }
      };
      contexts.push(this);
    }

    createMediaStreamSource() {
      return new FakeAudioNode();
    }

    createGain() {
      const node = new FakeAudioNode();
      node.gain = { value: 1 };
      return node;
    }

    async close() {
      this.closeCalls += 1;
      this.state = "closed";
    }
  }

  class FakeAudioWorkletNode extends FakeAudioNode {
    constructor() {
      super();
      this.port = new FakeCapturePort({ acknowledge });
      workletNodes.push(this);
    }
  }

  return {
    scope: {
      AudioContext: FakeAudioContext,
      AudioWorkletNode: FakeAudioWorkletNode,
      setTimeout,
      Blob: FakeBlob,
      URL: {
        createObjectURL: () => "blob:fake-capture",
        revokeObjectURL: () => {}
      },
      performance: { now: () => 5000 }
    },
    contexts,
    workletNodes
  };
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

assert.equal(
  buildVoiceWebSocketUrl("https://example.test/api/bots/personal/voice/realtime"),
  "wss://example.test/api/bots/personal/voice/realtime"
);

const sent = [];
const observed = [];
const revoked = [];
const audio = new FakeAudioElement();
const queue = new RealtimeVoicePlaybackQueue({
  audioElement: audio,
  sendJson: (payload) => sent.push(payload),
  getVolume: () => 0.7,
  callbacks: {
    onPlaybackEnqueued: () => observed.push("enqueued"),
    onPlaybackStarted: () => observed.push("started"),
    onPlaybackCompleted: () => observed.push("completed")
  },
  BlobImpl: FakeBlob,
  createObjectUrl: () => "blob:voice-1",
  revokeObjectUrl: (url) => revoked.push(url),
  now: () => 1000
});

const speechHeader = {
  delivery_id: "delivery-1",
  media_type: "audio/mpeg",
  byte_length: 4,
  binary_follows: true,
  text: "你好。"
};
queue.enqueue(speechHeader, new Uint8Array([1, 2, 3, 4]).buffer);
assert.deepEqual(sent.map((item) => item.type), ["client.playback.enqueued"]);
assert.equal(audio.playCalls, 1);
assert.equal(audio.volume, 0.7);

await tick();
assert.deepEqual(sent.map((item) => item.type), [
  "client.playback.enqueued",
  "client.playback.started"
]);
audio.currentTime = 0.64;
audio.dispatch("ended");
assert.deepEqual(sent.map((item) => item.type), [
  "client.playback.enqueued",
  "client.playback.started",
  "client.playback.completed"
]);
assert.equal(sent.at(-1).played_ms, 640);
assert.deepEqual(observed, ["enqueued", "started", "completed"]);
assert.deepEqual(revoked, ["blob:voice-1"]);

const interrupted = [];
const interruptAudio = new FakeAudioElement();
const interruptQueue = new RealtimeVoicePlaybackQueue({
  audioElement: interruptAudio,
  sendJson: (payload) => interrupted.push(payload),
  BlobImpl: FakeBlob,
  createObjectUrl: () => "blob:voice-2",
  revokeObjectUrl: () => {},
  now: () => 1200
});
interruptQueue.enqueue(
  { ...speechHeader, delivery_id: "delivery-2" },
  new Uint8Array([5, 6, 7, 8]).buffer
);
await tick();
interruptAudio.currentTime = 0.2;
interruptQueue.interrupt("user_started_voice_input");
assert.deepEqual(interrupted.map((item) => item.type), [
  "client.playback.enqueued",
  "client.playback.started",
  "client.playback.interrupted"
]);
assert.equal(interrupted.at(-1).played_ms, 200);
assert.equal(interrupted.at(-1).reason, "user_started_voice_input");

const controlled = [];
const controlEvents = [];
const controlAudio = new FakeAudioElement();
const controlQueue = new RealtimeVoicePlaybackQueue({
  audioElement: controlAudio,
  sendJson: (payload) => controlled.push(payload),
  getVolume: () => 0.8,
  callbacks: {
    onPlaybackDucked: () => controlEvents.push("ducked"),
    onPlaybackResumed: () => controlEvents.push("resumed"),
    onPlaybackInterrupted: () => controlEvents.push("stopped")
  },
  BlobImpl: FakeBlob,
  createObjectUrl: () => "blob:voice-control",
  revokeObjectUrl: () => {},
  now: () => 2000
});
controlQueue.enqueue(
  { ...speechHeader, delivery_id: "delivery-control" },
  new Uint8Array([9, 10, 11, 12]).buffer
);
await tick();
controlAudio.currentTime = 0.25;
const duckControl = {
  control_id: "control-duck",
  command_id: "command-duck",
  action: "duck",
  delivery_id: "delivery-control"
};
assert.equal(controlQueue.applyControl(duckControl), true);
assert.equal(controlAudio.volume, 0.8 * 0.24);
assert.equal(controlled.at(-1).type, "client.playback.control_ack");
assert.equal(controlled.at(-1).status, "applied");
assert.equal(controlled.at(-1).played_ms, 250);

assert.equal(
  controlQueue.applyControl({
    control_id: "control-resume",
    command_id: "command-resume",
    action: "resume",
    delivery_id: "delivery-control"
  }),
  true
);
assert.equal(controlAudio.volume, 0.8);
assert.equal(controlled.at(-1).status, "applied");

const stopControl = {
  control_id: "control-stop",
  command_id: "command-stop",
  action: "stop",
  delivery_id: "delivery-control",
  reason: "takeover"
};
assert.equal(controlQueue.applyControl(stopControl), true);
assert.equal(controlled.at(-1).status, "applied");
assert.equal(controlAudio.pauseCalls, 1);
assert.deepEqual(controlEvents, ["ducked", "resumed", "stopped"]);
const receiptCount = controlled.length;
assert.equal(controlQueue.applyControl(stopControl), true);
assert.equal(controlled.length, receiptCount + 1);
assert.equal(controlled.at(-1).control_id, "control-stop");

const sharedAudio = new FakeAudioElement();
const sharedTrack = {
  stopCalls: 0,
  stop() {
    this.stopCalls += 1;
  }
};
const callResources = new RealtimeVoiceCallResources({
  mediaStream: { getTracks: () => [sharedTrack] },
  audioElement: sharedAudio
});
const firstTurnEvents = [];
const secondTurnEvents = [];
const firstTurnQueue = callResources.createPlaybackQueue({
  sendJson: (payload) => firstTurnEvents.push(payload),
  BlobImpl: FakeBlob,
  createObjectUrl: () => "blob:call-turn-1",
  revokeObjectUrl: () => {},
  now: () => 3000
});
const secondTurnQueue = callResources.createPlaybackQueue({
  sendJson: (payload) => secondTurnEvents.push(payload),
  BlobImpl: FakeBlob,
  createObjectUrl: () => "blob:call-turn-2",
  revokeObjectUrl: () => {},
  now: () => 3000
});
firstTurnQueue.enqueue(
  { ...speechHeader, delivery_id: "delivery-call-turn-1" },
  new Uint8Array([1, 2, 3, 4]).buffer
);
secondTurnQueue.enqueue(
  { ...speechHeader, delivery_id: "delivery-call-turn-2" },
  new Uint8Array([5, 6, 7, 8]).buffer
);
await tick();
assert.equal(sharedAudio.playCalls, 1);
assert.equal(sharedAudio.src, "blob:call-turn-1");
assert.deepEqual(
  secondTurnEvents.map((item) => item.type),
  ["client.playback.enqueued"]
);
sharedAudio.dispatch("ended");
await tick();
assert.equal(sharedAudio.playCalls, 2);
assert.equal(sharedAudio.src, "blob:call-turn-2");
assert.deepEqual(
  secondTurnEvents.map((item) => item.type),
  ["client.playback.enqueued", "client.playback.started"]
);
assert.equal(sharedTrack.stopCalls, 0);
firstTurnQueue.close("turn_complete");
assert.equal(sharedTrack.stopCalls, 0);
sharedAudio.dispatch("ended");
callResources.close();
assert.equal(sharedTrack.stopCalls, 1);
assert.equal(sharedAudio.src, "");

const borrowedTrack = {
  stopCalls: 0,
  stop() {
    this.stopCalls += 1;
  }
};
const borrowedResources = new RealtimeVoiceCallResources({
  mediaStream: { getTracks: () => [borrowedTrack] },
  audioElement: new FakeAudioElement()
});
const borrowedSession = new RealtimeVoiceSession({
  websocketUrl: "wss://example.test/voice/realtime",
  callResources: borrowedResources,
  openPayload: {},
  workletModuleUrl: "voice-worklet.js",
  scope: {
    Blob: FakeBlob,
    URL: {
      createObjectURL: () => "blob:borrowed-session",
      revokeObjectURL: () => {}
    },
    performance: { now: () => 4000 }
  }
});
borrowedSession.sendJson = () => true;
borrowedSession.handleServerEvent({ type: "server.ready" });
assert.equal(borrowedResources.playbackQueues.size, 1);
assert.equal(borrowedSession.playbackQueue.audioElement, borrowedResources.audioElement);
borrowedSession.dispose("response_terminal");
await tick();
assert.equal(borrowedResources.playbackQueues.size, 0);
assert.equal(borrowedTrack.stopCalls, 0);
borrowedResources.close();
assert.equal(borrowedTrack.stopCalls, 1);

const captureTrack = {
  stopCalls: 0,
  stop() {
    this.stopCalls += 1;
  }
};
const captureResources = new RealtimeVoiceCallResources({
  mediaStream: { getTracks: () => [captureTrack] },
  audioElement: new FakeAudioElement()
});
const captureFakes = buildFakeCaptureScope();
const firstCaptureSession = new RealtimeVoiceSession({
  websocketUrl: "wss://example.test/voice/realtime",
  callResources: captureResources,
  openPayload: {},
  workletModuleUrl: "voice-worklet.js",
  scope: captureFakes.scope
});
await firstCaptureSession.startCapture();
assert.equal(captureFakes.contexts.length, 1);
assert.equal(captureFakes.workletNodes.length, 1);
const sharedCapturePort = captureFakes.workletNodes[0].port;
sharedCapturePort.emitPcm([0.1, 0.2]);
assert.equal(firstCaptureSession.pendingFrames.length, 1);
await firstCaptureSession.flushAndStopCapture();
assert.equal(captureFakes.contexts[0].closeCalls, 0);
assert.equal(captureTrack.stopCalls, 0);

const idleCaptureFrames = [];
captureResources.setIdleCaptureObserver({
  onPcm: (_buffer, frames) => {
    idleCaptureFrames.push(frames);
    captureResources.holdIdleCapture();
  }
});
sharedCapturePort.emitPcm([0.3, 0.4]);
assert.deepEqual(idleCaptureFrames, [2]);
assert.equal(captureResources.idleCaptureHeld, true);
const secondCaptureSession = new RealtimeVoiceSession({
  websocketUrl: "wss://example.test/voice/realtime",
  callResources: captureResources,
  openPayload: {},
  workletModuleUrl: "voice-worklet.js",
  scope: captureFakes.scope
});
await secondCaptureSession.startCapture();
assert.equal(captureFakes.contexts.length, 1);
assert.equal(captureResources.idleCaptureHeld, false);
sharedCapturePort.emitPcm([0.5, 0.6, 0.7]);
assert.equal(firstCaptureSession.pendingFrames.length, 1);
assert.equal(secondCaptureSession.pendingFrames.length, 2);
assert.equal(secondCaptureSession.pendingFrames[0].frameCount, 2);
assert.equal(secondCaptureSession.pendingFrames[1].frameCount, 3);
const fallbackPcm = secondCaptureSession.buildFallbackPcmBlob();
assert.equal(fallbackPcm.type, "audio/wav");
assert.equal(fallbackPcm.size, 44 + 5 * 2);
secondCaptureSession.releaseFallbackPcm();
assert.equal(secondCaptureSession.buildFallbackPcmBlob(), null);

const clippedWav = encodeFloat32PcmAsWav(
  [{ buffer: new Float32Array([-2, 0, 2]).buffer, frameCount: 3 }],
  16000,
  FakeBlob
);
assert.equal(clippedWav.type, "audio/wav");
assert.equal(clippedWav.size, 50);
const clippedWavView = new DataView(clippedWav.parts[0]);
assert.equal(clippedWavView.getUint32(24, true), 16000);
assert.equal(clippedWavView.getInt16(44, true), -32768);
assert.equal(clippedWavView.getInt16(46, true), 0);
assert.equal(clippedWavView.getInt16(48, true), 32767);

const blockedCaptureSession = new RealtimeVoiceSession({
  websocketUrl: "wss://example.test/voice/realtime",
  callResources: captureResources,
  openPayload: {},
  workletModuleUrl: "voice-worklet.js",
  scope: captureFakes.scope
});
await assert.rejects(blockedCaptureSession.startCapture(), /voice_call_capture_busy/);
const secondReleaseTask = secondCaptureSession.stopCapture();
const immediateNextSession = new RealtimeVoiceSession({
  websocketUrl: "wss://example.test/voice/realtime",
  callResources: captureResources,
  openPayload: {},
  workletModuleUrl: "voice-worklet.js",
  scope: captureFakes.scope
});
await immediateNextSession.startCapture();
await secondReleaseTask;
assert.equal(captureFakes.contexts.length, 1);
await immediateNextSession.stopCapture();
assert.equal(captureFakes.contexts[0].closeCalls, 0);
await captureResources.close();
assert.equal(captureFakes.contexts[0].closeCalls, 1);
assert.equal(captureTrack.stopCalls, 1);
assert.deepEqual(
  sharedCapturePort.messages.map((item) => item.type),
  ["reset", "flush", "reset", "reset", "reset", "reset", "stop"]
);

const stalledCaptureTrack = {
  stopCalls: 0,
  stop() {
    this.stopCalls += 1;
  }
};
const stalledCaptureResources = new RealtimeVoiceCallResources({
  mediaStream: { getTracks: () => [stalledCaptureTrack] },
  audioElement: new FakeAudioElement(),
  captureReceiptTimeoutMs: 5
});
const stalledCaptureFakes = buildFakeCaptureScope({ acknowledge: false });
const stalledCaptureSession = new RealtimeVoiceSession({
  websocketUrl: "wss://example.test/voice/realtime",
  callResources: stalledCaptureResources,
  openPayload: {},
  workletModuleUrl: "voice-worklet.js",
  scope: stalledCaptureFakes.scope
});
await assert.rejects(
  stalledCaptureSession.startCapture(),
  /voice_call_capture_reset_timeout/
);
await stalledCaptureResources.close();
assert.equal(stalledCaptureTrack.stopCalls, 1);

const endpointFailureSession = new RealtimeVoiceSession({
  websocketUrl: "wss://example.test/voice/realtime",
  mediaStream: null,
  audioElement: new FakeAudioElement(),
  openPayload: {},
  workletModuleUrl: "voice-worklet.js"
});
endpointFailureSession.flushAndStopCapture = async () => {};
endpointFailureSession.start = async () => endpointFailureSession;
endpointFailureSession.ready = true;
endpointFailureSession.sendJson = () => false;
await assert.rejects(
  endpointFailureSession.finishInput(),
  /voice_realtime_endpoint_send_failed/
);

const interruptionMessages = [];
const interruptionEvents = [];
const interruptionSession = new RealtimeVoiceSession({
  websocketUrl: "wss://example.test/voice/realtime",
  mediaStream: null,
  audioElement: new FakeAudioElement(),
  openPayload: {},
  workletModuleUrl: "voice-worklet.js",
  callbacks: {
    onInterruptionAccepted: () => interruptionEvents.push("accepted"),
    onInterruptionSkipped: () => interruptionEvents.push("skipped")
  },
  scope: {
    Blob: FakeBlob,
    URL: {
      createObjectURL: () => "blob:interruption-session",
      revokeObjectURL: () => {}
    },
    performance: { now: () => 5000 }
  }
});
interruptionSession.captureSampleRate = 16000;
interruptionSession.audioFramesSent = 3200;
interruptionSession.sendJson = (payload) => {
  interruptionMessages.push(payload);
  return true;
};
assert.equal(interruptionSession.reportInterruptionSuspected(), true);
assert.deepEqual(interruptionMessages, []);
interruptionSession.handleServerEvent({ type: "server.ready" });
assert.deepEqual(interruptionMessages, [
  {
    type: "client.interruption.suspected",
    audio_clock_ms: 200
  }
]);
assert.equal(interruptionSession.reportInterruptionSuspected(), false);
interruptionSession.handleServerEvent({ type: "server.interruption.accepted" });
interruptionSession.handleServerEvent({ type: "server.interruption.skipped" });
assert.deepEqual(interruptionEvents, ["accepted", "skipped"]);

class FakeWebSocket {
  static latest = null;

  constructor() {
    this.listeners = new Map();
    this.readyState = 0;
    this.sent = [];
    FakeWebSocket.latest = this;
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  send(payload) {
    this.sent.push(payload);
  }

  close() {
    this.readyState = 3;
  }

  emit(type, payload = {}) {
    if (type === "open") this.readyState = 1;
    if (type === "close") this.readyState = 3;
    for (const listener of this.listeners.get(type) || []) listener(payload);
  }
}

const transportFailures = [];
const transportFailureSession = new RealtimeVoiceSession({
  websocketUrl: "wss://example.test/voice/realtime",
  mediaStream: null,
  audioElement: new FakeAudioElement(),
  openPayload: {},
  workletModuleUrl: "voice-worklet.js",
  callbacks: {
    onFailure: (failure) => transportFailures.push(failure)
  },
  scope: {
    WebSocket: FakeWebSocket,
    setTimeout,
    clearTimeout,
    Blob: FakeBlob,
    URL: {
      createObjectURL: () => "blob:transport-failure",
      revokeObjectURL: () => {}
    },
    performance: { now: () => 6000 }
  }
});
const transportOpenTask = transportFailureSession.openSocket();
FakeWebSocket.latest.emit("error");
await assert.rejects(transportOpenTask, /voice_realtime_websocket_failed/);
assert.equal(transportFailures.length, 1);
assert.equal(transportFailures[0].terminal, true);
assert.equal(transportFailures[0].retryable, true);
assert.equal(transportFailures[0].committed, false);

const slowReadyEvents = [];
const slowReadyFailures = [];
const slowReadySession = new RealtimeVoiceSession({
  websocketUrl: "wss://example.test/voice/realtime",
  mediaStream: null,
  audioElement: new FakeAudioElement(),
  openPayload: {},
  workletModuleUrl: "voice-worklet.js",
  callbacks: {
    onTransportOpen: () => slowReadyEvents.push("transport"),
    onReady: () => slowReadyEvents.push("provider"),
    onFailure: (failure) => slowReadyFailures.push(failure)
  },
  scope: {
    WebSocket: FakeWebSocket,
    setTimeout,
    clearTimeout,
    Blob: FakeBlob,
    URL: {
      createObjectURL: () => "blob:slow-ready",
      revokeObjectURL: () => {}
    },
    performance: { now: () => 6500 }
  }
});
slowReadySession.startCapture = async () => {
  slowReadySession.captureSampleRate = 16000;
};
slowReadySession.flushAndStopCapture = async () => {};
const slowReadyStartTask = slowReadySession.start();
await tick();
const slowReadySocket = FakeWebSocket.latest;
slowReadySocket.emit("open");
await slowReadyStartTask;
assert.deepEqual(slowReadyEvents, ["transport"]);
assert.equal(slowReadySession.ready, false);
slowReadySession.acceptPcmFrame(new Float32Array([0.1, 0.2]).buffer, 2);
assert.equal(slowReadySession.pendingFrames.length, 1);
const slowReadyFinishTask = slowReadySession.finishInput();
await tick();
const beforeProviderReady = slowReadySocket.sent
  .filter((payload) => typeof payload === "string")
  .map((payload) => JSON.parse(payload).type);
assert.deepEqual(beforeProviderReady, ["client.open"]);
slowReadySession.handleServerEvent({ type: "server.ready" });
await slowReadyFinishTask;
assert.deepEqual(slowReadyEvents, ["transport", "provider"]);
assert.deepEqual(slowReadyFailures, []);
const afterProviderReady = slowReadySocket.sent
  .filter((payload) => typeof payload === "string")
  .map((payload) => JSON.parse(payload).type);
assert.deepEqual(afterProviderReady, ["client.open", "client.audio", "client.endpoint"]);
slowReadySession.dispose("test_complete");

const providerReadyTimers = new Map();
let nextProviderReadyTimer = 1;
const providerReadyTimeoutFailures = [];
const providerReadyTimeoutSession = new RealtimeVoiceSession({
  websocketUrl: "wss://example.test/voice/realtime",
  mediaStream: null,
  audioElement: new FakeAudioElement(),
  openPayload: {},
  workletModuleUrl: "voice-worklet.js",
  readyTimeoutMs: 25,
  callbacks: {
    onFailure: (failure) => providerReadyTimeoutFailures.push(failure)
  },
  scope: {
    WebSocket: FakeWebSocket,
    setTimeout: (callback) => {
      const timer = nextProviderReadyTimer;
      nextProviderReadyTimer += 1;
      providerReadyTimers.set(timer, callback);
      return timer;
    },
    clearTimeout: (timer) => providerReadyTimers.delete(timer),
    Blob: FakeBlob,
    URL: {
      createObjectURL: () => "blob:provider-ready-timeout",
      revokeObjectURL: () => {}
    },
    performance: { now: () => 6750 }
  }
});
providerReadyTimeoutSession.startCapture = async () => {
  providerReadyTimeoutSession.captureSampleRate = 16000;
};
providerReadyTimeoutSession.flushAndStopCapture = async () => {};
const providerReadyTimeoutStartTask = providerReadyTimeoutSession.start();
await tick();
FakeWebSocket.latest.emit("open");
await providerReadyTimeoutStartTask;
const providerReadyTimeoutFinishTask = providerReadyTimeoutSession.finishInput();
await tick();
assert.equal(providerReadyTimers.size, 1);
for (const callback of [...providerReadyTimers.values()]) callback();
await assert.rejects(providerReadyTimeoutFinishTask, /voice_realtime_not_ready/);
assert.equal(providerReadyTimeoutFailures.length, 1);
assert.equal(
  providerReadyTimeoutFailures[0].reason,
  "voice_realtime_provider_ready_timeout"
);
assert.equal(providerReadyTimeoutFailures[0].terminal, true);
assert.equal(providerReadyTimeoutFailures[0].retryable, true);

const cancelMessages = [];
const cancelTimers = new Map();
let nextCancelTimer = 1;
const cancelEvents = [];
const cancelSession = new RealtimeVoiceSession({
  websocketUrl: "wss://example.test/voice/realtime",
  mediaStream: null,
  audioElement: new FakeAudioElement(),
  openPayload: {},
  workletModuleUrl: "voice-worklet.js",
  callbacks: {
    onCancelled: () => cancelEvents.push("cancelled")
  },
  scope: {
    setTimeout: (callback) => {
      const timer = nextCancelTimer;
      nextCancelTimer += 1;
      cancelTimers.set(timer, callback);
      return timer;
    },
    clearTimeout: (timer) => cancelTimers.delete(timer),
    Blob: FakeBlob,
    URL: {
      createObjectURL: () => "blob:cancel-session",
      revokeObjectURL: () => {}
    },
    performance: { now: () => 7000 }
  }
});
cancelSession.stopCapture = async () => {};
cancelSession.sendJson = (payload) => {
  cancelMessages.push(payload);
  return true;
};
const cancelTask = cancelSession.cancel("new_voice_turn_committed");
await tick();
assert.deepEqual(cancelMessages, [
  {
    type: "client.cancel",
    reason: "new_voice_turn_committed"
  }
]);
assert.equal(cancelTimers.size, 1);
cancelSession.handleServerEvent({
  type: "server.cancelled",
  reason: "new_voice_turn_committed"
});
assert.equal(await cancelTask, true);
assert.equal(cancelTimers.size, 0);
assert.deepEqual(cancelEvents, ["cancelled"]);
assert.equal(cancelSession.closed, true);

const callScopedTrack = {
  stopCalls: 0,
  stop() {
    this.stopCalls += 1;
  }
};
const callScopedResources = new RealtimeVoiceCallResources({
  mediaStream: { getTracks: () => [callScopedTrack] },
  audioElement: new FakeAudioElement()
});
const callScopedFakes = buildFakeCaptureScope();
const callScopedEvents = [];
const callScopedSession = new RealtimeVoiceCallSession({
  websocketUrl: "wss://example.test/voice/realtime",
  callResources: callScopedResources,
  openPayload: {
    profile_user_id: "master",
    conversation_id: "call-conversation",
    session_id: "call-session",
    character_pack_id: "reimu",
    language: "zh",
    disposition: "message"
  },
  workletModuleUrl: "voice-worklet.js",
  callbacks: {
    onTransportOpen: () => callScopedEvents.push("transport"),
    onReady: () => callScopedEvents.push("call-ready")
  },
  scope: {
    ...callScopedFakes.scope,
    WebSocket: FakeWebSocket,
    clearTimeout
  }
});
const firstCallTurnEvents = [];
const firstCallTurn = callScopedSession.createTurn({
  voiceTurnId: "call-turn-1",
  audioStreamId: "call-audio-1",
  endpointDetector: { acceptPcmFrame() {}, observeTranscript() {} },
  callbacks: {
    onReady: () => firstCallTurnEvents.push("ready"),
    onFinal: () => firstCallTurnEvents.push("final")
  }
});
const firstCallTurnStart = firstCallTurn.start();
await tick();
const callSocket = FakeWebSocket.latest;
callSocket.emit("open");
await tick();
assert.equal(
  JSON.parse(callSocket.sent.find((payload) => typeof payload === "string")).type,
  "client.call.open"
);
callScopedFakes.workletNodes[0].port.emitPcm([0.1, 0.2, 0.3]);
assert.equal(firstCallTurn.pendingFrames.length, 1);
callSocket.emit("message", {
  data: JSON.stringify({ type: "server.call.ready", protocol_version: 2 })
});
await tick();
assert.equal(
  callSocket.sent
    .filter((payload) => typeof payload === "string")
    .map((payload) => JSON.parse(payload).type)
    .includes("client.turn.start"),
  true
);
callSocket.emit("message", {
  data: JSON.stringify({
    type: "server.turn.ready",
    protocol_version: 2,
    voice_turn_id: "call-turn-1"
  })
});
await firstCallTurnStart;
assert.deepEqual(callScopedEvents, ["transport", "call-ready"]);
assert.deepEqual(firstCallTurnEvents, ["ready"]);
assert.equal(firstCallTurn.pendingFrames.length, 0);
assert.equal(
  callSocket.sent
    .filter((payload) => typeof payload === "string")
    .map((payload) => JSON.parse(payload).type)
    .includes("client.audio"),
  true
);
await firstCallTurn.flushAndStopCapture();
await firstCallTurn.finishInput();
assert.equal(
  callSocket.sent
    .filter((payload) => typeof payload === "string")
    .map((payload) => JSON.parse(payload).type)
    .includes("client.turn.endpoint"),
  true
);
callSocket.emit("message", {
  data: JSON.stringify({
    type: "server.turn.final",
    protocol_version: 2,
    voice_turn_id: "call-turn-1",
    text: "第一轮"
  })
});
assert.deepEqual(firstCallTurnEvents, ["ready", "final"]);

const secondCallTurn = callScopedSession.createTurn({
  voiceTurnId: "call-turn-startup-failure",
  audioStreamId: "call-audio-startup-failure",
  endpointDetector: { acceptPcmFrame() {}, observeTranscript() {} }
});
const failedCallTurnStart = secondCallTurn.start();
await tick();
callSocket.emit("message", {
  data: JSON.stringify({
    type: "server.failed",
    protocol_version: 2,
    reason: "turn_start_rejected",
    terminal: false,
    retryable: false,
    message: "这一轮没有接通。"
  })
});
await assert.rejects(failedCallTurnStart, /voice_call_turn_not_ready/);

const thirdCallTurn = callScopedSession.createTurn({
  voiceTurnId: "call-turn-2",
  audioStreamId: "call-audio-2",
  endpointDetector: { acceptPcmFrame() {}, observeTranscript() {} }
});
const secondCallTurnStart = thirdCallTurn.start();
await tick();
assert.equal(FakeWebSocket.latest, callSocket);
callSocket.emit("message", {
  data: JSON.stringify({
    type: "server.turn.ready",
    protocol_version: 2,
    voice_turn_id: "call-turn-2"
  })
});
await secondCallTurnStart;
assert.equal(
  callSocket.sent
    .filter((payload) => typeof payload === "string")
    .map((payload) => JSON.parse(payload).type)
    .filter((type) => type === "client.call.open").length,
  1
);
await callScopedSession.close("test_complete");
await callScopedResources.close("test_complete");
assert.equal(callScopedTrack.stopCalls, 1);

console.log("realtime voice playback smoke: ok");
