import { clearRealtimeVoiceFinalWatchdog } from "../../desktop-voice-session.js";
import { hasRealtimeVoiceInputEvidence, shouldReconnectPassiveVoiceFailure } from "../../realtime-voice-call-flow.js";
import { REALTIME_VOICE_FINAL_TIMEOUT_MS, REALTIME_VOICE_STABLE_LISTENER_MS } from "../constants.js";

/** Consumes call-turn events and owns completion, final timeouts and failure recovery. */
export function createVoiceCallResponseController({
  state, lifecycle, bubble: bubblePort, chat: chatPort, status: statusPort, settings: settingsPort,
  character: characterPort, music: musicPort, recording: voiceRecordingPort,
  call: voiceCallPort, capture: voiceCallInputPort, presentation: voicePresentationPort
}) {

  function buildRealtimeVoiceCallCallbacks(turn) {
    const isCurrent = () => voiceCallPort.isActiveRealtimeCallTurn(turn);
    return {
      onReady() {
        if (!isCurrent()) return;
        turn.ready = true;
        turn.readyAt = Date.now();
        if (turn.call.inputTurn === turn) {
          const heardInput = hasRealtimeVoiceInputEvidence(turn.detector?.snapshot?.());
          statusPort.setRuntimeStatus(
            heardInput ? "语音通话中 · 听到你了" : "语音通话中 · 正在听",
            { mode: "listening" }
          );
        }
      },
      onTranscript({ kind, text, unstableTail }) {
        if (!isCurrent() || turn.call.inputTurn !== turn) return;
        const transcript = `${String(text || "")}${String(unstableTail || "")}`.trim();
        if (!transcript) return;
        turn.call.reconnect?.reset();
        const prefix = kind === "checkpoint" ? "听清了" : "正在听";
        statusPort.setRuntimeStatus(
          kind === "checkpoint" ? "语音通话中 · 已听清" : "语音通话中 · 听到你了",
          { mode: "listening" }
        );
        if (!voiceCallPort.hasVisibleRealtimeVoiceReply(turn.call, turn)) {
          bubblePort.showBubbleText(`${prefix}：${transcript}`, {
            transient: false,
            kind: "status"
          });
        }
      },
      onFinal(payload) {
        if (!isCurrent()) return;
        clearRealtimeVoiceFinalWatchdog(turn, lifecycle);
        turn.committed = true;
        turn.call.reconnect?.reset();
        turn.session?.releaseFallbackPcm();
        turn.fallbackBlob = null;
        turn.responseActive = true;
        refreshRealtimeVoiceCallSending(turn.call);
        if (payload?.disposition === "interaction") return;
        const awaitingDecision = payload?.input_state === "finalized";
        if (!voiceCallPort.hasVisibleRealtimeVoiceReply(turn.call, turn)) bubblePort.showThinking();
        const responseStatus = String(payload?.response?.status || "");
        if (responseStatus && !["started", "completed"].includes(responseStatus)) {
          statusPort.setRuntimeStatus("语音已识别，但回复启动失败", { mode: "error" });
        } else {
          statusPort.setRuntimeStatus(awaitingDecision ? "已听清，正在确认是否接管" : "她正在回应", { mode: "thinking" });
          turn.call.flow?.allowOverlapListening(turn.flowTurn);
        }
        statusPort.updateActivityControls();
        settingsPort.scheduleSettingsSnapshot();
      },
      onInteraction() {
        if (!isCurrent()) return;
        finishRealtimeVoiceCallTurn(turn, { ok: true });
      },
      onPlaybackEnqueued(header) {
        if (!isCurrent()) return;
        voicePresentationPort.showFirstRealtimeVoicePlaybackText(turn, header);
        statusPort.setRuntimeStatus("语音已生成，准备播放", { mode: "speaking" });
      },
      onPresentation(payload) {
        if (!isCurrent() || turn.presentation) return;
        turn.presentation = payload?.presentation;
        if (turn.call.presentingTurn === turn) voicePresentationPort.applyRealtimeVoicePresentation(turn);
      },
      onPlaybackStarted(header) {
        if (!isCurrent()) return;
        turn.playbackActive = true;
        turn.call.presentingTurn = turn;
        turn.call.lastPresentedTurnId = turn.id;
        if (state.currentEmotion === characterPort.resolveEmotionEntry("thinking")?.id) {
          musicPort.setRestingPetEmotion();
        }
        voicePresentationPort.showStartedRealtimeVoicePlaybackText(turn, header);
        voicePresentationPort.applyRealtimeVoicePresentation(turn);
        statusPort.setRuntimeStatus("语音通话中 · 她正在说", { mode: "speaking" });
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
        statusPort.setRuntimeStatus("播放控制未生效，已把真实状态交回服务端", {
          mode: "error"
        });
        statusPort.updateActivityControls();
      },
      onInterruptionAccepted() {
        if (!isCurrent()) return;
        statusPort.setRuntimeStatus("听到你在说话，正在判断是否接管", {
          mode: "listening"
        });
        statusPort.updateActivityControls();
      },
      onInterruptionSkipped() {
        if (!isCurrent() || turn.call.inputTurn !== turn) return;
        statusPort.setRuntimeStatus("语音通话中 · 正在听", { mode: "listening" });
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
        statusPort.setRuntimeStatus("语音没能播放，已把失败状态交回服务端", {
          mode: "error"
        });
        statusPort.updateActivityControls();
      },
      onResponseCompleted(payload) {
        if (!isCurrent()) return;
        const canPresent = voicePresentationPort.canPresentRealtimeVoiceTurn(turn);
        if (canPresent && payload?.delivery_status === "text_only" && !voiceCallPort.hasVisibleRealtimeVoiceReply(turn.call, turn)) {
          voicePresentationPort.applyRealtimeVoicePresentation(turn);
        }
        const speech = String(payload?.speech || "").trim();
        const finalText = speech || turn.replyBubbleText;
        if (finalText) {
          turn.replyBubbleText = finalText;
        }
        if (finalText && canPresent) {
          turn.hasShownSpeech = true;
          bubblePort.displayReplyBubbleText(finalText, { speaking: false });
          voicePresentationPort.scheduleRealtimeVoiceBubbleReset(finalText);
        }
        finishRealtimeVoiceCallTurn(turn, { ok: true });
      },
      onResponseFailed(payload) {
        if (!isCurrent()) return;
        finishRealtimeVoiceCallTurn(turn, {
          ok: false,
          cancelled: ["cancelled", "discarded"].includes(String(payload?.state || "")),
          message: String(payload?.message || "这次语音回复没有生成完成。")
        });
      },
      onFailure(failure) {
        if (!isCurrent()) return;
        void markRealtimeVoiceCallFailure(turn, failure);
      },
      onCancelled() {
        if (!isCurrent()) return;
        finishRealtimeVoiceCallTurn(turn, { ok: false, cancelled: true });
      }
    };
  }

  function refreshRealtimeVoiceCallSending(call) {
    if (!call) return;
    chatPort.setVoiceSending([...call.turns].some((turn) => turn.responseActive));
  }

  function armRealtimeVoiceCallFinalWatchdog(turn) {
    clearRealtimeVoiceFinalWatchdog(turn, lifecycle);
    if (!voiceCallPort.isActiveRealtimeCallTurn(turn) || turn.committed) return;
    turn.finalWatchdogId = lifecycle.setTimeout(() => {
      turn.finalWatchdogId = 0;
      if (!voiceCallPort.isActiveRealtimeCallTurn(turn) || turn.committed) return;
      void markRealtimeVoiceCallFailure(turn, {
        reason: "voice_realtime_final_timeout",
        message: "实时识别超时，正在改用普通识别。",
        terminal: true,
        committed: false
      });
    }, REALTIME_VOICE_FINAL_TIMEOUT_MS);
  }

  async function markRealtimeVoiceCallFailure(turn, failure) {
    if (!voiceCallPort.isActiveRealtimeCallTurn(turn)) return;
    const normalized = {
      reason: String(failure?.reason || "voice_realtime_failed"),
      message: String(failure?.message || "实时语音链路暂时不可用。"),
      terminal: Boolean(failure?.terminal),
      committed: Boolean(failure?.committed || turn.committed),
      retryable: Boolean(failure?.retryable)
    };
    if (!normalized.terminal) {
      statusPort.setRuntimeStatus(normalized.message, { mode: "error" });
      return;
    }
    if (turn.failureTask) return turn.failureTask;
    turn.failure = normalized;
    clearRealtimeVoiceFinalWatchdog(turn, lifecycle);
    turn.failureTask = (async () => {
      if (normalized.committed) {
        finishRealtimeVoiceCallTurn(turn, {
          ok: false,
          message: normalized.message
        });
        return;
      }
      const detectorSnapshot = turn.detector?.snapshot?.() || {};
      const hasInputEvidence = hasRealtimeVoiceInputEvidence(detectorSnapshot);
      if (!hasInputEvidence) {
        try {
          await voiceCallInputPort.stopRealtimeCallSafetyRecorder(turn);
        } catch {
          // There is no speech evidence to recover from this recorder.
        }
        if (!voiceCallPort.isActiveRealtimeCallTurn(turn)) return;
        turn.fallbackBlob = null;
        turn.recorderChunks = [];
        turn.session?.releaseFallbackPcm();
        if (
          shouldReconnectPassiveVoiceFailure({
            committed: normalized.committed,
            retryable: normalized.retryable,
            detectorSnapshot
          }) &&
          turn.call.hasBeenReady
        ) {
          const call = turn.call;
          if (
            turn.readyAt > 0 &&
            Date.now() - turn.readyAt >= REALTIME_VOICE_STABLE_LISTENER_MS
          ) {
            call.reconnect?.reset();
          }
          turn.closed = true;
          turn.session?.dispose(normalized.reason);
          call.turns.delete(turn);
          if (call.inputTurn === turn) call.inputTurn = null;
          call.flow?.complete(turn.flowTurn, { requestNext: false });
          refreshRealtimeVoiceCallSending(call);
          const reconnectState = call.reconnect?.schedule();
          if (reconnectState === "scheduled" || reconnectState === "pending") {
            const hasActiveReply = [...call.turns].some(
              (candidate) => candidate.responseActive || candidate.playbackActive
            );
            if (!hasActiveReply) {
              voiceRecordingPort.setVoiceInputState("recording");
              characterPort.setPetEmotion("listening", { persist: false });
              bubblePort.setPetMotion("thinking");
              bubblePort.showBubbleText("监听短暂断开，正在自动重连……", {
                transient: true,
                durationMs: 1600,
                kind: "status"
              });
            }
            statusPort.setRuntimeStatus("语音通话中 · 监听正在重连", {
              mode: "listening"
            });
            statusPort.updateActivityControls();
            settingsPort.scheduleSettingsSnapshot();
            return;
          }
          if (reconnectState === "exhausted") return;
        }
        await voiceCallPort.stopRealtimeVoiceCallWithError(turn.call, normalized.reason, normalized.message);
        return;
      }
      let blob = turn.session?.buildFallbackPcmBlob?.() || turn.fallbackBlob;
      if (!blob) {
        try {
          blob = await voiceCallInputPort.stopRealtimeCallSafetyRecorder(turn);
        } catch {
          blob = null;
        }
      }
      if (!voiceCallPort.isActiveRealtimeCallTurn(turn)) return;
      const revision = voiceRecordingPort.getVoiceInputRevision();
      turn.session?.releaseFallbackPcm();
      await voiceCallPort.stopRealtimeVoiceCall({
        notice: false,
        reason: normalized.reason
      });
      if (!voiceRecordingPort.isCurrentVoiceInput(revision)) return;
      if (blob?.size >= 512) {
        bubblePort.showBubbleText("实时链路不可用，已结束通话并改用普通识别……", {
          transient: false,
          kind: "status"
        });
        statusPort.setRuntimeStatus("已结束通话，正在改用普通识别", {
          mode: "thinking"
        });
        await voiceRecordingPort.transcribeVoiceBlob(blob, {
          autoSubmit: true,
          filename: voiceRecordingPort.getVoiceFilename(blob.type)
        });
        return;
      }
      bubblePort.showError(normalized.message);
    })();
    return turn.failureTask;
  }

  function finishRealtimeVoiceCallTurn(
    turn,
    { ok, message = "", cancelled = false } = {}
  ) {
    if (!voiceCallPort.isActiveRealtimeCallTurn(turn)) return;
    const call = turn.call;
    turn.closed = true;
    clearRealtimeVoiceFinalWatchdog(turn, lifecycle);
    turn.responseActive = false;
    turn.playbackActive = false;
    turn.fallbackBlob = null;
    turn.session?.releaseFallbackPcm();
    turn.session?.dispose(cancelled ? "response_cancelled" : "response_terminal");
    call.turns.delete(turn);
    if (call.presentingTurn === turn) call.presentingTurn = null;
    if (call.inputTurn === turn) call.inputTurn = null;
    refreshRealtimeVoiceCallSending(call);
    if (!ok && !cancelled) {
      bubblePort.showError(message || "这次语音回复没有生成完成。");
    } else if (ok && state.currentEmotion === characterPort.resolveEmotionEntry("thinking")?.id) {
      musicPort.setRestingPetEmotion();
    }
    if (voiceCallPort.isActiveRealtimeVoiceCall(call)) {
      voiceRecordingPort.setVoiceInputState("recording");
      const continuingPlayback = [...call.turns].some((candidate) => !candidate.closed && candidate.playbackActive);
      if (continuingPlayback) {
        bubblePort.setPetMotion("speaking");
        statusPort.setRuntimeStatus("语音通话中 · 她正在说", { mode: "speaking" });
      } else if ([...call.turns].some((candidate) => !candidate.closed && candidate.responseActive)) {
        characterPort.setPetEmotion("thinking", { persist: false });
        bubblePort.setPetMotion("thinking");
        statusPort.setRuntimeStatus("语音通话中 · 她正在回应", { mode: "thinking" });
      } else {
        characterPort.setPetEmotion("listening", { persist: false });
        bubblePort.setPetMotion("thinking");
        statusPort.setRuntimeStatus("语音通话中 · 继续听你说", { mode: "listening" });
      }
      call.flow?.complete(turn.flowTurn);
    }
    statusPort.updateActivityControls();
    settingsPort.scheduleSettingsSnapshot();
  }

  async function cancelRealtimeVoiceCallResponse(reason) {
    const call = voiceCallPort.realtimeVoiceCall;
    if (!voiceCallPort.isActiveRealtimeVoiceCall(call)) return false;
    const turn = [...call.turns].find(
      (candidate) => candidate.responseActive || candidate.playbackActive
    );
    if (!turn || turn.closed) return false;
    await cancelRealtimeVoiceResponseTurn(turn, reason);
    return true;
  }

  async function cancelRealtimeVoiceResponseTurn(turn, reason) {
    if (!voiceCallPort.isActiveRealtimeCallTurn(turn)) return false;
    let acknowledged = false;
    try {
      acknowledged = Boolean(await turn.session?.cancel(reason));
    } finally {
      const alreadyTerminal = !voiceCallPort.isActiveRealtimeCallTurn(turn);
      if (voiceCallPort.isActiveRealtimeCallTurn(turn)) {
        finishRealtimeVoiceCallTurn(turn, {
          ok: false,
          cancelled: true
        });
      }
      if (alreadyTerminal) acknowledged = true;
    }
    return acknowledged;
  }

  return {
    buildRealtimeVoiceCallCallbacks, refreshRealtimeVoiceCallSending,
    armRealtimeVoiceCallFinalWatchdog, markRealtimeVoiceCallFailure, finishRealtimeVoiceCallTurn,
    cancelRealtimeVoiceCallResponse, cancelRealtimeVoiceResponseTurn
  };
}
