import { buildBackendUrl, readActionResponse } from "./http.js";

export function createMemorySettingMethods({ botBaseUrl, fetchImpl, ensureVerifiedBackend }) {
  async function request(path, init = {}) {
    if (typeof fetchImpl !== "function" || !await ensureVerifiedBackend()) {
      return { ok: false, reason: "backend_unavailable" };
    }
    try {
      const response = await fetchImpl(buildBackendUrl(botBaseUrl, path), {
        headers: { Accept: "application/json", "Content-Type": "application/json" }, cache: "no-store", ...init
      });
      const data = await readActionResponse(response);
      return { ...data, ok: response.ok && data.ok === true,
        reason: data.reason || (!response.ok ? `http_${response.status}` : "") };
    } catch { return { ok: false, reason: "memory_settings_request_failed" }; }
  }
  return {
    readMemorySettings({ characterPackId = "" } = {}) {
      return request(`/control-center/memory-settings?character_pack_id=${encodeURIComponent(characterPackId)}`);
    },
    saveMemoryReceiver(groupId, enabled, { characterPackId = "" } = {}) {
      return request(`/control-center/memory-settings/groups/${encodeURIComponent(groupId)}`, {
        method: "POST", body: JSON.stringify({ enabled, characterPackId })
      });
    }
  };
}
