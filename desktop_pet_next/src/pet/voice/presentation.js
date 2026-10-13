import { canUseRealtimeVoice as canUseRealtimeVoiceHelper } from "../../desktop-voice-session.js";
import { supportsRealtimeVoiceCapture } from "../../realtime-voice-client.js";
import { normalizeUnitEmotion } from "../../speech-units.js";

/** Renders the microphone state and the reply belonging to the current speaker. */
export function createVoicePresentationController({
  state, els, platform: platformPort, lifecycle, bubble: bubblePort,
  character: characterPort, music: musicPort, recording: voiceRecordingPort, call: voiceCallPort
}) {
  const { window } = platformPort;

  function canPresentRealtimeVoiceTurn(turn) {
    const id = Number(turn.id);
    const lastPresentedId = Number(turn.call?.lastPresentedTurnId);
    if (Number.isFinite(id) && Number.isFinite(lastPresentedId) && id < lastPresentedId) return false;
    const owner = turn.call?.presentingTurn;
    return !owner || owner === turn || owner.closed || !(owner.responseActive || owner.playbackActive);
  }

  function canUseRealtimeVoice() {
    return canUseRealtimeVoiceHelper({
      voiceEnabled: state.voiceEnabled,
      hasVoicePlayer: Boolean(els.voicePlayer),
      supportsCapture: supportsRealtimeVoiceCapture(window)
    });
  }

  function showFirstRealtimeVoicePlaybackText(turn, header) {
    const text = String(header?.text || "").trim();
    if (!text) return false;
    const appended = appendRealtimeVoicePlaybackText(turn, header, { speaking: false });
    if (!appended) return false;
    if (!canPresentRealtimeVoiceTurn(turn)) return true;
    if (state.currentEmotion === characterPort.resolveEmotionEntry("thinking")?.id) {
      musicPort.setRestingPetEmotion();
    }
    return true;
  }

  function showStartedRealtimeVoicePlaybackText(turn, header) {
    const text = String(header?.text || "").trim();
    bubblePort.setPetMotion("speaking");
    if (text) appendRealtimeVoicePlaybackText(turn, header, { speaking: true });
  }

  function appendRealtimeVoicePlaybackText(turn, header, { speaking = false } = {}) {
    const text = String(header?.text || "").trim();
    if (!text) return false;
    const deliveryId = String(header?.delivery_id || "").trim();
    const ordinal = Number(header?.ordinal);
    const key = deliveryId || `${Number.isFinite(ordinal) ? ordinal : turn.replySegments.size}:${text}`;
    const duplicate = turn.replySegments.has(key);
    if (duplicate && !speaking) return false;
    if (!duplicate) {
      turn.replySegments.set(key, {
        ordinal: Number.isFinite(ordinal) ? ordinal : turn.replySegments.size,
        text
      });
      turn.replyBubbleText = [...turn.replySegments.values()]
        .sort((left, right) => left.ordinal - right.ordinal)
        .map((item) => item.text)
        .join("")
        .trim();
    }
    if (!canPresentRealtimeVoiceTurn(turn)) return !duplicate;
    if (speaking || !turn.hasShownSpeech) {
      const emotion = normalizeUnitEmotion(header?.emotion);
      if (emotion) {
        bubblePort.applyPayloadEmotion({ emotion });
        turn.unitEmotionPresented = true;
      }
    }
    lifecycle.clearTimeout(bubblePort.bubbleTimer);
    if (!turn.hasShownSpeech) {
      turn.hasShownSpeech = true;
      bubblePort.showBubbleText(turn.replyBubbleText, {
        transient: false,
        speaking,
        kind: "reply"
      });
    } else {
      bubblePort.displayReplyBubbleText(turn.replyBubbleText, { speaking });
      els.bubbleText.scrollTop = els.bubbleText.scrollHeight;
    }
    return true;
  }

  function scheduleRealtimeVoiceBubbleReset(text) {
    bubblePort.scheduleVoiceBubbleReset(text);
  }

  function applyRealtimeVoicePresentation(turn) {
    if (!turn.presentation || turn.unitEmotionPresented || !canPresentRealtimeVoiceTurn(turn)) return;
    bubblePort.applyPayloadEmotion(turn.presentation);
    if (turn.presentationApplied) return;
    turn.presentationApplied = true;
  }

  function updateVoiceRecordButton() {
    if (!els.voiceRecordButton) return;
    const effectiveState = state.voiceInputEnabled ? voiceRecordingPort.voiceInputState : "disabled";
    els.voiceRecordButton.classList.toggle("recording", effectiveState === "recording");
    els.voiceRecordButton.classList.toggle("processing", effectiveState === "processing");
    els.voiceRecordButton.disabled = effectiveState === "disabled" || effectiveState === "processing";
    if (voiceCallPort.realtimeVoiceCall) {
      els.voiceRecordButton.textContent = "挂";
      els.voiceRecordButton.title = "结束语音通话";
      els.voiceRecordButton.disabled = false;
    } else if (effectiveState === "opening") {
      els.voiceRecordButton.textContent = "取消";
      els.voiceRecordButton.title = "取消打开麦克风";
    } else if (effectiveState === "recording") {
      els.voiceRecordButton.textContent = "停";
      els.voiceRecordButton.title = "停止录音";
    } else if (effectiveState === "processing") {
      els.voiceRecordButton.textContent = "…";
      els.voiceRecordButton.title = "正在识别语音";
    } else {
      els.voiceRecordButton.textContent = "麦";
      els.voiceRecordButton.title = state.voiceInputEnabled
        ? canUseRealtimeVoice()
          ? "开始语音通话 Ctrl+Shift+Space"
          : "语音输入 Ctrl+Shift+Space"
        : "语音输入已关闭";
    }
  }

  return {
    canUseRealtimeVoice, canPresentRealtimeVoiceTurn, updateVoiceRecordButton, showFirstRealtimeVoicePlaybackText,
    showStartedRealtimeVoicePlaybackText, appendRealtimeVoicePlaybackText,
    scheduleRealtimeVoiceBubbleReset, applyRealtimeVoicePresentation
  };
}
