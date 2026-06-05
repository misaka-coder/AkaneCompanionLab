const characterProfileModules = import.meta.glob(
  "../../desktop_pet_creator_kit/characters/*/character.json",
  {
    eager: true,
    import: "default"
  }
);

const CHARACTER_PACK_STORAGE_KEY = "akane-next-character-pack-id";
const DEFAULT_CHARACTER_PACK_ID = "akane_sample";

const FALLBACK_PROFILE = {
  schema_version: "akane.character.v0.1",
  identity: {
    id: "akane_sample",
    name: "Akane",
    app_name: "Akane Next",
    self_reference: "我",
    user_title: "主人",
    relationship: "默认演示角色。"
  },
  persona_form: {
    personality_keywords: [],
    speaking_style: "",
    catchphrases: [],
    boundaries: "",
    proactive_style: "",
    example_lines: [],
    extra_setting: ""
  },
  appearance: {
    default_outfit: "猫娘",
    default_emotion: "正常",
    music_emotion: "听歌中",
    required_emotions: ["正常"],
    recommended_emotions: ["思考中", "侧耳听", "听歌中", "困惑", "开心", "得意"]
  },
  dialogue: {
    input_placeholder: "和 Akane 说点什么……",
    session_display_title: "Akane 桌宠对话",
    tts_test_text: "Akane Next：语音播放测试。",
    proactive_wake_prompt:
      "主人暂时没有说话。你像坐在旁边陪他一样，轻轻搭一句自然的话。桌面线索只当背景，不要刻意围绕窗口标题发挥。",
    local_click_lines: [{ text: "嗯？我在哦。", emotion: "正常" }]
  },
  emotion_aliases: {
    normal: ["正常", "normal"],
    thinking: ["思考中", "困惑", "正常"],
    happy: ["开心", "正常"],
    confused: ["困惑", "正常"],
    music: ["听歌中", "开心", "正常"]
  },
  assets: {
    runtime_source: "desktop_pet_next bundled assets",
    asset_root: "assets",
    bundled_outfit: "猫娘",
    portrait_glob: ""
  }
};

const staticCharacterPacks = buildCharacterPackRegistry();
let characterPacks = [...staticCharacterPacks];
let activeCharacterPackId = resolveInitialCharacterPackId();

export const CHARACTER_PROFILE_SOURCE = getActiveCharacterPack().source;
export const CHARACTER_PROFILE = getActiveCharacterProfile();
export const CHARACTER_ID = CHARACTER_PROFILE.identity.id;
export const CHARACTER_NAME = CHARACTER_PROFILE.identity.name;
export const APP_DISPLAY_NAME = CHARACTER_PROFILE.identity.appName;
export const USER_TITLE = CHARACTER_PROFILE.identity.userTitle;
export const DEFAULT_OUTFIT = CHARACTER_PROFILE.appearance.defaultOutfit;
export const DEFAULT_EMOTION = CHARACTER_PROFILE.appearance.defaultEmotion;
export const MUSIC_EMOTION = CHARACTER_PROFILE.appearance.musicEmotion;
export const REQUIRED_EMOTIONS = CHARACTER_PROFILE.appearance.requiredEmotions;
export const RECOMMENDED_EMOTIONS = CHARACTER_PROFILE.appearance.recommendedEmotions;
export const COMMON_EMOTION_CANDIDATES = CHARACTER_PROFILE.emotionAliases;
export const LOCAL_CLICK_LINES = CHARACTER_PROFILE.dialogue.localClickLines;
export const PROACTIVE_WAKE_PROMPT = CHARACTER_PROFILE.dialogue.proactiveWakePrompt;
export const INPUT_PLACEHOLDER = CHARACTER_PROFILE.dialogue.inputPlaceholder;
export const SESSION_DISPLAY_TITLE = CHARACTER_PROFILE.dialogue.sessionDisplayTitle;
export const TTS_TEST_TEXT = CHARACTER_PROFILE.dialogue.ttsTestText;
export const CHARACTER_ASSET_ROOT = CHARACTER_PROFILE.assets.assetRoot;
export const CHARACTER_PORTRAIT_GLOB = CHARACTER_PROFILE.assets.portraitGlob;

export function getActiveCharacterPack() {
  return resolveCharacterPack(activeCharacterPackId);
}

export function getActiveCharacterPackId() {
  return getActiveCharacterPack().packId;
}

export function getActiveCharacterProfile() {
  return getActiveCharacterPack().profile;
}

export function getActiveCharacterIdentity() {
  return getActiveCharacterProfile().identity;
}

export function getActiveCharacterAppearance() {
  return getActiveCharacterProfile().appearance;
}

export function getActiveCharacterDialogue() {
  return getActiveCharacterProfile().dialogue;
}

export function getActiveCharacterAssets() {
  return getActiveCharacterProfile().assets;
}

export function getActiveCharacterText(key, fallback = "") {
  return String(getActiveCharacterProfile()?.dialogue?.[key] || fallback || "").trim();
}

export function getActiveCharacterAppearanceValue(key, fallback = "") {
  return String(getActiveCharacterProfile()?.appearance?.[key] || fallback || "").trim();
}

export function selectCharacterPack(value, { persist = true } = {}) {
  const pack = resolveCharacterPack(value);
  activeCharacterPackId = pack.packId;
  if (persist) {
    writeStoredCharacterPackId(pack.packId);
  }
  return pack;
}

export function listCharacterPacks() {
  const activeId = getActiveCharacterPackId();
  return characterPacks.map((pack) => ({
    id: pack.packId,
    characterId: pack.profile.identity.id,
    name: pack.profile.identity.name,
    appName: pack.profile.identity.appName,
    userTitle: pack.profile.identity.userTitle,
    relationship: pack.profile.identity.relationship,
    schemaVersion: pack.profile.schemaVersion,
    source: pack.source,
    installedPath: pack.installedPath || "",
    assetCount: Number(pack.assetCount || 0),
    defaultOutfit: pack.profile.appearance.defaultOutfit,
    defaultEmotion: pack.profile.appearance.defaultEmotion,
    assetSource: pack.profile.assets.runtimeSource,
    selected: pack.packId === activeId
  }));
}

export function setRuntimeCharacterPacks(items) {
  const runtimePacks = (Array.isArray(items) ? items : [])
    .map(normalizeRuntimeCharacterPack)
    .filter((pack) => pack.packId);
  const merged = new Map();
  for (const pack of staticCharacterPacks) merged.set(pack.packId, pack);
  for (const pack of runtimePacks) merged.set(pack.packId, pack);
  characterPacks = sortCharacterPacks([...merged.values()]);
  activeCharacterPackId = resolveCharacterPack(activeCharacterPackId || readStoredCharacterPackId()).packId;
  return listCharacterPacks();
}

export function buildCharacterSnapshot() {
  const pack = getActiveCharacterPack();
  const profile = pack.profile;
  return {
    schemaVersion: profile.schemaVersion,
    source: pack.source,
    packId: pack.packId,
    availablePacks: listCharacterPacks(),
    id: profile.identity.id,
    name: profile.identity.name,
    appName: profile.identity.appName,
    userTitle: profile.identity.userTitle,
    selfReference: profile.identity.selfReference,
    relationship: profile.identity.relationship,
    defaultOutfit: profile.appearance.defaultOutfit,
    defaultEmotion: profile.appearance.defaultEmotion,
    musicEmotion: profile.appearance.musicEmotion,
    requiredEmotionCount: profile.appearance.requiredEmotions.length,
    recommendedEmotionCount: profile.appearance.recommendedEmotions.length,
    localLineCount: profile.dialogue.localClickLines.length,
    assetSource: profile.assets.runtimeSource,
    assetRoot: profile.assets.assetRoot,
    portraitGlob: profile.assets.portraitGlob,
    bundledOutfit: profile.assets.bundledOutfit,
    personaForm: { ...profile.personaForm },
    layout: profile.layout,
    voice: { ...profile.voice }
  };
}

function buildCharacterPackRegistry() {
  const entries = Object.entries(characterProfileModules)
    .map(([source, profile]) => {
      const packId = getPackIdFromSource(source);
      return {
        packId,
        source,
        profile: normalizeCharacterProfile(profile)
      };
    })
    .filter((pack) => pack.packId);

  if (!entries.length) {
    entries.push({
      packId: DEFAULT_CHARACTER_PACK_ID,
      source: "fallback",
      profile: normalizeCharacterProfile(FALLBACK_PROFILE)
    });
  }

  return sortCharacterPacks(entries);
}

function resolveInitialCharacterPackId() {
  const stored = readStoredCharacterPackId();
  return resolveCharacterPack(stored).packId;
}

function resolveCharacterPack(value) {
  const raw = String(value || "").trim();
  const normalized = normalizePackKey(raw);
  const match =
    characterPacks.find((pack) => pack.packId === raw) ||
    characterPacks.find((pack) => normalizePackKey(pack.packId) === normalized) ||
    characterPacks.find((pack) => pack.profile.identity.id === raw) ||
    characterPacks.find((pack) => normalizePackKey(pack.profile.identity.id) === normalized) ||
    characterPacks.find((pack) => pack.packId === DEFAULT_CHARACTER_PACK_ID) ||
    characterPacks[0];
  return match;
}

function getPackIdFromSource(source) {
  const match = String(source || "").match(/\/characters\/([^/]+)\/character\.json$/);
  return decodeURIComponent(match?.[1] || "").trim();
}

function normalizeRuntimeCharacterPack(item) {
  const source = item && typeof item === "object" ? item : {};
  const profile = normalizeCharacterProfile(source.profile || {});
  const packId = cleanText(source.id || source.packId || profile.identity.id, "");
  return {
    packId,
    source: cleanText(source.source, `runtime:${packId}`),
    installedPath: cleanText(source.installedPath, ""),
    assetCount: Number(source.assetCount || 0),
    profile
  };
}

function sortCharacterPacks(packs) {
  return packs.sort((a, b) => {
    if (a.packId === DEFAULT_CHARACTER_PACK_ID) return -1;
    if (b.packId === DEFAULT_CHARACTER_PACK_ID) return 1;
    return a.profile.identity.name.localeCompare(b.profile.identity.name, "zh-CN");
  });
}

function readStoredCharacterPackId() {
  try {
    return window.localStorage.getItem(CHARACTER_PACK_STORAGE_KEY) || "";
  } catch {
    return "";
  }
}

function writeStoredCharacterPackId(value) {
  try {
    window.localStorage.setItem(CHARACTER_PACK_STORAGE_KEY, String(value || ""));
  } catch {
    // Local storage can be unavailable in restrictive browser contexts.
  }
}

function normalizeCharacterProfile(value) {
  const source = value && typeof value === "object" ? value : {};
  const fallback = FALLBACK_PROFILE;
  const identity = source.identity && typeof source.identity === "object" ? source.identity : {};
  const appearance = source.appearance && typeof source.appearance === "object" ? source.appearance : {};
  const dialogue = source.dialogue && typeof source.dialogue === "object" ? source.dialogue : {};
  const personaForm = source.persona_form && typeof source.persona_form === "object" ? source.persona_form : {};
  const assets = source.assets && typeof source.assets === "object" ? source.assets : {};

  const defaultOutfit = cleanText(appearance.default_outfit, fallback.appearance.default_outfit);
  const defaultEmotion = cleanText(appearance.default_emotion, fallback.appearance.default_emotion);
  const identityName = cleanText(identity.name, fallback.identity.name);
  const appName = cleanText(identity.app_name, identityName);

  return {
    schemaVersion: cleanText(source.schema_version, fallback.schema_version),
    identity: {
      id: cleanText(identity.id, fallback.identity.id),
      name: identityName,
      appName,
      selfReference: cleanText(identity.self_reference, fallback.identity.self_reference || "我"),
      userTitle: cleanText(identity.user_title, fallback.identity.user_title),
      relationship: cleanText(identity.relationship, fallback.identity.relationship || "")
    },
    personaForm: {
      personalityKeywords: cleanStringArray(personaForm.personality_keywords, []),
      speakingStyle: cleanText(personaForm.speaking_style, ""),
      catchphrases: cleanStringArray(personaForm.catchphrases, []),
      boundaries: cleanText(personaForm.boundaries, ""),
      proactiveStyle: cleanText(personaForm.proactive_style, ""),
      exampleLines: cleanExampleLines(personaForm.example_lines),
      extraSetting: cleanText(personaForm.extra_setting, "")
    },
    appearance: {
      defaultOutfit,
      defaultEmotion,
      musicEmotion: cleanText(appearance.music_emotion, fallback.appearance.music_emotion),
      requiredEmotions: cleanStringArray(appearance.required_emotions, [defaultEmotion]),
      recommendedEmotions: cleanStringArray(
        appearance.recommended_emotions,
        fallback.appearance.recommended_emotions
      )
    },
    dialogue: {
      inputPlaceholder: cleanText(dialogue.input_placeholder, `和 ${identityName} 说点什么……`),
      sessionDisplayTitle: cleanText(dialogue.session_display_title, `${identityName} 桌宠对话`),
      ttsTestText: cleanText(dialogue.tts_test_text, `${appName}：语音播放测试。`),
      proactiveWakePrompt: cleanText(
        dialogue.proactive_wake_prompt,
        fallback.dialogue.proactive_wake_prompt
      ),
      localClickLines: cleanLocalClickLines(dialogue.local_click_lines, fallback.dialogue.local_click_lines)
    },
    emotionAliases: normalizeEmotionAliases(source.emotion_aliases || fallback.emotion_aliases, defaultEmotion),
    assets: {
      runtimeSource: cleanText(assets.runtime_source, fallback.assets.runtime_source),
      assetRoot: cleanText(assets.asset_root, "assets"),
      bundledOutfit: cleanText(assets.bundled_outfit, defaultOutfit),
      portraitGlob: cleanText(assets.portrait_glob, "")
    },
    layout: normalizeLayout(source.layout),
    voice: normalizeVoice(source.voice)
  };
}

function cleanExampleLines(value) {
  const items = Array.isArray(value) ? value : [];
  return items
    .filter((item) => item && typeof item === "object")
    .map((item) => ({
      text: cleanText(item.text),
      emotion: cleanText(item.emotion)
    }))
    .filter((item) => item.text);
}

function normalizeLayout(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function normalizeVoice(value) {
  const source = value && typeof value === "object" ? value : {};
  return {
    provider: cleanText(source.provider, ""),
    profileId: cleanText(source.profile_id, ""),
    notes: cleanText(source.notes, "")
  };
}

function cleanText(value, fallback = "") {
  const text = String(value ?? "").trim();
  return text || fallback;
}

function cleanStringArray(value, fallback = []) {
  const items = Array.isArray(value) ? value : fallback;
  return items.map((item) => String(item ?? "").trim()).filter(Boolean);
}

function cleanLocalClickLines(value, fallback = []) {
  const items = Array.isArray(value) ? value : fallback;
  const lines = items
    .filter((item) => item && typeof item === "object")
    .map((item) => ({
      text: cleanText(item.text),
      emotion: cleanText(item.emotion)
    }))
    .filter((item) => item.text);
  return lines.length ? lines : [{ text: "我在哦。", emotion: "" }];
}

function normalizeEmotionAliases(value, defaultEmotion) {
  const source = value && typeof value === "object" ? value : {};
  const entries = Object.entries(source)
    .map(([key, candidates]) => [
      String(key || "").trim().toLowerCase(),
      cleanStringArray(candidates, [defaultEmotion])
    ])
    .filter(([key, candidates]) => key && candidates.length);
  const aliases = Object.fromEntries(entries);
  if (!aliases.normal) aliases.normal = [defaultEmotion, "normal"].filter(Boolean);
  if (!aliases.thinking) aliases.thinking = ["思考中", defaultEmotion].filter(Boolean);
  if (!aliases.confused) aliases.confused = ["困惑", defaultEmotion].filter(Boolean);
  if (!aliases.music) aliases.music = [defaultEmotion];
  return aliases;
}

function normalizePackKey(value) {
  return String(value || "").trim().toLowerCase().replace(/[-\s]+/g, "_");
}
