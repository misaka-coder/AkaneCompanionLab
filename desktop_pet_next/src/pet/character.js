import { cloneCareState } from "../care-feature.js";
import { getActiveCharacterPackId, selectCharacterPack, APP_DISPLAY_NAME, CHARACTER_NAME, getActiveCharacterText, setRuntimeCharacterPacks, listCharacterPacks, getActiveCharacterProfile, DEFAULT_OUTFIT } from "../character-profile.js";
import { bindInstanceStorage } from "../instance-storage.js";
import { CLIENT_MODE, PROFILE_USER_ID, SCALE_MIN, SCALE_MAX, CHARACTER_PACK_ACTIVATED_EVENT } from './constants.js';
import { getProfileDefaultEmotion, normalizeCharacterPackId, getProfileDefaultOutfit, getProfileIdentityText, normalizeEntryKey, getProfileEmotionAliases, getProfileRequiredEmotions, getProfileRecommendedEmotions } from './profile.js';
import { generateSessionId, clamp, normalizeOutfitName, formatError, shortId, friendlyErrorMessage } from './utils.js';

/** Owns character behavior and its temporary runtime state. */
export function createCharacterController({
  state,
  resourceState,
  els,
  visualRenderer,
  bundledCharacterAssets,
  platform: platformPort,
  lifecycle,
  scope: runtimeScope,
  persistence: statePort,
  care: carePort,
  perception: perceptionPort,
  window: windowPort,
  status: statusPort,
  settings: settingsPort,
  backend: backendPort,
  panel: panelPort,
  interaction: interactionPort,
  bubble: bubblePort,
  tts: ttsPort,
  chat: chatPort,
  voice: voicePort,
  music: musicPort
}) {
  const { document } = platformPort;

  const bundledOutfit = buildBundledOutfit();

  let runtimeCharacterPacks = [];
  let characterSelectionRevision = 0;
  let resourceRefreshRevision = 0;

  let portraitAssetVersion = Date.now();

  let runtimeCharacterPackOutfits = [];

  let localOutfits = buildLocalOutfits();

  function buildCharacterRuntimeSnapshot() {
    const entries = Object.entries(ensureCharacterRuntimeMap()).map(([key, runtime]) => [
      key,
      runtime && typeof runtime === "object"
        ? { ...runtime, care: cloneCareState(runtime.care) }
        : runtime
    ]);
    return Object.fromEntries(entries);
  }

  function buildBackendCharacterContext() {
    return {
      client_mode: CLIENT_MODE,
      user_id: state.sessionId || "desktop_pet_next",
      session_id: state.sessionId || "desktop_pet_next",
      real_user_id: statePort.getProfileUserId(),
      character_pack_id: getCurrentCharacterPackId(),
      emotion: state.currentEmotion || getProfileDefaultEmotion()
    };
  }

  function getCharacterRuntimeKey(packId = getCurrentCharacterPackId()) {
    const profile = String(statePort.getProfileUserId() || PROFILE_USER_ID).trim() || PROFILE_USER_ID;
    const character = normalizeCharacterPackId(packId) || getActiveCharacterPackId();
    return `${profile}::${character}`;
  }

  function ensureCharacterRuntimeMap() {
    if (!state.characters || typeof state.characters !== "object" || Array.isArray(state.characters)) {
      state.characters = {};
    }
    return state.characters;
  }

  function persistCurrentCharacterRuntimeState(packId = state.characterPackId || getActiveCharacterPackId()) {
    const normalizedPackId = normalizeCharacterPackId(packId) || getActiveCharacterPackId();
    const map = ensureCharacterRuntimeMap();
    map[getCharacterRuntimeKey(normalizedPackId)] = {
      version: 1,
      characterPackId: normalizedPackId,
      sessionId: String(state.sessionId || "").trim() || generateSessionId(),
      outfit: String(state.outfit || "").trim() || getProfileDefaultOutfit(),
      currentEmotion: String(state.currentEmotion || "").trim() || getProfileDefaultEmotion(),
      x: statePort.normalizeNullableInteger(state.x),
      y: statePort.normalizeNullableInteger(state.y),
      width: null,
      height: null,
      scale: clamp(Number(state.scale ?? statePort.DEFAULT_STATE.scale), SCALE_MIN, SCALE_MAX),
      opacity: clamp(Number(state.opacity ?? statePort.DEFAULT_STATE.opacity), 0.55, 1),
      care: carePort.hasImportedCareState(getCharacterRuntimeKey(normalizedPackId))
        ? null
        : cloneCareState(state.care),
      updatedAt: Date.now()
    };
    return map[getCharacterRuntimeKey(normalizedPackId)];
  }

  function findCharacterRuntimeState(packId) {
    const key = getCharacterRuntimeKey(packId);
    return statePort.normalizeCharacterRuntimeState(ensureCharacterRuntimeMap()[key]);
  }

  function createCharacterRuntimeState(packId, profile, { seedFromCurrent = false } = {}) {
    const appearance = profile?.appearance || {};
    const defaultOutfit = String(appearance.defaultOutfit || getProfileDefaultOutfit()).trim() || getProfileDefaultOutfit();
    const defaultEmotion = String(appearance.defaultEmotion || getProfileDefaultEmotion()).trim() || getProfileDefaultEmotion();
    return {
      version: 1,
      characterPackId: normalizeCharacterPackId(packId) || getActiveCharacterPackId(),
      sessionId: seedFromCurrent ? String(state.sessionId || "").trim() || generateSessionId() : generateSessionId(),
      outfit: seedFromCurrent ? String(state.outfit || defaultOutfit).trim() || defaultOutfit : defaultOutfit,
      currentEmotion: seedFromCurrent ? String(state.currentEmotion || defaultEmotion).trim() || defaultEmotion : defaultEmotion,
      x: statePort.normalizeNullableInteger(state.x),
      y: statePort.normalizeNullableInteger(state.y),
      width: null,
      height: null,
      scale: clamp(Number(state.scale ?? statePort.DEFAULT_STATE.scale), SCALE_MIN, SCALE_MAX),
      opacity: clamp(Number(state.opacity ?? statePort.DEFAULT_STATE.opacity), 0.55, 1),
      care: seedFromCurrent ? cloneCareState(state.care) : null,
      updatedAt: Date.now()
    };
  }

  function applyCharacterRuntimeState(packId, profile, options = {}) {
    perceptionPort.clearScreenVisionWorkspace({ quiet: true });
    const normalizedPackId = normalizeCharacterPackId(packId) || getActiveCharacterPackId();
    const map = ensureCharacterRuntimeMap();
    const key = getCharacterRuntimeKey(normalizedPackId);
    const persistedRuntime = findCharacterRuntimeState(normalizedPackId);
    const runtime = persistedRuntime || createCharacterRuntimeState(normalizedPackId, profile, options);
    if (!persistedRuntime && state.care) {
      runtime.care = cloneCareState(state.care);
    }
    const appearance = profile?.appearance || {};
    const defaultOutfit = String(appearance.defaultOutfit || getProfileDefaultOutfit()).trim() || getProfileDefaultOutfit();
    const defaultEmotion = String(appearance.defaultEmotion || getProfileDefaultEmotion()).trim() || getProfileDefaultEmotion();

    carePort.resetCareAuthority(normalizedPackId);

    state.sessionId = String(runtime.sessionId || "").trim() || generateSessionId();
    state.outfit = normalizeOutfitName(runtime.outfit || defaultOutfit) || defaultOutfit;
    state.currentEmotion = String(runtime.currentEmotion || defaultEmotion).trim() || defaultEmotion;
    state.x = statePort.normalizeNullableInteger(runtime.x);
    state.y = statePort.normalizeNullableInteger(runtime.y);
    state.width = null;
    state.height = null;
    state.scale = clamp(Number(runtime.scale ?? state.scale ?? statePort.DEFAULT_STATE.scale), SCALE_MIN, SCALE_MAX);
    state.opacity = clamp(Number(runtime.opacity ?? state.opacity ?? statePort.DEFAULT_STATE.opacity), 0.55, 1);
    state.care = cloneCareState(runtime.care);
    carePort.stopCareRuntime();

    map[key] = persistCurrentCharacterRuntimeState(normalizedPackId);
    return runtime;
  }

  let transientEmotionTimer = 0;

  let transientEmotionToken = 0;

  let characterActivationBridgeRegistered = false;

  let characterActivationTask = Promise.resolve();

  async function loadAndApplyPersistedCharacterState({ expectedPackId = "" } = {}) {
    const revision = ++characterSelectionRevision;
    const requestScope = runtimeScope.capture();
    const assertCurrentLoad = () => {
      if (revision !== characterSelectionRevision || !runtimeScope.isCurrent(requestScope)) throw new Error("character_load_superseded");
    };
    const [persistedState, launchBinding] = await Promise.all([
      platformPort.invoke("load_pet_state"),
      platformPort.invoke("get_client_launch_binding")
    ]);
    assertCurrentLoad();
    const loaded = {
      ...persistedState,
      backendUrl: launchBinding?.hasBackendOverride
        ? launchBinding.backendUrl
        : persistedState?.backendUrl
    };
    const instanceId = String(loaded?.instanceId || "").trim();
    const hostId = String(loaded?.hostId || instanceId).trim();
    if (
      String(launchBinding?.instanceId || "").trim() !== instanceId ||
      String(launchBinding?.hostId || launchBinding?.instanceId || "").trim() !== hostId
    ) {
      throw new Error("桌面客户端实例绑定与状态文件不一致。");
    }
    bindInstanceStorage(hostId);
    const persistedPackId = String(loaded?.characterPackId || "").trim();
    await refreshRuntimeCharacterPacks({ silent: true, scheduleSnapshot: false });
    assertCurrentLoad();
    const nextState = statePort.normalizeState(loaded);
    if (expectedPackId && nextState.characterPackId !== expectedPackId) {
      throw new Error(`角色状态不一致：期望 ${expectedPackId}，实际 ${nextState.characterPackId}。`);
    }
    if (!listCharacterPacks().some(item => item.id === nextState.characterPackId)) {
      throw new Error(`角色包 ${persistedPackId} 未加载，已保留当前桌宠。`);
    }
    const pack = selectCharacterPack(nextState.characterPackId, { persist: false });
    Object.assign(state, nextState);
    state.characterPackId = pack.packId;
    resourceState.manifest = null;
    resourceState.source = "character_pack";
    refreshLocalResourceAssets();
    applyCharacterRuntimeState(pack.packId, pack.profile);
    applyCharacterChrome();
    windowPort.applyVisualState();
    if (canRenderCurrentLocalResources()) {
      setPetEmotion(state.currentEmotion, { persist: false, force: true });
    } else {
      state.currentEmotion = getProfileDefaultEmotion();
    }
    return pack;
  }

  async function registerCharacterActivationBridge() {
    if (!platformPort.isTauriRuntime || characterActivationBridgeRegistered) return;

    const unlisten = await platformPort.listen(CHARACTER_PACK_ACTIVATED_EVENT, (event) => {
      const packId = String(event?.payload?.packId || "").trim();
      if (!packId) return;
      characterActivationTask = characterActivationTask
        .catch(() => {})
        .then(() => applyPersistedCharacterActivation(packId))
        .catch((error) => {
          statusPort.setStatus(`角色切换失败：${formatError(error)}`);
        });
    });
    lifecycle.track(unlisten);
    characterActivationBridgeRegistered = true;
  }

  async function applyPersistedCharacterActivation(packId) {
    const pack = await loadAndApplyPersistedCharacterState({ expectedPackId: packId });
    statePort.scheduleSave(0);
    statusPort.setStatus(`角色包已切换为 ${pack.profile.identity.name}。`, { durationMs: 2400 });
    await reloadCharacterResources({ userTriggered: true });
    setPetEmotion(state.currentEmotion || getProfileDefaultEmotion(), { persist: false, force: true });
    windowPort.scheduleNativeWindowStateApply({ forceHitTest: true });
    await settingsPort.broadcastSettingsSnapshot();
    void backendPort.ensureBackendSession({ restoreLatest: state.restoreLatestOnStartup });
  }

  function applyCharacterChrome() {
    const appName = getProfileIdentityText("appName", APP_DISPLAY_NAME);
    const name = getProfileIdentityText("name", CHARACTER_NAME);
    document.title = appName;
    if (els.menuTitle) els.menuTitle.textContent = appName;
    if (els.chatInput) els.chatInput.placeholder = getActiveCharacterText("inputPlaceholder");
    if (els.hitbox) els.hitbox.setAttribute("aria-label", name);
    visualRenderer.setCharacterLabel(name);
    if (els.close) els.close.title = `关闭 ${appName}`;
    panelPort.schedulePanelStateSync(80);
  }

  function buildCurrentExpressionSnapshot() {
    const entry = resolveEmotionEntry(state.currentEmotion);
    if (!entry) {
      return {
        id: state.currentEmotion || "",
        name: state.currentEmotion || "",
        image: state.currentEmotion || "",
        outfitId: state.outfit || "",
        characterPackId: getCurrentCharacterPackId(),
        updatedAt: Date.now()
      };
    }
    return {
      id: entry.id || state.currentEmotion || "",
      name: entry.name || entry.id || state.currentEmotion || "",
      image: entry.image || entry.url || entry.key || entry.id || "",
      outfitId: state.outfit || "",
      characterPackId: getCurrentCharacterPackId(),
      updatedAt: Date.now()
    };
  }

  function renderResourceDetails() {
    if (!els.resourceDetails) return;
    const activeOutfit = getActiveOutfit();
    const outfits = getManifestOutfits();
    const count = getActiveEmotions().length;
    const source = resourceSourceLabel(resourceState.source);
    const requested =
      state.outfit && activeOutfit.id && state.outfit !== activeOutfit.id ? ` · 请求 ${state.outfit}` : "";
    const outfitHint = outfits.length > 1 ? ` · 可用服装 ${outfits.length}` : "";
    const sessionHint = state.sessionId ? ` · 会话 ${shortId(state.sessionId)}` : "";
    els.resourceDetails.textContent = `${source} · ${activeOutfit.id || getProfileDefaultOutfit()} · ${count} 表情${requested}${outfitHint}${sessionHint}`;
  }

  function renderEmotionGrid() {
    if (!els.emotionGrid) return;
    const emotions = getActiveEmotions();
    const signature = emotions
      .map((emotion) => `${emotion.id}:${emotion.name || ""}`)
      .join("|");
    const active = state.currentEmotion || getProfileDefaultEmotion();
    const gridSignature = `${signature}::${active}`;
    if (els.emotionGrid.dataset.signature === gridSignature) return;
    els.emotionGrid.dataset.signature = gridSignature;

    els.emotionGrid.replaceChildren(
      ...emotions.map((emotion) => {
        const id = String(emotion.id || emotion.name || "").trim();
        const button = document.createElement("button");
        button.type = "button";
        button.dataset.emotion = id;
        button.textContent = String(emotion.name || id);
        button.title = id;
        button.classList.toggle("active", id === active);
        return button;
      })
    );
  }

  async function updateOutfitFromInput() {
    await updateOutfit(els.outfit.value);
  }

  async function updateOutfit(value) {
    const outfit = normalizeOutfitName(value);
    state.outfit = outfit || getProfileDefaultOutfit();
    els.outfit.value = state.outfit;
    interactionPort.cancelEmotionPreview({ restore: true });
    statePort.scheduleSave(0);
    statusPort.setStatus(`服装已设置：${state.outfit}`);
    await reloadCharacterResources({ userTriggered: true });
  }

  async function refreshCharacterPacksFromSettings(value) {
    const options = value && typeof value === "object" ? value : { selectPackId: value };
    const refreshed = await refreshRuntimeCharacterPacks({ userTriggered: true });
    const selectPackId = String(options.selectPackId || "").trim();
    if (refreshed && selectPackId && options.apply !== false) {
      await updateCharacterPack(selectPackId);
      return;
    }
    if (refreshed) {
      statusPort.setStatus("角色包列表已刷新。", { durationMs: 1600 });
    }
  }

  async function refreshRuntimeCharacterPacks({
    userTriggered = false,
    silent = false,
    scheduleSnapshot = true
  } = {}) {
    const requestScope = runtimeScope.capture();
    if (!platformPort.isTauriRuntime) return false;
    const packs = await platformPort.tauriCall("list_character_packs", {}, { quiet: true });
    if (!runtimeScope.isCurrent(requestScope)) return false;
    if (!Array.isArray(packs)) {
      if (!silent && userTriggered) statusPort.setStatus("角色包列表刷新失败。", { durationMs: 1800 });
      return false;
    }
    runtimeCharacterPacks = packs;
    setRuntimeCharacterPacks(packs);
    refreshLocalResourceAssets();
    if (scheduleSnapshot) settingsPort.scheduleSettingsSnapshot();
    return true;
  }

  async function updateCharacterPack(value) {
    const selectionRevision = ++characterSelectionRevision;
    const assertLatestSelection = () => {
      if (selectionRevision !== characterSelectionRevision || lifecycle.disposed) throw new Error("character_selection_superseded");
    };
    const requestedPackId = String(value || "").trim();
    if (!requestedPackId) {
      throw new Error("角色包 ID 不能为空。");
    }
    if (platformPort.isTauriRuntime) {
      await refreshRuntimeCharacterPacks({ silent: true });
      assertLatestSelection();
    }
    const availablePack = listCharacterPacks().find((item) => item.id === requestedPackId);
    if (!availablePack) {
      throw new Error(`角色包 ${requestedPackId} 未加载，已保留当前角色。`);
    }

    const previousPackId = state.characterPackId || getActiveCharacterPackId();
    if (platformPort.isTauriRuntime) {
      await statePort.saveNow();
      assertLatestSelection();
    } else {
      persistCurrentCharacterRuntimeState(previousPackId);
    }
    await voicePort.cancelVoiceRecording();
    assertLatestSelection();
    chatPort.interruptReply({ reason: "character_changed" });
    const pack = selectCharacterPack(availablePack.id);
    if (pack.packId !== requestedPackId) {
      throw new Error(`角色包解析结果不一致：请求 ${requestedPackId}，得到 ${pack.packId}。`);
    }
    state.characterPackId = pack.packId;
    resourceState.manifest = null;
    resourceState.source = "character_pack";
    refreshLocalResourceAssets();
    applyCharacterRuntimeState(pack.packId, pack.profile);
    const selectionScope = runtimeScope.capture();
    applyCharacterChrome();
    windowPort.applyVisualState();
    setPetEmotion(state.currentEmotion || getProfileDefaultEmotion(), { persist: false, force: true });

    if (pack.packId === previousPackId) {
      statePort.scheduleSave(0);
      statusPort.setStatus(`角色包已是：${pack.profile.identity.name}`);
      await reloadCharacterResources({ userTriggered: true });
      assertLatestSelection();
      if (!runtimeScope.isCurrent(selectionScope)) throw new Error("character_selection_superseded");
      setPetEmotion(state.currentEmotion || getProfileDefaultEmotion(), { persist: false, force: true });
      return {
        requestedPackId,
        activePackId: state.characterPackId,
        characterName: pack.profile.identity.name,
        resourceSource: resourceState.source
      };
    }

    statusPort.setStatus(`角色包已切换为 ${pack.profile.identity.name}，正在应用。`, { durationMs: 2400 });
    if (platformPort.isTauriRuntime) {
      await statePort.saveNow();
    }
    await reloadCharacterResources({ userTriggered: true });
    assertLatestSelection();
    if (!runtimeScope.isCurrent(selectionScope)) throw new Error("character_selection_superseded");
    setPetEmotion(state.currentEmotion || getProfileDefaultEmotion(), { persist: false, force: true });
    windowPort.scheduleNativeWindowStateApply({ forceHitTest: true });
    void backendPort.ensureBackendSession({ restoreLatest: state.restoreLatestOnStartup });
    return {
        requestedPackId,
      activePackId: state.characterPackId,
      characterName: pack.profile.identity.name,
      resourceSource: resourceState.source
    };
  }

  async function reloadCharacterResources({ startup = false, userTriggered = false, silent = false } = {}) {
    musicPort.resetRestingEmotionFailures?.();
    const revision = ++resourceRefreshRevision;
    resourceState.health = "checking";
    resourceState.healthMessage = "Checking";
    statusPort.updateConnectionStatus();

    const { healthy, scope: requestScope } = await backendPort.checkBackendHealth();
    if (!runtimeScope.isCurrent(requestScope)) return false;
    if (revision !== resourceRefreshRevision) return false;
    if (!healthy) {
      carePort.markCareAuthorityUnavailable("backend_offline");
      useBundledResources();
      if (canRenderCurrentLocalResources()) {
        setPetEmotion(state.currentEmotion || getProfileDefaultEmotion(), { force: true });
      } else {
        state.currentEmotion = getProfileDefaultEmotion();
      }
      backendPort.scheduleBackendRetry();
      const message = "本地待机中：后端暂时连不上。";
      if (!silent && (startup || userTriggered)) bubblePort.showBubbleText(message, { transient: true, durationMs: 3200 });
      statusPort.setRuntimeStatus(message, { mode: "offline" });
      return false;
    }

    try {
      backendPort.clearBackendRetry();
      const manifest = await backendPort.fetchResourceManifest();
      if (!runtimeScope.isCurrent(requestScope)) return false;
      if (revision !== resourceRefreshRevision) return false;
      applyResourceManifest(manifest);
      await carePort.refreshDesktopCareState({ importLegacy: true, silent });
      if (!runtimeScope.isCurrent(requestScope)) return false;
      if (revision !== resourceRefreshRevision) return false;
      setPetEmotion(state.currentEmotion || getProfileDefaultEmotion(), { force: true });
      const count = getActiveEmotions().length;
      const message = `资源已加载：${getActiveOutfit().id} / ${count}`;
      if (!silent && userTriggered) bubblePort.showBubbleText(message, { transient: true });
      if (!silent || statusPort.runtimeMode === "offline" || statusPort.runtimeMode === "checking") {
        statusPort.setRuntimeStatus(message, { mode: "idle" });
      }
      if (state.voiceEnabled) {
        ttsPort.scheduleTtsPrewarm({ delayMs: startup ? 900 : 300 });
      }
      return true;
    } catch (error) {
      if (!runtimeScope.isCurrent(requestScope)) return false;
      if (revision !== resourceRefreshRevision) return false;
      carePort.markCareAuthorityUnavailable("resource_reload_failed");
      resourceState.healthMessage = formatError(error);
      useBundledResources();
      if (canRenderCurrentLocalResources()) {
        setPetEmotion(state.currentEmotion || getProfileDefaultEmotion(), { force: true });
      } else {
        state.currentEmotion = getProfileDefaultEmotion();
      }
      const message = `资源暂时没拉到：${friendlyErrorMessage(formatError(error))}`;
      if (!silent && (startup || userTriggered)) bubblePort.showBubbleText(message, { transient: true, durationMs: 3600 });
      statusPort.setRuntimeStatus(message, { mode: "error" });
      return false;
    } finally {
      if (runtimeScope.isCurrent(requestScope) && revision === resourceRefreshRevision) statusPort.updateConnectionStatus();
    }
  }

  function applyResourceManifest(manifest) {
    syncResourceContractFromManifest(manifest);
    const outfits = Array.isArray(manifest?.characters?.outfits) ? manifest.characters.outfits : [];
    const defaultOutfit = getManifestDefaultOutfit(manifest);
    const outfit =
      findEntry(outfits, state.outfit) ||
      findEntry(outfits, defaultOutfit) ||
      findEntry(outfits, getProfileDefaultOutfit()) ||
      outfits[0] ||
      null;

    if (!outfit || !Array.isArray(outfit.emotions) || outfit.emotions.length === 0) {
      throw new Error("manifest has no character emotions");
    }

    portraitAssetVersion = Math.max(Date.now(), portraitAssetVersion + 1);
    const emotions = outfit.emotions
      .filter((item) => item && typeof item === "object")
      .map((item) => ({
        ...item,
        id: String(item.id || item.name || "").trim(),
        name: String(item.name || item.id || "").trim(),
        aliases: Array.isArray(item.aliases) ? item.aliases.map((alias) => String(alias || "").trim()).filter(Boolean) : [],
        url: resolveAssetUrl(item.path || item.url || item.src, state.backendUrl)
      }))
      .filter((item) => item.id && item.url);

    if (emotions.length === 0) {
      throw new Error("manifest emotions have no image paths");
    }

    resourceState.manifest = manifest;
    resourceState.outfit = {
      ...outfit,
      id: String(outfit.id || getProfileDefaultOutfit()),
      name: String(outfit.name || outfit.id || getProfileDefaultOutfit()),
      aliases: Array.isArray(outfit.aliases) ? outfit.aliases : [],
      emotions
    };
    resourceState.source = "manifest";
    resourceState.loadedAt = Date.now();
    state.outfit = resourceState.outfit.id;
  }

  function syncResourceContractFromManifest(manifest) {
    const desktop = getDesktopManifestContract(manifest);
    if (!desktop) return;
    resourceState.contractVersion = String(
      desktop.contract_version || desktop.contractVersion || resourceState.contractVersion || ""
    );
    if (resourceState.contractVersion) {
      resourceState.contractSource = "desktop_pet";
    }
  }

  function getDesktopManifestContract(manifest) {
    const clients = manifest?.clients;
    if (!clients || typeof clients !== "object") return null;
    const desktop = clients.desktop_pet;
    return desktop && typeof desktop === "object" ? desktop : null;
  }

  function getManifestDefaultOutfit(manifest) {
    const desktop = getDesktopManifestContract(manifest);
    return String(
      desktop?.default_outfit ||
        desktop?.defaultOutfit ||
        manifest?.defaults?.desktop_pet_outfit ||
        manifest?.defaults?.outfit ||
        getProfileDefaultOutfit()
    );
  }

  function getManifestDefaultEmotion(manifest) {
    const desktop = getDesktopManifestContract(manifest);
    return String(
      desktop?.default_emotion ||
        desktop?.defaultEmotion ||
        manifest?.defaults?.desktop_pet_emotion ||
        manifest?.defaults?.emotion ||
        getProfileDefaultEmotion()
    );
  }

  function useBundledResources() {
    refreshLocalResourceAssets();
    resourceState.manifest = null;
    resourceState.outfit = findEntry(localOutfits, state.outfit) || getDefaultLocalOutfit();
    resourceState.source = getLocalResourceSource();
    resourceState.loadedAt = Date.now();
    if (!state.outfit) state.outfit = resourceState.outfit.id;
  }

  function canRenderCurrentLocalResources() {
    if (resourceState.source !== "bundled") return true;
    return canUseBundledEmotionFallback();
  }

  function canUseBundledEmotionFallback() {
    const packId = normalizeEntryKey(getCurrentCharacterPackId());
    const identityId = normalizeEntryKey(getActiveCharacterProfile()?.identity?.id);
    return packId === "akane_v1" || identityId === "akane_v1";
  }

  function setTransientEmotion(emotion, { durationMs = 2400 } = {}) {
    const token = ++transientEmotionToken;
    lifecycle.clearTimeout(transientEmotionTimer);
    const resolved = setPetEmotion(emotion, { persist: false });
    transientEmotionTimer = lifecycle.setTimeout(() => {
      if (token !== transientEmotionToken) return;
      transientEmotionTimer = 0;
      if (chatPort.sending || ttsPort.ttsActive || voicePort.voiceInputState === "recording") return;
      musicPort.setRestingPetEmotion();
      musicPort.scheduleMusicEmotionRestore();
    }, durationMs);
    return resolved;
  }

  function clearTransientEmotionRestore() {
    transientEmotionToken += 1;
    lifecycle.clearTimeout(transientEmotionTimer);
    transientEmotionTimer = 0;
  }

  function setPetEmotion(emotion, { persist = true, force = false } = {}) {
    if (persist || force) clearTransientEmotionRestore();
    if (persist && interactionPort.previewEmotionRestore) {
      interactionPort.cancelEmotionPreview({ restore: false });
    }
    let entry = resolveEmotionEntry(emotion);
    if (entry && !entry.url && entry.path && platformPort.isTauriRuntime) {
      try {
        entry = { ...entry, url: platformPort.convertFileSrc(entry.path) };
      } catch (error) {
        console.error("[setPetEmotion] convertFileSrc failed:", entry.path, error);
      }
    }
    if (entry && !entry.url && canUseBundledEmotionFallback()) {
      const fallback = findEntry(bundledOutfit.emotions, emotion) || bundledOutfit.emotions[0];
      if (fallback?.url) {
        console.warn("[setPetEmotion] falling back to bundled emotion:", emotion, fallback);
        entry = { ...fallback };
      }
    }
    if (!entry?.url) {
      console.error("[setPetEmotion] resolved entry has no image URL:", emotion, entry);
      statusPort.setStatus(`立绘地址缺失：${emotion}`, { durationMs: 3600 });
      if (entry?.id) state.currentEmotion = entry.id;
      settingsPort.scheduleSettingsSnapshot();
      windowPort.updateMenuLabels();
      if (persist) statePort.scheduleSave(0);
      return entry?.id || String(emotion || "").trim();
    }
    if (!force && state.currentEmotion === entry.id && els.petImage.src) return entry.id;
    state.currentEmotion = entry.id;
    visualRenderer.setExpression({ ...entry, idleBlink: getIdleBlinkForEntry(entry) }, { force });
    settingsPort.scheduleSettingsSnapshot();
    windowPort.updateMenuLabels();
    if (persist) statePort.scheduleSave(0);
    return entry.id;
  }

  function resolveEmotionEntry(value) {
    const emotions = getActiveEmotions();
    const candidates = buildEmotionCandidates(value);
    for (const candidate of candidates) {
      const match = findEntry(emotions, candidate);
      if (match) return match;
    }
    return findEntry(emotions, getProfileDefaultEmotion()) || findEntry(emotions, "normal") || emotions[0] || getDefaultLocalOutfit().emotions[0];
  }

  function buildEmotionCandidates(value) {
    const raw = String(value || "").trim();
    const result = [];
    const add = (item) => {
      const text = String(item || "").trim();
      if (text && !result.includes(text)) result.push(text);
    };

    add(raw);
    const key = normalizeEntryKey(raw);
    for (const item of getProfileEmotionAliases()[key] || []) add(item);
    add(getProfileDefaultEmotion());
    add("normal");
    return result;
  }

  function buildCurrentVisual() {
    const outfit = getActiveOutfit();
    const emotions = getActiveEmotions();
    const emotion = resolveEmotionEntry(state.currentEmotion)?.id;
    const characterPackId = getCurrentCharacterPackId();
    return {
      character_pack_id: characterPackId,
      emotion,
      character: {
        character_pack_id: characterPackId,
        outfit: outfit.id,
        available_emotions: emotions.map((item) => ({
          id: item.id,
          name: item.name || item.id,
          aliases: Array.isArray(item.aliases) ? item.aliases : []
        }))
      },
      scene: {},
      available_emotions: emotions.map((item) => item.id)
    };
  }

  function getCurrentCharacterPackId() {
    return state.characterPackId || getActiveCharacterPackId();
  }

  function getActiveOutfit() {
    return resourceState.outfit || getDefaultLocalOutfit();
  }

  function getActiveEmotions() {
    const emotions = Array.isArray(getActiveOutfit()?.emotions) ? getActiveOutfit().emotions : [];
    return emotions.length ? emotions : getDefaultLocalOutfit().emotions;
  }

  function getManifestOutfits() {
    return Array.isArray(resourceState.manifest?.characters?.outfits)
      ? resourceState.manifest.characters.outfits.filter((item) => item && typeof item === "object")
      : [];
  }

  function getAvailableOutfits() {
    const outfits = getManifestOutfits();
    return outfits.length ? outfits : localOutfits;
  }

  function serializeOutfit(outfit) {
    const id = String(outfit?.id || outfit?.name || "").trim();
    const name = String(outfit?.name || outfit?.id || "").trim();
    const aliases = normalizeAliases(outfit?.aliases);
    const emotions = listOutfitEmotions(outfit);
    const issues = buildResourceIssues(outfit, emotions);
    const active = findEntry([outfit], getActiveOutfit().id) !== null;
    return {
      id,
      name,
      aliases,
      active,
      source: resourceState.source,
      emotionCount: emotions.length,
      allowedEmotionCount: Array.isArray(outfit?.allowed_emotions) ? outfit.allowed_emotions.length : 0,
      missingRequired: issues.missingRequired,
      missingRecommended: issues.missingRecommended
    };
  }

  function serializeEmotion(emotion) {
    const id = String(emotion?.id || emotion?.name || "").trim();
    return {
      id,
      name: String(emotion?.name || emotion?.id || "").trim(),
      aliases: normalizeAliases(emotion?.aliases),
      image: String(emotion?.image || emotion?.url || "").trim(),
      path: String(emotion?.path || "").trim(),
      url: String(emotion?.url || "").trim()
    };
  }

  function listOutfitEmotions(outfit) {
    return (Array.isArray(outfit?.emotions) ? outfit.emotions : [])
      .filter((item) => item && typeof item === "object")
      .map((item) => ({
        ...item,
        id: String(item.id || item.name || "").trim(),
        name: String(item.name || item.id || "").trim(),
        aliases: normalizeAliases(item.aliases)
      }))
      .filter((item) => item.id);
  }

  function buildResourceIssues(outfit, emotions = listOutfitEmotions(outfit)) {
    const keys = new Set(
      (Array.isArray(emotions) ? emotions : [])
        .flatMap((item) => [item.id, item.name, ...normalizeAliases(item.aliases)])
        .map(normalizeEntryKey)
        .filter(Boolean)
    );
    return {
      missingRequired: getProfileRequiredEmotions().filter((item) => !keys.has(normalizeEntryKey(item))),
      missingRecommended: getProfileRecommendedEmotions().filter((item) => !keys.has(normalizeEntryKey(item)))
    };
  }

  function resourceSourceLabel(source) {
    return {
      manifest: "当前角色包",
      character_pack: "本地角色包",
      bundled: "内置兜底"
    }[String(source || "")] || "本地兜底";
  }

  function getLocalResourceSource() {
    return runtimeCharacterPackOutfits.length ? "character_pack" : "bundled";
  }

  function getDefaultLocalOutfit() {
    return findEntry(localOutfits, getProfileDefaultOutfit()) || localOutfits[0] || bundledOutfit;
  }

  function getLocalPortraitUrl(emotion, outfit = state.outfit) {
    if (!platformPort.isTauriRuntime) return '';
    const localOutfit = findEntry(localOutfits, outfit);
    return versionPortraitUrl(findEntry(localOutfit?.emotions || [], emotion)?.url || '');
  }

  function getIdleBlinkForEntry(entry) {
    const config = getActiveCharacterProfile()?.appearance?.idleBlink;
    if (!config || config.emotion !== entry.id || (config.outfit && config.outfit !== getActiveOutfit().id)) return null;
    const pack = runtimeCharacterPacks.find(item => item.id === getCurrentCharacterPackId());
    let url = '';
    if (platformPort.isTauriRuntime) {
      if (pack?.idleBlinkAsset) url = platformPort.convertFileSrc(pack.idleBlinkAsset);
    } else {
      // The manifest supplies the pack's public asset root; never accept an external URL from the profile.
      const root = entry.url?.split('/characters/')[0];
      if (root && root !== entry.url) url = `${root}/${config.asset.split('/').map(encodeURIComponent).join('/')}`;
    }
    return { ...config, url: versionPortraitUrl(url) };
  }

  function refreshLocalResourceAssets() {
    portraitAssetVersion = Math.max(Date.now(), portraitAssetVersion + 1);
    const activePackId = getCurrentCharacterPackId();
    runtimeCharacterPackOutfits = buildRuntimeCharacterPackOutfits(activePackId);
    localOutfits = buildLocalOutfits();
    if (resourceState.source !== "manifest") {
      resourceState.outfit = findEntry(localOutfits, state.outfit) || getDefaultLocalOutfit();
      resourceState.source = getLocalResourceSource();
      resourceState.loadedAt = Date.now();
    }
  }

  function buildLocalOutfits() {
    return runtimeCharacterPackOutfits.length ? runtimeCharacterPackOutfits : [bundledOutfit];
  }

  function buildRuntimeCharacterPackOutfits(activePackId = getCurrentCharacterPackId()) {
    const pack = runtimeCharacterPacks.find((item) => String(item?.id || item?.packId || "").trim() === activePackId);
    const outfits = Array.isArray(pack?.outfits) ? pack.outfits : [];
    const built = outfits
      .map((outfit) => {
        const outfitId = String(outfit?.id || outfit?.name || "").trim();
        const emotions = (Array.isArray(outfit?.emotions) ? outfit.emotions : [])
          .map((emotion) => {
            const id = String(emotion?.id || emotion?.name || "").trim();
            const path = String(emotion?.path || "").trim();
            let url = "";
            if (path) {
              try {
                url = platformPort.isTauriRuntime ? platformPort.convertFileSrc(path) : path;
              } catch (error) {
                console.error("[buildRuntimeCharacterPackOutfits] convertFileSrc failed:", path, error);
              }
            }
            return {
              id,
              name: String(emotion?.name || id).trim(),
              aliases: [],
              url: versionPortraitUrl(String(url || "").trim()),
              path
            };
          })
          .filter((emotion) => emotion.id && emotion.url);
        return {
          id: outfitId,
          name: String(outfit?.name || outfitId).trim(),
          aliases: [],
          emotions: sortEmotions(emotions)
        };
      })
      .filter((outfit) => outfit.id && outfit.emotions.length)
      .sort(compareOutfitEntries);
    return built.length ? built : [];
  }

  function buildBundledOutfit() {
    const emotions = Object.entries(bundledCharacterAssets)
      .map(([path, url]) => {
        const id = decodeURIComponent(path.split("/").pop()?.replace(/\.(png|jpe?g|webp)$/i, "") || "");
        return {
          id,
          name: id,
          aliases: [],
          url: String(url || "")
        };
      })
      .filter((item) => item.id && item.url)
      .sort(compareEmotionEntries);

    return {
      id: DEFAULT_OUTFIT,
      name: DEFAULT_OUTFIT,
      aliases: [],
      emotions
    };
  }

  function sortEmotions(emotions) {
    return [...emotions].sort(compareEmotionEntries);
  }

  function compareOutfitEntries(a, b) {
    const defaultOutfit = getProfileDefaultOutfit();
    if (a.id === defaultOutfit) return -1;
    if (b.id === defaultOutfit) return 1;
    return String(a.id || "").localeCompare(String(b.id || ""), "zh-CN");
  }

  function compareEmotionEntries(a, b) {
    const defaultEmotion = getProfileDefaultEmotion();
    if (a.id === defaultEmotion) return -1;
    if (b.id === defaultEmotion) return 1;
    return String(a.id || "").localeCompare(String(b.id || ""), "zh-CN");
  }

  function findEntry(items, value) {
    const raw = String(value || "").trim();
    if (!raw || !Array.isArray(items)) return null;
    const key = normalizeEntryKey(raw);
    return (
      items.find((item) => item && normalizeEntryKey(item.id) === key) ||
      items.find((item) => {
        if (!item || typeof item !== "object") return false;
        return entryLookupValues(item).some((option) => option === raw || normalizeEntryKey(option) === key);
      }) || null
    );
  }

  function entryLookupValues(entry) {
    const values = [];
    for (const key of ["id", "name"]) {
      const value = String(entry?.[key] || "").trim();
      if (value && !values.includes(value)) values.push(value);
    }
    for (const alias of entry?.aliases || []) {
      const value = String(alias || "").trim();
      if (value && !values.includes(value)) values.push(value);
    }
    return values;
  }

  function normalizeAliases(value) {
    return (Array.isArray(value) ? value : [])
      .map((item) => String(item || "").trim())
      .filter(Boolean);
  }

  function resolveAssetUrl(path, backendUrl) {
    const raw = String(path || "").trim();
    if (!raw) return "";
    if (/^(https?:|file:|data:|blob:)/i.test(raw)) return versionPortraitUrl(encodeURI(raw));
    const base = String(backendUrl || "").trim().replace(/\/+$/, "");
    if (!base) return encodeURI(raw);
    if (raw.startsWith("/")) return versionPortraitUrl(encodeURI(`${base}${raw}`));
    return versionPortraitUrl(encodeURI(`${base}/${raw.replace(/^\/+/, "")}`));
  }

  function versionPortraitUrl(value) {
    if (!/^https?:/i.test(value)) return value;
    const url = new URL(value);
    // Refresh once per resource load, then reuse decoded portraits between expressions.
    url.searchParams.set("portrait_v", String(portraitAssetVersion));
    return url.href;
  }

  return {
    buildCharacterRuntimeSnapshot,
    buildBackendCharacterContext,
    getCharacterRuntimeKey,
    persistCurrentCharacterRuntimeState,
    get transientEmotionTimer() { return transientEmotionTimer; },
    loadAndApplyPersistedCharacterState,
    registerCharacterActivationBridge,
    applyCharacterChrome,
    buildCurrentExpressionSnapshot,
    renderResourceDetails,
    renderEmotionGrid,
    updateOutfitFromInput,
    updateOutfit,
    refreshCharacterPacksFromSettings,
    updateCharacterPack,
    reloadCharacterResources,
    getManifestDefaultOutfit,
    getManifestDefaultEmotion,
    setTransientEmotion,
    clearTransientEmotionRestore,
    setPetEmotion,
    resolveEmotionEntry,
    buildCurrentVisual,
    getCurrentCharacterPackId,
    getActiveOutfit,
    getActiveEmotions,
    getAvailableOutfits,
    serializeOutfit,
    serializeEmotion,
    buildResourceIssues,
    resourceSourceLabel,
    getLocalResourceSource,
    getDefaultLocalOutfit,
    getLocalPortraitUrl
  };
}
