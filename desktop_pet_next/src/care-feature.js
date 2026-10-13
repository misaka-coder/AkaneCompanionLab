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

export const CARE_PASSIVE_TICK_MS = 60 * 1000;
export const CARE_DEFAULT_HUNGER_DECAY_PER_HOUR = 4;
export const CARE_DEFAULT_ENERGY_COST_PER_REPLY = 1;
export const CARE_DEFAULT_ENERGY_COST_PER_PROACTIVE = 0;

export function clampCareValue(value, min, max) {
  const number = Math.round(Number(value));
  return Math.min(max, Math.max(min, Number.isFinite(number) ? number : min));
}

export function normalizeCareDecayConfig(value) {
  const source = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  return {
    hungerPerHour: clampCareValue(
      source.hungerPerHour ?? source.hunger_per_hour ?? CARE_DEFAULT_HUNGER_DECAY_PER_HOUR,
      0,
      100
    ),
    energyPerReply: clampCareValue(
      source.energyPerReply ?? source.energy_per_reply ?? CARE_DEFAULT_ENERGY_COST_PER_REPLY,
      0,
      20
    ),
    energyPerProactive: clampCareValue(
      source.energyPerProactive ?? source.energy_per_proactive ?? CARE_DEFAULT_ENERGY_COST_PER_PROACTIVE,
      0,
      20
    )
  };
}

export function normalizeCareFeedback(value) {
  const source = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const bubble = source.bubble && typeof source.bubble === "object" && !Array.isArray(source.bubble) ? source.bubble : {};
  return {
    emotion: String(source.emotion || "").trim(),
    bubble: {
      text: String(bubble.text || "").trim(),
      durationMs: clampCareValue(bubble.durationMs ?? bubble.duration_ms ?? 0, 0, 60000)
    }
  };
}

export function normalizeCareWorkConfig(value) {
  const source = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const minReward = clampCareValue(source.rewardCoinsMin ?? source.reward_coins_min ?? 5, 0, 999999);
  const maxReward = clampCareValue(source.rewardCoinsMax ?? source.reward_coins_max ?? minReward, 0, 999999);
  return {
    enabled: Boolean(source.enabled),
    durationSeconds: clampCareValue(source.durationSeconds ?? source.duration_seconds ?? 20, 1, 3600),
    rewardCoinsMin: Math.min(minReward, maxReward),
    rewardCoinsMax: Math.max(minReward, maxReward),
    minHunger: clampCareValue(source.minHunger ?? source.min_hunger ?? 20, 0, 100),
    minEnergy: clampCareValue(source.minEnergy ?? source.min_energy ?? 25, 0, 100),
    hungerCost: clampCareValue(source.hungerCost ?? source.hunger_cost ?? 12, 0, 100),
    energyCost: clampCareValue(source.energyCost ?? source.energy_cost ?? 25, 0, 100),
    startFeedback: normalizeCareFeedback(source.startFeedback || source.start_feedback),
    completeFeedback: normalizeCareFeedback(source.completeFeedback || source.complete_feedback)
  };
}

export function normalizeCareAllowanceConfig(value) {
  const source = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  return {
    enabled: Boolean(source.enabled),
    coins: clampCareValue(source.coins ?? 4, 1, 999999),
    cooldownSeconds: clampCareValue(source.cooldownSeconds ?? source.cooldown_seconds ?? 300, 0, 86400),
    maxCoins: clampCareValue(source.maxCoins ?? source.max_coins ?? 6, 1, 999999),
    feedback: normalizeCareFeedback(source.feedback)
  };
}

export function normalizeCareWorkTask(value) {
  const source = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const completeAt = Math.max(0, Math.round(Number(source.completeAt || source.complete_at || 0)));
  if (!completeAt) return null;
  return {
    status: "active",
    startedAt: Math.max(0, Math.round(Number(source.startedAt || source.started_at || 0))),
    completeAt,
    rewardCoins: clampCareValue(source.rewardCoins ?? source.reward_coins ?? 0, 0, 999999)
  };
}

export function normalizeCareState(value, config = {}) {
  const source = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const inventory = source.inventory && typeof source.inventory === "object" && !Array.isArray(source.inventory)
    ? source.inventory
    : {};
  const normalizedInventory = {};
  for (const [id, count] of Object.entries(inventory)) {
    const itemId = String(id || "").trim();
    const amount = Math.max(0, Math.round(Number(count) || 0));
    if (itemId && amount > 0) normalizedInventory[itemId] = amount;
  }
  return {
    enabled: Boolean(config.enabled),
    coins: clampCareValue(source.coins ?? config.initialCoins, 0, 999999),
    hunger: clampCareValue(source.hunger ?? config.initialHunger, 0, 100),
    energy: clampCareValue(source.energy ?? config.initialEnergy, 0, 100),
    affection: clampCareValue(source.affection ?? config.initialAffection, 0, 100),
    inventory: normalizedInventory,
    workTask: normalizeCareWorkTask(source.workTask || source.work_task),
    lastAllowanceAt: Math.max(0, Math.round(Number(source.lastAllowanceAt || source.last_allowance_at || 0))),
    lastDecayAt: Math.max(0, Math.round(Number(source.lastDecayAt || source.last_decay_at || 0))),
    updatedAt: Math.max(0, Math.round(Number(source.updatedAt || source.updated_at || 0)))
  };
}

export function formatSignedCareDelta(value) {
  const number = Math.round(Number(value) || 0);
  return number > 0 ? `+${number}` : String(number);
}

export function formatCareFeedbackText(text, replacements = {}) {
  return String(text || "")
    .replace(/\{reward\}/g, String(replacements.reward ?? ""))
    .replace(/\{coins\}/g, String(replacements.coins ?? ""));
}
