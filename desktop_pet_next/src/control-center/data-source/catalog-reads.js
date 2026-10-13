import { buildBackendUrl, fetchJson } from "./http.js";
import { asObject } from "./shared.js";

export async function readPluginConnectionConfigs(fetchImpl, baseUrl, payload, profileUserId) {
  const plugins = Array.isArray(payload?.plugins) ? payload.plugins : [];
  const enriched = await Promise.all(plugins.map(async (plugin) => {
    const pluginId = String(plugin?.plugin_id || "").trim();
    const connections = Array.isArray(plugin?.contributions?.connections) ? plugin.contributions.connections : [];
    if (!pluginId || !connections.length) return plugin;
    const configs = await Promise.all(connections.map(async (connection) => {
      const name = String(connection?.name || "").trim();
      if (!name) return null;
      const result = await fetchJson(fetchImpl, buildBackendUrl(baseUrl, `/admin/plugins/${encodeURIComponent(pluginId)}/connections/${encodeURIComponent(name)}`, { profile_user_id: profileUserId }));
      if (result.ok && result.data && typeof result.data === "object") {
        return { ...result.data, connection: result.data.connection || connection };
      }
      return { ok: false, status: result.status || "unavailable", reason: result.error || "connection_config_unavailable", connection, revision: 0, enabled: false, values: {}, configured_private_fields: [], private_fields: connection.private_fields || [] };
    }));
    return { ...plugin, connection_configs: configs.filter(Boolean) };
  }));
  return { ...payload, plugins: enriched };
}

export async function readCapabilitiesCatalog(fetchImpl, baseUrl, params = {}) {
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

export async function readVoiceProfilesCatalog(fetchImpl, baseUrl, params = {}) {
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

export async function readApprovalRequestsCatalog(fetchImpl, baseUrl, params = {}) {
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
