import { invoke } from "@tauri-apps/api/core";
import { emit, listen } from "@tauri-apps/api/event";

import "./settings.css";

const SETTINGS_COMMAND_EVENT = "akane-next-settings-command";
const SETTINGS_SNAPSHOT_EVENT = "akane-next-settings-snapshot";
const DEFAULT_BACKEND_URL = "http://127.0.0.1:9999";
const DEFAULT_OUTFIT = "猫娘";
const DEFAULT_EMOTION = "正常";
const SCALE_PRESETS = [0.85, 1, 1.15, 1.3];
const OPACITY_PRESETS = [1, 0.85, 0.7, 0.55];

const els = {
  summary: document.querySelector("#settings-summary"),
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
  desktopContextEnabled: document.querySelector("#desktop-context-enabled"),
  clipboardContextEnabled: document.querySelector("#clipboard-context-enabled"),
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
  resource: null,
  active: null,
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
  els.desktopContextEnabled.addEventListener("change", () =>
    sendCommand("setDesktopContextEnabled", els.desktopContextEnabled.checked)
  );
  els.clipboardContextEnabled.addEventListener("change", () =>
    sendCommand("setClipboardContextEnabled", els.clipboardContextEnabled.checked)
  );
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
  view.resource = { ...(view.resource || {}), ...(snapshot.resource || {}) };
  view.active = { ...(view.active || {}), ...(snapshot.active || {}) };
  view.webglEnabled = Boolean(snapshot.webglEnabled);

  const state = view.state || {};
  const resource = view.resource || {};
  const scale = clamp(Number(state.scale ?? 1), 0.75, 1.45);
  const opacity = clamp(Number(state.opacity ?? 1), 0.55, 1);
  const voiceVolume = clamp(Number(state.voiceVolume ?? 0.85), 0, 1);

  setInputIfIdle(els.backendUrl, state.backendUrl || DEFAULT_BACKEND_URL);
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
  els.hitTest.checked = Boolean(state.hitTestEnabled);
  els.hitboxOverlay.checked = Boolean(state.hitboxOverlay);

  renderPresetChips();
  renderResourceAlert();
  renderResourceDetails();
  renderResourceMetrics();
  renderOutfitList();
  renderEmotionGrid();

  const source = sourceLabel(resource.source);
  els.summary.textContent = `${resource.activeOutfit || DEFAULT_OUTFIT} · ${source} · ${state.currentEmotion || DEFAULT_EMOTION}`;
  els.sessionId.textContent = state.sessionId || "-";
  els.sessionId.title = state.sessionId || "";
  els.copySession.disabled = !state.sessionId;
  els.activityStatus.textContent = buildActivityLine(snapshot);
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
  if (state.desktopContextEnabled === false) return "已关闭，不会向 /think 附带桌面上下文。";
  if (state.clipboardContextEnabled) {
    return "发送消息时临时附带最近前台窗口和剪贴板文本。";
  }
  return "发送消息时临时附带最近前台窗口；剪贴板默认不读取。";
}

function buildActivityLine(snapshot) {
  const active = snapshot?.active || view.active || {};
  const mode = String(snapshot?.runtimeMode || "").trim();
  const parts = [];
  if (active.sending || mode === "thinking") parts.push("思考中");
  if (mode === "replying" || (active.replyDisplayActive && !active.sending)) parts.push("回复显示中");
  if (active.speaking || mode === "speaking") parts.push("语音播放中");
  if (active.voiceInput === "recording") parts.push("语音录制中");
  if (active.voiceInput === "processing") parts.push("语音识别中");
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
    speaking: "语音播放中"
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
  return source === "manifest" ? "后端资源" : "本地资源";
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
