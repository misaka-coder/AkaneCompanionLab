const { execFile, spawn } = require("child_process");
const crypto = require("crypto");
const path = require("path");

const WINDOW_TITLE_LIMIT = 240;
const PROCESS_NAME_LIMIT = 100;
const CLIPBOARD_TEXT_LIMIT = 600;
const POWERSHELL_TIMEOUT_MS = 4000;
const CLIPBOARD_TIMEOUT_MS = 3000;
const LAST_EXTERNAL_MAX_AGE_MS = 2 * 60 * 1000;

let lastExternalWindow = null;
let watcherProcess = null;
let watcherBuffer = "";
let watcherMainWindow = null;
let watcherLoadSettings = null;
let watcherRestartTimer = 0;

function attachDesktopContextTracker(mainWindow, { loadSettings } = {}) {
  if (!mainWindow || mainWindow.isDestroyed()) return;

  watcherMainWindow = mainWindow;
  watcherLoadSettings = loadSettings;

  syncWindowWatcher();

  mainWindow.on("closed", () => {
    stopWindowWatcher();
  });
}

function refreshDesktopContextTracker() {
  syncWindowWatcher();
}

async function collectDesktopContext(mainWindow, settings = {}, options = {}) {
  if (settings?.desktopContextEnabled === false) {
    return {
      ok: false,
      enabled: false,
      reason: "disabled",
    };
  }

  const includeClipboard = Boolean(settings?.clipboardContextEnabled && options?.includeClipboard !== false);
  const foreground = await collectForegroundWindow(mainWindow);
  const clipboard = includeClipboard ? await readClipboardContext() : { included: false };

  return {
    ok: true,
    enabled: true,
    captured_at: Date.now(),
    platform: process.platform,
    foreground,
    clipboard,
  };
}

async function collectForegroundWindow(mainWindow) {
  if (process.platform !== "win32") {
    return {
      title: "",
      process_name: "",
      pid: null,
      source: "unsupported_platform",
    };
  }

  try {
    const probe = await probeForegroundWindow(mainWindow);
    if (probe.ok && hasWindowInfo(probe.window) && !probe.excluded) {
      rememberExternalWindow(probe.window, "send");
      return probe.window;
    }

    const cached = getFreshLastExternalWindow();
    if (cached) {
      return cached;
    }

    return {
      title: "",
      process_name: "",
      pid: null,
      source: probe.excluded ? "self" : "unknown",
    };
  } catch (error) {
    const cached = getFreshLastExternalWindow();
    if (cached) return cached;

    return {
      title: "",
      process_name: "",
      pid: null,
      source: "error",
      error: String(error?.message || error || "window_probe_failed").slice(0, 240),
    };
  }
}

function shouldTrackDesktopContext() {
  if (process.platform !== "win32") return false;
  if (!watcherMainWindow || watcherMainWindow.isDestroyed()) return false;
  try {
    return watcherLoadSettings ? watcherLoadSettings()?.desktopContextEnabled !== false : true;
  } catch {
    return true;
  }
}

function syncWindowWatcher() {
  if (shouldTrackDesktopContext()) {
    startWindowWatcher();
  } else {
    stopWindowWatcher();
  }
}

function startWindowWatcher() {
  if (watcherProcess || process.platform !== "win32") return;

  watcherBuffer = "";
  watcherProcess = spawn(
    "powershell.exe",
    [
      "-NoProfile",
      "-NonInteractive",
      "-ExecutionPolicy",
      "Bypass",
      "-File",
      path.join(__dirname, "window-watcher.ps1"),
    ],
    {
      windowsHide: true,
      stdio: ["ignore", "pipe", "ignore"],
    }
  );

  watcherProcess.stdout.setEncoding("utf8");
  watcherProcess.stdout.on("data", (chunk) => {
    watcherBuffer += chunk;
    drainWatcherBuffer();
  });

  watcherProcess.on("exit", () => {
    watcherProcess = null;
    watcherBuffer = "";
    if (shouldTrackDesktopContext()) {
      clearTimeout(watcherRestartTimer);
      watcherRestartTimer = setTimeout(() => {
        watcherRestartTimer = 0;
        startWindowWatcher();
      }, 2000);
    }
  });
}

function stopWindowWatcher() {
  clearTimeout(watcherRestartTimer);
  watcherRestartTimer = 0;
  if (watcherProcess) {
    const processToKill = watcherProcess;
    watcherProcess = null;
    watcherBuffer = "";
    try {
      processToKill.kill();
    } catch {
      // best effort
    }
  }
}

function drainWatcherBuffer() {
  while (true) {
    const newlineIndex = watcherBuffer.indexOf("\n");
    if (newlineIndex < 0) break;
    const line = watcherBuffer.slice(0, newlineIndex).trim();
    watcherBuffer = watcherBuffer.slice(newlineIndex + 1);
    if (!line) continue;
    try {
      handleWatcherWindow(JSON.parse(line));
    } catch {
      // Ignore malformed watcher output.
    }
  }
}

function handleWatcherWindow(payload) {
  if (!shouldTrackDesktopContext()) return;
  const windowInfo = normalizeWindowInfo(payload);
  if (!hasWindowInfo(windowInfo)) return;
  if (isExcludedWindow(windowInfo, getExcludeProcessIds(watcherMainWindow), watcherMainWindow?.getTitle?.() || "Akane")) {
    return;
  }
  rememberExternalWindow(
    {
      ...windowInfo,
      source: "foreground_watcher",
    },
    "watcher"
  );
}

async function probeForegroundWindow(mainWindow) {
  const excludePids = getExcludeProcessIds(mainWindow);
  const excludeTitle = mainWindow?.getTitle?.() || "Akane";
  const windowInfo = normalizeWindowInfo(await runPowerShellWindowProbe());
  return {
    ok: true,
    window: windowInfo,
    excluded: isExcludedWindow(windowInfo, excludePids, excludeTitle),
  };
}

function getExcludeProcessIds(mainWindow) {
  const ids = [process.pid];
  try {
    const rendererPid = mainWindow?.webContents?.getOSProcessId?.();
    if (Number.isFinite(Number(rendererPid))) ids.push(Math.round(Number(rendererPid)));
  } catch {
    // Older Electron builds may not expose getOSProcessId before the page is ready.
  }
  return Array.from(new Set(ids.filter((id) => Number.isFinite(Number(id)) && Number(id) > 0)));
}

function hasWindowInfo(windowInfo) {
  return Boolean(windowInfo?.title || windowInfo?.process_name);
}

function rememberExternalWindow(windowInfo, reason) {
  if (!hasWindowInfo(windowInfo)) return;
  lastExternalWindow = {
    ...windowInfo,
    source: "last_external_window",
    observed_source: windowInfo.source || "foreground",
    observed_reason: String(reason || "unknown"),
    captured_at: Date.now(),
  };
}

function getFreshLastExternalWindow() {
  if (!lastExternalWindow) return null;
  const ageMs = Date.now() - Number(lastExternalWindow.captured_at || 0);
  if (!Number.isFinite(ageMs) || ageMs < 0 || ageMs > LAST_EXTERNAL_MAX_AGE_MS) {
    return null;
  }
  return {
    ...lastExternalWindow,
    age_ms: Math.round(ageMs),
  };
}

function isExcludedWindow(windowInfo, excludePids, excludeTitle) {
  if (!windowInfo || (!windowInfo.title && !windowInfo.process_name)) return false;
  if (excludePids.includes(Number(windowInfo.pid))) return true;
  const title = String(windowInfo.title || "").trim();
  if (!title) return false;
  const processName = String(windowInfo.process_name || "").trim().toLowerCase();
  return title === excludeTitle || title === "Akane" || (processName === "electron" && /akane/i.test(title));
}

function runPowerShellWindowProbe() {
  const script = buildWindowProbeScript();
  return new Promise((resolve, reject) => {
    execFile(
      "powershell.exe",
      ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
      {
        timeout: POWERSHELL_TIMEOUT_MS,
        windowsHide: true,
        maxBuffer: 64 * 1024,
      },
      (error, stdout, stderr) => {
        if (error) {
          const detail = [
            stderr?.trim() || "powershell_window_probe_failed",
            error.killed ? "killed=true" : "",
            error.signal ? `signal=${error.signal}` : "",
            error.code ? `code=${error.code}` : "",
            error.message && !String(error.message).includes("-Command") ? error.message : "",
          ]
            .filter(Boolean)
            .join(" | ");
          reject(new Error(detail));
          return;
        }
        const text = String(stdout || "").trim();
        if (!text) {
          reject(new Error("empty_window_probe_output"));
          return;
        }
        try {
          resolve(JSON.parse(text.split(/\r?\n/).pop()));
        } catch (parseError) {
          reject(parseError);
        }
      }
    );
  });
}

function buildWindowProbeScript() {
  return `
$ErrorActionPreference = 'Stop'
Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class AkaneWindowProbe {
  [DllImport("user32.dll")]
  public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll", CharSet=CharSet.Unicode)]
  public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)]
  public static extern int GetWindowTextLength(IntPtr hWnd);
  [DllImport("user32.dll")]
  public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
}
"@
$handle = [AkaneWindowProbe]::GetForegroundWindow()
 $titleLength = [AkaneWindowProbe]::GetWindowTextLength($handle)
 $builder = New-Object System.Text.StringBuilder ([Math]::Max($titleLength + 1, 1))
 [void][AkaneWindowProbe]::GetWindowText($handle, $builder, $builder.Capacity)
 [uint32]$windowProcessId = 0
 [void][AkaneWindowProbe]::GetWindowThreadProcessId($handle, [ref]$windowProcessId)
 $processName = ''
 try {
   $processName = (Get-Process -Id $windowProcessId -ErrorAction Stop).ProcessName
 } catch {}
 [pscustomobject]@{
   title = $builder.ToString().Trim()
   process_name = $processName
   pid = [int]$windowProcessId
   source = 'foreground'
 } | ConvertTo-Json -Compress
`;
}

async function readClipboardContext() {
  const electronText = readElectronClipboardText();
  const windowsResult = process.platform === "win32" ? await readWindowsClipboardText() : { ok: false, text: "" };
  const source = windowsResult.ok ? "windows_clipboard" : "electron_clipboard";
  const raw = windowsResult.ok ? windowsResult.text : electronText;

  const normalized = normalizeText(raw, CLIPBOARD_TEXT_LIMIT);
  const fullNormalized = normalizeText(raw, Number.MAX_SAFE_INTEGER);
  return {
    included: true,
    has_text: Boolean(normalized),
    text: normalized,
    length: fullNormalized.length,
    truncated: fullNormalized.length > normalized.length,
    captured_at: Date.now(),
    source,
    hash: hashText(fullNormalized),
  };
}

function readElectronClipboardText() {
  try {
    const { clipboard } = require("electron");
    return clipboard?.readText?.("clipboard") || "";
  } catch {
    return "";
  }
}

function readWindowsClipboardText() {
  const script = `
$ErrorActionPreference = 'Stop'
$text = ''
try {
  Add-Type -AssemblyName System.Windows.Forms
  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
  $value = [System.Windows.Forms.Clipboard]::GetText([System.Windows.Forms.TextDataFormat]::UnicodeText)
  if ($null -ne $value) { $text = [string]$value }
} catch {
  $text = ''
}
[pscustomobject]@{
  ok = $true
  text = $text
} | ConvertTo-Json -Compress
`;
  return new Promise((resolve) => {
    execFile(
      "powershell.exe",
      ["-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-Command", script],
      {
        timeout: CLIPBOARD_TIMEOUT_MS,
        windowsHide: true,
        maxBuffer: 512 * 1024,
      },
      (error, stdout) => {
        if (error) {
          resolve({ ok: false, text: "" });
          return;
        }
        try {
          const payload = JSON.parse(String(stdout || "").trim().split(/\r?\n/).pop() || "{}");
          resolve({ ok: true, text: String(payload?.text || "") });
        } catch {
          resolve({ ok: false, text: "" });
        }
      }
    );
  });
}

function hashText(text) {
  return crypto.createHash("sha1").update(String(text || ""), "utf8").digest("hex").slice(0, 12);
}

function normalizeWindowInfo(raw) {
  const source = raw && typeof raw === "object" ? raw : {};
  const pid = Number(source.pid);
  return {
    title: normalizeText(source.title, WINDOW_TITLE_LIMIT),
    process_name: normalizeText(source.process_name, PROCESS_NAME_LIMIT),
    pid: Number.isFinite(pid) ? Math.round(pid) : null,
    source: normalizeText(source.source, 40) || "unknown",
  };
}

function normalizeText(value, limit) {
  const normalized = String(value || "")
    .replace(/[\u0000-\u001f\u007f]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!Number.isFinite(limit) || limit <= 0) return normalized;
  return normalized.length > limit ? normalized.slice(0, limit) : normalized;
}

function toPowerShellString(value) {
  return `'${String(value || "").replace(/'/g, "''")}'`;
}

module.exports = { attachDesktopContextTracker, collectDesktopContext, refreshDesktopContextTracker };
