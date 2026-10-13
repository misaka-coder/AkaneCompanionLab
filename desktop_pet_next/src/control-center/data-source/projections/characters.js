import { asArray, asObject, positiveNumber, stringValue } from "../shared.js";
import { DEFAULT_BACKEND_URL } from "../http.js";

export function buildCharacterRuntimePatch({
  baseUrl,
  resourceManifest,
  diagnostics,
  characterPackId,
  outfit,
  emotion,
  petState,
  availableCharacterPacks
}) {
  const manifest = asObject(resourceManifest);
  const resources = asObject(diagnostics?.resources);
  const defaults = asObject(manifest.defaults);
  const clients = asObject(manifest.clients);
  const desktop = asObject(clients.desktop_pet);
  const packId = stringValue(
    characterPackId ||
      petState?.characterPackId ||
      resources.character_pack_id ||
      resources.characterPackId ||
      desktop.character_pack_id
  );
  const activeOutfitId = stringValue(
    outfit ||
      petState?.outfit ||
      resources.outfit ||
      desktop.default_outfit ||
      defaults.desktop_pet_outfit ||
      defaults.outfit
  );
  const activeEmotionId = stringValue(
    emotion ||
      petState?.currentEmotion ||
      resources.default_emotion ||
      resources.defaultEmotion ||
      desktop.default_emotion ||
      defaults.desktop_pet_emotion ||
      defaults.emotion
  );
  const availablePacks = normalizeAvailableCharacterPacks(availableCharacterPacks, packId);
  const pack = findAvailableCharacterPack(availableCharacterPacks, packId);
  const profile = asObject(pack?.profile);
  const identity = asObject(profile.identity);
  const appearance = asObject(profile.appearance);
  const assets = asObject(profile.assets);
  const voice = normalizeCharacterVoicePreference(profile.voice);
  const characters = asObject(manifest.characters);
  const rawOutfits = asArray(characters.outfits);
  const allOutfits = normalizeOutfitCards(rawOutfits, {
    activeOutfitId,
    activeEmotionId,
    baseUrl
  });
  const activeOutfit =
    findManifestEntry(rawOutfits, activeOutfitId) ||
    findManifestEntry(rawOutfits, appearance.default_outfit) ||
    rawOutfits.find((item) => item && typeof item === "object") ||
    null;
  const emotionCards = normalizeEmotionCards(asArray(activeOutfit?.emotions), {
    activeEmotionId,
    baseUrl
  });
  const outfitCount = rawOutfits.length;
  const emotionCount = rawOutfits.reduce((total, item) => total + asArray(item?.emotions).length, 0);
  const manifestOk = Boolean(manifest.schema_version && outfitCount > 0 && emotionCount > 0);
  const displayName = stringValue(identity.app_name || identity.name || pack?.id || packId || "Akane Default");
  const version = stringValue(profile.version || profile.schema_version || desktop.contract_version || manifest.schema_version);
  const heroImage = toBackendAssetUrl(
    baseUrl,
    assets.hero || assets.cover || assets.banner || assets.header || assets.thumbnail
  );

  return {
    ...(heroImage ? { hero: heroImage } : {}),
    selectedPack: displayName,
    ...(packId ? { selectedPackId: packId } : {}),
    ...(availablePacks.length ? { availablePacks } : {}),
    voice,
    packInfo: [
      { label: "名称", value: displayName },
      { label: "版本", value: version || "resource-manifest" },
      { label: "作者", value: stringValue(profile.author || identity.author) || (pack ? "本地角色包" : "后端资源清单") },
      { label: "描述", value: stringValue(profile.description || assets.runtime_source) || `${outfitCount} 套服装 · ${emotionCount} 个表情` }
    ],
    completeness: manifestOk ? 100 : 0,
    outfits: limitActiveCards(allOutfits, activeOutfitId, 4),
    emotions: limitActiveCards(emotionCards, activeEmotionId, 4),
    warning: manifestOk
      ? {
          title: "资源状态良好",
          headline: "已加载统一资源清单",
          body: `${outfitCount} 套服装 · ${emotionCount} 个表情`,
          action: "刷新资源"
        }
      : {
          title: "资源缺失提示",
          headline: "暂未读取到角色资源清单",
          body: "当前保留本地预览资源",
          action: "重新检查"
        },
    tip: [
      packId ? `当前角色包 id：${packId}。` : "当前使用后端默认角色资源。",
      "服装与表情来自统一资源清单，桌宠和后端会按同一套资源理解当前形象。"
    ]
  };
}

export function buildCharacterRuntimePatchFromSettingsSnapshot(runtimeSnapshot) {
  const character = asObject(runtimeSnapshot?.character);
  const petState = asObject(runtimeSnapshot?.state);
  const resource = asObject(runtimeSnapshot?.resource);
  const currentExpression = asObject(runtimeSnapshot?.currentExpression);
  const selectedPackId = stringValue(
    petState.characterPackId ||
      character.packId ||
      currentExpression.characterPackId ||
      resource.characterPackId
  );
  const availablePacks = normalizeAvailableCharacterPacks(character.availablePacks, selectedPackId);
  const activePack = availablePacks.find((pack) => pack.selected || pack.id === selectedPackId) || null;
  const displayName = stringValue(
    character.appName ||
      character.name ||
      activePack?.appName ||
      activePack?.name ||
      selectedPackId
  );
  const schemaVersion = stringValue(character.schemaVersion || activePack?.schemaVersion);
  const defaultOutfit = stringValue(character.defaultOutfit || activePack?.defaultOutfit || resource.defaultOutfit);
  const defaultEmotion = stringValue(character.defaultEmotion || activePack?.defaultEmotion || resource.defaultEmotion);
  const voice = normalizeCharacterVoicePreference(character.voice);
  const activeEmotionId = stringValue(
    currentExpression.id || currentExpression.name || petState.currentEmotion || defaultEmotion
  );
  const liveEmotionCards = asArray(resource.emotions)
    .map((emotion) => {
      const entry = asObject(emotion);
      const id = stringValue(entry.id || entry.name);
      const image = stringValue(entry.image || entry.url);
      if (!id || !image) return null;
      return {
        id,
        name: stringValue(entry.name || id) || id,
        image,
        current: id === activeEmotionId || stringValue(entry.name) === activeEmotionId
      };
    })
    .filter(Boolean);

  const patch = {
    ...(displayName ? { selectedPack: displayName } : {}),
    ...(selectedPackId ? { selectedPackId } : {}),
    ...(availablePacks.length ? { availablePacks } : {}),
    ...(liveEmotionCards.length ? { emotions: limitActiveCards(liveEmotionCards, activeEmotionId, 4) } : {}),
    voice
  };

  if (displayName || schemaVersion || defaultOutfit || defaultEmotion) {
    patch.packInfo = [
      { label: "名称", value: displayName || selectedPackId || "当前角色包" },
      { label: "版本", value: schemaVersion || "-" },
      { label: "作者", value: "本地角色包" },
      { label: "描述", value: [defaultOutfit, defaultEmotion].filter(Boolean).join(" · ") || "等待资源同步" }
    ];
  }

  return Object.keys(patch).length ? patch : null;
}

function normalizeCharacterVoicePreference(value) {
  const source = asObject(value);
  return {
    provider: stringValue(source.provider),
    profileId: stringValue(source.profileId || source.profile_id),
    notes: stringValue(source.notes)
  };
}

function normalizeOutfitCards(outfits, { activeOutfitId, activeEmotionId, baseUrl }) {
  return outfits
    .filter((item) => item && typeof item === "object")
    .map((outfit) => {
      const id = stringValue(outfit.id || outfit.name);
      const emotions = normalizeEmotionCards(asArray(outfit.emotions), {
        activeEmotionId,
        baseUrl
      });
      const preview = emotions.find((item) => item.id === activeEmotionId) || emotions[0];
      const current = id === activeOutfitId || (!activeOutfitId && Boolean(outfit.current));
      return {
        id,
        name: stringValue(outfit.name || id) || "未命名服装",
        badge: current ? "当前" : "",
        image: preview?.image || "",
        current
      };
    })
    .filter((item) => item.id);
}

export function normalizeEmotionCards(emotions, { activeEmotionId, baseUrl }) {
  return emotions
    .filter((item) => item && typeof item === "object")
    .map((emotion) => {
      const id = stringValue(emotion.id || emotion.name);
      return {
        id,
        name: stringValue(emotion.name || id) || "未命名表情",
        image: toBackendAssetUrl(baseUrl, emotion.path || emotion.url || emotion.src),
        current: id === activeEmotionId || (!activeEmotionId && Boolean(emotion.current))
      };
    })
    .filter((item) => item.id);
}

function limitActiveCards(items, activeId, limit) {
  const cards = Array.isArray(items) ? items : [];
  const maxItems = Math.max(1, Number(limit || 4));
  if (cards.length <= maxItems) return cards;
  const active = cards.find((item) => item.id === activeId || item.current);
  const selected = [];
  if (active) selected.push(active);
  for (const item of cards) {
    if (selected.some((selectedItem) => selectedItem.id === item.id)) continue;
    selected.push(item);
    if (selected.length >= maxItems) break;
  }
  return selected;
}

function findAvailableCharacterPack(packs, packId) {
  const normalized = stringValue(packId);
  if (!normalized) return null;
  return asArray(packs).find((pack) => {
    const profile = asObject(pack?.profile);
    const identity = asObject(profile.identity);
    return [pack?.id, pack?.packId, identity.id].some((value) => stringValue(value) === normalized);
  }) || null;
}

export function findManifestEntry(items, target) {
  const normalized = stringValue(target);
  if (!normalized) return null;
  return asArray(items).find((item) => {
    if (!item || typeof item !== "object") return false;
    const candidates = [item.id, item.name, ...(Array.isArray(item.aliases) ? item.aliases : [])];
    return candidates.some((value) => stringValue(value) === normalized);
  }) || null;
}

function normalizeAvailableCharacterPacks(packs, selectedPackId) {
  const selected = stringValue(selectedPackId);
  const seen = new Set();
  return asArray(packs)
    .map((pack) => normalizeAvailableCharacterPack(pack, selected))
    .filter((pack) => {
      if (!pack?.id || seen.has(pack.id)) return false;
      seen.add(pack.id);
      return true;
    });
}

function normalizeAvailableCharacterPack(pack, selectedPackId) {
  if (!pack || typeof pack !== "object") return null;
  const profile = asObject(pack.profile);
  const identity = asObject(profile.identity);
  const appearance = asObject(profile.appearance);
  const assets = asObject(profile.assets);
  const id = stringValue(pack.id || pack.packId || pack.pack_id);
  if (!id) return null;
  const characterId = stringValue(pack.characterId || pack.character_id || identity.id);
  const name = stringValue(pack.name || identity.name || characterId || id);
  const appName = stringValue(pack.appName || pack.app_name || identity.appName || identity.app_name || name);
  const defaultOutfit = stringValue(pack.defaultOutfit || pack.default_outfit || appearance.defaultOutfit || appearance.default_outfit);
  const defaultEmotion = stringValue(pack.defaultEmotion || pack.default_emotion || appearance.defaultEmotion || appearance.default_emotion);
  const schemaVersion = stringValue(pack.schemaVersion || pack.schema_version || profile.schemaVersion || profile.schema_version);
  const assetSource = stringValue(pack.assetSource || pack.asset_source || assets.runtimeSource || assets.runtime_source);
  const assetCount = positiveNumber(pack.assetCount || pack.asset_count);
  return {
    id,
    characterId,
    name,
    appName: appName || name || id,
    schemaVersion,
    defaultOutfit,
    defaultEmotion,
    assetCount,
    assetSource,
    selected: selectedPackId ? id === selectedPackId : Boolean(pack.selected)
  };
}

function toBackendAssetUrl(baseUrl, value) {
  const raw = stringValue(value);
  if (!raw) return "";
  if (/^(https?:|data:|blob:)/i.test(raw)) return raw;
  const base = `${String(baseUrl || DEFAULT_BACKEND_URL).replace(/\/+$/, "")}/`;
  return new URL(raw, base).toString();
}
