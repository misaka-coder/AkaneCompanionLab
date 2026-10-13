import { asObject, safeAssetUrl, text } from "./shared.js";
import { normalizeScreenObservationSettings } from "../../screen-observation.js";

export function createPerceptionViewModel(live, connected) {
  const petState = asObject(live.state);
  const active = asObject(live.active);
  return {
    available: connected && Object.keys(petState).length > 0,
    ...normalizeScreenObservationSettings(petState),
    screenVisionEnabled: Boolean(petState.screenVisionEnabled),
    proactiveWakeEnabled: Boolean(petState.proactiveWakeEnabled),
    proactiveWakeIntervalSec: Number(petState.proactiveWakeIntervalSec) || 30,
    status: text(active.screenVision) || "off",
    error: text(active.screenVisionError),
    bufferedFrames: Number(active.screenVisionFrameBufferSize) || 0,
    evaluating: Boolean(active.proactiveWakeRunning),
    preparation: text(active.proactivePreparation),
    preparationError: text(active.proactivePreparationError)
  };
}

export function deriveSetupReadiness({ connected, packId, displayName, characterWarnings, model, abilities, voice }) {
  const hasActiveCharacter = Boolean(packId || (displayName && displayName !== "当前角色"));
  const characterReady = connected && hasActiveCharacter && !characterWarnings.length;
  const modelReady = connected && model.configured && Boolean(model.chatModel);
  const permissionsReady = connected && abilities.available && abilities.policy.families.length === 2;
  const skillsReady = connected && abilities.skills.status === "ready" && abilities.skills.total > 0;
  const voiceReady = connected && Boolean(voice.tts.provider.ready);
  const items = [
    {
      id: "character",
      label: "角色包",
      detail: !connected ? "等待桌宠连接" : !hasActiveCharacter ? "还没有选择角色包" : characterWarnings[0] || `${displayName} 已加载`,
      status: characterReady ? "ready" : "attention",
      statusLabel: characterReady ? "已就绪" : "去处理",
      page: "appearance",
      optional: false
    },
    {
      id: "model",
      label: "模型服务",
      detail: modelReady ? [model.providerId, model.chatModel].filter(Boolean).join(" · ") : model.available ? "配置尚未完整保存" : "等待配置可用模型",
      status: modelReady ? "ready" : "attention",
      statusLabel: modelReady ? "已连接" : "去配置",
      page: "model",
      optional: false
    },
    {
      id: "permissions",
      label: "能力与权限",
      detail: permissionsReady ? `${abilities.policy.label} · ${abilities.safetyStatus}` : "能力目录或审批策略尚未同步",
      status: permissionsReady ? "ready" : "attention",
      statusLabel: permissionsReady ? "已生效" : "去检查",
      page: "abilities",
      optional: false
    },
    {
      id: "skills",
      label: "Skill 操作手册",
      detail: skillsReady ? `${abilities.skills.total} 个可用 · ${abilities.skills.managed} 个自定义` : "可按需添加自己的操作手册",
      status: skillsReady ? "ready" : "optional",
      statusLabel: skillsReady ? "可使用" : "可选增强",
      page: "abilities",
      optional: true
    },
    {
      id: "voice",
      label: "语音与声线",
      detail: voiceReady ? `${voice.tts.provider.name} · ${voice.tts.provider.statusLabel}` : voice.tts.provider.reason || "不使用语音也不影响文字聊天",
      status: voiceReady ? "ready" : "optional",
      statusLabel: voiceReady ? "可使用" : "可选增强",
      page: "voice",
      optional: true
    }
  ];
  const coreItems = items.filter((item) => !item.optional);
  const coreReadyCount = coreItems.filter((item) => item.status === "ready").length;
  const optionalReadyCount = items.filter((item) => item.optional && item.status === "ready").length;
  return {
    items,
    coreReadyCount,
    coreTotal: coreItems.length,
    coreComplete: coreReadyCount === coreItems.length,
    optionalReadyCount,
    optionalTotal: items.length - coreItems.length
  };
}

export function deriveActivity(runtimeSnapshot, connected) {
  if (!connected) return { phase: "waiting", label: "等待桌宠连接", detail: "连接后会显示真实执行状态" };
  const active = asObject(runtimeSnapshot.active);
  const mode = text(runtimeSnapshot.runtimeMode).toLowerCase();
  const status = text(runtimeSnapshot.runtimeStatus);
  if (active.proactiveWakeRunning) return { phase: "observing", label: "正在评估是否开口" };
  if (active.sending) return { phase: mode.includes("tool") ? "using_tool" : "thinking", label: status || "正在处理请求" };
  if (active.speaking) return { phase: "delivering", label: status || "正在回应" };
  if (active.voiceInput === "opening") return { phase: "thinking", label: status || "正在打开麦克风" };
  if (active.voiceInput === "processing") return { phase: "thinking", label: status || "正在识别语音" };
  return { phase: "idle", label: status || "空闲，随时可以开始" };
}

export function deriveMusic(nowPlaying, musicRuntime) {
  const title = text(nowPlaying.title);
  const available = Boolean(title && title !== "暂无播放");
  const playback = nowPlaying.playing ? "playing" : nowPlaying.paused ? "paused" : available ? "stopped" : "unknown";
  return {
    available,
    title: available ? title : "",
    artist: text(nowPlaying.artist),
    cover: safeAssetUrl(text(nowPlaying.cover)),
    playback,
    detail: text(musicRuntime.bottomStatus)
  };
}

export function normalizeRecentOutputs(value) {
  return (Array.isArray(value) ? value : []).slice(0, 3).map((item) => ({
    id: text(item?.id) || text(item?.handle) || text(item?.title),
    title: text(item?.title) || "未命名文件",
    detail: text(item?.subtitle) || text(item?.format) || text(item?.kind),
    status: text(item?.status) || "available"
  }));
}
