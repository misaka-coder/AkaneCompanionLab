/**
 * Custom HTML context menu replacing Electron's native dark popup.
 * Styled with the misty blue theme, grouped items with icons.
 */

class ContextMenu {
  constructor(root, { getSettings, onAction } = {}) {
    this._root = root;
    this._getSettings = getSettings;
    this._onAction = onAction;
    this._visible = false;

    this._onClickOutside = this._onClickOutside.bind(this);
    this._onItemClick = this._onItemClick.bind(this);
    this._onKeyDown = this._onKeyDown.bind(this);
  }

  show(x, y) {
    this._render(x, y);
    this._root.classList.add("visible");
    this._root.setAttribute("aria-hidden", "false");
    document.addEventListener("click", this._onClickOutside, true);
    document.addEventListener("keydown", this._onKeyDown);
  }

  showPinned() {
    this._render(0, 0);
    this._root.classList.add("visible");
    this._root.setAttribute("aria-hidden", "false");
    this._root.addEventListener("click", this._onItemClick);
    document.addEventListener("keydown", this._onKeyDown);
  }

  _render(x, y) {
    this._visible = true;

    const panelStyle = `left:${x}px;top:${y}px;`;
    const settings = this._getSettings?.() || {};
    const petScale = Number(settings.petScale || 1);
    const scaleButtons = [0.85, 1, 1.15, 1.3]
      .map((value) => {
        const active = Math.abs(petScale - value) < 0.001 ? " active" : "";
        return `
          <button class="context-menu__chip${active}" data-action="set-pet-scale" data-value="${value}">
            ${Math.round(value * 100)}%
          </button>
        `;
      })
      .join("");

    this._root.innerHTML = `
      <div class="context-menu-panel" style="${panelStyle}">
        <div class="context-menu__head">
          <span class="context-menu__head-icon">✦</span>
          <span>Akane</span>
        </div>

        <div class="context-menu__group">
          <button class="context-menu__item" data-action="toggle-window">
            <span class="context-menu__icon">◈</span>
            <span>显示 / 隐藏</span>
          </button>
          <button class="context-menu__item" data-action="reload-sprite">
            <span class="context-menu__icon">↻</span>
            <span>重载立绘</span>
          </button>
          <button class="context-menu__item" data-action="workspace-panel">
            <span class="context-menu__icon">⊞</span>
            <span>手边物品</span>
          </button>
          <button class="context-menu__item" data-action="debug-panel">
            <span class="context-menu__icon">◇</span>
            <span>状态预览器</span>
          </button>
          <button class="context-menu__item" data-action="settings-panel">
            <span class="context-menu__icon">⚙</span>
            <span>设置</span>
          </button>
        </div>

        <div class="context-menu__divider"></div>
        <div class="context-menu__group context-menu__group--compact">
          <div class="context-menu__label">大小</div>
          <div class="context-menu__chips">${scaleButtons}</div>
        </div>

        <div class="context-menu__divider"></div>
        <div class="context-menu__group">
          <button class="context-menu__item context-menu__item--danger" data-action="quit">
            <span class="context-menu__icon">✕</span>
            <span>退出</span>
          </button>
        </div>
      </div>
    `;
  }

  hide() {
    if (!this._visible) return;
    this._visible = false;
    this._root.classList.remove("visible");
    this._root.setAttribute("aria-hidden", "true");
    this._root.innerHTML = "";
    document.removeEventListener("click", this._onClickOutside, true);
    this._root.removeEventListener("click", this._onItemClick);
    document.removeEventListener("keydown", this._onKeyDown);
  }

  isVisible() {
    return this._visible;
  }

  _onItemClick(event) {
    const item = event.target.closest?.("[data-action]");
    if (!item) return;
    const action = item.dataset.action;
    const value = item.dataset.value;
    this._onAction?.(action, value);
  }

  _onClickOutside(event) {
    if (!this._root.contains(event.target)) {
      this.hide();
    }
    // Delegate click handling
    const item = event.target.closest?.("[data-action]");
    if (item) {
      const action = item.dataset.action;
      const value = item.dataset.value;
      event.preventDefault();
      event.stopPropagation();
      this._onAction?.(action, value);
      this.hide();
    }
  }

  _onKeyDown(event) {
    if (event.key === "Escape") {
      this.hide();
    }
  }

  _escape(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
}

export { ContextMenu };
