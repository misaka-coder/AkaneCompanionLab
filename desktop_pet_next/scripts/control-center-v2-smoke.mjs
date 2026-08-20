import assert from "node:assert/strict";

import {
  createControlCenterViewModel,
  isObservedActionConfirmation,
  normalizeActionPresentation
} from "../src/control-center-v2/view-model.js";
import { renderCharacterAppearance } from "../src/control-center-v2/components/appearance.js";
import { renderChat } from "../src/control-center-v2/components/chat.js";
import {
  createBackendControlCenterSource,
  createControlCenterRuntimeSnapshot
} from "../src/control-center/data-sources.js";

const rawSnapshot = {
  sourceKind: "backend",
  fallbackReason: null,
  generatedAt: "2026-08-20T08:00:00.000Z",
  controlCenterRuntime: {
    health: { ok: true, data: { instance_id: "desktop-local" } }
  },
  overviewRuntime: {
    shell: { status: "在线" },
    emotion: { name: "开心", image: "https://127.0.0.1/assets/happy.png" },
    abilities: ["文件处理", "文档交付"],
    recentOutputs: [{ id: "gen_1", title: "交付结果.png", format: "PNG", status: "available" }]
  },
  characterRuntime: {
    selectedPack: "测试角色",
    selectedPackId: "test_character",
    hero: "https://127.0.0.1/assets/hero.png",
    availablePacks: [
      { id: "test_character", appName: "测试角色", defaultOutfit: "default", selected: true },
      { id: "second_character", appName: "另一位角色", defaultOutfit: "casual" }
    ],
    outfits: [
      { id: "default", name: "默认服装", image: "https://127.0.0.1/assets/default.png", current: true },
      { id: "casual", name: "日常服装", image: "https://127.0.0.1/assets/casual.png" }
    ],
    emotions: [
      { id: "happy", name: "开心", image: "https://127.0.0.1/assets/happy.png", current: true },
      { id: "thinking", name: "思考", image: "https://127.0.0.1/assets/thinking.png" }
    ],
    packInfo: [{ label: "版本", value: "0.2" }],
    completeness: 100,
    warning: { headline: "资源状态良好", body: "已加载统一资源清单" },
    emotions: [{ current: true, image: "https://127.0.0.1/assets/happy.png" }]
  },
  musicRuntime: {
    nowPlaying: { title: "星河", artist: "本地媒体", playing: true },
    bottomStatus: "正在播放"
  },
  chatSession: {
    session: { session_id: "session-chat", display_title: "下午的对话" },
    messages: [
      { source_id: "msg-user", seq_no: 1, role: "user", content: "今天一起做什么？", timestamp: 1787198400 },
      { source_id: "msg-assistant", seq_no: 2, role: "assistant", content: "先把界面做舒服。", timestamp: 1787198460 },
      {
        source_id: "msg-progress",
        seq_no: 3,
        role: "assistant",
        content: "我正在检查现有页面。",
        timestamp: 1787198520,
        memory_metadata: { turn_role: "intermediate" }
      }
    ]
  }
};

const runtimeSnapshot = {
  state: { instanceId: "desktop-local", sessionId: "session-chat", characterPackId: "test_character", outfit: "default" },
  currentExpression: { id: "happy", name: "开心", image: "https://127.0.0.1/assets/happy.png" },
  runtimeStatus: "正在整理文件",
  runtimeMode: "thinking",
  active: { sending: true, speaking: false, replyDisplayActive: false }
};

const viewModel = createControlCenterViewModel(rawSnapshot, runtimeSnapshot);
assert.equal(viewModel.shell.connected, true);
assert.equal(viewModel.shell.instanceLabel, "desktop-local");
assert.equal(viewModel.character.displayName, "测试角色");
assert.equal(viewModel.character.emotion, "开心");
assert.equal(viewModel.character.resourceWarnings.length, 0);
assert.equal(viewModel.character.availablePacks.length, 2);
assert.equal(viewModel.character.availablePacks[0].selected, true);
assert.equal(viewModel.character.outfits[0].current, true);
assert.equal(viewModel.character.emotions[0].current, true);
assert.equal(viewModel.character.completeness, 100);
assert.equal(viewModel.activity.phase, "thinking");
assert.equal(viewModel.activity.label, "正在整理文件");
assert.equal(viewModel.music.playback, "playing");
assert.equal(viewModel.recentOutputs[0].title, "交付结果.png");
assert.equal(viewModel.chat.title, "下午的对话");
assert.equal(viewModel.chat.messages.length, 3);
assert.equal(viewModel.chat.messages[2].intermediate, true);
assert.equal(viewModel.actions["chat.send"].available, false);
assert.equal(viewModel.actions["chat.stop"].available, true);
assert.equal(viewModel.actions["character.openWorkshop"].available, true);
assert.equal(viewModel.actions["character.selectPack"].available, true);
assert.equal(viewModel.actions["character.setOutfit"].available, true);
assert.equal(viewModel.actions["character.previewEmotion"].available, true);
assert.equal(viewModel.actions["window.minimize"].available, true);
assert.equal(viewModel.actions["window.maximize"].available, true);
assert.equal(viewModel.actions["window.close"].available, true);

const runtimeOnly = createControlCenterRuntimeSnapshot({
  ...rawSnapshot,
  overviewPage: { title: "legacy mock title" },
  characterPage: { selectedPack: "legacy mock character" },
  navItems: [{ id: "overview", label: "legacy mock nav" }]
});
assert.equal(Object.hasOwn(runtimeOnly, "overviewPage"), false);
assert.equal(Object.hasOwn(runtimeOnly, "characterPage"), false);
assert.equal(Object.hasOwn(runtimeOnly, "navItems"), false);
assert.equal(runtimeOnly.characterRuntime.selectedPack, "测试角色");

const unsafeAsset = createControlCenterViewModel({
  ...rawSnapshot,
  overviewRuntime: { emotion: { name: "未知", image: "file:///C:/private/character.png" } },
  characterRuntime: { selectedPack: "无图角色", hero: "C:/private/hero.png" }
});
assert.equal(unsafeAsset.character.visuals.avatar, "");
assert.equal(unsafeAsset.character.visuals.hero, "");

const relativeAsset = createControlCenterViewModel({
  ...rawSnapshot,
  overviewRuntime: { emotion: { name: "本地资源", image: "/assets/character.png" } }
});
assert.equal(relativeAsset.character.visuals.avatar, "/assets/character.png");

const unsafeDataAsset = createControlCenterViewModel({
  ...rawSnapshot,
  overviewRuntime: { emotion: { name: "坏资源", image: "data:text/html,<script>alert(1)</script>" } }
});
assert.equal(unsafeDataAsset.character.visuals.avatar, "");

assert.deepEqual(normalizeActionPresentation({ ok: true, status: "executed" }), {
  phase: "confirmed",
  label: "已完成",
  detail: ""
});
assert.deepEqual(normalizeActionPresentation({ ok: false, status: "execution_unknown", reason: "state_not_confirmed" }), {
  phase: "unknown",
  label: "已发送，未确认",
  detail: "state_not_confirmed"
});
assert.deepEqual(normalizeActionPresentation({ ok: false, status: "execution_unknown", reason: "runtime_state_not_confirmed" }), {
  phase: "unknown",
  label: "已发送，未确认",
  detail: "指令已发出，但没有在时限内观察到状态变化"
});
assert.deepEqual(normalizeActionPresentation({ ok: false, status: "failed", error: "permission_denied" }), {
  phase: "failed",
  label: "操作失败",
  detail: "permission_denied"
});

assert.equal(isObservedActionConfirmation(
  "chat.send",
  { active: { sending: false } },
  {
    active: { sending: true },
    settingsCommandResult: { command: "sendChatMessage", status: "accepted" }
  },
  { text: "你好" }
), true);
assert.equal(isObservedActionConfirmation(
  "chat.send",
  { active: { sending: false } },
  {
    active: { sending: false },
    settingsCommandResult: { command: "sendChatMessage", status: "busy" }
  },
  { text: "你好" }
), false);
assert.equal(isObservedActionConfirmation(
  "chat.new",
  { state: { sessionId: "session-a" } },
  { state: { sessionId: "session-b" } }
), true);
assert.equal(isObservedActionConfirmation(
  "chat.stop",
  { active: { sending: true, replyDisplayActive: true } },
  { active: { sending: false, replyDisplayActive: false } }
), true);
assert.equal(isObservedActionConfirmation(
  "music.pause",
  { active: { musicPlaying: true, musicPaused: false } },
  { active: { musicPlaying: false, musicPaused: true } }
), true);
assert.equal(isObservedActionConfirmation(
  "chat.new",
  { state: { sessionId: "session-a" } },
  { state: { sessionId: "session-a" } }
), false);

assert.equal(isObservedActionConfirmation(
  "character.selectPack",
  { state: { characterPackId: "test_character" } },
  { state: { characterPackId: "second_character" } },
  { value: "second_character" }
), true);
assert.equal(isObservedActionConfirmation(
  "character.setOutfit",
  { state: { outfit: "default" } },
  { state: { outfit: "casual" } },
  { value: "casual" }
), true);
assert.equal(isObservedActionConfirmation(
  "character.previewEmotion",
  { currentExpression: { id: "happy" } },
  { currentExpression: { id: "thinking" } },
  { value: "thinking" }
), true);
assert.equal(isObservedActionConfirmation(
  "character.selectPack",
  { state: { characterPackId: "test_character" } },
  { state: { characterPackId: "unexpected_character" } },
  { value: "second_character" }
), false);

const appearanceHtml = renderCharacterAppearance({
  viewModel,
  actionStates: {},
  phase: "ready"
});
assert.match(appearanceHtml, /另一位角色/);
assert.match(appearanceHtml, /character\.selectPack/);
assert.match(appearanceHtml, /data-action-value="second_character"/);
assert.match(appearanceHtml, /日常服装/);
assert.match(appearanceHtml, /data-action-value="casual"/);
assert.match(appearanceHtml, /character\.previewEmotion/);
assert.doesNotMatch(appearanceHtml, /Akane Default/);

const chatHtml = renderChat({
  viewModel,
  actionStates: {},
  phase: "ready"
});
assert.match(chatHtml, /下午的对话/);
assert.match(chatHtml, /今天一起做什么/);
assert.match(chatHtml, /先把界面做舒服/);
assert.match(chatHtml, /data-chat-viewport/);
assert.match(chatHtml, /data-chat-form/);
assert.match(chatHtml, /data-action="chat\.new"/);
assert.doesNotMatch(chatHtml, /假消息|演示消息/);

const chatRequests = [];
const chatSource = createBackendControlCenterSource({
  baseUrl: "http://127.0.0.1:9999",
  expectedInstanceId: "desktop-local",
  sessionId: "session-original",
  profileUserId: "master",
  characterPackId: "test_character",
  fetchImpl: async (url, init = {}) => {
    chatRequests.push({ url: String(url), init });
    if (String(url).includes("/health")) {
      return new Response(JSON.stringify({ status: "ok", root_binding: "valid", instance_id: "desktop-local" }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      });
    }
    return new Response(JSON.stringify({
      session: { session_id: "session-live" },
      messages: [{ role: "assistant", content: "真实历史" }]
    }), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    });
  }
});
const liveChatSession = await chatSource.readChatSession({ sessionId: "session-live", characterPackId: "second_character" });
assert.equal(liveChatSession.messages[0].content, "真实历史");
const chatRequestBody = JSON.parse(chatRequests.find((item) => item.url.includes("/sessions/ensure")).init.body);
assert.equal(chatRequestBody.session_id, "session-live");
assert.equal(chatRequestBody.character_pack_id, "second_character");

console.log("control-center V2 smoke passed");
