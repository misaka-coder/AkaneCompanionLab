/**
 * Chat input overlay. Shown on double-click, hidden on Escape or blur.
 */
class ChatInput {
  /**
   * @param {HTMLElement} container - the #chat-input-container element
   * @param {HTMLElement} input - the #chat-input textarea
   * @param {{ onSend: (text: string) => void }} callbacks
   */
  constructor(container, input, { onSend }) {
    this._container = container;
    this._input = input;
    this._onSend = onSend;
    this._visible = false;

    this._input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        this._submit();
      }
      if (e.key === "Escape") {
        this.hide();
      }
    });

    this._input.addEventListener("blur", () => {
      // Delay hide to allow button clicks outside
      window.setTimeout(() => {
        if (!this._input.value.trim()) {
          this.hide();
        }
      }, 200);
    });

    this._input.addEventListener("input", () => {
      this._autoResize();
    });
  }

  show() {
    this._container.classList.add("visible");
    this._visible = true;
    this._autoResize();
    this._input.focus();
    this._input.setSelectionRange(this._input.value.length, this._input.value.length);
  }

  hide() {
    this._container.classList.remove("visible");
    this._visible = false;
    this._input.blur();
  }

  toggle() {
    if (this._visible) {
      this.hide();
    } else {
      this.show();
    }
  }

  isVisible() {
    return this._visible;
  }

  setText(text, { append = false } = {}) {
    const value = String(text || "").trim();
    if (!value) return;
    if (append && this._input.value.trim()) {
      this._input.value = `${this._input.value.trim()} ${value}`;
    } else {
      this._input.value = value;
    }
    this.show();
    this._autoResize();
  }

  _submit() {
    const text = this._input.value.trim();
    if (!text) return;
    this._input.value = "";
    this._autoResize();
    this.hide();
    if (this._onSend) {
      this._onSend(text);
    }
  }

  _autoResize() {
    const minHeight = this._readCssPx("--chat-input-min-height", 42);
    const maxHeight = this._readCssPx("--chat-input-max-height", 96);
    this._input.style.height = `${minHeight}px`;
    const nextHeight = Math.min(this._input.scrollHeight, maxHeight);
    this._input.style.height = `${Math.max(minHeight, nextHeight)}px`;
  }

  refreshLayout() {
    this._autoResize();
  }

  _readCssPx(name, fallback) {
    const raw = getComputedStyle(document.documentElement).getPropertyValue(name);
    const value = Number.parseFloat(raw);
    return Number.isFinite(value) ? value : fallback;
  }

  destroy() {
    this.hide();
  }
}

export { ChatInput };
