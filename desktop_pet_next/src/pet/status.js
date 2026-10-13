import { MEDIA_CONTROL_TARGETS } from "../media-control.js";

/** Owns status behavior and its temporary runtime state. */
export function createStatusController({
  resourceState,
  els,
  bubble: bubblePort,
  settings: settingsPort,
  music: musicPort,
  chat: chatPort,
  character: characterPort,
  window: windowPort
}) {
  let runtimeMode = "idle";

  function setStatus(message, { transient = true, durationMs = 1800 } = {}) {
    setRuntimeStatus(message);
    bubblePort.showBubbleText(message, { transient, durationMs, kind: "status" });
  }

  function setRuntimeStatus(message, { mode = null } = {}) {
    if (mode) runtimeMode = mode;
    els.status.textContent = message;
    updateActivityControls();
    settingsPort.scheduleSettingsSnapshot();
  }

  function updateActivityControls() {
    const mediaControl = musicPort.buildActiveMediaControlSnapshot();
    if (els.stopReply) {
      els.stopReply.disabled = !chatPort.isReplyActive();
    }
    if (els.toggleMusic) {
      els.toggleMusic.disabled = !mediaControl.available || musicPort.musicLoading;
      els.toggleMusic.textContent = mediaControl.isPlaying ? "暂停音乐" : mediaControl.available ? "继续音乐" : "音乐";
    }
    if (els.stopMusic) {
      els.stopMusic.disabled = musicPort.musicLoading || !mediaControl.available;
    }
    if (els.clearMusicQueue) {
      els.clearMusicQueue.disabled = musicPort.musicLoading || (!musicPort.musicTrack && !musicPort.musicQueue.length);
    }
    if (els.previousMusic) {
      els.previousMusic.disabled = musicPort.musicLoading || !mediaControl.available || (mediaControl.target === MEDIA_CONTROL_TARGETS.local && !musicPort.hasPreviousMusicTrack());
    }
    if (els.nextMusic) {
      els.nextMusic.disabled = musicPort.musicLoading || !mediaControl.available || (mediaControl.target === MEDIA_CONTROL_TARGETS.local && !musicPort.hasNextMusicTrack());
    }
  }

  function updateConnectionStatus() {
    const healthLabel = {
      online: "已连接",
      offline: "离线",
      checking: "检查中",
      unknown: "未知"
    }[resourceState.health] || "未知";
    const outfit = characterPort.getActiveOutfit();
    const count = characterPort.getActiveEmotions().length;
    const source = characterPort.resourceSourceLabel(resourceState.source);
    const contract = resourceState.contractVersion || (resourceState.contractSource === "legacy" ? "legacy" : "");
    els.connectionStatus.textContent = `后端：${healthLabel}${contract ? ` · ${contract}` : ""} · ${source} · ${outfit.id}(${count})`;
    els.connectionStatus.title = `点击重新检查后端与资源${resourceState.healthEndpoint ? ` · ${resourceState.healthEndpoint}` : ""}`;
    windowPort.updateMenuLabels();
  }

  return {
    get runtimeMode() { return runtimeMode; },
    setStatus,
    setRuntimeStatus,
    updateActivityControls,
    updateConnectionStatus
  };
}
