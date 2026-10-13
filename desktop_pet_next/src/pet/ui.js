import { serializeBrowserAttachments } from "../pending-attachments.js";
import { isVoiceShortcut } from "../desktop-voice-session.js";
import { formatError } from './utils.js';

/** Owns ui behavior and its temporary runtime state. */
export function createUiController({
  state,
  els,
  platform: platformPort,
  lifecycle,
  interaction: interactionPort,
  window: windowPort,
  input: inputPort,
  panel: panelPort,
  status: statusPort,
  attachments: attachmentsPort,
  voice: voicePort,
  perception: perceptionPort,
  chat: chatPort,
  music: musicPort,
  settings: settingsPort,
  backend: backendPort,
  character: characterPort,
  systemMedia: systemMediaPort
}) {
  const { window, document } = platformPort;

  let voiceShortcutHeld = false;

  function bindUi() {
    lifecycle.on(els.hitbox, "pointerdown", (event) => {
      if (event.button !== 0 || state.clickThrough) return;
      if (event.detail >= 2) {
        event.preventDefault();
        event.stopPropagation();
        interactionPort.cancelLocalClick();
        windowPort.endManualDrag();
        inputPort.showChatInput();
        return;
      }

      event.preventDefault();
      windowPort.closeMenu();
      inputPort.hideChatInput();
      windowPort.beginManualDrag(event);
    });

    lifecycle.on(els.hitbox, "pointermove", (event) => {
      void windowPort.continueManualDrag(event);
    });
    lifecycle.on(els.hitbox, "pointerup", windowPort.handlePetPointerUp);
    lifecycle.on(els.hitbox, "pointercancel", windowPort.endManualDrag);
    lifecycle.on(els.hitbox, "lostpointercapture", windowPort.endManualDrag);
    lifecycle.on(els.hitbox, "click", (event) => {
      if (event.button !== 0 || event.detail !== 1) return;
      if (Date.now() < windowPort.suppressClickUntil) return;
      interactionPort.scheduleLocalClick();
    });
    lifecycle.on(els.hitbox, "dblclick", (event) => {
      event.preventDefault();
      event.stopPropagation();
      interactionPort.cancelLocalClick();
      windowPort.endManualDrag();
      inputPort.showChatInput();
    });
    lifecycle.on(els.hitbox, "contextmenu", (event) => {
      event.preventDefault();
      event.stopPropagation();
      interactionPort.cancelLocalClick();
      void panelPort.openPanelWindow();
    });
    lifecycle.on(els.petImage, "load", () => {
      if (import.meta.env.DEV) {
        console.log("[pet-image] loaded:", els.petImage.src);
      }
      windowPort.scheduleNativeHitTestSync();
    });
    lifecycle.on(els.hitbox, 'animationend', () => windowPort.scheduleNativeHitTestSync());
    lifecycle.on(els.petImage, "error", () => {
      const src = els.petImage.src || "";
      console.error("[pet-image] failed to load:", src.substring(0, 256));
      statusPort.setStatus(`立绘加载失败：${src ? src.split("/").pop() : "无图片地址"}`, { durationMs: 3600 });
    });

    lifecycle.on(els.chatForm, "submit", (event) => {
      event.preventDefault();
      inputPort.submitChatInput();
    });
    lifecycle.on(els.chatInput, "paste", (event) => {
      const files = Array.from(event.clipboardData?.files || []);
      if (!files.length) return;
      event.preventDefault();
      const scope = attachmentsPort.pendingAttachments.scope();
      void serializeBrowserAttachments(files).then(async (values) => {
        if (attachmentsPort.pendingAttachments.scope() !== scope) return;
        await attachmentsPort.importClipboardAttachments(values);
        inputPort.showChatInput();
      }).catch(error => statusPort.setRuntimeStatus(formatError(error), { mode: "error" }));
    });
    lifecycle.on(els.chatForm, "pointermove", () => {
      inputPort.scheduleChatInputAutoHide();
    });
    lifecycle.on(els.chatForm, "pointerdown", () => {
      inputPort.scheduleChatInputAutoHide();
    });

    lifecycle.on(els.chatInput, "keydown", (event) => {
      inputPort.scheduleChatInputAutoHide();
      if (event.isComposing) return;
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        inputPort.submitChatInput();
        return;
      }
      if (event.key === "ArrowUp" && inputPort.shouldNavigateInputHistory(event, "up")) {
        event.preventDefault();
        inputPort.navigateInputHistory("up");
        return;
      }
      if (event.key === "ArrowDown" && inputPort.shouldNavigateInputHistory(event, "down")) {
        event.preventDefault();
        inputPort.navigateInputHistory("down");
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        inputPort.hideChatInput();
      }
    });
    lifecycle.on(els.chatInput, "input", () => {
      inputPort.autoResizeChatInput();
      if (!inputPort.applyingInputHistory) inputPort.resetInputHistoryCursor();
      inputPort.scheduleChatInputAutoHide();
    });
    lifecycle.on(els.chatInput, "focus", () => {
      inputPort.scheduleChatInputAutoHide();
    });
    lifecycle.on(els.chatInput, "blur", () => {
      lifecycle.setTimeout(() => {
        if (!els.chatInput.value.trim() && document.activeElement !== els.chatInput) {
          inputPort.hideChatInput();
        }
      }, 180);
    });
    lifecycle.on(els.chatInput, "pointerdown", (event) => {
      event.stopPropagation();
      inputPort.scheduleChatInputAutoHide();
    });
    lifecycle.on(els.voiceRecordButton, "click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      void voicePort.toggleVoiceRecording();
    });
    lifecycle.on(els.toggle, "click", (event) => {
      event.stopPropagation();
      void panelPort.openPanelWindow();
    });
    lifecycle.on(els.close, "click", () => {
      void windowPort.closePetWindow();
    });
    lifecycle.on(els.quickInput, "click", () => {
      windowPort.closeMenu();
      inputPort.showChatInput();
    });
    lifecycle.on(els.openSettings, "click", () => {
      void panelPort.openPanelWindow();
    });
    lifecycle.on(els.screenShare, "click", () => {
      // Invoke in this trusted click stack: activation does not cross WebViews.
      void perceptionPort.setScreenVisionEnabled(!state.screenVisionEnabled);
      windowPort.closeMenu();
    });
    lifecycle.on(els.openWorkshop, "click", () => {
      void windowPort.openWorkshopWindow();
    });
    const openScene = document.querySelector("#open-scene");
    if (openScene) lifecycle.on(openScene, "click", () => {
      void platformPort.tauriCall("open_scene_window", {});
    });
    lifecycle.on(els.openWorkspace, "click", () => {
      void windowPort.openWorkspaceWindow();
    });
    const openGomoku = document.querySelector("#open-gomoku");
    if (openGomoku) lifecycle.on(openGomoku, "click", () => {
      void platformPort.tauriCall("open_gomoku_window", {});
    });
    lifecycle.on(els.stopReply, "click", () => {
      chatPort.interruptReply({ announce: true });
    });
    lifecycle.on(els.musicPlayer, "ended", () => {
      void musicPort.handleMusicEnded();
    });
    lifecycle.on(els.musicPlayer, "error", () => {
      void musicPort.handleMusicPlaybackError();
    });
    lifecycle.on(els.musicPlayer, "timeupdate", () => {
      if (musicPort.musicTrack) settingsPort.scheduleMusicSnapshot();
    });

    lifecycle.on(window, "contextmenu", (event) => {
      if (event.target.closest("#chat-form")) return;
      event.preventDefault();
      void panelPort.openPanelWindow();
    });

    lifecycle.on(window, "pointerdown", (event) => {
      if (!event.target.closest("#debug-menu, #debug-toggle, #chat-form")) windowPort.closeMenu();
    });

    lifecycle.on(window, "keydown", (event) => {
      if (isVoiceShortcut(event) && !event.repeat) {
        event.preventDefault();
        voiceShortcutHeld = true;
        inputPort.showChatInput();
        if (voicePort.canUseRealtimeVoice() || voicePort.realtimeVoiceCall) void voicePort.toggleVoiceRecording();
        else if (voicePort.voiceInputState !== "recording") void voicePort.startVoiceRecording();
        return;
      }

      if (event.key !== "Escape") return;
      if (voicePort.realtimeVoiceCall) {
        void voicePort.stopRealtimeVoiceCall({ notice: true, reason: "user_cancelled_call" });
        return;
      }
      if (voicePort.voiceInputState === "recording" || voicePort.voiceInputState === "opening") {
        void voicePort.cancelVoiceRecording({ notice: true });
        return;
      }
      if (!els.chatForm.hidden) {
        inputPort.hideChatInput();
        return;
      }
      windowPort.closeMenu();
    });
    lifecycle.on(window, "keyup", (event) => {
      if (!isVoiceShortcut(event) || !voiceShortcutHeld) return;
      event.preventDefault();
      voiceShortcutHeld = false;
      if (voicePort.canUseRealtimeVoice() || voicePort.realtimeVoiceCall) return;
      if (voicePort.voiceInputState === "opening") {
        void voicePort.cancelVoiceRecording({ notice: true });
      } else if (voicePort.voiceInputState === "recording") {
        void voicePort.stopVoiceRecording();
      }
    });
    lifecycle.on(window, "resize", () => {
      windowPort.repositionOpenMenu();
      windowPort.scheduleNativeHitTestSync({ force: true });
    });

    lifecycle.on(els.scale, "input", () => {
      windowPort.updateVisualScale(Number(els.scale.value));
    });

    lifecycle.on(els.opacity, "input", () => {
      windowPort.updateVisualOpacity(Number(els.opacity.value));
    });

    lifecycle.on(els.scalePresets, "click", (event) => {
      const button = event.target.closest("[data-scale]");
      if (!button) return;
      windowPort.updateVisualScale(Number(button.dataset.scale), { commitNow: true });
    });

    lifecycle.on(els.opacityPresets, "click", (event) => {
      const button = event.target.closest("[data-opacity]");
      if (!button) return;
      windowPort.updateVisualOpacity(Number(button.dataset.opacity), { saveNow: true });
    });

    lifecycle.on(els.backendSave, "click", () => {
      void backendPort.updateBackendUrlFromInput();
    });
    lifecycle.on(els.backendUrl, "keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        void backendPort.updateBackendUrlFromInput();
      }
    });

    lifecycle.on(els.outfitSave, "click", () => {
      void characterPort.updateOutfitFromInput();
    });
    lifecycle.on(els.outfit, "keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        void characterPort.updateOutfitFromInput();
      }
    });

    lifecycle.on(els.emotionGrid, "click", (event) => {
      const button = event.target.closest("[data-emotion]");
      if (!button) return;
      interactionPort.previewEmotion(button.dataset.emotion);
    });

    lifecycle.on(els.connectionStatus, "click", () => {
      void characterPort.reloadCharacterResources({ userTriggered: true });
    });

    lifecycle.on(els.alwaysOnTop, "click", async () => {
      await windowPort.setAlwaysOnTop(!state.alwaysOnTop);
    });

    lifecycle.on(els.taskbar, "click", async () => {
      await windowPort.setSkipTaskbar(!state.skipTaskbar);
    });

    lifecycle.on(els.webgl, "click", () => {
      windowPort.toggleWebglProbe();
    });

    lifecycle.on(els.passthrough, "click", async () => {
      windowPort.closeMenu();
      await windowPort.probeClickThrough(5000);
    });

    lifecycle.on(els.hitTestToggle, "click", async () => {
      await windowPort.setHitTestEnabled(!state.hitTestEnabled);
    });

    lifecycle.on(els.hitboxOverlayToggle, "click", () => {
      windowPort.setHitboxOverlay(!state.hitboxOverlay);
    });

    lifecycle.on(els.reset, "click", async () => {
      await windowPort.resetWindowPlacement();
    });

    lifecycle.on(els.reloadResources, "click", () => {
      void characterPort.reloadCharacterResources({ userTriggered: true });
    });

    lifecycle.on(els.previousMusic, "click", () => {
      void systemMediaPort.controlActiveMusic("previous", { source: "pet-controls" });
    });

    lifecycle.on(els.nextMusic, "click", () => {
      void systemMediaPort.controlActiveMusic("next", { source: "pet-controls" });
    });

    lifecycle.on(els.toggleMusic, "click", () => {
      void systemMediaPort.controlActiveMusic("toggle", { source: "pet-controls" });
    });

    lifecycle.on(els.stopMusic, "click", () => {
      void systemMediaPort.controlActiveMusic("stop", { source: "pet-controls" });
    });

    lifecycle.on(els.clearMusicQueue, "click", () => {
      musicPort.clearMusicQueue({ announce: true });
    });

    lifecycle.on(els.closeMenuButton, "click", () => {
      void windowPort.closePetWindow();
    });
  }

  return {
    bindUi
  };
}
