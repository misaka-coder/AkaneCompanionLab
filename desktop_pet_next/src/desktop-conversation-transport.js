import { waitWithSignal } from "./request-cancellation.js";

const CHAT_HEADER = "X-Akane-Desktop-Chat-Token";

function conversationTarget(input, init) {
  const url = new URL(String(input));
  if (url.protocol === "ws:") url.protocol = "http:";
  if (url.protocol === "wss:") url.protocol = "https:";
  const scoped = url.pathname.match(/^(\/api\/bots\/[^/]+)(\/.*)$/);
  const prefix = scoped?.[1] || "";
  const path = scoped?.[2] || url.pathname;
  const method = String(init?.method || "GET").toUpperCase();
  const conversation = method === "WEBSOCKET" && path === "/voice/realtime" || method === "POST" && [
    "/think", "/think_once", "/think/steer", "/think/stop", "/think/presence",
    "/desktop-pet/care/snapshot", "/desktop-pet/care/action",
    "/desktop-pet/workspace/action", "/desktop-pet/workspace/import-file",
    "/desktop-pet/attachments/audio", "/desktop-pet/music-timeline/prepare",
    "/desktop-pet/vision/clip", "/desktop-pet/vision/reaction", "/desktop-pet/vision/clear"
  ].includes(path) || method === "GET" && (
    ["/desktop-pet/workspace/summary", "/desktop-pet/vision/latest"].includes(path) ||
    /^\/desktop-pet\/(?:workspace\/)?(?:attachments|generated)\/[^/]+\/content$/.test(path)
  );
  return conversation ? { key: url.origin + prefix, endpoint: url.origin + prefix + "/control-center/desktop-chat-session" } : null;
}

/** Native admin transport issues a scoped credential; chat remains an ordinary streaming fetch. */
export function createDesktopConversationFetch({ fetch, invoke, now = Date.now }) {
  const sessions = new Map();

  async function session(target) {
    const existing = sessions.get(target.key);
    if (existing && existing.expiresAt > now() + 30_000) return existing;
    if (existing?.pending) return existing.pending;
    const entry = {};
    entry.pending = (async () => {
      const result = await invoke("backend_admin_request", {
        request: { url: target.endpoint, method: "POST", body: "{}" }
      });
      let payload;
      try { payload = JSON.parse(result?.body || "{}"); } catch { /* handled below */ }
      if (!result?.ok || !payload?.ok || !payload.token || !(payload.expiresAt > now())) {
        throw new Error(String(result?.reason || payload?.reason || "desktop_chat_auth_unavailable"));
      }
      entry.token = payload.token;
      entry.expiresAt = payload.expiresAt;
      return entry;
    })();
    sessions.set(target.key, entry);
    try { return await entry.pending; }
    catch (error) {
      if (sessions.get(target.key) === entry) sessions.delete(target.key);
      throw error;
    } finally { delete entry.pending; }
  }

  const backendFetch = async function(input, init = {}) {
    const target = conversationTarget(input, init);
    if (!target) return fetch(input, init);
    if (init.signal?.aborted) throw init.signal.reason || new DOMException("Aborted", "AbortError");
    const credential = await waitWithSignal(session(target), init.signal);
    if (init.signal?.aborted) throw init.signal.reason || new DOMException("Aborted", "AbortError");
    const send = async value => {
      const headers = new Headers(init.headers);
      headers.set(CHAT_HEADER, value.token);
      return fetch(input, { ...init, headers });
    };
    const response = await send(credential);
    if (response.status !== 401) return response;
    // A backend restart invalidates the ephemeral credential before its expiry.
    if (sessions.get(target.key) === credential) sessions.delete(target.key);
    await response.body?.cancel();
    const refreshed = await waitWithSignal(session(target), init.signal);
    if (init.signal?.aborted) throw init.signal.reason || new DOMException("Aborted", "AbortError");
    return send(refreshed);
  };
  backendFetch.websocketProtocols = async input => {
    const target = conversationTarget(input, { method: "WEBSOCKET" });
    if (!target) return [];
    if (!sessions.get(target.key)?.pending) sessions.delete(target.key);
    const credential = await session(target);
    return ["akane.desktop-chat." + credential.token];
  };
  return backendFetch;
}
