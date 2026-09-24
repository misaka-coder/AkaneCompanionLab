import assert from "node:assert/strict";

import {
  RealtimeVoiceCallFlow,
  RealtimeVoiceReconnectBackoff,
  hasRealtimeVoiceInputEvidence,
  shouldReconnectPassiveVoiceFailure
} from "../src/realtime-voice-call-flow.js";

const scheduled = [];
let listenRequests = 0;
const flow = new RealtimeVoiceCallFlow({
  schedule: (callback) => scheduled.push(callback),
  onListenRequested: () => {
    listenRequests += 1;
  }
});

assert.equal(flow.start(), true);
assert.equal(flow.start(), false);

const firstTurn = flow.beginListening();
assert.equal(firstTurn.revision, 1);
assert.equal(firstTurn.phase, "listening");
assert.equal(flow.beginListening(), null);
assert.equal(flow.acceptEndpoint(firstTurn, "commit"), true);
assert.equal(flow.acceptEndpoint(firstTurn, "commit"), false);
assert.equal(firstTurn.phase, "finalizing");
assert.equal(flow.markResponding(firstTurn), true);
assert.equal(firstTurn.phase, "responding");
assert.equal(listenRequests, 0);

assert.equal(flow.allowOverlapListening(firstTurn), true);
assert.equal(scheduled.length, 1);
assert.equal(flow.allowOverlapListening(firstTurn), false);
scheduled.shift()();
assert.equal(listenRequests, 1);

const overlapTurn = flow.beginListening();
assert.equal(overlapTurn.revision, 2);
assert.equal(flow.complete(firstTurn), true);
assert.equal(scheduled.length, 0);
assert.equal(flow.complete(firstTurn), false);

assert.equal(flow.acceptEndpoint(overlapTurn, "discard"), true);
assert.equal(overlapTurn.phase, "discarding");
assert.equal(flow.resumeListening(overlapTurn), true);
assert.equal(overlapTurn.phase, "listening");
assert.equal(overlapTurn.endpointAccepted, false);
assert.equal(flow.acceptEndpoint(overlapTurn, "discard"), true);
assert.equal(flow.complete(overlapTurn, { requestNext: false }), true);
assert.equal(scheduled.length, 0);
assert.equal(flow.requestNextListeningTurn(), true);
scheduled.shift()();
assert.equal(listenRequests, 2);

const thirdTurn = flow.beginListening();
assert.equal(thirdTurn.revision, 3);
assert.equal(flow.acceptEndpoint(thirdTurn, "commit"), true);
assert.equal(flow.stop(), true);
assert.equal(thirdTurn.phase, "stopped");
assert.equal(flow.complete(thirdTurn), false);
assert.equal(flow.requestNextListeningTurn(), false);
assert.equal(flow.stop(), false);

assert.equal(hasRealtimeVoiceInputEvidence({}), false);
assert.equal(
  hasRealtimeVoiceInputEvidence({ speech_started: true, has_transcript: false }),
  true
);
assert.equal(
  shouldReconnectPassiveVoiceFailure({
    retryable: true,
    committed: false,
    detectorSnapshot: { speech_started: false, has_transcript: false }
  }),
  true
);
assert.equal(
  shouldReconnectPassiveVoiceFailure({
    retryable: true,
    committed: false,
    detectorSnapshot: { speech_started: true, has_transcript: false }
  }),
  false
);
assert.equal(
  shouldReconnectPassiveVoiceFailure({
    retryable: false,
    committed: false,
    detectorSnapshot: { speech_started: false, has_transcript: false }
  }),
  false
);

const reconnectTimers = [];
const cancelledTimers = [];
const reconnects = [];
const exhausted = [];
const reconnect = new RealtimeVoiceReconnectBackoff({
  delaysMs: [100, 200],
  schedule: (callback, delayMs) => {
    const timer = { callback, delayMs };
    reconnectTimers.push(timer);
    return timer;
  },
  cancel: (timer) => cancelledTimers.push(timer),
  onReconnect: (payload) => reconnects.push(payload),
  onExhausted: (payload) => exhausted.push(payload)
});
assert.equal(reconnect.schedule(), "scheduled");
assert.equal(reconnect.pending, true);
assert.equal(reconnect.schedule(), "pending");
assert.equal(reconnectTimers[0].delayMs, 100);
reconnectTimers.shift().callback();
assert.equal(reconnect.pending, false);
assert.deepEqual(reconnects, [{ attempt: 1, delayMs: 100 }]);
assert.equal(reconnect.schedule(), "scheduled");
reconnectTimers.shift().callback();
assert.equal(reconnect.schedule(), "exhausted");
assert.deepEqual(exhausted, [{ attempts: 2 }]);
assert.equal(reconnect.reset(), true);
assert.equal(reconnect.attempts, 0);
assert.equal(reconnect.schedule(), "scheduled");
assert.equal(reconnect.stop(), true);
assert.equal(cancelledTimers.length, 1);
assert.equal(reconnect.pending, false);
assert.equal(reconnect.schedule(), "stopped");

console.log("realtime voice call flow smoke: ok");
