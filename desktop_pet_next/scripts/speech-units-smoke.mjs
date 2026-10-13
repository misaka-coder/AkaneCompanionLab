import assert from "node:assert/strict";
import { utteranceVoiceSegments, VoiceStageReplay, voiceQueueItem, canMergeVoiceItems } from "../src/speech-units.js";

const units = [{ style: "calm", text: "回来啦。喝杯茶吧。", say: "おかえり。お茶にしよう。" },
  { style: "happy", text: "太好啦！", say: "よかった！" }];
const events = utteranceVoiceSegments(units);
assert.deepEqual(events.map(e => e.text), ["おかえり。", "お茶にしよう。", "よかった！"]);
assert.deepEqual(events.map(e => e.style), ["calm", "calm", "happy"]);
assert.equal(canMergeVoiceItems(voiceQueueItem(events[0]), voiceQueueItem(events[1])), true);
assert.equal(canMergeVoiceItems(voiceQueueItem(events[1]), voiceQueueItem(events[2])), false);
assert.equal(utteranceVoiceSegments([{ text: "（端来热茶）" }, { text: "只展示", say: null }]).length, 0);
const replay = new VoiceStageReplay();
assert.equal(replay.accept(events[0]), true);
assert.equal(replay.accept(events[0]), false);
assert.deepEqual(replay.missingFinal(events), events.slice(1));
replay.finishStage();
assert.equal(replay.accept(events[0]), false);
assert.equal(replay.accept(events[1]), true);
assert.equal(replay.accept(events[2]), true);
replay.finishStage(); // assistant_stage_decision is emitted before final.
assert.deepEqual(replay.missingFinal(events).filter(e => replay.accept(e)), []);
const repeated = new VoiceStageReplay();
assert.equal(repeated.accept({ ...events[0], index: 0 }), true);
assert.equal(repeated.accept({ ...events[0], index: 1 }), true);
const recovery = new VoiceStageReplay();
assert.equal(recovery.accept({ ...events[2], index: 0 }), true); // tool preface
recovery.finishStage();
assert.equal(recovery.accept(events[0]), true);
recovery.finishStage();
assert.equal(recovery.accept({ ...events[1], index: 0 }), true); // recovery tail
const canonical = [events[0], events[1], { ...events[1], index: 2 }];
recovery.beginFinal(canonical);
assert.deepEqual(canonical.filter(event => recovery.accept(event)), [canonical[2]], "skip spoken prefix, retain intentional repetition");
const long = utteranceVoiceSegments([{ text: "多句", say: "一つ目です。".repeat(20) }]);
assert.equal(long.length, 20);
console.log("speech units: sentence splitting, style boundaries, missing speech, retry/final dedup passed");
