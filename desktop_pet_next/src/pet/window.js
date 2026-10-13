import { PET_DRAG_THRESHOLD_PX, calculateDragVelocity, PET_DRAG_VELOCITY_FACTOR, PET_THROW_THRESHOLD, PET_PHYSICS_FRAME_MS, stepPetPhysics, PET_WALL_PAIN_THRESHOLD, normalizeMenuAnchor, resolveMenuAnchorPoint, calculateMenuPosition, MENU_VIEWPORT_MARGIN, rectToPhysical, isUsableRect, buildPetPolygonPoints, PET_HIT_POLYGON, buildOverlayClipPath } from "../desktop-window-physics.js";
import { getActiveCharacterProfile } from "../character-profile.js";
import { isCareFeatureEnabled } from "../care-feature.js";
import { PET_MOTION_RESTORE_MS, PET_IDLE_JUMP_AFTER_MS, PET_IDLE_JUMP_COOLDOWN_MS, SCALE_MIN, SCALE_MAX, SCALE_PRESETS, OPACITY_PRESETS } from './constants.js';
import { getProfileDefaultOutfit, getProfileDefaultEmotion, getProfileCareConfig } from './profile.js';
import { formatError, clamp, friendlyErrorMessage } from './utils.js';
import { createPortraitHitMaskCache, containedPortraitRect, alphaMaskHitRegions } from '../portrait-hit-regions.js';

/** Owns window behavior and its temporary runtime state. */
export function createWindowController({
  state,
  resourceState,
  els,
  visualRenderer,
  platform: platformPort,
  lifecycle,
  interaction: interactionPort,
  bubble: bubblePort,
  persistence: statePort,
  chat: chatPort,
  tts: ttsPort,
  character: characterPort,
  voice: voicePort,
  music: musicPort,
  attachments: attachmentsPort,
  input: inputPort,
  status: statusPort,
  settings: settingsPort,
  care: carePort,
  webgl: webglPort
}) {
  const { window, document } = platformPort;
  const portraitHitMasks = createPortraitHitMaskCache({
    document, onUpdate: () => scheduleNativeHitTestSync(),
    resolveSource: source => {
      if (!platformPort.isTauriRuntime) return source;
      const entry = characterPort.resolveEmotionEntry(els.petImage.dataset.emotion);
      // A pending character/outfit switch must not use the next pack's pixels for the old image.
      if (!entry?.url || new URL(entry.url, window.location?.href).href !== source) return source;
      return characterPort.getLocalPortraitUrl(entry.id) || source;
    }
  });
  let portraitResourceRevision = resourceState.loadedAt;
  lifecycle.track(() => portraitHitMasks.clear());

  const playState = {
    mode: "idle",
    vx: 0,
    vy: 0,
    heldEmotion: "",
    lastWallHitAt: 0,
    lastLandAt: 0,
    lastIdleJumpAt: 0
  };

  let dragState = null;

  let physicsTimer = 0;

  let physicsTickInFlight = false;

  let monitorBoundsCache = null;

  let monitorBoundsCacheAt = 0;

  let lastWindowGeometry = null;

  let idleJumpTimer = 0;

  let lastUserPetInteractionAt = Date.now();

  let suppressClickUntil = 0;

  let hitSyncFrame = 0;

  let pendingHitSyncForce = false;

  let lastHitRegionSignature = "";

  let menuAnchor = null;

  let lastAppliedLayoutSignature = "";

  let musicDropHover = false;

  function beginManualDrag(event) {
    if (!platformPort.isTauriRuntime) return;
    markPetInteraction();
    stopPetPhysics({ restore: false, reschedule: false });
    interactionPort.cancelLocalClick();
    bubblePort.setPetMotion("dragging");
    dragState = {
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      lastScreenX: event.screenX,
      lastScreenY: event.screenY,
      lastMoveAt: Date.now(),
      vx: 0,
      vy: 0,
      moved: false
    };

    try {
      els.hitbox.setPointerCapture(event.pointerId);
    } catch {
      // Pointer capture may be unavailable while the webview is losing focus.
    }
  }

  async function continueManualDrag(event) {
    if (!dragState || event.pointerId !== dragState.pointerId) return;

    const totalDx = event.clientX - dragState.startClientX;
    const totalDy = event.clientY - dragState.startClientY;
    if (!dragState.moved && Math.hypot(totalDx, totalDy) < PET_DRAG_THRESHOLD_PX) return;

    dragState.moved = true;
    interactionPort.cancelLocalClick();
    bubblePort.hideBubble();
    bubblePort.setPetMotion("dragging");
    const dx = Math.round(event.screenX - dragState.lastScreenX);
    const dy = Math.round(event.screenY - dragState.lastScreenY);
    const now = Date.now();
    const vel = calculateDragVelocity({
      dx,
      dy,
      dt: now - dragState.lastMoveAt,
      factor: PET_DRAG_VELOCITY_FACTOR
    });
    if (vel.vx || vel.vy) {
      dragState.vx = vel.vx;
      dragState.vy = vel.vy;
      dragState.lastMoveAt = now;
    }
    if (!dx && !dy) return;

    dragState.lastScreenX = event.screenX;
    dragState.lastScreenY = event.screenY;
    event.preventDefault();

    const geometry = await platformPort.tauriCall("move_window_by", { dx, dy }, { quiet: true });
    if (geometry) {
      applyWindowGeometryToState(geometry);
      statePort.scheduleSave(250);
    }
  }

  function handlePetPointerUp(event) {
    const result = endManualDrag(event);
    const moved = Boolean(result?.moved);
    if (moved) {
      suppressClickUntil = Date.now() + 350;
      startPetThrow(result);
    }
  }

  function endManualDrag(event) {
    if (!dragState) return { moved: false, vx: 0, vy: 0 };
    const ended = dragState;
    const pointerId = ended.pointerId;
    const moved = Boolean(ended.moved);
    dragState = null;
    try {
      if (event?.currentTarget?.hasPointerCapture?.(pointerId)) {
        event.currentTarget.releasePointerCapture(pointerId);
      }
    } catch {
      // Nothing to release.
    }
    if (!moved) {
      bubblePort.setPetMotion("idle");
      releasePetPlayEmotion({ delayMs: 0 });
    }
    return {
      moved,
      vx: Number(ended.vx) || 0,
      vy: Number(ended.vy) || 0
    };
  }

  function markPetInteraction() {
    lastUserPetInteractionAt = Date.now();
    scheduleIdleJump();
  }

  function startPetThrow({ vx = 0, vy = 0 } = {}) {
    markPetInteraction();
    playState.vx = Number(vx) || 0;
    playState.vy = Number(vy) || 0;

    if (Math.hypot(playState.vx, playState.vy) <= 1) {
      bubblePort.setPetMotion("idle");
      releasePetPlayEmotion({ delayMs: 0 });
      return;
    }

    playState.mode = "physics";
    const isFastThrow = Math.abs(playState.vx) > PET_THROW_THRESHOLD || Math.abs(playState.vy) > PET_THROW_THRESHOLD;
    const feedback = getProfilePlayFeedback(isFastThrow ? "throwFast" : "throwLight");
    bubblePort.setPetMotion(isFastThrow ? "thrown" : "drag-release", { durationMs: isFastThrow ? 0 : 420 });
    holdPetPlayEmotion(feedback.emotion);
    showPetPlayBubble(feedback.bubble.text, { durationMs: feedback.bubble.durationMs });

    startPetPhysicsLoop();
  }

  function startPetPhysicsLoop() {
    if (physicsTimer || !platformPort.isTauriRuntime) return;
    physicsTimer = lifecycle.setInterval(() => {
      void tickPetPhysics();
    }, PET_PHYSICS_FRAME_MS);
  }

  function stopPetPhysics({ restore = true, reschedule = true } = {}) {
    lifecycle.clearInterval(physicsTimer);
    physicsTimer = 0;
    playState.mode = "idle";
    playState.vx = 0;
    playState.vy = 0;
    if (restore && !chatPort.sending && !ttsPort.ttsActive) {
      bubblePort.setPetMotion("idle");
      releasePetPlayEmotion({ delayMs: 0 });
    }
    if (reschedule) {
      statePort.scheduleSave(250);
      scheduleIdleJump();
    }
  }

  async function tickPetPhysics() {
    if (!physicsTimer || dragState || !platformPort.isTauriRuntime) return;
    if (physicsTickInFlight) return;
    physicsTickInFlight = true;
    try {
      const geometry = await getCachedWindowGeometry();
      if (!geometry) {
        stopPetPhysics({ restore: true });
        return;
      }

      const bounds = await getCurrentWorkAreaBounds(geometry);
      const step = stepPetPhysics({
        x: geometry.x,
        y: geometry.y,
        vx: playState.vx,
        vy: playState.vy,
        width: geometry.width,
        height: geometry.height,
        bounds
      });

      if (step.hitFloor && step.floorBounce) {
        bubblePort.setPetMotion("land", { durationMs: 420 });
        handlePetLand();
      }

      const dx = Math.round(step.nextX - geometry.x);
      const dy = Math.round(step.nextY - geometry.y);
      if (dx || dy) {
        const moved = await platformPort.tauriCall("move_window_by", { dx, dy }, { quiet: true });
        if (moved) {
          applyWindowGeometryToState(moved);
        } else {
          stopPetPhysics({ restore: true });
          return;
        }
      } else {
        lastWindowGeometry = { ...geometry, x: Math.round(step.nextX), y: Math.round(step.nextY) };
      }

      playState.vx = step.nextVx;
      playState.vy = step.nextVy;

      if (step.hitWall) {
        handlePetWallHit(step.wallImpactSpeed);
      }

      if (step.settledOnFloor) {
        stopPetPhysics({ restore: true });
      }
    } finally {
      physicsTickInFlight = false;
    }
  }

  async function getCachedWindowGeometry() {
    if (!platformPort.isTauriRuntime) return null;
    if (lastWindowGeometry?.width && lastWindowGeometry?.height) return lastWindowGeometry;
    const geometry = await platformPort.tauriCall("get_window_geometry", {}, { quiet: true });
    if (geometry) applyWindowGeometryToState(geometry);
    return lastWindowGeometry;
  }

  async function getCurrentWorkAreaBounds(geometry = lastWindowGeometry) {
    const now = Date.now();
    if (monitorBoundsCache && now - monitorBoundsCacheAt < 3000) return monitorBoundsCache;

    const monitor = await platformPort.currentMonitor().catch(() => null) || await platformPort.primaryMonitor().catch(() => null);
    const workArea = monitor?.workArea;
    const position = workArea?.position || monitor?.position || {};
    const size = workArea?.size || monitor?.size || {};
    const left = Number(position.x);
    const top = Number(position.y);
    const width = Number(size.width);
    const height = Number(size.height);

    if (Number.isFinite(left) && Number.isFinite(top) && Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0) {
      monitorBoundsCache = {
        left,
        top,
        right: left + width,
        bottom: top + height
      };
    } else {
      const x = Number(geometry?.x) || 0;
      const y = Number(geometry?.y) || 0;
      const fallbackWidth = Number(window.screen?.availWidth || window.innerWidth || 1280);
      const fallbackHeight = Number(window.screen?.availHeight || window.innerHeight || 720);
      monitorBoundsCache = {
        left: Math.min(0, x),
        top: Math.min(0, y),
        right: Math.max(fallbackWidth, x + (geometry?.width || 0)),
        bottom: Math.max(fallbackHeight, y + (geometry?.height || 0))
      };
    }
    monitorBoundsCacheAt = now;
    return monitorBoundsCache;
  }

  function handlePetWallHit(impactSpeed) {
    const now = Date.now();
    if (now - playState.lastWallHitAt < 300) return;
    playState.lastWallHitAt = now;
    markPetInteraction();
    bubblePort.setPetMotion("hit-wall", { durationMs: 520 });

    if (Math.abs(impactSpeed) > PET_WALL_PAIN_THRESHOLD) {
      const feedback = getProfilePlayFeedback("wallHit");
      holdPetPlayEmotion(feedback.emotion);
      showPetPlayBubble(feedback.bubble.text, { durationMs: feedback.bubble.durationMs });
    }
  }

  function handlePetLand() {
    const now = Date.now();
    if (now - playState.lastLandAt < 400) return;
    playState.lastLandAt = now;
    const feedback = getProfilePlayFeedback("land");
    holdPetPlayEmotion(feedback.emotion);
    showPetPlayBubble(feedback.bubble.text, { durationMs: feedback.bubble.durationMs });
  }

  function showPetPlayBubble(text, { durationMs = 1800 } = {}) {
    if (!String(text || "").trim() || durationMs <= 0) return;
    bubblePort.showBubbleText(text, { transient: true, durationMs, local: true, kind: "play" });
  }

  function holdPetPlayEmotion(emotion) {
    const value = String(emotion || "").trim();
    if (!value) return;
    playState.heldEmotion = value;
    characterPort.clearTransientEmotionRestore();
    characterPort.setPetEmotion(value, { persist: false });
  }

  function getProfilePlayFeedback(kind) {
    const feedback = getActiveCharacterProfile()?.playFeedback || {};
    const entry = feedback[kind] && typeof feedback[kind] === "object" ? feedback[kind] : {};
    const bubble = entry.bubble && typeof entry.bubble === "object" ? entry.bubble : {};
    return {
      emotion: String(entry.emotion || "").trim(),
      bubble: {
        text: String(bubble.text || "").trim(),
        durationMs: Math.max(0, Number(bubble.durationMs) || 0)
      }
    };
  }

  function releasePetPlayEmotion({ delayMs = PET_MOTION_RESTORE_MS } = {}) {
    const held = playState.heldEmotion;
    if (!held) return;
    lifecycle.setTimeout(() => {
      if (playState.heldEmotion !== held) return;
      if (chatPort.sending || ttsPort.ttsActive || voicePort.voiceInputState === "recording" || dragState || physicsTimer) return;
      playState.heldEmotion = "";
      musicPort.setRestingPetEmotion();
      musicPort.scheduleMusicEmotionRestore();
    }, Math.max(0, Number(delayMs) || 0));
  }

  function scheduleIdleJump() {
    lifecycle.clearTimeout(idleJumpTimer);
    if (!platformPort.isTauriRuntime) return;
    idleJumpTimer = lifecycle.setTimeout(() => {
      if (Date.now() - lastUserPetInteractionAt < PET_IDLE_JUMP_AFTER_MS) {
        scheduleIdleJump();
        return;
      }
      maybePlayIdleJump();
      scheduleIdleJump();
    }, PET_IDLE_JUMP_AFTER_MS);
  }

  function maybePlayIdleJump() {
    const now = Date.now();
    if (now - playState.lastIdleJumpAt < PET_IDLE_JUMP_COOLDOWN_MS) return;
    if (chatPort.sending || ttsPort.ttsActive || ttsPort.ttsQueue.length > 0 || bubblePort.replyDisplayActive || !els.chatForm.hidden || !els.menu.hidden) return;
    if (dragState || physicsTimer) return;

    playState.lastIdleJumpAt = now;
    bubblePort.setPetMotion("jump", { durationMs: 920 });
  }

  async function registerWindowListeners() {
    if (!platformPort.appWindow) return;

    lifecycle.track(await platformPort.appWindow.onMoved(({ payload }) => {
      state.x = Math.round(payload.x);
      state.y = Math.round(payload.y);
      statePort.scheduleSave(250);
    }));

    lifecycle.track(await platformPort.appWindow.onResized(({ payload }) => {
      void payload;
      state.width = null;
      state.height = null;
      scheduleNativeHitTestSync({ force: true });
      statePort.scheduleSave(250);
    }));


  }

  async function registerFileDropHandlers() {
    if (!platformPort.appWindow?.onDragDropEvent) return;
    lifecycle.track(await platformPort.appWindow.onDragDropEvent((event) => {
      const payload = event?.payload || {};
      const type = String(payload.type || "").toLowerCase();
      if (type === "drop") {
        musicDropHover = false;
        void attachmentsPort.handleDroppedFiles(payload.paths || []);
        return;
      }
      if (type === "over" || type === "enter") {
        bubblePort.showFileDropHint();
        return;
      }
      musicDropHover = false;
    }));
  }

  function applyVisualState() {
    document.documentElement.style.setProperty("--pet-scale", String(state.scale));
    document.documentElement.style.setProperty("--pet-opacity", String(state.opacity));
    els.scale.value = String(state.scale);
    els.opacity.value = String(state.opacity);
    els.scaleOutput.value = `${Math.round(state.scale * 100)}%`;
    els.opacityOutput.value = `${Math.round(state.opacity * 100)}%`;
    if (els.voicePlayer) els.voicePlayer.volume = state.voiceVolume;
    els.backendUrl.value = state.backendUrl;
    els.outfit.value = state.outfit || getProfileDefaultOutfit();
    els.stage.classList.toggle("show-hitbox-overlay", state.hitboxOverlay);
    applyCharacterLayout();
    voicePort.updateVoiceRecordButton();
    updateMenuLabels();
    scheduleNativeHitTestSync();
    inputPort.autoResizeChatInput();
  }

  function applyCharacterLayout() {
    const profile = getActiveCharacterProfile();
    const layouts = profile?.layout?.outfits && typeof profile.layout.outfits === "object"
      ? profile.layout.outfits
      : {};
    const outfit = String(state.outfit || getProfileDefaultOutfit()).trim() || getProfileDefaultOutfit();
    const outfitLayout = layouts[outfit] || layouts[getProfileDefaultOutfit()];
    if (!outfitLayout) {
      visualRenderer.setLayout(null);
      lastAppliedLayoutSignature = "";
      return;
    }

    visualRenderer.setLayout(outfitLayout);

    /* apply calibrated window size — only when dimensions change */
    const winW = Number(outfitLayout.window?.width) || 0;
    const winH = Number(outfitLayout.window?.height) || 0;
    const sig = `${characterPort.getCurrentCharacterPackId()}::${outfit}::${winW}x${winH}`;
    if (winW >= 200 && winH >= 200 && sig !== lastAppliedLayoutSignature) {
      lastAppliedLayoutSignature = sig;
      if (platformPort.isTauriRuntime) {
        void platformPort.invoke("resize_pet_window", { width: winW, height: winH }).catch((error) => {
          statusPort.setStatus(`窗口校准尺寸应用失败：${formatError(error)}`, { durationMs: 2400 });
        });
      }
    }
  }

  const LAYOUT_RESIZE_MAX_WIDTH = 1200;

  const LAYOUT_RESIZE_MAX_HEIGHT = 1600;

  function scheduleNativeWindowStateApply({ forceHitTest = false } = {}) {
    if (!platformPort.isTauriRuntime) return;
    lifecycle.setTimeout(() => {
      void applyNativeWindowState({ forceHitTest }).catch((error) => {
        statusPort.setStatus(`窗口状态应用失败：${formatError(error)}`, { durationMs: 2400 });
      });
    }, 0);
  }

  async function applyNativeWindowState({ forceHitTest = false } = {}) {
    const geometry = await platformPort.invoke("apply_window_state", { state });
    if (geometry) applyWindowGeometryToState(geometry);
    await applyCharacterLayoutResize();
    scheduleNativeHitTestSync({ force: forceHitTest });
  }

  async function applyCharacterLayoutResize() {
    if (!platformPort.isTauriRuntime) return;
    const profile = getActiveCharacterProfile();
    const layouts = profile?.layout?.outfits && typeof profile.layout.outfits === "object"
      ? profile.layout.outfits : {};
    const outfit = String(state.outfit || getProfileDefaultOutfit()).trim() || getProfileDefaultOutfit();
    const outfitLayout = layouts[outfit] || layouts[getProfileDefaultOutfit()];
    if (!outfitLayout) return;
    let winW = Number(outfitLayout.window?.width) || 0;
    let winH = Number(outfitLayout.window?.height) || 0;
    if (winW >= 200 && winH >= 200) {
      winW = Math.min(winW, LAYOUT_RESIZE_MAX_WIDTH);
      winH = Math.min(winH, LAYOUT_RESIZE_MAX_HEIGHT);
      await platformPort.invoke("resize_pet_window", { width: winW, height: winH }).catch((error) => {
        statusPort.setStatus(`窗口校准尺寸应用失败：${formatError(error)}`, { durationMs: 2400 });
      });
      lastAppliedLayoutSignature = `${characterPort.getCurrentCharacterPackId()}::${outfit}::${winW}x${winH}`;
    }
  }

  function updateMenuLabels() {
    els.taskbar.textContent = state.skipTaskbar ? "显示任务栏" : "隐藏任务栏";
    els.alwaysOnTop.textContent = state.alwaysOnTop ? "取消置顶" : "保持置顶";
    els.webgl.textContent = els.stage.classList.contains("show-webgl") ? "隐藏 WebGL" : "WebGL";
    els.hitTestToggle.textContent = state.hitTestEnabled ? "Hit-Test: on" : "Hit-Test: off";
    els.hitboxOverlayToggle.textContent = state.hitboxOverlay ? "Hitbox: on" : "Hitbox: off";
    if (els.menuSummary) {
      const outfit = characterPort.getActiveOutfit();
      const source = characterPort.resourceSourceLabel(resourceState.source);
      els.menuSummary.textContent = `${outfit.id || getProfileDefaultOutfit()} · ${source} · ${state.currentEmotion || getProfileDefaultEmotion()}`;
    }
    renderPresetChips();
    characterPort.renderResourceDetails();
    characterPort.renderEmotionGrid();
    repositionOpenMenu();
    settingsPort.scheduleSettingsSnapshot();
  }

  function updateVisualScale(value, { commitNow = false } = {}) {
    state.scale = clamp(Number(value), SCALE_MIN, SCALE_MAX);
    applyVisualState();
    if (commitNow) {
      lifecycle.clearTimeout(scaleTimer);
      void commitVisualScale();
    } else {
      scheduleScaleCommit();
    }
  }

  function updateVisualOpacity(value, { saveNow = false } = {}) {
    state.opacity = clamp(Number(value), 0.55, 1);
    applyVisualState();
    statePort.scheduleSave(saveNow ? 0 : undefined);
  }

  function renderPresetChips() {
    renderValueChips(els.scalePresets, SCALE_PRESETS, "scale", state.scale);
    renderValueChips(els.opacityPresets, OPACITY_PRESETS, "opacity", state.opacity);
  }

  function renderValueChips(container, values, dataKey, activeValue) {
    if (!container) return;
    const signature = `${dataKey}:${values.join(",")}:${activeValue}`;
    if (container.dataset.signature === signature) return;
    container.dataset.signature = signature;
    container.replaceChildren(
      ...values.map((value) => {
        const button = document.createElement("button");
        button.type = "button";
        button.dataset[dataKey] = String(value);
        button.textContent = `${Math.round(value * 100)}%`;
        button.classList.toggle("active", Math.abs(Number(activeValue) - value) < 0.001);
        return button;
      })
    );
  }

  function showMenu(anchor) {
    menuAnchor = normalizeMenuAnchor(anchor);
    els.menu.hidden = false;
    visualRenderer.syncIdleAnimation();
    els.menu.style.visibility = "hidden";
    statusPort.updateConnectionStatus();
    repositionOpenMenu();
    els.menu.style.visibility = "";
    scheduleNativeHitTestSync({ force: true });
  }

  function repositionOpenMenu() {
    if (!menuAnchor || els.menu.hidden) return;
    const point = resolveMenuAnchorPoint(menuAnchor);
    placeMenuInsideViewport(point.x, point.y);
  }

  function placeMenuInsideViewport(x, y) {
    const rect = els.menu.getBoundingClientRect();
    const pos = calculateMenuPosition({
      x,
      y,
      menuWidth: rect.width,
      menuHeight: rect.height,
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
      margin: MENU_VIEWPORT_MARGIN
    });
    els.menu.style.maxHeight = `${pos.maxHeight}px`;
    els.menu.style.left = `${pos.left}px`;
    els.menu.style.top = `${pos.top}px`;
  }

  function closeMenu() {
    menuAnchor = null;
    els.menu.hidden = true;
    visualRenderer.syncIdleAnimation();
    els.menu.style.visibility = "";
    scheduleNativeHitTestSync({ force: true });
  }

  function scheduleNativeHitTestSync({ force = false } = {}) {
    pendingHitSyncForce = pendingHitSyncForce || force;
    if (hitSyncFrame) return;

    hitSyncFrame = lifecycle.requestAnimationFrame(() => {
      const shouldForce = pendingHitSyncForce;
      pendingHitSyncForce = false;
      hitSyncFrame = 0;
      void syncNativeHitTest({ force: shouldForce });
    });
  }

  async function syncNativeHitTest({ force = false } = {}) {
    const regions = collectHitRegions();
    renderHitboxOverlay(regions);

    const signature = JSON.stringify({
      enabled: state.hitTestEnabled,
      regions
    });
    if (!force && signature === lastHitRegionSignature) return;
    lastHitRegionSignature = signature;

    if (!platformPort.isTauriRuntime) return;

    await platformPort.tauriCall("update_hit_regions", { regions }, { quiet: true });
    await platformPort.tauriCall("set_hit_test_enabled", { enabled: state.hitTestEnabled }, { quiet: true });
  }

  function collectHitRegions() {
    const regions = [];
    regions.push(...buildPetHitRegions());

    addElementHitRegion(regions, els.toggle, "debug-toggle");
    addElementHitRegion(regions, els.close, "close-button");
    if (els.bubble.classList.contains("visible")) {
      addElementHitRegion(regions, els.bubble, "speech-bubble");
    }
    if (!els.chatForm.hidden) {
      addElementHitRegion(regions, els.chatForm, "chat-form");
      const attachments = document.querySelector("#pending-attachments");
      if (attachments && !attachments.hidden) addElementHitRegion(regions, attachments, "pending-attachments");
    }
    if (!els.menu.hidden) addElementHitRegion(regions, els.menu, "debug-menu");

    return regions;
  }

  function buildPetHitRegions() {
    if (portraitResourceRevision !== resourceState.loadedAt) {
      portraitHitMasks.clear();
      portraitResourceRevision = resourceState.loadedAt;
    }
    const ratio = window.devicePixelRatio || 1;
    const mask = portraitHitMasks.get(els.petImage);
    if (mask && !mask.opaque) {
      const rect = containedPortraitRect(els.petImage.getBoundingClientRect(), els.petImage.naturalWidth, els.petImage.naturalHeight);
      if (rect.width > 0 && rect.height > 0) {
        els.stage.dataset.petHitShape = 'alpha';
        return alphaMaskHitRegions(mask, rect, ratio);
      }
    }
    // Opaque legacy portraits and an unavailable canvas retain a usable body target.
    els.stage.dataset.petHitShape = 'fallback';
    const rect = rectToPhysical(els.hitbox.getBoundingClientRect(), ratio);
    if (!isUsableRect(rect)) return [];

    return [{
      kind: "pet",
      rect,
      polygon: buildPetPolygonPoints(rect, PET_HIT_POLYGON)
    }];
  }

  function addElementHitRegion(regions, element, kind) {
    if (!element || element.hidden) return;

    const style = window.getComputedStyle(element);
    if (style.display === "none" || style.visibility === "hidden") return;

    const rect = rectToPhysical(element.getBoundingClientRect(), window.devicePixelRatio || 1);
    if (!isUsableRect(rect)) return;

    regions.push({ kind, rect, polygon: [] });
  }

  function renderHitboxOverlay(regions) {
    if (!els.hitboxOverlay) return;
    els.hitboxOverlay.replaceChildren();
    if (!state.hitboxOverlay) return;

    const ratio = window.devicePixelRatio || 1;
    for (const region of regions) {
      const marker = document.createElement("div");
      marker.className = "hitbox-overlay-shape";
      marker.dataset.kind = region.kind;
      marker.style.left = `${region.rect.x / ratio}px`;
      marker.style.top = `${region.rect.y / ratio}px`;
      marker.style.width = `${region.rect.width / ratio}px`;
      marker.style.height = `${region.rect.height / ratio}px`;

      const clipPath = buildOverlayClipPath(region);
      if (clipPath) marker.style.clipPath = clipPath;

      els.hitboxOverlay.append(marker);
    }
  }

  let scaleTimer = 0;

  function scheduleScaleCommit() {
    lifecycle.clearTimeout(scaleTimer);
    scaleTimer = lifecycle.setTimeout(commitVisualScale, 120);
  }

  async function commitVisualScale() {
    const geometry = await platformPort.tauriCall("set_visual_scale", { scale: state.scale });
    if (geometry) applyWindowGeometryToState(geometry);
    repositionOpenMenu();
    scheduleNativeHitTestSync({ force: true });
    statePort.scheduleSave(0);
  }

  function applyWindowGeometryToState(geometry) {
    if (!geometry || typeof geometry !== "object") return;
    lastWindowGeometry = {
      x: statePort.normalizeNullableInteger(geometry.x) ?? 0,
      y: statePort.normalizeNullableInteger(geometry.y) ?? 0,
      width: statePort.normalizePositiveInteger(geometry.width) ?? lastWindowGeometry?.width ?? Math.round(window.outerWidth || window.innerWidth || 340),
      height: statePort.normalizePositiveInteger(geometry.height) ?? lastWindowGeometry?.height ?? Math.round(window.outerHeight || window.innerHeight || 560)
    };
    state.x = statePort.normalizeNullableInteger(geometry.x);
    state.y = statePort.normalizeNullableInteger(geometry.y);
    state.width = null;
    state.height = null;
  }

  async function closePetWindow() {
    closeMenu();
    inputPort.hideChatInput();
    chatPort.interruptReply({ announce: false });
    await voicePort.cancelVoiceRecording();
    if (!platformPort.isTauriRuntime) {
      statusPort.setStatus("Close is Tauri only");
      return;
    }

    await statePort.saveNow();
    await platformPort.tauriCall("close_pet_app", {});
  }

  async function quitAkane() {
    chatPort.interruptReply({ announce: false });
    await voicePort.cancelVoiceRecording();
    await statePort.saveNow();
    statusPort.setRuntimeStatus("正在退出 Akane…", { mode: "idle" });
    try {
      await platformPort.invoke("quit_akane", {});
    } catch (error) {
      statusPort.setStatus(`退出失败：${friendlyErrorMessage(formatError(error))}`);
    }
  }

  async function openWorkspaceWindow() {
    closeMenu();
    if (!platformPort.isTauriRuntime) {
      statusPort.setStatus("手边物品窗口仅 Tauri 可用");
      return;
    }
    await statePort.saveNow();
    await platformPort.tauriCall("open_workspace_window", {});
    settingsPort.scheduleSettingsSnapshot(120);
  }

  async function openShopWindow() {
    closeMenu();
    if (!isCareFeatureEnabled(carePort.getCareFeatureStatus())) {
      carePort.rejectDisabledCareAction();
      return;
    }
    const careConfig = getProfileCareConfig();
    if (!careConfig.enabled || !careConfig.shopItems.length) {
      statusPort.setStatus("这个角色还没有配置商店。", { durationMs: 2200 });
      return;
    }
    if (!platformPort.isTauriRuntime) {
      statusPort.setStatus("商店窗口仅 Tauri 可用");
      return;
    }
    if (!carePort.isCareRuntimeActive() && !(await carePort.refreshDesktopCareState({ importLegacy: true }))) return;
    await carePort.refreshDesktopCareState({ silent: true });
    await statePort.saveNow();
    await platformPort.tauriCall("open_shop_window", {});
    settingsPort.scheduleSettingsSnapshot(120);
  }

  async function openWorkshopWindow() {
    closeMenu();
    if (!platformPort.isTauriRuntime) {
      statusPort.setStatus("角色工坊窗口仅 Tauri 可用");
      return;
    }
    await statePort.saveNow();
    await platformPort.tauriCall("open_workshop_window", {});
    settingsPort.scheduleSettingsSnapshot(120);
  }

  async function setAlwaysOnTop(enabled) {
    state.alwaysOnTop = Boolean(enabled);
    updateMenuLabels();
    await platformPort.tauriCall("set_always_on_top", { enabled: state.alwaysOnTop });
    statePort.scheduleSave(0);
  }

  async function setSkipTaskbar(enabled) {
    state.skipTaskbar = Boolean(enabled);
    updateMenuLabels();
    await platformPort.tauriCall("set_taskbar_visible", { visible: !state.skipTaskbar });
    statePort.scheduleSave(0);
  }

  async function setHitTestEnabled(enabled) {
    state.hitTestEnabled = Boolean(enabled);
    updateMenuLabels();
    await platformPort.tauriCall("set_hit_test_enabled", { enabled: state.hitTestEnabled });
    scheduleNativeHitTestSync({ force: true });
    statePort.scheduleSave(0);
  }

  function setHitboxOverlay(enabled) {
    state.hitboxOverlay = Boolean(enabled);
    applyVisualState();
    scheduleNativeHitTestSync({ force: true });
    statePort.scheduleSave(0);
  }

  function toggleWebglProbe() {
    const enabled = !els.stage.classList.contains("show-webgl");
    els.stage.classList.toggle("show-webgl", enabled);
    updateMenuLabels();
    settingsPort.scheduleSettingsSnapshot();
    if (enabled) webglPort.startWebglProbe();
  }

  async function resetWindowPlacement() {
    Object.assign(state, {
      x: null,
      y: null,
      width: null,
      height: null,
      scale: 1
    });
    applyVisualState();
    await platformPort.tauriCall("reset_window_geometry", {});
    await platformPort.tauriCall("set_hit_test_enabled", { enabled: state.hitTestEnabled });
    scheduleNativeHitTestSync({ force: true });
    statePort.scheduleSave(0);
    statusPort.setStatus("位置已重置");
  }

  async function resetVisuals() {
    state.scale = 1;
    state.opacity = 1;
    applyVisualState();
    await commitVisualScale();
    statePort.scheduleSave(0);
    statusPort.setStatus("大小和透明度已恢复默认。", { durationMs: 1800 });
  }

  async function probeClickThrough(durationMs) {
    if (!platformPort.isTauriRuntime) {
      statusPort.setStatus("仅 Tauri 可用");
      return;
    }

    statusPort.setStatus("临时穿透中");
    try {
      state.clickThrough = true;
      await platformPort.invoke("set_click_through", { enabled: true });
      lifecycle.setTimeout(async () => {
        state.clickThrough = false;
        await platformPort.invoke("set_click_through", { enabled: false });
        statusPort.setStatus("已恢复交互");
      }, durationMs);
    } catch (error) {
      state.clickThrough = false;
      statusPort.setStatus(`穿透失败：${formatError(error)}`);
    }
  }

  function setMusicDropHover(value) { musicDropHover = Boolean(value); }
  function clearMusicDropHover() { musicDropHover = false; }

  return {
    setMusicDropHover, clearMusicDropHover, stopPetPhysics,
    get playState() { return playState; },
    get dragState() { return dragState; },
    get physicsTimer() { return physicsTimer; },
    get suppressClickUntil() { return suppressClickUntil; },
    get musicDropHover() { return musicDropHover; },
    beginManualDrag,
    continueManualDrag,
    handlePetPointerUp,
    endManualDrag,
    scheduleIdleJump,
    registerWindowListeners,
    registerFileDropHandlers,
    applyVisualState,
    scheduleNativeWindowStateApply,
    updateMenuLabels,
    updateVisualScale,
    updateVisualOpacity,
    showMenu,
    repositionOpenMenu,
    closeMenu,
    scheduleNativeHitTestSync,
    applyWindowGeometryToState,
    closePetWindow,
    quitAkane,
    openWorkspaceWindow,
    openShopWindow,
    openWorkshopWindow,
    setAlwaysOnTop,
    setSkipTaskbar,
    setHitTestEnabled,
    setHitboxOverlay,
    toggleWebglProbe,
    resetWindowPlacement,
    resetVisuals,
    probeClickThrough
  };
}
