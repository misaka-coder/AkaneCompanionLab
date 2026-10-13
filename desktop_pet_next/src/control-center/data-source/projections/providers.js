import { asArray, asObject, optionalNumber, positiveNumber, stringValue } from "../shared.js";
import { mapCapabilityStatus } from "./capability-common.js";

export function buildAbilityProviderCards(entries, catalog, voiceProfilesCatalog = null) {
  const payload = asObject(catalog);
  const configStatus = stringValue(payload.providerConfigStatus || payload.configStatus || "available");
  const voiceProfiles = normalizeVoiceProfileEntries(voiceProfilesCatalog);
  return entries
    .filter((entry) => entry.kind === "provider" && entry.configurable && entry.source !== "mcp" && entry.adapter !== "mcp_stdio")
    .map((entry) => {
      const status = mapCapabilityStatus(entry.status);
      const providerVoiceProfiles = voiceProfiles.filter((profile) => profile.providerId === entry.id);
      return {
        id: entry.id,
        name: entry.name || providerDisplayName(entry),
        title: entry.name || providerDisplayName(entry),
        description: providerDescription(entry),
        adapter: entry.adapter,
        type: entry.type,
        source: entry.source,
        status: entry.status || "missing_config",
        statusLabel: status.label,
        statusTone: status.tone,
        reason: providerReasonLabel(entry),
        enabled: Boolean(entry.enabled),
        configured: Boolean(entry.configured),
        configurable: true,
        endpoint: entry.endpoint,
        defaultEndpoint: entry.defaultEndpoint,
        voiceProfiles: providerVoiceProfiles,
        defaultVoiceProfile: providerVoiceProfiles.find((profile) => profile.status === "ready") || providerVoiceProfiles[0] || null,
        usedByLabel: providerUsedByLabel(entry.usedBy),
        configStatus,
        actionsEnabled: configStatus !== "invalid_config"
      };
    });
}

function normalizeVoiceProfileEntries(catalog) {
  return asArray(asObject(catalog).voiceProfiles)
    .map((entry) => {
      const voiceProfileId = stringValue(entry.voiceProfileId || entry.id);
      const status = stringValue(entry.status || (entry.enabled === false ? "disabled" : "missing_config"));
      const mappedStatus = mapCapabilityStatus(status);
      return {
        id: voiceProfileId,
        voiceProfileId,
        providerId: stringValue(entry.providerId || "provider.tts.gpt_sovits.local"),
        name: stringValue(entry.name || voiceProfileId || "GPT-SoVITS 声线"),
        enabled: entry.enabled !== false,
        configured: Boolean(entry.configured),
        status,
        statusLabel: mappedStatus.label,
        statusTone: mappedStatus.tone,
        reason: stringValue(entry.reason),
        textLang: stringValue(entry.textLang || "zh"),
        promptLang: stringValue(entry.promptLang || "zh"),
        mediaType: stringValue(entry.mediaType || "wav"),
        irodoriVoiceId: stringValue(entry.irodoriVoiceId),
        parallelInfer: typeof entry.parallelInfer === "boolean" ? entry.parallelInfer : null,
        splitBucket: typeof entry.splitBucket === "boolean" ? entry.splitBucket : null,
        batchSize: optionalNumber(entry.batchSize),
        topK: optionalNumber(entry.topK),
        topP: optionalNumber(entry.topP),
        temperature: optionalNumber(entry.temperature),
        speedFactor: optionalNumber(entry.speedFactor),
        fragmentInterval: optionalNumber(entry.fragmentInterval),
        textSplitMethod: stringValue(entry.textSplitMethod),
        referenceAudioName: stringValue(entry.referenceAudioName),
        promptTextLength: positiveNumber(entry.promptTextLength),
        emotionSampleCount: positiveNumber(entry.emotionSampleCount),
        emotionSamples: asArray(entry.emotionSamples).map((sample) => ({
          emotionId: stringValue(sample?.emotionId),
          aliases: asArray(sample?.aliases).map(stringValue).filter(Boolean),
          referenceAudioName: stringValue(sample?.referenceAudioName),
          promptTextLength: positiveNumber(sample?.promptTextLength)
        })).filter((sample) => sample.emotionId),
        updatedAt: stringValue(entry.updatedAt)
      };
    })
    .filter((entry) => entry.voiceProfileId);
}

function providerDisplayName(entry) {
  if (entry.adapter === "comfyui") return "本地 ComfyUI";
  if (entry.adapter === "gpt_sovits") return "本地 GPT-SoVITS";
  if (entry.adapter === "irodori") return "本地 Irodori-TTS";
  return entry.id || "本地 Provider";
}

function providerDescription(entry) {
  if (entry.adapter === "comfyui") return "用于角色立绘处理、透明背景抠图和已绑定的图像工作流";
  if (entry.adapter === "gpt_sovits") return "外部 GPT-SoVITS 兼容服务；配置后用于角色语音合成与自定义声线";
  if (entry.adapter === "irodori") return "日语角色语音；当前使用平静语气，桌宠与 QQ 共用合成服务";
  return entry.type === "tts_provider" ? "本地语音能力提供方" : "本地能力执行器";
}

function providerUsedByLabel(usedBy = []) {
  const labels = {
    workshop: "角色工坊",
    image: "图像处理",
    desktop_pet: "桌宠",
    voice: "语音"
  };
  const mapped = usedBy.map((item) => labels[item] || "").filter(Boolean);
  return mapped.length ? mapped.join(" / ") : "本地能力";
}

function providerReasonLabel(entry) {
  const status = entry.status || "";
  if (status === "missing_config") return "需要配置本机服务地址";
  if (status === "configured") return "已保存，建议检查连接";
  if (status === "disabled") return "已配置但未启用";
  if (status === "unreachable") return "本地服务暂时未连接";
  if (status === "invalid_config") return "配置需要修复";
  if (status === "ready") return "连接正常";
  return entry.reason || "等待确认";
}
