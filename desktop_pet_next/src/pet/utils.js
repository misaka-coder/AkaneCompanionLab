import { DEFAULT_BACKEND_URL } from './constants.js';
import { getProfileDefaultOutfit } from './profile.js';

export function normalizeBackendUrl(url) {
  return String(url || "").trim().replace(/\/+$/, "") || DEFAULT_BACKEND_URL;
}

export function normalizeOutfitName(value) {
  return String(value || "").trim() || getProfileDefaultOutfit();
}

export function generateSessionId() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return `desktop_pet_next_${crypto.randomUUID()}`;
  }
  return `desktop_pet_next_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}

export function clamp(value, min, max) {
  return Math.min(max, Math.max(min, Number.isFinite(value) ? value : min));
}

export function readCssPx(name, fallback) {
  const value = Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue(name));
  return Number.isFinite(value) ? value : fallback;
}

export function shortId(value) {
  const text = String(value || "");
  return text.length > 10 ? text.slice(-10) : text;
}

export function formatError(error) {
  if (error?.name === "AbortError") return "请求超时";
  return error instanceof Error ? error.message : String(error);
}

export function friendlyErrorMessage(message) {
  const text = String(message || "").trim();
  if (!text) return "请求失败，稍后再试。";
  if (/麦克风|microphone|notallowed|securityerror|permission/i.test(text)) return "没有麦克风权限。";
  if (/notfound|devicesnotfound|no device/i.test(text)) return "没有找到可用麦克风。";
  if (/ASR|语音识别|录音/i.test(text)) return "语音识别暂时失败，可以再试一次。";
  if (/TTS|语音播放/i.test(text)) return "语音播放暂时失败，文字回复还在。";
  if (/workspace|手边|summary/i.test(text)) return "手边物品暂时打不开，请确认后端已经启动。";
  if (/timed out|timeout|请求超时|响应超时/i.test(text)) return "等待回复超时，可以稍后再试。";
  if (/后端未连接|failed to fetch|connection|network|fetch|dns|refused|timed out|timeout|请求超时/i.test(text)) {
    return "后端暂时连不上，请确认服务已经启动。";
  }
  if (/HTTP 5\d\d/i.test(text)) return "后端处理时出错了，稍后再试。";
  if (/HTTP 4\d\d/i.test(text)) return "请求没有被后端接受。";
  return text.length > 44 ? `${text.slice(0, 44)}…` : text;
}
