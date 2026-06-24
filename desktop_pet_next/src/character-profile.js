const characterProfileModules = import.meta.glob(
  "../../desktop_pet_creator_kit/characters/*/character.json",
  {
    eager: true,
    import: "default"
  }
);

const CHARACTER_PACK_STORAGE_KEY = "akane-next-character-pack-id";
const DEFAULT_CHARACTER_PACK_ID = "akane_v1";

const FALLBACK_PROFILE = {
  schema_version: "akane.character.v0.1",
  identity: {
    id: "akane_v1",
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
    default_outfit: "default",
    default_emotion: "normal",
    music_emotion: "listening",
    required_emotions: ["normal"],
    recommended_emotions: ["thinking", "happy", "confused", "listening"]
  },
  dialogue: {
    input_placeholder: "和 Akane 说点什么……",
    session_display_title: "Akane 桌宠对话",
    tts_test_text: "Akane Next：语音播放测试。",
    proactive_wake_prompt:
      "主人暂时没有说话。你像坐在旁边陪他一样，轻轻搭一句自然的话。桌面线索只当背景，不要刻意围绕窗口标题发挥。",
    local_click_lines: [{ text: "嗯？我在哦。", emotion: "normal" }]
  },
  care: {
    enabled: true,
    initial_coins: 20,
    initial_hunger: 55,
    initial_energy: 70,
    initial_affection: 10,
    decay: {
      hunger_per_hour: 4,
      energy_per_reply: 1,
      energy_per_proactive: 0
    },
    work: {
      enabled: true,
      duration_seconds: 20,
      reward_coins_min: 6,
      reward_coins_max: 12,
      min_hunger: 20,
      min_energy: 25,
      hunger_cost: 12,
      energy_cost: 25,
      start_feedback: {
        emotion: "normal",
        bubble: { text: "我出去转一圈，很快回来。", duration_ms: 1800 }
      },
      complete_feedback: {
        emotion: "happy",
        bubble: { text: "我回来啦，带了点金币。", duration_ms: 2200 }
      }
    },
    allowance: {
      enabled: true,
      coins: 4,
      cooldown_seconds: 300,
      max_coins: 6,
      feedback: {
        emotion: "normal",
        bubble: { text: "先拿去应急吧。", duration_ms: 1600 }
      }
    },
    shop_items: [
      {
        id: "strawberry_cake",
        name: "草莓蛋糕",
        description: "小小一块，适合当作投喂测试。",
        price: 8,
        effects: { hunger: 18, energy: 8, affection: 4 },
        feedback: { emotion: "happy", bubble: { text: "甜的！", duration_ms: 1800 } }
      },
      {
        id: "warm_tea",
        name: "热茶",
        description: "暖暖的一杯，适合累的时候递过去。",
        price: 5,
        effects: { hunger: 6, energy: 12, affection: 2 },
        feedback: { emotion: "happy", bubble: { text: "嗯，舒服多了。", duration_ms: 1800 } }
      },
      {
        id: "rice_ball",
        name: "饭团",
        description: "朴素但顶饱，饿的时候最可靠。",
        price: 10,
        effects: { hunger: 28, energy: 4, affection: 1 },
        feedback: { emotion: "normal", bubble: { text: "这个很安心。", duration_ms: 1800 } }
      },
      {
        id: "red_bean_daifuku",
        name: "红豆大福",
        description: "甜甜糯糯，适合当作灵梦也会喜欢的小点心。",
        price: 12,
        effects: { hunger: 22, energy: 6, affection: 3 },
        feedback: { emotion: "happy", bubble: { text: "这个味道不错。", duration_ms: 1800 } }
      },
      {
        id: "wake_soda",
        name: "清醒汽水",
        description: "不太顶饱，但很适合困到打哈欠的时候。",
        price: 9,
        effects: { hunger: 2, energy: 26, affection: 1 },
        feedback: { emotion: "happy", bubble: { text: "好，清醒一点了。", duration_ms: 1800 } }
      }
    ]
  },
  play_feedback: {
    throw_fast: { emotion: "shock", bubble: { text: "啊啊啊飞起来啦！", duration_ms: 1500 } },
    throw_light: { emotion: "confused", bubble: { text: "", duration_ms: 0 } },
    wall_hit: { emotion: "confused", bubble: { text: "撞到了。", duration_ms: 1200 } },
    land: { emotion: "", bubble: { text: "", duration_ms: 0 } }
  },
  emotion_aliases: {
    normal: ["正常", "normal"],
    thinking: ["思考中", "困惑", "正常"],
    happy: ["开心", "正常"],
    confused: ["困惑", "正常"],
    shock: ["困惑", "气鼓鼓", "正常"],
    pat: ["被摸头", "开心", "脸红", "正常"],
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
    care: {
      enabled: Boolean(profile.care?.enabled),
      initialCoins: Number(profile.care?.initialCoins || 0),
      initialHunger: Number(profile.care?.initialHunger || 0),
      initialEnergy: Number(profile.care?.initialEnergy || 0),
      initialAffection: Number(profile.care?.initialAffection || 0),
      decay: profile.care?.decay ? { ...profile.care.decay } : null,
      work: profile.care?.work ? { ...profile.care.work } : null,
      allowance: profile.care?.allowance ? { ...profile.care.allowance } : null,
      shopItems: Array.isArray(profile.care?.shopItems) ? profile.care.shopItems.map((item) => ({ ...item })) : []
    },
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
  const care = source.care && typeof source.care === "object" ? source.care : {};
  const playFeedback = source.play_feedback && typeof source.play_feedback === "object" ? source.play_feedback : {};
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
    care: normalizeCare(care, fallback.care),
    playFeedback: normalizePlayFeedback(playFeedback, fallback.play_feedback),
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

function normalizeCare(value, fallback = {}) {
  const source = value && typeof value === "object" ? value : {};
  return {
    enabled: source.enabled !== undefined ? Boolean(source.enabled) : Boolean(fallback.enabled),
    initialCoins: cleanNonNegativeInteger(source.initial_coins, fallback.initial_coins ?? 0),
    initialHunger: cleanBoundedInteger(source.initial_hunger, fallback.initial_hunger ?? 50, 0, 100),
    initialEnergy: cleanBoundedInteger(source.initial_energy, fallback.initial_energy ?? 50, 0, 100),
    initialAffection: cleanBoundedInteger(source.initial_affection, fallback.initial_affection ?? 0, 0, 100),
    decay: normalizeCareDecay(source.decay, fallback.decay),
    work: normalizeCareWork(source.work, fallback.work),
    allowance: normalizeCareAllowance(source.allowance, fallback.allowance),
    shopItems: cleanShopItems(source.shop_items, fallback.shop_items || []).filter(isShopItemUsableInDesktop),
    carePreferences: normalizeCarePreferences(source.care_preferences, fallback.care_preferences)
  };
}

function normalizeCareDecay(value, fallback = {}) {
  const source = value && typeof value === "object" ? value : {};
  return {
    hungerPerHour: cleanBoundedInteger(source.hunger_per_hour, fallback.hunger_per_hour ?? 4, 0, 100),
    energyPerReply: cleanBoundedInteger(source.energy_per_reply, fallback.energy_per_reply ?? 1, 0, 20),
    energyPerProactive: cleanBoundedInteger(source.energy_per_proactive, fallback.energy_per_proactive ?? 0, 0, 20)
  };
}

function normalizeCareWork(value, fallback = {}) {
  const source = value && typeof value === "object" ? value : {};
  const startFeedback = source.start_feedback && typeof source.start_feedback === "object" ? source.start_feedback : {};
  const completeFeedback = source.complete_feedback && typeof source.complete_feedback === "object" ? source.complete_feedback : {};
  return {
    enabled: source.enabled !== undefined ? Boolean(source.enabled) : Boolean(fallback.enabled),
    durationSeconds: cleanBoundedInteger(source.duration_seconds, fallback.duration_seconds ?? 20, 1, 3600),
    rewardCoinsMin: cleanNonNegativeInteger(source.reward_coins_min, fallback.reward_coins_min ?? 5),
    rewardCoinsMax: cleanNonNegativeInteger(source.reward_coins_max, fallback.reward_coins_max ?? 10),
    minHunger: cleanBoundedInteger(source.min_hunger, fallback.min_hunger ?? 20, 0, 100),
    minEnergy: cleanBoundedInteger(source.min_energy, fallback.min_energy ?? 25, 0, 100),
    hungerCost: cleanBoundedInteger(source.hunger_cost, fallback.hunger_cost ?? 12, 0, 100),
    energyCost: cleanBoundedInteger(source.energy_cost, fallback.energy_cost ?? 25, 0, 100),
    startFeedback: normalizeCareFeedback(startFeedback, fallback.start_feedback),
    completeFeedback: normalizeCareFeedback(completeFeedback, fallback.complete_feedback)
  };
}

function normalizeCareAllowance(value, fallback = {}) {
  const source = value && typeof value === "object" ? value : {};
  return {
    enabled: source.enabled !== undefined ? Boolean(source.enabled) : Boolean(fallback.enabled),
    coins: cleanBoundedInteger(source.coins, fallback.coins ?? 4, 1, 999999),
    cooldownSeconds: cleanBoundedInteger(
      source.cooldown_seconds ?? source.cooldownSeconds,
      fallback.cooldown_seconds ?? fallback.cooldownSeconds ?? 300,
      0,
      86400
    ),
    maxCoins: cleanBoundedInteger(
      source.max_coins ?? source.maxCoins,
      fallback.max_coins ?? fallback.maxCoins ?? 6,
      1,
      999999
    ),
    feedback: normalizeCareFeedback(source.feedback, fallback.feedback)
  };
}

function normalizeCareFeedback(value, fallback = {}) {
  const source = value && typeof value === "object" ? value : {};
  const fallbackBubble = fallback?.bubble && typeof fallback.bubble === "object" ? fallback.bubble : {};
  const bubble = source.bubble && typeof source.bubble === "object" ? source.bubble : {};
  return {
    emotion: cleanText(source.emotion, fallback?.emotion || ""),
    bubble: {
      text: cleanText(bubble.text, fallbackBubble.text || ""),
      durationMs: cleanNonNegativeInteger(bubble.duration_ms, fallbackBubble.duration_ms ?? 0)
    }
  };
}

function cleanShopItems(value, fallback = []) {
  const items = Array.isArray(value) ? value : fallback;
  return items
    .filter((item) => item && typeof item === "object")
    .map((item) => {
      const effects = item.effects && typeof item.effects === "object" ? item.effects : {};
      const feedback = item.feedback && typeof item.feedback === "object" ? item.feedback : {};
      const bubble = feedback.bubble && typeof feedback.bubble === "object" ? feedback.bubble : {};
      return {
        id: cleanText(item.id),
        name: cleanText(item.name),
        description: cleanText(item.description),
        price: cleanNonNegativeInteger(item.price, 0),
        category: cleanText(item.category),
        preferenceTags: cleanStringArray(item.preference_tags || item.preferenceTags),
        usableIn: cleanStringArray(item.usable_in || item.usableIn),
        feedbackTone: cleanText(item.feedback_tone || item.feedbackTone),
        effects: {
          hunger: cleanSignedInteger(effects.hunger, 0),
          affection: cleanSignedInteger(effects.affection, 0),
          energy: cleanSignedInteger(effects.energy, 0)
        },
        feedback: {
          emotion: cleanText(feedback.emotion),
          bubble: {
            text: cleanText(bubble.text),
            durationMs: cleanNonNegativeInteger(bubble.duration_ms, 0)
          }
        }
      };
    })
    .filter((item) => item.id && item.name);
}

function isShopItemUsableInDesktop(item) {
  return !item.usableIn.length || item.usableIn.includes("desktop_pet");
}

function normalizeCarePreferences(value, fallback = {}) {
  const source = value && typeof value === "object" ? value : {};
  const fallbackSource = fallback && typeof fallback === "object" ? fallback : {};
  return {
    favoriteTags: cleanStringArray(source.favorite_tags || source.favoriteTags, fallbackSource.favorite_tags || fallbackSource.favoriteTags),
    dislikedTags: cleanStringArray(source.disliked_tags || source.dislikedTags, fallbackSource.disliked_tags || fallbackSource.dislikedTags),
    offeringTags: cleanStringArray(source.offering_tags || source.offeringTags, fallbackSource.offering_tags || fallbackSource.offeringTags),
    defaultAffectionBonusTags: cleanStringArray(
      source.default_affection_bonus_tags || source.defaultAffectionBonusTags,
      fallbackSource.default_affection_bonus_tags || fallbackSource.defaultAffectionBonusTags
    )
  };
}

function normalizePlayFeedback(value, fallback = {}) {
  const source = value && typeof value === "object" ? value : {};
  return {
    throwFast: normalizePlayFeedbackEntry(source.throw_fast, fallback.throw_fast),
    throwLight: normalizePlayFeedbackEntry(source.throw_light, fallback.throw_light),
    wallHit: normalizePlayFeedbackEntry(source.wall_hit, fallback.wall_hit),
    land: normalizePlayFeedbackEntry(source.land, fallback.land)
  };
}

function cleanSignedInteger(value, fallback = 0) {
  const number = Math.round(Number(value));
  return Number.isFinite(number) ? number : fallback;
}

function cleanNonNegativeInteger(value, fallback = 0) {
  const number = Math.round(Number(value));
  return Number.isFinite(number) && number >= 0 ? number : fallback;
}

function cleanBoundedInteger(value, fallback, min, max) {
  const number = cleanSignedInteger(value, fallback);
  return Math.min(max, Math.max(min, number));
}

function normalizePlayFeedbackEntry(value, fallback = {}) {
  const source = value && typeof value === "object" ? value : {};
  const fallbackBubble = fallback.bubble && typeof fallback.bubble === "object" ? fallback.bubble : {};
  const bubble = source.bubble && typeof source.bubble === "object" ? source.bubble : {};
  const text = Object.prototype.hasOwnProperty.call(bubble, "text")
    ? String(bubble.text ?? "").trim()
    : cleanText(fallbackBubble.text, "");
  const durationMs = Number(bubble.duration_ms ?? fallbackBubble.duration_ms ?? 0);
  return {
    emotion: Object.prototype.hasOwnProperty.call(source, "emotion")
      ? String(source.emotion ?? "").trim()
      : cleanText(fallback.emotion, ""),
    bubble: {
      text,
      durationMs: Number.isFinite(durationMs) && durationMs > 0 ? durationMs : 0
    }
  };
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
  if (!aliases.shock) aliases.shock = ["困惑", "气鼓鼓", defaultEmotion].filter(Boolean);
  if (!aliases.pat) aliases.pat = ["被摸头", "开心", "脸红", defaultEmotion].filter(Boolean);
  if (!aliases.music) aliases.music = [defaultEmotion];
  return aliases;
}

function normalizePackKey(value) {
  return String(value || "").trim().toLowerCase().replace(/[-\s]+/g, "_");
}
