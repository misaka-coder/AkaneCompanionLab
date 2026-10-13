import { sceneOwnsPlayback } from "../../scene-desktop-bridge.js";
import { captureMicrophone } from "../../voice-capture.js";
import {
  stopVoiceRecorderToBlob as stopVoiceRecorderToBlobHelper, readJsonResponse,
  extractBackendErrorMessage, readBackendErrorMessage as readBackendErrorMessageHelper,
  selectVoiceMimeType as selectVoiceMimeTypeHelper, getVoiceFilename as getVoiceFilenameHelper,
  describeVoiceError as describeVoiceErrorHelper
} from "../../desktop-voice-session.js";
import { MIN_RECORDING_MS, ASR_TIMEOUT_MS, CLIENT_MODE } from "../constants.js";
import { friendlyErrorMessage, formatError } from "../utils.js";

/** Owns input revisions, pending microphone requests, recording and batch ASR. */
export function createVoiceRecordingController({
  state, els, platform: platformPort, lifecycle,
  bubble: bubblePort, chat: chatPort, persistence: statePort, status: statusPort,
  settings: settingsPort, character: characterPort, backend: backendPort, input: inputPort,
  singleTurn: voiceSingleTurnPort, call: voiceCallPort, presentation: voicePresentationPort
}) {
  const { navigator, MediaRecorder } = platformPort;
  let voiceInputState = "idle";
  let voiceRecorder = null;
  let voiceStream = null;
  let voiceChunks = [];
  let voiceMimeType = "";
  let voiceStartedAt = 0;
  let asrController = null;
  let voiceInputToken = 0;
  let voiceCaptureController = null;

  function beginVoiceCapture() {
    const token = ++voiceInputToken;
    const controller = new AbortController();
    voiceCaptureController = controller;
    return { token, controller };
  }

  function isCurrentVoiceInput(token) {
    return token === voiceInputToken && !lifecycle.disposed;
  }

  function getVoiceInputRevision() {
    return voiceInputToken;
  }

  function completeVoiceCapture(controller) {
    if (voiceCaptureController === controller) voiceCaptureController = null;
  }

  async function startVoiceRecording() {
    if (sceneOwnsPlayback()) { bubblePort.showError("请在小屋中继续对话。"); return; }
    if (["opening", "recording", "processing"].includes(voiceInputState)) return;
    if (!state.voiceInputEnabled) {
      bubblePort.showError("语音输入已关闭");
      return;
    }
    let tookOverReply = false;
    if (chatPort.isReplyActive()) {
      tookOverReply = chatPort.interruptReply({
        announce: false,
        reason: "user_started_voice_input"
      });
    }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      bubblePort.showError("当前 WebView 不支持录音");
      return;
    }
    const { token, controller } = beginVoiceCapture();
    setVoiceInputState("opening");
    bubblePort.showBubbleText("正在打开麦克风……再次点麦克风可取消。", { transient: false, kind: "status" });
    statusPort.setRuntimeStatus("正在打开麦克风", { mode: "listening" });
    try {
      await voiceCallPort.waitForVoiceCallCleanup();
      if (!isCurrentVoiceInput(token)) return;
      const stream = await captureMicrophone({
        getUserMedia: (constraints) => navigator.mediaDevices.getUserMedia(constraints),
        signal: controller.signal
      });
      if (!isCurrentVoiceInput(token)) {
        for (const track of stream?.getTracks?.() || []) track.stop();
        return;
      }
      voiceStream = stream;
      voiceChunks = [];
      voiceMimeType = selectVoiceMimeType();
      const options = voiceMimeType ? { mimeType: voiceMimeType } : undefined;
      try {
        voiceRecorder = new MediaRecorder(voiceStream, options);
      } catch {
        voiceRecorder = new MediaRecorder(voiceStream);
        voiceMimeType = voiceRecorder.mimeType || voiceMimeType;
      }
      const recorder = voiceRecorder;
      recorder.addEventListener("dataavailable", (event) => {
        if (!isCurrentVoiceInput(token) || voiceRecorder !== recorder) return;
        if (event.data?.size > 0) voiceChunks.push(event.data);
      });
      voiceStartedAt = Date.now();
      voiceRecorder.start();
      setVoiceInputState("recording");
      characterPort.setPetEmotion("listening", { persist: false });
      bubblePort.setPetMotion("thinking");
      bubblePort.showBubbleText(
        tookOverReply ? "上一轮已停下，正在听你说……" : "正在听……",
        { transient: false }
      );
      statusPort.setRuntimeStatus(tookOverReply ? "已接管上一轮，语音录制中" : "语音录制中", {
        mode: "listening"
      });
      if (voicePresentationPort.canUseRealtimeVoice()) {
        voiceSingleTurnPort.startRealtimeVoiceTurn(voiceStream);
      }
    } catch (error) {
      if (!isCurrentVoiceInput(token)) return;
      voiceSingleTurnPort.closeRealtimeVoiceTurn(voiceSingleTurnPort.realtimeVoiceTurn, "capture_start_failed");
      cleanupVoiceRecorder();
      setVoiceInputState("idle");
      bubblePort.showError(describeVoiceError(error));
    } finally {
      completeVoiceCapture(controller);
    }
  }

  async function stopVoiceRecording() {
    if (voiceInputState !== "recording" || !voiceRecorder) return Promise.resolve();

    const recorder = voiceRecorder;
    const token = voiceInputToken;
    const isCurrent = () => isCurrentVoiceInput(token) && voiceRecorder === recorder;
    const turn = voiceSingleTurnPort.realtimeVoiceTurn;
    const durationMs = Date.now() - voiceStartedAt;
    setVoiceInputState("processing");

    if (durationMs < MIN_RECORDING_MS) {
      voiceSingleTurnPort.closeRealtimeVoiceTurn(turn, "recording_too_short");
      try {
        await stopVoiceRecorderToBlob(recorder);
      } catch {
        // The short-recording message below is the actionable result.
      }
      if (!isCurrent()) return;
      cleanupVoiceRecorder();
      setVoiceInputState("idle");
      bubblePort.showError("录音太短啦，我没听清。");
      return;
    }

    const realtimeFinish = turn?.session
      ? turn.session
          .finishInput()
          .then(() => true)
          .catch((error) => {
            voiceSingleTurnPort.markRealtimeVoiceFailure(turn, {
              reason: "voice_realtime_finalize_failed",
              message: friendlyErrorMessage(formatError(error)),
              terminal: true,
              committed: turn.committed
            });
            return false;
          })
      : Promise.resolve(false);

    let blob;
    let realtimeAccepted = false;
    try {
      [realtimeAccepted, blob] = await Promise.all([
        realtimeFinish,
        stopVoiceRecorderToBlob(recorder)
      ]);
    } catch (error) {
      if (!isCurrent()) return;
      voiceSingleTurnPort.closeRealtimeVoiceTurn(turn, "recording_stop_failed");
      cleanupVoiceRecorder();
      setVoiceInputState("idle");
      bubblePort.showError(describeVoiceError(error));
      return;
    }
    if (!isCurrent()) return;
    cleanupVoiceRecorder();

    if (!turn) {
      if (blob.size < 512) {
        setVoiceInputState("idle");
        bubblePort.showError("录音太短啦，我没听清。");
        return;
      }
      await transcribeVoiceBlob(blob, { autoSubmit: true });
      return;
    }
    if (turn.closed || voiceSingleTurnPort.realtimeVoiceTurn !== turn) return;

    turn.fallbackBlob = turn.committed ? null : blob;
    if (realtimeAccepted && !turn.failure) {
      turn.responseActive = true;
      chatPort.setVoiceSending(true);
      bubblePort.showThinking();
      statusPort.setRuntimeStatus("语音识别收尾中", { mode: "thinking" });
      voiceSingleTurnPort.armRealtimeVoiceFinalWatchdog(turn);
      statusPort.updateActivityControls();
      settingsPort.scheduleSettingsSnapshot();
      return;
    }

    await voiceSingleTurnPort.fallbackRealtimeVoiceToBatch(turn);
  }

  async function cancelVoiceRecording({ notice = false } = {}) {
    voiceInputToken += 1;
    voiceCaptureController?.abort();
    voiceCaptureController = null;
    if (voiceCallPort.realtimeVoiceCall) {
      await voiceCallPort.stopRealtimeVoiceCall({
        notice,
        reason: notice ? "user_cancelled_call" : "voice_input_cancelled"
      });
      return;
    }
    if (asrController) {
      asrController.abort();
      asrController = null;
    }
    if (voiceRecorder && voiceRecorder.state !== "inactive") {
      try {
        voiceRecorder.stop();
      } catch {
        // Cancellation should stay quiet.
      }
    }
    voiceSingleTurnPort.closeRealtimeVoiceTurn(voiceSingleTurnPort.realtimeVoiceTurn, "voice_input_cancelled");
    cleanupVoiceRecorder();
    setVoiceInputState(state.voiceInputEnabled ? "idle" : "disabled");
    if (notice) {
      bubblePort.showBubbleText("语音输入已取消。", { transient: true, durationMs: 1600 });
      statusPort.setRuntimeStatus("语音输入已取消", { mode: "idle" });
    }
  }

  function stopVoiceRecorderToBlob(recorder) {
    return stopVoiceRecorderToBlobHelper(recorder, voiceChunks, voiceMimeType);
  }

  async function transcribeVoiceBlob(blob, { autoSubmit = false, filename = "" } = {}) {
    const token = ++voiceInputToken;
    setVoiceInputState("processing");
    characterPort.setPetEmotion("thinking", { persist: false });
    bubblePort.setPetMotion("thinking", { durationMs: 1400 });
    bubblePort.showBubbleText("我在识别语音……", { transient: false });
    statusPort.setRuntimeStatus("语音识别中", { mode: "thinking" });

    const controller = new AbortController();
    asrController = controller;
    const timeoutId = lifecycle.setTimeout(() => controller.abort(), ASR_TIMEOUT_MS);
    const form = new FormData();
    form.append("file", blob, filename || getVoiceFilename(blob?.type));
    form.append("language", "zh");
    form.append("user_id", state.sessionId || "desktop_pet_next");
    form.append("session_id", state.sessionId || "desktop_pet_next");
    form.append("real_user_id", statePort.getProfileUserId());
    form.append("client_mode", CLIENT_MODE);
    form.append("character_pack_id", characterPort.getCurrentCharacterPackId());

    try {
      const requestInit = {
        method: "POST",
        cache: "no-store",
        body: form,
        signal: controller.signal
      };
      if (platformPort.isTauriRuntime) {
        requestInit.connectTimeout = 30_000;
      }

      const response = await platformPort.backendFetch(backendPort.buildBackendEndpointUrl("asr", "/asr", { t: Date.now() }), requestInit);
      if (!isCurrentVoiceInput(token)) return;
      const payload = await readJsonResponse(response);
      if (!isCurrentVoiceInput(token)) return;
      if (!response.ok) {
        throw new Error(extractBackendErrorMessage(payload) || `ASR HTTP ${response.status}`);
      }

      const text = String(payload?.text || payload?.transcript || "").trim();
      if (!payload?.ok || !text) {
        throw new Error(extractBackendErrorMessage(payload) || "没听清，可以再说一次。");
      }

      if (autoSubmit) {
        bubblePort.showBubbleText(`听清了：${text}`, {
          transient: false,
          kind: "status"
        });
        statusPort.setRuntimeStatus("语音已识别，正在发送", { mode: "thinking" });
        await chatPort.sendMessage(text);
        return;
      }

      inputPort.setChatInputText(text, { append: Boolean(els.chatInput.value.trim()) });
      characterPort.setTransientEmotion("success", { durationMs: 2600 });
      bubblePort.showBubbleText("我听写好了，确认一下再发送。", {
        transient: true,
        durationMs: 2400,
        kind: "status"
      });
      statusPort.setRuntimeStatus("语音已转成文字", { mode: "idle" });
    } catch (error) {
      if (isCurrentVoiceInput(token) && !chatPort.isAbortLike(error)) {
        bubblePort.showError(describeVoiceError(error));
      }
    } finally {
      lifecycle.clearTimeout(timeoutId);
      if (asrController === controller) asrController = null;
      if (isCurrentVoiceInput(token)) {
        setVoiceInputState(state.voiceInputEnabled ? "idle" : "disabled");
      }
    }
  }

  async function readBackendErrorMessage(response, fallback = "请求失败") {
    return readBackendErrorMessageHelper(response, fallback, friendlyErrorMessage);
  }

  function setVoiceInputState(nextState) {
    voiceInputState = nextState;
    voicePresentationPort.updateVoiceRecordButton();
    statusPort.updateActivityControls();
    if (!els.chatForm.hidden) inputPort.scheduleChatInputAutoHide();
    settingsPort.scheduleSettingsSnapshot();
  }

  function cleanupVoiceRecorder() {
    for (const track of voiceStream?.getTracks?.() || []) {
      track.stop();
    }
    voiceRecorder = null;
    voiceStream = null;
    voiceChunks = [];
    voiceStartedAt = 0;
  }

  function selectVoiceMimeType() {
    return selectVoiceMimeTypeHelper(MediaRecorder);
  }

  function getVoiceFilename(mimeTypeValue = voiceMimeType) {
    return getVoiceFilenameHelper(mimeTypeValue);
  }

  function describeVoiceError(error) {
    return describeVoiceErrorHelper(error, (err) => friendlyErrorMessage(formatError(err)));
  }

  return {
    get voiceInputState() { return voiceInputState; },
    get asrController() { return asrController; },
    beginVoiceCapture, isCurrentVoiceInput, getVoiceInputRevision, completeVoiceCapture,
    startVoiceRecording, stopVoiceRecording, cancelVoiceRecording,
    transcribeVoiceBlob, readBackendErrorMessage, setVoiceInputState, cleanupVoiceRecorder,
    selectVoiceMimeType, getVoiceFilename, describeVoiceError
  };
}
