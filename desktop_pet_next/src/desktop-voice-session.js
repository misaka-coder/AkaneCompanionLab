/**
 * Desktop Voice Session & Audio Capture Helpers.
 * Handles voice recording MIME detection, voice error descriptions,
 * call state inspection helpers, and audio recorder stream utilities.
 */

export const VOICE_MIME_TYPES = Object.freeze([
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/ogg;codecs=opus",
  "audio/ogg",
  "audio/mp4"
]);

export function isVoiceShortcut(event) {
  return Boolean(
    event &&
      event.ctrlKey &&
      event.shiftKey &&
      !event.altKey &&
      event.code === "Space"
  );
}

export function selectVoiceMimeType(MediaRecorderImpl = globalThis.MediaRecorder, supportedTypes = VOICE_MIME_TYPES) {
  if (!MediaRecorderImpl || typeof MediaRecorderImpl.isTypeSupported !== "function") {
    return "";
  }
  return supportedTypes.find((type) => MediaRecorderImpl.isTypeSupported(type)) || "";
}

export function getVoiceFilename(mimeTypeValue = "") {
  const mimeType = String(mimeTypeValue || "").toLowerCase();
  if (mimeType.includes("wav")) return "akane_voice_input.wav";
  if (mimeType.includes("ogg")) return "akane_voice_input.ogg";
  if (mimeType.includes("mp4")) return "akane_voice_input.m4a";
  return "akane_voice_input.webm";
}

export function describeVoiceError(error, formatFallback = (err) => String(err?.message || err || "未知错误")) {
  const name = String(error?.name || "");
  if (name === "NotAllowedError" || name === "SecurityError") return "没有麦克风权限";
  if (name === "NotFoundError" || name === "DevicesNotFoundError") return "没有找到可用麦克风";
  return formatFallback(error);
}

export function stopVoiceRecorderToBlob(recorder, chunks = [], fallbackMimeType = "audio/webm") {
  return new Promise((resolve, reject) => {
    const handleStop = () => {
      cleanup();
      const mimeType = recorder?.mimeType || fallbackMimeType || "audio/webm";
      resolve(new Blob(chunks, { type: mimeType }));
    };
    const handleError = () => {
      cleanup();
      reject(new Error("voice_recording_failed"));
    };
    const cleanup = () => {
      recorder?.removeEventListener("stop", handleStop);
      recorder?.removeEventListener("error", handleError);
    };
    recorder?.addEventListener("stop", handleStop, { once: true });
    recorder?.addEventListener("error", handleError, { once: true });
    try {
      recorder?.stop();
    } catch (error) {
      cleanup();
      reject(error);
    }
  });
}

export function canUseRealtimeVoice({
  voiceEnabled = false,
  hasVoicePlayer = false,
  supportsCapture = false
} = {}) {
  return Boolean(voiceEnabled && hasVoicePlayer && supportsCapture);
}

export function isActiveRealtimeVoiceCall(call, activeCall = null) {
  const target = activeCall || call;
  return Boolean(call?.active && !call?.stopping && target === call);
}

export function getRealtimeVoiceCallTurns(call) {
  return call?.turns ? [...call.turns] : [];
}

export function hasVisibleRealtimeVoiceReply(call, exceptTurn = null) {
  return Boolean(
    call?.turns &&
      [...call.turns].some(
        (turn) =>
          turn !== exceptTurn &&
          !turn.closed &&
          turn.hasShownSpeech &&
          (turn.responseActive || turn.playbackActive)
      )
  );
}

export function hasRealtimeVoiceCallPlayback(call) {
  return getRealtimeVoiceCallTurns(call).some((turn) => turn.playbackActive);
}

export function hasRealtimeVoiceCallCommittedTurn(call) {
  return getRealtimeVoiceCallTurns(call).some((turn) => turn.committed);
}

export function hasRealtimeVoiceCallFallbackPending(call) {
  return getRealtimeVoiceCallTurns(call).some(
    (turn) => Boolean(turn.failure && !turn.committed)
  );
}

export function isActiveRealtimeCallTurn(turn, activeCall = null) {
  return Boolean(
    turn &&
      !turn.closed &&
      isActiveRealtimeVoiceCall(turn.call, activeCall) &&
      turn.call?.turns?.has(turn)
  );
}

export function clearRealtimeVoiceFinalWatchdog(turn, windowScope = globalThis) {
  if (!turn?.finalWatchdogId) return;
  windowScope.clearTimeout?.(turn.finalWatchdogId);
  turn.finalWatchdogId = 0;
}

export function extractBackendErrorMessage(payload) {
  if (!payload || typeof payload !== "object") return "";
  const detail = payload.detail;
  if (typeof detail === "string" && detail.trim()) return detail.trim();
  if (detail && typeof detail === "object") {
    const detailMessage = extractBackendErrorMessage(detail);
    if (detailMessage) return detailMessage;
  }
  for (const key of ["message", "error", "reason"]) {
    const value = String(payload[key] || "").trim();
    if (value) return value;
  }
  return "";
}

export async function readJsonResponse(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

export async function readBackendErrorMessage(response, fallback = "请求失败", friendlyFormatter = (msg) => msg) {
  const statusText = response?.status ? `HTTP ${response.status}` : fallback;
  const contentType = String(response?.headers?.get?.("content-type") || "").toLowerCase();
  try {
    if (contentType.includes("json")) {
      const payload = await response.json();
      return extractBackendErrorMessage(payload) || statusText;
    }
    const text = String(await response.text()).trim();
    if (text.startsWith("{")) {
      try {
        const payload = JSON.parse(text);
        const message = extractBackendErrorMessage(payload);
        if (message) return message;
      } catch {
        // Fall back to text below.
      }
    }
    return text ? friendlyFormatter(text) : statusText;
  } catch {
    return statusText;
  }
}
