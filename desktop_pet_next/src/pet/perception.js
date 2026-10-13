import { ScreenObservationPreparation, ScreenObservationBuffer, normalizeScreenObservationSettings, observationConfigurationIssue, packScreenObservation, proactiveWakeDelay, observationDuration, MODERATE_PROACTIVE_PROMPT } from "../screen-observation.js";
import { readJsonResponse } from "../desktop-voice-session.js";
import { sceneOwnsPlayback } from "../scene-desktop-bridge.js";
import { getActiveCharacterText } from "../character-profile.js";
import { SCREEN_VISION_JPEG_QUALITY, PROACTIVE_WAKE_RETRY_MS } from './constants.js';
import { formatError } from './utils.js';

/** Owns perception behavior and its temporary runtime state. */
export function createPerceptionController({
  state,
  resourceState,
  els,
  platform: platformPort,
  lifecycle,
  status: statusPort,
  chat: chatPort,
  persistence: statePort,
  settings: settingsPort,
  character: characterPort,
  window: windowPort,
  backend: backendPort,
  systemMedia: systemMediaPort,
  tts: ttsPort,
  bubble: bubblePort,
  voice: voicePort,
  interaction: interactionPort,
  attachments: attachmentsPort,
  music: musicPort
}) {
  const { window, document, navigator } = platformPort;

  let proactiveWakeTimer = 0;

  let proactiveWakeLastAt = 0;

  let proactiveWakeNextAllowedAt = 0;

  const proactivePreparation = new ScreenObservationPreparation();

  let proactiveWakeRunning = false;

  let screenObservationLastSentAt = 0;

  let screenVisionTimer = 0;

  let screenVisionStream = null;

  let screenVisionVideo = null;

  let screenVisionCanvas = null;

  const screenObservation = new ScreenObservationBuffer();

  let screenVisionCapturePending = null;

  let screenVisionCaptureRevision = 0;

  let screenVisionStatus = "off";

  let screenVisionError = "";

  async function setScreenVisionEnabled(enabled) {
    state.screenVisionEnabled = Boolean(enabled);
    screenVisionError = "";
    if (state.screenVisionEnabled) {
      const revision = screenVisionCaptureRevision;
      screenVisionStatus = "starting";
      statusPort.setRuntimeStatus("正在请求屏幕权限", { mode: "idle" });
      const started = await ensureScreenVisionCapture();
      if (revision !== screenVisionCaptureRevision || !state.screenVisionEnabled) return;
      if (started) {
        scheduleScreenVisionCapture({ immediate: true });
        statusPort.setRuntimeStatus("看屏幕已开启 · 连续画面直接交给视觉模型", { mode: "idle" });
      } else {
        state.screenVisionEnabled = false;
        clearScreenVisionWorkspace({ quiet: true });
        statusPort.setRuntimeStatus(`看屏幕开启失败：${screenVisionError || "未获得屏幕权限"}`, { mode: "error" });
      }
    } else {
      if (proactiveWakeRunning) chatPort.interruptReply({ announce: false });
      stopScreenVisionCapture();
      statusPort.setRuntimeStatus("看屏幕已关闭", { mode: "idle" });
    }
    statePort.scheduleSave(0);
    settingsPort.scheduleSettingsSnapshot();
  }

  function setProactiveWakeEnabled(enabled) {
    state.proactiveWakeEnabled = Boolean(enabled);
    if (state.proactiveWakeEnabled) {
      void prepareProactiveObservation();
      proactiveWakeNextAllowedAt = Date.now() + getProactiveWakeIntervalMs();
      scheduleProactiveWake({ immediate: false });
      statusPort.setRuntimeStatus("适度主动已开启 · 模型可选择安静", { mode: "idle" });
    } else {
      lifecycle.clearTimeout(proactiveWakeTimer);
      proactiveWakeTimer = 0;
      proactiveWakeNextAllowedAt = 0;
      proactivePreparation.clear();
      if (proactiveWakeRunning) chatPort.interruptReply({ announce: false });
      proactiveWakeRunning = false;
      statusPort.setRuntimeStatus("主动搭话已关闭", { mode: "idle" });
    }
    statePort.scheduleSave(0);
    settingsPort.scheduleSettingsSnapshot();
  }

  function setProactiveWakeIntervalSec(value) {
    state.proactiveWakeIntervalSec = statePort.normalizeProactiveWakeIntervalSec(value);
    proactiveWakeNextAllowedAt = Date.now() + getProactiveWakeIntervalMs();
    statePort.scheduleSave(0);
    scheduleProactiveWake({ immediate: false });
    statusPort.setRuntimeStatus(`主动观察评估间隔：${state.proactiveWakeIntervalSec} 秒（不强制开口）`, { mode: "idle" });
    settingsPort.scheduleSettingsSnapshot();
  }

  function setScreenObservationSetting(key, value) {
    const normalized = normalizeScreenObservationSettings({ ...state, [key]: value });
    const issue = observationConfigurationIssue(normalized);
    if (issue) throw new Error(issue);
    Object.assign(state, normalized);
    screenObservation.clear();
    screenObservationLastSentAt = 0;
    if (state.screenVisionEnabled) scheduleScreenVisionCapture({ immediate: true });
    statePort.scheduleSave(0);
    statusPort.setRuntimeStatus("屏幕观察配置已保存，下次采样生效", { mode: "idle" });
    settingsPort.scheduleSettingsSnapshot();
  }

  function screenObservationScope() {
    const scope = JSON.stringify([state.backendUrl, state.boundBotId, statePort.getProfileUserId(), characterPort.getCurrentCharacterPackId(), state.sessionId]);
    if (screenObservation.scope && screenObservation.scope !== scope) screenObservation.clear();
    return scope;
  }

  function scheduleScreenVisionCapture({ immediate = false } = {}) {
    lifecycle.clearTimeout(screenVisionTimer);
    screenVisionTimer = 0;
    if (!platformPort.isTauriRuntime || !state.screenVisionEnabled) return;
    const delay = immediate ? 0 : normalizeScreenObservationSettings(state).screenVisionSampleIntervalSec * 1000;
    screenVisionTimer = lifecycle.setTimeout(async () => {
      screenVisionTimer = 0;
      try {
        await captureScreenVisionFrame();
      } catch (error) {
        const nextStatus = "error";
        const nextError = formatError(error).slice(0, 160);
        if (screenVisionStatus !== nextStatus || screenVisionError !== nextError) {
          screenVisionStatus = nextStatus;
          screenVisionError = nextError;
          settingsPort.scheduleSettingsSnapshot();
        }
      } finally {
        scheduleScreenVisionCapture();
      }
    }, delay);
  }

  async function ensureScreenVisionCapture() {
    if (screenVisionStream && screenVisionVideo) return true;
    if (screenVisionCapturePending) return screenVisionCapturePending;
    if (!navigator.mediaDevices?.getDisplayMedia) {
      screenVisionStatus = "unsupported";
      screenVisionError = "当前 WebView 不支持屏幕捕获";
      return false;
    }
    const revision = screenVisionCaptureRevision;
    const pending = (async () => {
      let stream = null;
      try {
        stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
        if (!state.screenVisionEnabled || revision !== screenVisionCaptureRevision) {
          stream.getTracks().forEach((track) => track.stop());
          return false;
        }
        const video = document.createElement("video");
        video.muted = true;
        video.playsInline = true;
        video.srcObject = stream;
        await video.play();
        if (!state.screenVisionEnabled || revision !== screenVisionCaptureRevision) {
          stream.getTracks().forEach((track) => track.stop());
          return false;
        }
        screenVisionStream = stream;
        screenVisionVideo = video;
        screenVisionCanvas = document.createElement("canvas");
        stream.getVideoTracks()[0]?.addEventListener("ended", () => {
          if (screenVisionStream !== stream) return;
          state.screenVisionEnabled = false;
          stopScreenVisionCapture();
          statePort.scheduleSave(0);
          settingsPort.scheduleSettingsSnapshot();
        });
        screenVisionStatus = "watching";
        screenVisionError = "";
        return true;
      } catch (error) {
        stream?.getTracks().forEach((track) => track.stop());
        if (revision === screenVisionCaptureRevision) {
          screenVisionStatus = "error";
          screenVisionError = error?.name === "InvalidStateError"
            ? "请在桌宠弹出的快捷菜单点击「屏幕共享」后选择共享区域（需要采集窗口中的点击授权）"
            : formatError(error).slice(0, 160);
          if (error?.name === "InvalidStateError") showScreenCaptureActivationPrompt();
        }
        return false;
      }
    })();
    screenVisionCapturePending = pending;
    try {
      return await pending;
    } finally {
      if (screenVisionCapturePending === pending) screenVisionCapturePending = null;
    }
  }

  function showScreenCaptureActivationPrompt() {
    windowPort.showMenu({ x: window.innerWidth - 20, y: 40, source: "pointer" });
    if (platformPort.isTauriRuntime) void platformPort.appWindow?.setFocus().catch(() => {});
  }

  async function captureScreenVisionFrame() {
    if (!state.screenVisionEnabled) return;
    const revision = screenVisionCaptureRevision;
    const scope = screenObservationScope();
    if (!(await ensureScreenVisionCapture())) {
      if (revision === screenVisionCaptureRevision) {
        state.screenVisionEnabled = false;
        screenObservation.clear();
        screenObservationLastSentAt = 0;
        statePort.scheduleSave(0);
        settingsPort.scheduleSettingsSnapshot();
      }
      return;
    }
    if (!state.screenVisionEnabled || revision !== screenVisionCaptureRevision || scope !== screenObservationScope()) return;
    if (!screenVisionVideo?.videoWidth || !screenVisionVideo?.videoHeight) return;
    const frame = readCompressedScreenVisionFrame();
    if (!frame) return;
    screenObservation.push(frame, scope, state);
    const statusChanged = screenVisionStatus !== "watching" || screenVisionError !== "";
    screenVisionStatus = "watching";
    screenVisionError = "";
    if (statusChanged) {
      settingsPort.scheduleSettingsSnapshot(120);
    }
  }

  async function latestDesktopScreenFramesForThink() {
    if (!state.screenVisionEnabled) return [];
    const scope = screenObservationScope();
    const revision = screenObservation.revision;
    const frames = screenObservation.snapshot(scope, state);
    const packed = await packScreenObservation(frames, state);
    if (!state.screenVisionEnabled || scope !== screenObservationScope() || revision !== screenObservation.revision) return [];
    return packed;
  }

  function readCompressedScreenVisionFrame() {
    const sourceWidth = screenVisionVideo.videoWidth;
    const sourceHeight = screenVisionVideo.videoHeight;
    if (!sourceWidth || !sourceHeight) return null;
    const maxEdge = normalizeScreenObservationSettings(state).screenVisionMaxEdge;
    const scale = Math.min(1, maxEdge / Math.max(sourceWidth, sourceHeight));
    const width = Math.max(1, Math.round(sourceWidth * scale));
    const height = Math.max(1, Math.round(sourceHeight * scale));
    screenVisionCanvas.width = width;
    screenVisionCanvas.height = height;
    const ctx = screenVisionCanvas.getContext("2d", { alpha: false });
    if (!ctx) throw new Error("screen_canvas_unavailable");
    ctx.drawImage(screenVisionVideo, 0, 0, width, height);
    return {
      captured_at: Date.now() / 1000,
      width,
      height,
      data_url: screenVisionCanvas.toDataURL("image/jpeg", SCREEN_VISION_JPEG_QUALITY)
    };
  }

  function stopScreenVisionCapture() {
    screenVisionCaptureRevision += 1;
    lifecycle.clearTimeout(screenVisionTimer);
    screenVisionTimer = 0;
    const stream = screenVisionStream;
    screenVisionStream = null;
    stream?.getTracks().forEach((track) => track.stop());
    screenVisionVideo = null;
    screenVisionCanvas = null;
    screenObservation.clear();
    screenObservationLastSentAt = 0;
    screenVisionStatus = "off";
  }

  function clearScreenVisionWorkspace({ quiet = false } = {}) {
    screenObservation.clear();
    screenObservationLastSentAt = 0;
    if (!quiet) statusPort.setRuntimeStatus("最近屏幕画面已清空", { mode: "idle" });
    settingsPort.scheduleSettingsSnapshot();
  }

  async function prepareProactiveObservation() {
    if (!platformPort.isTauriRuntime || !state.proactiveWakeEnabled || resourceState.health !== "online") return;
    const scope = screenObservationScope();
    const pending = proactivePreparation.begin(scope, async () => {
      const request = platformPort.backendFetch(backendPort.buildBackendEndpointUrl("tool_exposure", "/capabilities/tool-exposure", {
        user_id: state.sessionId, real_user_id: statePort.getProfileUserId(), character_pack_id: characterPort.getCurrentCharacterPackId()
      }), { method: "GET", cache: "no-store", connectTimeout: 5000 }).then(async (response) => {
        const payload = await readJsonResponse(response);
        if (!response.ok || !payload?.ok) throw Error("观察能力准备失败，正式请求时会重试");
      });
      const result = await systemMediaPort.waitForSystemMediaLyrics(request, 30000);
      if (result.timedOut) throw Error("观察能力准备超过 30 秒，首次请求仍可能较慢");
    });
    settingsPort.scheduleSettingsSnapshot();
    await pending;
    if (scope === screenObservationScope()) settingsPort.scheduleSettingsSnapshot();
  }

  function scheduleProactiveWake({ immediate = false, delayMs = null } = {}) {
    lifecycle.clearTimeout(proactiveWakeTimer);
    proactiveWakeTimer = 0;
    if (!platformPort.isTauriRuntime || !state.proactiveWakeEnabled) return;
    const now = Date.now();
    const delay = proactiveWakeDelay({ now, nextAllowedAt: proactiveWakeNextAllowedAt, intervalMs: getProactiveWakeIntervalMs(), immediate, delayMs });
    proactiveWakeTimer = lifecycle.setTimeout(() => {
      proactiveWakeTimer = 0;
      void runProactiveWake();
    }, delay);
  }

  async function runProactiveWake() {
    if (!state.proactiveWakeEnabled) return;
    if (resourceState.health === "online" && proactivePreparation.scope !== screenObservationScope()) void prepareProactiveObservation();
    if (proactivePreparation.status === "preparing") {
      scheduleProactiveWake({ delayMs: PROACTIVE_WAKE_RETRY_MS });
      return;
    }
    const remainingMs = proactiveWakeNextAllowedAt - Date.now();
    if (remainingMs > 0) {
      scheduleProactiveWake({ delayMs: remainingMs });
      return;
    }
    if (state.screenVisionEnabled && (!screenObservation.ready(screenObservationScope(), state)
        || (state.screenVisionPacking === "contact_sheet" && state.screenVisionSheetCount > 1
          && Date.now() - screenObservationLastSentAt < observationDuration(state) * 1000))) {
      scheduleProactiveWake({ delayMs: normalizeScreenObservationSettings(state).screenVisionSampleIntervalSec * 1000 });
      return;
    }
    if (!canStartProactiveWake()) {
      scheduleProactiveWake({ delayMs: Math.max(PROACTIVE_WAKE_RETRY_MS, getProactiveWakeRemainingMs()) });
      return;
    }
    const requestScope = screenObservationScope();
    try {
      const response = await platformPort.backendFetch(backendPort.buildBackendEndpointUrl("private_presence", "/think/presence"), {
        method: "POST", headers: { "Content-Type": "application/json" }, connectTimeout: 5000,
        body: JSON.stringify(characterPort.buildBackendCharacterContext())
      });
      const presence = await readJsonResponse(response);
      if (requestScope !== screenObservationScope() || !state.proactiveWakeEnabled) return;
      if (!response.ok || !presence?.ok) throw Error("私人会话状态暂时不可用");
      if (presence.shared) {
        const latestAt = Math.max(Number(presence.last_user_at || 0), Number(presence.last_reply_at || 0)) * 1000;
        const delay = latestAt + getProactiveWakeIntervalMs() - Date.now();
        if (presence.busy || delay > 0) {
          scheduleProactiveWake({ delayMs: Math.max(PROACTIVE_WAKE_RETRY_MS, delay) });
          return;
        }
      }
    } catch {
      scheduleProactiveWake({ delayMs: PROACTIVE_WAKE_RETRY_MS });
      return;
    }
    if (!canStartProactiveWake()) {
      scheduleProactiveWake({ delayMs: PROACTIVE_WAKE_RETRY_MS });
      return;
    }
    await sendProactiveWake();
    scheduleProactiveWake();
  }

  function getProactiveWakeIntervalMs() {
    return statePort.normalizeProactiveWakeIntervalSec(state.proactiveWakeIntervalSec) * 1000;
  }

  function getProactiveWakeRemainingMs() {
    return Math.max(0, proactiveWakeNextAllowedAt - Date.now());
  }

  function canStartProactiveWake() {
    if (sceneOwnsPlayback()) return false;
    if (proactiveWakeRunning || chatPort.sending || ttsPort.ttsActive || ttsPort.ttsQueue.length > 0 || bubblePort.replyDisplayActive) return false;
    if (["opening", "recording", "processing"].includes(voicePort.voiceInputState)) return false;
    if (!els.chatForm.hidden || !els.menu.hidden) return false;
    if (interactionPort.localInteractionActive) return false;
    const bubbleVisible = els.bubble.classList.contains("visible");
    if (bubbleVisible && !["status", "vision", "none"].includes(bubblePort.bubbleKind)) return false;
    return true;
  }

  async function sendProactiveWake() {
    const turnToken = chatPort.beginTurn("proactive");
    let didReply = false;
    const startedAt = Date.now();
    proactiveWakeLastAt = startedAt;
    proactiveWakeNextAllowedAt = startedAt + getProactiveWakeIntervalMs();
    proactiveWakeRunning = true;
    chatPort.setVoiceSending(true);
    bubblePort.resetReplyIdentity();
    ttsPort.resetStreamingTtsState(turnToken);
    attachmentsPort.resetFileDeliveryTracking();
    settingsPort.scheduleSettingsSnapshot();

    try {
      if (resourceState.health !== "online") {
        chatPort.markTurnLatency("resource-reload-start", { health: resourceState.health });
        const healthy = await characterPort.reloadCharacterResources({ silent: true });
        chatPort.markTurnLatency("resource-reload-finished", { healthy, health: resourceState.health });
        if (!healthy) return;
      }
      if (!chatPort.isTurnActive(turnToken)) return;
      const desktopScreenFrames = await latestDesktopScreenFramesForThink();
      if (!chatPort.isTurnActive(turnToken)) return;
      if (state.screenVisionEnabled && !desktopScreenFrames.length) {
        screenVisionStatus = screenVisionError ? "error" : "waiting";
        settingsPort.scheduleSettingsSnapshot();
        return;
      }
      screenObservationLastSentAt = Date.now();
      const stream = chatPort.sendThinkStream(buildProactiveWakeMessage(), turnToken, {
        turnKind: "desktop_pet_proactive",
        proactiveIntervalSeconds: getProactiveWakeIntervalMs() / 1000,
        transientUserMessage: true,
        desktopScreenFrames
      });
      didReply = await chatPort.processThinkStream(stream, turnToken, { backgroundObservation: true });
      proactiveWakeLastAt = startedAt;
    } catch (error) {
      if (!chatPort.isTurnActive(turnToken) || chatPort.isAbortLike(error)) return;
      statusPort.setRuntimeStatus(`主动搭话暂时失败：${formatError(error)}`, { mode: "error" });
    } finally {
      chatPort.markTurnLatency("turn-finished");
      if (chatPort.isTurnActive(turnToken)) {
        proactiveWakeRunning = false;
        chatPort.endTurn(turnToken);
        if (didReply && state.currentEmotion === characterPort.resolveEmotionEntry("thinking")?.id) {
          musicPort.setRestingPetEmotion();
        }
        if (didReply && !els.bubble.classList.contains("visible")) {
          bubblePort.setPetMotion("idle");
        }
        if (didReply) musicPort.scheduleMusicEmotionRestore();
        statusPort.updateActivityControls();
        settingsPort.scheduleSettingsSnapshot();
      }
      chatPort.finishTurnLatencyTrace(turnToken);
    }
  }

  function buildProactiveWakeMessage() {
    const prompt = getActiveCharacterText(
      "proactiveWakePrompt",
      "主人暂时没有说话。你像坐在旁边陪他一样，自然地轻声搭一句话。"
    );
    return `角色搭话风格参考（不代表必须开口）：\n${prompt}\n\n${MODERATE_PROACTIVE_PROMPT}`;
  }

  function cancelProactiveRun() { proactiveWakeRunning = false; }

  return {
    cancelProactiveRun,
    get proactiveWakeTimer() { return proactiveWakeTimer; },
    get proactiveWakeLastAt() { return proactiveWakeLastAt; },
    get proactivePreparation() { return proactivePreparation; },
    get proactiveWakeRunning() { return proactiveWakeRunning; },
    get screenObservationLastSentAt() { return screenObservationLastSentAt; },
    get screenObservation() { return screenObservation; },
    get screenVisionStatus() { return screenVisionStatus; },
    get screenVisionError() { return screenVisionError; },
    setScreenVisionEnabled,
    setProactiveWakeEnabled,
    setProactiveWakeIntervalSec,
    setScreenObservationSetting,
    screenObservationScope,
    scheduleScreenVisionCapture,
    latestDesktopScreenFramesForThink,
    stopScreenVisionCapture,
    clearScreenVisionWorkspace,
    prepareProactiveObservation,
    scheduleProactiveWake
  };
}
