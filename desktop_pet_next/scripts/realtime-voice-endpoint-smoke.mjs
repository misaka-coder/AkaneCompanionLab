import assert from "node:assert/strict";

import { RealtimeVoiceEndpointDetector } from "../src/realtime-voice-endpoint.js";
import { RealtimeVoiceSession } from "../src/realtime-voice-client.js";

const SAMPLE_RATE = 16000;
const FRAME_MS = 20;
const FRAME_SAMPLES = Math.round((SAMPLE_RATE * FRAME_MS) / 1000);

function pcmFrame(amplitude) {
  const samples = new Float32Array(FRAME_SAMPLES);
  samples.fill(amplitude);
  return samples.buffer;
}

function feed(detector, amplitude, durationMs) {
  const count = Math.round(durationMs / FRAME_MS);
  for (let index = 0; index < count; index += 1) {
    detector.acceptPcmFrame(pcmFrame(amplitude), FRAME_SAMPLES, SAMPLE_RATE);
  }
}

const stableEndpoints = [];
const speechStarts = [];
const stableDetector = new RealtimeVoiceEndpointDetector({
  onSpeechStarted: (payload) => speechStarts.push(payload),
  onEndpoint: (payload) => stableEndpoints.push(payload)
});
feed(stableDetector, 0.005, 500);
feed(stableDetector, 0.08, 20);
feed(stableDetector, 0.005, 100);
assert.equal(stableDetector.snapshot().speech_started, false);

feed(stableDetector, 0.06, 400);
stableDetector.observeTranscript({
  kind: "partial",
  unstableTail: "我想问一下"
});
feed(stableDetector, 0, 240);
assert.equal(stableEndpoints.length, 0);
feed(stableDetector, 0.06, 220);
stableDetector.observeTranscript({
  kind: "checkpoint",
  text: "我想问一下今天的安排"
});
feed(stableDetector, 0, 340);
assert.equal(stableEndpoints.length, 0);
feed(stableDetector, 0, 20);
assert.equal(stableDetector.snapshot().endpoint_pending, true);
assert.equal(stableEndpoints.length, 0);
feed(stableDetector, 0.06, 220);
assert.equal(stableDetector.snapshot().endpoint_pending, false);
feed(stableDetector, 0, 620);
assert.equal(stableEndpoints.length, 0);
feed(stableDetector, 0, 20);
assert.equal(speechStarts.length, 1);
assert.equal(stableEndpoints.length, 1);
assert.equal(stableEndpoints[0].action, "commit");
assert.equal(stableEndpoints[0].reason, "stable_checkpoint_silence");
feed(stableDetector, 0, 400);
assert.equal(stableEndpoints.length, 1);

const partialEndpoints = [];
const partialDetector = new RealtimeVoiceEndpointDetector({
  onEndpoint: (payload) => partialEndpoints.push(payload)
});
feed(partialDetector, 0.06, 300);
partialDetector.observeTranscript({
  kind: "partial",
  unstableTail: "还没有稳定"
});
feed(partialDetector, 0, 660);
assert.equal(partialEndpoints.length, 0);
feed(partialDetector, 0, 300);
assert.equal(partialEndpoints.length, 1);
assert.equal(partialEndpoints[0].reason, "partial_transcript_silence");

const shortEndpoints = [];
const shortDetector = new RealtimeVoiceEndpointDetector({
  onEndpoint: (payload) => shortEndpoints.push(payload)
});
feed(shortDetector, 0.06, 100);
shortDetector.observeTranscript({
  kind: "checkpoint",
  text: "嗯"
});
feed(shortDetector, 0, 360);
assert.equal(shortDetector.snapshot().endpoint_pending, true);
assert.equal(shortEndpoints.length, 0);
feed(shortDetector, 0, 280);
assert.equal(shortEndpoints.length, 1);
assert.equal(shortEndpoints[0].action, "commit");

const discardEndpoints = [];
const discardDetector = new RealtimeVoiceEndpointDetector({
  onEndpoint: (payload) => discardEndpoints.push(payload)
});
feed(discardDetector, 0.004, 400);
feed(discardDetector, 0.06, 300);
feed(discardDetector, 0, 1080);
assert.equal(discardEndpoints.length, 0);
feed(discardDetector, 0, 300);
assert.equal(discardEndpoints.length, 1);
assert.equal(discardEndpoints[0].action, "discard");
assert.equal(discardEndpoints[0].reason, "speech_without_transcript");

const noisyDetector = new RealtimeVoiceEndpointDetector();
feed(noisyDetector, 0.03, 5000);
assert.equal(noisyDetector.snapshot().speech_started, false);
assert.equal(noisyDetector.snapshot().endpoint_emitted, false);

const sessionEndpoints = [];
const sessionDetector = new RealtimeVoiceEndpointDetector({
  onEndpoint: (payload) => sessionEndpoints.push(payload)
});
const session = new RealtimeVoiceSession({
  websocketUrl: "wss://example.test/voice/realtime",
  mediaStream: {},
  audioElement: {},
  openPayload: {},
  workletModuleUrl: "voice-worklet.js",
  endpointDetector: sessionDetector
});
session.captureSampleRate = SAMPLE_RATE;
for (let index = 0; index < 15; index += 1) {
  session.acceptPcmFrame(pcmFrame(0.06), FRAME_SAMPLES);
}
session.handleServerEvent({
  type: "server.checkpoint",
  text: "会话接缝已经收到检查点"
});
for (let index = 0; index < 18; index += 1) {
  session.acceptPcmFrame(pcmFrame(0), FRAME_SAMPLES);
}
assert.equal(sessionDetector.snapshot().endpoint_pending, true);
assert.equal(sessionEndpoints.length, 0);
for (let index = 0; index < 14; index += 1) {
  session.acceptPcmFrame(pcmFrame(0), FRAME_SAMPLES);
}
assert.equal(sessionEndpoints.length, 1);
assert.equal(sessionEndpoints[0].reason, "stable_checkpoint_silence");
assert.equal(session.pendingFrames.length, 47);

console.log("realtime voice endpoint smoke: ok");
