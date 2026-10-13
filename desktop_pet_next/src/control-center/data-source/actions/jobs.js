import { buildBackendUrl, readActionResponse } from "../http.js";
import { formatDataSourceError } from "../shared.js";

export async function runChatBackendAction(fetchImpl, baseUrl, actionId, payload = {}, params = {}) {
  const jobId = String(payload.jobId || payload.job_id || "").trim();
  const controlAction = String(payload.action || "").trim().toLowerCase();
  if (!jobId || !["pause", "resume", "stop"].includes(controlAction)) {
    return {
      ok: false,
      status: "invalid-payload",
      actionId,
      jobId,
      controlAction,
      refresh: false,
      error: "jobId and action are required"
    };
  }
  try {
    const response = await fetchImpl(
      buildBackendUrl(baseUrl, `/admin/plugins/jobs/${encodeURIComponent(jobId)}/control`, params),
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ action: controlAction }),
        cache: "no-store"
      }
    );
    const result = await readActionResponse(response);
    const ok = response.ok && Boolean(result?.ok);
    return {
      ...result,
      ok,
      actionId,
      jobId,
      controlAction,
      status: String(result?.status || (ok ? "completed" : `http-${response.status}`)),
      reason: ok ? String(result?.reason || "") : jobControlFailureDetail(result, response.status),
      jobStatus: String(result?.job_status || ""),
      controlState: String(result?.control_state || ""),
      refresh: true
    };
  } catch (error) {
    return {
      ok: false,
      status: "request-failed",
      actionId,
      jobId,
      controlAction,
      refresh: true,
      error: formatDataSourceError(error),
      reason: "请求没有完成，请检查服务连接后重试"
    };
  }
}

function jobControlFailureDetail(result, httpStatus) {
  const status = String(result?.status || "").trim();
  const reason = String(result?.reason || "").trim();
  if (status === "pause_unavailable") return "当前执行器没有合作式暂停点，请先停止任务";
  if (status === "resume_unavailable") return "当前任务没有处于可恢复的暂停状态";
  if (status === "unknown" || status === "not_found" || httpStatus === 404) return "任务已经不存在或不属于当前会话";
  if (status === "invalid" || httpStatus === 400) return "任务控制请求无效";
  if (httpStatus === 401 || httpStatus === 403) return "此操作只能从受信控制中心执行";
  return reason || `http-${httpStatus}`;
}
