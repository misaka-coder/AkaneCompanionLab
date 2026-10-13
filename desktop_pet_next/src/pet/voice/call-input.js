import { RealtimeVoiceEndpointDetector } from "../../realtime-voice-endpoint.js";
import { stopVoiceRecorderToBlob } from "../../desktop-voice-session.js";
import { friendlyErrorMessage, formatError } from "../utils.js";

/** Owns listening turns, standby capture, safety recording and endpoint submission. */
export function createVoiceCallInputController({
  platform: platformPort, status: statusPort, bubble: bubblePort,
  recording: voiceRecordingPort, call: voiceCallPort, response: voiceCallResponsePort
}) {
  const { MediaRecorder } = platformPort;

  async function openRealtimeVoiceCallTurn(call) {
    if (
      !call?.active ||
      call.stopping ||
      voiceCallPort.realtimeVoiceCall !== call ||
      call.inputTurn ||
      call.openingTurn
    ) {
      return false;
    }
    const flowTurn = call.flow?.beginListening();
    if (!flowTurn) return false;
    call.openingTurn = true;
    const turn = {
      call,
      flowTurn,
      id: call.nextTurnId,
      voiceTurnId: `${call.sessionId}:voice:${call.nextTurnId}`,
      audioStreamId: `${call.sessionId}:audio:${call.nextTurnId}`,
      session: null,
      detector: null,
      recorder: null,
      recorderMimeType: "",
      recorderChunks: [],
      recorderStopTask: null,
      ready: false,
      transportOpen: false,
      endpointAccepted: false,
      endpointTask: null,
      committed: false,
      responseActive: false,
      playbackActive: false,
      failure: null,
      failureTask: null,
      fallbackBlob: null,
      finalWatchdogId: 0,
      closed: false,
      readyAt: 0,
      hasShownSpeech: false,
      replySegments: new Map(),
      replyBubbleText: ""
    };
    call.nextTurnId += 1;
    call.turns.add(turn);
    call.inputTurn = turn;

    try {
      startRealtimeCallSafetyRecorder(turn, call.stream);
      turn.detector = new RealtimeVoiceEndpointDetector({
        onSpeechStarted: () => {
          if (!voiceCallPort.isActiveRealtimeCallTurn(turn) || call.inputTurn !== turn) return;
          statusPort.setRuntimeStatus("语音通话中 · 听到你了", { mode: "listening" });
          const overlapsPlayback = [...call.turns].some(
            (candidate) =>
              candidate !== turn &&
              !candidate.closed &&
              candidate.playbackActive
          );
          if (overlapsPlayback) {
            turn.session?.reportInterruptionSuspected();
          }
        },
        onEndpoint: (endpoint) => {
          void handleRealtimeVoiceCallEndpoint(turn, endpoint);
        }
      });
      turn.session = call.transport.createTurn({
        voiceTurnId: turn.voiceTurnId,
        audioStreamId: turn.audioStreamId,
        endpointDetector: turn.detector,
        callbacks: voiceCallResponsePort.buildRealtimeVoiceCallCallbacks(turn)
      });
      call.standbyDetector?.reset();
      call.standbySpeechPending = false;
      await turn.session.start();
      if (!voiceCallPort.isActiveRealtimeCallTurn(turn) || call.inputTurn !== turn) return false;
      turn.transportOpen = true;
      return true;
    } catch (error) {
      await voiceCallResponsePort.markRealtimeVoiceCallFailure(turn, {
        reason: "voice_realtime_start_failed",
        message: friendlyErrorMessage(formatError(error)),
        terminal: true,
        committed: false
      });
      return false;
    } finally {
      call.openingTurn = false;
    }
  }

  function configureRealtimeVoiceStandbyCapture(call) {
    call.standbyDetector = new RealtimeVoiceEndpointDetector({
      onSpeechStarted: () => {
        if (
          !voiceCallPort.isActiveRealtimeVoiceCall(call) ||
          call.inputTurn ||
          call.standbySpeechPending
        ) {
          return;
        }
        call.standbySpeechPending = true;
        call.resources.holdIdleCapture();
        const hasActiveReply = [...call.turns].some(
          (turn) => turn.responseActive || turn.playbackActive
        );
        statusPort.setRuntimeStatus(
          hasActiveReply
            ? "听到你了 · 已保留这一句，准备接管"
            : "听到你了 · 正在恢复实时监听",
          { mode: "listening" }
        );
        statusPort.updateActivityControls();
      }
    });
    call.resources.setIdleCaptureObserver({
      onPcm: (buffer, frames, sampleRate) => {
        if (!voiceCallPort.isActiveRealtimeVoiceCall(call) || call.inputTurn) return;
        call.standbyDetector?.acceptPcmFrame(buffer, frames, sampleRate);
      },
      onError: (error) => {
        if (!voiceCallPort.isActiveRealtimeVoiceCall(call)) return;
        const reason = String(error?.message || "");
        statusPort.setRuntimeStatus(
          reason === "voice_call_idle_capture_overflow"
            ? "连续收音过长，已保留最近的语音"
            : "连续收音缓冲异常，当前通话仍在继续",
          { mode: "error" }
        );
        statusPort.updateActivityControls();
      }
    });
  }

  function startRealtimeCallSafetyRecorder(turn, stream) {
    const preferredMimeType = voiceRecordingPort.selectVoiceMimeType();
    const options = preferredMimeType ? { mimeType: preferredMimeType } : undefined;
    let recorder;
    try {
      recorder = new MediaRecorder(stream, options);
    } catch {
      recorder = new MediaRecorder(stream);
    }
    turn.recorder = recorder;
    turn.recorderMimeType = recorder.mimeType || preferredMimeType || "audio/webm";
    recorder.addEventListener("dataavailable", (event) => {
      if (event.data?.size > 0) turn.recorderChunks.push(event.data);
    });
    recorder.start();
  }

  function stopRealtimeCallSafetyRecorder(turn) {
    if (!turn?.recorder) {
      return Promise.resolve(
        new Blob(turn?.recorderChunks || [], {
          type: turn?.recorderMimeType || "audio/webm"
        })
      );
    }
    if (turn.recorderStopTask) return turn.recorderStopTask;
    const recorder = turn.recorder;
    if (recorder.state === "inactive") {
      turn.recorderStopTask = Promise.resolve(
        new Blob(turn.recorderChunks, {
          type: turn.recorderMimeType || recorder.mimeType || "audio/webm"
        })
      );
      return turn.recorderStopTask;
    }
    turn.recorderStopTask = stopVoiceRecorderToBlob(recorder, turn.recorderChunks, turn.recorderMimeType);
    return turn.recorderStopTask;
  }

  async function handleRealtimeVoiceCallEndpoint(turn, endpoint) {
    if (!voiceCallPort.isActiveRealtimeCallTurn(turn) || turn.endpointAccepted || turn.endpointTask) return;
    const action = String(endpoint?.action || "");
    if (!turn.call.flow?.acceptEndpoint(turn.flowTurn, action)) return;
    turn.endpointAccepted = true;
    turn.endpointTask = (async () => {
      if (action === "discard") {
        if (voiceCallPort.isActiveRealtimeVoiceCall(turn.call)) {
          turn.endpointAccepted = false;
          turn.detector?.reset();
          turn.call.flow?.resumeListening(turn.flowTurn);
          statusPort.setRuntimeStatus("没有听到清晰内容，继续听你说", { mode: "listening" });
          if (!voiceCallPort.hasVisibleRealtimeVoiceReply(turn.call, turn)) {
            bubblePort.showBubbleText("刚才没有识别出清晰内容，我还在听。", {
              transient: true,
              durationMs: 2200,
              kind: "status"
            });
          }
        }
        return;
      }

      turn.call.inputTurn = null;
      statusPort.setRuntimeStatus("听清了，正在结束这一轮输入", { mode: "thinking" });
      let realtimeAccepted = false;
      let blob;
      try {
        [, blob] = await Promise.all([
          turn.session.flushAndStopCapture(),
          stopRealtimeCallSafetyRecorder(turn)
        ]);
        if (!voiceCallPort.isActiveRealtimeCallTurn(turn)) return;
        realtimeAccepted = await turn.session
          .finishInput()
          .then(() => true)
          .catch((error) => {
            turn.failure = {
              reason: "voice_realtime_finalize_failed",
              message: friendlyErrorMessage(formatError(error)),
              terminal: true,
              committed: turn.committed
            };
            return false;
          });
      } catch (error) {
        turn.failure = {
          reason: "voice_recording_stop_failed",
          message: voiceRecordingPort.describeVoiceError(error),
          terminal: true,
          committed: turn.committed
        };
      }
      if (!voiceCallPort.isActiveRealtimeCallTurn(turn)) return;
      turn.fallbackBlob = turn.committed ? null : blob;
      if (!realtimeAccepted || turn.failure) {
        await voiceCallResponsePort.markRealtimeVoiceCallFailure(
          turn,
          turn.failure || {
            reason: "voice_realtime_finalize_failed",
            message: "实时语音收尾失败。",
            terminal: true,
            committed: turn.committed
          }
        );
        return;
      }
      if (!turn.committed) {
        statusPort.setRuntimeStatus("语音识别收尾中", { mode: "thinking" });
        voiceCallResponsePort.armRealtimeVoiceCallFinalWatchdog(turn);
      }
      turn.call.flow?.markResponding(turn.flowTurn);
    })();
    await turn.endpointTask;
    if (action === "discard" && voiceCallPort.isActiveRealtimeCallTurn(turn)) {
      turn.endpointTask = null;
    }
  }

  return {
    openRealtimeVoiceCallTurn, configureRealtimeVoiceStandbyCapture,
    startRealtimeCallSafetyRecorder, stopRealtimeCallSafetyRecorder, handleRealtimeVoiceCallEndpoint
  };
}
