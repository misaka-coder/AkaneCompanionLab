import { invoke } from "@tauri-apps/api/core";
import { emit, emitTo, listen } from "@tauri-apps/api/event";
import { fetch as tauriFetch } from "@tauri-apps/plugin-http";

import akaneNormal from "./assets/characters/猫娘/正常.png";
import akaneThinking from "./assets/characters/猫娘/思考中.png";
import akaneHappy from "./assets/characters/猫娘/开心.png";
import akaneListening from "./assets/characters/猫娘/侧耳听.png";
import akaneMusic from "./assets/characters/猫娘/听歌中.png";
import akaneShy from "./assets/characters/猫娘/脸红.png";
import akaneConfused from "./assets/characters/猫娘/困惑.png";
import akanePout from "./assets/characters/猫娘/气鼓鼓.png";
import akaneCute from "./assets/characters/猫娘/卖萌.png";
import skyCityBalcony from "./assets/control-center-lab/backgrounds/sky-city-balcony.png";
import akaneSakuraWide from "./assets/control-center-lab/heroes/akane-sakura-wide.png";
import akaneSkyWide from "./assets/control-center-lab/heroes/akane-sky-wide.png";
import akaneNightWindow from "./assets/control-center-lab/covers/akane-night-window.png";
import akaneSakuraClose from "./assets/control-center-lab/covers/akane-sakura-close.png";
import akaneSkyPaperPlane from "./assets/control-center-lab/covers/akane-sky-paper-plane.png";
import cloudLetter from "./assets/control-center-lab/covers/cloud-letter.png";
import moonBalcony from "./assets/control-center-lab/covers/moon-balcony.png";
import starryCloudCat from "./assets/control-center-lab/covers/starry-cloud-cat.png";
import {
  CONTROL_CENTER_ACTIONS,
  createControlCenterActionRouter,
  isControlCenterBridgedAction
} from "./control-center/action-router.js";
import { CONTROL_CENTER_CLIENT_HANDLED_ACTION_IDS } from "./control-center/action-surface-contract.js";
import {
  createControlCenterActionPayloadFromDataset,
  secondsFromIntervalLabel
} from "./control-center/action-helpers.js";
import { createControlCenterSnapshot } from "./control-center/data-adapter.js";
import {
  buildCharacterRuntimePatchFromSettingsSnapshot,
  buildMusicRuntimePatch,
  buildOverviewEmotionRuntimePatchFromSettingsSnapshot,
  CONTROL_CENTER_SOURCE_KIND,
  createControlCenterDataSource
} from "./control-center/data-sources.js";
import "./control-center-lab.css";

const DEFAULT_BACKEND_URL = "http://127.0.0.1:9999";
const SETTINGS_COMMAND_EVENT = "akane-next-settings-command";
const SETTINGS_SNAPSHOT_EVENT = "akane-next-settings-snapshot";
const RUNTIME_SNAPSHOT_HYDRATE_DELAY_MS = 900;
const SCREEN_VISION_INTERVAL_OPTIONS_SEC = Object.freeze([15, 25, 30, 60, 120, 300, 600]);
const SCREEN_VISION_FRAME_COUNT_MIN = 1;
const SCREEN_VISION_FRAME_COUNT_MAX = 5;
const MUSIC_PLAY_MODE_OPTIONS = Object.freeze(["列表循环", "单曲循环", "随机播放"]);
const WORKFLOW_FILE_IMPORT_MAX_BYTES = 4 * 1024 * 1024;
const ANYSEARCH_MCP_PRESET = Object.freeze({
  serverId: "anysearch",
  displayName: "AnySearch 网页搜索",
  command: "npx",
  argsText: "-y\nmcp-remote\nhttps://api.anysearch.com/mcp\n--header\nAuthorization: Bearer ${ANYSEARCH_API_KEY}",
  reason: "推荐搜索 MCP；API key 通过本机环境变量 ANYSEARCH_API_KEY 传入，不保存在 Akane 配置里。",
  toolLabels: ["网页搜索", "正文提取", "垂直搜索"],
  lastDiscoveryLabel: "预设待保存"
});
const MCP_STDIO_TEMPLATES = Object.freeze([
  {
    id: "custom-stdio",
    serverId: "custom_mcp",
    label: "添加自定义 stdio",
    displayName: "自定义 MCP",
    command: "",
    argsText: "",
    reason: "填写本地 MCP server 的启动命令，再保存并发现工具。"
  },
  {
    id: "node-stdio",
    serverId: "node_mcp",
    label: "Node / npx 模板",
    displayName: "Node MCP",
    command: "npx",
    argsText: "-y\nyour-mcp-server-package",
    reason: "适合 npm 包形式的 MCP server；把包名替换成真实服务。"
  },
  {
    id: "python-stdio",
    serverId: "python_mcp",
    label: "Python 模板",
    displayName: "Python MCP",
    command: "python",
    argsText: "-m\nyour_mcp_server",
    reason: "适合 Python 模块形式的 MCP server；把模块名替换成真实服务。"
  }
]);
const MCP_TRANSPORT_ROADMAP = Object.freeze([
  { label: "HTTP / Streamable", status: "后续接入" },
  { label: "SSE", status: "后续接入" }
]);
const isTauriRuntime = Boolean(window.__TAURI_INTERNALS__);
let latestRuntimeSnapshot = null;
let runtimeSnapshotHydrateTimer = 0;
let renderedPageId = "";
let providerTestAudioUrl = "";
let providerTestAudio = {
  providerId: "",
  url: "",
  mediaType: "",
  audioBytes: 0
};
const CO_LISTEN_CONTROL_NAMES = ["pause", "next", "prev", "recommend"];
let listeningTogetherState = {
  status: "idle",
  now: null,
  recent: [],
  controls: { pause: true, next: true, prev: true, recommend: true },
  message: "",
  lastFetchTrackKey: "",
  lastFetchAt: 0
};
let listeningTogetherRefreshInFlight = null;
const runtimePatchSignatures = {
  music: "",
  overview: "",
  character: ""
};
const pageScrollPositions = new Map();
const clientHandledActionIds = new Set(CONTROL_CENTER_CLIENT_HANDLED_ACTION_IDS);
const root = document.querySelector("#app");
let dataSource = createControlCenterDataSource(createControlCenterDataSourceOptions());
let snapshot = createControlCenterSnapshot(dataSource.readInitialState());
let actionRouter = createRuntimeActionRouter(dataSource);
// Settings Catalog (read-only) is fetched directly from the backend, not carried
// in the mock snapshot — so it never inherits the mock-baseline patching.
let settingsCatalog = null;
let settingsCatalogStatus = "";
let settingsSaveNote = "";
let settingsSaveOk = true;
let { labMeta, navItems, backgroundAsset } = snapshot.shell;
let {
  abilities: abilitiesPage,
  advanced: advancedPage,
  character: characterPage,
  model: modelPage,
  music: musicPage,
  overview: overviewPage,
  perception: perceptionPage,
  voice: voicePage
} = snapshot.pages;
const images = {
  normal: akaneNormal,
  thinking: akaneThinking,
  happy: akaneHappy,
  listening: akaneListening,
  music: akaneMusic,
  shy: akaneShy,
  confused: akaneConfused,
  pout: akanePout,
  cute: akaneCute,
  skyCityBalcony,
  akaneSakuraWide,
  akaneSkyWide,
  akaneNightWindow,
  akaneSakuraClose,
  akaneSkyPaperPlane,
  cloudLetter,
  moonBalcony,
  starryCloudCat
};

const state = {
  activePage: resolveInitialPage(),
  activeOutfit: characterPage.outfits?.find((item) => item.current)?.id || characterPage.outfits?.[0]?.id || "",
  activeEmotion: characterPage.emotions?.find((item) => item.current)?.id || characterPage.emotions?.[0]?.id || "",
  switches: Object.fromEntries(perceptionPage.featureCards.map((card) => [card.id, card.enabled])),
  screenVision: buildScreenVisionState(perceptionPage.featureCards),
  activeInterval: perceptionPage.featureCards.find((card) => card.id === "proactive")?.activeOption || "5 分钟",
  activeVoicePreset: voicePage.tts?.voice || "Akane Voice",
  activeMusicMode: musicPage.modes[0],
  modelDraft: buildModelServiceDraft(modelPage),
  modelModels: [],
  modelActionStatus: "",
  modelBusyAction: "",
  voice: buildVoiceState(voicePage),
  advancedCoreSwitches: buildAdvancedCoreSwitchState(advancedPage.coreSettings),
  expandedPerceptionCard: null,
  activeProviderConfigId: "",
  activeMcpConfigId: "",
  activeWorkflowConfigId: "",
  providerActionStatus: {},
  mcpActionStatus: {},
  mcpConfigDrafts: {},
  providerVoiceProfileDrafts: {},
  workflowActionStatus: {},
  showAllAbilityCalls: false,
  showAllDiagnosticLogs: false,
  qqSelfCheckResult: null
};

// Shell may fail mid-render (e.g. missing asset). Events must always bind so
// the page is not left in a half-rendered dead state.
try {
  renderShell();
} finally {
  bindEvents();
}
renderActivePage();
void hydrateControlCenterSnapshot();
void bindSettingsSnapshotListener();

function createControlCenterDataSourceOptions(overrides = {}) {
  const params = new URLSearchParams(window.location.search);
  const source = String(params.get("source") || "").trim().toLowerCase();
  const petState = overrides.petState && typeof overrides.petState === "object" ? overrides.petState : {};
  if (source === CONTROL_CENTER_SOURCE_KIND.mock) {
    return { kind: CONTROL_CENTER_SOURCE_KIND.mock };
  }
  return {
    kind: CONTROL_CENTER_SOURCE_KIND.backend,
    baseUrl: params.get("backend") || params.get("backend_url") || petState.backendUrl || localStorage.getItem("akane.controlCenter.backendUrl") || DEFAULT_BACKEND_URL,
    fetchImpl: isTauriRuntime ? tauriFetch : typeof window.fetch === "function" ? window.fetch.bind(window) : undefined,
    sessionId: params.get("session_id") || params.get("user_id") || petState.sessionId || localStorage.getItem("akane.controlCenter.sessionId") || "control-center-lab",
    profileUserId: params.get("real_user_id") || params.get("profile_user_id") || petState.profileUserId || localStorage.getItem("akane.controlCenter.profileUserId") || "master",
    characterPackId: params.get("character_pack_id") || params.get("characterPackId") || petState.characterPackId || localStorage.getItem("akane.controlCenter.characterPackId") || "",
    outfit: params.get("outfit") || petState.outfit || "",
    emotion: params.get("emotion") || petState.currentEmotion || "",
    musicSnapshot: overrides.musicSnapshot || latestRuntimeSnapshot?.music || null,
    petState,
    availableCharacterPacks: overrides.availableCharacterPacks || [],
    tauriBridge: isTauriRuntime
      ? {
          invoke,
          emit: emitMainEvent
        }
      : undefined
  };
}

async function hydrateControlCenterSnapshot() {
  try {
    const runtimeOptions = await createRuntimeDataSourceOptions();
    if (runtimeOptions) {
      dataSource = createControlCenterDataSource(runtimeOptions);
      actionRouter = createRuntimeActionRouter(dataSource);
    }
    if (dataSource?.readSnapshot) {
      const raw = await dataSource.readSnapshot();
      if (raw) {
        applyControlCenterSnapshot(createControlCenterSnapshot(raw), { renderShell: false });
        await hydrateModelService();
      }
    }
    // Independent of the snapshot read (it has its own backend endpoint) — must
    // run even if the snapshot is unavailable, or the settings page stays on
    // its "loading" placeholder forever.
    await hydrateSettingsCatalog();
  } catch (error) {
    console.info("[control-center] keep mock snapshot:", formatError(error));
  }
}

async function hydrateModelService() {
  if (typeof dataSource?.readModelService !== "function") return;
  try {
    const payload = await dataSource.readModelService();
    if (!payload || typeof payload !== "object") return;
    modelPage = {
      ...modelPage,
      ...payload,
      providers: Array.isArray(payload.providers) ? payload.providers : modelPage.providers
    };
    state.modelDraft = buildModelServiceDraft(modelPage);
    state.modelActionStatus = "";
    if (state.activePage === "model") {
      renderActivePage();
    }
  } catch (error) {
    state.modelActionStatus = `读取配置失败：${formatError(error)}`;
  }
}

async function hydrateSettingsCatalog() {
  if (typeof dataSource?.readSettingsCatalog !== "function") {
    settingsCatalogStatus = "未能读取设置目录（请确认已连接后端）";
    if (state.activePage === "settings") renderActivePage();
    return;
  }
  try {
    const payload = await dataSource.readSettingsCatalog();
    if (payload && typeof payload === "object" && Array.isArray(payload.categories)) {
      settingsCatalog = payload;
      settingsCatalogStatus = "";
    } else {
      settingsCatalog = null;
      settingsCatalogStatus = "未能读取设置目录（请确认已连接后端）";
    }
  } catch (error) {
    settingsCatalog = null;
    settingsCatalogStatus = `读取设置目录失败：${formatError(error)}`;
  }
  if (state.activePage === "settings") {
    renderActivePage();
  }
}

async function saveSetting(key, rawValue) {
  if (typeof dataSource?.updateSetting !== "function") {
    settingsSaveOk = false;
    settingsSaveNote = "当前来源不支持修改（需连接后端）";
    if (state.activePage === "settings") renderActivePage();
    return;
  }
  try {
    const result = await dataSource.updateSetting(key, rawValue);
    if (result && result.ok) {
      for (const group of settingsCatalog?.categories || []) {
        for (const entry of group.settings || []) {
          if (entry.key === key) entry.current = result.value;
        }
      }
      settingsSaveOk = true;
      settingsSaveNote = `已更新 ${key}`;
    } else {
      settingsSaveOk = false;
      settingsSaveNote = `修改失败（${key}）：${(result && result.status) || "未知"}`;
    }
  } catch (error) {
    settingsSaveOk = false;
    settingsSaveNote = `修改失败（${key}）：${formatError(error)}`;
  }
  if (state.activePage === "settings") renderActivePage();
}

async function createRuntimeDataSourceOptions() {
  if (!isTauriRuntime) return null;
  try {
    const petState = await invoke("load_pet_state");
    let availableCharacterPacks = [];
    try {
      availableCharacterPacks = await invoke("list_character_packs");
    } catch {
      availableCharacterPacks = [];
    }
    return createControlCenterDataSourceOptions({
      petState,
      availableCharacterPacks,
      musicSnapshot: latestRuntimeSnapshot?.music || null
    });
  } catch {
    return null;
  }
}

function applyControlCenterSnapshot(nextSnapshot, options = {}) {
  if (!nextSnapshot || typeof nextSnapshot !== "object") return;
  const renderShellAfterApply = options.renderShell !== false;
  const renderPageAfterApply = options.renderPage !== false;
  rememberRenderedPageScroll();
  snapshot = nextSnapshot;
  ({ labMeta, navItems, backgroundAsset } = snapshot.shell);
  ({
    abilities: abilitiesPage,
    advanced: advancedPage,
    character: characterPage,
    model: modelPage,
    music: musicPage,
    overview: overviewPage,
    perception: perceptionPage,
    voice: voicePage
  } = snapshot.pages);
  if (!navItems.some((item) => item.id === state.activePage)) {
    state.activePage = labMeta.defaultPage;
  }
  syncInteractiveStateWithSnapshot();
  if (renderShellAfterApply) {
    renderShell();
  }
  if (renderPageAfterApply) {
    renderActivePage();
  }
  refreshListeningTogetherCard().catch(() => {});
}

function friendlyMusicSourceLabel(sourceValue) {
  switch (String(sourceValue || "").toLowerCase()) {
    case "qq_music": return "QQ 音乐";
    case "netease_music": return "网易云";
    case "spotify": return "Spotify";
    case "youtube_music": return "YouTube Music";
    case "apple_music": return "Apple Music";
    case "local_akane": return "本地音乐";
    case "system_media_unknown": return "系统播放器";
    case "external_unknown": return "其他来源";
    default: return "";
  }
}

function moodPhraseFromEmotion(name) {
  const n = String(name || "").toLowerCase().trim();
  const MAP = {
    "开心": "她好像挺开心的～",
    "高兴": "她好像挺高兴的～",
    "兴奋": "她好像很兴奋",
    "开朗": "她心情看起来不错",
    "温柔": "她好像很温柔",
    "平静": "她安静地在听",
    "默然": "她安静地在听",
    "思考": "她好像在想什么",
    "沉思": "她好像在想什么",
    "好奇": "她好像很好奇",
    "害羞": "她有点害羞",
    "难过": "她好像有点难过",
    "委屈": "她好像有点委屈",
    "无聊": "她好像有点无聊",
  };
  return MAP[n] || "";
}

async function refreshListeningTogetherCard({ force = false } = {}) {
  if (listeningTogetherRefreshInFlight) return;
  const fetchOptions = createControlCenterDataSourceOptions();
  if (!fetchOptions.fetchImpl || !fetchOptions.baseUrl) return;

  const systemMedia = latestRuntimeSnapshot?.music?.systemMedia;
  const trackKey = String(systemMedia?.trackKey || "").trim();
  if (!force && trackKey && trackKey === listeningTogetherState.lastFetchTrackKey) {
    // Same track was already queried recently; no need to refetch.
    return;
  }

  const body = {
    title: String(systemMedia?.title || "").trim(),
    artist: String(systemMedia?.artist || "").trim(),
    album: String(systemMedia?.album || "").trim(),
    source_kind: systemMedia ? "system_media" : "",
    source_app: String(systemMedia?.sourceApp || "").trim(),
    system_media: Boolean(systemMedia),
    recent_limit: 5
  };

  const baseUrl = String(fetchOptions.baseUrl).replace(/\/+$/, "");
  const sessionId = encodeURIComponent(String(fetchOptions.sessionId || "control-center-lab"));
  const profileUserId = encodeURIComponent(String(fetchOptions.profileUserId || "master"));
  const url = `${baseUrl}/capabilities/music/co_listen_summary?user_id=${sessionId}&real_user_id=${profileUserId}`;

  listeningTogetherRefreshInFlight = (async () => {
    try {
      const response = await fetchOptions.fetchImpl(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });
      let payload = null;
      if (response && typeof response.json === "function") {
        try { payload = await response.json(); } catch { payload = null; }
      }
      if (response?.ok && payload?.ok) {
        const enabledList = Array.isArray(payload.enabled_music_controls) ? payload.enabled_music_controls : null;
        const controls = enabledList
          ? {
              pause: enabledList.includes("pause"),
              next: enabledList.includes("next"),
              prev: enabledList.includes("prev"),
              recommend: enabledList.includes("recommend")
            }
          : listeningTogetherState.controls;
        listeningTogetherState = {
          status: "ready",
          now: payload.now || null,
          recent: Array.isArray(payload.recent) ? payload.recent : [],
          controls,
          message: "",
          lastFetchTrackKey: trackKey,
          lastFetchAt: Date.now()
        };
      } else {
        listeningTogetherState = {
          status: "error",
          now: null,
          recent: [],
          message: String(payload?.status || response?.status || "unknown_error"),
          lastFetchTrackKey: trackKey,
          lastFetchAt: Date.now()
        };
      }
    } catch (error) {
      listeningTogetherState = {
        status: "error",
        now: null,
        recent: [],
        message: typeof formatError === "function" ? formatError(error) : String(error),
        lastFetchTrackKey: trackKey,
        lastFetchAt: Date.now()
      };
    } finally {
      listeningTogetherRefreshInFlight = null;
      if (state.activePage === "overview") {
        renderActivePage();
      }
    }
  })();
}

function renderListeningTogetherCard() {
  const data = listeningTogetherState;
  const now = data.now;
  const hasNow = Boolean(now);
  const hasRecent = Array.isArray(data.recent) && data.recent.length > 0;

  if (!hasNow && !hasRecent) {
    return "";
  }

  let nowBlock = "";
  if (hasNow) {
    const title = String(now.title || "").trim() || "她还没听清是哪首";
    const artist = String(now.artist || "").trim();
    const sourceLabel = friendlyMusicSourceLabel(now.source);
    const metaParts = [];
    if (artist) metaParts.push(artist);
    if (sourceLabel) metaParts.push(sourceLabel);
    const meta = metaParts.join(" · ");
    const count = Number(now.co_listen_count || 0);
    const lastLabel = String(now.last_listened_label || "").trim();
    let storyText = "";
    if (now.is_first_listen) {
      storyText = "这是你们第一次一起听这首。";
    } else if (count >= 2) {
      storyText = `已经一起听过 ${count} 次${lastLabel ? `，上次是${lastLabel}` : ""}。`;
    } else if (count === 1) {
      storyText = lastLabel ? `这首之前一起听过一次（${lastLabel}）。` : "这首之前一起听过一次。";
    }
    const emotionName = String(latestRuntimeSnapshot?.currentExpression?.name || "").trim();
    const moodLine = moodPhraseFromEmotion(emotionName);
    nowBlock = `
      <div class="listening-now">
        <small>现在听的</small>
        <strong>${escapeHtml(title)}</strong>
        ${meta ? `<span>${escapeHtml(meta)}</span>` : ""}
        ${storyText ? `<p>${escapeHtml(storyText)}</p>` : ""}
        ${moodLine ? `<p class="mood-line">${escapeHtml(moodLine)}</p>` : ""}
      </div>
    `;
  }

  let recentBlock = "";
  if (hasRecent) {
    const rows = data.recent.slice(0, 5).map((item) => {
      const title = String(item.title || "").trim() || "某首歌";
      const artist = String(item.artist || "").trim();
      const lastLabel = String(item.last_listened_label || "").trim();
      const count = Number(item.co_listen_count || 0);
      const parts = [];
      if (artist) parts.push(artist);
      if (lastLabel) parts.push(`${lastLabel}听过`);
      if (count >= 2) parts.push(`共 ${count} 次`);
      const meta = parts.join(" · ");
      return `
        <li>
          <strong>${escapeHtml(title)}</strong>
          ${meta ? `<small>${escapeHtml(meta)}</small>` : ""}
        </li>
      `;
    }).join("");
    recentBlock = `
      <div class="listening-recent">
        <small>最近也一起听过</small>
        <ul>${rows}</ul>
      </div>
    `;
  }

  const controls = data.controls || { pause: true, next: true, prev: true, recommend: true };
  const controlDefs = [
    { id: "pause", label: "让她暂停" },
    { id: "next", label: "让她切歌" },
    { id: "prev", label: "让她回上一首" },
    { id: "recommend", label: "让她推荐新歌" }
  ];
  const permissionButtons = controlDefs.map(({ id, label }) => {
    const enabled = controls[id] !== false;
    return `<button type="button"
              data-co-listen-control="${escapeAttr(id)}"
              data-co-listen-enabled="${enabled ? "true" : "false"}"
              aria-pressed="${enabled ? "true" : "false"}"
            >${escapeHtml(label)} · ${enabled ? "开" : "关"}</button>`;
  }).join("");
  const permissionsBlock = `
    <div class="listening-permissions">
      <small>让她也能</small>
      <div class="permission-row">${permissionButtons}</div>
    </div>
  `;

  return `
    <article class="glass-card listening-together-card" data-card-id="listening-together">
      <div class="card-heading">
        <h2>${icon("music")} 我们的共听</h2>
      </div>
      ${nowBlock}
      ${recentBlock}
      ${permissionsBlock}
    </article>
  `;
}

async function toggleListeningTogetherControl(controlName, nextEnabled) {
  const prevControls = { ...(listeningTogetherState.controls || {}) };
  listeningTogetherState = {
    ...listeningTogetherState,
    controls: { ...prevControls, [controlName]: nextEnabled }
  };
  renderActivePage();

  const fetchOptions = createControlCenterDataSourceOptions();
  if (!fetchOptions.fetchImpl || !fetchOptions.baseUrl) return;
  const baseUrl = String(fetchOptions.baseUrl).replace(/\/+$/, "");
  const sessionId = encodeURIComponent(String(fetchOptions.sessionId || "control-center-lab"));
  const profileUserId = encodeURIComponent(String(fetchOptions.profileUserId || "master"));
  const url = `${baseUrl}/capabilities/music/control_permissions?user_id=${sessionId}&real_user_id=${profileUserId}`;

  try {
    const response = await fetchOptions.fetchImpl(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ controls: { [controlName]: nextEnabled } })
    });
    let payload = null;
    if (response && typeof response.json === "function") {
      try { payload = await response.json(); } catch { payload = null; }
    }
    if (response?.ok && payload?.ok && payload.controls && typeof payload.controls === "object") {
      listeningTogetherState = {
        ...listeningTogetherState,
        controls: {
          pause: payload.controls.pause !== false,
          next: payload.controls.next !== false,
          prev: payload.controls.prev !== false,
          recommend: payload.controls.recommend !== false
        }
      };
    } else {
      listeningTogetherState = {
        ...listeningTogetherState,
        controls: prevControls,
        message: String(payload?.status || response?.status || "toggle_failed")
      };
    }
  } catch (error) {
    listeningTogetherState = {
      ...listeningTogetherState,
      controls: prevControls,
      message: typeof formatError === "function" ? formatError(error) : String(error)
    };
  } finally {
    if (state.activePage === "overview") {
      renderActivePage();
    }
  }
}

function syncInteractiveStateWithSnapshot() {
  const outfits = Array.isArray(characterPage.outfits) ? characterPage.outfits : [];
  const emotions = Array.isArray(characterPage.emotions) ? characterPage.emotions : [];
  if (!outfits.some((item) => item.id === state.activeOutfit)) {
    state.activeOutfit = outfits.find((item) => item.current)?.id || outfits[0]?.id || "";
  }
  if (!emotions.some((item) => item.id === state.activeEmotion)) {
    state.activeEmotion = emotions.find((item) => item.current)?.id || emotions[0]?.id || "";
  }
  syncPerceptionInteractiveState();
  syncVoiceInteractiveState();
  syncModelInteractiveState();
  syncAdvancedInteractiveState();
  syncProviderInteractiveState();
}

function syncPerceptionInteractiveState() {
  const featureCards = Array.isArray(perceptionPage.featureCards) ? perceptionPage.featureCards : [];
  for (const card of featureCards) {
    if (!card?.id) continue;
    state.switches[card.id] = Boolean(card.enabled);
  }
  const proactive = featureCards.find((card) => card?.id === "proactive");
  if (proactive?.activeOption) {
    state.activeInterval = String(proactive.activeOption);
  }
  state.screenVision = buildScreenVisionState(featureCards);
}

function syncAdvancedInteractiveState() {
  state.advancedCoreSwitches = {
    ...state.advancedCoreSwitches,
    ...buildAdvancedCoreSwitchState(advancedPage.coreSettings)
  };
}

function syncVoiceInteractiveState() {
  state.voice = buildVoiceState(voicePage);
}

function syncModelInteractiveState() {
  if (!state.modelBusyAction) {
    state.modelDraft = buildModelServiceDraft(modelPage);
  }
}

function syncProviderInteractiveState() {
  const providers = Array.isArray(abilitiesPage.providers) ? abilitiesPage.providers : [];
  if (state.activeProviderConfigId && !providers.some((item) => item.id === state.activeProviderConfigId)) {
    state.activeProviderConfigId = "";
  }
  const workflows = Array.isArray(abilitiesPage.workflows) ? abilitiesPage.workflows : [];
  if (state.activeWorkflowConfigId && !workflows.some((item) => item.workflowId === state.activeWorkflowConfigId || item.id === state.activeWorkflowConfigId)) {
    state.activeWorkflowConfigId = "";
  }
  const mcpServers = buildVisibleMcpServers();
  if (state.activeMcpConfigId && !mcpServers.some((item) => item.serverId === state.activeMcpConfigId || item.id === state.activeMcpConfigId)) {
    state.activeMcpConfigId = "";
  }
}

function renderShell() {
  rememberRenderedPageScroll();
  root.innerHTML = `
    <div class="lab-sky" style="--lab-background-image: url(${imageFor(backgroundAsset)})" aria-hidden="true">
      <span class="sparkle sparkle-one"></span>
      <span class="sparkle sparkle-two"></span>
      <span class="sparkle sparkle-three"></span>
      <span class="skyline skyline-left"></span>
      <span class="skyline skyline-right"></span>
      <span class="flower-haze flower-left"></span>
      <span class="flower-haze flower-right"></span>
    </div>
    <main class="cc-shell" aria-label="Akane 控制中心 UI 原型">
      <aside class="cc-sidebar">
        <div class="brand-block">
          <div>
            <strong>Akane</strong>
            <span>控制中心</span>
          </div>
          <i aria-hidden="true">✦</i>
        </div>
        <nav class="side-nav" aria-label="控制中心导航">
          ${navItems.map(renderNavButton).join("")}
        </nav>
        <div class="sidebar-spacer"></div>
        <section class="companion-card">
          <span class="mini-sparkle" aria-hidden="true">✦</span>
          <p>与 Akane 一起<br />让每一天都更轻松</p>
          <span class="cat-mark" aria-hidden="true"></span>
        </section>
        <section class="online-card">
          <img src="${images.happy}" alt="" />
          <div>
            <strong><span></span>${labMeta.status}</strong>
            <p>${labMeta.statusDetail}</p>
          </div>
        </section>
      </aside>

      <section class="cc-main">
        <header class="window-chrome">
          <button class="chrome-icon" type="button" data-action-id="${CONTROL_CENTER_ACTIONS.windowNotify}" aria-label="通知">${icon("bell")}</button>
          <div class="chrome-actions" aria-label="窗口操作">
            <button class="chrome-icon" type="button" data-action-id="${CONTROL_CENTER_ACTIONS.windowMinimize}" aria-label="最小化">${icon("minus")}</button>
            <button class="chrome-icon" type="button" data-action-id="${CONTROL_CENTER_ACTIONS.windowMaximize}" aria-label="最大化">${icon("square")}</button>
            <button class="chrome-icon" type="button" data-action-id="${CONTROL_CENTER_ACTIONS.windowClose}" aria-label="关闭">${icon("x")}</button>
          </div>
        </header>
        <div id="page-content" class="page-content"></div>
        <footer class="cc-footer">
          <span>${icon("lock")} ${labMeta.footer} ✦</span>
          <span>${labMeta.version}</span>
        </footer>
      </section>
    </main>
  `;
  renderedPageId = "";
  applyActionAvailability(root);
}

function bindEvents() {
  root.addEventListener("click", (event) => {
    const modelActionButton = event.target.closest("[data-model-service-action]");
    if (modelActionButton) {
      void runModelServiceAction(modelActionButton.dataset.modelServiceAction);
      return;
    }

    const coListenControlBtn = event.target.closest("[data-co-listen-control]");
    if (coListenControlBtn) {
      const controlName = coListenControlBtn.dataset.coListenControl;
      const currentEnabled = coListenControlBtn.dataset.coListenEnabled !== "false";
      void toggleListeningTogetherControl(controlName, !currentEnabled);
      return;
    }

    const providerSaveButton = event.target.closest("[data-provider-config-save]");
    if (providerSaveButton) {
      if (providerSaveButton.dataset.actionUnavailable === "true") return;
      void runProviderConfigAction(
        providerSaveButton,
        CONTROL_CENTER_ACTIONS.abilitiesProviderConfigSave
      );
      return;
    }

    const providerHealthButton = event.target.closest("[data-provider-health-check]");
    if (providerHealthButton) {
      if (providerHealthButton.dataset.actionUnavailable === "true") return;
      void runProviderConfigAction(
        providerHealthButton,
        CONTROL_CENTER_ACTIONS.abilitiesProviderHealthCheck
      );
      return;
    }

    const providerTtsTestButton = event.target.closest("[data-provider-tts-test]");
    if (providerTtsTestButton) {
      if (providerTtsTestButton.dataset.actionUnavailable === "true") return;
      void runProviderConfigAction(
        providerTtsTestButton,
        CONTROL_CENTER_ACTIONS.abilitiesProviderTtsTest
      );
      return;
    }

    const providerVoiceProfileInspectButton = event.target.closest("[data-provider-voice-profile-inspect]");
    if (providerVoiceProfileInspectButton) {
      if (providerVoiceProfileInspectButton.dataset.actionUnavailable === "true") return;
      void runProviderConfigAction(
        providerVoiceProfileInspectButton,
        CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileInspectFolder
      );
      return;
    }

    const providerVoiceProfileSaveButton = event.target.closest("[data-provider-voice-profile-save]");
    if (providerVoiceProfileSaveButton) {
      if (providerVoiceProfileSaveButton.dataset.actionUnavailable === "true") return;
      void runProviderConfigAction(
        providerVoiceProfileSaveButton,
        CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileSave
      );
      return;
    }

    const providerVoiceProfileAssignButton = event.target.closest("[data-provider-voice-profile-assign]");
    if (providerVoiceProfileAssignButton) {
      if (providerVoiceProfileAssignButton.dataset.actionUnavailable === "true") return;
      void runProviderConfigAction(
        providerVoiceProfileAssignButton,
        CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileAssignToCurrentCharacter
      );
      return;
    }

    const providerVoiceProfileClearButton = event.target.closest("[data-provider-voice-profile-clear]");
    if (providerVoiceProfileClearButton) {
      if (providerVoiceProfileClearButton.dataset.actionUnavailable === "true") return;
      void runProviderConfigAction(
        providerVoiceProfileClearButton,
        CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileClearCurrentCharacter
      );
      return;
    }

    const mcpPresetButton = event.target.closest("[data-mcp-preset]");
    if (mcpPresetButton) {
      applyMcpPreset(mcpPresetButton.dataset.mcpPreset);
      return;
    }

    const mcpTemplateButton = event.target.closest("[data-mcp-template]");
    if (mcpTemplateButton) {
      applyMcpTemplate(mcpTemplateButton.dataset.mcpTemplate);
      return;
    }

    const mcpSaveButton = event.target.closest("[data-mcp-config-save]");
    if (mcpSaveButton) {
      if (mcpSaveButton.dataset.actionUnavailable === "true") return;
      void runMcpConfigAction(
        mcpSaveButton,
        CONTROL_CENTER_ACTIONS.abilitiesMcpConfigSave
      );
      return;
    }

    const mcpDiscoverButton = event.target.closest("[data-mcp-discover]");
    if (mcpDiscoverButton) {
      if (mcpDiscoverButton.dataset.actionUnavailable === "true") return;
      void runMcpConfigAction(
        mcpDiscoverButton,
        CONTROL_CENTER_ACTIONS.abilitiesMcpDiscover
      );
      return;
    }

    const workflowSaveButton = event.target.closest("[data-workflow-config-save]");
    if (workflowSaveButton) {
      if (workflowSaveButton.dataset.actionUnavailable === "true") return;
      void runWorkflowConfigAction(
        workflowSaveButton,
        CONTROL_CENTER_ACTIONS.abilitiesWorkflowConfigSave
      );
      return;
    }

    const workflowValidateButton = event.target.closest("[data-workflow-validate]");
    if (workflowValidateButton) {
      if (workflowValidateButton.dataset.actionUnavailable === "true") return;
      void runWorkflowConfigAction(
        workflowValidateButton,
        CONTROL_CENTER_ACTIONS.abilitiesWorkflowValidate
      );
      return;
    }

    const workflowImportButton = event.target.closest("[data-workflow-file-import]");
    if (workflowImportButton) {
      if (workflowImportButton.disabled) return;
      const row = workflowImportButton.closest("[data-workflow-row]");
      row?.querySelector?.("[data-workflow-file-input]")?.click();
      return;
    }

    const actionButton = event.target.closest("[data-action-id]");
    if (actionButton) {
      if (actionButton.dataset.actionUnavailable === "true") {
        return;
      }
      const payload = createControlCenterActionPayloadFromDataset(actionButton.dataset, state.activePage);
      applyLocalActionOptimisticUpdate(actionButton.dataset.actionId, payload);
      void actionRouter.run(
        actionButton.dataset.actionId,
        payload,
        { source: "control-center-lab" }
      );
      return;
    }

    const navButton = event.target.closest("[data-page]");
    if (navButton) {
      state.activePage = navButton.dataset.page;
      const url = new URL(window.location.href);
      url.searchParams.set("page", state.activePage);
      window.history.replaceState({}, "", url);
      renderActivePage();
      // Lazy-load / retry the settings catalog when its page is opened, so a
      // backend that wasn't ready at startup still fills in on first view.
      if (state.activePage === "settings" && !settingsCatalog) {
        void hydrateSettingsCatalog();
      }
      return;
    }

    const switchButton = event.target.closest("[data-switch]");
    if (switchButton) {
      const key = switchButton.dataset.switch;
      state.switches[key] = !state.switches[key];
      switchButton.classList.toggle("is-on", state.switches[key]);
      switchButton.setAttribute("aria-checked", String(state.switches[key]));
      const actionId = switchButton.dataset.switchActionId || actionIdForPerceptionSwitch(key);
      if (actionId) {
        void actionRouter.run(actionId, { value: state.switches[key], featureId: key }, { source: "control-center-lab" });
      }
      return;
    }

    const intervalButton = event.target.closest("[data-interval]");
    if (intervalButton) {
      state.activeInterval = intervalButton.dataset.interval;
      renderActivePage();
      const seconds = secondsFromIntervalLabel(state.activeInterval);
      if (seconds > 0) {
        void actionRouter.run(CONTROL_CENTER_ACTIONS.perceptionProactiveWakeSetIntervalSec, {
          value: seconds,
          label: state.activeInterval
        }, { source: "control-center-lab" });
      }
      return;
    }

    const screenVisionIntervalButton = event.target.closest("[data-screen-vision-interval-step]");
    if (screenVisionIntervalButton) {
      const step = Number(screenVisionIntervalButton.dataset.screenVisionIntervalStep || 1);
      const value = nextScreenVisionIntervalSec(state.screenVision.intervalSec, step);
      state.screenVision.intervalSec = value;
      renderActivePage();
      void actionRouter.run(
        CONTROL_CENTER_ACTIONS.perceptionScreenVisionSetIntervalSec,
        { value, label: `${value} 秒` },
        { source: "control-center-lab" }
      );
      return;
    }

    const screenVisionFrameButton = event.target.closest("[data-screen-vision-frame-step]");
    if (screenVisionFrameButton) {
      const step = Number(screenVisionFrameButton.dataset.screenVisionFrameStep || 0);
      const value = clampScreenVisionFrameCount(state.screenVision.frameCount + step);
      state.screenVision.frameCount = value;
      renderActivePage();
      void actionRouter.run(
        CONTROL_CENTER_ACTIONS.perceptionScreenVisionSetFrameCount,
        { value, frames: value },
        { source: "control-center-lab" }
      );
      return;
    }

    const voiceToggle = event.target.closest("[data-voice-toggle]");
    if (voiceToggle) {
      const key = voiceToggle.dataset.voiceToggle;
      const actionId = voiceToggle.dataset.voiceActionId || actionIdForVoiceToggle(key);
      if (actionId) {
        const value = !Boolean(state.voice[key]);
        state.voice[key] = value;
        renderActivePage();
        void actionRouter.run(actionId, { value, settingId: key }, { source: "control-center-lab" });
      }
      return;
    }

    const voiceVolumeButton = event.target.closest("[data-voice-volume-step]");
    if (voiceVolumeButton) {
      const step = Number(voiceVolumeButton.dataset.voiceVolumeStep || 0);
      const percent = clampVoiceVolumePercent(state.voice.volumePercent + step);
      state.voice.volumePercent = percent;
      renderActivePage();
      void actionRouter.run(
        CONTROL_CENTER_ACTIONS.voiceSetVolume,
        { value: percent / 100, percent },
        { source: "control-center-lab" }
      );
      return;
    }

    const advancedCoreToggle = event.target.closest("[data-advanced-core-toggle]");
    if (advancedCoreToggle) {
      const settingId = advancedCoreToggle.dataset.advancedCoreToggle;
      const setting = advancedPage.coreSettings.find((item) => item.id === settingId);
      if (setting?.actionId) {
        const value = !Boolean(state.advancedCoreSwitches[settingId]);
        state.advancedCoreSwitches[settingId] = value;
        renderActivePage();
        void actionRouter.run(setting.actionId, { value, settingId }, { source: "control-center-lab" });
      }
      return;
    }

    const outfitButton = event.target.closest("[data-character-outfit]");
    if (outfitButton) {
      state.activeOutfit = outfitButton.dataset.characterOutfit;
      renderActivePage();
      void actionRouter.run(
        CONTROL_CENTER_ACTIONS.characterSetOutfit,
        { value: state.activeOutfit, outfitId: state.activeOutfit },
        { source: "control-center-lab" }
      );
      return;
    }

    const emotionButton = event.target.closest("[data-character-emotion]");
    if (emotionButton) {
      state.activeEmotion = emotionButton.dataset.characterEmotion;
      renderActivePage();
      void actionRouter.run(
        CONTROL_CENTER_ACTIONS.characterPreviewEmotion,
        { value: state.activeEmotion, emotionId: state.activeEmotion },
        { source: "control-center-lab" }
      );
      return;
    }

    const voicePreset = event.target.closest("[data-voice-preset]");
    if (voicePreset) {
      state.activeVoicePreset = voicePreset.dataset.voicePreset;
      renderActivePage();
      return;
    }

    const musicMode = event.target.closest("[data-music-mode]");
    if (musicMode) {
      state.activeMusicMode = musicMode.dataset.musicMode;
      renderActivePage();
      void actionRouter.run(
        CONTROL_CENTER_ACTIONS.musicSetMood,
        { value: state.activeMusicMode, mood: state.activeMusicMode },
        { source: "control-center-lab" }
      );
    }
  });

  root.addEventListener("change", (event) => {
    const settingInput = event.target.closest("[data-setting-key]");
    if (settingInput) {
      const key = settingInput.dataset.settingKey;
      const value = settingInput.type === "checkbox" ? settingInput.checked : settingInput.value;
      void saveSetting(key, value);
      return;
    }

    const modelProviderSelect = event.target.closest("[data-model-provider]");
    if (modelProviderSelect) {
      const draft = readModelServiceForm();
      const preset = modelProviderById(modelProviderSelect.value);
      state.modelDraft = {
        ...draft,
        providerId: modelProviderSelect.value,
        protocol: preset?.protocol || draft.protocol || "openai",
        baseUrl: preset?.baseUrl || (modelProviderSelect.value === "openai_compatible" ? draft.baseUrl : "")
      };
      state.modelModels = [];
      state.modelActionStatus = preset?.description || "";
      renderActivePage();
      return;
    }

    const modelVisionToggle = event.target.closest("[data-model-use-vision]");
    if (modelVisionToggle) {
      state.modelDraft = {
        ...readModelServiceForm(),
        useForVision: Boolean(modelVisionToggle.checked)
      };
      renderActivePage();
      return;
    }

    const workflowFileInput = event.target.closest("[data-workflow-file-input]");
    if (workflowFileInput) {
      void importWorkflowFile(workflowFileInput);
    }
  });

  window.addEventListener("beforeunload", () => {
    cleanupProviderTestAudioUrl();
  });
}

async function runProviderConfigAction(button, actionId) {
  const payload = readProviderConfigPayload(button);
  if (!payload.providerId) return;
  if (actionId === CONTROL_CENTER_ACTIONS.abilitiesProviderTtsTest) {
    cleanupProviderTestAudioUrl();
  }
  state.providerActionStatus[payload.providerId] = "处理中";
  renderActivePage();
  const result = await actionRouter.run(actionId, payload, { source: "control-center-lab" });
  if (actionId === CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileInspectFolder && result?.ok) {
    applyProviderVoiceProfileSuggestion(payload.providerId, result, payload);
  }
  if (actionId === CONTROL_CENTER_ACTIONS.abilitiesProviderTtsTest && result?.ok) {
    state.providerActionStatus[payload.providerId] = await playProviderTestAudio(payload.providerId, result);
  } else {
    state.providerActionStatus[payload.providerId] = providerActionStatusLabel(result);
  }
  renderActivePage();
}

async function runModelServiceAction(actionId) {
  if (!actionId || state.modelBusyAction || typeof dataSource?.runModelServiceAction !== "function") {
    return;
  }
  const draft = readModelServiceForm();
  state.modelDraft = draft;
  state.modelBusyAction = actionId;
  state.modelActionStatus = modelBusyLabel(actionId);
  renderActivePage();
  try {
    const result = await dataSource.runModelServiceAction(actionId, draft);
    if (actionId === "models" && result?.ok) {
      state.modelModels = Array.isArray(result.models) ? result.models : [];
      if (!state.modelDraft.chatModel && state.modelModels.length) {
        state.modelDraft.chatModel = state.modelModels[0];
      }
    }
    if (actionId === "save" && result?.ok) {
      modelPage = {
        ...modelPage,
        ...result,
        providers: Array.isArray(result.providers) ? result.providers : modelPage.providers
      };
      state.modelDraft = buildModelServiceDraft(modelPage);
    }
    state.modelActionStatus = modelActionStatusLabel(actionId, result);
  } catch (error) {
    state.modelActionStatus = `操作失败：${formatError(error)}`;
  } finally {
    state.modelBusyAction = "";
    renderActivePage();
  }
}

function modelBusyLabel(actionId) {
  if (actionId === "models") return "正在读取服务商的模型列表...";
  if (actionId === "test") return "正在发送最小测试请求...";
  return "正在保存并刷新模型服务...";
}

function modelActionStatusLabel(actionId, result) {
  if (!result?.ok) {
    const reason = String(result?.reason || result?.error || result?.status || "未知错误");
    return `失败：${reason}`;
  }
  if (actionId === "models") {
    return result.models?.length ? `发现 ${result.models.length} 个模型，可以直接选择。` : "服务可连接，但没有返回模型列表。";
  }
  if (actionId === "test") {
    return `连接成功，模型返回：${String(result.message || "OK")}`;
  }
  return "已保存并立即生效。";
}

async function runMcpConfigAction(button, actionId) {
  const payload = readMcpConfigPayload(button);
  if (!payload.serverId) return;
  state.activeMcpConfigId = payload.serverId;
  state.mcpActionStatus[payload.serverId] = actionId === CONTROL_CENTER_ACTIONS.abilitiesMcpDiscover ? "正在保存并发现工具" : "正在保存";
  renderActivePage();
  if (actionId === CONTROL_CENTER_ACTIONS.abilitiesMcpDiscover) {
    const saveResult = await actionRouter.run(
      CONTROL_CENTER_ACTIONS.abilitiesMcpConfigSave,
      payload,
      { source: "control-center-lab" }
    );
    if (!saveResult?.ok) {
      state.mcpActionStatus[payload.serverId] = `保存失败：${mcpActionStatusLabel(saveResult)}`;
      renderActivePage();
      return;
    }
    state.mcpActionStatus[payload.serverId] = "已保存，正在测试连接";
    renderActivePage();
  }
  const result = await actionRouter.run(actionId, payload, { source: "control-center-lab" });
  state.mcpActionStatus[payload.serverId] = mcpActionStatusLabel(result);
  renderActivePage();
}

async function runWorkflowConfigAction(button, actionId) {
  const payload = readWorkflowConfigPayload(button);
  if (!payload.workflowId) return;
  state.workflowActionStatus[payload.workflowId] = "处理中";
  renderActivePage();
  const result = await actionRouter.run(actionId, payload, { source: "control-center-lab" });
  state.workflowActionStatus[payload.workflowId] = workflowActionStatusLabel(result);
  renderActivePage();
}

async function importWorkflowFile(input) {
  const row = input?.closest?.("[data-workflow-row]");
  const workflowId = String(input?.dataset?.workflowId || row?.dataset?.workflowId || "").trim();
  const file = input?.files?.[0] || null;
  if (input) input.value = "";
  if (!workflowId || !file) return;
  if (!file.name.toLowerCase().endsWith(".json")) {
    state.workflowActionStatus[workflowId] = "请选择 JSON 文件";
    renderActivePage();
    return;
  }
  if (file.size <= 0 || file.size > WORKFLOW_FILE_IMPORT_MAX_BYTES) {
    state.workflowActionStatus[workflowId] = "文件大小不合适";
    renderActivePage();
    return;
  }
  const workflowPathInput = row?.querySelector?.("[data-workflow-path-input]");
  const workflowPath = String(workflowPathInput?.value || "").trim() || "workflows/comfyui/portrait_cutout.json";
  state.workflowActionStatus[workflowId] = "正在导入";
  renderActivePage();
  try {
    const workflowJson = await file.text();
    const result = await actionRouter.run(
      CONTROL_CENTER_ACTIONS.abilitiesWorkflowFileImport,
      { workflowId, workflowPath, workflowJson },
      { source: "control-center-lab" }
    );
    if (result?.ok && result.workflowPath) {
      updateWorkflowPath(workflowId, result.workflowPath);
    }
    state.workflowActionStatus[workflowId] = workflowActionStatusLabel(result);
  } catch (error) {
    state.workflowActionStatus[workflowId] = `导入失败：${formatError(error)}`;
  }
  renderActivePage();
}

function updateWorkflowPath(workflowId, workflowPath) {
  const workflows = Array.isArray(abilitiesPage.workflows) ? abilitiesPage.workflows : [];
  const item = workflows.find((workflow) => (
    workflow?.workflowId === workflowId || workflow?.id === workflowId
  ));
  if (item) {
    item.workflowPath = workflowPath;
  }
}

function readProviderConfigPayload(button) {
  const providerId = String(button?.dataset?.providerId || "").trim();
  const row = button?.closest?.("[data-provider-row]");
  const endpointInput = row?.querySelector?.("[data-provider-endpoint-input]");
  const enabledInput = row?.querySelector?.("[data-provider-enabled-input]");
  const ttsTestTextInput = row?.querySelector?.("[data-provider-tts-test-text]");
  const ttsProfileInput = row?.querySelector?.("[data-provider-tts-profile-input]");
  const profileNameInput = row?.querySelector?.("[data-provider-voice-profile-name-input]");
  const profileEnabledInput = row?.querySelector?.("[data-provider-voice-profile-enabled-input]");
  const modelFolderPathInput = row?.querySelector?.("[data-provider-model-folder-path-input]");
  const textLangInput = row?.querySelector?.("[data-provider-text-lang-input]");
  const promptLangInput = row?.querySelector?.("[data-provider-prompt-lang-input]");
  const mediaTypeInput = row?.querySelector?.("[data-provider-media-type-input]");
  const refAudioPathInput = row?.querySelector?.("[data-provider-ref-audio-path-input]");
  const promptTextInput = row?.querySelector?.("[data-provider-prompt-text-input]");
  const voiceProfileId = String(ttsProfileInput?.value || "").trim();
  const payload = {
    page: state.activePage,
    providerId,
    characterPackId: getCurrentControlCenterCharacterPackId(),
    endpoint: String(endpointInput?.value || "").trim(),
    enabled: Boolean(enabledInput?.checked),
    text: String(ttsTestTextInput?.value || "").trim(),
    voiceProfileId,
    displayName: String(profileNameInput?.value || "").trim(),
    voiceProfileEnabled: profileEnabledInput ? Boolean(profileEnabledInput.checked) : true,
    folderPath: String(modelFolderPathInput?.value || "").trim(),
    textLang: String(textLangInput?.value || "").trim(),
    promptLang: String(promptLangInput?.value || "").trim(),
    mediaType: String(mediaTypeInput?.value || "").trim(),
    refAudioPath: String(refAudioPathInput?.value || "").trim(),
    promptText: String(promptTextInput?.value || "").trim()
  };
  if (providerId && (ttsProfileInput || modelFolderPathInput || profileNameInput)) {
    state.providerVoiceProfileDrafts[providerId] = {
      ...(state.providerVoiceProfileDrafts[providerId] || {}),
      voiceProfileId,
      displayName: payload.displayName,
      voiceProfileEnabled: payload.voiceProfileEnabled,
      folderPath: payload.folderPath,
      textLang: payload.textLang,
      promptLang: payload.promptLang,
      mediaType: payload.mediaType,
      refAudioPath: payload.refAudioPath,
      promptText: payload.promptText
    };
  }
  return payload;
}

function getCurrentControlCenterCharacterPackId() {
  return String(
    latestRuntimeSnapshot?.state?.characterPackId ||
      characterPage.selectedPackId ||
      characterPage.selectedPack ||
      ""
  ).trim();
}

function applyProviderVoiceProfileSuggestion(providerId, result, payload = {}) {
  const suggested = result?.suggestedProfile && typeof result.suggestedProfile === "object" ? result.suggestedProfile : {};
  const current = state.providerVoiceProfileDrafts[providerId] || {};
  const warnings = Array.isArray(result?.warnings) ? result.warnings.map((item) => String(item || "").trim()).filter(Boolean) : [];
  state.providerVoiceProfileDrafts[providerId] = {
    ...current,
    folderPath: payload.folderPath || current.folderPath || "",
    voiceProfileId: String(suggested.voiceProfileId || current.voiceProfileId || "").trim(),
    displayName: String(suggested.displayName || suggested.name || current.displayName || "").trim(),
    voiceProfileEnabled: suggested.enabled === undefined ? (current.voiceProfileEnabled ?? true) : Boolean(suggested.enabled),
    textLang: String(suggested.textLang || current.textLang || "zh").trim(),
    promptLang: String(suggested.promptLang || current.promptLang || "zh").trim(),
    mediaType: String(suggested.mediaType || current.mediaType || "wav").trim(),
    refAudioPath: String(suggested.refAudioPath || current.refAudioPath || "").trim(),
    promptText: String(suggested.promptText || current.promptText || "").trim(),
    inspectWarnings: warnings,
    detected: result?.detected && typeof result.detected === "object" ? result.detected : {}
  };
}

function readMcpConfigPayload(button) {
  const row = button?.closest?.("[data-mcp-row]");
  const initialServerId = String(button?.dataset?.serverId || row?.dataset?.mcpServerId || "").trim();
  const serverIdInput = row?.querySelector?.("[data-mcp-server-id-input]");
  const displayNameInput = row?.querySelector?.("[data-mcp-display-name-input]");
  const commandInput = row?.querySelector?.("[data-mcp-command-input]");
  const argsInput = row?.querySelector?.("[data-mcp-args-input]");
  const cwdInput = row?.querySelector?.("[data-mcp-cwd-input]");
  const envInput = row?.querySelector?.("[data-mcp-env-input]");
  const enabledInput = row?.querySelector?.("[data-mcp-enabled-input]");
  const serverId = String(serverIdInput?.value || initialServerId).trim();
  const payload = {
    page: state.activePage,
    serverId,
    displayName: String(displayNameInput?.value || "").trim(),
    command: String(commandInput?.value || "").trim(),
    args: parseMcpArgs(String(argsInput?.value || "")),
    cwd: String(cwdInput?.value || "").trim(),
    env: parseMcpEnv(String(envInput?.value || "")),
    enabled: enabledInput ? Boolean(enabledInput.checked) : true
  };
  if (serverId) {
    if (initialServerId && initialServerId !== serverId) {
      delete state.mcpConfigDrafts[initialServerId];
    }
    state.mcpConfigDrafts[serverId] = {
      serverId,
      displayName: payload.displayName,
      command: payload.command,
      argsText: String(argsInput?.value || "").trim(),
      cwd: payload.cwd,
      envText: String(envInput?.value || "").trim(),
      enabled: payload.enabled
    };
  }
  return payload;
}

function parseMcpArgs(value) {
  return String(value || "")
    .split(/\r?\n|,/)
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(0, 24);
}

function parseMcpEnv(value) {
  const env = {};
  for (const line of String(value || "").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const separator = trimmed.indexOf("=");
    if (separator <= 0) continue;
    const key = trimmed.slice(0, separator).trim();
    const val = trimmed.slice(separator + 1).trim();
    if (key && val) env[key] = val;
  }
  return env;
}

function applyMcpPreset(presetId) {
  if (String(presetId || "") !== "anysearch") return;
  const serverId = ANYSEARCH_MCP_PRESET.serverId;
  state.activeMcpConfigId = serverId;
  state.mcpConfigDrafts[serverId] = {
    serverId,
    displayName: ANYSEARCH_MCP_PRESET.displayName,
    command: ANYSEARCH_MCP_PRESET.command,
    argsText: ANYSEARCH_MCP_PRESET.argsText,
    cwd: "",
    envText: "",
    enabled: true
  };
  renderActivePage();
}

function applyMcpTemplate(templateId) {
  const template = MCP_STDIO_TEMPLATES.find((item) => item.id === String(templateId || "").trim());
  if (!template) return;
  const serverId = uniqueMcpDraftServerId(template.serverId);
  state.activeMcpConfigId = serverId;
  state.mcpConfigDrafts[serverId] = {
    serverId,
    displayName: template.displayName,
    command: template.command,
    argsText: template.argsText,
    cwd: "",
    envText: "",
    enabled: true,
    templateId: template.id,
    reason: template.reason
  };
  renderActivePage();
}

function uniqueMcpDraftServerId(baseId) {
  const base = safeMcpDraftServerId(baseId) || "custom_mcp";
  const existing = new Set([
    ...Object.keys(state.mcpConfigDrafts || {}),
    ...(Array.isArray(abilitiesPage.mcpServers) ? abilitiesPage.mcpServers.map((item) => item.serverId || item.id || "") : [])
  ].map((item) => String(item || "").trim()).filter(Boolean));
  if (!existing.has(base)) return base;
  for (let index = 2; index <= 20; index += 1) {
    const candidate = `${base}_${index}`;
    if (!existing.has(candidate)) return candidate;
  }
  return `${base}_${Date.now().toString(36).slice(-4)}`;
}

function safeMcpDraftServerId(value) {
  const normalized = String(value || "")
    .trim()
    .replace(/[^A-Za-z0-9_.-]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 80);
  return normalized || "custom_mcp";
}

function readWorkflowConfigPayload(button) {
  const workflowId = String(button?.dataset?.workflowId || "").trim();
  const row = button?.closest?.("[data-workflow-row]");
  const workflowPathInput = row?.querySelector?.("[data-workflow-path-input]");
  const inputSlotInput = row?.querySelector?.("[data-workflow-input-slot-input]");
  const outputSlotInput = row?.querySelector?.("[data-workflow-output-slot-input]");
  const enabledInput = row?.querySelector?.("[data-workflow-enabled-input]");
  return {
    page: state.activePage,
    workflowId,
    workflowPath: String(workflowPathInput?.value || "").trim(),
    inputImageSlot: String(inputSlotInput?.value || "").trim(),
    outputImageSlot: String(outputSlotInput?.value || "").trim(),
    enabled: Boolean(enabledInput?.checked)
  };
}

function providerActionStatusLabel(result) {
  if (!result) return "未完成";
  if (result.status === "inspected") return Array.isArray(result.warnings) && result.warnings.length ? "已识别，仍需补几项" : "已自动填入声线";
  if (result.status === "missing_model_folder") return "模型文件夹不存在";
  if (result.status === "missing_model_files") return "未找到模型文件";
  if (result.reason === "model_folder_must_be_absolute") return "需要填写完整文件夹路径";
  if (result.reason === "model_folder_path_invalid") return "模型路径需要修正";
  if (result.status === "tts-test-ready") return "试听成功";
  if (result.reason === "provider_tts_test_empty_audio") return "试听失败：未返回音频";
  if (result.reason === "provider_tts_test_invalid_config") return "试听配置异常";
  if (result.reason === "provider_tts_test_failed" && result.profileApplied === false && result.voiceProfileId) return "试听失败：声线档案未应用";
  if (result.status === "tts-test-failed") return "试听失败";
  if (result.status === "tts-test-too-large") return "测试音频过大";
  if (result.status === "invalid_voice_profile") return "声线 ID 需要修正";
  if (result.status === "assigned") return result.ok ? "已设为当前角色声音" : "设置角色声音失败";
  if (result.status === "cleared") return result.ok ? "已恢复默认声线" : "恢复默认声线失败";
  if (result.status === "invalid-payload" && result.actionId === CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileAssignToCurrentCharacter) return "请选择声线和当前角色包";
  if (result.status === "invalid-payload" && result.actionId === CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileClearCurrentCharacter) return "请选择当前角色包";
  if (result.status === "saved") return result.ok ? (result.voiceProfileId ? "声线档案已保存" : "已保存") : "保存失败";
  if (result.status === "ready") return "连接正常";
  if (result.status === "unreachable") return "未连接";
  if (result.status === "missing_config") return "待填写地址";
  if (result.status === "unsupported_provider") return "暂不支持试听";
  if (result.status === "invalid_config") return "配置异常";
  if (result.status === "not-implemented") return "当前环境不可写";
  return result.ok ? "已完成" : "操作失败";
}

function mcpActionStatusLabel(result) {
  if (!result) return "未完成";
  if (result.status === "saved") return result.ok ? "MCP 配置已保存" : "保存失败";
  if (result.status === "discovered") return result.ok ? `发现 ${Number(result.toolCount || 0)} 个工具` : "发现失败";
  if (result.status === "missing_config") return "需要填写启动命令";
  if (result.status === "disabled") return "服务已关闭，请先启用";
  if (result.status === "invalid_config") return "配置异常";
  if (result.status === "invalid_mcp_server") return "服务 ID 需要修正";
  if (result.status === "discovery-failed") return "发现工具失败";
  if (result.status === "not-implemented" || result.reason === "mcp_discoverer_not_bound") return "当前后端未绑定 MCP 发现器";
  if (result.reason === "mcp_server_config_missing") return "请先保存 MCP 配置";
  if (result.reason === "mcp_server_disabled") return "服务已关闭，请先启用";
  if (result.reason === "mcp_discovery_failed") return "MCP 启动或 tools/list 失败";
  if (result.status === "request-failed") return "请求失败";
  return result.ok ? "已完成" : "操作失败";
}

async function playProviderTestAudio(providerId, result) {
  const audioBase64 = String(result?.audioBase64 || "");
  if (!audioBase64) return "试听成功";
  cleanupProviderTestAudioUrl();
  let audioUrl = "";
  try {
    const binary = window.atob(audioBase64);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) {
      bytes[index] = binary.charCodeAt(index);
    }
    const blob = new Blob([bytes], { type: result.mediaType || "audio/wav" });
    audioUrl = URL.createObjectURL(blob);
    providerTestAudioUrl = audioUrl;
    providerTestAudio = {
      providerId: String(providerId || ""),
      url: audioUrl,
      mediaType: result.mediaType || "audio/wav",
      audioBytes: bytes.length
    };
  } catch (error) {
    cleanupProviderTestAudioUrl();
    return `试听失败：${formatError(error)}`;
  }
  try {
    const audio = new Audio(audioUrl);
    await audio.play();
    return "试听音频已播放";
  } catch (error) {
    return "试听已生成，请点播放器播放";
  }
}

function cleanupProviderTestAudioUrl() {
  if (providerTestAudioUrl) {
    URL.revokeObjectURL(providerTestAudioUrl);
    providerTestAudioUrl = "";
  }
  providerTestAudio = {
    providerId: "",
    url: "",
    mediaType: "",
    audioBytes: 0
  };
}

function workflowActionStatusLabel(result) {
  if (!result) return "未完成";
  if (result.status === "workflow_file_saved") return "已导入工作流文件";
  if (result.status === "saved") return result.ok ? "已保存绑定" : "保存失败";
  if (result.status === "validated_config") return "配置可保存";
  if (result.status === "missing_workflow") return "待绑定";
  if (result.status === "missing_slot_mapping") return "槽位待补齐";
  if (result.status === "invalid_workflow_config" && result.reason === "workflow_file_invalid_json") return "JSON 格式错误";
  if (result.status === "invalid_workflow_config" && result.reason === "workflow_json_invalid") return "不是 API 工作流";
  if (result.status === "invalid_workflow_config" && result.reason === "workflow_file_required") return "文件为空";
  if (result.status === "invalid_workflow_config") return "绑定配置异常";
  if (result.status === "invalid_config") return "配置异常";
  if (result.status === "not-implemented") return "当前环境不可写";
  return result.ok ? "已完成" : "操作失败";
}

function formatSettingValue(value) {
  if (value === null || value === undefined || value === "") return "（空）";
  if (typeof value === "boolean") return value ? "开" : "关";
  let text = Array.isArray(value) ? value.join(", ") : String(value);
  if (!text) return "（空）";
  if (text.length > 48) text = `${text.slice(0, 46)}…`;
  return text;
}

function renderSettingControl(entry) {
  if (entry.editable && entry.type === "bool") {
    const checked = entry.current === true ? " checked" : "";
    return `<input type="checkbox" class="settings-edit-toggle" data-setting-key="${escapeAttr(entry.key)}"${checked} />`;
  }
  if (entry.editable) {
    const raw = entry.current === null || entry.current === undefined ? "" : String(entry.current);
    return `<input type="text" class="settings-edit-input" data-setting-key="${escapeAttr(entry.key)}" value="${escapeAttr(raw)}" />`;
  }
  const value = entry.sensitive
    ? (entry.isSet ? "已配置 · 已隐藏" : "未配置")
    : formatSettingValue(entry.current);
  return `<span class="settings-value">${escapeHtml(value)}</span>`;
}

function settingsManagedTarget(managedIn) {
  if (managedIn === "model-service") return { page: "model", label: "在模型页管理" };
  if (managedIn === "capabilities") return { page: "abilities", label: "在能力页管理" };
  return null;
}

function renderSettingRow(entry) {
  const legend = (settingsCatalog && settingsCatalog.scopeLegend) || {};
  const scopeText = legend[entry.scope] || entry.scope || "";
  const sensitiveTag = entry.sensitive ? `<span class="settings-tag settings-tag--secret">敏感</span>` : "";
  const target = entry.managedIn ? settingsManagedTarget(entry.managedIn) : null;
  const managed = target
    ? `<button type="button" class="settings-jump" data-page="${escapeAttr(target.page)}">${escapeHtml(target.label)} →</button>`
    : entry.managedIn
      ? `<span class="settings-tag">在「${escapeHtml(entry.managedIn)}」管理</span>`
      : "";
  return `
    <div class="settings-row">
      <div class="settings-row__info">
        <code class="settings-key">${escapeHtml(entry.key || "")}</code>
        <span class="settings-desc">${escapeHtml(entry.description || "")}</span>
      </div>
      <div class="settings-row__meta">
        ${renderSettingControl(entry)}
        <span class="settings-scope" data-scope="${escapeHtml(entry.scope || "")}">${escapeHtml(scopeText)}</span>
        ${sensitiveTag}
        ${managed}
      </div>
    </div>`;
}

function renderSettingsGroup(group) {
  const rows = Array.isArray(group.settings) ? group.settings.map(renderSettingRow).join("") : "";
  return `
      <article class="glass-card settings-group">
        <h2>${escapeHtml(group.category || "")}</h2>
        <div class="settings-row-list">${rows}</div>
      </article>`;
}

function renderSettingsPage() {
  const note = settingsSaveNote
    ? `<p class="settings-note${settingsSaveOk ? "" : " settings-note--error"}">${escapeHtml(settingsSaveNote)}</p>`
    : "";
  const head = `
    <header class="settings-head">
      <h1>${icon("sparkle")} 设置目录</h1>
      <p>「运行时」项可直接改、即时生效；标「需重启」的与密钥为只读。</p>
      ${note}
    </header>`;
  if (!settingsCatalog || !Array.isArray(settingsCatalog.categories) || !settingsCatalog.categories.length) {
    const msg = settingsCatalogStatus || "正在读取设置目录…";
    return `<section class="settings-page">${head}<article class="glass-card"><p class="settings-empty">${escapeHtml(msg)}</p></article></section>`;
  }
  const categories = settingsCatalog.categories;
  const commonHtml = categories.filter((group) => group.tier !== "advanced").map(renderSettingsGroup).join("");
  const advanced = categories.filter((group) => group.tier === "advanced");
  const advancedCount = advanced.reduce(
    (total, group) => total + (Array.isArray(group.settings) ? group.settings.length : 0),
    0
  );
  const advancedHtml = advanced.length
    ? `<details class="settings-advanced">
        <summary>${icon("sparkle")} 高级 · 实验性设置（${advancedCount} 项，默认折叠）</summary>
        <div class="settings-advanced-body">${advanced.map(renderSettingsGroup).join("")}</div>
      </details>`
    : "";
  return `<section class="settings-page">${head}${commonHtml}${advancedHtml}</section>`;
}

function resolveInitialPage() {
  const params = new URLSearchParams(window.location.search);
  const candidate = params.get("page") || window.location.hash.replace(/^#/, "");
  if (Array.isArray(navItems) && navItems.some((item) => item.id === candidate)) {
    return candidate;
  }
  return labMeta?.defaultPage || "overview";
}

function renderActivePage() {
  document.documentElement.dataset.activePage = state.activePage;
  root.querySelectorAll("[data-page]").forEach((button) => {
    button.classList.toggle("active", button.dataset.page === state.activePage);
  });

  const content = root.querySelector("#page-content");
  if (!content) return;
  if (renderedPageId) {
    pageScrollPositions.set(renderedPageId, content.scrollTop);
  }
  const renderers = {
    overview: renderOverviewPage,
    model: renderModelPage,
    character: renderCharacterPage,
    voice: renderVoicePage,
    music: renderMusicPage,
    context: renderPerceptionPage,
    abilities: renderAbilitiesPage,
    advanced: renderAdvancedPage,
    settings: renderSettingsPage
  };
  content.innerHTML = (renderers[state.activePage] || renderOverviewPage)();
  applyActionAvailability(content);
  const scrollTop = pageScrollPositions.get(state.activePage) || 0;
  content.scrollTop = scrollTop;
  requestAnimationFrame(() => {
    if (renderedPageId === state.activePage) {
      content.scrollTop = scrollTop;
    }
  });
  renderedPageId = state.activePage;
}

function applyLocalActionOptimisticUpdate(actionId, payload) {
  if (actionId === CONTROL_CENTER_ACTIONS.abilitiesApprovalPolicySave) {
    const defaultMode = String(payload.defaultMode || payload.value || "").trim();
    if (["ask_each_time", "trusted_auto_allow"].includes(defaultMode)) {
      const currentSafety = abilitiesPage.safety || {};
      const currentPolicy = currentSafety.approvalPolicy || {};
      const label = defaultMode === "trusted_auto_allow" ? "完全访问" : "请求批准";
      abilitiesPage.safety = {
        ...currentSafety,
        approvalPolicy: {
          ...currentPolicy,
          defaultMode,
          label,
          summary: defaultMode === "trusted_auto_allow"
            ? "高风险能力自动允许；URL、路径、密钥和本地边界校验仍保持开启。"
            : "高风险能力在执行前创建审批请求，由用户允许或拒绝。",
          trustedAutoAllowHighRisk: defaultMode === "trusted_auto_allow",
          requiresConfirmationByDefault: defaultMode !== "trusted_auto_allow"
        },
        items: (Array.isArray(currentSafety.items) ? currentSafety.items : []).map((item) => (
          item.label === "当前审批模式" ? { ...item, status: label } : item
        ))
      };
      renderActivePage();
    }
    return;
  }
  if (actionId === CONTROL_CENTER_ACTIONS.musicSetPlayMode) {
    const nextMode = nextMusicPlayMode(musicPage.currentPlayMode);
    musicPage.currentPlayMode = nextMode;
    payload.value = nextMode;
    payload.playMode = nextMode;
    payload.field = "playMode";
    renderActivePage();
    return;
  }
  if (actionId === CONTROL_CENTER_ACTIONS.musicSetVolumeNormalization) {
    musicPage.volumeNormalization = Boolean(payload.value);
    renderActivePage();
  }
}

function nextMusicPlayMode(currentMode) {
  const index = MUSIC_PLAY_MODE_OPTIONS.indexOf(String(currentMode || "").trim());
  return MUSIC_PLAY_MODE_OPTIONS[(index + 1) % MUSIC_PLAY_MODE_OPTIONS.length];
}

function rememberRenderedPageScroll() {
  if (!renderedPageId) return;
  const content = root.querySelector("#page-content");
  if (!content) return;
  pageScrollPositions.set(renderedPageId, content.scrollTop);
}

function isControlCenterClientHandledAction(actionId) {
  return clientHandledActionIds.has(actionId);
}

function applyActionAvailability(container) {
  if (!container) return;
  const elements = container.querySelectorAll("[data-action-id]");
  for (let index = 0; index < elements.length; index += 1) {
    const element = elements[index];
    try {
      const actionId = element.dataset.actionId;
      if (isControlCenterBridgedAction(actionId) || isControlCenterClientHandledAction(actionId)) {
        delete element.dataset.actionUnavailable;
        element.removeAttribute("aria-disabled");
        if ("disabled" in element) {
          element.disabled = false;
        }
        continue;
      }
      element.dataset.actionUnavailable = "true";
      element.setAttribute("aria-disabled", "true");
      element.setAttribute("title", element.getAttribute("title") || "暂未接入真实功能");
      if ("disabled" in element) {
        element.disabled = true;
      }
    } catch {
      // One bad action element must not crash the whole page.
    }
  }
}

function renderNavButton(item) {
  return `
    <button class="${item.id === state.activePage ? "active" : ""}" data-page="${item.id}" type="button">
      ${icon(item.icon)}
      <span>${escapeHtml(item.label)}</span>
    </button>
  `;
}

function renderPageTitle(page, extra = "") {
  return `
    <section class="page-title-row">
      <div>
        <h1>${escapeHtml(page.title)}${page.accent ? ` <span>${escapeHtml(page.accent)}</span>` : ""}</h1>
        <p>${escapeHtml(page.subtitle)}</p>
      </div>
      ${extra || `<div class="title-orbit" aria-hidden="true">${Array.from({ length: 18 }, (_, index) => `<i style="--bar: ${((index * 5) % 20) + 8}px"></i>`).join("")}</div>`}
    </section>
  `;
}

function renderOverviewPage() {
  return `
    ${renderPageTitle(overviewPage)}
    <section class="overview-dashboard-page">
      <div class="overview-top-grid">
        <article class="glass-card overview-status-card">
          <div class="overview-status-art">
            <img src="${imageFor(overviewPage.status.hero || overviewPage.status.image)}" alt="" />
          </div>
          <div class="overview-status-content">
            <div class="card-heading">
              <h2>${escapeHtml(overviewPage.status.title)}</h2>
              <span class="connected-badge">${icon("checkCircle")} ${escapeHtml(overviewPage.status.badge)}</span>
            </div>
            <div class="status-detail-list">
              ${overviewPage.status.items.map(renderOverviewStatusRow).join("")}
            </div>
          </div>
        </article>

        <article class="glass-card overview-diagnostic-card">
          <div class="card-heading">
            <h2>${escapeHtml(overviewPage.connection.title)}</h2>
            <span class="connected-badge">${icon("checkCircle")} ${escapeHtml(overviewPage.connection.badge)}</span>
          </div>
          <div class="connection-list">
            ${overviewPage.connection.rows.map(renderConnectionRow).join("")}
          </div>
        </article>

        <article class="glass-card overview-actions-card">
          <h2>快捷操作</h2>
          <div class="overview-action-stack">
            ${overviewPage.quickActions.map(renderOverviewAction).join("")}
          </div>
        </article>
      </div>

      <div class="overview-mid-grid">
        ${renderOverviewPackCard()}
        ${renderOverviewEmotionCard()}
        ${renderOverviewVoiceCard()}
      </div>

      ${renderListeningTogetherCard()}

      <div class="overview-feature-grid">
        <article class="glass-card overview-sense-card">
          <h2>${icon("eye")} ${escapeHtml(overviewPage.sense.title)}</h2>
          <div class="sense-toggle-row">
            ${overviewPage.sense.toggles.map(renderOverviewSenseToggle).join("")}
          </div>
          <p>${icon("shield")} ${escapeHtml(overviewPage.sense.note)}</p>
        </article>
        <article class="glass-card overview-abilities-card">
          <h2>${icon("star")} 能力一览</h2>
          <div class="overview-ability-chip-row">
            ${overviewPage.abilities.map((item, index) => `
              <span class="tone-${index % 6}">${escapeHtml(item)}</span>
            `).join("")}
          </div>
        </article>
      </div>

      ${renderRecentOutputs(overviewPage.recentOutputs)}

      <article class="glass-card overview-health-card">
        <h2>${icon("equalizer")} 应用健康诊断</h2>
        <div class="health-grid">
          ${overviewPage.health.map(renderHealthTile).join("")}
        </div>
      </article>
    </section>
  `;
}

function renderOverviewStatusRow(item) {
  return `
    <div>
      <span>${icon(item.icon)} ${escapeHtml(item.label)}:</span>
      <strong>${escapeHtml(item.value)}</strong>
    </div>
  `;
}

function renderConnectionRow(item) {
  return `
    <div>
      <span>${icon(item.icon)}</span>
      <b>${escapeHtml(item.label)}:</b>
      <strong class="${item.tone || ""}">${escapeHtml(item.value)}</strong>
    </div>
  `;
}

function renderOverviewAction(item) {
  return `
    <button class="${item.tone}" type="button" data-action-id="${escapeAttr(item.commandId)}">
      ${icon(item.icon)}
      <span>${escapeHtml(item.label)}</span>
    </button>
  `;
}

function renderOverviewSenseToggle(item) {
  const key = item.id || "";
  const enabled = typeof state.switches[key] === "boolean" ? state.switches[key] : Boolean(item.enabled);
  if (!item.actionId || !key) {
    return `<span>${icon(item.icon || "clipboard")} ${escapeHtml(item.label)} <i class="${enabled ? "is-on" : ""}"></i></span>`;
  }
  return `
    <button class="${enabled ? "is-on" : ""}" type="button" data-switch="${escapeAttr(key)}" data-switch-action-id="${escapeAttr(item.actionId)}" role="switch" aria-checked="${enabled}">
      ${icon(item.icon || "clipboard")}
      <span>${escapeHtml(item.label)}</span>
      <i></i>
    </button>
  `;
}

function renderOverviewPackCard() {
  return `
    <article class="glass-card overview-pack-card">
      <h2>${escapeHtml(overviewPage.pack.title)}</h2>
      <div class="pack-folder-visual">${icon("folder")}<span>✿</span></div>
      <div>
        <strong>${escapeHtml(overviewPage.pack.name)}</strong>
        <p>版本：${escapeHtml(overviewPage.pack.version)}</p>
        <p>发布时间：${escapeHtml(overviewPage.pack.publishedAt)}</p>
        <button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.characterOpenPackFolder}">${escapeHtml(overviewPage.pack.action)} ${icon("chevron")}</button>
      </div>
    </article>
  `;
}

function renderOverviewEmotionCard() {
  return `
    <article class="glass-card overview-emotion-card">
      <h2>${escapeHtml(overviewPage.emotion.title)}</h2>
      <img src="${imageFor(overviewPage.emotion.image)}" alt="" />
      <strong>${escapeHtml(overviewPage.emotion.name)}</strong>
      <div class="preview-dots"><span></span><span class="active"></span><span></span></div>
    </article>
  `;
}

function renderOverviewVoiceCard() {
  const rows = Array.isArray(overviewPage.voice.rows) ? overviewPage.voice.rows : [];
  return `
    <article class="glass-card overview-voice-card">
      <h2>${icon("equalizer")} ${escapeHtml(overviewPage.voice.title)}</h2>
      <div class="overview-voice-toggles">
        ${rows.map(renderOverviewVoiceToggle).join("")}
      </div>
      <p>${icon("checkCircle")} ${escapeHtml(overviewPage.voice.status)} <i></i></p>
    </article>
  `;
}

function renderOverviewVoiceToggle(row) {
  const key = row.id || "";
  const enabled = typeof state.voice[key] === "boolean" ? state.voice[key] : Boolean(row.enabled);
  if (!row.actionId || !key) {
    return `<div><span>${escapeHtml(row.label)}</span>${renderStaticSwitch(enabled)}</div>`;
  }
  return `
    <button type="button" data-voice-toggle="${escapeAttr(key)}" data-voice-action-id="${escapeAttr(row.actionId)}" aria-pressed="${enabled}">
      <span>${escapeHtml(row.label)}</span>${renderStaticSwitch(enabled)}
    </button>
  `;
}

function renderOverviewMusicCard() {
  return `
    <article class="glass-card overview-music-card">
      <h2>${icon("music")} ${escapeHtml(overviewPage.music.title)}</h2>
      <div class="overview-mini-player">
        <div class="mini-album" style="--cover-image: url(${imageFor(overviewPage.music.cover)})"></div>
        <div>
          <strong>${escapeHtml(overviewPage.music.song)}</strong>
          <p>${escapeHtml(overviewPage.music.artist)}</p>
        </div>
        <span class="mini-bars" aria-hidden="true">${Array.from({ length: 12 }, (_, index) => `<i style="--bar: ${((index * 7) % 24) + 8}px"></i>`).join("")}</span>
      </div>
      <div class="mini-control-row">
        ${overviewPage.music.controls.map((item, index) => `
          <button class="${index === 2 ? "active" : ""}" type="button"${item.actionId ? ` data-action-id="${escapeAttr(item.actionId)}"` : ""}>${index === 0 ? icon("previous") : index === 1 ? icon("next") : index === 2 ? icon("pause") : index === 3 ? icon("stop") : icon("trash")} ${escapeHtml(item.label)}</button>
        `).join("")}
      </div>
    </article>
  `;
}

function renderRecentOutputs(recentOutputs) {
  const outputs = Array.isArray(recentOutputs) ? recentOutputs : [];
  return `
    <article class="glass-card overview-outputs-card">
      <div class="card-heading">
        <h2>${icon("sparkle")} 最近成果</h2>
        <button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.workspaceOpen}">打开工作区 ${icon("chevron")}</button>
      </div>
      ${outputs.length > 0 ? `
        <div class="recent-outputs-list">
          ${outputs.slice(0, 3).map((item) => `
            <div class="recent-output-item">
              <span class="recent-output-icon">${icon("file")}</span>
              <div class="recent-output-info">
                <strong>${escapeHtml(item.title || "未命名文件")}</strong>
                <small>${escapeHtml(item.subtitle || item.format || "")}</small>
              </div>
            </div>
          `).join("")}
        </div>
      ` : `
        <div class="recent-outputs-empty">
          <p>暂无生成成果</p>
          <small>Akane 处理完任务后会在这里展示成果</small>
        </div>
      `}
    </article>
  `;
}

function renderHealthTile(item) {
  return `
    <div class="health-tile">
      <span>${escapeHtml(item.label)}</span>
      <strong>${escapeHtml(item.value)}</strong>
      ${typeof item.progress === "number" ? `<i><b style="width: ${item.progress}%"></b></i>` : `<em class="${item.detail || ""}"></em>`}
      ${item.note ? `<small>${escapeHtml(item.note)}</small>` : ""}
    </div>
  `;
}

function renderStaticSwitch(isOn) {
  return `<span class="static-switch ${isOn ? "is-on" : ""}"><i></i></span>`;
}

function renderModelPage() {
  const draft = state.modelDraft || buildModelServiceDraft(modelPage);
  const provider = modelProviderById(draft.providerId);
  const configured = String(modelPage.status || "") === "configured";
  const statusLabel = configured ? "已配置" : "等待配置";
  const statusDetail = configured
    ? `${provider?.label || draft.providerId} · ${draft.chatModel || "未选择模型"}`
    : "填写并测试后即可开始对话";
  const apiKeyPlaceholder = modelPage.hasApiKey
    ? "已保存，留空表示继续使用原密钥"
    : provider?.apiKeyRequired === false
      ? "本地服务通常无需填写"
      : "粘贴服务商提供的 API Key";
  const modelOptions = Array.from(new Set([
    ...state.modelModels,
    draft.chatModel
  ].filter(Boolean)));
  const busy = Boolean(state.modelBusyAction);
  return `
    ${renderPageTitle(modelPage)}
    <section class="model-service-page">
      <article class="glass-card model-service-status ${configured ? "is-ready" : "needs-config"}">
        <div class="model-status-mark">${icon(configured ? "check" : "sparkle")}</div>
        <div>
          <span>当前连接</span>
          <h2>${escapeHtml(statusLabel)}</h2>
          <p>${escapeHtml(statusDetail)}</p>
        </div>
        <div class="model-status-source">
          <span>配置来源</span>
          <strong>${escapeHtml(modelPage.source === "local_file" ? "控制中心" : "系统默认")}</strong>
        </div>
      </article>

      <div class="model-service-grid">
        <article class="glass-card model-config-card">
          <div class="card-heading">
            <div>
              <span>基础配置</span>
              <h2>连接模型服务</h2>
            </div>
            <span class="model-protocol-badge">${escapeHtml(draft.protocol || provider?.protocol || "openai")}</span>
          </div>

          <div class="model-config-form">
            <label class="span-2">
              <span>服务商</span>
              <select data-model-provider>
                ${(modelPage.providers || []).map((item) => `
                  <option value="${escapeAttr(item.id)}"${item.id === draft.providerId ? " selected" : ""}>${escapeHtml(item.label)}</option>
                `).join("")}
              </select>
              <small>${escapeHtml(provider?.description || "填写兼容 OpenAI Chat Completions 的服务地址。")}</small>
            </label>

            <label class="span-2">
              <span>Base URL</span>
              <input data-model-base-url type="text" value="${escapeAttr(draft.baseUrl)}" placeholder="https://api.example.com/v1" autocomplete="url" />
              <small>填写到服务根地址或 /v1，不要填 /chat/completions。</small>
            </label>

            <label class="span-2">
              <span>API Key</span>
              <input data-model-api-key type="password" value="${escapeAttr(draft.apiKey)}" placeholder="${escapeAttr(apiKeyPlaceholder)}" autocomplete="off" />
              <small>密钥只保存在本机配置文件中，读取接口不会把它返回给界面。</small>
            </label>

            <label>
              <span>聊天模型</span>
              <input data-model-chat-model type="text" list="model-service-models" value="${escapeAttr(draft.chatModel)}" placeholder="先检测，或手动填写模型名" />
              <datalist id="model-service-models">
                ${modelOptions.map((model) => `<option value="${escapeAttr(model)}"></option>`).join("")}
              </datalist>
            </label>

            <label>
              <span>请求超时</span>
              <input data-model-timeout type="number" min="5" max="600" value="${escapeAttr(String(draft.timeoutSeconds || 120))}" />
              <small>单位：秒</small>
            </label>

            <label class="model-inline-toggle span-2">
              <input data-model-use-vision type="checkbox"${draft.useForVision ? " checked" : ""} />
              <span>
                <strong>同时用于图片与屏幕理解</strong>
                <small>主模型支持视觉时可直接开启；也可以在下方填写不同的视觉模型名。</small>
              </span>
            </label>

            ${draft.useForVision ? `
              <label class="span-2">
                <span>视觉模型（可选）</span>
                <input data-model-vision-model type="text" list="model-service-models" value="${escapeAttr(draft.visionModel)}" placeholder="留空则使用聊天模型" />
              </label>
            ` : ""}

            ${modelPage.hasApiKey ? `
              <label class="model-clear-key span-2">
                <input data-model-clear-key type="checkbox" />
                <span>清除已经保存的 API Key</span>
              </label>
            ` : ""}
          </div>

          <div class="model-config-actions">
            <button type="button" data-model-service-action="models"${busy ? " disabled" : ""}>${icon("refresh")} ${state.modelBusyAction === "models" ? "检测中" : "检测模型"}</button>
            <button type="button" data-model-service-action="test"${busy ? " disabled" : ""}>${icon("check")} ${state.modelBusyAction === "test" ? "测试中" : "测试 API"}</button>
            <button class="primary" type="button" data-model-service-action="save"${busy ? " disabled" : ""}>${icon("checkCircle")} ${state.modelBusyAction === "save" ? "保存中" : "保存并应用"}</button>
          </div>
          <p class="model-action-status">${escapeHtml(state.modelActionStatus || "建议先“检测模型”，选好后再“测试 API”，最后保存。")}</p>
        </article>

        <aside class="glass-card model-guide-card">
          <span class="model-guide-kicker">首次配置</span>
          <h2>只需要三样东西</h2>
          <ol>
            <li><strong>服务商</strong><span>选择官方服务、本地 Ollama 或兼容中转。</span></li>
            <li><strong>API Key</strong><span>Ollama 以外通常需要；Akane 不会在响应中回传密钥。</span></li>
            <li><strong>模型名</strong><span>优先点“检测模型”，不支持列表接口时再手动填写。</span></li>
          </ol>
          <div class="model-guide-note">
            ${icon("info")}
            <p><strong>高级用户</strong><br />仍可在 <code>.env</code> 中分别配置 CHAT、AUX、TEXT、VISION；控制中心保存的一体化配置优先用于本机日常使用。</p>
          </div>
        </aside>
      </div>
    </section>
  `;
}

function buildModelServiceDraft(source = {}) {
  return {
    providerId: String(source.providerId || "openai_compatible"),
    protocol: String(source.protocol || "openai"),
    baseUrl: String(source.baseUrl || ""),
    apiKey: "",
    chatModel: String(source.chatModel || ""),
    useForVision: source.useForVision !== false,
    visionModel: String(source.visionModel || ""),
    timeoutSeconds: Number(source.timeoutSeconds || 120),
    clearApiKey: false
  };
}

function readModelServiceForm() {
  const fallback = state.modelDraft || buildModelServiceDraft(modelPage);
  const providerId = String(root.querySelector("[data-model-provider]")?.value || fallback.providerId);
  const provider = modelProviderById(providerId);
  return {
    providerId,
    protocol: String(provider?.protocol || fallback.protocol || "openai"),
    baseUrl: String(root.querySelector("[data-model-base-url]")?.value || fallback.baseUrl || "").trim(),
    apiKey: String(root.querySelector("[data-model-api-key]")?.value || fallback.apiKey || "").trim(),
    chatModel: String(root.querySelector("[data-model-chat-model]")?.value || fallback.chatModel || "").trim(),
    useForVision: Boolean(root.querySelector("[data-model-use-vision]")?.checked ?? fallback.useForVision),
    visionModel: String(root.querySelector("[data-model-vision-model]")?.value || fallback.visionModel || "").trim(),
    timeoutSeconds: Number(root.querySelector("[data-model-timeout]")?.value || fallback.timeoutSeconds || 120),
    clearApiKey: Boolean(root.querySelector("[data-model-clear-key]")?.checked)
  };
}

function modelProviderById(providerId) {
  return (modelPage.providers || []).find((item) => item.id === providerId) || null;
}

function renderCharacterPage() {
  const activeOutfit =
    characterPage.outfits.find((item) => item.id === state.activeOutfit) || characterPage.outfits[0] || {};
  const activeEmotion =
    characterPage.emotions.find((item) => item.id === state.activeEmotion) || characterPage.emotions[0] || {};

  return `
    <section class="character-lab-page">
      <div class="character-top-grid">
        <article class="glass-card character-hero-panel">
          <img src="${imageFor(characterPage.hero || "happy")}" alt="" />
          <div>
            <h1>${escapeHtml(characterPage.title)}</h1>
            <p>${escapeHtml(characterPage.subtitle)}</p>
          </div>
        </article>
        <article class="glass-card pack-select-panel">
          <h2>${icon("folder")} 当前角色包选择</h2>
          <button class="select-like" type="button" data-action-id="${CONTROL_CENTER_ACTIONS.characterSelectPack}" data-payload-field="packId" data-payload-value="${escapeAttr(characterPage.selectedPackId || characterPage.selectedPack)}" data-payload-pack-id="${escapeAttr(characterPage.selectedPackId || characterPage.selectedPack)}">
            <span>${escapeHtml(characterPage.selectedPack)}</span>
            ${icon("chevronDown")}
          </button>
          ${renderCharacterPackSwitcher()}
          <div class="pack-action-row">
            <button class="pink-action" type="button" data-action-id="${CONTROL_CENTER_ACTIONS.characterImportZip}">${icon("cloudUpload")} 导入 zip</button>
            <button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.characterOpenPackFolder}">${icon("folder")} 打开角色包目录</button>
          </div>
        </article>
        <article class="glass-card pack-info-panel">
          <h2>当前包信息</h2>
          <dl>
            ${characterPage.packInfo
              .map((item) => `<div><dt>${escapeHtml(item.label)}:</dt><dd>${escapeHtml(item.value)}</dd></div>`)
              .join("")}
          </dl>
          <div class="pack-completeness">
            <div><span>资源完整度</span><strong>${characterPage.completeness}%</strong></div>
            <i><b style="width: ${characterPage.completeness}%"></b></i>
            <p>${icon("checkCircle")} 资源状态良好</p>
          </div>
        </article>
      </div>

      <div class="character-middle-grid">
        <article class="glass-card outfit-panel">
          <div class="card-heading">
            <h2>${icon("shirt")} 服装选择</h2>
            <button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.characterManageOutfits}" data-payload-pack-id="${escapeAttr(characterPage.selectedPackId || characterPage.selectedPack)}">管理服装</button>
          </div>
          <div class="outfit-strip">
            ${characterPage.outfits.map((item) => renderOutfitTile(item, activeOutfit)).join("")}
            <button class="outfit-next" type="button" data-action-id="${CONTROL_CENTER_ACTIONS.characterManageOutfits}" data-payload-pack-id="${escapeAttr(characterPage.selectedPackId || characterPage.selectedPack)}" aria-label="更多服装">${icon("chevron")}</button>
          </div>
        </article>
        <article class="glass-card expression-panel">
          <div class="card-heading">
            <h2>${icon("smile")} 表情预览</h2>
            <button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.characterMoreExpressions}" data-payload-outfit-id="${escapeAttr(state.activeOutfit)}" data-payload-emotion-id="${escapeAttr(state.activeEmotion)}">更多表情 ${icon("chevron")}</button>
          </div>
          <div class="expression-layout">
            <div class="expression-strip">
              ${characterPage.emotions.map((item) => renderExpressionTile(item, activeEmotion)).join("")}
            </div>
            <div class="expression-preview">
              <img src="${imageFor(activeEmotion.image)}" alt="" />
              <div>
                <strong>${escapeHtml(activeEmotion.name)}</strong>
                <span>${icon("circle")} 当前预览</span>
              </div>
            </div>
          </div>
        </article>
      </div>

      <div class="character-bottom-grid">
        <article class="glass-card resource-warning-panel">
          <h2>${icon("alert")} ${escapeHtml(characterPage.warning.title)}</h2>
          <div>
            <div>
              <strong>${escapeHtml(characterPage.warning.headline)}</strong>
              <p>${escapeHtml(characterPage.warning.body)}</p>
            </div>
            <button type="button"${characterPage.warning.actionId ? ` data-action-id="${escapeAttr(characterPage.warning.actionId)}"` : ""}>${escapeHtml(characterPage.warning.action)}</button>
            <span aria-hidden="true">${icon("folder")}</span>
          </div>
        </article>
        <article class="glass-card resource-status-panel">
          <h2>${icon("folder")} 资源与包状态</h2>
          <div class="resource-list">
            ${characterPage.resources.map(renderResourceRow).join("")}
          </div>
        </article>
        <article class="glass-card character-tip-panel">
          <h2>${icon("help")} 小贴士</h2>
          ${characterPage.tip.map((line) => `<p>${escapeHtml(line)}</p>`).join("")}
          <span aria-hidden="true">✦</span>
        </article>
      </div>

      <div class="character-action-bar">
        <button class="apply-button" type="button" data-action-id="${CONTROL_CENTER_ACTIONS.characterApply}">${icon("checkCircle")} ${escapeHtml(characterPage.actions[0])}</button>
        <button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.characterRefresh}">${icon("refresh")} ${escapeHtml(characterPage.actions[1])}</button>
        <button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.characterRestoreDefaults}">${icon("undo")} ${escapeHtml(characterPage.actions[2])}</button>
      </div>
    </section>
  `;
}

function renderCharacterPackSwitcher() {
  const packs = normalizeCharacterPackCards(characterPage.availablePacks, characterPage.selectedPackId);
  if (!packs.length) {
    return `<p class="pack-switcher-empty">等待桌宠同步可用角色包。</p>`;
  }
  return `
    <div class="pack-switcher" aria-label="可用角色包">
      ${packs.map(renderCharacterPackSwitchCard).join("")}
    </div>
  `;
}

function normalizeCharacterPackCards(packs, selectedPackId) {
  const selected = String(selectedPackId || characterPage.selectedPack || "").trim();
  const seen = new Set();
  return (Array.isArray(packs) ? packs : [])
    .map((pack) => {
      if (!pack || typeof pack !== "object") return null;
      const id = String(pack.id || pack.packId || pack.pack_id || "").trim();
      if (!id || seen.has(id)) return null;
      seen.add(id);
      const label = String(pack.appName || pack.app_name || pack.name || pack.label || id).trim();
      const detail = [
        String(pack.name || pack.characterId || pack.character_id || "").trim(),
        String(pack.defaultOutfit || pack.default_outfit || "").trim()
      ].filter(Boolean).join(" · ");
      return {
        id,
        label: label || id,
        detail,
        selected: selected ? id === selected : Boolean(pack.selected)
      };
    })
    .filter(Boolean)
    .slice(0, 6);
}

function renderCharacterPackSwitchCard(pack) {
  const disabled = pack.selected ? " disabled aria-disabled=\"true\"" : "";
  return `
    <button class="pack-switch-card ${pack.selected ? "active" : ""}" type="button" data-action-id="${CONTROL_CENTER_ACTIONS.characterSelectPack}" data-payload-field="packId" data-payload-value="${escapeAttr(pack.id)}" data-payload-pack-id="${escapeAttr(pack.id)}"${disabled}>
      <strong>${escapeHtml(pack.label)}</strong>
      <span>${escapeHtml(pack.detail || pack.id)}</span>
      ${pack.selected ? `<small>${icon("check")} 当前</small>` : ""}
    </button>
  `;
}

function renderOutfitTile(item, activeOutfit) {
  const active = item.id === activeOutfit.id;
  return `
    <button class="outfit-tile ${active ? "active" : ""}" data-character-outfit="${escapeAttr(item.id)}" type="button">
      <img src="${imageFor(item.image)}" alt="" />
      ${active ? `<span class="check-corner">${icon("check")}</span>` : ""}
      <strong>${escapeHtml(item.name)}</strong>
      ${item.badge ? `<small>${escapeHtml(item.badge)}</small>` : ""}
    </button>
  `;
}

function renderExpressionTile(item, activeEmotion) {
  const active = item.id === activeEmotion.id;
  return `
    <button class="expression-tile ${active ? "active" : ""}" data-character-emotion="${escapeAttr(item.id)}" type="button">
      <img src="${imageFor(item.image)}" alt="" />
      <span>${escapeHtml(item.name)}</span>
    </button>
  `;
}

function renderResourceRow(item) {
  return `
    <div class="${item.tone}">
      <span>${icon(item.tone === "pink" ? "shirt" : item.tone === "blue" ? "sparkle" : "image")} ${escapeHtml(item.label)}</span>
      <strong>${escapeHtml(item.value)}</strong>
    </div>
  `;
}

function imageFor(key) {
  if (isImageUrl(key)) return String(key);
  return images[key] || images.normal;
}

function isImageUrl(value) {
  const raw = String(value || "").trim();
  return /^(https?:|data:|blob:|\/)/i.test(raw);
}

function renderVoicePage() {
  return `
    ${renderPageTitle(voicePage)}
    <section class="voice-lab-page">
      <div class="voice-top-grid">
        ${renderTtsCard()}
        ${renderAsrCard()}
        ${renderVoicePreviewCard()}
      </div>

      <div class="voice-action-row">
        <button class="voice-test-button" type="button" data-action-id="${CONTROL_CENTER_ACTIONS.voiceTest}">${icon("mic")} <span>测试语音<small>检测麦克风与识别效果</small></span></button>
        <button class="voice-stop-button" type="button" data-action-id="${CONTROL_CENTER_ACTIONS.voiceStop}">${icon("stop")} <span>停止语音<small>停止当前语音会话</small></span></button>
      </div>

      <div class="voice-bottom-grid">
        ${renderVoiceRecordsCard()}
        ${renderVoiceQueueCard()}
        ${renderVoiceProcessingCard()}
        ${renderVoiceDiagnosticsCard()}
      </div>

      <footer class="voice-footer-note">${icon("sparkle")} ${escapeHtml(voicePage.footer)}</footer>
    </section>
  `;
}

function renderTtsCard() {
  const ttsEnabled = Boolean(state.voice.ttsEnabled);
  const volumePercent = clampVoiceVolumePercent(state.voice.volumePercent);
  return `
    <article class="glass-card voice-config-card tts-card">
      <div class="voice-card-title">
        <div>
          <h2>${icon("volume")} ${escapeHtml(voicePage.tts.title)}</h2>
          <p>${escapeHtml(voicePage.tts.subtitle)}</p>
        </div>
        <button type="button" data-voice-toggle="ttsEnabled" aria-pressed="${ttsEnabled}">${ttsEnabled ? "朗读开启" : "朗读关闭"}</button>
      </div>
      ${renderVoiceProviderStatus(voicePage.tts.providerStatus, "朗读通道")}
      <div class="voice-form-grid">
        <label><span>${icon("user")} 选择音色</span><button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.voiceSelectTtsVoice}" data-payload-field="ttsVoice" data-payload-value="${escapeAttr(voicePage.tts.voice)}" data-action-unavailable="true" aria-disabled="true" title="音色选择将在后续版本开放">${icon("equalizer")} ${escapeHtml(voicePage.tts.voice)} ${icon("chevronDown")}</button></label>
        <label>
          <span>${icon("volume")} 输出音量</span>
          <div class="voice-stepper">
            <button type="button" data-voice-volume-step="-10" aria-label="降低输出音量">−</button>
            ${renderRangeBar(volumePercent)}
            <button type="button" data-voice-volume-step="10" aria-label="提高输出音量">＋</button>
          </div>
          <strong>${volumePercent}%</strong>
        </label>
        <label><span>${icon("clock")} 语速调节</span>${renderRangeBar(52)}<strong data-action-id="${CONTROL_CENTER_ACTIONS.voiceSetSpeed}" data-payload-field="speed" data-payload-value="${escapeAttr(voicePage.tts.speed)}">${escapeHtml(voicePage.tts.speed)}</strong></label>
      </div>
    </article>
  `;
}

function renderAsrCard() {
  const asrEnabled = Boolean(state.voice.asrEnabled);
  return `
    <article class="glass-card voice-config-card asr-card">
      <div class="voice-card-title">
        <div>
          <h2>${icon("mic")} ${escapeHtml(voicePage.asr.title)}</h2>
          <p>${escapeHtml(voicePage.asr.subtitle)}</p>
        </div>
        <button type="button" data-voice-toggle="asrEnabled" aria-pressed="${asrEnabled}">${asrEnabled ? "识别开启" : "识别关闭"}</button>
      </div>
      ${renderVoiceProviderStatus(voicePage.asr.providerStatus, "识别通道")}
      <div class="voice-form-grid">
        <label><span>${icon("mic")} 麦克风设备</span><button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.voiceSelectAsrDevice}" data-payload-field="asrDevice" data-payload-value="${escapeAttr(voicePage.asr.device)}" data-action-unavailable="true" aria-disabled="true" title="设备选择将在后续版本开放">${escapeHtml(voicePage.asr.device)} ${icon("chevronDown")}</button></label>
        <label><span>${icon("settings")} 识别语言</span><button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.voiceSetAsrLanguage}" data-payload-field="asrLanguage" data-payload-value="${escapeAttr(voicePage.asr.language)}" data-action-unavailable="true" aria-disabled="true" title="语言切换将在后续版本开放">${escapeHtml(voicePage.asr.language)} ${icon("chevronDown")}</button></label>
        <label><span>${icon("equalizer")} 输入灵敏度</span>${renderRangeBar(voicePage.asr.sensitivity)}<strong>${voicePage.asr.sensitivity}%</strong></label>
        <label><span>实时输入</span><em class="voice-live-wave">${Array.from({ length: 24 }, (_, index) => `<i style="--bar: ${((index * 7) % 24) + 8}px"></i>`).join("")}</em></label>
      </div>
    </article>
  `;
}

function renderVoiceProviderStatus(providerStatus, label) {
  if (!providerStatus || typeof providerStatus !== "object") return "";
  const statusTone = providerStatus.statusTone || "muted";
  const activeName = providerStatus.activeProviderName || "待确认";
  const requestedName = providerStatus.requestedProviderName || "";
  const degraded = Boolean(providerStatus.fallbackProviderId);
  const reason = providerStatus.reasonLabel || providerStatus.reason || "";
  const profile = providerStatus.voiceProfileId ? ` · ${providerStatus.voiceProfileId}` : "";
  const detail = degraded && requestedName
    ? `${reason || "已自动降级"} · 请求 ${requestedName}${profile}`
    : reason || "当前通道可用";
  return `
    <div class="voice-provider-status ${escapeAttr(statusTone)}">
      <span>${icon(statusTone === "good" ? "checkCircle" : "info")} ${escapeHtml(label)}</span>
      <strong>${escapeHtml(activeName)}</strong>
      <small>${escapeHtml(detail)}</small>
    </div>
  `;
}

function renderVoicePreviewCard() {
  const previewLines = voicePreviewTextLines();
  const previewText = previewLines.join("\n");
  return `
    <article class="glass-card voice-preview-card">
      <h2>${icon("equalizer")} ${escapeHtml(voicePage.preview.title)}</h2>
      <p>${escapeHtml(voicePage.preview.subtitle)}</p>
      <div class="voice-preview-body">
        <img src="${imageFor("happy")}" alt="" />
        <div class="speech-bubble">
          ${previewLines.map((line) => `<span>${escapeHtml(line)}</span>`).join("")}
        </div>
      </div>
      <div class="voice-preview-player">
        <button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.voicePreviewPlay}" data-payload-text="${escapeAttr(previewText)}" data-payload-value="${escapeAttr(previewText)}" aria-label="播放试听">${icon("play")}</button>
        <span>${Array.from({ length: 28 }, (_, index) => `<i style="--bar: ${((index * 5) % 28) + 8}px"></i>`).join("")}</span>
        <time>${escapeHtml(voicePage.preview.duration)}</time>
      </div>
    </article>
  `;
}

function renderVoiceRecordsCard() {
  return `
    <article class="glass-card voice-list-card">
      <div class="card-heading"><h2>${icon("clock")} 识别记录</h2><button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.voiceRecordsClear}" data-payload-field="records">清空记录</button></div>
      <p>最近识别到的语音内容</p>
      <div class="voice-record-list">
        ${voicePage.records.map((item) => `
          <div><strong>${escapeHtml(item.text)}</strong><time>${escapeHtml(item.time)}</time><span>${escapeHtml(item.score)}</span></div>
        `).join("")}
      </div>
    </article>
  `;
}

function renderVoiceQueueCard() {
  return `
    <article class="glass-card voice-list-card">
      <div class="card-heading"><h2>${icon("equalizer")} 合成队列 / 最近朗读</h2><button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.voiceQueueClear}" data-payload-field="queue">清空队列</button></div>
      <p>Akane 最近为你朗读的内容</p>
      <div class="voice-queue-list">
        ${voicePage.queue.map((item, index) => `
          <div><span>${index + 1}</span><strong>${escapeHtml(item.text)}</strong><time>${escapeHtml(item.duration)}</time></div>
        `).join("")}
      </div>
    </article>
  `;
}

function renderVoiceProcessingCard() {
  return `
    <article class="glass-card voice-processing-card">
      <h2>${icon("equalizer")} 语音处理</h2>
      <div class="processing-list">
        ${voicePage.processing.map((item) => `
          <div>
            <span>${icon("plusCircle")}</span>
            <strong>${escapeHtml(item.label)}<small>${escapeHtml(item.detail)}</small></strong>
            ${renderStaticSwitch(item.enabled)}
          </div>
        `).join("")}
      </div>
      <label><span>唤醒词设置</span><button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.voiceSetWakeWord}" data-payload-field="wakeWord" data-payload-value="${escapeAttr(voicePage.wakeWord || "Akane")}">${escapeHtml(voicePage.wakeWord || "Akane")}</button></label>
      <label><span>唤醒灵敏度</span><button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.voiceSetWakeSensitivity}" data-payload-field="wakeSensitivity" data-payload-value="${escapeAttr(voicePage.wakeSensitivity || "中等")}">${escapeHtml(voicePage.wakeSensitivity || "中等")}（推荐） ${icon("chevronDown")}</button></label>
    </article>
  `;
}

function renderVoiceDiagnosticsCard() {
  return `
    <article class="glass-card voice-diagnostics-card">
      <div class="card-heading">
        <h2>${icon("shield")} 语音服务状态</h2>
        <span>${icon("checkCircle")} 一切正常</span>
      </div>
      <p>实时检测语音服务状态</p>
      <div class="voice-diagnostic-list">
        ${voicePage.diagnostics.map((item) => `
          <div><span>${escapeHtml(item.label)}</span><strong>${escapeHtml(item.value)}</strong><b>${icon("checkCircle")} 正常</b></div>
        `).join("")}
      </div>
      <footer>语音服务运行良好，陪伴随时在线</footer>
    </article>
  `;
}

function renderRangeBar(value) {
  return `<i class="range-visual"><b style="width: ${Number(value) || 0}%"></b><span style="left: ${Number(value) || 0}%"></span></i>`;
}

function renderMusicPage() {
  const outputDevice = musicPage.outputDevice || "扬声器";
  const volumeNormalization = musicPage.volumeNormalization !== false;
  return `
    <section class="music-lab-page">
      <header class="music-title-row">
        <div>
          <h1>${escapeHtml(musicPage.title)} <span>${escapeHtml(musicPage.accent)}</span></h1>
          <p>${escapeHtml(musicPage.subtitle)}</p>
        </div>
        <div class="music-equalizer" aria-hidden="true">
          ${Array.from({ length: 34 }, (_, index) => `<i style="--bar: ${((index * 7) % 24) + 8}px"></i>`).join("")}
        </div>
      </header>

      <div class="music-top-grid">
        <article class="glass-card now-playing-panel">
          <h2>${icon("equalizer")} 播放配置</h2>
          ${renderSystemMediaStatus(musicPage)}
          <div class="play-mode-row">
            <span>${icon("repeat")} 播放模式</span>
            <button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.musicSetPlayMode}" data-payload-field="playMode" data-payload-value="${escapeAttr(musicPage.currentPlayMode || "列表循环")}">${icon("repeat")} ${escapeHtml(musicPage.currentPlayMode || "列表循环")} ${icon("chevronDown")}</button>
          </div>
        </article>

        <article class="glass-card lyric-panel">
          <div class="card-heading">
            <h2>${icon("file")} 歌词</h2>
            <span>面板负责播放控制</span>
          </div>
          <div class="lyric-lines">
            ${musicPage.lyrics.map((line, index) => `
              <p class="${index === musicPage.activeLyric ? "active" : ""}">
                ${index === musicPage.activeLyric ? icon("equalizer") : ""}
                ${escapeHtml(line)}
              </p>
            `).join("")}
          </div>
        </article>

        <article class="glass-card queue-panel">
          <div class="card-heading">
            <h2>${icon("music")} 播放队列</h2>
            <span>${musicPage.playlist.length} 首</span>
          </div>
          <div class="queue-list">
            ${musicPage.playlist.map((item, index) => renderQueueItem(item, index)).join("")}
          </div>
        </article>
      </div>

      <div class="music-bottom-grid">
        <article class="glass-card play-info-panel">
          <h2>${icon("music")} 播放信息</h2>
          ${musicPage.info.map((item) => `
            <div><span>${escapeHtml(item.label)}</span><strong>${escapeHtml(item.value)}</strong></div>
          `).join("")}
          <div class="mini-wave" aria-hidden="true">${Array.from({ length: 42 }, (_, index) => `<i style="--bar: ${((index * 5) % 18) + 4}px"></i>`).join("")}</div>
        </article>
        <article class="glass-card mood-panel">
          <h2>${icon("star")} 音乐心情</h2>
          <p>选择心情，Akane 为你匹配氛围音乐</p>
          <div class="mood-grid">
            ${musicPage.modes.map((mode) => `
              <button class="${mode === state.activeMusicMode ? "active" : ""}" data-music-mode="${escapeAttr(mode)}" type="button" data-action-unavailable="true" aria-disabled="true" disabled title="暂未接入真实音乐推荐">
                ${renderMoodIcon(mode)} ${escapeHtml(mode)}
              </button>
            `).join("")}
          </div>
        </article>
        <article class="glass-card recommend-panel">
          <div class="card-heading">
            <h2>${icon("sparkle")} Akane 推荐</h2>
            <button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.musicRefreshRecommendations}" data-payload-source="recommendations" data-action-unavailable="true" aria-disabled="true" title="推荐功能将在后续版本开放">${icon("refresh")} 换一批</button>
          </div>
          ${renderRecommendBody(musicPage)}
        </article>
      </div>

      <footer class="music-bottom-bar">
        <span>${icon("refresh")} ${escapeHtml(musicPage.bottomStatus)} ✦</span>
        <div>
          <b>音量均衡</b>
          <button class="tiny-switch ${volumeNormalization ? "is-on" : ""}" type="button" data-action-id="${CONTROL_CENTER_ACTIONS.musicSetVolumeNormalization}" data-payload-field="volumeNormalization" data-payload-value="${!volumeNormalization}" aria-label="音量均衡"><span></span></button>
          <strong>${icon("volume")} 设备输出：${escapeHtml(outputDevice)}</strong>
        </div>
      </footer>
    </section>
  `;
}

function renderSystemMediaStatus(musicPage) {
  const systemMedia = musicPage?.systemMedia && typeof musicPage.systemMedia === "object" ? musicPage.systemMedia : null;
  if (!systemMedia) return "";
  const status = systemMedia.statusLabel || "Unavailable";
  const lyrics = systemMedia.lyrics?.statusDetail || systemMedia.lyrics?.statusLabel || "Unavailable";
  const source = systemMedia.sourceApp ? ` · ${systemMedia.sourceApp}` : "";
  const timing = systemMedia.durationSeconds > 0
    ? ` · ${formatMusicSeconds(systemMedia.positionSeconds)} / ${formatMusicSeconds(systemMedia.durationSeconds)}`
    : "";
  return `
    <div class="system-media-status">
      <span>${icon("equalizer")} System music: ${escapeHtml(status)}</span>
      <strong>${escapeHtml(lyrics)}</strong>
      <small>${escapeHtml(`${systemMedia.title || "No system track"}${source}${timing}`)}</small>
    </div>
  `;
}

function formatMusicSeconds(seconds) {
  const value = Math.max(0, Math.round(Number(seconds) || 0));
  const minutes = Math.floor(value / 60);
  const secs = value % 60;
  return `${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

function renderRecommendBody(musicPage) {
  const recs = Array.isArray(musicPage.recommendations) ? musicPage.recommendations : [];
  if (!recs.length) {
    return `
      <div class="recommend-body recommend-body--empty">
        <img src="${imageFor(musicPage.nowPlaying.cover || "music")}" alt="" />
        <div class="recommend-empty-state">
          <p>暂无推荐</p>
          <small>播放音乐后将自动生成推荐列表</small>
        </div>
      </div>
    `;
  }
  return `
    <div class="recommend-body">
      <img src="${imageFor(recs[0]?.cover || musicPage.nowPlaying.cover)}" alt="" />
      <div>
        ${recs.map((item) => {
          const clickable = item.playable && item.handle;
          const itemType = item.itemType === "generated" ? "generated" : "attachment";
          return `
            <div class="recommend-row">
              ${clickable
                ? `<button class="recommend-play" type="button" data-action-id="${CONTROL_CENTER_ACTIONS.musicPlayWorkspaceRecommendation}" data-payload-item-type="${escapeAttr(itemType)}" data-payload-handle="${escapeAttr(item.handle)}" data-payload-title="${escapeAttr(item.title)}" aria-label="播放 ${escapeAttr(item.title)}">${icon("play")}</button>`
                : `<span class="recommend-play-icon">${icon("play")}</span>`}
              <strong>${escapeHtml(item.title)}</strong>
              <small>${escapeHtml(item.reason || item.artist || "队列推荐")}</small>
              <time>${escapeHtml(item.duration || item.durationLabel || "")}</time>
            </div>
          `;
        }).join("")}
      </div>
    </div>
  `;
}

function renderQueueItem(item, index) {
  const trackId = item.id || item.title || `queue_${index + 1}`;
  return `
    <button class="queue-item ${item.active ? "active" : ""}" type="button" data-action-id="${CONTROL_CENTER_ACTIONS.musicSelectQueueItem}" data-payload-value="${escapeAttr(trackId)}" data-payload-track-id="${escapeAttr(trackId)}" data-payload-index="${index}" data-track-id="${escapeAttr(trackId)}" data-track-index="${index}">
      <span class="queue-cover"><img src="${imageFor(item.cover || musicPage.nowPlaying.cover)}" alt="" /></span>
      <span>
        <strong>${escapeHtml(item.title)}</strong>
        <small>${escapeHtml(item.artist)}</small>
      </span>
      <time>${escapeHtml(item.duration)}</time>
      ${item.active ? icon("equalizer") : icon("menu")}
    </button>
  `;
}

function renderMoodIcon(mode) {
  const iconMap = {
    放松: "leaf",
    专注: "target",
    治愈: "leaf",
    活力: "sun",
    思念: "heart",
    睡前: "moon"
  };
  return icon(iconMap[mode] || "sparkle");
}

function renderPerceptionPage() {
  return `
    ${renderPageTitle(perceptionPage, `<button class="privacy-help" type="button" data-action-id="${CONTROL_CENTER_ACTIONS.perceptionPrivacyHelp}">${icon("help")} ${escapeHtml(perceptionPage.helpLabel)}</button>`)}
    <section class="perception-page">
      <div class="perception-feature-grid">
        ${perceptionPage.featureCards.map(renderPerceptionFeatureCard).join("")}
      </div>
      <div class="perception-info-row">
        ${renderPrivacyCard()}
        ${renderPermissionCard()}
      </div>
      <div class="perception-bottom-grid">
        ${renderSenseStatusCard()}
        ${renderSenseNoteCard()}
        ${renderDiagnosticCard()}
      </div>
    </section>
  `;
}

function renderAbilitiesPage() {
  return `
    ${renderPageTitle(abilitiesPage)}
    <section class="abilities-page">
      <div class="ability-top-grid">
        <article class="ability-portrait-card">
          <img src="${images.normal}" alt="" />
        </article>
        <article class="glass-card ability-overview-card">
          <h2>能力概览 ${icon("info")}</h2>
          <div class="ability-stats">
            ${abilitiesPage.overview.stats.map(renderAbilityStat).join("")}
          </div>
          <div class="availability-row">
            <span>整体可用性</span>
            <strong>${abilitiesPage.overview.availability}%</strong>
          </div>
          <div class="availability-bar">
            <span style="width: ${abilitiesPage.overview.availability}%"></span>
          </div>
          <p>${icon("check")} ${escapeHtml(abilitiesPage.overview.note)}</p>
        </article>
        <article class="glass-card quick-actions-card">
          <h2>快捷操作</h2>
          <p>常用能力一键直达</p>
          <div class="quick-action-grid">
            ${abilitiesPage.quickActions.map((item, index) => renderQuickAction(item, index)).join("")}
          </div>
        </article>
      </div>
      <div class="ability-body-grid">
        <div class="ability-left-stack">
          ${renderProductizationPanel()}
          <article class="glass-card modules-card">
            <div class="card-heading">
              <h2>能力模块 ${icon("info")}</h2>
              <button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.abilitiesManageModules}">管理模块与权限 ${icon("chevron")}</button>
            </div>
            <div class="module-grid">
              ${abilitiesPage.modules.map(renderAbilityModule).join("")}
            </div>
          </article>
          ${renderProviderPanel()}
          ${renderQqPanel()}
          ${renderMcpPanel()}
          <article class="glass-card workflow-card">
            <h2>本地工作流 ${icon("info")}</h2>
            <div class="workflow-list">
              ${abilitiesPage.workflows.map(renderWorkflow).join("")}
              <button class="more-workflow" type="button" data-action-id="${CONTROL_CENTER_ACTIONS.abilitiesMoreWorkflows}">更多工作流 ${icon("chevron")}</button>
            </div>
          </article>
          <article class="glass-card calls-card">
            <div class="card-heading">
              <h2>最近能力调用</h2>
              <button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.abilitiesLogsViewAll}">${state.showAllAbilityCalls ? "收起日志" : "查看全部日志"} ${icon("chevron")}</button>
            </div>
            ${renderCallsTable()}
          </article>
        </div>
        <aside class="ability-right-stack">
          ${renderSafetyPanel()}
          ${renderLive2dPanel()}
        </aside>
      </div>
    </section>
  `;
}

function renderProductizationPanel() {
  const items = Array.isArray(abilitiesPage.productization) ? abilitiesPage.productization : [];
  if (!items.length) return "";
  return `
    <article class="glass-card productization-card">
      <div class="card-heading">
        <h2>${icon("checkCircle")} 产品化状态</h2>
        <span>开源前验收</span>
      </div>
      <p class="productization-note">这些不是要隐藏的功能，而是已经做过、正在补成可配置可诊断产品形态的能力。</p>
      <div class="productization-grid">
        ${items.map(renderProductizationItem).join("")}
      </div>
    </article>
  `;
}

function renderProductizationItem(item) {
  const tone = item.tone || productizationTone(item.status);
  return `
    <section class="productization-item ${escapeAttr(tone)}">
      <header>
        <strong>${escapeHtml(item.title || "能力")}</strong>
        <span>${escapeHtml(item.status || "待确认")}</span>
      </header>
      <p>${escapeHtml(item.description || "")}</p>
      <dl>
        <div><dt>配置</dt><dd>${escapeHtml(item.configure || "待补入口")}</dd></div>
        <div><dt>验证</dt><dd>${escapeHtml(item.verify || "待补自检")}</dd></div>
        <div><dt>依赖</dt><dd>${escapeHtml(item.dependency || "无额外依赖")}</dd></div>
      </dl>
    </section>
  `;
}

function productizationTone(status) {
  const value = String(status || "").toLowerCase();
  if (value.includes("ready")) return "green";
  if (value.includes("alpha")) return "blue";
  if (value.includes("gap") || value.includes("待") || value.includes("productization")) return "orange";
  return "muted";
}

function renderAdvancedPage() {
  return `
    ${renderPageTitle(advancedPage, renderAdvancedSystemStrip())}
    <section class="advanced-lab-page">
      <div class="advanced-main-grid">
        <div class="advanced-left-stack">
          <article class="glass-card advanced-core-card">
            <h2>${icon("cube")} 核心渲染与交互设置</h2>
            <div class="advanced-toggle-list">
              ${advancedPage.coreSettings.map(renderAdvancedCoreItem).join("")}
            </div>
          </article>

          <article class="glass-card advanced-run-card">
            <h2>${icon("zap")} 运行操作</h2>
            <div class="advanced-operation-list">
              ${advancedPage.operations.map(renderAdvancedOperation).join("")}
            </div>
          </article>
        </div>

        <article class="glass-card advanced-diagnostics-card">
          <h2>${icon("stethoscope")} 诊断信息</h2>
          <div class="advanced-metric-grid">
            ${advancedPage.diagnostics.metrics.map(renderAdvancedMetric).join("")}
          </div>
          <section class="advanced-log-panel">
            <div class="card-heading">
              <h3>运行日志 <span>最近 20 条</span></h3>
              <button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.advancedLogsClear}">${icon("trash")} 清空日志</button>
            </div>
            <div class="advanced-log-list">
              ${renderAdvancedLogs()}
            </div>
            <button class="advanced-more-log" type="button" data-action-id="${CONTROL_CENTER_ACTIONS.advancedLogsMore}">${state.showAllDiagnosticLogs ? "收起日志" : "查看更多日志"} ${icon("chevronDown")}</button>
          </section>
        </article>

        <div class="advanced-right-stack">
          <article class="glass-card advanced-live2d-card" data-action-id="${CONTROL_CENTER_ACTIONS.advancedLive2dOpenStatus}">
            <h2>${icon("star")} Live2D 预留状态</h2>
            <div class="advanced-live2d-body">
              <div class="advanced-live2d-ring">
                <img src="${imageFor(advancedPage.live2d.image)}" alt="" />
              </div>
              <div class="advanced-live2d-list">
                ${advancedPage.live2d.rows.map(renderAdvancedLive2dRow).join("")}
              </div>
            </div>
          </article>

          <article class="glass-card advanced-ability-card">
            <h2>${icon("star")} 能力概览</h2>
            <div class="advanced-ability-grid">
              ${advancedPage.abilityOverview.map((item, index) => renderAdvancedAbility(item, index)).join("")}
            </div>
          </article>
        </div>
      </div>

      <article class="glass-card advanced-expert-card">
        <h2>${icon("sparkle")} 专家选项</h2>
        <div class="advanced-expert-grid">
          ${advancedPage.expertOptions.map((item, index) => renderExpertOption(item, index)).join("")}
        </div>
        <p>${icon("star")} ${escapeHtml(advancedPage.expertNote)}</p>
      </article>
    </section>
  `;
}

function renderAdvancedSystemStrip() {
  return `
    <div class="advanced-system-strip">
      ${advancedPage.systemStrip.map((item) => `
        <span class="${escapeAttr(item.tone)}">
          ${icon(item.icon)}
          <b>${escapeHtml(item.label)}</b>
          ${item.value ? `<strong>${escapeHtml(item.value)}</strong>` : ""}
        </span>
      `).join("")}
    </div>
  `;
}

function renderAdvancedCoreItem(item) {
  const isOn = Boolean(state.advancedCoreSwitches[item.id] ?? item.enabled);
  return `
    <button
      class="advanced-toggle-item ${escapeAttr(item.tone)}"
      data-advanced-core-toggle="${escapeAttr(item.id)}"
      type="button"
      aria-pressed="${isOn}"
    >
      <span class="advanced-item-icon">${icon(item.icon)}</span>
      <span>
        <strong>${escapeHtml(item.title)}</strong>
        <small>${escapeHtml(item.description)}</small>
      </span>
      ${renderStaticSwitch(isOn)}
    </button>
  `;
}

function renderAdvancedOperation(item) {
  const actionAttrs = item.actionId
    ? ` data-action-id="${escapeAttr(item.actionId)}"${item.actionId === CONTROL_CENTER_ACTIONS.advancedExitPet ? ' data-payload-requires-confirmation="true"' : ""}`
    : "";
  return `
    <div class="advanced-operation ${escapeAttr(item.tone)}">
      <span class="advanced-item-icon">${icon(item.icon)}</span>
      <span>
        <strong>${escapeHtml(item.title)}</strong>
        <small>${escapeHtml(item.description)}</small>
      </span>
      <button type="button"${actionAttrs}>${escapeHtml(item.action)}</button>
    </div>
  `;
}

function renderAdvancedMetric(item) {
  return `
    <div class="advanced-metric ${escapeAttr(item.tone)} ${item.spark ? "has-spark" : ""}">
      <span class="advanced-item-icon">${icon(item.icon)}</span>
      <span>
        <small>${escapeHtml(item.label)}</small>
        <strong>${escapeHtml(item.value)}</strong>
      </span>
      ${item.spark ? `<i>${Array.from({ length: 12 }, (_, index) => `<b style="--bar:${((index * 7) % 20) + 8}px"></b>`).join("")}</i>` : ""}
    </div>
  `;
}

function renderAdvancedLog(item) {
  return `
    <div>
      <time>${escapeHtml(item.time)}</time>
      <span>${escapeHtml(item.level)}</span>
      <strong>${escapeHtml(item.message)}</strong>
    </div>
  `;
}

function renderAdvancedLogs() {
  const allLogs = Array.isArray(advancedPage.diagnostics.logs) ? advancedPage.diagnostics.logs : [];
  const visibleLogs = state.showAllDiagnosticLogs ? allLogs : allLogs.slice(0, 5);
  return visibleLogs.map(renderAdvancedLog).join("");
}

function renderAdvancedLive2dRow(row) {
  return `
    <div>
      <span>${escapeHtml(row.label)}：</span>
      <strong>${icon("checkCircle")} ${escapeHtml(row.value)}</strong>
    </div>
  `;
}

function renderAdvancedAbility(item, index) {
  return `
    <button class="${escapeAttr(item.tone)}" type="button" data-action-id="${CONTROL_CENTER_ACTIONS.advancedAbilityDetails}" data-payload-field="label" data-payload-value="${escapeAttr(item.label)}" data-payload-index="${index}">
      ${icon(item.icon)}
      <span>${escapeHtml(item.label)}</span>
    </button>
  `;
}

function renderExpertOption(item, index) {
  const optionId = item.id || item.title || `expert_${index + 1}`;
  const nextValue = !Boolean(item.enabled);
  return `
    <div class="advanced-expert-option" data-action-id="${CONTROL_CENTER_ACTIONS.advancedExpertOption}" data-payload-field="enabled" data-payload-value="${nextValue}" data-payload-option-id="${escapeAttr(optionId)}" data-payload-index="${index}">
      <span class="advanced-item-icon">${icon(item.icon)}</span>
      <span>
        <strong>${escapeHtml(item.title)}</strong>
        <small>${escapeHtml(item.description)}</small>
      </span>
      ${renderStaticSwitch(item.enabled)}
    </div>
  `;
}

function renderPerceptionFeatureCard(card) {
  const isOn = Boolean(state.switches[card.id]);
  return `
    <article class="glass-card feature-card">
      <header>
        <div>
          <h2>${icon(card.icon)} ${escapeHtml(card.title)}</h2>
          <p>${escapeHtml(card.description)}</p>
        </div>
        ${renderSwitch(card.id, isOn)}
      </header>
      ${renderFeaturePreview(card)}
    </article>
  `;
}

function renderFeaturePreview(card) {
  if (card.previewType === "window") {
    return `
      <div class="feature-preview window-preview">
        <strong>${escapeHtml(card.label)}</strong>
        <div class="window-row">
          <div class="mini-code-window">
            <span></span><span></span><span></span><i></i><i></i><i></i>
          </div>
          <div>
            <b>${escapeHtml(card.appName)}</b>
            <small>${escapeHtml(card.appDetail)}</small>
            <small>${escapeHtml(card.version)}</small>
          </div>
        </div>
      </div>
    `;
  }

  if (card.previewType === "code") {
    return `
      <div class="feature-preview clipboard-preview">
        <strong>${escapeHtml(card.label)}</strong>
        <pre>${card.code.map(escapeHtml).join("\n")}</pre>
        <div class="preview-footer">
          <span>${escapeHtml(card.source)}</span>
        </div>
      </div>
    `;
  }

  if (card.previewType === "settings") {
    const intervalSec = state.screenVision.intervalSec || secondsFromIntervalLabel(card.frequency);
    const frameCount = state.screenVision.frameCount || parsePositiveInteger(card.frames, SCREEN_VISION_FRAME_COUNT_MIN);
    return `
      <div class="feature-preview settings-preview">
        <strong>${escapeHtml(card.label)}</strong>
        <label>
          <span>截图间隔</span>
          <button type="button" data-screen-vision-interval-step="1">${escapeHtml(`${intervalSec} 秒`)} ${icon("chevronDown")}</button>
        </label>
        <label>
          <span>保留帧数</span>
          <div class="stepper">
            <button type="button" data-screen-vision-frame-step="-1">−</button>
            <b>${escapeHtml(frameCount)}</b>
            <button type="button" data-screen-vision-frame-step="1">＋</button>
          </div>
          <small>${escapeHtml(card.hint)}</small>
        </label>
        <label>
          <span>观察记录</span>
          <button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.perceptionScreenVisionClear}">清空记录</button>
        </label>
        <p>${icon("shield")} ${escapeHtml(card.note)}</p>
      </div>
    `;
  }

  return `
    <div class="feature-preview interval-preview">
      <strong>${escapeHtml(card.label)}</strong>
      <div class="interval-grid">
        ${card.options.map((option) => `
          <button
            class="${option === state.activeInterval ? "active" : ""}"
            data-interval="${escapeAttr(option)}"
            type="button"
          >${escapeHtml(option)}</button>
        `).join("")}
      </div>
      <p>${icon("heart")} ${escapeHtml(card.note)}</p>
    </div>
  `;
}

function renderPrivacyCard() {
  return `
    <article class="glass-card privacy-card">
      <div class="privacy-shield">${icon("lock")}</div>
      <div>
        <h2>隐私与安全说明</h2>
        <ul>
          ${perceptionPage.privacy.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
        </ul>
        <button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.perceptionPrivacyHelp}">了解更多隐私保护细节 ${icon("arrowRight")}</button>
      </div>
    </article>
  `;
}

function renderPermissionCard() {
  const cards = perceptionPage.featureCards || [];
  const screen = cards.find((c) => c.id === "screen");
  const clipboard = cards.find((c) => c.id === "clipboard");
  const items = [
    { label: "屏幕捕获", status: screen?.enabled ? "已启用" : "未启用", icon: "shield", tone: screen?.enabled ? "good" : "info" },
    { label: "剪贴板", status: clipboard?.enabled ? "已启用" : "未启用", icon: "clipboard", tone: clipboard?.enabled ? "good" : "info" },
    { label: "麦克风", status: "需系统授权", icon: "mic", tone: "warn", fixed: true },
    { label: "文件访问", status: "按需工作", icon: "folder", tone: "caution", fixed: true }
  ];
  return `
    <article class="glass-card permission-card">
      <div class="card-heading">
        <h2>感知边界</h2>
      </div>
      <div class="permission-grid">
        ${items.map((item) => `
          <div class="permission-tile ${item.tone}">
            ${icon(item.icon)}
            <span>${escapeHtml(item.label)}</span>
            <strong>${icon("checkCircle")} ${escapeHtml(item.status)}</strong>
          </div>
        `).join("")}
      </div>
    </article>
  `;
}

function renderSenseStatusCard() {
  const cards = perceptionPage.featureCards || [];
  const makeItem = (id, icon, label, getStatus) => {
    const card = cards.find((c) => c.id === id);
    return { icon, label, status: getStatus(card) };
  };
  const statusItems = [
    makeItem("activeWindow", "lock", "前台窗口感知", (c) =>
      c?.enabled ? "已启用" : "已关闭"
    ),
    makeItem("clipboard", "clipboard", "剪贴板文本", (c) =>
      c?.enabled ? "已启用" : "已关闭"
    ),
    makeItem("screen", "eye", "屏幕捕获", (c) => {
      if (!c?.enabled) return "已关闭";
      const parts = ["已启用"];
      if (c.frequency) parts.push(`间隔 ${c.frequency}`);
      if (c.frames) parts.push(`保留 ${c.frames} 帧`);
      return parts.join(" · ");
    }),
    makeItem("proactive", "chat", "主动搭话", (c) => {
      if (!c?.enabled) return "已关闭";
      const parts = ["已启用"];
      if (c.activeOption) parts.push(`间隔 ${c.activeOption}`);
      return parts.join(" · ");
    })
  ];
  return `
    <article class="glass-card sense-status-card">
      <div class="card-heading">
        <h2>感知运行状态</h2>
      </div>
      <div class="sense-status-list">
        ${statusItems.map((item) => `
          <div class="sense-status-row">
            <span class="sense-status-icon">${icon(item.icon)}</span>
            <span class="sense-status-label">${escapeHtml(item.label)}</span>
            <strong class="sense-status-value">${escapeHtml(item.status)}</strong>
          </div>
        `).join("")}
      </div>
    </article>
  `;
}

function renderSenseNoteCard() {
  const cards = perceptionPage.featureCards || [];
  const aw = cards.find((c) => c.id === "activeWindow");
  const cb = cards.find((c) => c.id === "clipboard");
  const sc = cards.find((c) => c.id === "screen");
  const pr = cards.find((c) => c.id === "proactive");
  const items = [];
  if (aw?.enabled) {
    items.push("前台窗口感知已开启，Akane 会在发送消息时参考当前窗口上下文。");
  }
  if (cb?.enabled) {
    items.push("剪贴板感知已开启，Akane 只会在发送消息时临时参考复制内容，正文不在控制中心展示。");
  }
  if (sc?.enabled) {
    items.push("屏幕捕获已开启，Akane 可以按设置保留最近画面作为上下文。");
  } else {
    items.push("屏幕捕获已关闭，Akane 不会主动查看你的屏幕画面。");
  }
  if (pr?.enabled) {
    const detail = pr.activeOption ? `（当前间隔 ${pr.activeOption}）` : "";
    items.push(`主动搭话已开启，Akane 会按设定间隔轻声提醒你${detail}。`);
  }
  const anyEnabled = Boolean(aw?.enabled || cb?.enabled || sc?.enabled || pr?.enabled);
  if (!anyEnabled) {
    items.length = 0;
    items.push("所有桌面感知能力均已关闭，Akane 目前只根据对话内容陪伴你。");
  }
  return `
    <article class="glass-card sense-note-card">
      <div class="card-heading">
        <h2>${icon("sparkle")} Akane 的感知小记</h2>
      </div>
      <div class="sense-note-body">
        <div>
          ${items.map((text) => `<p>${icon("heart")} ${escapeHtml(text)}</p>`).join("")}
        </div>
        <img src="${images.thinking}" alt="" />
      </div>
    </article>
  `;
}

function renderDiagnosticCard() {
  return `
    <article class="glass-card diagnostic-card">
      <h2>感知诊断</h2>
      <div class="diagnostic-list">
        ${perceptionPage.diagnostics.map((item) => `
          <div>
            <span>${icon("plusCircle")} ${escapeHtml(item.label)}</span>
            <strong class="${item.tone}">${escapeHtml(item.value)}</strong>
            <small>${escapeHtml(item.detail)}</small>
          </div>
        `).join("")}
      </div>
      <button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.perceptionRunDiagnostics}">${icon("refresh")} 运行诊断</button>
    </article>
  `;
}

function renderAbilityStat(item) {
  return `
    <div>
      <strong>${escapeHtml(item.value)}</strong>
      <span>${escapeHtml(item.label)}</span>
    </div>
  `;
}

function renderQuickAction(item, index) {
  const actionId = item.actionId || CONTROL_CENTER_ACTIONS.abilitiesQuickAction;
  return `
    <button class="quick-action ${item.tone}" type="button" data-action-id="${actionId}" data-payload-field="label" data-payload-value="${escapeAttr(item.label)}" data-payload-index="${index}">
      ${icon(item.icon)}
      <span>${escapeHtml(item.label)}</span>
    </button>
  `;
}

function renderAbilityModule(item) {
  const statusLabel = item.statusLabel || "可用";
  const statusTone = item.statusTone || "ready";
  return `
    <article class="module-tile ${item.tone}">
      <div class="module-icon">${icon(item.icon)}</div>
      <div>
        <div class="module-title-row">
          <h3>${escapeHtml(item.title)}</h3>
          <span class="module-status ${escapeAttr(statusTone)}">${escapeHtml(statusLabel)}</span>
        </div>
        <p>${escapeHtml(item.description)}</p>
      </div>
      <footer>
        <span>${icon("circle")} 权限：${escapeHtml(item.permission)}</span>
        <strong>${escapeHtml(item.count)}</strong>
      </footer>
    </article>
  `;
}

function renderProviderPanel() {
  const providers = Array.isArray(abilitiesPage.providers) ? abilitiesPage.providers : [];
  if (!providers.length) return "";
  return `
    <article class="glass-card provider-panel">
      <div class="card-heading">
        <h2>${icon("cube")} 本地能力环境</h2>
        <span>${providers.filter((item) => item.status === "ready").length}/${providers.length} 可用</span>
      </div>
      <div class="provider-list">
        ${providers.map(renderProviderRow).join("")}
      </div>
    </article>
  `;
}

function renderQqPanel() {
  const qq = abilitiesPage.qqStatus || {};
  const enabled = Boolean(qq.enabled);
  const onebotUrl = String(qq.onebotHttpUrl || qq.onebot_http_url || "http://127.0.0.1:3001");
  const botQq = String(qq.botQq || qq.bot_qq || "");
  const selfCheckResult = state.qqSelfCheckResult || null;
  const selfCheckStatus = String(selfCheckResult?.status || "");
  const selfCheckReason = String(selfCheckResult?.reason || "");
  const selfCheckNickname = String(selfCheckResult?.payload?.nickname || selfCheckResult?.nickname || "");
  const selfCheckBotQq = String(selfCheckResult?.payload?.botQq || selfCheckResult?.bot_qq || "");
  const statusTone = !enabled ? "warning"
    : selfCheckStatus === "connected" ? "good"
    : selfCheckStatus ? "danger"
    : "warning";
  const statusLabel = !enabled ? "未启用"
    : selfCheckStatus === "connected" ? "已连接"
    : selfCheckStatus === "bridge_disabled" ? "Bridge 未启用"
    : selfCheckStatus === "unreachable" ? "端口不可达"
    : selfCheckStatus === "auth_failed" ? "鉴权失败"
    : selfCheckStatus === "timeout" ? "连接超时"
    : selfCheckStatus ? "连接失败"
    : "待自检";
  const selfCheckActionAttr = ` data-action-id="${CONTROL_CENTER_ACTIONS.abilitiesQqSelfCheck}"`;
  return `
    <article class="glass-card qq-panel">
      <div class="card-heading">
        <h2>${icon("message")} QQ / NapCat</h2>
        <span class="module-status ${escapeAttr(statusTone)}">${escapeHtml(statusLabel)}</span>
      </div>
      <div class="qq-self-check">
        <div class="qq-status-row">
          <span>${icon("wifi")} OneBot 地址</span>
          <strong>${escapeHtml(onebotUrl)}</strong>
        </div>
        <div class="qq-status-row">
          <span>${icon("settings")} Bridge 状态</span>
          <strong>${enabled ? "已启用" : "未启用（QQ_BRIDGE_ENABLED=false）"}</strong>
        </div>
        ${botQq || selfCheckBotQq ? `
        <div class="qq-status-row">
          <span>${icon("mic")} Bot QQ</span>
          <strong>${escapeHtml(selfCheckBotQq || botQq)}${selfCheckNickname ? ` · ${escapeHtml(selfCheckNickname)}` : ""}</strong>
        </div>` : ""}
        ${selfCheckReason ? `
        <p class="qq-self-check-reason ${escapeAttr(selfCheckStatus === "connected" ? "good" : "warn")}">
          ${icon(selfCheckStatus === "connected" ? "checkCircle" : "alert")} ${escapeHtml(selfCheckReason)}
        </p>` : ""}
        <div class="qq-self-check-actions">
          <button
            type="button"
            ${selfCheckActionAttr}
          >${icon("search")} 执行自检</button>
          ${selfCheckStatus === "connected" ? `
          <div class="qq-check-items">
            <span class="qq-check-item good">${icon("checkCircle")} Bridge 已启用</span>
            <span class="qq-check-item good">${icon("checkCircle")} 端口可达</span>
            <span class="qq-check-item good">${icon("checkCircle")} 登录信息</span>
            <span class="qq-check-item warn">${icon("info")} 发送测试：未执行</span>
          </div>` : ""}
        </div>
        <p class="qq-external-note">${icon("shield")} Akane 不内置 NapCat。需要用户在本机自行部署 NapCat / 其他 OneBot v11 兼容服务，并在 .env 配置 QQ_BRIDGE_ENABLED=true 和 QQ_ONEBOT_HTTP_URL。</p>
      </div>
    </article>
  `;
}

function renderMcpPanel() {
  const servers = buildVisibleMcpServers();
  const readyCount = servers.filter((item) => item.status === "ready").length;
  const toolCount = servers.reduce((sum, item) => sum + Number(item.toolCount || 0), 0);
  return `
    <article class="glass-card mcp-panel">
      <div class="card-heading">
        <h2>${icon("sparkle")} 外部 MCP 工具</h2>
        <div class="mcp-heading-actions">
          <button type="button" data-mcp-preset="anysearch">${icon("search")} AnySearch 预设</button>
          <button type="button" data-mcp-template="custom-stdio">${icon("plusCircle")} 添加本地工具</button>
          <span>${readyCount}/${servers.length} 就绪 · ${toolCount} 个工具</span>
        </div>
      </div>
      ${renderMcpManagerGuide()}
      ${renderMcpTemplateStrip()}
      <div class="mcp-server-list">
        ${servers.map(renderMcpServerRow).join("")}
      </div>
      <p class="mcp-panel-note">${icon("shield")} 保存后自动发现工具列表，不会主动调用，也不直接进入对话上下文。</p>
    </article>
  `;
}

function renderMcpManagerGuide() {
  const steps = [
    ["1", "选择模板", "AnySearch / Node / Python / 自定义 stdio"],
    ["2", "保存配置", "命令与参数写入本地用户配置"],
    ["3", "测试并发现", "启动 MCP 并读取 tools/list"],
    ["4", "安全纳入目录", "默认不自动调用、不进提示词"]
  ];
  return `
    <div class="mcp-manager-guide" aria-label="MCP 配置向导">
      ${steps.map(([index, title, body]) => `
        <span>
          <strong>${escapeHtml(index)}</strong>
          <em>${escapeHtml(title)}</em>
          <small>${escapeHtml(body)}</small>
        </span>
      `).join("")}
    </div>
  `;
}

function resolveGptSoVitsWizardStep(provider) {
  if (!provider || provider.adapter !== "gpt_sovits") return 1;
  const hasEndpoint = Boolean(provider.endpoint);
  const healthOk = provider.status === "ready";
  const hasVoiceProfile = Boolean(provider.defaultVoiceProfile);
  const voice = characterPage?.voice && typeof characterPage.voice === "object" ? characterPage.voice : {};
  const charProvider = String(voice.provider || "").trim();
  const charProfileId = String(voice.profileId || "").trim();
  const isBound = isProviderVoiceMatch(provider.id || "", charProvider) && Boolean(charProfileId);
  if (isBound) return 5;
  if (hasVoiceProfile) return 4;
  if (healthOk) return 3;
  if (hasEndpoint) return 2;
  return 1;
}

function renderGptSoVitsGuide(activeStep = 1) {
  // [index, title, default-body, active-status]
  const steps = [
    ["1", "连接服务", "填写服务地址并保存", "待填写服务地址"],
    ["2", "检查连接", "点击健康检查确认连通", "连接失败，请检查"],
    ["3", "声线档案", "识别模型文件夹并保存档案", "待保存声线档案"],
    ["4", "绑定角色", "试听确认后设为当前声音", "待绑定当前角色"],
    ["5", "已完成", "当前角色使用此声线", "当前角色已绑定"]
  ];
  return `
    <div class="gpt-sovits-guide" aria-label="GPT-SoVITS 配置向导">
      ${steps.map(([index, title, body, statusText], i) => {
        const stepNum = i + 1;
        const isDone = stepNum < activeStep;
        const isActive = stepNum === activeStep;
        const cls = isDone ? "is-done" : isActive ? "is-active" : "";
        const displayText = isDone ? "已完成" : isActive ? statusText : body;
        return `
          <span${cls ? ` class="${escapeAttr(cls)}"` : ""}>
            <strong>${escapeHtml(index)}</strong>
            <em>${escapeHtml(title)}</em>
            <small>${escapeHtml(displayText)}</small>
          </span>
        `;
      }).join("")}
    </div>
  `;
}

function renderMcpTemplateStrip() {
  const templateButtons = MCP_STDIO_TEMPLATES
    .filter((item) => item.id !== "custom-stdio")
    .map((item) => `
      <button type="button" data-mcp-template="${escapeAttr(item.id)}">
        ${icon("sparkle")}
        <span>${escapeHtml(item.label)}</span>
      </button>
    `)
    .join("");
  const roadmap = MCP_TRANSPORT_ROADMAP.map((item) => `
    <span class="mcp-roadmap-chip">
      ${icon("info")}
      <b>${escapeHtml(item.label)}</b>
      <em>${escapeHtml(item.status)}</em>
    </span>
  `).join("");
  return `
    <div class="mcp-template-strip">
      <strong>MCP 模板</strong>
      ${templateButtons}
      ${roadmap}
    </div>
  `;
}

function buildVisibleMcpServers() {
  const servers = Array.isArray(abilitiesPage.mcpServers) ? abilitiesPage.mcpServers : [];
  const draftRows = buildDraftMcpServers(servers);
  if (servers.length) {
    return [...draftRows, ...servers];
  }
  const draftId = Object.keys(state.mcpConfigDrafts || {})[0] || ANYSEARCH_MCP_PRESET.serverId;
  if (draftId === ANYSEARCH_MCP_PRESET.serverId) return [buildAnySearchDraftMcpServer()];
  const draft = state.mcpConfigDrafts[draftId] || {};
  return [buildCustomDraftMcpServer(draftId, draft)];
}

function buildDraftMcpServers(servers = []) {
  const existingIds = new Set(servers.map((item) => String(item.serverId || item.id || "").trim()).filter(Boolean));
  return Object.entries(state.mcpConfigDrafts || {})
    .filter(([serverId]) => !existingIds.has(serverId))
    .map(([serverId, draft]) => (
      serverId === ANYSEARCH_MCP_PRESET.serverId
        ? buildAnySearchDraftMcpServer()
        : buildCustomDraftMcpServer(serverId, draft)
    ));
}

function buildCustomDraftMcpServer(serverId, draft = {}) {
  return {
    id: `provider.mcp.${draft.serverId || serverId}`,
    serverId: draft.serverId || serverId,
    title: draft.displayName || "自定义 MCP",
    status: "missing_config",
    statusLabel: "未配置",
    statusTone: "warning",
    reason: draft.reason || "填写本地 MCP stdio 启动命令后，可以手动发现工具目录",
    enabled: draft.enabled ?? true,
    configured: false,
    transport: "stdio",
    commandName: draft.command || "",
    toolCount: 0,
    safeToolLabels: ["等待工具发现"],
    toolDetails: [],
    highRiskCount: 0,
    promptExposedCount: 0,
    requiresConfirmation: false,
    approvalMode: "disabled",
    approvalLabel: "暂不可用",
    lastDiscoveryLabel: "未执行发现",
    isDraft: true
  };
}

function buildAnySearchDraftMcpServer() {
  const draft = state.mcpConfigDrafts[ANYSEARCH_MCP_PRESET.serverId] || {};
  return {
    id: `provider.mcp.${ANYSEARCH_MCP_PRESET.serverId}`,
    serverId: ANYSEARCH_MCP_PRESET.serverId,
    title: draft.displayName || ANYSEARCH_MCP_PRESET.displayName,
    status: "missing_config",
    statusLabel: "预设",
    statusTone: "warning",
    reason: ANYSEARCH_MCP_PRESET.reason,
    enabled: draft.enabled ?? true,
    configured: false,
    transport: "stdio",
    commandName: draft.command || ANYSEARCH_MCP_PRESET.command,
    toolCount: 0,
    safeToolLabels: ANYSEARCH_MCP_PRESET.toolLabels,
    toolDetails: ANYSEARCH_MCP_PRESET.toolLabels.map((label) => ({
      title: label,
      detail: "预设能力，保存并发现后以 MCP 返回的工具目录为准。",
      riskLabel: "低风险",
      promptLabel: "发现后默认开放"
    })),
    highRiskCount: 0,
    promptExposedCount: 0,
    requiresConfirmation: false,
    approvalMode: "disabled",
    approvalLabel: "暂不可用",
    lastDiscoveryLabel: ANYSEARCH_MCP_PRESET.lastDiscoveryLabel,
    isDraft: true,
    preset: "anysearch"
  };
}

function renderMcpServerRow(server) {
  const serverId = server.serverId || "";
  const isOpen = state.activeMcpConfigId === serverId;
  const statusTone = server.statusTone || "warning";
  const toolLabels = Array.isArray(server.safeToolLabels) ? server.safeToolLabels : [];
  const commandName = server.commandName || "本地启动器";
  const statusMessage = state.mcpActionStatus[serverId] || server.reason || server.statusLabel || "待确认";
  const approvalLabel = server.approvalLabel || approvalModeUiLabel(server.approvalMode);
  const safetyText = server.highRiskCount
    ? `${server.highRiskCount} 个高风险工具 · ${approvalLabel}`
    : server.requiresConfirmation
      ? `调用前需要确认 · ${approvalLabel}`
      : approvalLabel;
  const promptText = server.promptExposedCount
      ? `${server.promptExposedCount} 个工具已开放给提示词`
      : "暂未开放给提示词";
  return `
    <section class="mcp-server-row ${isOpen ? "is-open" : ""}" data-mcp-row data-mcp-server-id="${escapeAttr(serverId)}">
      <div class="mcp-server-head">
        <span class="provider-icon mcp-icon">${icon("cube")}</span>
        <div>
          <div class="provider-title-line">
            <h3>${escapeHtml(server.title || "MCP 外部工具")}</h3>
            <span class="module-status ${escapeAttr(statusTone)}">${escapeHtml(server.statusLabel || "待确认")}</span>
          </div>
          <p>${escapeHtml(statusMessage)}</p>
        </div>
        <button
          type="button"
          data-action-id="${CONTROL_CENTER_ACTIONS.abilitiesMcpConfigOpen}"
          data-payload-server-id="${escapeAttr(serverId)}"
          aria-expanded="${isOpen}"
        >${isOpen ? "收起" : "配置"} ${icon("chevronDown")}</button>
      </div>
      <div class="mcp-meta-row">
        <span>${icon("wifi")} ${escapeHtml(server.transport || "stdio")}</span>
        <span>${icon("folder")} ${escapeHtml(commandName)}</span>
        <strong>${escapeHtml(server.lastDiscoveryLabel || "未执行发现")}</strong>
      </div>
      <div class="mcp-tool-strip">
        ${toolLabels.map((label) => `<span>${escapeHtml(label)}</span>`).join("")}
      </div>
      <div class="mcp-safety-row">
        <span>${icon("shield")} ${escapeHtml(safetyText)}</span>
        <span>${icon("info")} ${escapeHtml(promptText)}</span>
      </div>
      ${isOpen ? renderMcpConfigBody(server) : ""}
    </section>
  `;
}

function approvalModeUiLabel(mode) {
  const labels = {
    trusted_auto_allow: "自动允许",
    ask_each_time: "每次确认",
    disabled: "暂不可用"
  };
  return labels[mode] || "待确认";
}

function renderMcpConfigBody(server) {
  const serverId = server.serverId || "custom";
  const draft = state.mcpConfigDrafts[serverId] || {};
  const isAnySearchPreset = server.preset === "anysearch" || serverId === ANYSEARCH_MCP_PRESET.serverId;
  const displayName = draft.displayName ?? (isAnySearchPreset ? ANYSEARCH_MCP_PRESET.displayName : server.isDraft ? "" : server.title || "");
  const commandValue = draft.command ?? (isAnySearchPreset ? ANYSEARCH_MCP_PRESET.command : "");
  const argsValue = draft.argsText ?? (isAnySearchPreset ? ANYSEARCH_MCP_PRESET.argsText : "");
  const commandPlaceholder = server.commandName
    ? `已保存 ${server.commandName}；修改时重新填写完整命令`
    : "例如 python 或 npx";
  const argsPlaceholder = isAnySearchPreset
    ? "AnySearch 预设参数；使用 ANYSEARCH_API_KEY 环境变量，不要填真实 key"
    : "每行一个参数，例如\n-m\nmy_mcp_server";
  const envPlaceholder = isAnySearchPreset
    ? "留空；请在启动 Akane 的环境里设置 ANYSEARCH_API_KEY"
    : "可选，每行 KEY=VALUE；不要填写 API key / token";
  return `
    <div class="mcp-config-body">
      <div class="mcp-config-fields">
        <label>
          <span>服务 ID</span>
          <input
            type="text"
            value="${escapeAttr(draft.serverId || serverId)}"
            placeholder="custom"
            data-mcp-server-id-input
            autocomplete="off"
            spellcheck="false"
            ${server.isDraft ? "" : "readonly"}
          />
        </label>
        <label>
          <span>显示名</span>
          <input
            type="text"
            value="${escapeAttr(displayName)}"
            placeholder="Browser MCP"
            data-mcp-display-name-input
            autocomplete="off"
            spellcheck="false"
          />
        </label>
        <label class="span-2">
          <span>启动命令</span>
          <input
            type="text"
            value="${escapeAttr(commandValue)}"
            placeholder="${escapeAttr(commandPlaceholder)}"
            data-mcp-command-input
            autocomplete="off"
            spellcheck="false"
          />
        </label>
        <label class="span-2">
          <span>工作目录</span>
          <input
            type="text"
            value="${escapeAttr(draft.cwd || "")}"
            placeholder="可选；仅保存到本地配置，不在面板回显"
            data-mcp-cwd-input
            autocomplete="off"
            spellcheck="false"
          />
        </label>
        <label>
          <span>启动参数</span>
          <textarea
            data-mcp-args-input
            autocomplete="off"
            spellcheck="false"
            placeholder="${escapeAttr(argsPlaceholder)}"
          >${escapeHtml(argsValue)}</textarea>
        </label>
        <label>
          <span>环境变量</span>
          <textarea
            data-mcp-env-input
            autocomplete="off"
            spellcheck="false"
            placeholder="${escapeAttr(envPlaceholder)}"
          >${escapeHtml(draft.envText || "")}</textarea>
        </label>
      </div>
      <div class="mcp-config-footer">
        <label class="provider-toggle mcp-enable-toggle">
          <input type="checkbox" data-mcp-enabled-input ${(draft.enabled ?? server.enabled) ? "checked" : ""} />
          <span>启用此 MCP</span>
        </label>
        <div class="provider-config-actions mcp-config-actions">
          <button
            type="button"
            data-mcp-discover
            data-server-id="${escapeAttr(serverId)}"
            data-action-id="${CONTROL_CENTER_ACTIONS.abilitiesMcpDiscover}"
          >${icon("sparkle")} 保存并发现工具</button>
          <button
            type="button"
            data-mcp-config-save
            data-server-id="${escapeAttr(serverId)}"
            data-action-id="${CONTROL_CENTER_ACTIONS.abilitiesMcpConfigSave}"
          >${icon("checkCircle")} 保存配置</button>
        </div>
      </div>
      ${renderMcpToolDetails(server)}
      ${renderMcpDiagnostics(server)}
      <p>${icon("shield")} ${isAnySearchPreset ? "AnySearch API key 只通过本机环境变量读取；控制中心不会保存或回显真实 key。" : "已保存命令和路径不会在控制中心回显；发现工具只读取 tools/list，不会调用工具。"}</p>
    </div>
  `;
}

function renderMcpToolDetails(server) {
  const tools = Array.isArray(server.toolDetails) ? server.toolDetails : [];
  if (!tools.length) {
    return `
      <div class="mcp-tool-details empty">
        <strong>${icon("info")} 工具清单</strong>
        <p>保存并发现工具后，这里会显示安全摘要、风险级别和提示词暴露状态。</p>
      </div>
    `;
  }
  return `
    <div class="mcp-tool-details">
      <strong>${icon("info")} 工具清单</strong>
      <div>
        ${tools.slice(0, 8).map((tool) => `
          <span>
            <b>${escapeHtml(tool.title || "外部工具")}</b>
            <em>${escapeHtml(tool.riskLabel || "待确认")}</em>
            <small>${escapeHtml(tool.promptLabel || "默认不进提示词")}</small>
          </span>
        `).join("")}
      </div>
    </div>
  `;
}

function renderMcpDiagnostics(server) {
  const diagnostics = [
    ["传输", server.transport || "stdio"],
    ["启动器", server.commandName || "待填写"],
    ["发现状态", server.lastDiscoveryLabel || "未执行发现"],
    ["审批策略", server.approvalLabel || approvalModeUiLabel(server.approvalMode)]
  ];
  return `
    <div class="mcp-diagnostics">
      ${diagnostics.map(([label, value]) => `
        <span>
          <em>${escapeHtml(label)}</em>
          <strong>${escapeHtml(value)}</strong>
        </span>
      `).join("")}
    </div>
  `;
}

function renderProviderRow(provider) {
  const providerId = provider.id || "";
  const isOpen = state.activeProviderConfigId === providerId;
  const statusTone = provider.statusTone || "warning";
  const endpoint = provider.endpoint || "";
  const defaultEndpoint = provider.defaultEndpoint || "";
  const displayEndpoint = endpoint || defaultEndpoint || "待配置";
  const statusMessage = state.providerActionStatus[providerId] || provider.reason || provider.statusLabel || "待确认";
  return `
    <section class="provider-row ${isOpen ? "is-open" : ""}" data-provider-row data-provider-id="${escapeAttr(providerId)}">
      <div class="provider-summary">
        <span class="provider-icon">${icon(provider.adapter === "gpt_sovits" ? "mic" : "cube")}</span>
        <div>
          <div class="provider-title-line">
            <h3>${escapeHtml(provider.title || provider.name || "本地能力")}</h3>
            <span class="module-status ${escapeAttr(statusTone)}">${escapeHtml(provider.statusLabel || "待确认")}</span>
          </div>
          <p>${escapeHtml(provider.description || "本地能力执行环境")}</p>
          <div class="provider-meta">
            <span>${icon("folder")} ${escapeHtml(provider.usedByLabel || "本地能力")}</span>
            <span>${icon("wifi")} ${escapeHtml(displayEndpoint)}</span>
            <strong>${escapeHtml(statusMessage)}</strong>
          </div>
        </div>
        <button
          type="button"
          data-action-id="${CONTROL_CENTER_ACTIONS.abilitiesProviderConfigOpen}"
          data-payload-provider-id="${escapeAttr(providerId)}"
          aria-expanded="${isOpen}"
        >${isOpen ? "收起" : "配置"} ${icon("chevronDown")}</button>
      </div>
      ${isOpen ? renderProviderConfigBody(provider, endpoint, defaultEndpoint) : ""}
    </section>
  `;
}

function renderProviderConfigBody(provider, endpoint, defaultEndpoint) {
  const providerId = provider.id || "";
  const inputValue = endpoint || defaultEndpoint || "";
  const actionsDisabled = provider.actionsEnabled === false;
  const healthActionAttr = actionsDisabled ? "" : ` data-action-id="${CONTROL_CENTER_ACTIONS.abilitiesProviderHealthCheck}"`;
  const wizardStep = provider.adapter === "gpt_sovits" ? resolveGptSoVitsWizardStep(provider) : 0;
  const saveActionAttr = actionsDisabled ? "" : ` data-action-id="${CONTROL_CENTER_ACTIONS.abilitiesProviderConfigSave}"`;
  const ttsTestActionAttr = actionsDisabled ? "" : ` data-action-id="${CONTROL_CENTER_ACTIONS.abilitiesProviderTtsTest}"`;
  const voiceProfileInspectActionAttr = actionsDisabled ? "" : ` data-action-id="${CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileInspectFolder}"`;
  const voiceProfileSaveActionAttr = actionsDisabled ? "" : ` data-action-id="${CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileSave}"`;
  const voiceProfileAssignActionAttr = actionsDisabled ? "" : ` data-action-id="${CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileAssignToCurrentCharacter}"`;
  const voiceProfileClearActionAttr = actionsDisabled ? "" : ` data-action-id="${CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileClearCurrentCharacter}"`;
  const disabledAttr = actionsDisabled ? ' aria-disabled="true" disabled' : "";
  const voiceProfile = provider.adapter === "gpt_sovits" ? getProviderVoiceProfileDraft(provider) : null;
  return `
    <div class="provider-config-body">
      ${provider.adapter === "gpt_sovits" ? renderGptSoVitsGuide(wizardStep) : ""}
      <label>
        <span>本地服务地址</span>
        <input
          type="text"
          value="${escapeAttr(inputValue)}"
          placeholder="${escapeAttr(defaultEndpoint || "http://127.0.0.1:8188")}"
          data-provider-endpoint-input
          autocomplete="off"
          spellcheck="false"
        />
      </label>
      <label class="provider-toggle">
        <input type="checkbox" data-provider-enabled-input ${provider.enabled ? "checked" : ""} />
        <span>启用此能力</span>
      </label>
      <div class="provider-config-actions">
        <button
          type="button"
          data-provider-health-check
          data-provider-id="${escapeAttr(providerId)}"
          ${healthActionAttr}
          ${disabledAttr}
        >${icon("wifi")} 检查连接</button>
        <button
          type="button"
          data-provider-config-save
          data-provider-id="${escapeAttr(providerId)}"
          ${saveActionAttr}
          ${disabledAttr}
        >${icon("checkCircle")} 保存配置</button>
      </div>
      ${provider.adapter === "gpt_sovits" ? `
        ${renderProviderVoiceProfileConfig(providerId, voiceProfile, voiceProfileInspectActionAttr, voiceProfileSaveActionAttr, voiceProfileAssignActionAttr, voiceProfileClearActionAttr, ttsTestActionAttr, disabledAttr)}
      ` : ""}
      <p>${icon("shield")} 只接受本机 localhost / 127.0.0.1 地址；检查连接不会自动启用能力。</p>
    </div>
  `;
}

function getProviderVoiceProfileDraft(provider) {
  const providerId = provider?.id || "";
  const saved = provider?.defaultVoiceProfile || {};
  const draft = state.providerVoiceProfileDrafts[providerId] || {};
  return {
    voiceProfileId: draft.voiceProfileId || saved.voiceProfileId || "",
    displayName: draft.displayName || saved.name || "",
    voiceProfileEnabled: draft.voiceProfileEnabled ?? saved.enabled ?? true,
    folderPath: draft.folderPath || "",
    textLang: draft.textLang || saved.textLang || "zh",
    promptLang: draft.promptLang || saved.promptLang || "zh",
    mediaType: draft.mediaType || saved.mediaType || "wav",
    refAudioPath: draft.refAudioPath || "",
    promptText: draft.promptText || "",
    inspectWarnings: Array.isArray(draft.inspectWarnings) ? draft.inspectWarnings : [],
    detected: draft.detected && typeof draft.detected === "object" ? draft.detected : {},
    referenceAudioName: saved.referenceAudioName || "",
    promptTextLength: saved.promptTextLength || 0,
    statusLabel: saved.statusLabel || "",
    statusTone: saved.statusTone || "warning"
  };
}

function renderProviderVoiceCurrentSummary(providerId) {
  const voice = characterPage?.voice && typeof characterPage.voice === "object" ? characterPage.voice : {};
  const provider = String(voice.provider || voice.providerId || "").trim();
  const profileId = String(voice.profileId || voice.profile_id || "").trim();
  const isCurrentProvider = isProviderVoiceMatch(providerId, provider);
  if (!provider && !profileId) {
    return `
      <p class="provider-voice-profile-summary provider-voice-profile-current is-default">
        ${icon("checkCircle")} 当前角色：默认声线
      </p>
    `;
  }
  const providerLabel = providerVoiceDisplayName(provider);
  const profile = profileId ? ` · ${escapeHtml(profileId)}` : "";
  const suffix = isCurrentProvider ? "" : " · 其他提供方";
  return `
    <p class="provider-voice-profile-summary provider-voice-profile-current">
      ${icon("mic")} 当前角色：${escapeHtml(providerLabel)}${profile}${suffix}
    </p>
  `;
}

function isProviderVoiceMatch(providerId, provider) {
  const normalizedProviderId = String(providerId || "").trim();
  const normalizedProvider = String(provider || "").trim();
  if (!normalizedProvider) return false;
  if (normalizedProviderId === normalizedProvider) return true;
  return normalizedProvider === "gpt_sovits" && normalizedProviderId === "provider.tts.gpt_sovits.local";
}

function providerVoiceDisplayName(provider) {
  const normalized = String(provider || "").trim();
  if (!normalized) return "默认声线";
  if (normalized === "gpt_sovits" || normalized === "provider.tts.gpt_sovits.local") return "GPT-SoVITS";
  if (normalized === "provider.tts.edge" || normalized === "edge") return "Microsoft Edge";
  return normalized;
}

function renderProviderVoiceProfileConfig(providerId, profile, inspectActionAttr, saveActionAttr, assignActionAttr, clearActionAttr, ttsTestActionAttr, disabledAttr) {
  const currentVoiceSummary = renderProviderVoiceCurrentSummary(providerId);
  const savedSummary = profile.referenceAudioName || profile.promptTextLength
    ? `
      <p class="provider-voice-profile-summary">
        ${icon("shield")}
        已保存：${escapeHtml(profile.referenceAudioName || "参考音频")} · 参考文本 ${escapeHtml(String(profile.promptTextLength || 0))} 字
      </p>
    `
    : "";
  const inspectSummary = renderProviderVoiceInspectSummary(profile);
  const testStatus = String(state.providerActionStatus[providerId] || "").trim();
  return `
    <div class="provider-voice-profile">
      <div class="provider-voice-profile-head">
        <strong>${icon("mic")} 声线档案</strong>
        <span>角色包里的 profile_id 会匹配这里的声线 ID</span>
      </div>
      ${currentVoiceSummary}
      <div class="provider-voice-profile-folder">
        <label>
          <span>模型文件夹</span>
          <input
            type="text"
            value="${escapeAttr(profile.folderPath)}"
            placeholder="F:\\models\\dania"
            data-provider-model-folder-path-input
            autocomplete="off"
            spellcheck="false"
          />
        </label>
        <button
          type="button"
          data-provider-voice-profile-inspect
          data-provider-id="${escapeAttr(providerId)}"
          ${inspectActionAttr}
          ${disabledAttr}
        >${icon("search")} 自动识别声线</button>
      </div>
      <div class="provider-voice-profile-fields">
        <label>
          <span>声线 ID</span>
          <input
            type="text"
            value="${escapeAttr(profile.voiceProfileId)}"
            placeholder="例如 reimu_main"
            data-provider-voice-profile-id-input
            data-provider-tts-profile-input
            autocomplete="off"
            spellcheck="false"
          />
        </label>
        <label>
          <span>显示名</span>
          <input
            type="text"
            value="${escapeAttr(profile.displayName)}"
            placeholder="Akane 主声线"
            data-provider-voice-profile-name-input
            autocomplete="off"
            spellcheck="false"
          />
        </label>
        <label>
          <span>文本语言</span>
          <input
            type="text"
            value="${escapeAttr(profile.textLang)}"
            placeholder="zh"
            data-provider-text-lang-input
            autocomplete="off"
            spellcheck="false"
          />
        </label>
        <label>
          <span>参考语言</span>
          <input
            type="text"
            value="${escapeAttr(profile.promptLang)}"
            placeholder="zh"
            data-provider-prompt-lang-input
            autocomplete="off"
            spellcheck="false"
          />
        </label>
        <label>
          <span>返回格式</span>
          <input
            type="text"
            value="${escapeAttr(profile.mediaType)}"
            placeholder="wav"
            data-provider-media-type-input
            autocomplete="off"
            spellcheck="false"
          />
        </label>
        <label class="provider-toggle voice-profile-enable-toggle">
          <input type="checkbox" data-provider-voice-profile-enabled-input ${profile.voiceProfileEnabled ? "checked" : ""} />
          <span>启用档案</span>
        </label>
        <label class="span-2">
          <span>参考音频路径</span>
          <input
            type="text"
            value="${escapeAttr(profile.refAudioPath)}"
            placeholder="${escapeAttr(profile.referenceAudioName ? `已保存 ${profile.referenceAudioName}；需要替换时再填写` : "D:\\voices\\akane_ref.wav")}"
            data-provider-ref-audio-path-input
            autocomplete="off"
            spellcheck="false"
          />
        </label>
        <label class="span-2">
          <span>参考文本</span>
          <input
            type="text"
            value="${escapeAttr(profile.promptText)}"
            placeholder="${escapeAttr(profile.promptTextLength ? `已保存 ${profile.promptTextLength} 字；需要替换时再填写` : "参考音频对应的原文")}"
            data-provider-prompt-text-input
            autocomplete="off"
            spellcheck="false"
          />
        </label>
      </div>
      ${savedSummary}
      ${inspectSummary}
      <div class="provider-voice-profile-actions">
        <button
          type="button"
          data-provider-voice-profile-save
          data-provider-id="${escapeAttr(providerId)}"
          ${saveActionAttr}
          ${disabledAttr}
        >${icon("checkCircle")} 保存声线档案</button>
        <button
          type="button"
          data-provider-voice-profile-assign
          data-provider-id="${escapeAttr(providerId)}"
          ${assignActionAttr}
          ${disabledAttr}
        >${icon("mic")} 设为当前角色声音</button>
        <button
          type="button"
          data-provider-voice-profile-clear
          data-provider-id="${escapeAttr(providerId)}"
          ${clearActionAttr}
          ${disabledAttr}
        >${icon("undo")} 恢复默认声线</button>
      </div>
    </div>
    <div class="provider-tts-test">
      <label>
        <span>测试台词</span>
        <input
          type="text"
          value="你好，主人，本地语音服务已经接通。"
          data-provider-tts-test-text
          autocomplete="off"
          spellcheck="false"
        />
      </label>
      <button
        type="button"
        data-provider-tts-test
        data-provider-id="${escapeAttr(providerId)}"
        ${ttsTestActionAttr}
        ${disabledAttr}
      >${icon("play")} 使用此声线试听</button>
      ${testStatus ? `<strong class="provider-tts-test-status">${escapeHtml(testStatus)}</strong>` : ""}
    </div>
    ${renderProviderTtsTestPlayer(providerId)}
  `;
}

function renderProviderTtsTestPlayer(providerId) {
  if (providerTestAudio.providerId !== providerId || !providerTestAudio.url) return "";
  const mediaType = String(providerTestAudio.mediaType || "audio/wav").split(";", 1)[0];
  const sizeLabel = providerTestAudio.audioBytes > 0 ? `${Math.ceil(providerTestAudio.audioBytes / 1024)} KB` : "";
  return `
    <div class="provider-tts-test-player">
      <audio
        controls
        preload="metadata"
        src="${escapeAttr(providerTestAudio.url)}"
        data-provider-test-audio
        data-provider-id="${escapeAttr(providerId)}"
      ></audio>
      <span>${icon("volume")} ${escapeHtml([mediaType, sizeLabel].filter(Boolean).join(" · "))}</span>
    </div>
  `;
}

function renderProviderVoiceInspectSummary(profile) {
  const detected = profile.detected || {};
  const detectedItems = [
    detected.configFileName ? `配置 ${detected.configFileName}` : "",
    detected.referenceAudioName ? `参考音频 ${detected.referenceAudioName}` : "",
    detected.gptWeightName ? `GPT ${detected.gptWeightName}` : "",
    detected.sovitsWeightName ? `SoVITS ${detected.sovitsWeightName}` : ""
  ].filter(Boolean);
  const warningLabels = (Array.isArray(profile.inspectWarnings) ? profile.inspectWarnings : [])
    .map(providerVoiceInspectWarningLabel)
    .filter(Boolean);
  if (!detectedItems.length && !warningLabels.length) return "";
  const summary = detectedItems.length ? `识别到：${detectedItems.join(" · ")}` : "";
  const warning = warningLabels.length ? `提示：${warningLabels.join("、")}` : "";
  return `
    <p class="provider-voice-profile-summary provider-voice-profile-inspect-summary">
      ${icon(warningLabels.length ? "alert" : "checkCircle")}
      ${escapeHtml([summary, warning].filter(Boolean).join("；"))}
    </p>
  `;
}

function providerVoiceInspectWarningLabel(reason) {
  const labels = {
    tts_infer_yaml_missing: "没有 tts_infer.yaml",
    reference_audio_missing: "参考音频待补",
    prompt_text_missing: "参考文本待补",
    gpt_weight_missing: "GPT 权重未识别",
    sovits_weight_missing: "SoVITS 权重未识别"
  };
  return labels[reason] || "";
}

function renderWorkflow(item) {
  const workflowId = item.workflowId || item.id || "";
  const isConfigurable = Boolean(workflowId && (item.workflowPath !== undefined || item.defaultWorkflowPath !== undefined || item.actionsEnabled));
  const isOpen = Boolean(workflowId && state.activeWorkflowConfigId === workflowId);
  const statusBadge = item.statusLabel
    ? `<span class="module-status ${escapeAttr(item.statusTone || "warning")}">${escapeHtml(item.statusLabel)}</span>`
    : "";
  const detail = item.detail ? `<p>${escapeHtml(item.detail)}</p>` : "";
  const statusMessage = state.workflowActionStatus[workflowId] || item.reason || item.statusLabel || "待确认";
  return `
    <article class="workflow-tile ${isOpen ? "is-open" : ""}" data-workflow-row data-workflow-id="${escapeAttr(workflowId)}">
      <div class="workflow-main">
        <div class="workflow-icons">
          ${item.steps.map((step, index) => `
            <span>${icon(index === 0 ? "folder" : index === 1 ? "file" : "upload")}</span>
          `).join("<i>›</i>")}
        </div>
        <div>
          <div class="workflow-title-line">
            <strong>${escapeHtml(item.title)}</strong>
            ${statusBadge}
          </div>
          ${detail}
          <small>${escapeHtml(statusMessage)}</small>
        </div>
      </div>
      ${isConfigurable ? `
        <button
          class="workflow-config-toggle"
          type="button"
          data-action-id="${CONTROL_CENTER_ACTIONS.abilitiesWorkflowConfigOpen}"
          data-payload-workflow-id="${escapeAttr(workflowId)}"
          aria-expanded="${isOpen}"
        >${isOpen ? "收起" : "配置"} ${icon("chevronDown")}</button>
      ` : ""}
      ${isConfigurable && isOpen ? renderWorkflowConfigBody(item) : ""}
    </article>
  `;
}

function renderWorkflowConfigBody(item) {
  const workflowId = item.workflowId || item.id || "";
  const workflowPath = item.workflowPath || item.defaultWorkflowPath || "workflows/comfyui/portrait_cutout.json";
  const inputSlot = item.inputImageSlot || "12.inputs.image";
  const outputSlot = item.outputImageSlot || "20.inputs.filename_prefix";
  const actionsDisabled = item.actionsEnabled === false;
  const saveActionAttr = actionsDisabled ? "" : ` data-action-id="${CONTROL_CENTER_ACTIONS.abilitiesWorkflowConfigSave}"`;
  const validateActionAttr = actionsDisabled ? "" : ` data-action-id="${CONTROL_CENTER_ACTIONS.abilitiesWorkflowValidate}"`;
  const disabledAttr = actionsDisabled ? ' aria-disabled="true" disabled' : "";
  return `
    <div class="workflow-config-body">
      <div class="workflow-config-fields">
        <label>
          <span>工作流文件</span>
          <input
            type="text"
            value="${escapeAttr(workflowPath)}"
            placeholder="workflows/comfyui/portrait_cutout.json"
            data-workflow-path-input
            autocomplete="off"
            spellcheck="false"
          />
        </label>
        <label>
          <span>输入节点</span>
          <input
            type="text"
            value="${escapeAttr(inputSlot)}"
            placeholder="12.inputs.image"
            data-workflow-input-slot-input
            autocomplete="off"
            spellcheck="false"
          />
        </label>
        <label>
          <span>输出前缀</span>
          <input
            type="text"
            value="${escapeAttr(outputSlot)}"
            placeholder="20.inputs.filename_prefix"
            data-workflow-output-slot-input
            autocomplete="off"
            spellcheck="false"
          />
        </label>
      </div>
      <div class="workflow-config-footer">
        <label class="provider-toggle workflow-enable-toggle">
          <input type="checkbox" data-workflow-enabled-input ${item.enabled ? "checked" : ""} />
          <span>启用绑定</span>
        </label>
        <div class="provider-config-actions workflow-config-actions">
          <input
            type="file"
            accept="application/json,.json"
            data-workflow-file-input
            data-workflow-id="${escapeAttr(workflowId)}"
            hidden
          />
          <button
            type="button"
            data-workflow-file-import
            data-workflow-id="${escapeAttr(workflowId)}"
            ${disabledAttr}
          >${icon("upload")} 导入 JSON</button>
          <button
            type="button"
            data-workflow-validate
            data-workflow-id="${escapeAttr(workflowId)}"
            ${validateActionAttr}
            ${disabledAttr}
          >${icon("checkCircle")} 验证配置</button>
          <button
            type="button"
            data-workflow-config-save
            data-workflow-id="${escapeAttr(workflowId)}"
            ${saveActionAttr}
            ${disabledAttr}
          >${icon("checkCircle")} 保存绑定</button>
        </div>
      </div>
      <p>${icon("shield")} 导入只会把 API 工作流 JSON 保存到当前用户能力目录；验证不执行 ComfyUI，真正处理发生在角色工坊自动抠图时。</p>
    </div>
  `;
}

function renderCallsTable() {
  const allCalls = Array.isArray(abilitiesPage.calls) ? abilitiesPage.calls : [];
  const visibleCalls = state.showAllAbilityCalls ? allCalls : allCalls.slice(0, 3);
  return `
    <div class="calls-table" role="table" aria-label="最近能力调用">
      <div class="calls-row calls-head" role="row">
        <span>时间</span>
        <span>能力模块</span>
        <span>操作描述</span>
        <span>状态</span>
        <span>耗时</span>
        <span>调用方式</span>
      </div>
      ${visibleCalls.map((item) => `
        <div class="calls-row" role="row">
          <span>${escapeHtml(item.time)}</span>
          <strong>${escapeHtml(item.module)}</strong>
          <span>${escapeHtml(item.description)}</span>
          <b class="${item.status === "成功" ? "success" : "blocked"}">${escapeHtml(item.status)}</b>
          <span>${escapeHtml(item.duration)}</span>
          <span>${escapeHtml(item.method)}</span>
        </div>
      `).join("")}
      ${!state.showAllAbilityCalls && allCalls.length > 3 ? `<div class="calls-row"><span>还有 ${allCalls.length - 3} 条记录 · 点击"查看全部日志"展开</span></div>` : ""}
    </div>
  `;
}

function renderSafetyPanel() {
  const safety = abilitiesPage.safety || {};
  const policy = normalizeApprovalPolicyForUi(safety.approvalPolicy);
  const items = Array.isArray(safety.items) ? safety.items : [];
  return `
    <article class="glass-card side-status-panel safety-panel">
      <div class="card-heading">
        <h2>${icon("shield")} 安全边界</h2>
        <span>${escapeHtml(safety.status || "待连接")}</span>
      </div>
      <div class="approval-policy-control" role="group" aria-label="能力审批模式">
        ${policy.availableModes.map((mode) => {
          const active = mode.id === policy.defaultMode;
          return `
            <button
              class="${active ? "active" : ""}"
              type="button"
              data-action-id="${CONTROL_CENTER_ACTIONS.abilitiesApprovalPolicySave}"
              data-payload-field="defaultMode"
              data-payload-value="${escapeAttr(mode.id)}"
              aria-pressed="${active}"
            >
              <strong>${escapeHtml(mode.label)}</strong>
              <span>${escapeHtml(mode.summary)}</span>
            </button>
          `;
        }).join("")}
      </div>
      <p class="approval-policy-note">${icon("lock")} ${escapeHtml(policy.summary)}</p>
      <div class="side-list">
        ${items.map((item) => `
          <div>
            <span>${icon("checkCircle")} ${escapeHtml(item.label)}</span>
            <strong>${icon("clock")} ${escapeHtml(item.status)}</strong>
          </div>
        `).join("")}
      </div>
      <button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.abilitiesSafetyDetails}">查看详细策略 ${icon("chevron")}</button>
    </article>
  `;
}

function normalizeApprovalPolicyForUi(policy) {
  const data = policy && typeof policy === "object" ? policy : {};
  const defaultMode = ["ask_each_time", "trusted_auto_allow"].includes(String(data.defaultMode || ""))
    ? String(data.defaultMode)
    : "ask_each_time";
  const fallbackModes = [
    {
      id: "ask_each_time",
      label: "请求批准",
      summary: "危险动作进入审批队列"
    },
    {
      id: "trusted_auto_allow",
      label: "完全访问",
      summary: "跳过逐次确认"
    }
  ];
  const availableModes = (Array.isArray(data.availableModes) && data.availableModes.length ? data.availableModes : fallbackModes)
    .map((item) => ({
      id: String(item?.id || "").trim(),
      label: String(item?.label || "").trim(),
      summary: String(item?.summary || "").trim()
    }))
    .filter((item) => ["ask_each_time", "trusted_auto_allow"].includes(item.id))
    .map((item) => ({
      ...item,
      label: item.label || (item.id === "trusted_auto_allow" ? "完全访问" : "请求批准"),
      summary: item.summary || (item.id === "trusted_auto_allow" ? "跳过逐次确认" : "危险动作进入审批队列")
    }));
  return {
    defaultMode,
    label: String(data.label || "").trim() || (defaultMode === "trusted_auto_allow" ? "完全访问" : "请求批准"),
    summary: String(data.summary || "").trim() || (
      defaultMode === "trusted_auto_allow"
        ? "完全访问会跳过逐次审批，但 URL、路径、密钥和本地边界校验仍然开启。"
        : "请求批准会让高风险能力先进入审批队列。"
    ),
    availableModes: availableModes.length ? availableModes : fallbackModes
  };
}

function renderLive2dPanel() {
  return `
    <article class="glass-card side-status-panel live-panel">
      <div class="card-heading">
        <h2>${icon("sparkle")} Live2D 预留状态</h2>
        <span>${escapeHtml(abilitiesPage.live2d.status)}</span>
      </div>
      <div class="side-list live-list">
        ${abilitiesPage.live2d.items.map((item) => `
          <div>
            <span>${escapeHtml(item.label)}</span>
            <strong>${escapeHtml(item.value)}</strong>
            ${icon("star")}
          </div>
        `).join("")}
      </div>
      <button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.abilitiesLive2dOpenSettings}">打开 Live2D 设置面板</button>
    </article>
  `;
}

function renderSwitch(key, isOn) {
  return `
    <button class="switch ${isOn ? "is-on" : ""}" data-switch="${escapeAttr(key)}" type="button" role="switch" aria-checked="${isOn}">
      <span></span>
    </button>
  `;
}

function icon(name) {
  const paths = {
    home: '<path d="M3 11.5 12 4l9 7.5"/><path d="M5.5 10.5V20h5v-5.5h3V20h5v-9.5"/>',
    user: '<path d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z"/><path d="M4.5 20a7.5 7.5 0 0 1 15 0"/>',
    mic: '<path d="M12 14a3.5 3.5 0 0 0 3.5-3.5v-4a3.5 3.5 0 0 0-7 0v4A3.5 3.5 0 0 0 12 14Z"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/>',
    music: '<path d="M9 18V5l10-2v13"/><path d="M9 18a3 3 0 1 1-3-3 3 3 0 0 1 3 3Z"/><path d="M19 16a3 3 0 1 1-3-3 3 3 0 0 1 3 3Z"/>',
    monitor: '<path d="M4 5h16v11H4Z"/><path d="M9 20h6"/><path d="M12 16v4"/>',
    sparkle: '<path d="m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8Z"/><path d="m19 15 .8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8Z"/>',
    settings: '<path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z"/><path d="M19.4 15a1.8 1.8 0 0 0 .36 2l.05.05-2.1 2.1-.05-.05a1.8 1.8 0 0 0-2-.36 1.8 1.8 0 0 0-1.1 1.66V20.5h-3V20.4a1.8 1.8 0 0 0-1.1-1.66 1.8 1.8 0 0 0-2 .36l-.05.05-2.1-2.1.05-.05a1.8 1.8 0 0 0 .36-2 1.8 1.8 0 0 0-1.66-1.1H5v-3h.06a1.8 1.8 0 0 0 1.66-1.1 1.8 1.8 0 0 0-.36-2l-.05-.05 2.1-2.1.05.05a1.8 1.8 0 0 0 2 .36A1.8 1.8 0 0 0 11.56 4V3.5h3V4a1.8 1.8 0 0 0 1.1 1.66 1.8 1.8 0 0 0 2-.36l.05-.05 2.1 2.1-.05.05a1.8 1.8 0 0 0-.36 2 1.8 1.8 0 0 0 1.66 1.1H21v3h-.06A1.8 1.8 0 0 0 19.4 15Z"/>',
    wifi: '<path d="M5 9.5a10 10 0 0 1 14 0"/><path d="M8.5 13a5 5 0 0 1 7 0"/><path d="M12 17h.01"/>',
    focus: '<path d="M8 4H5a1 1 0 0 0-1 1v3"/><path d="M16 4h3a1 1 0 0 1 1 1v3"/><path d="M8 20H5a1 1 0 0 1-1-1v-3"/><path d="M16 20h3a1 1 0 0 0 1-1v-3"/><path d="M9 12h6"/>',
    code: '<path d="m8 8-4 4 4 4"/><path d="m16 8 4 4-4 4"/><path d="m14 5-4 14"/>',
    cpu: '<path d="M8 8h8v8H8Z"/><path d="M4 10h4"/><path d="M4 14h4"/><path d="M16 10h4"/><path d="M16 14h4"/><path d="M10 4v4"/><path d="M14 4v4"/><path d="M10 16v4"/><path d="M14 16v4"/>',
    zap: '<path d="m13 2-8 12h6l-1 8 9-13h-6Z"/>',
    cube: '<path d="m12 3 8 4.5v9L12 21l-8-4.5v-9Z"/><path d="M12 12 4 7.5"/><path d="m12 12 8-4.5"/><path d="M12 12v9"/>',
    stethoscope: '<path d="M6 4v5a4 4 0 0 0 8 0V4"/><path d="M4 4h4"/><path d="M12 4h4"/><path d="M10 15a5 5 0 0 0 10 0v-2"/><path d="M20 13a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z"/>',
    shirt: '<path d="M8 4 5 6.5l-2 4L6 12l1-2v10h10V10l1 2 3-1.5-2-4L16 4l-4 2Z"/><path d="M9 4a3 3 0 0 0 6 0"/>',
    smile: '<path d="M12 20a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z"/><path d="M8.5 10h.01"/><path d="M15.5 10h.01"/><path d="M8.5 14a5 5 0 0 0 7 0"/>',
    cloudUpload: '<path d="M16 17h2.5a3.5 3.5 0 0 0 .4-7 5.5 5.5 0 0 0-10.6-1.7A4.5 4.5 0 0 0 8 17h2"/><path d="M12 18V10"/><path d="m8.5 13.5 3.5-3.5 3.5 3.5"/>',
    alert: '<path d="m12 4 9 16H3Z"/><path d="M12 9v5"/><path d="M12 17h.01"/>',
    undo: '<path d="M9 7H4v5"/><path d="M4 12a8 8 0 1 0 2.4-5.7Z"/>',
    image: '<path d="M4 5h16v14H4Z"/><path d="m7 15 3-3 2.5 2.5L15 12l2 3"/><path d="M8.5 9h.01"/>',
    bell: '<path d="M18 16H6l1.2-1.6V10a4.8 4.8 0 0 1 9.6 0v4.4Z"/><path d="M10 19a2 2 0 0 0 4 0"/>',
    minus: '<path d="M6 12h12"/>',
    square: '<path d="M7 7h10v10H7Z"/>',
    x: '<path d="m7 7 10 10"/><path d="m17 7-10 10"/>',
    lock: '<path d="M7 11V8a5 5 0 0 1 10 0v3"/><path d="M5.5 11h13v9h-13Z"/><path d="M12 15v2"/>',
    clipboard: '<path d="M9 4h6l1 2h3v15H5V6h3Z"/><path d="M9 4v3h6V4"/><path d="M8 11h8"/><path d="M8 15h6"/>',
    eye: '<path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z"/><path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z"/>',
    chat: '<path d="M5 6h14v9H9l-4 4Z"/><path d="M8 10h8"/><path d="M8 13h5"/>',
    shield: '<path d="M12 3 19 6v5c0 4.5-2.8 7.8-7 10-4.2-2.2-7-5.5-7-10V6Z"/><path d="m9 12 2 2 4-5"/>',
    folder: '<path d="M3.5 6.5h6l2 2h9v9.5h-17Z"/><path d="M3.5 9h17"/>',
    file: '<path d="M7 3.5h7l4 4V20H7Z"/><path d="M14 3.5V8h4"/><path d="M9.5 12h5"/><path d="M9.5 15h5"/>',
    doc: '<path d="M7 3.5h7l4 4V20H7Z"/><path d="M14 3.5V8h4"/><path d="M10 11h4"/><path d="M10 14h6"/><path d="M10 17h4"/>',
    gift: '<path d="M4 10h16v10H4Z"/><path d="M12 10v10"/><path d="M3.5 7h17v3h-17Z"/><path d="M8.5 7C6 7 6 4 8.3 4 10 4 12 7 12 7s2-3 3.7-3C18 4 18 7 15.5 7"/>',
    play: '<path d="M8 5v14l11-7Z"/>',
    previous: '<path d="M6 5v14"/><path d="m18 6-9 6 9 6Z"/>',
    next: '<path d="M18 5v14"/><path d="m6 6 9 6-9 6Z"/>',
    pause: '<path d="M8 5h3v14H8Z"/><path d="M13 5h3v14h-3Z"/>',
    stop: '<path d="M7 7h10v10H7Z"/>',
    trash: '<path d="M4 7h16"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M6 7l1 13h10l1-13"/><path d="M9 7V4h6v3"/>',
    repeat: '<path d="M17 2l4 4-4 4"/><path d="M3 11V9a3 3 0 0 1 3-3h15"/><path d="M7 22l-4-4 4-4"/><path d="M21 13v2a3 3 0 0 1-3 3H3"/>',
    volume: '<path d="M4 10v4h4l5 4V6l-5 4Z"/><path d="M16 9a5 5 0 0 1 0 6"/><path d="M18.5 6.5a8 8 0 0 1 0 11"/>',
    equalizer: '<path d="M4 14v4"/><path d="M8 10v8"/><path d="M12 6v12"/><path d="M16 9v9"/><path d="M20 13v5"/>',
    menu: '<path d="M5 7h14"/><path d="M5 12h14"/><path d="M5 17h14"/>',
    leaf: '<path d="M5 19c10 0 14-8 14-14-8 0-14 4-14 14Z"/><path d="M5 19c3-5 7-8 14-14"/>',
    target: '<path d="M12 20a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z"/><path d="M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z"/><path d="M12 12h.01"/>',
    sun: '<path d="M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.9 4.9 1.4 1.4"/><path d="m17.7 17.7 1.4 1.4"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m4.9 19.1 1.4-1.4"/><path d="m17.7 6.3 1.4-1.4"/>',
    moon: '<path d="M20 14.5A7.5 7.5 0 0 1 9.5 4 8.5 8.5 0 1 0 20 14.5Z"/>',
    log: '<path d="M7 4h10v16H7Z"/><path d="M10 8h4"/><path d="M10 12h4"/><path d="M10 16h3"/>',
    panel: '<path d="M4 5h16v14H4Z"/><path d="M4 10h16"/><path d="M10 10v9"/>',
    upload: '<path d="M12 16V4"/><path d="m8 8 4-4 4 4"/><path d="M5 16v4h14v-4"/>',
    window: '<path d="M4 5h16v14H4Z"/><path d="M4 9h16"/><path d="M8 7h.01"/><path d="M11 7h.01"/>',
    camera: '<path d="M5 8h3l1.5-2h5L16 8h3v10H5Z"/><path d="M12 16a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z"/>',
    message: '<path d="M4 5h16v11H9l-5 4Z"/><path d="M8 10h8"/><path d="M8 13h5"/>',
    help: '<path d="M12 20a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z"/><path d="M9.8 9.5a2.3 2.3 0 1 1 3.7 1.8c-.9.6-1.5 1-1.5 2.2"/><path d="M12 16h.01"/>',
    info: '<path d="M12 20a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z"/><path d="M12 11v5"/><path d="M12 8h.01"/>',
    chevron: '<path d="m9 6 6 6-6 6"/>',
    chevronDown: '<path d="m7 10 5 5 5-5"/>',
    arrowRight: '<path d="M5 12h14"/><path d="m13 6 6 6-6 6"/>',
    check: '<path d="m5 12 4 4 10-10"/>',
    heart: '<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.7A4 4 0 0 1 19 10c0 5.6-7 10-7 10Z"/>',
    plusCircle: '<path d="M12 20a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z"/><path d="M12 8v8"/><path d="M8 12h8"/>',
    checkCircle: '<path d="M12 20a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z"/><path d="m8.5 12 2.2 2.2 4.8-5"/>',
    refresh: '<path d="M20 12a8 8 0 0 1-13.6 5.7"/><path d="M4 12A8 8 0 0 1 17.6 6.3"/><path d="M17 3v4h-4"/><path d="M7 21v-4h4"/>',
    circle: '<path d="M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z"/>',
    star: '<path d="m12 4 2.2 4.5 5 .7-3.6 3.5.9 5-4.5-2.4-4.5 2.4.9-5-3.6-3.5 5-.7Z"/>',
    clock: '<path d="M12 20a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z"/><path d="M12 8v5l3 2"/>'
  };
  return `<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.sparkle}</svg>`;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => {
    const entities = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
    return entities[char];
  });
}

function escapeAttr(value) {
  return escapeHtml(value);
}

function formatError(error) {
  return error instanceof Error ? error.message : String(error || "unknown");
}

function voicePreviewTextLines() {
  const text = voicePage.preview?.text;
  if (Array.isArray(text)) return text.map((line) => String(line || ""));
  if (typeof text === "string") return text.split(/\r?\n/).filter(Boolean);
  return [];
}

function actionIdForPerceptionSwitch(featureId) {
  const map = {
    activeWindow: CONTROL_CENTER_ACTIONS.perceptionDesktopContextSetEnabled,
    clipboard: CONTROL_CENTER_ACTIONS.perceptionClipboardContextSetEnabled,
    screen: CONTROL_CENTER_ACTIONS.perceptionScreenVisionSetEnabled,
    proactive: CONTROL_CENTER_ACTIONS.perceptionProactiveWakeSetEnabled
  };
  return map[featureId] || "";
}

function actionIdForVoiceToggle(key) {
  const map = {
    ttsEnabled: CONTROL_CENTER_ACTIONS.voiceSetTtsEnabled,
    asrEnabled: CONTROL_CENTER_ACTIONS.voiceSetAsrEnabled
  };
  return map[key] || "";
}

function buildAdvancedCoreSwitchState(items) {
  const entries = {};
  for (const item of Array.isArray(items) ? items : []) {
    if (!item?.id) continue;
    entries[item.id] = Boolean(item.enabled);
  }
  return entries;
}

function buildVoiceState(page) {
  return {
    ttsEnabled: Boolean(page?.tts?.enabled),
    asrEnabled: Boolean(page?.asr?.enabled),
    volumePercent: clampVoiceVolumePercent(page?.tts?.volume)
  };
}

function clampVoiceVolumePercent(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 80;
  const percent = number <= 1 ? number * 100 : number;
  return Math.max(0, Math.min(100, Math.round(percent)));
}

function buildScreenVisionState(featureCards) {
  const screenCard = (Array.isArray(featureCards) ? featureCards : []).find((card) => card?.id === "screen");
  const intervalSec = secondsFromIntervalLabel(screenCard?.frequency) || SCREEN_VISION_INTERVAL_OPTIONS_SEC[1];
  return {
    intervalSec: normalizeScreenVisionIntervalSec(intervalSec),
    frameCount: clampScreenVisionFrameCount(parsePositiveInteger(screenCard?.frames, 4))
  };
}

function nextScreenVisionIntervalSec(current, step = 1) {
  const currentValue = normalizeScreenVisionIntervalSec(current);
  const currentIndex = SCREEN_VISION_INTERVAL_OPTIONS_SEC.indexOf(currentValue);
  const startIndex = currentIndex >= 0
    ? currentIndex
    : SCREEN_VISION_INTERVAL_OPTIONS_SEC.findIndex((value) => value >= currentValue);
  const safeIndex = startIndex >= 0 ? startIndex : 0;
  const nextIndex = (safeIndex + Number(step || 1) + SCREEN_VISION_INTERVAL_OPTIONS_SEC.length) % SCREEN_VISION_INTERVAL_OPTIONS_SEC.length;
  return SCREEN_VISION_INTERVAL_OPTIONS_SEC[nextIndex];
}

function normalizeScreenVisionIntervalSec(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return SCREEN_VISION_INTERVAL_OPTIONS_SEC[1];
  const nearest = SCREEN_VISION_INTERVAL_OPTIONS_SEC.reduce((best, candidate) => (
    Math.abs(candidate - number) < Math.abs(best - number) ? candidate : best
  ), SCREEN_VISION_INTERVAL_OPTIONS_SEC[0]);
  return nearest;
}

function clampScreenVisionFrameCount(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 4;
  return Math.max(SCREEN_VISION_FRAME_COUNT_MIN, Math.min(SCREEN_VISION_FRAME_COUNT_MAX, Math.round(number)));
}

function parsePositiveInteger(value, fallback) {
  const match = String(value ?? "").match(/\d+/);
  const number = match ? Number.parseInt(match[0], 10) : Number(fallback);
  return Number.isFinite(number) && number > 0 ? number : fallback;
}

function createRuntimeActionRouter(dataSource) {
  const router = createControlCenterActionRouter({
    dataSource,
    onAfterAction: handleControlCenterActionResult
  });
  router.registerHandlers({
    [CONTROL_CENTER_ACTIONS.perceptionActiveWindowDetails]: () => {
      state.expandedPerceptionCard =
        state.expandedPerceptionCard === "activeWindow" ? null : "activeWindow";
      renderActivePage();
      return { ok: true, refresh: false };
    },
    [CONTROL_CENTER_ACTIONS.abilitiesLogsViewAll]: () => {
      state.showAllAbilityCalls = !state.showAllAbilityCalls;
      renderActivePage();
      return { ok: true, refresh: false };
    },
    [CONTROL_CENTER_ACTIONS.abilitiesProviderConfigOpen]: (payload) => {
      const providerId = String(payload?.providerId || "").trim();
      state.activeProviderConfigId = state.activeProviderConfigId === providerId ? "" : providerId;
      renderActivePage();
      return { ok: true, refresh: false };
    },
    [CONTROL_CENTER_ACTIONS.abilitiesMcpConfigOpen]: (payload) => {
      const serverId = String(payload?.serverId || "").trim();
      state.activeMcpConfigId = state.activeMcpConfigId === serverId ? "" : serverId;
      renderActivePage();
      return { ok: true, refresh: false };
    },
    [CONTROL_CENTER_ACTIONS.abilitiesWorkflowConfigOpen]: (payload) => {
      const workflowId = String(payload?.workflowId || "").trim();
      state.activeWorkflowConfigId = state.activeWorkflowConfigId === workflowId ? "" : workflowId;
      renderActivePage();
      return { ok: true, refresh: false };
    },
    [CONTROL_CENTER_ACTIONS.advancedLogsMore]: () => {
      state.showAllDiagnosticLogs = !state.showAllDiagnosticLogs;
      renderActivePage();
      return { ok: true, refresh: false };
    }
  });
  return router;
}

function handleControlCenterActionResult(result) {
  if (result?.actionId === CONTROL_CENTER_ACTIONS.abilitiesQqSelfCheck) {
    state.qqSelfCheckResult = result;
    renderActivePage();
    return;
  }
  if (!result?.refresh) return;
  scheduleRuntimeSnapshotHydrate();
  if (isTauriRuntime) {
    emitSettingsCommand({ command: "requestSnapshot" }).catch(() => {});
  }
}

async function bindSettingsSnapshotListener() {
  if (!isTauriRuntime) return;
  try {
    await listen(SETTINGS_SNAPSHOT_EVENT, (event) => {
      latestRuntimeSnapshot = event.payload || null;
      applySettingsSnapshotPatch(latestRuntimeSnapshot);
      refreshListeningTogetherCard().catch(() => {});
    });
    await emitSettingsCommand({ command: "requestSnapshot" });
  } catch {
    // settings window may not be open yet
  }
}

async function emitMainEvent(eventName, payload) {
  try {
    await emitTo("main", eventName, payload);
  } catch {
    await emit(eventName, payload);
  }
}

async function emitSettingsCommand(payload) {
  await emitMainEvent(SETTINGS_COMMAND_EVENT, payload);
}

function applySettingsSnapshotPatch(runtimeSnapshot) {
  if (!runtimeSnapshot || typeof runtimeSnapshot !== "object") return;

  const musicRuntime = buildMusicRuntimePatch({
    musicSnapshot: runtimeSnapshot.music,
    petState: runtimeSnapshot.state || {}
  }) || undefined;

  const overviewRuntime = buildOverviewEmotionRuntimePatchFromSettingsSnapshot(runtimeSnapshot) || undefined;
  const characterRuntime = buildCharacterRuntimePatchFromSettingsSnapshot(runtimeSnapshot) || undefined;

  if (!musicRuntime && !overviewRuntime && !characterRuntime) return;

  const nextSignatures = {
    music: musicRuntime ? stableRuntimePatchSignature(musicRuntime) : runtimePatchSignatures.music,
    overview: overviewRuntime ? stableRuntimePatchSignature(overviewRuntime) : runtimePatchSignatures.overview,
    character: characterRuntime ? stableRuntimePatchSignature(characterRuntime) : runtimePatchSignatures.character
  };
  const changed = {
    music: nextSignatures.music !== runtimePatchSignatures.music,
    overview: nextSignatures.overview !== runtimePatchSignatures.overview,
    character: nextSignatures.character !== runtimePatchSignatures.character
  };
  if (!changed.music && !changed.overview && !changed.character) return;
  Object.assign(runtimePatchSignatures, nextSignatures);

  const nextSnapshot = createControlCenterSnapshot({
    navItems,
    labMeta: {
      ...labMeta,
      backgroundAsset,
      defaultPage: labMeta.defaultPage || snapshot.shell.labMeta.defaultPage
    },
    overviewPage,
    characterPage,
    voicePage,
    musicPage,
    perceptionPage,
    abilitiesPage,
    advancedPage,
    musicRuntime,
    overviewRuntime,
    characterRuntime
  });
  applyControlCenterSnapshot(nextSnapshot, {
    renderShell: false,
    renderPage:
      (state.activePage === "music" && changed.music) ||
      (state.activePage === "overview" && changed.overview) ||
      (state.activePage === "character" && changed.character)
  });
}

function stableRuntimePatchSignature(value) {
  try {
    return JSON.stringify(value);
  } catch {
    return "";
  }
}

function scheduleRuntimeSnapshotHydrate(delay = RUNTIME_SNAPSHOT_HYDRATE_DELAY_MS) {
  if (runtimeSnapshotHydrateTimer) return;
  runtimeSnapshotHydrateTimer = window.setTimeout(() => {
    runtimeSnapshotHydrateTimer = 0;
    void hydrateControlCenterSnapshot();
  }, delay);
}
