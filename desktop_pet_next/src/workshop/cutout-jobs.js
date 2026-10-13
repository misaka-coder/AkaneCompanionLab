import { PORTRAIT_CUTOUT_WORKFLOW_ID, CLIENT_MODE } from "./constants.js";
import { readResponseError } from "./transport.js";
import { delay } from "./shared.js";

export function createPortraitCutoutJobs({
  backendFetch,
  buildBackendUrl,
  view,
  isTauriRuntime,
  setPortraitStatus,
}) {
  async function startPortraitCutoutJob({ inputImageHandle, outputImageHandle, imageBytes, mimeType }) {
    const response = await backendFetch(buildBackendUrl(`/capabilities/workflows/${PORTRAIT_CUTOUT_WORKFLOW_ID}/jobs`, {
      user_id: "desktop",
      session_id: "desktop",
      real_user_id: view.profileUserId || "master",
      client: CLIENT_MODE,
    }), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        inputImageHandle,
        outputImageHandle,
        inputImageBytes: Array.from(imageBytes),
        inputImageContentType: mimeType,
      }),
      cache: "no-store",
      ...(isTauriRuntime ? { connectTimeout: 3_500 } : {}),
    });
    if (!response.ok) {
      throw new Error(await readResponseError(response, `HTTP ${response.status}`));
    }
    const payload = await response.json();
    if (!payload?.ok || !payload?.jobId) {
      throw new Error(payload?.reason || payload?.status || "workflow_job_start_failed");
    }
    return payload;
  }

  async function waitForPortraitCutoutJob(jobId) {
    const startedAt = Date.now();
    while (Date.now() - startedAt < 90_000) {
      const response = await backendFetch(buildBackendUrl(`/capabilities/workflow-jobs/${jobId}`, {
        user_id: "desktop",
        session_id: "desktop",
        real_user_id: view.profileUserId || "master",
        client: CLIENT_MODE,
        t: Date.now(),
      }), {
        method: "GET",
        headers: { Accept: "application/json" },
        cache: "no-store",
        ...(isTauriRuntime ? { connectTimeout: 3_500 } : {}),
      });
      if (!response.ok) {
        throw new Error(await readResponseError(response, `HTTP ${response.status}`));
      }
      const payload = await response.json();
      const status = String(payload?.status || "").trim();
      if (status === "completed") return payload;
      if (status === "failed") {
        throw new Error(payload?.reason || "workflow_job_failed");
      }
      setPortraitStatus(`自动抠图处理中：${status || "running"}…`);
      await delay(900);
    }
    throw new Error("workflow_job_timeout");
  }

  async function fetchPortraitCutoutOutput(jobId, outputHandle) {
    const response = await backendFetch(buildBackendUrl(`/capabilities/workflow-jobs/${jobId}/outputs/${outputHandle}`, {
      user_id: "desktop",
      session_id: "desktop",
      real_user_id: view.profileUserId || "master",
      client: CLIENT_MODE,
    }), {
      method: "GET",
      headers: { Accept: "image/png,image/webp,image/jpeg,*/*" },
      cache: "no-store",
      ...(isTauriRuntime ? { connectTimeout: 3_500 } : {}),
    });
    if (!response.ok) {
      throw new Error(await readResponseError(response, `HTTP ${response.status}`));
    }
    return new Uint8Array(await response.arrayBuffer());
  }

  return { startPortraitCutoutJob, waitForPortraitCutoutJob, fetchPortraitCutoutOutput };
}
