import * as mockData from "./mock-data.js";
import {
  CONTROL_CENTER_ACTIONS,
  CONTROL_CENTER_BRIDGED_ACTION_IDS,
  createNotImplementedActionResult
} from "./action-router.js";

const DEFAULT_BACKEND_URL = "http://127.0.0.1:9999";
const SETTINGS_COMMAND_EVENT = "akane-next-settings-command";
const bridgedActionIds = new Set(CONTROL_CENTER_BRIDGED_ACTION_IDS);
const providerBackendActionIds = new Set([
  CONTROL_CENTER_ACTIONS.abilitiesProviderConfigSave,
  CONTROL_CENTER_ACTIONS.abilitiesProviderHealthCheck,
  CONTROL_CENTER_ACTIONS.abilitiesProviderTtsTest,
  CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileInspectFolder,
  CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileSave
]);
const workflowBackendActionIds = new Set([
  CONTROL_CENTER_ACTIONS.abilitiesWorkflowConfigSave,
  CONTROL_CENTER_ACTIONS.abilitiesWorkflowFileImport,
  CONTROL_CENTER_ACTIONS.abilitiesWorkflowValidate
]);
const mcpBackendActionIds = new Set([
  CONTROL_CENTER_ACTIONS.abilitiesMcpConfigSave,
  CONTROL_CENTER_ACTIONS.abilitiesMcpDiscover
]);
const approvalPolicyBackendActionIds = new Set([
  CONTROL_CENTER_ACTIONS.abilitiesApprovalPolicySave
]);
const qqBackendActionIds = new Set([
  CONTROL_CENTER_ACTIONS.abilitiesQqSelfCheck
]);
const tauriInvokeOnlyActionIds = new Set([
  CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileAssignToCurrentCharacter,
  CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileClearCurrentCharacter
]);
const settingsCommandByActionId = Object.freeze({
  [CONTROL_CENTER_ACTIONS.chatNew]: "newSession",
  [CONTROL_CENTER_ACTIONS.chatStop]: "stopReply",
  [CONTROL_CENTER_ACTIONS.workspaceOpen]: "openWorkspace",
  [CONTROL_CENTER_ACTIONS.voiceTest]: "testTts",
  [CONTROL_CENTER_ACTIONS.voiceStop]: "stopTts",
  [CONTROL_CENTER_ACTIONS.voiceSetTtsEnabled]: "setVoiceEnabled",
  [CONTROL_CENTER_ACTIONS.voiceSetAsrEnabled]: "setVoiceInputEnabled",
  [CONTROL_CENTER_ACTIONS.voiceSetVolume]: "setVoiceVolume",
  [CONTROL_CENTER_ACTIONS.voicePreviewPlay]: "previewTts",
  [CONTROL_CENTER_ACTIONS.voiceSetSpeed]: "setVoiceSpeed",
  [CONTROL_CENTER_ACTIONS.voiceSetWakeWord]: "setWakeWord",
  [CONTROL_CENTER_ACTIONS.voiceSetWakeSensitivity]: "setWakeSensitivity",
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
  [CONTROL_CENTER_ACTIONS.perceptionRunDiagnostics]: "requestSnapshot",
  [CONTROL_CENTER_ACTIONS.advancedProbeClickThrough]: "probeClickThrough",
  [CONTROL_CENTER_ACTIONS.advancedResetWindow]: "resetWindow",
  [CONTROL_CENTER_ACTIONS.advancedToggleWebgl]: "toggleWebgl",
  [CONTROL_CENTER_ACTIONS.advancedSetHitTestEnabled]: "setHitTestEnabled",
  [CONTROL_CENTER_ACTIONS.advancedSetHitboxOverlay]: "setHitboxOverlay",
  [CONTROL_CENTER_ACTIONS.characterSelectPack]: "setCharacterPack",
  [CONTROL_CENTER_ACTIONS.characterSetOutfit]: "setOutfit",
  [CONTROL_CENTER_ACTIONS.musicPrevious]: "previousMusic",
  [CONTROL_CENTER_ACTIONS.musicNext]: "nextMusic",
  [CONTROL_CENTER_ACTIONS.musicPause]: "toggleMusic",
  [CONTROL_CENTER_ACTIONS.musicStop]: "stopMusic",
  [CONTROL_CENTER_ACTIONS.musicClear]: "clearMusicQueue",
  [CONTROL_CENTER_ACTIONS.musicSetPlayMode]: "setMusicPlayMode",
  [CONTROL_CENTER_ACTIONS.musicSetVolumeNormalization]: "setMusicVolumeNormalization",
  [CONTROL_CENTER_ACTIONS.musicSeek]: "seekMusic",
  [CONTROL_CENTER_ACTIONS.musicSelectQueueItem]: "playMusicTrack",
  [CONTROL_CENTER_ACTIONS.musicPlayWorkspaceRecommendation]: "playWorkspaceAudio"
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
  CONTROL_CENTER_ACTIONS.characterPreviewEmotion,
  CONTROL_CENTER_ACTIONS.musicPlayWorkspaceRecommendation
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
  const fallbackReason = "mock-source";
  return {
    kind: CONTROL_CENTER_SOURCE_KIND.mock,
    backendUrl: null,
    fallbackReason,
    getFallbackReason() {
      return fallbackReason;
    },
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
      const normalizedActionId = normalizeActionId(actionId);
      if (providerBackendActionIds.has(normalizedActionId) || workflowBackendActionIds.has(normalizedActionId) || mcpBackendActionIds.has(normalizedActionId) || approvalPolicyBackendActionIds.has(normalizedActionId) || qqBackendActionIds.has(normalizedActionId) || tauriInvokeOnlyActionIds.has(normalizedActionId)) {
        return createNotImplementedActionResult(normalizedActionId);
      }
      return {
        ok: true,
        status: "mocked",
        actionId,
        payload,
        refresh: true
      };
    },
    async readModelService() {
      return data.modelPage || null;
    },
    async readSettingsCatalog() {
      return null;
    },
    async updateSetting() {
      return { ok: false, status: "not-available" };
    },
    async runModelServiceAction(actionId, payload = {}) {
      return {
        ok: true,
        status: "mocked",
        actionId,
        payload,
        models: actionId === "models" ? ["deepseek-chat", "deepseek-reasoner"] : undefined
      };
    }
  };
}

export function createTauriControlCenterSource(options = {}) {
  const fallbackReason = "tauri-source-awaiting-runtime-snapshot";
  return {
    kind: CONTROL_CENTER_SOURCE_KIND.tauri,
    backendUrl: null,
    fallbackReason,
    getFallbackReason() {
      return fallbackReason;
    },
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
    },
    async readModelService() {
      return null;
    },
    async readSettingsCatalog() {
      return null;
    },
    async updateSetting() {
      return { ok: false, status: "not-available" };
    },
    async runModelServiceAction(actionId) {
      return { ok: false, status: "not-available", actionId };
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
  let lastFallbackReason = null;

  function setFallbackReason(reason) {
    lastFallbackReason = reason;
  }

  const source = {
    kind: CONTROL_CENTER_SOURCE_KIND.backend,
    backendUrl: baseUrl,
    get fallbackReason() {
      return lastFallbackReason;
    },
    getFallbackReason() {
      return lastFallbackReason;
    },
    async readSnapshot() {
      if (typeof fetchImpl !== "function") {
        setFallbackReason("no-fetch-impl");
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
        setFallbackReason(null);
        return snapshotResult;
      }

      setFallbackReason("unified-snapshot-unavailable");
      const [health, diagnostics, workspace, resourceManifest, metrics, capabilitiesCatalog, voiceProfilesCatalog, approvalRequestsCatalog] = await Promise.all([
        fetchJson(fetchImpl, buildBackendUrl(baseUrl, "/health", { t: commonParams.t })),
        fetchJson(fetchImpl, buildBackendUrl(baseUrl, "/desktop-pet/diagnostics", commonParams)),
        fetchJson(fetchImpl, buildBackendUrl(baseUrl, "/desktop-pet/workspace/summary", commonParams)),
        fetchJson(fetchImpl, buildBackendUrl(baseUrl, "/resource-manifest", commonParams)),
        fetchText(fetchImpl, buildBackendUrl(baseUrl, "/metrics", { t: commonParams.t })),
        readCapabilitiesCatalog(fetchImpl, baseUrl, commonParams),
        readVoiceProfilesCatalog(fetchImpl, baseUrl, commonParams),
        readApprovalRequestsCatalog(fetchImpl, baseUrl, commonParams)
      ]);
      if (![health, diagnostics, workspace, resourceManifest, metrics, capabilitiesCatalog, voiceProfilesCatalog, approvalRequestsCatalog].some((item) => item.ok)) {
        setFallbackReason("all-backend-endpoints-failed");
        return null;
      }
      setFallbackReason(null);
      // KNOWN PROTOTYPE STATE (control-center-LAB): the live/backend snapshot
      // still starts from mockData and patches real values over it by row label
      // (data-adapter.js::patchRowsByLabel). Rows the backend doesn't supply stay
      // mock even though sourceKind=backend / fallbackReason=null. Intentional
      // scaffolding while panels are wired up one by one — do NOT "fix" it by
      // dropping ...mockData (panels expect the skeleton). Real resolution =
      // finish per-panel live wiring, or mark still-mock rows in the UI.
      return {
        ...mockData,
        sourceKind: CONTROL_CENTER_SOURCE_KIND.backend,
        backendUrl: baseUrl,
        fallbackReason: null,
        controlCenterRuntime: {
          backendBaseUrl: baseUrl,
          health,
          diagnostics,
          workspace,
          resourceManifest,
          metrics,
          capabilitiesCatalog,
          voiceProfilesCatalog,
          approvalRequestsCatalog
        },
        overviewRuntime: buildOverviewRuntimePatch({
          health: health.data,
          diagnostics: diagnostics.data,
          workspace: workspace.data,
          metricsText: metrics.data,
          petState,
          baseUrl,
          resourceManifest: resourceManifest.data,
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
          petState,
          capabilitiesCatalog: capabilitiesCatalog.data
        }),
        perceptionRuntime: buildPerceptionRuntimePatch({
          petState,
          diagnostics: diagnostics.data
        }),
        musicRuntime: buildMusicRuntimePatch({ musicSnapshot, petState }),
        abilitiesRuntime: buildAbilitiesRuntimePatch({
          diagnostics: diagnostics.data,
          workspace: workspace.data,
          capabilitiesCatalog: capabilitiesCatalog.data,
          voiceProfilesCatalog: voiceProfilesCatalog.data,
          approvalRequestsCatalog: approvalRequestsCatalog.data,
          connected: health.ok || diagnostics.ok || capabilitiesCatalog.ok || voiceProfilesCatalog.ok || approvalRequestsCatalog.ok
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
    async readModelService() {
      if (typeof fetchImpl !== "function") return null;
      const result = await fetchJson(
        fetchImpl,
        buildBackendUrl(baseUrl, "/control-center/model-service", { t: String(Date.now()) })
      );
      return result.ok && result.data && typeof result.data === "object" ? result.data : null;
    },
    async readSettingsCatalog() {
      if (typeof fetchImpl !== "function") return null;
      const result = await fetchJson(
        fetchImpl,
        buildBackendUrl(baseUrl, "/control-center/settings-catalog", { t: String(Date.now()) })
      );
      return result.ok && result.data && typeof result.data === "object" ? result.data : null;
    },
    async updateSetting(key, value) {
      if (typeof fetchImpl !== "function") return { ok: false, status: "not-available" };
      try {
        const response = await fetchImpl(
          buildBackendUrl(baseUrl, `/control-center/settings-catalog/${encodeURIComponent(key)}`),
          {
            method: "POST",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify({ value }),
            cache: "no-store"
          }
        );
        const data = await response.json();
        return data && typeof data === "object" ? data : { ok: false, status: "bad-response" };
      } catch (error) {
        return { ok: false, status: "request-failed" };
      }
    },
    async runModelServiceAction(actionId, payload = {}) {
      if (typeof fetchImpl !== "function") {
        return { ok: false, status: "not-available", actionId };
      }
      const actionPath = actionId === "save" ? "" : `/${encodeURIComponent(actionId)}`;
      try {
        const response = await fetchImpl(
          buildBackendUrl(baseUrl, `/control-center/model-service${actionPath}`),
          {
            method: "POST",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify(payload),
            cache: "no-store"
          }
        );
        const result = await readActionResponse(response);
        return {
          ...result,
          ok: response.ok && Boolean(result?.ok),
          actionId,
          status: result?.status || (response.ok ? "available" : `http-${response.status}`)
        };
      } catch (error) {
        return {
          ok: false,
          status: "request-failed",
          actionId,
          error: formatDataSourceError(error)
        };
      }
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

      if (providerBackendActionIds.has(normalizedActionId) || workflowBackendActionIds.has(normalizedActionId) || mcpBackendActionIds.has(normalizedActionId) || approvalPolicyBackendActionIds.has(normalizedActionId) || qqBackendActionIds.has(normalizedActionId)) {
        if (typeof fetchImpl !== "function") {
          return createNotImplementedActionResult(normalizedActionId);
        }
        const routeAction = providerBackendActionIds.has(normalizedActionId)
          ? runProviderBackendAction
          : workflowBackendActionIds.has(normalizedActionId)
            ? runWorkflowBackendAction
            : mcpBackendActionIds.has(normalizedActionId)
              ? runMcpBackendAction
              : qqBackendActionIds.has(normalizedActionId)
                ? runQqBackendAction
                : runApprovalPolicyBackendAction;
        return routeAction(fetchImpl, baseUrl, normalizedActionId, payload, {
          user_id: sessionId,
          real_user_id: profileUserId,
          client,
          t: String(Date.now())
        });
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
  return source;
}

async function runApprovalPolicyBackendAction(fetchImpl, baseUrl, actionId, payload = {}, params = {}) {
  const body = {
    defaultMode: String(payload.defaultMode || payload.default_mode || payload.value || "").trim()
  };
  try {
    const response = await fetchImpl(buildBackendUrl(baseUrl, "/capabilities/approval-policy", params), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
      cache: "no-store"
    });
    if (response.status === 404 || response.status === 405) {
      return createNotImplementedActionResult(actionId);
    }
    if (!response.ok) {
      return { ok: false, status: `http-${response.status}`, actionId, refresh: false };
    }
    const result = await readActionResponse(response);
    return {
      ...result,
      ok: Boolean(result?.ok),
      actionId,
      refresh: result?.refresh === undefined ? true : Boolean(result.refresh)
    };
  } catch (error) {
    return { ok: false, status: "request-failed", actionId, refresh: false, error: formatDataSourceError(error) };
  }
}

async function runQqBackendAction(fetchImpl, baseUrl, actionId, payload = {}, params = {}) {
  // abilities.qq.selfCheck → POST /api/qq/self-check
  // 不暴露 token/cookie/本地路径；结果里敏感字段由后端过滤
  try {
    const response = await fetchImpl(buildBackendUrl(baseUrl, "/api/qq/self-check", params), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({}),
      cache: "no-store"
    });
    if (response.status === 404 || response.status === 405) {
      return createNotImplementedActionResult(actionId);
    }
    if (!response.ok) {
      return { ok: false, status: `http-${response.status}`, actionId, refresh: false };
    }
    const envelope = await readActionResponse(response);
    const data = envelope?.data || envelope || {};
    return {
      ok: Boolean(data.ok),
      status: String(data.status || (data.ok ? "connected" : "failed")),
      actionId,
      refresh: false,
      reason: String(data.reason || ""),
      payload: {
        selfCheckStatus: String(data.status || ""),
        reason: String(data.reason || ""),
        onebotHttpUrl: String(data.onebot_http_url || ""),
        botQq: String(data.bot_qq || ""),
        nickname: String(data.nickname || ""),
        checks: data.checks && typeof data.checks === "object" ? data.checks : {}
      }
    };
  } catch (error) {
    return { ok: false, status: "request-failed", actionId, refresh: false, error: formatDataSourceError(error) };
  }
}

async function runMcpBackendAction(fetchImpl, baseUrl, actionId, payload = {}, params = {}) {
  const serverId = String(payload.serverId || payload.server_id || payload.id || "").trim();
  if (!serverId) {
    return { ok: false, status: "invalid-payload", actionId, refresh: false, error: "serverId is required" };
  }
  const endpoint = `/capabilities/mcp-servers/${encodeURIComponent(serverId)}/${mcpActionPath(actionId)}`;
  const body = buildMcpActionBody(actionId, payload);
  try {
    const response = await fetchImpl(buildBackendUrl(baseUrl, endpoint, params), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
      cache: "no-store"
    });
    if (response.status === 404 || response.status === 405) {
      return createNotImplementedActionResult(actionId);
    }
    if (!response.ok) {
      return { ok: false, status: `http-${response.status}`, actionId, serverId, refresh: false };
    }
    const result = await readActionResponse(response);
    return {
      ...result,
      ok: Boolean(result?.ok),
      actionId,
      serverId,
      refresh: result?.refresh === undefined ? true : Boolean(result.refresh)
    };
  } catch (error) {
    return { ok: false, status: "request-failed", actionId, serverId, refresh: false, error: formatDataSourceError(error) };
  }
}

function mcpActionPath(actionId) {
  if (actionId === CONTROL_CENTER_ACTIONS.abilitiesMcpConfigSave) return "config";
  return "discover";
}

function buildMcpActionBody(actionId, payload = {}) {
  if (actionId !== CONTROL_CENTER_ACTIONS.abilitiesMcpConfigSave) return {};
  const body = {
    enabled: Boolean(payload.enabled),
    transport: "stdio",
    command: String(payload.command || "").trim()
  };
  const displayName = String(payload.displayName || payload.name || "").trim();
  if (displayName) body.displayName = displayName;
  const cwd = String(payload.cwd || "").trim();
  if (cwd) body.cwd = cwd;
  const args = Array.isArray(payload.args)
    ? payload.args.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  if (args.length) body.args = args;
  const env = payload.env && typeof payload.env === "object" && !Array.isArray(payload.env) ? payload.env : {};
  const safeEnv = {};
  for (const [key, value] of Object.entries(env)) {
    const envKey = String(key || "").trim();
    const envValue = String(value || "").trim();
    if (envKey && envValue) safeEnv[envKey] = envValue;
  }
  if (Object.keys(safeEnv).length) body.env = safeEnv;
  return body;
}

async function runWorkflowBackendAction(fetchImpl, baseUrl, actionId, payload = {}, params = {}) {
  const workflowId = String(payload.workflowId || payload.workflow_id || payload.id || "").trim();
  if (!workflowId) {
    return { ok: false, status: "invalid-payload", actionId, refresh: false, error: "workflowId is required" };
  }
  const endpoint = `/capabilities/workflows/${encodeURIComponent(workflowId)}/${workflowActionPath(actionId)}`;
  const body = buildWorkflowActionBody(actionId, payload);
  try {
    const response = await fetchImpl(buildBackendUrl(baseUrl, endpoint, params), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
      cache: "no-store"
    });
    if (response.status === 404 || response.status === 405) {
      return createNotImplementedActionResult(actionId);
    }
    if (!response.ok) {
      return { ok: false, status: `http-${response.status}`, actionId, workflowId, refresh: false };
    }
    const result = await readActionResponse(response);
    return {
      ...result,
      ok: Boolean(result?.ok),
      actionId,
      workflowId,
      refresh: result?.refresh === undefined ? true : Boolean(result.refresh)
    };
  } catch (error) {
    return { ok: false, status: "request-failed", actionId, workflowId, refresh: false, error: formatDataSourceError(error) };
  }
}

function workflowActionPath(actionId) {
  if (actionId === CONTROL_CENTER_ACTIONS.abilitiesWorkflowFileImport) return "file";
  if (actionId === CONTROL_CENTER_ACTIONS.abilitiesWorkflowConfigSave) return "config";
  return "validate";
}

function buildWorkflowActionBody(actionId, payload = {}) {
  if (actionId === CONTROL_CENTER_ACTIONS.abilitiesWorkflowFileImport) {
    return {
      workflowPath: String(payload.workflowPath || "").trim(),
      workflowJson: String(payload.workflowJson || payload.workflowText || "")
    };
  }
  if (actionId === CONTROL_CENTER_ACTIONS.abilitiesWorkflowConfigSave) {
    return {
      enabled: Boolean(payload.enabled),
      workflowPath: String(payload.workflowPath || "").trim(),
      slotMapping: {
        input_image_handle: String(payload.inputImageSlot || payload.input_image_handle || "").trim(),
        output_image_handle: String(payload.outputImageSlot || payload.output_image_handle || "").trim()
      }
    };
  }
  return {};
}

async function runProviderBackendAction(fetchImpl, baseUrl, actionId, payload = {}, params = {}) {
  const providerId = String(payload.providerId || payload.provider_id || "").trim();
  if (!providerId) {
    return { ok: false, status: "invalid-payload", actionId, refresh: false, error: "providerId is required" };
  }
  if (actionId === CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileSave) {
    const voiceProfileId = String(payload.voiceProfileId || payload.voice_profile_id || payload.profileId || "").trim();
    if (!voiceProfileId) {
      return { ok: false, status: "invalid-payload", actionId, providerId, refresh: false, error: "voiceProfileId is required" };
    }
  }
  if (actionId === CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileInspectFolder) {
    const folderPath = String(payload.folderPath || payload.modelFolderPath || payload.path || "").trim();
    if (!folderPath) {
      return { ok: false, status: "invalid-payload", actionId, providerId, refresh: false, error: "folderPath is required" };
    }
  }
  const endpoint = `/capabilities/providers/${encodeURIComponent(providerId)}/${providerActionPath(actionId, payload)}`;
  const body = buildProviderActionBody(actionId, payload);
  try {
    const response = await fetchImpl(buildBackendUrl(baseUrl, endpoint, params), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
      cache: "no-store"
    });
    if (response.status === 404 || response.status === 405) {
      return createNotImplementedActionResult(actionId);
    }
    if (!response.ok) {
      return { ok: false, status: `http-${response.status}`, actionId, refresh: false };
    }
    const result = await readActionResponse(response);
    return {
      ...result,
      ok: Boolean(result?.ok),
      actionId,
      providerId,
      refresh: result?.refresh === undefined ? true : Boolean(result.refresh)
    };
  } catch (error) {
    return { ok: false, status: "request-failed", actionId, providerId, refresh: false, error: formatDataSourceError(error) };
  }
}

function providerActionPath(actionId, payload = {}) {
  if (actionId === CONTROL_CENTER_ACTIONS.abilitiesProviderConfigSave) return "config";
  if (actionId === CONTROL_CENTER_ACTIONS.abilitiesProviderTtsTest) return "tts-test";
  if (actionId === CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileInspectFolder) return "voice-profiles/inspect-folder";
  if (actionId === CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileSave) {
    const voiceProfileId = String(payload.voiceProfileId || payload.voice_profile_id || payload.profileId || "").trim();
    return `voice-profiles/${encodeURIComponent(voiceProfileId)}/config`;
  }
  return "health-check";
}

function buildProviderActionBody(actionId, payload = {}) {
  const endpoint = String(payload.endpoint || "").trim();
  if (actionId === CONTROL_CENTER_ACTIONS.abilitiesProviderTtsTest) {
    const body = {
      ...(endpoint ? { endpoint } : {}),
      text: String(payload.text || payload.testText || "").trim(),
      voiceProfileId: String(payload.voiceProfileId || payload.profileId || "").trim()
    };
    for (const [bodyKey, payloadKey] of [
      ["textLang", "textLang"],
      ["promptLang", "promptLang"],
      ["mediaType", "mediaType"],
      ["refAudioPath", "refAudioPath"],
      ["promptText", "promptText"]
    ]) {
      const value = String(payload[payloadKey] || "").trim();
      if (value) body[bodyKey] = value;
    }
    return body;
  }
  if (actionId === CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileInspectFolder) {
    return {
      folderPath: String(payload.folderPath || payload.modelFolderPath || payload.path || "").trim()
    };
  }
  if (actionId === CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileSave) {
    const body = {
      enabled: payload.voiceProfileEnabled === undefined ? true : Boolean(payload.voiceProfileEnabled)
    };
    for (const [bodyKey, payloadKey] of [
      ["displayName", "displayName"],
      ["textLang", "textLang"],
      ["promptLang", "promptLang"],
      ["mediaType", "mediaType"],
      ["refAudioPath", "refAudioPath"],
      ["promptText", "promptText"]
    ]) {
      const value = String(payload[payloadKey] || "").trim();
      if (value) body[bodyKey] = value;
    }
    return {
      ...body
    };
  }
  if (actionId === CONTROL_CENTER_ACTIONS.abilitiesProviderConfigSave) {
    return {
      enabled: Boolean(payload.enabled),
      ...(endpoint ? { endpoint } : {})
    };
  }
  return endpoint ? { endpoint } : {};
}

function buildWorkspaceRecommendationValue(payload = {}) {
  const itemType = payload.itemType === "generated" ? "generated" : "attachment";
  const handle = String(payload.handle || "").trim();
  const title = String(payload.title || "").trim();
  if (!handle) return null;
  return { itemType, handle, title };
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
    if (actionId === CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileAssignToCurrentCharacter) {
      return await runTauriCharacterVoiceProfileAssignment(payload, context, options, bridge);
    }
    if (actionId === CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileClearCurrentCharacter) {
      return await runTauriCharacterVoiceProfileClear(payload, context, options, bridge);
    }

    if (command && typeof bridge.invoke === "function") {
      await bridge.invoke(command, {});
      return { ok: true, status: "executed", actionId, payload, refresh: true };
    }

    if (settingsCommand && typeof bridge.emit === "function") {
      if (actionId === CONTROL_CENTER_ACTIONS.musicPlayWorkspaceRecommendation) {
        const value = buildWorkspaceRecommendationValue(payload);
        if (!value) {
          return { ok: false, status: "invalid-payload", actionId, error: "missing handle" };
        }
        await bridge.emit(SETTINGS_COMMAND_EVENT, { command: settingsCommand, value, source: context?.source || "control-center" });
        return { ok: true, status: "executed", actionId, payload: value, refresh: true };
      }
      await bridge.emit(SETTINGS_COMMAND_EVENT, {
        ...payload,
        command: settingsCommand,
        value: payload?.value ?? payload?.text ?? null,
        source: context?.source || payload?.source || "control-center"
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

async function runTauriCharacterVoiceProfileAssignment(payload = {}, context = {}, options = {}, bridge) {
  if (typeof bridge.invoke !== "function") {
    return createNotImplementedActionResult(CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileAssignToCurrentCharacter);
  }
  const voiceProfileId = String(payload.voiceProfileId || payload.voice_profile_id || payload.profileId || payload.value || "").trim();
  const packId = resolveCharacterVoiceActionPackId(payload, options);
  if (!voiceProfileId || !packId) {
    return {
      ok: false,
      status: "invalid-payload",
      actionId: CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileAssignToCurrentCharacter,
      refresh: false,
      error: !voiceProfileId ? "voiceProfileId is required" : "characterPackId is required"
    };
  }
  const provider = String(payload.provider || payload.voiceProvider || payload.providerId || "gpt_sovits").trim();
  const notes = String(payload.notes || `控制中心声线 ${voiceProfileId}`).trim();
  const request = {
    packId,
    provider,
    profileId: voiceProfileId,
    ...(notes ? { notes } : {})
  };
  const result = await bridge.invoke("set_character_voice_profile", { request });
  if (typeof bridge.emit === "function") {
    await bridge.emit(SETTINGS_COMMAND_EVENT, {
      command: "refreshCharacterPacks",
      value: { selectPackId: packId, apply: true },
      source: context?.source || payload?.source || "control-center"
    });
  }
  return {
    ok: true,
    status: "assigned",
    actionId: CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileAssignToCurrentCharacter,
    provider,
    voiceProfileId,
    characterPackId: packId,
    characterName: String(result?.profile?.identity?.name || ""),
    refresh: true
  };
}

async function runTauriCharacterVoiceProfileClear(payload = {}, context = {}, options = {}, bridge) {
  if (typeof bridge.invoke !== "function") {
    return createNotImplementedActionResult(CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileClearCurrentCharacter);
  }
  const packId = resolveCharacterVoiceActionPackId(payload, options);
  if (!packId) {
    return {
      ok: false,
      status: "invalid-payload",
      actionId: CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileClearCurrentCharacter,
      refresh: false,
      error: "characterPackId is required"
    };
  }
  const result = await bridge.invoke("clear_character_voice_profile", { packId });
  if (typeof bridge.emit === "function") {
    await bridge.emit(SETTINGS_COMMAND_EVENT, {
      command: "refreshCharacterPacks",
      value: { selectPackId: packId, apply: true },
      source: context?.source || payload?.source || "control-center"
    });
  }
  return {
    ok: true,
    status: "cleared",
    actionId: CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileClearCurrentCharacter,
    characterPackId: packId,
    characterName: String(result?.profile?.identity?.name || ""),
    refresh: true
  };
}

function resolveCharacterVoiceActionPackId(payload = {}, options = {}) {
  return String(
    payload.characterPackId ||
      payload.character_pack_id ||
      payload.packId ||
      payload.pack_id ||
      options.characterPackId ||
      ""
  ).trim();
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

async function readCapabilitiesCatalog(fetchImpl, baseUrl, params = {}) {
  const result = await fetchJson(fetchImpl, buildBackendUrl(baseUrl, "/capabilities", params));
  if (!result.ok) {
    return { ok: false, status: result.status || "unavailable", data: null, error: result.error || null };
  }
  const payload = asObject(result.data);
  if (payload.ok !== true || !Array.isArray(payload.capabilities)) {
    return { ok: false, status: "invalid-capabilities-catalog", data: null };
  }
  return {
    ok: true,
    status: payload.status || result.status || "available",
    data: payload
  };
}

async function readVoiceProfilesCatalog(fetchImpl, baseUrl, params = {}) {
  const result = await fetchJson(fetchImpl, buildBackendUrl(baseUrl, "/capabilities/voice-profiles", params));
  if (!result.ok) {
    return { ok: false, status: result.status || "unavailable", data: null, error: result.error || null };
  }
  const payload = asObject(result.data);
  if (payload.ok !== true || !Array.isArray(payload.voiceProfiles)) {
    return { ok: false, status: "invalid-voice-profiles-catalog", data: null };
  }
  return {
    ok: true,
    status: payload.status || result.status || "available",
    data: payload
  };
}

async function readApprovalRequestsCatalog(fetchImpl, baseUrl, params = {}) {
  const result = await fetchJson(fetchImpl, buildBackendUrl(baseUrl, "/capabilities/approval-requests", params));
  if (!result.ok) {
    return { ok: false, status: result.status || "unavailable", data: null, error: result.error || null };
  }
  const payload = asObject(result.data);
  if (payload.ok !== true || !Array.isArray(payload.approvalRequests)) {
    return { ok: false, status: "invalid-approval-requests", data: null };
  }
  return {
    ok: true,
    status: payload.status || result.status || "available",
    data: payload
  };
}

function buildOverviewRuntimePatch({ health, diagnostics, workspace, metricsText, petState, baseUrl, resourceManifest, connected }) {
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
  const activeEmotionId = String(petState?.currentEmotion || defaultEmotion || "").trim();
  const emotionCount = positiveNumber(resources.emotion_count ?? resources.emotionCount);

  const manifest = asObject(resourceManifest);
  const rawOutfits = asArray(asObject(manifest.characters).outfits);
  const activeOutfitId = String(petState?.outfit || outfit || resources.outfit || "").trim();
  const activeOutfit = findManifestEntry(rawOutfits, activeOutfitId) || rawOutfits.find(Boolean) || null;
  const emotionCards = normalizeEmotionCards(asArray(activeOutfit?.emotions), { activeEmotionId, baseUrl });
  const activeEmotion = emotionCards.find((card) =>
    card.id === activeEmotionId || card.name === activeEmotionId
  );
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
      name: activeEmotion?.name || activeEmotionId || defaultEmotion || "微笑",
      image: activeEmotion?.image || activeEmotion?.url || activeEmotion?.key || activeEmotionId || ""
    },
    voice: {
      ttsEnabled: Boolean(health?.contracts?.desktop_pet?.tts || health?.contracts?.desktop_pet?.health || serviceOk),
      asrEnabled: serviceOk,
      status: serviceOk ? "语音状态：后端已连接" : "语音状态：等待连接"
    },
    ...(senseRuntime ? { sense: senseRuntime } : {}),
    recentOutputs: buildRecentOutputsPatch(workspace),
    abilities: buildAbilityLabels({ tools, workspaceCounts: effectiveWorkspaceCounts }),
    health: buildHealthTiles({
      metrics,
      runtimeMetrics,
      workspaceCounts: effectiveWorkspaceCounts,
      health,
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
  const availablePacks = normalizeAvailableCharacterPacks(availableCharacterPacks, packId);
  const pack = findAvailableCharacterPack(availableCharacterPacks, packId);
  const profile = asObject(pack?.profile);
  const identity = asObject(profile.identity);
  const appearance = asObject(profile.appearance);
  const assets = asObject(profile.assets);
  const voice = normalizeCharacterVoicePreference(profile.voice);
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
    ...(packId ? { selectedPackId: packId } : {}),
    ...(availablePacks.length ? { availablePacks } : {}),
    voice,
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

export function buildCharacterRuntimePatchFromSettingsSnapshot(runtimeSnapshot) {
  const character = asObject(runtimeSnapshot?.character);
  const petState = asObject(runtimeSnapshot?.state);
  const resource = asObject(runtimeSnapshot?.resource);
  const currentExpression = asObject(runtimeSnapshot?.currentExpression);
  const selectedPackId = stringValue(
    petState.characterPackId ||
      character.packId ||
      currentExpression.characterPackId ||
      resource.characterPackId
  );
  const availablePacks = normalizeAvailableCharacterPacks(character.availablePacks, selectedPackId);
  const activePack = availablePacks.find((pack) => pack.selected || pack.id === selectedPackId) || null;
  const displayName = stringValue(
    character.appName ||
      character.name ||
      activePack?.appName ||
      activePack?.name ||
      selectedPackId
  );
  const schemaVersion = stringValue(character.schemaVersion || activePack?.schemaVersion);
  const defaultOutfit = stringValue(character.defaultOutfit || activePack?.defaultOutfit || resource.defaultOutfit);
  const defaultEmotion = stringValue(character.defaultEmotion || activePack?.defaultEmotion || resource.defaultEmotion);
  const assetCount = positiveNumber(character.assetCount || activePack?.assetCount);
  const resourceEmotionCount = positiveNumber(resource.emotionCount);
  const voice = normalizeCharacterVoicePreference(character.voice);

  const patch = {
    ...(displayName ? { selectedPack: displayName } : {}),
    ...(selectedPackId ? { selectedPackId } : {}),
    ...(availablePacks.length ? { availablePacks } : {}),
    voice
  };

  if (displayName || schemaVersion || defaultOutfit || defaultEmotion || assetCount || resourceEmotionCount) {
    patch.packInfo = [
      { label: "名称", value: displayName || selectedPackId || "当前角色包" },
      { label: "版本", value: schemaVersion || "-" },
      { label: "作者", value: "本地角色包" },
      { label: "描述", value: [defaultOutfit, defaultEmotion].filter(Boolean).join(" · ") || "等待资源同步" }
    ];
    patch.resources = [
      { label: "动作资源", value: assetCount ? `${assetCount}` : "预留", tone: "blue" },
      { label: "表情资源", value: resourceEmotionCount ? `${resourceEmotionCount}` : "等待同步", tone: "green" },
      { label: "服装资源", value: Array.isArray(resource.outfits) ? `${resource.outfits.length}` : "等待同步", tone: "pink" },
      { label: "背景资源", value: "预留", tone: "green" }
    ];
  }

  return Object.keys(patch).length ? patch : null;
}

function normalizeCharacterVoicePreference(value) {
  const source = asObject(value);
  return {
    provider: stringValue(source.provider),
    profileId: stringValue(source.profileId || source.profile_id),
    notes: stringValue(source.notes)
  };
}

function buildVoiceRuntimePatch({ health, diagnostics, petState, capabilitiesCatalog }) {
  const healthData = asObject(health);
  const diagnosticsData = asObject(diagnostics);
  const runtime = asObject(diagnosticsData.runtime);
  const runtimeMetrics = asObject(runtime.metrics);
  const healthTts = asObject(healthData.tts);
  const healthAsr = asObject(healthData.asr);
  const ttsResolution = normalizeVoiceProviderResolution(capabilitiesCatalog, "voice.tts.character");
  const asrResolution = normalizeVoiceProviderResolution(capabilitiesCatalog, "voice.input.asr");

  const serviceOk = stringValue(healthData?.status) === "ok" || stringValue(diagnosticsData?.status) === "ok";
  const ttsEnabled = petState?.voiceEnabled ?? healthTts.enabled ?? serviceOk;
  const ttsVolume = coerceVolumePercent(petState?.voiceVolume, 80);
  const asrAvailable = Boolean(healthAsr.endpoint || Object.keys(healthAsr).length);
  const asrEnabled = petState?.voiceInputEnabled ?? asrAvailable;
  const overallState = serviceOk ? "正常运行" : "未连接";
  const overallTone = serviceOk ? "good" : "warning";
  const ttsOnline = ttsResolution?.activeProviderName || (healthTts.endpoint ? "在线" : serviceOk ? "未启用" : "离线");
  const ttsTone = ttsResolution?.statusTone || (healthTts.endpoint ? "good" : "warning");
  const asrOnline = asrResolution?.activeProviderName || (asrAvailable ? "在线" : "未启用");
  const asrTone = asrResolution?.statusTone || (asrAvailable ? "good" : "muted");
  const networkState = serviceOk ? "良好" : "离线";
  const networkTone = serviceOk ? "good" : "warning";
  const latency = inferLatencyLabel(runtimeMetrics);

  const petVoiceSpeed = String(petState?.voiceSpeed ?? "").trim();
  const petWakeWord = String(petState?.wakeWord ?? "").trim();
  const petWakeSensitivity = String(petState?.wakeSensitivity ?? "").trim();

  return {
    tts: {
      enabled: Boolean(ttsEnabled),
      volume: ttsVolume,
      ...(ttsResolution ? { providerStatus: ttsResolution } : {}),
      ...(petVoiceSpeed ? { speed: petVoiceSpeed } : {}),
    },
    asr: {
      enabled: Boolean(asrEnabled),
      ...(asrResolution ? { providerStatus: asrResolution } : {})
    },
    ...(petWakeWord ? { wakeWord: petWakeWord } : {}),
    ...(petWakeSensitivity ? { wakeSensitivity: petWakeSensitivity } : {}),
    diagnostics: [
      { label: "整体状态", value: overallState, tone: overallTone },
      { label: "TTS 语音引擎", value: ttsOnline, tone: ttsTone },
      { label: "ASR 语音引擎", value: asrOnline, tone: asrTone },
      { label: "响应延迟", value: latency, tone: serviceOk ? "good" : "warning" },
      { label: "网络状态", value: networkState, tone: networkTone }
    ]
  };
}

function normalizeVoiceProviderResolution(catalog, capabilityId) {
  const resolutions = asObject(asObject(catalog).resolutions);
  const raw = asObject(resolutions[capabilityId]);
  if (!raw.capabilityId && !raw.activeProviderId && !raw.requestedProviderId) return null;
  const status = stringValue(raw.status || "unavailable");
  const reason = stringValue(raw.reason);
  return {
    capabilityId: stringValue(raw.capabilityId || capabilityId),
    status,
    statusLabel: voiceProviderStatusLabel(status),
    statusTone: voiceProviderStatusTone(status),
    reason,
    reasonLabel: voiceProviderReasonLabel(reason),
    requestSource: stringValue(raw.requestSource),
    requestedProviderId: stringValue(raw.requestedProviderId),
    requestedProviderName: stringValue(raw.requestedProviderName || raw.requestedProviderId),
    activeProviderId: stringValue(raw.activeProviderId),
    activeProviderName: stringValue(raw.activeProviderName || raw.activeProviderId),
    fallbackProviderId: stringValue(raw.fallbackProviderId),
    voiceProfileId: stringValue(raw.voiceProfileId)
  };
}

function voiceProviderStatusLabel(status) {
  const labels = {
    ready: "已就绪",
    degraded: "已降级",
    unavailable: "不可用"
  };
  return labels[status] || "待确认";
}

function voiceProviderStatusTone(status) {
  if (status === "ready") return "good";
  if (status === "degraded") return "warning";
  return "muted";
}

function voiceProviderReasonLabel(reason) {
  const labels = {
    requested_voice_profile_missing: "角色声线档案未配置，先使用兜底通道",
    requested_provider_missing_config: "请求的语音服务还未配置",
    requested_provider_missing_executor: "请求的本地执行器未安装或未发现",
    requested_provider_missing_model: "请求的声线模型缺失",
    requested_provider_unreachable: "请求的语音服务暂时未连接",
    requested_provider_disabled: "请求的语音服务已关闭",
    requested_provider_pending_health_check: "请求的语音服务已保存，等待连接检查",
    requested_provider_unknown: "角色请求的语音服务暂不认识",
    no_ready_provider: "没有可用的语音通道"
  };
  return labels[reason] || reason || "";
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

export function buildMusicRuntimePatch({ musicSnapshot, petState }) {
  if (!musicSnapshot || typeof musicSnapshot !== "object") return null;

  const track = musicSnapshot.track;
  const localDisplayName = stringValue(track?.displayName || musicSnapshot.displayName);
  const systemMedia = normalizeSystemMediaRuntime(musicSnapshot.systemMedia, musicSnapshot.systemLyrics);
  const hasSystemTrack = systemMedia.ready
    && !Boolean(musicSnapshot.playing)
    && !Boolean(musicSnapshot.paused)
    && !Boolean(localDisplayName)
    && !Number(musicSnapshot.queueCount);
  const hasTrack = Boolean(localDisplayName) && !hasSystemTrack;
  const displayName = hasTrack ? localDisplayName : hasSystemTrack ? systemMedia.title : "";
  const queue = Array.isArray(musicSnapshot.queue) ? musicSnapshot.queue : [];
  const rawQueueIndex = Number(musicSnapshot.queueIndex);
  const queueIndex = Number.isInteger(rawQueueIndex) ? rawQueueIndex : -1;
  const activeQueueIndex = queueIndex >= 0 && queueIndex < queue.length ? queueIndex : -1;
  const progressSec = hasSystemTrack
    ? systemMedia.positionSeconds
    : Number.isFinite(musicSnapshot.progressSeconds) ? Math.max(0, musicSnapshot.progressSeconds) : 0;
  const durationSec = hasSystemTrack
    ? systemMedia.durationSeconds
    : Number.isFinite(musicSnapshot.durationSeconds) ? Math.max(0, musicSnapshot.durationSeconds) : 0;
  const progress = durationSec > 0 ? Math.min(100, Math.round((progressSec / durationSec) * 100)) : 0;
  const volume = coerceVolumePercent(petState?.voiceVolume, 68);
  const currentLyric = musicSnapshot.currentLyric && typeof musicSnapshot.currentLyric === "object"
    ? musicSnapshot.currentLyric
    : null;
  const hasLyrics = Boolean(currentLyric && currentLyric.lineCount > 0);
  const hasSystemLyrics = hasSystemTrack && Boolean(systemMedia.lyrics.current || systemMedia.lyrics.previous || systemMedia.lyrics.next);

  const nowPlaying = {
    title: hasTrack ? displayName : "暂无播放",
    artist: hasTrack ? "" : "",
    quality: hasTrack ? (track?.timelineQuality || track?.extension || "本地文件") : "",
    elapsed: formatSeconds(progressSec),
    duration: formatSeconds(durationSec),
    progressSeconds: progressSec,
    durationSeconds: durationSec,
    progress,
    volume,
    playing: Boolean(musicSnapshot.playing),
    paused: Boolean(musicSnapshot.paused),
    cover: "music"
  };
  if (hasSystemTrack) {
    nowPlaying.title = systemMedia.title;
    nowPlaying.artist = systemMedia.artist;
    nowPlaying.quality = "系统媒体";
    nowPlaying.playing = systemMedia.isPlaying;
    nowPlaying.paused = systemMedia.playbackStatus === "paused";
  }

  const playlist = hasTrack
    ? queue.map((item, index) => ({
        id: item.sourceId || item.id || item.fileName || item.displayName || `queue_${index + 1}`,
        sourceId: item.sourceId || item.id || "",
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
  } else if (hasSystemLyrics) {
    const lines = [];
    if (systemMedia.lyrics.previous) lines.push(systemMedia.lyrics.previous);
    const currentIndex = systemMedia.lyrics.current ? lines.length : -1;
    if (systemMedia.lyrics.current) lines.push(systemMedia.lyrics.current);
    if (systemMedia.lyrics.next) lines.push(systemMedia.lyrics.next);
    lyrics = lines;
    activeLyric = currentIndex;
  } else {
    lyrics = [hasSystemTrack ? "当前系统音乐暂无可用歌词" : "当前音乐暂无歌词"];
    activeLyric = -1;
  }

  const info = hasSystemTrack
    ? [
        { label: "系统媒体", value: systemMedia.statusLabel },
        { label: "歌词", value: systemMedia.lyrics.statusDetail || systemMedia.lyrics.statusLabel },
        { label: "来源", value: systemMedia.sourceApp || "-" }
      ]
    : hasTrack
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

  const bottomStatus = hasSystemTrack
    ? `System music: ${systemMedia.statusLabel} · Lyrics: ${systemMedia.lyrics.statusDetail || systemMedia.lyrics.statusLabel}`
    : hasTrack
    ? `${musicSnapshot.playing ? "正在播放" : musicSnapshot.paused ? "已暂停" : "已停止"} · ${queue.length > 0 && activeQueueIndex >= 0 ? `${activeQueueIndex + 1}/${queue.length}` : "单曲"}`
    : "暂无播放 · 等待音乐加入队列";

  const petPlayMode = String(petState?.musicPlayMode ?? "").trim();
  const petVolumeNormalization = typeof petState?.musicVolumeNormalization === "boolean" ? petState.musicVolumeNormalization : undefined;

  const recommendations = Array.isArray(musicSnapshot?.recommendations)
    ? musicSnapshot.recommendations.map((item) => {
        const itemType = item.itemType === "generated" ? "generated" : "attachment";
        return {
          id: item.id || item.sourceId || "",
          sourceId: String(item.sourceId || item.source_id || "").trim(),
          itemType,
          handle: String(item.handle || "").trim(),
          title: String(item.title || "").trim(),
          artist: String(item.artist || "").trim(),
          duration: item.durationLabel || item.duration || "",
          durationLabel: item.durationLabel || "",
          durationSeconds: Number(item.durationSeconds || 0),
          reason: String(item.reason || "").trim(),
          playable: item.playable !== false
        };
      })
    : [];

  return {
    nowPlaying, playlist, lyrics, activeLyric, info, bottomStatus,
    systemMedia,
    ...(petPlayMode ? { currentPlayMode: petPlayMode } : {}),
    ...(typeof petVolumeNormalization === "boolean" ? { volumeNormalization: petVolumeNormalization } : {}),
    recommendations,
  };
}

function normalizeSystemMediaRuntime(systemMediaSnapshot, systemLyricsSnapshot) {
  const media = systemMediaSnapshot && typeof systemMediaSnapshot === "object" ? systemMediaSnapshot : {};
  const lyric = systemLyricsSnapshot && typeof systemLyricsSnapshot === "object" ? systemLyricsSnapshot : {};
  const title = stringValue(media.title);
  const artist = stringValue(media.artist);
  const sourceApp = stringValue(media.sourceApp || media.source_app);
  const status = stringValue(media.status || "unavailable").toLowerCase();
  const playbackStatus = stringValue(media.playbackStatus || media.playback_status || "unknown").toLowerCase();
  const positionSeconds = Number.isFinite(media.positionSeconds) ? Math.max(0, media.positionSeconds) : 0;
  const durationSeconds = Number.isFinite(media.durationSeconds) ? Math.max(0, media.durationSeconds) : 0;
  const lyricStatus = stringValue(lyric.status || "unavailable").toLowerCase();
  const lyricReason = stringValue(lyric.reason);
  const lyricFound = lyricStatus === "ready" && Boolean(lyric.current || lyric.previous || lyric.next);
  const lyricStatusLabel = lyricFound ? "Found" : lyricsStatusLabel(lyricStatus, lyricReason);
  const lyricReasonLabel = lyricsReasonLabel(lyricReason);
  return {
    ready: Boolean(media.ok && media.fresh && (title || artist) && status === "ready"),
    status,
    statusLabel: systemMediaStatusLabel(status),
    title: title && artist ? `${title} - ${artist}` : title || artist || "系统正在播放的音乐",
    artist,
    sourceApp: shortSystemMediaSource(sourceApp),
    playbackStatus,
    isPlaying: Boolean(media.isPlaying || playbackStatus === "playing"),
    positionSeconds,
    durationSeconds,
    lyrics: {
      status: lyricStatus,
      statusLabel: lyricStatusLabel,
      statusDetail: lyricReasonLabel ? `${lyricStatusLabel} · ${lyricReasonLabel}` : lyricStatusLabel,
      reason: lyricReason,
      reasonLabel: lyricReasonLabel,
      source: stringValue(lyric.source),
      confidence: stringValue(lyric.confidence),
      lineCount: Number(lyric.lineCount || 0),
      current: stringValue(lyric.current),
      previous: stringValue(lyric.previous),
      next: stringValue(lyric.next)
    }
  };
}

function systemMediaStatusLabel(status) {
  return {
    ready: "Ready",
    empty: "Empty",
    unavailable: "Unavailable"
  }[status] || "Unavailable";
}

function lyricsStatusLabel(status, reason = "") {
  if (status === "pending" && reason === "lyrics_lookup_slow") return "Checking";
  if (status === "unavailable" && reason === "backend_offline") return "Unavailable";
  return {
    ready: "Found",
    "not-found": "Not found",
    disabled: "Disabled",
    unavailable: "Unavailable",
    pending: "Checking",
    "low-confidence": "Unavailable"
  }[status] || "Unavailable";
}

function lyricsReasonLabel(reason) {
  return {
    lyrics_lookup_pending: "pending",
    lyrics_lookup_slow: "provider slow",
    lyrics_request_failed: "request failed",
    lyrics_provider_failed: "provider failed",
    backend_offline: "backend offline",
    syncedlyrics_missing: "dependency missing",
    network_lyrics_disabled: "disabled",
    lyrics_not_found: "not found",
    ambiguous_track_metadata: "low confidence",
    insufficient_synced_lines: "low confidence"
  }[reason] || "";
}

function shortSystemMediaSource(value) {
  const text = stringValue(value).replace(/\.(exe|app)$/i, "");
  if (!text) return "";
  const parts = text.split(/[.!\\/:]+/).filter(Boolean);
  return parts.length ? parts[parts.length - 1].slice(0, 40) : text.slice(0, 40);
}

export function buildOverviewEmotionRuntimePatchFromSettingsSnapshot(runtimeSnapshot) {
  const currentExpression = runtimeSnapshot?.currentExpression || null;
  if (currentExpression && currentExpression.id) {
    return {
      emotion: {
        name: currentExpression.name || currentExpression.id || "",
        image: currentExpression.image || currentExpression.id || ""
      }
    };
  }
  const petState = runtimeSnapshot?.state || {};
  const emotionId = String(petState.currentEmotion || "").trim();
  if (emotionId) {
    return {
      emotion: {
        name: emotionId,
        image: emotionId
      }
    };
  }
  return null;
}

function formatSeconds(seconds) {
  const sec = Math.max(0, Math.round(Number(seconds) || 0));
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function buildAbilitiesRuntimePatch({ diagnostics, workspace, capabilitiesCatalog, voiceProfilesCatalog, approvalRequestsCatalog, connected }) {
  const diagnosticsData = asObject(diagnostics);
  const capabilities = asObject(diagnosticsData.capabilities);
  const catalogEntries = normalizeCapabilityCatalogEntries(capabilitiesCatalog);
  const approvalPolicy = normalizeApprovalPolicyEntry(capabilitiesCatalog?.approvalPolicy);
  const approvalRequests = normalizeApprovalRequestsCatalog(approvalRequestsCatalog);
  const catalogSummary = summarizeCapabilityCatalogEntries(catalogEntries);
  const safety = asObject(diagnosticsData.safety);
  const runtime = asObject(diagnosticsData.runtime);
  const runtimeMetrics = asObject(runtime.metrics);
  const workspaceCounts = asObject(diagnosticsData.workspace);
  const workspaceDataCounts = asObject(workspace?.counts);
  const tools = normalizeStringList(capabilities.tool_names || capabilities.toolNames);
  const effectiveModules = normalizeStringList(capabilities.effective_modules || capabilities.effectiveModules);
  const declared = normalizeStringList(capabilities.declared);
  const serviceOk = Boolean(connected) || stringValue(diagnosticsData.status) === "ok";
  const moduleCards = catalogEntries.length
    ? buildCapabilityCatalogModuleCards({ entries: catalogEntries, workspaceCounts, workspaceDataCounts, safety })
    : buildAbilityModuleCards({ tools, workspaceCounts, workspaceDataCounts, safety });
  const availableModuleCount = moduleCards.length;
  const toolCount = catalogEntries.length ? catalogSummary.total : tools.length;
  const pendingApprovalCount = catalogEntries.length ? catalogSummary.needsAttention : countPendingSafetyItems(safety);
  const activeApprovalCount = positiveNumber(approvalRequests.pendingCount);
  const availability = serviceOk
    ? (catalogEntries.length ? catalogSummary.availability : Math.min(100, Math.max(0, toolCount ? 98 : 72)))
    : 0;
  const syncedAt = diagnosticsData.server_time
    ? formatTimeOfDay(new Date(Number(diagnosticsData.server_time) * 1000))
    : formatTimeOfDay(new Date());
  const overviewStats = catalogEntries.length
    ? [
        { label: "能力模块", value: String(availableModuleCount) },
        { label: "可用能力", value: String(catalogSummary.ready) },
        { label: activeApprovalCount ? "待确认" : "待完善", value: String(activeApprovalCount || catalogSummary.needsAttention) }
      ]
    : [
        { label: "可用模块", value: String(availableModuleCount || effectiveModules.length || declared.length || 0) },
        { label: "可用能力", value: String(toolCount) },
        { label: "需审批权限", value: String(pendingApprovalCount) }
      ];

  return {
    overview: {
      stats: overviewStats,
      availability,
      note: buildAbilityOverviewNote({ serviceOk, catalogEntries, catalogSummary })
    },
    modules: moduleCards,
    providers: buildAbilityProviderCards(catalogEntries, capabilitiesCatalog, voiceProfilesCatalog),
    mcpServers: buildAbilityMcpServerCards(catalogEntries),
    workflows: buildAbilityWorkflows(moduleCards, catalogEntries),
    calls: buildAbilityStatusRows({
      syncedAt,
      serviceOk,
      toolCount,
      moduleCount: availableModuleCount,
      catalogSummary,
      workspaceCounts: mergeWorkspaceCounts(workspaceCounts, workspaceDataCounts),
      safety,
      approvalRequests,
      approvalPolicy,
      runtimeMetrics
    }),
    safety: buildAbilitySafetyPanel(safety, serviceOk, approvalRequests, approvalPolicy),
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

function normalizeApprovalRequestsCatalog(catalog) {
  const payload = asObject(catalog);
  const requests = asArray(payload.approvalRequests)
    .filter((entry) => entry && typeof entry === "object")
    .map((entry) => ({
      requestId: stringValue(entry.requestId),
      status: stringValue(entry.status || "pending"),
      decision: stringValue(entry.decision),
      capabilityId: stringValue(entry.capabilityId),
      actionId: stringValue(entry.actionId),
      title: stringValue(entry.title || "能力请求"),
      summary: stringValue(entry.summary),
      risk: stringValue(entry.risk || "medium"),
      approvalMode: normalizeApprovalMode(entry.approvalMode, entry),
      approvalReason: stringValue(entry.approvalReason),
      requestedBy: stringValue(entry.requestedBy || "akane"),
      createdAt: stringValue(entry.createdAt),
      expiresAt: stringValue(entry.expiresAt)
    }))
    .filter((entry) => entry.requestId || entry.capabilityId || entry.actionId);
  return {
    pendingCount: positiveNumber(payload.pendingCount ?? requests.filter((entry) => entry.status === "pending").length),
    approvalRequests: requests,
    status: stringValue(payload.status || "available")
  };
}

function normalizeApprovalPolicyEntry(entry) {
  const payload = asObject(entry);
  const defaultMode = normalizeApprovalMode(payload.defaultMode || payload.default_mode || "ask_each_time", {
    requiresConfirmation: true,
    risk: "high"
  });
  const mode = defaultMode === "trusted_auto_allow" ? "trusted_auto_allow" : "ask_each_time";
  const availableModes = asArray(payload.availableModes).length
    ? asArray(payload.availableModes)
    : [
        {
          id: "ask_each_time",
          label: "请求批准",
          summary: "高风险动作先进入审批队列。"
        },
        {
          id: "trusted_auto_allow",
          label: "完全访问",
          summary: "跳过高风险动作的逐次确认，但不跳过硬安全校验。"
        }
      ];
  return {
    defaultMode: mode,
    label: stringValue(payload.label) || (mode === "trusted_auto_allow" ? "完全访问" : "请求批准"),
    summary: stringValue(payload.summary) || (
      mode === "trusted_auto_allow"
        ? "高风险能力自动允许；URL、路径、密钥和本地边界校验仍保持开启。"
        : "高风险能力在执行前创建审批请求，由用户允许或拒绝。"
    ),
    trustedAutoAllowHighRisk: mode === "trusted_auto_allow",
    requiresConfirmationByDefault: mode !== "trusted_auto_allow",
    updatedAt: stringValue(payload.updatedAt),
    availableModes: availableModes
      .map((item) => asObject(item))
      .map((item) => {
        const id = stringValue(item.id);
        return {
          id,
          label: stringValue(item.label) || (id === "trusted_auto_allow" ? "完全访问" : "请求批准"),
          summary: stringValue(item.summary)
        };
      })
      .filter((item) => ["ask_each_time", "trusted_auto_allow"].includes(item.id))
  };
}

function buildAbilityOverviewNote({ serviceOk, catalogEntries, catalogSummary }) {
  if (!serviceOk) return "等待后端连接，能力状态暂不可用";
  if (!catalogEntries.length) return "能力注册表已同步，当前模块可正常使用";
  if (catalogSummary.needsAttention > 0) {
    return `已同步本地能力目录，${catalogSummary.needsAttention} 项能力等待配置或本地环境`;
  }
  return "本地能力目录已同步，当前能力状态良好";
}

function normalizeCapabilityCatalogEntries(catalog) {
  const payload = asObject(catalog);
  return asArray(payload.capabilities)
    .filter((entry) => entry && typeof entry === "object")
    .map((entry) => ({
      id: stringValue(entry.id),
      kind: stringValue(entry.kind),
      type: stringValue(entry.type),
      source: stringValue(entry.source),
      adapter: stringValue(entry.adapter),
      executionMode: stringValue(entry.executionMode),
      capabilityId: stringValue(entry.capabilityId),
      workflowId: stringValue(entry.workflowId),
      providerId: stringValue(entry.providerId),
      serverId: stringValue(entry.serverId),
      toolType: stringValue(entry.toolType),
      group: stringValue(entry.group),
      name: stringValue(entry.name),
      description: stringValue(entry.description),
      enabled: entry.enabled !== false,
      status: stringValue(entry.status || (entry.enabled === false ? "disabled" : "ready")),
      reason: stringValue(entry.reason),
      risk: stringValue(entry.risk),
      requiresConfirmation: Boolean(entry.requiresConfirmation),
      approvalMode: normalizeApprovalMode(entry.approvalMode, entry),
      approvalReason: stringValue(entry.approvalReason),
      configured: Boolean(entry.configured),
      configurable: Boolean(entry.configurable),
      executionReady: Boolean(entry.executionReady),
      endpoint: stringValue(entry.endpoint),
      defaultEndpoint: stringValue(entry.defaultEndpoint),
      transport: stringValue(entry.transport),
      commandName: safeDisplayBasename(entry.commandName),
      argsCount: positiveNumber(entry.argsCount),
      envCount: positiveNumber(entry.envCount),
      toolCount: positiveNumber(entry.toolCount),
      lastDiscovery: asObject(entry.lastDiscovery),
      inputSchema: asObject(entry.inputSchema),
      exposedToPrompt: Boolean(entry.exposedToPrompt),
      workflowPath: stringValue(entry.workflowPath),
      defaultWorkflowPath: stringValue(entry.defaultWorkflowPath),
      autoEnabled: Boolean(entry.autoEnabled),
      usedBy: normalizeStringList(entry.usedBy),
      toolTypes: normalizeStringList(entry.toolTypes),
      target: stringValue(entry.target),
      output: stringValue(entry.output),
      slots: asObject(entry.slots)
    }))
    .filter((entry) => entry.id || entry.name || entry.group || entry.type);
}

function normalizeApprovalMode(mode, entry = {}) {
  const normalized = stringValue(mode);
  if (["trusted_auto_allow", "ask_each_time", "disabled"].includes(normalized)) return normalized;
  const status = stringValue(entry.status || (entry.enabled === false ? "disabled" : "ready"));
  const unavailableStatuses = new Set([
    "configured",
    "disabled",
    "error",
    "invalid_config",
    "misconfigured",
    "missing_config",
    "missing_executor",
    "missing_model",
    "missing_slot_mapping",
    "missing_workflow",
    "unavailable",
    "unreachable",
    "unsupported_platform"
  ]);
  if (entry.enabled === false || unavailableStatuses.has(status)) return "disabled";
  if (entry.requiresConfirmation || entry.risk === "high") return "ask_each_time";
  return "trusted_auto_allow";
}

function approvalModeLabel(mode) {
  const labels = {
    trusted_auto_allow: "自动允许",
    ask_each_time: "每次确认",
    disabled: "暂不可用"
  };
  return labels[mode] || "待确认";
}

function summarizeCapabilityCatalogEntries(entries) {
  const total = entries.length;
  const ready = entries.filter((entry) => isCapabilityReady(entry)).length;
  const needsAttention = entries.filter((entry) => !isCapabilityReady(entry)).length;
  return {
    total,
    ready,
    needsAttention,
    availability: total ? Math.round((ready / total) * 100) : 0
  };
}

function buildCapabilityCatalogModuleCards({ entries, workspaceCounts, workspaceDataCounts, safety }) {
  const definitions = [
    {
      title: "记忆与提醒",
      description: "长期记忆 / 提醒事项 / 人设资料",
      permission: "个人记忆与资料",
      tone: "blue",
      icon: "sparkle",
      match: (entry) => capabilityEntryText(entry).match(/memory|reminder|persona|retrieve/)
    },
    {
      title: "文件与工作区",
      description: "材料整理 / 任务管理 / 文件交付",
      permission: "工作区文件访问",
      tone: "orange",
      icon: "folder",
      match: (entry) => capabilityEntryText(entry).match(/workspace|attachment|generated_files|file_handoff|gift|artifact|task|inventory|send_file|inspect_attachment/)
    },
    {
      title: "文档处理",
      description: "阅读资料 / 生成报告 / 修改文稿",
      permission: "文档读写",
      tone: "purple",
      icon: "file",
      match: (entry) => capabilityEntryText(entry).match(/documents|compose|revise|style|section|document/)
    },
    {
      title: "音频与语音",
      description: "朗读 / 识别 / 音频处理",
      permission: "麦克风与音频",
      tone: "green",
      icon: "mic",
      match: (entry) => capabilityEntryText(entry).match(/tts|asr|audio|voice|media|transcribe|stems|ffmpeg|ffprobe|faster_whisper/)
    },
    {
      title: "本地模型与执行器",
      description: "转码 / 抠图 / 模型工作流预留",
      permission: "本地运行环境",
      tone: "pink",
      icon: "cube",
      match: (entry) => entry.source === "external_executor" || entry.executionMode === "external" || capabilityEntryText(entry).match(/comfyui|gpt_sovits|rvc|demucs|ffmpeg|ffprobe/)
    },
    {
      title: "桌面感知",
      description: "窗口上下文 / 看屏幕 / 主动唤醒",
      permission: "桌面上下文",
      tone: "blue",
      icon: "eye",
      match: (entry) => capabilityEntryText(entry).match(/desktop|screen|vision|clipboard|context|proactive/)
    },
    {
      title: "安全与契约",
      description: "权限确认 / 风险隔离 / 能力契约",
      permission: "安全与确认",
      tone: "pink",
      icon: "shield",
      match: (entry) => entry.requiresConfirmation || entry.approvalMode === "ask_each_time" || entry.risk === "high" || capabilityEntryText(entry).match(/guard|safe|security|approval|sandbox/)
    }
  ];

  const cards = [];
  for (const definition of definitions) {
    const matched = entries.filter((entry) => definition.match(entry));
    const fallbackCount = definition.title === "文件与工作区"
      ? positiveNumber(workspaceCounts.files) + positiveNumber(workspaceDataCounts.files)
      : definition.title === "安全与契约"
        ? countPendingSafetyItems(safety)
        : 0;
    if (!matched.length && !fallbackCount) continue;
    const status = summarizeCapabilityModuleStatus(matched);
    const count = matched.length || fallbackCount;
    cards.push({
      title: definition.title,
      description: definition.description,
      permission: definition.permission,
      count: `${count} 项能力`,
      tone: definition.tone,
      icon: definition.icon,
      statusLabel: status.label,
      statusTone: status.tone
    });
  }

  if (!cards.length) {
    return buildAbilityModuleCards({
      tools: entries.map((entry) => entry.toolType || entry.id || entry.name).filter(Boolean),
      workspaceCounts,
      workspaceDataCounts,
      safety
    });
  }
  return cards.slice(0, 8);
}

function buildAbilityProviderCards(entries, catalog, voiceProfilesCatalog = null) {
  const payload = asObject(catalog);
  const configStatus = stringValue(payload.providerConfigStatus || payload.configStatus || "available");
  const voiceProfiles = normalizeVoiceProfileEntries(voiceProfilesCatalog);
  return entries
    .filter((entry) => entry.kind === "provider" && entry.configurable && entry.source !== "mcp" && entry.adapter !== "mcp_stdio")
    .map((entry) => {
      const status = mapCapabilityStatus(entry.status);
      const providerVoiceProfiles = voiceProfiles.filter((profile) => profile.providerId === entry.id);
      return {
        id: entry.id,
        name: entry.name || providerDisplayName(entry),
        title: entry.name || providerDisplayName(entry),
        description: providerDescription(entry),
        adapter: entry.adapter,
        type: entry.type,
        source: entry.source,
        status: entry.status || "missing_config",
        statusLabel: status.label,
        statusTone: status.tone,
        reason: providerReasonLabel(entry),
        enabled: Boolean(entry.enabled),
        configured: Boolean(entry.configured),
        configurable: true,
        endpoint: entry.endpoint,
        defaultEndpoint: entry.defaultEndpoint,
        voiceProfiles: providerVoiceProfiles,
        defaultVoiceProfile: providerVoiceProfiles.find((profile) => profile.status === "ready") || providerVoiceProfiles[0] || null,
        usedByLabel: providerUsedByLabel(entry.usedBy),
        configStatus,
        actionsEnabled: configStatus !== "invalid_config"
      };
    });
}

function buildAbilityMcpServerCards(entries) {
  const toolsByProvider = new Map();
  for (const entry of entries) {
    if (entry.kind !== "mcp_tool") continue;
    const providerId = entry.providerId || (entry.serverId ? `provider.mcp.${entry.serverId}` : "");
    if (!providerId) continue;
    if (!toolsByProvider.has(providerId)) toolsByProvider.set(providerId, []);
    toolsByProvider.get(providerId).push(entry);
  }
  return entries
    .filter((entry) => entry.kind === "provider" && (entry.source === "mcp" || entry.adapter === "mcp_stdio"))
    .map((entry) => {
      const status = mapCapabilityStatus(entry.status);
      const tools = toolsByProvider.get(entry.id) || [];
      const highRiskCount = tools.filter((tool) => tool.risk === "high" || tool.requiresConfirmation).length;
      const discoveredAt = stringValue(entry.lastDiscovery?.discoveredAt);
      const approvalMode = mcpServerApprovalMode(entry, tools);
      return {
        id: entry.id,
        serverId: entry.serverId,
        title: entry.name || "MCP 外部工具",
        status: entry.status || "missing_config",
        statusLabel: status.label,
        statusTone: status.tone,
        reason: mcpServerReasonLabel(entry),
        enabled: Boolean(entry.enabled),
        configured: Boolean(entry.configured),
        transport: entry.transport || "stdio",
        commandName: entry.commandName || "",
        toolCount: entry.toolCount || tools.length,
        safeToolLabels: summarizeMcpToolLabels(tools),
        toolDetails: summarizeMcpToolDetails(tools),
        highRiskCount,
        promptExposedCount: tools.filter((tool) => tool.exposedToPrompt).length,
        requiresConfirmation: Boolean(highRiskCount),
        approvalMode,
        approvalLabel: approvalModeLabel(approvalMode),
        lastDiscoveryLabel: discoveredAt ? "已发现工具" : "未执行发现"
      };
    });
}

function mcpServerApprovalMode(entry, tools = []) {
  const baseMode = normalizeApprovalMode(entry.approvalMode, entry);
  if (baseMode === "disabled" || !isCapabilityReady(entry)) return "disabled";
  if (tools.some((tool) => normalizeApprovalMode(tool.approvalMode, tool) === "ask_each_time")) {
    return "ask_each_time";
  }
  return baseMode;
}

function summarizeMcpToolLabels(tools) {
  const labels = [];
  for (const tool of tools) {
    const label = mcpToolCapabilityLabel(tool);
    if (label && !labels.includes(label)) labels.push(label);
    if (labels.length >= 4) break;
  }
  return labels.length ? labels : ["等待工具发现"];
}

function summarizeMcpToolDetails(tools) {
  const seen = new Map();
  const details = [];
  for (const tool of tools) {
    const label = mcpToolCapabilityLabel(tool);
    const count = (seen.get(label) || 0) + 1;
    seen.set(label, count);
    const approvalMode = normalizeApprovalMode(tool.approvalMode, tool);
    const riskLabel = tool.risk === "high" || tool.requiresConfirmation ? "高风险" : "普通";
    details.push({
      title: count > 1 ? `${label} ${count}` : label,
      riskLabel,
      approvalLabel: approvalModeLabel(approvalMode),
      promptLabel: tool.exposedToPrompt ? "已进提示词" : "默认不进提示词",
      statusLabel: mapCapabilityStatus(tool.status).label,
      schemaFields: Object.keys(asObject(tool.inputSchema?.properties)).slice(0, 6)
    });
    if (details.length >= 8) break;
  }
  return details;
}

function mcpToolCapabilityLabel(tool) {
  const text = capabilityEntryText(tool);
  if (/shell|terminal|cmd|powershell|exec|command|process|delete|remove|write|modify|click/.test(text)) return "需确认的操作";
  if (/browser|page|tab|url|web|navigate|read_page|search/.test(text)) return "浏览器上下文";
  if (/screenshot|screen|image|vision|ocr/.test(text)) return "屏幕与图像";
  if (/file|folder|workspace|path|document/.test(text)) return "文件上下文";
  if (/memory|retrieve|query/.test(text)) return "检索资料";
  return "外部工具";
}

function mcpServerReasonLabel(entry) {
  const status = entry.status || "";
  if (status === "ready") return "工具已发现，暂未开放自动调用";
  if (status === "configured") return "已保存，等待发现工具";
  if (status === "missing_config") return "需要配置本地 MCP 启动命令";
  if (status === "disabled") return "已配置但未启用";
  if (status === "invalid_config") return "配置需要修复";
  return entry.reason || "等待同步";
}

function normalizeVoiceProfileEntries(catalog) {
  return asArray(asObject(catalog).voiceProfiles)
    .map((entry) => {
      const voiceProfileId = stringValue(entry.voiceProfileId || entry.id);
      const status = stringValue(entry.status || (entry.enabled === false ? "disabled" : "missing_config"));
      const mappedStatus = mapCapabilityStatus(status);
      return {
        id: voiceProfileId,
        voiceProfileId,
        providerId: stringValue(entry.providerId || "provider.tts.gpt_sovits.local"),
        name: stringValue(entry.name || voiceProfileId || "GPT-SoVITS 声线"),
        enabled: entry.enabled !== false,
        configured: Boolean(entry.configured),
        status,
        statusLabel: mappedStatus.label,
        statusTone: mappedStatus.tone,
        reason: stringValue(entry.reason),
        textLang: stringValue(entry.textLang || "zh"),
        promptLang: stringValue(entry.promptLang || "zh"),
        mediaType: stringValue(entry.mediaType || "wav"),
        hasReferenceAudio: Boolean(entry.hasReferenceAudio),
        referenceAudioName: stringValue(entry.referenceAudioName),
        promptTextLength: positiveNumber(entry.promptTextLength),
        updatedAt: stringValue(entry.updatedAt)
      };
    })
    .filter((entry) => entry.voiceProfileId);
}

function providerDisplayName(entry) {
  if (entry.adapter === "comfyui") return "本地 ComfyUI";
  if (entry.adapter === "gpt_sovits") return "本地 GPT-SoVITS";
  return entry.id || "本地 Provider";
}

function providerDescription(entry) {
  if (entry.adapter === "comfyui") return "用于角色立绘处理、透明背景抠图和图像工作流预留";
  if (entry.adapter === "gpt_sovits") return "外部 GPT-SoVITS 兼容服务；配置后用于角色语音合成与自定义声线";
  return entry.type === "tts_provider" ? "本地语音能力提供方" : "本地能力执行器";
}

function providerUsedByLabel(usedBy = []) {
  const labels = {
    workshop: "角色工坊",
    image: "图像处理",
    desktop_pet: "桌宠",
    voice: "语音"
  };
  const mapped = usedBy.map((item) => labels[item] || "").filter(Boolean);
  return mapped.length ? mapped.join(" / ") : "本地能力";
}

function providerReasonLabel(entry) {
  const status = entry.status || "";
  if (status === "missing_config") return "需要配置本机服务地址";
  if (status === "configured") return "已保存，建议检查连接";
  if (status === "disabled") return "已配置但未启用";
  if (status === "unreachable") return "本地服务暂时未连接";
  if (status === "invalid_config") return "配置需要修复";
  if (status === "ready") return "连接正常";
  return entry.reason || "等待确认";
}

function capabilityEntryText(entry) {
  return [
    entry.id,
    entry.kind,
    entry.type,
    entry.source,
    entry.adapter,
    entry.executionMode,
    entry.toolType,
    entry.group,
    entry.name,
    ...entry.usedBy,
    ...entry.toolTypes
  ].join(" ").toLowerCase();
}

function summarizeCapabilityModuleStatus(entries) {
  if (!entries.length) return { label: "可用", tone: "ready" };
  const ready = entries.filter((entry) => isCapabilityReady(entry)).length;
  if (ready === entries.length) return { label: "可用", tone: "ready" };
  if (ready > 0) return { label: "部分可用", tone: "warning" };
  const status = entries.map((entry) => entry.status).find(Boolean) || "unavailable";
  return mapCapabilityStatus(status);
}

function isCapabilityReady(entry) {
  return entry.enabled !== false && ["ready", "available", "checked", "ok"].includes(entry.status || "ready");
}

function mapCapabilityStatus(status) {
  const normalized = stringValue(status);
  const labels = {
    ready: { label: "可用", tone: "ready" },
    available: { label: "可用", tone: "ready" },
    ok: { label: "可用", tone: "ready" },
    checked: { label: "可用", tone: "ready" },
    missing_executor: { label: "缺少本地运行环境", tone: "warning" },
    missing_model: { label: "缺少模型", tone: "warning" },
    missing_config: { label: "未配置", tone: "warning" },
    configured: { label: "待检查", tone: "warning" },
    missing_workflow: { label: "待绑定", tone: "warning" },
    disabled: { label: "未启用", tone: "muted" },
    unreachable: { label: "未连接", tone: "warning" },
    invalid_config: { label: "配置异常", tone: "danger" },
    unavailable: { label: "不可用", tone: "danger" }
  };
  return labels[normalized] || { label: "待确认", tone: "warning" };
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
      description: "读取 / 整理 / 转换",
      permission: "受限文件访问",
      tone: "blue",
      icon: "folder",
      pattern: /file|attachment|document|read|inspect|sync_attachment/i
    },
    {
      title: "生成文件交付",
      description: "文档 / 报告 / 表格",
      permission: "生成与导出",
      tone: "purple",
      icon: "file",
      pattern: /compose|send_file|generated|delivery|handoff/i
    },
    {
      title: "手边物品",
      description: "材料 / 成果 / 任务",
      permission: "工作区管理",
      tone: "orange",
      icon: "gift",
      pattern: /workspace|task|gift|clipboard/i,
      extraCount: numberOrFallback(workspaceCounts.files, workspaceDataCounts.files, 0)
    },
    {
      title: "媒体工具",
      description: "转写 / 分离 / 转码",
      permission: "多媒体操作",
      tone: "green",
      icon: "play",
      pattern: /media|audio|voice|transcribe|stems|clean/i
    },
    {
      title: "记忆检索",
      description: "长期记忆 / 上下文",
      permission: "记忆读取",
      tone: "blue",
      icon: "sparkle",
      pattern: /memory|retrieve/i
    },
    {
      title: "安全边界",
      description: "权限 / 审批 / 保护",
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

function buildAbilityWorkflows(modules, catalogEntries = []) {
  const catalogWorkflows = buildCatalogWorkflowCards(catalogEntries);
  const names = new Set(modules.map((item) => item.title));
  const hasFile = names.has("文件处理") || names.has("文件与工作区");
  const hasDocument = names.has("生成文件交付") || names.has("文档处理");
  const hasAudio = names.has("媒体工具") || names.has("音频与语音");
  const workflows = [];
  if (hasFile && hasDocument) {
    workflows.push({
      steps: ["资料", "文档", "交付"],
      title: "收集资料 → 生成文档 → 放入工作区",
      detail: "把材料读懂、整理成文件，再交付到工作区"
    });
  }
  if (hasAudio) {
    workflows.push({
      steps: ["语音", "转写", "朗读"],
      title: "语音输入 → 转写理解 → 回复朗读",
      detail: "让 Akane 听得更准，也能把回复读出来"
    });
  }
  if (names.has("手边物品") || names.has("文件与工作区")) {
    workflows.push({
      steps: ["手边物品", "整理", "归档"],
      title: "手边材料 → 整理 → 归档",
      detail: "把临时材料收进工作区，方便后续继续处理"
    });
  }
  if (names.has("本地模型与执行器")) {
    workflows.push({
      steps: ["探活", "绑定", "处理"],
      title: "发现本地环境 → 绑定工作流 → 处理角色素材",
      detail: "为后续抠图、转码、语音模型等本地能力预留入口"
    });
  }
  if (names.has("记忆与提醒")) {
    workflows.push({
      steps: ["记忆", "提醒", "跟进"],
      title: "记住事项 → 到点提醒 → 继续跟进",
      detail: "让常用偏好、提醒和任务上下文留在角色身边"
    });
  }
  const fallbackWorkflows = workflows.length ? workflows : [
    {
      steps: ["诊断", "同步", "等待"],
      title: "能力诊断 → 等待同步",
      detail: "后端连接后会显示可用工作流"
    }
  ];
  return catalogWorkflows.length ? catalogWorkflows.slice(0, 3) : fallbackWorkflows;
}

function buildCatalogWorkflowCards(entries) {
  return entries
    .filter((entry) => entry.kind === "workflow")
    .map((entry) => {
      const status = mapWorkflowStatus(entry.status);
      const slotMapping = asObject(entry.slotMapping);
      return {
        id: entry.id,
        workflowId: entry.id,
        steps: workflowSteps(entry),
        title: workflowTitle(entry),
        detail: workflowDetail(entry),
        statusLabel: status.label,
        statusTone: status.tone,
        enabled: Boolean(entry.enabled),
        configured: Boolean(entry.configured),
        executionReady: Boolean(entry.executionReady),
        workflowPath: entry.workflowPath,
        defaultWorkflowPath: entry.defaultWorkflowPath,
        inputImageSlot: stringValue(slotMapping.input_image_handle),
        outputImageSlot: stringValue(slotMapping.output_image_handle),
        actionsEnabled: entry.configurable !== false
      };
    });
}

function workflowSteps(entry) {
  if (entry.capabilityId === "workshop.portrait.cutout") {
    return ["立绘", "处理", "透明PNG"];
  }
  if (entry.type === "tts_provider") {
    return ["文本", "声线", "语音"];
  }
  return ["输入", "处理", "输出"];
}

function workflowTitle(entry) {
  if (entry.capabilityId === "workshop.portrait.cutout") return "透明背景处理";
  return entry.name || "本地工作流";
}

function workflowDetail(entry) {
  const status = entry.status || "";
  if (status === "missing_config") return "需要先配置本地执行环境";
  if (status === "missing_workflow") return "本地服务已保存，等待绑定具体工作流";
  if (status === "missing_slot_mapping") return "工作流已选择，仍需补齐输入输出槽位";
  if (status === "unreachable") return "本地服务暂时未连接";
  if (status === "invalid_config") return "配置需要修复后才能使用";
  if (status === "invalid_workflow_config") return "工作流绑定配置需要修复";
  if (status === "disabled") return "已配置但未启用";
  if (status === "configured") return "工作流绑定已保存，执行入口尚未开放";
  if (status === "ready") return "工作流已可供相关页面调用";
  return entry.description || "等待本地能力同步";
}

function mapWorkflowStatus(status) {
  const normalized = stringValue(status);
  if (normalized === "configured" || normalized === "validated_config") {
    return { label: "已绑定", tone: "warning" };
  }
  if (normalized === "missing_slot_mapping") {
    return { label: "待补齐", tone: "warning" };
  }
  if (normalized === "invalid_workflow_config") {
    return { label: "配置异常", tone: "danger" };
  }
  return mapCapabilityStatus(status);
}

function buildAbilityStatusRows({ syncedAt, serviceOk, toolCount, moduleCount, catalogSummary, workspaceCounts, safety, approvalRequests, approvalPolicy, runtimeMetrics }) {
  const hasCatalog = Boolean(catalogSummary?.total);
  const pendingApprovalCount = positiveNumber(approvalRequests?.pendingCount);
  const rows = [
    {
      time: syncedAt,
      module: "能力注册表",
      description: serviceOk
        ? (hasCatalog
            ? `已同步 ${moduleCount} 个模块、${catalogSummary.ready}/${catalogSummary.total} 项能力可用`
            : `已同步 ${moduleCount} 个模块、${toolCount} 项能力`)
        : "等待后端同步能力注册表",
      status: serviceOk ? (catalogSummary?.needsAttention ? "需配置" : "成功") : "待连接",
      duration: inferLatencyLabel(runtimeMetrics),
      method: hasCatalog ? "能力目录" : "后端诊断"
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
    description: buildSafetyDescription(safety, approvalRequests, approvalPolicy),
    status: pendingApprovalCount ? "待确认" : safety?.secrets_exposed ? "已拦截" : "成功",
    duration: "-",
    method: pendingApprovalCount ? "审批队列" : "策略检查"
  });
  return rows;
}

function buildAbilitySafetyPanel(safety, serviceOk, approvalRequests = {}, approvalPolicy = null) {
  const pendingApprovalCount = positiveNumber(approvalRequests.pendingCount);
  const latestRequest = asArray(approvalRequests.approvalRequests).find((item) => item.status === "pending") || null;
  const policy = approvalPolicy || normalizeApprovalPolicyEntry(null);
  const approvalRequirementLabel = policy.defaultMode === "trusted_auto_allow" ? "自动允许" : "请求批准";
  return {
    status: pendingApprovalCount ? `${pendingApprovalCount} 项待确认` : serviceOk ? "已生效" : "待连接",
    approvalPolicy: policy,
    items: [
      {
        label: "当前审批模式",
        status: policy.label || approvalRequirementLabel
      },
      {
        label: "审批请求队列",
        status: pendingApprovalCount
          ? `${pendingApprovalCount} 项待确认`
          : "空闲"
      },
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
        status: latestRequest ? latestRequest.title : approvalRequirementLabel
      }
    ]
  };
}

function buildSafetyDescription(safety, approvalRequests = {}, approvalPolicy = null) {
  const pendingApprovalCount = positiveNumber(approvalRequests.pendingCount);
  if (pendingApprovalCount) return `有 ${pendingApprovalCount} 项能力请求等待用户确认`;
  if (approvalPolicy?.defaultMode === "trusted_auto_allow") return "高风险能力已按用户策略自动允许，硬安全校验保持开启";
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

function normalizeAvailableCharacterPacks(packs, selectedPackId) {
  const selected = stringValue(selectedPackId);
  const seen = new Set();
  return asArray(packs)
    .map((pack) => normalizeAvailableCharacterPack(pack, selected))
    .filter((pack) => {
      if (!pack?.id || seen.has(pack.id)) return false;
      seen.add(pack.id);
      return true;
    });
}

function normalizeAvailableCharacterPack(pack, selectedPackId) {
  if (!pack || typeof pack !== "object") return null;
  const profile = asObject(pack.profile);
  const identity = asObject(profile.identity);
  const appearance = asObject(profile.appearance);
  const assets = asObject(profile.assets);
  const id = stringValue(pack.id || pack.packId || pack.pack_id);
  if (!id) return null;
  const characterId = stringValue(pack.characterId || pack.character_id || identity.id);
  const name = stringValue(pack.name || identity.name || characterId || id);
  const appName = stringValue(pack.appName || pack.app_name || identity.appName || identity.app_name || name);
  const defaultOutfit = stringValue(pack.defaultOutfit || pack.default_outfit || appearance.defaultOutfit || appearance.default_outfit);
  const defaultEmotion = stringValue(pack.defaultEmotion || pack.default_emotion || appearance.defaultEmotion || appearance.default_emotion);
  const schemaVersion = stringValue(pack.schemaVersion || pack.schema_version || profile.schemaVersion || profile.schema_version);
  const assetSource = stringValue(pack.assetSource || pack.asset_source || assets.runtimeSource || assets.runtime_source);
  const assetCount = positiveNumber(pack.assetCount || pack.asset_count);
  return {
    id,
    characterId,
    name,
    appName: appName || name || id,
    schemaVersion,
    defaultOutfit,
    defaultEmotion,
    assetCount,
    assetSource,
    selected: selectedPackId ? id === selectedPackId : Boolean(pack.selected)
  };
}

function toBackendAssetUrl(baseUrl, value) {
  const raw = stringValue(value);
  if (!raw) return "";
  if (/^(https?:|data:|blob:)/i.test(raw)) return raw;
  const base = `${String(baseUrl || DEFAULT_BACKEND_URL).replace(/\/+$/, "")}/`;
  return new URL(raw.replace(/^\/+/, ""), base).toString();
}

function buildRecentOutputsPatch(workspace) {
  const sections = asObject(workspace?.data?.sections || workspace?.sections);
  const outputs = Array.isArray(sections.outputs) ? sections.outputs : [];
  return outputs
    .map((item) => {
      const title = String(item.title || item.name || "").trim();
      const status = String(item.status || "").trim();
      if (!title || status === "failed") return null;
      return {
        id: String(item.handle || item.id || "").trim(),
        handle: String(item.handle || "").trim(),
        title,
        subtitle: String(item.subtitle || item.format || "").trim(),
        kind: String(item.kind || "file").trim().toLowerCase(),
        format: String(item.format || item.file_ext || "").trim(),
        status,
        updatedAt: Number(item.updated_at || item.updatedAt || 0)
      };
    })
    .filter(Boolean)
    .slice(0, 3);
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

function buildHealthTiles({ metrics, runtimeMetrics, workspaceCounts, health, toolCount, serviceOk }) {
  const currentMemoryBytes = metrics.akane_tracemalloc_current_bytes;
  const peakMemoryBytes = metrics.akane_tracemalloc_peak_bytes;
  const vectorEntries = metrics.akane_vector_entries;
  const activeThinks = metrics.akane_public_guard_active_thinks;
  const cpuPercent = metrics.cpu_percent;
  const contractVersion = health?.contracts?.desktop_pet?.version || "";
  const cpuValue = Number(cpuPercent);
  const hasCpuPercent = Number.isFinite(cpuValue);
  return {
    "CPU 占用": hasCpuPercent ? `${Math.round(cpuValue)}%` : (serviceOk ? "运行中" : "待连接"),
    "内存占用": currentMemoryBytes ? formatBytes(currentMemoryBytes) : "-",
    "记忆容量": vectorEntries ? `${Math.round(vectorEntries)} 条记忆` : `${workspaceCounts.files} 个文件`,
    "峰值内存": peakMemoryBytes ? `峰值 ${formatBytes(peakMemoryBytes)}` : "待采集",
    "错误数": String(countMetricErrors(runtimeMetrics)),
    "活跃守护": activeThinks ? `${activeThinks} 个会话` : "0",
    "协议版本": contractVersion || "待同步",
    "能力注册": toolCount ? `${toolCount} 工具` : "等待诊断"
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

function safeDisplayBasename(value) {
  const raw = stringValue(value);
  if (!raw) return "";
  return raw.split(/[\\/]/).filter(Boolean).pop() || raw;
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
    const capabilitiesCatalog = await readCapabilitiesCatalog(
      fetchImpl,
      baseUrl,
      requestParams || { t: String(Date.now()) }
    );
    const voiceProfilesCatalog = await readVoiceProfilesCatalog(
      fetchImpl,
      baseUrl,
      requestParams || { t: String(Date.now()) }
    );
    const approvalRequestsCatalog = await readApprovalRequestsCatalog(
      fetchImpl,
      baseUrl,
      requestParams || { t: String(Date.now()) }
    );
    const metricsText = typeof metrics.data === "string" ? metrics.data : "";
    return {
      ...mockData,
      sourceKind: CONTROL_CENTER_SOURCE_KIND.backend,
      backendUrl: baseUrl,
      fallbackReason: null,
      controlCenterRuntime: {
        backendBaseUrl: baseUrl,
        health,
        diagnostics,
        workspace,
        resourceManifest,
        metrics,
        capabilitiesCatalog,
        voiceProfilesCatalog,
        approvalRequestsCatalog
      },
      overviewRuntime: buildOverviewRuntimePatch({
        health: health.data,
        diagnostics: diagnostics.data,
        workspace: workspace.data,
        metricsText,
        petState,
        baseUrl,
        resourceManifest: resourceManifest.data,
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
        petState,
        capabilitiesCatalog: capabilitiesCatalog.data
      }),
      perceptionRuntime: buildPerceptionRuntimePatch({
        petState,
        diagnostics: diagnostics.data
      }),
      musicRuntime: buildMusicRuntimePatch({ musicSnapshot, petState }),
      abilitiesRuntime: buildAbilitiesRuntimePatch({
        diagnostics: diagnostics.data,
        workspace: workspace.data,
        capabilitiesCatalog: capabilitiesCatalog.data,
        voiceProfilesCatalog: voiceProfilesCatalog.data,
        approvalRequestsCatalog: approvalRequestsCatalog.data,
        connected: health.ok || diagnostics.ok || capabilitiesCatalog.ok || voiceProfilesCatalog.ok || approvalRequestsCatalog.ok
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
