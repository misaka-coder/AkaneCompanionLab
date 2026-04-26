const TTS_TIMEOUT_MS = 45000;

class TtsPlayer {
  /**
   * @param {HTMLAudioElement} audioEl
   * @param {{
   *   getBackendUrl: () => string,
   *   enabled?: boolean,
   *   onPlaybackStart?: () => void,
   *   onPlaybackEnd?: () => void,
   *   onPlaybackError?: (error: unknown) => void
   * }} options
   */
  constructor(audioEl, { getBackendUrl, enabled = false, onPlaybackStart, onPlaybackEnd, onPlaybackError } = {}) {
    this._audioEl = audioEl;
    this._getBackendUrl = getBackendUrl;
    this._enabled = Boolean(enabled);
    this._onPlaybackStart = onPlaybackStart;
    this._onPlaybackEnd = onPlaybackEnd;
    this._onPlaybackError = onPlaybackError;
    this._queue = [];
    this._queueToken = 0;
    this._pumpActive = false;
    this._playbackActive = false;
    this._currentController = null;
    this._currentUrl = "";
    this._resolvePlaybackWait = null;
  }

  setEnabled(enabled) {
    this._enabled = Boolean(enabled);
    if (!this._enabled) {
      this.stop();
    }
  }

  isEnabled() {
    return this._enabled;
  }

  speak(text) {
    const normalized = this._normalizeText(text);
    if (!normalized || !this._enabled) return;

    this.stop();
    this._queue.push(normalized);
    void this._pumpQueue();
  }

  speakSegments(segments) {
    const items = Array.isArray(segments)
      ? segments.map((item) => this._normalizeText(item)).filter(Boolean)
      : [];
    if (!items.length || !this._enabled) return;

    this.stop();
    this._queue.push(...items);
    void this._pumpQueue();
  }

  stop() {
    this._queueToken += 1;
    this._queue = [];
    this._abortCurrentRequest();
    this._stopCurrentAudio();
    this._finishPlaybackWait(false);
    this._notifyPlaybackEnd();
  }

  clearQueue() {
    this._queue = [];
  }

  async _pumpQueue() {
    if (this._pumpActive || !this._enabled) return;
    this._pumpActive = true;
    const token = this._queueToken;
    this._notifyPlaybackStart();

    try {
      while (this._enabled && token === this._queueToken && this._queue.length > 0) {
        const text = this._queue.shift();
        if (!text) continue;
        await this._playText(text, token);
      }
    } finally {
      if (token === this._queueToken) {
        this._notifyPlaybackEnd();
      }
      this._pumpActive = false;
      if (this._enabled && this._queue.length > 0) {
        void this._pumpQueue();
      }
    }
  }

  async _playText(text, token) {
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), TTS_TIMEOUT_MS);
    this._currentController = controller;

    try {
      const response = await fetch(this._buildTtsUrl(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        signal: controller.signal,
        body: JSON.stringify({ text }),
      });
      if (!response.ok) {
        throw new Error(`TTS request failed: HTTP ${response.status}`);
      }

      const blob = await response.blob();
      if (controller.signal.aborted || token !== this._queueToken) return;

      this._currentUrl = URL.createObjectURL(blob);
      this._audioEl.src = this._currentUrl;
      this._audioEl.currentTime = 0;

      await this._audioEl.play();
      if (controller.signal.aborted || token !== this._queueToken) return;
      await this._waitForAudioEnd(token);
    } catch (error) {
      if (!controller.signal.aborted && token === this._queueToken) {
        this._warn(error);
      }
    } finally {
      window.clearTimeout(timeoutId);
      if (this._currentController === controller) {
        this._currentController = null;
      }
      this._cleanupAudioUrl();
    }
  }

  _buildTtsUrl() {
    const baseUrl = String(this._getBackendUrl?.() || "").trim().replace(/\/+$/, "");
    if (!baseUrl) {
      throw new Error("TTS backend URL is empty");
    }

    const url = new URL("/tts", `${baseUrl}/`);
    url.searchParams.set("t", String(Date.now()));
    return url.toString();
  }

  _waitForAudioEnd(token) {
    return new Promise((resolve) => {
      const cleanup = () => {
        this._audioEl.removeEventListener("ended", handleEnded);
        this._audioEl.removeEventListener("error", handleError);
        this._resolvePlaybackWait = null;
      };
      const finish = (completed) => {
        cleanup();
        resolve(completed && token === this._queueToken);
      };
      const handleEnded = () => finish(true);
      const handleError = () => finish(false);

      this._resolvePlaybackWait = finish;
      this._audioEl.addEventListener("ended", handleEnded, { once: true });
      this._audioEl.addEventListener("error", handleError, { once: true });
    });
  }

  _finishPlaybackWait(completed) {
    if (!this._resolvePlaybackWait) return;
    const resolve = this._resolvePlaybackWait;
    this._resolvePlaybackWait = null;
    resolve(completed);
  }

  _abortCurrentRequest() {
    if (!this._currentController) return;
    this._currentController.abort();
    this._currentController = null;
  }

  _stopCurrentAudio() {
    this._audioEl.pause();
    this._audioEl.removeAttribute("src");
    this._audioEl.load();
    this._cleanupAudioUrl();
  }

  _cleanupAudioUrl() {
    if (this._currentUrl) {
      URL.revokeObjectURL(this._currentUrl);
      this._currentUrl = "";
    }
  }

  _notifyPlaybackStart() {
    if (this._playbackActive) return;
    this._playbackActive = true;
    this._onPlaybackStart?.();
  }

  _notifyPlaybackEnd() {
    if (!this._playbackActive) return;
    this._playbackActive = false;
    this._onPlaybackEnd?.();
  }

  _warn(error) {
    console.warn("[AkanePet] TTS playback failed:", error);
    this._onPlaybackError?.(error);
  }

  _normalizeText(text) {
    return String(text || "").replace(/\s+/g, " ").trim();
  }
}

export { TtsPlayer };
