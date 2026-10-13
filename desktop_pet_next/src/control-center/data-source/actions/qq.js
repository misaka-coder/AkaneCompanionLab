import { buildBackendUrl, qqSelfCheckPath, readActionResponse } from "../http.js";
import { createNotImplementedActionResult } from "../../action-router.js";
import { formatDataSourceError } from "../shared.js";

export async function runQqBackendAction(fetchImpl, baseUrl, actionId, payload = {}, params = {}) {
  // abilities.qq.selfCheck → POST /api/qq/self-check
  // 不暴露 token/cookie/本地路径；结果里敏感字段由后端过滤
  try {
    const response = await fetchImpl(buildBackendUrl(baseUrl, qqSelfCheckPath(baseUrl), params), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({}),
      cache: "no-store"
    });
    if (response.status === 404 || response.status === 405) {
      return createNotImplementedActionResult(actionId);
    }
    if (!response.ok) {
      return { ok: false, status: `http-${response.status}`, actionId, refresh: false };
    }
    const envelope = await readActionResponse(response);
    const data = envelope?.data || envelope || {};
    return {
      ok: Boolean(data.ok),
      status: String(data.status || (data.ok ? "connected" : "failed")),
      actionId,
      refresh: false,
      reason: String(data.reason || ""),
      payload: {
        selfCheckStatus: String(data.status || ""),
        reason: String(data.reason || ""),
        onebotHttpUrl: String(data.onebot_http_url || ""),
        botQq: String(data.bot_qq || ""),
        nickname: String(data.nickname || ""),
        checks: data.checks && typeof data.checks === "object" ? data.checks : {}
      }
    };
  } catch (error) {
    return { ok: false, status: "request-failed", actionId, refresh: false, error: formatDataSourceError(error) };
  }
}
