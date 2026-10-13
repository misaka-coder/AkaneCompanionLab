import { asObject, finitePercent, safeAssetUrl, text } from "./shared.js";

export function createCharacterViewModel(value, live, runtime) {
  const characterRuntime = asObject(value);
  const petState = asObject(live.state);
  const expression = asObject(live.currentExpression);
  const packId = text(characterRuntime.selectedPackId) || text(petState.characterPackId);
  const emotion = text(expression.name) || text(asObject(runtime.emotion).name) || text(petState.currentEmotion);
  const portrait = safeAssetUrl(text(expression.image) || text(asObject(runtime.emotion).image) || firstActiveEmotionImage(characterRuntime));
  return {
    packId,
    displayName: text(characterRuntime.selectedPack) || text(petState.characterPackId) || "当前角色",
    voice: normalizeCharacterVoicePreference(characterRuntime.voice),
    outfit: text(petState.outfit),
    emotion,
    visuals: { avatar: portrait, portrait, hero: safeAssetUrl(text(characterRuntime.hero)) },
    resourceWarnings: normalizeCharacterWarnings(characterRuntime),
    availablePacks: normalizeAvailablePacks(characterRuntime.availablePacks, packId),
    outfits: normalizeVisualChoices(characterRuntime.outfits, text(petState.outfit), "服装"),
    emotions: normalizeVisualChoices(characterRuntime.emotions, emotion || text(petState.currentEmotion), "表情"),
    packInfo: normalizePackInfo(characterRuntime.packInfo),
    completeness: finitePercent(characterRuntime.completeness)
  };
}

function normalizeCharacterVoicePreference(value) {
  const source = asObject(value);
  return {
    provider: text(source.provider),
    profileId: text(source.profileId) || text(source.profile_id),
    notes: text(source.notes)
  };
}

function normalizeCharacterWarnings(characterRuntime) {
  const warning = asObject(characterRuntime.warning);
  if (!text(warning.headline)) return [];
  return /良好|已加载/.test(text(warning.headline)) ? [] : [text(warning.headline), text(warning.body)].filter(Boolean);
}

function normalizeAvailablePacks(value, activePackId) {
  const active = text(activePackId);
  return (Array.isArray(value) ? value : []).map((item) => {
    const source = asObject(item);
    const id = text(source.id) || text(source.packId) || text(source.pack_id);
    if (!id) return null;
    return {
      id,
      displayName: text(source.appName) || text(source.name) || id,
      description: [text(source.defaultOutfit), text(source.defaultEmotion)].filter(Boolean).join(" · ") || text(source.schemaVersion),
      selected: active ? id === active : Boolean(source.selected)
    };
  }).filter(Boolean);
}

function normalizeVisualChoices(value, activeValue, fallbackLabel) {
  const active = text(activeValue).toLowerCase();
  return (Array.isArray(value) ? value : []).map((item, index) => {
    const source = asObject(item);
    const id = text(source.id) || text(source.name) || `${fallbackLabel}_${index + 1}`;
    const name = text(source.name) || id;
    const current = Boolean(source.current) || Boolean(active && (id.toLowerCase() === active || name.toLowerCase() === active));
    return { id, name, image: safeAssetUrl(text(source.image)), current };
  });
}

function normalizePackInfo(value) {
  return (Array.isArray(value) ? value : []).map((item) => ({
    label: text(item?.label),
    value: text(item?.value)
  })).filter((item) => item.label && item.value);
}

function firstActiveEmotionImage(characterRuntime) {
  const emotions = Array.isArray(characterRuntime.emotions) ? characterRuntime.emotions : [];
  const current = emotions.find((item) => item?.current) || emotions[0];
  return text(current?.image);
}
