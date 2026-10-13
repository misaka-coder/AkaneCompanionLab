import assert from "node:assert/strict";
import { test, after } from "node:test";
import { createBackgroundTurnInbox } from "../src/pet/background-turn-stream.js";
import { createLifecycle } from "../src/pet/lifecycle.js";
import { createRuntimeScope } from "../src/pet/scope.js";
import { loadPetRuntime, createEnvironment, mockBackend, tick } from "./pet-runtime-harness.mjs";

const runtime = await loadPetRuntime();
after(() => runtime.close());
const settle = async () => { for (let i = 0; i < 5; i++) await tick(); };

async function fixture(t) {
  const env = createEnvironment({ ...mockBackend(), tauri: true });
  const app = runtime.createPetApplication({ ...env, bundledCharacterAssets: { "/synthetic/normal.png": "synthetic.png" } });
  t.after(() => app.dispose());
  assert.equal(await app.start(), true);
  app.state.voiceEnabled = false;
  await app.controllers.plugins.registerPluginAgentEventBridge();
  const bridge = env.nativeHandlers.get("akane:plugin-agent-event-frame");
  assert.equal(typeof bridge, "function");
  let deliveries = 0;
  const send = (sequence, events, streamId = "turn-a", overrides = {}) => {
    bridge({ payload: { deliveryId: `fixture-${deliveries++}`, payload: { _desktop_turn_event: {
      stream_id: streamId, sequence, session_id: app.state.sessionId,
      profile_user_id: app.controllers.persistence.getProfileUserId(),
      character_pack_id: app.controllers.character.getCurrentCharacterPackId(), events, ...overrides
    } } } });
  };
  return { ...env, app, send };
}

const start = [{ type: "stream_start", phase: "resuming" }];
const final = text => [{ type: "final", payload: { speech: text } }, { type: "stream_end", status: "ok" }];

test("background progress is visible before final and does not wait for its own chat/TTS", async t => {
  const { app, elements, send, nativeCalls } = await fixture(t);
  send(0, start);
  await settle();
  assert.equal(app.controllers.chat.sending, true);
  assert.match(elements.bubbleText.textContent, /恢复任务/);
  send(1, [{ type: "speech_segment", index: 0, text: "我先检查。" }]);
  await settle();
  assert.equal(elements.bubbleText.textContent, "我先检查。");
  assert.equal(app.controllers.chat.sending, true);
  send(2, [{ type: "assistant_working", message: "检查工具中。" }]);
  await settle();
  assert.equal(elements.bubbleText.textContent, "我先检查。", "working state must not overwrite an expressive preface");
  send(3, final("已完成。"));
  await settle();
  assert.equal(app.controllers.chat.sending, false);
  assert.deepEqual(app.controllers.bubble.streamedReplyTexts, ["我先检查。", "已完成。"]);
  assert.equal(nativeCalls.filter(item => item.command === "acknowledge_plugin_agent_event_frame").length, 4);
});

test("duplicate frames, stage replay and canonical final do not repeat speech", async t => {
  const { app, send } = await fixture(t);
  send(0, start);
  send(1, [{ type: "speech_segment", index: 0, text: "同一段话。" }]);
  send(1, [{ type: "speech_segment", index: 0, text: "同一段话。" }]);
  send(2, [{ type: "assistant_stage_decision", has_tool_call: true }]);
  send(3, [{ type: "speech_segment", index: 0, text: "同一段话。" }]);
  send(4, final("同一段话。"));
  await settle();
  assert.equal(app.controllers.chat.sending, false);
  assert.deepEqual(app.controllers.bubble.streamedReplyTexts, ["同一段话。"]);
  const token = app.controllers.chat.activeTurnToken;
  send(0, start);
  send(5, final("迟到的旧回复。"));
  await settle();
  assert.equal(app.controllers.chat.activeTurnToken, token);
  assert.deepEqual(app.controllers.bubble.streamedReplyTexts, ["同一段话。"]);
});

test("structured utterance text and final use the existing unit replay path", async t => {
  const { app, send } = await fixture(t);
  send(0, start);
  send(1, [{ type: "speech_mode", speech_protocol: "speech_units_v1", emotion: "normal" },
    { type: "speech_unit_text", unit_index: 0, text: "这一段完成。", emotion: "happy" },
    { type: "voice_unit_end", unit_index: 0, display_text: "这一段完成。", has_voice: false }]);
  send(2, [{ type: "final", payload: { speech: "这一段完成。", speech_protocol: "speech_units_v1",
    utterances: [{ text: "这一段完成。", say: null, emotion: "happy" }] } }, { type: "stream_end", status: "ok" }]);
  await settle();
  assert.equal(app.controllers.chat.sending, false);
  assert.deepEqual(app.controllers.bubble.streamedReplyTexts, ["这一段完成。"]);
});

test("error and stopped streams release ownership without a fabricated final", async t => {
  const { app, send, elements } = await fixture(t);
  send(0, start);
  send(1, [{ type: "speech_segment", index: 0, text: "检查进行中。" }]);
  await settle();
  send(2, [{ type: "stream_error", message: "后台任务未能完成。" }, { type: "stream_end", status: "error" }]);
  await settle();
  assert.equal(app.controllers.chat.sending, false);
  assert.match(elements.bubbleText.textContent, /未能完成/);
  send(0, start, "turn-b");
  send(1, [{ type: "turn_stopped" }, { type: "stream_end", status: "stopped" }], "turn-b");
  await settle();
  assert.equal(app.controllers.chat.sending, false);
  assert.match(elements.bubbleText.textContent, /已停止/);
});

test("scope checks reject old sessions, profiles and characters", async t => {
  const { app, send } = await fixture(t);
  for (const key of ["session_id", "profile_user_id", "character_pack_id"]) send(0, start, key, { [key]: "obsolete" });
  await settle();
  assert.equal(app.controllers.chat.sending, false);
  send(0, start);
  await settle();
  const old = app.state.sessionId;
  app.controllers.chat.interruptReply({ announce: false });
  app.state.sessionId = "restored-session";
  send(1, final("旧会话迟到回复。"), "turn-a", { session_id: old });
  await settle();
  assert.equal(app.controllers.chat.sending, false);
  assert.ok(!app.controllers.bubble.streamedReplyTexts.includes("旧会话迟到回复。"));
});

test("a queued stream waits behind a foreground owner without losing its initial event", async t => {
  const { app, send } = await fixture(t);
  const token = app.controllers.chat.beginTurn("foreground-fixture");
  send(0, start);
  send(1, [{ type: "speech_segment", index: 0, text: "排队任务继续。" }]);
  await settle();
  assert.equal(app.controllers.chat.activeTurnToken, token);
  app.controllers.chat.endTurn(token);
  await new Promise(resolve => setTimeout(resolve, 250));
  assert.notEqual(app.controllers.chat.activeTurnToken, token);
  assert.deepEqual(app.controllers.bubble.streamedReplyTexts, ["排队任务继续。"]);
  send(2, final("完成。"));
  await settle();
  assert.equal(app.controllers.chat.sending, false);
});

test("consecutive FIFO background turns do not overtake a pending final", async t => {
  const { app, send } = await fixture(t);
  send(0, start);
  send(1, final("第一项完成。"));
  send(0, start, "turn-b");
  send(1, final("第二项完成。"), "turn-b");
  await settle();
  assert.equal(app.controllers.chat.sending, false);
  assert.deepEqual(app.controllers.bubble.streamedReplyTexts, ["第二项完成。"]);
});

test("legacy final-only plugin frames remain compatible", async t => {
  const { elements, nativeHandlers } = await fixture(t);
  nativeHandlers.get("akane:plugin-agent-event-frame")({ payload: {
    deliveryId: "legacy", payload: { speech: "旧插件最终回复。" }
  } });
  await settle();
  assert.equal(elements.bubbleText.textContent, "旧插件最终回复。");
});

test("dispose releases a waiting subscriber and rejects late events", async t => {
  const { app, send } = await fixture(t);
  send(0, start);
  await settle();
  app.dispose();
  send(1, final("销毁后的回复。"));
  await settle();
  assert.equal(app.controllers.chat.sending, false);
  assert.ok(!app.controllers.bubble.streamedReplyTexts.includes("销毁后的回复。"));
});

test("acknowledged stop releases only the local subscriber and rejects late presentation", async t => {
  const { app, send, elements } = await fixture(t);
  send(0, start);
  await settle();
  app.controllers.chat.interruptReply({ announce: true });
  await settle();
  assert.equal(app.controllers.chat.sending, false);
  assert.match(elements.status.textContent, /停止请求已送达/);
  send(1, final("停止后迟到回复。"));
  await settle();
  assert.ok(!app.controllers.bubble.streamedReplyTexts.includes("停止后迟到回复。"));
});

test("idle timeout closes a missing-terminal stream without restarting work", async () => {
  const lifecycle = createLifecycle({ setTimeout, clearTimeout, setInterval, clearInterval });
  const scope = createRuntimeScope({ sessionId: "fixture" });
  let busy = false, starts = 0, ends = 0;
  const errors = [];
  const inbox = createBackgroundTurnInbox({ lifecycle, scope, matches: () => true, available: () => !busy,
    begin: () => { busy = true; starts += 1; return starts; },
    end: () => { busy = false; ends += 1; },
    process: async events => { for await (const event of events) assert.ok(event.type); },
    onError: error => errors.push(error.message), idleTimeout: 10 });
  try {
    inbox.receive({ stream_id: "slow", sequence: 0, events: start });
    await new Promise(resolve => setTimeout(resolve, 30));
    assert.equal(busy, false);
    assert.equal(inbox.active, false);
    assert.equal(starts, 1);
    assert.equal(ends, 1);
    assert.equal(errors.length, 1);
    assert.equal(inbox.receive({ stream_id: "slow", sequence: 1, events: final("late") }), false);
  } finally { lifecycle.dispose(); }
});
