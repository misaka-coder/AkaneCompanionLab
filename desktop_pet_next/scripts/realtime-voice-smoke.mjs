import assert from "node:assert/strict";

import {
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
interruptQueue.interrupt("user_stopped_reply");
assert.deepEqual(interrupted.map((item) => item.type), [
  "client.playback.enqueued",
  "client.playback.started",
  "client.playback.interrupted"
]);
assert.equal(interrupted.at(-1).played_ms, 200);
assert.equal(interrupted.at(-1).reason, "user_stopped_reply");

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
