import { buildBackendUrl, fetchJson } from "../http.js";
import { readPluginConnectionConfigs } from "../catalog-reads.js";

export function createCatalogsMethods({
  fetchImpl,
  ensureVerifiedBackend,
  botBaseUrl,
  baseUrl,
  profileUserId
}) {
  return {
    async readModelService() {
      if (typeof fetchImpl !== "function") return null;
      if (!(await ensureVerifiedBackend())) return null;
      const result = await fetchJson(
        fetchImpl,
        buildBackendUrl(botBaseUrl, "/control-center/model-service", { t: String(Date.now()) })
      );
      return result.ok && result.data && typeof result.data === "object" ? result.data : null;
    },
    async readSettingsCatalog() {
      if (typeof fetchImpl !== "function") return null;
      if (!(await ensureVerifiedBackend())) return null;
      const result = await fetchJson(
        fetchImpl,
        buildBackendUrl(botBaseUrl, "/control-center/settings-catalog", { t: String(Date.now()) })
      );
      return result.ok && result.data && typeof result.data === "object" ? result.data : null;
    },
    async readBotCatalog() {
      if (typeof fetchImpl !== "function") return null;
      if (!(await ensureVerifiedBackend())) return null;
      const result = await fetchJson(
        fetchImpl,
        buildBackendUrl(baseUrl, "/api/bots", { t: String(Date.now()) })
      );
      return result.ok && result.data && typeof result.data === "object" ? result.data : null;
    },
    async readPluginCatalog() {
      if (typeof fetchImpl !== "function") {
        return { ok: false, status: "not-available", data: null };
      }
      if (!(await ensureVerifiedBackend())) {
        return { ok: false, status: "backend-unavailable", data: null };
      }
      const result = await fetchJson(
        fetchImpl,
        buildBackendUrl(botBaseUrl, "/plugins/catalog", { t: String(Date.now()) })
      );
      const payload = result?.data && typeof result.data === "object" ? result.data : null;
      if (!result.ok || payload?.ok !== true || !Array.isArray(payload.plugins)) {
        return {
          ok: false,
          status: result.status || "invalid-plugin-catalog",
          data: null,
          error: result.error || null
        };
      }
      const enrichedPayload = await readPluginConnectionConfigs(fetchImpl, botBaseUrl, payload, profileUserId);
      return { ok: true, status: "available", data: enrichedPayload };
    },
    async readPluginMarket() {
      if (typeof fetchImpl !== "function" || !(await ensureVerifiedBackend())) {
        return { ok: false, status: "backend-unavailable", data: null };
      }
      const result = await fetchJson(fetchImpl, buildBackendUrl(botBaseUrl, "/plugins/market", { t: String(Date.now()) }));
      const payload = result?.data;
      if (!result.ok || payload?.ok !== true || !Array.isArray(payload.plugins)) {
        return { ok: false, status: result.status || "invalid-plugin-market", data: null,
          error: payload?.reason || result.error || "plugin_market_unavailable" };
      }
      return { ok: true, status: "available", data: payload };
    },
    async readPluginManagement() {
      if (typeof fetchImpl !== "function") {
        return { ok: false, status: "not-available", data: null };
      }
      if (!(await ensureVerifiedBackend())) {
        return { ok: false, status: "backend-unavailable", data: null };
      }
      const result = await fetchJson(
        fetchImpl,
        buildBackendUrl(botBaseUrl, "/admin/plugins/status", { t: String(Date.now()) })
      );
      const payload = result?.data && typeof result.data === "object" ? result.data : null;
      if (!result.ok || !payload || !payload.management || !payload.artifacts) {
        return {
          ok: false,
          status: result.status || "invalid-plugin-management",
          data: null,
          error: result.error || null
        };
      }
      return { ok: true, status: "available", data: payload };
    }
  };
}
