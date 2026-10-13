import { attachDesktopCareContext } from "../care-feature.js";
import { SpeechStageReplay } from "../speech-delivery.js";
import { VoiceStageReplay, SPEECH_UNITS_PROTOCOL, utteranceDeliveryEvents, normalizeUnitEmotion } from "../speech-units.js";
import { CLIENT_MODE, THINK_IDLE_TIMEOUT_MS } from './constants.js';
import { getProfileDefaultEmotion } from './profile.js';
import { formatError } from './utils.js';
import { waitWithSignal } from '../request-cancellation.js';
import { createBackgroundTurnInbox } from './background-turn-stream.js';

/** Owns chat behavior and its temporary runtime state. */
export function createChatController({
  state,
  resourceState,
  els,
  platform: platformPort,
  lifecycle,
  scope: runtimeScope,
  perception: perceptionPort,
  character: characterPort,
  bubble: bubblePort,
  interaction: interactionPort,
  status: statusPort,
  persistence: statePort,
  backend: backendPort,
  voice: voicePort,
  tts: ttsPort,
  attachments: attachmentsPort,
  music: musicPort,
  settings: settingsPort,
  input: inputPort,
  systemMedia: systemMediaPort,
  care: carePort
}) {
  const { window } = platformPort;

  let sending = false;
  let voiceSending = false;

  let activeTurnToken = 0;
  let activeTurnScope = null;

  let activeTurnLatencyTrace = null;

  let thinkController = null;

  const backgroundTurns = createBackgroundTurnInbox({
    lifecycle, scope: runtimeScope,
    matches: event => event.session_id === state.sessionId
      && event.profile_user_id === statePort.getProfileUserId()
      && event.character_pack_id === characterPort.getCurrentCharacterPackId(),
    available: () => !sending && !voiceSending,
    begin: () => beginTurn("background"), end: endTurn,
    process: (events, token) => processThinkStream(events, token, { backgroundExecution: true }),
    onError: (error, token) => { if (isTurnActive(token)) bubblePort.showError(formatError(error)); },
    idleTimeout: THINK_IDLE_TIMEOUT_MS
  });

  function interruptReply({ announce = false, reason = "user_stopped_reply" } = {}) {
    const hadActivity = isReplyActive();
    if (announce && (sending || voiceSending)) {
      void requestActiveTurnStop(reason);
      bubblePort.showBubbleText("正在请求停止当前任务……", {
        transient: true,
        durationMs: 1800
      });
      statusPort.setRuntimeStatus("正在请求停止", { mode: "stopping" });
      statusPort.updateActivityControls();
      return true;
    }
    if (!announce && (sending || voiceSending)) {
      // Lifecycle takeovers still discard local presentation immediately, but
      // must not leave the host turn alive.
      void requestActiveTurnStop(reason);
    }
    activeTurnToken += 1;
    sending = false;
    backgroundTurns.cancel();
    perceptionPort.cancelProactiveRun();

    if (voicePort.hasRealtimeVoiceCallPlayback() || voicePort.hasRealtimeVoiceCallCommittedTurn()) {
      void voicePort.cancelRealtimeVoiceCallResponse(reason);
    }

    if (
      voicePort.realtimeVoiceTurn?.responseActive ||
      voicePort.realtimeVoiceTurn?.playbackActive ||
      voicePort.voiceInputState === "processing"
    ) {
      voicePort.closeRealtimeVoiceTurn(voicePort.realtimeVoiceTurn, reason);
      voicePort.setVoiceInputState(state.voiceInputEnabled ? "idle" : "disabled");
    }

    if (thinkController) {
      thinkController.abort();
      thinkController = null;
    }

    ttsPort.cancelTtsPrewarm();
    ttsPort.stopTts();
    interactionPort.clearLocalInteraction();
    bubblePort.resetReplyIdentity();
    ttsPort.resetStreamingTtsState();
    bubblePort.resetStreamedReplySegments();
    attachmentsPort.resetFileDeliveryTracking();
    bubblePort.cancelReplyDisplay();

    if (state.currentEmotion === characterPort.resolveEmotionEntry("thinking")?.id) {
      musicPort.setRestingPetEmotion();
    }
    bubblePort.setPetMotion("idle");
    musicPort.scheduleMusicEmotionRestore();

    if (announce) {
      bubblePort.showBubbleText(hadActivity ? "已停止回复。" : "现在没有正在回复的内容。", {
        transient: true,
        durationMs: hadActivity ? 1800 : 1500
      });
      statusPort.setRuntimeStatus(hadActivity ? "已停止回复" : "空闲中", { mode: hadActivity ? "stopped" : "idle" });
    } else {
      bubblePort.hideBubble();
    }

    statusPort.updateActivityControls();
    settingsPort.scheduleSettingsSnapshot();
    return hadActivity;
  }

  function isTurnActive(turnToken) {
    return !lifecycle.disposed && turnToken === activeTurnToken &&
      (!activeTurnScope || runtimeScope.isCurrent(activeTurnScope));
  }

  function isReplyActive() {
    return (
      sending || voiceSending ||
      ttsPort.ttsActive ||
      ttsPort.ttsQueue.length > 0 ||
      bubblePort.replyDisplayActive ||
      Boolean(voicePort.realtimeVoiceTurn?.responseActive) ||
      Boolean(voicePort.realtimeVoiceTurn?.playbackActive) ||
      voicePort.hasRealtimeVoiceCallPlayback() ||
      voicePort.getRealtimeVoiceCallTurns().some((turn) => turn.responseActive)
    );
  }

  function isAbortLike(error) {
    const name = String(error?.name || "").toLowerCase();
    const message = formatError(error).toLowerCase();
    return name === "aborterror" || message.includes("abort") || message.includes("cancel");
  }

  function nowForTurnLatency() {
    return window.performance?.now ? window.performance.now() : Date.now();
  }

  function isTurnLatencyDebugEnabled() {
    try {
      const storage = window.localStorage;
      return storage?.getItem("akane.debug.turn") === "1" || storage?.getItem("akane.debug.latency") === "1";
    } catch {
      return false;
    }
  }

  function createTurnLatencyTrace(kind, turnToken, details = {}) {
    if (!isTurnLatencyDebugEnabled()) return null;
    const startedAt = nowForTurnLatency();
    const seen = new Set();
    const trace = {
      turnToken,
      mark(event, eventDetails = {}) {
        if (!isTurnActive(turnToken)) return;
        const elapsedMs = Math.round((nowForTurnLatency() - startedAt) * 10) / 10;
        console.debug("[Akane Turn]", event, {
          ms: elapsedMs,
          kind,
          turnToken,
          ...details,
          ...eventDetails
        });
      },
      markOnce(event, eventDetails = {}) {
        if (seen.has(event)) return;
        seen.add(event);
        trace.mark(event, eventDetails);
      }
    };
    trace.mark("turn-created");
    return trace;
  }

  function markTurnLatency(event, details = {}) {
    activeTurnLatencyTrace?.mark(event, details);
  }

  function markTurnLatencyOnce(event, details = {}) {
    activeTurnLatencyTrace?.markOnce(event, details);
  }

  function finishTurnLatencyTrace(turnToken) {
    if (activeTurnLatencyTrace?.turnToken === turnToken) {
      activeTurnLatencyTrace = null;
    }
  }

  async function sendMessage(text, { requireNewTurn = false, includeAttachments = true } = {}) {
    if (requireNewTurn && (sending || voiceSending)) return false;
    if (includeAttachments && attachmentsPort.pendingAttachments.importing()) {
      inputPort.restoreFailedInput(String(text || ""));
      statusPort.setRuntimeStatus("附件正在接收，完成后再发送。", { mode: "working" });
      return false;
    }
    const trimmed = String(text || "").trim() || (includeAttachments && attachmentsPort.pendingAttachments.list().length ? "请查看这些附件。" : "");
    if (!trimmed) return;
    const attachmentBatch = includeAttachments ? attachmentsPort.pendingAttachments.take()
      : { scope: attachmentsPort.pendingAttachments.scope(), items: [] };
    const attachmentIds = attachmentBatch.items.map(item => item.attachmentId);

    inputPort.rememberInputHistory(trimmed);
    if (perceptionPort.proactiveWakeRunning) interruptReply({ announce: false });
    if (sending || voiceSending) {
      const accepted = await submitTurnSteer(trimmed, attachmentIds);
      if (!accepted && !attachmentsPort.pendingAttachments.restore(attachmentBatch) && attachmentsPort.pendingAttachments.scope() === attachmentBatch.scope) {
        statusPort.setRuntimeStatus("追加未成功；待发送区已满，原附件仍保留在工作台。", { mode: "error" });
      }
      return accepted;
    }
    interruptReply({ announce: false });
    const turnToken = beginTurn("user", { messageLength: trimmed.length });
    let restoreText = "";
    interactionPort.cancelEmotionPreview({ restore: true });
    interactionPort.clearLocalInteraction();
    bubblePort.resetReplyIdentity();
    ttsPort.resetStreamingTtsState(turnToken);
    attachmentsPort.resetFileDeliveryTracking();
    bubblePort.showThinking();
    statusPort.setRuntimeStatus("正在发送消息", { mode: "thinking" });
    bubblePort.showBubbleText("正在发送消息……", { kind: "thinking" });
    markTurnLatency("thinking-shown");
    settingsPort.scheduleSettingsSnapshot();

    try {
      if (resourceState.health !== "online") {
        markTurnLatency("resource-reload-start", { health: resourceState.health });
        const healthy = await characterPort.reloadCharacterResources();
        markTurnLatency("resource-reload-finished", { healthy, health: resourceState.health });
        if (!healthy) throw new Error("后端未连接");
        if (turnToken === activeTurnToken) activeTurnScope = runtimeScope.capture();
      }
      if (!isTurnActive(turnToken)) return;
      if (attachmentsPort.pendingAttachments.scope() !== attachmentBatch.scope) return;
      const stream = sendThinkStream(trimmed, turnToken, { attachmentIds });
      const rendered = await processThinkStream(stream, turnToken);
      if (!rendered) throw new Error("未收到回复");
      return true;
    } catch (error) {
      if (!isTurnActive(turnToken)) return;
      restoreText = trimmed;
      const restored = attachmentsPort.pendingAttachments.restore(attachmentBatch);
      const attachmentNotice = !restored && attachmentsPort.pendingAttachments.scope() === attachmentBatch.scope
        ? "；待发送区已满，原附件仍保留在工作台。" : "";
      bubblePort.showError((isAbortLike(error) ? "请求超时" : formatError(error)) + attachmentNotice);
      return false;
    } finally {
      markTurnLatency("turn-finished");
      if (isTurnActive(turnToken)) {
        sending = false;
        if (state.currentEmotion === characterPort.resolveEmotionEntry("thinking")?.id) {
          musicPort.setRestingPetEmotion();
        }
        if (!els.bubble.classList.contains("visible")) {
          bubblePort.setPetMotion("idle");
        }
        musicPort.scheduleMusicEmotionRestore();
        if (restoreText) {
          inputPort.restoreFailedInput(restoreText);
        }
        statusPort.updateActivityControls();
        settingsPort.scheduleSettingsSnapshot();
      }
      endTurn(turnToken);
      finishTurnLatencyTrace(turnToken);
    }
  }

  function createDesktopSourceMessageId() {
    return typeof globalThis.crypto?.randomUUID === "function"
      ? `desktop:${globalThis.crypto.randomUUID()}`
      : `desktop:${Date.now()}:${Math.random().toString(16).slice(2)}`;
  }

  function buildTurnControlPayload(message = "", attachmentIds = []) {
    return {
      user_id: state.sessionId,
      real_user_id: statePort.getProfileUserId(),
      actor_stable_id: `desktop:${statePort.getProfileUserId()}`,
      actor_display_name: "",
      source_message_id: createDesktopSourceMessageId(),
      message: String(message || "").trim(),
      current_attachment_ids: attachmentIds,
      timestamp: Math.floor(Date.now() / 1000),
      client_mode: CLIENT_MODE,
      client_capabilities: bubblePort.buildClientCapabilities(),
      character_pack_id: characterPort.getCurrentCharacterPackId(),
      current_visual: characterPort.buildCurrentVisual(),
      desktop_activity: musicPort.buildDesktopMusicActivity()
    };
  }

  async function postTurnControl(action, message = "", attachmentIds = []) {
    const response = await platformPort.backendFetch(
      backendPort.buildBackendEndpointUrl(`think_${action}`, `/think/${action}`, { t: Date.now() }),
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify(buildTurnControlPayload(message, attachmentIds))
      }
    );
    let result = {};
    try {
      result = await response.json();
    } catch {
      result = {};
    }
    return { response, result };
  }

  async function submitTurnSteer(message, attachmentIds = []) {
    try {
      const { response, result } = await postTurnControl("steer", message, attachmentIds);
      if (!response.ok || !result?.ok) {
        inputPort.restoreFailedInput(message);
        const reason = String(result?.reason || "active_turn_unavailable");
        const text = reason === "active_turn_finalizing"
          ? "当前任务正在收尾，这条还没有加入；我已放回输入框。"
          : "这条调整暂时没有加入当前任务，我已放回输入框。";
        bubblePort.showBubbleText(text, { transient: true, durationMs: 2200, kind: "status" });
        statusPort.setRuntimeStatus(text, { mode: "error" });
        return false;
      }
      statusPort.setRuntimeStatus("已追加任务要求", { mode: "working" });
      return true;
    } catch (error) {
      inputPort.restoreFailedInput(message);
      const text = `追加失败：${formatError(error)}`;
      bubblePort.showBubbleText(text, { transient: true, durationMs: 2200, kind: "status" });
      statusPort.setRuntimeStatus(text, { mode: "error" });
      return false;
    }
  }

  async function requestActiveTurnStop(reason = "user_stopped_reply") {
    const requestScope = runtimeScope.capture();
    try {
      const { response, result } = await postTurnControl("stop", reason);
      if (!runtimeScope.isCurrent(requestScope)) return false;
      if (!response.ok || !result?.ok) {
        statusPort.setRuntimeStatus("当前任务未能收到停止请求", { mode: "error" });
        return false;
      }
      statusPort.setRuntimeStatus("停止请求已送达，等待安全结束", { mode: "stopping" });
      // Stop revokes stale presentation; the host's terminal event may be
      // suppressed. Release only the local subscriber, not host work.
      if (backgroundTurns.active) {
        activeTurnToken += 1;
        sending = false;
        backgroundTurns.cancel();
        statusPort.updateActivityControls();
      }
      return true;
    } catch (error) {
      if (!runtimeScope.isCurrent(requestScope)) return false;
      statusPort.setRuntimeStatus(`停止请求失败：${formatError(error)}`, { mode: "error" });
      return false;
    }
  }

  async function* sendThinkStream(message, turnToken, options = {}) {
    const controller = new AbortController();
    thinkController = controller;
    let timeoutId;
    const resetIdleTimeout = () => {
      lifecycle.clearTimeout(timeoutId);
      timeoutId = lifecycle.setTimeout(() => {
        controller.abort(new DOMException("等待后端响应超时", "TimeoutError"));
      }, THINK_IDLE_TIMEOUT_MS);
    };
    resetIdleTimeout();
    try {
      markTurnLatency("turn-context-start");
      if (platformPort.isTauriRuntime) {
        Promise.resolve(systemMediaPort.refreshSystemMediaSnapshot()).catch(() => {});
        markTurnLatency("media-snapshot-background");
      }
      const mediaOptions = { ...options, waitForLyricsHydration: options.waitForLyricsHydration === true };
      const lyricsHydrationPromise = systemMediaPort.hydrateSystemMediaLyricsForTurn(message, mediaOptions)
        .then((result) => {
          markTurnLatency("lyrics-hydration-ready", {
            waited: Boolean(result?.waited),
            timedOut: Boolean(result?.timedOut),
            status: String(result?.status || systemMediaPort.systemMediaLyrics.status || "")
          });
          return result;
        })
        .catch((error) => {
          markTurnLatency("lyrics-hydration-error", { error: formatError(error).slice(0, 120) });
          return { waited: false, status: "error" };
        });
      if (systemMediaPort.shouldWaitForSystemMediaLyricsForTurn(message, mediaOptions)) {
        await waitWithSignal(lyricsHydrationPromise, controller.signal);
      } else {
        markTurnLatency("lyrics-hydration-background");
      }
      if (!isTurnActive(turnToken)) {
        return;
      }
      const desktopScreenFrames = Array.isArray(options.desktopScreenFrames)
        ? options.desktopScreenFrames
        : await waitWithSignal(perceptionPort.latestDesktopScreenFramesForThink(), controller.signal);
      if (!isTurnActive(turnToken)) {
        return;
      }
      const requestPayload = attachDesktopCareContext({
        user_id: state.sessionId,
        real_user_id: statePort.getProfileUserId(),
        source_message_id: createDesktopSourceMessageId(),
        message,
        turn_kind: String(options.turnKind || ""),
        ...(options.turnKind === "desktop_pet_proactive"
          ? { proactive_interval_seconds: Number(options.proactiveIntervalSeconds || 60) } : {}),
        ...(Array.isArray(options.attachmentIds) ? { current_attachment_ids: options.attachmentIds } : {}),
        transient_user_message: Boolean(options.transientUserMessage),
        client_mode: CLIENT_MODE,
        character_pack_id: characterPort.getCurrentCharacterPackId(),
        client_capabilities: bubblePort.buildClientCapabilities(),
        current_visual: characterPort.buildCurrentVisual(),
        desktop_screen_frames: desktopScreenFrames,
        desktop_activity: musicPort.buildDesktopMusicActivity({ excludePaused: desktopScreenFrames.length > 0 || options.turnKind === "desktop_pet_proactive" })
      }, carePort.buildDesktopCareContext(), carePort.getCareFeatureStatus());
      const requestInit = {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        signal: controller.signal,
        body: JSON.stringify(requestPayload)
      };

      if (platformPort.isTauriRuntime) {
        requestInit.connectTimeout = 30_000;
      }

      markTurnLatency("think-request-start");
      const response = await platformPort.backendFetch(backendPort.buildBackendEndpointUrl("think", "/think", { t: Date.now() }), requestInit);
      markTurnLatency("think-response-headers", { status: response.status });
      resetIdleTimeout();
      if (!isTurnActive(turnToken)) return;
      if (!response.ok) {
        throw new Error(await voicePort.readBackendErrorMessage(response, `HTTP ${response.status}`));
      }
      for await (const event of backendPort.readNdjsonEvents(response)) {
        if (!isTurnActive(turnToken)) return;
        // Tool work and heartbeats keep a long turn alive. Only silence times out.
        resetIdleTimeout();
        yield event;
      }
    } finally {
      lifecycle.clearTimeout(timeoutId);
      if (thinkController === controller) thinkController = null;
    }
  }

  async function processThinkStream(stream, turnToken, { backgroundObservation = false, backgroundExecution = false } = {}) {
    let partialSpeech = "";
    let rendered = false;
    let streamErrored = false;
    let streamErrorMessage = "";
    let waitingPhase = "processing";
    const speechReplay = new SpeechStageReplay();
    const voiceReplay = new VoiceStageReplay();
    const unitReplay = new VoiceStageReplay();
    const unitTexts = new Map();
    const acceptedUnits = new Set();
    const completedUnits = new Set();
    const voicedUnits = new Set();
    let unitStage = 0;
    let speechUnits = false;
    let rootEmotion = "";
    let legacyEmotionApplied = false;
    let acceptedSpeechIndex = 0;
    bubblePort.resetStreamedReplySegments(turnToken);

    const deliverUnitEvent = (event) => {
      const presentationKey = `${turnToken}:${unitStage}:${event.unit_index}`;
      const text = String(event.type === "speech_unit_text" ? event.text : (event.display_text || unitTexts.get(event.unit_index) || "")).trim();
      if (text) {
        unitTexts.set(event.unit_index, text);
        if (unitReplay.accept({ index: event.unit_index, text })) {
          acceptedUnits.add(presentationKey);
          const emotion = normalizeUnitEmotion(event.emotion)
            || (event.unit_index === 0 ? rootEmotion || getProfileDefaultEmotion() : "");
          if (bubblePort.queueSpeechUnitText(text, turnToken, presentationKey, emotion)) rendered = true;
        }
      }
      if (event.type === "voice_segment") {
        if (voiceReplay.accept(event)) {
          voicedUnits.add(presentationKey);
          ttsPort.queueSpeechUnitAudio({ ...event, presentation_key: presentationKey }, turnToken);
        }
      } else if (event.type === "voice_unit_end") {
        const queueTextOnly = acceptedUnits.has(presentationKey) && !completedUnits.has(presentationKey);
        completedUnits.add(presentationKey);
        ttsPort.finishSpeechUnitAudio({ ...event, display_text: text, presentation_key: presentationKey, queue_text_only: queueTextOnly }, turnToken);
      }
    };

    const finishPendingUnitTexts = () => {
      // A broken stream can close text but never reach say or its end marker.
      // Keep those complete texts readable in the same delivery order.
      for (const [unitIndex, text] of unitTexts) {
        const presentationKey = `${turnToken}:${unitStage}:${unitIndex}`;
        if (acceptedUnits.has(presentationKey) && !completedUnits.has(presentationKey)) {
          deliverUnitEvent({ type: "voice_unit_end", unit_index: unitIndex, display_text: text,
            has_voice: voicedUnits.has(presentationKey) });
        }
      }
    };

    const finishUnitStage = () => {
      voiceReplay.finishStage();
      unitReplay.finishStage();
      unitStage += 1;
      unitTexts.clear();
    };

    for await (const event of stream) {
      if (!isTurnActive(turnToken)) return false;
      const type = String(event?.type || "").trim().toLowerCase();

      if (type === "stream_start") {
        markTurnLatency("backend-received");
        if (!rendered && !backgroundObservation) {
          const message = event.phase === "resuming" ? "正在恢复任务，等待后端处理"
            : event.phase === "queued" ? "排队任务已接收，等待后端处理" : "消息已收到，等待后端处理";
          statusPort.setRuntimeStatus(message, { mode: "thinking" });
          bubblePort.showBubbleText(message + "……", { kind: "thinking" });
        }
      } else if (type === "turn_progress") {
        if (event.phase === "retrying_model") waitingPhase = "retrying";
        if (!rendered && !backgroundObservation && waitingPhase !== "tool") {
          const seconds = Math.floor(Math.max(0, Number(event.elapsed_ms) || 0) / 1000);
          const text = waitingPhase === "retrying" ? "模型请求未成功，正在重试"
            : waitingPhase === "model" ? "消息已收到，正在等待模型回复" : "消息已收到，后端仍在处理";
          const display = text + (seconds ? `（${seconds} 秒）` : "");
          statusPort.setRuntimeStatus(display, { mode: "thinking" });
          bubblePort.showBubbleText(display, { kind: "thinking" });
        }
      } else if (type === "turn_start") {
        markTurnLatency("stream-turn-start");
        waitingPhase = "model";
        if (!rendered && !backgroundObservation) {
          bubblePort.showThinking();
          statusPort.setRuntimeStatus("消息已收到，正在等待模型回复", { mode: "thinking" });
          bubblePort.showBubbleText("消息已收到，正在等待模型回复……", { kind: "thinking" });
        }
      } else if (type === "ui") {
        // Wait until the body protocol is known. A top-level expression must
        // not flash before the first unit's expression arrives.
        rootEmotion = normalizeUnitEmotion(event.emotion);
        legacyEmotionApplied = false;
      } else if (type === "speech_mode") {
        if (speechUnits) finishPendingUnitTexts();
        speechUnits = event.speech_protocol === SPEECH_UNITS_PROTOCOL;
        rootEmotion = normalizeUnitEmotion(event.emotion) || rootEmotion;
        if (speechUnits) ttsPort.beginSpeechUnits(turnToken);
        finishUnitStage();
      } else if (type === "speech_unit_text" || type === "voice_segment" || type === "voice_unit_end") {
        speechUnits = true;
        deliverUnitEvent(event);
      } else if (type === "speech_warning") {
        if (event.code === "missing_say") statusPort.setRuntimeStatus("这段回复缺少配音台词，已保留文字。", { mode: "error" });
      } else if (type === "speech_chunk") {
        const chunk = String(event?.text || "");
        if (chunk) {
          partialSpeech += chunk;
          // Keep chunks as transport progress only. They are not stable display
          // units and may end halfway through a sentence. The authoritative
          // speech_segment events below own stable sentence delivery.
        }
      } else if (type === "speech_segment") {
        const text = speechReplay.push(event?.text, event?.index);
        if (!text) continue;
        if (text) markTurnLatencyOnce("first-speech-segment", { chars: text.length, index: event?.index });
        if (!speechUnits) {
          if (rootEmotion && !legacyEmotionApplied && (!backgroundObservation || rendered)) {
            bubblePort.applyPayloadEmotion({ emotion: rootEmotion });
            legacyEmotionApplied = true;
          }
          if (bubblePort.queueStreamedReplySegment(text, turnToken)) rendered = true;
          ttsPort.queueStreamedTtsSegment(text, turnToken, acceptedSpeechIndex++);
        }
      } else if (type === "assistant_stage_decision") {
        if (speechUnits) finishPendingUnitTexts();
        ttsPort.flushSpeechUnitAudio(turnToken);
        speechReplay.finishStage();
        finishUnitStage();
      } else if (type === "speech_reset") {
        partialSpeech = String(event?.speech || "");
      } else if (type === "file_ready" || type === "generated_file_ready") {
        void attachmentsPort.handleDesktopFileDeliveryEvent(event);
      } else if (type === "browser_open_requested") {
        void attachmentsPort.handleBrowserOpenEvent(event);
      } else if (type === "assistant_working") {
        waitingPhase = "tool";
        bubblePort.showToolWorking(event, { hasShownReply: rendered });
      } else if (type === "turn_steer_applied") {
        statusPort.setRuntimeStatus("已按新增要求继续处理", { mode: "working" });
      } else if (type === "turn_steer_failed") {
        statusPort.setRuntimeStatus("新增要求未能写入当前上下文", { mode: "error" });
      } else if (type === "turn_stopped") {
        rendered = true;
        bubblePort.showBubbleText("当前任务已停止。", { transient: true, durationMs: 1800, kind: "status" });
        statusPort.setRuntimeStatus("当前任务已停止", { mode: "stopped" });
      } else if (type === "turn_queued") {
        rendered = true;
        const text = event?.status === "duplicate"
          ? "这条消息已经收到，正在按顺序处理。"
          : "这条消息已排队，稍后会继续回复。";
        bubblePort.showBubbleText(text, { transient: true, durationMs: 2200, kind: "status" });
        statusPort.setRuntimeStatus(text, { mode: "working" });
      } else if (type === "final" || type === "final_ui") {
        const payload = event?.payload || event;
        speechUnits ||= payload?.speech_protocol === SPEECH_UNITS_PROTOCOL || Array.isArray(payload?.utterances);
        rootEmotion = normalizeUnitEmotion(payload?.emotion) || rootEmotion;
        if (speechUnits) {
          finishPendingUnitTexts();
          finishUnitStage();
          ttsPort.beginSpeechUnits(turnToken);
          const finalEvents = utteranceDeliveryEvents(payload?.utterances, rootEmotion || getProfileDefaultEmotion());
          unitReplay.beginFinal(finalEvents.filter(event => event.type === "speech_unit_text")
            .map(event => ({ index: event.unit_index, text: event.text })));
          voiceReplay.beginFinal(finalEvents.filter(event => event.type === "voice_segment"));
          for (const unitEvent of finalEvents) deliverUnitEvent(unitEvent);
          ttsPort.flushSpeechUnitAudio(turnToken);
        }
        if (payload?._deliberate_silence) {
          partialSpeech = "";
          continue;
        }
        const canonicalSpeech = String(payload?.speech || payload?.text || "").trim();
        const finalSegments = canonicalSpeech
          ? bubblePort.splitSpeechText(canonicalSpeech)
          : bubblePort.normalizeSegments(payload?.speech_segments || payload?.segments);
        for (const segment of speechUnits ? [] : bubblePort.missingFinalReplySegments(finalSegments)) {
          if (bubblePort.queueStreamedReplySegment(segment, turnToken)) {
            rendered = true;
            ttsPort.queueStreamedTtsSegment(segment, turnToken);
          }
        }
        const hasStreamedReply = bubblePort.streamedReplyTexts.length > 0;
        if (bubblePort.renderPayload(payload, { suppressSpeech: hasStreamedReply, turnToken })) {
          rendered = true;
        }
      } else if (type === "npc_turn") {
        if (!rendered && bubblePort.renderPayload(event, { turnToken })) {
          rendered = true;
        }
      } else if (type === "stream_error" || type === "error") {
        streamErrored = true;
        streamErrorMessage = String(event?.message || "Stream error");
        if (backgroundExecution) {
          // A partial preface is not a successful completion. Keep its history,
          // but make the terminal failure visible instead of silently ending.
          bubblePort.showError(streamErrorMessage);
          rendered = true;
        }
        if (event?.partial && !rendered) {
          if (bubblePort.renderPayload({ ...event.partial, ...(speechUnits ? { speech_protocol: SPEECH_UNITS_PROTOCOL } : {}) }, { turnToken })) rendered = true;
        }
      } else if (type === "stream_end") {
        ttsPort.flushSpeechUnitAudio(turnToken);
        ttsPort.flushStreamingTtsPending({ turnToken });
        if (event?.partial && !rendered) {
          if (bubblePort.renderPayload({ ...event.partial, ...(speechUnits ? { speech_protocol: SPEECH_UNITS_PROTOCOL } : {}) }, { turnToken })) rendered = true;
        }
      }
    }

    if (!isTurnActive(turnToken)) return false;
    if (speechUnits) finishPendingUnitTexts();
    ttsPort.flushSpeechUnitAudio(turnToken);
    ttsPort.flushStreamingTtsPending({ turnToken });
    if (speechUnits) ttsPort.finishSpeechUnits(turnToken);
    if (!rendered && partialSpeech.trim()) {
      rendered = bubblePort.renderPayload({ speech: partialSpeech.trim(), ...(speechUnits ? { speech_protocol: SPEECH_UNITS_PROTOCOL } : {}) }, { turnToken });
    }

    if (!rendered && streamErrored) {
      throw new Error(streamErrorMessage || "未收到完整回应");
    }
    if (!rendered && bubblePort.bubbleKind === "thinking") {
      bubblePort.hideBubble();
    }
    return rendered;
  }

  function beginTurn(kind, detail = {}) {
    const token = ++activeTurnToken;
    activeTurnScope = runtimeScope.capture();
    activeTurnLatencyTrace = createTurnLatencyTrace(kind, token, detail);
    sending = true;
    return token;
  }

  function endTurn(token) {
    if (token !== activeTurnToken) return;
    sending = false;
    statusPort.updateActivityControls();
    settingsPort.scheduleSettingsSnapshot();
  }
  function setVoiceSending(value) { voiceSending = Boolean(value); }

  return {
    beginTurn, endTurn, setVoiceSending,
    get sending() { return sending || voiceSending; },
    get activeTurnToken() { return activeTurnToken; },
    get activeTurnLatencyTrace() { return activeTurnLatencyTrace; },
    get thinkController() { return thinkController; },
    interruptReply,
    isTurnActive,
    isReplyActive,
    isAbortLike,
    createTurnLatencyTrace,
    markTurnLatency,
    finishTurnLatencyTrace,
    sendMessage,
    sendThinkStream,
    processThinkStream,
    receiveBackgroundTurnEvents: backgroundTurns.receive
  };
}
