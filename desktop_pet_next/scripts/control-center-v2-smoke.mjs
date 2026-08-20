import assert from "node:assert/strict";

import {
  createControlCenterViewModel,
  isObservedActionConfirmation,
  normalizeActionPresentation
} from "../src/control-center-v2/view-model.js";
import { renderCharacterAppearance } from "../src/control-center-v2/components/appearance.js";
import { renderAbilities } from "../src/control-center-v2/components/abilities.js";
import { renderChat } from "../src/control-center-v2/components/chat.js";
import { renderVoice } from "../src/control-center-v2/components/voice.js";
import { bindInstanceStorage } from "../src/instance-storage.js";
import {
  loadPresentationPreferences,
  normalizePresentationPreferences,
  presentationCssVariables,
  resetPresentationFrame,
  resolveThemeMode,
  savePresentationPreferences,
  updatePresentationFrame
} from "../src/control-center-v2/presentation-preferences.js";
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
  abilitiesRuntime: {
    overview: {
      availability: 86,
      note: "本地能力目录已同步，2 项能力等待配置",
      stats: [
        { label: "能力模块", value: "6" },
        { label: "可用能力", value: "24" },
        { label: "待完善", value: "2" }
      ]
    },
    modules: [
      { title: "文件与工作区", description: "材料整理 / 文件交付", permission: "工作区文件访问", count: "8 项能力", tone: "orange", statusLabel: "可用", statusTone: "ready" },
      { title: "安全与契约", description: "权限确认 / 风险隔离", permission: "安全与确认", count: "3 项能力", tone: "pink", statusLabel: "已生效", statusTone: "ready" }
    ],
    providers: [{ title: "本地语音服务", status: "ready", statusLabel: "可用", reason: "已连接" }],
    mcpServers: [{ title: "AnySearch", status: "missing_config", statusLabel: "待配置", reason: "需要配置本地 MCP" }],
    workflows: [{ title: "透明背景处理", status: "ready", statusLabel: "可用", detail: "工作流已绑定" }],
    safety: {
      status: "已生效",
      approvalPolicy: {
        defaultMode: "ask_each_time",
        label: "请求批准",
        summary: "高风险能力在执行前创建审批请求。",
        availableModes: [
          { id: "ask_each_time", label: "请求批准", summary: "高风险动作先确认。" },
          { id: "trusted_auto_allow", label: "完全访问", summary: "自动允许，但保留硬安全校验。" }
        ]
      },
      items: [
        { label: "当前审批模式", status: "请求批准" },
        { label: "密钥与敏感信息", status: "未暴露" }
      ]
    },
    calls: [{ time: "14:20", module: "能力注册表", description: "已同步 24 项能力", status: "成功", method: "能力目录" }]
  },
  voiceRuntime: {
    tts: {
      enabled: true,
      volume: 82,
      speed: "1.15x",
      providerStatus: { status: "ready", statusLabel: "已就绪", activeProviderName: "GPT-SoVITS" }
    },
    asr: {
      enabled: true,
      providerStatus: { status: "degraded", statusLabel: "已降级", activeProviderName: "本地 ASR", reasonLabel: "使用本地通道" }
    },
    wakeWord: "Akane",
    wakeSensitivity: "中等",
    diagnostics: [
      { label: "整体状态", value: "正常运行", tone: "good" },
      { label: "响应延迟", value: "420 ms", tone: "good" }
    ]
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
  state: {
    instanceId: "desktop-local",
    sessionId: "session-chat",
    characterPackId: "test_character",
    outfit: "default",
    voiceEnabled: true,
    voiceInputEnabled: true,
    voiceVolume: 0.82,
    voiceSpeed: "1.15x",
    wakeWord: "Akane",
    wakeSensitivity: "中等"
  },
  currentExpression: { id: "happy", name: "开心", image: "https://127.0.0.1/assets/happy.png" },
  runtimeStatus: "正在整理文件",
  runtimeMode: "thinking",
  active: { sending: true, speaking: false, voiceInput: "idle", replyDisplayActive: false },
  tts: { active: false, queueLength: 0 }
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
assert.equal(viewModel.abilities.available, true);
assert.equal(viewModel.abilities.availability, 86);
assert.equal(viewModel.abilities.modules.length, 2);
assert.equal(viewModel.abilities.policy.defaultMode, "ask_each_time");
assert.equal(viewModel.abilities.integrations.length, 3);
assert.equal(viewModel.voice.available, true);
assert.equal(viewModel.voice.controlsAvailable, true);
assert.equal(viewModel.voice.tts.provider.name, "GPT-SoVITS");
assert.equal(viewModel.voice.tts.volume, 82);
assert.equal(viewModel.voice.asr.provider.status, "degraded");
assert.equal(viewModel.voice.wakeSensitivity, "中等");
assert.equal(viewModel.actions["abilities.approvalPolicy.save"].available, true);
assert.equal(viewModel.chat.title, "下午的对话");
assert.equal(viewModel.chat.messages.length, 3);
assert.equal(viewModel.chat.messages[2].intermediate, true);
assert.equal(viewModel.actions["chat.send"].available, false);
assert.equal(viewModel.actions["chat.stop"].available, true);
assert.equal(viewModel.actions["character.openWorkshop"].available, true);
assert.equal(viewModel.actions["character.selectPack"].available, true);
assert.equal(viewModel.actions["character.setOutfit"].available, true);
assert.equal(viewModel.actions["character.previewEmotion"].available, true);
assert.equal(viewModel.actions["voice.previewPlay"].available, true);
assert.equal(viewModel.actions["voice.stop"].available, false);
assert.equal(viewModel.actions["voice.setTtsEnabled"].available, true);
assert.equal(viewModel.actions["window.minimize"].available, true);
assert.equal(viewModel.actions["window.maximize"].available, true);
assert.equal(viewModel.actions["window.close"].available, true);

const liveVoiceOverride = createControlCenterViewModel(rawSnapshot, {
  ...runtimeSnapshot,
  state: {
    ...runtimeSnapshot.state,
    voiceEnabled: false,
    voiceInputEnabled: false,
    voiceVolume: 0.47,
    voiceSpeed: "0.85x",
    wakeWord: "灵梦",
    wakeSensitivity: "低"
  }
});
assert.equal(liveVoiceOverride.voice.tts.enabled, false);
assert.equal(liveVoiceOverride.voice.asr.enabled, false);
assert.equal(liveVoiceOverride.voice.tts.volume, 47);
assert.equal(liveVoiceOverride.voice.tts.speed, "0.85x");
assert.equal(liveVoiceOverride.voice.wakeWord, "灵梦");
assert.equal(liveVoiceOverride.voice.wakeSensitivity, "低");

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
  "voice.previewPlay",
  { active: { speaking: false } },
  { active: { speaking: true } },
  { text: "试听" }
), true);
assert.equal(isObservedActionConfirmation(
  "voice.stop",
  { active: { speaking: true } },
  { active: { speaking: false } }
), true);
assert.equal(isObservedActionConfirmation(
  "voice.setTtsEnabled",
  { state: { voiceEnabled: true } },
  { state: { voiceEnabled: false } },
  { value: false }
), true);
assert.equal(isObservedActionConfirmation(
  "voice.setTtsEnabled",
  { state: { voiceEnabled: true } },
  { state: {} },
  { value: false }
), false);
assert.equal(isObservedActionConfirmation(
  "voice.setVolume",
  { state: { voiceVolume: 0.82 } },
  { state: { voiceVolume: 0.64 } },
  { value: 0.64 }
), true);
assert.equal(isObservedActionConfirmation(
  "voice.setWakeWord",
  { state: { wakeWord: "Akane" } },
  { state: { wakeWord: "灵梦" } },
  { value: "灵梦" }
), true);
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
assert.match(appearanceHtml, /data-theme-mode="system"/);
assert.match(appearanceHtml, /data-theme-mode="light"/);
assert.match(appearanceHtml, /data-accent-preset="sakura"/);
assert.match(appearanceHtml, /data-font-preset="rounded"/);
assert.match(appearanceHtml, /data-presentation-range="surfaceOpacity"/);
assert.match(appearanceHtml, /data-presentation-range="backgroundDim"/);
assert.match(appearanceHtml, /data-presentation-range="blurAmount"/);
assert.match(appearanceHtml, /data-presentation-toggle="reducedMotion"/);
assert.match(appearanceHtml, /data-framing-stage/);
assert.match(appearanceHtml, /data-framing-target="portrait"/);
assert.doesNotMatch(appearanceHtml, /Akane Default/);
assert.doesNotMatch(appearanceHtml, /<html[^>]*data-theme-mode/);

const normalizedPresentation = normalizePresentationPreferences({
  themeMode: "LIGHT",
  accentPreset: "sakura",
  fontPreset: "serif",
  surfaceOpacity: 20,
  backgroundDim: 95,
  blurAmount: 12.4,
  reducedMotion: true,
  frames: {
    avatar: { x: -20, y: 125, scale: 4 },
    portrait: { x: 80, y: 40, scale: 1.35 }
  }
});
assert.deepEqual(normalizedPresentation.frames.avatar, { x: 0, y: 100, scale: 2 });
assert.deepEqual(normalizedPresentation.frames.background, { x: 50, y: 50, scale: 1 });
assert.equal(normalizedPresentation.themeMode, "light");
assert.equal(normalizedPresentation.accentPreset, "sakura");
assert.equal(normalizedPresentation.fontPreset, "serif");
assert.equal(normalizedPresentation.surfaceOpacity, 55);
assert.equal(normalizedPresentation.backgroundDim, 80);
assert.equal(normalizedPresentation.blurAmount, 12.4);
assert.equal(normalizedPresentation.reducedMotion, true);
assert.equal(resolveThemeMode("system", true), "light");
assert.equal(resolveThemeMode("system", false), "dark");
assert.equal(resolveThemeMode("invalid", false), "dark");
assert.deepEqual(presentationCssVariables(normalizedPresentation), {
  "--cc-surface-alpha": "0.55",
  "--cc-background-dim": "0.8",
  "--cc-backdrop-blur": "12px",
  "--cc-avatar-x": "0%",
  "--cc-avatar-y": "100%",
  "--cc-avatar-size": "200%",
  "--cc-portrait-shift-x": "15%",
  "--cc-portrait-shift-y": "-27%",
  "--cc-portrait-scale": "1.35",
  "--cc-background-x": "50%",
  "--cc-background-y": "50%",
  "--cc-background-scale": "1"
});
const movedPresentation = updatePresentationFrame(normalizedPresentation, "background", { x: 72.5, scale: 1.2 });
assert.deepEqual(movedPresentation.frames.background, { x: 72.5, y: 50, scale: 1.2 });
assert.deepEqual(resetPresentationFrame(movedPresentation, "background").frames.background, { x: 50, y: 50, scale: 1 });

const storageValues = new Map();
const fakeStorage = {
  getItem(key) { return storageValues.has(key) ? storageValues.get(key) : null; },
  setItem(key, value) { storageValues.set(key, String(value)); },
  removeItem(key) { storageValues.delete(key); }
};
bindInstanceStorage("presentation-smoke");
assert.equal(savePresentationPreferences("pack-a", movedPresentation, { storage: fakeStorage }), true);
assert.deepEqual(loadPresentationPreferences("pack-a", { storage: fakeStorage }), movedPresentation);
assert.equal(loadPresentationPreferences("pack-b", { storage: fakeStorage }).frames.background.x, 50);
assert.equal(loadPresentationPreferences("pack-b", { storage: fakeStorage }).themeMode, "light");
assert.equal(loadPresentationPreferences("pack-b", { storage: fakeStorage }).accentPreset, "sakura");
assert.equal(loadPresentationPreferences("pack-b", { storage: fakeStorage }).fontPreset, "serif");
assert.equal(loadPresentationPreferences("pack-b", { storage: fakeStorage }).reducedMotion, true);

const throwingStorage = {
  getItem() { return null; },
  setItem() { throw new Error("quota_exceeded"); }
};
assert.equal(savePresentationPreferences("pack-a", movedPresentation, { storage: throwingStorage }), false);

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

const abilitiesHtml = renderAbilities({ viewModel, actionStates: {}, phase: "ready" });
assert.match(abilitiesHtml, /她现在能做什么/);
assert.match(abilitiesHtml, /文件与工作区/);
assert.match(abilitiesHtml, /data-approval-mode="ask_each_time"/);
assert.match(abilitiesHtml, /data-approval-mode="trusted_auto_allow"/);
assert.match(abilitiesHtml, /硬安全边界始终保留/);
assert.match(abilitiesHtml, /AnySearch/);
assert.doesNotMatch(abilitiesHtml, /api_key|cached_path|local_path/);

const voiceHtml = renderVoice({ viewModel, actionStates: {}, phase: "ready" });
assert.match(voiceHtml, /让她听见，也让她说出来/);
assert.match(voiceHtml, /GPT-SoVITS/);
assert.match(voiceHtml, /data-action="voice\.setTtsEnabled"/);
assert.match(voiceHtml, /data-action-value-type="boolean"/);
assert.match(voiceHtml, /data-voice-range="volume"/);
assert.match(voiceHtml, /data-wake-word-form/);
assert.match(voiceHtml, /data-voice-preview-form/);
assert.match(voiceHtml, /打开角色工坊/);
assert.doesNotMatch(voiceHtml, /portrait|hero-image|character-preview/);

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
