import { normalizeBackendUrl } from "./shared.js";
import { createDesktopConversationFetch } from "../desktop-conversation-transport.js";

export function createWorkshopBackendClient({ view, isTauriRuntime, invoke, tauriFetch }) {
  const conversationFetch = createDesktopConversationFetch({ fetch: tauriFetch, invoke });
  let verifiedWorkshopBindingKey = "";
  let verifiedWorkshopBindingAt = 0;

  async function backendFetch(input, init = {}) {
    if (!isTauriRuntime) return window.fetch(input, init);
    await ensureWorkshopBackendBinding();
    const url = typeof input === "string" ? input : String(input?.url || input || "");
    const method = String(init?.method || "GET").trim().toUpperCase();
    const target = new URL(url);
    if (method === "POST" && target.pathname.startsWith("/capabilities/")) {
      const result = await invoke("backend_admin_request", {
        request: {
          url,
          body: typeof init?.body === "string" ? init.body : "{}"
        }
      });
      return new Response(String(result?.body || ""), {
        status: Number(result?.httpStatus || 502),
        headers: { "Content-Type": String(result?.contentType || "application/json") }
      });
    }
    return conversationFetch(input, init);
  }

  async function ensureWorkshopBackendBinding() {
    const key = `${view.instanceId}|${normalizeBackendUrl(view.backendUrl)}`;
    if (verifiedWorkshopBindingKey === key && Date.now() - verifiedWorkshopBindingAt < 3000) return;
    const result = await invoke("verify_backend_instance", {
      backendUrl: normalizeBackendUrl(view.backendUrl)
    });
    if (!result?.ok || String(result.instanceId || "") !== view.instanceId) {
      verifiedWorkshopBindingKey = "";
      verifiedWorkshopBindingAt = 0;
      const actual = String(result?.actualInstanceId || "").trim();
      throw new Error(actual
        ? `后端实例不匹配：当前 ${view.instanceId}，目标 ${actual}`
        : `无法验证实例 ${view.instanceId} 的后端身份`);
    }
    verifiedWorkshopBindingKey = key;
    verifiedWorkshopBindingAt = Date.now();
  }

  function buildBackendUrl(path, params = null) {
    const base = `${normalizeBackendUrl(view.backendUrl).replace(/\/+$/, "")}/`;
    const url = new URL(String(path || "/").replace(/^\/+/, ""), base);
    for (const [key, value] of Object.entries(params || {})) {
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, String(value));
      }
    }
    return url.toString();
  }

  return { backendFetch, buildBackendUrl };
}

export async function readResponseError(response, fallback) {
  try {
    const contentType = String(response.headers?.get?.("content-type") || "").toLowerCase();
    if (contentType.includes("json")) {
      const payload = await response.json();
      return String(payload?.detail || payload?.message || payload?.error || payload?.reason || payload?.status || fallback);
    }
    const text = String(await response.text()).trim();
    return text || fallback;
  } catch {
    return fallback;
  }
}
