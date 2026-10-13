import { observeBubbleLayout } from "../bubble-layout.js";
import { bindInstanceStorage } from "../instance-storage.js";
import { registerSceneDesktopBridge } from "../scene-desktop-bridge.js";
import { createCharacterController } from './character.js';
import { createStateController } from './state.js';
import { createCareController } from './care.js';
import { createWindowController } from './window.js';
import { createWebglController } from './webgl.js';
import { createChatController } from './chat.js';
import { createStatusController } from './status.js';
import { createBubbleController } from './bubble.js';
import { createActivityController } from './activity.js';
import { createInteractionController } from './interaction.js';
import { createInputController } from './input.js';
import { createSettingsController } from './settings.js';
import { createPluginsController } from './plugins.js';
import { createMusicController } from './music.js';
import { createSystemMediaController } from './system-media.js';
import { createPanelController } from './panel.js';
import { createGomokuController } from './gomoku.js';
import { getProfileIdentityText } from './profile.js';
import { CHARACTER_NAME } from '../character-profile.js';
import { createTtsController } from './tts.js';
import { createAttachmentsController } from './attachments.js';
import { createVoiceController } from './voice.js';
import { createUiController } from './ui.js';
import { createPerceptionController } from './perception.js';
import { createBackendController } from './backend.js';
import { createVisualRenderer } from '../visual-renderer.js';
import { createUnresolvedCareFeature } from '../care-feature.js';
import { PUBLIC_HEALTH_PATH, LOCAL_DEFAULT_INSTANCE_ID } from './constants.js';
import { createLifecycle } from './lifecycle.js';
import { createRuntimeScope } from './scope.js';
import { formatError, friendlyErrorMessage, generateSessionId } from './utils.js';

export function createPetApplication({ platform, bundledCharacterAssets, elements }) {
  const platformPort = platform;
  const lifecycle = createLifecycle(platform.window);
  const { window, document } = platform;
  const scopeFields = new Set(["instanceId", "backendUrl", "boundBotId", "profileUserId", "characterPackId", "sessionId"]);
  let scope;
  // Persisted settings are shared. Controllers own their request and playback state.
  const state = new Proxy({}, {
    set(target, key, value) {
      const changed = target[key] !== value;
      target[key] = value;
      if (changed && scopeFields.has(key)) scope.invalidate();
      return true;
    }
  });
  scope = createRuntimeScope(state, () => lifecycle.disposed);
  const resourceState = {};
  const els = elements || collectElements(document);
  const characterPort = {};
  const statePort = {};
  const carePort = {};
  const windowPort = {};
  const webglPort = {};
  const chatPort = {};
  const statusPort = {};
  const bubblePort = {};
  const activityPort = {};
  const interactionPort = {};
  const inputPort = {};
  const settingsPort = {};
  const pluginsPort = {};
  const musicPort = {};
  const systemMediaPort = {};
  const panelPort = {};
  const gomokuPort = {};
  const ttsPort = {};
  const attachmentsPort = {};
  const voicePort = {};
  const uiPort = {};
  const perceptionPort = {};
  const backendPort = {};
  const visualRenderer = createVisualRenderer({
    stage: els.stage, image: els.petImage,
    canIdle: () => musicPort.canApplyRestingEmotionNow?.() === true,
    onMotionUpdate: () => windowPort.scheduleNativeHitTestSync(),
    onIdleAnimationError: error => statusPort.setStatus(`眨眼素材暂不可用：${friendlyErrorMessage(formatError(error))}`, {durationMs:3000}),
    onImageLoadError: ({ expression, error }) => {
      statusPort.setRuntimeStatus(
        `立绘加载失败：${expression?.name || expression?.id || 'unknown'} · ${friendlyErrorMessage(formatError(error))}`, {mode:'error'});
      musicPort.handleRestingEmotionLoadError(expression);
    },
  });
  // Link interfaces after construction; descriptors preserve live readonly getters.
  // Controllers install listeners and start work only after the graph is complete.
  const wire = (port, controller) => Object.defineProperties(port, Object.getOwnPropertyDescriptors(controller));
  const common = { state, resourceState, els, visualRenderer, bundledCharacterAssets, platform, lifecycle, scope };
  wire(statePort, createStateController({
    ...common, status: statusPort, settings: settingsPort, window: windowPort, character: characterPort
  }));
  wire(characterPort, createCharacterController({
    ...common, persistence: statePort, care: carePort, perception: perceptionPort, window: windowPort,
    status: statusPort, settings: settingsPort, backend: backendPort, panel: panelPort,
    interaction: interactionPort, bubble: bubblePort, tts: ttsPort, chat: chatPort, voice: voicePort,
    music: musicPort
  }));
  Object.assign(resourceState, {
    health: "unknown",
    healthMessage: "Not checked",
    healthEndpoint: PUBLIC_HEALTH_PATH,
    contractVersion: "",
    contractSource: "unknown",
    capabilities: [],
    endpoints: {},
    tts: {
      enabled: null,
      endpoint: "/tts",
      responseMediaType: "audio/mpeg"
    },
    asr: {
      available: null,
      endpoint: "/asr",
      uploadField: "file"
    },
    features: {
      care: createUnresolvedCareFeature()
    },
    manifest: null,
    outfit: characterPort.getDefaultLocalOutfit(),
    source: characterPort.getLocalResourceSource(),
    loadedAt: 0
  });
  wire(carePort, createCareController({
    ...common, character: characterPort, settings: settingsPort, panel: panelPort, status: statusPort,
    persistence: statePort, backend: backendPort, bubble: bubblePort, chat: chatPort, tts: ttsPort,
    attachments: attachmentsPort, music: musicPort, input: inputPort
  }));
  wire(windowPort, createWindowController({
    ...common, interaction: interactionPort, bubble: bubblePort, persistence: statePort, chat: chatPort,
    tts: ttsPort, character: characterPort, voice: voicePort, music: musicPort, attachments: attachmentsPort,
    input: inputPort, status: statusPort, settings: settingsPort, care: carePort, webgl: webglPort
  }));
  wire(webglPort, createWebglController({
    ...common, status: statusPort
  }));
  wire(chatPort, createChatController({
    ...common, perception: perceptionPort, character: characterPort, bubble: bubblePort,
    interaction: interactionPort, status: statusPort, persistence: statePort,
    backend: backendPort, voice: voicePort, tts: ttsPort, attachments: attachmentsPort, music: musicPort,
    settings: settingsPort, input: inputPort, systemMedia: systemMediaPort, care: carePort
  }));
  wire(statusPort, createStatusController({
    ...common, bubble: bubblePort, settings: settingsPort, music: musicPort, chat: chatPort,
    character: characterPort, window: windowPort
  }));
  wire(bubblePort, createBubbleController({
    ...common, attachments: attachmentsPort, status: statusPort, tts: ttsPort,
    chat: chatPort, character: characterPort, care: carePort, interaction: interactionPort, window: windowPort,
    music: musicPort, voice: voicePort
  }));
  wire(activityPort, createActivityController({
    ...common, status: statusPort, settings: settingsPort, music: musicPort,
    character: characterPort, backend: backendPort
  }));
  wire(interactionPort, createInteractionController({
    ...common, chat: chatPort, tts: ttsPort, bubble: bubblePort, character: characterPort, music: musicPort
  }));
  wire(inputPort, createInputController({
    ...common, interaction: interactionPort, window: windowPort, attachments: attachmentsPort, chat: chatPort,
    voice: voicePort
  }));
  wire(settingsPort, createSettingsController({
    ...common, character: characterPort, care: carePort, window: windowPort, input: inputPort,
    backend: backendPort, voice: voicePort, perception: perceptionPort, persistence: statePort, chat: chatPort,
    tts: ttsPort, status: statusPort, systemMedia: systemMediaPort, music: musicPort,
    attachments: attachmentsPort, interaction: interactionPort, bubble: bubblePort
  }));
  wire(pluginsPort, createPluginsController({
    ...common, chat: chatPort, tts: ttsPort, bubble: bubblePort, settings: settingsPort, activity: activityPort
  }));
  wire(musicPort, createMusicController({
    ...common, persistence: statePort, status: statusPort, settings: settingsPort, bubble: bubblePort,
    systemMedia: systemMediaPort, character: characterPort, chat: chatPort, tts: ttsPort, voice: voicePort,
    window: windowPort, interaction: interactionPort, backend: backendPort, care: carePort
  }));
  wire(systemMediaPort, createSystemMediaController({
    ...common, perception: perceptionPort, chat: chatPort, panel: panelPort, music: musicPort,
    settings: settingsPort, persistence: statePort, backend: backendPort, activity: activityPort,
    status: statusPort, bubble: bubblePort
  }));
  wire(panelPort, createPanelController({
    ...common, chat: chatPort, window: windowPort, voice: voicePort, persistence: statePort,
    systemMedia: systemMediaPort, status: statusPort, music: musicPort, care: carePort, backend: backendPort
  }));
  wire(gomokuPort, createGomokuController({
    ...common, chat: chatPort, persistence: statePort, backend: backendPort,
    characterName: () => getProfileIdentityText("displayName", CHARACTER_NAME)
  }));
  wire(ttsPort, createTtsController({
    ...common, chat: chatPort, bubble: bubblePort, perception: perceptionPort, voice: voicePort,
    character: characterPort, backend: backendPort, status: statusPort, settings: settingsPort,
    music: musicPort
  }));
  wire(attachmentsPort, createAttachmentsController({
    ...common, persistence: statePort, settings: settingsPort, status: statusPort, bubble: bubblePort,
    music: musicPort, chat: chatPort, input: inputPort, backend: backendPort, voice: voicePort,
    window: windowPort
  }));
  wire(voicePort, createVoiceController({
    ...common, tts: ttsPort, bubble: bubblePort, chat: chatPort, persistence: statePort, status: statusPort,
    settings: settingsPort, character: characterPort, backend: backendPort, music: musicPort,
    input: inputPort
  }));
  wire(uiPort, createUiController({
    ...common, interaction: interactionPort, window: windowPort, input: inputPort, panel: panelPort,
    status: statusPort, attachments: attachmentsPort, voice: voicePort, perception: perceptionPort,
    chat: chatPort, music: musicPort, settings: settingsPort, backend: backendPort, character: characterPort,
    systemMedia: systemMediaPort
  }));
  wire(perceptionPort, createPerceptionController({
    ...common, status: statusPort, chat: chatPort, persistence: statePort, settings: settingsPort,
    character: characterPort, window: windowPort, backend: backendPort, systemMedia: systemMediaPort,
    tts: ttsPort, bubble: bubblePort, voice: voicePort, interaction: interactionPort,
    attachments: attachmentsPort, music: musicPort
  }));
  wire(backendPort, createBackendController({
    ...common, status: statusPort, persistence: statePort, character: characterPort, settings: settingsPort,
    chat: chatPort, voice: voicePort, attachments: attachmentsPort, care: carePort, bubble: bubblePort
  }));

  async function boot() {
    uiPort.bindUi();
    if (!platformPort.isTauriRuntime) {
      bindInstanceStorage(LOCAL_DEFAULT_INSTANCE_ID);
    }
    characterPort.applyCharacterChrome();
    windowPort.applyVisualState();
    statusPort.updateConnectionStatus();
    windowPort.scheduleIdleJump();

    if (!platformPort.isTauriRuntime) {
      state.sessionId = generateSessionId();
      statusPort.setStatus("Browser preview");
      const backendReady = await characterPort.reloadCharacterResources({ startup: true });
      if (lifecycle.disposed) return false;
      windowPort.scheduleNativeHitTestSync();
      musicPort.scheduleWorkspaceMusicRecommendationsRefresh();
      return backendReady;
    }

    try {
      await reportUiLaunchState("starting", "ui_initializing");
      if (lifecycle.disposed) return false;
      scheduleTauriRuntimeBridges();
      await characterPort.loadAndApplyPersistedCharacterState();
      if (lifecycle.disposed) return false;
      const backendReady = await characterPort.reloadCharacterResources({ startup: true });
      if (lifecycle.disposed) return false;
      windowPort.scheduleNativeWindowStateApply({ forceHitTest: true });
      if (backendReady) {
        statePort.scheduleSave(0);
        const session = await backendPort.ensureBackendSession({ restoreLatest: state.restoreLatestOnStartup });
        if (lifecycle.disposed) return false;
        if (!session) throw new Error("backend_session_unavailable");
        await reportUiLaunchState("ready", "ui_ready");
      } else {
        await reportUiLaunchState("failed", "backend_unavailable", resourceState.healthMessage || "backend_offline");
      }
      systemMediaPort.scheduleSystemMediaPoll({ immediate: true });
      perceptionPort.scheduleScreenVisionCapture({ immediate: true });
      perceptionPort.scheduleProactiveWake();
      void perceptionPort.prepareProactiveObservation();
      musicPort.scheduleWorkspaceMusicRecommendationsRefresh();
      return backendReady;
    } catch (error) {
      if (lifecycle.disposed) return false;
      statusPort.setStatus(`Tauri init failed: ${formatError(error)}`);
      await reportUiLaunchState("failed", "ui_initialization_failed", formatError(error));
      return false;
    }
  }

  async function reportUiLaunchState(status, phase, reason = "") {
    if (!platformPort.isTauriRuntime) return null;
    try {
      return await platformPort.invoke("report_ui_ready", {
        status: String(status || "starting"),
        phase: String(phase || ""),
        reason: String(reason || "")
      });
    } catch (error) {
      console.error("[launch] ui state report failed:", error);
      return null;
    }
  }

  function scheduleTauriRuntimeBridges() {
    if (!platformPort.isTauriRuntime) return;
    lifecycle.setTimeout(startTauriRuntimeBridges, 0);
  }

  function startTauriRuntimeBridges() {
    if (!platformPort.isTauriRuntime) return;
    void registerSceneDesktopBridge({
      platform: platformPort,
      isActive: () => !lifecycle.disposed,
      stop: async () => { await chatPort.interruptReply({ reason: "scene_playback_handoff" }); musicPort.stopMusic(); },
      scope: () => ({ profile: state.profileUserId, pack: state.characterPackId, bot: state.boundBotId || "" }),
      equip: characterPort.updateOutfit,
    }).then(lifecycle.track).catch(error => statusPort.setStatus(`小屋播放交接不可用：${formatError(error)}`));
    void characterPort.registerCharacterActivationBridge().catch((error) => {
      statusPort.setStatus(`角色切换桥接不可用：${formatError(error)}`);
    });
    void settingsPort.registerSettingsBridge().catch((error) => {
      statusPort.setStatus(`设置桥接不可用：${formatError(error)}`);
    });
    void pluginsPort.registerPluginAgentEventBridge().catch((error) => {
      statusPort.setRuntimeStatus(`插件事件桥接不可用：${formatError(error)}`, { mode: "error" });
    });
    void panelPort.registerPanelBridge().catch(() => {});
    void gomokuPort.registerBridge().catch(error => {
      statusPort.setRuntimeStatus(`五子棋桥接不可用：${formatError(error)}`, { mode: "error" });
    });
    void Promise.allSettled([
      windowPort.registerWindowListeners(),
      windowPort.registerFileDropHandlers()
    ]);
  }

  let startup = null;
  function start() {
    if (lifecycle.disposed) return Promise.reject(new Error("pet_application_disposed"));
    if (!startup) {
      lifecycle.track(observeBubbleLayout({
        stage: els.stage, bubble: els.bubble, obstruction: els.chatForm,
        onResize: () => windowPort.scheduleNativeHitTestSync({ force: true })
      }));
      lifecycle.on(window, "pagehide", dispose, { once: true });
      lifecycle.on(window, "beforeunload", dispose, { once: true });
      startup = boot().catch(error => {
        if (!lifecycle.disposed) statusPort.setRuntimeStatus(`桌宠启动失败：${friendlyErrorMessage(formatError(error))}`, { mode: "error" });
        return false;
      });
    }
    return startup;
  }

  function dispose() {
    if (lifecycle.disposed) return;
    const errors = lifecycle.dispose();
    const stop = [
      () => chatPort.interruptReply({ reason: "window_closed" }),
      () => voicePort.cancelVoiceRecording(),
      () => perceptionPort.stopScreenVisionCapture(),
      () => carePort.stopCareRuntime(),
      () => windowPort.stopPetPhysics({ restore: false, reschedule: false }),
      () => ttsPort.stopTts(),
      () => musicPort.stopMusic({ silent: true, clearQueue: true }),
      () => visualRenderer.dispose()
    ];
    for (const cleanup of stop) {
      try {
        Promise.resolve(cleanup()).catch(error => console.error("[pet] cleanup failed", error));
      } catch (error) { errors.push(error); }
    }
    for (const error of errors) console.error("[pet] cleanup failed", error);
  }

  platform.onError = (command, error) => statusPort.setStatus(`${command}: ${formatError(error)}`);
  return {
    start, dispose, state, resourceState,
    controllers: {
      character: characterPort,
      persistence: statePort,
      care: carePort,
      window: windowPort,
      webgl: webglPort,
      chat: chatPort,
      status: statusPort,
      bubble: bubblePort,
      activity: activityPort,
      interaction: interactionPort,
      input: inputPort,
      settings: settingsPort,
      plugins: pluginsPort,
      music: musicPort,
      systemMedia: systemMediaPort,
      panel: panelPort,
      gomoku: gomokuPort,
      tts: ttsPort,
      attachments: attachmentsPort,
      voice: voicePort,
      ui: uiPort,
      perception: perceptionPort,
      backend: backendPort
    }
  };
}

function collectElements(document) {
  return {
    stage: document.querySelector(".stage"),
    hitboxOverlay: document.querySelector("#hitbox-overlay"),
    hitbox: document.querySelector("#pet-hitbox"),
    petImage: document.querySelector("#pet-image"),
    menu: document.querySelector("#debug-menu"),
    menuTitle: document.querySelector("#debug-menu .menu-head strong"),
    menuSummary: document.querySelector("#menu-summary"),
    toggle: document.querySelector("#debug-toggle"),
    close: document.querySelector("#close-window"),
    quickInput: document.querySelector("#quick-input"),
    openSettings: document.querySelector("#open-settings"),
    screenShare: document.querySelector("#screen-share"),
    openWorkshop: document.querySelector("#open-workshop"),
    openWorkspace: document.querySelector("#open-workspace"),
    stopReply: document.querySelector("#stop-reply"),
    bubble: document.querySelector("#bubble"),
    bubbleText: document.querySelector("#bubble-text"),
    chatForm: document.querySelector("#chat-form"),
    chatInput: document.querySelector("#chat-input"),
    voiceRecordButton: document.querySelector("#voice-record-button"),
    scale: document.querySelector("#scale-range"),
    scaleOutput: document.querySelector("#scale-output"),
    scalePresets: document.querySelector("#scale-presets"),
    opacity: document.querySelector("#opacity-range"),
    opacityOutput: document.querySelector("#opacity-output"),
    opacityPresets: document.querySelector("#opacity-presets"),
    backendUrl: document.querySelector("#backend-url-input"),
    backendSave: document.querySelector("#backend-url-save"),
    outfit: document.querySelector("#outfit-input"),
    outfitSave: document.querySelector("#outfit-save"),
    alwaysOnTop: document.querySelector("#always-on-top-toggle"),
    taskbar: document.querySelector("#taskbar-toggle"),
    webgl: document.querySelector("#webgl-toggle"),
    passthrough: document.querySelector("#passthrough-probe"),
    hitTestToggle: document.querySelector("#hit-test-toggle"),
    hitboxOverlayToggle: document.querySelector("#hitbox-overlay-toggle"),
    reset: document.querySelector("#reset-window"),
    reloadResources: document.querySelector("#reload-resources"),
    closeMenuButton: document.querySelector("#close-window-menu"),
    previousMusic: document.querySelector("#previous-music"),
    nextMusic: document.querySelector("#next-music"),
    toggleMusic: document.querySelector("#toggle-music"),
    stopMusic: document.querySelector("#stop-music"),
    clearMusicQueue: document.querySelector("#clear-music-queue"),
    resourceDetails: document.querySelector("#resource-details"),
    emotionGrid: document.querySelector("#emotion-grid"),
    connectionStatus: document.querySelector("#connection-status"),
    status: document.querySelector("#runtime-status"),
    voicePlayer: document.querySelector("#voice-player"),
    musicPlayer: document.querySelector("#music-player"),
    canvas: document.querySelector("#webgl-probe")
  };
}
