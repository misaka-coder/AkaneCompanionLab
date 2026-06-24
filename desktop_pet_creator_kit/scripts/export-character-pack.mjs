#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { promises as fs } from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const kitRoot = path.resolve(scriptDir, "..");
const validatorPath = path.join(scriptDir, "validate-character-pack.mjs");
const DEFAULT_PACK = path.join(kitRoot, "characters", "akane_v1");
const EXPORT_FORMAT = "akane.character_pack_export.v0.1";
const ZIP_UTF8_FLAG = 0x0800;
const IGNORED_FILE_NAMES = new Set([".DS_Store", "Thumbs.db", "desktop.ini"]);
const PRIVATE_LOCAL_DIRECTORY = "_local";

const args = process.argv.slice(2);
const packArg = readPositionalArg(args) || DEFAULT_PACK;
const outputDir = path.resolve(kitRoot, "dist");
const packDir = path.resolve(process.cwd(), packArg);

async function main() {
  runValidator(packDir);

  const character = await readCharacterJson(packDir);
  const packId = sanitizePackId(path.basename(packDir));
  const characterId = sanitizePackId(character?.identity?.id || packId);
  const exportName = `${packId}.zip`;
  const outputPath = path.join(outputDir, exportName);
  const files = await collectFiles(packDir);

  if (!files.length) {
    throw new Error(`No exportable files found in ${packDir}`);
  }

  const manifest = buildExportManifest({
    packId,
    characterId,
    character,
    files
  });
  const entries = await buildZipEntries({ packId, files, manifest });

  await fs.mkdir(outputDir, { recursive: true });
  await writeZip(outputPath, entries);

  const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
  console.log("");
  console.log("Akane Creator Kit character pack export");
  console.log(`Pack: ${packDir}`);
  console.log(`Character: ${character?.identity?.name || characterId} (${characterId})`);
  console.log(`Files: ${files.length}`);
  console.log(`Source bytes: ${totalBytes}`);
  console.log(`Output: ${outputPath}`);
}

function readPositionalArg(values) {
  for (const value of values) {
    if (!value.startsWith("-")) return value;
  }
  return "";
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

async function readCharacterJson(targetDir) {
  const filePath = path.join(targetDir, "character.json");
  try {
    return JSON.parse(await fs.readFile(filePath, "utf8"));
  } catch (error) {
    throw new Error(`Cannot read character.json: ${error.message}`);
  }
}

async function collectFiles(rootDir) {
  const files = [];

  async function walk(currentDir) {
    const entries = await fs.readdir(currentDir, { withFileTypes: true });
    for (const entry of entries) {
      if (IGNORED_FILE_NAMES.has(entry.name)) continue;
      if (
        entry.isDirectory() &&
        entry.name.toLowerCase() === PRIVATE_LOCAL_DIRECTORY
      ) continue;
      const absolutePath = path.join(currentDir, entry.name);
      if (entry.isDirectory()) {
        await walk(absolutePath);
        continue;
      }
      if (!entry.isFile()) continue;
      const stat = await fs.stat(absolutePath);
      const relativePath = toZipPath(path.relative(rootDir, absolutePath));
      files.push({
        absolutePath,
        relativePath,
        size: stat.size,
        mtime: stat.mtime
      });
    }
  }

  await walk(rootDir);
  return files.sort((a, b) => a.relativePath.localeCompare(b.relativePath, "en"));
}

function buildExportManifest({ packId, characterId, character, files }) {
  return {
    format: EXPORT_FORMAT,
    exported_at: new Date().toISOString(),
    pack_id: packId,
    character_id: characterId,
    character_name: character?.identity?.name || characterId,
    schema_version: character?.schema_version || "",
    asset_root: character?.assets?.asset_root || "assets",
    files: files.map((file) => file.relativePath)
  };
}

async function buildZipEntries({ packId, files, manifest }) {
  const entries = [];
  for (const file of files) {
    entries.push({
      name: `${packId}/${file.relativePath}`,
      data: await fs.readFile(file.absolutePath),
      mtime: file.mtime
    });
  }

  entries.push({
    name: `${packId}/akane-export.json`,
    data: Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`, "utf8"),
    mtime: new Date()
  });
  entries.push({
    name: `${packId}/INSTALL.md`,
    data: Buffer.from(buildInstallNotes(packId), "utf8"),
    mtime: new Date()
  });

  return entries;
}

function buildInstallNotes(packId) {
  return [
    "# Akane Character Pack",
    "",
    "Install this folder into:",
    "",
    "```text",
    `desktop_pet_creator_kit/characters/${packId}/`,
    "```",
    "",
    "Then run:",
    "",
    "```powershell",
    "cd desktop_pet_creator_kit",
    `npm run check -- ./characters/${packId}`,
    "```",
    ""
  ].join("\n");
}

async function writeZip(outputPath, entries) {
  const localParts = [];
  const centralParts = [];
  let offset = 0;

  for (const entry of entries) {
    const nameBuffer = Buffer.from(toZipPath(entry.name), "utf8");
    const data = Buffer.isBuffer(entry.data) ? entry.data : Buffer.from(entry.data);
    const crc = crc32(data);
    const { time, date } = toDosDateTime(entry.mtime || new Date());
    const localHeader = Buffer.alloc(30);

    localHeader.writeUInt32LE(0x04034b50, 0);
    localHeader.writeUInt16LE(20, 4);
    localHeader.writeUInt16LE(ZIP_UTF8_FLAG, 6);
    localHeader.writeUInt16LE(0, 8);
    localHeader.writeUInt16LE(time, 10);
    localHeader.writeUInt16LE(date, 12);
    localHeader.writeUInt32LE(crc, 14);
    localHeader.writeUInt32LE(data.length, 18);
    localHeader.writeUInt32LE(data.length, 22);
    localHeader.writeUInt16LE(nameBuffer.length, 26);
    localHeader.writeUInt16LE(0, 28);

    localParts.push(localHeader, nameBuffer, data);

    const centralHeader = Buffer.alloc(46);
    centralHeader.writeUInt32LE(0x02014b50, 0);
    centralHeader.writeUInt16LE(20, 4);
    centralHeader.writeUInt16LE(20, 6);
    centralHeader.writeUInt16LE(ZIP_UTF8_FLAG, 8);
    centralHeader.writeUInt16LE(0, 10);
    centralHeader.writeUInt16LE(time, 12);
    centralHeader.writeUInt16LE(date, 14);
    centralHeader.writeUInt32LE(crc, 16);
    centralHeader.writeUInt32LE(data.length, 20);
    centralHeader.writeUInt32LE(data.length, 24);
    centralHeader.writeUInt16LE(nameBuffer.length, 28);
    centralHeader.writeUInt16LE(0, 30);
    centralHeader.writeUInt16LE(0, 32);
    centralHeader.writeUInt16LE(0, 34);
    centralHeader.writeUInt16LE(0, 36);
    centralHeader.writeUInt32LE(0, 38);
    centralHeader.writeUInt32LE(offset, 42);
    centralParts.push(centralHeader, nameBuffer);

    offset += localHeader.length + nameBuffer.length + data.length;
  }

  const centralDirectory = Buffer.concat(centralParts);
  const localData = Buffer.concat(localParts);
  const endRecord = Buffer.alloc(22);
  endRecord.writeUInt32LE(0x06054b50, 0);
  endRecord.writeUInt16LE(0, 4);
  endRecord.writeUInt16LE(0, 6);
  endRecord.writeUInt16LE(entries.length, 8);
  endRecord.writeUInt16LE(entries.length, 10);
  endRecord.writeUInt32LE(centralDirectory.length, 12);
  endRecord.writeUInt32LE(localData.length, 16);
  endRecord.writeUInt16LE(0, 20);

  await fs.writeFile(outputPath, Buffer.concat([localData, centralDirectory, endRecord]));
}

function toDosDateTime(value) {
  const date = value instanceof Date ? value : new Date(value);
  const year = Math.max(date.getFullYear(), 1980);
  const dosTime =
    (date.getHours() << 11) |
    (date.getMinutes() << 5) |
    Math.floor(date.getSeconds() / 2);
  const dosDate =
    ((year - 1980) << 9) |
    ((date.getMonth() + 1) << 5) |
    date.getDate();
  return { time: dosTime, date: dosDate };
}

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) {
    crc = (crc >>> 8) ^ CRC_TABLE[(crc ^ byte) & 0xff];
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function makeCrcTable() {
  const table = new Uint32Array(256);
  for (let i = 0; i < 256; i += 1) {
    let value = i;
    for (let j = 0; j < 8; j += 1) {
      value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
    }
    table[i] = value >>> 0;
  }
  return table;
}

function sanitizePackId(value) {
  return String(value || "character_pack")
    .trim()
    .replace(/[^\w.-]+/g, "_")
    .replace(/^_+|_+$/g, "") || "character_pack";
}

function toZipPath(value) {
  return String(value || "").replace(/\\/g, "/").replace(/^\/+/, "");
}

const CRC_TABLE = makeCrcTable();

main().catch((error) => {
  console.error(`Export failed: ${error.message}`);
  process.exitCode = 1;
});
