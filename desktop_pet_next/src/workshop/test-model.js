import { asArray } from "./shared.js";

export function collectTestAvailableEmotions(appearance, outfitAsset, defaultEmotion) {
  const entries = [];
  const seen = new Set();
  const add = (id, name = "") => {
    const cleanId = String(id || "").trim();
    if (!cleanId || seen.has(cleanId)) return;
    seen.add(cleanId);
    entries.push({ id: cleanId, name: String(name || cleanId).trim() || cleanId });
  };

  add(defaultEmotion);
  for (const item of asArray(outfitAsset?.emotions)) {
    add(item?.id || item?.name, item?.name || item?.id);
  }
  add(appearance.default_emotion || appearance.defaultEmotion);
  add(appearance.music_emotion || appearance.musicEmotion);
  for (const id of asArray(appearance.required_emotions || appearance.requiredEmotions)) add(id);
  for (const id of asArray(appearance.recommended_emotions || appearance.recommendedEmotions)) add(id);
  return entries;
}

export function findTestOutfitAsset(assets, outfitId) {
  const id = String(outfitId || "").trim();
  if (!id) return null;
  return (Array.isArray(assets) ? assets : []).find((item) => String(item?.id || "").trim() === id) || null;
}

export function formatTestLayoutSummary(layout) {
  const width = Number(layout?.window?.width) || 0;
  const height = Number(layout?.window?.height) || 0;
  if (width > 0 && height > 0) return `${Math.round(width)} × ${Math.round(height)}`;
  return "已保存";
}

export function normalizeResponseSegments(value) {
  return asArray(value)
    .map((item) => {
      if (typeof item === "string") return item.trim();
      if (item && typeof item === "object") {
        return String(item.text || item.speech || item.content || "").trim();
      }
      return "";
    })
    .filter(Boolean);
}

export function splitDisplayText(value) {
  const text = String(value || "").trim();
  if (!text) return [];
  return text
    .split(/(?<=[。！？!?])\s*/)
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(0, 4);
}
