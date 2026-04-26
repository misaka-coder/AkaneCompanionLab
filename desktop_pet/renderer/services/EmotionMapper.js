const LEGACY_EMOTION_FILE_MAP = {
  normal: "normal",
  shy: "shy",
  smug: "smug",
  sumg: "smug",
  cry: "sad",
  dizzy: "dizzy",
  sad: "sad",
  sleeping: "sleeping",
};

const DEFAULT_EMOTION = "normal";
const DEFAULT_OUTFIT = "水手服";

function normalizeKey(value) {
  return String(value || "").trim().toLowerCase().replace(/[-\s]+/g, "_");
}

function resolveEmotion(emotion) {
  const key = normalizeKey(emotion);
  return LEGACY_EMOTION_FILE_MAP[key] || LEGACY_EMOTION_FILE_MAP[DEFAULT_EMOTION];
}

function buildSpriteUrl(outfit, emotion, backendUrl) {
  const filename = resolveEmotion(emotion);
  const safeOutfit = encodeURIComponent(outfit || DEFAULT_OUTFIT);
  return `${String(backendUrl || "").replace(/\/+$/, "")}/assets/characters/${safeOutfit}/${filename}.png`;
}

function buildManifestIndex(manifest) {
  const outfits = Array.isArray(manifest?.characters?.outfits) ? manifest.characters.outfits : [];
  const outfitItems = outfits.filter((item) => item && typeof item === "object");
  const defaults = manifest?.defaults && typeof manifest.defaults === "object" ? manifest.defaults : {};

  return {
    defaults,
    outfits: outfitItems,
  };
}

function resolveManifestSprite({ manifest, outfit, emotion, backendUrl, assetVersion = "" }) {
  const index = buildManifestIndex(manifest);
  const outfitEntry = findOutfit(index, outfit);
  const fallbackOutfit = outfitEntry || findOutfit(index, index.defaults.outfit) || index.outfits[0] || null;

  if (!fallbackOutfit) {
    return legacySpriteResolution({ outfit, emotion, backendUrl, assetVersion });
  }

  const emotionEntry =
    findEmotion(fallbackOutfit, emotion) ||
    findEmotion(fallbackOutfit, index.defaults.emotion) ||
    findEmotion(fallbackOutfit, DEFAULT_EMOTION) ||
    firstEmotion(fallbackOutfit);

  if (!emotionEntry?.path) {
    return legacySpriteResolution({
      outfit: fallbackOutfit.id || outfit,
      emotion: emotionEntry?.id || emotion,
      backendUrl,
      assetVersion,
    });
  }

  return {
    id: String(emotionEntry.id || emotion || DEFAULT_EMOTION),
    outfitId: String(fallbackOutfit.id || outfit || DEFAULT_OUTFIT),
    url: withAssetVersion(resolveAssetUrl(emotionEntry.path, backendUrl), assetVersion),
    source: "manifest",
    knownEmotionIds: listEmotionIds(fallbackOutfit),
  };
}

function findOutfit(index, value) {
  const items = Array.isArray(index?.outfits) ? index.outfits : [];
  return findEntry(items, value);
}

function findEmotion(outfit, value) {
  const items = Array.isArray(outfit?.emotions) ? outfit.emotions : [];
  return findEntry(items, value) || findEntry(items, resolveEmotion(value));
}

function findEntry(items, value) {
  const raw = String(value || "").trim();
  if (!raw) return null;
  const key = normalizeKey(raw);
  return (
    items.find((item) => {
      if (!item || typeof item !== "object") return false;
      return entryLookupValues(item).some((option) => option === raw || normalizeKey(option) === key);
    }) || null
  );
}

function entryLookupValues(entry) {
  const values = [];
  for (const key of ["id", "name"]) {
    const value = String(entry?.[key] || "").trim();
    if (value && !values.includes(value)) values.push(value);
  }
  for (const alias of entry?.aliases || []) {
    const value = String(alias || "").trim();
    if (value && !values.includes(value)) values.push(value);
  }
  return values;
}

function firstEmotion(outfit) {
  const items = Array.isArray(outfit?.emotions) ? outfit.emotions : [];
  return items.find((item) => item && typeof item === "object") || null;
}

function listEmotionIds(outfit) {
  return (Array.isArray(outfit?.emotions) ? outfit.emotions : [])
    .map((item) => String(item?.id || "").trim())
    .filter(Boolean);
}

function resolveAssetUrl(path, backendUrl) {
  const raw = String(path || "").trim();
  if (!raw) return "";
  if (/^(https?:|file:|data:|blob:)/i.test(raw)) return encodeURI(raw);

  const base = String(backendUrl || "").trim().replace(/\/+$/, "");
  if (!base) return encodeURI(raw);
  if (raw.startsWith("/")) return encodeURI(`${base}${raw}`);
  return encodeURI(`${base}/${raw.replace(/^\/+/, "")}`);
}

function withAssetVersion(url, assetVersion) {
  const version = String(assetVersion || "").trim();
  if (!version || !url || /^(data:|blob:)/i.test(url)) return url;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}v=${encodeURIComponent(version)}`;
}

function legacySpriteResolution({ outfit, emotion, backendUrl, assetVersion }) {
  const url = withAssetVersion(buildSpriteUrl(outfit || DEFAULT_OUTFIT, emotion || DEFAULT_EMOTION, backendUrl), assetVersion);
  return {
    id: resolveEmotion(emotion),
    outfitId: String(outfit || DEFAULT_OUTFIT),
    url,
    source: "legacy",
    knownEmotionIds: Object.keys(LEGACY_EMOTION_FILE_MAP),
  };
}

export {
  DEFAULT_EMOTION,
  buildManifestIndex,
  buildSpriteUrl,
  resolveEmotion,
  resolveManifestSprite,
};
