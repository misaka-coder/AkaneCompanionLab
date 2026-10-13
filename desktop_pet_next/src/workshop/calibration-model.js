import { BUBBLE_STYLE_LABELS, DEFAULT_BUBBLE_STYLE } from "./constants.js";
import { clampNumber } from "./shared.js";

export function normalizeBubbleStyle(value) {
  const style = String(value || "").trim().toLowerCase();
  return Object.prototype.hasOwnProperty.call(BUBBLE_STYLE_LABELS, style) ? style : DEFAULT_BUBBLE_STYLE;
}

export function readSliderUnit(input, fallback) {
  const raw = Number(input?.value);
  const percent = Number.isFinite(raw) ? raw : fallback * 100;
  return clampNumber(percent / 100, 0, 1);
}

export function readUnitValue(value, fallback) {
  const next = Number(value);
  if (!Number.isFinite(next)) return fallback;
  return clampNumber(next, 0, 1);
}
