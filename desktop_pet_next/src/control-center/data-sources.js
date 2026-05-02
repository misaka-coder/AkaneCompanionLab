import * as mockData from "./mock-data.js";

const DEFAULT_BACKEND_URL = "http://127.0.0.1:9999";

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
    async runAction(actionId, payload = {}) {
      return {
        ok: true,
        status: "mocked",
        actionId,
        payload
      };
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
    async runAction(actionId, payload = {}) {
      if (typeof fetchImpl !== "function") {
        return { ok: false, status: "missing-fetch", actionId };
      }
      try {
        const response = await fetchImpl(buildBackendUrl(baseUrl, `/control-center/actions/${encodeURIComponent(actionId)}`), {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify(payload)
        });
        if (!response.ok) {
          return { ok: false, status: `http-${response.status}`, actionId, payload };
        }
        return response.json();
      } catch (error) {
        return { ok: false, status: "request-failed", actionId, payload, error: formatDataSourceError(error) };
      }
    }
  };
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

function buildOverviewRuntimePatch({ health, diagnostics, workspace, metricsText, connected }) {
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
  const activeImage = emotionCards.find((item) => item.current)?.image || emotionCards[0]?.image || allOutfits[0]?.image || "";

  return {
    hero: activeImage,
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

function formatDataSourceError(error) {
  return error instanceof Error ? error.message : String(error || "unknown");
}
