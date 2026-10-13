import { RealtimeVoiceSession, buildVoiceWebSocketUrl } from "../../realtime-voice-client.js";
import { clearRealtimeVoiceFinalWatchdog } from "../../desktop-voice-session.js";
import { REALTIME_VOICE_FINAL_TIMEOUT_MS } from "../constants.js";
import { friendlyErrorMessage, formatError, generateSessionId } from "../utils.js";

/** Owns one-shot realtime input, response callbacks, fallback and lifetime. */
export function createVoiceSingleTurnController({
  state, els, platform: platformPort, lifecycle,
  tts: ttsPort, bubble: bubblePort, chat: chatPort, persistence: statePort, status: statusPort,
  settings: settingsPort, character: characterPort, backend: backendPort, music: musicPort,
  recording: voiceRecordingPort, presentation: voicePresentationPort
}) {
  let realtimeVoiceTurn = null;

  function startRealtimeVoiceTurn(stream) {
    closeRealtimeVoiceTurn(realtimeVoiceTurn, "voice_turn_replaced");
    ttsPort.cancelTtsPrewarm();
    ttsPort.stopTts();
    const sessionId = String(state.sessionId || "").trim() || generateSessionId();
    if (!state.sessionId) {
      state.sessionId = sessionId;
      statePort.scheduleSave(0);
    }

    const turn = {
      session: null,
      startTask: null,
      ready: false,
      committed: false,
      responseActive: false,
      playbackActive: false,
      failure: null,
      fallbackBlob: null,
      fallbackStarted: false,
      finalWatchdogId: 0,
      closed: false,
      hasShownSpeech: false,
      replySegments: new Map(),
      replyBubbleText: ""
    };
    realtimeVoiceTurn = turn;

    try {
      const session = new RealtimeVoiceSession({
        getSocketProtocols: platformPort.voiceSocketProtocols,
        websocketUrl: buildVoiceWebSocketUrl(
          backendPort.buildBackendEndpointUrl("voiceRealtime", "/voice/realtime", { t: Date.now() })
        ),
        mediaStream: stream,
        audioElement: els.voicePlayer,
        workletModuleUrl: platformPort.VOICE_PCM_WORKLET_URL,
        getVolume: () => state.voiceVolume,
        openPayload: {
          profile_user_id: statePort.getProfileUserId(),
          conversation_id: sessionId,
          session_id: sessionId,
          character_pack_id: characterPort.getCurrentCharacterPackId(),
          language: "zh",
          disposition: "message"
        },
        callbacks: buildRealtimeVoiceCallbacks(turn)
      });
      turn.session = session;
      turn.startTask = session
        .start()
        .then(() => {
          if (realtimeVoiceTurn !== turn || turn.closed) return false;
          turn.ready = true;
          return true;
        })
        .catch((error) => {
          markRealtimeVoiceFailure(turn, {
            reason: "voice_realtime_start_failed",
            message: friendlyErrorMessage(formatError(error)),
            terminal: true,
            committed: false
          });
          return false;
        });
    } catch (error) {
      markRealtimeVoiceFailure(turn, {
        reason: "voice_realtime_start_failed",
        message: friendlyErrorMessage(formatError(error)),
        terminal: true,
        committed: false
      });
    }
  }

  function buildRealtimeVoiceCallbacks(turn) {
    const isCurrent = () => realtimeVoiceTurn === turn && !turn.closed;
    return {
      onReady() {
        if (!isCurrent()) return;
        turn.ready = true;
        if (voiceRecordingPort.voiceInputState === "recording") {
          statusPort.setRuntimeStatus("实时语音聆听中", { mode: "listening" });
        }
      },
      onTranscript({ kind, text, unstableTail }) {
        if (!isCurrent() || voiceRecordingPort.voiceInputState !== "recording") return;
        const transcript = `${String(text || "")}${String(unstableTail || "")}`.trim();
        if (!transcript) return;
        const prefix = kind === "checkpoint" ? "听清了" : "正在听";
        bubblePort.showBubbleText(`${prefix}：${transcript}`, { transient: false, kind: "status" });
      },
      onFinal(payload) {
        if (!isCurrent() || turn.fallbackStarted) return;
        clearRealtimeVoiceFinalWatchdog(turn, lifecycle);
        turn.committed = true;
        turn.fallbackBlob = null;
        turn.responseActive = true;
        chatPort.setVoiceSending(true);
        voiceRecordingPort.setVoiceInputState("processing");
        bubblePort.showThinking();
        const responseStatus = String(payload?.response?.status || "");
        if (responseStatus && !["started", "completed"].includes(responseStatus)) {
          statusPort.setRuntimeStatus("语音已识别，但回复启动失败", { mode: "error" });
        }
        statusPort.updateActivityControls();
        settingsPort.scheduleSettingsSnapshot();
      },
      onInteraction() {
        if (!isCurrent()) return;
        finishRealtimeVoiceTurn(turn, { ok: true });
      },
      onPlaybackEnqueued(header) {
        if (!isCurrent()) return;
        voicePresentationPort.showFirstRealtimeVoicePlaybackText(turn, header);
        statusPort.setRuntimeStatus("语音已生成，准备播放", { mode: "speaking" });
      },
      onPresentation(payload) {
        if (!isCurrent() || turn.presentation) return;
        turn.presentation = payload?.presentation;
        if (turn.hasStartedPlayback) voicePresentationPort.applyRealtimeVoicePresentation(turn);
      },
      onPlaybackStarted(header) {
        if (!isCurrent()) return;
        turn.playbackActive = true;
        turn.hasStartedPlayback = true;
        if (state.currentEmotion === characterPort.resolveEmotionEntry("thinking")?.id) {
          musicPort.setRestingPetEmotion();
        }
        voicePresentationPort.showStartedRealtimeVoicePlaybackText(turn, header);
        voicePresentationPort.applyRealtimeVoicePresentation(turn);
        statusPort.setRuntimeStatus("语音回复中", { mode: "speaking" });
        statusPort.updateActivityControls();
      },
      onPlaybackDucked() {
        if (!isCurrent()) return;
        turn.playbackActive = true;
        statusPort.setRuntimeStatus("听到新的声音，已暂时压低回复音量", { mode: "listening" });
        statusPort.updateActivityControls();
      },
      onPlaybackResumed() {
        if (!isCurrent()) return;
        turn.playbackActive = true;
        statusPort.setRuntimeStatus("继续语音回复", { mode: "speaking" });
        statusPort.updateActivityControls();
      },
      onPlaybackControlFailed(_control, reason) {
        if (!isCurrent()) return;
        void reason;
        statusPort.setRuntimeStatus("播放控制未生效，已把真实状态交回服务端", { mode: "error" });
        statusPort.updateActivityControls();
      },
      onPlaybackCompleted() {
        if (!isCurrent()) return;
        turn.playbackActive = false;
        if (turn.responseActive) {
          bubblePort.setPetMotion("thinking");
          statusPort.setRuntimeStatus("回复收尾中", { mode: "thinking" });
        }
        statusPort.updateActivityControls();
      },
      onPlaybackInterrupted() {
        if (!isCurrent()) return;
        turn.playbackActive = false;
        statusPort.updateActivityControls();
      },
      onPlaybackFailed(_header, reason) {
        if (!isCurrent()) return;
        turn.playbackActive = false;
        void reason;
        statusPort.setRuntimeStatus("语音没能播放，已把失败状态交回服务端", { mode: "error" });
        statusPort.updateActivityControls();
      },
      onResponseCompleted(payload) {
        if (!isCurrent()) return;
        if (payload?.delivery_status === "text_only") voicePresentationPort.applyRealtimeVoicePresentation(turn);
        const speech = String(payload?.speech || "").trim();
        if (speech && !turn.hasShownSpeech) {
          bubblePort.showBubbleText(speech, { dismiss: true, kind: "reply" });
        }
        finishRealtimeVoiceTurn(turn, { ok: true });
      },
      onResponseFailed(payload) {
        if (!isCurrent()) return;
        const message = String(payload?.message || "这次语音回复没有生成完成。");
        finishRealtimeVoiceTurn(turn, {
          ok: false, message,
          cancelled: ["cancelled", "discarded"].includes(String(payload?.state || ""))
        });
      },
      onFailure(failure) {
        if (!isCurrent()) return;
        markRealtimeVoiceFailure(turn, failure);
      },
      onCancelled() {
        if (!isCurrent()) return;
        finishRealtimeVoiceTurn(turn, { ok: false, cancelled: true });
      }
    };
  }

  function markRealtimeVoiceFailure(turn, failure) {
    if (!turn || turn.closed || realtimeVoiceTurn !== turn) return;
    const normalized = {
      reason: String(failure?.reason || "voice_realtime_failed"),
      message: String(failure?.message || "实时语音链路暂时不可用。"),
      terminal: Boolean(failure?.terminal),
      committed: Boolean(failure?.committed || turn.committed)
    };
    if (!normalized.terminal) {
      statusPort.setRuntimeStatus(normalized.message, { mode: "error" });
      return;
    }
    clearRealtimeVoiceFinalWatchdog(turn, lifecycle);
    turn.failure = normalized;
    if (!normalized.committed) turn.session?.dispose(normalized.reason);
    if (normalized.committed) {
      finishRealtimeVoiceTurn(turn, { ok: false, message: normalized.message });
      return;
    }
    if (voiceRecordingPort.voiceInputState === "recording") {
      statusPort.setRuntimeStatus("实时链路不可用，录完后改用普通识别", { mode: "listening" });
      return;
    }
    if (turn.fallbackBlob) void fallbackRealtimeVoiceToBatch(turn);
  }

  function armRealtimeVoiceFinalWatchdog(turn) {
    clearRealtimeVoiceFinalWatchdog(turn, lifecycle);
    if (!turn || turn.closed || turn.committed || turn.fallbackStarted) return;
    turn.finalWatchdogId = lifecycle.setTimeout(() => {
      turn.finalWatchdogId = 0;
      if (
        !turn ||
        turn.closed ||
        turn.committed ||
        turn.fallbackStarted ||
        realtimeVoiceTurn !== turn
      ) {
        return;
      }
      bubblePort.showBubbleText("实时识别超时，正在改用普通识别……", {
        transient: false,
        kind: "status"
      });
      markRealtimeVoiceFailure(turn, {
        reason: "voice_realtime_final_timeout",
        message: "实时识别超时，正在改用普通识别。",
        terminal: true,
        committed: false
      });
    }, REALTIME_VOICE_FINAL_TIMEOUT_MS);
  }

  async function fallbackRealtimeVoiceToBatch(turn) {
    if (!turn || turn.closed || turn.committed || turn.fallbackStarted) return;
    const blob = turn.fallbackBlob;
    if (!blob || blob.size < 512) {
      finishRealtimeVoiceTurn(turn, { ok: false, message: "录音太短啦，我没听清。" });
      return;
    }
    turn.fallbackStarted = true;
    clearRealtimeVoiceFinalWatchdog(turn, lifecycle);
    turn.responseActive = false;
    chatPort.setVoiceSending(false);
    turn.session?.dispose("batch_asr_fallback");
    turn.closed = true;
    if (realtimeVoiceTurn === turn) realtimeVoiceTurn = null;
    const timedOut = turn.failure?.reason === "voice_realtime_final_timeout";
    const statusText = timedOut
      ? "实时识别超时，正在改用普通识别"
      : "实时链路不可用，正在改用普通识别";
    bubblePort.showBubbleText(`${statusText}……`, { transient: false, kind: "status" });
    statusPort.setRuntimeStatus(statusText, { mode: "thinking" });
    try {
      await voiceRecordingPort.transcribeVoiceBlob(blob, { autoSubmit: true });
    } finally {
      statusPort.updateActivityControls();
      settingsPort.scheduleSettingsSnapshot();
    }
  }

  function finishRealtimeVoiceTurn(turn, { ok, message = "", cancelled = false }) {
    if (!turn || turn.closed || realtimeVoiceTurn !== turn) return;
    turn.closed = true;
    clearRealtimeVoiceFinalWatchdog(turn, lifecycle);
    turn.responseActive = false;
    turn.playbackActive = false;
    turn.fallbackBlob = null;
    chatPort.setVoiceSending(false);
    turn.session?.dispose(cancelled ? "response_cancelled" : "response_terminal");
    realtimeVoiceTurn = null;
    voiceRecordingPort.setVoiceInputState(state.voiceInputEnabled ? "idle" : "disabled");
    if (!ok && !cancelled) {
      bubblePort.showError(message || "这次语音回复没有生成完成。");
    } else if (ok) {
      if (state.currentEmotion === characterPort.resolveEmotionEntry("thinking")?.id) {
        musicPort.setRestingPetEmotion();
      }
      statusPort.setRuntimeStatus("语音回复完成", { mode: "idle" });
    }
    if (!els.bubble.classList.contains("visible")) bubblePort.setPetMotion("idle");
    musicPort.scheduleMusicEmotionRestore();
    statusPort.updateActivityControls();
    settingsPort.scheduleSettingsSnapshot();
  }

  function closeRealtimeVoiceTurn(turn, reason) {
    if (!turn || turn.closed) return;
    const hadActiveResponse = turn.responseActive || turn.playbackActive;
    turn.closed = true;
    clearRealtimeVoiceFinalWatchdog(turn, lifecycle);
    turn.responseActive = false;
    turn.playbackActive = false;
    turn.fallbackBlob = null;
    void turn.session?.cancel(reason);
    if (realtimeVoiceTurn === turn) realtimeVoiceTurn = null;
    if (hadActiveResponse) {
      chatPort.setVoiceSending(false);
      if (voiceRecordingPort.voiceInputState === "processing") {
        voiceRecordingPort.setVoiceInputState(state.voiceInputEnabled ? "idle" : "disabled");
      }
      statusPort.updateActivityControls();
      settingsPort.scheduleSettingsSnapshot();
    }
  }

  return {
    get realtimeVoiceTurn() { return realtimeVoiceTurn; },
    startRealtimeVoiceTurn, closeRealtimeVoiceTurn, markRealtimeVoiceFailure,
    armRealtimeVoiceFinalWatchdog, fallbackRealtimeVoiceToBatch
  };
}
