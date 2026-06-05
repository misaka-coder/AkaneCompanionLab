import { invoke } from "@tauri-apps/api/core";
import { emit, listen } from "@tauri-apps/api/event";
import { fetch as tauriFetch } from "@tauri-apps/plugin-http";
import { getCurrentWindow } from "@tauri-apps/api/window";

import { APP_DISPLAY_NAME, CHARACTER_NAME, DEFAULT_EMOTION, DEFAULT_OUTFIT } from "./character-profile.js";
import "./settings.css";

const SETTINGS_COMMAND_EVENT = "akane-next-settings-command";
const SETTINGS_SNAPSHOT_EVENT = "akane-next-settings-snapshot";
const DEFAULT_BACKEND_URL = "http://127.0.0.1:9999";
const MAX_CHARACTER_PACK_ZIP_BYTES = 300 * 1024 * 1024;
const SCALE_PRESETS = [0.85, 1, 1.15, 1.3];
const OPACITY_PRESETS = [1, 0.85, 0.7, 0.55];
const isTauriRuntime = Boolean(window.__TAURI_INTERNALS__);
const appWindow = isTauriRuntime ? getCurrentWindow() : null;

const els = {
  tabs: document.querySelector("#settings-tabs"),
  pages: document.querySelector(".settings-pages"),
  windowMinimize: document.querySelector("#settings-window-minimize"),
  windowMaximize: document.querySelector("#settings-window-maximize"),
  windowClose: document.querySelector("#settings-window-close"),
  sidebarStatusCard: document.querySelector(".sidebar-status-card"),
  sidebarStatus: document.querySelector("#sidebar-status"),
  sidebarMeta: document.querySelector("#sidebar-meta"),
  overviewAvatar: document.querySelector("#overview-avatar"),
  overviewCharacterName: document.querySelector("#overview-character-name"),
  overviewBackendBadge: document.querySelector("#overview-backend-badge"),
  overviewDetails: document.querySelector("#overview-details"),
  overviewAbilities: document.querySelector("#overview-abilities"),
  overviewTools: document.querySelector("#overview-tools"),
  summary: document.querySelector("#settings-summary"),
  title: document.querySelector(".settings-header h1"),
  characterPack: document.querySelector("#character-pack"),
  saveCharacterPack: document.querySelector("#save-character-pack"),
  characterPackZip: document.querySelector("#character-pack-zip"),
  chooseCharacterPackZip: document.querySelector("#choose-character-pack-zip"),
  overwriteCharacterPack: document.querySelector("#overwrite-character-pack"),
  openCharacterWorkshop: document.querySelector("#open-character-workshop"),
  openCharacterPacksFolder: document.querySelector("#open-character-packs-folder"),
  copyCharacterPackPath: document.querySelector("#copy-character-pack-path"),
  characterImportDropzone: document.querySelector("#character-import-dropzone"),
  characterImportStatus: document.querySelector("#character-import-status"),
  characterPackList: document.querySelector("#character-pack-list"),
  characterDetails: document.querySelector("#character-details"),
  characterMetrics: document.querySelector("#character-metrics"),
  characterHeroPreview: document.querySelector("#character-hero-preview"),
  characterHeroPills: document.querySelector("#character-hero-pills"),
  openInput: document.querySelector("#open-input"),
  backendUrl: document.querySelector("#backend-url"),
  saveBackend: document.querySelector("#save-backend"),
  outfit: document.querySelector("#outfit"),
  saveOutfit: document.querySelector("#save-outfit"),
  resourceAlert: document.querySelector("#resource-alert"),
  restoreLatest: document.querySelector("#restore-latest"),
  sessionId: document.querySelector("#session-id"),
  copySession: document.querySelector("#copy-session"),
  activityStatus: document.querySelector("#activity-status"),
  newSession: document.querySelector("#new-session"),
  openWorkspace: document.querySelector("#open-workspace"),
  stopReply: document.querySelector("#stop-reply"),
  checkConnection: document.querySelector("#check-connection"),
  reloadResources: document.querySelector("#reload-resources"),
  scale: document.querySelector("#scale"),
  scaleOutput: document.querySelector("#scale-output"),
  scalePresets: document.querySelector("#scale-presets"),
  opacity: document.querySelector("#opacity"),
  opacityOutput: document.querySelector("#opacity-output"),
  opacityPresets: document.querySelector("#opacity-presets"),
  voiceEnabled: document.querySelector("#voice-enabled"),
  voiceInputEnabled: document.querySelector("#voice-input-enabled"),
  voiceVolume: document.querySelector("#voice-volume"),
  voiceVolumeOutput: document.querySelector("#voice-volume-output"),
  voiceStatusSummary: document.querySelector("#voice-status-summary"),
  voiceStatusPills: document.querySelector("#voice-status-pills"),
  testTts: document.querySelector("#test-tts"),
  stopTts: document.querySelector("#stop-tts"),
  musicPageSummary: document.querySelector("#music-page-summary"),
  musicPagePills: document.querySelector("#music-page-pills"),
  musicStatus: document.querySelector("#music-status"),
  musicLyric: document.querySelector("#music-lyric"),
  musicQueue: document.querySelector("#music-queue"),
  previousMusic: document.querySelector("#previous-music"),
  nextMusic: document.querySelector("#next-music"),
  toggleMusic: document.querySelector("#toggle-music"),
  stopMusic: document.querySelector("#stop-music"),
  clearMusicQueue: document.querySelector("#clear-music-queue"),
  desktopContextEnabled: document.querySelector("#desktop-context-enabled"),
  clipboardContextEnabled: document.querySelector("#clipboard-context-enabled"),
  screenVisionEnabled: document.querySelector("#screen-vision-enabled"),
  screenVisionMode: document.querySelector("#screen-vision-mode"),
  proactiveWakeEnabled: document.querySelector("#proactive-wake-enabled"),
  proactiveWakeInterval: document.querySelector("#proactive-wake-interval"),
  screenVisionIntervalRow: document.querySelector("#screen-vision-interval-row"),
  screenVisionInterval: document.querySelector("#screen-vision-interval"),
  screenVisionFrameCount: document.querySelector("#screen-vision-frame-count"),
  applyVisionRecommendation: document.querySelector("#apply-vision-recommendation"),
  clearScreenVision: document.querySelector("#clear-screen-vision"),
  desktopContextNote: document.querySelector("#desktop-context-note"),
  contextStatusSummary: document.querySelector("#context-status-summary"),
  contextStatusPills: document.querySelector("#context-status-pills"),
  alwaysOnTop: document.querySelector("#always-on-top"),
  skipTaskbar: document.querySelector("#skip-taskbar"),
  advancedStatusSummary: document.querySelector("#advanced-status-summary"),
  advancedStatusPills: document.querySelector("#advanced-status-pills"),
  resetVisuals: document.querySelector("#reset-visuals"),
  resetPlacement: document.querySelector("#reset-placement"),
  resourceDetails: document.querySelector("#resource-details"),
  resourceMetrics: document.querySelector("#resource-metrics"),
  outfitList: document.querySelector("#outfit-list"),
  refreshDiagnostics: document.querySelector("#refresh-diagnostics"),
  diagnosticsSummary: document.querySelector("#diagnostics-summary"),
  diagnosticsMetrics: document.querySelector("#diagnostics-metrics"),
  diagnosticsTools: document.querySelector("#diagnostics-tools"),
  emotionGrid: document.querySelector("#emotion-grid"),
  hitTest: document.querySelector("#hit-test"),
  hitboxOverlay: document.querySelector("#hitbox-overlay"),
  toggleWebgl: document.querySelector("#toggle-webgl"),
  probeClickThrough: document.querySelector("#probe-click-through"),
  resetWindow: document.querySelector("#reset-window"),
  closePet: document.querySelector("#close-pet"),
  connectionStatus: document.querySelector("#connection-status"),
  runtimeStatus: document.querySelector("#runtime-status")
};

const view = {
  state: null,
  character: null,
  resource: null,
  active: null,
  music: null,
  activePage: "overview",
  diagnostics: null,
  diagnosticsLoading: false,
  diagnosticsAutoKey: "",
  diagnosticsTimer: 0,
  webglEnabled: false,
  lastCharacterImportPath: "",
  scaleTimer: 0,
  opacityTimer: 0
};

boot();

async function boot() {
  bindUi();
  await bindNativeDropHandlers();
  renderPresetChips();

  if (!isTauriRuntime) {
    applySnapshot(buildBrowserPreviewSnapshot());
    setStatus("浏览器预览模式");
    return;
  }

  try {
    const state = await invoke("load_pet_state");
    applySnapshot({
      state,
      resource: {
        health: "unknown",
        source: "bundled",
        activeOutfit: state?.outfit || DEFAULT_OUTFIT,
        emotionCount: 0,
        outfits: [],
        emotions: []
      },
      runtimeStatus: "等待桌宠同步"
    });
  } catch (error) {
    setStatus(`读取设置失败：${formatError(error)}`);
  }

  await listen(SETTINGS_SNAPSHOT_EVENT, (event) => {
    applySnapshot(event.payload);
  });
  await sendCommand("requestSnapshot");
}

function bindUi() {
  bindWindowChrome();
  els.tabs.addEventListener("click", (event) => {
    const button = event.target.closest("[data-settings-tab]");
    if (!button) return;
    setActiveSettingsPage(button.dataset.settingsTab);
  });
  els.openInput.addEventListener("click", () => sendCommand("openInput"));
  els.saveBackend.addEventListener("click", () => saveBackendUrl());
  els.saveCharacterPack.addEventListener("click", () => saveCharacterPack());
  els.characterPack.addEventListener("change", () => updateCharacterPackButton());
  els.chooseCharacterPackZip.addEventListener("click", () => els.characterPackZip.click());
  els.openCharacterWorkshop.addEventListener("click", () => openCharacterWorkshop());
  els.openCharacterPacksFolder.addEventListener("click", () => openCharacterPacksFolder());
  els.copyCharacterPackPath.addEventListener("click", () => copyCharacterPackPath());
  els.characterPackZip.addEventListener("change", () => {
    const file = els.characterPackZip.files?.[0];
    if (file) {
      void importCharacterPackZipFile(file);
    }
    els.characterPackZip.value = "";
  });
  els.characterImportDropzone.addEventListener("dragover", (event) => {
    event.preventDefault();
    els.characterImportDropzone.classList.add("is-dragging");
  });
  els.characterImportDropzone.addEventListener("dragleave", () => {
    els.characterImportDropzone.classList.remove("is-dragging");
  });
  els.characterImportDropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    els.characterImportDropzone.classList.remove("is-dragging");
    const file = [...(event.dataTransfer?.files || [])].find((item) => isZipName(item.name));
    if (file) void importCharacterPackZipFile(file);
  });
  els.backendUrl.addEventListener("keydown", (event) => {
    if (event.isComposing) return;
    if (event.key === "Enter") {
      event.preventDefault();
      saveBackendUrl();
    }
  });

  els.saveOutfit.addEventListener("click", () => saveOutfit());
  els.outfit.addEventListener("keydown", (event) => {
    if (event.isComposing) return;
    if (event.key === "Enter") {
      event.preventDefault();
      saveOutfit();
    }
  });

  els.newSession.addEventListener("click", () => sendCommand("newSession"));
  els.openWorkspace.addEventListener("click", () => sendCommand("openWorkspace"));
  els.stopReply.addEventListener("click", () => sendCommand("stopReply"));
  els.checkConnection.addEventListener("click", () => sendCommand("reloadResources"));
  els.copySession.addEventListener("click", () => copySessionId());
  els.reloadResources.addEventListener("click", () => sendCommand("reloadResources"));
  els.refreshDiagnostics.addEventListener("click", () => refreshDiagnostics());
  els.restoreLatest.addEventListener("change", () =>
    sendCommand("setRestoreLatestOnStartup", els.restoreLatest.checked)
  );
  els.outfitList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-outfit]");
    if (!button) return;
    const outfit = String(button.dataset.outfit || "").trim();
    if (outfit) {
      setInputIfIdle(els.outfit, outfit);
      sendCommand("setOutfit", outfit);
    }
  });

  els.scale.addEventListener("input", () => {
    updateOutput(els.scaleOutput, els.scale.value);
    scheduleValueCommand("scaleTimer", "setScale", Number(els.scale.value), 120);
  });
  els.opacity.addEventListener("input", () => {
    updateOutput(els.opacityOutput, els.opacity.value);
    scheduleValueCommand("opacityTimer", "setOpacity", Number(els.opacity.value), 80);
  });

  els.scalePresets.addEventListener("click", (event) => {
    const button = event.target.closest("[data-scale]");
    if (!button) return;
    const value = Number(button.dataset.scale);
    els.scale.value = String(value);
    updateOutput(els.scaleOutput, value);
    sendCommand("setScale", value);
  });
  els.opacityPresets.addEventListener("click", (event) => {
    const button = event.target.closest("[data-opacity]");
    if (!button) return;
    const value = Number(button.dataset.opacity);
    els.opacity.value = String(value);
    updateOutput(els.opacityOutput, value);
    sendCommand("setOpacity", value);
  });

  els.alwaysOnTop.addEventListener("change", () => sendCommand("setAlwaysOnTop", els.alwaysOnTop.checked));
  els.skipTaskbar.addEventListener("change", () => sendCommand("setSkipTaskbar", els.skipTaskbar.checked));
  els.resetVisuals.addEventListener("click", () => sendCommand("resetVisuals"));
  els.resetPlacement.addEventListener("click", () => sendCommand("resetWindow"));
  els.voiceEnabled.addEventListener("change", () => sendCommand("setVoiceEnabled", els.voiceEnabled.checked));
  els.voiceInputEnabled.addEventListener("change", () =>
    sendCommand("setVoiceInputEnabled", els.voiceInputEnabled.checked)
  );
  els.voiceVolume.addEventListener("input", () => {
    updateOutput(els.voiceVolumeOutput, els.voiceVolume.value);
    scheduleValueCommand("voiceVolumeTimer", "setVoiceVolume", Number(els.voiceVolume.value), 80);
  });
  els.testTts.addEventListener("click", () => sendCommand("testTts"));
  els.stopTts.addEventListener("click", () => sendCommand("stopTts"));
  els.previousMusic.addEventListener("click", () => sendCommand("previousMusic"));
  els.nextMusic.addEventListener("click", () => sendCommand("nextMusic"));
  els.toggleMusic.addEventListener("click", () => sendCommand("toggleMusic"));
  els.stopMusic.addEventListener("click", () => sendCommand("stopMusic"));
  els.clearMusicQueue.addEventListener("click", () => sendCommand("clearMusicQueue"));
  els.musicQueue.addEventListener("click", (event) => {
    const button = event.target.closest("[data-music-action]");
    if (!button) return;
    const sourceId = String(button.closest("[data-source-id]")?.dataset.sourceId || "").trim();
    if (!sourceId) return;
    if (button.dataset.musicAction === "play") sendCommand("playMusicTrack", sourceId);
    if (button.dataset.musicAction === "remove") sendCommand("removeMusicTrack", sourceId);
  });
  els.desktopContextEnabled.addEventListener("change", () =>
    sendCommand("setDesktopContextEnabled", els.desktopContextEnabled.checked)
  );
  els.clipboardContextEnabled.addEventListener("change", () =>
    sendCommand("setClipboardContextEnabled", els.clipboardContextEnabled.checked)
  );
  els.screenVisionEnabled.addEventListener("change", () =>
    sendCommand("setScreenVisionEnabled", els.screenVisionEnabled.checked)
  );
  els.screenVisionMode.addEventListener("change", () => sendCommand("setScreenVisionMode", els.screenVisionMode.value));
  els.proactiveWakeEnabled.addEventListener("change", () =>
    sendCommand("setProactiveWakeEnabled", els.proactiveWakeEnabled.checked)
  );
  els.proactiveWakeInterval.addEventListener("change", () =>
    sendCommand("setProactiveWakeIntervalSec", Number(els.proactiveWakeInterval.value))
  );
  els.screenVisionInterval.addEventListener("change", () =>
    sendCommand("setScreenVisionIntervalSec", Number(els.screenVisionInterval.value))
  );
  els.screenVisionFrameCount.addEventListener("change", () =>
    sendCommand("setScreenVisionFrameCount", Number(els.screenVisionFrameCount.value))
  );
  els.applyVisionRecommendation.addEventListener("click", () => {
    const recommended = Number(view.state?.recommendedScreenVisionIntervalSec || 25);
    els.screenVisionInterval.value = String(recommended);
    sendCommand("setScreenVisionIntervalSec", recommended);
  });
  els.clearScreenVision.addEventListener("click", () => sendCommand("clearScreenVision"));
  els.hitTest.addEventListener("change", () => sendCommand("setHitTestEnabled", els.hitTest.checked));
  els.hitboxOverlay.addEventListener("change", () => sendCommand("setHitboxOverlay", els.hitboxOverlay.checked));

  els.emotionGrid.addEventListener("click", (event) => {
    const button = event.target.closest("[data-emotion]");
    if (!button) return;
    sendCommand("previewEmotion", button.dataset.emotion);
  });

  els.toggleWebgl.addEventListener("click", () => sendCommand("toggleWebgl"));
  els.probeClickThrough.addEventListener("click", () => sendCommand("probeClickThrough"));
  els.resetWindow.addEventListener("click", () => sendCommand("resetWindow"));
  els.closePet.addEventListener("click", () => sendCommand("closePet"));
}

function bindWindowChrome() {
  els.windowMinimize?.addEventListener("click", () => {
    void runWindowAction(() => appWindow?.minimize?.());
  });
  els.windowMaximize?.addEventListener("click", () => {
    void runWindowAction(async () => {
      if (typeof appWindow?.toggleMaximize === "function") {
        await appWindow.toggleMaximize();
        return;
      }
      if (typeof appWindow?.isMaximized === "function" && typeof appWindow?.unmaximize === "function") {
        if (await appWindow.isMaximized()) {
          await appWindow.unmaximize();
          return;
        }
      }
      await appWindow?.maximize?.();
    });
  });
  els.windowClose?.addEventListener("click", () => {
    void runWindowAction(() => appWindow?.close?.());
  });
}

async function runWindowAction(action) {
  if (!appWindow) return;
  try {
    await action();
  } catch (error) {
    setStatus(`窗口操作失败：${formatError(error)}`);
  }
}

async function bindNativeDropHandlers() {
  if (!appWindow?.onDragDropEvent) return;
  try {
    await appWindow.onDragDropEvent((event) => {
      const payload = event?.payload || {};
      const type = String(payload.type || "").toLowerCase();
      if (type === "drop") {
        els.characterImportDropzone.classList.remove("is-dragging");
        const zipPath = (payload.paths || []).find((item) => isZipName(item));
        if (zipPath) {
          void importCharacterPackZipPath(zipPath);
        }
        return;
      }
      if (type === "over" || type === "enter") {
        els.characterImportDropzone.classList.add("is-dragging");
        return;
      }
      els.characterImportDropzone.classList.remove("is-dragging");
    });
  } catch {
    // Native drag/drop is a convenience path; the file input still works.
  }
}

function saveBackendUrl() {
  sendCommand("setBackendUrl", normalizeBackendUrl(els.backendUrl.value));
}

function saveCharacterPack() {
  const value = String(els.characterPack.value || "").trim();
  if (!value) return;
  setStatus("正在应用角色包");
  sendCommand("setCharacterPack", value);
}

async function importCharacterPackZipFile(file) {
  if (!file || !isZipName(file.name)) {
    setCharacterImportStatus("请选择 .zip 角色包");
    return;
  }
  if (file.size > MAX_CHARACTER_PACK_ZIP_BYTES) {
    setCharacterImportStatus("角色包 zip 暂时请控制在 300MB 以内");
    return;
  }

  setCharacterImportStatus(`正在导入：${file.name}`);
  els.chooseCharacterPackZip.disabled = true;
  try {
    const bytes = Array.from(new Uint8Array(await file.arrayBuffer()));
    const result = await invoke("install_character_pack_zip_bytes", {
      fileName: file.name,
      bytes,
      overwrite: els.overwriteCharacterPack.checked
    });
    handleCharacterPackInstallResult(result);
  } catch (error) {
    setCharacterImportStatus(`导入失败：${formatError(error)}`);
  } finally {
    els.chooseCharacterPackZip.disabled = false;
  }
}

async function importCharacterPackZipPath(path) {
  if (!isZipName(path)) {
    setCharacterImportStatus("拖入的不是 .zip 角色包");
    return;
  }

  setCharacterImportStatus(`正在导入：${shortPath(path)}`);
  els.chooseCharacterPackZip.disabled = true;
  try {
    const result = await invoke("install_character_pack_zip_file", {
      path,
      overwrite: els.overwriteCharacterPack.checked
    });
    handleCharacterPackInstallResult(result);
  } catch (error) {
    setCharacterImportStatus(`导入失败：${formatError(error)}`);
  } finally {
    els.chooseCharacterPackZip.disabled = false;
  }
}

function handleCharacterPackInstallResult(result) {
  const packId = String(result?.packId || "").trim();
  const name = String(result?.characterName || result?.characterId || packId || "角色包").trim();
  const warning = Array.isArray(result?.warnings) && result.warnings.length ? ` · ${result.warnings[0]}` : "";
  view.lastCharacterImportPath = String(result?.installedPath || "").trim();
  els.copyCharacterPackPath.disabled = !view.lastCharacterImportPath;
  setCharacterImportStatus(`${name} 已安装到 characters/${packId}${warning} · 正在刷新并应用`);
  void sendCommand("refreshCharacterPacks", { selectPackId: packId, apply: true });
}

function saveOutfit() {
  sendCommand("setOutfit", normalizeOutfitName(els.outfit.value));
}

async function openCharacterPacksFolder() {
  try {
    await invoke("open_character_packs_folder");
    setCharacterImportStatus("已打开角色包目录");
  } catch (error) {
    setCharacterImportStatus(`打开目录失败：${formatError(error)}`);
  }
}

async function openCharacterWorkshop() {
  try {
    await invoke("open_workshop_window");
    setCharacterImportStatus("已打开角色工坊");
  } catch (error) {
    setCharacterImportStatus(`打开工坊失败：${formatError(error)}`);
  }
}

async function copyCharacterPackPath() {
  const path = String(view.lastCharacterImportPath || "").trim();
  if (!path) {
    setCharacterImportStatus("还没有可复制的导入路径");
    return;
  }
  try {
    await navigator.clipboard.writeText(path);
    setCharacterImportStatus("角色包安装路径已复制");
  } catch (error) {
    setCharacterImportStatus(`复制路径失败：${formatError(error)}`);
  }
}

async function copySessionId() {
  const sessionId = String(view.state?.sessionId || "").trim();
  if (!sessionId) {
    setStatus("没有可复制的会话");
    return;
  }

  try {
    await navigator.clipboard.writeText(sessionId);
    setStatus("会话 ID 已复制");
  } catch (error) {
    setStatus(`复制失败：${formatError(error)}`);
  }
}

function scheduleValueCommand(timerKey, command, value, delay) {
  window.clearTimeout(view[timerKey]);
  view[timerKey] = window.setTimeout(() => {
    sendCommand(command, value);
  }, delay);
}

async function sendCommand(command, value = null) {
  if (!isTauriRuntime) {
    setStatus(`浏览器预览：${command}`);
    return;
  }
  try {
    await emit(SETTINGS_COMMAND_EVENT, { command, value });
  } catch (error) {
    setStatus(`发送命令失败：${formatError(error)}`);
  }
}

function buildBrowserPreviewSnapshot() {
  return {
    state: {
      backendUrl: DEFAULT_BACKEND_URL,
      sessionId: "browser-preview",
      outfit: DEFAULT_OUTFIT,
      currentEmotion: DEFAULT_EMOTION,
      restoreLatestOnStartup: true,
      scale: 1,
      opacity: 1,
      voiceEnabled: true,
      voiceInputEnabled: true,
      voiceVolume: 0.85,
      desktopContextEnabled: true,
      clipboardContextEnabled: false,
      screenVisionEnabled: false,
      screenVisionMode: "summary",
      proactiveWakeEnabled: false,
      proactiveWakeIntervalSec: 30,
      screenVisionIntervalSec: 25,
      screenVisionFrameCount: 4,
      recommendedScreenVisionIntervalSec: 25,
      alwaysOnTop: true,
      skipTaskbar: false,
      hitTestEnabled: false,
      hitboxOverlay: false
    },
    character: {
      appName: APP_DISPLAY_NAME,
      name: CHARACTER_NAME,
      id: "akane_preview",
      packId: "browser_preview",
      schemaVersion: "preview",
      defaultOutfit: DEFAULT_OUTFIT,
      defaultEmotion: DEFAULT_EMOTION,
      musicEmotion: "听歌中",
      userTitle: "主人",
      localLineCount: 0,
      assetSource: "浏览器预览",
      availablePacks: []
    },
    resource: {
      health: "unknown",
      source: "bundled",
      activeOutfit: DEFAULT_OUTFIT,
      activeOutfitName: DEFAULT_OUTFIT,
      requestedOutfit: DEFAULT_OUTFIT,
      emotionCount: 0,
      outfits: [],
      emotions: [],
      missingRequired: [],
      missingRecommended: [],
      tts: { enabled: true, endpoint: "/tts" },
      asr: { available: true, endpoint: "/asr" }
    },
    active: {},
    runtimeStatus: "浏览器预览模式"
  };
}

function applySnapshot(snapshot) {
  if (!snapshot || typeof snapshot !== "object") return;
  view.state = { ...(view.state || {}), ...(snapshot.state || {}) };
  view.character = { ...(view.character || {}), ...(snapshot.character || {}) };
  view.resource = { ...(view.resource || {}), ...(snapshot.resource || {}) };
  view.active = { ...(view.active || {}), ...(snapshot.active || {}) };
  view.music = snapshot.music || view.music || null;
  view.webglEnabled = Boolean(snapshot.webglEnabled);

  const state = view.state || {};
  const resource = view.resource || {};
  const scale = clamp(Number(state.scale ?? 1), 0.75, 1.45);
  const opacity = clamp(Number(state.opacity ?? 1), 0.55, 1);
  const voiceVolume = clamp(Number(state.voiceVolume ?? 0.85), 0, 1);

  setInputIfIdle(els.backendUrl, state.backendUrl || DEFAULT_BACKEND_URL);
  renderCharacterPackSelect();
  setInputIfIdle(els.outfit, state.outfit || resource.activeOutfit || DEFAULT_OUTFIT);
  els.scale.value = String(scale);
  els.opacity.value = String(opacity);
  els.voiceVolume.value = String(voiceVolume);
  updateOutput(els.scaleOutput, scale);
  updateOutput(els.opacityOutput, opacity);
  updateOutput(els.voiceVolumeOutput, voiceVolume);

  els.alwaysOnTop.checked = Boolean(state.alwaysOnTop);
  els.skipTaskbar.checked = Boolean(state.skipTaskbar);
  els.restoreLatest.checked = Boolean(state.restoreLatestOnStartup ?? true);
  els.voiceEnabled.checked = Boolean(state.voiceEnabled);
  els.voiceInputEnabled.checked = Boolean(state.voiceInputEnabled ?? true);
  els.desktopContextEnabled.checked = Boolean(state.desktopContextEnabled ?? true);
  els.clipboardContextEnabled.checked = Boolean(state.clipboardContextEnabled);
  els.clipboardContextEnabled.disabled = !els.desktopContextEnabled.checked;
  els.screenVisionEnabled.checked = Boolean(state.screenVisionEnabled);
  els.screenVisionMode.value = String(state.screenVisionMode || "summary");
  els.proactiveWakeEnabled.checked = Boolean(state.proactiveWakeEnabled);
  setInputIfIdle(els.proactiveWakeInterval, state.proactiveWakeIntervalSec || 30);
  setInputIfIdle(els.screenVisionInterval, state.screenVisionIntervalSec || state.recommendedScreenVisionIntervalSec || 25);
  setInputIfIdle(els.screenVisionFrameCount, state.screenVisionFrameCount || 4);
  updateScreenVisionIntervalControls(state);
  els.hitTest.checked = Boolean(state.hitTestEnabled);
  els.hitboxOverlay.checked = Boolean(state.hitboxOverlay);

  renderPresetChips();
  renderCharacterDetails();
  renderCharacterMetrics();
  renderCharacterPackList();
  renderResourceAlert();
  renderResourceDetails();
  renderResourceMetrics();
  renderOutfitList();
  renderDiagnostics();
  renderOverview();
  scheduleDiagnosticsAutoRefresh();
  renderEmotionGrid();

  const source = sourceLabel(resource.source);
  els.summary.textContent = `${view.character?.name || CHARACTER_NAME} · ${resource.activeOutfit || DEFAULT_OUTFIT} · ${source} · ${state.currentEmotion || DEFAULT_EMOTION}`;
  els.sessionId.textContent = state.sessionId || "-";
  els.sessionId.title = state.sessionId || "";
  els.copySession.disabled = !state.sessionId;
  els.activityStatus.textContent = buildActivityLine(snapshot);
  renderMusicStatus(view.music);
  els.desktopContextNote.textContent = buildDesktopContextNote(state);
  els.connectionStatus.textContent = buildConnectionLine(resource);
  els.runtimeStatus.textContent = snapshot.runtimeStatus || "Ready";
  els.toggleWebgl.textContent = view.webglEnabled ? "隐藏 WebGL" : "WebGL";
  els.stopTts.disabled = !snapshot.tts?.active;
  els.stopReply.disabled = !isReplyActive(snapshot);
  els.testTts.disabled = resource.tts?.enabled === false;
  els.testTts.textContent = state.voiceEnabled ? "测试语音" : "开启并测试";
  renderPageStatusCards();
}

function setActiveSettingsPage(page) {
  const next = String(page || "overview").trim() || "overview";
  const target = document.querySelector(`[data-settings-page="${cssEscape(next)}"]`);
  if (!target) return;
  view.activePage = next;
  for (const button of els.tabs.querySelectorAll("[data-settings-tab]")) {
    button.classList.toggle("active", button.dataset.settingsTab === next);
  }
  for (const pageElement of els.pages.querySelectorAll("[data-settings-page]")) {
    const active = pageElement.dataset.settingsPage === next;
    pageElement.hidden = !active;
    pageElement.classList.toggle("active", active);
  }
}

function renderPresetChips() {
  renderChips(els.scalePresets, SCALE_PRESETS, "scale", Number(view.state?.scale ?? 1));
  renderChips(els.opacityPresets, OPACITY_PRESETS, "opacity", Number(view.state?.opacity ?? 1));
}

function renderChips(container, values, key, activeValue) {
  container.replaceChildren(
    ...values.map((value) => {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset[key] = String(value);
      button.textContent = `${Math.round(value * 100)}%`;
      button.classList.toggle("active", Math.abs(value - activeValue) < 0.001);
      return button;
    })
  );
}

function renderCharacterDetails() {
  if (!els.characterDetails) return;
  const character = view.character || {};
  const name = String(character.name || CHARACTER_NAME);
  const id = String(character.id || "-");
  const appName = String(character.appName || APP_DISPLAY_NAME || name);
  const schema = String(character.schemaVersion || "-");
  const source = String(character.source || "内置角色包");
  document.title = `${appName} 设置`;
  if (els.title) els.title.textContent = appName;
  const pack = String(character.packId || "-");
  els.characterDetails.textContent = `${appName} · ${name} · ${id} · ${pack} · ${schema}`;
  els.characterDetails.title = source;
}

function renderCharacterPackSelect() {
  if (!els.characterPack) return;
  const packs = getCharacterPacks();
  const active = getActiveCharacterPackId();
  if (!packs.length) {
    els.characterPack.replaceChildren(new Option("暂无角色包", ""));
    els.characterPack.disabled = true;
    updateCharacterPackButton();
    return;
  }

  els.characterPack.disabled = false;
  els.characterPack.replaceChildren(
    ...packs.map((pack) => {
      const label = `${pack.appName || pack.name || pack.id} · ${pack.id}`;
      return new Option(label, pack.id);
    })
  );
  if (active) {
    els.characterPack.value = active;
  }
  updateCharacterPackButton();
}

function renderCharacterPackList() {
  if (!els.characterPackList) return;
  const packs = getCharacterPacks();
  if (!packs.length) {
    els.characterPackList.textContent = "暂无角色包。";
    return;
  }

  const active = getActiveCharacterPackId();
  els.characterPackList.replaceChildren(
    ...packs.map((pack) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "character-pack-card";
      button.classList.toggle("active", pack.id === active || Boolean(pack.selected));
      button.dataset.characterPack = pack.id;
      button.addEventListener("click", () => {
        els.characterPack.value = pack.id;
        updateCharacterPackButton();
      });
      button.append(
        buildText("strong", pack.appName || pack.name || pack.id),
        buildText("span", `${pack.name || pack.characterId || "-"} · ${pack.defaultOutfit || "-"}`),
        buildText("small", `${pack.defaultEmotion || "-"} · ${pack.assetSource || "-"}`)
      );
      return button;
    })
  );
}

function updateCharacterPackButton() {
  if (!els.saveCharacterPack || !els.characterPack) return;
  const active = getActiveCharacterPackId();
  const selected = String(els.characterPack.value || "").trim();
  els.saveCharacterPack.disabled = !selected || selected === active;
}

function renderCharacterMetrics() {
  if (!els.characterMetrics) return;
  const character = view.character || {};
  const rows = [
    ["称呼", character.userTitle || "-"],
    ["默认服装", character.defaultOutfit || DEFAULT_OUTFIT],
    ["默认表情", character.defaultEmotion || DEFAULT_EMOTION],
    ["音乐表情", character.musicEmotion || "-"],
    ["本地台词", `${character.localLineCount || 0}`],
    ["资源模式", character.assetSource || "-"]
  ];

  els.characterMetrics.replaceChildren(
    ...rows.map(([label, value]) => {
      const item = document.createElement("div");
      item.className = "metric";
      const key = document.createElement("span");
      key.textContent = label;
      const data = document.createElement("strong");
      data.textContent = value;
      item.append(key, data);
      return item;
    })
  );
}

function getCharacterPacks() {
  const packs = Array.isArray(view.character?.availablePacks) ? view.character.availablePacks : [];
  return packs.filter((pack) => pack && typeof pack === "object" && pack.id);
}

function getActiveCharacterPackId() {
  return String(view.character?.packId || view.state?.characterPackId || "").trim();
}

function renderOverview() {
  const state = view.state || {};
  const resource = view.resource || {};
  const character = view.character || {};
  const name = String(character.appName || character.name || CHARACTER_NAME);
  const health = String(resource.health || "unknown");
  const emotion = String(state.currentEmotion || character.defaultEmotion || DEFAULT_EMOTION);
  const outfit = String(resource.activeOutfit || state.outfit || DEFAULT_OUTFIT);
  const source = sourceLabel(resource.source);

  if (els.overviewCharacterName) {
    els.overviewCharacterName.textContent = `${name} 状态卡`;
  }
  if (els.overviewBackendBadge) {
    els.overviewBackendBadge.dataset.health = health;
    els.overviewBackendBadge.textContent = `后端 ${healthLabel(health)}`;
  }
  if (els.overviewDetails) {
    const emotionCount = Number(resource.emotionCount || 0);
    const activity = buildActivityLine({ active: view.active, runtimeMode: "", music: view.music }).replace(/^状态：/, "");
    els.overviewDetails.textContent = `${outfit} · ${emotion} · ${source} · ${emotionCount} 表情 · ${activity}`;
  }
  renderOverviewAvatar(emotion);
  renderCharacterHeroPreview(emotion);
  renderSidebarStatus(health, name, outfit, emotion);
  renderPillRow(els.characterHeroPills, buildCharacterHeroPills(state, resource, character), "等待角色包状态");
  renderPillRow(els.overviewAbilities, buildOverviewAbilityPills(state, resource), "等待能力状态");
  renderPillRow(els.overviewTools, buildOverviewToolPills(), "能力诊断会在后端连接后显示");
}

function renderOverviewAvatar(activeEmotion) {
  if (!els.overviewAvatar) return;
  applyEmotionPreview(els.overviewAvatar, activeEmotion, 1);
}

function renderCharacterHeroPreview(activeEmotion) {
  if (!els.characterHeroPreview) return;
  applyEmotionPreview(els.characterHeroPreview, activeEmotion, 1);
}

function applyEmotionPreview(element, activeEmotion, fallbackLength) {
  const entry = resolveEmotionPreview(activeEmotion);
  const url = String(entry?.url || "").trim();
  if (url) {
    element.style.backgroundImage = `url("${url}")`;
    element.textContent = "";
    return;
  }
  element.style.backgroundImage = "";
  element.textContent = String(view.character?.name || CHARACTER_NAME)
    .trim()
    .slice(0, fallbackLength || 1) || "A";
}

function resolveEmotionPreview(activeEmotion) {
  const emotions = Array.isArray(view.resource?.emotions) ? view.resource.emotions : [];
  return (
    emotions.find((item) => String(item.id || item.name || "") === activeEmotion) ||
    emotions.find((item) => String(item.id || item.name || "") === DEFAULT_EMOTION) ||
    emotions[0]
  );
}

function renderSidebarStatus(health, name, outfit, emotion) {
  if (els.sidebarStatusCard) {
    els.sidebarStatusCard.dataset.health = health;
  }
  if (els.sidebarStatus) {
    els.sidebarStatus.textContent = `${name} · ${healthLabel(health)}`;
  }
  if (els.sidebarMeta) {
    els.sidebarMeta.textContent = `${outfit} · ${emotion}`;
  }
}

function buildOverviewAbilityPills(state, resource) {
  const pills = [];
  pills.push(resource.health === "online" ? "后端在线" : "本地待机");
  pills.push(state.voiceEnabled ? "回复朗读" : "朗读关闭");
  pills.push(state.voiceInputEnabled ? "语音输入" : "语音输入关");
  if (view.music?.track || view.music?.queueCount) pills.push("音乐队列");
  if (state.desktopContextEnabled) pills.push("前台窗口");
  if (state.clipboardContextEnabled) pills.push("剪贴板");
  if (state.screenVisionEnabled) pills.push("看屏幕");
  if (state.proactiveWakeEnabled) pills.push("主动搭话");
  return pills;
}

function buildCharacterHeroPills(state, resource, character) {
  const outfitCount = Array.isArray(resource.outfits) ? resource.outfits.length : 0;
  return [
    getActiveCharacterPackId() ? "角色包已选择" : "内置角色",
    `${resource.emotionCount || 0} 表情`,
    `${outfitCount} 服装`,
    resource.health === "online" ? "前后端统一" : "本地预览",
    character.schemaVersion ? `契约 ${character.schemaVersion}` : ""
  ].filter(Boolean);
}

function buildOverviewToolPills() {
  const payload = view.diagnostics?.payload && typeof view.diagnostics.payload === "object" ? view.diagnostics.payload : null;
  const capabilities = payload?.capabilities && typeof payload.capabilities === "object" ? payload.capabilities : {};
  const tools = normalizeDiagnosticsList(capabilities.tool_names || capabilities.toolNames);
  if (tools.length) return tools.slice(0, 8);
  return ["文件处理", "生成文件交付", "手边物品", "媒体工具", "安全边界", "Live2D 预留"];
}

function renderPageStatusCards() {
  renderVoiceStatusCard();
  renderMusicStatusCard();
  renderContextStatusCard();
  renderAdvancedStatusCard();
}

function renderVoiceStatusCard() {
  const state = view.state || {};
  const resource = view.resource || {};
  const tts = resource.tts && typeof resource.tts === "object" ? resource.tts : {};
  const asr = resource.asr && typeof resource.asr === "object" ? resource.asr : {};
  const volume = Math.round(clamp(Number(state.voiceVolume ?? 0.85), 0, 1) * 100);
  if (els.voiceStatusSummary) {
    els.voiceStatusSummary.textContent = `${state.voiceEnabled ? "会朗读回复" : "当前不朗读回复"} · 音量 ${volume}% · ${healthLabel(resource.health)}`;
  }
  renderPillRow(
    els.voiceStatusPills,
    [
      state.voiceEnabled ? "TTS 开启" : "TTS 关闭",
      state.voiceInputEnabled ? "ASR 开启" : "ASR 关闭",
      tts.enabled === false ? "后端 TTS 未启用" : tts.endpoint || "/tts",
      asr.available === false ? "后端 ASR 未声明" : asr.endpoint || "/asr"
    ],
    "等待语音状态"
  );
}

function renderMusicStatusCard() {
  const music = view.music && typeof view.music === "object" ? view.music : {};
  const track = music.track && typeof music.track === "object" ? music.track : {};
  const name = String(music.displayName || track.displayName || track.fileName || "").trim();
  const queueCount = Number(music.queueCount || (Array.isArray(music.queue) ? music.queue.length : 0));
  const progress = formatMusicProgress(music.progressSeconds, music.durationSeconds);
  const status = music.playing ? "播放中" : music.paused ? "已暂停" : name ? "已停止" : "等待音乐";
  if (els.musicPageSummary) {
    els.musicPageSummary.textContent = name ? `${status}：${name}${progress ? ` · ${progress}` : ""}` : "把音乐拖到桌宠身上后，队列和歌词会在这里同步。";
  }
  renderPillRow(
    els.musicPagePills,
    [
      status,
      queueCount ? `${queueCount} 首队列` : "队列为空",
      track.lyricLineCount || music.currentLyric?.lineCount ? "本地歌词" : "本地歌词待定",
      track.timelineLyricLineCount ? "后端歌词线索" : "后端线索待定"
    ],
    "等待音乐状态"
  );
}

function renderContextStatusCard() {
  const state = view.state || {};
  if (els.contextStatusSummary) {
    els.contextStatusSummary.textContent = buildDesktopContextNote(state);
  }
  renderPillRow(
    els.contextStatusPills,
    [
      state.desktopContextEnabled ? "前台窗口" : "前台窗口关",
      state.clipboardContextEnabled ? "剪贴板" : "剪贴板关",
      state.screenVisionEnabled ? `看屏幕 · ${state.screenVisionMode === "direct" ? "直看" : "摘要"}` : "看屏幕关",
      state.proactiveWakeEnabled ? `主动搭话 ${state.proactiveWakeIntervalSec || 30}s` : "主动搭话关"
    ],
    "等待感知状态"
  );
}

function renderAdvancedStatusCard() {
  const state = view.state || {};
  const scale = Math.round(clamp(Number(state.scale ?? 1), 0.75, 1.45) * 100);
  const opacity = Math.round(clamp(Number(state.opacity ?? 1), 0.55, 1) * 100);
  if (els.advancedStatusSummary) {
    els.advancedStatusSummary.textContent = `外观 ${scale}% · 透明度 ${opacity}% · ${view.webglEnabled ? "WebGL 显示中" : "WebGL 已隐藏"}`;
  }
  renderPillRow(
    els.advancedStatusPills,
    [
      view.webglEnabled ? "WebGL 显示" : "WebGL 隐藏",
      state.hitTestEnabled ? "Hit-Test 开" : "Hit-Test 关",
      state.hitboxOverlay ? "Hitbox 显示" : "Hitbox 隐藏",
      state.alwaysOnTop ? "窗口置顶" : "窗口不置顶",
      state.skipTaskbar ? "任务栏隐藏" : "任务栏显示",
      "Live2D 预留"
    ],
    "等待运行状态"
  );
}

function renderPillRow(container, items, emptyText) {
  if (!container) return;
  const values = Array.isArray(items) ? items.map((item) => String(item || "").trim()).filter(Boolean) : [];
  if (!values.length) {
    const empty = document.createElement("span");
    empty.className = "is-empty";
    empty.textContent = emptyText || "暂无状态";
    container.replaceChildren(empty);
    return;
  }
  container.replaceChildren(
    ...values.map((item) => {
      const chip = document.createElement("span");
      chip.textContent = item;
      chip.title = item;
      return chip;
    })
  );
}

function renderResourceDetails() {
  const resource = view.resource || {};
  const source = sourceLabel(resource.source);
  const session = resource.sessionShort ? ` · 会话 ${resource.sessionShort}` : "";
  const outfits = Array.isArray(resource.outfits) && resource.outfits.length ? ` · 可用服装 ${resource.outfits.length}` : "";
  const contract = resource.contractVersion ? ` · ${resource.contractVersion}` : "";
  const loadedAt = formatLoadedAt(resource.loadedAt);
  els.resourceDetails.textContent =
    `${source}${contract} · ${resource.activeOutfit || DEFAULT_OUTFIT} · ${resource.emotionCount || 0} 表情${outfits}${session}${loadedAt}`;
}

function renderResourceAlert() {
  const resource = view.resource || {};
  const health = String(resource.health || "unknown");
  const isFallback = resource.source !== "manifest";
  const missingRequired = Array.isArray(resource.missingRequired) ? resource.missingRequired : [];
  const messages = [];

  if (health === "offline") {
    messages.push(
      resource.retrying ? "后端离线，当前使用本地兜底立绘，并会轻量重试。" : "后端离线，当前使用本地兜底立绘。"
    );
  } else if (health === "checking") {
    messages.push("正在检查后端与角色包资源清单。");
  } else if (health === "online" && resource.contractSource === "legacy") {
    messages.push("后端已连接，但尚未提供桌宠健康契约，当前使用旧健康检查兼容。");
  } else if (isFallback) {
    messages.push("未使用统一资源清单，当前使用本地可见立绘。");
  }

  if (missingRequired.length) {
    messages.push(`缺少基础表情：${missingRequired.join("、")}。`);
  }

  if (
    resource.healthMessage &&
    health !== "checking" &&
    (health !== "online" || isFallback) &&
    !/^connected$/i.test(String(resource.healthMessage))
  ) {
    messages.push(String(resource.healthMessage));
  }

  els.resourceAlert.hidden = messages.length === 0;
  els.resourceAlert.textContent = messages.join(" ");
  els.resourceAlert.dataset.status = health === "offline" || missingRequired.length ? "warning" : "info";
}

function renderResourceMetrics() {
  const resource = view.resource || {};
  const missingRequired = Array.isArray(resource.missingRequired) ? resource.missingRequired.length : 0;
  const missingRecommended = Array.isArray(resource.missingRecommended) ? resource.missingRecommended.length : 0;
  const tts = resource.tts && typeof resource.tts === "object" ? resource.tts : {};
  const asr = resource.asr && typeof resource.asr === "object" ? resource.asr : {};
  const rows = [
    ["来源", sourceLabel(resource.source)],
    ["后端", healthLabel(resource.health)],
    ["契约", resource.contractVersion || (resource.contractSource === "legacy" ? "legacy" : "-")],
    ["健康入口", resource.healthEndpoint || "-"],
    ["TTS", tts.enabled === false ? "关闭" : tts.endpoint || "/tts"],
    ["ASR", asr.available === false ? "未声明" : asr.endpoint || "/asr"],
    ["服装", resource.activeOutfit || DEFAULT_OUTFIT],
    ["表情", `${resource.emotionCount || 0}`],
    ["基础缺失", `${missingRequired}`],
    ["推荐缺失", `${missingRecommended}`]
  ];

  els.resourceMetrics.replaceChildren(
    ...rows.map(([label, value]) => {
      const item = document.createElement("div");
      item.className = "metric";
      const key = document.createElement("span");
      key.textContent = label;
      const data = document.createElement("strong");
      data.textContent = value;
      item.append(key, data);
      return item;
    })
  );
}

function renderDiagnostics() {
  if (!els.diagnosticsSummary || !els.diagnosticsMetrics || !els.diagnosticsTools) return;
  const entry = view.diagnostics && typeof view.diagnostics === "object" ? view.diagnostics : {};
  const payload = entry.payload && typeof entry.payload === "object" ? entry.payload : null;
  const diagnosticsReady = Boolean(payload);

  if (view.diagnosticsLoading) {
    els.diagnosticsSummary.textContent = diagnosticsReady ? "正在刷新能力诊断……" : "正在读取后端能力诊断……";
  } else if (entry.error) {
    els.diagnosticsSummary.textContent = `诊断暂不可用：${entry.error}`;
  } else if (!canFetchDiagnostics()) {
    els.diagnosticsSummary.textContent = diagnosticsReady
      ? "后端当前不可用，下面保留上次能力诊断。"
      : "后端连接后会显示桌宠当前可用能力。";
  } else if (!payload) {
    els.diagnosticsSummary.textContent = "等待刷新能力诊断。";
  } else {
    els.diagnosticsSummary.textContent = buildDiagnosticsSummary(payload, entry.loadedAt);
  }

  if (!payload) {
    els.diagnosticsMetrics.replaceChildren();
    renderDiagnosticsTools([]);
    renderPillRow(els.overviewTools, buildOverviewToolPills(), "能力诊断会在后端连接后显示");
    if (els.refreshDiagnostics) {
      els.refreshDiagnostics.disabled = view.diagnosticsLoading || !canFetchDiagnostics();
    }
    return;
  }

  const capabilities = payload.capabilities && typeof payload.capabilities === "object" ? payload.capabilities : {};
  const resources = payload.resources && typeof payload.resources === "object" ? payload.resources : {};
  const workspace = payload.workspace && typeof payload.workspace === "object" ? payload.workspace : {};
  const safety = payload.safety && typeof payload.safety === "object" ? payload.safety : {};
  const declared = normalizeDiagnosticsList(capabilities.declared);
  const modules = normalizeDiagnosticsList(capabilities.effective_modules || capabilities.effectiveModules);
  const layers = normalizeDiagnosticsList(capabilities.tool_layers || capabilities.toolLayers);
  const tools = normalizeDiagnosticsList(capabilities.tool_names || capabilities.toolNames);

  renderDiagnosticsMetrics([
    ["模式", payload.client_mode || payload.clientMode || "desktop_pet"],
    ["契约", payload.contract_version || payload.contractVersion || "-"],
    ["能力", `${declared.length}`],
    ["模块", `${modules.length}`],
    ["工具层", `${layers.length}`],
    ["工具", `${tools.length}`],
    ["角色包", resources.character_pack_id || resources.characterPackId || getActiveCharacterPackId() || "-"],
    ["服装", resources.outfit || view.resource?.activeOutfit || DEFAULT_OUTFIT],
    ["默认表情", resources.default_emotion || resources.defaultEmotion || DEFAULT_EMOTION],
    ["资源清单", formatDiagnosticsOk(resources.resource_manifest_ok ?? resources.resourceManifestOk)],
    ["手边文件", `${countDiagnosticsValue(workspace.files)}`],
    ["生成文件", `${countDiagnosticsValue(workspace.outputs)}`],
    ["任务", `${countDiagnosticsValue(workspace.tasks)}`],
    ["密钥", safety.secrets_exposed || safety.secretsExposed ? "疑似暴露" : "未暴露"],
    ["磁盘扫描", safety.full_disk_scan || safety.fullDiskScan ? "开启" : "关闭"],
    [
      "桌面动作",
      safety.desktop_actions_require_client || safety.desktopActionsRequireClient ? "客户端确认" : "未声明"
    ]
  ]);
  renderDiagnosticsTools(tools);
  renderPillRow(els.overviewTools, buildOverviewToolPills(), "能力诊断会在后端连接后显示");
  if (els.refreshDiagnostics) {
    els.refreshDiagnostics.disabled = view.diagnosticsLoading || !canFetchDiagnostics();
  }
}

function renderDiagnosticsMetrics(rows) {
  els.diagnosticsMetrics.replaceChildren(
    ...rows.map(([label, value]) => {
      const item = document.createElement("div");
      item.className = "metric";
      const key = document.createElement("span");
      key.textContent = label;
      const data = document.createElement("strong");
      data.textContent = String(value ?? "-");
      item.append(key, data);
      return item;
    })
  );
}

function renderDiagnosticsTools(tools) {
  const items = normalizeDiagnosticsList(tools);
  if (!items.length) {
    const empty = document.createElement("span");
    empty.className = "is-empty";
    empty.textContent = "暂无工具列表";
    els.diagnosticsTools.replaceChildren(empty);
    return;
  }

  const visible = items.slice(0, 18);
  const chips = visible.map((name) => {
    const chip = document.createElement("span");
    chip.textContent = name;
    chip.title = name;
    return chip;
  });
  if (items.length > visible.length) {
    const more = document.createElement("span");
    more.textContent = `+${items.length - visible.length}`;
    more.title = items.slice(visible.length).join(" / ");
    chips.push(more);
  }
  els.diagnosticsTools.replaceChildren(...chips);
}

function buildDiagnosticsSummary(payload, loadedAt) {
  const resources = payload.resources && typeof payload.resources === "object" ? payload.resources : {};
  const capabilities = payload.capabilities && typeof payload.capabilities === "object" ? payload.capabilities : {};
  const status = String(payload.status || "ok");
  const mode = String(payload.client_mode || payload.clientMode || "desktop_pet");
  const contract = String(payload.contract_version || payload.contractVersion || "-");
  const pack = String(resources.character_pack_id || resources.characterPackId || getActiveCharacterPackId() || "-");
  const emotionCount = countDiagnosticsValue(resources.emotion_count ?? resources.emotionCount);
  const toolCount = normalizeDiagnosticsList(capabilities.tool_names || capabilities.toolNames).length;
  return `${diagnosticsStatusLabel(status)} · ${mode} · ${contract} · ${pack} · ${emotionCount} 表情 · ${toolCount} 工具${formatLoadedAt(loadedAt)}`;
}

function scheduleDiagnosticsAutoRefresh() {
  const key = buildDiagnosticsKey();
  if (!key) {
    renderDiagnostics();
    return;
  }
  if (view.diagnosticsAutoKey === key && view.diagnostics?.payload) return;
  view.diagnosticsAutoKey = key;
  window.clearTimeout(view.diagnosticsTimer);
  view.diagnosticsTimer = window.setTimeout(() => {
    void refreshDiagnostics({ quiet: true });
  }, 260);
}

async function refreshDiagnostics({ quiet = false } = {}) {
  if (!canFetchDiagnostics()) {
    view.diagnostics = { payload: null, loadedAt: 0, error: "后端尚未连接" };
    renderDiagnostics();
    return;
  }

  view.diagnosticsLoading = true;
  renderDiagnostics();
  try {
    const response = await settingsBackendFetch(buildDiagnosticsUrl(), {
      method: "GET",
      cache: "no-store",
      connectTimeout: 5000
    });
    if (!response.ok) {
      throw new Error(await readDiagnosticsError(response, `HTTP ${response.status}`));
    }
    const payload = await response.json();
    view.diagnostics = { payload, loadedAt: Date.now(), error: "" };
    if (!quiet) setStatus("能力诊断已刷新");
  } catch (error) {
    view.diagnostics = {
      payload: view.diagnostics?.payload || null,
      loadedAt: view.diagnostics?.loadedAt || 0,
      error: formatError(error)
    };
    if (!quiet) setStatus(`能力诊断失败：${formatError(error)}`);
  } finally {
    view.diagnosticsLoading = false;
    renderDiagnostics();
  }
}

function buildDiagnosticsUrl() {
  const state = view.state || {};
  const resource = view.resource || {};
  const params = new URLSearchParams({
    user_id: state.sessionId || "desktop_pet_next_diagnostics",
    real_user_id: state.profileUserId || "master",
    client: "desktop_pet",
    character_pack_id: getActiveCharacterPackId(),
    outfit: state.outfit || resource.activeOutfit || DEFAULT_OUTFIT,
    emotion: state.currentEmotion || DEFAULT_EMOTION,
    t: String(Date.now())
  });
  return buildSettingsBackendUrl("/desktop-pet/diagnostics", params);
}

function buildSettingsBackendUrl(endpoint, params = null) {
  const base = `${normalizeBackendUrl(view.state?.backendUrl || DEFAULT_BACKEND_URL).replace(/\/+$/, "")}/`;
  const url = new URL(endpoint, base);
  const entries = params instanceof URLSearchParams ? [...params.entries()] : Object.entries(params || {});
  for (const [key, value] of entries) {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

function buildDiagnosticsKey() {
  if (!canFetchDiagnostics()) return "";
  const state = view.state || {};
  return [
    normalizeBackendUrl(state.backendUrl || DEFAULT_BACKEND_URL),
    state.sessionId || "",
    state.profileUserId || "master",
    getActiveCharacterPackId(),
    state.outfit || view.resource?.activeOutfit || DEFAULT_OUTFIT,
    state.currentEmotion || DEFAULT_EMOTION
  ].join("|");
}

function canFetchDiagnostics() {
  return Boolean(normalizeBackendUrl(view.state?.backendUrl || DEFAULT_BACKEND_URL)) && view.resource?.health === "online";
}

function settingsBackendFetch(input, init) {
  return isTauriRuntime ? tauriFetch(input, init) : window.fetch(input, init);
}

async function readDiagnosticsError(response, fallback) {
  try {
    const text = await response.text();
    return text ? text.slice(0, 180) : fallback;
  } catch {
    return fallback;
  }
}

function normalizeDiagnosticsList(value) {
  if (!Array.isArray(value)) return [];
  return value.map((item) => String(item || "").trim()).filter(Boolean);
}

function countDiagnosticsValue(value) {
  if (Array.isArray(value)) return value.length;
  const number = Number(value || 0);
  return Number.isFinite(number) && number > 0 ? number : 0;
}

function formatDiagnosticsOk(value) {
  if (value === false) return "异常";
  if (value === true) return "OK";
  return "-";
}

function diagnosticsStatusLabel(status) {
  return {
    ok: "正常",
    degraded: "降级",
    error: "异常"
  }[String(status || "").toLowerCase()] || String(status || "未知");
}

function renderOutfitList() {
  const resource = view.resource || {};
  const outfits = Array.isArray(resource.outfits) ? resource.outfits : [];
  const active = String(resource.activeOutfit || view.state?.outfit || DEFAULT_OUTFIT);
  if (!outfits.length) {
    els.outfitList.textContent = "暂无可用服装列表。";
    return;
  }

  els.outfitList.replaceChildren(
    ...outfits.map((outfit) => {
      const id = String(outfit.id || outfit.name || "").trim();
      const name = String(outfit.name || id).trim();
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.outfit = id;
      button.className = "outfit-card";
      button.classList.toggle("active", id === active || name === active || Boolean(outfit.active));
      button.append(
        buildText("strong", name || id),
        buildText("span", `${id}${outfit.aliases?.length ? ` · ${outfit.aliases.join(" / ")}` : ""}`),
        buildText("small", buildOutfitMeta(outfit))
      );
      return button;
    })
  );
}

function renderEmotionGrid() {
  const emotions = Array.isArray(view.resource?.emotions) ? view.resource.emotions : [];
  const active = String(view.state?.currentEmotion || DEFAULT_EMOTION);
  if (!emotions.length) {
    els.emotionGrid.textContent = "暂无表情列表。";
    return;
  }

  els.emotionGrid.replaceChildren(
    ...emotions.map((emotion) => {
      const id = String(emotion.id || emotion.name || "").trim();
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.emotion = id;
      button.textContent = String(emotion.name || id);
      const aliases = Array.isArray(emotion.aliases) ? emotion.aliases : [];
      button.title = aliases.length ? `${id} · ${aliases.join(" / ")}` : id;
      button.classList.toggle("active", id === active);
      return button;
    })
  );
}

function buildConnectionLine(resource) {
  const source = sourceLabel(resource.source);
  const contract = resource.contractVersion || (resource.contractSource === "legacy" ? "legacy" : "");
  return `后端：${healthLabel(resource.health)}${contract ? ` · ${contract}` : ""} · ${source} · ${resource.activeOutfit || DEFAULT_OUTFIT}`;
}

function buildDesktopContextNote(state) {
  if (state.desktopContextEnabled === false && !state.screenVisionEnabled && !state.proactiveWakeEnabled) {
    return "已关闭，不会向 /think 附带桌面上下文。";
  }
  const screenVision =
    state.screenVisionEnabled && state.screenVisionMode === "direct"
      ? "看屏幕会保留最近几张临时画面，用新画面顶掉旧画面。"
      : state.screenVisionEnabled
        ? "看屏幕会维护最近几条短期印象，不写入长期记忆。"
        : "";
  const visionMode =
    state.screenVisionEnabled && state.screenVisionMode === "direct"
      ? "直看模式会在主动搭话时把最近截图临时发给主模型，用完即丢。"
      : "";
  const proactive = state.proactiveWakeEnabled
    ? `${view.character?.name || CHARACTER_NAME} 会约每 ${state.proactiveWakeIntervalSec || 30} 秒醒来一次；视觉摘要建议 ${state.recommendedScreenVisionIntervalSec || 25} 秒，可手动覆盖。`
    : "";
  if (state.clipboardContextEnabled) {
    return ["发送消息时临时附带最近前台窗口和剪贴板文本。", screenVision, visionMode, proactive].filter(Boolean).join(" ");
  }
  return ["发送消息时临时附带最近前台窗口；剪贴板默认不读取。", screenVision, visionMode, proactive].filter(Boolean).join(" ");
}

function renderMusicStatus(music) {
  const payload = music && typeof music === "object" ? music : {};
  const name = String(payload.displayName || payload.track?.displayName || payload.track?.fileName || "").trim();
  const hasTrack = Boolean(payload.track || name);
  const queueCount = Number(payload.queueCount || 0);
  const queueIndex = Number(payload.queueIndex || -1);
  const queueLabel = queueCount > 1 && queueIndex >= 0 ? `（${queueIndex + 1}/${queueCount}）` : "";
  const progressLabel = formatMusicProgress(payload.progressSeconds, payload.durationSeconds);
  if (payload.loading) {
    els.musicStatus.textContent = "正在准备音乐……";
  } else if (payload.playing) {
    els.musicStatus.textContent = `正在播放：${name || "未命名音乐"}${queueLabel}${progressLabel ? ` · ${progressLabel}` : ""}`;
  } else if (payload.paused) {
    els.musicStatus.textContent = `已暂停：${name || "未命名音乐"}${queueLabel}${progressLabel ? ` · ${progressLabel}` : ""}`;
  } else {
    els.musicStatus.textContent = hasTrack
      ? `已停止：${name || "未命名音乐"}${queueLabel}`
      : `把一首或多首 mp3 / wav / flac / ogg / m4a 等音频文件拖到 ${view.character?.name || CHARACTER_NAME} 身上就可以播放。`;
  }
  els.previousMusic.disabled = Boolean(payload.loading) || !payload.hasPrevious;
  els.nextMusic.disabled = Boolean(payload.loading) || !payload.hasNext;
  els.toggleMusic.disabled = !hasTrack || Boolean(payload.loading);
  els.stopMusic.disabled = !hasTrack && !payload.loading;
  els.clearMusicQueue.disabled = Boolean(payload.loading) || (!hasTrack && !queueCount);
  els.toggleMusic.textContent = payload.playing ? "暂停音乐" : hasTrack ? "继续播放" : "播放/暂停";
  renderMusicLyric(payload, hasTrack);
  renderMusicQueue(payload);
}

function renderMusicLyric(payload, hasTrack) {
  const lyric = payload.currentLyric && typeof payload.currentLyric === "object" ? payload.currentLyric : {};
  const line = String(lyric.text || "").trim();
  const next = String(lyric.nextText || "").trim();
  const track = payload.track && typeof payload.track === "object" ? payload.track : {};
  const lineCount = Number(track.lyricLineCount || lyric.lineCount || track.timelineLyricLineCount || 0);
  const timelineStatus = String(track.timelineStatus || "").trim();
  if (line) {
    els.musicLyric.textContent = `歌词：${line}`;
  } else if (next && hasTrack) {
    els.musicLyric.textContent = `下一句：${next}`;
  } else if (hasTrack && lineCount > 0) {
    els.musicLyric.textContent = `已载入歌词 ${lineCount} 行，等待歌曲开始。`;
  } else if (hasTrack && (track.timelineLoading || ["uploading", "pending", "processing"].includes(timelineStatus))) {
    els.musicLyric.textContent = "正在准备后端歌词线索……";
  } else if (hasTrack && timelineStatus === "failed") {
    els.musicLyric.textContent = "后端歌词线索暂时没准备好，当前只显示播放状态。";
  } else if (hasTrack) {
    els.musicLyric.textContent = "没有找到同名 .lrc，后端会尝试准备歌词线索。";
  } else {
    els.musicLyric.textContent = "歌词会显示在这里。";
  }
}

function renderMusicQueue(payload) {
  const queue = Array.isArray(payload.queue) ? payload.queue : [];
  els.musicQueue.replaceChildren();
  if (!queue.length) {
    const empty = document.createElement("div");
    empty.className = "music-queue-empty";
    empty.textContent = "队列还空着，把音乐拖给桌宠就会出现在这里。";
    els.musicQueue.append(empty);
    return;
  }

  const currentId = String(payload.track?.sourceId || "").trim();
  for (const [index, track] of queue.entries()) {
    const sourceId = String(track.sourceId || "").trim();
    const isCurrent = sourceId && sourceId === currentId;
    const row = document.createElement("article");
    row.className = "music-queue-item";
    if (isCurrent) row.classList.add("is-current");
    row.dataset.sourceId = sourceId;

    const mark = document.createElement("span");
    mark.className = "music-queue-index";
    mark.textContent = String(index + 1);

    const body = document.createElement("div");
    body.className = "music-queue-body";
    const title = document.createElement("strong");
    title.textContent = String(track.displayName || track.fileName || "未命名音乐");
    const meta = document.createElement("span");
    meta.textContent = [
      isCurrent ? "当前" : "",
      track.extension ? track.extension.toUpperCase() : "",
      track.lyricLineCount ? `LRC ${track.lyricLineCount} 行` : "",
      track.timelineLyricLineCount ? `后端 ${track.timelineLyricLineCount} 行` : "",
      track.timelineLoading || ["uploading", "pending", "processing"].includes(String(track.timelineStatus || "")) ? "准备歌词中" : "",
      formatSize(track.sizeBytes)
    ]
      .filter(Boolean)
      .join(" · ");
    body.append(title, meta);

    const actions = document.createElement("div");
    actions.className = "music-queue-actions";
    const play = document.createElement("button");
    play.type = "button";
    play.dataset.musicAction = "play";
    play.textContent = isCurrent ? "当前" : "播放";
    play.disabled = isCurrent || !sourceId;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.dataset.musicAction = "remove";
    remove.textContent = "移除";
    remove.disabled = !sourceId;
    actions.append(play, remove);

    row.append(mark, body, actions);
    els.musicQueue.append(row);
  }
}

function formatMusicProgress(progressValue, durationValue) {
  const progress = Number(progressValue || 0);
  const duration = Number(durationValue || 0);
  if (!Number.isFinite(progress) || progress <= 0) return "";
  const current = formatDuration(progress);
  if (!Number.isFinite(duration) || duration <= 0) return current;
  return `${current}/${formatDuration(duration)}`;
}

function formatDuration(value) {
  const total = Math.max(0, Math.floor(Number(value || 0)));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function formatSize(bytes) {
  const size = Number(bytes || 0);
  if (!Number.isFinite(size) || size <= 0) return "";
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function updateScreenVisionIntervalControls(state) {
  const directMode = String(state.screenVisionMode || "summary") === "direct";
  const disabled = directMode;
  els.screenVisionInterval.disabled = disabled;
  els.applyVisionRecommendation.disabled = disabled;
  els.screenVisionIntervalRow.classList.toggle("is-disabled", disabled);
  const title = disabled
    ? "直看模式不生成后台视觉摘要，视觉间隔只影响“先整理屏幕印象”模式。"
    : "视觉间隔只影响“先整理屏幕印象”模式下的后台摘要频率。";
  els.screenVisionIntervalRow.title = title;
  els.screenVisionInterval.title = title;
  els.applyVisionRecommendation.title = title;
}

function buildActivityLine(snapshot) {
  const active = snapshot?.active || view.active || {};
  const mode = String(snapshot?.runtimeMode || "").trim();
  const parts = [];
  if (active.sending || mode === "thinking") parts.push("思考中");
  if (mode === "replying" || (active.replyDisplayActive && !active.sending)) parts.push("回复显示中");
  if (active.speaking || mode === "speaking") parts.push("语音播放中");
  if (active.musicPlaying || snapshot?.music?.playing) parts.push("音乐播放中");
  if (active.musicPaused || snapshot?.music?.paused) parts.push("音乐暂停");
  if (active.voiceInput === "recording") parts.push("语音录制中");
  if (active.voiceInput === "processing") parts.push("语音识别中");
  if (active.screenVision === "watching" || active.screenVision === "observing") parts.push("看屏幕中");
  if (active.screenVision === "uploading") parts.push("整理屏幕印象");
  if (active.proactiveWakeRunning) parts.push("主动搭话中");
  if (active.bubbleVisible) parts.push("气泡显示中");
  if (!parts.length) parts.push(modeLabel(mode));
  return `状态：${parts.filter(Boolean).join(" · ")}`;
}

function modeLabel(mode) {
  return {
    idle: "空闲",
    stopped: "已停止",
    error: "错误",
    offline: "后端离线",
    checking: "检查中",
    listening: "语音录制中",
    thinking: "思考中",
    replying: "回复显示中",
    speaking: "语音播放中",
    music: "音乐播放中",
    "music-paused": "音乐暂停"
  }[mode] || "空闲";
}

function buildOutfitMeta(outfit) {
  const parts = [`${outfit.emotionCount || 0} 表情`];
  if (Number(outfit.allowedEmotionCount || 0) > 0) parts.push(`${outfit.allowedEmotionCount} 允许`);
  if (Array.isArray(outfit.missingRequired) && outfit.missingRequired.length) {
    parts.push(`缺 ${outfit.missingRequired.join("、")}`);
  } else {
    parts.push("基础 OK");
  }
  if (Array.isArray(outfit.missingRecommended) && outfit.missingRecommended.length) {
    parts.push(`推荐缺 ${outfit.missingRecommended.length}`);
  }
  return parts.join(" · ");
}

function buildText(tagName, text) {
  const element = document.createElement(tagName);
  element.textContent = text;
  return element;
}

function sourceLabel(source) {
  return {
    manifest: "当前角色包",
    character_pack: "本地角色包",
    bundled: "内置兜底"
  }[String(source || "")] || "本地兜底";
}

function healthLabel(health) {
  return {
    online: "已连接",
    offline: "离线",
    checking: "检查中",
    unknown: "未知"
  }[health] || "未知";
}

function isReplyActive(snapshot) {
  const active = snapshot?.active || view.active || {};
  return Boolean(active.sending || active.speaking || active.replyDisplayActive || snapshot?.tts?.active);
}

function formatLoadedAt(value) {
  const timestamp = Number(value || 0);
  if (!Number.isFinite(timestamp) || timestamp <= 0) return "";
  return ` · ${new Date(timestamp).toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit"
  })}`;
}

function updateOutput(output, value) {
  output.value = `${Math.round(Number(value) * 100)}%`;
}

function setInputIfIdle(input, value) {
  if (document.activeElement !== input) input.value = value;
}

function setStatus(message) {
  els.runtimeStatus.textContent = message;
}

function setCharacterImportStatus(message) {
  if (els.characterImportStatus) {
    els.characterImportStatus.textContent = message;
  }
  setStatus(message);
}

function isZipName(value) {
  return String(value || "").trim().toLowerCase().endsWith(".zip");
}

function shortPath(value) {
  const text = String(value || "").replace(/\\/g, "/");
  return text.split("/").filter(Boolean).pop() || text;
}

function normalizeBackendUrl(url) {
  return String(url || "").trim().replace(/\/+$/, "") || DEFAULT_BACKEND_URL;
}

function normalizeOutfitName(value) {
  return String(value || "").trim() || DEFAULT_OUTFIT;
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, Number.isFinite(value) ? value : min));
}

function formatError(error) {
  return error instanceof Error ? error.message : String(error);
}

function cssEscape(value) {
  if (window.CSS?.escape) return window.CSS.escape(value);
  return String(value || "").replace(/"/g, '\\"');
}
