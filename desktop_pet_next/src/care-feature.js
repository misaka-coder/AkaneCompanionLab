export function createUnresolvedCareFeature() {
  return {
    enabled: false,
    status: "unknown",
    reason: "not_resolved",
    resetBaselineOnStart: false,
    contractSource: "unresolved"
  };
}

export function cloneCareState(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  try {
    return JSON.parse(JSON.stringify(value));
  } catch {
    return null;
  }
}

export function resetCareEvaluationBaseline(value, now = Date.now()) {
  const care = cloneCareState(value);
  if (!care) return null;
  const baseline = Math.max(1, Math.round(Number(now) || Date.now()));
  care.lastDecayAt = baseline;
  care.updatedAt = Math.max(Number(care.updatedAt || 0), baseline);
  return care;
}

export function resolveCareFeatureFromHealth(payload) {
  const data = payload && typeof payload === "object" ? payload : {};
  const features = data.features && typeof data.features === "object" ? data.features : {};
  const care = features.care && typeof features.care === "object" ? features.care : null;
  if (!care || typeof care.enabled !== "boolean") {
    return {
      enabled: true,
      status: "enabled",
      reason: "legacy_contract",
      resetBaselineOnStart: false,
      contractSource: "legacy"
    };
  }

  const enabled = care.enabled;
  return {
    enabled,
    status: enabled ? "enabled" : "disabled",
    reason: enabled ? "" : String(care.reason || "feature_disabled"),
    resetBaselineOnStart: Boolean(enabled && (care.reset_baseline_on_start ?? care.resetBaselineOnStart)),
    contractSource: "host"
  };
}

export function isCareFeatureEnabled(feature) {
  return Boolean(feature && feature.enabled === true && feature.status === "enabled");
}

export function attachDesktopCareContext(payload, careContext, feature) {
  const result = payload && typeof payload === "object" ? { ...payload } : {};
  delete result.desktop_care;
  if (isCareFeatureEnabled(feature) && careContext && typeof careContext === "object") {
    result.desktop_care = careContext;
  }
  return result;
}
