import assert from "node:assert/strict";

import { segmentSpeechForDelivery, missingReplySegments, SpeechStageReplay } from "../src/speech-delivery.js";

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

const bracketed = "刚才[The quick brown fox jumps over the lazy dog and returns to the castle]这段挺有意思。";
assert.deepEqual(segmentSpeechForDelivery(bracketed, { maxChars: 56, allowHardCut: false }), [bracketed]);
assert.deepEqual(segmentSpeechForDelivery('她说：“[English, with punctuation! And another sentence?]”然后笑了。', { maxChars: 24, allowHardCut: false }), ['她说：“[English, with punctuation! And another sentence?]”然后笑了。']);
assert.deepEqual(segmentSpeechForDelivery('[English\ncontinues here]结束。', { maxChars: 8, allowHardCut: false }), ['[English continues here]结束。']);
assert.equal(segmentSpeechForDelivery("中文字".repeat(30), { maxChars: 56, allowHardCut: false }).length, 1);
assert.deepEqual(missingReplySegments(["前半句，", "后半句。"], ["前半句，后半句。"]), []);
assert.deepEqual(missingReplySegments(["前半句，后半句。"], ["前半句，", "后半句。"]), []);
assert.deepEqual(missingReplySegments(["好的。", "好的。"], ["好的。"]), ["好的。"]);
assert.deepEqual(missingReplySegments(["已完成。下一步。"], ["我先查一下。", "已完成。"]), ["下一步。"]);
assert.deepEqual(missingReplySegments(["行，不同内容。"], ["行。"]), ["行，不同内容。"]);
const replay = new SpeechStageReplay();
assert.equal(replay.push("我查一下。", 0), "我查一下。");
assert.equal(replay.push("我查一下。", 0), "");
replay.finishStage();
assert.equal(replay.push("我查一下。结果出来了。", 0), "结果出来了。");
assert.equal(replay.push("好的。", 1), "好的。");
assert.equal(replay.push("好的。", 2), "好的。");
const divergingReplay = new SpeechStageReplay();
divergingReplay.push("这个步骤已经成功。", 0);
divergingReplay.finishStage();
assert.equal(divergingReplay.push("这个步骤", 0), "");
assert.equal(divergingReplay.push("遇到了新的问题。", 1), "这个步骤 遇到了新的问题。");
console.log("speech delivery: bracket safety, exact cross-boundary reconciliation and stage replay passed");

const pairs = [..."([{（【《「『“‘〈〔〖〘〚［｛｟«‹<"].map((open, i) => [open, [...")] }）】》」』”’〉〕〗〙〛］｝｠»›>".replaceAll(" ", "")][i]]);
pairs.push(['"', '"'], ["'", "'"], ["`", "`"], ["```", "```"]);
for (const [open, close] of pairs) {
  const text = `${open}Long English, with words!\nAnd more words?${close}结束。`;
  assert.deepEqual(segmentSpeechForDelivery(`${text}下一句。`, { maxChars: 12, allowHardCut: false }), [text.replaceAll("\n", " "), "下一句。"], open);
}
for (const [text, expected] of [
  ['她说："看《书里[还有\'nested!\']》吧。"结束。', ['她说："看《书里[还有\'nested!\']》吧。"结束。']],
  ["Don't worry. John's here.", ["Don't worry.", "John's here."]],
  ["‘Don’t worry! It’s fine.’结束。", ["‘Don’t worry! It’s fine.’结束。"]],
  ['完成。"Next! Still quoted?"结束。', ['完成。', '"Next! Still quoted?"结束。']],
  ['2 < 3。下一句。', ['2 < 3。', '下一句。']],
  ['[原文本来未闭合，继续输出。', ['[原文本来未闭合，继续输出。']]
]) assert.deepEqual(segmentSpeechForDelivery(text, { allowHardCut: false }), expected);
console.log("speech delivery: paired symbols, nesting, apostrophes and unclosed source passed");
