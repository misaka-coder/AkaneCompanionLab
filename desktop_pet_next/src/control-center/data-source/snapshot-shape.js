

export const CONTROL_CENTER_SOURCE_KIND = Object.freeze({
  backend: "backend"
});

export function createControlCenterRuntimeSnapshot(rawSnapshot = {}) {
  const raw = rawSnapshot && typeof rawSnapshot === "object" && !Array.isArray(rawSnapshot) ? rawSnapshot : {};
  return {
    sourceKind: raw.sourceKind || "unknown",
    backendUrl: raw.backendUrl || null,
    fallbackReason: raw.fallbackReason || null,
    generatedAt: raw.generatedAt || new Date().toISOString(),
    controlCenterRuntime: raw.controlCenterRuntime || {},
    overviewRuntime: raw.overviewRuntime || {},
    characterRuntime: raw.characterRuntime || {},
    voiceRuntime: raw.voiceRuntime || {},
    perceptionRuntime: raw.perceptionRuntime || {},
    musicRuntime: raw.musicRuntime || {},
    abilitiesRuntime: raw.abilitiesRuntime || {},
    pluginRuntime: raw.pluginRuntime || {},
    pluginManagementRuntime: raw.pluginManagementRuntime || {},
    pluginMarketRuntime: raw.pluginMarketRuntime || {},
    advancedRuntime: raw.advancedRuntime || {}
  };
}
