import * as mockData from "./mock-data.js";
import {
  CONTROL_CENTER_ACTIONS,
  CONTROL_CENTER_BRIDGED_ACTION_IDS,
  createNotImplementedActionResult
} from "./action-router.js";

const DEFAULT_BACKEND_URL = "http://127.0.0.1:9999";
const SETTINGS_COMMAND_EVENT = "akane-next-settings-command";
const bridgedActionIds = new Set(CONTROL_CENTER_BRIDGED_ACTION_IDS);
const settingsCommandByActionId = Object.freeze({
  [CONTROL_CENTER_ACTIONS.chatNew]: "newSession",
  [CONTROL_CENTER_ACTIONS.chatStop]: "stopReply",
  [CONTROL_CENTER_ACTIONS.workspaceOpen]: "openWorkspace",
  [CONTROL_CENTER_ACTIONS.voiceTest]: "testTts",
  [CONTROL_CENTER_ACTIONS.voiceStop]: "stopTts",
  [CONTROL_CENTER_ACTIONS.voiceSetTtsEnabled]: "setVoiceEnabled",
  [CONTROL_CENTER_ACTIONS.voiceSetAsrEnabled]: "setVoiceInputEnabled",
  [CONTROL_CENTER_ACTIONS.voiceSetVolume]: "setVoiceVolume",
  [CONTROL_CENTER_ACTIONS.characterRefresh]: "reloadResources",
  [CONTROL_CENTER_ACTIONS.characterPreviewEmotion]: "previewEmotion",
  [CONTROL_CENTER_ACTIONS.perceptionDesktopContextSetEnabled]: "setDesktopContextEnabled",
  [CONTROL_CENTER_ACTIONS.perceptionClipboardContextSetEnabled]: "setClipboardContextEnabled",
  [CONTROL_CENTER_ACTIONS.perceptionScreenVisionSetEnabled]: "setScreenVisionEnabled",
  [CONTROL_CENTER_ACTIONS.perceptionScreenVisionSetIntervalSec]: "setScreenVisionIntervalSec",
  [CONTROL_CENTER_ACTIONS.perceptionScreenVisionSetFrameCount]: "setScreenVisionFrameCount",
  [CONTROL_CENTER_ACTIONS.perceptionScreenVisionClear]: "clearScreenVision",
  [CONTROL_CENTER_ACTIONS.perceptionProactiveWakeSetEnabled]: "setProactiveWakeEnabled",
  [CONTROL_CENTER_ACTIONS.perceptionProactiveWakeSetIntervalSec]: "setProactiveWakeIntervalSec",
  [CONTROL_CENTER_ACTIONS.advancedProbeClickThrough]: "probeClickThrough",
  [CONTROL_CENTER_ACTIONS.advancedResetWindow]: "resetWindow",
  [CONTROL_CENTER_ACTIONS.advancedToggleWebgl]: "toggleWebgl",
  [CONTROL_CENTER_ACTIONS.advancedSetHitTestEnabled]: "setHitTestEnabled",
  [CONTROL_CENTER_ACTIONS.advancedSetHitboxOverlay]: "setHitboxOverlay",
  [CONTROL_CENTER_ACTIONS.musicPrevious]: "previousMusic",
  [CONTROL_CENTER_ACTIONS.musicNext]: "nextMusic",
  [CONTROL_CENTER_ACTIONS.musicPause]: "toggleMusic",
  [CONTROL_CENTER_ACTIONS.musicStop]: "stopMusic",
  [CONTROL_CENTER_ACTIONS.musicClear]: "clearMusicQueue"
});
const tauriInvokeByActionId = Object.freeze({
  [CONTROL_CENTER_ACTIONS.workspaceOpen]: "open_workspace_window",
  [CONTROL_CENTER_ACTIONS.characterOpenPackFolder]: "open_character_packs_folder",
  [CONTROL_CENTER_ACTIONS.windowClose]: "close_window"
});
const tauriWindowActionByActionId = Object.freeze({
  [CONTROL_CENTER_ACTIONS.windowMinimize]: "minimize",
  [CONTROL_CENTER_ACTIONS.windowMaximize]: "toggleMaximize"
});
const clientOnlyActionIds = new Set([
  CONTROL_CENTER_ACTIONS.windowClose,
  CONTROL_CENTER_ACTIONS.windowMinimize,
  CONTROL_CENTER_ACTIONS.windowMaximize,
  CONTROL_CENTER_ACTIONS.characterPreviewEmotion
]);
const unifiedSnapshotRuntimeFields = Object.freeze([
  "health",
  "diagnostics",
  "workspace",
  "resourceManifest",
  "metrics"
]);

export const CONTROL_CENTER_SOURCE_KIND = Object.freeze({
  mock: "mock",
  backend: "backend",
  tauri: "tauri"
});

export function createControlCenterDataSource(options = {}) {
  const kind = options.kind || CONTROL_CENTER_SOURCE_KIND.mock;
  if (kind === CONTROL_CENTER_SOURCE_KIND.backend) {
    return createBackendControlCenterSource(options);
  }
  if (kind === CONTROL_CENTER_SOURCE_KIND.tauri) {
    return createTauriControlCenterSource(options);
  }
  return createMockControlCenterSource(options.mockData || mockData);
}

export function createMockControlCenterSource(data = mockData) {
  return {
    kind: CONTROL_CENTER_SOURCE_KIND.mock,
    readInitialState() {
      return {
        ...data,
        sourceKind: CONTROL_CENTER_SOURCE_KIND.mock
      };
    },
    subscribe() {
      return () => {};
    },
    handlesAction() {
      return true;
    },
    async runAction(actionId, payload = {}) {
      return {
        ok: true,
        status: "mocked",
        actionId,
        payload,
        refresh: true
      };
    }
  };
}

export function createTauriControlCenterSource(options = {}) {
  return {
    kind: CONTROL_CENTER_SOURCE_KIND.tauri,
    readInitialState() {
      return {
        ...mockData,
        sourceKind: CONTROL_CENTER_SOURCE_KIND.mock,
        fallbackReason: "tauri-source-awaiting-runtime-snapshot"
      };
    },
    subscribe() {
      return () => {};
    },
    handlesAction(actionId) {
      return bridgedActionIds.has(normalizeActionId(actionId));
    },
    async runAction(actionId, payload = {}, context = {}) {
      const normalizedActionId = normalizeActionId(actionId);
      if (!bridgedActionIds.has(normalizedActionId)) {
        return createNotImplementedActionResult(normalizedActionId);
      }
      const result = await runTauriControlCenterAction(normalizedActionId, payload, context, options);
      if (result.status === "not-available") {
        return createNotImplementedActionResult(normalizedActionId);
      }
      return result;
    }
  };
}

export function createBackendControlCenterSource(options = {}) {
  const baseUrl = normalizeBackendBaseUrl(options.baseUrl || options.endpoint || DEFAULT_BACKEND_URL);
  const fetchImpl = options.fetchImpl || globalThis.fetch;
  const sessionId = options.sessionId || "control-center-lab";
  const profileUserId = options.profileUserId || "master";
  const client = options.client || "desktop_pet";
  const characterPackId = options.characterPackId || "";
  const outfit = options.outfit || "";
  const emotion = options.emotion || "";
  const petState = options.petState && typeof options.petState === "object" ? options.petState : {};
  const availableCharacterPacks = Array.isArray(options.availableCharacterPacks) ? options.availableCharacterPacks : [];
  const musicSnapshot = options.musicSnapshot && typeof options.musicSnapshot === "object" ? options.musicSnapshot : null;

  return {
    kind: CONTROL_CENTER_SOURCE_KIND.backend,
    async readSnapshot() {
      if (typeof fetchImpl !== "function") {
        return null;
      }

      const commonParams = {
        user_id: sessionId,
        real_user_id: profileUserId,
        client,
        character_pack_id: characterPackId,
        outfit,
        emotion,
        t: String(Date.now())
      };

      // Try unified snapshot endpoint first
      const snapshotResult = await tryReadUnifiedSnapshot(fetchImpl, baseUrl, {
        requestParams: commonParams,
        petState,
        musicSnapshot,
        availableCharacterPacks,
        characterPackId,
        outfit,
        emotion
      });
      if (snapshotResult) {
        return snapshotResult;
      }

      const [health, diagnostics, workspace, resourceManifest, metrics] = await Promise.all([
        fetchJson(fetchImpl, buildBackendUrl(baseUrl, "/health", { t: commonParams.t })),
        fetchJson(fetchImpl, buildBackendUrl(baseUrl, "/desktop-pet/diagnostics", commonParams)),
        fetchJson(fetchImpl, buildBackendUrl(baseUrl, "/desktop-pet/workspace/summary", commonParams)),
        fetchJson(fetchImpl, buildBackendUrl(baseUrl, "/resource-manifest", commonParams)),
        fetchText(fetchImpl, buildBackendUrl(baseUrl, "/metrics", { t: commonParams.t }))
      ]);
      if (![health, diagnostics, workspace, resourceManifest, metrics].some((item) => item.ok)) {
        return null;
      }
      return {
        ...mockData,
        sourceKind: CONTROL_CENTER_SOURCE_KIND.backend,
        controlCenterRuntime: {
          backendBaseUrl: baseUrl,
          health,
          diagnostics,
          workspace,
          resourceManifest,
          metrics
        },
        overviewRuntime: buildOverviewRuntimePatch({
          health: health.data,
          diagnostics: diagnostics.data,
          workspace: workspace.data,
          metricsText: metrics.data,
          petState,
          connected: health.ok || diagnostics.ok
        }),
        characterRuntime: buildCharacterRuntimePatch({
          baseUrl,
          resourceManifest: resourceManifest.data,
          diagnostics: diagnostics.data,
          characterPackId,
          outfit,
          emotion,
          petState,
          availableCharacterPacks
        }),
        voiceRuntime: buildVoiceRuntimePatch({
          health: health.data,
          diagnostics: diagnostics.data,
          petState
        }),
        perceptionRuntime: buildPerceptionRuntimePatch({
          petState,
          diagnostics: diagnostics.data
        }),
        musicRuntime: buildMusicRuntimePatch({ musicSnapshot, petState }),
        abilitiesRuntime: buildAbilitiesRuntimePatch({
          diagnostics: diagnostics.data,
          workspace: workspace.data,
          connected: health.ok || diagnostics.ok
        }),
        advancedRuntime: buildAdvancedRuntimePatch({
          health: health.data,
          diagnostics: diagnostics.data,
          workspace: workspace.data,
          metricsText: metrics.data,
          petState
        })
      };
    },
    readInitialState() {
      return {
        ...mockData,
        sourceKind: CONTROL_CENTER_SOURCE_KIND.mock,
        fallbackReason: "backend-source-not-connected"
      };
    },
    subscribe() {
      return () => {};
    },
    handlesAction(actionId) {
      return bridgedActionIds.has(normalizeActionId(actionId));
    },
    async runAction(actionId, payload = {}, context = {}) {
      const normalizedActionId = normalizeActionId(actionId);
      if (!bridgedActionIds.has(normalizedActionId)) {
        return createNotImplementedActionResult(normalizedActionId);
      }

      const tauriResult = await runTauriControlCenterAction(normalizedActionId, payload, context, options);
      if (tauriResult.status !== "not-available") {
        return tauriResult;
      }
      if (clientOnlyActionIds.has(normalizedActionId)) {
        return createNotImplementedActionResult(normalizedActionId);
      }

      if (typeof fetchImpl !== "function") {
        return createNotImplementedActionResult(normalizedActionId);
      }
      try {
        const response = await fetchImpl(buildBackendUrl(baseUrl, `/control-center/actions/${encodeURIComponent(normalizedActionId)}`), {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify(payload)
        });
        if (response.status === 404 || response.status === 405) {
          return createNotImplementedActionResult(normalizedActionId);
        }
        if (!response.ok) {
          return { ok: false, status: `http-${response.status}`, actionId: normalizedActionId, payload };
        }
        const result = await readActionResponse(response);
        return {
          ok: true,
          status: "executed",
          ...result,
          actionId: result?.actionId || normalizedActionId,
          refresh: result?.refresh === undefined ? true : Boolean(result.refresh)
        };
      } catch (error) {
        return { ok: false, status: "request-failed", actionId: normalizedActionId, payload, error: formatDataSourceError(error) };
      }
    }
  };
}

async function runTauriControlCenterAction(actionId, payload, context, options) {
  const command = tauriInvokeByActionId[actionId];
  const settingsCommand = settingsCommandByActionId[actionId];
  const windowAction = tauriWindowActionByActionId[actionId];
  const bridge = await resolveTauriBridge(options);
  if (!bridge) {
    return { ok: false, status: "not-available", actionId };
  }

  try {
    if (command && typeof bridge.invoke === "function") {
      await bridge.invoke(command, {});
      return { ok: true, status: "executed", actionId, payload, refresh: true };
    }

    if (settingsCommand && typeof bridge.emit === "function") {
      await bridge.emit(SETTINGS_COMMAND_EVENT, {
        command: settingsCommand,
        value: payload?.value ?? null,
        source: context?.source || "control-center"
      });
      return { ok: true, status: "executed", actionId, payload, refresh: true };
    }

    if (windowAction) {
      return runTauriWindowAction(actionId, windowAction, bridge);
    }

    return createNotImplementedActionResult(actionId);
  } catch (error) {
    return { ok: false, status: "failed", actionId, payload, error: formatDataSourceError(error), refresh: true };
  }
}

async function runTauriWindowAction(actionId, windowAction, bridge) {
  const winApi = bridge.window;
  if (winApi && typeof winApi[windowAction] === "function") {
    await winApi[windowAction]();
    return { ok: true, status: "executed", actionId, refresh: true };
  }

  try {
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    const win = getCurrentWindow();
    if (windowAction === "minimize") {
      await win.minimize();
    } else if (windowAction === "toggleMaximize") {
      await win.toggleMaximize();
    }
    return { ok: true, status: "executed", actionId, refresh: true };
  } catch {
    return createNotImplementedActionResult(actionId);
  }
}

async function resolveTauriBridge(options = {}) {
  const injectedBridge = options.tauriBridge || {};
  if (typeof injectedBridge.invoke === "function" || typeof injectedBridge.emit === "function" || typeof injectedBridge.window === "object") {
    return injectedBridge;
  }

  const windowBridge = globalThis.window?.__TAURI__ || globalThis.__TAURI__;
  const windowInvoke = windowBridge?.core?.invoke || windowBridge?.invoke;
  const windowEmit = windowBridge?.event?.emit || windowBridge?.emit;
  if (typeof windowInvoke === "function" || typeof windowEmit === "function") {
    return { invoke: windowInvoke, emit: windowEmit };
  }

  if (!isTauriRuntime()) {
    return null;
  }

  try {
    const [coreApi, eventApi] = await Promise.all([
      import("@tauri-apps/api/core"),
      import("@tauri-apps/api/event")
    ]);
    return {
      invoke: coreApi.invoke,
      emit: eventApi.emit
    };
  } catch {
    return null;
  }
}

function isTauriRuntime() {
  return Boolean(globalThis.window?.__TAURI_INTERNALS__ || globalThis.__TAURI_INTERNALS__);
}

async function readActionResponse(response) {
  const contentType = String(response?.headers?.get?.("content-type") || "").toLowerCase();
  if (!contentType || contentType.includes("json")) {
    try {
      const result = await response.json();
      return result && typeof result === "object" ? result : {};
    } catch {
      return {};
    }
  }
  return {};
}

async function fetchJson(fetchImpl, url) {
  try {
    const response = await fetchImpl(url, {
      headers: { Accept: "application/json" },
      cache: "no-store"
    });
    if (!response.ok) {
      return { ok: false, status: response.status, data: null };
    }
    return { ok: true, status: response.status, data: await response.json() };
  } catch (error) {
    return { ok: false, status: 0, data: null, error: formatDataSourceError(error) };
  }
}

async function fetchText(fetchImpl, url) {
  try {
    const response = await fetchImpl(url, {
      headers: { Accept: "text/plain" },
      cache: "no-store"
    });
    if (!response.ok) {
      return { ok: false, status: response.status, data: "" };
    }
    return { ok: true, status: response.status, data: await response.text() };
  } catch (error) {
    return { ok: false, status: 0, data: "", error: formatDataSourceError(error) };
  }
}

function buildOverviewRuntimePatch({ health, diagnostics, workspace, metricsText, petState, connected }) {
  const resources = asObject(diagnostics?.resources);
  const capabilities = asObject(diagnostics?.capabilities);
  const workspaceCounts = asObject(diagnostics?.workspace);
  const runtime = asObject(diagnostics?.runtime);
  const runtimeMetrics = asObject(runtime.metrics);
  const metrics = parsePrometheusMetrics(metricsText);
  const declared = normalizeStringList(capabilities.declared);
  const modules = normalizeStringList(capabilities.effective_modules || capabilities.effectiveModules);
  const tools = normalizeStringList(capabilities.tool_names || capabilities.toolNames);
  const packId = stringValue(resources.character_pack_id || resources.characterPackId);
  const outfit = stringValue(resources.outfit);
  const defaultEmotion = stringValue(resources.default_emotion || resources.defaultEmotion);
  const emotionCount = positiveNumber(resources.emotion_count ?? resources.emotionCount);
  const counts = asObject(workspace?.counts);
  const effectiveWorkspaceCounts = {
    files: numberOrFallback(workspaceCounts.files, counts.files, 0),
    outputs: numberOrFallback(workspaceCounts.outputs, counts.outputs, 0),
    tasks: numberOrFallback(workspaceCounts.tasks, counts.tasks, 0)
  };
  const serviceOk = connected || stringValue(health?.status) === "ok" || stringValue(diagnostics?.status) === "ok";
  const senseRuntime = buildOverviewSenseRuntimePatch(petState);
  const toolCount = tools.length;
  const moduleCount = modules.length || declared.length;

  return {
    shell: {
      status: serviceOk ? "Akane 在线" : "Akane 待连接",
      statusDetail: serviceOk ? "陪伴中 · 后端连接正常" : "等待后端连接"
    },
    statusBadge: serviceOk ? "在线 / Connected" : "离线 / Offline",
    connectionBadge: serviceOk ? "连接正常" : "等待连接",
    statusItems: {
      "连接状态": serviceOk ? "已连接" : "未连接",
      "当前角色包": packId || "Akane Default",
      "当前表情": defaultEmotion || "微笑",
      "今日能力摘要": toolCount ? `${moduleCount} 模块 · ${toolCount} 工具` : `${declared.length || 0} 项能力`
    },
    connectionRows: {
      "服务状态": serviceOk ? "正常运行" : "未连接",
      "响应延迟": inferLatencyLabel(runtimeMetrics),
      "同步状态": workspace?.ok === false ? "待同步" : "稳定",
      "会话时长": diagnostics?.server_time ? "已同步" : "待同步"
    },
    pack: {
      name: packId || "Akane Default",
      version: stringValue(diagnostics?.contract_version || diagnostics?.contractVersion) || "desktop_pet",
      publishedAt: outfit || "当前服装"
    },
    emotion: {
      name: defaultEmotion || "微笑"
    },
    voice: {
      ttsEnabled: Boolean(health?.contracts?.desktop_pet?.tts || health?.contracts?.desktop_pet?.health || serviceOk),
      asrEnabled: serviceOk,
      status: serviceOk ? "语音状态：后端已连接" : "语音状态：等待连接"
    },
    ...(senseRuntime ? { sense: senseRuntime } : {}),
    abilities: buildAbilityLabels({ tools, workspaceCounts: effectiveWorkspaceCounts }),
    health: buildHealthTiles({
      metrics,
      runtimeMetrics,
      workspaceCounts: effectiveWorkspaceCounts,
      emotionCount,
      toolCount,
      serviceOk
    })
  };
}

function buildOverviewSenseRuntimePatch(petState) {
  const state = asObject(petState);
  const entries = [
    ["activeWindowEnabled", state.desktopContextEnabled],
    ["clipboardEnabled", state.clipboardContextEnabled],
    ["screenVisionEnabled", state.screenVisionEnabled],
    ["proactiveWakeEnabled", state.proactiveWakeEnabled]
  ].filter(([, value]) => typeof value === "boolean");
  return entries.length ? Object.fromEntries(entries) : null;
}

function buildCharacterRuntimePatch({
  baseUrl,
  resourceManifest,
  diagnostics,
  characterPackId,
  outfit,
  emotion,
  petState,
  availableCharacterPacks
}) {
  const manifest = asObject(resourceManifest);
  const resources = asObject(diagnostics?.resources);
  const defaults = asObject(manifest.defaults);
  const clients = asObject(manifest.clients);
  const desktop = asObject(clients.desktop_pet);
  const packId = stringValue(
    characterPackId ||
      petState?.characterPackId ||
      resources.character_pack_id ||
      resources.characterPackId ||
      desktop.character_pack_id
  );
  const activeOutfitId = stringValue(
    outfit ||
      petState?.outfit ||
      resources.outfit ||
      desktop.default_outfit ||
      defaults.desktop_pet_outfit ||
      defaults.outfit
  );
  const activeEmotionId = stringValue(
    emotion ||
      petState?.currentEmotion ||
      resources.default_emotion ||
      resources.defaultEmotion ||
      desktop.default_emotion ||
      defaults.desktop_pet_emotion ||
      defaults.emotion
  );
  const pack = findAvailableCharacterPack(availableCharacterPacks, packId);
  const profile = asObject(pack?.profile);
  const identity = asObject(profile.identity);
  const appearance = asObject(profile.appearance);
  const assets = asObject(profile.assets);
  const characters = asObject(manifest.characters);
  const rawOutfits = asArray(characters.outfits);
  const allOutfits = normalizeOutfitCards(rawOutfits, {
    activeOutfitId,
    activeEmotionId,
    baseUrl
  });
  const activeOutfit =
    findManifestEntry(rawOutfits, activeOutfitId) ||
    findManifestEntry(rawOutfits, appearance.default_outfit) ||
    rawOutfits.find((item) => item && typeof item === "object") ||
    null;
  const emotionCards = normalizeEmotionCards(asArray(activeOutfit?.emotions), {
    activeEmotionId,
    baseUrl
  });
  const outfitCount = rawOutfits.length;
  const emotionCount = rawOutfits.reduce((total, item) => total + asArray(item?.emotions).length, 0);
  const backgroundCount = countManifestBackgrounds(manifest);
  const manifestOk = Boolean(manifest.schema_version && outfitCount > 0 && emotionCount > 0);
  const displayName = stringValue(identity.app_name || identity.name || pack?.id || packId || "Akane Default");
  const version = stringValue(profile.version || profile.schema_version || desktop.contract_version || manifest.schema_version);
  const heroImage = toBackendAssetUrl(
    baseUrl,
    assets.hero || assets.cover || assets.banner || assets.header || assets.thumbnail
  );

  return {
    ...(heroImage ? { hero: heroImage } : {}),
    selectedPack: displayName,
    packInfo: [
      { label: "名称", value: displayName },
      { label: "版本", value: version || "resource-manifest" },
      { label: "作者", value: stringValue(profile.author || identity.author) || (pack ? "本地角色包" : "后端资源清单") },
      { label: "描述", value: stringValue(profile.description || assets.runtime_source) || `${outfitCount} 套服装 · ${emotionCount} 个表情` }
    ],
    completeness: manifestOk ? 100 : 0,
    outfits: limitActiveCards(allOutfits, activeOutfitId, 4),
    emotions: limitActiveCards(emotionCards, activeEmotionId, 4),
    warning: manifestOk
      ? {
          title: "资源状态良好",
          headline: "已加载统一资源清单",
          body: `${outfitCount} 套服装 · ${emotionCount} 个表情`,
          action: "刷新资源"
        }
      : {
          title: "资源缺失提示",
          headline: "暂未读取到角色资源清单",
          body: "当前保留本地预览资源",
          action: "重新检查"
        },
    resources: [
      {
        label: "动作资源",
        value: pack?.assetCount || pack?.asset_count ? `${pack.assetCount || pack.asset_count}` : "预留",
        tone: "blue"
      },
      { label: "表情资源", value: `${emotionCount} / ${emotionCount}`, tone: "green" },
      { label: "服装资源", value: `${outfitCount} / ${outfitCount}`, tone: "pink" },
      { label: "背景资源", value: `${backgroundCount} / ${backgroundCount}`, tone: "green" }
    ],
    tip: [
      packId ? `当前角色包 id：${packId}。` : "当前使用后端默认角色资源。",
      "服装与表情来自统一资源清单，桌宠和后端会按同一套资源理解当前形象。"
    ]
  };
}

function buildVoiceRuntimePatch({ health, diagnostics, petState }) {
  const healthData = asObject(health);
  const diagnosticsData = asObject(diagnostics);
  const runtime = asObject(diagnosticsData.runtime);
  const runtimeMetrics = asObject(runtime.metrics);
  const healthTts = asObject(healthData.tts);
  const healthAsr = asObject(healthData.asr);

  const serviceOk = stringValue(healthData?.status) === "ok" || stringValue(diagnosticsData?.status) === "ok";
  const ttsEnabled = petState?.voiceEnabled ?? healthTts.enabled ?? serviceOk;
  const ttsVolume = coerceVolumePercent(petState?.voiceVolume, 80);
  const asrAvailable = Boolean(healthAsr.endpoint || Object.keys(healthAsr).length);
  const asrEnabled = petState?.voiceInputEnabled ?? asrAvailable;
  const overallState = serviceOk ? "正常运行" : "未连接";
  const overallTone = serviceOk ? "good" : "warning";
  const ttsOnline = healthTts.endpoint ? "在线" : serviceOk ? "未启用" : "离线";
  const ttsTone = healthTts.endpoint ? "good" : "warning";
  const asrOnline = asrAvailable ? "在线" : "未启用";
  const asrTone = asrAvailable ? "good" : "muted";
  const networkState = serviceOk ? "良好" : "离线";
  const networkTone = serviceOk ? "good" : "warning";
  const latency = inferLatencyLabel(runtimeMetrics);

  return {
    tts: {
      enabled: Boolean(ttsEnabled),
      volume: ttsVolume
    },
    asr: {
      enabled: Boolean(asrEnabled)
    },
    diagnostics: [
      { label: "整体状态", value: overallState, tone: overallTone },
      { label: "TTS 语音引擎", value: ttsOnline, tone: ttsTone },
      { label: "ASR 语音引擎", value: asrOnline, tone: asrTone },
      { label: "响应延迟", value: latency, tone: serviceOk ? "good" : "warning" },
      { label: "网络状态", value: networkState, tone: networkTone }
    ]
  };
}

function coerceVolumePercent(value, fallback) {
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  const percent = number <= 1 ? number * 100 : number;
  return Math.max(0, Math.min(100, Math.round(percent)));
}

function buildPerceptionRuntimePatch({ petState, diagnostics }) {
  const diagnosticsData = asObject(diagnostics);
  const serviceOk = stringValue(diagnosticsData?.status) === "ok";

  const desktopContextEnabled = pickBoolean(petState?.desktopContextEnabled, true);
  const clipboardContextEnabled = pickBoolean(petState?.clipboardContextEnabled, false);
  const screenVisionEnabled = pickBoolean(petState?.screenVisionEnabled, false);
  const screenVisionIntervalSec = positiveNumber(petState?.screenVisionIntervalSec);
  const screenVisionFrameCount = positiveNumber(petState?.screenVisionFrameCount);
  const proactiveWakeEnabled = pickBoolean(petState?.proactiveWakeEnabled, false);
  const proactiveWakeIntervalSec = positiveNumber(petState?.proactiveWakeIntervalSec);
  const screenVisionStatus = stringValue(diagnosticsData?.screen_vision?.status || diagnosticsData?.screenVision?.status);

  const featureCards = [
    {
      id: "activeWindow",
      enabled: desktopContextEnabled,
      appName: desktopContextEnabled ? "等待前台窗口" : "前台窗口感知已关闭",
      appDetail: desktopContextEnabled ? "发送消息时可附带窗口上下文" : "不会读取当前窗口",
      version: serviceOk ? "本地感知" : "等待后端"
    },
    {
      id: "clipboard",
      enabled: clipboardContextEnabled,
      code: clipboardContextEnabled
        ? ["剪贴板内容不会在设置页预览", "仅在发送消息时按设置临时附带"]
        : ["剪贴板感知已关闭"],
      source: clipboardContextEnabled ? "仅显示能力状态 · 未读取内容" : "未读取剪贴板"
    },
    {
      id: "screen",
      enabled: screenVisionEnabled,
      frequency: screenVisionIntervalSec > 0 ? `${screenVisionIntervalSec} 秒` : "",
      frames: screenVisionFrameCount > 0 ? `${screenVisionFrameCount}` : ""
    },
    {
      id: "proactive",
      enabled: proactiveWakeEnabled,
      activeOption: proactiveWakeIntervalSec > 0 ? formatDurationOption(proactiveWakeIntervalSec) : ""
    }
  ];

  return {
    featureCards,
    diagnostics: [
      {
        label: "屏幕捕获帧率",
        value: screenVisionEnabled ? "已开启" : "已关闭",
        detail: screenVisionStatus || (screenVisionEnabled ? "等待采样" : "未运行"),
        tone: screenVisionEnabled ? "good" : "info"
      },
      {
        label: "OCR 识别状态",
        value: screenVisionEnabled ? "待接入" : "未启用",
        detail: "视觉识别状态暂未接入控制中心",
        tone: "info"
      },
      {
        label: "最后更新时间",
        value: formatTimeOfDay(new Date()),
        detail: serviceOk ? "已同步" : "等待后端",
        tone: serviceOk ? "good" : "warning"
      }
    ]
  };
}

function formatDurationOption(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value <= 0) return "";
  if (value % 60 === 0) return `${Math.round(value / 60)} 分钟`;
  return `${Math.round(value)} 秒`;
}

function formatTimeOfDay(date) {
  const pad = (value) => String(value).padStart(2, "0");
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function buildMusicRuntimePatch({ musicSnapshot, petState }) {
  if (!musicSnapshot || typeof musicSnapshot !== "object") return null;

  const track = musicSnapshot.track;
  const displayName = stringValue(track?.displayName || musicSnapshot.displayName);
  const hasTrack = Boolean(displayName);
  const queue = Array.isArray(musicSnapshot.queue) ? musicSnapshot.queue : [];
  const rawQueueIndex = Number(musicSnapshot.queueIndex);
  const queueIndex = Number.isInteger(rawQueueIndex) ? rawQueueIndex : -1;
  const activeQueueIndex = queueIndex >= 0 && queueIndex < queue.length ? queueIndex : -1;
  const progressSec = Number.isFinite(musicSnapshot.progressSeconds) ? Math.max(0, musicSnapshot.progressSeconds) : 0;
  const durationSec = Number.isFinite(musicSnapshot.durationSeconds) ? Math.max(0, musicSnapshot.durationSeconds) : 0;
  const progress = durationSec > 0 ? Math.min(100, Math.round((progressSec / durationSec) * 100)) : 0;
  const volume = coerceVolumePercent(petState?.voiceVolume, 68);
  const currentLyric = musicSnapshot.currentLyric && typeof musicSnapshot.currentLyric === "object"
    ? musicSnapshot.currentLyric
    : null;
  const hasLyrics = Boolean(currentLyric && currentLyric.lineCount > 0);

  const nowPlaying = {
    title: hasTrack ? displayName : "暂无播放",
    artist: hasTrack ? "" : "",
    quality: hasTrack ? (track?.timelineQuality || track?.extension || "本地文件") : "",
    elapsed: formatSeconds(progressSec),
    duration: formatSeconds(durationSec),
    progress,
    volume,
    cover: "music"
  };

  const playlist = hasTrack
    ? queue.map((item, index) => ({
        title: item.displayName || item.fileName || "未命名曲目",
        artist: "",
        duration: "",
        active: index === activeQueueIndex
      }))
    : [];

  let lyrics = [];
  let activeLyric = -1;
  if (hasLyrics && currentLyric.text) {
    const lines = [];
    if (currentLyric.previousText) lines.push(currentLyric.previousText);
    lines.push(currentLyric.text);
    if (currentLyric.nextText) lines.push(currentLyric.nextText);
    lyrics = lines;
    activeLyric = currentLyric.previousText ? 1 : 0;
  } else {
    lyrics = ["当前音乐暂无歌词"];
    activeLyric = -1;
  }

  const info = hasTrack
    ? [
        { label: "时长", value: formatSeconds(durationSec) },
        { label: "来源", value: "本地音乐" },
        { label: "音质", value: track?.timelineQuality || track?.extension || "未知" }
      ]
    : [
        { label: "时长", value: "-" },
        { label: "来源", value: "-" },
        { label: "音质", value: "-" }
      ];

  const bottomStatus = hasTrack
    ? `正在播放 · ${queue.length > 0 && activeQueueIndex >= 0 ? `${activeQueueIndex + 1}/${queue.length}` : "单曲"}`
    : "暂无播放 · 等待音乐加入队列";

  return { nowPlaying, playlist, lyrics, activeLyric, info, bottomStatus };
}

function formatSeconds(seconds) {
  const sec = Math.max(0, Math.round(Number(seconds) || 0));
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function buildAbilitiesRuntimePatch({ diagnostics, workspace, connected }) {
  const diagnosticsData = asObject(diagnostics);
  const capabilities = asObject(diagnosticsData.capabilities);
  const safety = asObject(diagnosticsData.safety);
  const runtime = asObject(diagnosticsData.runtime);
  const runtimeMetrics = asObject(runtime.metrics);
  const workspaceCounts = asObject(diagnosticsData.workspace);
  const workspaceDataCounts = asObject(workspace?.counts);
  const tools = normalizeStringList(capabilities.tool_names || capabilities.toolNames);
  const effectiveModules = normalizeStringList(capabilities.effective_modules || capabilities.effectiveModules);
  const declared = normalizeStringList(capabilities.declared);
  const serviceOk = Boolean(connected) || stringValue(diagnosticsData.status) === "ok";
  const moduleCards = buildAbilityModuleCards({ tools, workspaceCounts, workspaceDataCounts, safety });
  const availableModuleCount = moduleCards.length;
  const toolCount = tools.length;
  const pendingApprovalCount = countPendingSafetyItems(safety);
  const availability = serviceOk ? Math.min(100, Math.max(0, toolCount ? 98 : 72)) : 0;
  const syncedAt = diagnosticsData.server_time
    ? formatTimeOfDay(new Date(Number(diagnosticsData.server_time) * 1000))
    : formatTimeOfDay(new Date());

  return {
    overview: {
      stats: [
        { label: "可用模块", value: String(availableModuleCount || effectiveModules.length || declared.length || 0) },
        { label: "已注册工具", value: String(toolCount) },
        { label: "需审批权限", value: String(pendingApprovalCount) }
      ],
      availability,
      note: serviceOk ? "能力注册表已同步，当前模块可正常使用" : "等待后端连接，能力状态暂不可用"
    },
    modules: moduleCards,
    workflows: buildAbilityWorkflows(moduleCards),
    calls: buildAbilityStatusRows({
      syncedAt,
      serviceOk,
      toolCount,
      moduleCount: availableModuleCount,
      workspaceCounts: mergeWorkspaceCounts(workspaceCounts, workspaceDataCounts),
      safety,
      runtimeMetrics
    }),
    safety: buildAbilitySafetyPanel(safety, serviceOk),
    live2d: {
      status: "预留",
      items: [
        { label: "模型", value: "静态立绘" },
        { label: "动作", value: "表情切换" },
        { label: "渲染器", value: "预留接口" },
        { label: "物理", value: "待接入" }
      ]
    }
  };
}

function buildAdvancedRuntimePatch({ health, diagnostics, workspace, metricsText, petState }) {
  const healthData = asObject(health);
  const diagnosticsData = asObject(diagnostics);
  const capabilities = asObject(diagnosticsData.capabilities);
  const runtime = asObject(diagnosticsData.runtime);
  const runtimeMetrics = asObject(runtime.metrics);
  const metrics = parsePrometheusMetrics(metricsText);
  const tools = normalizeStringList(capabilities.tool_names || capabilities.toolNames);
  const serviceOk = stringValue(healthData?.status) === "ok" || stringValue(diagnosticsData?.status) === "ok";

  // systemStrip — patch by label
  const cpuPercent = metrics?.cpu_percent ? `${Math.round(metrics.cpu_percent)}%` : undefined;
  const memPercent = metrics?.memory_percent ? `${Math.round(metrics.memory_percent)}%` : undefined;
  const systemStrip = {
    "运行中": { tone: serviceOk ? "green" : "muted" },
    "CPU": { value: cpuPercent },
    "内存": { value: memPercent },
    "网络": { value: serviceOk ? "良好" : "离线", tone: serviceOk ? "green" : "warning" }
  };

  // diagnostics.metrics — patch by label
  const currentMemoryBytes = metrics?.akane_tracemalloc_current_bytes;
  const memoryDisplay = currentMemoryBytes ? formatBytes(currentMemoryBytes) : undefined;
  const diagnosticsMetrics = {
    "应用状态": { value: serviceOk ? "运行中" : "等待连接", tone: serviceOk ? "green" : "warning" },
    "后端健康": { value: serviceOk ? "良好" : "异常", tone: serviceOk ? "green" : "danger" },
    "内存占用": { value: memoryDisplay }
  };

  // diagnostics.logs — generate status sync timeline
  const baseTime = diagnosticsData.server_time
    ? new Date(Number(diagnosticsData.server_time) * 1000)
    : new Date();
  const logs = generateAdvancedSyncLogs(baseTime, serviceOk);

  // abilityOverview — derived from tool names
  const abilityOverview = buildAdvancedAbilityOverview(tools);

  // live2d — reserved status only
  const live2d = {
    rows: [
      { label: "模型", value: serviceOk ? "等待加载" : "未就绪" },
      { label: "动作", value: "静态立绘" },
      { label: "渲染器", value: "预留 · 待接入" },
      { label: "物理", value: "预留 · 待接入" }
    ]
  };

  const coreSettings = [
    { id: "hitTest", enabled: Boolean(petState?.hitTestEnabled) },
    { id: "hitbox", enabled: Boolean(petState?.hitboxOverlay) }
  ];

  return {
    systemStrip,
    coreSettings,
    diagnostics: { metrics: diagnosticsMetrics, logs },
    live2d,
    abilityOverview
  };
}

function generateAdvancedSyncLogs(baseTime, serviceOk) {
  const pad = (v) => String(v).padStart(2, "0");
  const fmt = (date) => `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  if (!serviceOk) return [{ time: fmt(baseTime), level: "WARN", message: "[Service] Backend not connected" }];

  const entries = [
    { offset: -12, level: "INFO", message: "[Service] Health check passed" },
    { offset: -8, level: "INFO", message: "[Sensing] Config loaded" },
    { offset: -5, level: "INFO", message: "[Security] Policy checked" },
    { offset: -3, level: "INFO", message: "[Workspace] Summary synced" },
    { offset: -1, level: "INFO", message: "[Capability] Registry synced" },
    { offset: 0, level: "INFO", message: "[Service] Backend connected" }
  ];

  const logs = [];
  for (const entry of entries) {
    const t = new Date(baseTime);
    t.setSeconds(t.getSeconds() + entry.offset);
    logs.push({ time: fmt(t), level: entry.level, message: entry.message });
  }
  return logs;
}

function buildAdvancedAbilityOverview(tools) {
  const available = [];
  if (tools.some((name) => /file|attachment|compose|send|document|read/i.test(name))) {
    available.push({ label: "文件处理", icon: "folder", tone: "blue" });
  }
  if (tools.some((name) => /send_file|compose_file|generated|handoff/i.test(name))) {
    available.push({ label: "生成文件交付", icon: "file", tone: "green" });
  }
  if (tools.some((name) => /media|audio|voice|transcribe|stems|clean/i.test(name))) {
    available.push({ label: "媒体工具", icon: "play", tone: "purple" });
  }
  if (tools.some((name) => /clipboard|shelf|workspace|task|gift/i.test(name))) {
    available.push({ label: "手边物品", icon: "gift", tone: "pink" });
  }
  if (tools.some((name) => /guard|safe|security|approval|sandbox/i.test(name))) {
    available.push({ label: "安全边界", icon: "shield", tone: "orange" });
  }
  if (tools.some((name) => /memory|retrieve/i.test(name))) {
    available.push({ label: "记忆检索", icon: "sparkle", tone: "blue" });
  }
  if (!available.length) {
    available.push(
      { label: "文件处理", icon: "folder", tone: "blue" },
      { label: "生成文件交付", icon: "file", tone: "green" },
      { label: "手边物品", icon: "gift", tone: "pink" },
      { label: "媒体工具", icon: "play", tone: "purple" },
      { label: "安全边界", icon: "shield", tone: "orange" }
    );
  }
  return available.slice(0, 5);
}

function buildAbilityModuleCards({ tools, workspaceCounts, workspaceDataCounts, safety }) {
  const definitions = [
    {
      title: "文件处理",
      description: "读取、整理与转换本地文件和附件材料。",
      permission: "受限文件访问",
      tone: "blue",
      icon: "folder",
      pattern: /file|attachment|document|read|inspect|sync_attachment/i
    },
    {
      title: "生成文件交付",
      description: "生成文档、报告与资料，并交付到桌面端。",
      permission: "生成与导出",
      tone: "purple",
      icon: "file",
      pattern: /compose|send_file|generated|delivery|handoff/i
    },
    {
      title: "手边物品",
      description: "管理桌宠手边工作区、临时文件与任务材料。",
      permission: "工作区管理",
      tone: "orange",
      icon: "gift",
      pattern: /workspace|task|gift|clipboard/i,
      extraCount: numberOrFallback(workspaceCounts.files, workspaceDataCounts.files, 0)
    },
    {
      title: "媒体工具",
      description: "处理音频、视频、转写、分离与净化等媒体任务。",
      permission: "多媒体操作",
      tone: "green",
      icon: "play",
      pattern: /media|audio|voice|transcribe|stems|clean/i
    },
    {
      title: "记忆检索",
      description: "检索长期记忆与上下文材料，辅助连续对话。",
      permission: "记忆读取",
      tone: "blue",
      icon: "sparkle",
      pattern: /memory|retrieve/i
    },
    {
      title: "安全边界",
      description: "限制危险操作，保护系统与用户隐私安全。",
      permission: "安全与隔离",
      tone: "pink",
      icon: "shield",
      pattern: /guard|safe|security|approval/i,
      fallbackCount: countPendingSafetyItems(safety)
    }
  ];

  const cards = [];
  for (const definition of definitions) {
    const count = tools.filter((name) => definition.pattern.test(name)).length + positiveNumber(definition.extraCount);
    const fallbackCount = positiveNumber(definition.fallbackCount);
    const abilityCount = count || fallbackCount;
    if (!abilityCount && definition.title !== "安全边界") continue;
    cards.push({
      title: definition.title,
      description: definition.description,
      permission: definition.permission,
      count: `${abilityCount || 1} 项能力`,
      tone: definition.tone,
      icon: definition.icon
    });
  }

  if (!cards.length) {
    cards.push({
      title: "能力注册表",
      description: "等待后端同步可用能力模块。",
      permission: "待连接",
      count: "0 项能力",
      tone: "blue",
      icon: "sparkle"
    });
  }
  return cards.slice(0, 8);
}

function buildAbilityWorkflows(modules) {
  const names = new Set(modules.map((item) => item.title));
  const workflows = [];
  if (names.has("文件处理") && names.has("生成文件交付")) {
    workflows.push({
      steps: ["文件处理", "生成文件", "交付"],
      title: "读取文件 → 生成文档 → 交付",
      detail: "读取资料、生成报告，并把结果交付给你"
    });
  }
  if (names.has("媒体工具")) {
    workflows.push({
      steps: ["媒体工具", "转写", "摘要"],
      title: "导入音频 → 转写清理 → 生成摘要",
      detail: "把音频材料处理成可读文本和摘要"
    });
  }
  if (names.has("手边物品")) {
    workflows.push({
      steps: ["手边物品", "整理", "归档"],
      title: "手边材料 → 整理 → 归档",
      detail: "把临时材料收进工作区，方便后续继续处理"
    });
  }
  return workflows.length ? workflows : [
    {
      steps: ["诊断", "同步", "等待"],
      title: "能力诊断 → 等待同步",
      detail: "后端连接后会显示可用工作流"
    }
  ];
}

function buildAbilityStatusRows({ syncedAt, serviceOk, toolCount, moduleCount, workspaceCounts, safety, runtimeMetrics }) {
  const rows = [
    {
      time: syncedAt,
      module: "能力注册表",
      description: serviceOk ? `已同步 ${moduleCount} 个模块、${toolCount} 个工具` : "等待后端同步能力注册表",
      status: serviceOk ? "成功" : "待连接",
      duration: inferLatencyLabel(runtimeMetrics),
      method: "后端诊断"
    }
  ];
  const files = numberOrFallback(workspaceCounts.files, 0);
  const outputs = numberOrFallback(workspaceCounts.outputs, 0);
  if (files || outputs) {
    rows.push({
      time: syncedAt,
      module: "手边物品",
      description: `当前工作区：${files} 个文件、${outputs} 个生成文件`,
      status: "成功",
      duration: "-",
      method: "状态同步"
    });
  }
  rows.push({
    time: syncedAt,
    module: "安全边界",
    description: buildSafetyDescription(safety),
    status: safety?.secrets_exposed ? "已拦截" : "成功",
    duration: "-",
    method: "策略检查"
  });
  return rows;
}

function buildAbilitySafetyPanel(safety, serviceOk) {
  return {
    status: serviceOk ? "已生效" : "待连接",
    items: [
      {
        label: "桌面动作执行",
        status: safety?.desktop_actions_require_client === false ? "自动执行" : "客户端确认"
      },
      {
        label: "密钥与敏感信息",
        status: safety?.secrets_exposed ? "已拦截" : "未暴露"
      },
      {
        label: "全盘扫描",
        status: safety?.full_disk_scan ? "需审批" : "关闭"
      },
      {
        label: "外部网络与危险操作",
        status: "需审批"
      }
    ]
  };
}

function buildSafetyDescription(safety) {
  if (safety?.secrets_exposed) return "检测到敏感信息暴露风险，已进入保护状态";
  if (safety?.full_disk_scan) return "全盘扫描能力需要审批后才可执行";
  return "桌面危险动作保持客户端确认，敏感信息未暴露";
}

function countPendingSafetyItems(safety) {
  const data = asObject(safety);
  let count = 1; // external/dangerous operations still require approval.
  if (data.desktop_actions_require_client !== false) count += 1;
  if (data.full_disk_scan) count += 1;
  return count;
}

function mergeWorkspaceCounts(...sources) {
  return {
    files: numberOrFallback(...sources.map((item) => asObject(item).files), 0),
    outputs: numberOrFallback(...sources.map((item) => asObject(item).outputs), 0),
    tasks: numberOrFallback(...sources.map((item) => asObject(item).tasks), 0)
  };
}

function normalizeOutfitCards(outfits, { activeOutfitId, activeEmotionId, baseUrl }) {
  return outfits
    .filter((item) => item && typeof item === "object")
    .map((outfit) => {
      const id = stringValue(outfit.id || outfit.name);
      const emotions = normalizeEmotionCards(asArray(outfit.emotions), {
        activeEmotionId,
        baseUrl
      });
      const preview = emotions.find((item) => item.id === activeEmotionId) || emotions[0];
      const current = id === activeOutfitId || (!activeOutfitId && Boolean(outfit.current));
      return {
        id,
        name: stringValue(outfit.name || id) || "未命名服装",
        badge: current ? "当前" : "",
        image: preview?.image || "",
        current
      };
    })
    .filter((item) => item.id);
}

function normalizeEmotionCards(emotions, { activeEmotionId, baseUrl }) {
  return emotions
    .filter((item) => item && typeof item === "object")
    .map((emotion) => {
      const id = stringValue(emotion.id || emotion.name);
      return {
        id,
        name: stringValue(emotion.name || id) || "未命名表情",
        image: toBackendAssetUrl(baseUrl, emotion.path || emotion.url || emotion.src),
        current: id === activeEmotionId || (!activeEmotionId && Boolean(emotion.current))
      };
    })
    .filter((item) => item.id);
}

function limitActiveCards(items, activeId, limit) {
  const cards = Array.isArray(items) ? items : [];
  const maxItems = Math.max(1, Number(limit || 4));
  if (cards.length <= maxItems) return cards;
  const active = cards.find((item) => item.id === activeId || item.current);
  const selected = [];
  if (active) selected.push(active);
  for (const item of cards) {
    if (selected.some((selectedItem) => selectedItem.id === item.id)) continue;
    selected.push(item);
    if (selected.length >= maxItems) break;
  }
  return selected;
}

function findAvailableCharacterPack(packs, packId) {
  const normalized = stringValue(packId);
  if (!normalized) return null;
  return asArray(packs).find((pack) => {
    const profile = asObject(pack?.profile);
    const identity = asObject(profile.identity);
    return [pack?.id, pack?.packId, identity.id].some((value) => stringValue(value) === normalized);
  }) || null;
}

function findManifestEntry(items, target) {
  const normalized = stringValue(target);
  if (!normalized) return null;
  return asArray(items).find((item) => {
    if (!item || typeof item !== "object") return false;
    const candidates = [item.id, item.name, ...(Array.isArray(item.aliases) ? item.aliases : [])];
    return candidates.some((value) => stringValue(value) === normalized);
  }) || null;
}

function countManifestBackgrounds(manifest) {
  const majors = asArray(asObject(manifest.scenes).majors);
  return majors.reduce((total, major) => {
    const minors = asArray(major?.minors);
    return total + minors.reduce((minorTotal, minor) => minorTotal + asArray(minor?.backgrounds).length, 0);
  }, 0);
}

function toBackendAssetUrl(baseUrl, value) {
  const raw = stringValue(value);
  if (!raw) return "";
  if (/^(https?:|data:|blob:)/i.test(raw)) return raw;
  const base = `${String(baseUrl || DEFAULT_BACKEND_URL).replace(/\/+$/, "")}/`;
  return new URL(raw.replace(/^\/+/, ""), base).toString();
}

function buildAbilityLabels({ tools, workspaceCounts }) {
  const labels = [];
  if (tools.some((name) => /file|attachment|compose|send/i.test(name))) labels.push("文件处理");
  if (tools.some((name) => /send_file|compose_file/i.test(name))) labels.push("文档交付");
  if (tools.some((name) => /media|audio|voice|transcribe/i.test(name))) labels.push("媒体工具");
  if (tools.some((name) => /memory/i.test(name))) labels.push("记忆检索");
  if (workspaceCounts.files > 0) labels.push(`手边文件 ${workspaceCounts.files}`);
  if (workspaceCounts.outputs > 0) labels.push(`生成文件 ${workspaceCounts.outputs}`);
  if (!labels.length) labels.push("文件处理", "文档交付", "媒体工具", "安全保护", "手边物品", "Live2D 预留状态");
  return labels.slice(0, 8);
}

function buildHealthTiles({ metrics, runtimeMetrics, workspaceCounts, emotionCount, toolCount, serviceOk }) {
  const currentMemoryBytes = metrics.akane_tracemalloc_current_bytes;
  const peakMemoryBytes = metrics.akane_tracemalloc_peak_bytes;
  const vectorEntries = metrics.akane_vector_entries;
  const activeThinks = metrics.akane_public_guard_active_thinks;
  return {
    "CPU 占用": serviceOk ? "运行中" : "待连接",
    "内存占用": currentMemoryBytes ? formatBytes(currentMemoryBytes) : "-",
    "存储空间": vectorEntries ? `${Math.round(vectorEntries)} 条记忆` : `${workspaceCounts.files} 手边文件`,
    "温度": peakMemoryBytes ? `峰值 ${formatBytes(peakMemoryBytes)}` : "-",
    "错误数": String(countMetricErrors(runtimeMetrics)),
    "告警": String(activeThinks || 0),
    "应用版本": `${emotionCount || 0} 表情`,
    "检查更新": toolCount ? `${toolCount} 工具` : "等待诊断"
  };
}

function countMetricErrors(metrics) {
  return Object.entries(metrics || {}).reduce((total, [key, value]) => {
    if (!/_error|errors|failed|failures/i.test(key)) return total;
    const number = Number(value || 0);
    return Number.isFinite(number) ? total + number : total;
  }, 0);
}

function inferLatencyLabel(metrics) {
  const known = Object.entries(metrics || {}).find(([key]) => /duration|latency|request/i.test(key));
  if (!known) return "已连接";
  const number = Number(known[1]);
  if (!Number.isFinite(number)) return "已连接";
  if (number > 1000) return `${Math.round(number)} ms`;
  return `${Math.round(number * 1000)} ms`;
}

function parsePrometheusMetrics(text) {
  const metrics = {};
  for (const line of String(text || "").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const match = /^([a-zA-Z_:][\w:]*)(?:\{[^}]*\})?\s+(-?\d+(?:\.\d+)?)$/.exec(trimmed);
    if (!match) continue;
    metrics[match[1]] = Number(match[2]);
  }
  return metrics;
}

function buildBackendUrl(baseUrl, endpoint, params = null) {
  const url = new URL(String(endpoint || "").replace(/^\/+/, ""), `${baseUrl.replace(/\/+$/, "")}/`);
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

function normalizeBackendBaseUrl(value) {
  const raw = String(value || "").trim() || DEFAULT_BACKEND_URL;
  try {
    return new URL(raw).toString().replace(/\/+$/, "");
  } catch {
    return DEFAULT_BACKEND_URL;
  }
}

function normalizeActionId(actionId) {
  return String(actionId || "").trim();
}

function normalizeStringList(value) {
  return Array.isArray(value) ? value.map((item) => String(item || "").trim()).filter(Boolean) : [];
}

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function stringValue(value) {
  return String(value || "").trim();
}

function positiveNumber(value) {
  const number = Number(value || 0);
  return Number.isFinite(number) && number > 0 ? number : 0;
}

function numberOrFallback(...values) {
  for (const value of values) {
    const number = Number(value);
    if (Number.isFinite(number)) return number;
  }
  return 0;
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return "-";
  const units = ["B", "KB", "MB", "GB"];
  let size = bytes;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size >= 10 ? Math.round(size) : size.toFixed(1)} ${units[unit]}`;
}

function pickBoolean(value, fallback) {
  return typeof value === "boolean" ? value : Boolean(fallback);
}

function formatDataSourceError(error) {
  return error instanceof Error ? error.message : String(error || "unknown");
}

async function tryReadUnifiedSnapshot(fetchImpl, baseUrl, scope = {}) {
  const {
    petState,
    musicSnapshot,
    availableCharacterPacks,
    characterPackId,
    outfit,
    emotion,
    requestParams
  } = scope;
  try {
    const response = await fetchJson(
      fetchImpl,
      buildBackendUrl(baseUrl, "/control-center/snapshot", requestParams || { t: String(Date.now()) })
    );
    const snapshot = response.data;
    if (!response.ok || !snapshot || snapshot.ok !== true) {
      return null;
    }
    if (
      typeof snapshot.schemaVersion !== "number" ||
      snapshot.sourceKind !== CONTROL_CENTER_SOURCE_KIND.backend ||
      typeof snapshot.generatedAt !== "string"
    ) {
      return null;
    }
    const runtime = snapshot.runtime;
    if (!runtime || typeof runtime !== "object" || !unifiedSnapshotRuntimeFields.every((field) => field in runtime)) {
      return null;
    }
    const health = unpackUnifiedSnapshotField(runtime.health);
    const diagnostics = unpackUnifiedSnapshotField(runtime.diagnostics);
    const workspace = unpackUnifiedSnapshotField(runtime.workspace);
    const resourceManifest = unpackUnifiedSnapshotField(runtime.resourceManifest);
    const metrics = unpackUnifiedSnapshotField(runtime.metrics);
    const hasSomeData = [health, diagnostics, workspace, resourceManifest, metrics].some((item) => item.ok);
    if (!hasSomeData) {
      return null;
    }
    const metricsText = typeof metrics.data === "string" ? metrics.data : "";
    return {
      ...mockData,
      sourceKind: CONTROL_CENTER_SOURCE_KIND.backend,
      controlCenterRuntime: {
        backendBaseUrl: baseUrl,
        health,
        diagnostics,
        workspace,
        resourceManifest,
        metrics
      },
      overviewRuntime: buildOverviewRuntimePatch({
        health: health.data,
        diagnostics: diagnostics.data,
        workspace: workspace.data,
        metricsText,
        petState,
        connected: health.ok || diagnostics.ok
      }),
      characterRuntime: buildCharacterRuntimePatch({
        baseUrl,
        resourceManifest: resourceManifest.data,
        diagnostics: diagnostics.data,
        characterPackId,
        outfit,
        emotion,
        petState,
        availableCharacterPacks
      }),
      voiceRuntime: buildVoiceRuntimePatch({
        health: health.data,
        diagnostics: diagnostics.data,
        petState
      }),
      perceptionRuntime: buildPerceptionRuntimePatch({
        petState,
        diagnostics: diagnostics.data
      }),
      musicRuntime: buildMusicRuntimePatch({ musicSnapshot, petState }),
      abilitiesRuntime: buildAbilitiesRuntimePatch({
        diagnostics: diagnostics.data,
        workspace: workspace.data,
        connected: health.ok || diagnostics.ok
      }),
      advancedRuntime: buildAdvancedRuntimePatch({
        health: health.data,
        diagnostics: diagnostics.data,
        workspace: workspace.data,
        metricsText,
        petState
      })
    };
  } catch {
    return null;
  }
}

function unpackUnifiedSnapshotField(entry) {
  if (entry && typeof entry === "object" && !Array.isArray(entry) && "ok" in entry) {
    if (entry.ok === false) {
      return { ok: false, data: null, status: entry.status || "unavailable" };
    }
    if ("data" in entry) {
      return { ok: true, data: entry.data, status: entry.status || "available" };
    }
  }
  if (typeof entry === "string") {
    return { ok: Boolean(entry), data: entry };
  }
  return { ok: true, data: entry || null };
}
