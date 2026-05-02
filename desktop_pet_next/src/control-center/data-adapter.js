import {
  CONTROL_CENTER_PAGE_IDS,
  CONTROL_CENTER_SCHEMA_VERSION,
  isKnownControlCenterPage
} from "./snapshot-schema.js";

const overviewActionIds = ["chat.new", "chat.stop", "workspace.open"];

export function createControlCenterSnapshot(raw = {}) {
  const shell = createShellSnapshot(raw);
  const overviewRuntime = raw.overviewRuntime || raw.runtime?.overview || {};
  const characterRuntime = raw.characterRuntime || raw.runtime?.character || {};
  const voiceRuntime = raw.voiceRuntime || raw.runtime?.voice || {};
  const perceptionRuntime = raw.perceptionRuntime || raw.runtime?.perception || {};
  const musicRuntime = raw.musicRuntime || raw.runtime?.music || {};
  const abilitiesRuntime = raw.abilitiesRuntime || raw.runtime?.abilities || {};
  return {
    schemaVersion: CONTROL_CENTER_SCHEMA_VERSION,
    sourceKind: raw.sourceKind || "unknown",
    generatedAt: new Date().toISOString(),
    shell,
    pages: {
      overview: adaptOverviewPage(raw.overviewPage || raw.overview || {}, overviewRuntime),
      character: adaptCharacterPage(raw.characterPage || raw.character || {}, characterRuntime),
      voice: adaptVoicePage(raw.voicePage || raw.voice || {}, voiceRuntime),
      music: adaptMusicPage(raw.musicPage || raw.music || {}, musicRuntime),
      perception: adaptPerceptionPage(raw.perceptionPage || raw.perception || {}, perceptionRuntime),
      abilities: adaptAbilitiesPage(raw.abilitiesPage || raw.abilities || {}, abilitiesRuntime),
      advanced: raw.advancedPage || raw.advanced || {}
    },
    dataDomains: raw.controlCenterDataDomains || {},
    featureFlags: deriveFeatureFlags(raw)
  };
}

function adaptCharacterPage(page, runtime = {}) {
  const character = { ...page };
  if (runtime.hero) character.hero = runtime.hero;
  if (runtime.selectedPack) character.selectedPack = runtime.selectedPack;
  if (Array.isArray(runtime.packInfo) && runtime.packInfo.length) {
    character.packInfo = runtime.packInfo;
  }
  if (typeof runtime.completeness === "number") {
    character.completeness = Math.max(0, Math.min(100, runtime.completeness));
  }
  if (Array.isArray(runtime.outfits) && runtime.outfits.length) {
    character.outfits = runtime.outfits;
  }
  if (Array.isArray(runtime.emotions) && runtime.emotions.length) {
    character.emotions = runtime.emotions;
  }
  if (runtime.warning && typeof runtime.warning === "object") {
    character.warning = { ...(character.warning || {}), ...dropEmpty(runtime.warning) };
  }
  if (Array.isArray(runtime.resources) && runtime.resources.length) {
    character.resources = runtime.resources;
  }
  if (Array.isArray(runtime.tip) && runtime.tip.length) {
    character.tip = runtime.tip;
  }
  if (Array.isArray(runtime.actions) && runtime.actions.length) {
    character.actions = runtime.actions;
  }
  return character;
}

function adaptVoicePage(page, runtime = {}) {
  const voice = { ...page };
  if (runtime.tts && typeof runtime.tts === "object") {
    voice.tts = { ...voice.tts, ...dropEmpty(runtime.tts) };
  }
  if (runtime.asr && typeof runtime.asr === "object") {
    voice.asr = { ...voice.asr, ...dropEmpty(runtime.asr) };
  }
  if (Array.isArray(runtime.diagnostics) && runtime.diagnostics.length) {
    voice.diagnostics = runtime.diagnostics;
  }
  return voice;
}

function adaptPerceptionPage(page, runtime = {}) {
  const perception = { ...page };
  const cardPatchById = {};
  if (Array.isArray(runtime.featureCards)) {
    for (const card of runtime.featureCards) {
      const id = String(card?.id || "").trim();
      if (id) cardPatchById[id] = card;
    }
  }
  if (Array.isArray(perception.featureCards)) {
    perception.featureCards = perception.featureCards.map((card) => {
      const id = String(card?.id || "").trim();
      const patch = cardPatchById[id];
      if (!patch) return card;
      const merged = { ...card };
      if (typeof patch.enabled === "boolean") merged.enabled = patch.enabled;
      if (patch.appName !== undefined) merged.appName = String(patch.appName || "");
      if (patch.appDetail !== undefined) merged.appDetail = String(patch.appDetail || "");
      if (patch.version !== undefined) merged.version = String(patch.version || "");
      if (patch.code !== undefined) merged.code = Array.isArray(patch.code) ? patch.code : [];
      if (patch.source !== undefined) merged.source = String(patch.source || "");
      if (patch.frequency !== undefined) merged.frequency = String(patch.frequency || "");
      if (patch.frames !== undefined) merged.frames = String(patch.frames || "");
      if (patch.activeOption !== undefined) {
        merged.activeOption = String(patch.activeOption || "");
        if (
          merged.activeOption &&
          Array.isArray(merged.options) &&
          !merged.options.includes(merged.activeOption)
        ) {
          merged.options = [...merged.options, merged.activeOption];
        }
      }
      return merged;
    });
  }
  if (Array.isArray(runtime.diagnostics) && runtime.diagnostics.length) {
    perception.diagnostics = runtime.diagnostics;
  }
  return perception;
}

function adaptMusicPage(page, runtime = {}) {
  if (!runtime || Object.keys(runtime).length === 0) return page;
  const music = { ...page };
  if (runtime.nowPlaying && typeof runtime.nowPlaying === "object") {
    music.nowPlaying = { ...music.nowPlaying, ...runtime.nowPlaying };
  }
  if (Array.isArray(runtime.playlist)) {
    music.playlist = runtime.playlist.map((item) => ({
      cover: music.nowPlaying.cover,
      ...item
    }));
  }
  if (Array.isArray(runtime.lyrics)) music.lyrics = runtime.lyrics;
  if (typeof runtime.activeLyric === "number") music.activeLyric = runtime.activeLyric;
  if (Array.isArray(runtime.info)) music.info = runtime.info;
  if (runtime.bottomStatus !== undefined) music.bottomStatus = runtime.bottomStatus;
  return music;
}

function adaptAbilitiesPage(page, runtime = {}) {
  if (!runtime || Object.keys(runtime).length === 0) return page;
  const abilities = { ...page };
  if (runtime.overview && typeof runtime.overview === "object") {
    abilities.overview = { ...(abilities.overview || {}), ...dropEmpty(runtime.overview) };
    if (Array.isArray(runtime.overview.stats) && runtime.overview.stats.length) {
      abilities.overview.stats = runtime.overview.stats;
    }
    if (typeof runtime.overview.availability === "number") {
      abilities.overview.availability = Math.max(0, Math.min(100, runtime.overview.availability));
    }
  }
  if (Array.isArray(runtime.quickActions) && runtime.quickActions.length) {
    abilities.quickActions = runtime.quickActions;
  }
  if (Array.isArray(runtime.modules) && runtime.modules.length) {
    abilities.modules = runtime.modules;
  }
  if (Array.isArray(runtime.workflows) && runtime.workflows.length) {
    abilities.workflows = runtime.workflows;
  }
  if (Array.isArray(runtime.calls) && runtime.calls.length) {
    abilities.calls = runtime.calls;
  }
  if (runtime.safety && typeof runtime.safety === "object") {
    abilities.safety = { ...(abilities.safety || {}), ...dropEmpty(runtime.safety) };
    if (Array.isArray(runtime.safety.items)) {
      abilities.safety.items = runtime.safety.items;
    }
  }
  if (runtime.live2d && typeof runtime.live2d === "object") {
    abilities.live2d = { ...(abilities.live2d || {}), ...dropEmpty(runtime.live2d) };
    if (Array.isArray(runtime.live2d.items)) {
      abilities.live2d.items = runtime.live2d.items;
    }
  }
  return abilities;
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
