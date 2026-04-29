import { invoke } from "@tauri-apps/api/core";
import { emit, listen } from "@tauri-apps/api/event";

import { APP_DISPLAY_NAME, CHARACTER_NAME, DEFAULT_EMOTION, DEFAULT_OUTFIT } from "./character-profile.js";
import "./settings.css";

const SETTINGS_COMMAND_EVENT = "akane-next-settings-command";
const SETTINGS_SNAPSHOT_EVENT = "akane-next-settings-snapshot";
const DEFAULT_BACKEND_URL = "http://127.0.0.1:9999";
const SCALE_PRESETS = [0.85, 1, 1.15, 1.3];
const OPACITY_PRESETS = [1, 0.85, 0.7, 0.55];

const els = {
  summary: document.querySelector("#settings-summary"),
  title: document.querySelector(".settings-header h1"),
  characterPack: document.querySelector("#character-pack"),
  saveCharacterPack: document.querySelector("#save-character-pack"),
  characterPackList: document.querySelector("#character-pack-list"),
  characterDetails: document.querySelector("#character-details"),
  characterMetrics: document.querySelector("#character-metrics"),
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
  testTts: document.querySelector("#test-tts"),
  stopTts: document.querySelector("#stop-tts"),
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
  alwaysOnTop: document.querySelector("#always-on-top"),
  skipTaskbar: document.querySelector("#skip-taskbar"),
  resetVisuals: document.querySelector("#reset-visuals"),
  resetPlacement: document.querySelector("#reset-placement"),
  resourceDetails: document.querySelector("#resource-details"),
  resourceMetrics: document.querySelector("#resource-metrics"),
  outfitList: document.querySelector("#outfit-list"),
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
  webglEnabled: false,
  scaleTimer: 0,
  opacityTimer: 0
};

boot();

async function boot() {
  bindUi();
  renderPresetChips();

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
  els.openInput.addEventListener("click", () => sendCommand("openInput"));
  els.saveBackend.addEventListener("click", () => saveBackendUrl());
  els.saveCharacterPack.addEventListener("click", () => saveCharacterPack());
  els.characterPack.addEventListener("change", () => updateCharacterPackButton());
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

function saveBackendUrl() {
  sendCommand("setBackendUrl", normalizeBackendUrl(els.backendUrl.value));
}

function saveCharacterPack() {
  const value = String(els.characterPack.value || "").trim();
  if (!value) return;
  setStatus("正在应用角色包");
  sendCommand("setCharacterPack", value);
}

function saveOutfit() {
  sendCommand("setOutfit", normalizeOutfitName(els.outfit.value));
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
  try {
    await emit(SETTINGS_COMMAND_EVENT, { command, value });
  } catch (error) {
    setStatus(`发送命令失败：${formatError(error)}`);
  }
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
    messages.push(resource.retrying ? "后端离线，当前使用本地立绘，并会轻量重试。" : "后端离线，当前使用本地立绘。");
  } else if (health === "checking") {
    messages.push("正在检查后端与资源。");
  } else if (health === "online" && resource.contractSource === "legacy") {
    messages.push("后端已连接，但尚未提供桌宠健康契约，当前使用旧健康检查兼容。");
  } else if (isFallback) {
    messages.push("当前使用本地立绘。");
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
    manifest: "后端资源",
    character_pack: "角色包资源",
    bundled: "内置资源"
  }[String(source || "")] || "本地资源";
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
