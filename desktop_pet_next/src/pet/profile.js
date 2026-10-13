import { getActiveCharacterProfile, DEFAULT_OUTFIT, DEFAULT_EMOTION, MUSIC_EMOTION, RECOMMENDED_EMOTIONS, COMMON_EMOTION_CANDIDATES, LOCAL_CLICK_LINES, getActiveCharacterPackId, listCharacterPacks } from "../character-profile.js";
import { clampCareValue, normalizeCareWorkConfig, normalizeCareAllowanceConfig, normalizeCareDecayConfig } from "../care-feature.js";

export function getProfileCareConfig() {
  const care = getActiveCharacterProfile()?.care || {};
  return {
    enabled: Boolean(care.enabled),
    initialCoins: clampCareValue(care.initialCoins, 0, 999999),
    initialHunger: clampCareValue(care.initialHunger, 0, 100),
    initialEnergy: clampCareValue(care.initialEnergy, 0, 100),
    initialAffection: clampCareValue(care.initialAffection, 0, 100),
    work: normalizeCareWorkConfig(care.work),
    allowance: normalizeCareAllowanceConfig(care.allowance),
    decay: normalizeCareDecayConfig(care.decay),
    shopItems: Array.isArray(care.shopItems) ? care.shopItems : []
  };
}

export function getProfileDefaultOutfit() {
  return String(getActiveCharacterProfile()?.appearance?.defaultOutfit || DEFAULT_OUTFIT).trim() || DEFAULT_OUTFIT;
}

export function getProfileDefaultEmotion() {
  return String(getActiveCharacterProfile()?.appearance?.defaultEmotion || DEFAULT_EMOTION).trim() || DEFAULT_EMOTION;
}

export function getProfileMusicEmotion() {
  return String(getActiveCharacterProfile()?.appearance?.musicEmotion || MUSIC_EMOTION).trim() || getProfileDefaultEmotion();
}

export function getProfileRestingEmotions() {
  return getActiveCharacterProfile()?.appearance?.restingEmotions || {};
}

export function getProfileRequiredEmotions() {
  const values = getActiveCharacterProfile()?.appearance?.requiredEmotions;
  return Array.isArray(values) && values.length ? values : [getProfileDefaultEmotion()];
}

export function getProfileRecommendedEmotions() {
  const values = getActiveCharacterProfile()?.appearance?.recommendedEmotions;
  return Array.isArray(values) ? values : RECOMMENDED_EMOTIONS;
}

export function getProfileEmotionAliases() {
  return getActiveCharacterProfile()?.emotionAliases || COMMON_EMOTION_CANDIDATES;
}

export function getProfileLocalClickLines() {
  const lines = getActiveCharacterProfile()?.dialogue?.localClickLines;
  return Array.isArray(lines) && lines.length ? lines : LOCAL_CLICK_LINES;
}

export function getProfileIdentityText(key, fallback) {
  return String(getActiveCharacterProfile()?.identity?.[key] || fallback || "").trim();
}

export function normalizeEntryKey(value) {
  return String(value || "").trim().toLowerCase().replace(/[-\s]+/g, "_");
}

export function normalizeCharacterPackId(value) {
  const requested = String(value || "").trim();
  if (!requested) return getActiveCharacterPackId();
  const normalized = normalizeEntryKey(requested);
  const packs = listCharacterPacks();
  const match =
    packs.find((pack) => pack.id === requested) ||
    packs.find((pack) => normalizeEntryKey(pack.id) === normalized) ||
    packs.find((pack) => pack.characterId === requested) ||
    packs.find((pack) => normalizeEntryKey(pack.characterId) === normalized);
  return String(match?.id || getActiveCharacterPackId()).trim();
}
