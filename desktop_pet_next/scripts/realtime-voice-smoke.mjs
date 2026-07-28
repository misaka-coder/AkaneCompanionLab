import assert from "node:assert/strict";

import {
  RealtimeVoiceCallResources,
  RealtimeVoicePlaybackQueue,
  RealtimeVoiceSession,
  buildVoiceWebSocketUrl
} from "../src/realtime-voice-client.js";

class FakeBlob {
  constructor(parts, options = {}) {
    this.parts = parts;
    this.type = options.type || "";
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

console.log("realtime voice playback smoke: ok");
