import { normalizeBotId, botScopedPath } from "../bot-routing.js";
import { readJsonResponse } from "../desktop-voice-session.js";
import { resolveCareFeatureFromHealth } from "../care-feature.js";
import { getActiveCharacterText } from "../character-profile.js";
import { PUBLIC_HEALTH_PATH, DESKTOP_HEALTH_PATH, BACKEND_RETRY_MS, CLIENT_MODE } from './constants.js';
import { getProfileDefaultOutfit, getProfileDefaultEmotion } from './profile.js';
import { normalizeBackendUrl, formatError, generateSessionId, friendlyErrorMessage } from './utils.js';

/** Owns backend behavior and its temporary runtime state. */
export function createBackendController({
  state,
  resourceState,
  els,
  platform: platformPort,
  lifecycle,
  scope: runtimeScope,
  status: statusPort,
  persistence: statePort,
  character: characterPort,
  settings: settingsPort,
  chat: chatPort,
  voice: voicePort,
  attachments: attachmentsPort,
  care: carePort,
  bubble: bubblePort
}) {
  const { window } = platformPort;

  let backendRetryTimer = 0;

  let backendSwitchToken = 0;
  let healthRevision = 0;

  let backendSwitchPending = false;
  let backendSwitchError = "";

  let validatedBotBindingKey = "";

  async function updateBackendUrlFromInput() {
    await updateBackendUrl(els.backendUrl.value);
  }

  async function updateBackendUrl(value) {
    const candidateUrl = normalizeBackendUrl(value);
    const previousUrl = state.backendUrl;
    if (backendSwitchPending) {
      backendSwitchError = "backend_switch_in_progress";
      statusPort.setStatus("正在验证另一个后端地址，请稍候。", { durationMs: 1800 });
      els.backendUrl.value = previousUrl;
      return false;
    }

    const switchToken = ++backendSwitchToken;
    backendSwitchError = "";
    backendSwitchPending = true;
    els.backendSave.disabled = true;
    els.backendUrl.disabled = true;
    statusPort.setStatus(`正在验证实例 ${state.instanceId}。`, { durationMs: 2200 });
    try {
      const binding = await verifyBackendInstance(candidateUrl);
      if (switchToken !== backendSwitchToken) return false;
      await resetInstanceBoundRuntimeForBackendSwitch();
      if (switchToken !== backendSwitchToken || lifecycle.disposed) return false;
      state.backendUrl = candidateUrl;
      els.backendUrl.value = candidateUrl;
      clearBackendRetry();
      statePort.scheduleSave(0);
      statusPort.setStatus(`已连接实例 ${binding.instanceId}，正在加载资源。`, { durationMs: 2200 });
      const healthy = await characterPort.reloadCharacterResources({ userTriggered: true });
      if (!healthy) backendSwitchError = resourceState.healthMessage || "backend_resources_unavailable";
      if (healthy) void ensureBackendSession();
      return healthy;
    } catch (error) {
      if (switchToken === backendSwitchToken) {
        backendSwitchError = friendlyBackendBindingError(error);
        state.backendUrl = previousUrl;
        els.backendUrl.value = previousUrl;
        statusPort.setStatus(`拒绝切换后端：${friendlyBackendBindingError(error)}`, { mode: "error", durationMs: 4200 });
      }
      return false;
    } finally {
      if (switchToken === backendSwitchToken) {
        backendSwitchPending = false;
        els.backendSave.disabled = false;
        els.backendUrl.disabled = false;
      }
    }
  }

  async function updateBoundBot(value) {
    if (backendSwitchPending) throw new Error("backend_switch_in_progress");
    const switchToken = ++backendSwitchToken;
    const assertCurrentSwitch = () => {
      if (switchToken !== backendSwitchToken || lifecycle.disposed) throw new Error("bot_selection_superseded");
    };
    const nextBotId = normalizeBotId(value);
    const previousBotId = normalizeBotId(state.boundBotId);
    if (!nextBotId) {
      throw new Error("invalid_bound_bot_id");
    }
    if (nextBotId === previousBotId) {
      statusPort.setStatus("已经在使用这个 Bot。", { durationMs: 1800 });
      return true;
    }

    await verifyBackendInstance(state.backendUrl);
    assertCurrentSwitch();
    const catalog = await fetchBotCatalog();
    assertCurrentSwitch();
    const target = catalog.bots.find((item) => item.botId === nextBotId);
    if (!target) throw new Error("bot_not_registered");
    if (!target.available) throw new Error(target.reason || "bot_runtime_unavailable");

    statusPort.setStatus(`正在切换到 ${target.displayName || nextBotId}。`, { durationMs: 2200 });
    await resetInstanceBoundRuntimeForBackendSwitch();
    assertCurrentSwitch();
    state.boundBotId = nextBotId;
    validatedBotBindingKey = `${state.backendUrl.replace(/\/+$/, "")}|${nextBotId}`;
    try {
      const healthy = await characterPort.reloadCharacterResources({ userTriggered: false, silent: true });
      assertCurrentSwitch();
      if (!healthy) throw new Error(resourceState.healthMessage || "bot_route_unavailable");
      const session = await ensureBackendSession({ restoreLatest: state.restoreLatestOnStartup });
      assertCurrentSwitch();
      if (!session) throw new Error("bot_session_unavailable");
      await statePort.saveNow();
      statusPort.setStatus(`已切换到 ${target.displayName || nextBotId}。`, { durationMs: 2600 });
      await settingsPort.broadcastSettingsSnapshot();
      return true;
    } catch (error) {
      assertCurrentSwitch();
      state.boundBotId = previousBotId;
      validatedBotBindingKey = "";
      await characterPort.reloadCharacterResources({ userTriggered: false, silent: true });
      await statePort.saveNow();
      throw error;
    }
  }

  async function fetchBotCatalog() {
    const requestScope = runtimeScope.capture();
    const url = new URL("/api/bots", `${state.backendUrl.replace(/\/+$/, "")}/`);
    url.searchParams.set("t", String(Date.now()));
    const response = await platformPort.backendFetch(url.toString(), {
      method: "GET",
      cache: "no-store",
      connectTimeout: 5000
    });
    if (!runtimeScope.isCurrent(requestScope)) return { bots: [], reason: "request_scope_changed" };
    if (!response.ok) throw new Error(`bot_catalog_http_${response.status}`);
    const payload = await readJsonResponse(response);
    if (!runtimeScope.isCurrent(requestScope)) return { bots: [], reason: "request_scope_changed" };
    const bots = Array.isArray(payload?.bots)
      ? payload.bots
          .map((item) => ({
            botId: normalizeBotId(item?.botId || item?.bot_id),
            displayName: String(item?.displayName || item?.display_name || item?.botId || "").trim(),
            available: item?.available !== false,
            reason: String(item?.reason || "").trim()
          }))
          .filter((item) => item.botId)
      : [];
    return { bots, defaultBotId: normalizeBotId(payload?.defaultBotId || payload?.default_bot_id) };
  }

  async function resetInstanceBoundRuntimeForBackendSwitch() {
    chatPort.interruptReply({ announce: false });
    await voicePort.cancelVoiceRecording();
    clearBackendRetry();
    attachmentsPort.resetFileDeliveryTracking();
    resourceState.manifest = null;
    resourceState.health = "checking";
    resourceState.healthMessage = "Verifying instance";
    resourceState.contractVersion = "";
    resourceState.contractSource = "unknown";
    resourceState.capabilities = [];
    resourceState.endpoints = {};
    carePort.markCareAuthorityUnavailable("backend_switch");
    statusPort.updateConnectionStatus();
  }

  async function verifyBackendInstance(backendUrl) {
    const normalizedUrl = normalizeBackendUrl(backendUrl);
    if (platformPort.isTauriRuntime) {
      const result = await platformPort.invoke("verify_backend_instance", { backendUrl: normalizedUrl });
      if (!result?.ok) {
        const error = new Error(String(result?.reason || result?.status || "instance_verification_failed"));
        error.bindingStatus = String(result?.status || "rejected");
        error.bindingReason = String(result?.reason || "instance_verification_failed");
        error.actualInstanceId = String(result?.actualInstanceId || "");
        throw error;
      }
      return result;
    }

    const response = await window.fetch(`${normalizedUrl}${PUBLIC_HEALTH_PATH}`, {
      method: "GET",
      cache: "no-store"
    });
    if (!response.ok) throw new Error(`health_http_${response.status}`);
    const payload = await response.json();
    const actualInstanceId = String(payload?.instance_id || "").trim();
    if (
      String(payload?.status || "") !== "ok" ||
      String(payload?.root_binding || "") !== "valid" ||
      actualInstanceId !== state.hostId
    ) {
      const error = new Error("instance_id_mismatch");
      error.bindingStatus = "rejected";
      error.bindingReason = "instance_id_mismatch";
      error.actualInstanceId = actualInstanceId;
      throw error;
    }
    return { ok: true, status: "verified", reason: "", instanceId: actualInstanceId };
  }

  function friendlyBackendBindingError(error) {
    const reason = String(error?.bindingReason || formatError(error) || "").trim();
    const actual = String(error?.actualInstanceId || "").trim();
    if (reason === "instance_id_mismatch") {
      return actual
        ? `目标是 Host ${actual}，当前客户端只允许 ${state.hostId}`
        : `后端 Host 身份与 ${state.hostId} 不匹配`;
    }
    if (reason === "missing_admin_token") return "命名实例缺少管理凭据";
    if (reason.includes("health") || reason.includes("connect") || reason.includes("request")) {
      return "无法通过公开 health 验证实例身份";
    }
    return reason || "实例验证失败";
  }

  async function checkBackendHealth() {
    let requestScope = runtimeScope.capture();
    const revision = ++healthRevision;
    const isCurrent = () => revision === healthRevision && runtimeScope.isCurrent(requestScope);
    const result = (healthy) => ({ healthy, scope: requestScope });
    try {
      await verifyBackendInstance(state.backendUrl);
      if (!isCurrent()) return result(false);
      // Registration may intentionally adopt the backend's default Bot. Return
      // the resulting scope so resource loading follows that validated binding.
      requestScope = await ensureBoundBotRegistered();
      if (!isCurrent()) return result(false);
    } catch (error) {
      if (!isCurrent()) return result(false);
      resourceState.health = "offline";
      resourceState.healthMessage = friendlyBackendBindingError(error);
      resourceState.healthEndpoint = PUBLIC_HEALTH_PATH;
      resourceState.contractSource = "instance_rejected";
      if (String(error?.bindingReason || "") === "instance_id_mismatch") {
        clearBackendRetry();
      } else {
        scheduleBackendRetry();
      }
      statusPort.updateConnectionStatus();
      return result(false);
    }

    const query = new URLSearchParams({
      user_id: state.sessionId || "desktop_pet_next_health",
      real_user_id: statePort.getProfileUserId(),
      ...characterPort.buildBackendCharacterContext(),
      t: String(Date.now())
    });

    try {
      const response = await platformPort.backendFetch(buildBackendEndpointUrl("health", DESKTOP_HEALTH_PATH, query), {
        method: "GET",
        cache: "no-store",
        connectTimeout: 3500
      });
      if (!isCurrent()) return result(false);
      if (!response.ok) throw new Error(await voicePort.readBackendErrorMessage(response, `HTTP ${response.status}`));
      const payload = await readJsonResponse(response);
      if (!isCurrent()) return result(false);
      applyBackendHealthPayload(payload, { endpoint: DESKTOP_HEALTH_PATH, contractSource: "desktop_pet" });
      resourceState.health = "online";
      resourceState.healthMessage = "Connected";
      clearBackendRetry();
      statusPort.updateConnectionStatus();
      return result(true);
    } catch (error) {
      if (!isCurrent()) return result(false);
      resourceState.health = "offline";
      resourceState.healthMessage = formatError(error);
      resourceState.healthEndpoint = DESKTOP_HEALTH_PATH;
      resourceState.contractSource = "unavailable";
      scheduleBackendRetry();
      statusPort.updateConnectionStatus();
      return result(false);
    }
  }

  async function ensureBoundBotRegistered() {
    const requestScope = runtimeScope.capture();
    const currentBotId = normalizeBotId(state.boundBotId);
    const bindingKey = `${state.backendUrl.replace(/\/+$/, "")}|${currentBotId}`;
    if (currentBotId && validatedBotBindingKey === bindingKey) return requestScope;

    const catalog = await fetchBotCatalog();
    if (!runtimeScope.isCurrent(requestScope)) throw new Error("bot_binding_superseded");
    let target = catalog.bots.find((item) => item.botId === currentBotId);
    if (!target) {
      target = catalog.bots.find((item) => item.botId === catalog.defaultBotId);
      if (!target) throw new Error("default_bot_not_registered");
    }
    if (!target.available) throw new Error(target.reason || "bot_runtime_unavailable");
    if (target.botId !== currentBotId) {
      state.boundBotId = target.botId;
      statePort.scheduleSave(0);
      settingsPort.scheduleSettingsSnapshot(0);
    }
    validatedBotBindingKey = `${state.backendUrl.replace(/\/+$/, "")}|${target.botId}`;
    return runtimeScope.capture();
  }

  function applyBackendHealthPayload(payload, { endpoint, contractSource } = {}) {
    const data = payload && typeof payload === "object" ? payload : {};
    const tts = data.tts && typeof data.tts === "object" ? data.tts : {};
    const asr = data.asr && typeof data.asr === "object" ? data.asr : {};
    resourceState.healthEndpoint = endpoint || DESKTOP_HEALTH_PATH;
    resourceState.contractVersion = String(data.contract_version || data.contractVersion || "");
    resourceState.contractSource = contractSource || (resourceState.contractVersion ? "desktop_pet" : "legacy");
    resourceState.capabilities = Array.isArray(data.capabilities)
      ? data.capabilities.map((item) => String(item || "").trim()).filter(Boolean)
      : [];
    resourceState.endpoints = data.endpoints && typeof data.endpoints === "object" ? { ...data.endpoints } : {};
    resourceState.tts = {
      enabled: typeof tts.enabled === "boolean" ? tts.enabled : null,
      endpoint: String(tts.endpoint || resourceState.endpoints.tts || "/tts"),
      responseMediaType: String(tts.response_media_type || tts.responseMediaType || "audio/mpeg")
    };
    resourceState.asr = {
      available: Boolean(resourceState.endpoints.asr || asr.endpoint || resourceState.capabilities.includes("asr")),
      endpoint: String(asr.endpoint || resourceState.endpoints.asr || "/asr"),
      uploadField: String(asr.upload_field || asr.uploadField || "file")
    };
    carePort.applyCareFeatureStatus(resolveCareFeatureFromHealth(data));
  }

  function scheduleBackendRetry(delay = BACKEND_RETRY_MS) {
    if (!platformPort.isTauriRuntime || backendRetryTimer || resourceState.health === "online") return;
    backendRetryTimer = lifecycle.setTimeout(async () => {
      backendRetryTimer = 0;
      const recovered = await characterPort.reloadCharacterResources({ silent: true });
      if (recovered) {
        statePort.scheduleSave(0);
        void ensureBackendSession();
        if (!chatPort.isReplyActive() && els.chatForm.hidden) {
          bubblePort.showBubbleText("后端已经连回来了。", { transient: true, durationMs: 2200 });
        }
      }
      settingsPort.scheduleSettingsSnapshot();
    }, delay);
    settingsPort.scheduleSettingsSnapshot();
  }

  function clearBackendRetry() {
    if (!backendRetryTimer) return;
    lifecycle.clearTimeout(backendRetryTimer);
    backendRetryTimer = 0;
    settingsPort.scheduleSettingsSnapshot();
  }

  async function fetchResourceManifest() {
    const requestScope = runtimeScope.capture();
    const query = new URLSearchParams({
      user_id: state.sessionId,
      real_user_id: statePort.getProfileUserId(),
      client: CLIENT_MODE,
      character_pack_id: characterPort.getCurrentCharacterPackId(),
      outfit: state.outfit || getProfileDefaultOutfit(),
      emotion: state.currentEmotion || getProfileDefaultEmotion(),
      t: String(Date.now())
    });
    const response = await platformPort.backendFetch(buildBackendEndpointUrl("resource_manifest", "/resource-manifest", query), {
      method: "GET",
      cache: "no-store",
      connectTimeout: 5000
    });
    if (!runtimeScope.isCurrent(requestScope)) return null;
    if (!response.ok) throw new Error(await voicePort.readBackendErrorMessage(response, `HTTP ${response.status}`));
    return response.json();
  }

  async function ensureBackendSession({ restoreLatest = false, retriedCharacterMismatch = false } = {}) {
    const requestScope = runtimeScope.capture();
    if (resourceState.health !== "online") return null;
    try {
      const response = await platformPort.backendFetch(buildBackendEndpointUrl("session_ensure", "/sessions/ensure", { t: Date.now() }), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        connectTimeout: 5000,
        body: JSON.stringify({
          user_id: state.sessionId,
          session_id: state.sessionId,
          real_user_id: statePort.getProfileUserId(),
          display_title: getActiveCharacterText("sessionDisplayTitle"),
          ...characterPort.buildBackendCharacterContext()
        })
      });
      if (!runtimeScope.isCurrent(requestScope)) return null;

      if (!response.ok) {
        const errorPayload = await readOptionalJsonResponse(response);
        if (!runtimeScope.isCurrent(requestScope)) return null;
        if (response.status === 409 && !retriedCharacterMismatch && isSessionCharacterMismatch(errorPayload)) {
          state.sessionId = generateSessionId();
          characterPort.persistCurrentCharacterRuntimeState();
          bubblePort.resetReplyIdentity();
          statePort.scheduleSave(0);
          statusPort.setRuntimeStatus("已为当前角色切换到独立会话", { mode: "idle" });
          return ensureBackendSession({ restoreLatest: false, retriedCharacterMismatch: true });
        }
        throw new Error(formatBackendErrorPayload(errorPayload) || await voicePort.readBackendErrorMessage(response, `HTTP ${response.status}`));
      }
      const bundle = await response.json();
      if (!runtimeScope.isCurrent(requestScope)) return null;
      if (restoreLatest && bubblePort.restoreLatestReply(bundle)) {
        statusPort.setRuntimeStatus("已恢复上一轮回复", { mode: "idle" });
      } else {
        statusPort.setRuntimeStatus("后端已就绪", { mode: "idle" });
      }
      return bundle;
    } catch (error) {
      if (!runtimeScope.isCurrent(requestScope)) return null;
      resourceState.health = "offline";
      resourceState.healthMessage = formatError(error);
      scheduleBackendRetry();
      statusPort.updateConnectionStatus();
      statusPort.setRuntimeStatus(`本地待机中：${friendlyErrorMessage(formatError(error))}`, { mode: "offline" });
      return null;
    }
  }

  async function readOptionalJsonResponse(response) {
    try {
      return await response.clone().json();
    } catch (_error) {
      return null;
    }
  }

  function isSessionCharacterMismatch(payload) {
    return String(payload?.error || "").trim() === "session_character_mismatch";
  }

  function formatBackendErrorPayload(payload) {
    if (!payload || typeof payload !== "object") return "";
    return String(payload.message || payload.error || payload.status || "").trim();
  }

  async function* readNdjsonEvents(response) {
    const reader = response.body?.getReader();
    if (!reader) {
      // Fallback: response body streaming not available.
      const raw = await response.text();
      const lines = raw.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
      const tail = !lines.length && raw.trim() ? [raw.trim()] : [];
      for (const line of [...lines, ...tail]) {
        try { yield JSON.parse(line); } catch { /* skip */ }
      }
      return;
    }

    const decoder = new TextDecoder();
    let buffer = "";
    let complete = false;

    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) { complete = true; break; }

        buffer += decoder.decode(value, { stream: true });
        const segments = buffer.split(/\r?\n/);
        buffer = segments.pop() || "";

        for (const segment of segments) {
          const trimmed = segment.trim();
          if (!trimmed) continue;
          try {
            yield JSON.parse(trimmed);
          } catch {
            // Skip malformed stream lines.
          }
        }
      }

      buffer += decoder.decode();
      const tail = buffer.trim();
      if (tail) {
        try { yield JSON.parse(tail); } catch { /* skip */ }
      }
    } finally {
      if (!complete) await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
  }

  function buildBackendEndpointUrl(name, fallbackPath, params = null) {
    const endpoint = scopeBackendEndpointToBoundBot(getBackendEndpoint(name, fallbackPath));
    const base = `${state.backendUrl.replace(/\/+$/, "")}/`;
    const url = new URL(endpoint, base);
    const entries =
      params instanceof URLSearchParams
        ? [...params.entries()]
        : Object.entries(params || {});
    for (const [key, value] of entries) {
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, String(value));
      }
    }
    return url.toString();
  }

  function scopeBackendEndpointToBoundBot(endpoint) {
    return botScopedPath(state.boundBotId, endpoint);
  }

  function getBackendEndpoint(name, fallbackPath) {
    const endpoints = resourceState.endpoints && typeof resourceState.endpoints === "object" ? resourceState.endpoints : {};
    const specialized =
      name === "tts"
        ? resourceState.tts?.endpoint
        : name === "asr"
          ? resourceState.asr?.endpoint
          : "";
    const value = String(specialized || endpoints[name] || fallbackPath || "").trim();
    if (!value) return "/";
    if (/^https?:\/\//i.test(value)) return value;
    return value.startsWith("/") ? value : `/${value}`;
  }

  return {
    get backendSwitchError() { return backendSwitchError; },
    get backendRetryTimer() { return backendRetryTimer; },
    updateBackendUrlFromInput,
    updateBackendUrl,
    updateBoundBot,
    checkBackendHealth,
    scheduleBackendRetry,
    clearBackendRetry,
    fetchResourceManifest,
    ensureBackendSession,
    readNdjsonEvents,
    buildBackendEndpointUrl
  };
}
