import { buildBackendUrl, fetchJson, readActionResponse } from "../http.js";

export function createExposureMethods({
  ensureVerifiedBackend,
  sessionId,
  profileUserId,
  characterPackId,
  fetchImpl,
  botBaseUrl
}) {
  return {
    async readToolExposure(overrides = {}) {
      if (!await ensureVerifiedBackend()) return { ok: false, reason: "instance_health_unavailable" };
      const params = { user_id: overrides.sessionId || sessionId, real_user_id: profileUserId,
        character_pack_id: overrides.characterPackId ?? characterPackId };
      const result = await fetchJson(fetchImpl, buildBackendUrl(botBaseUrl, "/capabilities/tool-exposure", params));
      return result.ok ? result.data : { ok: false, reason: result.error || result.status };
    },
    async saveToolExposure(payload, overrides = {}) {
      if (!await ensureVerifiedBackend()) return { ok: false, reason: "instance_health_unavailable" };
      const params = { user_id: overrides.sessionId || sessionId, real_user_id: profileUserId,
        character_pack_id: overrides.characterPackId ?? characterPackId };
      const response = await fetchImpl(buildBackendUrl(botBaseUrl, "/capabilities/tool-exposure", params), {
        method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(payload), cache: "no-store"
      });
      const result = await readActionResponse(response);
      return { ...result, ok: response.ok && result?.ok === true };
    }
  };
}
