/**
 * Distinguishes single-click, double-click, and drag on an element.
 * Calls onDragMove(dx, dy) in pixels for window repositioning.
 */

const DRAG_THRESHOLD = 5;     // px moved before it's a drag
const DBL_CLICK_WINDOW = 400; // ms between clicks for double-click

class DragHandler {
  /**
   * @param {HTMLElement} element
   * @param {{
 *   onClick?: () => void,
 *   onDoubleClick?: () => void,
 *   canStart?: (event: MouseEvent) => boolean,
 *   onDragStart?: () => void,
 *   onDragMove?: (dx: number, dy: number) => void,
 *   onDragEnd?: () => void,
 * }} callbacks
 */
  constructor(element, { onClick, onDoubleClick, canStart, onDragStart, onDragMove, onDragEnd } = {}) {
    this._el = element;
    this._onClick = onClick;
    this._onDoubleClick = onDoubleClick;
    this._canStart = canStart;
    this._onDragStart = onDragStart;
    this._onDragMove = onDragMove;
    this._onDragEnd = onDragEnd;

    this._dragging = false;
    this._moved = 0;
    this._startX = 0;
    this._startY = 0;
    this._lastClickTime = 0;
    this._clickPending = 0;

    this._onMouseDown = this._onMouseDown.bind(this);
    this._onMouseMove = this._onMouseMove.bind(this);
    this._onMouseUp = this._onMouseUp.bind(this);

    element.addEventListener("mousedown", this._onMouseDown);
  }

  _onMouseDown(e) {
    if (e.button !== 0) return;
    if (this._canStart && !this._canStart(e)) return;
    e.preventDefault();
    this._startX = e.screenX;
    this._startY = e.screenY;
    this._moved = 0;
    this._dragging = false;
    document.addEventListener("mousemove", this._onMouseMove);
    document.addEventListener("mouseup", this._onMouseUp);
  }

  _onMouseMove(e) {
    const dx = e.screenX - this._startX;
    const dy = e.screenY - this._startY;
    this._moved = Math.abs(dx) + Math.abs(dy);

    if (!this._dragging && this._moved > DRAG_THRESHOLD) {
      this._dragging = true;
      window.clearTimeout(this._clickPending);
      if (this._onDragStart) this._onDragStart();
    }

    if (this._dragging && this._onDragMove) {
      this._onDragMove(dx, dy);
      this._startX = e.screenX;
      this._startY = e.screenY;
    }
  }

  _onMouseUp() {
    document.removeEventListener("mousemove", this._onMouseMove);
    document.removeEventListener("mouseup", this._onMouseUp);

    if (this._dragging) {
      if (this._onDragEnd) this._onDragEnd();
      return;
    }

    // It's a click — check for double-click
    window.clearTimeout(this._clickPending);
    const now = Date.now();
    if (now - this._lastClickTime < DBL_CLICK_WINDOW) {
      this._lastClickTime = 0;
      if (this._onDoubleClick) this._onDoubleClick();
    } else {
      this._lastClickTime = now;
      this._clickPending = window.setTimeout(() => {
        this._lastClickTime = 0;
        if (this._onClick) this._onClick();
      }, DBL_CLICK_WINDOW);
    }
  }

  destroy() {
    window.clearTimeout(this._clickPending);
    this._el.removeEventListener("mousedown", this._onMouseDown);
    document.removeEventListener("mousemove", this._onMouseMove);
    document.removeEventListener("mouseup", this._onMouseUp);
  }
}

export { DragHandler };
