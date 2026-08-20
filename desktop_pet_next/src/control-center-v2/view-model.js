const SUCCESS_STATUSES = new Set(["executed", "completed", "available", "already-playing", "already-paused", "already-stopped"]);

export function createControlCenterViewModel(rawSnapshot, runtimeSnapshot = null) {
  const raw = asObject(rawSnapshot);
  const runtime = asObject(raw.overviewRuntime);
  const characterRuntime = asObject(raw.characterRuntime);
  const live = asObject(runtimeSnapshot);
  const active = asObject(live.active);
  const petState = asObject(live.state);
  const expression = asObject(live.currentExpression);
  const musicRuntime = asObject(raw.musicRuntime);
  const nowPlaying = asObject(musicRuntime.nowPlaying);
  const connected = raw.sourceKind === "backend" && !raw.fallbackReason;
  const bridgeStatus = asObject(raw.controlCenterV2);
  const liveSnapshotStatus = text(bridgeStatus.liveSnapshotStatus) || "not-applicable";
  const displayName = text(characterRuntime.selectedPack) || text(petState.characterPackId) || "当前角色";
  const emotionName = text(expression.name) || text(asObject(runtime.emotion).name) || text(petState.currentEmotion);
  const portrait = safeAssetUrl(text(expression.image) || text(asObject(runtime.emotion).image) || firstActiveEmotionImage(characterRuntime));
  const hero = safeAssetUrl(text(characterRuntime.hero));
  const activity = deriveActivity(live, connected);
  const music = deriveMusic(nowPlaying, musicRuntime);
  const outputs = normalizeRecentOutputs(runtime.recentOutputs);
  const warnings = normalizeCharacterWarnings(characterRuntime);
  const instanceLabel = text(petState.instanceId) || text(raw.controlCenterRuntime?.health?.data?.instance_id) || "本地实例";

  return {
    shell: {
      connected,
      connectionStatus: connected ? "connected" : raw.fallbackReason ? "failed" : "disconnected",
      connectionLabel: connected ? "已连接" : raw.fallbackReason ? "连接失败" : "未连接",
      connectionDetail: connected && liveSnapshotStatus === "unavailable" ? "实时状态同步不可用" : "",
      liveSnapshotStatus,
      instanceLabel,
      generatedAt: text(raw.generatedAt) || new Date().toISOString()
    },
    character: {
      packId: text(characterRuntime.selectedPackId) || text(petState.characterPackId),
      displayName,
      outfit: text(petState.outfit),
      emotion: emotionName,
      visuals: {
        avatar: portrait,
        portrait,
        hero
      },
      resourceWarnings: warnings
    },
    activity,
    music,
    recentOutputs: outputs,
    abilities: Array.isArray(runtime.abilities) ? runtime.abilities.map(text).filter(Boolean) : [],
    actions: {
      "chat.new": { available: connected, reason: connected ? "" : "桌宠尚未连接" },
      "chat.stop": { available: connected && Boolean(active.sending || active.replyDisplayActive), reason: "当前没有进行中的回复" },
      "workspace.open": { available: true, reason: "" },
      "character.openWorkshop": { available: true, reason: "" },
      "character.openPackFolder": { available: true, reason: "" },
      "music.pause": { available: music.available, reason: "当前没有可控制的音乐" }
    }
  };
}

export function normalizeActionPresentation(result) {
  const value = asObject(result);
  const status = text(value.status) || (value.ok ? "executed" : "failed");
  if (value.ok && (SUCCESS_STATUSES.has(status) || status === "handled")) {
    return { phase: "confirmed", label: "已完成", detail: "" };
  }
  if (status === "execution_unknown" || status === "unknown") {
    const reason = text(value.reason) || text(value.error);
    return {
      phase: "unknown",
      label: "已发送，未确认",
      detail: reason === "runtime_state_not_confirmed" ? "指令已发出，但没有在时限内观察到状态变化" : reason
    };
  }
  if (status === "not-implemented" || status === "not-available") {
    return { phase: "failed", label: "当前不可用", detail: text(value.reason) || "该入口尚未接通" };
  }
  if (value.ok) {
    return { phase: "confirmed", label: status === "mocked" ? "仅模拟" : "已完成", detail: "" };
  }
  return { phase: "failed", label: "操作失败", detail: text(value.reason) || text(value.error) || status };
}

export function isObservedActionConfirmation(actionId, beforeSnapshot, afterSnapshot) {
  const before = asObject(beforeSnapshot);
  const after = asObject(afterSnapshot);
  const beforeState = asObject(before.state);
  const afterState = asObject(after.state);
  const beforeActive = asObject(before.active);
  const afterActive = asObject(after.active);

  if (actionId === "chat.new") {
    const beforeSession = text(beforeState.sessionId);
    const afterSession = text(afterState.sessionId);
    return Boolean(beforeSession && afterSession && beforeSession !== afterSession);
  }
  if (actionId === "chat.stop") {
    const wasActive = Boolean(beforeActive.sending || beforeActive.replyDisplayActive);
    return wasActive && !afterActive.sending && !afterActive.replyDisplayActive;
  }
  if (actionId === "music.pause") {
    const beforePlayback = `${Boolean(beforeActive.musicPlaying)}:${Boolean(beforeActive.musicPaused)}`;
    const afterPlayback = `${Boolean(afterActive.musicPlaying)}:${Boolean(afterActive.musicPaused)}`;
    return beforePlayback !== afterPlayback;
  }
  return true;
}

function deriveActivity(runtimeSnapshot, connected) {
  if (!connected) return { phase: "waiting", label: "等待桌宠连接", detail: "连接后会显示真实执行状态" };
  const active = asObject(runtimeSnapshot.active);
  const mode = text(runtimeSnapshot.runtimeMode).toLowerCase();
  const status = text(runtimeSnapshot.runtimeStatus);
  if (active.sending) return { phase: mode.includes("tool") ? "using_tool" : "thinking", label: status || "正在处理请求" };
  if (active.speaking) return { phase: "delivering", label: status || "正在回应" };
  if (active.voiceInput === "processing") return { phase: "thinking", label: status || "正在识别语音" };
  if (active.proactiveWakeRunning) return { phase: "thinking", label: status || "正在准备主动搭话" };
  return { phase: "idle", label: status || "空闲，随时可以开始" };
}

function deriveMusic(nowPlaying, musicRuntime) {
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

function normalizeRecentOutputs(value) {
  return (Array.isArray(value) ? value : []).slice(0, 3).map((item) => ({
    id: text(item?.id) || text(item?.handle) || text(item?.title),
    title: text(item?.title) || "未命名文件",
    detail: text(item?.subtitle) || text(item?.format) || text(item?.kind),
    status: text(item?.status) || "available"
  }));
}

function normalizeCharacterWarnings(characterRuntime) {
  const warning = asObject(characterRuntime.warning);
  if (!text(warning.headline)) return [];
  return /良好|已加载/.test(text(warning.headline)) ? [] : [text(warning.headline), text(warning.body)].filter(Boolean);
}

function firstActiveEmotionImage(characterRuntime) {
  const emotions = Array.isArray(characterRuntime.emotions) ? characterRuntime.emotions : [];
  const current = emotions.find((item) => item?.current) || emotions[0];
  return text(current?.image);
}

function safeAssetUrl(value) {
  const raw = text(value);
  if (!raw) return "";
  if (/^\/(?!\/)/.test(raw) || /^(https?:|blob:|asset:)/i.test(raw) || /^data:image\//i.test(raw)) return raw;
  return "";
}

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function text(value) {
  return typeof value === "string" ? value.trim() : value == null ? "" : String(value).trim();
}
