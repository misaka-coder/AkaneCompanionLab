import { createActionAvailability } from "./view-model/availability.js";
export { normalizeActionPresentation, isObservedActionConfirmation, observedActionOutcome } from "./view-model/action-results.js";
import { createCharacterViewModel } from "./view-model/character.js";
import { createChatViewModel, normalizeBotCatalog } from "./view-model/conversation.js";
import { createPerceptionViewModel, deriveSetupReadiness, deriveActivity, deriveMusic, normalizeRecentOutputs } from "./view-model/overview.js";
import { normalizeSystemRuntime } from "./view-model/system.js";
import { normalizeVoiceRuntime } from "./view-model/voice.js";
import { asObject, text } from "./view-model/shared.js";
import { createAbilitiesViewModel } from "./view-model/capabilities.js";
import { normalizeModelServiceRuntime } from "./model-service.js";
import { normalizeQqSetup } from "./qq-setup.js";

export function createControlCenterViewModel(rawSnapshot, runtimeSnapshot = null) {
  const raw = asObject(rawSnapshot);
  const runtime = asObject(raw.overviewRuntime);
  const live = asObject(runtimeSnapshot);
  const active = asObject(live.active);
  const petState = asObject(live.state);
  const musicRuntime = asObject(raw.musicRuntime);
  const connected = raw.sourceKind === "backend" && !raw.fallbackReason;
  const bridgeStatus = asObject(raw.controlCenterV2);
  const liveSnapshotStatus = text(bridgeStatus.liveSnapshotStatus) || "not-applicable";
  const character = createCharacterViewModel(raw.characterRuntime, live, runtime);
  const activity = deriveActivity(live, connected);
  const music = deriveMusic(asObject(musicRuntime.nowPlaying), musicRuntime);
  const outputs = normalizeRecentOutputs(runtime.recentOutputs);
  const abilities = createAbilitiesViewModel(raw, { connected, liveSnapshotStatus });
  const model = normalizeModelServiceRuntime(raw.modelRuntime, connected);
  const voice = normalizeVoiceRuntime(raw.voiceRuntime, live, connected);
  const setup = deriveSetupReadiness({
    connected,
    packId: character.packId,
    displayName: character.displayName,
    characterWarnings: character.resourceWarnings,
    model,
    abilities,
    voice
  });
  const system = normalizeSystemRuntime(raw, live, {
    connected,
    liveSnapshotStatus,
    characterWarnings: character.resourceWarnings,
    voice
  });
  const qqSetup = normalizeQqSetup(raw.qqSetupRuntime, liveSnapshotStatus !== "not-applicable", raw.backendUrl);
  const instanceLabel = text(petState.instanceId) || text(raw.controlCenterRuntime?.health?.data?.instance_id) || "本地实例";
  const chat = createChatViewModel(raw.chatSession, live, character, raw.chatRuntime);
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
    character,
    activity,
    perception: createPerceptionViewModel(live, connected),
    chat,
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
    actions: createActionAvailability({ connected, liveSnapshotStatus, active, live, character, abilities, model, voice, music, system, qqSetup, bots })
  };
}
