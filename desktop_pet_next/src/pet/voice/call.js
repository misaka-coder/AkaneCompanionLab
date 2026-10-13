import { sceneOwnsPlayback } from "../../scene-desktop-bridge.js";
import { captureMicrophone } from "../../voice-capture.js";
import {
  isActiveRealtimeVoiceCall as isActiveRealtimeVoiceCallHelper,
  getRealtimeVoiceCallTurns as getRealtimeVoiceCallTurnsHelper,
  hasVisibleRealtimeVoiceReply as hasVisibleRealtimeVoiceReplyHelper,
  hasRealtimeVoiceCallPlayback as hasRealtimeVoiceCallPlaybackHelper,
  hasRealtimeVoiceCallCommittedTurn as hasRealtimeVoiceCallCommittedTurnHelper,
  hasRealtimeVoiceCallFallbackPending as hasRealtimeVoiceCallFallbackPendingHelper,
  isActiveRealtimeCallTurn as isActiveRealtimeCallTurnHelper, clearRealtimeVoiceFinalWatchdog
} from "../../desktop-voice-session.js";
import { RealtimeVoiceCallSession, buildVoiceWebSocketUrl, RealtimeVoiceCallResources } from "../../realtime-voice-client.js";
import { RealtimeVoiceCallFlow, RealtimeVoiceReconnectBackoff } from "../../realtime-voice-call-flow.js";
import { friendlyErrorMessage, formatError, generateSessionId } from '../utils.js';
import { createVoiceCallInputController } from './call-input.js';
import { createVoiceCallResponseController } from './call-response.js';

/** Owns the active call, transport, reconnection and shared-resource shutdown. */
export function createVoiceCallController({
  state, els, platform: platformPort, lifecycle,
  tts: ttsPort, bubble: bubblePort, chat: chatPort, persistence: statePort, status: statusPort,
  settings: settingsPort, character: characterPort, backend: backendPort, music: musicPort,
  recording: voiceRecordingPort, presentation: voicePresentationPort
}) {
  const { navigator, MediaRecorder } = platformPort;
  let realtimeVoiceCall = null;
  let voiceCallCleanupTask = null;
  const voiceCallResponsePort = {};
  const voiceCallPort = {
    get realtimeVoiceCall() { return realtimeVoiceCall; },
    isActiveRealtimeVoiceCall, isActiveRealtimeCallTurn, hasVisibleRealtimeVoiceReply,
    stopRealtimeVoiceCall, stopRealtimeVoiceCallWithError
  };
  const voiceCallInputPort = createVoiceCallInputController({
    platform: platformPort, status: statusPort, bubble: bubblePort,
    recording: voiceRecordingPort, call: voiceCallPort, response: voiceCallResponsePort
  });
  Object.defineProperties(voiceCallResponsePort, Object.getOwnPropertyDescriptors(createVoiceCallResponseController({
    state, lifecycle, bubble: bubblePort, chat: chatPort, status: statusPort, settings: settingsPort,
    character: characterPort, music: musicPort, recording: voiceRecordingPort,
    call: voiceCallPort, capture: voiceCallInputPort, presentation: voicePresentationPort
  })));

  function createRealtimeVoiceCallTransport(call) {
    let transport = null;
    transport = new RealtimeVoiceCallSession({
      getSocketProtocols: platformPort.voiceSocketProtocols,
      websocketUrl: buildVoiceWebSocketUrl(
        backendPort.buildBackendEndpointUrl("voiceRealtime", "/voice/realtime", { t: Date.now() })
      ),
      callResources: call.resources,
      workletModuleUrl: platformPort.VOICE_PCM_WORKLET_URL,
      getVolume: () => state.voiceVolume,
      openPayload: {
        profile_user_id: statePort.getProfileUserId(),
        conversation_id: call.sessionId,
        session_id: call.sessionId,
        character_pack_id: characterPort.getCurrentCharacterPackId(),
        language: "zh",
        disposition: "message"
      },
      callbacks: {
        onTransportOpen: () => {
          if (isActiveRealtimeVoiceCall(call) && call.transport === transport) {
            statusPort.setRuntimeStatus("语音通话中 · 连接识别服务", { mode: "listening" });
          }
        },
        onReady: () => {
          if (isActiveRealtimeVoiceCall(call) && call.transport === transport) {
            call.hasBeenReady = true;
            statusPort.setRuntimeStatus("语音通话中 · 正在听", { mode: "listening" });
          }
        },
        onFailure: (failure) => {
          if (!isActiveRealtimeVoiceCall(call) || call.transport !== transport) return;
          const message = String(failure?.message || "实时语音链路暂时不可用。");
          if (!failure?.terminal) {
            statusPort.setRuntimeStatus(message, { mode: "error" });
            statusPort.updateActivityControls();
            return;
          }
          if (!call.inputTurn) {
            void stopRealtimeVoiceCallWithError(call, failure?.reason || "voice_call_transport_failed", message);
            return;
          }
          void voiceCallResponsePort.markRealtimeVoiceCallFailure(call.inputTurn, {
            reason: failure?.reason,
            message,
            terminal: true,
            committed: Boolean(call.inputTurn?.committed),
            retryable: Boolean(failure?.retryable)
          });
        },
        onCallClosed: () => {
          if (!isActiveRealtimeVoiceCall(call) || call.transport !== transport) return;
          void stopRealtimeVoiceCallWithError(call, "voice_call_closed_by_server", "实时语音服务结束了这次通话，请重新点击麦克风。");
        }
      }
    });
    return transport;
  }

  async function startRealtimeVoiceCall() {
    if (sceneOwnsPlayback()) { bubblePort.showError("声音正在小屋中播放，请先关闭小屋。"); return; }
    if (realtimeVoiceCall || ["opening", "recording", "processing"].includes(voiceRecordingPort.voiceInputState)) return;
    if (!state.voiceInputEnabled) {
      bubblePort.showError("语音输入已关闭");
      return;
    }
    if (!voicePresentationPort.canUseRealtimeVoice()) {
      bubblePort.showError("当前环境不支持实时语音通话");
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      bubblePort.showError("当前 WebView 不支持录音");
      return;
    }

    const tookOverReply = chatPort.isReplyActive()
      ? chatPort.interruptReply({
          announce: false,
          reason: "user_started_voice_call"
        })
      : false;
    const { token, controller } = voiceRecordingPort.beginVoiceCapture();
    voiceRecordingPort.setVoiceInputState("opening");
    bubblePort.showBubbleText("正在打开麦克风……再次点麦克风可取消。", { transient: false, kind: "status" });
    statusPort.setRuntimeStatus("正在打开麦克风", { mode: "thinking" });

    let stream;
    try {
      await waitForVoiceCallCleanup();
      if (!voiceRecordingPort.isCurrentVoiceInput(token)) return;
      stream = await captureMicrophone({
        getUserMedia: (constraints) => navigator.mediaDevices.getUserMedia(constraints),
        signal: controller.signal
      });
      if (!voiceRecordingPort.isCurrentVoiceInput(token)) {
        for (const track of stream?.getTracks?.() || []) track.stop();
        return;
      }
      const sessionId = String(state.sessionId || "").trim() || generateSessionId();
      if (!state.sessionId) {
        state.sessionId = sessionId;
        statePort.scheduleSave(0);
      }
      const resources = new RealtimeVoiceCallResources({
        mediaStream: stream,
        audioElement: els.voicePlayer
      });
      const call = {
        active: true,
        stopping: false,
        stopTask: null,
        flow: null,
        resources,
        stream,
        sessionId,
        turns: new Set(),
        inputTurn: null,
        openingTurn: false,
        reconnect: null,
        transport: null,
        standbyDetector: null,
        standbySpeechPending: false,
        hasBeenReady: false,
        lastPresentedTurnId: 0,
        nextTurnId: 1
      };
      call.transport = createRealtimeVoiceCallTransport(call);
      call.flow = new RealtimeVoiceCallFlow({
        onListenRequested: () => scheduleNextRealtimeVoiceCallTurn(call)
      });
      call.reconnect = new RealtimeVoiceReconnectBackoff({
        schedule: (callback, delayMs) => lifecycle.setTimeout(callback, delayMs),
        cancel: (timer) => lifecycle.clearTimeout(timer),
        onReconnect: () => {
          if (!isActiveRealtimeVoiceCall(call)) return;
          void (async () => {
            const failedTransport = call.transport;
            statusPort.setRuntimeStatus("语音通话中 · 正在恢复监听", { mode: "listening" });
            await failedTransport?.close("voice_call_reconnect");
            if (!isActiveRealtimeVoiceCall(call)) return;
            call.transport = createRealtimeVoiceCallTransport(call);
            call.flow?.requestNextListeningTurn();
          })().catch((error) => {
            void stopRealtimeVoiceCallWithError(call, "voice_call_reconnect_failed", friendlyErrorMessage(formatError(error)));
          });
        },
        onExhausted: () => {
          if (!isActiveRealtimeVoiceCall(call)) return;
          void stopRealtimeVoiceCallWithError(call, "voice_realtime_reconnect_exhausted", "实时监听连续重连失败，语音通话已结束。");
        }
      });
      call.flow.start();
      realtimeVoiceCall = call;
      voiceCallInputPort.configureRealtimeVoiceStandbyCapture(call);
      ttsPort.cancelTtsPrewarm();
      ttsPort.stopTts();
      voiceRecordingPort.setVoiceInputState("recording");
      characterPort.setPetEmotion("listening", { persist: false });
      bubblePort.setPetMotion("thinking");
      bubblePort.showBubbleText(
        tookOverReply
          ? "上一轮已停下，麦克风已打开。你现在说话即可。"
          : "麦克风已打开。你现在说话即可。",
        { transient: false, kind: "status" }
      );
      statusPort.setRuntimeStatus("语音通话中 · 连接识别服务", { mode: "listening" });
      statusPort.updateActivityControls();
      settingsPort.scheduleSettingsSnapshot();

      const opened = await voiceCallInputPort.openRealtimeVoiceCallTurn(call);
      if (
        !opened &&
        realtimeVoiceCall === call &&
        !call.stopping &&
        !call.reconnect?.pending
      ) {
        await stopRealtimeVoiceCallWithError(call, "initial_voice_turn_failed", "实时语音没有接通，请再试一次。");
      }
    } catch (error) {
      if (!voiceRecordingPort.isCurrentVoiceInput(token)) return;
      for (const track of stream?.getTracks?.() || []) track.stop();
      if (stream && realtimeVoiceCall?.stream === stream) {
        await stopRealtimeVoiceCall({
          notice: false,
          reason: "voice_call_start_failed"
        });
      } else {
        voiceRecordingPort.setVoiceInputState(state.voiceInputEnabled ? "idle" : "disabled");
      }
      if (!voiceRecordingPort.isCurrentVoiceInput(token)) return;
      bubblePort.showError(voiceRecordingPort.describeVoiceError(error));
    } finally {
      voiceRecordingPort.completeVoiceCapture(controller);
    }
  }

  function isActiveRealtimeVoiceCall(call) {
    return isActiveRealtimeVoiceCallHelper(call, realtimeVoiceCall);
  }

  function getRealtimeVoiceCallTurns() {
    return getRealtimeVoiceCallTurnsHelper(realtimeVoiceCall);
  }

  function hasVisibleRealtimeVoiceReply(call, exceptTurn = null) {
    return hasVisibleRealtimeVoiceReplyHelper(call, exceptTurn);
  }

  function hasRealtimeVoiceCallPlayback() {
    return hasRealtimeVoiceCallPlaybackHelper(realtimeVoiceCall);
  }

  function hasRealtimeVoiceCallCommittedTurn() {
    return hasRealtimeVoiceCallCommittedTurnHelper(realtimeVoiceCall);
  }

  function hasRealtimeVoiceCallFallbackPending() {
    return hasRealtimeVoiceCallFallbackPendingHelper(realtimeVoiceCall);
  }

  function isActiveRealtimeCallTurn(turn) {
    return isActiveRealtimeCallTurnHelper(turn, realtimeVoiceCall);
  }

  function scheduleNextRealtimeVoiceCallTurn(call) {
    if (!isActiveRealtimeVoiceCall(call) || call.inputTurn || call.openingTurn) return;
    lifecycle.setTimeout(() => {
      if (!isActiveRealtimeVoiceCall(call) || call.inputTurn || call.openingTurn) return;
      void voiceCallInputPort.openRealtimeVoiceCallTurn(call);
    }, 0);
  }

  async function stopRealtimeVoiceCall({
    notice = false,
    reason = "voice_call_ended"
  } = {}) {
    const call = realtimeVoiceCall;
    if (!call) return voiceCallCleanupTask;
    if (call.stopTask) return call.stopTask;
    const revision = voiceRecordingPort.getVoiceInputRevision();
    call.active = false;
    call.stopping = true;
    call.reconnect?.stop();
    call.flow?.stop();
    realtimeVoiceCall = null;
    const turns = [...call.turns];
    for (const turn of turns) {
      turn.closed = true;
      clearRealtimeVoiceFinalWatchdog(turn, lifecycle);
      turn.responseActive = false;
      turn.playbackActive = false;
      turn.session?.releaseFallbackPcm();
    }
    call.inputTurn = null;
    call.turns.clear();
    chatPort.setVoiceSending(false);
    voiceRecordingPort.setVoiceInputState(state.voiceInputEnabled ? "idle" : "disabled");
    const stopTask = (async () => {
      const results = await Promise.allSettled(
        turns.map((turn) => voiceCallInputPort.stopRealtimeCallSafetyRecorder(turn))
      );
      const failures = results.filter(result => result.status === "rejected").map(result => result.reason);
      try { await call.transport?.close(reason); }
      catch (error) { failures.push(error); }
      for (const turn of turns) {
        try { turn.session?.dispose(reason); }
        catch (error) { failures.push(error); }
      }
      try { await call.resources.close(reason); }
      catch (error) { failures.push(error); }
      call.stopFailure = failures[0] || null;
      call.stopping = false;
      if (!voiceRecordingPort.isCurrentVoiceInput(revision) || realtimeVoiceCall) return;
      if (failures.length) {
        bubblePort.showError(`语音通话已结束，资源释放失败：${voiceRecordingPort.describeVoiceError(failures[0])}`);
      } else if (notice) {
        bubblePort.showBubbleText("语音通话已结束。", {
          transient: true,
          durationMs: 1800,
          kind: "status"
        });
        statusPort.setRuntimeStatus("语音通话已结束", { mode: "idle" });
      }
      if (!els.bubble.classList.contains("visible")) bubblePort.setPetMotion("idle");
      musicPort.scheduleMusicEmotionRestore();
      statusPort.updateActivityControls();
      settingsPort.scheduleSettingsSnapshot();
    })();
    voiceCallCleanupTask = stopTask;
    call.stopTask = stopTask.finally(() => {
      if (voiceCallCleanupTask === stopTask) voiceCallCleanupTask = null;
    });
    return call.stopTask;
  }

  function waitForVoiceCallCleanup() {
    return voiceCallCleanupTask || Promise.resolve();
  }

  async function stopRealtimeVoiceCallWithError(call, reason, message) {
    if (!isActiveRealtimeVoiceCall(call)) return;
    const revision = voiceRecordingPort.getVoiceInputRevision();
    await stopRealtimeVoiceCall({ notice: false, reason });
    if (voiceRecordingPort.isCurrentVoiceInput(revision) && !realtimeVoiceCall && !call.stopFailure) {
      bubblePort.showError(message);
    }
  }

  return {
    get realtimeVoiceCall() { return realtimeVoiceCall; },
    startRealtimeVoiceCall, stopRealtimeVoiceCall, stopRealtimeVoiceCallWithError, waitForVoiceCallCleanup,
    isActiveRealtimeVoiceCall, isActiveRealtimeCallTurn,
    getRealtimeVoiceCallTurns, hasVisibleRealtimeVoiceReply, hasRealtimeVoiceCallPlayback,
    hasRealtimeVoiceCallCommittedTurn, hasRealtimeVoiceCallFallbackPending,
    cancelRealtimeVoiceCallResponse: voiceCallResponsePort.cancelRealtimeVoiceCallResponse
  };
}
