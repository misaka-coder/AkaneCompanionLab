#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { promises as fs } from "node:fs";
import path from "node:path";
import process from "node:process";
import { inflateRawSync } from "node:zlib";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const kitRoot = path.resolve(scriptDir, "..");
const validatorPath = path.join(scriptDir, "validate-character-pack.mjs");
const DEFAULT_CHARACTERS_DIR = path.join(kitRoot, "characters");
const SUPPORTED_METHODS = new Set([0, 8]);
const PRIVATE_LOCAL_DIRECTORY = "_local";

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (!options.zipPath) {
    printUsage();
    process.exitCode = 1;
    return;
  }

  const zipPath = path.resolve(process.cwd(), options.zipPath);
  const charactersDir = path.resolve(process.cwd(), options.to || DEFAULT_CHARACTERS_DIR);
  const entries = readZipEntries(await fs.readFile(zipPath));
  const root = detectCharacterPackRoot(entries);
  const sourcePackId = sanitizePackId(path.basename(root || path.basename(zipPath, ".zip")));
  const packId = sanitizePackId(options.as || sourcePackId);
  const tempRoot = path.join(kitRoot, `.tmp_import_${process.pid}_${Date.now()}`);
  const tempPackDir = path.join(tempRoot, packId);
  const destination = resolveInside(charactersDir, packId);
  const backupPath = resolveInside(
    charactersDir,
    `.${packId}.backup_${process.pid}_${Date.now()}`
  );

  if (await pathExists(destination)) {
    if (!options.force) {
      throw new Error(
        `Destination already exists: ${destination}. Re-run with --force to overwrite it.`
      );
    }
    assertRemovableDestination(charactersDir, destination);
  }

  try {
    await extractPackEntries({ entries, root, targetDir: tempPackDir });
    runValidator(tempPackDir);

    await fs.mkdir(charactersDir, { recursive: true });
    if (await pathExists(destination)) {
      await fs.rename(destination, backupPath);
    }
    try {
      await fs.rename(tempPackDir, destination);
      await restorePrivateLocalDirectory(backupPath, destination);
    } catch (error) {
      await fs.rm(destination, { recursive: true, force: true });
      if (await pathExists(backupPath)) {
        await fs.rename(backupPath, destination);
      }
      throw error;
    }
    runValidator(destination);
    await fs.rm(backupPath, { recursive: true, force: true });

    console.log("");
    console.log("Akane Creator Kit character pack import");
    console.log(`Zip: ${zipPath}`);
    console.log(`Pack: ${packId}`);
    console.log(`Installed: ${destination}`);
  } finally {
    await fs.rm(tempRoot, { recursive: true, force: true });
  }
}

function parseArgs(args) {
  const options = {
    zipPath: "",
    as: "",
    to: "",
    force: false
  };

  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === "--force") {
      options.force = true;
      continue;
    }
    if (arg === "--as") {
      options.as = args[index + 1] || "";
      index += 1;
      continue;
    }
    if (arg === "--to") {
      options.to = args[index + 1] || "";
      index += 1;
      continue;
    }
    if (!arg.startsWith("-") && !options.zipPath) {
      options.zipPath = arg;
      continue;
    }
    throw new Error(`Unknown argument: ${arg}`);
  }

  return options;
}

function printUsage() {
  console.log("Usage:");
  console.log("  npm run import -- ./dist/akane_v1.zip");
  console.log("  npm run import -- ./dist/akane_v1.zip --as my_character --force");
}

function readZipEntries(buffer) {
  const eocdOffset = findEndOfCentralDirectory(buffer);
  const entryCount = buffer.readUInt16LE(eocdOffset + 10);
  const centralDirectoryOffset = buffer.readUInt32LE(eocdOffset + 16);
  const entries = [];
  let offset = centralDirectoryOffset;

  for (let index = 0; index < entryCount; index += 1) {
    if (buffer.readUInt32LE(offset) !== 0x02014b50) {
      throw new Error("Invalid zip central directory.");
    }

    const method = buffer.readUInt16LE(offset + 10);
    const compressedSize = buffer.readUInt32LE(offset + 20);
    const uncompressedSize = buffer.readUInt32LE(offset + 24);
    const nameLength = buffer.readUInt16LE(offset + 28);
    const extraLength = buffer.readUInt16LE(offset + 30);
    const commentLength = buffer.readUInt16LE(offset + 32);
    const localHeaderOffset = buffer.readUInt32LE(offset + 42);
    const name = normalizeZipPath(buffer.slice(offset + 46, offset + 46 + nameLength).toString("utf8"));

    if (!SUPPORTED_METHODS.has(method)) {
      throw new Error(`Unsupported zip compression method ${method} for ${name}.`);
    }

    const localNameLength = buffer.readUInt16LE(localHeaderOffset + 26);
    const localExtraLength = buffer.readUInt16LE(localHeaderOffset + 28);
    const dataOffset = localHeaderOffset + 30 + localNameLength + localExtraLength;
    const compressed = buffer.slice(dataOffset, dataOffset + compressedSize);
    const data = method === 8 ? inflateRawSync(compressed) : compressed;

    if (data.length !== uncompressedSize) {
      throw new Error(`Unexpected uncompressed size for ${name}.`);
    }

    entries.push({
      name,
      directory: name.endsWith("/"),
      data
    });

    offset += 46 + nameLength + extraLength + commentLength;
  }

  return entries;
}

function findEndOfCentralDirectory(buffer) {
  const minOffset = Math.max(0, buffer.length - 0xffff - 22);
  for (let offset = buffer.length - 22; offset >= minOffset; offset -= 1) {
    if (buffer.readUInt32LE(offset) === 0x06054b50) {
      return offset;
    }
  }
  throw new Error("Cannot find zip end of central directory.");
}

function detectCharacterPackRoot(entries) {
  const characterJsonEntries = entries
    .filter((entry) => !entry.directory)
    .filter((entry) => entry.name.endsWith("character.json"));

  if (!characterJsonEntries.length) {
    throw new Error("Zip does not contain a character.json file.");
  }
  if (characterJsonEntries.length > 1) {
    throw new Error("Zip contains multiple character.json files; import one pack at a time.");
  }

  return characterJsonEntries[0].name.replace(/character\.json$/, "").replace(/\/$/, "");
}

async function extractPackEntries({ entries, root, targetDir }) {
  const rootPrefix = root ? `${root}/` : "";
  for (const entry of entries) {
    if (entry.directory) continue;
    if (rootPrefix && !entry.name.startsWith(rootPrefix)) continue;
    const relativePath = normalizeZipPath(rootPrefix ? entry.name.slice(rootPrefix.length) : entry.name);
    if (!relativePath || isPrivateLocalPath(relativePath)) continue;
    const targetPath = resolveInside(targetDir, relativePath);
    await fs.mkdir(path.dirname(targetPath), { recursive: true });
    await fs.writeFile(targetPath, entry.data);
  }
}

async function restorePrivateLocalDirectory(backupDir, destination) {
  if (!(await pathExists(backupDir))) return;
  const source = resolveInside(backupDir, PRIVATE_LOCAL_DIRECTORY);
  if (!(await pathExists(source))) return;
  const target = resolveInside(destination, PRIVATE_LOCAL_DIRECTORY);
  await fs.rm(target, { recursive: true, force: true });
  await fs.rename(source, target);
}

function isPrivateLocalPath(relativePath) {
  return normalizeZipPath(relativePath).split("/")[0].toLowerCase() === PRIVATE_LOCAL_DIRECTORY;
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
  const normalized = normalizeZipPath(relativePath);
  const target = path.resolve(base, normalized.replace(/\//g, path.sep));
  if (target !== base && !target.startsWith(`${base}${path.sep}`)) {
    throw new Error(`Unsafe path in character pack: ${relativePath}`);
  }
  return target;
}

function normalizeZipPath(value) {
  const raw = String(value || "").replace(/\\/g, "/");
  if (/^[A-Za-z]:/.test(raw) || raw.startsWith("/")) {
    throw new Error(`Unsafe absolute path in zip: ${value}`);
  }
  const normalized = path.posix.normalize(raw).replace(/^\.\//, "");
  if (normalized === ".." || normalized.startsWith("../") || normalized.includes("/../")) {
    throw new Error(`Unsafe relative path in zip: ${value}`);
  }
  return normalized === "." ? "" : normalized;
}

function sanitizePackId(value) {
  return String(value || "character_pack")
    .trim()
    .replace(/[^\w.-]+/g, "_")
    .replace(/^_+|_+$/g, "") || "character_pack";
}

main().catch((error) => {
  console.error(`Import failed: ${error.message}`);
  process.exitCode = 1;
});
