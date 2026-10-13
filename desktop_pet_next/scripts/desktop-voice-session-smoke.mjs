import assert from "node:assert/strict";

import {
  VOICE_MIME_TYPES,
  isVoiceShortcut,
  selectVoiceMimeType,
  getVoiceFilename,
  describeVoiceError,
  canUseRealtimeVoice,
  isActiveRealtimeVoiceCall,
  getRealtimeVoiceCallTurns,
  hasVisibleRealtimeVoiceReply,
  hasRealtimeVoiceCallPlayback,
  hasRealtimeVoiceCallCommittedTurn,
  hasRealtimeVoiceCallFallbackPending,
  isActiveRealtimeCallTurn,
  clearRealtimeVoiceFinalWatchdog,
  extractBackendErrorMessage,
  readJsonResponse,
  readBackendErrorMessage
} from "../src/desktop-voice-session.js";

// 1. Shortcut
assert.equal(isVoiceShortcut({ ctrlKey: true, shiftKey: true, altKey: false, code: "Space" }), true);
assert.equal(isVoiceShortcut({ ctrlKey: false, shiftKey: true, altKey: false, code: "Space" }), false);
assert.equal(isVoiceShortcut({ ctrlKey: true, shiftKey: true, altKey: true, code: "Space" }), false);

// 2. MIME & filenames
assert.equal(VOICE_MIME_TYPES.length >= 3, true);
assert.equal(getVoiceFilename("audio/wav"), "akane_voice_input.wav");
assert.equal(getVoiceFilename("audio/ogg"), "akane_voice_input.ogg");
assert.equal(getVoiceFilename("audio/mp4"), "akane_voice_input.m4a");
assert.equal(getVoiceFilename("audio/webm"), "akane_voice_input.webm");

const mockMediaRecorder = {
  isTypeSupported: (t) => t.includes("opus")
};
assert.equal(selectVoiceMimeType(mockMediaRecorder), "audio/webm;codecs=opus");
assert.equal(selectVoiceMimeType(null), "");

// 3. Error descriptions
assert.equal(describeVoiceError({ name: "NotAllowedError" }), "没有麦克风权限");
assert.equal(describeVoiceError({ name: "DevicesNotFoundError" }), "没有找到可用麦克风");
assert.equal(describeVoiceError({ name: "Custom", message: "fail" }), "fail");

// 4. Capability check
assert.equal(canUseRealtimeVoice({ voiceEnabled: true, hasVoicePlayer: true, supportsCapture: true }), true);
assert.equal(canUseRealtimeVoice({ voiceEnabled: false, hasVoicePlayer: true, supportsCapture: true }), false);

// 5. Call state & turns
const call = {
  active: true,
  stopping: false,
  turns: new Set()
};
assert.equal(isActiveRealtimeVoiceCall(call, call), true);
assert.equal(isActiveRealtimeVoiceCall({ active: false }, call), false);

const turn1 = { call, closed: false, playbackActive: true, committed: true, hasShownSpeech: true };
const turn2 = { call, closed: false, playbackActive: false, committed: false, failure: { reason: "err" } };
call.turns.add(turn1);
call.turns.add(turn2);

assert.equal(getRealtimeVoiceCallTurns(call).length, 2);
assert.equal(hasVisibleRealtimeVoiceReply(call), true);
assert.equal(hasRealtimeVoiceCallPlayback(call), true);
assert.equal(hasRealtimeVoiceCallCommittedTurn(call), true);
assert.equal(hasRealtimeVoiceCallFallbackPending(call), true);
assert.equal(isActiveRealtimeCallTurn(turn1, call), true);

turn1.closed = true;
assert.equal(isActiveRealtimeCallTurn(turn1, call), false);

// 6. Watchdog clearing
let cleared = false;
const mockWindow = { clearTimeout: () => { cleared = true; } };
const turn3 = { finalWatchdogId: 42 };
clearRealtimeVoiceFinalWatchdog(turn3, mockWindow);
assert.equal(cleared, true);
assert.equal(turn3.finalWatchdogId, 0);

// 7. Backend response/error extraction
assert.equal(extractBackendErrorMessage({ detail: "detail error" }), "detail error");
assert.equal(extractBackendErrorMessage({ error: "custom error" }), "custom error");
assert.equal(extractBackendErrorMessage({ detail: { message: "nested error" } }), "nested error");

// 8. readJsonResponse
const mockResponse = {
  json: async () => ({ ok: true })
};
const parsed = await readJsonResponse(mockResponse);
assert.deepEqual(parsed, { ok: true });

console.log("desktop voice session smoke: ok");
