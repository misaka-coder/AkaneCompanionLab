#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { promises as fs } from "node:fs";
import path from "node:path";
import process from "node:process";
import { createInterface } from "node:readline/promises";
import { fileURLToPath } from "node:url";

const SCHEMA_VERSION = "akane.character.v0.1";
const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const kitRoot = path.resolve(scriptDir, "..");
const validatorPath = path.join(scriptDir, "validate-character-pack.mjs");
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

  const pack = buildPack({
    id: packId,
    name: answers.name,
    appName: answers.appName,
    userTitle: answers.userTitle,
    outfit: answers.outfit,
    emotion: answers.emotion,
    musicEmotion: answers.musicEmotion
  });

  if (answers.dryRun) {
    printPreview({ pack, targetDir, outfit: answers.outfit, emotion: answers.emotion });
    return;
  }

  if (await pathExists(targetDir)) {
    assertRemovableDestination(charactersDir, targetDir);
    await fs.rm(targetDir, { recursive: true, force: true });
  }

  await writePack({
    targetDir,
    pack,
    outfit: answers.outfit,
    emotion: answers.emotion
  });
  runValidator(targetDir);
  printResult({ pack, targetDir, outfit: answers.outfit, emotion: answers.emotion });
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
    to: "",
    force: false,
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
    return normalizeDefaults({
      ...options,
      id,
      name,
      appName: options.appName || (await ask(rl, "App display name", `${name} Pet`)),
      userTitle: options.userTitle || (await ask(rl, "User title", "主人")),
      outfit: options.outfit || (await ask(rl, "Default outfit folder", "default")),
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
  const outfit = sanitizeAssetId(options.outfit || "default");
  const emotion = sanitizeAssetId(options.emotion || "normal");
  const musicEmotion = sanitizeAssetId(options.musicEmotion || "listening");

  return {
    ...options,
    id,
    name,
    appName,
    userTitle: String(options.userTitle || "主人").trim(),
    outfit,
    emotion,
    musicEmotion
  };
}

async function ask(rl, label, fallback) {
  const answer = await rl.question(`${label} (${fallback}): `);
  return answer.trim() || fallback;
}

function buildPack({ id, name, appName, userTitle, outfit, emotion, musicEmotion }) {
  return {
    schema_version: SCHEMA_VERSION,
    identity: {
      id,
      name,
      app_name: appName,
      user_title: userTitle
    },
    appearance: {
      default_outfit: outfit,
      default_emotion: emotion,
      music_emotion: musicEmotion,
      required_emotions: [emotion],
      recommended_emotions: uniqueStrings(["thinking", "happy", "confused", musicEmotion])
    },
    dialogue: {
      input_placeholder: `和 ${name} 说点什么……`,
      session_display_title: `${name} 桌宠对话`,
      tts_test_text: `${name}：语音播放测试。`,
      proactive_wake_prompt: `${userTitle}暂时没有说话。你像坐在旁边陪伴一样，参考刚才看见的情况自然接话。`,
      local_click_lines: [
        { text: "我在哦。", emotion },
        { text: "有什么新计划吗？", emotion }
      ]
    },
    emotion_aliases: buildEmotionAliases({ emotion, musicEmotion }),
    assets: {
      runtime_source: "character pack assets, with desktop_pet_next bundled fallback",
      asset_root: "assets",
      bundled_outfit: outfit,
      portrait_glob: "assets/characters/<outfit>/<emotion>.png"
    }
  };
}

function buildEmotionAliases({ emotion, musicEmotion }) {
  return {
    normal: uniqueStrings([emotion, "normal"]),
    thinking: uniqueStrings(["thinking", emotion, "normal"]),
    happy: uniqueStrings(["happy", emotion, "normal"]),
    confused: uniqueStrings(["confused", emotion, "normal"]),
    music: uniqueStrings([musicEmotion, "happy", emotion, "normal"])
  };
}

async function writePack({ targetDir, pack, outfit, emotion }) {
  await fs.mkdir(path.join(targetDir, "assets", "characters", outfit), { recursive: true });
  await fs.writeFile(path.join(targetDir, "character.json"), `${JSON.stringify(pack, null, 2)}\n`);
  await fs.writeFile(path.join(targetDir, "character.toml"), buildToml(pack));
  await fs.writeFile(path.join(targetDir, "persona.md"), buildPersona(pack));
  await fs.writeFile(path.join(targetDir, "assets", "README.md"), buildAssetsReadme());
  await fs.writeFile(
    path.join(targetDir, "assets", "characters", "README.md"),
    buildCharactersReadme()
  );
  await fs.writeFile(
    path.join(targetDir, "assets", "characters", outfit, "README.md"),
    buildOutfitReadme({ outfit, emotion })
  );
}

function buildToml(pack) {
  return [
    `schema_version = ${tomlString(pack.schema_version)}`,
    "",
    "[identity]",
    `id = ${tomlString(pack.identity.id)}`,
    `name = ${tomlString(pack.identity.name)}`,
    `app_name = ${tomlString(pack.identity.app_name)}`,
    `user_title = ${tomlString(pack.identity.user_title)}`,
    "",
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

function buildPersona(pack) {
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
    "The current desktop runtime does not load this file yet. It is kept as the future backend persona source.",
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

function buildOutfitReadme({ outfit, emotion }) {
  return [
    `# ${outfit}`,
    "",
    `Put the default portrait at \`${emotion}.png\`.`,
    "",
    "Optional recommended files:",
    "",
    "```text",
    `${emotion}.png`,
    "thinking.png",
    "happy.png",
    "confused.png",
    "listening.png",
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

function printPreview({ pack, targetDir, outfit, emotion }) {
  console.log("Akane Creator Kit character pack create preview");
  console.log(`Pack: ${targetDir}`);
  console.log(`Character: ${pack.identity.name} (${pack.identity.id})`);
  console.log(`Default art slot: assets/characters/${outfit}/${emotion}.png`);
  console.log("");
  console.log(JSON.stringify(pack, null, 2));
}

function printResult({ pack, targetDir, outfit, emotion }) {
  const relativeToCharacters = path.relative(DEFAULT_CHARACTERS_DIR, targetDir);
  const defaultCommandsPath =
    relativeToCharacters && !relativeToCharacters.startsWith("..") && !path.isAbsolute(relativeToCharacters)
      ? `./characters/${toPosixPath(relativeToCharacters)}`
      : toPosixPath(path.relative(kitRoot, targetDir)) || targetDir;

  console.log("");
  console.log("Akane Creator Kit character pack create");
  console.log(`Pack: ${targetDir}`);
  console.log(`Character: ${pack.identity.name} (${pack.identity.id})`);
  console.log(`Next image: ${path.join(targetDir, "assets", "characters", outfit, `${emotion}.png`)}`);
  console.log("");
  console.log("Next commands:");
  console.log(`  npm run check -- ${defaultCommandsPath}`);
  console.log(`  npm run export -- ${defaultCommandsPath}`);
}

function printUsage() {
  console.log("Usage:");
  console.log("  npm run create");
  console.log("  npm run create -- --id my_character --name Mika");
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
  console.log("  --to <dir>                Parent directory. Defaults to ./characters.");
  console.log("  --force                   Overwrite an existing pack folder.");
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
