// Wire versions, timeouts and platform/endpoint validation.

export const VOICE_REALTIME_PROTOCOL_VERSION = 1;

export const VOICE_PLAYBACK_OUTPUT_MODE = "binary_audio_ack_v1";

export const DEFAULT_SOCKET_OPEN_TIMEOUT_MS = 5000;

export const DEFAULT_PROVIDER_READY_TIMEOUT_MS = 30000;

export const CAPTURE_FLUSH_TIMEOUT_MS = 750;

export const DEFAULT_IDLE_PRE_ROLL_MS = 800;

export const DEFAULT_IDLE_CAPTURE_MAX_MS = 2 * 60 * 1000;

export const DEFAULT_FALLBACK_PCM_MAX_MS = 2 * 60 * 1000;

export const CANCEL_RECEIPT_TIMEOUT_MS = 2000;

export function buildVoiceWebSocketUrl(endpoint) {
  const url = new URL(String(endpoint || ""));
  if (url.protocol === "http:") url.protocol = "ws:";
  else if (url.protocol === "https:") url.protocol = "wss:";
  else if (url.protocol !== "ws:" && url.protocol !== "wss:") {
    throw new Error("voice_realtime_endpoint_invalid");
  }
  return url.toString();
}

export function supportsRealtimeVoiceCapture(scope = globalThis) {
  const AudioContextImpl = scope.AudioContext || scope.webkitAudioContext;
  return Boolean(
    scope.WebSocket &&
      AudioContextImpl &&
      scope.AudioWorkletNode &&
      scope.URL?.createObjectURL &&
      scope.Blob
  );
}

export const VOICE_REALTIME_CALL_PROTOCOL_VERSION = 2;

export const DEFAULT_CALL_READY_TIMEOUT_MS = 8000;

export const DEFAULT_CALL_PENDING_PCM_MAX_MS = 10000;

export const CALL_RESPONSE_CANCEL_TIMEOUT_MS = 2000;

export function safeReason(value) {
  const normalized = String(value || "voice_realtime_failed")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_.:-]+/g, "_")
    .slice(0, 96);
  return normalized || "voice_realtime_failed";
}
