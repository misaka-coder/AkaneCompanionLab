import { DEFAULT_BACKEND_URL } from "./constants.js";

export function formatError(error) {
  return error instanceof Error ? error.message : String(error || "unknown error");
}

export function stableSignature(value) {
  try {
    return JSON.stringify(value);
  } catch {
    return String(Date.now());
  }
}

export function formatFileSize(bytes) {
  if (!bytes || bytes < 1024) return `${bytes || 0} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function sanitizeScopePart(value) {
  const raw = String(value || "").trim().toLowerCase();
  const encoded = encodeURIComponent(raw).replace(/%/g, "_").replace(/[^a-z0-9_-]+/g, "_");
  const text = encoded || raw.replace(/[^a-z0-9_-]+/g, "_");
  return text.replace(/^_+|_+$/g, "").slice(0, 64) || "character";
}

export function asArray(value) {
  return Array.isArray(value) ? value : [];
}

export function summarizeList(value) {
  const items = asArray(value).map((item) => String(item || "").trim()).filter(Boolean);
  if (!items.length) return "-";
  return items.slice(0, 4).join("、") + (items.length > 4 ? ` 等 ${items.length} 项` : "");
}

export function summarizeText(value) {
  const text = String(value || "").trim();
  if (!text) return "-";
  return text.length <= 34 ? text : `${text.slice(0, 34)}...`;
}

export function asKeywordsInput(value) {
  if (Array.isArray(value)) return value.join("、");
  return String(value ?? "");
}

export function splitKeywords(value) {
  return String(value ?? "")
    .split(/[,，、\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export function asLinesInput(value) {
  if (Array.isArray(value)) return value.join("\n");
  return String(value ?? "");
}

export function splitLines(value) {
  return String(value ?? "")
    .split(/\n/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export function clampNumber(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

export function normalizeBackendUrl(value) {
  const raw = String(value || "").trim() || DEFAULT_BACKEND_URL;
  try {
    return new URL(raw).toString().replace(/\/+$/, "");
  } catch {
    return DEFAULT_BACKEND_URL;
  }
}

export function delay(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}
