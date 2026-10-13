import { clamp } from './utils.js';
import { createVoiceRecordingController } from './voice/recording.js';
import { createVoiceCallController } from './voice/call.js';
import { createVoiceSingleTurnController } from './voice/single-turn.js';
import { createVoicePresentationController } from './voice/presentation.js';

/** Composes voice owners and preserves the main-window voice interface. */
export function createVoiceController({
  state,
  els,
  platform: platformPort,
  lifecycle,
  tts: ttsPort,
  bubble: bubblePort,
  chat: chatPort,
  persistence: statePort,
  status: statusPort,
  settings: settingsPort,
  character: characterPort,
  backend: backendPort,
  music: musicPort,
  input: inputPort
}) {
  const voiceSingleTurnPort = {};
  const voiceCallPort = {};
  const voicePresentationPort = {};
  const voiceRecordingPort = createVoiceRecordingController({
    state, els, platform: platformPort, lifecycle, bubble: bubblePort, chat: chatPort,
    persistence: statePort, status: statusPort, settings: settingsPort,
    character: characterPort, backend: backendPort, input: inputPort,
    singleTurn: voiceSingleTurnPort, call: voiceCallPort, presentation: voicePresentationPort
  });
  const common = {
    state, els, platform: platformPort, lifecycle, tts: ttsPort, bubble: bubblePort, chat: chatPort,
    persistence: statePort, status: statusPort, settings: settingsPort, character: characterPort,
    backend: backendPort, music: musicPort, recording: voiceRecordingPort, presentation: voicePresentationPort
  };
  Object.defineProperties(voiceSingleTurnPort, Object.getOwnPropertyDescriptors(createVoiceSingleTurnController(common)));
  Object.defineProperties(voiceCallPort, Object.getOwnPropertyDescriptors(createVoiceCallController(common)));
  Object.defineProperties(voicePresentationPort, Object.getOwnPropertyDescriptors(createVoicePresentationController({
    state, els, platform: platformPort, lifecycle, bubble: bubblePort,
    character: characterPort, music: musicPort, recording: voiceRecordingPort, call: voiceCallPort
  })));

  function setVoiceEnabled(enabled) {
    state.voiceEnabled = Boolean(enabled);
    if (!state.voiceEnabled) {
      if (voiceRecordingPort.voiceInputState === "opening") void voiceRecordingPort.cancelVoiceRecording();
      void voiceCallPort.stopRealtimeVoiceCall({
        notice: false,
        reason: "voice_output_disabled"
      });
      voiceSingleTurnPort.closeRealtimeVoiceTurn(voiceSingleTurnPort.realtimeVoiceTurn, "voice_output_disabled");
      ttsPort.cancelTtsPrewarm();
      ttsPort.stopTts();
      if (bubblePort.streamedReplySegments.length && chatPort.isTurnActive(bubblePort.streamedReplyTurnToken)) {
        bubblePort.showNextStreamedReplySegment(bubblePort.streamedReplyTurnToken);
      }
    } else {
      ttsPort.scheduleTtsPrewarm({ force: true, delayMs: 200 });
    }
    statePort.scheduleSave(0);
    statusPort.setRuntimeStatus(state.voiceEnabled ? "语音播放已开启" : "语音播放已关闭", { mode: "idle" });
    settingsPort.scheduleSettingsSnapshot();
  }

  function setVoiceInputEnabled(enabled) {
    if (state.voiceInputEnabled === Boolean(enabled)) return;
    state.voiceInputEnabled = Boolean(enabled);
    if (!state.voiceInputEnabled) {
      void voiceRecordingPort.cancelVoiceRecording();
    }
    statePort.scheduleSave(0);
    voiceRecordingPort.setVoiceInputState(state.voiceInputEnabled ? "idle" : "disabled");
    statusPort.setRuntimeStatus(state.voiceInputEnabled ? "语音输入已开启" : "语音输入已关闭", { mode: "idle" });
    settingsPort.scheduleSettingsSnapshot();
  }

  function setVoiceVolume(value) {
    state.voiceVolume = clamp(Number(value), 0, 1);
    if (els.voicePlayer) els.voicePlayer.volume = state.voiceVolume;
    if (els.musicPlayer) els.musicPlayer.volume = state.voiceVolume;
    statePort.scheduleSave(0);
    settingsPort.scheduleSettingsSnapshot();
  }

  async function toggleVoiceRecording() {
    if (voiceRecordingPort.voiceInputState === "opening") {
      await voiceRecordingPort.cancelVoiceRecording({ notice: true });
      return;
    }
    if (voiceCallPort.realtimeVoiceCall) {
      await voiceCallPort.stopRealtimeVoiceCall({ notice: true, reason: "user_ended_call" });
      return;
    }
    if (voicePresentationPort.canUseRealtimeVoice()) {
      await voiceCallPort.startRealtimeVoiceCall();
      return;
    }
    if (voiceRecordingPort.voiceInputState === "recording") {
      await voiceRecordingPort.stopVoiceRecording();
    } else {
      await voiceRecordingPort.startVoiceRecording();
    }
  }

  return {
    get voiceInputState() { return voiceRecordingPort.voiceInputState; },
    get realtimeVoiceTurn() { return voiceSingleTurnPort.realtimeVoiceTurn; },
    get realtimeVoiceCall() { return voiceCallPort.realtimeVoiceCall; },
    get asrController() { return voiceRecordingPort.asrController; },
    setVoiceEnabled,
    setVoiceInputEnabled,
    setVoiceVolume,
    toggleVoiceRecording,
    startVoiceRecording: voiceRecordingPort.startVoiceRecording,
    stopVoiceRecording: voiceRecordingPort.stopVoiceRecording,
    cancelVoiceRecording: voiceRecordingPort.cancelVoiceRecording,
    canUseRealtimeVoice: voicePresentationPort.canUseRealtimeVoice,
    getRealtimeVoiceCallTurns: voiceCallPort.getRealtimeVoiceCallTurns,
    hasRealtimeVoiceCallPlayback: voiceCallPort.hasRealtimeVoiceCallPlayback,
    hasRealtimeVoiceCallCommittedTurn: voiceCallPort.hasRealtimeVoiceCallCommittedTurn,
    hasRealtimeVoiceCallFallbackPending: voiceCallPort.hasRealtimeVoiceCallFallbackPending,
    cancelRealtimeVoiceCallResponse: voiceCallPort.cancelRealtimeVoiceCallResponse,
    stopRealtimeVoiceCall: voiceCallPort.stopRealtimeVoiceCall,
    closeRealtimeVoiceTurn: voiceSingleTurnPort.closeRealtimeVoiceTurn,
    readBackendErrorMessage: voiceRecordingPort.readBackendErrorMessage,
    setVoiceInputState: voiceRecordingPort.setVoiceInputState,
    updateVoiceRecordButton: voicePresentationPort.updateVoiceRecordButton,
    cleanupVoiceRecorder: voiceRecordingPort.cleanupVoiceRecorder,
  };
}
