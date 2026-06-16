#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { promises as fs } from "node:fs";
import path from "node:path";
import process from "node:process";
import { createInterface } from "node:readline/promises";
import { fileURLToPath } from "node:url";

const SCHEMA_VERSION = "akane.character.v0.2";
const IMAGE_EXTENSIONS = new Set([".png", ".jpg", ".jpeg", ".webp"]);
const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const kitRoot = path.resolve(scriptDir, "..");
const validatorPath = path.join(scriptDir, "validate-character-pack.mjs");
const exporterPath = path.join(scriptDir, "export-character-pack.mjs");
const DEFAULT_CHARACTERS_DIR = path.join(kitRoot, "characters");

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    printUsage();
    return;
  }

  const answers = await collectOptions(options);
  const packId = sanitizePackId(answers.id);
  if (!packId) {
    throw new Error("Pack id is required. Use --id my_character.");
  }

  const charactersDir = path.resolve(process.cwd(), answers.to || DEFAULT_CHARACTERS_DIR);
  const targetDir = resolveInside(charactersDir, packId);
  if ((await pathExists(targetDir)) && !answers.force) {
    throw new Error(`Pack already exists: ${targetDir}. Re-run with --force to overwrite it.`);
  }

  const imageDraft = answers.fromImages
    ? await buildImageDraft({
        sourceDir: path.resolve(process.cwd(), answers.fromImages),
        defaultOutfit: answers.outfit,
        preferredEmotion: answers.emotion,
        preferredMusicEmotion: answers.musicEmotion
      })
    : null;

  const pack = buildPack({
    id: packId,
    name: answers.name,
    appName: answers.appName,
    userTitle: answers.userTitle,
    outfit: imageDraft?.defaultOutfit || answers.outfit,
    emotion: imageDraft?.defaultEmotion || answers.emotion,
    musicEmotion: imageDraft?.musicEmotion || answers.musicEmotion,
    availableEmotions: imageDraft?.allEmotions || []
  });

  if (answers.dryRun) {
    printPreview({ pack, targetDir, imageDraft });
    return;
  }

  if (await pathExists(targetDir)) {
    assertRemovableDestination(charactersDir, targetDir);
    await fs.rm(targetDir, { recursive: true, force: true });
  }

  await writePack({
    targetDir,
    pack,
    outfit: pack.appearance.default_outfit,
    emotion: pack.appearance.default_emotion,
    imageDraft
  });
  runValidator(targetDir);
  if (answers.exportZip) {
    runExporter(targetDir);
  }
  printResult({ pack, targetDir, imageDraft, exportZip: answers.exportZip });
}

function parseArgs(args) {
  const options = {
    id: "",
    name: "",
    appName: "",
    userTitle: "",
    outfit: "",
    emotion: "",
    musicEmotion: "",
    fromImages: "",
    to: "",
    force: false,
    exportZip: false,
    dryRun: false,
    help: false
  };

  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === "--force") {
      options.force = true;
      continue;
    }
    if (arg === "--dry-run") {
      options.dryRun = true;
      continue;
    }
    if (arg === "--export") {
      options.exportZip = true;
      continue;
    }
    if (arg === "--help" || arg === "-h") {
      options.help = true;
      continue;
    }

    const valueArgs = {
      "--id": "id",
      "--name": "name",
      "--app-name": "appName",
      "--user-title": "userTitle",
      "--outfit": "outfit",
      "--emotion": "emotion",
      "--music-emotion": "musicEmotion",
      "--from-images": "fromImages",
      "--to": "to"
    };
    const key = valueArgs[arg];
    if (key) {
      options[key] = args[index + 1] || "";
      index += 1;
      continue;
    }

    throw new Error(`Unknown argument: ${arg}`);
  }

  return options;
}

async function collectOptions(options) {
  const defaults = normalizeDefaults(options);
  const missingRequired = !defaults.id || !defaults.name;
  if (!missingRequired) {
    return defaults;
  }
  if (!process.stdin.isTTY || !process.stdout.isTTY) {
    printUsage();
    throw new Error("Missing required options. Use --id and --name, or run in a terminal.");
  }

  const rl = createInterface({
    input: process.stdin,
    output: process.stdout
  });

  try {
    const id = defaults.id || (await ask(rl, "Pack id / folder name", "my_character"));
    const suggestedName = defaults.name || titleFromId(id);
    const name = defaults.name || (await ask(rl, "Character name", suggestedName));
    const appName = options.appName || (await ask(rl, "App display name", `${name} Pet`));
    const userTitle = options.userTitle || (await ask(rl, "User title", "主人"));
    const outfit = options.outfit || (await ask(rl, "Default outfit folder", "default"));
    if (options.fromImages) {
      return normalizeDefaults({
        ...options,
        id,
        name,
        appName,
        userTitle,
        outfit
      });
    }
    return normalizeDefaults({
      ...options,
      id,
      name,
      appName,
      userTitle,
      outfit,
      emotion: options.emotion || (await ask(rl, "Default emotion image name", "normal")),
      musicEmotion: options.musicEmotion || (await ask(rl, "Music emotion image name", "listening"))
    });
  } finally {
    rl.close();
  }
}

function normalizeDefaults(options) {
  const id = sanitizePackId(options.id);
  const name = String(options.name || titleFromId(id) || "").trim();
  const appName = String(options.appName || (name ? `${name} Pet` : "")).trim();
  const hasImageSource = Boolean(options.fromImages);
  const outfit = sanitizeAssetId(options.outfit || "default");
  const emotion = options.emotion ? sanitizeAssetId(options.emotion) : hasImageSource ? "" : "normal";
  const musicEmotion = options.musicEmotion ? sanitizeAssetId(options.musicEmotion) : hasImageSource ? "" : "listening";

  return {
    ...options,
    id,
    name,
    appName,
    userTitle: String(options.userTitle || "主人").trim(),
    outfit,
    emotion,
    musicEmotion,
    fromImages: String(options.fromImages || "").trim()
  };
}

async function ask(rl, label, fallback) {
  const answer = await rl.question(`${label} (${fallback}): `);
  return answer.trim() || fallback;
}

function buildPack({ id, name, appName, userTitle, outfit, emotion, musicEmotion, availableEmotions = [] }) {
  const recommended = buildRecommendedEmotions({
    availableEmotions,
    emotion,
    musicEmotion
  });
  return {
    schema_version: SCHEMA_VERSION,
    identity: {
      id,
      name,
      app_name: appName,
      self_reference: "我",
      user_title: userTitle,
      relationship: `住在桌面边上的 ${name}，会按自己的性格陪伴和回应 ${userTitle}。`
    },
    persona_form: {
      personality_keywords: [],
      speaking_style: "短句自然，不写解释性设定说明。",
      catchphrases: [],
      boundaries: "不要把自己说成通用客服。",
      proactive_style: `${userTitle}暂时没有说话时，按角色风格轻轻搭一句话。`,
      example_lines: buildLocalClickLines({ emotion, availableEmotions }).slice(0, 1),
      extra_setting: ""
    },
    context_libraries: [],
    appearance: {
      default_outfit: outfit,
      default_emotion: emotion,
      music_emotion: musicEmotion,
      required_emotions: [emotion],
      recommended_emotions: recommended
    },
    dialogue: {
      input_placeholder: `和 ${name} 说点什么……`,
      session_display_title: `${name} 桌宠对话`,
      tts_test_text: `${name}：语音播放测试。`,
      proactive_wake_prompt: `${userTitle}暂时没有说话。你像坐在旁边陪伴一样，轻轻搭一句自然的话。桌面线索只当背景，不要刻意围绕窗口标题发挥。`,
      local_click_lines: buildLocalClickLines({ emotion, availableEmotions })
    },
    emotion_aliases: buildEmotionAliases({ emotion, musicEmotion, availableEmotions }),
    layout: {
      outfits: {
        [outfit]: {
          window: { width: 340, height: 560 },
          portrait: { scale: 1, offset_x: 0, offset_y: 0, fit: "contain", anchor: "bottom_center" },
          bubble: { anchor_x: 0.5, anchor_y: 0.12, max_width: 300 }
        }
      }
    },
    voice: {
      provider: "",
      profile_id: "",
      notes: ""
    },
    assets: {
      runtime_source: "character pack assets, with desktop_pet_next bundled fallback",
      asset_root: "assets",
      bundled_outfit: outfit,
      portrait_glob: "assets/characters/<outfit>/<emotion>.png"
    }
  };
}

function buildRecommendedEmotions({ availableEmotions, emotion, musicEmotion }) {
  const detected = uniqueStrings(availableEmotions);
  if (!detected.length) {
    return uniqueStrings(["thinking", "happy", "confused", musicEmotion]);
  }
  return uniqueStrings([
    findEmotion(detected, ["思考中", "thinking", "think"]),
    findEmotion(detected, ["开心", "happy", "joy"]),
    findEmotion(detected, ["困惑", "confused", "question"]),
    musicEmotion,
    ...detected.filter((item) => item !== emotion)
  ]).filter(Boolean);
}

function buildLocalClickLines({ emotion, availableEmotions }) {
  const happy = findEmotion(availableEmotions, ["开心", "happy", "joy", "得意", "proud"]);
  const thinking = findEmotion(availableEmotions, ["思考中", "thinking", "think", "困惑", "confused"]);
  return [
    { text: "我在哦。", emotion },
    { text: "有什么新计划吗？", emotion: thinking || emotion },
    { text: "今天也一起稳稳推进吧。", emotion: happy || emotion }
  ];
}

function buildEmotionAliases({ emotion, musicEmotion, availableEmotions = [] }) {
  const normal = findEmotion(availableEmotions, ["正常", "normal", "默认", "default", "idle", "平静"]) || emotion;
  const thinking = findEmotion(availableEmotions, ["思考中", "thinking", "think", "困惑", "confused"]) || normal;
  const happy = findEmotion(availableEmotions, ["开心", "happy", "joy", "得意", "proud"]) || normal;
  const confused = findEmotion(availableEmotions, ["困惑", "confused", "question", "思考中", "thinking"]) || normal;
  const music = findEmotion(availableEmotions, [musicEmotion, "听歌中", "listening", "music", "开心", "happy"]) || musicEmotion || normal;
  return {
    normal: uniqueStrings([normal, "normal"]),
    idle: uniqueStrings([normal, "normal"]),
    thinking: uniqueStrings([thinking, normal, "thinking", "normal"]),
    think: uniqueStrings([thinking, normal, "thinking", "normal"]),
    happy: uniqueStrings([happy, normal, "happy", "normal"]),
    joy: uniqueStrings([happy, normal, "happy", "normal"]),
    confused: uniqueStrings([confused, thinking, normal, "confused", "normal"]),
    question: uniqueStrings([confused, thinking, normal, "confused", "normal"]),
    music: uniqueStrings([music, happy, normal, "music", "normal"]),
    listening: uniqueStrings([music, normal, "listening", "normal"])
  };
}

async function writePack({ targetDir, pack, outfit, emotion, imageDraft = null }) {
  await fs.mkdir(path.join(targetDir, "assets", "characters", outfit), { recursive: true });
  await fs.writeFile(path.join(targetDir, "character.json"), `${JSON.stringify(pack, null, 2)}\n`);
  await fs.writeFile(path.join(targetDir, "character.toml"), buildToml(pack));
  await fs.writeFile(path.join(targetDir, "persona.md"), buildPersona(pack, { imageDraft }));
  await fs.writeFile(path.join(targetDir, "assets", "README.md"), buildAssetsReadme());
  await fs.writeFile(
    path.join(targetDir, "assets", "characters", "README.md"),
    buildCharactersReadme()
  );
  if (imageDraft) {
    await copyImageDraft(targetDir, imageDraft);
    for (const draftOutfit of imageDraft.outfits) {
      await fs.writeFile(
        path.join(targetDir, "assets", "characters", draftOutfit.id, "README.md"),
        buildOutfitReadme({
          outfit: draftOutfit.id,
          emotion: draftOutfit.id === imageDraft.defaultOutfit ? imageDraft.defaultEmotion : draftOutfit.images[0]?.emotion || emotion,
          files: draftOutfit.images.map((image) => image.targetName)
        })
      );
    }
    return;
  }
  await fs.writeFile(path.join(targetDir, "assets", "characters", outfit, "README.md"), buildOutfitReadme({ outfit, emotion }));
}

async function buildImageDraft({ sourceDir, defaultOutfit, preferredEmotion, preferredMusicEmotion }) {
  const stat = await fs.stat(sourceDir).catch(() => null);
  if (!stat?.isDirectory()) {
    throw new Error(`Image source folder does not exist: ${sourceDir}`);
  }

  const outfits = [];
  const usedOutfits = new Set();
  const rootImages = await collectImageFiles(sourceDir);
  if (rootImages.length) {
    outfits.push(buildDraftOutfit(uniqueAssetId(defaultOutfit, usedOutfits), rootImages));
  }

  const entries = await fs.readdir(sourceDir, { withFileTypes: true });
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const outfitDir = path.join(sourceDir, entry.name);
    const images = await collectImageFiles(outfitDir);
    if (!images.length) continue;
    outfits.push(buildDraftOutfit(uniqueAssetId(entry.name, usedOutfits), images));
  }

  if (!outfits.length) {
    throw new Error(`No supported images found in ${sourceDir}. Supported: png, jpg, jpeg, webp.`);
  }

  const requestedOutfit = sanitizeAssetId(defaultOutfit);
  const defaultOutfitEntry = outfits.find((outfit) => outfit.id === requestedOutfit) || outfits[0];
  const defaultEmotions = defaultOutfitEntry.images.map((image) => image.emotion);
  const allEmotions = uniqueStrings(outfits.flatMap((outfit) => outfit.images.map((image) => image.emotion)));
  const defaultEmotion =
    findEmotion(defaultEmotions, [preferredEmotion, "正常", "normal", "默认", "default", "idle", "平静"]) ||
    defaultEmotions[0];
  const musicEmotion =
    findEmotion(defaultEmotions, [preferredMusicEmotion, "听歌中", "listening", "music", "开心", "happy"]) ||
    preferredMusicEmotion ||
    findEmotion(defaultEmotions, ["开心", "happy"]) ||
    defaultEmotion;

  return {
    sourceDir,
    defaultOutfit: defaultOutfitEntry.id,
    defaultEmotion,
    musicEmotion,
    allEmotions,
    outfits
  };
}

async function collectImageFiles(sourceDir) {
  const entries = await fs.readdir(sourceDir, { withFileTypes: true });
  return entries
    .filter((entry) => entry.isFile())
    .filter((entry) => IMAGE_EXTENSIONS.has(path.extname(entry.name).toLowerCase()))
    .map((entry) => ({
      name: entry.name,
      sourcePath: path.join(sourceDir, entry.name),
      extension: path.extname(entry.name).toLowerCase()
    }))
    .sort((a, b) => a.name.localeCompare(b.name, "zh-Hans-CN"));
}

function buildDraftOutfit(rawOutfitId, images) {
  const used = new Set();
  return {
    id: sanitizeAssetId(rawOutfitId),
    images: images.map((image) => {
      const rawEmotion = path.basename(image.name, path.extname(image.name));
      const emotion = uniqueAssetId(sanitizeAssetId(rawEmotion), used);
      return {
        ...image,
        emotion,
        targetName: `${emotion}${image.extension}`
      };
    })
  };
}

async function copyImageDraft(targetDir, imageDraft) {
  for (const outfit of imageDraft.outfits) {
    const outfitDir = path.join(targetDir, "assets", "characters", outfit.id);
    await fs.mkdir(outfitDir, { recursive: true });
    for (const image of outfit.images) {
      await fs.copyFile(image.sourcePath, path.join(outfitDir, image.targetName));
    }
  }
}

function buildToml(pack) {
  return [
    `schema_version = ${tomlString(pack.schema_version)}`,
    "",
    "[identity]",
    `id = ${tomlString(pack.identity.id)}`,
    `name = ${tomlString(pack.identity.name)}`,
    `app_name = ${tomlString(pack.identity.app_name)}`,
    `self_reference = ${tomlString(pack.identity.self_reference)}`,
    `user_title = ${tomlString(pack.identity.user_title)}`,
    `relationship = ${tomlString(pack.identity.relationship)}`,
    "",
    "[persona_form]",
    `personality_keywords = ${tomlArray(pack.persona_form.personality_keywords)}`,
    `speaking_style = ${tomlString(pack.persona_form.speaking_style)}`,
    `catchphrases = ${tomlArray(pack.persona_form.catchphrases)}`,
    `boundaries = ${tomlString(pack.persona_form.boundaries)}`,
    `proactive_style = ${tomlString(pack.persona_form.proactive_style)}`,
    `extra_setting = ${tomlString(pack.persona_form.extra_setting)}`,
    "",
    ...pack.persona_form.example_lines.flatMap((line) => [
      "[[persona_form.example_lines]]",
      `text = ${tomlString(line.text)}`,
      `emotion = ${tomlString(line.emotion)}`,
      ""
    ]),
    "[appearance]",
    `default_outfit = ${tomlString(pack.appearance.default_outfit)}`,
    `default_emotion = ${tomlString(pack.appearance.default_emotion)}`,
    `music_emotion = ${tomlString(pack.appearance.music_emotion)}`,
    `required_emotions = ${tomlArray(pack.appearance.required_emotions)}`,
    `recommended_emotions = ${tomlArray(pack.appearance.recommended_emotions)}`,
    "",
    "[dialogue]",
    `input_placeholder = ${tomlString(pack.dialogue.input_placeholder)}`,
    `session_display_title = ${tomlString(pack.dialogue.session_display_title)}`,
    `tts_test_text = ${tomlString(pack.dialogue.tts_test_text)}`,
    `proactive_wake_prompt = ${tomlString(pack.dialogue.proactive_wake_prompt)}`,
    "",
    ...pack.dialogue.local_click_lines.flatMap((line) => [
      "[[dialogue.local_click_lines]]",
      `text = ${tomlString(line.text)}`,
      `emotion = ${tomlString(line.emotion)}`,
      ""
    ]),
    "[emotion_aliases]",
    ...Object.entries(pack.emotion_aliases).map(
      ([key, values]) => `${key} = ${tomlArray(values)}`
    ),
    "",
    "[assets]",
    `runtime_source = ${tomlString(pack.assets.runtime_source)}`,
    `asset_root = ${tomlString(pack.assets.asset_root)}`,
    `bundled_outfit = ${tomlString(pack.assets.bundled_outfit)}`,
    `portrait_glob = ${tomlString(pack.assets.portrait_glob)}`,
    ""
  ].join("\n");
}

function buildPersona(pack, { imageDraft = null } = {}) {
  const draftLine = imageDraft
    ? `This draft was generated from ${imageDraft.allEmotions.length} expression image(s). Replace the notes below with the character's real voice before paid delivery.`
    : "The desktop-pet backend reads this file for the selected character pack. Keep the notes concise and usable as prompt reference.";
  return [
    `# ${pack.identity.name} Persona`,
    "",
    "## Voice",
    "",
    `Write how ${pack.identity.name} speaks, what tone they use, and how they address ${pack.identity.user_title}.`,
    "",
    "## Relationship Boundary",
    "",
    "Describe the relationship, allowed topics, and things the character should avoid.",
    "",
    "## World Notes",
    "",
    "Add background, preferences, habits, and repeated motifs here.",
    "",
    draftLine,
    ""
  ].join("\n");
}

function buildAssetsReadme() {
  return [
    "# Character Assets",
    "",
    "Put portrait and expression assets here:",
    "",
    "```text",
    "assets/",
    "  characters/",
    "    default/",
    "      normal.png",
    "      happy.png",
    "      thinking.png",
    "```",
    "",
    "The runtime expects `assets/characters/<outfit>/<emotion>.png`.",
    ""
  ].join("\n");
}

function buildCharactersReadme() {
  return [
    "# Character Portraits",
    "",
    "Create one folder per outfit, then put expression images inside it:",
    "",
    "```text",
    "characters/",
    "  default/",
    "    normal.png",
    "    happy.png",
    "    thinking.png",
    "```",
    ""
  ].join("\n");
}

function buildOutfitReadme({ outfit, emotion, files = [] }) {
  const visibleFiles = uniqueStrings(files.length ? files : [`${emotion}.png`, "thinking.png", "happy.png", "confused.png", "listening.png"]).slice(0, 12);
  return [
    `# ${outfit}`,
    "",
    `Default emotion id: \`${emotion}\`.`,
    "",
    "Expression files in this outfit use their file names as emotion ids:",
    "",
    "```text",
    ...visibleFiles,
    "```",
    ""
  ].join("\n");
}

function runValidator(targetDir) {
  const result = spawnSync(process.execPath, [validatorPath, targetDir], {
    cwd: kitRoot,
    stdio: "inherit"
  });
  if (result.status !== 0) {
    process.exit(result.status || 1);
  }
}

function runExporter(targetDir) {
  const result = spawnSync(process.execPath, [exporterPath, targetDir], {
    cwd: kitRoot,
    encoding: "utf8",
    stdio: "pipe"
  });
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  if (result.status !== 0) {
    process.exit(result.status || 1);
  }
}

function printPreview({ pack, targetDir, imageDraft }) {
  console.log("Akane Creator Kit character pack create preview");
  console.log(`Pack: ${targetDir}`);
  console.log(`Character: ${pack.identity.name} (${pack.identity.id})`);
  console.log(`Default art slot: assets/characters/${pack.appearance.default_outfit}/${pack.appearance.default_emotion}.*`);
  if (imageDraft) {
    console.log(`Image source: ${imageDraft.sourceDir}`);
    console.log(`Images: ${imageDraft.allEmotions.length}`);
  }
  console.log("");
  console.log(JSON.stringify(pack, null, 2));
}

function printResult({ pack, targetDir, imageDraft, exportZip }) {
  const relativeToCharacters = path.relative(DEFAULT_CHARACTERS_DIR, targetDir);
  const defaultCommandsPath =
    relativeToCharacters && !relativeToCharacters.startsWith("..") && !path.isAbsolute(relativeToCharacters)
      ? `./characters/${toPosixPath(relativeToCharacters)}`
      : toPosixPath(path.relative(kitRoot, targetDir)) || targetDir;

  console.log("");
  console.log("Akane Creator Kit character pack create");
  console.log(`Pack: ${targetDir}`);
  console.log(`Character: ${pack.identity.name} (${pack.identity.id})`);
  if (imageDraft) {
    console.log(`Imported images: ${imageDraft.allEmotions.length}`);
  } else {
    console.log(`Next image: ${path.join(targetDir, "assets", "characters", pack.appearance.default_outfit, `${pack.appearance.default_emotion}.png`)}`);
  }
  console.log("");
  console.log("Next commands:");
  console.log(`  npm run check -- ${defaultCommandsPath}`);
  if (!exportZip) {
    console.log(`  npm run export -- ${defaultCommandsPath}`);
  }
}

function printUsage() {
  console.log("Usage:");
  console.log("  npm run create");
  console.log("  npm run create -- --id my_character --name Mika");
  console.log("  npm run create -- --from-images ./raw_images --id my_character --name Mika --export");
  console.log("  npm run create -- --id my_character --name Mika --user-title 主人 --force");
  console.log("");
  console.log("Options:");
  console.log("  --id <id>                 Pack folder id, for example my_character.");
  console.log("  --name <name>             Character display name.");
  console.log("  --app-name <name>         App display name. Defaults to \"<name> Pet\".");
  console.log("  --user-title <title>      How the character addresses the user. Defaults to 主人.");
  console.log("  --outfit <id>             Default outfit folder. Defaults to default.");
  console.log("  --emotion <id>            Default emotion image name. Defaults to normal.");
  console.log("  --music-emotion <id>      Music emotion image name. Defaults to listening.");
  console.log("  --from-images <dir>       Copy image files into the pack and infer emotion ids from file names.");
  console.log("  --to <dir>                Parent directory. Defaults to ./characters.");
  console.log("  --force                   Overwrite an existing pack folder.");
  console.log("  --export                  Export a zip after creating and validating the pack.");
  console.log("  --dry-run                 Print the generated metadata without writing files.");
}

async function pathExists(targetPath) {
  try {
    await fs.access(targetPath);
    return true;
  } catch {
    return false;
  }
}

function assertRemovableDestination(baseDir, destination) {
  const base = path.resolve(baseDir);
  const target = path.resolve(destination);
  if (target === base || !target.startsWith(`${base}${path.sep}`)) {
    throw new Error(`Refusing to overwrite unsafe destination: ${destination}`);
  }
}

function resolveInside(baseDir, relativePath) {
  const base = path.resolve(baseDir);
  const target = path.resolve(base, String(relativePath || ""));
  if (target !== base && !target.startsWith(`${base}${path.sep}`)) {
    throw new Error(`Unsafe target path: ${relativePath}`);
  }
  return target;
}

function sanitizePackId(value) {
  return String(value || "")
    .trim()
    .replace(/[^\w.-]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function sanitizeAssetId(value) {
  return String(value || "")
    .trim()
    .replace(/[\\/:*?"<>|]+/g, "_")
    .replace(/\s+/g, "_")
    .replace(/^\.+|\.+$/g, "")
    .replace(/^_+|_+$/g, "") || "default";
}

function uniqueAssetId(value, used) {
  const base = sanitizeAssetId(value);
  let candidate = base;
  let index = 2;
  while (used.has(candidate)) {
    candidate = `${base}_${index}`;
    index += 1;
  }
  used.add(candidate);
  return candidate;
}

function findEmotion(emotions, candidates) {
  const list = uniqueStrings(emotions);
  const wanted = (Array.isArray(candidates) ? candidates : [candidates]).filter(Boolean);
  for (const candidate of wanted) {
    const raw = String(candidate || "").trim();
    const match = list.find((item) => item === raw);
    if (match) return match;
    const key = normalizeLookupKey(raw);
    const normalizedMatch = list.find((item) => normalizeLookupKey(item) === key);
    if (normalizedMatch) return normalizedMatch;
  }
  return "";
}

function normalizeLookupKey(value) {
  return String(value || "").trim().toLowerCase().replace(/[-_\s]+/g, "");
}

function titleFromId(value) {
  return String(value || "")
    .split(/[_\-.]+/)
    .filter(Boolean)
    .map((part) => `${part.slice(0, 1).toUpperCase()}${part.slice(1)}`)
    .join(" ");
}

function tomlString(value) {
  return JSON.stringify(String(value || ""));
}

function tomlArray(values) {
  return `[${values.map(tomlString).join(", ")}]`;
}

function uniqueStrings(values) {
  return [...new Set(values.filter(Boolean))];
}

function toPosixPath(value) {
  return String(value || "").replace(/\\/g, "/");
}

main().catch((error) => {
  console.error(`Create failed: ${error.message}`);
  process.exitCode = 1;
});
