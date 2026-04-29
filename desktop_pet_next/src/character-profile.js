import runtimeCharacterProfile from "../../desktop_pet_creator_kit/characters/akane_sample/character.json";

const FALLBACK_PROFILE = {
  schema_version: "akane.character.v0.1",
  identity: {
    id: "akane_sample",
    name: "Akane",
    app_name: "Akane Next",
    user_title: "主人"
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
      "主人暂时没有说话。你像坐在旁边陪他一样，参考刚才看见的情况自然接话。",
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
    bundled_outfit: "猫娘"
  }
};

export const CHARACTER_PROFILE_SOURCE =
  "desktop_pet_creator_kit/characters/akane_sample/character.json";

export const CHARACTER_PROFILE = normalizeCharacterProfile(runtimeCharacterProfile);
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

export function buildCharacterSnapshot() {
  return {
    schemaVersion: CHARACTER_PROFILE.schemaVersion,
    source: CHARACTER_PROFILE_SOURCE,
    id: CHARACTER_ID,
    name: CHARACTER_NAME,
    appName: APP_DISPLAY_NAME,
    userTitle: USER_TITLE,
    defaultOutfit: DEFAULT_OUTFIT,
    defaultEmotion: DEFAULT_EMOTION,
    musicEmotion: MUSIC_EMOTION,
    requiredEmotionCount: REQUIRED_EMOTIONS.length,
    recommendedEmotionCount: RECOMMENDED_EMOTIONS.length,
    localLineCount: LOCAL_CLICK_LINES.length,
    assetSource: CHARACTER_PROFILE.assets.runtimeSource,
    assetRoot: CHARACTER_PROFILE.assets.assetRoot,
    portraitGlob: CHARACTER_PROFILE.assets.portraitGlob,
    bundledOutfit: CHARACTER_PROFILE.assets.bundledOutfit
  };
}

function normalizeCharacterProfile(value) {
  const source = value && typeof value === "object" ? value : {};
  const fallback = FALLBACK_PROFILE;
  const identity = source.identity && typeof source.identity === "object" ? source.identity : {};
  const appearance = source.appearance && typeof source.appearance === "object" ? source.appearance : {};
  const dialogue = source.dialogue && typeof source.dialogue === "object" ? source.dialogue : {};
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
      userTitle: cleanText(identity.user_title, fallback.identity.user_title)
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
    }
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
