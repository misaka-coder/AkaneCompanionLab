const DEFAULT_DURATION_MS = 3200;
const MIN_DURATION_MS = 2500;
const MAX_DURATION_MS = 4000;

class StickerOverlay {
  constructor(element) {
    this._el = element;
    this._timer = 0;
    this._token = 0;
    this._img = document.createElement("img");
    this._img.alt = "";
    this._img.draggable = false;
    this._label = document.createElement("div");
    this._label.className = "sticker-label";
    this._el.appendChild(this._img);
    this._el.appendChild(this._label);
  }

  show({ url, label = "", durationMs = DEFAULT_DURATION_MS } = {}) {
    const nextUrl = String(url || "").trim();
    if (!nextUrl) return false;

    const token = this._beginShow();
    const safeLabel = String(label || "").trim();
    const ms = Math.max(MIN_DURATION_MS, Math.min(MAX_DURATION_MS, Number(durationMs) || DEFAULT_DURATION_MS));

    this._img.onload = () => {
      if (token !== this._token) return;
      this._img.alt = safeLabel;
      this._label.textContent = safeLabel;
      this._label.hidden = !safeLabel;
      this._el.classList.add("visible");
      this._timer = window.setTimeout(() => this.hide(token), ms);
    };

    this._img.onerror = () => {
      if (token !== this._token) return;
      console.warn("[AkanePet] Sticker failed to load:", nextUrl);
      this.hide(token);
    };

    this._img.src = nextUrl;
    return true;
  }

  hide(token = this._token) {
    if (token !== this._token) return;
    window.clearTimeout(this._timer);
    this._timer = 0;
    this._el.classList.remove("visible");
  }

  destroy() {
    this._beginShow();
    this._el.classList.remove("visible");
    this._img.removeAttribute("src");
    this._label.textContent = "";
  }

  _beginShow() {
    window.clearTimeout(this._timer);
    this._timer = 0;
    this._token += 1;
    this._el.classList.remove("visible");
    this._img.onload = null;
    this._img.onerror = null;
    return this._token;
  }
}

export { StickerOverlay };
