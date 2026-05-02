import {
  CONTROL_CENTER_PAGE_IDS,
  CONTROL_CENTER_SCHEMA_VERSION,
  isKnownControlCenterPage
} from "./snapshot-schema.js";

const overviewActionIds = ["chat.new", "chat.stop", "workspace.open"];

export function createControlCenterSnapshot(raw = {}) {
  const shell = createShellSnapshot(raw);
  const overviewRuntime = raw.overviewRuntime || raw.runtime?.overview || {};
  return {
    schemaVersion: CONTROL_CENTER_SCHEMA_VERSION,
    sourceKind: raw.sourceKind || "unknown",
    generatedAt: new Date().toISOString(),
    shell,
    pages: {
      overview: adaptOverviewPage(raw.overviewPage || raw.overview || {}, overviewRuntime),
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
  const runtimeShell = raw.overviewRuntime?.shell || raw.runtime?.overview?.shell || {};

  return {
    navItems,
    labMeta: {
      defaultPage,
      version: raw.labMeta?.version || "v0.0.0",
      status: runtimeShell.status || raw.labMeta?.status || "Akane 离线",
      statusDetail: runtimeShell.statusDetail || raw.labMeta?.statusDetail || "等待连接",
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

function adaptOverviewPage(page, runtime = {}) {
  const overview = {
    ...page,
    quickActions: withActionIds(page.quickActions, overviewActionIds)
  };
  if (runtime.statusBadge && overview.status) {
    overview.status = { ...overview.status, badge: runtime.statusBadge };
  }
  if (runtime.connectionBadge && overview.connection) {
    overview.connection = { ...overview.connection, badge: runtime.connectionBadge };
  }
  if (runtime.statusItems && overview.status) {
    overview.status = {
      ...overview.status,
      items: patchRowsByLabel(overview.status.items, runtime.statusItems)
    };
  }
  if (runtime.connectionRows && overview.connection) {
    overview.connection = {
      ...overview.connection,
      rows: patchRowsByLabel(overview.connection.rows, runtime.connectionRows)
    };
  }
  if (runtime.pack && overview.pack) {
    overview.pack = { ...overview.pack, ...dropEmpty(runtime.pack) };
  }
  if (runtime.emotion && overview.emotion) {
    overview.emotion = { ...overview.emotion, ...dropEmpty(runtime.emotion) };
  }
  if (runtime.voice && overview.voice) {
    const ttsEnabled = pickBoolean(runtime.voice.ttsEnabled, overview.voice.rows?.[0]?.enabled);
    const asrEnabled = pickBoolean(runtime.voice.asrEnabled, overview.voice.rows?.[1]?.enabled);
    overview.voice = {
      ...overview.voice,
      status: runtime.voice.status || overview.voice.status,
      rows: [
        { ...(overview.voice.rows?.[0] || { label: "回复朗读（TTS）" }), enabled: ttsEnabled },
        { ...(overview.voice.rows?.[1] || { label: "语音输入（ASR）" }), enabled: asrEnabled }
      ]
    };
  }
  if (Array.isArray(runtime.abilities) && runtime.abilities.length) {
    overview.abilities = runtime.abilities;
  }
  if (runtime.health && overview.health) {
    overview.health = patchRowsByLabel(overview.health, runtime.health);
  }
  return overview;
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

function patchRowsByLabel(rows, valuesByLabel) {
  if (!Array.isArray(rows)) return [];
  const values = valuesByLabel && typeof valuesByLabel === "object" ? valuesByLabel : {};
  return rows.map((row) => {
    const label = String(row?.label || "").trim();
    if (!Object.prototype.hasOwnProperty.call(values, label)) {
      return row;
    }
    const nextValue = values[label];
    if (nextValue && typeof nextValue === "object" && !Array.isArray(nextValue)) {
      return { ...row, ...dropEmpty(nextValue) };
    }
    return { ...row, value: String(nextValue ?? row.value ?? "") };
  });
}

function dropEmpty(value) {
  const result = {};
  for (const [key, item] of Object.entries(value || {})) {
    if (item !== undefined && item !== null && item !== "") {
      result[key] = item;
    }
  }
  return result;
}

function pickBoolean(value, fallback) {
  return typeof value === "boolean" ? value : Boolean(fallback);
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
