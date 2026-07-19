const BOT_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;

export function normalizeBotId(value) {
  const normalized = String(value || "").trim();
  return BOT_ID_PATTERN.test(normalized) ? normalized : "";
}

export function botApiPrefix(botId) {
  const normalized = normalizeBotId(botId);
  return normalized ? `/api/bots/${encodeURIComponent(normalized)}` : "";
}

export function botScopedPath(botId, path) {
  const value = String(path || "").trim();
  if (!value || /^https?:\/\//i.test(value) || value.startsWith("/api/bots/")) {
    return value || "/";
  }
  const prefix = botApiPrefix(botId);
  const endpoint = value.startsWith("/") ? value : `/${value}`;
  if (!prefix) return endpoint;
  if (endpoint === "/api/qq" || endpoint.startsWith("/api/qq/")) {
    return `${prefix}/qq${endpoint.slice("/api/qq".length)}`;
  }
  return `${prefix}/${endpoint.replace(/^\/+/, "")}`;
}

export function buildBotBackendBaseUrl(baseUrl, botId) {
  const prefix = botApiPrefix(botId);
  const normalizedBaseUrl = new URL(String(baseUrl || "")).toString().replace(/\/+$/, "");
  return prefix ? new URL(`${prefix.replace(/^\/+/, "")}/`, `${normalizedBaseUrl}/`).toString().replace(/\/+$/, "") : normalizedBaseUrl;
}
