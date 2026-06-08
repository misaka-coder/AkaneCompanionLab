export function secondsFromIntervalLabel(label) {
  const text = String(label || "").trim();
  const match = /^(\d+)\s*(秒|分钟)$/.exec(text);
  if (!match) return 0;
  const value = Number.parseInt(match[1], 10);
  if (!Number.isFinite(value) || value <= 0) return 0;
  return match[2] === "分钟" ? value * 60 : value;
}

export function createControlCenterActionPayloadFromDataset(dataset = {}, page = "") {
  const payload = { page };

  if (dataset.payloadField) {
    payload.field = dataset.payloadField;
    payload.value = dataset.payloadValue !== undefined ? coerceDatasetPayloadValue(dataset.payloadValue) : null;
  }
  if (dataset.payloadValue !== undefined && !Object.prototype.hasOwnProperty.call(payload, "value")) {
    payload.value = coerceDatasetPayloadValue(dataset.payloadValue);
  }
  if (dataset.payloadText !== undefined) {
    payload.text = dataset.payloadText;
  }
  if (dataset.payloadSource !== undefined) {
    payload.source = dataset.payloadSource;
  }
  if (dataset.payloadPackId !== undefined) {
    payload.packId = dataset.payloadPackId;
  }
  if (dataset.payloadOutfitId !== undefined) {
    payload.outfitId = dataset.payloadOutfitId;
  }
  if (dataset.payloadIndex !== undefined) {
    payload.index = Number(dataset.payloadIndex);
  }
  if (dataset.payloadFeatureId !== undefined) {
    payload.featureId = dataset.payloadFeatureId;
  }
  if (dataset.payloadEmotionId !== undefined) {
    payload.emotionId = dataset.payloadEmotionId;
  }
  if (dataset.payloadTrackId !== undefined) {
    payload.trackId = dataset.payloadTrackId;
  }
  if (dataset.payloadOptionId !== undefined) {
    payload.optionId = dataset.payloadOptionId;
  }
  if (dataset.payloadProviderId !== undefined) {
    payload.providerId = String(dataset.payloadProviderId).trim();
  }
  if (dataset.payloadWorkflowId !== undefined) {
    payload.workflowId = String(dataset.payloadWorkflowId).trim();
  }
  if (dataset.payloadServerId !== undefined) {
    payload.serverId = String(dataset.payloadServerId).trim();
  }
  if (dataset.payloadEndpoint !== undefined) {
    payload.endpoint = String(dataset.payloadEndpoint).trim();
  }
  if (dataset.payloadRequiresConfirmation !== undefined) {
    payload.requiresConfirmation = coerceDatasetPayloadValue(dataset.payloadRequiresConfirmation);
  }
  if (dataset.payloadItemType !== undefined) {
    payload.itemType = String(dataset.payloadItemType).trim();
  }
  if (dataset.payloadHandle !== undefined) {
    payload.handle = String(dataset.payloadHandle).trim();
  }
  if (dataset.payloadTitle !== undefined) {
    payload.title = String(dataset.payloadTitle).trim();
  }
  if (dataset.trackId !== undefined) {
    payload.trackId = dataset.trackId;
  }
  if (dataset.trackIndex !== undefined) {
    payload.index = Number(dataset.trackIndex);
  }

  applyNamedPayloadField(payload);
  return payload;
}

export function coerceDatasetPayloadValue(value) {
  if (value === "true") return true;
  if (value === "false") return false;
  return value;
}

function applyNamedPayloadField(payload) {
  const field = typeof payload.field === "string" ? payload.field.trim() : "";
  if (!/^[A-Za-z_$][A-Za-z0-9_$]*$/.test(field)) return;
  if (Object.prototype.hasOwnProperty.call(payload, field)) return;
  payload[field] = payload.value;
}
