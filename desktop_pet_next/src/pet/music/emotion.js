import { MUSIC_EMOTION_RESTORE_DELAY_MS } from '../constants.js';
import { getProfileMusicEmotion, getProfileDefaultEmotion, getProfileRestingEmotions } from '../profile.js';
import { createRestingExpressionResolver } from '../../resting-expression.js';

/** Restores local care/music expressions after higher-priority interaction ends. */
export function createMusicEmotionController({
  state, els, lifecycle, systemMedia: systemMediaPort, character: characterPort,
  chat: chatPort, tts: ttsPort, voice: voicePort, window: windowPort, interaction: interactionPort,
  bubble: bubblePort, care: carePort, visualRenderer
}) {
  let musicEmotionRestoreTimer = 0;
  const resting = createRestingExpressionResolver();
  const failedExpressions = new Map();
  let failureScope = '';

  function setMusicEmotion(active) {
    syncMusicMotion();
    lifecycle.clearTimeout(musicEmotionRestoreTimer);
    musicEmotionRestoreTimer = 0;
    if (active || systemMediaPort.isMusicEmotionSourceActive()) scheduleMusicEmotionRestore({ delayMs: 0 });
    else setRestingPetEmotion();
  }

  function scheduleMusicEmotionRestore({ delayMs = MUSIC_EMOTION_RESTORE_DELAY_MS } = {}) {
    lifecycle.clearTimeout(musicEmotionRestoreTimer);
    musicEmotionRestoreTimer = 0;
    // Capture the low-state transition even while a reply or preview owns the image.
    getRestingPetEmotion();
    const delay = Math.max(0, Number(delayMs) || 0);
    musicEmotionRestoreTimer = lifecycle.setTimeout(() => {
      musicEmotionRestoreTimer = 0;
      setRestingPetEmotion();
    }, delay);
  }

  function canApplyRestingEmotionNow() {
    if (chatPort.sending || ttsPort.ttsActive || ttsPort.ttsQueue.length > 0) return false;
    if (["opening", "recording", "processing"].includes(voicePort.voiceInputState)) return false;
    if (voicePort.realtimeVoiceCall || bubblePort?.replyDisplayActive || els.bubble?.classList.contains('visible')) return false;
    if (windowPort.dragState || windowPort.physicsTimer || windowPort.playState.heldEmotion) return false;
    if (interactionPort.localInteractionActive || interactionPort.previewEmotionRestore) return false;
    if (characterPort.transientEmotionTimer) return false;
    if (!els.chatForm.hidden || !els.menu.hidden) return false;
    return true;
  }

  function setRestingPetEmotion({ persist = false } = {}) {
    syncMusicMotion();
    const emotion = getRestingPetEmotion();
    if (!canApplyRestingEmotionNow()) {
      scheduleMusicEmotionRestore();
      return state.currentEmotion;
    }
    return characterPort.setPetEmotion(emotion, { persist });
  }

  function getRestingPetEmotion() {
    const scopeKey = [state.instanceId, state.backendUrl, state.boundBotId, state.profileUserId, state.characterPackId];
    const scope = JSON.stringify(scopeKey);
    if (failureScope !== scope) { failedExpressions.clear(); failureScope = scope; }
    const emotion = resting.resolve({
      scopeKey,
      rules: getProfileRestingEmotions(),
      care: carePort?.isCareRuntimeActive() ? state.care : null,
      defaultEmotion: getProfileDefaultEmotion(),
      musicEmotion: systemMediaPort.isMusicEmotionSourceActive() ? getProfileMusicEmotion() : ''
    });
    const entry = characterPort.resolveEmotionEntry(emotion);
    return failedExpressions.has(entry?.id) && failedExpressions.get(entry.id) === (entry.url || entry.path || '')
      ? getProfileDefaultEmotion() : emotion;
  }

  function syncMusicMotion() {
    visualRenderer?.setMusicMotion({
      active: systemMediaPort.isMusicEmotionSourceActive(),
      emotion: characterPort.resolveEmotionEntry(getProfileMusicEmotion())?.id || ''
    });
  }

  function handleRestingEmotionLoadError(expression) {
    const entry = characterPort.resolveEmotionEntry(getRestingPetEmotion());
    const fallback = characterPort.resolveEmotionEntry(getProfileDefaultEmotion());
    if (expression?.id !== entry?.id || entry?.id === fallback?.id || state.currentEmotion !== entry?.id) return;
    failedExpressions.set(entry.id, entry.url || entry.path || '');
    setRestingPetEmotion();
  }

  return {
    get musicEmotionRestoreTimer() { return musicEmotionRestoreTimer; },
    setMusicEmotion,
    scheduleMusicEmotionRestore,
    setRestingPetEmotion,
    getRestingPetEmotion,
    canApplyRestingEmotionNow,
    handleRestingEmotionLoadError,
    resetRestingEmotionFailures: () => failedExpressions.clear(),
  };
}
