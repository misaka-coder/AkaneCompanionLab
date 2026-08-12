import assert from "node:assert/strict";

import { segmentSpeechForDelivery } from "../src/speech-delivery.js";

assert.deepEqual(segmentSpeechForDelivery("1. 先检查文件。2. 再执行转换。"), [
  "1. 先检查文件。",
  "2. 再执行转换。"
]);
assert.deepEqual(segmentSpeechForDelivery("《孤独摇滚！》很好看。下一句。"), [
  "《孤独摇滚！》很好看。",
  "下一句。"
]);
assert.deepEqual(segmentSpeechForDelivery("她说：“我在看《孤独摇滚！》。”然后笑了。"), [
  "她说：“我在看《孤独摇滚！》。”然后笑了。"
]);
assert.deepEqual(segmentSpeechForDelivery("收益率是 3.5%。"), ["收益率是 3.5%。"]);
assert.deepEqual(segmentSpeechForDelivery("U.S. market is open. OK."), ["U.S. market is open.", "OK."]);
assert.deepEqual(segmentSpeechForDelivery("……喵。行了，别得意了。"), [
  "……",
  "喵。",
  "行了，别得意了。"
]);

const ttsChunks = segmentSpeechForDelivery(
  "这是一个需要交给语音合成的很长句子，《孤独摇滚！》的标题必须保持完整，然后再继续说下去。",
  { minChars: 1, maxChars: 24 }
);
assert.ok(ttsChunks.length > 1);
assert.equal(ttsChunks.some((item) => item === "《孤独摇滚！》"), false);
assert.equal(ttsChunks.join(""), "这是一个需要交给语音合成的很长句子，《孤独摇滚！》的标题必须保持完整，然后再继续说下去。");

console.log("speech delivery smoke: ok");
