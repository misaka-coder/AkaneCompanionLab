import { createUnresolvedCareFeature, isCareFeatureEnabled, normalizeCareState, cloneCareState, CARE_PASSIVE_TICK_MS, formatSignedCareDelta, formatCareFeedbackText } from "../care-feature.js";
import { readJsonResponse } from "../desktop-voice-session.js";
import { SHOP_STATUS_EVENT } from './constants.js';
import { getProfileCareConfig } from './profile.js';
import { formatError } from './utils.js';

/** Owns care behavior and its temporary runtime state. */
export function createCareController({
  state,
  resourceState,
  els,
  platform: platformPort,
  lifecycle,
  scope: runtimeScope,
  character: characterPort,
  settings: settingsPort,
  panel: panelPort,
  status: statusPort,
  persistence: statePort,
  backend: backendPort,
  bubble: bubblePort,
  chat: chatPort,
  tts: ttsPort,
  attachments: attachmentsPort,
  music: musicPort,
  input: inputPort
}) {
  const careAuthority = {
    status: "unresolved",
    characterPackId: "",
    reason: ""
  };

  const careAuthorityImportedKeys = new Set();

  function getCareFeatureStatus() {
    return resourceState.features?.care || createUnresolvedCareFeature();
  }

  function isCareConfiguredForCurrentCharacter() {
    return isCareFeatureEnabled(getCareFeatureStatus()) && Boolean(getProfileCareConfig()?.enabled);
  }

  function isCareRuntimeActive() {
    return isCareConfiguredForCurrentCharacter()
      && careAuthority.status === "ready"
      && careAuthority.characterPackId === characterPort.getCurrentCharacterPackId();
  }

  function applyCareFeatureStatus(feature) {
    resourceState.features = {
      ...(resourceState.features || {}),
      care: feature && typeof feature === "object" ? { ...feature } : createUnresolvedCareFeature()
    };

    if (!isCareFeatureEnabled(getCareFeatureStatus())) {
      careAuthority.status = "disabled";
      careAuthority.characterPackId = characterPort.getCurrentCharacterPackId();
      careAuthority.reason = "feature_disabled";
      stopCareRuntime();
      settingsPort.scheduleSettingsSnapshot(0);
      panelPort.schedulePanelStateSync(0);
      return;
    }

    const config = getProfileCareConfig();
    if (config.enabled) {
      if (careAuthority.characterPackId !== characterPort.getCurrentCharacterPackId() || careAuthority.status === "disabled") {
        careAuthority.status = "unresolved";
        careAuthority.characterPackId = characterPort.getCurrentCharacterPackId();
        careAuthority.reason = "awaiting_snapshot";
      }
    } else {
      careAuthority.status = "disabled";
      careAuthority.characterPackId = characterPort.getCurrentCharacterPackId();
      careAuthority.reason = "character_care_disabled";
      stopCareRuntime();
    }
    settingsPort.scheduleSettingsSnapshot(0);
    panelPort.schedulePanelStateSync(0);
  }

  function stopCareRuntime() {
    lifecycle.clearTimeout(carePassiveTimer);
    lifecycle.clearTimeout(careWorkTimer);
    carePassiveTimer = 0;
    careWorkTimer = 0;
    if (els?.stage) els.stage.classList.remove("is-away");
    void setCareAwayClickThrough(false);
    musicPort.scheduleMusicEmotionRestore({ delayMs: 0 });
  }

  function rejectDisabledCareAction() {
    const message = careAuthority.status === "unavailable"
      ? "养成服务暂时不可用。"
      : "养成模块未启用。";
    statusPort.setStatus(message, { durationMs: 2200 });
    notifyShopStatus(message, "disabled");
    return false;
  }

  function markCareAuthorityUnavailable(reason = "care_runtime_unavailable") {
    if (!isCareFeatureEnabled(getCareFeatureStatus()) || !getProfileCareConfig()?.enabled) {
      careAuthority.status = "disabled";
    } else {
      careAuthority.status = "unavailable";
    }
    careAuthority.characterPackId = characterPort.getCurrentCharacterPackId();
    careAuthority.reason = String(reason || "care_runtime_unavailable");
    stopCareRuntime();
    settingsPort.scheduleSettingsSnapshot(0);
    panelPort.schedulePanelStateSync(0);
  }

  function applyAuthoritativeCareSnapshot(snapshot, { characterPackId = characterPort.getCurrentCharacterPackId() } = {}) {
    if (!snapshot || typeof snapshot !== "object" || Array.isArray(snapshot)) return false;
    if (String(snapshot.authority || snapshot.source || "") !== "care_runtime") return false;
    if (characterPackId !== characterPort.getCurrentCharacterPackId()) return false;
    const snapshotUpdatedAt = Math.max(0, Number(snapshot.updated_at || snapshot.updatedAt || 0));
    const currentUpdatedAt = Math.max(0, Number(state.care?.updatedAt || state.care?.updated_at || 0));
    if (isCareRuntimeActive() && snapshotUpdatedAt && currentUpdatedAt > snapshotUpdatedAt) {
      return true;
    }
    state.care = normalizeCareState(snapshot, getProfileCareConfig());
    careAuthority.status = "ready";
    careAuthority.characterPackId = characterPackId;
    careAuthority.reason = "";
    careAuthorityImportedKeys.add(characterPort.getCharacterRuntimeKey(characterPackId));
    characterPort.persistCurrentCharacterRuntimeState(characterPackId);
    syncCareAwayVisualState();
    scheduleCareWorkCompletion();
    scheduleCarePassiveTick();
    musicPort.scheduleMusicEmotionRestore({ delayMs: 0 });
    statePort.scheduleSave(0);
    settingsPort.scheduleSettingsSnapshot(0);
    panelPort.schedulePanelStateSync(0);
    return true;
  }

  function buildCareAuthorityRequest(extra = {}) {
    return {
      ...characterPort.buildBackendCharacterContext(),
      ...extra
    };
  }

  async function refreshDesktopCareState({ importLegacy = false, silent = false } = {}) {
    const requestScope = runtimeScope.capture();
    if (!isCareFeatureEnabled(getCareFeatureStatus()) || !getProfileCareConfig()?.enabled) {
      careAuthority.status = "disabled";
      careAuthority.characterPackId = characterPort.getCurrentCharacterPackId();
      careAuthority.reason = "care_disabled";
      stopCareRuntime();
      return false;
    }

    const requestedCharacterPackId = characterPort.getCurrentCharacterPackId();
    const legacyState = importLegacy && !careAuthorityImportedKeys.has(characterPort.getCharacterRuntimeKey(requestedCharacterPackId))
      ? cloneCareState(state.care)
      : null;
    try {
      const response = await platformPort.backendFetch(
        backendPort.buildBackendEndpointUrl("care_snapshot", "/desktop-pet/care/snapshot", { t: Date.now() }),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          cache: "no-store",
          body: JSON.stringify(buildCareAuthorityRequest({
            legacy_state: platformPort.isTauriRuntime ? legacyState : null
          }))
        }
      );
      if (!runtimeScope.isCurrent(requestScope)) return false;
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await readJsonResponse(response);
      if (!runtimeScope.isCurrent(requestScope)) return false;
      if (requestedCharacterPackId !== characterPort.getCurrentCharacterPackId()) return false;
      if (!payload?.ok || !applyAuthoritativeCareSnapshot(payload.snapshot, { characterPackId: requestedCharacterPackId })) {
        markCareAuthorityUnavailable(payload?.reason || "invalid_care_snapshot");
        if (!silent) rejectDisabledCareAction();
        return false;
      }
      return true;
    } catch {
      if (!runtimeScope.isCurrent(requestScope)) return false;
      if (requestedCharacterPackId !== characterPort.getCurrentCharacterPackId()) return false;
      markCareAuthorityUnavailable("care_snapshot_failed");
      if (!silent) rejectDisabledCareAction();
      return false;
    }
  }

  async function performDesktopCareAction(action, { itemId = "", silent = false } = {}) {
    const requestScope = runtimeScope.capture();
    if (!isCareRuntimeActive()) {
      const refreshed = await refreshDesktopCareState({ importLegacy: true, silent: true });
      if (!runtimeScope.isCurrent(requestScope)) return { ok: false, status: "cancelled", reason: "care_scope_changed" };
      if (!refreshed) {
        if (!silent) rejectDisabledCareAction();
        return null;
      }
    }
    const requestedCharacterPackId = characterPort.getCurrentCharacterPackId();
    try {
      const response = await platformPort.backendFetch(
        backendPort.buildBackendEndpointUrl("care_action", "/desktop-pet/care/action", { t: Date.now() }),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          cache: "no-store",
          body: JSON.stringify(buildCareAuthorityRequest({
            action: String(action || "").trim(),
            request_id: globalThis.crypto?.randomUUID?.() || `care-${Date.now()}-${Math.random().toString(36).slice(2)}`,
            item_id: String(itemId || "").trim()
          }))
        }
      );
      if (!runtimeScope.isCurrent(requestScope)) return { ok: false, status: "cancelled", reason: "care_scope_changed" };
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await readJsonResponse(response);
      if (!runtimeScope.isCurrent(requestScope)) return { ok: false, status: "cancelled", reason: "care_scope_changed" };
      if (requestedCharacterPackId !== characterPort.getCurrentCharacterPackId()) return null;
      if (payload?.snapshot) {
        applyAuthoritativeCareSnapshot(payload.snapshot, { characterPackId: requestedCharacterPackId });
      }
      return payload && typeof payload === "object" ? payload : null;
    } catch {
      if (!runtimeScope.isCurrent(requestScope)) return { ok: false, status: "cancelled", reason: "care_scope_changed" };
      if (requestedCharacterPackId !== characterPort.getCurrentCharacterPackId()) return null;
      markCareAuthorityUnavailable("care_action_failed");
      if (!silent) rejectDisabledCareAction();
      return null;
    }
  }

  function describeCareActionFailure(result) {
    const reason = String(result?.reason || result?.status || "").trim();
    if (reason === "insufficient_coins") return "钱不够啦。";
    if (reason === "item_not_in_inventory") return "背包里没有这个。";
    if (reason === "hunger_too_low") return "她有点饿，先喂点东西吧。";
    if (reason === "energy_too_low") return "她现在没什么精神，先休息或投喂一下吧。";
    if (reason === "work_already_active") return "她已经出门啦。";
    if (reason === "work_not_due") {
      const seconds = Math.max(1, Math.ceil(Number(result?.remaining_ms || 0) / 1000));
      return `还要约 ${seconds} 秒才回来。`;
    }
    if (reason === "coins_not_low_enough") {
      return `金币低于 ${getProfileCareConfig().allowance.maxCoins} 时才能领取补给。`;
    }
    if (reason === "allowance_cooldown") {
      const seconds = Math.max(1, Math.ceil(Number(result?.remaining_ms || 0) / 1000));
      return `补给还在冷却，约 ${seconds} 秒后可以领取。`;
    }
    if (reason === "work_not_configured") return "这个角色还没有配置外出。";
    if (reason === "allowance_not_configured") return "这个角色还没有配置补给。";
    if (reason === "work_not_active") return "她现在没有外出。";
    if (reason === "item_not_available") return "这个商品暂时买不了。";
    return "养成操作暂时没有完成。";
  }

  let careWorkTimer = 0;

  let carePassiveTimer = 0;

  let careAwayClickThrough = false;

  function scheduleCarePassiveTick() {
    lifecycle.clearTimeout(carePassiveTimer);
    carePassiveTimer = 0;
    if (!isCareRuntimeActive()) return;
    carePassiveTimer = lifecycle.setTimeout(async () => {
      await refreshDesktopCareState({ silent: true });
    }, CARE_PASSIVE_TICK_MS);
  }

  function buildDesktopCareContext() {
    if (!isCareRuntimeActive()) return null;
    const config = getProfileCareConfig();
    if (!config.enabled) return null;
    const care = normalizeCareState(state.care, config);
    return {
      enabled: true,
      now: Date.now(),
      hunger: care.hunger,
      energy: care.energy,
      affection: care.affection,
      coins: care.coins,
      work_task_active: Boolean(care.workTask),
      thresholds: {
        hunger_low: 25,
        hunger_critical: 12,
        energy_low: 25,
        energy_critical: 12,
        affection_warm: 45,
        affection_close: 75
      }
    };
  }

  async function buyShopItem(itemId) {
    if (!isCareConfiguredForCurrentCharacter()) return rejectDisabledCareAction();
    const item = findCareShopItem(itemId);
    if (!item) {
      notifyShopStatus("这个商品暂时买不了。", "error");
      return false;
    }
    const result = await performDesktopCareAction("buy", { itemId: item.id });
    if (!result?.ok) {
      const message = describeCareActionFailure(result);
      notifyShopStatus(message, "warn");
      bubblePort.showBubbleText(message, { transient: true, durationMs: 1600, kind: "shop" });
      return false;
    }
    notifyShopStatus(`买到了：${item.name}`, "ok");
    bubblePort.showBubbleText(`买到了 ${item.name}。`, { transient: true, durationMs: 1600, kind: "shop" });
    return true;
  }

  async function feedInventoryItem(itemId) {
    if (!isCareConfiguredForCurrentCharacter()) return rejectDisabledCareAction();
    const item = findCareShopItem(itemId);
    if (!item) {
      notifyShopStatus("这个商品暂时不能使用。", "error");
      return false;
    }
    const result = await performDesktopCareAction("feed", { itemId: item.id });
    if (!result?.ok) {
      const message = describeCareActionFailure(result);
      notifyShopStatus(message, "warn");
      bubblePort.showBubbleText(message, { transient: true, durationMs: 1600, kind: "shop" });
      return false;
    }
    const care = normalizeCareState(result.snapshot, getProfileCareConfig());
    const effects = result.effects_applied && typeof result.effects_applied === "object"
      ? result.effects_applied
      : {};
    const hungerDelta = Number(effects.hunger || 0);
    const affectionDelta = Number(effects.affection || 0);
    const energyDelta = Number(effects.energy || 0);
    applyCareFeedback(item);
    notifyShopStatus(`投喂了：${item.name}`, "ok");
    void sendCareFeedReply({ item, care, hungerDelta, energyDelta, affectionDelta });
    return true;
  }

  async function sendCareFeedReply({ item, care, hungerDelta, energyDelta, affectionDelta }) {
    if (!isCareRuntimeActive()) return;
    if (chatPort.sending || ttsPort.ttsActive || bubblePort.replyDisplayActive) return;
    const itemName = String(item?.name || "").trim();
    if (!itemName) return;
    const turnToken = chatPort.beginTurn("care_feed", { itemName });
    bubblePort.resetReplyIdentity();
    ttsPort.resetStreamingTtsState(turnToken);
    attachmentsPort.resetFileDeliveryTracking();
    settingsPort.scheduleSettingsSnapshot();

    try {
      if (resourceState.health !== "online") {
        const healthy = await characterPort.reloadCharacterResources({ silent: true });
        if (!healthy) return;
      }
      if (!chatPort.isTurnActive(turnToken)) return;
      const itemDescription = String(item?.description || "").replace(/\s+/gu, " ").trim().slice(0, 120);
      const message = [
        `刚才发生的互动：我投喂了你「${itemName}」。`,
        `状态变化：饥饿 ${formatSignedCareDelta(hungerDelta)}，精力 ${formatSignedCareDelta(energyDelta)}，好感 ${formatSignedCareDelta(affectionDelta)}。`,
        `当前状态：饥饿 ${care.hunger}/100，精力 ${care.energy}/100，好感 ${care.affection}/100。`,
        itemDescription ? `商品介绍：${itemDescription}` : ""
      ].filter(Boolean).join("\n");
      const stream = chatPort.sendThinkStream(message, turnToken, {
        turnKind: "desktop_pet_care_feed"
      });
      await chatPort.processThinkStream(stream, turnToken);
    } catch (error) {
      if (!chatPort.isTurnActive(turnToken) || chatPort.isAbortLike(error)) return;
      statusPort.setRuntimeStatus(`投喂回复暂时失败：${formatError(error)}`, { mode: "error" });
    } finally {
      chatPort.markTurnLatency("turn-finished");
      if (chatPort.isTurnActive(turnToken)) {
        chatPort.endTurn(turnToken);
        if (state.currentEmotion === characterPort.resolveEmotionEntry("thinking")?.id) {
          musicPort.setRestingPetEmotion();
        }
        if (!els.bubble.classList.contains("visible")) {
          bubblePort.setPetMotion("idle");
        }
        musicPort.scheduleMusicEmotionRestore();
        statusPort.updateActivityControls();
        settingsPort.scheduleSettingsSnapshot();
      }
      chatPort.finishTurnLatencyTrace(turnToken);
    }
  }

  async function startCareWork() {
    if (!isCareConfiguredForCurrentCharacter()) return rejectDisabledCareAction();
    const config = getProfileCareConfig();
    if (!config.enabled || !config.work.enabled) {
      notifyShopStatus("这个角色还没有配置外出。", "error");
      return false;
    }
    const result = await performDesktopCareAction("start_work");
    if (!result?.ok) {
      const message = describeCareActionFailure(result);
      notifyShopStatus(message, "warn");
      bubblePort.showBubbleText(message, { transient: true, durationMs: 2000, kind: "work" });
      return false;
    }
    applyCareFeedback(config.work.startFeedback, {
      fallbackText: "我出去转一圈，很快回来。",
      kind: "work",
      durationMs: 1800
    });
    syncCareAwayVisualState();
    scheduleCareWorkCompletion();
    notifyShopStatus("她出门啦，等一会儿就回来。", "ok");
    return true;
  }

  async function claimCareAllowance() {
    if (!isCareConfiguredForCurrentCharacter()) return rejectDisabledCareAction();
    const config = getProfileCareConfig();
    const allowance = config.allowance;
    if (!config.enabled || !allowance.enabled) {
      notifyShopStatus("这个角色还没有配置补给。", "error");
      return false;
    }
    const result = await performDesktopCareAction("claim_allowance");
    if (!result?.ok) {
      const message = describeCareActionFailure(result);
      notifyShopStatus(message, "warn");
      bubblePort.showBubbleText(message, { transient: true, durationMs: 1800, kind: "shop" });
      return false;
    }
    const grant = Math.max(0, Math.round(Number(result.coins_granted || 0)));
    applyCareFeedback(allowance.feedback, {
      fallbackText: `拿到 ${grant} 枚应急金币。`,
      replacements: { coins: grant },
      kind: "shop",
      durationMs: 1800
    });
    notifyShopStatus(`领取补给：+${grant} 金币`, "ok");
    return true;
  }

  async function settleCareWorkIfDue({ silent = false } = {}) {
    if (!isCareRuntimeActive()) return false;
    const config = getProfileCareConfig();
    const care = normalizeCareState(state.care, config);
    if (!care.workTask) {
      scheduleCareWorkCompletion();
      return false;
    }
    const result = await performDesktopCareAction("settle_work", { silent: true });
    if (!result?.ok) {
      if (result?.reason === "work_not_due") scheduleCareWorkCompletion();
      else if (!silent) notifyShopStatus(describeCareActionFailure(result), "warn");
      return false;
    }
    const reward = Math.max(0, Math.round(Number(result.reward_coins || 0)));
    syncCareAwayVisualState();
    applyCareFeedback(config.work.completeFeedback, {
      fallbackText: `我回来啦，带回 ${reward} 枚金币。`,
      replacements: { reward },
      kind: "work",
      durationMs: 2200
    });
    notifyShopStatus(`外出完成：+${reward} 金币`, "ok");
    return true;
  }

  function scheduleCareWorkCompletion() {
    lifecycle.clearTimeout(careWorkTimer);
    careWorkTimer = 0;
    if (!isCareRuntimeActive()) {
      stopCareRuntime();
      return;
    }
    const care = normalizeCareState(state.care, getProfileCareConfig());
    const task = care.workTask;
    syncCareAwayVisualState(care);
    if (!task) return;
    const delay = Math.max(0, task.completeAt - Date.now());
    careWorkTimer = lifecycle.setTimeout(() => {
      void settleCareWorkIfDue();
    }, Math.min(delay, 2147483647));
  }

  function syncCareAwayVisualState(care = normalizeCareState(state.care, getProfileCareConfig())) {
    if (!isCareRuntimeActive()) {
      if (els?.stage) els.stage.classList.remove("is-away");
      void setCareAwayClickThrough(false);
      return;
    }
    const away = Boolean(care.workTask);
    els.stage.classList.toggle("is-away", away);
    void setCareAwayClickThrough(away);
    if (away) {
      inputPort.hideChatInput();
      els.bubble.classList.remove("visible");
    }
  }

  async function setCareAwayClickThrough(enabled) {
    const next = Boolean(enabled);
    if (careAwayClickThrough === next) return;
    careAwayClickThrough = next;
    state.clickThrough = next;
    if (!platformPort.isTauriRuntime) return;
    try {
      await platformPort.invoke("set_click_through", { enabled: next });
    } catch {
      careAwayClickThrough = !next;
      state.clickThrough = !next;
    }
  }

  function applyCareFeedback(item) {
    const feedback = item?.feedback || item || {};
    if (feedback.emotion) {
      characterPort.setTransientEmotion(feedback.emotion, { durationMs: 2200 });
    }
    const fallbackText = arguments[1]?.fallbackText || `${item.name}，收下啦。`;
    const replacements = arguments[1]?.replacements || {};
    const text = formatCareFeedbackText(String(feedback.bubble?.text || fallbackText).trim(), replacements);
    const durationMs = Math.max(1000, Number(feedback.bubble?.durationMs || arguments[1]?.durationMs || 1800));
    bubblePort.showBubbleText(text, { transient: true, durationMs, local: true, kind: arguments[1]?.kind || "feed" });
  }

  function notifyShopStatus(message, tone = "info") {
    void platformPort.emitTo("shop", SHOP_STATUS_EVENT, { message, tone, t: Date.now() }).catch(() => {});
  }

  function findCareShopItem(itemId) {
    if (!isCareConfiguredForCurrentCharacter()) return null;
    const id = String(itemId || "").trim();
    return getProfileCareConfig().shopItems.find((item) => item.id === id) || null;
  }

  function resetCareAuthority(characterPackId) {
    Object.assign(careAuthority, { status: "unresolved", characterPackId, reason: "character_changed" });
    musicPort.scheduleMusicEmotionRestore({ delayMs: 0 });
  }

  function hasImportedCareState(key) { return careAuthorityImportedKeys.has(key); }

  return {
    resetCareAuthority,
    hasImportedCareState,
    getCareFeatureStatus,
    isCareRuntimeActive,
    applyCareFeatureStatus,
    stopCareRuntime,
    rejectDisabledCareAction,
    markCareAuthorityUnavailable,
    applyAuthoritativeCareSnapshot,
    refreshDesktopCareState,
    buildDesktopCareContext,
    buyShopItem,
    feedInventoryItem,
    startCareWork,
    claimCareAllowance,
    notifyShopStatus
  };
}
