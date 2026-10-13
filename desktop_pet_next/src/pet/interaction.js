import { LOCAL_CLICK_DELAY_MS } from './constants.js';
import { getProfileLocalClickLines, getProfileDefaultEmotion } from './profile.js';

/** Owns interaction behavior and its temporary runtime state. */
export function createInteractionController({
  state,
  els,
  lifecycle,
  chat: chatPort,
  tts: ttsPort,
  bubble: bubblePort,
  character: characterPort,
  music: musicPort
}) {
  let localInteractionTimer = 0;

  let localInteractionToken = 0;

  let localInteractionActive = false;

  let lastLocalClickIndex = -1;

  let previewEmotionTimer = 0;

  let previewEmotionRestore = "";

  let previewEmotionToken = 0;

  let clickTimer = 0;

  function scheduleLocalClick() {
    const bubbleBusy = els.bubble.classList.contains("visible") && !canLocalInteractionReplaceBubble();
    if (chatPort.sending || ttsPort.ttsActive || ttsPort.ttsQueue.length > 0 || bubbleBusy || !els.chatForm.hidden || !els.menu.hidden) {
      return;
    }
    cancelLocalClick();
    clickTimer = lifecycle.setTimeout(() => {
      clickTimer = 0;
      showLocalInteraction();
    }, LOCAL_CLICK_DELAY_MS);
  }

  function cancelLocalClick() {
    lifecycle.clearTimeout(clickTimer);
    clickTimer = 0;
  }

  function canLocalInteractionReplaceBubble() {
    if (!els.bubble.classList.contains("visible")) return true;
    return localInteractionActive || (!chatPort.sending && !ttsPort.ttsActive && ttsPort.ttsQueue.length === 0 && !bubblePort.replyDisplayActive);
  }

  function showLocalInteraction() {
    if (chatPort.sending || !els.chatForm.hidden) return;
    cancelEmotionPreview({ restore: true });
    const item = pickLocalClickLine();
    const token = ++localInteractionToken;
    lifecycle.clearTimeout(localInteractionTimer);
    localInteractionActive = true;
    bubblePort.setPetMotion("click", { durationMs: 680 });
    characterPort.setPetEmotion(item.emotion, { persist: false });
    bubblePort.showBubbleText(item.text, { transient: true, durationMs: 2600, local: true });
    localInteractionTimer = lifecycle.setTimeout(() => {
      if (token !== localInteractionToken || chatPort.sending) return;
      localInteractionActive = false;
      musicPort.setRestingPetEmotion();
      musicPort.scheduleMusicEmotionRestore();
    }, 2700);
  }

  function clearLocalInteraction() {
    localInteractionToken += 1;
    localInteractionActive = false;
    lifecycle.clearTimeout(localInteractionTimer);
  }

  function pickLocalClickLine() {
    const lines = getProfileLocalClickLines();
    if (lines.length <= 1) {
      lastLocalClickIndex = 0;
      return lines[0];
    }
    let index = Math.floor(Math.random() * lines.length);
    if (index === lastLocalClickIndex) {
      index = (index + 1 + Math.floor(Math.random() * (lines.length - 1))) % lines.length;
    }
    lastLocalClickIndex = index;
    return lines[index];
  }

  function previewEmotion(emotion) {
    if (chatPort.sending || !emotion) return false;
    const previous = previewEmotionRestore || state.currentEmotion || getProfileDefaultEmotion();
    const token = ++previewEmotionToken;
    lifecycle.clearTimeout(previewEmotionTimer);
    previewEmotionRestore = previous;
    const resolved = characterPort.setPetEmotion(emotion, { persist: false });
    bubblePort.showBubbleText(`表情预览：${resolved}`, { transient: true, durationMs: 2200 });
    previewEmotionTimer = lifecycle.setTimeout(() => {
      if (token !== previewEmotionToken || chatPort.sending) return;
      cancelEmotionPreview({ restore: true });
    }, 2300);
    return true;
  }

  function cancelEmotionPreview({ restore = false } = {}) {
    lifecycle.clearTimeout(previewEmotionTimer);
    previewEmotionTimer = 0;
    previewEmotionToken += 1;
    const restoreEmotion = previewEmotionRestore;
    previewEmotionRestore = "";
    if (restore && restoreEmotion) {
      characterPort.setPetEmotion(restoreEmotion, { persist: false });
    }
    musicPort.scheduleMusicEmotionRestore();
  }

  return {
    get localInteractionActive() { return localInteractionActive; },
    get previewEmotionRestore() { return previewEmotionRestore; },
    scheduleLocalClick,
    cancelLocalClick,
    clearLocalInteraction,
    previewEmotion,
    cancelEmotionPreview
  };
}
