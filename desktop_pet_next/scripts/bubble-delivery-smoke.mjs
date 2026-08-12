import assert from "node:assert/strict";

import {
  completeSegmentedBubbleDelivery,
  getBubbleSegmentDisplayDelay
} from "../src/bubble-delivery.js";

assert.equal(getBubbleSegmentDisplayDelay("短句。"), 2200);
assert.equal(getBubbleSegmentDisplayDelay("稍长一些的句子，用来计算展示时间。"), 2200);
assert.equal(getBubbleSegmentDisplayDelay("长".repeat(80)), 3000);

assert.deepEqual(
  completeSegmentedBubbleDelivery({
    finalSpeech: "第一条。第二条。第三条。",
    lastSegment: "第三条。"
  }),
  { replayFinalSpeech: false, dismissCharCount: 4 }
);

console.log("bubble delivery smoke: ok");
