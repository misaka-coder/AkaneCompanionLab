import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const backendUrl = normalizeBackendUrl(process.env.AKANE_BACKEND_URL || "http://127.0.0.1:9999");
const checks = [];

await checkCommand("node", ["--version"], { label: "Node.js", required: true });
await checkCommand("npm", ["--version"], { label: "npm", required: true });
await checkCommand("cargo", ["--version"], { label: "Cargo/Rust", required: true });
await checkCommand("rustc", ["--version"], { label: "rustc", required: true });
checkNodeModules();
await checkWebView2();
await checkBackend();

printSummary();

const hasFailure = checks.some((check) => check.level === "fail");
process.exitCode = hasFailure ? 1 : 0;

async function checkCommand(command, args, { label, required }) {
  try {
    const { stdout } = await exec(command, args);
    pass(label, firstLine(stdout));
  } catch (error) {
    const message = `未找到 ${command}，请先安装或刷新 PATH。`;
    if (required) fail(label, message);
    else warn(label, message);
  }
}

function checkNodeModules() {
  const vite = hasPackage("vite");
  const tauriCli = hasPackage("@tauri-apps/cli");
  const tauriApi = hasPackage("@tauri-apps/api");
  const httpPlugin = hasPackage("@tauri-apps/plugin-http");

  if (vite && tauriCli && tauriApi && httpPlugin) {
    pass("npm dependencies", "node_modules 已就绪");
    return;
  }

  const missing = [
    ["vite", vite],
    ["@tauri-apps/cli", tauriCli],
    ["@tauri-apps/api", tauriApi],
    ["@tauri-apps/plugin-http", httpPlugin]
  ]
    .filter(([, resolved]) => !resolved)
    .map(([name]) => name)
    .join(", ");
  fail("npm dependencies", `缺少 ${missing}，请运行 npm install。`);
}

async function checkWebView2() {
  if (process.platform !== "win32") {
    warn("WebView2", "当前不是 Windows，跳过 WebView2 检查。");
    return;
  }

  const candidates = [
    "C:\\Program Files (x86)\\Microsoft\\EdgeWebView\\Application",
    "C:\\Program Files\\Microsoft\\EdgeWebView\\Application"
  ];
  if (candidates.some((path) => existsSync(path))) {
    pass("WebView2", "Microsoft Edge WebView2 Runtime 已发现");
    return;
  }

  try {
    const { stdout } = await exec("powershell.exe", [
      "-NoProfile",
      "-ExecutionPolicy",
      "Bypass",
      "-Command",
      "(Get-ItemProperty 'HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\EdgeUpdate\\Clients\\*' -ErrorAction SilentlyContinue | Where-Object { $_.name -like '*WebView2*' } | Select-Object -First 1 -ExpandProperty pv)"
    ]);
    if (stdout.trim()) {
      pass("WebView2", `Registry version ${stdout.trim()}`);
      return;
    }
  } catch {
    // Fall through to a warning.
  }

  warn("WebView2", "未确认 WebView2 Runtime；若 Tauri 窗口打不开，请安装 Microsoft Edge WebView2 Runtime。");
}

async function checkBackend() {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 2500);
    const response = await fetch(`${backendUrl}/health?t=${Date.now()}`, {
      cache: "no-store",
      signal: controller.signal
    });
    clearTimeout(timer);
    if (response.ok) {
      pass("Backend", `${backendUrl} 可访问`);
    } else {
      warn("Backend", `${backendUrl}/health 返回 HTTP ${response.status}；对话前请确认后端。`);
    }
  } catch {
    warn("Backend", `${backendUrl} 暂时连不上；只影响对话、资源、TTS、ASR、Workspace。`);
  }
}

function hasPackage(name) {
  return existsSync(join(root, "node_modules", ...name.split("/")));
}

function exec(command, args) {
  return new Promise((resolve, reject) => {
    const candidates = commandCandidates(command, args);
    let index = 0;

    const handleError = (error) => {
      if (index < candidates.length - 1) {
        index += 1;
        tryNext();
        return;
      }
      reject(error);
    };

    const tryNext = () => {
      const current = candidates[index];
      try {
        execFile(current.command, current.args, { cwd: root, windowsHide: true }, (error, stdout, stderr) => {
          if (error) {
            handleError(error);
            return;
          }
          resolve({ stdout: String(stdout || ""), stderr: String(stderr || "") });
        });
      } catch (error) {
        handleError(error);
      }
    };

    tryNext();
  });
}

function commandCandidates(command, args) {
  const direct = { command, args };
  if (process.platform !== "win32" || /\.(?:cmd|bat|exe)$/i.test(command)) {
    return [direct];
  }
  return [
    direct,
    {
      command: "cmd.exe",
      args: ["/d", "/s", "/c", [command, ...args].map(quoteCmdArg).join(" ")]
    }
  ];
}

function quoteCmdArg(value) {
  const text = String(value);
  if (text && !/[ \t&()^%!<>|"]/u.test(text)) {
    return text;
  }
  return `"${text.replace(/"/g, '\\"')}"`;
}

function normalizeBackendUrl(url) {
  return String(url || "").trim().replace(/\/+$/, "") || "http://127.0.0.1:9999";
}

function firstLine(value) {
  return String(value || "").trim().split(/\r?\n/)[0] || "OK";
}

function pass(name, detail) {
  checks.push({ level: "pass", name, detail });
}

function warn(name, detail) {
  checks.push({ level: "warn", name, detail });
}

function fail(name, detail) {
  checks.push({ level: "fail", name, detail });
}

function printSummary() {
  console.log("Akane Desktop Pet Next Doctor\n");
  for (const check of checks) {
    const mark = check.level === "pass" ? "[OK]" : check.level === "warn" ? "[WARN]" : "[FAIL]";
    console.log(`${mark} ${check.name}: ${check.detail}`);
  }

  const failed = checks.filter((check) => check.level === "fail").length;
  const warned = checks.filter((check) => check.level === "warn").length;
  console.log(`\nSummary: ${failed} failed, ${warned} warning(s).`);
}
