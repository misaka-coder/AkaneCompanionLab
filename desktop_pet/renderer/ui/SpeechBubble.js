/**
 * Speech bubble overlay positioned above the sprite.
 * Supports speech_segments (one-at-a-time display with char-based timing).
 */

const SEGMENT_MIN_MS = 1200;
const SEGMENT_MAX_MS = 4500;
const SEGMENT_CHAR_RATE = 120;

class SpeechBubble {
  constructor(element) {
    this._el = element;
    this._dismissTimer = 0;
    this._segmentTimer = 0;
    this._waitResolver = null;
    this._playbackToken = 0;
    this._onHidden = null;
  }

  show() {
    window.clearTimeout(this._dismissTimer);
    this._dismissTimer = 0;
    this._el.classList.add("visible");
  }

  hide() {
    const wasVisible = this.isVisible();
    this._cancelPlayback();
    this._el.classList.remove("visible");
    if (wasVisible && this._onHidden) {
      this._onHidden();
    }
  }

  toggle() {
    if (this._el.classList.contains("visible")) {
      this.hide();
    } else {
      this.show();
    }
  }

  isVisible() {
    return this._el.classList.contains("visible");
  }

  setOnHidden(callback) {
    this._onHidden = typeof callback === "function" ? callback : null;
  }

  getText() {
    return this._el.textContent || "";
  }

  setText(text) {
    this._cancelPlayback();
    this._setContent(text);
  }

  appendText(text) {
    if (!text) return;
    this._cancelPlayback();
    this._setContent(`${this.getText()}${text}`);
  }

  clear() {
    this._cancelPlayback();
    this._setContent("");
  }

  /**
   * Display segments one at a time, replacing the previous.
   * Returns total char count for dismiss timing, or 0 if interrupted.
   * @param {string[]} segments
   * @returns {Promise<number>}
   */
  async showSegments(segments) {
    const items = Array.isArray(segments) ? segments.filter(Boolean) : [];
    if (!items.length) {
      this.clear();
      return 0;
    }

    const token = this._beginPlayback();
    this.show();

    for (let i = 0; i < items.length; i++) {
      if (!this._isActive(token)) return 0;

      this._setContent(items[i]);

      if (i < items.length - 1) {
        const ms = Math.max(SEGMENT_MIN_MS, Math.min(SEGMENT_MAX_MS, items[i].length * SEGMENT_CHAR_RATE));
        const completed = await this._wait(ms, token);
        if (!completed) return 0;
      }
    }

    if (!this._isActive(token)) return 0;

    // Auto-dismiss after last segment
    const lastChars = items[items.length - 1].length;
    this.dismissAfter(Math.max(lastChars, 4));
    return items.reduce((total, item) => total + item.length, 0);
  }

  dismissAfter(charCount) {
    window.clearTimeout(this._dismissTimer);
    this._dismissTimer = 0;
    const ms = Math.max(3000, Math.min(15000, (charCount || 40) * 70));
    const token = this._playbackToken;
    this._dismissTimer = window.setTimeout(() => {
      if (!this._isActive(token)) return;
      this.hide();
    }, ms);
  }

  destroy() {
    this.hide();
    this._setContent("");
  }

  _beginPlayback() {
    this._cancelPlayback();
    this._playbackToken += 1;
    return this._playbackToken;
  }

  _cancelPlayback() {
    window.clearTimeout(this._dismissTimer);
    window.clearTimeout(this._segmentTimer);
    this._dismissTimer = 0;
    this._segmentTimer = 0;

    if (this._waitResolver) {
      const resolve = this._waitResolver;
      this._waitResolver = null;
      resolve(false);
    }

    this._playbackToken += 1;
  }

  _isActive(token) {
    return token === this._playbackToken;
  }

  _setContent(text) {
    this._el.textContent = text || "";
    this._el.scrollTop = 0;
  }

  _wait(ms, token) {
    return new Promise((resolve) => {
      this._waitResolver = resolve;
      this._segmentTimer = window.setTimeout(() => {
        this._segmentTimer = 0;
        if (this._waitResolver === resolve) {
          this._waitResolver = null;
        }
        resolve(this._isActive(token));
      }, ms);
    });
  }
}

export { SpeechBubble };
