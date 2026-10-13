import { asObject, coerceVolumePercent, stringValue } from "../shared.js";
import { inferLatencyLabel } from "./metrics.js";

export function buildVoiceRuntimePatch({ health, diagnostics, petState, capabilitiesCatalog }) {
  const healthData = asObject(health);
  const diagnosticsData = asObject(diagnostics);
  const runtime = asObject(diagnosticsData.runtime);
  const runtimeMetrics = asObject(runtime.metrics);
  const healthTts = asObject(healthData.tts);
  const healthAsr = asObject(healthData.asr);
  const ttsResolution = normalizeVoiceProviderResolution(capabilitiesCatalog, "voice.tts.character");
  const asrResolution = normalizeVoiceProviderResolution(capabilitiesCatalog, "voice.input.asr");

  const serviceOk = stringValue(healthData?.status) === "ok" || stringValue(diagnosticsData?.status) === "ok";
  const ttsEnabled = petState?.voiceEnabled ?? healthTts.enabled ?? serviceOk;
  const ttsVolume = coerceVolumePercent(petState?.voiceVolume, 80);
  const asrAvailable = Boolean(healthAsr.endpoint || Object.keys(healthAsr).length);
  const asrEnabled = petState?.voiceInputEnabled ?? asrAvailable;
  const overallState = serviceOk ? "正常运行" : "未连接";
  const overallTone = serviceOk ? "good" : "warning";
  const ttsOnline = ttsResolution?.activeProviderName || (healthTts.endpoint ? "在线" : serviceOk ? "未启用" : "离线");
  const ttsTone = ttsResolution?.statusTone || (healthTts.endpoint ? "good" : "warning");
  const asrOnline = asrResolution?.activeProviderName || (asrAvailable ? "在线" : "未启用");
  const asrTone = asrResolution?.statusTone || (asrAvailable ? "good" : "muted");
  const networkState = serviceOk ? "良好" : "离线";
  const networkTone = serviceOk ? "good" : "warning";
  const latency = inferLatencyLabel(runtimeMetrics, ["tts", "asr"]);


  return {
    tts: {
      enabled: Boolean(ttsEnabled),
      volume: ttsVolume,
      ...(ttsResolution ? { providerStatus: ttsResolution } : {}),
    },
    asr: {
      enabled: Boolean(asrEnabled),
      ...(asrResolution ? { providerStatus: asrResolution } : {})
    },
    diagnostics: [
      { label: "整体状态", value: overallState, tone: overallTone },
      { label: "TTS 语音引擎", value: ttsOnline, tone: ttsTone },
      { label: "ASR 语音引擎", value: asrOnline, tone: asrTone },
      { label: "响应延迟", value: latency, tone: serviceOk ? "good" : "warning" },
      { label: "网络状态", value: networkState, tone: networkTone }
    ]
  };
}

function normalizeVoiceProviderResolution(catalog, capabilityId) {
  const resolutions = asObject(asObject(catalog).resolutions);
  const raw = asObject(resolutions[capabilityId]);
  if (!raw.capabilityId && !raw.activeProviderId && !raw.requestedProviderId) return null;
  const status = stringValue(raw.status || "unavailable");
  const reason = stringValue(raw.reason);
  return {
    capabilityId: stringValue(raw.capabilityId || capabilityId),
    status,
    statusLabel: voiceProviderStatusLabel(status),
    statusTone: voiceProviderStatusTone(status),
    reason,
    reasonLabel: voiceProviderReasonLabel(reason),
    requestSource: stringValue(raw.requestSource),
    requestedProviderId: stringValue(raw.requestedProviderId),
    requestedProviderName: stringValue(raw.requestedProviderName || raw.requestedProviderId),
    activeProviderId: stringValue(raw.activeProviderId),
    activeProviderName: stringValue(raw.activeProviderName || raw.activeProviderId),
    fallbackProviderId: stringValue(raw.fallbackProviderId),
    voiceProfileId: stringValue(raw.voiceProfileId)
  };
}

function voiceProviderStatusLabel(status) {
  const labels = {
    ready: "已就绪",
    degraded: "已降级",
    disabled: "已关闭",
    unavailable: "不可用"
  };
  return labels[status] || "待确认";
}

function voiceProviderStatusTone(status) {
  if (status === "ready") return "good";
  if (status === "degraded") return "warning";
  return "muted";
}

function voiceProviderReasonLabel(reason) {
  const labels = {
    requested_voice_profile_missing: "角色声线档案未配置，保留文字回复",
    requested_provider_missing_config: "请求的语音服务还未配置",
    requested_provider_missing_executor: "请求的本地执行器未安装或未发现",
    requested_provider_missing_model: "请求的声线模型缺失",
    requested_provider_unreachable: "请求的语音服务暂时未连接",
    requested_provider_disabled: "请求的语音服务已关闭",
    requested_provider_pending_health_check: "请求的语音服务已保存，等待连接检查",
    requested_provider_unknown: "角色请求的语音服务暂不认识",
    requested_provider_invalid: "角色请求的语音服务标识无效",
    requested_provider_not_ready: "指定的语音服务尚未就绪，保留文字回复",
    text_only_requested: "角色使用仅文字回复",
    no_ready_provider: "没有可用的语音通道"
  };
  return labels[reason] || reason || "";
}
