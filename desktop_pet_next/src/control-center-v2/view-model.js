import { MODEL_SERVICE_ACTIONS, normalizeModelServiceRuntime } from "./model-service.js";

const SUCCESS_STATUSES = new Set(["executed", "completed", "available", "connected", "configured", "already-playing", "already-paused", "already-stopped"]);

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
  const availablePacks = normalizeAvailablePacks(characterRuntime.availablePacks, text(characterRuntime.selectedPackId) || text(petState.characterPackId));
  const outfits = normalizeVisualChoices(characterRuntime.outfits, text(petState.outfit), "服装");
  const emotions = normalizeVisualChoices(characterRuntime.emotions, emotionName || text(petState.currentEmotion), "表情");
  const activity = deriveActivity(live, connected);
  const music = deriveMusic(nowPlaying, musicRuntime);
  const outputs = normalizeRecentOutputs(runtime.recentOutputs);
  const warnings = normalizeCharacterWarnings(characterRuntime);
  const abilities = normalizeAbilitiesRuntime(raw.abilitiesRuntime, connected);
  const model = normalizeModelServiceRuntime(raw.modelRuntime, connected);
  const voice = normalizeVoiceRuntime(raw.voiceRuntime, live, connected);
  const system = normalizeSystemRuntime(raw, live, {
    connected,
    liveSnapshotStatus,
    characterWarnings: warnings,
    voice
  });
  const instanceLabel = text(petState.instanceId) || text(raw.controlCenterRuntime?.health?.data?.instance_id) || "本地实例";
  const chat = normalizeChatSession(raw.chatSession, {
    sessionId: text(petState.sessionId),
    characterName: displayName,
    characterAvatar: portrait
  });
  const bots = normalizeBotCatalog(raw.botCatalog, text(petState.boundBotId));

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
      resourceWarnings: warnings,
      availablePacks,
      outfits,
      emotions,
      packInfo: normalizePackInfo(characterRuntime.packInfo),
      completeness: finitePercent(characterRuntime.completeness)
    },
    activity,
    chat,
    bots,
    music,
    recentOutputs: outputs,
    abilities,
    model,
    voice,
    system,
    abilityLabels: Array.isArray(runtime.abilities) ? runtime.abilities.map(text).filter(Boolean) : [],
    actions: {
      "chat.new": { available: connected, reason: connected ? "" : "桌宠尚未连接" },
      "chat.send": {
        available: connected && liveSnapshotStatus === "connected" && !Boolean(active.sending),
        reason: !connected ? "桌宠尚未连接" : liveSnapshotStatus !== "connected" ? "请在桌面端窗口中发送" : "正在回复，请稍后再发"
      },
      "chat.stop": { available: connected && Boolean(active.sending || active.replyDisplayActive), reason: "当前没有进行中的回复" },
      "workspace.open": { available: true, reason: "" },
      "settings.selectBot": { available: connected && liveSnapshotStatus === "connected" && bots.items.length > 1, reason: connected ? "没有其他可切换的 Bot" : "桌宠尚未连接" },
      "character.openWorkshop": { available: true, reason: "" },
      "character.openPackFolder": { available: true, reason: "" },
      "character.selectPack": { available: connected && availablePacks.length > 0, reason: connected ? "没有可切换的角色包" : "桌宠尚未连接" },
      "character.setOutfit": { available: connected && outfits.length > 0, reason: connected ? "当前角色没有服装资源" : "桌宠尚未连接" },
      "character.previewEmotion": { available: connected && emotions.length > 0, reason: connected ? "当前服装没有表情资源" : "桌宠尚未连接" },
      "character.refresh": { available: connected, reason: "桌宠尚未连接" },
      "abilities.approvalPolicy.save": { available: connected && abilities.policy.availableModes.length > 0, reason: connected ? "审批策略暂不可用" : "桌宠尚未连接" },
      "abilities.approvalRequest.decide": { available: connected && abilities.approvalRequests.length > 0, reason: connected ? "当前没有待确认请求" : "桌宠尚未连接" },
      "abilities.skills.openFolder": { available: true, reason: "" },
      "abilities.provider.config.save": capabilityActionAvailability(connected, abilities.providers),
      "abilities.provider.healthCheck": capabilityActionAvailability(connected, abilities.providers),
      "abilities.mcp.config.save": capabilityActionAvailability(connected, abilities.mcpServers),
      "abilities.mcp.discover": capabilityActionAvailability(connected, abilities.mcpServers),
      "abilities.workflow.config.save": capabilityActionAvailability(connected, abilities.workflows),
      "abilities.workflow.validate": capabilityActionAvailability(connected, abilities.workflows),
      [MODEL_SERVICE_ACTIONS.models]: { available: model.available, reason: model.connected ? "模型配置接口暂不可用" : "桌宠尚未连接" },
      [MODEL_SERVICE_ACTIONS.test]: { available: model.available, reason: model.connected ? "模型配置接口暂不可用" : "桌宠尚未连接" },
      [MODEL_SERVICE_ACTIONS.save]: { available: model.available, reason: model.connected ? "模型配置接口暂不可用" : "桌宠尚未连接" },
      "voice.test": voiceActionAvailability(voice.controlsAvailable && !voice.speaking, "当前正在播放语音"),
      "voice.stop": voiceActionAvailability(voice.controlsAvailable && voice.speaking, "当前没有正在播放的语音"),
      "voice.previewPlay": voiceActionAvailability(voice.controlsAvailable && !voice.speaking, "当前正在播放语音"),
      "voice.setTtsEnabled": voiceActionAvailability(voice.controlsAvailable),
      "voice.setAsrEnabled": voiceActionAvailability(voice.controlsAvailable),
      "voice.setVolume": voiceActionAvailability(voice.controlsAvailable),
      "voice.setSpeed": voiceActionAvailability(voice.controlsAvailable),
      "voice.setWakeWord": voiceActionAvailability(voice.controlsAvailable),
      "voice.setWakeSensitivity": voiceActionAvailability(voice.controlsAvailable),
      "perception.runDiagnostics": voiceActionAvailability(system.controlsAvailable),
      "advanced.setHitTestEnabled": voiceActionAvailability(system.controlsAvailable),
      "advanced.setHitboxOverlay": voiceActionAvailability(system.controlsAvailable),
      "advanced.resetWindow": voiceActionAvailability(system.controlsAvailable),
      "music.pause": { available: music.available, reason: "当前没有可控制的音乐" },
      "window.minimize": { available: true, reason: "" },
      "window.maximize": { available: true, reason: "" },
      "window.close": { available: true, reason: "" }
    }
  };
}

export function normalizeActionPresentation(result) {
  const value = asObject(result);
  const status = text(value.status) || (value.ok ? "executed" : "failed");
  if (value.ok && status === "executed" && text(value.actionId) === "advanced.resetWindow") {
    return { phase: "unknown", label: "重置请求已发送", detail: "请观察桌宠窗口是否已经恢复" };
  }
  if (value.ok && (SUCCESS_STATUSES.has(status) || status === "handled")) {
    return { phase: "confirmed", label: "已完成", detail: "" };
  }
  if (status === "execution_unknown" || status === "unknown") {
    const reason = friendlyActionDetail(text(value.reason) || text(value.error));
    return {
      phase: "unknown",
      label: "已发送，未确认",
      detail: reason === "runtime_state_not_confirmed" ? "指令已发出，但没有在时限内观察到状态变化" : reason
    };
  }
  if (status === "not-implemented" || status === "not-available") {
    return { phase: "failed", label: "当前不可用", detail: friendlyActionDetail(text(value.reason)) || "该入口尚未接通" };
  }
  if (value.ok) {
    return { phase: "confirmed", label: status === "mocked" ? "仅模拟" : "已完成", detail: "" };
  }
  return { phase: "failed", label: "操作失败", detail: friendlyActionDetail(text(value.reason) || text(value.error) || status) };
}

function friendlyActionDetail(value) {
  return {
    admin_auth_required: "此操作只能从桌面控制中心执行",
    request_failed: "请求没有完成，请检查服务连接后重试",
    "request-failed": "请求没有完成，请检查服务连接后重试"
  }[value] || value;
}

export function isObservedActionConfirmation(actionId, beforeSnapshot, afterSnapshot, payload = {}) {
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
  if (actionId === "chat.send") {
    const commandResult = asObject(after.settingsCommandResult);
    return (
      text(commandResult.command) === "sendChatMessage" &&
      text(commandResult.status) === "accepted" &&
      Boolean(afterActive.sending)
    );
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
  if (actionId === "voice.test" || actionId === "voice.previewPlay") {
    return !Boolean(beforeActive.speaking) && Boolean(afterActive.speaking);
  }
  if (actionId === "voice.stop") {
    return Boolean(beforeActive.speaking) && !Boolean(afterActive.speaking);
  }
  if (actionId === "voice.setTtsEnabled") {
    return typeof afterState.voiceEnabled === "boolean" && afterState.voiceEnabled === Boolean(payload.value);
  }
  if (actionId === "voice.setAsrEnabled") {
    return typeof afterState.voiceInputEnabled === "boolean" && afterState.voiceInputEnabled === Boolean(payload.value);
  }
  if (actionId === "voice.setVolume") {
    return numbersClose(afterState.voiceVolume, payload.value);
  }
  if (actionId === "voice.setSpeed") {
    return text(afterState.voiceSpeed) === text(payload.value);
  }
  if (actionId === "voice.setWakeWord") {
    return text(afterState.wakeWord) === text(payload.value);
  }
  if (actionId === "voice.setWakeSensitivity") {
    return text(afterState.wakeSensitivity) === text(payload.value);
  }
  if (actionId === "advanced.setHitTestEnabled") {
    return typeof afterState.hitTestEnabled === "boolean" && afterState.hitTestEnabled === Boolean(payload.value);
  }
  if (actionId === "advanced.setHitboxOverlay") {
    return typeof afterState.hitboxOverlay === "boolean" && afterState.hitboxOverlay === Boolean(payload.value);
  }
  if (actionId === "settings.selectBot") {
    return Boolean(text(afterState.boundBotId) && text(afterState.boundBotId) === text(payload.value));
  }
  const expected = text(payload.value);
  if (actionId === "character.selectPack") {
    const beforePack = text(beforeState.characterPackId) || text(asObject(before.character).packId);
    const afterPack = text(afterState.characterPackId) || text(asObject(after.character).packId);
    return Boolean(afterPack && afterPack !== beforePack && (!expected || afterPack === expected));
  }
  if (actionId === "character.setOutfit") {
    const beforeOutfit = text(beforeState.outfit);
    const afterOutfit = text(afterState.outfit);
    return Boolean(afterOutfit && afterOutfit !== beforeOutfit && (!expected || afterOutfit === expected));
  }
  if (actionId === "character.previewEmotion") {
    const beforeExpression = text(asObject(before.currentExpression).id) || text(asObject(before.currentExpression).name) || text(beforeState.currentEmotion);
    const afterExpression = text(asObject(after.currentExpression).id) || text(asObject(after.currentExpression).name) || text(afterState.currentEmotion);
    return Boolean(afterExpression && afterExpression !== beforeExpression && (!expected || afterExpression === expected));
  }
  return true;
}

function normalizeSystemRuntime(raw, live, options = {}) {
  const advanced = asObject(raw.advancedRuntime);
  const diagnostics = asObject(advanced.diagnostics);
  const liveState = asObject(live.state);
  const liveResource = asObject(live.resource);
  const control = asObject(raw.controlCenterRuntime);
  const connected = Boolean(options.connected);
  const services = normalizeSystemServices(control);
  const metrics = normalizeSystemMetrics(advanced, diagnostics, connected);
  const controlsAvailable = connected && Object.keys(liveState).length > 0;
  const settings = normalizeSystemSettings(advanced.coreSettings, liveState);
  const issues = [];

  if (!connected) {
    issues.push(systemIssue("danger", "桌宠服务未连接", "请确认本地后端已经启动，然后重新检查。"));
  }
  if (options.liveSnapshotStatus === "unavailable") {
    issues.push(systemIssue("warning", "桌面实时状态不可用", "后端仍可读取，但窗口状态和本地操作暂时无法确认。"));
  }
  const resourceHealth = text(liveResource.health);
  if (resourceHealth && !["online", "ok", "ready"].includes(resourceHealth.toLowerCase())) {
    issues.push(systemIssue("warning", "角色资源没有完全就绪", text(liveResource.healthMessage) || resourceHealth));
  }
  for (const warning of Array.isArray(options.characterWarnings) ? options.characterWarnings : []) {
    issues.push(systemIssue("warning", warning, "可前往角色与外观页检查资源。"));
  }
  for (const provider of [options.voice?.tts?.provider, options.voice?.asr?.provider]) {
    if (!provider || provider.ready || provider.status === "unknown") continue;
    issues.push(systemIssue(provider.status === "unavailable" ? "danger" : "warning", `${provider.name}：${provider.statusLabel}`, provider.reason));
  }
  for (const service of services) {
    if (service.ready || service.optional) continue;
    issues.push(systemIssue("warning", `${service.label}不可用`, service.detail || service.statusLabel));
  }

  const dedupedIssues = dedupeSystemIssues(issues).slice(0, 8);
  const overallTone = !connected || dedupedIssues.some((item) => item.tone === "danger")
    ? "danger"
    : dedupedIssues.length
      ? "warning"
      : "good";
  return {
    available: connected || Object.keys(advanced).length > 0,
    controlsAvailable,
    overallTone,
    overallLabel: overallTone === "good" ? "运行正常" : overallTone === "warning" ? "需要留意" : "连接异常",
    overallDetail: overallTone === "good"
      ? controlsAvailable ? "核心服务与桌面状态均已同步" : "后端检查正常；桌面操作仅在应用内可用"
      : dedupedIssues[0]?.title || "部分状态暂不可用",
    metrics,
    services,
    settings,
    issues: dedupedIssues,
    details: normalizeSystemDetails(raw, live, metrics),
    hasEventSource: false
  };
}

function normalizeSystemMetrics(advanced, diagnostics, connected) {
  const strip = asObject(advanced.systemStrip);
  const detailMetrics = asObject(diagnostics.metrics);
  const candidates = [
    metricFromMap("应用状态", detailMetrics, connected ? "运行中" : "等待连接", connected ? "good" : "danger"),
    metricFromMap("CPU", strip),
    metricFromMap("内存", strip),
    metricFromMap("内存占用", detailMetrics),
    metricFromMap("网络", strip, connected ? "良好" : "离线", connected ? "good" : "danger")
  ].filter(Boolean);
  const seen = new Set();
  return candidates.filter((item) => {
    const key = `${item.label}:${item.value}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 5);
}

function metricFromMap(label, source, fallbackValue = "", fallbackTone = "muted") {
  const entry = asObject(source[label]);
  const value = text(entry.value) || fallbackValue;
  if (!value) return null;
  return {
    label,
    value,
    tone: normalizeSystemTone(entry.tone || fallbackTone)
  };
}

function normalizeSystemServices(control) {
  const definitions = [
    ["health", "核心服务", false],
    ["diagnostics", "诊断接口", false],
    ["workspace", "工作区", false],
    ["metrics", "运行指标", true],
    ["capabilitiesCatalog", "能力目录", true]
  ];
  return definitions.map(([id, label, optional]) => {
    const source = asObject(control[id]);
    if (!Object.keys(source).length) return null;
    const ready = source.ok === true;
    const status = text(source.status);
    return {
      id,
      label,
      ready,
      optional,
      statusLabel: ready ? "可用" : status ? `状态 ${status}` : "不可用",
      detail: ready ? "最近一次读取成功" : "重新检查后仍不可用时，请查看服务启动状态"
    };
  }).filter(Boolean);
}

function normalizeSystemSettings(value, liveState) {
  const runtimeById = new Map((Array.isArray(value) ? value : []).map((item) => [text(item?.id), asObject(item)]));
  return [
    {
      id: "hitTest",
      title: "桌宠点击区域",
      description: "让鼠标能够命中桌宠的可交互区域。",
      actionId: "advanced.setHitTestEnabled",
      enabled: typeof liveState.hitTestEnabled === "boolean" ? liveState.hitTestEnabled : Boolean(runtimeById.get("hitTest")?.enabled)
    },
    {
      id: "hitbox",
      title: "显示点击边界",
      description: "排查点不到或误触时临时显示真实交互范围。",
      actionId: "advanced.setHitboxOverlay",
      enabled: typeof liveState.hitboxOverlay === "boolean" ? liveState.hitboxOverlay : Boolean(runtimeById.get("hitbox")?.enabled)
    }
  ];
}

function normalizeSystemDetails(raw, live, metrics) {
  const resource = asObject(live.resource);
  return [
    { label: "实例", value: text(asObject(live.state).instanceId) || "本地实例" },
    { label: "桌面运行状态", value: text(live.runtimeStatus) || "未提供" },
    { label: "角色资源", value: text(resource.health) || "未提供" },
    { label: "资源来源", value: text(resource.source) || "未提供" },
    { label: "最近同步", value: formatSystemTime(raw.generatedAt) },
    ...metrics.filter((item) => !["应用状态", "网络"].includes(item.label)).map((item) => ({ label: item.label, value: item.value }))
  ].filter((item, index, items) => item.value && items.findIndex((other) => other.label === item.label) === index);
}

function systemIssue(tone, title, detail = "") {
  return { tone: normalizeSystemTone(tone), title: text(title), detail: text(detail) };
}

function dedupeSystemIssues(value) {
  const seen = new Set();
  return value.filter((item) => {
    if (!item.title || seen.has(item.title)) return false;
    seen.add(item.title);
    return true;
  });
}

function normalizeSystemTone(value) {
  const tone = text(value).toLowerCase();
  if (["green", "good", "ready", "success"].includes(tone)) return "good";
  if (["danger", "error", "failed", "red"].includes(tone)) return "danger";
  if (["warning", "warn", "orange"].includes(tone)) return "warning";
  return "muted";
}

function formatSystemTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "未提供";
  return date.toLocaleString("zh-CN", { hour12: false });
}

function normalizeVoiceRuntime(value, live, connected) {
  const source = asObject(value);
  const tts = asObject(source.tts);
  const asr = asObject(source.asr);
  const liveState = asObject(live.state);
  const active = asObject(live.active);
  const providerTts = normalizeVoiceProvider(tts.providerStatus, "TTS 语音输出");
  const providerAsr = normalizeVoiceProvider(asr.providerStatus, "ASR 语音输入");
  const liveVolume = Number(liveState.voiceVolume);
  const volume = Number.isFinite(liveVolume)
    ? liveVolume * 100
    : finiteNumber(tts.volume, 80);
  const liveSnapshotAvailable = Boolean(connected && Object.keys(liveState).length);
  return {
    available: connected && Object.keys(source).length > 0,
    controlsAvailable: liveSnapshotAvailable,
    controlsUnavailableReason: connected ? "请在桌面端控制中心中调整" : "桌宠尚未连接",
    speaking: Boolean(active.speaking),
    inputState: text(active.voiceInput) || (asr.enabled ? "idle" : "disabled"),
    queueLength: Math.max(0, Math.round(finiteNumber(asObject(live.tts).queueLength, 0))),
    tts: {
      enabled: typeof liveState.voiceEnabled === "boolean" ? liveState.voiceEnabled : Boolean(tts.enabled),
      volume: Math.max(0, Math.min(100, Math.round(volume))),
      speed: text(liveState.voiceSpeed) || text(tts.speed) || "1.00x",
      provider: providerTts
    },
    asr: {
      enabled: typeof liveState.voiceInputEnabled === "boolean" ? liveState.voiceInputEnabled : Boolean(asr.enabled),
      provider: providerAsr
    },
    wakeWord: text(liveState.wakeWord) || text(source.wakeWord) || "Akane",
    wakeSensitivity: text(liveState.wakeSensitivity) || text(source.wakeSensitivity) || "中等",
    diagnostics: normalizeVoiceDiagnostics(source.diagnostics)
  };
}

function normalizeVoiceProvider(value, fallbackLabel) {
  const source = asObject(value);
  const status = text(source.status);
  const activeName = text(source.activeProviderName);
  return {
    name: activeName || text(source.requestedProviderName) || fallbackLabel,
    status: status || (activeName ? "ready" : "unknown"),
    statusLabel: text(source.statusLabel) || (activeName ? "已就绪" : "待确认"),
    reason: text(source.reasonLabel) || text(source.reason),
    ready: status === "ready" || Boolean(!status && activeName)
  };
}

function normalizeVoiceDiagnostics(value) {
  return (Array.isArray(value) ? value : []).slice(0, 8).map((item) => {
    const source = asObject(item);
    return {
      label: text(source.label),
      value: text(source.value),
      tone: ["good", "warning", "danger", "muted"].includes(text(source.tone)) ? text(source.tone) : "muted"
    };
  }).filter((item) => item.label && item.value);
}

function voiceActionAvailability(available, unavailableReason = "请在桌面端控制中心中调整") {
  return { available: Boolean(available), reason: available ? "" : unavailableReason };
}

function numbersClose(left, right) {
  const a = Number(left);
  const b = Number(right);
  return Number.isFinite(a) && Number.isFinite(b) && Math.abs(a - b) < 0.001;
}

function finiteNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function normalizeChatSession(value, options = {}) {
  const source = asObject(value);
  const session = asObject(source.session);
  const expectedSessionId = text(options.sessionId);
  const actualSessionId = text(session.session_id) || text(session.sessionId);
  const matchesCurrentSession = !expectedSessionId || !actualSessionId || expectedSessionId === actualSessionId;
  const messages = matchesCurrentSession
    ? (Array.isArray(source.messages) ? source.messages : [])
      .map((item) => normalizeChatMessage(item))
      .filter(Boolean)
    : [];
  return {
    sessionId: actualSessionId || expectedSessionId,
    title: matchesCurrentSession
      ? text(session.display_title) || text(session.displayTitle) || "当前对话"
      : "正在切换会话",
    messages,
    characterName: text(options.characterName) || "桌宠",
    characterAvatar: safeAssetUrl(text(options.characterAvatar)),
    hasHistory: messages.length > 0,
    matchesCurrentSession
  };
}

function normalizeChatMessage(value) {
  const source = asObject(value);
  const role = text(source.role).toLowerCase();
  const content = text(source.content);
  if (!content || !["user", "assistant"].includes(role)) return null;
  const metadata = asObject(source.memory_metadata);
  const timestamp = Number(source.timestamp);
  return {
    id: text(source.source_id) || `${role}-${text(source.seq_no)}-${timestamp || 0}`,
    role,
    content,
    timestamp: Number.isFinite(timestamp) && timestamp > 0 ? timestamp : 0,
    intermediate: text(metadata.turn_role).toLowerCase() === "intermediate"
  };
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

function normalizeAbilitiesRuntime(value, connected) {
  const source = asObject(value);
  const overview = asObject(source.overview);
  const safety = asObject(source.safety);
  const policy = asObject(safety.approvalPolicy);
  const defaultMode = ["ask_each_time", "trusted_auto_allow"].includes(text(policy.defaultMode))
    ? text(policy.defaultMode)
    : "ask_each_time";
  const availableModes = (Array.isArray(policy.availableModes) ? policy.availableModes : [])
    .map((item) => {
      const entry = asObject(item);
      const id = text(entry.id);
      if (!["ask_each_time", "trusted_auto_allow"].includes(id)) return null;
      return {
        id,
        label: text(entry.label) || (id === "trusted_auto_allow" ? "完全访问" : "请求批准"),
        summary: text(entry.summary)
      };
    })
    .filter(Boolean);
  if (!availableModes.length && connected) {
    availableModes.push(
      { id: "ask_each_time", label: "请求批准", summary: "高风险动作执行前先请求确认。" },
      { id: "trusted_auto_allow", label: "完全访问", summary: "自动允许高风险能力，但保留路径、密钥和边界校验。" }
    );
  }
  return {
    available: connected && Object.keys(source).length > 0,
    availability: finitePercent(overview.availability),
    note: text(overview.note),
    stats: normalizeLabelValueRows(overview.stats, 4),
    modules: (Array.isArray(source.modules) ? source.modules : []).map((item) => {
      const entry = asObject(item);
      return {
        title: text(entry.title),
        description: text(entry.description),
        permission: text(entry.permission),
        count: text(entry.count),
        tone: text(entry.tone) || "blue",
        statusLabel: text(entry.statusLabel) || "待同步",
        statusTone: text(entry.statusTone) || "warning"
      };
    }).filter((item) => item.title),
    policy: {
      defaultMode,
      label: text(policy.label) || (defaultMode === "trusted_auto_allow" ? "完全访问" : "请求批准"),
      summary: text(policy.summary),
      availableModes
    },
    safetyStatus: text(safety.status) || (connected ? "已生效" : "待连接"),
    safetyItems: normalizeLabelValueRows(safety.items, 8),
    approvalRequests: normalizeApprovalRequests(safety.approvalRequests),
    skills: normalizeAbilitySkills(source.skills),
    providers: normalizeAbilityProviders(source.providers),
    mcpServers: normalizeAbilityMcpServers(source.mcpServers),
    workflows: normalizeAbilityWorkflows(source.workflows),
    integrations: normalizeAbilityIntegrations(source),
    calls: (Array.isArray(source.calls) ? source.calls : []).slice(0, 5).map((item) => {
      const entry = asObject(item);
      return {
        time: text(entry.time),
        module: text(entry.module),
        description: text(entry.description),
        status: text(entry.status),
        method: text(entry.method)
      };
    }).filter((item) => item.module || item.description)
  };
}

function normalizeAbilitySkills(value) {
  const source = asObject(value);
  return {
    status: text(source.status) || "unavailable",
    catalogRevision: text(source.catalogRevision),
    total: Number(source.total || 0),
    bundled: Number(source.bundled || 0),
    managed: Number(source.managed || 0),
    entries: (Array.isArray(source.entries) ? source.entries : []).map((item) => {
      const entry = asObject(item);
      return {
        name: text(entry.name),
        description: text(entry.description),
        source: text(entry.source) || "bundled",
        revision: text(entry.revision),
        requiredTools: (Array.isArray(entry.requiredTools) ? entry.requiredTools : []).map(text).filter(Boolean),
        resourceCount: Number(entry.resourceCount || 0)
      };
    }).filter((item) => item.name && item.description),
    diagnostics: (Array.isArray(source.diagnostics) ? source.diagnostics : []).map((item) => {
      const entry = asObject(item);
      return {
        name: text(entry.name),
        reason: text(entry.reason),
        fallback: text(entry.fallback)
      };
    }).filter((item) => item.name || item.reason)
  };
}

function normalizeApprovalRequests(value) {
  return (Array.isArray(value) ? value : []).map((item) => {
    const entry = asObject(item);
    return {
      requestId: text(entry.requestId),
      title: text(entry.title) || "能力请求",
      summary: text(entry.summary) || text(entry.approvalReason),
      risk: text(entry.risk) || "medium",
      requestedBy: text(entry.requestedBy) || "Akane",
      createdAt: text(entry.createdAt),
      status: text(entry.status) || "pending"
    };
  }).filter((item) => item.requestId && item.status === "pending");
}

function normalizeAbilityProviders(value) {
  return (Array.isArray(value) ? value : []).map((item) => {
    const entry = asObject(item);
    return {
      id: text(entry.id),
      title: text(entry.title) || text(entry.name) || "本地服务",
      description: text(entry.description),
      status: text(entry.status) || "missing_config",
      statusLabel: text(entry.statusLabel) || "待配置",
      statusTone: text(entry.statusTone) || "warning",
      reason: text(entry.reason),
      enabled: Boolean(entry.enabled),
      configured: Boolean(entry.configured),
      endpoint: text(entry.endpoint),
      defaultEndpoint: text(entry.defaultEndpoint),
      usedByLabel: text(entry.usedByLabel),
      actionsEnabled: entry.actionsEnabled !== false && Boolean(text(entry.id))
    };
  }).filter((item) => item.title);
}

function normalizeAbilityMcpServers(value) {
  return (Array.isArray(value) ? value : []).map((item) => {
    const entry = asObject(item);
    return {
      serverId: text(entry.serverId),
      title: text(entry.title) || "MCP 外部工具",
      status: text(entry.status) || "missing_config",
      statusLabel: text(entry.statusLabel) || "待配置",
      statusTone: text(entry.statusTone) || "warning",
      reason: text(entry.reason),
      enabled: Boolean(entry.enabled),
      configured: Boolean(entry.configured),
      commandName: text(entry.commandName),
      toolCount: Number(entry.toolCount || 0),
      safeToolLabels: (Array.isArray(entry.safeToolLabels) ? entry.safeToolLabels : []).map(text).filter(Boolean),
      approvalLabel: text(entry.approvalLabel),
      lastDiscoveryLabel: text(entry.lastDiscoveryLabel),
      actionsEnabled: entry.actionsEnabled !== false && Boolean(text(entry.serverId))
    };
  }).filter((item) => item.title);
}

function normalizeAbilityWorkflows(value) {
  return (Array.isArray(value) ? value : []).map((item) => {
    const entry = asObject(item);
    return {
      workflowId: text(entry.workflowId) || text(entry.id),
      title: text(entry.title) || "本地工作流",
      detail: text(entry.detail),
      statusLabel: text(entry.statusLabel) || "待配置",
      statusTone: text(entry.statusTone) || "warning",
      enabled: Boolean(entry.enabled),
      configured: Boolean(entry.configured),
      executionReady: Boolean(entry.executionReady),
      workflowPath: text(entry.workflowPath),
      defaultWorkflowPath: text(entry.defaultWorkflowPath),
      inputImageSlot: text(entry.inputImageSlot),
      outputImageSlot: text(entry.outputImageSlot),
      actionsEnabled: entry.actionsEnabled !== false && Boolean(text(entry.workflowId) || text(entry.id))
    };
  }).filter((item) => item.title);
}

function normalizeAbilityIntegrations(source) {
  const groups = [
    ["本地服务", source.providers],
    ["MCP 工具", source.mcpServers],
    ["工作流", source.workflows]
  ];
  return groups.flatMap(([group, values]) => (Array.isArray(values) ? values : []).map((item) => {
    const entry = asObject(item);
    return {
      group,
      title: text(entry.title) || text(entry.name) || text(entry.workflowId) || group,
      status: text(entry.statusLabel) || text(entry.status) || "待同步",
      detail: text(entry.reason) || text(entry.detail) || text(entry.description),
      ready: ["ready", "available", "已就绪", "可用"].includes(text(entry.status).toLowerCase()) || /可用|就绪/.test(text(entry.statusLabel))
    };
  })).slice(0, 8);
}

function capabilityActionAvailability(connected, entries) {
  const available = connected && entries.some((entry) => entry.actionsEnabled !== false);
  return { available, reason: connected ? "当前没有可配置的项目" : "桌宠尚未连接" };
}

function normalizeLabelValueRows(value, limit) {
  return (Array.isArray(value) ? value : []).slice(0, limit).map((item) => {
    const entry = asObject(item);
    return {
      label: text(entry.label),
      value: text(entry.value) || text(entry.status)
    };
  }).filter((item) => item.label && item.value);
}

function normalizeCharacterWarnings(characterRuntime) {
  const warning = asObject(characterRuntime.warning);
  if (!text(warning.headline)) return [];
  return /良好|已加载/.test(text(warning.headline)) ? [] : [text(warning.headline), text(warning.body)].filter(Boolean);
}

function normalizeBotCatalog(value, activeBotId) {
  const source = asObject(value);
  const active = text(activeBotId) || text(source.defaultBotId) || text(source.default_bot_id);
  const items = (Array.isArray(source.bots) ? source.bots : []).map((item) => {
    const entry = asObject(item);
    const id = text(entry.botId) || text(entry.bot_id);
    if (!id) return null;
    return {
      id,
      displayName: text(entry.displayName) || text(entry.display_name) || id,
      available: entry.available !== false,
      isDefault: Boolean(entry.default),
      selected: id === active
    };
  }).filter(Boolean);
  return { activeId: active, items };
}

function normalizeAvailablePacks(value, activePackId) {
  const active = text(activePackId);
  return (Array.isArray(value) ? value : []).map((item) => {
    const source = asObject(item);
    const id = text(source.id) || text(source.packId) || text(source.pack_id);
    if (!id) return null;
    return {
      id,
      displayName: text(source.appName) || text(source.name) || id,
      description: [text(source.defaultOutfit), text(source.defaultEmotion)].filter(Boolean).join(" · ") || text(source.schemaVersion),
      selected: active ? id === active : Boolean(source.selected)
    };
  }).filter(Boolean);
}

function normalizeVisualChoices(value, activeValue, fallbackLabel) {
  const active = text(activeValue).toLowerCase();
  return (Array.isArray(value) ? value : []).map((item, index) => {
    const source = asObject(item);
    const id = text(source.id) || text(source.name) || `${fallbackLabel}_${index + 1}`;
    const name = text(source.name) || id;
    const current = Boolean(source.current) || Boolean(active && (id.toLowerCase() === active || name.toLowerCase() === active));
    return { id, name, image: safeAssetUrl(text(source.image)), current };
  });
}

function normalizePackInfo(value) {
  return (Array.isArray(value) ? value : []).map((item) => ({
    label: text(item?.label),
    value: text(item?.value)
  })).filter((item) => item.label && item.value);
}

function finitePercent(value) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(0, Math.min(100, number)) : null;
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
