#!/usr/bin/env node

import { promises as fs } from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const CURRENT_SCHEMA_VERSION = "akane.character.v0.2";
const SUPPORTED_SCHEMA_VERSIONS = new Set(["akane.character.v0.1", CURRENT_SCHEMA_VERSION]);
const IMAGE_EXTENSIONS = new Set([".png", ".jpg", ".jpeg", ".webp"]);

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const kitRoot = path.resolve(scriptDir, "..");
const packArg = process.argv[2];
const packDir = packArg
  ? path.resolve(process.cwd(), packArg)
  : path.join(kitRoot, "characters", "akane_sample");

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

function validateClickLines(dialogue, availableEmotions) {
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
    if (availableEmotions.size && emotion && !availableEmotions.has(emotion)) {
      addWarning(
        `dialogue.local_click_lines[${index}].emotion "${emotion}" has no matching image in this pack.`
      );
    }
  });
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
  validateLayout(character);
  validateVoice(character);

  const assetRoot = getOptionalString(assets, "asset_root", "assets.asset_root", "assets");
  const charactersDir = path.join(packDir, assetRoot, "characters");
  const outfits = await scanCharacterAssets(charactersDir);
  const availableEmotions = new Set(outfits.flatMap((outfit) => outfit.emotions));

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

  validateClickLines(dialogue, availableEmotions);

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
