export const MODEL_SERVICE_ACTIONS = Object.freeze({
  models: "model.models",
  test: "model.test",
  save: "model.save",
  subagentModels: "model.subagent.models",
  subagentTest: "model.subagent.test",
  visionModels: "model.vision.models",
  visionTest: "model.vision.test"
});

export function normalizeModelServiceRuntime(value, connected = false) {
  const source = asObject(value);
  const providers = (Array.isArray(source.providers) ? source.providers : []).map((item) => {
    const provider = asObject(item);
    return {
      id: text(provider.id),
      label: text(provider.label) || text(provider.id),
      description: text(provider.description),
      protocol: text(provider.protocol) || "openai",
      baseUrl: text(provider.baseUrl),
      apiKeyRequired: provider.apiKeyRequired !== false
    };
  }).filter((item) => item.id);
  const providerId = text(source.providerId) || providers[0]?.id || "openai_compatible";
  const subagentSource = asObject(source.subagent);
  const subagentMode = ["inherit", "shared_service", "independent"].includes(text(subagentSource.mode))
    ? text(subagentSource.mode)
    : "inherit";
  const subagentProviderId = text(subagentSource.providerId) || providers[0]?.id || "openai_compatible";
  const subagent = {
    mode: subagentMode,
    providerId: subagentProviderId,
    protocol: text(subagentSource.protocol) || providers.find((item) => item.id === subagentProviderId)?.protocol || "openai",
    baseUrl: text(subagentSource.baseUrl),
    hasApiKey: Boolean(subagentSource.hasApiKey),
    model: text(subagentSource.model),
    thinkingMode: text(subagentSource.thinkingMode),
    reasoningEffort: text(subagentSource.reasoningEffort),
    maxOutputTokens: finiteNumber(subagentSource.maxOutputTokens, 0),
    timeoutSeconds: finiteNumber(subagentSource.timeoutSeconds, 120),
    configured: Boolean(subagentSource.configured)
  };

  return {
    available: connected && Boolean(Object.keys(source).length),
    connected,
    status: text(source.status) || "unconfigured",
    configured: text(source.status) === "configured",
    source: text(source.source),
    providerId,
    protocol: text(source.protocol) || providers.find((item) => item.id === providerId)?.protocol || "openai",
    baseUrl: text(source.baseUrl),
    chatModel: text(source.chatModel),
    chatThinkingMode: text(source.chatThinkingMode),
    chatReasoningEffort: text(source.chatReasoningEffort),
    chatMaxOutputTokens: finiteNumber(source.chatMaxOutputTokens, 0),
    hasApiKey: Boolean(source.hasApiKey),
    useForVision: source.useForVision !== false,
    visionModel: text(source.visionModel),
    visionConfigured: Boolean(source.visionConfigured),
    standaloneVision: source.standaloneVision ?? Boolean(source.visionConfigured || (text(source.visionBaseUrl) && text(source.visionModel))),
    visionBaseUrl: text(source.visionBaseUrl),
    visionApiProtocol: text(source.visionApiProtocol) || "openai",
    hasVisionApiKey: Boolean(source.hasVisionApiKey),
    timeoutSeconds: finiteNumber(source.timeoutSeconds, 120),
    subagent,
    providers
  };
}

export function createModelServiceDraft(model = {}) {
  const source = asObject(model);
  const standaloneVision = source.standaloneVision ?? Boolean(source.visionConfigured || (text(source.visionBaseUrl) && text(source.visionModel)));
  const subagentSource = asObject(source.subagent);
  const subagentDraft = {
    mode: ["inherit", "shared_service", "independent"].includes(text(subagentSource.mode))
      ? text(subagentSource.mode)
      : "inherit",
    providerId: text(subagentSource.providerId) || "openai_compatible",
    protocol: text(subagentSource.protocol) || "openai",
    baseUrl: text(subagentSource.baseUrl),
    apiKey: "",
    model: text(subagentSource.model),
    thinkingMode: text(subagentSource.thinkingMode),
    reasoningEffort: text(subagentSource.reasoningEffort),
    maxOutputTokens: finiteNumber(subagentSource.maxOutputTokens, 0),
    timeoutSeconds: finiteNumber(subagentSource.timeoutSeconds, 120),
    clearApiKey: false
  };

  return {
    providerId: text(source.providerId) || "openai_compatible",
    protocol: text(source.protocol) || "openai",
    baseUrl: text(source.baseUrl),
    apiKey: "",
    chatModel: text(source.chatModel),
    chatThinkingMode: text(source.chatThinkingMode),
    chatReasoningEffort: text(source.chatReasoningEffort),
    chatMaxOutputTokens: finiteNumber(source.chatMaxOutputTokens, 0),
    useForVision: source.useForVision !== false,
    visionModel: text(source.visionModel),
    standaloneVision,
    visionBaseUrl: text(source.visionBaseUrl),
    visionApiKey: "",
    visionApiProtocol: text(source.visionApiProtocol) || "openai",
    timeoutSeconds: finiteNumber(source.timeoutSeconds, 120),
    clearApiKey: false,
    subagent: subagentDraft
  };
}

export function applyModelProvider(model, draft, providerId) {
  const selectedId = text(providerId);
  const provider = (Array.isArray(model?.providers) ? model.providers : []).find((item) => item.id === selectedId);
  return {
    ...createModelServiceDraft(draft),
    ...draft,
    providerId: selectedId,
    protocol: text(provider?.protocol) || text(draft?.protocol) || "openai",
    baseUrl: text(provider?.baseUrl) || (selectedId === "openai_compatible" ? text(draft?.baseUrl) : ""),
    apiKey: ""
  };
}

export function applySubagentModelProvider(model, draft, providerId) {
  const selectedId = text(providerId);
  const provider = (Array.isArray(model?.providers) ? model.providers : []).find((item) => item.id === selectedId);
  const currentSubagent = asObject(draft?.subagent);
  return {
    ...createModelServiceDraft(draft),
    ...draft,
    subagent: {
      ...currentSubagent,
      providerId: selectedId,
      protocol: text(provider?.protocol) || text(currentSubagent?.protocol) || "openai",
      baseUrl: text(provider?.baseUrl) || (selectedId === "openai_compatible" ? text(currentSubagent?.baseUrl) : ""),
      apiKey: ""
    }
  };
}

export function modelServicePayload(draft = {}, target = "") {
  const payload = {
    providerId: text(draft.providerId),
    protocol: text(draft.protocol) || "openai",
    baseUrl: text(draft.baseUrl),
    apiKey: text(draft.apiKey),
    chatModel: text(draft.chatModel),
    chatThinkingMode: text(draft.chatThinkingMode),
    chatReasoningEffort: text(draft.chatReasoningEffort),
    chatMaxOutputTokens: Math.max(0, finiteNumber(draft.chatMaxOutputTokens, 0)),
    useForVision: draft.useForVision !== false,
    visionModel: text(draft.visionModel),
    timeoutSeconds: Math.max(5, Math.min(600, finiteNumber(draft.timeoutSeconds, 120))),
    clearApiKey: Boolean(draft.clearApiKey)
  };
  payload.standaloneVision = Boolean(draft.standaloneVision);
  payload.visionBaseUrl = text(draft.visionBaseUrl);
  payload.visionApiKey = text(draft.visionApiKey);
  payload.visionApiProtocol = text(draft.visionApiProtocol) || "openai";
  const subagent = asObject(draft.subagent);
  payload.subagent = {
    mode: ["inherit", "shared_service", "independent"].includes(text(subagent.mode))
      ? text(subagent.mode)
      : "inherit",
    providerId: text(subagent.providerId) || "openai_compatible",
    protocol: text(subagent.protocol) || "openai",
    baseUrl: text(subagent.baseUrl),
    apiKey: text(subagent.apiKey),
    model: text(subagent.model),
    thinkingMode: text(subagent.thinkingMode),
    reasoningEffort: text(subagent.reasoningEffort),
    maxOutputTokens: Math.max(0, finiteNumber(subagent.maxOutputTokens, 0)),
    timeoutSeconds: Math.max(5, Math.min(600, finiteNumber(subagent.timeoutSeconds, 120))),
    clearApiKey: Boolean(subagent.clearApiKey)
  };
  if (target) {
    payload.target = target;
  }
  return payload;
}

export function modelServiceOperation(actionId) {
  if ([MODEL_SERVICE_ACTIONS.subagentModels, MODEL_SERVICE_ACTIONS.visionModels].includes(actionId)) return "models";
  if ([MODEL_SERVICE_ACTIONS.subagentTest, MODEL_SERVICE_ACTIONS.visionTest].includes(actionId)) return "test";
  return Object.entries(MODEL_SERVICE_ACTIONS).find(([, value]) => value === actionId)?.[0] || "";
}

export async function runModelServiceBridgeAction(source, actionId, payload = {}) {
  const operation = modelServiceOperation(actionId);
  if (!operation || typeof source?.runModelServiceAction !== "function") {
    return { ok: false, status: "not-available", actionId, refresh: false };
  }
  const result = await source.runModelServiceAction(operation, payload);
  return {
    ...result,
    actionId,
    refresh: operation === "save" && Boolean(result?.ok)
  };
}

function finiteNumber(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function text(value) {
  return typeof value === "string" ? value.trim() : value == null ? "" : String(value).trim();
}
