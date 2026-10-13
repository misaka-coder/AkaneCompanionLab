import { buildBackendUrl, readActionResponse } from "../http.js";
import { formatDataSourceError } from "../shared.js";

export function createSettingActionMethods({ fetchImpl, botBaseUrl }) {
  return {
    async updateSetting(key, value) {
      if (typeof fetchImpl !== "function") return { ok: false, status: "not-available" };
      try {
        const response = await fetchImpl(
          buildBackendUrl(botBaseUrl, `/control-center/settings-catalog/${encodeURIComponent(key)}`),
          {
            method: "POST",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify({ value }),
            cache: "no-store"
          }
        );
        const data = await response.json();
        if (!response.ok && data?.ok === true) {
          return { ...data, ok: false, status: `http-${response.status}` };
        }
        return data && typeof data === "object" ? data : { ok: false, status: "bad-response" };
      } catch (error) {
        return { ok: false, status: "request-failed" };
      }
    },
    async runModelServiceAction(actionId, payload = {}) {
      if (typeof fetchImpl !== "function") {
        return { ok: false, status: "not-available", actionId };
      }
      const actionPath = actionId === "save" ? "" : `/${encodeURIComponent(actionId)}`;
      try {
        const response = await fetchImpl(
          buildBackendUrl(botBaseUrl, `/control-center/model-service${actionPath}`),
          {
            method: "POST",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify(payload),
            cache: "no-store"
          }
        );
        const result = await readActionResponse(response);
        return {
          ...result,
          ok: response.ok && Boolean(result?.ok),
          actionId,
          status: result?.status || (response.ok ? "available" : `http-${response.status}`)
        };
      } catch (error) {
        return {
          ok: false,
          status: "request-failed",
          actionId,
          error: formatDataSourceError(error)
        };
      }
    }
  };
}
