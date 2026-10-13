import { MODEL_SERVICE_ACTIONS } from "../model-service.js";
import { QQ_SETUP_ACTIONS } from "../qq-setup.js";
import { SCREEN_OBSERVATION_COMMANDS } from "../../screen-observation.js";

export function createActionAvailability({ connected, liveSnapshotStatus, active, live, character, abilities, model, voice, music, system, qqSetup, bots }) {
  return {
    [QQ_SETUP_ACTIONS.detect]: { available: qqSetup.supported && !qqSetup.busy, reason: qqSetup.detail },
    [QQ_SETUP_ACTIONS.select]: { available: qqSetup.supported && !qqSetup.blocked && !qqSetup.busy && !qqSetup.native.starting, reason: qqSetup.detail },
    [QQ_SETUP_ACTIONS.start]: { available: qqSetup.supported && !qqSetup.blocked && !qqSetup.busy && qqSetup.native.installed && !qqSetup.native.starting, reason: qqSetup.detail },
    [QQ_SETUP_ACTIONS.openLogin]: { available: qqSetup.supported && !qqSetup.blocked && !qqSetup.busy && qqSetup.native.webuiReady, reason: qqSetup.detail },
    [QQ_SETUP_ACTIONS.openFolder]: { available: qqSetup.supported && !qqSetup.blocked && !qqSetup.busy && qqSetup.native.installed, reason: qqSetup.detail },
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
    "character.selectPack": { available: connected && character.availablePacks.length > 0, reason: connected ? "没有可切换的角色包" : "桌宠尚未连接" },
    "character.setOutfit": { available: connected && character.outfits.length > 0, reason: connected ? "当前角色没有服装资源" : "桌宠尚未连接" },
    "character.previewEmotion": { available: connected && character.emotions.length > 0, reason: connected ? "当前服装没有表情资源" : "桌宠尚未连接" },
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
      available: connected && Boolean(character.packId) && abilities.providers.some((entry) => entry.voiceProfiles.some((profile) => profile.enabled)),
      reason: !connected ? "桌宠尚未连接" : !character.packId ? "当前没有可绑定的角色包" : "还没有可用的声线档案"
    },
    "abilities.provider.voiceProfile.clearCurrentCharacter": {
      available: connected && Boolean(character.packId) && Boolean(character.voice.profileId),
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
    ...Object.fromEntries(Object.values(MODEL_SERVICE_ACTIONS).map(id => [id, {
      available: model.available, reason: model.connected ? "模型配置接口暂不可用" : "桌宠尚未连接"
    }])),
    "voice.test": voiceActionAvailability(voice.controlsAvailable && !voice.speaking, "当前正在播放语音"),
    "voice.stop": voiceActionAvailability(voice.controlsAvailable && voice.speaking, "当前没有正在播放的语音"),
    "voice.previewPlay": voiceActionAvailability(voice.controlsAvailable && !voice.speaking, "当前正在播放语音"),
    "voice.setTtsEnabled": voiceActionAvailability(voice.controlsAvailable),
    "voice.setAsrEnabled": voiceActionAvailability(voice.controlsAvailable),
    "voice.setVolume": voiceActionAvailability(voice.controlsAvailable),
    "perception.runDiagnostics": voiceActionAvailability(system.controlsAvailable),
    ...Object.fromEntries(Object.keys(SCREEN_OBSERVATION_COMMANDS).map((id) => [id, voiceActionAvailability(system.controlsAvailable)])),
    "advanced.setHitTestEnabled": voiceActionAvailability(system.controlsAvailable),
    "advanced.setHitboxOverlay": voiceActionAvailability(system.controlsAvailable),
    "advanced.resetWindow": voiceActionAvailability(system.controlsAvailable),
    "music.togglePlayback": { available: music.available, reason: "当前没有可控制的音乐" },
    "window.minimize": { available: true, reason: "" },
    "window.maximize": { available: true, reason: "" },
    "window.close": { available: true, reason: "" }
  };
}

function capabilityActionAvailability(connected, entries) {
  const available = connected && entries.some((entry) => entry.actionsEnabled !== false);
  return { available, reason: connected ? "当前没有可配置的项目" : "桌宠尚未连接" };
}

function voiceProfileActionAvailability(connected, providers, operation) {
  const voiceProviders = providers.filter((entry) => entry.adapter === "gpt_sovits" || entry.type === "tts_provider" || entry.voiceProfiles.length);
  const available = connected && voiceProviders.some((entry) => entry.actionsEnabled !== false
    && (operation !== "test" || entry.voiceProfiles.some((profile) => profile.enabled)));
  return {
    available,
    reason: !connected ? "桌宠尚未连接" : operation === "test" ? "还没有可试听的声线档案" : "当前没有可配置的语音服务"
  };
}

function voiceActionAvailability(available, unavailableReason = "请在桌面端控制中心中调整") {
  return { available: Boolean(available), reason: available ? "" : unavailableReason };
}
