import { SETTINGS_COMMAND_EVENT, SETTINGS_SNAPSHOT_EVENT } from "../control-center/event-bridge.js";
import { performChatFileAction } from "../chat-file-action.js";
import { buildCharacterSnapshot } from "../character-profile.js";
import { normalizeCareState } from "../care-feature.js";
import { normalizeScreenObservationSettings } from "../screen-observation.js";
import { getProfileCareConfig, getProfileDefaultOutfit } from './profile.js';
import { friendlyErrorMessage, formatError, shortId } from './utils.js';

/** Owns settings behavior and its temporary runtime state. */
export function createSettingsController({
  state,
  resourceState,
  els,
  visualRenderer,
  platform: platformPort,
  lifecycle,
  character: characterPort,
  care: carePort,
  window: windowPort,
  input: inputPort,
  backend: backendPort,
  voice: voicePort,
  perception: perceptionPort,
  persistence: statePort,
  chat: chatPort,
  tts: ttsPort,
  status: statusPort,
  systemMedia: systemMediaPort,
  music: musicPort,
  attachments: attachmentsPort,
  interaction: interactionPort,
  bubble: bubblePort
}) {
  let settingsSnapshotTimer = 0;

  let settingsBridgeRegistered = false;

  let lastSettingsCommandResult = null;

  let musicSnapshotTimer = 0;

  async function registerSettingsBridge() {
    if (!platformPort.isTauriRuntime || settingsBridgeRegistered) return;

    const unlisten = await platformPort.listen(SETTINGS_COMMAND_EVENT, (event) => {
      void handleSettingsCommand(event.payload);
    });
    lifecycle.track(unlisten);
    settingsBridgeRegistered = true;
    // Close the startup race where the settings window requests a snapshot
    // before the main window has finished registering this listener.
    await broadcastSettingsSnapshot();
  }

  const commandRevisions = new Map();

  async function handleSettingsCommand(payload) {
    const command = String(payload?.command || "").trim();
    if (!command || lifecycle.disposed) return null;
    const revision = (commandRevisions.get(command) || 0) + 1;
    commandRevisions.set(command, revision);
    const isLatest = () => !lifecycle.disposed && commandRevisions.get(command) === revision;
    try {
      const result = await executeSettingsCommand(payload);
      if (isLatest() && result) lastSettingsCommandResult = result;
      if (isLatest()) scheduleSettingsSnapshot(payload.operationId ? 0 : 40);
      return result;
    } catch (error) {
      const result = { command, operationId: String(payload?.operationId || "").trim(),
        ok: false, status: "failed", reason: formatError(error), boundBotId: state.boundBotId, at: Date.now() };
      if (isLatest()) {
        lastSettingsCommandResult = result;
        statusPort.setStatus(`设置命令失败：${result.reason}`);
        scheduleSettingsSnapshot(0);
      }
      return result;
    }
  }

  async function executeSettingsCommand(payload) {
    const command = String(payload?.command || "").trim();
    if (!command) return;
    const operationId = String(payload?.operationId || "").trim();
    let commandHandled = true;
    let commandResult = null;
    if (["buyShopItem", "feedInventoryItem", "startCareWork", "claimCareAllowance"].includes(command)
        && ((payload.characterPackId && payload.characterPackId !== characterPort.getCurrentCharacterPackId())
            || (payload.boundBotId && payload.boundBotId !== state.boundBotId))) {
      carePort.notifyShopStatus("角色或连接已切换，请刷新商店后重试。", "warn");
      throw new Error("shop_scope_changed");
    }

    switch (command) {
      case "requestSnapshot":
        await broadcastSettingsSnapshot();
        break;
      case "openInput":
        windowPort.closeMenu();
        inputPort.showChatInput();
        break;
      case "openWorkspace":
        await windowPort.openWorkspaceWindow();
        break;
      case "openShop":
        await windowPort.openShopWindow();
        break;
      case "openWorkshop":
        await windowPort.openWorkshopWindow();
        break;
      case "setBackendUrl": {
        const changed = await backendPort.updateBackendUrl(payload.value);
        commandResult = { command, operationId, ok: Boolean(changed), status: changed ? "completed" : "failed",
          reason: changed ? "" : backendPort.backendSwitchError || "backend_binding_rejected", at: Date.now() };
        break;
      }
      case "setBoundBot":
        await backendPort.updateBoundBot(payload.value);
        commandResult = {
          command,
          ok: true,
          status: "completed",
          operationId,
          boundBotId: state.boundBotId,
          at: Date.now()
        };
        break;
      case "setOutfit":
        await characterPort.updateOutfit(payload.value);
        break;
      case "setCharacterPack":
        await characterPort.updateCharacterPack(payload.value);
        break;
      case "refreshCharacterPacks":
        await characterPort.refreshCharacterPacksFromSettings(payload.value);
        break;
      case "setScale":
        windowPort.updateVisualScale(Number(payload.value), { commitNow: true });
        break;
      case "setOpacity":
        windowPort.updateVisualOpacity(Number(payload.value), { saveNow: true });
        break;
      case "setVoiceEnabled":
        voicePort.setVoiceEnabled(Boolean(payload.value));
        break;
      case "setVoiceInputEnabled":
        voicePort.setVoiceInputEnabled(Boolean(payload.value));
        break;
      case "setVoiceVolume":
        voicePort.setVoiceVolume(Number(payload.value));
        break;
      case "setScreenVisionEnabled":
        await perceptionPort.setScreenVisionEnabled(Boolean(payload.value));
        commandResult = {
          command,
          ok: state.screenVisionEnabled === Boolean(payload.value),
          status: state.screenVisionEnabled === Boolean(payload.value) ? "completed" : "failed",
          reason: state.screenVisionEnabled === Boolean(payload.value) ? "" : perceptionPort.screenVisionError || "screen_capture_unavailable",
          operationId,
          at: Date.now()
        };
        break;
      case "setScreenVisionPacking":
        perceptionPort.setScreenObservationSetting("screenVisionPacking", payload.value);
        break;
      case "setProactiveWakeEnabled":
        perceptionPort.setProactiveWakeEnabled(Boolean(payload.value));
        break;
      case "setProactiveWakeIntervalSec":
        perceptionPort.setProactiveWakeIntervalSec(Number(payload.value));
        break;
      case "setScreenVisionSampleIntervalSec":
        perceptionPort.setScreenObservationSetting("screenVisionSampleIntervalSec", Number(payload.value));
        break;
      case "setScreenVisionWindowSec":
        perceptionPort.setScreenObservationSetting("screenVisionWindowSec", Number(payload.value));
        break;
      case "setScreenVisionMaxEdge":
        perceptionPort.setScreenObservationSetting("screenVisionMaxEdge", Number(payload.value));
        break;
      case "setScreenVisionSheetCount":
        perceptionPort.setScreenObservationSetting("screenVisionSheetCount", Number(payload.value));
        break;
      case "setScreenVisionFrameCount":
        perceptionPort.setScreenObservationSetting("screenVisionFrameCount", Number(payload.value));
        break;
      case "clearScreenVision":
        perceptionPort.clearScreenVisionWorkspace();
        break;
      case "setRestoreLatestOnStartup":
        statePort.setRestoreLatestOnStartup(Boolean(payload.value));
        break;
      case "stopReply":
        chatPort.interruptReply({ announce: true });
        break;
      case "stopTts":
        ttsPort.stopTts();
        statusPort.setRuntimeStatus("语音已停止", { mode: "stopped" });
        break;
      case "testTts":
        await ttsPort.testTts();
        break;
      case "previewTts":
        await ttsPort.previewTts(payload.value);
        break;
      case "previousMusic":
        commandResult = await systemMediaPort.controlActiveMusic("previous", payload);
        break;
      case "nextMusic":
        commandResult = await systemMediaPort.controlActiveMusic("next", payload);
        break;
      case "playMusicTrack":
        await musicPort.playMusicTrackBySourceId(payload.value);
        break;
      case "playWorkspaceAudio": {
        const raw = payload.value && typeof payload.value === "object" ? payload.value : payload;
        const played = await musicPort.playWorkspaceAudioItem({ itemType: raw.itemType, handle: raw.handle, title: raw.title });
        commandResult = { command, operationId, ok: Boolean(played), status: played ? "completed" : "failed", reason: played ? "" : "workspace_audio_unavailable", at: Date.now() };
        break;
      }
      case "removeMusicTrack":
        await musicPort.removeMusicTrackBySourceId(payload.value);
        break;
      case "buyShopItem":
        await carePort.buyShopItem(payload.value);
        break;
      case "feedInventoryItem":
        await carePort.feedInventoryItem(payload.value);
        break;
      case "startCareWork":
        await carePort.startCareWork();
        break;
      case "claimCareAllowance":
        await carePort.claimCareAllowance();
        break;
      case "toggleMusic":
        await musicPort.toggleMusicPlayback();
        break;
      case "toggleActiveMusic":
        commandResult = await systemMediaPort.controlActiveMusic("toggle", payload);
        break;
      case "controlActiveMusic": {
        const raw = payload.value && typeof payload.value === "object" ? payload.value : payload;
        commandResult = await systemMediaPort.controlActiveMusic(raw.action || payload.action || payload.value, payload);
        break;
      }
      case "seekMusic":
        musicPort.seekMusicPlayback(Number(payload.value));
        break;
      case "stopMusic":
        commandResult = await systemMediaPort.controlActiveMusic("stop", payload);
        break;
      case "clearMusicQueue":
        musicPort.clearMusicQueue({ announce: true });
        break;
      case "setAlwaysOnTop":
        await windowPort.setAlwaysOnTop(Boolean(payload.value));
        break;
      case "setSkipTaskbar":
        await windowPort.setSkipTaskbar(Boolean(payload.value));
        break;
      case "setHitTestEnabled":
        await windowPort.setHitTestEnabled(Boolean(payload.value));
        break;
      case "setHitboxOverlay":
        windowPort.setHitboxOverlay(Boolean(payload.value));
        break;
      case "toggleWebgl":
        windowPort.toggleWebglProbe();
        break;
      case "probeClickThrough":
        await windowPort.probeClickThrough(5000);
        break;
      case "resetWindow":
        await windowPort.resetWindowPlacement();
        break;
      case "resetVisuals":
        await windowPort.resetVisuals();
        break;
      case "sendChatMessage": {
        if (attachmentsPort.pendingAttachments.importing() || (payload.draftToken && String(payload.draftToken) !== attachmentsPort.pendingAttachments.token())) {
          commandResult = { command, operationId, ok: false, status: "failed",
            reason: attachmentsPort.pendingAttachments.importing() ? "attachments_uploading" : "attachment_scope_changed", at: Date.now() };
          scheduleSettingsSnapshot();
          break;
        }
        const text = String(payload.value ?? payload.text ?? "").trim() || (attachmentsPort.pendingAttachments.list().length ? "请查看这些附件。" : "");
        if (!text) {
          commandResult = {
            command,
            ok: false,
            status: "invalid-payload",
            reason: "message_required",
            operationId,
            at: Date.now()
          };
          scheduleSettingsSnapshot();
          break;
        }
        if (perceptionPort.proactiveWakeRunning) chatPort.interruptReply({ announce: false });
        if (chatPort.sending) {
          const accepted = await chatPort.sendMessage(text);
          commandResult = {
            command,
            ok: Boolean(accepted),
            status: accepted ? "accepted" : "failed",
            reason: accepted ? "" : "steer_not_accepted",
            operationId,
            at: Date.now()
          };
          scheduleSettingsSnapshot();
          break;
        }
        commandResult = {
          command,
          ok: true,
          status: "accepted",
          operationId,
          messageLength: text.length,
          at: Date.now()
        };
        void chatPort.sendMessage(text);
        break;
      }
      case "chatFileAction": {
        try {
          if (String(payload.draftToken || "") !== attachmentsPort.pendingAttachments.token()) throw new Error("attachment_scope_changed");
          const result = await performChatFileAction(payload, { scope: attachmentsPort.attachmentDraftScope(), invoke: platformPort.invoke,
            play: musicPort.playWorkspaceAudioItem, isPlaying: item => Boolean(musicPort.musicPlaying && musicPort.musicQueue[musicPort.musicQueueIndex]?.workspaceHandle === item.handle
              && musicPort.musicQueue[musicPort.musicQueueIndex]?.workspaceItemType === item.itemType) });
          commandResult = { command, operationId, ...result, at: Date.now() };
        } catch (error) {
          commandResult = { command, operationId, ok: false, status: "failed", reason: friendlyErrorMessage(formatError(error)), at: Date.now() };
        }
        scheduleSettingsSnapshot();
        break;
      }
      case "attachChatFiles":
      case "removeChatAttachment":
      case "playChatAttachment": {
        try {
          if (String(payload.draftToken || "") !== attachmentsPort.pendingAttachments.token()) throw new Error("attachment_scope_changed");
          let result = { ok: true, status: "completed" };
          if (command === "attachChatFiles") {
            const token = attachmentsPort.pendingAttachments.token();
            const paths = payload.files ? null : (Array.isArray(payload.paths) ? payload.paths : await platformPort.invoke("choose_workspace_files"));
            if (token !== attachmentsPort.pendingAttachments.token()) throw new Error("attachment_scope_changed");
            if (paths && !paths.length) result = { ok: false, status: "cancelled", reason: "picker_cancelled" };
            else result = payload.files ? await attachmentsPort.importClipboardAttachments(payload.files) : await attachmentsPort.importDroppedFilesToWorkspace(paths);
          } else {
            const item = attachmentsPort.pendingAttachments.list().find(item => item.attachmentId === payload.attachmentId);
            if (!item) throw new Error("attachment_not_in_draft");
            if (command === "removeChatAttachment") attachmentsPort.pendingAttachments.remove(item.attachmentId);
            else {
              await musicPort.playWorkspaceAudioItem(item);
              result = { ok: Boolean(musicPort.musicPlaying && musicPort.musicQueue[musicPort.musicQueueIndex]?.workspaceHandle === item.handle), status: "completed" };
            }
          }
          commandResult = { command, operationId, ok: Boolean(result.ok), status: result.status || (result.ok ? "completed" : "failed"), reason: result.reason || "", at: Date.now() };
        } catch (error) {
          commandResult = { command, operationId, ok: false, status: "failed", reason: friendlyErrorMessage(formatError(error)), at: Date.now() };
        }
        scheduleSettingsSnapshot();
        break;
      }
      case "reloadResources":
        commandResult = { command, operationId, ok: await characterPort.reloadCharacterResources({ userTriggered: true }), at: Date.now() };
        commandResult.status = commandResult.ok ? "completed" : "failed";
        commandResult.reason = commandResult.ok ? "" : resourceState.healthMessage || "resource_reload_failed";
        break;
      case "previewEmotion":
        if (!interactionPort.previewEmotion(payload.value)) {
          commandResult = {
            command,
            ok: false,
            status: "busy",
            reason: chatPort.sending ? "reply_in_progress" : "emotion_required",
            operationId,
            at: Date.now()
          };
        }
        break;
      case "closePet":
        await windowPort.closePetWindow();
        break;
      case "setMusicPlayMode":
        musicPort.setMusicPlayMode(payload.value);
        break;
      case "setMusicVolumeNormalization":
        musicPort.setMusicVolumeNormalization(payload.value);
        break;
      case "smtcAction":
        commandResult = { command, operationId,
          ...await systemMediaPort.controlSystemMediaPlayback(payload.action, { operationId }), at: Date.now() };
        break;
      default:
        statusPort.setRuntimeStatus(`未知设置命令：${command}`);
        commandHandled = false;
        break;
    }

    if (!commandHandled) {
      return {
        command,
        ok: false,
        status: "not-supported",
        reason: "unknown_settings_command",
        ...(operationId ? { operationId } : {}),
        at: Date.now()
      };
    }

    if (commandResult && typeof commandResult === "object") {
      const ok = typeof commandResult.ok === "boolean"
        ? commandResult.ok
        : !["failed", "error", "busy", "blocked", "rejected", "timed_out", "not-supported", "unavailable", "invalid-payload", "invalid_action"].includes(String(commandResult.status || ""));
      commandResult = {
        ...commandResult,
        command,
        ok,
        status: commandResult.status || (ok ? "completed" : "failed"),
        ...(operationId ? { operationId } : {}),
        at: typeof commandResult.at === "number" ? commandResult.at : Date.now()
      };
    } else if (operationId) {
      commandResult = {
        command,
        ok: true,
        status: "completed",
        operationId,
        at: Date.now()
      };
    }
    return commandResult;
  }

  function scheduleSettingsSnapshot(delay = 40) {
    if (!platformPort.isTauriRuntime) return;
    lifecycle.clearTimeout(settingsSnapshotTimer);
    settingsSnapshotTimer = lifecycle.setTimeout(() => {
      void broadcastSettingsSnapshot();
    }, delay);
  }

  function scheduleMusicSnapshot(delay = 420) {
    if (!platformPort.isTauriRuntime || musicSnapshotTimer) return;
    musicSnapshotTimer = lifecycle.setTimeout(() => {
      musicSnapshotTimer = 0;
      void broadcastSettingsSnapshot();
    }, delay);
  }

  async function broadcastSettingsSnapshot() {
    if (!platformPort.isTauriRuntime) return;
    const payload = buildSettingsSnapshot();
    await Promise.allSettled([
      platformPort.emitTo("settings", SETTINGS_SNAPSHOT_EVENT, payload),
      platformPort.emitTo("workshop", SETTINGS_SNAPSHOT_EVENT, payload),
      platformPort.emitTo("workspace", SETTINGS_SNAPSHOT_EVENT, payload),
      platformPort.emitTo("shop", SETTINGS_SNAPSHOT_EVENT, payload)
    ]);
    try {
      await platformPort.emit(SETTINGS_SNAPSHOT_EVENT, payload);
    } catch {
      // The settings window may not be open yet.
    }
  }

  function buildSettingsSnapshot() {
    const activeOutfit = characterPort.getActiveOutfit();
    const emotions = characterPort.getActiveEmotions();
    const issues = characterPort.buildResourceIssues(activeOutfit, emotions);
    return {
      character: buildCharacterSnapshot(),
      settingsCommandResult: lastSettingsCommandResult ? { ...lastSettingsCommandResult } : null,
      state: {
        instanceId: state.instanceId,
        hostId: state.hostId,
        boundBotId: state.boundBotId,
        scale: state.scale,
        opacity: state.opacity,
        skipTaskbar: state.skipTaskbar,
        alwaysOnTop: state.alwaysOnTop,
        backendUrl: state.backendUrl,
        profileUserId: state.profileUserId,
        characterPackId: state.characterPackId,
        characterRuntimeKey: characterPort.getCharacterRuntimeKey(state.characterPackId),
        characters: characterPort.buildCharacterRuntimeSnapshot(),
        care: carePort.isCareRuntimeActive() ? normalizeCareState(state.care, getProfileCareConfig()) : null,
        sessionId: state.sessionId,
        outfit: state.outfit,
        currentEmotion: state.currentEmotion,
        restoreLatestOnStartup: state.restoreLatestOnStartup,
        voiceEnabled: state.voiceEnabled,
        voiceInputEnabled: state.voiceInputEnabled,
        voiceVolume: state.voiceVolume,
        screenVisionEnabled: state.screenVisionEnabled,
        ...normalizeScreenObservationSettings(state),
        proactiveWakeEnabled: state.proactiveWakeEnabled,
        proactiveWakeIntervalSec: state.proactiveWakeIntervalSec,
        hitTestEnabled: state.hitTestEnabled,
        hitboxOverlay: state.hitboxOverlay,
        musicPlayMode: state.musicPlayMode,
        musicVolumeNormalization: state.musicVolumeNormalization
      },
      resource: {
        health: resourceState.health,
        healthMessage: resourceState.healthMessage,
        healthEndpoint: resourceState.healthEndpoint,
        contractVersion: resourceState.contractVersion,
        contractSource: resourceState.contractSource,
        capabilities: Array.isArray(resourceState.capabilities) ? [...resourceState.capabilities] : [],
        endpoints: { ...(resourceState.endpoints || {}) },
        tts: { ...(resourceState.tts || {}) },
        asr: { ...(resourceState.asr || {}) },
        features: {
          care: { ...carePort.getCareFeatureStatus() }
        },
        source: resourceState.source,
        activeOutfit: activeOutfit.id || getProfileDefaultOutfit(),
        activeOutfitName: activeOutfit.name || activeOutfit.id || getProfileDefaultOutfit(),
        requestedOutfit: state.outfit || getProfileDefaultOutfit(),
        defaultOutfit: characterPort.getManifestDefaultOutfit(resourceState.manifest),
        defaultEmotion: characterPort.getManifestDefaultEmotion(resourceState.manifest),
        emotionCount: emotions.length,
        outfits: characterPort.getAvailableOutfits().map(characterPort.serializeOutfit).filter((item) => item.id),
        emotions: emotions.map(characterPort.serializeEmotion).filter((item) => item.id),
        missingRequired: issues.missingRequired,
        missingRecommended: issues.missingRecommended,
        loadedAt: resourceState.loadedAt,
        sessionShort: shortId(state.sessionId),
        retrying: Boolean(backendPort.backendRetryTimer)
      },
      runtimeStatus: els.status.textContent || "",
      pendingAttachments: attachmentsPort.pendingAttachments.list(),
      attachmentDraftToken: attachmentsPort.pendingAttachments.token(),
      attachmentUploadCount: attachmentsPort.pendingAttachments.importing(),
      runtimeMode: statusPort.runtimeMode,
      active: {
        sending: chatPort.sending,
        speaking:
          ttsPort.hasLocalTtsPlayback() ||
          Boolean(voicePort.realtimeVoiceTurn?.playbackActive) ||
          voicePort.hasRealtimeVoiceCallPlayback(),
        voiceInput: voicePort.voiceInputState,
        musicPlaying: musicPort.musicPlaying,
        musicPaused: musicPort.musicPaused,
        screenVision: perceptionPort.screenVisionStatus,
        screenVisionError: perceptionPort.screenVisionError,
        screenVisionFrameBufferSize: perceptionPort.screenObservation.snapshot(perceptionPort.screenObservationScope(), state).length,
        screenVisionBufferRevision: perceptionPort.screenObservation.revision,
        proactiveWake: state.proactiveWakeEnabled ? "enabled" : "off",
        proactiveWakeRunning: perceptionPort.proactiveWakeRunning,
        proactivePreparation: perceptionPort.proactivePreparation.status,
        proactivePreparationError: perceptionPort.proactivePreparation.error,
        proactiveWakeLastAt: perceptionPort.proactiveWakeLastAt,
        bubbleVisible: els.bubble.classList.contains("visible"),
        bubbleKind: bubblePort.bubbleKind,
        replyDisplayActive: bubblePort.replyDisplayActive
      },
      tts: {
        active: ttsPort.ttsActive,
        queueLength: ttsPort.ttsQueue.length
      },
      realtimeVoice: {
        active:
          Boolean(voicePort.realtimeVoiceTurn?.responseActive) ||
          Boolean(voicePort.realtimeVoiceCall?.active),
        callActive: Boolean(voicePort.realtimeVoiceCall?.active),
        callTurnCount: voicePort.realtimeVoiceCall?.turns?.size || 0,
        ready:
          Boolean(voicePort.realtimeVoiceTurn?.ready) ||
          Boolean(voicePort.realtimeVoiceCall?.inputTurn?.ready),
        committed:
          Boolean(voicePort.realtimeVoiceTurn?.committed) ||
          voicePort.hasRealtimeVoiceCallCommittedTurn(),
        playbackActive:
          Boolean(voicePort.realtimeVoiceTurn?.playbackActive) ||
          voicePort.hasRealtimeVoiceCallPlayback(),
        fallbackPending:
          Boolean(voicePort.realtimeVoiceTurn?.failure && !voicePort.realtimeVoiceTurn?.committed) ||
          voicePort.hasRealtimeVoiceCallFallbackPending()
      },
      visual: visualRenderer.getStatus(),
      music: musicPort.buildMusicSnapshot(),
      currentExpression: characterPort.buildCurrentExpressionSnapshot(),
      webglEnabled: els.stage.classList.contains("show-webgl")
    };
  }

  return {
    handleSettingsCommand, buildSettingsSnapshot,
    get lastSettingsCommandResult() { return lastSettingsCommandResult; },
    registerSettingsBridge,
    scheduleSettingsSnapshot,
    scheduleMusicSnapshot,
    broadcastSettingsSnapshot
  };
}
