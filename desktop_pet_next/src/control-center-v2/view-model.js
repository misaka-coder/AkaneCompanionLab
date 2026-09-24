import { MODEL_SERVICE_ACTIONS, normalizeModelServiceRuntime } from "./model-service.js";
import { normalizeQqSetup, QQ_SETUP_ACTIONS } from "./qq-setup.js";
import { SCREEN_OBSERVATION_COMMANDS, normalizeScreenObservationSettings } from "../screen-observation.js";

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
  abilities.plugins = normalizePluginRuntime(raw.pluginRuntime, raw.pluginManagementRuntime, connected);
  abilities.plugins.market = normalizePluginMarket(raw.pluginMarketRuntime, connected);
  abilities.plugins.localPickerAvailable = connected && liveSnapshotStatus !== "not-applicable" && isLoopbackBackendUrl(raw.backendUrl);
  abilities.available = abilities.available || abilities.plugins.available;
  const model = normalizeModelServiceRuntime(raw.modelRuntime, connected);
  const voice = normalizeVoiceRuntime(raw.voiceRuntime, live, connected);
  const setup = deriveSetupReadiness({
    connected,
    packId: text(characterRuntime.selectedPackId) || text(petState.characterPackId),
    displayName,
    characterWarnings: warnings,
    model,
    abilities,
    voice
  });
  const system = normalizeSystemRuntime(raw, live, {
    connected,
    liveSnapshotStatus,
    characterWarnings: warnings,
    voice
  });
  const qqSetup = normalizeQqSetup(raw.qqSetupRuntime, liveSnapshotStatus !== "not-applicable", raw.backendUrl);
  const instanceLabel = text(petState.instanceId) || text(raw.controlCenterRuntime?.health?.data?.instance_id) || "本地实例";
  const chat = normalizeChatSession(raw.chatSession, {
    sessionId: text(petState.sessionId),
    botId: text(petState.boundBotId),
    characterPackId: text(petState.characterPackId),
    characterName: displayName,
    characterAvatar: portrait,
    runtime: raw.chatRuntime
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
      voice: normalizeCharacterVoicePreference(characterRuntime.voice),
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
    perception: {
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
    },
    chat: { ...chat, pendingAttachments: Array.isArray(live.pendingAttachments) ? live.pendingAttachments : [],
      uploadingAttachments: Number(live.attachmentUploadCount) || 0 },
    bots,
    music,
    recentOutputs: outputs,
    abilities,
    model,
    voice,
    setup,
    system,
    qqSetup,
    abilityLabels: Array.isArray(runtime.abilities) ? runtime.abilities.map(text).filter(Boolean) : [],
    actions: {
      [QQ_SETUP_ACTIONS.detect]: { available: qqSetup.supported && !qqSetup.busy, reason: qqSetup.detail },
      [QQ_SETUP_ACTIONS.select]: { available: qqSetup.supported && !qqSetup.blocked && !qqSetup.busy && !qqSetup.native.starting, reason: qqSetup.detail },
      [QQ_SETUP_ACTIONS.start]: { available: qqSetup.supported && !qqSetup.blocked && !qqSetup.busy && qqSetup.native.installed && !qqSetup.native.starting, reason: qqSetup.detail },
      [QQ_SETUP_ACTIONS.openLogin]: { available: qqSetup.supported && !qqSetup.blocked && !qqSetup.busy && qqSetup.native.webuiReady, reason: qqSetup.detail },
      [QQ_SETUP_ACTIONS.openFolder]: { available: qqSetup.supported && !qqSetup.blocked && !qqSetup.busy && qqSetup.native.installed, reason: qqSetup.detail },
      "chat.new": { available: connected, reason: connected ? "" : "桌宠尚未连接" },
      "chat.send": {
        available: connected && liveSnapshotStatus === "connected" && !Number(live.attachmentUploadCount),
        reason: !connected ? "桌宠尚未连接" : liveSnapshotStatus !== "connected" ? "请在桌面端窗口中发送" : "附件正在接收，完成后再发送"
      },
      "chat.attach": { available: connected && liveSnapshotStatus === "connected", reason: "请连接桌宠后添加附件" },
      "chat.removeAttachment": { available: liveSnapshotStatus === "connected", reason: "桌宠未连接" },
      "chat.playAttachment": { available: connected && liveSnapshotStatus === "connected", reason: "桌宠未连接" },
      "chat.fileAction": { available: connected && liveSnapshotStatus === "connected", reason: "桌宠未连接" },
      "chat.stop": { available: connected && Boolean(active.sending || active.replyDisplayActive), reason: "当前没有进行中的回复" },
      "chat.jobControl": { available: connected, reason: "桌宠尚未连接" },
      "workspace.open": { available: true, reason: "" },
      "settings.selectBot": { available: connected && liveSnapshotStatus === "connected" && bots.items.length > 1, reason: connected ? "没有其他可切换的 Bot" : "桌宠尚未连接" },
      "character.openWorkshop": { available: true, reason: "" },
      "character.openPackFolder": { available: true, reason: "" },
      "character.selectPack": { available: connected && availablePacks.length > 0, reason: connected ? "没有可切换的角色包" : "桌宠尚未连接" },
      "character.setOutfit": { available: connected && outfits.length > 0, reason: connected ? "当前角色没有服装资源" : "桌宠尚未连接" },
      "character.previewEmotion": { available: connected && emotions.length > 0, reason: connected ? "当前服装没有表情资源" : "桌宠尚未连接" },
      "character.refresh": { available: connected, reason: "桌宠尚未连接" },
      "abilities.approvalPolicy.save": { available: connected && abilities.policy.families.some((family) => family.availableModes.length > 0), reason: connected ? "审批策略暂不可用" : "桌宠尚未连接" },
      "abilities.approvalRequest.decide": { available: connected && abilities.approvalRequests.length > 0, reason: connected ? "当前没有待确认请求" : "桌宠尚未连接" },
      "abilities.skills.openFolder": { available: true, reason: "" },
      "abilities.provider.config.save": capabilityActionAvailability(connected, abilities.providers),
      "abilities.provider.healthCheck": capabilityActionAvailability(connected, abilities.providers),
      "abilities.provider.ttsTest": voiceProfileActionAvailability(connected, abilities.providers, "test"),
      "abilities.provider.voiceProfile.inspectFolder": voiceProfileActionAvailability(connected, abilities.providers, "configure"),
      "abilities.provider.voiceProfile.save": voiceProfileActionAvailability(connected, abilities.providers, "configure"),
      "abilities.provider.voiceProfile.assignToCurrentCharacter": {
        available: connected && Boolean(text(characterRuntime.selectedPackId) || text(petState.characterPackId)) && abilities.providers.some((entry) => entry.voiceProfiles.some((profile) => profile.enabled)),
        reason: !connected ? "桌宠尚未连接" : !(text(characterRuntime.selectedPackId) || text(petState.characterPackId)) ? "当前没有可绑定的角色包" : "还没有可用的声线档案"
      },
      "abilities.provider.voiceProfile.clearCurrentCharacter": {
        available: connected && Boolean(text(characterRuntime.selectedPackId) || text(petState.characterPackId)) && Boolean(text(asObject(characterRuntime.voice).profileId)),
        reason: !connected ? "桌宠尚未连接" : "当前角色没有单独绑定声线"
      },
      "abilities.mcp.config.save": capabilityActionAvailability(connected, abilities.mcpServers),
      "abilities.mcp.discover": capabilityActionAvailability(connected, abilities.mcpServers),
      "abilities.mcp.enable": capabilityActionAvailability(connected, abilities.mcpServers),
      "abilities.mcp.disable": capabilityActionAvailability(connected, abilities.mcpServers),
      "abilities.mcp.restart": capabilityActionAvailability(connected, abilities.mcpServers),
      "abilities.mcp.remove": capabilityActionAvailability(connected, abilities.mcpServers),
      "abilities.plugin.enable": {
        available: connected && abilities.plugins.entries.some((item) => item.actionsEnabled && !item.enabled),
        reason: connected ? "当前没有可启用的插件" : "桌宠尚未连接"
      },
      "abilities.plugin.disable": {
        available: connected && abilities.plugins.entries.some((item) => item.actionsEnabled && item.enabled),
        reason: connected ? "当前没有可停用的插件" : "桌宠尚未连接"
      },
      "abilities.plugin.rollback": {
        available: connected && abilities.plugins.entries.some((item) => item.actionsEnabled && item.rollbackAvailable),
        reason: connected ? "当前没有可回滚的插件版本" : "桌宠尚未连接"
      },
      "abilities.plugin.stageSource": {
        available: connected && abilities.plugins.managementAvailable && abilities.plugins.supports.includes("stage_source"),
        reason: connected ? "当前宿主不支持从源码暂存插件" : "桌宠尚未连接"
      },
      "abilities.plugin.stageWheel": {
        available: connected && abilities.plugins.managementAvailable && abilities.plugins.supports.includes("stage_wheel"),
        reason: connected ? "当前宿主不支持暂存 wheel" : "桌宠尚未连接"
      },
      "abilities.plugin.stageMarket": {
        available: connected && abilities.plugins.managementAvailable && abilities.plugins.supports.includes("stage_market") && abilities.plugins.market.status === "available",
        reason: connected ? "市场或插件管理服务暂不可用" : "桌宠尚未连接"
      },
      "abilities.plugin.install": {
        available: connected && abilities.plugins.managementAvailable && abilities.plugins.supports.includes("install") && abilities.plugins.stages.some((item) => item.ok),
        reason: connected ? "当前没有可安装的已审查候选" : "桌宠尚未连接"
      },
      "abilities.plugin.discardStage": {
        available: connected && abilities.plugins.managementAvailable && abilities.plugins.supports.includes("discard_stage") && abilities.plugins.stages.length > 0,
        reason: connected ? "当前没有可丢弃的暂存候选" : "桌宠尚未连接"
      },
      "abilities.plugin.uninstall": {
        available: connected && abilities.plugins.managementAvailable && abilities.plugins.supports.includes("uninstall") && abilities.plugins.entries.some((item) => item.actionsEnabled && item.source === "managed"),
        reason: connected ? "当前没有可卸载的用户插件" : "桌宠尚未连接"
      },
      "abilities.plugin.connection.save": {
        available: connected && abilities.plugins.managementAvailable && abilities.plugins.entries.some((item) => item.actionsEnabled && item.connectionConfigs.length > 0),
        reason: connected ? "当前没有可配置的插件连接" : "桌宠尚未连接"
      },
      "abilities.plugin.pickSource": {
        available: abilities.plugins.localPickerAvailable,
        reason: abilities.plugins.localPickerAvailable ? "" : "远端 Bot 需要填写远端宿主路径"
      },
      "abilities.plugin.pickWheel": {
        available: abilities.plugins.localPickerAvailable,
        reason: abilities.plugins.localPickerAvailable ? "" : "远端 Bot 需要填写远端宿主路径"
      },
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
      ...Object.fromEntries(Object.keys(SCREEN_OBSERVATION_COMMANDS).map((id) => [id, voiceActionAvailability(system.controlsAvailable)])),
      "advanced.setHitTestEnabled": voiceActionAvailability(system.controlsAvailable),
      "advanced.setHitboxOverlay": voiceActionAvailability(system.controlsAvailable),
      "advanced.resetWindow": voiceActionAvailability(system.controlsAvailable),
      "music.togglePlayback": { available: music.available, reason: "当前没有可控制的音乐" },
      "window.minimize": { available: true, reason: "" },
      "window.maximize": { available: true, reason: "" },
      "window.close": { available: true, reason: "" }
    }
  };
}

function deriveSetupReadiness({ connected, packId, displayName, characterWarnings, model, abilities, voice }) {
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

export function normalizeActionPresentation(result) {
  const value = asObject(result);
  const status = text(value.status) || (value.ok ? "executed" : "failed");
  if (status === "cancelled") {
    return { phase: "confirmed", label: "已取消", detail: "" };
  }
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
  const commandOutcome = observedActionOutcome(actionId, payload, after);
  if (commandOutcome && commandOutcome.ok === false) return true;
  if (["chat.attach", "chat.removeAttachment", "chat.playAttachment", "chat.fileAction"].includes(actionId)) return Boolean(commandOutcome?.ok);
  const observationCommand = SCREEN_OBSERVATION_COMMANDS[actionId];
  if (observationCommand) {
    if (!commandOutcome?.ok) return false;
    if (!observationCommand.field) return Number(afterActive.screenVisionBufferRevision) > Number(beforeActive.screenVisionBufferRevision || 0);
    const actual = afterState[observationCommand.field];
    return typeof actual === "number" ? numbersClose(actual, payload.value) : actual === payload.value;
  }

  if (actionId === "chat.new") {
    const beforeSession = text(beforeState.sessionId);
    const afterSession = text(afterState.sessionId);
    return Boolean(beforeSession && afterSession && beforeSession !== afterSession);
  }
  if (actionId === "chat.send") {
    const commandResult = asObject(after.settingsCommandResult);
    return (
      text(commandResult.command) === "sendChatMessage" &&
      (!text(payload.operationId) || text(commandResult.operationId) === text(payload.operationId)) &&
      text(commandResult.status) === "accepted" && Boolean(commandResult.ok)
    );
  }
  if (actionId === "chat.stop") {
    const wasActive = Boolean(beforeActive.sending || beforeActive.replyDisplayActive);
    return wasActive && !afterActive.sending && !afterActive.replyDisplayActive;
  }
  if (["music.previous", "music.next", "music.togglePlayback", "music.stop"].includes(actionId)) {
    if (text(payload.operationId)) {
      return Boolean(observedActionOutcome(actionId, payload, after));
    }
    return musicPlaybackSignature(before) !== musicPlaybackSignature(after);
  }
  if (actionId === "voice.test" || actionId === "voice.previewPlay") {
    return Boolean(commandOutcome?.ok) && Boolean(afterActive.speaking);
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
    return Boolean(
      commandOutcome?.ok &&
      afterExpression &&
      (!expected || afterExpression === expected) &&
      (afterExpression !== beforeExpression || text(asObject(after.settingsCommandResult).status) === "completed")
    );
  }
  return true;
}

export function observedActionOutcome(actionId, payload = {}, snapshot = {}) {
  const commandResult = asObject(asObject(snapshot).settingsCommandResult);
  const expectedOperationId = text(payload.operationId);
  const expectedCommand = SCREEN_OBSERVATION_COMMANDS[actionId]?.command || {
    "chat.send": "sendChatMessage",
    "chat.attach": "attachChatFiles",
    "chat.removeAttachment": "removeChatAttachment",
    "chat.playAttachment": "playChatAttachment",
    "chat.fileAction": "chatFileAction",
    "voice.test": "testTts",
    "voice.previewPlay": "previewTts",
    "character.previewEmotion": "previewEmotion",
    "music.previous": "controlActiveMusic",
    "music.next": "controlActiveMusic",
    "music.togglePlayback": "controlActiveMusic",
    "music.stop": "controlActiveMusic"
  }[actionId];
  if (!expectedCommand || text(commandResult.command) !== expectedCommand) return null;
  if (expectedOperationId && text(commandResult.operationId) !== expectedOperationId) return null;
  if (!text(commandResult.status)) return null;
  const outcome = {
    ok: Boolean(commandResult.ok),
    status: text(commandResult.status),
    reason: text(commandResult.reason)
  };
  if (["music.previous", "music.next", "music.togglePlayback", "music.stop"].includes(actionId)) {
    return {
      ...outcome,
      target: text(commandResult.target),
      targetId: text(commandResult.targetId),
      mediaAction: text(commandResult.action)
    };
  }
  return outcome;
}

function musicPlaybackSignature(snapshot) {
  const value = asObject(snapshot);
  const active = asObject(value.active);
  const music = asObject(value.music);
  const localTrack = asObject(music.track);
  const localStatePresent = Object.prototype.hasOwnProperty.call(active, "musicPlaying")
    || Object.prototype.hasOwnProperty.call(active, "musicPaused");
  const localAvailable = Boolean(text(localTrack.displayName) || text(music.displayName) || Number(music.queueCount));
  const systemMedia = asObject(music.systemMedia);
  const systemAvailable = Boolean(text(systemMedia.playbackStatus) || text(systemMedia.title));
  if (localAvailable || (localStatePresent && !systemAvailable)) {
    return `local:${Boolean(active.musicPlaying)}:${Boolean(active.musicPaused)}`;
  }
  return `system:${text(systemMedia.playbackStatus)}:${Boolean(systemMedia.isPlaying)}`;
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
    details: normalizeSystemDetails(raw, live, metrics)
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
  const runtime = asObject(options.runtime);
  const session = asObject(source.session);
  const messagePage = asObject(source.message_page);
  const expectedSessionId = text(options.sessionId);
  const actualSessionId = text(session.session_id) || text(session.sessionId);
  const matchesCurrentSession = (!expectedSessionId || !actualSessionId || expectedSessionId === actualSessionId)
    && (!text(source.bot_id) || !text(options.botId) || text(source.bot_id) === text(options.botId))
    && (!text(session.character_pack_id) || !text(options.characterPackId) || text(session.character_pack_id) === text(options.characterPackId));
  const messages = matchesCurrentSession
    ? (Array.isArray(source.messages) ? source.messages : [])
      .map((item) => normalizeChatMessage(item))
      .filter(Boolean)
    : [];
  const latestOutcome = matchesCurrentSession ? normalizeChatOutcome(source.latest_final_json) : null;
  return {
    sessionId: actualSessionId || expectedSessionId,
    title: matchesCurrentSession
      ? text(session.display_title) || text(session.displayTitle) || "当前对话"
      : "正在切换会话",
    messages,
    latestOutcome,
    outputs: matchesCurrentSession ? (Array.isArray(source.workspace_outputs) ? source.workspace_outputs : []).slice(0, 12)
      .map(item => normalizeChatFile({ ...item, item_type: "generated" })) : [],
    outputsStatus: matchesCurrentSession ? text(source.workspace_outputs_status) : "switching",
    totalCount: Math.max(messages.length, finiteNumber(session.message_count, messages.length)),
    history: {
      pageKnown: Object.keys(messagePage).length > 0,
      hasMore: Boolean(messagePage.has_more),
      nextBeforeSeq: finiteNumber(messagePage.next_before_seq, 0)
    },
    characterName: text(options.characterName) || "桌宠",
    characterAvatar: safeAssetUrl(text(options.characterAvatar)),
    hasHistory: messages.length > 0,
    matchesCurrentSession,
    loadStatus: text(runtime.status) || "idle",
    loadError: text(runtime.reason)
  };
}

function normalizeChatOutcome(value) {
  const source = asObject(value);
  if (!Object.keys(source).length) return null;
  const events = (Array.isArray(source.tool_events) ? source.tool_events : [])
    .slice(0, 16)
    .map((item) => {
      const entry = asObject(item);
      const type = text(entry.type);
      const toolType = text(entry.tool_type) || text(entry.capabilityId) || text(entry.capability_id);
      const status = text(entry.status) || (entry.is_error ? "error" : "completed");
      const jobId = text(entry.job_id) || text(entry.jobId);
      const explicitJobStatus = text(entry.job_status) || text(entry.jobStatus);
      const explicitControlState = text(entry.control_state) || text(entry.controlState);
      const jobStatus = explicitJobStatus || (jobId && type === "background_job_accepted" ? "queued" : "");
      const controlState = explicitControlState || (jobStatus === "queued" ? "running" : "");
      if (!toolType && !type) return null;
      return {
        type,
        toolType: toolType || outcomeEventLabel(type),
        status,
        reason: text(entry.reason),
        jobId,
        jobStatus,
        controlState,
        tone: outcomeTone(status, entry.is_error === true)
      };
    })
    .filter(Boolean);
  const rawStatus = text(source.status);
  const reason = text(source.reason) || text(source.error);
  const hasStructuredValue = Object.hasOwn(source, "value") || Object.hasOwn(source, "data") || Object.hasOwn(source, "content");
  const hasFailure = Boolean(reason) || events.some((item) => item.tone === "danger");
  const status = rawStatus || (hasFailure ? "failed" : events.length || hasStructuredValue ? "completed" : "");
  if (!status && !events.length && !hasStructuredValue) return null;
  return {
    status,
    statusLabel: outcomeStatusLabel(status),
    tone: outcomeTone(status, hasFailure),
    reason: outcomeReasonLabel(reason),
    events,
    hasStructuredValue,
    artifactCount: events.filter((item) => ["generated_file_ready", "file_ready", "artifact_ready"].includes(item.type)).length,
    toolCount: events.filter((item) => item.toolType && !["工具", "文件产物", "状态更新"].includes(item.toolType)).length
  };
}

function outcomeEventLabel(type) {
  if (["generated_file_ready", "file_ready", "artifact_ready"].includes(type)) return "文件产物";
  if (["state_update", "status_update"].includes(type)) return "状态更新";
  if (["capability_execution_result", "tool_completed"].includes(type)) return "工具调用";
  return "工具";
}

function outcomeTone(status, explicitFailure = false) {
  const normalized = text(status).toLowerCase();
  if (explicitFailure || ["error", "failed", "failure", "blocked", "rejected", "policy_failed", "policy_rejected", "resource_exhausted", "cancelled", "timed_out", "execution_unknown", "unavailable"].includes(normalized)) return "danger";
  if (["ask", "approval_required", "pending", "running", "accepted", "queued", "paused", "stopping", "cancelling"].includes(normalized)) return "warning";
  return "good";
}

function outcomeStatusLabel(status) {
  const normalized = text(status).toLowerCase();
  return {
    ok: "已完成",
    completed: "已完成",
    success: "已完成",
    succeeded: "已完成",
    executed: "已执行",
    accepted: "已排队",
    queued: "已排队",
    running: "进行中",
    paused: "已暂停",
    stopping: "停止中",
    cancelling: "停止中",
    pending: "等待处理",
    ask: "等待确认",
    approval_required: "等待确认",
    resource_exhausted: "资源不足",
    policy_rejected: "策略阻止",
    policy_failed: "策略失败",
    blocked: "已阻止",
    cancelled: "已取消",
    timed_out: "已超时",
    execution_unknown: "结果未确认",
    failed: "执行失败",
    error: "执行失败"
  }[normalized] || (status ? `状态：${status}` : "已完成");
}

function outcomeReasonLabel(reason) {
  const normalized = text(reason);
  return {
    execution_resource_limit_exceeded: "本次调用达到部署资源限额。",
    requires_user_decision: "需要先完成能力审批。",
    capability_disabled_by_policy: "当前能力策略没有允许这项调用。",
    plugin_capability_revoked: "插件能力已撤销，调用没有继续执行。"
  }[normalized] || normalized;
}

function normalizeChatMessage(value) {
  const source = asObject(value);
  const role = text(source.role).toLowerCase();
  const content = text(source.content);
  if ((!content && !source.attachments?.length) || !["user", "assistant"].includes(role)) return null;
  const metadata = asObject(source.memory_metadata);
  const timestamp = Number(source.timestamp);
  return {
    id: text(source.source_id) || `${role}-${text(source.seq_no)}-${timestamp || 0}`,
    seqNo: finiteNumber(source.seq_no, 0),
    role,
    content,
    timestamp: Number.isFinite(timestamp) && timestamp > 0 ? timestamp : 0,
    intermediate: text(metadata.turn_role).toLowerCase() === "intermediate",
    attachments: (Array.isArray(source.attachments) ? source.attachments : []).slice(0, 40).map(normalizeChatFile)
  };
}

function deriveActivity(runtimeSnapshot, connected) {
  if (!connected) return { phase: "waiting", label: "等待桌宠连接", detail: "连接后会显示真实执行状态" };
  const active = asObject(runtimeSnapshot.active);
  const mode = text(runtimeSnapshot.runtimeMode).toLowerCase();
  const status = text(runtimeSnapshot.runtimeStatus);
  if (active.proactiveWakeRunning) return { phase: "observing", label: "正在评估是否开口" };
  if (active.sending) return { phase: mode.includes("tool") ? "using_tool" : "thinking", label: status || "正在处理请求" };
  if (active.speaking) return { phase: "delivering", label: status || "正在回应" };
  if (active.voiceInput === "opening") return { phase: "thinking", label: status || "正在打开麦克风" };
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

function normalizeChatFile(item) {
  return { handle: text(item?.handle), attachmentId: text(item?.attachment_id),
    itemType: item?.item_type === "generated" ? "generated" : "attachment",
    title: text(item?.title) || "附件", kind: text(item?.kind), format: text(item?.format),
    status: text(item?.status) || "unavailable", canOpen: item?.can_open === true,
    sizeBytes: Math.max(0, finiteNumber(item?.size_bytes, 0)),
    durationSeconds: Math.max(0, finiteNumber(item?.duration_seconds, 0)),
    timestamp: Math.max(0, finiteNumber(item?.created_at || item?.updated_at, 0)) };
}

function normalizeAbilitiesRuntime(value, connected) {
  const source = asObject(value);
  const overview = asObject(source.overview);
  const safety = asObject(source.safety);
  const policy = asObject(safety.approvalPolicy);
  const families = (Array.isArray(policy.families) ? policy.families : [])
    .map((item) => {
      const entry = asObject(item);
      const id = text(entry.id);
      if (!["ops", "extensions"].includes(id)) return null;
      const mode = ["trusted_auto_allow", "ask_each_time", "disabled"].includes(text(entry.mode))
        ? text(entry.mode)
        : "ask_each_time";
      const modes = (Array.isArray(entry.availableModes) ? entry.availableModes : [])
        .map((modeItem) => {
          const option = asObject(modeItem);
          const optionId = text(option.id);
          if (!["trusted_auto_allow", "ask_each_time", "disabled"].includes(optionId)) return null;
          return { id: optionId, label: text(option.label), summary: text(option.summary) };
        })
        .filter(Boolean);
      return {
        id,
        label: text(entry.label) || (id === "ops" ? "本机与外部操作" : "扩展管理"),
        summary: text(entry.summary),
        mode,
        availableModes: modes
      };
    })
    .filter(Boolean);
  const allSameMode = families.length > 0 && families.every((family) => family.mode === families[0].mode);
  const policyLabel = allSameMode
    ? ({ trusted_auto_allow: "全部直接允许", ask_each_time: "全部每次询问", disabled: "全部关闭" }[families[0].mode] || "按能力分组设置")
    : families.length ? "按能力分组设置" : "策略不可用";
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
      label: policyLabel,
      summary: families.length ? "本机操作与扩展管理分别授权。" : "后端未返回权限分组。",
      families
    },
    safetyStatus: text(safety.status) || (connected ? "已生效" : "待连接"),
    safetyItems: normalizeLabelValueRows(safety.items, 8),
    approvalRequests: normalizeApprovalRequests(safety.approvalRequests),
    skills: normalizeAbilitySkills(source.skills),
    providers: normalizeAbilityProviders(source.providers),
    mcpServers: normalizeAbilityMcpServers(source.mcpServers),
    workflows: normalizeAbilityWorkflows(source.workflows),
    integrations: normalizeAbilityIntegrations(source),
    execution: normalizeExecutionRuntime(source.execution, connected),
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

function normalizeExecutionRuntime(value, connected) {
  const source = asObject(value);
  const policyPayload = asObject(source.policy);
  const resourcePayload = asObject(source.resource);
  const policy = asObject(policyPayload.policy);
  const resource = asObject(resourcePayload.policy);
  const limits = asObject(resource.limits);
  const policyEntries = (Array.isArray(policy.policies) ? policy.policies : []).map((item) => {
    const entry = asObject(item);
    const stages = Array.isArray(entry.stages) ? entry.stages.map(text).filter(Boolean) : [];
    return {
      policyId: text(entry.policy_id),
      serviceId: text(entry.service_id),
      version: Number(entry.version) || 1,
      order: Number(entry.order) || 0,
      status: text(entry.status) || "unavailable",
      reason: text(entry.reason),
      stages
    };
  }).filter((item) => item.policyId || item.serviceId);
  const limitEntries = ["max_input_bytes", "max_dependency_depth", "max_dependency_calls"].map((key) => {
    const entry = asObject(limits[key]);
    return {
      key,
      value: entry.value === null || entry.value === undefined ? null : Number(entry.value),
      source: text(entry.source) || "deployment_default"
    };
  });
  const policyAvailable = connected && policyPayload.ok === true && Boolean(policyPayload.policy && typeof policyPayload.policy === "object");
  const resourceAvailable = connected && resourcePayload.ok === true && Boolean(resource.policy_id || resource.limits);
  return {
    available: policyAvailable || resourceAvailable,
    status: policyAvailable || resourceAvailable ? "available" : text(policyPayload.reason) || text(resourcePayload.reason) || "unavailable",
    policy: {
      available: policyAvailable,
      status: text(policyPayload.status) || (policyAvailable ? "available" : "unavailable"),
      policyId: text(policy.policy_id),
      entries: policyEntries,
      diagnostics: (Array.isArray(policy.diagnostics) ? policy.diagnostics : []).map((item) => {
        const entry = asObject(item);
        return { policyId: text(entry.policy_id), phase: text(entry.phase), target: text(entry.target), reason: text(entry.reason) };
      }).filter((item) => item.policyId || item.reason)
    },
    resource: {
      available: resourceAvailable,
      status: text(resourcePayload.status) || (resourceAvailable ? "available" : "unavailable"),
      policyId: text(resource.policy_id),
      limits: limitEntries
    }
  };
}


function normalizePluginRuntime(value, managementValue, connected) {
  const runtime = asObject(value);
  const payload = asObject(runtime.data);
  const status = text(runtime.status) || "not-requested";
  const managementRuntime = asObject(managementValue);
  const managementPayload = asObject(managementRuntime.data);
  const management = asObject(managementPayload.management);
  const artifacts = asObject(managementPayload.artifacts);
  const managementStatus = text(managementRuntime.status) || "not-requested";
  const dependencySupply = normalizePluginDependencySupply(payload.dependency_supply || artifacts.dependency_supply);
  const supports = (Array.isArray(management.supports) ? management.supports : []).map(text).filter(Boolean);
  const entries = (Array.isArray(payload.plugins) ? payload.plugins : [])
    .map((item) => {
      const entry = asObject(item);
      const pluginId = text(entry.plugin_id);
      if (!pluginId) return null;
      const runtimeStatus = text(entry.runtime_status) || "unavailable";
      const enabled = Boolean(entry.enabled);
      const pendingActivation = Boolean(entry.pending_activation);
      const presentation = pluginStatusPresentation(runtimeStatus, enabled, pendingActivation);
      const normalizedContributions = normalizePluginContributions(entry.contributions);
      const connectionConfigs = normalizePluginConnectionConfigs(entry.connection_configs, normalizedContributions.connections);
      return {
        pluginId,
        version: text(entry.version),
        source: text(entry.source) === "managed" ? "managed" : "bundled",
        manageable: entry.manageable === true,
        enabled,
        runtimeStatus,
        statusLabel: presentation.label,
        statusTone: presentation.tone,
        reason: text(entry.reason),
        dependencyErrors: normalizePluginDependencyErrors(entry.dependency_errors),
        generation: Number(entry.generation || 0),
        surfaces: (Array.isArray(entry.surfaces) ? entry.surfaces : [])
          .map(text)
          .filter((surface) => ["desktop", "qq"].includes(surface)),
        contributions: normalizedContributions,
        connectionConfigs,
        contributionCount: Object.values(normalizedContributions)
          .reduce((total, values) => total + values.length, 0),
        permissions: (Array.isArray(entry.permissions) ? entry.permissions : []).map(text).filter(Boolean),
        declaredOnly: Boolean(entry.declared_only),
        pendingActivation,
        rollbackAvailable: Boolean(entry.rollback_available)
      };
    })
    .filter(Boolean)
    .map((entry) => ({
      ...entry,
      actionsEnabled: connected && status === "available" && entry.manageable && !entry.pendingActivation
    }));
  return {
    available: connected && status === "available" && payload.ok === true,
    status,
    reason: text(runtime.reason) || text(payload.reason),
    generation: Number(payload.generation || 0),
    managementAvailable: connected && managementStatus === "available" && text(management.status) === "ready",
    managementStatus,
    managementReason: text(managementRuntime.reason) || text(managementPayload.reason),
    supports,
    dependencySupply,
    stages: (Array.isArray(artifacts.stages) ? artifacts.stages : []).map((item) => {
      const entry = asObject(item);
      const pluginId = text(entry.plugin_id);
      const stageId = text(entry.stage_id);
      if (!stageId) return null;
      const contributions = normalizePluginContributions(entry.contribution_snapshot);
      return {
        ok: entry.ok === true,
        stageId,
        pluginId,
        version: text(entry.version),
        distributionName: text(entry.distribution_name),
        status: text(entry.status),
        reason: text(entry.reason),
        permissions: (Array.isArray(entry.permissions) ? entry.permissions : []).map(text).filter(Boolean),
        contributions,
        contributionCount: Object.values(contributions).reduce((total, values) => total + values.length, 0)
      };
    }).filter(Boolean),
    total: entries.length,
    active: entries.filter((item) => item.enabled && item.runtimeStatus === "active").length,
    entries
  };
}

function normalizePluginMarket(value, connected) {
  const runtime = asObject(value);
  const payload = asObject(runtime.data);
  return {
    status: text(runtime.status) || "not-requested",
    reason: text(runtime.reason) || text(payload.reason),
    sourceKind: text(payload.source_kind),
    entries: connected && runtime.status === "available" && payload.ok === true
      ? (Array.isArray(payload.plugins) ? payload.plugins : []).map((entry) => ({
          pluginId: text(entry.plugin_id), version: text(entry.version), displayName: text(entry.display_name),
          summary: text(entry.summary), digest: text(entry.sha256), sizeBytes: Number(entry.size_bytes || 0),
          installedVersion: text(entry.installed_version), installedStatus: text(entry.installed_status),
          permissions: (Array.isArray(entry.permissions) ? entry.permissions : []).map(text).filter(Boolean),
          requirements: (Array.isArray(entry.requirements) ? entry.requirements : []).map(text).filter(Boolean)
        })).filter((entry) => entry.pluginId && /^[a-f0-9]{64}$/.test(entry.digest))
      : []
  };
}

function normalizePluginContributions(value) {
  const contributions = asObject(value);
  const normalized = {};
  for (const key of ["capabilities", "commands", "event_handlers", "hooks", "background_services", "prompt_blocks", "skills"]) {
    normalized[key] = (Array.isArray(contributions[key]) ? contributions[key] : []).map(text).filter(Boolean);
  }
  normalized.connections = normalizePluginConnectionDeclarations(contributions.connections);
  return normalized;
}

function normalizePluginConnectionDeclarations(value) {
  return (Array.isArray(value) ? value : []).map((item) => {
    const entry = asObject(item);
    const schema = normalizePluginConnectionSchema(entry.schema);
    const properties = asObject(schema.properties);
    const privateFields = (Array.isArray(entry.private_fields) ? entry.private_fields : [])
      .map(text)
      .filter((field) => Object.hasOwn(properties, field));
    return {
      name: text(entry.name),
      version: Math.max(1, Number(entry.version) || 1),
      description: text(entry.description),
      schema,
      privateFields
    };
  }).filter((item) => item.name && item.schema.type === "object");
}

function normalizePluginConnectionSchema(value) {
  const source = asObject(value);
  const properties = asObject(source.properties);
  const normalizedProperties = {};
  for (const [key, raw] of Object.entries(properties)) {
    const field = asObject(raw);
    const type = ["string", "number", "integer", "boolean"].includes(text(field.type)) ? text(field.type) : "string";
    const next = { type };
    for (const property of ["title", "description", "default", "minimum", "maximum", "pattern", "format", "step"]) {
      if (field[property] !== undefined && field[property] !== null) next[property] = field[property];
    }
    if (Array.isArray(field.enum)) next.enum = field.enum.slice(0, 40);
    normalizedProperties[key] = next;
  }
  return {
    type: "object",
    properties: normalizedProperties,
    required: (Array.isArray(source.required) ? source.required : []).map(text).filter((key) => Object.hasOwn(normalizedProperties, key)),
    additionalProperties: source.additionalProperties === true
  };
}

function normalizePluginConnectionConfigs(value, declarations) {
  const configs = Array.isArray(value) ? value : [];
  return (Array.isArray(declarations) ? declarations : []).map((declaration) => {
    const entry = configs.find((item) => text(asObject(item).connection?.name) === declaration.name) || {};
    const values = asObject(entry.values);
    const privateFields = Array.from(new Set([
      ...declaration.privateFields,
      ...(Array.isArray(entry.private_fields) ? entry.private_fields.map(text) : [])
    ])).filter((field) => Object.hasOwn(declaration.schema.properties, field));
    return {
      ...declaration,
      status: text(entry.status) || "not_configured",
      reason: text(entry.reason),
      revision: Math.max(0, Number(entry.revision) || 0),
      enabled: entry.enabled === true,
      values: Object.fromEntries(Object.entries(values).filter(([key]) => !privateFields.includes(key))),
      configuredPrivateFields: (Array.isArray(entry.configured_private_fields) ? entry.configured_private_fields : [])
        .map(text)
        .filter((field) => privateFields.includes(field)),
      privateFields
    };
  });
}

function isLoopbackBackendUrl(value) {
  try {
    const hostname = new URL(text(value)).hostname.replace(/^\[|\]$/g, "").toLowerCase();
    return hostname === "localhost" || hostname.startsWith("127.") || hostname === "::1";
  } catch {
    return false;
  }
}

function pluginStatusPresentation(status, enabled, pendingActivation) {
  if (pendingActivation) return { label: "等待激活", tone: "warning" };
  if (!enabled || status === "disabled") return { label: "已停用", tone: "muted" };
  if (status === "active") return { label: "运行中", tone: "ready" };
  if (status === "waiting_dependency") return { label: "等待依赖", tone: "warning" };
  if (["loading", "starting"].includes(status)) return { label: "启动中", tone: "warning" };
  return { label: "不可用", tone: "danger" };
}

function normalizePluginDependencySupply(value) {
  const source = asObject(value);
  const kind = text(source.kind);
  const status = text(source.status);
  if (!kind && !status) return null;
  const kindLabels = {
    wheelhouse: "离线 wheelhouse",
    package_index: "受控包索引",
    host_only: "仅宿主提供"
  };
  const statusLabels = {
    ready: "已就绪",
    configured: "已配置",
    unconfigured: "未配置",
    unavailable: "不可用"
  };
  const tone = ["ready", "configured"].includes(status) ? "ready" : ["unavailable"].includes(status) ? "danger" : "warning";
  return {
    kind,
    kindLabel: kindLabels[kind] || "依赖供应",
    configured: source.configured === true,
    status,
    statusLabel: statusLabels[status] || (status ? `状态：${status}` : "待确认"),
    tone
  };
}

function normalizePluginDependencyErrors(value) {
  return (Array.isArray(value) ? value : []).slice(0, 8).map((item) => {
    const entry = asObject(item);
    return {
      reason: text(entry.reason),
      serviceId: text(entry.service_id),
      selectedProvider: text(entry.selected_provider),
      requiredService: text(entry.required_service),
      diagnosticId: text(entry.diagnostic_id)
    };
  }).filter((item) => item.reason || item.serviceId || item.selectedProvider || item.requiredService);
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
      adapter: text(entry.adapter),
      type: text(entry.type),
      status: text(entry.status) || "missing_config",
      statusLabel: text(entry.statusLabel) || "待配置",
      statusTone: text(entry.statusTone) || "warning",
      reason: text(entry.reason),
      enabled: Boolean(entry.enabled),
      configured: Boolean(entry.configured),
      endpoint: text(entry.endpoint),
      defaultEndpoint: text(entry.defaultEndpoint),
      usedByLabel: text(entry.usedByLabel),
      voiceProfiles: normalizeAbilityVoiceProfiles(entry.voiceProfiles),
      defaultVoiceProfile: voiceProfileIdOf(entry.defaultVoiceProfile) ? normalizeAbilityVoiceProfile(entry.defaultVoiceProfile) : null,
      actionsEnabled: entry.actionsEnabled !== false && Boolean(text(entry.id))
    };
  }).filter((item) => item.title);
}

function normalizeAbilityVoiceProfiles(value) {
  return (Array.isArray(value) ? value : [])
    .map((item) => normalizeAbilityVoiceProfile(item))
    .filter((item) => item.voiceProfileId);
}

function voiceProfileIdOf(value) {
  const entry = asObject(value);
  return text(entry.voiceProfileId) || text(entry.id);
}

function normalizeAbilityVoiceProfile(value) {
  const entry = asObject(value);
  const voiceProfileId = voiceProfileIdOf(entry);
  return {
    voiceProfileId,
    providerId: text(entry.providerId),
    name: text(entry.name) || voiceProfileId || "GPT-SoVITS 声线",
    enabled: entry.enabled !== false,
    configured: Boolean(entry.configured),
    status: text(entry.status) || "missing_config",
    statusLabel: text(entry.statusLabel) || "待配置",
    statusTone: text(entry.statusTone) || "warning",
    reason: text(entry.reason),
    textLang: text(entry.textLang) || "zh",
    promptLang: text(entry.promptLang) || "zh",
    mediaType: text(entry.mediaType) || "wav",
    parallelInfer: typeof entry.parallelInfer === "boolean" ? entry.parallelInfer : null,
    splitBucket: typeof entry.splitBucket === "boolean" ? entry.splitBucket : null,
    batchSize: optionalFiniteNumber(entry.batchSize),
    topK: optionalFiniteNumber(entry.topK),
    topP: optionalFiniteNumber(entry.topP),
    temperature: optionalFiniteNumber(entry.temperature),
    speedFactor: optionalFiniteNumber(entry.speedFactor),
    fragmentInterval: optionalFiniteNumber(entry.fragmentInterval),
    textSplitMethod: text(entry.textSplitMethod),
    referenceAudioName: text(entry.referenceAudioName),
    promptTextLength: Math.max(0, Math.round(finiteNumber(entry.promptTextLength, 0))),
    emotionSampleCount: Math.max(0, Math.round(finiteNumber(entry.emotionSampleCount, 0))),
    emotionSamples: (Array.isArray(entry.emotionSamples) ? entry.emotionSamples : []).map((sample) => {
      const item = asObject(sample);
      return {
        emotionId: text(item.emotionId),
        aliases: (Array.isArray(item.aliases) ? item.aliases : []).map(text).filter(Boolean),
        referenceAudioName: text(item.referenceAudioName),
        promptTextLength: Math.max(0, Math.round(finiteNumber(item.promptTextLength, 0)))
      };
    }).filter((sample) => sample.emotionId),
    updatedAt: text(entry.updatedAt)
  };
}

function optionalFiniteNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
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
      transport: text(entry.transport) || "stdio",
      executionLocation: text(entry.executionLocation) || "host",
      executionLocationLabel: text(entry.executionLocationLabel) || "Akane Host",
      executionLocationDetail: text(entry.executionLocationDetail),
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

function voiceProfileActionAvailability(connected, providers, operation) {
  const voiceProviders = providers.filter((entry) => entry.adapter === "gpt_sovits" || entry.type === "tts_provider" || entry.voiceProfiles.length);
  const available = connected && voiceProviders.some((entry) => entry.actionsEnabled !== false) && (operation !== "test" || voiceProviders.some((entry) => entry.voiceProfiles.some((profile) => profile.enabled)));
  return {
    available,
    reason: !connected ? "桌宠尚未连接" : operation === "test" ? "还没有可试听的声线档案" : "当前没有可配置的语音服务"
  };
}

function normalizeCharacterVoicePreference(value) {
  const source = asObject(value);
  return {
    provider: text(source.provider),
    profileId: text(source.profileId) || text(source.profile_id),
    notes: text(source.notes)
  };
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
