export const MODEL_SERVICE_ACTIONS = Object.freeze({
  models: "model.models",
  test: "model.test",
  save: "model.save"
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
    hasApiKey: Boolean(source.hasApiKey),
    useForVision: source.useForVision !== false,
    visionModel: text(source.visionModel),
    visionConfigured: Boolean(source.visionConfigured),
    visionBaseUrl: text(source.visionBaseUrl),
    visionApiProtocol: text(source.visionApiProtocol) || "openai",
    hasVisionApiKey: Boolean(source.hasVisionApiKey),
    timeoutSeconds: finiteNumber(source.timeoutSeconds, 120),
    providers
  };
}

export function createModelServiceDraft(model = {}) {
  const source = asObject(model);
  const standaloneVision = Boolean(source.visionConfigured || (text(source.visionBaseUrl) && text(source.visionModel)));
  return {
    providerId: text(source.providerId) || "openai_compatible",
    protocol: text(source.protocol) || "openai",
    baseUrl: text(source.baseUrl),
    apiKey: "",
    chatModel: text(source.chatModel),
    useForVision: source.useForVision !== false,
    visionModel: text(source.visionModel),
    standaloneVision,
    visionBaseUrl: text(source.visionBaseUrl),
    visionApiKey: "",
    visionApiProtocol: text(source.visionApiProtocol) || "openai",
    timeoutSeconds: finiteNumber(source.timeoutSeconds, 120),
    clearApiKey: false
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

export function modelServicePayload(draft = {}) {
  const payload = {
    providerId: text(draft.providerId),
    protocol: text(draft.protocol) || "openai",
    baseUrl: text(draft.baseUrl),
    apiKey: text(draft.apiKey),
    chatModel: text(draft.chatModel),
    useForVision: draft.useForVision !== false,
    visionModel: text(draft.visionModel),
    timeoutSeconds: Math.max(5, Math.min(600, finiteNumber(draft.timeoutSeconds, 120))),
    clearApiKey: Boolean(draft.clearApiKey)
  };
  if (draft.standaloneVision && text(draft.visionBaseUrl) && text(draft.visionModel)) {
    payload.visionBaseUrl = text(draft.visionBaseUrl);
    payload.visionApiKey = text(draft.visionApiKey);
    payload.visionApiProtocol = text(draft.visionApiProtocol) || "openai";
  }
  return payload;
}

export function modelServiceOperation(actionId) {
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
