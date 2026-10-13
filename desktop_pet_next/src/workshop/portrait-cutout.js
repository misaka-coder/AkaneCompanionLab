import { stableSignature, normalizeBackendUrl, formatError } from "./shared.js";
import { PORTRAIT_CUTOUT_CAPABILITY_CACHE_MS, PORTRAIT_CUTOUT_WORKFLOW_ID, CLIENT_MODE } from "./constants.js";
import { normalizePortraitCutoutCapability, portraitCutoutTone, buildGeneratedCutoutEmotionId, portraitMimeType, formatPortraitCutoutError } from "./portrait-model.js";
import { readResponseError } from "./transport.js";

export function createPortraitCutoutController({
  isTauriRuntime,
  view,
  cutoutState,
  backendFetch,
  buildBackendUrl,
  els,
  portraitState,
  isPortraitTarget,
  isPackWriteBusy,
  setPortraitStatus,
  invoke,
  runPortraitMutation,
  readPortraitImageBytes,
  startPortraitCutoutJob,
  waitForPortraitCutoutJob,
  fetchPortraitCutoutOutput,
}) {
  async function refreshPortraitCutoutCapability(options = {}) {
    if (!isTauriRuntime) {
      applyPortraitCutoutCapability({
        status: "unavailable",
        label: "浏览器预览不可用",
        detail: "请在桌宠窗口中读取本地能力状态。",
      });
      return view.portraitCutoutCapability;
    }
    const signature = stableSignature({
      backendUrl: normalizeBackendUrl(view.backendUrl),
      profileUserId: view.profileUserId || "master",
    });
    const cacheIsFresh = signature === cutoutState.lastCutoutCapabilitySignature &&
      Date.now() - cutoutState.lastCutoutCapabilitySyncedAt < PORTRAIT_CUTOUT_CAPABILITY_CACHE_MS;
    if (!options.force && cacheIsFresh) {
      renderPortraitCutoutStatus();
      return view.portraitCutoutCapability;
    }
    if (cutoutState.cutoutCapabilityRequest && signature === cutoutState.lastCutoutCapabilitySignature) {
      return cutoutState.cutoutCapabilityRequest;
    }
    cutoutState.lastCutoutCapabilitySignature = signature;
    const generation = ++cutoutState.cutoutCapabilityGeneration;
    cutoutState.cutoutCapabilityRequest = (async () => {
      try {
        const payload = await readWorkflowCatalog();
        if (generation !== cutoutState.cutoutCapabilityGeneration) return view.portraitCutoutCapability;
        const workflows = Array.isArray(payload?.workflows) ? payload.workflows : [];
        const workflow = workflows.find((item) => String(item?.id || "").trim() === PORTRAIT_CUTOUT_WORKFLOW_ID);
        applyPortraitCutoutCapability(workflow
          ? normalizePortraitCutoutCapability(workflow)
          : {
              status: "unavailable",
              label: "未找到透明背景处理入口",
              detail: "能力注册表暂未提供角色立绘透明背景处理。",
            });
        cutoutState.lastCutoutCapabilitySyncedAt = Date.now();
      } catch (error) {
        if (generation !== cutoutState.cutoutCapabilityGeneration) return view.portraitCutoutCapability;
        applyPortraitCutoutCapability({
          status: "unavailable",
          label: "能力状态未同步",
          detail: "立绘管理仍可正常使用；稍后可到设置里的能力页检查本地环境。",
          reason: formatError(error),
        });
        cutoutState.lastCutoutCapabilitySyncedAt = Date.now();
        if (!options.silent && view.activeTab === "portraits") {
          renderPortraitCutoutStatus();
        }
      } finally {
        if (generation === cutoutState.cutoutCapabilityGeneration) cutoutState.cutoutCapabilityRequest = null;
      }
      return view.portraitCutoutCapability;
    })();
    return cutoutState.cutoutCapabilityRequest;
  }

  async function readWorkflowCatalog() {
    const profileId = view.profileUserId || "master";
    const response = await backendFetch(buildBackendUrl("/capabilities/workflows", {
      user_id: "desktop",
      session_id: "desktop",
      real_user_id: profileId,
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
    return response.json();
  }

  function applyPortraitCutoutCapability(next) {
    view.portraitCutoutCapability = {
      status: String(next?.status || "unknown").trim() || "unknown",
      label: String(next?.label || "能力状态待同步").trim() || "能力状态待同步",
      detail: String(next?.detail || "").trim(),
      configured: Boolean(next?.configured),
      enabled: Boolean(next?.enabled),
      executionReady: Boolean(next?.executionReady),
      canConfigure: Boolean(next?.canConfigure),
      reason: String(next?.reason || "").trim(),
    };
    if (view.activeTab === "portraits") {
      renderPortraitCutoutStatus();
    }
  }

  function renderPortraitCutoutStatus() {
    if (!els.portraitCutoutStatus || !els.portraitCutoutSummary) return;
    const state = view.portraitCutoutCapability || {};
    const status = String(state.status || "unknown").trim() || "unknown";
    els.portraitCutoutStatus.dataset.state = portraitCutoutTone(status);
    els.portraitCutoutSummary.textContent = state.label || "能力状态待同步";
    els.portraitCutoutStatus.title = state.detail || "";
    if (els.portraitCutoutConfig) {
      els.portraitCutoutConfig.hidden = state.canConfigure === false;
      els.portraitCutoutConfig.textContent = state.configured ? "查看配置" : "去配置";
    }
    if (els.portraitCutoutRun) {
      const canRun = status === "ready" && state.executionReady && !view.portraitCutoutRunning
        && portraitState.previewingEmotion?.packId === portraitState.portraitLoadedPackId
        && Boolean(portraitState.portraitLoadedPackId) && isPortraitTarget(portraitState.portraitLoadedPackId)
        && !isPackWriteBusy(portraitState.portraitLoadedPackId);
      els.portraitCutoutRun.hidden = !(status === "ready" && state.executionReady);
      els.portraitCutoutRun.disabled = !canRun;
      els.portraitCutoutRun.textContent = view.portraitCutoutRunning ? "处理中..." : "自动抠图";
    }
  }

  async function openCapabilitySettings() {
    if (!isTauriRuntime) {
      setPortraitStatus("浏览器预览模式无法打开设置窗口。", true);
      return;
    }
    try {
      await invoke("open_settings_window");
      setPortraitStatus("已打开设置窗口。请在“能力”页查看本地能力环境。");
    } catch (error) {
      setPortraitStatus(`打开设置失败：${formatError(error)}`, true);
    }
  }

  async function runPortraitCutoutForPreview() {
    if (!isTauriRuntime) { setPortraitStatus("浏览器预览模式无法执行自动抠图。", true); return; }
    if (view.portraitCutoutRunning) return;
    const target = portraitState.previewingEmotion;
    const packId = String(target?.packId || "").trim();
    const outfitId = String(target?.outfitId || "").trim();
    const emotion = target?.emotion || null;
    const emotionId = String(emotion?.id || "").trim();
    if (!packId || !outfitId || !emotionId) {
      setPortraitStatus("请先在右侧预览一张要处理的表情图。", true);
      return;
    }
    return runPortraitMutation(packId, "自动抠图", async ({ current, report }) => {
      view.portraitCutoutRunning = true;
      renderPortraitCutoutStatus();
      try {
        const capability = await refreshPortraitCutoutCapability({ force: true, silent: true });
        if (!current()) return { cancelled: true };
        if (capability.status !== "ready" || !capability.executionReady) {
          report("自动抠图还没准备好，请先在能力页完成 ComfyUI 和工作流绑定。", true);
          return { cancelled: true };
        }
        const generatedEmotion = buildGeneratedCutoutEmotionId(emotionId);
        const outputHandle = `portrait_cutout_${Date.now().toString(36)}`;
        report(`正在处理：${outfitId} / ${emotionId}…`);
        const imageBytes = await readPortraitImageBytes(packId, outfitId, emotionId);
        if (!current()) return { cancelled: true };
        const job = await startPortraitCutoutJob({ inputImageHandle: "portrait_source",
          outputImageHandle: outputHandle, imageBytes, mimeType: portraitMimeType(emotion?.path) });
        const completed = await waitForPortraitCutoutJob(job.jobId);
        const output = Array.isArray(completed?.job?.outputs) ? completed.job.outputs[0] : null;
        const outputBytes = await fetchPortraitCutoutOutput(job.jobId, output?.handle || outputHandle);
        await invoke("import_generated_portrait_image", { packId, outfit: outfitId, emotion: generatedEmotion,
          imageBytes: Array.from(outputBytes), mimeType: output?.contentType || "image/png", overwrite: false });
        return { message: `已生成透明背景版本：${outfitId} / ${generatedEmotion}` };
      } catch (error) {
        throw new Error(formatPortraitCutoutError(error));
      } finally {
        view.portraitCutoutRunning = false;
        renderPortraitCutoutStatus();
      }
    });
  }

  return { refreshPortraitCutoutCapability, renderPortraitCutoutStatus, openCapabilitySettings, runPortraitCutoutForPreview };
}
