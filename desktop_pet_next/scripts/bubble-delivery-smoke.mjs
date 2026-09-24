import assert from "node:assert/strict";

import {
  getBubbleSegmentDisplayDelay
} from "../src/bubble-delivery.js";

assert.equal(getBubbleSegmentDisplayDelay("短句。"), 2200);
assert.equal(getBubbleSegmentDisplayDelay("稍长一些的句子，用来计算展示时间。"), 2200);
assert.equal(getBubbleSegmentDisplayDelay("长".repeat(80)), 3000);

console.log("bubble delivery smoke: ok");
