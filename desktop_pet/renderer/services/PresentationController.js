import { TtsPlayer } from "./TtsPlayer.js";

const MAX_CLIENT_SEGMENTS = 3;
const THINKING_TEXT = "……";
const PASSIVE_QUEUE_LIMIT = 6;
const RECENT_EVENT_TTL_MS = 1200;

const SOURCE_PRIORITY = {
  passive: 1,
  system: 2,
  formal: 3,
};

class PresentationController {
  constructor({
    speechBubble,
    stickerOverlay,
    spriteRenderer,
    voicePlayerEl,
    getSpriteContext,
    onPetStateChange,
    onEmotionChange,
    onIdle,
  }) {
    this._bubble = speechBubble;
    this._stickerOverlay = stickerOverlay;
    this._spriteRenderer = spriteRenderer;
    this._getSpriteContext = getSpriteContext;
    this._onPetStateChange = onPetStateChange;
    this._onEmotionChange = onEmotionChange;
    this._onIdle = onIdle;
    this._ttsPlayer = new TtsPlayer(voicePlayerEl, {
      getBackendUrl: () => this._getContext().backendUrl,
      onPlaybackStart: () => this._handleTtsStart(),
      onPlaybackEnd: () => this._handleTtsEnd(),
      onPlaybackError: (error) => this._handleTtsError(error),
    });
    this._activeSource = "idle";
    this._activePriority = 0;
    this._bubbleVisible = false;
    this._ttsActive = false;
    this._suppressBubbleHidden = false;
    this._formalBubbleSignature = "";
    this._formalBubbleContentPriority = 0;
    this._lastTtsSignature = "";
    this._recentKeys = new Map();
    this._passiveQueue = [];

    this._bubble.setOnHidden(() => {
      if (this._suppressBubbleHidden) return;
      this._bubbleVisible = false;
      if (!this._ttsActive) {
        this._finishActivePresentation();
      }
    });
  }

  dispatch(event) {
    const type = String(event?.type || "").trim();
    if (!type) return false;

    if (type === "show_bubble") return this._handleShowBubble(event);
    if (type === "show_sticker") return this._handleShowSticker(event);
    if (type === "change_emotion") return this._handleChangeEmotion(event);
    if (type === "play_tts") return this._handlePlayTts(event);
    if (type === "task_notice") return this._handleTaskNotice(event);
    if (type === "local_reaction") return this._handleLocalReaction(event);
    return false;
  }

  showThinking() {
    return this.dispatch({
      type: "show_bubble",
      source: "formal",
      text: THINKING_TEXT,
      dismiss: false,
      reset: true,
      stopTts: true,
      state: "thinking",
    });
  }

  hideBubble() {
    this._hideBubbleSilently();
    if (!this._ttsActive) {
      this._finishActivePresentation();
    }
  }

  interrupt({ reset = true, stopTts = true, hideBubble = true } = {}) {
    if (stopTts) {
      this._ttsPlayer.stop();
      this._lastTtsSignature = "";
    }
    if (hideBubble) {
      this._hideBubbleSilently();
    }
    if (reset) {
      this._resetFormalDedupe();
    }
    this._activeSource = "idle";
    this._activePriority = 0;
    if (!this._ttsActive && !this._bubbleVisible) {
      this._onPetStateChange?.("idle");
    }
  }

  setVoiceEnabled(enabled) {
    this._ttsPlayer.setEnabled(enabled);
  }

  isVoiceEnabled() {
    return this._ttsPlayer.isEnabled();
  }

  canAcceptPassive() {
    return !this.isFormalActive() && !this._bubble.isVisible() && !this._ttsActive;
  }

  isFormalActive() {
    return this._activeSource === "formal" && (this._bubbleVisible || this._ttsActive);
  }

  _handleShowBubble(event) {
    const content = this._buildBubbleContent(event);
    if (!content) return false;

    const source = this._normalizeSource(event.source);
    const priority = SOURCE_PRIORITY[source] || SOURCE_PRIORITY.passive;

    if (event.reset) {
      this._resetFormalDedupe();
    }

    if (source === "passive" && !this.canAcceptPassive()) {
      if (event.queue) {
        this._enqueuePassive({ ...event, type: "show_bubble", queue: false });
      }
      return false;
    }

    if (source !== "formal" && this.isFormalActive()) {
      return false;
    }

    const dedupeKey = String(event.key || (source === "formal" ? "" : content.signature)).trim();
    if (dedupeKey && !event.force && this._isRecentDuplicate(dedupeKey)) {
      return false;
    }

    if (source === "formal" && !event.force) {
      if (content.signature === this._formalBubbleSignature) return false;
      if (content.priority < this._formalBubbleContentPriority) return false;
    }

    if (event.interrupt !== false || source === "formal") {
      this._interruptVisuals({
        stopTts: event.stopTts !== false,
        resetDedupe: false,
      });
    }

    if (source === "formal") {
      this._formalBubbleSignature = content.signature;
      this._formalBubbleContentPriority = content.priority;
    }

    this._activeSource = source;
    this._activePriority = priority;
    this._bubbleVisible = true;
    this._onPetStateChange?.(event.state || "speaking");
    this._renderBubble(content, { dismiss: event.dismiss !== false });
    return true;
  }

  _handleTaskNotice(event) {
    const text = String(event.text || event.message || "").trim();
    if (!text) return false;
    return this._handleShowBubble({
      type: "show_bubble",
      source: "passive",
      text,
      queue: true,
      key: event.key || `task_notice:${text}`,
    });
  }

  _handleLocalReaction(event) {
    const text = String(event.text || event.message || "").trim();
    if (!text || !this.canAcceptPassive()) return false;
    return this._handleShowBubble({
      type: "show_bubble",
      source: "passive",
      text,
      queue: false,
      key: event.key || `local_reaction:${text}`,
      state: "idle",
    });
  }

  _handlePlayTts(event) {
    const source = this._normalizeSource(event.source || "formal");
    if (source !== "formal" && !event.allowPassive) return false;

    const content = this._buildBubbleContent(event);
    if (!content) return false;
    if (!event.force && content.signature === this._lastTtsSignature) return false;

    this._lastTtsSignature = content.signature;
    if (content.kind === "segments") {
      this._ttsPlayer.speakSegments(content.segments);
    } else {
      this._ttsPlayer.speak(content.text);
    }
    return true;
  }

  _handleChangeEmotion(event) {
    const emotion = String(event.emotion || event.payload?.emotion || "").trim();
    if (!emotion) return false;

    const { outfit, backendUrl } = this._getContext();
    try {
      if (event.reload) {
        void this._spriteRenderer.reloadEmotion(emotion, outfit, backendUrl);
      } else {
        void this._spriteRenderer.showEmotion(emotion, outfit, backendUrl);
      }
      const resolution = this._spriteRenderer.getLastResolution?.();
      this._onEmotionChange?.(resolution?.id || emotion);
    } catch (error) {
      console.warn("[AkanePet] Emotion change failed:", error);
      this._onEmotionChange?.(emotion);
    }
    return true;
  }

  _handleShowSticker(event) {
    const sticker = event.sticker && typeof event.sticker === "object" ? event.sticker : {};
    const url = this._resolveStickerUrl(event.url || sticker.url || sticker.public_path || sticker.path || "");
    const label = event.label || sticker.display_name || sticker.label || "";
    if (!url) return false;

    const key = event.key || `sticker:${url}:${label}`;
    if (!event.force && this._isRecentDuplicate(key)) return false;

    return this._stickerOverlay.show({
      url,
      label,
      durationMs: event.durationMs || sticker.durationMs,
    });
  }

  _buildBubbleContent(event) {
    if (event.content && typeof event.content === "object") {
      return event.content;
    }

    const payload = event.payload && typeof event.payload === "object" ? event.payload : event;
    const segments = this._normalizeSegments(payload.speech_segments || payload.segments);
    if (segments.length > 0) {
      return {
        kind: "segments",
        source: "speech_segments",
        segments,
        signature: `segments:${segments.join("\u241e")}`,
        priority: 2,
      };
    }

    const speech = String(payload.speech || payload.text || "").trim();
    if (!speech) return null;

    const split = this._splitLongSegment(speech);
    if (split.length > 1) {
      return {
        kind: "segments",
        source: "speech",
        segments: split,
        signature: `segments:${split.join("\u241e")}`,
        priority: 1,
      };
    }

    return {
      kind: "text",
      source: "speech",
      text: speech,
      charCount: this._countChars(speech),
      signature: `text:${speech}`,
      priority: 1,
    };
  }

  _renderBubble(content, { dismiss }) {
    this._bubble.clear();
    if (content.kind === "segments") {
      void this._bubble.showSegments(content.segments);
      return;
    }

    this._bubble.setText(content.text);
    this._bubble.show();
    if (dismiss) {
      this._bubble.dismissAfter(content.charCount);
    }
  }

  _interruptVisuals({ stopTts, resetDedupe }) {
    if (stopTts) {
      this._ttsPlayer.stop();
      this._lastTtsSignature = "";
    }
    this._hideBubbleSilently();
    if (resetDedupe) {
      this._resetFormalDedupe();
    }
  }

  _hideBubbleSilently() {
    this._suppressBubbleHidden = true;
    try {
      this._bubble.hide();
    } finally {
      this._suppressBubbleHidden = false;
      this._bubbleVisible = false;
    }
  }

  _handleTtsStart() {
    this._ttsActive = true;
    this._onPetStateChange?.("speaking");
  }

  _handleTtsEnd() {
    this._ttsActive = false;
    if (!this._bubble.isVisible()) {
      this._finishActivePresentation();
    }
  }

  _handleTtsError(_error) {
    // TtsPlayer already logs a warning; the presentation queue should keep moving.
  }

  _finishActivePresentation() {
    this._activeSource = "idle";
    this._activePriority = 0;
    this._bubbleVisible = false;
    this._onPetStateChange?.("idle");
    this._onIdle?.();
    this._flushPassiveQueue();
  }

  _enqueuePassive(event) {
    const key = String(event.key || event.text || "").trim();
    if (key && this._passiveQueue.some((item) => item.key === key)) return;
    this._passiveQueue.push({ ...event, key });
    if (this._passiveQueue.length > PASSIVE_QUEUE_LIMIT) {
      this._passiveQueue.shift();
    }
  }

  _flushPassiveQueue() {
    if (!this.canAcceptPassive()) return;
    const event = this._passiveQueue.shift();
    if (!event) return;
    window.setTimeout(() => {
      if (this.canAcceptPassive()) {
        this._handleShowBubble(event);
      } else {
        this._enqueuePassive(event);
      }
    }, 0);
  }

  _resolveStickerUrl(url) {
    const raw = String(url || "").trim();
    if (!raw) return "";
    if (/^(https?:|file:|data:|blob:)/i.test(raw)) return raw;
    const { backendUrl } = this._getContext();
    if (!backendUrl) return raw;
    if (raw.startsWith("/")) return `${backendUrl}${raw}`;
    return `${backendUrl}/${raw.replace(/^\/+/, "")}`;
  }

  _getContext() {
    const context = this._getSpriteContext?.() || {};
    return {
      outfit: String(context.outfit || "").trim(),
      backendUrl: String(context.backendUrl || "").trim().replace(/\/+$/, ""),
    };
  }

  _resetFormalDedupe() {
    this._formalBubbleSignature = "";
    this._formalBubbleContentPriority = 0;
    this._lastTtsSignature = "";
  }

  _normalizeSource(source) {
    const value = String(source || "").trim().toLowerCase();
    if (value === "formal" || value === "system" || value === "passive") return value;
    return "passive";
  }

  _normalizeSegments(speechSegments) {
    if (!Array.isArray(speechSegments)) return [];
    return speechSegments.map((segment) => String(segment || "").trim()).filter(Boolean);
  }

  _splitLongSegment(text) {
    if (!text) return [];

    const lines = text.split(/\n+/).map((item) => item.trim()).filter(Boolean);
    if (lines.length >= 2) {
      if (lines.length <= MAX_CLIENT_SEGMENTS) return lines;
      return this._groupInto(lines, MAX_CLIENT_SEGMENTS);
    }

    const sentences = text.split(/(?<=[。！？!?～…])\s*/).map((item) => item.trim()).filter(Boolean);
    if (sentences.length >= 2) {
      if (sentences.length <= MAX_CLIENT_SEGMENTS) return sentences;
      return this._groupInto(sentences, MAX_CLIENT_SEGMENTS);
    }

    return [text];
  }

  _groupInto(items, count) {
    const size = Math.ceil(items.length / count);
    const result = [];
    for (let i = 0; i < count; i += 1) {
      const chunk = items.slice(i * size, (i + 1) * size).join("\n");
      if (chunk) result.push(chunk);
    }
    return result;
  }

  _countChars(text) {
    return String(text || "").length;
  }

  _isRecentDuplicate(key) {
    const normalized = String(key || "").trim();
    if (!normalized) return false;

    const now = Date.now();
    for (const [itemKey, timestamp] of this._recentKeys.entries()) {
      if (now - timestamp > RECENT_EVENT_TTL_MS) {
        this._recentKeys.delete(itemKey);
      }
    }

    if (this._recentKeys.has(normalized)) return true;
    this._recentKeys.set(normalized, now);
    return false;
  }
}

export { PresentationController };
