import { buildBackendUrl, fetchJson } from "../http.js";
import { formatDataSourceError } from "../shared.js";
import { readChatImageBlob } from "../../chat-image-preview.js";

export function createChatMethods({
  fetchImpl,
  setChatSessionError,
  ensureVerifiedBackend,
  sessionId,
  characterPackId,
  botBaseUrl,
  profileUserId,
  client,
  boundBotId
}) {
  let latestSessionRequest = 0;
  return {
    async readChatSession(overrides = {}) {
      const requestId = ++latestSessionRequest;
      const setCurrentSessionError = (error) => {
        if (requestId === latestSessionRequest) setChatSessionError(error);
      };
      if (typeof fetchImpl !== "function") {
        setCurrentSessionError("no-fetch-impl");
        return null;
      }
      const health = await ensureVerifiedBackend();
      if (!health) {
        setCurrentSessionError("instance-health-unavailable");
        return null;
      }
      const requestedSessionId = String(overrides.sessionId || sessionId).trim() || sessionId;
      const requestedCharacterPackId = String(overrides.characterPackId ?? characterPackId).trim();
      try {
        const response = await fetchImpl(buildBackendUrl(botBaseUrl, "/sessions/ensure", { t: String(Date.now()) }), {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          cache: "no-store",
          body: JSON.stringify({
            user_id: requestedSessionId,
            session_id: requestedSessionId,
            real_user_id: profileUserId,
            client_mode: client,
            ...(requestedCharacterPackId ? { character_pack_id: requestedCharacterPackId } : {})
          })
        });
        if (!response.ok) {
          setCurrentSessionError(`http-${response.status}`);
          return null;
        }
        const payload = await response.json();
        if (!payload || typeof payload !== "object") {
          setCurrentSessionError("invalid-response");
          return null;
        }
        setCurrentSessionError("");
        const workspace = await fetchJson(fetchImpl, buildBackendUrl(botBaseUrl, "/desktop-pet/workspace/summary", {
          user_id: requestedSessionId, real_user_id: profileUserId, character_pack_id: requestedCharacterPackId, limit: 12, t: String(Date.now())
        }));
        return { ...payload, bot_id: boundBotId,
          workspace_outputs: workspace.ok && Array.isArray(workspace.data?.sections?.outputs) ? workspace.data.sections.outputs : [],
          workspace_outputs_status: workspace.ok ? "ready" : "unavailable" };
      } catch (error) {
        setCurrentSessionError(`request-failed:${formatDataSourceError(error)}`);
        return null;
      }
    },
    async readChatImagePreview(item, overrides = {}, signal) {
      const health = await ensureVerifiedBackend();
      if (!health) throw Error("preview_backend_unavailable");
      return readChatImageBlob({ fetchImpl, baseUrl: botBaseUrl, instanceId: health.data.instance_id,
        profileUserId, sessionId: overrides.sessionId || sessionId,
        characterPackId: overrides.characterPackId ?? characterPackId,
        handle: item.handle, itemType: item.itemType || "attachment", signal });
    },
    async readChatHistoryPage(overrides = {}) {
      if (typeof fetchImpl !== "function") return null;
      const health = await ensureVerifiedBackend();
      if (!health) return null;
      const requestedSessionId = String(overrides.sessionId || sessionId).trim() || sessionId;
      const requestedCharacterPackId = String(overrides.characterPackId ?? characterPackId).trim();
      const beforeSeq = Number(overrides.beforeSeq);
      const limit = Math.max(1, Math.min(120, Number(overrides.limit) || 60));
      if (!Number.isFinite(beforeSeq) || beforeSeq <= 0) return null;
      const result = await fetchJson(
        fetchImpl,
        buildBackendUrl(botBaseUrl, "/sessions/messages", {
          user_id: requestedSessionId,
          session_id: requestedSessionId,
          real_user_id: profileUserId,
          client,
          ...(requestedCharacterPackId ? { character_pack_id: requestedCharacterPackId } : {}),
          before_seq: String(Math.trunc(beforeSeq)),
          limit: String(Math.trunc(limit)),
          t: String(Date.now())
        })
      );
      return result.ok && result.data && typeof result.data === "object" ? result.data : null;
    }
  };
}
