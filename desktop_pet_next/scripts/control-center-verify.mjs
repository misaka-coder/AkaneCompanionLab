import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(__dirname, "..");

const npmCmd = process.platform === "win32" ? "npm.cmd" : "npm";

const REQUIRED_FILES = [
  "control-center-lab.html",
  "src/control-center-lab.js",
  "src/control-center-lab.css",
  "src/control-center/action-router.js",
  "src/control-center/action-surface-contract.js",
  "src/control-center/data-sources.js",
  "src/control-center/data-adapter.js",
  "scripts/control-center-action-bridge-smoke.mjs",
  "scripts/control-center-runtime-probe.mjs",
  "scripts/control-center-ux-smoke.mjs",
];

const FORBIDDEN_LEGACY_FILES = [
  "src/settings.js",
  "src/settings.css",
];

function checkRequiredFiles() {
  const missing = REQUIRED_FILES.filter((rel) => !existsSync(resolve(projectRoot, rel)));
  if (missing.length) {
    console.error("[control-center] MISSING REQUIRED FILES:");
    for (const file of missing) {
      console.error(`  ${file}`);
    }
    process.exit(1);
  }
  const legacyFiles = FORBIDDEN_LEGACY_FILES.filter((rel) => existsSync(resolve(projectRoot, rel)));
  if (legacyFiles.length) {
    console.error("[control-center] LEGACY SETTINGS IMPLEMENTATION RETURNED:");
    for (const file of legacyFiles) {
      console.error(`  ${file}`);
    }
    process.exit(1);
  }
  console.log(`[control-center] all ${REQUIRED_FILES.length} required files exist`);
}

function runStep(label, args) {
  console.log(`\n--- [control-center] ${label} ---`);
  const result = spawnNpm(args);
  if (result.error) {
    console.error(`[control-center] FAILED: ${label} (${result.error.message})`);
    process.exit(1);
  }
  if (result.signal) {
    console.error(`[control-center] FAILED: ${label} (signal ${result.signal})`);
    process.exit(1);
  }
  if (result.status !== 0) {
    const exitCode = typeof result.status === "number" ? result.status : 1;
    console.error(`[control-center] FAILED: ${label} (exit ${exitCode})`);
    process.exit(exitCode);
  }
  console.log(`[control-center] passed: ${label}`);
}

function spawnNpm(args) {
  const options = {
    cwd: projectRoot,
    stdio: "inherit",
  };
  if (process.platform !== "win32") {
    return spawnSync(npmCmd, args, options);
  }

  const commandLine = [npmCmd, ...args].map(quoteWindowsCommandArg).join(" ");
  return spawnSync(process.env.ComSpec || "cmd.exe", ["/d", "/s", "/c", commandLine], options);
}

function quoteWindowsCommandArg(value) {
  const text = String(value);
  if (/^[A-Za-z0-9:._/-]+$/.test(text)) return text;
  return `"${text.replace(/"/g, '\\"')}"`;
}

console.log("=== control-center verification matrix ===\n");

// Production /control-center/snapshot providers (companion_v01/routes/control_center.py):
//   health   → _build_snapshot_health       (config_module, pid, python, contracts)
//   diagnostics → _build_snapshot_diagnostics (engine, runtime_metrics, public_guard)
//   workspace → _build_snapshot_workspace     (engine.build_desktop_pet_workspace_panel)
//   resourceManifest → _build_snapshot_resource_manifest (engine.build_resource_manifest)
//   metrics  → _build_snapshot_metrics_text   (tracemalloc, llm, vector_store, counter, guard)
// Providers are real production implementations; individual failures degrade gracefully.

// 1. File existence check
checkRequiredFiles();

// 2. Action bridge smoke
runStep("smoke actions", ["run", "smoke:control-center-actions"]);

// 3. Runtime probe
runStep("runtime probe", ["run", "probe:control-center-runtime"]);

// 4. Build (produces dist/ artifacts for UX smoke)
runStep("build", ["run", "build"]);

// 5. UX smoke (built artifact structure, CSS rules, screenshot placeholders)
runStep("UX smoke", ["run", "smoke:control-center-ux"]);

console.log("\n=== control-center verification passed ===");
