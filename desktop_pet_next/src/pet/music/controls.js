import { resolveActiveMediaControl, normalizeMediaControlAction, resolveMediaControlAction, MEDIA_CONTROL_TARGETS } from '../../media-control.js';

/** Routes commands to the active local or system media owner. */
export function createMusicControlsController({
  playback: musicPlaybackPort, systemMedia: systemMediaPort, status: statusPort,
  settings: settingsPort, bubble: bubblePort
}) {


  function buildActiveMediaControlSnapshot() {
    return resolveActiveMediaControl({
      localTrack: musicPlaybackPort.musicTrack,
      localPlaying: musicPlaybackPort.musicPlaying,
      localPaused: musicPlaybackPort.musicPaused,
      systemMedia: systemMediaPort.systemMedia,
      systemControllable: systemMediaPort.isControllableSystemMedia(systemMediaPort.systemMedia)
    });
  }

  async function executeActiveMusicCommand(action, payload = {}) {
    const control = buildActiveMediaControlSnapshot();
    const normalized = normalizeMediaControlAction(action);
    const resolvedAction = resolveMediaControlAction(normalized, control);
    const operationId = String(payload?.operationId || "").trim();
    let outcome;

    if (!normalized) {
      outcome = { ok: false, status: "invalid_action", reason: "invalid_media_action" };
    } else if (control.target === MEDIA_CONTROL_TARGETS.none) {
      bubblePort.showBubbleText("现在没有可控制的音乐。", { transient: true, durationMs: 1800, kind: "music" });
      statusPort.setRuntimeStatus("暂无可控制的音乐", { mode: "idle" });
      outcome = { ok: false, status: "unavailable", reason: "no_active_media" };
    } else if (control.target === MEDIA_CONTROL_TARGETS.system) {
      const result = await systemMediaPort.controlSystemMediaPlayback(resolvedAction, { operationId });
      outcome = {
        ok: Boolean(result?.ok),
        status: result?.ok ? "completed" : result?.status || "failed",
        reason: result?.reason || "",
        providerStatus: result?.status || "",
        playbackStatus: result?.playbackStatus || result?.playback_status || ""
      };
    } else {
      let ok = false;
      if (resolvedAction === "play") {
        ok = musicPlaybackPort.musicPlaying ? true : await musicPlaybackPort.toggleMusicPlayback();
      } else if (resolvedAction === "pause") {
        ok = musicPlaybackPort.musicPlaying ? await musicPlaybackPort.toggleMusicPlayback() : musicPlaybackPort.musicPaused;
      } else if (resolvedAction === "next") {
        ok = await musicPlaybackPort.playNextMusicTrack();
      } else if (resolvedAction === "previous") {
        ok = await musicPlaybackPort.playPreviousMusicTrack();
      } else if (resolvedAction === "stop") {
        musicPlaybackPort.stopMusic({ announce: true });
        ok = true;
      }
      outcome = {
        ok,
        status: ok ? "completed" : "failed",
        reason: ok ? "" : `local_media_${resolvedAction}_failed`,
        playbackStatus: buildActiveMediaControlSnapshot().playbackStatus
      };
    }

    const commandResult = {
      command: "controlActiveMusic",
      operationId,
      target: control.target,
      targetId: control.targetId,
      action: resolvedAction || normalized,
      ...outcome,
      at: Date.now()
    };
    settingsPort.scheduleSettingsSnapshot(0);
    return commandResult;
  }

  function notifyMusicActivityUnavailable(message) {
    statusPort.setRuntimeStatus(message, { mode: "music" });
    bubblePort.showBubbleText(message, { transient: true, durationMs: 2200, kind: "music" });
  }

  return {
    buildActiveMediaControlSnapshot,
    executeActiveMusicCommand,
    notifyMusicActivityUnavailable,
  };
}
