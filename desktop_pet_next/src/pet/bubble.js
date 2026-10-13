import { SPEECH_UNITS_PROTOCOL, utteranceDeliveryEvents, normalizeUnitEmotion } from "../speech-units.js";
import { segmentSpeechForDelivery, missingReplySegments } from "../speech-delivery.js";
import { getBubbleSegmentDisplayDelay } from "../bubble-delivery.js";
import { setReplyText } from "../reply-text.js";
import { CHARACTER_NAME } from "../character-profile.js";
import { BASE_CAPABILITIES, AUDIO_PLAYBACK_CAPABILITY, CLIENT_SEGMENT_SOFT_LIMIT, TTS_SHORT_SEGMENT_MAX_CHARS } from './constants.js';
import { getProfileIdentityText } from './profile.js';
import { friendlyErrorMessage } from './utils.js';

/** Owns bubble behavior and its temporary runtime state. */
export function createBubbleController({
  state,
  resourceState,
  els,
  visualRenderer,
  lifecycle,
  attachments: attachmentsPort,
  status: statusPort,
  tts: ttsPort,
  chat: chatPort,
  character: characterPort,
  care: carePort,
  interaction: interactionPort,
  window: windowPort,
  music: musicPort,
  voice: voicePort
}) {
  let bubbleToken = 0;

  let bubbleTimer = 0;

  let bubbleKind = "none";

  let replyDisplayActive = false;

  let segmentTimer = 0;

  let streamedReplyTurnToken = 0;

  let streamedReplySegments = [];

  let streamedReplyUnitKeys = [];

  let streamedReplySeenUnitKeys = new Set();

  const streamedReplyUnitEmotions = new Map();

  let streamedReplyTexts = [];

  let streamedReplySegmentActive = false;

  let lastTurnSignature = "";

  let lastTurnTextKey = "";

  let motionTimer = 0;

  function restoreLatestReply(bundle) {
    const payload = bundle?.latest_final_json;
    if (!payload || typeof payload !== "object") return false;
    return renderPayload(payload, {
      source: "restore",
      speaking: false,
      persistEmotion: false,
      force: true
    });
  }

  function renderPayload(
    payload,
    {
      source = "live",
      speaking = source === "live",
      persistEmotion = true,
      force = false,
      suppressSpeech = false,
      turnToken = 0
    } = {}
  ) {
    if (!payload || typeof payload !== "object") return false;
    if (payload._deliberate_silence) return false;
    applyPayloadCareSnapshot(payload, { source });
    const speechUnits = payload.speech_protocol === SPEECH_UNITS_PROTOCOL || Array.isArray(payload.utterances);
    if (!speechUnits) applyPayloadEmotion(payload, { persist: persistEmotion });
    else if (source !== "live") {
      const lastExpression = [...(payload.utterances || [])].reverse()
        .find(unit => normalizeUnitEmotion(unit?.emotion));
      applyPayloadEmotion({ emotion: lastExpression?.emotion || payload.emotion }, { persist: persistEmotion });
    }
    // Restored/replayed frames are presentation data. Tool effects are owned by
    // the live execution path and must never be re-issued by history rendering.
    if (source === "live") {
      attachmentsPort.applyPayloadFileDeliveries(payload);
      attachmentsPort.applyPayloadBrowserEvents(payload);
    }
    if (suppressSpeech) return false;

    const speech = String(payload.speech || payload.text || "").trim();
    const segments = speech
      ? splitSpeechText(speech)
      : normalizeSegments(payload.speech_segments || payload.segments);
    if (segments.length > 0) {
      const signature = `segments:${segments.join("\u241e")}`;
      const textKey = buildSpeechTextKey(segments.join(""));
      if (!force && (signature === lastTurnSignature || (textKey && textKey === lastTurnTextKey))) return false;
      lastTurnSignature = signature;
      lastTurnTextKey = textKey;
      if (!speechUnits || source !== "live" || !payload.utterances?.length) {
        showSpeechSegments(segments, { speaking, syncToAudio: !speechUnits && source === "live" && shouldSyncReplyToTts() });
      }
      if (source === "live") statusPort.setRuntimeStatus("回复中", { mode: "replying" });
      if (source === "live" && !speechUnits) ttsPort.queueLiveTtsPayloadItems(segments, signature);
      if (source === "live" && speechUnits) {
        const activeTurn = turnToken || chatPort.activeTurnToken;
        ttsPort.beginSpeechUnits(activeTurn);
        for (const event of utteranceDeliveryEvents(payload.utterances, payload.emotion)) {
          const presentationKey = `${activeTurn}:final:${event.unit_index}`;
          if (event.type === "speech_unit_text") queueSpeechUnitText(event.text, activeTurn, presentationKey, event.emotion);
          else if (event.type === "voice_segment") ttsPort.queueSpeechUnitAudio({ ...event, presentation_key: presentationKey }, activeTurn);
          else ttsPort.finishSpeechUnitAudio({ ...event, presentation_key: presentationKey, queue_text_only: true }, activeTurn);
        }
        ttsPort.finishSpeechUnits(activeTurn);
      }
      return true;
    }

    return false;
  }

  function applyPayloadEmotion(payload, { persist = true } = {}) {
    const emotion = String(payload?.emotion || "").trim();
    if (emotion) characterPort.setPetEmotion(emotion, { persist });
  }

  function applyPayloadCareSnapshot(payload, { source = "live" } = {}) {
    if (source !== "live") return false;
    const snapshot = payload?.care_state || payload?.careState;
    return carePort.applyAuthoritativeCareSnapshot(snapshot);
  }

  function normalizeSegments(value) {
    if (!Array.isArray(value)) return [];
    return value
      .map((item) => {
        if (item && typeof item === "object") return item.text || item.speech || item.content || "";
        return item;
      })
      .map((item) => String(item || "").trim())
      .filter(Boolean);
  }

  function buildClientCapabilities() {
    return [...BASE_CAPABILITIES, AUDIO_PLAYBACK_CAPABILITY];
  }

  function buildSpeechTextKey(text) {
    return String(text || "").replace(/\s+/g, "").trim();
  }

  function splitSpeechText(text) {
    const source = String(text || "").replace(/\r\n/g, "\n").trim();
    if (!source) return [];
    return segmentSpeechForDelivery(source, { minChars: 2, maxChars: CLIENT_SEGMENT_SOFT_LIMIT, allowHardCut: false });
  }

  function showThinking() {
    characterPort.setPetEmotion("thinking");
    setPetMotion("thinking");
    statusPort.setRuntimeStatus("思考中", { mode: "thinking" });
    showBubbleText("……", { transient: false, kind: "thinking" });
  }

  function showToolWorking(event, { hasShownReply = false } = {}) {
    // Backend emits assistant_working ("我查一下。") right before a tool runs, so
    // the pet can show it's actively working instead of looking frozen on
    // "thinking" through a multi-second tool call (web search, memory, …).
    const message = String(event?.message || "").trim() || "我查一下。";
    setPetMotion("thinking");
    statusPort.setRuntimeStatus("查一下…", { mode: "thinking" });
    if (hasShownReply) {
      // The model already spoke a pre-tool line; don't clobber that expressive
      // bubble (INV-1) — the motion + status above are enough of a working hint.
      return;
    }
    // Nothing shown yet (e.g. native tool with empty pre-speech): surface the
    // working line as a thinking-kind bubble. It is replaced by the real reply
    // and, if the turn ends with nothing rendered, cleared by the stream_end
    // leftover-thinking guard.
    characterPort.setPetEmotion("thinking");
    showBubbleText(message, { transient: false, kind: "thinking" });
  }

  function showError(message) {
    const friendly = friendlyErrorMessage(message);
    characterPort.setTransientEmotion("confused", { durationMs: 3600 });
    statusPort.setRuntimeStatus(friendly, { mode: "error" });
    showBubbleText(friendly, { transient: true, durationMs: 3500, kind: "error" });
  }

  function shouldSyncReplyToTts() {
    return Boolean(state.voiceEnabled && els.voicePlayer && resourceState.tts?.enabled !== false);
  }

  function showSpeechSegments(segments, { speaking = true, syncToAudio = false } = {}) {
    const items = Array.isArray(segments) ? segments.filter(Boolean) : [];
    if (!items.length) return;
    if (syncToAudio) {
      resetStreamedReplySegments(chatPort.activeTurnToken);
      streamedReplySegments.push(...items);
      streamedReplyUnitKeys.push(...items.map(() => null));
      showBubbleText(items[0], { transient: false, kind: "reply" });
      setPetMotion("thinking");
      return;
    }

    interactionPort.clearLocalInteraction();
    lifecycle.clearTimeout(bubbleTimer);
    lifecycle.clearTimeout(segmentTimer);
    const token = ++bubbleToken;
    bubbleKind = "reply";
    replyDisplayActive = true;
    let index = 0;

    const showNext = () => {
      if (token !== bubbleToken) return;
      const text = items[index];
      displayReplyBubbleText(text, { speaking });
      index += 1;

      if (index < items.length) {
        segmentTimer = lifecycle.setTimeout(showNext, getSegmentDisplayDelay(text));
        return;
      }

      scheduleBubbleReset(Math.max(text.length, 4), token);
    };

    showNext();
  }

  function resetStreamedReplySegments(turnToken = 0) {
    lifecycle.clearTimeout(segmentTimer);
    segmentTimer = 0;
    streamedReplyTurnToken = Number.isFinite(Number(turnToken)) ? Number(turnToken) : 0;
    streamedReplySegments = [];
    streamedReplyUnitKeys = [];
    streamedReplySeenUnitKeys.clear();
    streamedReplyUnitEmotions.clear();
    streamedReplyTexts = [];
    streamedReplySegmentActive = false;
  }

  function queueStreamedReplySegment(text, turnToken, { syncToAudio = true } = {}) {
    // This is a delivery queue derived from the model's canonical `speech`
    // field. It must never become a second body-authoring surface.
    const normalized = String(text || "").trim();
    const normalizedTurnToken = Number.isFinite(Number(turnToken)) ? Number(turnToken) : 0;
    if (!normalized || !chatPort.isTurnActive(normalizedTurnToken)) return false;
    if (streamedReplyTurnToken !== normalizedTurnToken) resetStreamedReplySegments(normalizedTurnToken);
    const textKey = buildSpeechTextKey(normalized);
    if (!textKey) return false;
    streamedReplyTexts.push(normalized);
    streamedReplySegments.push(normalized);
    streamedReplyUnitKeys.push(null);
    if (syncToAudio && shouldSyncReplyToTts()) {
      // Preview the first sentence while synthesis runs. Further sentences are
      // displayed by the audio queue when that exact audio begins playing.
      if (bubbleKind !== "reply" && normalized.length > TTS_SHORT_SEGMENT_MAX_CHARS) {
        const preview = ttsPort.streamingTtsPendingShort ? `${ttsPort.streamingTtsPendingShort} ${normalized}` : normalized;
        showBubbleText(preview, { transient: false, kind: "reply" });
        setPetMotion("thinking");
      }
    } else if (!streamedReplySegmentActive) {
      showNextStreamedReplySegment(normalizedTurnToken);
    }
    return true;
  }

  function queueSpeechUnitText(text, turnToken, presentationKey, emotion = "") {
    const normalized = String(text || "").trim();
    if (!normalized || !chatPort.isTurnActive(turnToken)) return false;
    if (streamedReplyTurnToken !== turnToken) resetStreamedReplySegments(turnToken);
    if (streamedReplySeenUnitKeys.has(presentationKey)) return false;
    streamedReplySeenUnitKeys.add(presentationKey);
    const expression = normalizeUnitEmotion(emotion);
    if (expression) streamedReplyUnitEmotions.set(presentationKey, expression);
    streamedReplyTexts.push(normalized);
    streamedReplySegments.push(normalized);
    streamedReplyUnitKeys.push(presentationKey);
    lifecycle.clearTimeout(bubbleTimer);
    if (shouldSyncReplyToTts() && ttsPort.speechUnitsMutedTurnToken !== turnToken) {
      // Full bilingual expressions have no sentence-by-sentence alignment.
      // Only the first is previewed; audio playback owns later transitions.
      if (streamedReplyTexts.length === 1) {
        applySpeechUnitEmotion(presentationKey);
        showBubbleText(normalized, { transient: false, kind: "reply" });
        setPetMotion("thinking");
      }
    } else if (!streamedReplySegmentActive) showNextStreamedReplySegment(turnToken);
    return true;
  }

  function markSpeechUnitPresented(presentationKey) {
    applySpeechUnitEmotion(presentationKey);
    const index = streamedReplyUnitKeys.indexOf(presentationKey);
    if (index < 0) return;
    streamedReplyUnitKeys.splice(index, 1);
    streamedReplySegments.splice(index, 1);
  }

  function applySpeechUnitEmotion(presentationKey) {
    const emotion = streamedReplyUnitEmotions.get(presentationKey);
    streamedReplyUnitEmotions.delete(presentationKey);
    if (emotion) characterPort.setPetEmotion(emotion);
  }

  function missingFinalReplySegments(segments) {
    return missingReplySegments(segments, streamedReplyTexts);
  }

  function showNextStreamedReplySegment(turnToken) {
    if (!chatPort.isTurnActive(turnToken) || streamedReplyTurnToken !== turnToken) return;
    const text = streamedReplySegments.shift();
    const presentationKey = streamedReplyUnitKeys.shift();
    if (!text) {
      streamedReplySegmentActive = false;
      return;
    }
    streamedReplySegmentActive = true;
    applySpeechUnitEmotion(presentationKey);
    lifecycle.clearTimeout(bubbleTimer);
    displayReplyBubbleText(text, { speaking: true });
    segmentTimer = lifecycle.setTimeout(() => {
      segmentTimer = 0;
      if (streamedReplySegments.length) {
        showNextStreamedReplySegment(turnToken);
        return;
      }
      streamedReplySegmentActive = false;
      scheduleBubbleReset(Math.max(text.length, 4), bubbleToken);
    }, getSegmentDisplayDelay(text));
  }

  function displayReplyBubbleText(text, { speaking = true } = {}) {
    bubbleKind = "reply";
    replyDisplayActive = true;
    setBubbleContent(text);
    els.bubble.classList.add("visible");
    windowPort.scheduleNativeHitTestSync({ force: true });
    if (speaking) setPetMotion("speaking");
    statusPort.updateActivityControls();
  }

  function getSegmentDisplayDelay(text) {
    return getBubbleSegmentDisplayDelay(text);
  }

  function showBubbleText(
    text,
    { transient = false, dismiss = false, durationMs = 1800, speaking = false, local = false, kind = "" } = {}
  ) {
    const nextKind = kind || (local ? "local" : "status");
    if (!local) interactionPort.clearLocalInteraction();
    lifecycle.clearTimeout(bubbleTimer);
    lifecycle.clearTimeout(segmentTimer);
    const token = ++bubbleToken;
    bubbleKind = text ? nextKind : "none";
    replyDisplayActive = text ? nextKind === "reply" : false;
    setBubbleContent(text || "");
    if (text) {
      els.bubble.classList.add("visible");
      if (speaking) setPetMotion("speaking");
    } else {
      els.bubble.classList.remove("visible");
    }
    windowPort.scheduleNativeHitTestSync({ force: true });
    statusPort.updateActivityControls();

    if (dismiss) {
      scheduleBubbleReset(Math.max(String(text || "").length, 4), token);
    } else if (transient) {
      bubbleTimer = lifecycle.setTimeout(() => {
        if (token !== bubbleToken) return;
        hideBubble(token);
      }, durationMs);
    }
  }

  function scheduleBubbleReset(charCount, token) {
    const ms = Math.max(3000, Math.min(15000, (charCount || 40) * 70));
    bubbleTimer = lifecycle.setTimeout(() => {
      if (token !== bubbleToken) return;
      hideBubble(token);
    }, ms);
  }

  function hideBubble(token = null) {
    if (token !== null && token !== bubbleToken) return;
    lifecycle.clearTimeout(bubbleTimer);
    lifecycle.clearTimeout(segmentTimer);
    if (token === null) bubbleToken += 1;
    if (bubbleKind !== "local") interactionPort.clearLocalInteraction();
    bubbleKind = "none";
    replyDisplayActive = false;
    els.bubble.classList.remove("visible");
    setBubbleContent("");
    windowPort.scheduleNativeHitTestSync({ force: true });
    restoreMotionAfterBubble();
    musicPort.scheduleMusicEmotionRestore();
    statusPort.updateActivityControls();
  }

  function setBubbleContent(text) {
    const value = String(text || "").trim();
    setReplyText(els.bubbleText, value, { highlight: bubbleKind === "reply" || bubbleKind === "local" });
    els.bubbleText.scrollTop = 0;
    els.bubble.dataset.size = getBubbleSizeForText(value);
  }

  function getBubbleSizeForText(text) {
    const value = String(text || "");
    if (!value) return "empty";
    const lineCount = value.split(/\r?\n/u).length;
    if (value.length <= 18 && lineCount <= 1) return "short";
    if (value.length >= 50 || lineCount >= 3) return "long";
    return "medium";
  }

  function setPetMotion(motion, { durationMs = 0 } = {}) {
    lifecycle.clearTimeout(motionTimer);
    const next = motion && motion !== "idle" ? motion : "idle";
    visualRenderer.setMotion(next, { restart: next === "click" });
    if (durationMs > 0) {
      motionTimer = lifecycle.setTimeout(() => {
        if (chatPort.sending) return;
        visualRenderer.setMotion(windowPort.physicsTimer ? "thrown" : "idle");
      }, durationMs);
    }
  }

  function restoreMotionAfterBubble() {
    if (windowPort.dragState) {
      visualRenderer.setMotion("dragging");
      return;
    }
    if (windowPort.physicsTimer) {
      visualRenderer.setMotion("thrown");
      return;
    }
    visualRenderer.setMotion(
      ttsPort.hasLocalTtsPlayback() || voicePort.realtimeVoiceTurn?.playbackActive || voicePort.hasRealtimeVoiceCallPlayback()
        ? "speaking"
        : "idle"
    );
  }

  function showFileDropHint() {
    if (windowPort.musicDropHover) return;
    windowPort.setMusicDropHover(true);
    statusPort.setRuntimeStatus(`把文件拖给 ${getProfileIdentityText("name", CHARACTER_NAME)}，会先放进手边工作台`, { mode: "idle" });
    if (!chatPort.sending && !replyDisplayActive) {
      showBubbleText("递给我就行。", { transient: true, durationMs: 1400, kind: "status" });
    }
  }

  function resetReplyIdentity() {
    lastTurnSignature = "";
    lastTurnTextKey = "";
  }

  function cancelReplyDisplay() {
    lifecycle.clearTimeout(bubbleTimer);
    lifecycle.clearTimeout(segmentTimer);
    bubbleToken += 1;
    bubbleKind = "none";
    replyDisplayActive = false;
    streamedReplyUnitEmotions.clear();
  }

  function clearQueuedReplySegments() { streamedReplySegments = []; streamedReplyUnitKeys = []; streamedReplyUnitEmotions.clear(); }

  function scheduleVoiceBubbleReset(text) {
    lifecycle.clearTimeout(bubbleTimer);
    const token = bubbleToken;
    const charCount = Math.max(String(text || "").length, 8);
    bubbleTimer = lifecycle.setTimeout(() => hideBubble(token), Math.max(6000, Math.min(30000, charCount * 120)));
  }

  return {
    resetReplyIdentity, cancelReplyDisplay, clearQueuedReplySegments, scheduleVoiceBubbleReset,
    get bubbleToken() { return bubbleToken; },
    get bubbleTimer() { return bubbleTimer; },
    get bubbleKind() { return bubbleKind; },
    get replyDisplayActive() { return replyDisplayActive; },
    get segmentTimer() { return segmentTimer; },
    get streamedReplyTurnToken() { return streamedReplyTurnToken; },
    get streamedReplySegments() { return streamedReplySegments; },
    get streamedReplyTexts() { return streamedReplyTexts; },
    get lastTurnSignature() { return lastTurnSignature; },
    get lastTurnTextKey() { return lastTurnTextKey; },
    restoreLatestReply,
    renderPayload,
    applyPayloadEmotion,
    normalizeSegments,
    buildClientCapabilities,
    buildSpeechTextKey,
    splitSpeechText,
    showThinking,
    showToolWorking,
    showError,
    shouldSyncReplyToTts,
    resetStreamedReplySegments,
    queueStreamedReplySegment,
    queueSpeechUnitText,
    markSpeechUnitPresented,
    missingFinalReplySegments,
    showNextStreamedReplySegment,
    displayReplyBubbleText,
    getSegmentDisplayDelay,
    showBubbleText,
    scheduleBubbleReset,
    hideBubble,
    setPetMotion,
    showFileDropHint
  };
}
