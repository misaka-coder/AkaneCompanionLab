import { invoke } from "@tauri-apps/api/core";
import { emit, emitTo, listen } from "@tauri-apps/api/event";
import { fetch as tauriFetch } from "@tauri-apps/plugin-http";
import { getCurrentWindow } from "@tauri-apps/api/window";
import "./panel.css";

const isTauri = Boolean(window.__TAURI_INTERNALS__);
const appWindow = isTauri ? getCurrentWindow() : null;
const DEFAULT_BACKEND_URL = "http://127.0.0.1:9999";

// ── Runtime state ─────────────────────────────────────────────────────────────
const state = {
  online: false,
  characterName: "Akane",
  emotion: "正常",
  avatarSrc: "",
  musicPlaying: false,
  musicTitle: "",
  musicArtist: "",
  musicPosition: 0,
  musicDuration: 0,
  musicPositionAt: 0,
  musicController: "model",
  muted: false,
  scale: 1,
  opacity: 1,
};

// Local interpolation for smooth progress bar without event spam
let progressRafId = null;

function startProgressInterpolation() {
  if (progressRafId) return;
  function tick() {
    if (!state.musicPlaying || !state.musicDuration) {
      progressRafId = null;
      return;
    }
    const elapsed = state.musicPositionAt > 0
      ? (Date.now() - state.musicPositionAt) / 1000
      : 0;
    const pos = Math.min(state.musicPosition + elapsed, state.musicDuration);
    const pct = (pos / state.musicDuration) * 100;
    els.progressFill.style.transition = "none";
    els.progressFill.style.width = `${pct}%`;
    els.progressTime.textContent =
      `${formatTime(pos)} / ${formatTime(state.musicDuration)}`;
    progressRafId = requestAnimationFrame(tick);
  }
  progressRafId = requestAnimationFrame(tick);
}

function stopProgressInterpolation() {
  if (progressRafId) {
    cancelAnimationFrame(progressRafId);
    progressRafId = null;
  }
}

// ── DOM refs ──────────────────────────────────────────────────────────────────
const els = {
  avatar:        document.getElementById("avatar-img"),
  onlineDot:     document.getElementById("online-dot"),
  charName:      document.getElementById("character-name"),
  charStatus:    document.getElementById("character-status"),
  themeToggle:   document.getElementById("theme-toggle-btn"),
  themeIcon:     document.getElementById("theme-icon"),
  closeBtn:      document.getElementById("close-btn"),
  musicSection:  document.getElementById("music-section"),
  controllerBadge: document.getElementById("music-controller-badge"),
  waveform:      document.getElementById("waveform"),
  musicTitle:    document.getElementById("music-title"),
  musicArtist:   document.getElementById("music-artist"),
  mcPrev:        document.getElementById("mc-prev"),
  mcPlay:        document.getElementById("mc-play"),
  mcNext:        document.getElementById("mc-next"),
  progressFill:  document.getElementById("progress-fill"),
  progressTime:  document.getElementById("progress-time"),
  btnNewSession: document.getElementById("btn-new-session"),
  btnWorkspace:  document.getElementById("btn-workspace"),
  btnShop:       document.getElementById("btn-shop"),
  btnStop:       document.getElementById("btn-stop"),
  btnMute:       document.getElementById("btn-mute"),
  muteLabel:     document.getElementById("mute-label"),
  scaleSlider:   document.getElementById("scale-slider"),
  scaleOutput:   document.getElementById("scale-output"),
  opacitySlider: document.getElementById("opacity-slider"),
  opacityOutput: document.getElementById("opacity-output"),
  recentList:    document.getElementById("recent-list"),
  btnSettings:   document.getElementById("btn-settings"),
  btnWorkshop:   document.getElementById("btn-workshop"),
  btnQuit:       document.getElementById("btn-quit"),
};

// ── Helpers ───────────────────────────────────────────────────────────────────
function formatTime(sec) {
  if (!Number.isFinite(sec) || sec < 0) return "--:--";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

async function tauriCall(command, args = {}) {
  if (!isTauri) return null;
  try {
    return await invoke(command, args);
  } catch {
    return null;
  }
}

async function emitPanelAction(payload) {
  if (!isTauri) return false;
  try {
    await emitTo("main", "panel:action", payload);
    return true;
  } catch {
    try {
      await emit("panel:action", payload);
      return true;
    } catch {
      return false;
    }
  }
}

async function openPanelOwnedWindow(command, action) {
  await tauriCall(command, {});
  await emitPanelAction({ action });
}

function applyTheme(theme) {
  const nextTheme = theme === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = nextTheme;
  try {
    localStorage.setItem("panel-theme", nextTheme);
  } catch {
    // Local storage may be unavailable in restricted webviews.
  }
  if (els.themeToggle) {
    els.themeToggle.title = nextTheme === "dark" ? "切换到浅色主题" : "切换到深色主题";
  }
  if (els.themeIcon) {
    els.themeIcon.innerHTML = nextTheme === "dark"
      ? '<path d="M8 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13Zm0 2a4.5 4.5 0 1 1 0 9 4.5 4.5 0 0 1 0-9Z"/><path d="M8 0v1.1M8 14.9V16M0 8h1.1M14.9 8H16M2.3 2.3l.8.8M12.9 12.9l.8.8M2.3 13.7l.8-.8M12.9 3.1l.8-.8"/>'
      : '<path d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1zm0 12.5A5.5 5.5 0 1 1 8 2.5a5.5 5.5 0 0 1 0 11z"/>';
  }
}

function loadSavedTheme() {
  try {
    return localStorage.getItem("panel-theme") || "light";
  } catch {
    return "light";
  }
}

// ── Waveform ──────────────────────────────────────────────────────────────────
const WAVEFORM_BARS = 38;

function buildWaveform() {
  for (let i = 0; i < WAVEFORM_BARS; i++) {
    const bar = document.createElement("div");
    bar.className = "waveform-bar";
    const maxH = 5 + Math.random() * 21;
    const dur  = (0.35 + Math.random() * 0.55).toFixed(3);
    const delay = (-Math.random() * 1.5).toFixed(3);
    bar.style.setProperty("--max-h",  `${maxH}px`);
    bar.style.setProperty("--dur",    `${dur}s`);
    bar.style.setProperty("--delay",  `${delay}s`);
    els.waveform.appendChild(bar);
  }
}

function setWaveformState(playing) {
  for (const bar of els.waveform.children) {
    bar.style.setProperty("--wave-state", playing ? "running" : "paused");
    bar.classList.toggle("paused", !playing);
  }
}

function updatePlayIcon(playing) {
  const icon = document.getElementById("mc-play-icon");
  if (!icon) return;
  if (playing) {
    // pause bars
    icon.innerHTML = '<rect x="3" y="2" width="3.5" height="12" rx="1.2"/><rect x="9.5" y="2" width="3.5" height="12" rx="1.2"/>';
  } else {
    // play triangle
    icon.innerHTML = '<path d="M4.5 2.5 L13 8 L4.5 13.5 Z"/>';
  }
  els.mcPlay.title = playing ? "暂停" : "播放";
}

// ── Render ────────────────────────────────────────────────────────────────────
function renderStatus() {
  const dot = els.onlineDot;
  dot.classList.toggle("online", state.online);
  els.charName.textContent = state.characterName || "Akane";
  els.charStatus.textContent = state.online
    ? `${state.emotion} · 在线`
    : "未连接";
}

function renderAvatar() {
  if (state.avatarSrc) {
    els.avatar.src = state.avatarSrc;
  }
}

function renderMusic() {
  const hasMusic = state.musicTitle !== "";
  els.musicSection.hidden = !hasMusic;
  if (!hasMusic) {
    stopProgressInterpolation();
    return;
  }

  els.musicTitle.textContent  = state.musicTitle;
  els.musicArtist.textContent = state.musicArtist || "";

  // Reset transition before overriding from server data
  els.progressFill.style.transition = "width 1s linear";
  const pct = state.musicDuration > 0
    ? (state.musicPosition / state.musicDuration) * 100
    : 0;
  els.progressFill.style.width = `${Math.min(100, pct)}%`;
  els.progressTime.textContent = state.musicDuration > 0
    ? `${formatTime(state.musicPosition)} / ${formatTime(state.musicDuration)}`
    : "";

  setWaveformState(state.musicPlaying);
  updatePlayIcon(state.musicPlaying);

  if (state.musicPlaying) {
    startProgressInterpolation();
  } else {
    stopProgressInterpolation();
  }
}

function renderMute() {
  els.muteLabel.textContent = state.muted ? "已静音" : "声音";
  els.btnMute.title = state.muted ? "取消静音" : "静音";
}

function updateControllerBadge(controller) {
  const normalized = controller === "user" ? "user" : "model";
  state.musicController = normalized;
  if (!els.controllerBadge) return;
  els.controllerBadge.textContent = normalized === "model" ? "Akane 控制" : "用户控制";
  els.controllerBadge.dataset.controller = normalized;
  els.controllerBadge.title = normalized === "model"
    ? "Akane 可主动暂停、切歌或推荐；点击切回用户控制"
    : "Akane 会先询问，不主动控制音乐；点击允许 Akane 控制";
}

function renderSliders() {
  els.scaleSlider.value   = String(state.scale);
  els.opacitySlider.value = String(state.opacity);
  els.scaleOutput.value   = `${Math.round(state.scale * 100)}%`;
  els.opacityOutput.value = `${Math.round(state.opacity * 100)}%`;
}

// ── Health poll ───────────────────────────────────────────────────────────────
async function pollHealth() {
  try {
    const res = await tauriFetch(`${DEFAULT_BACKEND_URL}/desktop-pet/health`, {
      method: "GET",
      connectTimeout: 3000,
    });
    const wasOnline = state.online;
    state.online = res.ok;
    if (state.online !== wasOnline) renderStatus();
  } catch {
    if (state.online) {
      state.online = false;
      renderStatus();
    }
  }
}

// ── Event bridge with main window ────────────────────────────────────────────
async function setupEventBridge() {
  if (!isTauri) return;

  await listen("panel:state-update", (event) => {
    const s = event.payload || {};
    let changed = false;

    if (s.characterName !== undefined && s.characterName !== state.characterName) {
      state.characterName = s.characterName;
      changed = true;
    }
    if (s.emotion !== undefined && s.emotion !== state.emotion) {
      state.emotion = s.emotion;
      changed = true;
    }
    if (s.avatarSrc && s.avatarSrc !== state.avatarSrc) {
      state.avatarSrc = s.avatarSrc;
      renderAvatar();
    }
    if (typeof s.musicPlaying === "boolean") state.musicPlaying = s.musicPlaying;
    if (s.musicTitle  !== undefined) state.musicTitle  = s.musicTitle;
    if (s.musicArtist !== undefined) state.musicArtist = s.musicArtist;
    if (typeof s.musicPosition === "number") state.musicPosition = s.musicPosition;
    if (typeof s.musicDuration === "number") state.musicDuration = s.musicDuration;
    if (typeof s.musicPositionAt === "number") state.musicPositionAt = s.musicPositionAt;
    if (s.musicController === "model" || s.musicController === "user") {
      updateControllerBadge(s.musicController);
    }
    if (typeof s.muted === "boolean" && s.muted !== state.muted) {
      state.muted = s.muted;
      renderMute();
    }
    if (typeof s.scale === "number") {
      state.scale = s.scale;
      renderSliders();
    }
    if (typeof s.opacity === "number") {
      state.opacity = s.opacity;
      renderSliders();
    }

    if (changed) renderStatus();
    renderMusic();
  });

  await listen("panel:recent-update", (event) => {
    const items = Array.isArray(event.payload) ? event.payload : [];
    renderRecent(items);
  });
}

function renderRecent(items) {
  els.recentList.innerHTML = "";
  if (!items.length) {
    const li = document.createElement("li");
    li.className = "recent-empty";
    li.textContent = "暂无记录";
    els.recentList.appendChild(li);
    return;
  }
  for (const item of items.slice(0, 5)) {
    const li = document.createElement("li");
    li.className = "recent-item";
    li.textContent = String(item);
    els.recentList.appendChild(li);
  }
}

// ── Button wiring ─────────────────────────────────────────────────────────────
function wireButtons() {
  els.themeToggle?.addEventListener("click", () => {
    const current = document.documentElement.dataset.theme || "light";
    applyTheme(current === "light" ? "dark" : "light");
  });

  els.closeBtn.addEventListener("click", () => {
    appWindow?.close();
  });

  els.btnNewSession.addEventListener("click", async () => {
    await emitPanelAction({ action: "new-session" });
  });

  els.btnWorkspace.addEventListener("click", async () => {
    await openPanelOwnedWindow("open_workspace_window", "open-workspace");
  });

  els.btnShop.addEventListener("click", async () => {
    await openPanelOwnedWindow("open_shop_window", "open-shop");
  });

  els.btnMute.addEventListener("click", async () => {
    state.muted = !state.muted;
    renderMute();
    await emitPanelAction({ action: "toggle-mute", muted: state.muted });
  });

  els.mcPrev.addEventListener("click", () => {
    tauriCall("control_system_media", { action: "previous" });
  });

  els.mcPlay.addEventListener("click", () => {
    const willPlay = !state.musicPlaying;
    tauriCall("control_system_media", { action: state.musicPlaying ? "pause" : "play" });
    // Optimistic icon update; corrected on next state-update from main window
    updatePlayIcon(willPlay);
  });

  els.mcNext.addEventListener("click", () => {
    tauriCall("control_system_media", { action: "next" });
  });

  els.controllerBadge?.addEventListener("click", async () => {
    const next = state.musicController === "model" ? "user" : "model";
    await emitPanelAction({ action: "set-music-controller", controller: next });
  });

  els.btnStop.addEventListener("click", async () => {
    await emitPanelAction({ action: "stop-reply" });
  });

  els.scaleSlider.addEventListener("input", async () => {
    const value = parseFloat(els.scaleSlider.value);
    els.scaleOutput.value = `${Math.round(value * 100)}%`;
    await emitPanelAction({ action: "set-scale", value });
  });

  els.opacitySlider.addEventListener("input", async () => {
    const value = parseFloat(els.opacitySlider.value);
    els.opacityOutput.value = `${Math.round(value * 100)}%`;
    await emitPanelAction({ action: "set-opacity", value });
  });

  els.btnSettings.addEventListener("click", () => {
    tauriCall("open_settings_window", {});
  });

  els.btnWorkshop.addEventListener("click", async () => {
    await openPanelOwnedWindow("open_workshop_window", "open-workshop");
  });

  els.btnQuit.addEventListener("click", async () => {
    await emitPanelAction({ action: "quit" });
  });
}

// ── Init ──────────────────────────────────────────────────────────────────────
async function init() {
  applyTheme(loadSavedTheme());
  buildWaveform();
  renderStatus();
  renderMusic();
  renderMute();
  updateControllerBadge(state.musicController);
  renderSliders();
  wireButtons();
  await setupEventBridge();

  if (isTauri) {
    pollHealth();
    setInterval(pollHealth, 10_000);
    // Signal to main window that panel is ready for a state push
    await emitTo("main", "panel:ready", {}).catch(() => emit("panel:ready", {}));
    await emitPanelAction({ action: "refresh-co-listen" });
    await emitPanelAction({ action: "refresh-music-controller" });
  }
}

init();
