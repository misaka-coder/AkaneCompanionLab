import assert from "node:assert/strict";

import {
  createControlCenterViewModel,
  isObservedActionConfirmation,
  normalizeActionPresentation
} from "../src/control-center-v2/view-model.js";
import { createControlCenterRuntimeSnapshot } from "../src/control-center/data-sources.js";

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
    warning: { headline: "资源状态良好", body: "已加载统一资源清单" },
    emotions: [{ current: true, image: "https://127.0.0.1/assets/happy.png" }]
  },
  musicRuntime: {
    nowPlaying: { title: "星河", artist: "本地媒体", playing: true },
    bottomStatus: "正在播放"
  }
};

const runtimeSnapshot = {
  state: { instanceId: "desktop-local", characterPackId: "test_character", outfit: "default" },
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
assert.equal(viewModel.activity.phase, "thinking");
assert.equal(viewModel.activity.label, "正在整理文件");
assert.equal(viewModel.music.playback, "playing");
assert.equal(viewModel.recentOutputs[0].title, "交付结果.png");
assert.equal(viewModel.actions["chat.stop"].available, true);
assert.equal(viewModel.actions["character.openWorkshop"].available, true);
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

console.log("control-center V2 smoke passed");
