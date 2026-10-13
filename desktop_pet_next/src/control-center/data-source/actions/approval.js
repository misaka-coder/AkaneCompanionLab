import { buildBackendUrl, readActionResponse } from "../http.js";
import { createNotImplementedActionResult } from "../../action-router.js";
import { formatDataSourceError } from "../shared.js";

export async function runApprovalPolicyBackendAction(fetchImpl, baseUrl, actionId, payload = {}, params = {}) {
  const familyId = String(payload.familyId || payload.family_id || "").trim();
  const familyMode = String(payload.mode || "").trim();
  if (!["ops", "extensions"].includes(familyId) || !["trusted_auto_allow", "ask_each_time", "disabled"].includes(familyMode)) {
    return { ok: false, status: "invalid-payload", actionId, refresh: false, error: "familyId and mode are required" };
  }
  const body = { familyId, mode: familyMode };
  try {
    const response = await fetchImpl(buildBackendUrl(baseUrl, "/capabilities/approval-policy", params), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
      cache: "no-store"
    });
    if (response.status === 404 || response.status === 405) {
      return createNotImplementedActionResult(actionId);
    }
    if (!response.ok) {
      return { ok: false, status: `http-${response.status}`, actionId, refresh: false };
    }
    const result = await readActionResponse(response);
    return {
      ...result,
      ok: Boolean(result?.ok),
      actionId,
      refresh: result?.refresh === undefined ? true : Boolean(result.refresh)
    };
  } catch (error) {
    return { ok: false, status: "request-failed", actionId, refresh: false, error: formatDataSourceError(error) };
  }
}

export async function runApprovalRequestBackendAction(fetchImpl, baseUrl, actionId, payload = {}, params = {}) {
  const requestId = String(payload.requestId || payload.request_id || "").trim();
  const decision = String(payload.decision || "").trim().toLowerCase();
  if (!requestId || !["approved", "denied"].includes(decision)) {
    return { ok: false, status: "invalid-payload", actionId, refresh: false, error: "requestId and decision are required" };
  }
  try {
    const response = await fetchImpl(buildBackendUrl(baseUrl, `/capabilities/approval-requests/${encodeURIComponent(requestId)}/decision`, params), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ decision }),
      cache: "no-store"
    });
    if (response.status === 404 || response.status === 405) return createNotImplementedActionResult(actionId);
    if (!response.ok) return { ok: false, status: `http-${response.status}`, actionId, requestId, refresh: false };
    const result = await readActionResponse(response);
    return {
      ...result,
      ok: Boolean(result?.ok),
      actionId,
      requestId,
      refresh: result?.refresh === undefined ? true : Boolean(result.refresh)
    };
  } catch (error) {
    return { ok: false, status: "request-failed", actionId, requestId, refresh: false, error: formatDataSourceError(error) };
  }
}
