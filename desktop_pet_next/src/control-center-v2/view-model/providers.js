import { asObject, finiteNumber, optionalFiniteNumber, text } from "./shared.js";

export function normalizeAbilityProviders(value) {
  return (Array.isArray(value) ? value : []).map((item) => {
    const entry = asObject(item);
    return {
      id: text(entry.id),
      title: text(entry.title) || text(entry.name) || "本地服务",
      description: text(entry.description),
      adapter: text(entry.adapter),
      type: text(entry.type),
      status: text(entry.status) || "missing_config",
      statusLabel: text(entry.statusLabel) || "待配置",
      statusTone: text(entry.statusTone) || "warning",
      reason: text(entry.reason),
      enabled: Boolean(entry.enabled),
      configured: Boolean(entry.configured),
      endpoint: text(entry.endpoint),
      defaultEndpoint: text(entry.defaultEndpoint),
      usedByLabel: text(entry.usedByLabel),
      voiceProfiles: normalizeAbilityVoiceProfiles(entry.voiceProfiles),
      defaultVoiceProfile: voiceProfileIdOf(entry.defaultVoiceProfile) ? normalizeAbilityVoiceProfile(entry.defaultVoiceProfile) : null,
      actionsEnabled: entry.actionsEnabled !== false && Boolean(text(entry.id))
    };
  }).filter((item) => item.title);
}

function normalizeAbilityVoiceProfiles(value) {
  return (Array.isArray(value) ? value : [])
    .map((item) => normalizeAbilityVoiceProfile(item))
    .filter((item) => item.voiceProfileId);
}

function voiceProfileIdOf(value) {
  const entry = asObject(value);
  return text(entry.voiceProfileId) || text(entry.id);
}

function normalizeAbilityVoiceProfile(value) {
  const entry = asObject(value);
  const voiceProfileId = voiceProfileIdOf(entry);
  return {
    voiceProfileId,
    providerId: text(entry.providerId),
    name: text(entry.name) || voiceProfileId || "GPT-SoVITS 声线",
    enabled: entry.enabled !== false,
    configured: Boolean(entry.configured),
    status: text(entry.status) || "missing_config",
    statusLabel: text(entry.statusLabel) || "待配置",
    statusTone: text(entry.statusTone) || "warning",
    reason: text(entry.reason),
    textLang: text(entry.textLang) || "zh",
    promptLang: text(entry.promptLang) || "zh",
    mediaType: text(entry.mediaType) || "wav",
    irodoriVoiceId: text(entry.irodoriVoiceId),
    parallelInfer: typeof entry.parallelInfer === "boolean" ? entry.parallelInfer : null,
    splitBucket: typeof entry.splitBucket === "boolean" ? entry.splitBucket : null,
    batchSize: optionalFiniteNumber(entry.batchSize),
    topK: optionalFiniteNumber(entry.topK),
    topP: optionalFiniteNumber(entry.topP),
    temperature: optionalFiniteNumber(entry.temperature),
    speedFactor: optionalFiniteNumber(entry.speedFactor),
    fragmentInterval: optionalFiniteNumber(entry.fragmentInterval),
    textSplitMethod: text(entry.textSplitMethod),
    referenceAudioName: text(entry.referenceAudioName),
    promptTextLength: Math.max(0, Math.round(finiteNumber(entry.promptTextLength, 0))),
    emotionSampleCount: Math.max(0, Math.round(finiteNumber(entry.emotionSampleCount, 0))),
    emotionSamples: (Array.isArray(entry.emotionSamples) ? entry.emotionSamples : []).map((sample) => {
      const item = asObject(sample);
      return {
        emotionId: text(item.emotionId),
        aliases: (Array.isArray(item.aliases) ? item.aliases : []).map(text).filter(Boolean),
        referenceAudioName: text(item.referenceAudioName),
        promptTextLength: Math.max(0, Math.round(finiteNumber(item.promptTextLength, 0)))
      };
    }).filter((sample) => sample.emotionId),
    updatedAt: text(entry.updatedAt)
  };
}
