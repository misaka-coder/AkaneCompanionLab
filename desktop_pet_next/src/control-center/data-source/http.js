import { formatDataSourceError } from "./shared.js";

export const DEFAULT_BACKEND_URL = "http://127.0.0.1:9999";

export async function readActionResponse(response) {
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

export async function fetchJson(fetchImpl, url) {
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

export async function fetchText(fetchImpl, url) {
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

export function buildBackendUrl(baseUrl, endpoint, params = null) {
  const url = new URL(String(endpoint || "").replace(/^\/+/, ""), `${baseUrl.replace(/\/+$/, "")}/`);
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

export function qqSelfCheckPath(baseUrl) {
  try {
    return /^\/api\/bots\/[^/]+\/?$/.test(new URL(baseUrl).pathname)
      ? "/qq/self-check"
      : "/api/qq/self-check";
  } catch {
    return "/api/qq/self-check";
  }
}

export function normalizeBackendBaseUrl(value) {
  const raw = String(value || "").trim() || DEFAULT_BACKEND_URL;
  try {
    return new URL(raw).toString().replace(/\/+$/, "");
  } catch {
    return DEFAULT_BACKEND_URL;
  }
}
