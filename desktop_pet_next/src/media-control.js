export const MEDIA_CONTROL_TARGETS = Object.freeze({
  local: "local",
  system: "system",
  none: "none"
});

export function createMediaCommandQueue(execute) {
  const operations = new Map();
  let tail = Promise.resolve();
  return (action, payload = {}) => {
    const id = String(payload.operationId || globalThis.crypto.randomUUID());
    const normalized = normalizeMediaControlAction(action);
    const existing = operations.get(id);
    if (existing) {
      return existing.action === normalized ? existing.promise : Promise.resolve({
        ok: false, status: "rejected", reason: "operation_id_conflict", operationId: id
      });
    }
    const promise = tail.catch(() => {}).then(() => execute(normalized, { ...payload, operationId: id }));
    operations.set(id, { action: normalized, promise });
    tail = promise;
    const trim = () => {
      while (operations.size > 512 && operations.keys().next().value !== id) {
        operations.delete(operations.keys().next().value);
      }
    };
    promise.then(trim, trim);
    return promise;
  };
}

export const MEDIA_CONTROL_ACTIONS = Object.freeze({
  toggle: "toggle",
  play: "play",
  pause: "pause",
  stop: "stop",
  next: "next",
  previous: "previous"
});

export function normalizeMediaControlAction(value) {
  const action = String(value || "").trim().toLowerCase();
  if (action === "resume") return MEDIA_CONTROL_ACTIONS.play;
  if (action === "skip") return MEDIA_CONTROL_ACTIONS.next;
  if (action === "prev") return MEDIA_CONTROL_ACTIONS.previous;
  return Object.values(MEDIA_CONTROL_ACTIONS).includes(action) ? action : "";
}

export function resolveActiveMediaControl({
  localTrack = null,
  localPlaying = false,
  localPaused = false,
  systemMedia = null,
  systemControllable = false
} = {}) {
  const localAvailable = Boolean(localTrack);
  const systemAvailable = Boolean(systemControllable);
  const systemPlaying = Boolean(systemAvailable && systemMedia?.isPlaying);

  // Actual playback wins over merely loaded/stale media. This keeps a stopped
  // local queue from shadowing the system player that the user is hearing.
  if (localPlaying && localAvailable) {
    return localControl(localTrack, "playing");
  }
  if (systemPlaying) {
    return systemControl(systemMedia, "playing");
  }
  if (localPaused && localAvailable) {
    return localControl(localTrack, "paused");
  }
  if (systemAvailable) {
    return systemControl(systemMedia, systemMedia?.playbackStatus || "stopped");
  }
  if (localAvailable) {
    return localControl(localTrack, "stopped");
  }
  return {
    target: MEDIA_CONTROL_TARGETS.none,
    targetId: "",
    playbackStatus: "unavailable",
    isPlaying: false,
    available: false,
    canToggle: false,
    canPrevious: false,
    canNext: false,
    canStop: false
  };
}

export function resolveMediaControlAction(action, control) {
  const normalized = normalizeMediaControlAction(action);
  if (!normalized) return "";
  if (normalized !== MEDIA_CONTROL_ACTIONS.toggle) return normalized;
  return control?.isPlaying ? MEDIA_CONTROL_ACTIONS.pause : MEDIA_CONTROL_ACTIONS.play;
}

function localControl(track, playbackStatus) {
  return {
    target: MEDIA_CONTROL_TARGETS.local,
    targetId: String(track?.sourceId || track?.id || "local_music_current"),
    playbackStatus,
    isPlaying: playbackStatus === "playing",
    available: true,
    canToggle: true,
    canPrevious: true,
    canNext: true,
    canStop: true
  };
}

function systemControl(media, playbackStatus) {
  return {
    target: MEDIA_CONTROL_TARGETS.system,
    targetId: String(media?.trackKey || "system_media_current"),
    playbackStatus: String(playbackStatus || "unknown"),
    isPlaying: Boolean(media?.isPlaying),
    available: true,
    canToggle: true,
    canPrevious: true,
    canNext: true,
    canStop: true
  };
}
