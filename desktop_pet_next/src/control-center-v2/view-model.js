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
  const availablePacks = normalizeAvailablePacks(characterRuntime.availablePacks, text(characterRuntime.selectedPackId) || text(petState.characterPackId));
  const outfits = normalizeVisualChoices(characterRuntime.outfits, text(petState.outfit), "服装");
  const emotions = normalizeVisualChoices(characterRuntime.emotions, emotionName || text(petState.currentEmotion), "表情");
  const activity = deriveActivity(live, connected);
  const music = deriveMusic(nowPlaying, musicRuntime);
  const outputs = normalizeRecentOutputs(runtime.recentOutputs);
  const warnings = normalizeCharacterWarnings(characterRuntime);
  const abilities = normalizeAbilitiesRuntime(raw.abilitiesRuntime, connected);
  const instanceLabel = text(petState.instanceId) || text(raw.controlCenterRuntime?.health?.data?.instance_id) || "本地实例";
  const chat = normalizeChatSession(raw.chatSession, {
    sessionId: text(petState.sessionId),
    characterName: displayName,
    characterAvatar: portrait
  });

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
    music,
    recentOutputs: outputs,
    abilities,
    abilityLabels: Array.isArray(runtime.abilities) ? runtime.abilities.map(text).filter(Boolean) : [],
    actions: {
      "chat.new": { available: connected, reason: connected ? "" : "桌宠尚未连接" },
      "chat.send": {
        available: connected && liveSnapshotStatus === "connected" && !Boolean(active.sending),
        reason: !connected ? "桌宠尚未连接" : liveSnapshotStatus !== "connected" ? "请在桌面端窗口中发送" : "正在回复，请稍后再发"
      },
      "chat.stop": { available: connected && Boolean(active.sending || active.replyDisplayActive), reason: "当前没有进行中的回复" },
      "workspace.open": { available: true, reason: "" },
      "character.openWorkshop": { available: true, reason: "" },
      "character.openPackFolder": { available: true, reason: "" },
      "character.selectPack": { available: connected && availablePacks.length > 0, reason: connected ? "没有可切换的角色包" : "桌宠尚未连接" },
      "character.setOutfit": { available: connected && outfits.length > 0, reason: connected ? "当前角色没有服装资源" : "桌宠尚未连接" },
      "character.previewEmotion": { available: connected && emotions.length > 0, reason: connected ? "当前服装没有表情资源" : "桌宠尚未连接" },
      "character.refresh": { available: connected, reason: "桌宠尚未连接" },
      "abilities.approvalPolicy.save": { available: connected && abilities.policy.availableModes.length > 0, reason: connected ? "审批策略暂不可用" : "桌宠尚未连接" },
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
