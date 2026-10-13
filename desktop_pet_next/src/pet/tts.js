import { sceneOwnsPlayback } from "../scene-desktop-bridge.js";
import { voiceQueueItem, canMergeVoiceItems, continuousVoiceChunks, CONTINUOUS_VOICE_MAX_CHARS } from "../speech-units.js";
import { getActiveCharacterText, getActiveCharacterProfile } from "../character-profile.js";
import { segmentSpeechForDelivery } from "../speech-delivery.js";
import { TTS_SHORT_SEGMENT_MAX_CHARS, TTS_SHORT_SEGMENT_HOLD_MS, TTS_PREWARM_DELAY_MS, TTS_PREWARM_COOLDOWN_MS, TTS_PREWARM_TIMEOUT_MS, TTS_PREWARM_TEXT, TTS_TIMEOUT_MS, TTS_SLOW_REQUEST_MS, TTS_CHUNK_SOFT_LIMIT } from './constants.js';
import { formatError, friendlyErrorMessage } from './utils.js';

/** Owns tts behavior and its temporary runtime state. */
export function createTtsController({
  state,
  resourceState,
  els,
  platform: platformPort,
  lifecycle,
  chat: chatPort,
  bubble: bubblePort,
  perception: perceptionPort,
  voice: voicePort,
  character: characterPort,
  backend: backendPort,
  status: statusPort,
  settings: settingsPort,
  music: musicPort
}) {
  const { window } = platformPort;

  let ttsToken = 0;

  let ttsController = null;

  let ttsObjectUrl = "";

  let ttsActive = false;

  let ttsPlaybackToken = 0;

  let ttsQueue = [];

  let ttsPrefetch = null;

  let lastTtsSignature = "";

  let resolveTtsWait = null;

  let streamingTtsTurnToken = 0;

  let streamingTtsText = "";

  let streamingTtsPendingShort = "";

  let streamingTtsPendingShortKey = "";

  let streamingTtsPendingShortTimer = 0;

  let speechUnitPending = null;

  let speechUnitPendingTimer = 0;

  let speechUnitsTurnToken = 0;

  let speechUnitsStreaming = false;

  let speechUnitsMutedTurnToken = 0;

  const streamingTtsSegmentKeys = new Set();

  let ttsPrewarmTimer = 0;

  let ttsPrewarmController = null;

  let ttsPrewarmInFlightKey = "";

  const ttsPrewarmReadyAtByKey = new Map();

  function resetStreamingTtsState(turnToken = 0) {
    clearSpeechUnitAudio();
    speechUnitsTurnToken = 0;
    clearStreamingTtsPending();
    streamingTtsTurnToken = Number.isFinite(Number(turnToken)) ? Number(turnToken) : 0;
    streamingTtsText = "";
    streamingTtsSegmentKeys.clear();
  }

  function ensureStreamingTtsTurn(turnToken) {
    const normalizedToken = Number.isFinite(Number(turnToken)) ? Number(turnToken) : 0;
    if (streamingTtsTurnToken === normalizedToken) return;
    resetStreamingTtsState(normalizedToken);
  }

  function queueStreamedTtsSegment(text, turnToken, segmentIndex = null) {
    const normalized = normalizeTtsText(text);
    if (!normalized || !chatPort.isTurnActive(turnToken) || !state.voiceEnabled) return;
    if (resourceState.tts?.enabled === false) return;

    ensureStreamingTtsTurn(turnToken);
    const textKey = bubblePort.buildSpeechTextKey(normalized);
    if (!textKey) return;
    const numericIndex = segmentIndex === null || segmentIndex === undefined ? NaN : Number(segmentIndex);
    const segmentKey = Number.isFinite(numericIndex)
      ? `${numericIndex}:${textKey}`
      : `tail:${streamingTtsSegmentKeys.size}:${textKey}`;
    if (streamingTtsSegmentKeys.has(segmentKey)) return;

    streamingTtsSegmentKeys.add(segmentKey);
    streamingTtsText = normalizeTtsText(streamingTtsText ? `${streamingTtsText}${normalized}` : normalized);
    bufferStreamingTtsSegment(normalized, turnToken, segmentKey);
  }

  function bufferStreamingTtsSegment(text, turnToken, segmentKey = "") {
    const normalized = normalizeTtsText(text);
    if (!normalized || !chatPort.isTurnActive(turnToken) || !state.voiceEnabled) return;

    lifecycle.clearTimeout(streamingTtsPendingShortTimer);
    streamingTtsPendingShortTimer = 0;
    const combined = normalizeTtsText(
      streamingTtsPendingShort ? `${streamingTtsPendingShort} ${normalized}` : normalized
    );
    const combinedKey = [streamingTtsPendingShortKey, segmentKey].filter(Boolean).join("+");
    streamingTtsPendingShort = "";
    streamingTtsPendingShortKey = "";

    if (combined.length <= TTS_SHORT_SEGMENT_MAX_CHARS) {
      streamingTtsPendingShort = combined;
      streamingTtsPendingShortKey = combinedKey;
      streamingTtsPendingShortTimer = lifecycle.setTimeout(() => {
        streamingTtsPendingShortTimer = 0;
        flushStreamingTtsPending({ turnToken });
      }, TTS_SHORT_SEGMENT_HOLD_MS);
      return;
    }

    queueTtsItems([combined], `stream:${turnToken}:${combinedKey || bubblePort.buildSpeechTextKey(combined)}`, {
      append: true,
      preserveSegments: true,
      reply: true
    });
  }

  function flushStreamingTtsPending({ turnToken = streamingTtsTurnToken } = {}) {
    const text = streamingTtsPendingShort;
    const segmentKey = streamingTtsPendingShortKey;
    clearStreamingTtsPending();
    if (!text || !chatPort.isTurnActive(turnToken) || !state.voiceEnabled) return false;
    queueTtsItems([text], `stream:${turnToken}:${segmentKey || bubblePort.buildSpeechTextKey(text)}`, {
      append: true,
      preserveSegments: true,
      reply: true
    });
    return true;
  }

  function clearStreamingTtsPending() {
    lifecycle.clearTimeout(streamingTtsPendingShortTimer);
    streamingTtsPendingShortTimer = 0;
    streamingTtsPendingShort = "";
    streamingTtsPendingShortKey = "";
  }

  function queueLiveTtsPayloadItems(items, signature = "") {
    const normalized = (Array.isArray(items) ? items : [items])
      .map((item) => normalizeTtsText(item))
      .filter(Boolean);
    if (!normalized.length) return;

    if (streamingTtsText) {
      const tail = removeStreamingTtsPrefix(normalized.join(""));
      if (tail) {
        const tailSegments = bubblePort.splitSpeechText(tail);
        for (const segment of tailSegments.length ? tailSegments : [tail]) {
          bufferStreamingTtsSegment(segment, chatPort.activeTurnToken, `tail:${bubblePort.buildSpeechTextKey(segment)}`);
        }
      }
      flushStreamingTtsPending({ turnToken: chatPort.activeTurnToken });
      return;
    }

    queueTtsItems(normalized, signature, { reply: true });
  }

  function removeStreamingTtsPrefix(text) {
    const finalText = normalizeTtsText(text);
    const prefix = normalizeTtsText(streamingTtsText);
    if (!finalText || !prefix) return finalText;
    if (finalText.startsWith(prefix)) return normalizeTtsText(finalText.slice(prefix.length));

    const finalKey = bubblePort.buildSpeechTextKey(finalText);
    const prefixKey = bubblePort.buildSpeechTextKey(prefix);
    if (!prefixKey || !finalKey.startsWith(prefixKey)) return "";
    if (finalKey.length <= prefixKey.length) return "";

    let compactCount = 0;
    let sliceIndex = 0;
    for (let index = 0; index < finalText.length; index += 1) {
      if (!/\s/.test(finalText[index])) compactCount += 1;
      if (compactCount >= prefixKey.length) {
        sliceIndex = index + 1;
        break;
      }
    }
    return normalizeTtsText(finalText.slice(sliceIndex));
  }

  function scheduleTtsPrewarm({ force = false, delayMs = TTS_PREWARM_DELAY_MS } = {}) {
    lifecycle.clearTimeout(ttsPrewarmTimer);
    ttsPrewarmTimer = 0;
    if (!canRunTtsPrewarm()) return;
    ttsPrewarmTimer = lifecycle.setTimeout(() => {
      ttsPrewarmTimer = 0;
      void runTtsPrewarm({ force });
    }, Math.max(0, Number(delayMs) || 0));
  }

  function cancelTtsPrewarm() {
    lifecycle.clearTimeout(ttsPrewarmTimer);
    ttsPrewarmTimer = 0;
    if (ttsPrewarmController) {
      ttsPrewarmController.abort();
      ttsPrewarmController = null;
    }
    ttsPrewarmInFlightKey = "";
  }

  function canRunTtsPrewarm() {
    if (!state.voiceEnabled || resourceState.health !== "online" || resourceState.tts?.enabled === false) return false;
    if (chatPort.sending || ttsActive || ttsQueue.length > 0 || bubblePort.replyDisplayActive || perceptionPort.proactiveWakeRunning) return false;
    if (["opening", "recording", "processing"].includes(voicePort.voiceInputState)) return false;
    return true;
  }

  function getTtsPrewarmKey() {
    return [
      characterPort.getCharacterRuntimeKey(),
      String(resourceState.tts?.endpoint || ""),
      String(resourceState.tts?.responseMediaType || "")
    ].join("::");
  }

  async function runTtsPrewarm({ force = false } = {}) {
    if (!canRunTtsPrewarm()) return;
    const key = getTtsPrewarmKey();
    const warmedAt = Number(ttsPrewarmReadyAtByKey.get(key) || 0);
    if (!force && warmedAt && Date.now() - warmedAt < TTS_PREWARM_COOLDOWN_MS) return;
    if (ttsPrewarmInFlightKey === key) return;

    const controller = new AbortController();
    const timeoutId = lifecycle.setTimeout(() => controller.abort(), Math.max(TTS_PREWARM_TIMEOUT_MS, 60_000));
    const startedAt = performance.now();
    ttsPrewarmController = controller;
    ttsPrewarmInFlightKey = key;

    try {
      const requestInit = {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        signal: controller.signal,
        body: JSON.stringify({
          text: TTS_PREWARM_TEXT,
          prewarm: true,
          ...characterPort.buildBackendCharacterContext()
        })
      };
      if (platformPort.isTauriRuntime) requestInit.connectTimeout = TTS_PREWARM_TIMEOUT_MS;

      const response = await platformPort.backendFetch(backendPort.buildBackendEndpointUrl("tts", "/tts", { t: Date.now() }), requestInit);
      if (response.ok) {
        await response.arrayBuffer();
        ttsPrewarmReadyAtByKey.set(key, Date.now());
        logTtsTiming("prewarm-ready", {
          requestMs: Math.round(performance.now() - startedAt),
          character: characterPort.getCurrentCharacterPackId()
        });
      } else {
        logTtsTiming("prewarm-skipped", { status: response.status });
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        logTtsTiming("prewarm-failed", { error: formatError(error) });
      }
    } finally {
      lifecycle.clearTimeout(timeoutId);
      if (ttsPrewarmController === controller) ttsPrewarmController = null;
      if (ttsPrewarmInFlightKey === key) ttsPrewarmInFlightKey = "";
    }
  }

  function queueTtsItems(items, signature = "", { append = false, preserveSegments = false, reply = false } = {}) {
    if (sceneOwnsPlayback()) return;
    const normalized = (Array.isArray(items) ? items : [items])
      .map((item) => normalizeTtsText(item))
      .filter(Boolean);
    if (!state.voiceEnabled || !normalized.length) return;
    if (resourceState.tts?.enabled === false) {
      statusPort.setRuntimeStatus("后端语音暂未开启", { mode: "error" });
      return;
    }
    const nextItems = reply
      ? normalized.flatMap((displayText) => buildTtsQueueItems([displayText], { preserveSegments: true })
        .map((text) => ({ text, reply, displayText })))
      : buildTtsQueueItems(normalized, { preserveSegments }).map((text) => ({ text, reply }));
    if (!nextItems.length) return;
    cancelTtsPrewarm();

    if (append) {
      ttsQueue.push(...nextItems);
      if (!ttsActive) {
        ttsToken += 1;
        void runTtsQueue(ttsToken);
      } else {
        ttsPrefetch?.();
      }
      return;
    }

    if (signature && signature === lastTtsSignature) return;

    stopTts({ resetSignature: false });
    lastTtsSignature = signature || `tts:${normalized.join("\u241e")}`;
    ttsToken += 1;
    const token = ttsToken;
    ttsQueue = nextItems;
    void runTtsQueue(token);
  }

  function queueSpeechUnitAudio(event, turnToken) {
    if (!chatPort.isTurnActive(turnToken) || !state.voiceEnabled || speechUnitsMutedTurnToken === turnToken
        || resourceState.tts?.enabled === false || sceneOwnsPlayback()) return;
    speechUnitsTurnToken = turnToken;
    let item = { ...voiceQueueItem(event), turnToken };
    if (!item.text) return;
    const continuous = usesContinuousTts();
    if (speechUnitPending && (!canMergeVoiceItems(speechUnitPending, item)
        || (continuous && (speechUnitPending.unitIndex !== item.unitIndex
            || speechUnitPending.text.length + item.text.length > CONTINUOUS_VOICE_MAX_CHARS)))) {
      flushSpeechUnitAudio(turnToken);
    }
    if (speechUnitPending) item = { ...item, text: speechUnitPending.text + item.text,
      displayText: item.displayText || speechUnitPending.displayText };
    clearSpeechUnitAudio();
    speechUnitPending = item;
    if (continuous) {
      if (item.text.length >= CONTINUOUS_VOICE_MAX_CHARS) flushSpeechUnitAudio(turnToken);
      return; // Flush on say completion, unit/style change or stage/final end.
    }
    if (item.text.length <= TTS_SHORT_SEGMENT_MAX_CHARS) {
      speechUnitPendingTimer = lifecycle.setTimeout(() => flushSpeechUnitAudio(turnToken), TTS_SHORT_SEGMENT_HOLD_MS);
    } else {
      flushSpeechUnitAudio(turnToken);
    }
  }

  function clearSpeechUnitAudio() {
    lifecycle.clearTimeout(speechUnitPendingTimer);
    speechUnitPendingTimer = 0;
    speechUnitPending = null;
  }

  function finishSpeechUnitAudio(event, turnToken) {
    if (!chatPort.isTurnActive(turnToken) || speechUnitsTurnToken !== turnToken) return;
    if (speechUnitPending?.unitIndex === event.unit_index) flushSpeechUnitAudio(turnToken);
    if (event.has_voice === false && event.queue_text_only && state.voiceEnabled && speechUnitsMutedTurnToken !== turnToken
        && resourceState.tts?.enabled !== false && !sceneOwnsPlayback()) {
      // Silent stage directions retain their place among spoken expressions.
      ttsQueue.push({ textOnly: true, reply: true, displayText: event.display_text,
        presentationKey: event.presentation_key, turnToken });
      if (!ttsActive) {
        ttsToken += 1;
        void runTtsQueue(ttsToken);
      } else ttsPrefetch?.();
    }
  }

  function flushSpeechUnitAudio(turnToken) {
    const item = speechUnitPending;
    clearSpeechUnitAudio();
    if (!item || !chatPort.isTurnActive(turnToken) || !state.voiceEnabled) return;
    cancelTtsPrewarm();
    ttsQueue.push(...buildTtsQueueItems([item.text], { preserveSegments: true }).map(text => ({ ...item, text })));
    if (!ttsActive) {
      ttsToken += 1;
      void runTtsQueue(ttsToken);
    } else {
      ttsPrefetch?.();
    }
  }

  async function testTts() {
    if (!state.voiceEnabled) {
      voicePort.setVoiceEnabled(true);
    }
    queueTtsItems([getActiveCharacterText("ttsTestText")], `test:${Date.now()}`);
  }

  async function previewTts(text) {
    const normalized = normalizeTtsText(text) || getActiveCharacterText("ttsTestText");
    if (!state.voiceEnabled) {
      voicePort.setVoiceEnabled(true);
    }
    queueTtsItems([normalized], `preview:${Date.now()}`);
  }

  function stopTts({ resetSignature = true } = {}) {
    if (!state.voiceEnabled) speechUnitsMutedTurnToken = speechUnitsTurnToken;
    speechUnitsStreaming = false;
    clearSpeechUnitAudio();
    clearStreamingTtsPending();
    ttsPrefetch = null;
    ttsToken += 1;
    ttsQueue = [];
    if (resetSignature) lastTtsSignature = "";
    if (ttsController) {
      ttsController.abort();
      ttsController = null;
    }
    finishTtsWait(false);
    stopTtsAudio();
    setTtsActive(false);
  }

  async function runTtsQueue(token) {
    setTtsActive(true);
    let pendingPrepared = null;
    // A later model sentence may arrive after playback has begun. Wake this
    // owner's single lookahead slot immediately instead of waiting for ended.
    const prefetch = () => {
      if (token === ttsToken && state.voiceEnabled && !pendingPrepared) {
        pendingPrepared = startNextTtsPrepare(token);
      }
    };
    ttsPrefetch = prefetch;
    try {
      while (token === ttsToken && state.voiceEnabled) {
        prefetch();
        if (!pendingPrepared) break;

        const prepared = await pendingPrepared;
        pendingPrepared = null;
        if (token !== ttsToken || !state.voiceEnabled) {
          discardPreparedTtsAudio(prepared?.audio);
          break;
        }

        prefetch();
        if (prepared?.textOnly) {
          statusPort.setRuntimeStatus("回复中", { mode: "replying" });
          await showFailedTtsReply(prepared.displayText, token, prepared);
        } else if (prepared?.error) {
          reportTtsError(prepared.error, token);
          if (prepared.reply) await showFailedTtsReply(prepared.displayText || prepared.text, token, prepared);
        } else if (prepared?.audio) {
          try {
            await playPreparedTtsAudio(prepared.audio, token, prepared);
            if (token === ttsToken && pendingPrepared) {
              bubblePort.setPetMotion("thinking");
              statusPort.setRuntimeStatus("语音生成中…", { mode: "thinking" });
            }
          } catch (error) {
            reportTtsError(error, token);
            if (prepared.reply) await showFailedTtsReply(prepared.displayText || prepared.text, token, prepared);
          }
        }
      }
    } finally {
      if (ttsPrefetch === prefetch) ttsPrefetch = null;
      if (token !== ttsToken || !state.voiceEnabled) {
        discardPendingTtsPrepare(pendingPrepared);
      }
      if (token === ttsToken) {
        ttsQueue = [];
        if (bubblePort.shouldSyncReplyToTts() && speechUnitsTurnToken !== chatPort.activeTurnToken) bubblePort.clearQueuedReplySegments();
        setTtsActive(false);
        if (bubblePort.bubbleKind === "reply" && !bubblePort.streamedReplySegments?.length && !speechUnitsStreaming) {
          bubblePort.scheduleBubbleReset(els.bubbleText.textContent.length, bubblePort.bubbleToken);
        }
      }
    }
  }

  function startNextTtsPrepare(token) {
    while (ttsQueue.length > 0) {
      const item = ttsQueue.shift();
      if (item?.textOnly) return Promise.resolve(item);
      if (!item?.text) continue;
      return fetchTtsAudio(item.text, token, item)
        .then((audio) => ({ ...item, audio }))
        .catch((error) => ({ ...item, error }));
    }
    return null;
  }

  async function fetchTtsAudio(text, token, item = {}) {
    const controller = new AbortController();
    const startedAt = performance.now();
    const timeoutId = lifecycle.setTimeout(() => controller.abort(), TTS_TIMEOUT_MS);
    const slowTimerId = lifecycle.setTimeout(() => {
      if (token === ttsToken && ttsController === controller && !controller.signal.aborted && els.voicePlayer.paused) {
        statusPort.setRuntimeStatus("语音生成中…", { mode: "thinking" });
      }
    }, TTS_SLOW_REQUEST_MS);
    ttsController = controller;

    try {
      const requestInit = {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        signal: controller.signal,
        body: JSON.stringify({
          text,
          ...(item.speechProtocol ? { speech_protocol: item.speechProtocol, voice_style: item.voiceStyle } : {}),
          ...characterPort.buildBackendCharacterContext()
        })
      };
      if (platformPort.isTauriRuntime) requestInit.connectTimeout = 30_000;

      const response = await platformPort.backendFetch(backendPort.buildBackendEndpointUrl("tts", "/tts", { t: Date.now() }), requestInit);
      if (!response.ok) throw new Error(await voicePort.readBackendErrorMessage(response, `TTS HTTP ${response.status}`));

      const arrayBuffer = await response.arrayBuffer();
      if (token !== ttsToken) return null;
      if (controller.signal.aborted) throw createAbortError();

      const contentType = response.headers.get("content-type") || "audio/mpeg";
      const blob = new Blob([arrayBuffer], { type: contentType });
      const finishedAt = performance.now();
      const audio = {
        text,
        contentType,
        objectUrl: URL.createObjectURL(blob),
        requestMs: Math.round(finishedAt - startedAt)
      };
      logTtsTiming("prepared", {
        requestMs: audio.requestMs,
        textLength: text.length,
        contentType: audio.contentType
      });
      return audio;
    } finally {
      lifecycle.clearTimeout(timeoutId);
      lifecycle.clearTimeout(slowTimerId);
      if (ttsController === controller) ttsController = null;
    }
  }

  async function playPreparedTtsAudio(prepared, token, { reply = false, displayText = "", presentationKey, turnToken } = {}) {
    if (sceneOwnsPlayback() || (platformPort.isTauriRuntime && await platformPort.invoke("scene_playback_active"))) return;
    if (!prepared?.objectUrl || token !== ttsToken) return;
    const objectUrl = prepared.objectUrl;
    ttsObjectUrl = objectUrl;
    prepared.objectUrl = "";
    try {
      els.voicePlayer.src = objectUrl;
      els.voicePlayer.currentTime = 0;
      els.voicePlayer.volume = state.voiceVolume;
      const playRequestedAt = performance.now();
      await waitForTtsAudio(token, () => {
        ttsPlaybackToken = token;
        statusPort.setRuntimeStatus("语音播放中", { mode: "speaking" });
        bubblePort.setPetMotion("speaking");
        if (reply) presentTtsReplyText(displayText || prepared.text, { presentationKey, turnToken });
        logTtsTiming("playback-started", {
          requestMs: prepared.requestMs,
          playStartupMs: Math.round(performance.now() - playRequestedAt),
          textLength: prepared.text?.length || 0
        });
      });
    } finally {
      if (ttsPlaybackToken === token) ttsPlaybackToken = 0;
      settingsPort.scheduleSettingsSnapshot();
      // A cancelled play() can settle after another reply has acquired the player.
      if (ttsObjectUrl === objectUrl) {
        els.voicePlayer.pause();
        cleanupTtsObjectUrl();
      } else URL.revokeObjectURL(objectUrl);
    }
  }

  async function showFailedTtsReply(text, token, item = {}) {
    if (token !== ttsToken) return;
    presentTtsReplyText(text, item);
    bubblePort.setPetMotion("idle");
    // Keep failed speech readable, then continue the queue. This wait is cancelled
    // by the same stop owner as audio playback.
    await new Promise((resolve) => {
      const finish = () => {
        lifecycle.clearTimeout(timer);
        if (resolveTtsWait === finish) resolveTtsWait = null;
        resolve();
      };
      const timer = lifecycle.setTimeout(finish, bubblePort.getSegmentDisplayDelay(text));
      resolveTtsWait = finish;
    });
  }

  function presentTtsReplyText(text, { presentationKey, turnToken } = {}) {
    // Several audio chunks can belong to one visible sentence. Its preview is
    // already on screen; starting that audio must not reset the same bubble.
    if (turnToken !== undefined && !chatPort.isTurnActive(turnToken)) return;
    if (presentationKey !== undefined) bubblePort.markSpeechUnitPresented(presentationKey);
    lifecycle.clearTimeout(bubblePort.bubbleTimer);
    if (bubblePort.bubbleKind === "reply" && els.bubbleText.textContent === text) return;
    bubblePort.showBubbleText(text, { transient: false, kind: "reply" });
  }

  function discardPreparedTtsAudio(prepared) {
    if (!prepared?.objectUrl) return;
    URL.revokeObjectURL(prepared.objectUrl);
    prepared.objectUrl = "";
  }

  function discardPendingTtsPrepare(pendingPrepared) {
    if (!pendingPrepared) return;
    pendingPrepared
      .then((prepared) => {
        if (prepared?.audio) discardPreparedTtsAudio(prepared.audio);
      })
      .catch(() => {});
  }

  function reportTtsError(error, token) {
    if (token !== ttsToken) return;
    statusPort.setRuntimeStatus(`语音播放失败：${friendlyErrorMessage(formatError(error))}`, { mode: "error" });
  }

  function createAbortError() {
    const error = new Error("请求超时");
    error.name = "AbortError";
    return error;
  }

  function logTtsTiming(event, details = {}) {
    try {
      if (window.localStorage?.getItem("akane.debug.tts") !== "1") return;
    } catch {
      return;
    }
    console.debug("[Akane TTS]", event, details);
  }

  function waitForTtsAudio(token, onStarted) {
    return new Promise((resolve, reject) => {
      const player = els.voicePlayer;
      let settled = false;
      let started = false;
      let ended = false;
      let timer;
      const finish = (completed = false, error = null) => {
        if (settled) return;
        settled = true;
        lifecycle.clearTimeout(timer);
        player.removeEventListener("ended", handleEnded);
        player.removeEventListener("error", handleError);
        if (resolveTtsWait === finish) resolveTtsWait = null;
        if (error) reject(error);
        else resolve(completed && token === ttsToken);
      };
      const handleEnded = () => {
        ended = true;
        if (started) finish(true);
      };
      const handleError = () => finish(false, new Error("音频播放失败，请检查输出设备。"));
      resolveTtsWait = finish;
      // Listen before play(): short audio may finish before its promise resolves.
      player.addEventListener("ended", handleEnded, { once: true });
      player.addEventListener("error", handleError, { once: true });
      timer = lifecycle.setTimeout(() => finish(false, new Error("音频启动超时，请检查输出设备。")), 15000);
      Promise.resolve().then(() => {
        if (settled || token !== ttsToken) { finish(); return; }
        return player.play();
      }).then(() => {
        if (settled || token !== ttsToken) { finish(); return; }
        started = true;
        onStarted();
        lifecycle.clearTimeout(timer);
        if (ended || player.ended) { finish(true); return; }
        const durationMs = Number.isFinite(player.duration) ? player.duration * 1000 : 120000;
        timer = lifecycle.setTimeout(() => finish(false, new Error("音频播放中断，请检查输出设备。")), Math.max(15000, durationMs + 10000));
      }).catch((error) => finish(false, error));
    });
  }

  function finishTtsWait(completed) {
    if (!resolveTtsWait) return;
    const resolve = resolveTtsWait;
    resolveTtsWait = null;
    resolve(completed);
  }

  function stopTtsAudio() {
    if (!els.voicePlayer) return;
    els.voicePlayer.pause();
    els.voicePlayer.removeAttribute("src");
    els.voicePlayer.load();
    cleanupTtsObjectUrl();
  }

  function cleanupTtsObjectUrl() {
    if (!ttsObjectUrl) return;
    URL.revokeObjectURL(ttsObjectUrl);
    ttsObjectUrl = "";
  }

  function hasLocalTtsPlayback() {
    return ttsActive && ttsPlaybackToken === ttsToken;
  }

  function setTtsActive(active) {
    const next = Boolean(active);
    if (ttsActive === next) return;
    ttsActive = next;
    if (ttsActive) {
      bubblePort.setPetMotion("thinking");
      statusPort.setRuntimeStatus("语音生成中…", { mode: "thinking" });
    } else {
      bubblePort.setPetMotion("idle");
      musicPort.scheduleMusicEmotionRestore();
      statusPort.updateActivityControls();
      settingsPort.scheduleSettingsSnapshot();
    }
  }

  function normalizeTtsText(text) {
    return String(text || "").replace(/\s+/g, " ").trim();
  }

  function buildTtsQueueItems(items, { preserveSegments = false } = {}) {
    const source = (Array.isArray(items) ? items : [items])
      .map((item) => normalizeTtsText(item))
      .filter(Boolean);
    if (!source.length) return [];
    if (usesContinuousTts()) return source.flatMap(continuousVoiceChunks);
    if (preserveSegments) {
      return source.flatMap((item) =>
        item.length > TTS_CHUNK_SOFT_LIMIT ? splitTtsTextForLatency(item) : [item]
      );
    }

    const chunks = [];
    let current = "";
    const pushCurrent = () => {
      if (!current.trim()) return;
      chunks.push(current.trim());
      current = "";
    };

    for (const item of source) {
      const pieces = splitTtsTextForLatency(item);
      for (const piece of pieces) {
        const text = normalizeTtsText(piece);
        if (!text) continue;
        const glue = current && /[。！？!?；;，,、…]$/.test(current) ? "" : "，";
        const next = current ? `${current}${glue}${text}` : text;
        if (current && next.length > TTS_CHUNK_SOFT_LIMIT) {
          pushCurrent();
          current = text;
        } else {
          current = next;
        }
      }
    }

    pushCurrent();
    return chunks.length ? chunks : source;
  }

  function splitTtsTextForLatency(text) {
    const normalized = normalizeTtsText(text);
    if (!normalized) return [];
    return segmentSpeechForDelivery(normalized, { minChars: 1, maxChars: TTS_CHUNK_SOFT_LIMIT });
  }

  function usesContinuousTts() {
    const provider = String(getActiveCharacterProfile()?.voice?.provider || "").trim().toLowerCase();
    return ["irodori", "irodori_tts", "irodori-tts", "provider.tts.irodori.local"].includes(provider);
  }

  function beginSpeechUnits(turnToken) {
    if (speechUnitsTurnToken === turnToken) flushSpeechUnitAudio(turnToken);
    else clearSpeechUnitAudio();
    speechUnitsTurnToken = turnToken;
    if (!state.voiceEnabled) speechUnitsMutedTurnToken = turnToken;
    speechUnitsStreaming = true;
  }

  function finishSpeechUnits(turnToken) {
    if (!chatPort.isTurnActive(turnToken) || speechUnitsTurnToken !== turnToken) return;
    speechUnitsStreaming = false;
    if (!ttsActive && !bubblePort.streamedReplySegments?.length && bubblePort.bubbleKind === "reply") {
      bubblePort.scheduleBubbleReset(els.bubbleText.textContent.length, bubblePort.bubbleToken);
    }
  }

  return {
    beginSpeechUnits,
    finishSpeechUnits,
    get ttsActive() { return ttsActive; },
    get ttsQueue() { return ttsQueue; },
    get streamingTtsPendingShort() { return streamingTtsPendingShort; },
    get speechUnitsTurnToken() { return speechUnitsTurnToken; },
    get speechUnitsMutedTurnToken() { return speechUnitsMutedTurnToken; },
    resetStreamingTtsState,
    queueStreamedTtsSegment,
    flushStreamingTtsPending,
    queueLiveTtsPayloadItems,
    scheduleTtsPrewarm,
    cancelTtsPrewarm,
    queueSpeechUnitAudio,
    finishSpeechUnitAudio,
    flushSpeechUnitAudio,
    testTts,
    previewTts,
    stopTts,
    hasLocalTtsPlayback
  };
}
