import { buildBackendUrl, readActionResponse } from "../http.js";
import { CONTROL_CENTER_ACTIONS, createNotImplementedActionResult } from "../../action-router.js";
import { formatDataSourceError } from "../shared.js";

const VOICE_PROFILE_FIELDS = Object.freeze([
  "textLang", "promptLang", "mediaType", "refAudioPath", "promptText",
  "irodoriVoiceId",
  "parallelInfer", "splitBucket", "batchSize", "topK", "topP", "temperature",
  "speedFactor", "fragmentInterval", "textSplitMethod"
]);

function applyVoiceProfileFields(body, payload) {
  for (const key of VOICE_PROFILE_FIELDS) {
    const value = payload[key] === false ? "false" : String(payload[key] ?? "").trim();
    if (value) body[key] = value;
  }
  if (payload.emotionVoiceMap && typeof payload.emotionVoiceMap === "object" && !Array.isArray(payload.emotionVoiceMap)) {
    body.emotionVoiceMap = payload.emotionVoiceMap;
  }
  return body;
}

export async function runMcpBackendAction(fetchImpl, baseUrl, actionId, payload = {}, params = {}) {
  const serverId = String(payload.serverId || payload.server_id || payload.id || "").trim();
  if (!serverId) {
    return { ok: false, status: "invalid-payload", actionId, refresh: false, error: "serverId is required" };
  }
  const actionPath = mcpActionPath(actionId);
  const endpoint = `/capabilities/mcp-servers/${encodeURIComponent(serverId)}${actionPath ? `/${actionPath}` : ""}`;
  const body = buildMcpActionBody(actionId, payload);
  try {
    const response = await fetchImpl(buildBackendUrl(baseUrl, endpoint, params), {
      method: actionId === CONTROL_CENTER_ACTIONS.abilitiesMcpRemove ? "DELETE" : "POST",
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
  if (actionId === CONTROL_CENTER_ACTIONS.abilitiesMcpEnable) return "enable";
  if (actionId === CONTROL_CENTER_ACTIONS.abilitiesMcpDisable) return "disable";
  if (actionId === CONTROL_CENTER_ACTIONS.abilitiesMcpRestart) return "restart";
  if (actionId === CONTROL_CENTER_ACTIONS.abilitiesMcpRemove) return "";
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

export async function runWorkflowBackendAction(fetchImpl, baseUrl, actionId, payload = {}, params = {}) {
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

export async function runProviderBackendAction(fetchImpl, baseUrl, actionId, payload = {}, params = {}) {
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
      voiceProfileId: String(payload.voiceProfileId || payload.profileId || "").trim(),
      ...(String(payload.emotion || "").trim() ? { emotion: String(payload.emotion).trim() } : {})
    };
    return applyVoiceProfileFields(body, payload);
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
    const displayName = String(payload.displayName ?? "").trim();
    if (displayName) body.displayName = displayName;
    return applyVoiceProfileFields(body, payload);
  }
  if (actionId === CONTROL_CENTER_ACTIONS.abilitiesProviderConfigSave) {
    return {
      enabled: Boolean(payload.enabled),
      ...(endpoint ? { endpoint } : {})
    };
  }
  return endpoint ? { endpoint } : {};
}
