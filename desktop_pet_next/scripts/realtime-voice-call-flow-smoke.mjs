import assert from "node:assert/strict";

import { RealtimeVoiceCallFlow } from "../src/realtime-voice-call-flow.js";

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
assert.equal(flow.complete(overlapTurn), true);
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

console.log("realtime voice call flow smoke: ok");
