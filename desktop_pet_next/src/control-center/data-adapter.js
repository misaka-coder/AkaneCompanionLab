import {
  CONTROL_CENTER_PAGE_IDS,
  CONTROL_CENTER_SCHEMA_VERSION,
  isKnownControlCenterPage
} from "./snapshot-schema.js";

const overviewActionIds = ["chat.new", "chat.stop", "workspace.open"];
const overviewMusicControlActionIds = [
  "music.previous",
  "music.next",
  "music.pause",
  "music.stop",
  "music.clear"
];
const overviewVoiceRowDefaults = [
  { id: "ttsEnabled", actionId: "voice.setTtsEnabled", label: "回复朗读（TTS）" },
  { id: "asrEnabled", actionId: "voice.setAsrEnabled", label: "语音输入（ASR）" }
];
const overviewSenseToggleDefaults = [
  { id: "activeWindow", actionId: "perception.desktopContext.setEnabled", label: "前台窗口感知", icon: "clipboard" },
  { id: "clipboard", actionId: "perception.clipboardContext.setEnabled", label: "剪贴板文本", icon: "clipboard" },
  { id: "screen", actionId: "perception.screenVision.setEnabled", label: "看屏幕", icon: "clipboard" },
  { id: "proactive", actionId: "perception.proactiveWake.setEnabled", label: "主动搭话", icon: "clipboard" }
];
const characterWarningActionId = "character.refresh";
const advancedCoreSettingDefaults = [
  { id: "webgl", actionId: "advanced.toggleWebgl" },
  { id: "hitTest", actionId: "advanced.setHitTestEnabled" },
  { id: "hitbox", actionId: "advanced.setHitboxOverlay" }
];
const advancedOperationActionIds = ["advanced.probeClickThrough", "advanced.resetWindow"];

export function createControlCenterSnapshot(raw = {}) {
  const shell = createShellSnapshot(raw);
  const overviewRuntime = raw.overviewRuntime || raw.runtime?.overview || {};
  const characterRuntime = raw.characterRuntime || raw.runtime?.character || {};
  const voiceRuntime = raw.voiceRuntime || raw.runtime?.voice || {};
  const perceptionRuntime = raw.perceptionRuntime || raw.runtime?.perception || {};
  const musicRuntime = raw.musicRuntime || raw.runtime?.music || {};
  const abilitiesRuntime = raw.abilitiesRuntime || raw.runtime?.abilities || {};
  const advancedRuntime = raw.advancedRuntime || raw.runtime?.advanced || {};
  return {
    schemaVersion: CONTROL_CENTER_SCHEMA_VERSION,
    sourceKind: raw.sourceKind || "unknown",
    backendUrl: raw.backendUrl || null,
    fallbackReason: raw.fallbackReason || null,
    generatedAt: new Date().toISOString(),
    shell,
    pages: {
      overview: adaptOverviewPage(raw.overviewPage || raw.overview || {}, overviewRuntime),
      model: adaptModelPage(raw.modelPage || raw.model || {}),
      character: adaptCharacterPage(raw.characterPage || raw.character || {}, characterRuntime),
      voice: adaptVoicePage(raw.voicePage || raw.voice || {}, voiceRuntime),
      music: adaptMusicPage(raw.musicPage || raw.music || {}, musicRuntime),
      perception: adaptPerceptionPage(raw.perceptionPage || raw.perception || {}, perceptionRuntime),
      abilities: adaptAbilitiesPage(raw.abilitiesPage || raw.abilities || {}, abilitiesRuntime),
      advanced: adaptAdvancedPage(raw.advancedPage || raw.advanced || {}, advancedRuntime)
    },
    dataDomains: raw.controlCenterDataDomains || {},
    featureFlags: deriveFeatureFlags(raw)
  };
}

function adaptModelPage(page) {
  const model = page && typeof page === "object" ? { ...page } : {};
  model.providers = Array.isArray(model.providers) ? model.providers : [];
  model.providerId = String(model.providerId || model.providers[0]?.id || "openai_compatible");
  model.protocol = String(model.protocol || "openai");
  model.baseUrl = String(model.baseUrl || "");
  model.chatModel = String(model.chatModel || "");
  model.visionModel = String(model.visionModel || "");
  model.hasApiKey = Boolean(model.hasApiKey);
  model.useForVision = model.useForVision !== false;
  model.timeoutSeconds = Number(model.timeoutSeconds || 120);
  return model;
}

function adaptCharacterPage(page, runtime = {}) {
  const character = { ...page };
  if (runtime.hero) character.hero = runtime.hero;
  if (runtime.selectedPack) character.selectedPack = runtime.selectedPack;
  if (runtime.selectedPackId) character.selectedPackId = String(runtime.selectedPackId);
  if (!character.selectedPackId) {
    character.selectedPackId = character.selectedPack || "";
  }
  if (Array.isArray(runtime.availablePacks)) {
    character.availablePacks = normalizeCharacterAvailablePacks(runtime.availablePacks, character.selectedPackId);
  }
  if (Array.isArray(runtime.packInfo) && runtime.packInfo.length) {
    character.packInfo = runtime.packInfo;
  }
  if (typeof runtime.completeness === "number") {
    character.completeness = Math.max(0, Math.min(100, runtime.completeness));
  }
  if (Array.isArray(runtime.outfits) && runtime.outfits.length) {
    character.outfits = runtime.outfits.map((item, index) => ({
      ...item,
      id: item.id || item.name || `outfit_${index + 1}`
    }));
  }
  if (Array.isArray(runtime.emotions) && runtime.emotions.length) {
    character.emotions = runtime.emotions.map((item, index) => ({
      ...item,
      id: item.id || item.name || `emotion_${index + 1}`
    }));
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
  if (Object.prototype.hasOwnProperty.call(runtime, "voice")) {
    character.voice = normalizeCharacterVoice(runtime.voice);
  }
  if (character.warning && typeof character.warning === "object") {
    character.warning = {
      ...character.warning,
      actionId: character.warning.actionId || characterWarningActionId
    };
  }
  return character;
}

function normalizeCharacterVoice(value) {
  const source = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  return {
    provider: String(source.provider || "").trim(),
    profileId: String(source.profileId || source.profile_id || "").trim(),
    notes: String(source.notes || "").trim()
  };
}

function normalizeCharacterAvailablePacks(packs, selectedPackId) {
  const selected = String(selectedPackId || "").trim();
  const seen = new Set();
  return (Array.isArray(packs) ? packs : [])
    .map((pack) => {
      if (!pack || typeof pack !== "object") return null;
      const profile = pack.profile && typeof pack.profile === "object" ? pack.profile : {};
      const identity = profile.identity && typeof profile.identity === "object" ? profile.identity : {};
      const appearance = profile.appearance && typeof profile.appearance === "object" ? profile.appearance : {};
      const assets = profile.assets && typeof profile.assets === "object" ? profile.assets : {};
      const id = String(pack.id || pack.packId || pack.pack_id || "").trim();
      if (!id || seen.has(id)) return null;
      seen.add(id);
      const characterId = String(pack.characterId || pack.character_id || identity.id || "").trim();
      const name = String(pack.name || identity.name || characterId || id).trim();
      const appName = String(pack.appName || pack.app_name || identity.appName || identity.app_name || name).trim();
      const assetCount = Number(pack.assetCount || pack.asset_count || 0);
      return {
        id,
        characterId,
        name,
        appName: appName || name || id,
        schemaVersion: String(pack.schemaVersion || pack.schema_version || profile.schemaVersion || profile.schema_version || "").trim(),
        defaultOutfit: String(pack.defaultOutfit || pack.default_outfit || appearance.defaultOutfit || appearance.default_outfit || "").trim(),
        defaultEmotion: String(pack.defaultEmotion || pack.default_emotion || appearance.defaultEmotion || appearance.default_emotion || "").trim(),
        assetCount: Number.isFinite(assetCount) && assetCount > 0 ? assetCount : 0,
        assetSource: String(pack.assetSource || pack.asset_source || assets.runtimeSource || assets.runtime_source || "").trim(),
        selected: selected ? id === selected : Boolean(pack.selected)
      };
    })
    .filter(Boolean);
}

function adaptVoicePage(page, runtime = {}) {
  const voice = { ...page };
  if (runtime.tts && typeof runtime.tts === "object") {
    voice.tts = { ...voice.tts, ...dropEmpty(runtime.tts) };
  }
  if (runtime.asr && typeof runtime.asr === "object") {
    voice.asr = { ...voice.asr, ...dropEmpty(runtime.asr) };
  }
  if (runtime.preview && typeof runtime.preview === "object") {
    voice.preview = { ...voice.preview, ...dropEmpty(runtime.preview) };
  }
  if (runtime.wakeWord !== undefined) {
    voice.wakeWord = String(runtime.wakeWord || "");
  }
  if (runtime.wakeSensitivity !== undefined) {
    voice.wakeSensitivity = String(runtime.wakeSensitivity || "");
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
    music.playlist = runtime.playlist.map((item, index) => ({
      cover: music.nowPlaying.cover,
      ...item,
      id: item.id || item.title || `queue_${index + 1}`
    }));
  }
  if (Array.isArray(runtime.lyrics)) music.lyrics = runtime.lyrics;
  if (typeof runtime.activeLyric === "number") music.activeLyric = runtime.activeLyric;
  if (runtime.currentPlayMode !== undefined || runtime.playMode !== undefined) {
    music.currentPlayMode = String(runtime.currentPlayMode ?? runtime.playMode ?? "");
  }
  if (runtime.outputDevice !== undefined) {
    music.outputDevice = String(runtime.outputDevice || "");
  }
  if (typeof runtime.volumeNormalization === "boolean") {
    music.volumeNormalization = runtime.volumeNormalization;
  }
  if (Array.isArray(runtime.info)) music.info = runtime.info;
  if (runtime.bottomStatus !== undefined) music.bottomStatus = runtime.bottomStatus;
  if (runtime.systemMedia && typeof runtime.systemMedia === "object") {
    music.systemMedia = runtime.systemMedia;
  }
  if ("recommendations" in runtime && Array.isArray(runtime.recommendations)) {
    music.recommendations = runtime.recommendations;
  }
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
  if (Array.isArray(runtime.productization) && runtime.productization.length) {
    abilities.productization = runtime.productization;
  }
  if (Array.isArray(runtime.modules) && runtime.modules.length) {
    abilities.modules = runtime.modules;
  }
  if (Array.isArray(runtime.providers)) {
    abilities.providers = runtime.providers;
  }
  if (Array.isArray(runtime.mcpServers)) {
    abilities.mcpServers = runtime.mcpServers;
  }
  if (runtime.qqStatus && typeof runtime.qqStatus === "object") {
    abilities.qqStatus = runtime.qqStatus;
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

function adaptAdvancedPage(page, runtime = {}) {
  const runtimeData = runtime && typeof runtime === "object" ? runtime : {};
  const advanced = { ...page };

  // systemStrip: patch items by label
  if (runtimeData.systemStrip && typeof runtimeData.systemStrip === "object") {
    advanced.systemStrip = patchRowsByLabel(advanced.systemStrip, runtimeData.systemStrip);
  }

  // diagnostics: merge sub-fields
  if (runtimeData.diagnostics && typeof runtimeData.diagnostics === "object") {
    advanced.diagnostics = { ...advanced.diagnostics };
    if (runtimeData.diagnostics.metrics && typeof runtimeData.diagnostics.metrics === "object") {
      advanced.diagnostics.metrics = patchRowsByLabel(advanced.diagnostics.metrics, runtimeData.diagnostics.metrics);
    }
    if (Array.isArray(runtimeData.diagnostics.logs) && runtimeData.diagnostics.logs.length) {
      advanced.diagnostics.logs = runtimeData.diagnostics.logs;
    }
  }

  // live2d: merge rows
  if (runtimeData.live2d && typeof runtimeData.live2d === "object") {
    advanced.live2d = { ...advanced.live2d, ...dropEmpty(runtimeData.live2d) };
    if (Array.isArray(runtimeData.live2d.rows)) {
      advanced.live2d.rows = runtimeData.live2d.rows;
    }
  }

  // abilityOverview: replace entirely when runtime provides it, ensure stable id
  if (Array.isArray(runtimeData.abilityOverview)) {
    advanced.abilityOverview = runtimeData.abilityOverview.map((item, index) => ({
      ...item,
      id: item.id || item.label || `ability_${index + 1}`
    }));
  }

  if (Array.isArray(runtimeData.coreSettings)) {
    advanced.coreSettings = mergeAdvancedCoreSettings(advanced.coreSettings, runtimeData.coreSettings);
  }
  if (Array.isArray(runtimeData.operations)) {
    advanced.operations = runtimeData.operations.map((item, index) => ({
      ...item,
      id: item.id || item.title || `operation_${index + 1}`
    }));
  }

  // expertOptions: ensure stable id
  if (Array.isArray(runtimeData.expertOptions)) {
    advanced.expertOptions = runtimeData.expertOptions.map((item, index) => ({
      ...item,
      id: item.id || item.title || `expert_${index + 1}`
    }));
  }

  return withAdvancedOperationActionIds(withAdvancedCoreSettingActionIds(advanced));
}

function mergeAdvancedCoreSettings(baseItems, runtimeItems) {
  const base = withAdvancedCoreSettingActionIds({ coreSettings: Array.isArray(baseItems) ? baseItems : [] }).coreSettings;
  const runtimeById = new Map();
  for (const [index, item] of runtimeItems.entries()) {
    if (!item || typeof item !== "object") continue;
    const fallback = advancedCoreSettingDefaults[index];
    const id = String(item.id || fallback?.id || "").trim();
    if (id) runtimeById.set(id, item);
  }
  return base.map((item) => {
    const patch = runtimeById.get(item.id);
    if (!patch) return item;
    return {
      ...item,
      ...dropEmpty(patch),
      enabled: typeof patch.enabled === "boolean" ? patch.enabled : item.enabled
    };
  });
}

function withAdvancedCoreSettingActionIds(advanced) {
  if (Array.isArray(advanced.coreSettings)) {
    return {
      ...advanced,
      coreSettings: advanced.coreSettings.map((item, index) => {
        if (!item || typeof item !== "object") return item;
        const fallback = advancedCoreSettingDefaults[index] || {};
        return {
          ...item,
          id: item.id || fallback.id || "",
          ...(item.actionId || fallback.actionId ? { actionId: item.actionId || fallback.actionId } : {})
        };
      })
    };
  }
  return advanced;
}

function withAdvancedOperationActionIds(advanced) {
  if (Array.isArray(advanced.operations)) {
    return {
      ...advanced,
      operations: advanced.operations.map((item, index) => {
        if (!item || typeof item !== "object" || item.actionId) return item;
        const actionId = advancedOperationActionIds[index];
        return actionId ? { ...item, actionId } : item;
      })
    };
  }
  return advanced;
}

function createShellSnapshot(raw) {
  const navItems = normalizeNavItems(raw.navItems);
  // The read-only Settings Catalog page is always available (its data is fetched
  // directly from the backend by the renderer, not carried in the mock snapshot).
  if (!navItems.some((item) => item.id === "settings")) {
    navItems.push({ id: "settings", label: "设置", icon: "sparkle", enabled: true });
  }
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
  if (overview.music && Array.isArray(overview.music.controls)) {
    overview.music = {
      ...overview.music,
      controls: normalizeMusicControls(overview.music.controls)
    };
  }
  if (overview.voice) {
    overview.voice = {
      ...overview.voice,
      rows: normalizeOverviewVoiceRows(overview.voice.rows)
    };
  }
  if (overview.sense) {
    overview.sense = {
      ...overview.sense,
      toggles: normalizeOverviewSenseToggles(overview.sense.toggles)
    };
  }
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
    const enabledById = {
      ttsEnabled: runtime.voice.ttsEnabled,
      asrEnabled: runtime.voice.asrEnabled
    };
    overview.voice = {
      ...overview.voice,
      status: runtime.voice.status || overview.voice.status,
      rows: normalizeOverviewVoiceRows(overview.voice.rows).map((row) => ({
        ...row,
        enabled: pickBoolean(enabledById[row.id], row.enabled)
      }))
    };
  }
  if (runtime.sense && overview.sense) {
    const enabledById = {
      activeWindow: runtime.sense.activeWindowEnabled,
      clipboard: runtime.sense.clipboardEnabled,
      screen: runtime.sense.screenVisionEnabled ?? runtime.sense.screenEnabled,
      proactive: runtime.sense.proactiveWakeEnabled ?? runtime.sense.proactiveEnabled
    };
    overview.sense = {
      ...overview.sense,
      note: runtime.sense.note || overview.sense.note,
      toggles: normalizeOverviewSenseToggles(overview.sense.toggles).map((item) => ({
        ...item,
        enabled: pickBoolean(enabledById[item.id], item.enabled)
      }))
    };
  }
  if (Array.isArray(runtime.abilities) && runtime.abilities.length) {
    overview.abilities = runtime.abilities;
  }
  if (runtime.health && overview.health) {
    overview.health = patchRowsByLabel(overview.health, runtime.health);
  }
  if (Array.isArray(runtime.recentOutputs)) {
    overview.recentOutputs = runtime.recentOutputs;
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

function normalizeMusicControls(controls) {
  if (!Array.isArray(controls)) return [];
  return controls.map((item, index) => {
    const fallbackId = overviewMusicControlActionIds[index];
    if (typeof item === "string") {
      return {
        label: item,
        ...(fallbackId ? { actionId: fallbackId } : {})
      };
    }
    if (item && typeof item === "object") {
      const label = item.label || item.value || item.title || item.name || "";
      return {
        ...item,
        label,
        ...(item.actionId || fallbackId ? { actionId: item.actionId || fallbackId } : {})
      };
    }
    return {
      label: "",
      ...(fallbackId ? { actionId: fallbackId } : {})
    };
  });
}

function normalizeOverviewVoiceRows(rows) {
  const sourceRows = Array.isArray(rows) ? rows : [];
  const rowCount = sourceRows.length > 0 ? sourceRows.length : overviewVoiceRowDefaults.length;
  return Array.from({ length: rowCount }, (_, index) => {
    const fallback = overviewVoiceRowDefaults[index] || {};
    const item = sourceRows[index];
    if (typeof item === "string") {
      return {
        label: item || fallback.label || "",
        enabled: false,
        ...(fallback.id ? { id: fallback.id } : {}),
        ...(fallback.actionId ? { actionId: fallback.actionId } : {})
      };
    }
    if (item && typeof item === "object") {
      const label = item.label || item.value || item.title || item.name || fallback.label || "";
      return {
        ...item,
        label,
        enabled: Boolean(item.enabled),
        ...(item.id || fallback.id ? { id: item.id || fallback.id } : {}),
        ...(item.actionId || fallback.actionId ? { actionId: item.actionId || fallback.actionId } : {})
      };
    }
    return {
      label: fallback.label || "",
      enabled: false,
      ...(fallback.id ? { id: fallback.id } : {}),
      ...(fallback.actionId ? { actionId: fallback.actionId } : {})
    };
  });
}

function normalizeOverviewSenseToggles(toggles) {
  const sourceItems = Array.isArray(toggles) ? toggles : [];
  const itemCount = sourceItems.length > 0 ? sourceItems.length : overviewSenseToggleDefaults.length;
  return Array.from({ length: itemCount }, (_, index) => {
    const fallback = overviewSenseToggleDefaults[index] || {};
    const item = sourceItems[index];
    if (typeof item === "string") {
      return {
        label: item || fallback.label || "",
        enabled: true,
        icon: fallback.icon || "clipboard",
        ...(fallback.id ? { id: fallback.id } : {}),
        ...(fallback.actionId ? { actionId: fallback.actionId } : {})
      };
    }
    if (item && typeof item === "object") {
      const label = item.label || item.value || item.title || item.name || fallback.label || "";
      return {
        ...item,
        label,
        enabled: typeof item.enabled === "boolean" ? item.enabled : true,
        icon: item.icon || fallback.icon || "clipboard",
        ...(item.id || fallback.id ? { id: item.id || fallback.id } : {}),
        ...(item.actionId || fallback.actionId ? { actionId: item.actionId || fallback.actionId } : {})
      };
    }
    return {
      label: fallback.label || "",
      enabled: true,
      icon: fallback.icon || "clipboard",
      ...(fallback.id ? { id: fallback.id } : {}),
      ...(fallback.actionId ? { actionId: fallback.actionId } : {})
    };
  });
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
