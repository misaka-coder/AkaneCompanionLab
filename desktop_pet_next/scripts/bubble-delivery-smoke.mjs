import assert from "node:assert/strict";

import {
  completeSegmentedBubbleDelivery,
  getBubbleSegmentDisplayDelay,
  splitStreamedBubbleText
} from "../src/bubble-delivery.js";

assert.equal(getBubbleSegmentDisplayDelay("短句。"), 2200);
assert.equal(getBubbleSegmentDisplayDelay("稍长一些的句子，用来计算展示时间。"), 2200);
assert.equal(getBubbleSegmentDisplayDelay("长".repeat(80)), 3000);

assert.deepEqual(
  splitStreamedBubbleText("嗒，给你看——气鼓鼓，就是这样。满意？那就好！"),
  ["嗒，给你看——气鼓鼓，就是这样。", "满意？", "那就好！"]
);

assert.deepEqual(
  completeSegmentedBubbleDelivery({
    finalSpeech: "第一条。第二条。第三条。",
    lastSegment: "第三条。"
  }),
  { replayFinalSpeech: false, dismissCharCount: 4 }
);

console.log("bubble delivery smoke: ok");
