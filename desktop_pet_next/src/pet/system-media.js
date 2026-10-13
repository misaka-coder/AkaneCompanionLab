import { mediaPosition, mediaKind } from "../media-awareness.js";
import { simpleHash, normalizeTimelineLyricSegments } from "../desktop-music-player.js";
import { readJsonResponse } from "../desktop-voice-session.js";
import { createMediaCommandQueue } from "../media-control.js";
import { SYSTEM_MEDIA_POLL_MS, SYSTEM_MEDIA_MAX_AGE_MS, SYSTEM_MEDIA_LYRICS_RETRY_MS, SYSTEM_MEDIA_LYRICS_TURN_WAIT_FOCUSED_MS, SYSTEM_MEDIA_LYRICS_TURN_WAIT_MS } from './constants.js';

/** Owns system-media behavior and its temporary runtime state. */
export function createSystemMediaController({
  state,
  resourceState,
  els,
  platform: platformPort,
  lifecycle,
  scope: runtimeScope,
  perception: perceptionPort,
  chat: chatPort,
  panel: panelPort,
  music: musicPort,
  settings: settingsPort,
  persistence: statePort,
  backend: backendPort,
  activity: activityPort,
  status: statusPort,
  bubble: bubblePort
}) {
  let systemMediaPollTimer = 0;

  let systemMediaReadPending = null;

  let systemMedia = emptySystemMediaSnapshot();

  let systemMediaLyrics = emptySystemMediaLyricsSnapshot();


  let systemMediaLyricsLastAttemptAt = 0;

  const systemMediaLyricsCache = new Map();

  const systemMediaLyricsRequests = new Map();

  function scheduleSystemMediaPoll({ immediate = false } = {}) {
    lifecycle.clearTimeout(systemMediaPollTimer);
    systemMediaPollTimer = 0;
    if (!platformPort.isTauriRuntime) return;

    const delay = immediate ? 0 : SYSTEM_MEDIA_POLL_MS;
    systemMediaPollTimer = lifecycle.setTimeout(async () => {
      systemMediaPollTimer = 0;
      await refreshSystemMediaSnapshot();
      scheduleSystemMediaPoll();
    }, delay);
  }

  function refreshSystemMediaSnapshot() {
    if (!systemMediaReadPending) {
      systemMediaReadPending = readAndApplySystemMediaSnapshot().finally(() => { systemMediaReadPending = null; });
    }
    return systemMediaReadPending;
  }

  async function readAndApplySystemMediaSnapshot() {
    const requestScope = runtimeScope.capture();
    const previous = systemMedia;
    const snapshot = await platformPort.tauriCall("get_current_system_media", {}, { quiet: true });
    if (!runtimeScope.isCurrent(requestScope)) return null;
    systemMedia = normalizeSystemMediaSnapshot(snapshot);
    const trackChanged = systemMedia.trackKey !== previous.trackKey;
    const statusChanged =
      systemMedia.playbackStatus !== previous.playbackStatus ||
      systemMedia.status !== previous.status ||
      systemMedia.isPlaying !== previous.isPlaying;
    const progressChanged = Math.abs(safePositiveSeconds(systemMedia.positionSeconds) - safePositiveSeconds(previous.positionSeconds)) >= 1.2;
    const positionJumped = mediaPosition(previous) !== null && systemMedia.positionSeconds !== null
      && Math.abs(systemMedia.positionSeconds - mediaPosition(previous)) > 3;
    if (state.screenVisionEnabled && ["video", "browser_media"].includes(mediaKind(systemMedia))
        && (trackChanged || positionJumped)) {
      // A seek/switch invalidates the scene behind an in-flight proactive remark.
      if (perceptionPort.proactiveWakeRunning) chatPort.interruptReply({ announce: false });
      perceptionPort.clearScreenVisionWorkspace({ quiet: true });
      perceptionPort.scheduleScreenVisionCapture({ immediate: true });
    }
    if (trackChanged) {
      applySystemMediaLyricsForTrack(systemMedia);
      if (previous.title && previous.trackKey && mediaKind(previous) === "music") {
        panelPort.recordRecentTrack(previous.title, previous.artist);
      }
    }
    if (isFreshSystemMedia(systemMedia) && mediaKind(systemMedia) === "music") {
      void ensureSystemMediaLyrics(systemMedia);
    } else if (trackChanged || statusChanged) {
      systemMediaLyrics = emptySystemMediaLyricsSnapshot({
        trackKey: systemMedia.trackKey || "",
        status: systemMedia.status === "unavailable" ? "unavailable" : "not-found",
        reason: systemMedia.reason || "system_media_unavailable"
      });
    }
    if (trackChanged || statusChanged) {
      if (isMusicEmotionSourceActive()) {
        musicPort.scheduleMusicEmotionRestore({ delayMs: 0 });
      } else if (!musicPort.musicPlaying) {
        musicPort.setMusicEmotion(false);
      }
    }
    if (trackChanged || statusChanged) {
      settingsPort.scheduleMusicSnapshot(120);
    }
    if (
      trackChanged ||
      statusChanged ||
      (systemMedia.ok && progressChanged)
    ) {
      panelPort.schedulePanelStateSync(200);
    }
  }

  function emptySystemMediaSnapshot() {
    return {
      ok: false,
      status: "unavailable",
      reason: "not-polled",
      capturedAt: 0,
      platform: "",
      trackKey: "",
      title: "",
      artist: "",
      album: "",
      sourceApp: "",
      playbackStatus: "unknown",
      isPlaying: false,
      positionSeconds: 0,
      durationSeconds: 0
    };
  }

  function normalizeSystemMediaSnapshot(value) {
    if (!value || typeof value !== "object") return emptySystemMediaSnapshot();
    const title = cleanSystemMediaText(value.title, 120);
    const artist = cleanSystemMediaText(value.artist, 100);
    const album = cleanSystemMediaText(value.album, 120);
    const sourceApp = cleanSystemMediaText(value.sourceApp || value.source_app, 120);
    const playbackStatus = String(value.playbackStatus || value.playback_status || "unknown").trim().toLowerCase() || "unknown";
    const trackKey =
      cleanSystemMediaText(value.trackKey || value.track_key, 220) ||
      simpleHash(`${sourceApp}|${title}|${artist}|${album}`);
    const capturedAt = Number(value.capturedAt || value.captured_at || Date.now());
    return {
      ok: Boolean(value.ok) && Boolean(title || artist) && !isOwnSystemMediaSource(sourceApp),
      status: String(value.status || "").trim().toLowerCase() || (value.ok ? "ready" : "unavailable"),
      reason: cleanSystemMediaText(value.reason, 180),
      capturedAt: Number.isFinite(capturedAt) ? capturedAt : Date.now(),
      platform: cleanSystemMediaText(value.platform, 40),
      trackKey,
      title,
      artist,
      album,
      sourceApp,
      playbackStatus,
      isPlaying: Boolean(value.isPlaying || value.is_playing || playbackStatus === "playing"),
      mediaType: mediaKind(value),
      playbackRate: Number.isFinite(Number(value.playbackRate)) && value.playbackRate != null ? Number(value.playbackRate) : 1,
      timelineUpdatedAt: Number(value.timelineUpdatedAt || 0),
      positionSeconds: value.positionSeconds == null && value.position_seconds == null ? null : safePositiveSeconds(value.positionSeconds ?? value.position_seconds),
      durationSeconds: safePositiveSeconds(value.durationSeconds ?? value.duration_seconds)
    };
  }

  function cleanSystemMediaText(value, limit = 120) {
    const text = String(value || "").replace(/\x00/g, " ").replace(/\s+/g, " ").trim();
    return limit > 0 && text.length > limit ? text.slice(0, limit) : text;
  }

  function safePositiveSeconds(value) {
    const seconds = Number(value);
    return Number.isFinite(seconds) ? Math.max(0, seconds) : 0;
  }

  function isOwnSystemMediaSource(sourceApp) {
    const source = String(sourceApp || "").toLowerCase();
    return source.includes("akane_desktop_pet_next") || source.includes("akane desktop pet");
  }

  function isFreshSystemMedia(snapshot = systemMedia) {
    if (!isControllableSystemMedia(snapshot)) return false;
    return snapshot.playbackStatus !== "stopped";
  }

  function isControllableSystemMedia(snapshot = systemMedia) {
    if (!snapshot?.ok || snapshot.status !== "ready") return false;
    if (!snapshot.title && !snapshot.artist) return false;
    if (snapshot.playbackStatus === "closed") return false;
    const capturedAt = Number(snapshot.capturedAt || 0);
    return capturedAt > 0 && Date.now() - capturedAt <= SYSTEM_MEDIA_MAX_AGE_MS;
  }

  function isMusicEmotionSourceActive() {
    return Boolean(musicPort.musicPlaying || (isFreshSystemMedia(systemMedia) && systemMedia.isPlaying && mediaKind(systemMedia) === "music"));
  }

  function summarizeSystemMedia(snapshot = systemMedia) {
    return {
      ok: Boolean(snapshot?.ok),
      status: snapshot?.status || "unavailable",
      reason: snapshot?.reason || "",
      capturedAt: Number(snapshot?.capturedAt || 0),
      platform: snapshot?.platform || "",
      trackKey: snapshot?.trackKey || "",
      title: snapshot?.title || "",
      artist: snapshot?.artist || "",
      album: snapshot?.album || "",
      sourceApp: snapshot?.sourceApp || "",
      playbackStatus: snapshot?.playbackStatus || "unknown",
      isPlaying: Boolean(snapshot?.isPlaying),
      mediaType: mediaKind(snapshot),
      positionSeconds: mediaPosition(snapshot),
      durationSeconds: safePositiveSeconds(snapshot?.durationSeconds),
      fresh: isFreshSystemMedia(snapshot),
      controllable: isControllableSystemMedia(snapshot)
    };
  }

  function emptySystemMediaLyricsSnapshot(overrides = {}) {
    return {
      ok: false,
      status: "unavailable",
      reason: "not-polled",
      trackKey: "",
      source: "",
      confidence: "",
      lineCount: 0,
      segments: [],
      cached: false,
      updatedAt: 0,
      ...overrides
    };
  }

  function applySystemMediaLyricsForTrack(snapshot = systemMedia) {
    systemMediaLyricsLastAttemptAt = 0;
    const trackKey = String(snapshot?.trackKey || "").trim();
    if (!trackKey) {
      systemMediaLyrics = emptySystemMediaLyricsSnapshot({ reason: "track_key_missing" });
      return;
    }
    const cached = systemMediaLyricsCache.get(trackKey);
    if (cached) {
      systemMediaLyrics = { ...cached, cached: true, segments: Array.isArray(cached.segments) ? [...cached.segments] : [] };
      return;
    }
    systemMediaLyrics = emptySystemMediaLyricsSnapshot({
      status: "pending",
      reason: "lyrics_lookup_pending",
      trackKey
    });
  }

  async function ensureSystemMediaLyrics(snapshot = systemMedia, options = {}) {
    const requestScope = runtimeScope.capture();
    if (!isFreshSystemMedia(snapshot)) return;
    const trackKey = String(snapshot.trackKey || "").trim();
    if (!trackKey || !snapshot.title) return;
    const cached = systemMediaLyricsCache.get(trackKey);
    if (cached) {
      if (systemMediaLyrics.trackKey !== trackKey || systemMediaLyrics.status === "pending") {
        systemMediaLyrics = { ...cached, cached: true, segments: Array.isArray(cached.segments) ? [...cached.segments] : [] };
        settingsPort.scheduleMusicSnapshot(120);
      }
      return;
    }
    const pendingRequest = systemMediaLyricsRequests.get(trackKey);
    if (pendingRequest) return pendingRequest;
    const now = Date.now();
    const force = Boolean(options.force);
    if (!force && systemMediaLyricsLastAttemptAt && now - systemMediaLyricsLastAttemptAt < SYSTEM_MEDIA_LYRICS_RETRY_MS) return;
    if (resourceState.health !== "online") {
      systemMediaLyrics = emptySystemMediaLyricsSnapshot({
        status: "unavailable",
        reason: "backend_offline",
        trackKey
      });
      settingsPort.scheduleMusicSnapshot(120);
      return;
    }

    systemMediaLyricsLastAttemptAt = now;
    const currentTrackKey = String(systemMedia.trackKey || "").trim();
    if (currentTrackKey === trackKey || systemMediaLyrics.trackKey === trackKey) {
      systemMediaLyrics = {
        ...systemMediaLyrics,
        trackKey,
        status: systemMediaLyrics.status === "ready" ? systemMediaLyrics.status : "pending",
        reason: "lyrics_lookup_pending"
      };
    }
    settingsPort.scheduleMusicSnapshot(120);

    const request = (async () => {
      try {
        const sessionId = state.sessionId || "desktop_pet_next";
        const profileUserId = statePort.getProfileUserId();
        const response = await platformPort.backendFetch(
          backendPort.buildBackendEndpointUrl("music_lyrics", "/capabilities/music/lyrics", {
            user_id: sessionId,
            session_id: sessionId,
            real_user_id: profileUserId,
            t: Date.now()
          }),
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            cache: "no-store",
            body: JSON.stringify({
              user_id: sessionId,
              session_id: sessionId,
              real_user_id: profileUserId,
              trackKey,
              title: snapshot.title || "",
              artist: snapshot.artist || "",
              album: snapshot.album || "",
              source: "system_media",
              positionSeconds: safePositiveSeconds(snapshot.positionSeconds)
            }),
            connectTimeout: 20_000
          }
        );
        if (!runtimeScope.isCurrent(requestScope)) return null;
        const payload = await readJsonResponse(response);
        if (!runtimeScope.isCurrent(requestScope)) return null;
        const normalized = normalizeSystemMediaLyricsSnapshot(payload, trackKey);
        if (normalized.status !== "pending" && normalized.status !== "unavailable") {
          systemMediaLyricsCache.set(trackKey, normalized);
        }
        if (normalized.status === "disabled") {
          systemMediaLyricsCache.set(trackKey, normalized);
        }
        if (String(systemMedia.trackKey || "").trim() === trackKey || systemMediaLyrics.trackKey === trackKey) {
          systemMediaLyrics = normalized;
        }
        return normalized;
      } catch {
        if (!runtimeScope.isCurrent(requestScope)) return null;
        const failed = emptySystemMediaLyricsSnapshot({
          status: "unavailable",
          reason: "lyrics_request_failed",
          trackKey
        });
        if (String(systemMedia.trackKey || "").trim() === trackKey || systemMediaLyrics.trackKey === trackKey) {
          systemMediaLyrics = failed;
        }
        return failed;
      } finally {
        systemMediaLyricsRequests.delete(trackKey);

        settingsPort.scheduleMusicSnapshot(120);
      }
    })();
    systemMediaLyricsRequests.set(trackKey, request);
    return request;
  }

  function isLyricsFocusedTurnMessage(message) {
    const text = String(message || "").trim().toLowerCase();
    if (!text) return false;
    return /歌词|唱到|唱的是|哪一句|这一句|当前.*(歌|音乐)|这首|正在放|播放|听/.test(text);
  }

  function shouldWaitForSystemMediaLyricsForTurn(message, options = {}) {
    if (mediaKind(systemMedia) !== "music" || options.turnKind === "desktop_pet_proactive") return false;
    if (options.waitForLyricsHydration === false) return false;
    if (options.waitForLyricsHydration === true) return true;
    return isLyricsFocusedTurnMessage(message);
  }

  function waitForSystemMediaLyrics(request, timeoutMs) {
    if (!request || !Number.isFinite(timeoutMs) || timeoutMs <= 0) return Promise.resolve({ timedOut: false });
    let timeoutId = 0;
    return Promise.race([
      Promise.resolve(request).then((value) => ({ timedOut: false, value })),
      new Promise((resolve) => {
        timeoutId = lifecycle.setTimeout(() => resolve({ timedOut: true }), timeoutMs);
      })
    ]).finally(() => {
      if (timeoutId) lifecycle.clearTimeout(timeoutId);
    });
  }

  async function hydrateSystemMediaLyricsForTurn(message, options = {}) {
    const requestScope = runtimeScope.capture();
    if (!isFreshSystemMedia(systemMedia) || mediaKind(systemMedia) !== "music") return { waited: false, status: "unavailable" };
    const trackKey = String(systemMedia.trackKey || "").trim();
    if (!trackKey || !systemMedia.title) return { waited: false, status: "unavailable" };
    const cached = systemMediaLyricsCache.get(trackKey);
    if (cached) {
      systemMediaLyrics = { ...cached, cached: true, segments: Array.isArray(cached.segments) ? [...cached.segments] : [] };
      return { waited: false, status: "cached" };
    }
    if (
      systemMediaLyrics.trackKey === trackKey &&
      systemMediaLyrics.status === "ready" &&
      Array.isArray(systemMediaLyrics.segments) &&
      systemMediaLyrics.segments.length > 0
    ) {
      return { waited: false, status: "ready" };
    }
    const focused = isLyricsFocusedTurnMessage(message);
    const shouldWait = shouldWaitForSystemMediaLyricsForTurn(message, options);
    const timeoutMs = shouldWait
      ? (focused ? SYSTEM_MEDIA_LYRICS_TURN_WAIT_FOCUSED_MS : SYSTEM_MEDIA_LYRICS_TURN_WAIT_MS)
      : 0;
    const force =
      focused ||
      (systemMediaLyrics.trackKey === trackKey && systemMediaLyrics.reason === "backend_offline");
    const request = ensureSystemMediaLyrics(systemMedia, { force });
    if (!timeoutMs) return { waited: false, status: "background" };
    const result = await waitForSystemMediaLyrics(request, timeoutMs);
    if (!runtimeScope.isCurrent(requestScope)) return null;
    if (
      result.timedOut &&
      String(systemMedia.trackKey || "").trim() === trackKey &&
      systemMediaLyrics.trackKey === trackKey &&
      systemMediaLyrics.status === "pending"
    ) {
      systemMediaLyrics = {
        ...systemMediaLyrics,
        reason: "lyrics_lookup_slow",
        updatedAt: Date.now()
      };
      settingsPort.scheduleMusicSnapshot(120);
    }
    return {
      waited: true,
      timedOut: Boolean(result.timedOut),
      status: systemMediaLyrics.status || "unavailable"
    };
  }

  function normalizeSystemMediaLyricsSnapshot(payload, fallbackTrackKey = "") {
    const value = payload && typeof payload === "object" ? payload : {};
    const status = String(value.status || (value.ok ? "ready" : "unavailable")).trim().toLowerCase() || "unavailable";
    const confidence = String(value.confidence || "").trim().toLowerCase();
    const source = cleanSystemMediaText(value.source || value.provider || "", 80);
    const segments =
      status === "ready" && confidence !== "low"
        ? normalizeTimelineLyricSegments(value.segments)
        : [];
    return emptySystemMediaLyricsSnapshot({
      ok: Boolean(value.ok) && status === "ready" && segments.length > 0,
      status,
      reason: cleanSystemMediaText(value.reason, 120),
      trackKey: cleanSystemMediaText(value.trackKey || value.track_key || fallbackTrackKey, 220),
      source,
      confidence,
      lineCount: segments.length || Number(value.lineCount || value.line_count || 0) || 0,
      segments,
      cached: Boolean(value.cached),
      updatedAt: Date.now()
    });
  }

  function buildSystemMediaLyricSnapshot(timeSeconds = safePositiveSeconds(systemMedia.positionSeconds)) {
    const trackKey = String(systemMedia.trackKey || "").trim();
    if (!trackKey || systemMediaLyrics.trackKey !== trackKey) return null;
    const status = String(systemMediaLyrics.status || "unavailable").trim().toLowerCase();
    const lines = Array.isArray(systemMediaLyrics.segments) ? systemMediaLyrics.segments : [];
    if (status !== "ready" || !lines.length || systemMediaLyrics.confidence === "low") {
      return {
        source: systemMediaLyrics.source ? `online:${systemMediaLyrics.source}` : "online",
        status,
        reason: systemMediaLyrics.reason || "",
        confidence: systemMediaLyrics.confidence || "",
        lineCount: Number(systemMediaLyrics.lineCount || 0),
        index: -1,
        timeSeconds: 0,
        text: "",
        previousText: "",
        nextText: ""
      };
    }
    let currentIndex = -1;
    const currentTime = Number.isFinite(timeSeconds) ? Math.max(0, timeSeconds) : 0;
    for (let index = 0; index < lines.length; index += 1) {
      const start = safePositiveSeconds(lines[index].timeSeconds);
      const end = safePositiveSeconds(lines[index].endSeconds);
      if (start <= currentTime + 0.12) currentIndex = index;
      if (end > currentTime + 0.12) break;
    }
    const current = currentIndex >= 0 ? lines[currentIndex] : null;
    const previous = currentIndex > 0 ? lines[currentIndex - 1] : null;
    const next = lines[Math.max(0, currentIndex + 1)] || null;
    return {
      source: systemMediaLyrics.source ? `online:${systemMediaLyrics.source}` : "online",
      status,
      reason: systemMediaLyrics.reason || "",
      confidence: systemMediaLyrics.confidence || "",
      lineCount: lines.length,
      index: currentIndex,
      timeSeconds: current?.timeSeconds ?? 0,
      text: cleanSystemMediaText(current?.text || "", 120),
      previousText: cleanSystemMediaText(previous?.text || "", 100),
      nextText: cleanSystemMediaText(next?.text || "", 100)
    };
  }

  function summarizeSystemMediaLyrics() {
    const lyric = buildSystemMediaLyricSnapshot();
    if (!lyric) {
      return {
        ok: false,
        status: systemMediaLyrics.status || "unavailable",
        reason: systemMediaLyrics.reason || "",
        source: systemMediaLyrics.source || "",
        confidence: systemMediaLyrics.confidence || "",
        lineCount: Number(systemMediaLyrics.lineCount || 0),
        cached: Boolean(systemMediaLyrics.cached),
        index: -1,
        current: "",
        previous: "",
        next: ""
      };
    }
    return {
      ok: Boolean(lyric.text),
      status: lyric.status || "unavailable",
      reason: lyric.reason || "",
      source: lyric.source || "",
      confidence: lyric.confidence || "",
      lineCount: Number(lyric.lineCount || 0),
      cached: Boolean(systemMediaLyrics.cached),
      index: lyric.index ?? -1,
      current: lyric.text || "",
      previous: lyric.previousText || "",
      next: lyric.nextText || ""
    };
  }

  function isSystemMediaControlAction(action) {
    return ["play", "resume", "pause", "stop", "next", "skip", "previous", "prev"].includes(String(action || "").trim().toLowerCase());
  }

  function normalizeSystemMediaControlAction(action) {
    const normalized = String(action || "").trim().toLowerCase();
    if (normalized === "resume") return "play";
    if (normalized === "skip") return "next";
    if (normalized === "prev") return "previous";
    return normalized;
  }

  function normalizeSystemMediaControlActionLabel(action) {
    const normalized = normalizeSystemMediaControlAction(action);
    if (normalized === "play") return "播放";
    if (normalized === "pause") return "暂停";
    if (normalized === "stop") return "停止";
    if (normalized === "next") return "切到下一首";
    if (normalized === "previous") return "切到上一首";
    return "媒体";
  }

  function activityExplicitlyTargetsSystemMedia(activity, sourceId) {
    const source = String(sourceId || "").trim().toLowerCase();
    const handle = String(activity?.handle || activity?.target || "").trim().toLowerCase();
    return (
      source.startsWith("system_media:") ||
      source === "system_media_current" ||
      handle === "system_media" ||
      handle === "system_media_current" ||
      activity?.system_media === true ||
      String(activity?.source_kind || activity?.sourceKind || "").trim().toLowerCase() === "system_media"
    );
  }

  function shouldControlSystemMediaForActivity(activity, action, sourceId) {
    if (!isSystemMediaControlAction(action) || !isFreshSystemMedia(systemMedia)) return false;
    if (activityExplicitlyTargetsSystemMedia(activity, sourceId)) return true;
    if (!musicPort.musicTrack) return true;
    const normalized = normalizeSystemMediaControlAction(action);
    if (!musicPort.musicPlaying && systemMedia.isPlaying && ["pause", "stop", "next", "previous"].includes(normalized)) return true;
    if (!musicPort.musicPlaying && !musicPort.musicPaused && ["play", "next", "previous"].includes(normalized)) return true;
    return false;
  }

  function systemMediaControlMessage(action, result) {
    const normalized = normalizeSystemMediaControlAction(action);
    const title = [result?.title || systemMedia.title, result?.artist || systemMedia.artist].filter(Boolean).join(" - ");
    const suffix = title ? `：${title}` : "";
    if (normalized === "play") return `已请求系统播放器继续播放${suffix}`;
    if (normalized === "pause") return `已请求系统播放器暂停${suffix}`;
    if (normalized === "stop") return `已请求系统播放器停止${suffix}`;
    if (normalized === "next") return "已请求系统播放器切到下一首。";
    if (normalized === "previous") return "已请求系统播放器切到上一首。";
    return "已请求系统播放器执行操作。";
  }

  function triggerPetReachGesture() {
    const PEAK_MS = 600;
    const TOTAL_MS = 1200;
    return new Promise((resolve) => {
      const stage = els.stage;
      if (!stage) {
        resolve();
        return;
      }
      stage.classList.remove("is-reaching");
      void stage.offsetWidth;
      stage.classList.add("is-reaching");
      lifecycle.setTimeout(resolve, PEAK_MS);
      lifecycle.setTimeout(() => stage.classList.remove("is-reaching"), TOTAL_MS);
    });
  }

  async function controlSystemMediaPlayback(action, { operationId = "" } = {}) {
    if (!platformPort.isTauriRuntime) {
      musicPort.notifyMusicActivityUnavailable("系统媒体控制只在桌面端可用。");
      return { ok: false, status: "unavailable", reason: "desktop_runtime_required" };
    }
    const normalized = normalizeSystemMediaControlAction(action);
    if (!["play", "pause", "stop", "next", "previous"].includes(normalized)) {
      return { ok: false, status: "invalid_action", reason: "invalid_action", action: normalized };
    }

    const effectiveOperationId = String(operationId || `ui:${Date.now()}:${Math.random().toString(16).slice(2)}`).trim();
    if (activityPort.hasExecutedActivityOperation(`system:${effectiveOperationId}`)) {
      return {
        ok: false,
        status: "duplicate",
        reason: "operation_already_submitted",
        action: normalized,
        operationId: effectiveOperationId
      };
    }
    // Reserve before the gesture/IPC awaits so repeated frames or double clicks
    // cannot enqueue a second non-idempotent system-media command.
    activityPort.rememberActivityOperation(`system:${effectiveOperationId}`);

    void triggerPetReachGesture();

    const result = await platformPort.tauriCall("control_system_media", {
      action: normalized,
      operationId: effectiveOperationId
    }, { quiet: true });
    if (result?.ok) {
      const message = systemMediaControlMessage(normalized, result);
      statusPort.setRuntimeStatus(message, { mode: normalized === "pause" || normalized === "stop" ? "music-paused" : "music" });
      bubblePort.showBubbleText(message, { transient: true, durationMs: 2200, kind: "music" });
      await refreshSystemMediaSnapshot().catch(() => {});
      return { ...result, action: normalized };
    }
    if (result?.status === "execution_unknown") {
      const message = `已向系统播放器发送${normalizeSystemMediaControlActionLabel(normalized)}指令，但还没有确认到播放状态变化。`;
      statusPort.setRuntimeStatus(message, { mode: "music" });
      bubblePort.showBubbleText(message, { transient: true, durationMs: 2200, kind: "music" });
      await refreshSystemMediaSnapshot().catch(() => {});
      return { ...result, ok: false, action: normalized };
    }
    const reason = String(result?.reason || "unavailable").trim();
    const message = reason === "no_active_session"
      ? "现在没有可控制的系统播放器。"
      : reason === "session_rejected"
        ? "这个播放器暂时不接受系统媒体控制。"
        : "系统媒体控制暂时不可用。";
    musicPort.notifyMusicActivityUnavailable(message);
    return { ...result, ok: false, status: result?.status || "unavailable", reason, action: normalized };
  }

  const controlActiveMusic = createMediaCommandQueue((action, payload) => musicPort.executeActiveMusicCommand(action, payload));

  return {
    get systemMediaPollTimer() { return systemMediaPollTimer; },
    get systemMedia() { return systemMedia; },
    get systemMediaLyrics() { return systemMediaLyrics; },
    scheduleSystemMediaPoll,
    refreshSystemMediaSnapshot,
    safePositiveSeconds,
    isFreshSystemMedia,
    isControllableSystemMedia,
    isMusicEmotionSourceActive,
    summarizeSystemMedia,
    shouldWaitForSystemMediaLyricsForTurn,
    waitForSystemMediaLyrics,
    hydrateSystemMediaLyricsForTurn,
    buildSystemMediaLyricSnapshot,
    summarizeSystemMediaLyrics,
    shouldControlSystemMediaForActivity,
    controlSystemMediaPlayback,
    get controlActiveMusic() { return controlActiveMusic; }
  };
}
