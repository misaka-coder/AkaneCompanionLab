import {
  CONTROL_CENTER_PAGE_IDS,
  CONTROL_CENTER_SCHEMA_VERSION,
  isKnownControlCenterPage
} from "./snapshot-schema.js";

const overviewActionIds = ["chat.new", "chat.stop", "workspace.open"];

export function createControlCenterSnapshot(raw = {}) {
  const shell = createShellSnapshot(raw);
  return {
    schemaVersion: CONTROL_CENTER_SCHEMA_VERSION,
    sourceKind: raw.sourceKind || "unknown",
    generatedAt: new Date().toISOString(),
    shell,
    pages: {
      overview: adaptOverviewPage(raw.overviewPage || raw.overview || {}),
      character: raw.characterPage || raw.character || {},
      voice: raw.voicePage || raw.voice || {},
      music: raw.musicPage || raw.music || {},
      perception: raw.perceptionPage || raw.perception || {},
      abilities: raw.abilitiesPage || raw.abilities || {},
      advanced: raw.advancedPage || raw.advanced || {}
    },
    dataDomains: raw.controlCenterDataDomains || {},
    featureFlags: deriveFeatureFlags(raw)
  };
}

function createShellSnapshot(raw) {
  const navItems = normalizeNavItems(raw.navItems);
  const defaultPage = isKnownControlCenterPage(raw.labMeta?.defaultPage) ? raw.labMeta.defaultPage : "overview";

  return {
    navItems,
    labMeta: {
      defaultPage,
      version: raw.labMeta?.version || "v0.0.0",
      status: raw.labMeta?.status || "Akane 离线",
      statusDetail: raw.labMeta?.statusDetail || "等待连接",
      footer: raw.labMeta?.footer || ""
    },
    backgroundAsset: raw.labMeta?.backgroundAsset || "skyCityBalcony"
  };
}

function normalizeNavItems(items) {
  const navItems = Array.isArray(items) ? items : [];
  const knownItems = navItems.filter((item) => isKnownControlCenterPage(item.id));
  if (knownItems.length > 0) {
    return knownItems.map((item) => ({ enabled: true, ...item }));
  }
  return CONTROL_CENTER_PAGE_IDS.map((id) => ({ id, label: id, icon: "sparkle", enabled: true }));
}

function adaptOverviewPage(page) {
  return {
    ...page,
    quickActions: withActionIds(page.quickActions, overviewActionIds)
  };
}

function withActionIds(items, actionIds) {
  if (!Array.isArray(items)) {
    return [];
  }
  return items.map((item, index) => ({
    ...item,
    commandId: item.commandId || item.id || actionIds[index] || `control-center.action.${index + 1}`
  }));
}

function deriveFeatureFlags(raw) {
  return {
    hasCharacterPackages: Boolean(raw.characterPage?.selectedPack || raw.character?.selectedPack),
    hasVoiceControls: Boolean(raw.voicePage?.tts || raw.voice?.tts),
    hasMusicControls: Boolean(raw.musicPage?.nowPlaying || raw.music?.nowPlaying),
    hasDesktopSensing: Boolean(raw.perceptionPage?.featureCards || raw.perception?.featureCards),
    hasAdvancedDiagnostics: Boolean(raw.advancedPage?.diagnostics || raw.advanced?.diagnostics)
  };
}
