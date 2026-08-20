import { getInstanceStorageItem, setInstanceStorageItem } from "../instance-storage.js";

const THEME_STORAGE_KEY = "controlCenterV2.presentation.theme";
const FRAME_STORAGE_PREFIX = "controlCenterV2.presentation.frame";
const THEME_MODES = new Set(["system", "dark", "light"]);
const FRAME_TARGETS = new Set(["avatar", "portrait", "background"]);

export const DEFAULT_PRESENTATION_PREFERENCES = Object.freeze({
  themeMode: "system",
  frames: Object.freeze({
    avatar: Object.freeze({ x: 50, y: 50, scale: 1 }),
    portrait: Object.freeze({ x: 50, y: 100, scale: 1 }),
    background: Object.freeze({ x: 50, y: 50, scale: 1 })
  })
});

export function loadPresentationPreferences(packId, options = {}) {
  const themeMode = normalizeThemeMode(getInstanceStorageItem(THEME_STORAGE_KEY, options));
  const rawFrame = getInstanceStorageItem(frameStorageKey(packId), options);
  let frameValue = null;
  try {
    frameValue = rawFrame ? JSON.parse(rawFrame) : null;
  } catch {
    frameValue = null;
  }
  return normalizePresentationPreferences({ themeMode, frames: frameValue });
}

export function savePresentationPreferences(packId, preferences, options = {}) {
  const normalized = normalizePresentationPreferences(preferences);
  const themeSaved = setInstanceStorageItem(THEME_STORAGE_KEY, normalized.themeMode, options);
  const frameSaved = setInstanceStorageItem(frameStorageKey(packId), JSON.stringify(normalized.frames), options);
  return themeSaved && frameSaved;
}

export function normalizePresentationPreferences(value = {}) {
  const source = value && typeof value === "object" ? value : {};
  const sourceFrames = source.frames && typeof source.frames === "object" ? source.frames : {};
  return {
    themeMode: normalizeThemeMode(source.themeMode),
    frames: {
      avatar: normalizeFrame(sourceFrames.avatar, DEFAULT_PRESENTATION_PREFERENCES.frames.avatar),
      portrait: normalizeFrame(sourceFrames.portrait, DEFAULT_PRESENTATION_PREFERENCES.frames.portrait),
      background: normalizeFrame(sourceFrames.background, DEFAULT_PRESENTATION_PREFERENCES.frames.background)
    }
  };
}

export function updatePresentationFrame(preferences, target, patch = {}) {
  const normalized = normalizePresentationPreferences(preferences);
  const frameTarget = normalizeFrameTarget(target);
  if (!frameTarget) return normalized;
  return normalizePresentationPreferences({
    ...normalized,
    frames: {
      ...normalized.frames,
      [frameTarget]: { ...normalized.frames[frameTarget], ...patch }
    }
  });
}

export function resetPresentationFrame(preferences, target) {
  const frameTarget = normalizeFrameTarget(target);
  if (!frameTarget) return normalizePresentationPreferences(preferences);
  return updatePresentationFrame(preferences, frameTarget, DEFAULT_PRESENTATION_PREFERENCES.frames[frameTarget]);
}

export function resolveThemeMode(themeMode, prefersLight = false) {
  const normalized = normalizeThemeMode(themeMode);
  return normalized === "system" ? (prefersLight ? "light" : "dark") : normalized;
}

export function presentationCssVariables(preferences) {
  const normalized = normalizePresentationPreferences(preferences);
  const avatar = normalized.frames.avatar;
  const portrait = normalized.frames.portrait;
  const background = normalized.frames.background;
  return {
    "--cc-avatar-x": `${avatar.x}%`,
    "--cc-avatar-y": `${avatar.y}%`,
    "--cc-avatar-size": `${Math.round(avatar.scale * 1000) / 10}%`,
    "--cc-portrait-shift-x": `${Math.round((portrait.x - 50) * 500) / 1000}%`,
    "--cc-portrait-shift-y": `${Math.round((portrait.y - 100) * 450) / 1000}%`,
    "--cc-portrait-scale": String(portrait.scale),
    "--cc-background-x": `${background.x}%`,
    "--cc-background-y": `${background.y}%`,
    "--cc-background-scale": String(background.scale)
  };
}

function normalizeThemeMode(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return THEME_MODES.has(normalized) ? normalized : DEFAULT_PRESENTATION_PREFERENCES.themeMode;
}

function normalizeFrameTarget(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return FRAME_TARGETS.has(normalized) ? normalized : "";
}

function normalizeFrame(value, fallback) {
  const source = value && typeof value === "object" ? value : {};
  return {
    x: clampNumber(source.x, 0, 100, fallback.x),
    y: clampNumber(source.y, 0, 100, fallback.y),
    scale: clampNumber(source.scale, 0.6, 2, fallback.scale)
  };
}

function clampNumber(value, min, max, fallback) {
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  return Math.min(max, Math.max(min, Math.round(number * 1000) / 1000));
}

function frameStorageKey(packId) {
  const normalizedPackId = String(packId || "default").trim() || "default";
  return `${FRAME_STORAGE_PREFIX}.${encodeURIComponent(normalizedPackId)}`;
}
