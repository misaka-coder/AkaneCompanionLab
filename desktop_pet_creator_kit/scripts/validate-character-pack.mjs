#!/usr/bin/env node

import { promises as fs } from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const CURRENT_SCHEMA_VERSION = "akane.character.v0.2";
const SUPPORTED_SCHEMA_VERSIONS = new Set(["akane.character.v0.1", CURRENT_SCHEMA_VERSION]);
const IMAGE_EXTENSIONS = new Set([".png", ".jpg", ".jpeg", ".webp"]);
const VALID_ITEM_CATEGORIES = new Set(["food", "drink", "gift", "offering", "charm", "trick", "potion"]);
const VALID_USABLE_IN = new Set(["desktop_pet", "qq"]);

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const kitRoot = path.resolve(scriptDir, "..");
const packArg = process.argv[2];
const packDir = packArg
  ? path.resolve(process.cwd(), packArg)
  : path.join(kitRoot, "characters", "akane_v1");

const errors = [];
const warnings = [];

function addError(message) {
  errors.push(message);
}

function addWarning(message) {
  warnings.push(message);
}

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function getObject(source, key, label) {
  const value = source?.[key];
  if (!isObject(value)) {
    addError(`${label} must be an object.`);
    return {};
  }
  return value;
}

function getRequiredString(source, key, label) {
  const value = source?.[key];
  if (typeof value !== "string" || !value.trim()) {
    addError(`${label} must be a non-empty string.`);
    return "";
  }
  return value.trim();
}

function getOptionalString(source, key, label, fallback = "") {
  const value = source?.[key];
  if (value === undefined || value === null || value === "") {
    return fallback;
  }
  if (typeof value !== "string") {
    addError(`${label} must be a string when provided.`);
    return fallback;
  }
  return value.trim();
}

function getStringArray(source, key, label, required = false) {
  const value = source?.[key];
  if (value === undefined) {
    if (required) {
      addError(`${label} must be an array of strings.`);
    }
    return [];
  }
  if (!Array.isArray(value)) {
    addError(`${label} must be an array of strings.`);
    return [];
  }
  const clean = [];
  for (const [index, entry] of value.entries()) {
    if (typeof entry !== "string" || !entry.trim()) {
      addError(`${label}[${index}] must be a non-empty string.`);
      continue;
    }
    clean.push(entry.trim());
  }
  return clean;
}

function validatePersonaForm(character) {
  if (character.persona_form === undefined) {
    addWarning("persona_form is missing; workshop persona fields will start empty.");
    return;
  }
  if (!isObject(character.persona_form)) {
    addError("persona_form must be an object when provided.");
    return;
  }
  const form = character.persona_form;
  getStringArray(form, "personality_keywords", "persona_form.personality_keywords");
  getStringArray(form, "catchphrases", "persona_form.catchphrases");
  getOptionalString(form, "speaking_style", "persona_form.speaking_style");
  getOptionalString(form, "boundaries", "persona_form.boundaries");
  getOptionalString(form, "proactive_style", "persona_form.proactive_style");
  getOptionalString(form, "extra_setting", "persona_form.extra_setting");
  const exampleLines = form.example_lines;
  if (exampleLines !== undefined) {
    if (!Array.isArray(exampleLines)) {
      addError("persona_form.example_lines must be an array when provided.");
    } else {
      exampleLines.forEach((line, index) => {
        if (!isObject(line)) {
          addError(`persona_form.example_lines[${index}] must be an object.`);
          return;
        }
        getRequiredString(line, "text", `persona_form.example_lines[${index}].text`);
        getOptionalString(line, "emotion", `persona_form.example_lines[${index}].emotion`);
      });
    }
  }
}

function validateLayout(character) {
  if (character.layout === undefined) return;
  if (!isObject(character.layout)) {
    addError("layout must be an object when provided.");
    return;
  }
  const outfits = character.layout.outfits;
  if (outfits !== undefined && !isObject(outfits)) {
    addError("layout.outfits must be an object when provided.");
    return;
  }
}

function validateVoice(character) {
  if (character.voice === undefined) return;
  if (!isObject(character.voice)) {
    addError("voice must be an object when provided.");
    return;
  }
  getOptionalString(character.voice, "provider", "voice.provider");
  getOptionalString(character.voice, "profile_id", "voice.profile_id");
  getOptionalString(character.voice, "notes", "voice.notes");
}

async function validateContextLibraries(character) {
  const libraries = character.context_libraries;
  if (libraries === undefined) return;
  if (!Array.isArray(libraries)) {
    addError("context_libraries must be an array when provided.");
    return;
  }

  const seenFolders = new Set();
  const seenAliases = new Map();
  for (const [index, library] of libraries.entries()) {
    const label = `context_libraries[${index}]`;
    if (!isObject(library)) {
      addError(`${label} must be an object.`);
      continue;
    }
    const folder = getRequiredString(library, "folder", `${label}.folder`);
    getOptionalString(library, "name", `${label}.name`);
    getOptionalString(library, "description", `${label}.description`);
    getOptionalString(library, "load_when", `${label}.load_when`);
    const aliasKeys = [];
    if (library.aliases !== undefined) {
      if (!isObject(library.aliases)) {
        addError(`${label}.aliases must be an object when provided.`);
      } else {
        for (const [fileName, rawAliases] of Object.entries(library.aliases)) {
          const cleanFileName = String(fileName || "").trim();
          if (!cleanFileName) {
            addError(`${label}.aliases contains an empty file name.`);
            continue;
          }
          aliasKeys.push(cleanFileName);
          if (!Array.isArray(rawAliases)) {
            addError(`${label}.aliases.${cleanFileName} must be an array of strings.`);
            continue;
          }
          for (const [aliasIndex, rawAlias] of rawAliases.entries()) {
            const alias = typeof rawAlias === "string" ? rawAlias.trim() : "";
            if (!alias) {
              addError(`${label}.aliases.${cleanFileName}[${aliasIndex}] must be a non-empty string.`);
              continue;
            }
            const previous = seenAliases.get(alias);
            if (previous && previous !== `${folder}/${cleanFileName}`) {
              addWarning(
                `Alias "${alias}" is shared by ${previous} and ${folder}/${cleanFileName}; both files may auto-load.`
              );
            } else {
              seenAliases.set(alias, `${folder}/${cleanFileName}`);
            }
          }
        }
      }
    }
    if (!folder) continue;
    if (
      folder === "." ||
      folder === ".." ||
      folder.toLowerCase() === "_local" ||
      folder.includes("/") ||
      folder.includes("\\") ||
      path.basename(folder) !== folder
    ) {
      addError(`${label}.folder must be a shareable direct child folder name; "_local" is reserved.`);
      continue;
    }
    if (seenFolders.has(folder)) {
      addError(`${label}.folder duplicates "${folder}".`);
      continue;
    }
    seenFolders.add(folder);

    const libraryDir = path.join(packDir, folder);
    if (!(await pathExists(libraryDir))) {
      addWarning(`${label}.folder "${folder}" does not exist yet.`);
      continue;
    }
    const entries = await fs.readdir(libraryDir, { withFileTypes: true });
    const markdownFiles = entries.filter(
      (entry) => entry.isFile() && path.extname(entry.name).toLowerCase() === ".md"
    );
    if (!markdownFiles.length) {
      addWarning(`${label}.folder "${folder}" has no direct .md files.`);
    }
    const markdownNames = new Set(
      markdownFiles.map((entry) => path.basename(entry.name, path.extname(entry.name)))
    );
    for (const aliasKey of aliasKeys) {
      if (!markdownNames.has(aliasKey)) {
        addWarning(`${label}.aliases.${aliasKey} has no matching direct .md file.`);
      }
    }
  }
}

async function pathExists(targetPath) {
  try {
    await fs.access(targetPath);
    return true;
  } catch {
    return false;
  }
}

async function readJson(filePath) {
  try {
    return JSON.parse(await fs.readFile(filePath, "utf8"));
  } catch (error) {
    addError(`Cannot read valid JSON from ${filePath}: ${error.message}`);
    return {};
  }
}

async function scanCharacterAssets(charactersDir) {
  const outfits = [];
  if (!(await pathExists(charactersDir))) {
    return outfits;
  }

  const entries = await fs.readdir(charactersDir, { withFileTypes: true });
  for (const entry of entries) {
    if (!entry.isDirectory()) {
      continue;
    }

    const outfitDir = path.join(charactersDir, entry.name);
    const files = await fs.readdir(outfitDir, { withFileTypes: true });
    const emotions = files
      .filter((file) => file.isFile())
      .filter((file) => IMAGE_EXTENSIONS.has(path.extname(file.name).toLowerCase()))
      .map((file) => path.basename(file.name, path.extname(file.name)))
      .sort((a, b) => a.localeCompare(b, "zh-Hans-CN"));

    if (emotions.length) {
      outfits.push({ id: entry.name, emotions });
    }
  }

  return outfits.sort((a, b) => a.id.localeCompare(b.id, "zh-Hans-CN"));
}

function validateAliases(character) {
  const aliases = character.emotion_aliases;
  if (aliases === undefined) {
    addWarning("emotion_aliases is missing; backend English emotion labels will have fewer fallbacks.");
    return;
  }
  if (!isObject(aliases)) {
    addError("emotion_aliases must be an object.");
    return;
  }

  for (const [key, value] of Object.entries(aliases)) {
    if (!key.trim()) {
      addError("emotion_aliases contains an empty alias key.");
    }
    if (!Array.isArray(value) || !value.length) {
      addError(`emotion_aliases.${key} must be a non-empty array.`);
      continue;
    }
    value.forEach((entry, index) => {
      if (typeof entry !== "string" || !entry.trim()) {
        addError(`emotion_aliases.${key}[${index}] must be a non-empty string.`);
      }
    });
  }
}

function normalizeEmotionKey(value) {
  return String(value || "").trim().toLowerCase().replace(/[-\s]+/g, "_");
}

function buildEmotionAliasMap(character) {
  const aliases = character.emotion_aliases;
  if (!isObject(aliases)) return new Map();
  return new Map(
    Object.entries(aliases)
      .map(([key, values]) => [
        normalizeEmotionKey(key),
        Array.isArray(values)
          ? values.map((item) => String(item || "").trim()).filter(Boolean)
          : []
      ])
      .filter(([key, values]) => key && values.length)
  );
}

function hasEmotionReference(emotion, availableEmotions, aliasMap) {
  const raw = String(emotion || "").trim();
  if (!raw || !availableEmotions.size) return true;
  if (availableEmotions.has(raw)) return true;
  const candidates = aliasMap.get(normalizeEmotionKey(raw)) || [];
  return candidates.some((candidate) => availableEmotions.has(candidate));
}

function validateClickLines(dialogue, availableEmotions, aliasMap) {
  const lines = dialogue.local_click_lines;
  if (!Array.isArray(lines) || !lines.length) {
    addError("dialogue.local_click_lines must contain at least one line.");
    return;
  }

  lines.forEach((line, index) => {
    if (!isObject(line)) {
      addError(`dialogue.local_click_lines[${index}] must be an object.`);
      return;
    }
    const text = getRequiredString(line, "text", `dialogue.local_click_lines[${index}].text`);
    const emotion = getRequiredString(line, "emotion", `dialogue.local_click_lines[${index}].emotion`);
    if (text.length > 80) {
      addWarning(`dialogue.local_click_lines[${index}].text is longer than 80 characters.`);
    }
    if (!hasEmotionReference(emotion, availableEmotions, aliasMap)) {
      addWarning(
        `dialogue.local_click_lines[${index}].emotion "${emotion}" has no matching image or emotion_aliases entry in this pack.`
      );
    }
  });
}

function validatePlayFeedback(character, availableEmotions, aliasMap) {
  const feedback = character.play_feedback;
  if (feedback === undefined) return;
  if (!isObject(feedback)) {
    addError("play_feedback must be an object when provided.");
    return;
  }

  const allowedKeys = new Set(["throw_fast", "throw_light", "wall_hit", "land"]);
  for (const key of Object.keys(feedback)) {
    if (!allowedKeys.has(key)) {
      addWarning(`play_feedback.${key} is not used by desktop_pet_next yet.`);
    }
  }

  for (const key of allowedKeys) {
    const entry = feedback[key];
    if (entry === undefined) continue;
    if (!isObject(entry)) {
      addError(`play_feedback.${key} must be an object.`);
      continue;
    }

    const emotion = getOptionalString(entry, "emotion", `play_feedback.${key}.emotion`);
    if (!hasEmotionReference(emotion, availableEmotions, aliasMap)) {
      addWarning(
        `play_feedback.${key}.emotion "${emotion}" has no matching image or emotion_aliases entry in this pack.`
      );
    }

    const bubble = entry.bubble;
    if (bubble === undefined) continue;
    if (!isObject(bubble)) {
      addError(`play_feedback.${key}.bubble must be an object when provided.`);
      continue;
    }
    getOptionalString(bubble, "text", `play_feedback.${key}.bubble.text`);
    const durationMs = bubble.duration_ms;
    if (
      durationMs !== undefined &&
      (!Number.isFinite(Number(durationMs)) || Number(durationMs) < 0)
    ) {
      addError(`play_feedback.${key}.bubble.duration_ms must be a non-negative number.`);
    }
  }
}

function validateCare(character, availableEmotions, aliasMap) {
  const care = character.care;
  if (care === undefined) return;
  if (!isObject(care)) {
    addError("care must be an object when provided.");
    return;
  }

  if (care.enabled !== undefined && typeof care.enabled !== "boolean") {
    addError("care.enabled must be a boolean when provided.");
  }
  validateNumber(care.initial_coins, "care.initial_coins", { min: 0 });
  validateNumber(care.initial_hunger, "care.initial_hunger", { min: 0, max: 100 });
  validateNumber(care.initial_energy, "care.initial_energy", { min: 0, max: 100 });
  validateNumber(care.initial_affection, "care.initial_affection", { min: 0, max: 100 });
  validateCareDecay(care.decay);
  validateCareWork(care.work, availableEmotions, aliasMap);
  validateCareAllowance(care.allowance, availableEmotions, aliasMap);
  validateCarePreferences(care.care_preferences);

  const items = care.shop_items;
  if (items === undefined) return;
  if (!Array.isArray(items)) {
    addError("care.shop_items must be an array when provided.");
    return;
  }

  const seenIds = new Set();
  items.forEach((item, index) => {
    const label = `care.shop_items[${index}]`;
    if (!isObject(item)) {
      addError(`${label} must be an object.`);
      return;
    }

    const id = getRequiredString(item, "id", `${label}.id`);
    getRequiredString(item, "name", `${label}.name`);
    getOptionalString(item, "description", `${label}.description`);
    validateNumber(item.price, `${label}.price`, { min: 0 });
    if (id) {
      if (seenIds.has(id)) {
        addError(`${label}.id "${id}" is duplicated.`);
      }
      seenIds.add(id);
    }

    const category = item.category;
    if (category !== undefined) {
      if (typeof category !== "string" || !VALID_ITEM_CATEGORIES.has(category)) {
        addError(`${label}.category must be one of: ${[...VALID_ITEM_CATEGORIES].join(", ")}.`);
      }
    }

    const preferenceTagsRaw = item.preference_tags;
    if (preferenceTagsRaw !== undefined) {
      if (!Array.isArray(preferenceTagsRaw)) {
        addError(`${label}.preference_tags must be an array when provided.`);
      } else {
        preferenceTagsRaw.forEach((tag, ti) => {
          if (typeof tag !== "string" || !tag.trim()) {
            addError(`${label}.preference_tags[${ti}] must be a non-empty string.`);
          }
        });
      }
    }

    const usableIn = item.usable_in;
    if (usableIn !== undefined) {
      if (!Array.isArray(usableIn) || !usableIn.length) {
        addError(`${label}.usable_in must be a non-empty array when provided.`);
      } else {
        usableIn.forEach((entry, ui) => {
          if (!VALID_USABLE_IN.has(String(entry || "").toLowerCase())) {
            addError(`${label}.usable_in[${ui}] must be one of: ${[...VALID_USABLE_IN].join(", ")}.`);
          }
        });
      }
    }

    const feedbackTone = item.feedback_tone;
    if (feedbackTone !== undefined && typeof feedbackTone !== "string") {
      addError(`${label}.feedback_tone must be a string when provided.`);
    }

    const effects = item.effects;
    if (effects !== undefined) {
      if (!isObject(effects)) {
        addError(`${label}.effects must be an object when provided.`);
      } else {
        // Additive effects
        validateNumber(effects.hunger, `${label}.effects.hunger`, { min: -100, max: 100 });
        validateNumber(effects.energy, `${label}.effects.energy`, { min: -100, max: 100 });
        validateNumber(effects.affection, `${label}.effects.affection`, { min: -10, max: 10 });
        // Direct-set effects (trick/potion items)
        validateNumber(effects.hunger_set, `${label}.effects.hunger_set`, { min: 0, max: 100 });
        validateNumber(effects.energy_set, `${label}.effects.energy_set`, { min: 0, max: 100 });
        validateNumber(effects.affection_set, `${label}.effects.affection_set`, { min: 0, max: 100 });
        // Boolean-flag effects
        for (const flag of ["hunger_energy_swap", "random_vitals", "random_affection"]) {
          if (effects[flag] !== undefined && effects[flag] !== true) {
            addError(`${label}.effects.${flag} must be true when provided.`);
          }
        }
      }
    }

    const feedback = item.feedback;
    if (feedback === undefined) return;
    if (!isObject(feedback)) {
      addError(`${label}.feedback must be an object when provided.`);
      return;
    }

    const emotion = getOptionalString(feedback, "emotion", `${label}.feedback.emotion`);
    if (!hasEmotionReference(emotion, availableEmotions, aliasMap)) {
      addWarning(
        `${label}.feedback.emotion "${emotion}" has no matching image or emotion_aliases entry in this pack.`
      );
    }

    const bubble = feedback.bubble;
    if (bubble === undefined) return;
    if (!isObject(bubble)) {
      addError(`${label}.feedback.bubble must be an object when provided.`);
      return;
    }
    getOptionalString(bubble, "text", `${label}.feedback.bubble.text`);
    validateNumber(bubble.duration_ms, `${label}.feedback.bubble.duration_ms`, { min: 0 });
  });
}

function validateQQDelivery(character, availableEmotions, aliasMap) {
  const delivery = character.qq_delivery;
  if (delivery === undefined) return;
  if (!isObject(delivery)) {
    addError("qq_delivery must be an object when provided.");
    return;
  }

  const emotionImages = delivery.emotion_images;
  if (emotionImages !== undefined) {
    if (!isObject(emotionImages)) {
      addError("qq_delivery.emotion_images must be an object when provided.");
    } else {
      if (emotionImages.enabled !== undefined && typeof emotionImages.enabled !== "boolean") {
        addError("qq_delivery.emotion_images.enabled must be a boolean when provided.");
      }
      validateNumber(
        emotionImages.min_interval_seconds,
        "qq_delivery.emotion_images.min_interval_seconds",
        { min: 0, max: 3600 }
      );
    }
  }

  const emotionMfaces = delivery.emotion_mfaces;
  if (emotionMfaces === undefined) return;
  if (!isObject(emotionMfaces)) {
    addError("qq_delivery.emotion_mfaces must be an object when provided.");
    return;
  }

  if (emotionMfaces.enabled !== undefined && typeof emotionMfaces.enabled !== "boolean") {
    addError("qq_delivery.emotion_mfaces.enabled must be a boolean when provided.");
  }
  validateNumber(
    emotionMfaces.min_interval_seconds,
    "qq_delivery.emotion_mfaces.min_interval_seconds",
    { min: 0, max: 3600 }
  );

  const mapping = emotionMfaces.map;
  if (mapping === undefined) {
    if (emotionMfaces.enabled === true) {
      addWarning("qq_delivery.emotion_mfaces is enabled but map is missing.");
    }
    return;
  }
  if (!isObject(mapping)) {
    addError("qq_delivery.emotion_mfaces.map must be an object when provided.");
    return;
  }
  if (emotionMfaces.enabled === true && !Object.keys(mapping).length) {
    addWarning("qq_delivery.emotion_mfaces is enabled but map is empty.");
  }

  for (const [emotion, mface] of Object.entries(mapping)) {
    const label = `qq_delivery.emotion_mfaces.map.${emotion || "(empty)"}`;
    if (!emotion.trim()) {
      addError("qq_delivery.emotion_mfaces.map contains an empty emotion key.");
    }
    if (!hasEmotionReference(emotion, availableEmotions, aliasMap)) {
      addWarning(`${label} has no matching image or emotion_aliases entry in this pack.`);
    }
    if (!isObject(mface)) {
      addError(`${label} must be an object.`);
      continue;
    }

    const packageNumber = Number(mface.emoji_package_id);
    if (!Number.isInteger(packageNumber) || packageNumber < 0) {
      addError(`${label}.emoji_package_id must be a non-negative integer.`);
    }
    getRequiredString(mface, "emoji_id", `${label}.emoji_id`);
    getRequiredString(mface, "key", `${label}.key`);
    getRequiredString(mface, "summary", `${label}.summary`);
  }
}

function validateCarePreferences(prefs) {
  if (prefs === undefined) return;
  if (!isObject(prefs)) {
    addError("care.care_preferences must be an object when provided.");
    return;
  }
  for (const key of ["favorite_tags", "disliked_tags", "offering_tags", "default_affection_bonus_tags"]) {
    const val = prefs[key];
    if (val === undefined) continue;
    if (!Array.isArray(val)) {
      addError(`care.care_preferences.${key} must be an array when provided.`);
      continue;
    }
    val.forEach((tag, ti) => {
      if (typeof tag !== "string" || !tag.trim()) {
        addError(`care.care_preferences.${key}[${ti}] must be a non-empty string.`);
      }
    });
  }
}

function validateCareDecay(decay) {
  if (decay === undefined) return;
  if (!isObject(decay)) {
    addError("care.decay must be an object when provided.");
    return;
  }
  validateNumber(decay.hunger_per_hour, "care.decay.hunger_per_hour", { min: 0, max: 100 });
  validateNumber(decay.energy_per_reply, "care.decay.energy_per_reply", { min: 0, max: 20 });
  validateNumber(decay.energy_per_proactive, "care.decay.energy_per_proactive", { min: 0, max: 20 });
}

function validateCareWork(work, availableEmotions, aliasMap) {
  if (work === undefined) return;
  if (!isObject(work)) {
    addError("care.work must be an object when provided.");
    return;
  }
  if (work.enabled !== undefined && typeof work.enabled !== "boolean") {
    addError("care.work.enabled must be a boolean when provided.");
  }
  validateNumber(work.duration_seconds, "care.work.duration_seconds", { min: 1, max: 3600 });
  validateNumber(work.reward_coins_min, "care.work.reward_coins_min", { min: 0 });
  validateNumber(work.reward_coins_max, "care.work.reward_coins_max", { min: 0 });
  validateNumber(work.min_hunger, "care.work.min_hunger", { min: 0, max: 100 });
  validateNumber(work.min_energy, "care.work.min_energy", { min: 0, max: 100 });
  validateNumber(work.hunger_cost, "care.work.hunger_cost", { min: 0, max: 100 });
  validateNumber(work.energy_cost, "care.work.energy_cost", { min: 0, max: 100 });
  if (
    Number.isFinite(Number(work.reward_coins_min)) &&
    Number.isFinite(Number(work.reward_coins_max)) &&
    Number(work.reward_coins_min) > Number(work.reward_coins_max)
  ) {
    addError("care.work.reward_coins_min must be less than or equal to reward_coins_max.");
  }
  validateCareFeedback(work.start_feedback, "care.work.start_feedback", availableEmotions, aliasMap);
  validateCareFeedback(work.complete_feedback, "care.work.complete_feedback", availableEmotions, aliasMap);
}

function validateCareAllowance(allowance, availableEmotions, aliasMap) {
  if (allowance === undefined) return;
  if (!isObject(allowance)) {
    addError("care.allowance must be an object when provided.");
    return;
  }
  if (allowance.enabled !== undefined && typeof allowance.enabled !== "boolean") {
    addError("care.allowance.enabled must be a boolean when provided.");
  }
  validateNumber(allowance.coins, "care.allowance.coins", { min: 1 });
  validateNumber(allowance.cooldown_seconds, "care.allowance.cooldown_seconds", { min: 0, max: 86400 });
  validateNumber(allowance.max_coins, "care.allowance.max_coins", { min: 1 });
  validateCareFeedback(allowance.feedback, "care.allowance.feedback", availableEmotions, aliasMap);
}

function validateCareFeedback(feedback, label, availableEmotions, aliasMap) {
  if (feedback === undefined) return;
  if (!isObject(feedback)) {
    addError(`${label} must be an object when provided.`);
    return;
  }
  const emotion = getOptionalString(feedback, "emotion", `${label}.emotion`);
  if (!hasEmotionReference(emotion, availableEmotions, aliasMap)) {
    addWarning(`${label}.emotion "${emotion}" has no matching image or emotion_aliases entry in this pack.`);
  }
  const bubble = feedback.bubble;
  if (bubble === undefined) return;
  if (!isObject(bubble)) {
    addError(`${label}.bubble must be an object when provided.`);
    return;
  }
  getOptionalString(bubble, "text", `${label}.bubble.text`);
  validateNumber(bubble.duration_ms, `${label}.bubble.duration_ms`, { min: 0 });
}

function validateNumber(value, label, { min = -Infinity, max = Infinity } = {}) {
  if (value === undefined) return;
  const number = Number(value);
  if (!Number.isFinite(number)) {
    addError(`${label} must be a finite number.`);
    return;
  }
  if (number < min || number > max) {
    addError(`${label} must be between ${min} and ${max}.`);
  }
}

async function validatePack() {
  const characterPath = path.join(packDir, "character.json");
  const tomlPath = path.join(packDir, "character.toml");
  const personaPath = path.join(packDir, "persona.md");

  if (!(await pathExists(packDir))) {
    addError(`Pack folder does not exist: ${packDir}`);
    return null;
  }
  if (!(await pathExists(characterPath))) {
    addError(`Missing character.json in ${packDir}`);
    return null;
  }
  if (!(await pathExists(tomlPath))) {
    addWarning("character.toml is missing; creators lose the friendlier authoring copy.");
  }
  if (!(await pathExists(personaPath))) {
    addWarning("persona.md is missing; later backend persona extraction will have no source text.");
  }

  const character = await readJson(characterPath);

  const schemaVersion = getRequiredString(character, "schema_version", "schema_version");
  if (schemaVersion && !SUPPORTED_SCHEMA_VERSIONS.has(schemaVersion)) {
    addError(`schema_version must be one of: ${[...SUPPORTED_SCHEMA_VERSIONS].join(", ")}.`);
  }

  const identity = getObject(character, "identity", "identity");
  const appearance = getObject(character, "appearance", "appearance");
  const dialogue = getObject(character, "dialogue", "dialogue");
  const assets = isObject(character.assets) ? character.assets : {};

  const id = getRequiredString(identity, "id", "identity.id");
  const name = getRequiredString(identity, "name", "identity.name");
  getRequiredString(identity, "app_name", "identity.app_name");
  getRequiredString(identity, "user_title", "identity.user_title");
  getOptionalString(identity, "self_reference", "identity.self_reference");
  getOptionalString(identity, "relationship", "identity.relationship");

  const defaultOutfit = getRequiredString(
    appearance,
    "default_outfit",
    "appearance.default_outfit"
  );
  const defaultEmotion = getRequiredString(
    appearance,
    "default_emotion",
    "appearance.default_emotion"
  );
  getOptionalString(appearance, "music_emotion", "appearance.music_emotion", defaultEmotion);

  const requiredEmotions = getStringArray(
    appearance,
    "required_emotions",
    "appearance.required_emotions"
  );
  if (!requiredEmotions.length && defaultEmotion) {
    addWarning("appearance.required_emotions is empty; default emotion should normally be listed.");
  } else if (defaultEmotion && !requiredEmotions.includes(defaultEmotion)) {
    addWarning("appearance.required_emotions should include appearance.default_emotion.");
  }

  getStringArray(appearance, "recommended_emotions", "appearance.recommended_emotions");
  getOptionalString(dialogue, "input_placeholder", "dialogue.input_placeholder");
  getOptionalString(dialogue, "session_display_title", "dialogue.session_display_title");
  getOptionalString(dialogue, "tts_test_text", "dialogue.tts_test_text");
  getOptionalString(dialogue, "proactive_wake_prompt", "dialogue.proactive_wake_prompt");
  validateAliases(character);
  validatePersonaForm(character);
  await validateContextLibraries(character);
  validateLayout(character);
  validateVoice(character);

  const assetRoot = getOptionalString(assets, "asset_root", "assets.asset_root", "assets");
  const charactersDir = path.join(packDir, assetRoot, "characters");
  const outfits = await scanCharacterAssets(charactersDir);
  const availableEmotions = new Set(outfits.flatMap((outfit) => outfit.emotions));
  const aliasMap = buildEmotionAliasMap(character);

  if (!outfits.length) {
    addWarning(
      "No images found under assets/characters/<outfit>/<emotion>.png; the dev app will use bundled fallback art."
    );
  } else {
    const defaultOutfitEntry = outfits.find((outfit) => outfit.id === defaultOutfit);
    if (!defaultOutfitEntry) {
      addError(`Default outfit "${defaultOutfit}" has no folder under ${charactersDir}.`);
    } else if (!defaultOutfitEntry.emotions.includes(defaultEmotion)) {
      addError(
        `Default emotion "${defaultEmotion}" has no image under outfit "${defaultOutfit}".`
      );
    }

    for (const emotion of requiredEmotions) {
      if (!availableEmotions.has(emotion)) {
        addError(`Required emotion "${emotion}" has no image in this pack.`);
      }
    }
  }

  validateClickLines(dialogue, availableEmotions, aliasMap);
  validatePlayFeedback(character, availableEmotions, aliasMap);
  validateCare(character, availableEmotions, aliasMap);
  validateQQDelivery(character, availableEmotions, aliasMap);

  return {
    id,
    name,
    assetRoot,
    outfits,
  };
}

function printReport(result) {
  console.log("Akane Creator Kit character pack check");
  console.log(`Pack: ${packDir}`);

  if (result) {
    console.log(`Character: ${result.name || "(missing name)"} (${result.id || "missing id"})`);
    console.log(`Asset root: ${result.assetRoot}`);
    if (result.outfits.length) {
      console.log("Outfits:");
      for (const outfit of result.outfits) {
        console.log(`- ${outfit.id}: ${outfit.emotions.length} emotion image(s)`);
      }
    } else {
      console.log("Outfits: none found");
    }
  }

  if (warnings.length) {
    console.log("");
    console.log("Warnings:");
    warnings.forEach((warning) => console.log(`- ${warning}`));
  }

  if (errors.length) {
    console.log("");
    console.log("Errors:");
    errors.forEach((error) => console.log(`- ${error}`));
    process.exitCode = 1;
    return;
  }

  console.log("");
  console.log("Result: OK");
}

const result = await validatePack();
printReport(result);
