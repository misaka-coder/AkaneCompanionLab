const AUDIO_EXTENSIONS = new Set(["mp3", "wav", "flac", "m4a", "aac", "ogg", "opus"]);
const ACTION_DEDUPE_MS = 1200;
const TIMELINE_PREPARE_DEDUPE_MS = 30 * 1000;
const AUDIO_READY_TIMEOUT_MS = 15000;
const PLAY_CONFIRM_TIMEOUT_MS = 2800;
const PLAY_RETRY_DELAYS_MS = [0, 350, 900];

class ActivityRuntime {
  constructor({ audioEl, backendClient, getIdentity, onNotice, onStatusChange } = {}) {
    this._audioEl = audioEl;
    this._backendClient = backendClient;
    this._getIdentity = getIdentity;
    this._onNotice = onNotice;
    this._onStatusChange = onStatusChange;
    this._current = null;
    this._targets = new Map();
    this._latestByRole = new Map();
    this._lastActionSignature = "";
    this._lastActionAt = 0;
    this._lastTimelinePrepareSignature = "";
    this._lastTimelinePrepareAt = 0;
    this._playRequestId = 0;
    this._lastProgressEmitAt = 0;

    if (this._audioEl) {
      this._audioEl.preload = "auto";
      this._audioEl.volume = 1;
      this._audioEl.muted = false;
      this._audioEl.addEventListener("timeupdate", () => this._syncProgress({ emit: true }));
      this._audioEl.addEventListener("loadedmetadata", () => this._syncDuration());
      this._audioEl.addEventListener("ended", () => this._markCompleted());
      this._audioEl.addEventListener("play", () => this._markStatus("running"));
      this._audioEl.addEventListener("pause", () => {
        if (!this._current || this._audioEl.ended) return;
        if (this._current.status === "running") this._markStatus("paused");
      });
    }
  }

  canAcceptFile(file) {
    if (!file) return false;
    const type = String(file.type || "").toLowerCase();
    if (type.startsWith("audio/")) return true;
    return AUDIO_EXTENSIONS.has(this._extensionOf(file.name));
  }

  async handleDroppedAudio(file) {
    if (!this.canAcceptFile(file)) {
      this._notice("这个文件暂时不是可播放音频。");
      return null;
    }

    const identity = this._getIdentity?.();
    if (!identity?.profileUserId || !identity?.sessionId) {
      this._notice("会话还没准备好，稍等一下再拖音频给我。");
      return null;
    }

    this._notice("我在把音频放到手边……");
    const result = await this._backendClient.uploadAudioAttachment({
      profileUserId: identity.profileUserId,
      sessionId: identity.sessionId,
      file,
    });
    const attachment = result?.attachment || result;
    if (!result?.ok || !attachment?.handle) {
      throw new Error(result?.message || result?.error || "音频上传失败");
    }

    this._setCurrentFromAttachment(attachment);
    this._notice(`音频已放在手边：${this._current.title}`);
    return this.getCurrentActivity();
  }

  registerGeneratedFile(generatedFile) {
    const target = this._buildGeneratedTarget(generatedFile);
    if (!target) return false;

    this._rememberTarget(target);
    return true;
  }

  getCurrentActivity() {
    if (!this._current) return null;
    this._syncProgress();
    this._syncDuration();
    const activity = {
      type: this._current.type || "audio_playback",
      status: this._current.status || "ready",
      source_id: this._current.source_id || "",
      title: this._current.title || "未命名音频",
      progress_seconds: this._current.progress_seconds || 0,
    };
    if (Number.isFinite(Number(this._current.duration_seconds))) {
      activity.duration_seconds = Number(this._current.duration_seconds);
    }
    if (this._current.attachment_id) activity.attachment_id = this._current.attachment_id;
    if (this._current.attachment_handle) activity.attachment_handle = this._current.attachment_handle;
    if (this._current.generated_id) activity.generated_id = this._current.generated_id;
    if (this._current.generated_handle) activity.generated_handle = this._current.generated_handle;
    if (this._current.source_kind) activity.source_kind = this._current.source_kind;
    if (this._current.role) activity.role = this._current.role;
    if (this._current.timeline_id) activity.timeline_id = this._current.timeline_id;
    if (this._current.timeline_status) activity.timeline_status = this._current.timeline_status;
    if (Number.isFinite(Number(this._current.timeline_ready_until_seconds))) {
      activity.timeline_ready_until_seconds = Number(this._current.timeline_ready_until_seconds);
    }
    return activity;
  }

  interruptForUserMessage() {
    if (this._current?.type === "vocal_performance" && this._current.status === "running") {
      this._pause({ nextStatus: "interrupted" });
    }
    return this.getCurrentActivity();
  }

  async applyAction(actionPayload) {
    const action = this._normalizeAction(actionPayload);
    if (!action || this._isDuplicateAction(action)) return false;

    try {
      if (action.action === "play") {
        await this._play(action);
        return true;
      }
      if (action.action === "pause") {
        this._pause({ nextStatus: "paused" });
        return true;
      }
      if (action.action === "resume") {
        await this._resume();
        return true;
      }
      if (action.action === "stop") {
        this._stop();
        return true;
      }
    } catch (error) {
      console.warn("[AkanePet] activity action failed:", error);
      this._notice("音频播放动作失败了，可能是文件不可播放或被系统拦截。");
    }
    return false;
  }

  async _play(action) {
    const next = this._resolveTarget(action);
    if (!next) {
      this._notice("现在手边还没有可播放的音频。");
      return;
    }
    const switchingTarget = this._current && this._current !== next;
    const requestId = this._beginAudioTransition(next);
    this._current = next;
    if (["audio_playback", "vocal_performance"].includes(action.type)) {
      this._current.type = action.type;
    }
    await this._ensureAudioSourceReady({ resetSource: switchingTarget });
    if (requestId !== this._playRequestId) return;
    const startSeconds = this._resolvePlayStartSeconds(action, { switchingTarget });
    if (Number.isFinite(startSeconds)) {
      this._seekTo(startSeconds);
    }
    const played = await this._playWithConfirmation({
      requestId,
      startSeconds: Number.isFinite(startSeconds) ? startSeconds : null,
      allowReload: true,
    });
    if (!played) return;
    this._markStatus("running");
    this._prepareTimelineForCurrent();
  }

  async _resume() {
    if (!this._current) {
      this._notice("现在手边还没有可继续播放的音频。");
      return;
    }
    const requestId = ++this._playRequestId;
    await this._ensureAudioSourceReady();
    if (requestId !== this._playRequestId) return;
    const played = await this._playWithConfirmation({
      requestId,
      startSeconds: this._current.progress_seconds || this._audioEl.currentTime || 0,
      allowReload: true,
    });
    if (!played) return;
    this._markStatus("running");
    this._prepareTimelineForCurrent();
  }

  _pause({ nextStatus }) {
    if (!this._current) return;
    this._playRequestId += 1;
    this._syncProgress();
    this._audioEl?.pause();
    this._markStatus(nextStatus || "paused");
  }

  _stop() {
    if (!this._current) return;
    this._playRequestId += 1;
    this._syncProgress();
    this._audioEl?.pause();
    if (this._audioEl) {
      try {
        this._audioEl.currentTime = 0;
      } catch {
        // Some media backends reject seeking before metadata is ready.
      }
    }
    this._current.progress_seconds = 0;
    this._markStatus("stopped");
  }

  _setCurrentFromAttachment(attachment) {
    const sourceId = String(attachment?.source_id || attachment?.handle || attachment?.attachment_id || "").trim();
    const title = String(attachment?.title || attachment?.origin_name || sourceId || "未命名音频").trim();
    const target = {
      type: "audio_playback",
      status: "ready",
      source_kind: "attachment",
      source_id: sourceId,
      attachment_id: String(attachment?.attachment_id || "").trim(),
      attachment_handle: String(attachment?.handle || attachment?.attachment_handle || sourceId).trim(),
      title,
      url: this._backendClient.resolveUrl(attachment?.url || ""),
      duration_seconds: this._finiteNumber(attachment?.duration_seconds),
      progress_seconds: 0,
    };
    this._rememberTarget(target);
    this._setCurrentTarget(target);
  }

  _resolveTarget(action) {
    const sourceId = String(action.source_id || "").trim();
    if (sourceId) {
      return (
        this._targets.get(sourceId) ||
        this._targets.get(sourceId.toLowerCase()) ||
        this._targetFromGeneratedHandle(sourceId) ||
        this._targetFromAttachmentHandle(sourceId)
      );
    }

    const targetText = String(action.target || "").trim().toLowerCase();
    if (targetText && !["current", "latest", "当前", "最近"].includes(targetText)) {
      const direct = this._targets.get(action.target) || this._targets.get(targetText);
      if (direct) return direct;
      const generated = this._targetFromGeneratedHandle(action.target || targetText);
      if (generated) return generated;
      const attachment = this._targetFromAttachmentHandle(action.target || targetText);
      if (attachment) return attachment;
      if (["instrumental", "accompaniment", "伴奏", "伴奏轨"].includes(targetText)) {
        return this._latestByRole.get("instrumental") || null;
      }
      if (["vocals", "vocal", "voice", "人声", "人声轨"].includes(targetText)) {
        return this._latestByRole.get("vocals") || null;
      }
    }
    return this._current;
  }

  async _prepareTimelineForCurrent() {
    if (!this._current?.source_id || !this._backendClient?.prepareMusicTimeline) return;
    const identity = this._getIdentity?.();
    if (!identity?.profileUserId || !identity?.sessionId) return;

    const signature = [
      identity.profileUserId,
      identity.sessionId,
      this._current.source_kind || "",
      this._current.source_id || "",
      this._current.generated_handle || "",
      this._current.attachment_id || "",
    ].join("|");
    const now = Date.now();
    if (signature === this._lastTimelinePrepareSignature && now - this._lastTimelinePrepareAt < TIMELINE_PREPARE_DEDUPE_MS) {
      return;
    }
    this._lastTimelinePrepareSignature = signature;
    this._lastTimelinePrepareAt = now;

    try {
      const result = await this._backendClient.prepareMusicTimeline({
        profileUserId: identity.profileUserId,
        sessionId: identity.sessionId,
        activity: this.getCurrentActivity(),
      });
      const timeline = result?.timeline;
      if (!timeline || !this._current) return;
      const sourceId = String(timeline.source_id || "").trim();
      if (sourceId && sourceId !== String(this._current.source_id || "").trim()) return;
      this._current.timeline_id = String(timeline.timeline_id || "").trim();
      this._current.timeline_status = String(timeline.status || "").trim();
      this._current.timeline_ready_until_seconds = this._finiteNumber(timeline.ready_until_seconds);
    } catch (error) {
      console.warn("[AkanePet] music timeline prepare failed:", error);
    }
  }

  _targetFromGeneratedHandle(value) {
    const handle = String(value || "").trim();
    if (!/^gen_\d+$/i.test(handle)) return null;
    const identity = this._getIdentity?.();
    if (!identity?.profileUserId || !identity?.sessionId) return null;
    const target = {
      type: "audio_playback",
      status: "ready",
      source_kind: "generated_file",
      source_id: handle,
      generated_handle: handle,
      title: handle,
      url: this._backendClient.buildGeneratedAudioUrl({
        profileUserId: identity.profileUserId,
        sessionId: identity.sessionId,
        generatedHandle: handle,
      }),
      duration_seconds: null,
      progress_seconds: 0,
    };
    this._rememberTarget(target);
    return target;
  }

  _targetFromAttachmentHandle(value) {
    const handle = String(value || "").trim();
    if (!/^(audio|file)_\d+$/i.test(handle)) return null;
    const identity = this._getIdentity?.();
    if (!identity?.profileUserId || !identity?.sessionId || !this._backendClient?.buildAttachmentAudioUrl) return null;
    const target = {
      type: "audio_playback",
      status: "ready",
      source_kind: "attachment",
      source_id: handle,
      attachment_handle: handle,
      title: handle,
      url: this._backendClient.buildAttachmentAudioUrl({
        profileUserId: identity.profileUserId,
        sessionId: identity.sessionId,
        attachmentHandle: handle,
      }),
      duration_seconds: null,
      progress_seconds: 0,
    };
    this._rememberTarget(target);
    return target;
  }

  _buildGeneratedTarget(generatedFile) {
    if (!generatedFile || typeof generatedFile !== "object") return null;
    const ext = String(generatedFile.file_ext || generatedFile.output_format || "").trim().toLowerCase().replace(/^\./, "");
    const mimeType = String(generatedFile.mime_type || "").trim().toLowerCase();
    if (!AUDIO_EXTENSIONS.has(ext) && !mimeType.startsWith("audio/")) return null;

    const identity = this._getIdentity?.();
    if (!identity?.profileUserId || !identity?.sessionId) return null;

    const handle = String(generatedFile.generated_handle || generatedFile.handle || generatedFile.generated_id || "").trim();
    if (!handle) return null;

    const card = generatedFile.content_card && typeof generatedFile.content_card === "object" ? generatedFile.content_card : {};
    const separation = card.separation && typeof card.separation === "object" ? card.separation : {};
    const role = this._normalizeStemRole(separation.stem_role || generatedFile.stem_role || generatedFile.role);
    const title = String(generatedFile.output_title || generatedFile.title || handle || "生成音频").trim();
    return {
      type: "audio_playback",
      status: "ready",
      source_kind: "generated_file",
      source_id: handle,
      generated_id: String(generatedFile.generated_id || "").trim(),
      generated_handle: String(generatedFile.generated_handle || handle).trim(),
      role,
      title,
      url: this._backendClient.buildGeneratedAudioUrl({
        profileUserId: identity.profileUserId,
        sessionId: identity.sessionId,
        generatedHandle: handle,
      }),
      duration_seconds: this._finiteNumber(card.media_info?.duration_seconds),
      progress_seconds: 0,
    };
  }

  _setCurrentTarget(target) {
    this._current = target;
    this._current.progress_seconds = this._current.progress_seconds || 0;
    if (this._audioEl) {
      this._audioEl.pause();
      this._audioEl.removeAttribute("src");
      this._audioEl.load();
    }
    this._emitStatusChange();
  }

  _rememberTarget(target) {
    if (!target?.source_id) return;
    const keys = [
      target.source_id,
      target.attachment_id,
      target.attachment_handle,
      target.generated_id,
      target.generated_handle,
    ]
      .map((item) => String(item || "").trim())
      .filter(Boolean);
    for (const key of keys) {
      this._targets.set(key, target);
      this._targets.set(key.toLowerCase(), target);
    }
    if (target.role) {
      this._latestByRole.set(target.role, target);
    }
  }

  _normalizeStemRole(value) {
    const text = String(value || "").trim().toLowerCase();
    if (["instrumental", "accompaniment", "no_vocals", "伴奏", "伴奏轨"].includes(text)) return "instrumental";
    if (["vocals", "vocal", "voice", "人声", "人声轨"].includes(text)) return "vocals";
    return "";
  }

  _beginAudioTransition(next) {
    const requestId = ++this._playRequestId;
    if (this._audioEl && this._current && this._current !== next) {
      this._syncProgress();
      this._audioEl.pause();
    }
    return requestId;
  }

  async _ensureAudioSourceReady({ resetSource = false } = {}) {
    if (!this._audioEl || !this._current?.url) {
      throw new Error("missing_audio_source");
    }
    const desiredUrl = this._normalizeAudioUrl(this._current.url);
    const activeUrl = this._normalizeAudioUrl(this._audioEl.currentSrc || this._audioEl.src || "");
    if (resetSource || !activeUrl || activeUrl !== desiredUrl) {
      this._audioEl.pause();
      this._audioEl.src = desiredUrl;
      this._audioEl.load();
    }
    await this._waitForAudioReady(1, AUDIO_READY_TIMEOUT_MS);
  }

  async _waitForAudioReady(minReadyState = 2, timeoutMs = AUDIO_READY_TIMEOUT_MS) {
    if (!this._audioEl) throw new Error("missing_audio_element");
    if (this._audioEl.readyState >= minReadyState) return;
    await new Promise((resolve, reject) => {
      const timeoutId = window.setTimeout(() => {
        cleanup();
        reject(new Error("audio_load_timeout"));
      }, timeoutMs);
      const cleanup = () => {
        window.clearTimeout(timeoutId);
        this._audioEl.removeEventListener("loadedmetadata", onReady);
        this._audioEl.removeEventListener("canplay", onReady);
        this._audioEl.removeEventListener("canplaythrough", onReady);
        this._audioEl.removeEventListener("error", onError);
      };
      const onReady = () => {
        if (this._audioEl.readyState < minReadyState) return;
        cleanup();
        resolve();
      };
      const onError = () => {
        cleanup();
        reject(new Error("audio_load_error"));
      };
      this._audioEl.addEventListener("loadedmetadata", onReady, { once: true });
      this._audioEl.addEventListener("canplay", onReady, { once: true });
      this._audioEl.addEventListener("canplaythrough", onReady, { once: true });
      this._audioEl.addEventListener("error", onError, { once: true });
    });
  }

  async _playWithConfirmation({ requestId, startSeconds = null, allowReload = true }) {
    if (!this._audioEl || !this._current?.url) throw new Error("missing_audio_source");
    const desiredUrl = this._normalizeAudioUrl(this._current.url);
    let lastError = null;

    for (let attempt = 0; attempt < PLAY_RETRY_DELAYS_MS.length; attempt += 1) {
      if (requestId !== this._playRequestId) return false;
      const delay = PLAY_RETRY_DELAYS_MS[attempt];
      if (delay > 0) await this._sleep(delay);
      if (requestId !== this._playRequestId) return false;

      try {
        if (attempt > 0 && allowReload) {
          this._audioEl.pause();
          this._audioEl.src = desiredUrl;
          this._audioEl.load();
          await this._waitForAudioReady(1, AUDIO_READY_TIMEOUT_MS);
          if (Number.isFinite(Number(startSeconds))) {
            this._seekTo(Number(startSeconds));
          }
        }

        this._audioEl.muted = false;
        this._audioEl.volume = 1;
        await this._waitForAudioReady(2, AUDIO_READY_TIMEOUT_MS);
        const before = Number(this._audioEl.currentTime) || 0;
        await this._audioEl.play();
        const confirmed = await this._waitForPlaybackProgress({
          requestId,
          startTime: before,
          timeoutMs: PLAY_CONFIRM_TIMEOUT_MS,
        });
        if (confirmed) return true;
        lastError = new Error("audio_play_no_progress");
      } catch (error) {
        lastError = error;
        console.warn(`[AkanePet] audio play attempt ${attempt + 1} failed:`, error);
      }
    }

    throw lastError || new Error("audio_play_failed");
  }

  async _waitForPlaybackProgress({ requestId, startTime, timeoutMs }) {
    if (!this._audioEl) return false;
    const initial = Number(startTime) || 0;
    const startedAt = Date.now();
    return new Promise((resolve) => {
      const cleanup = () => {
        window.clearInterval(intervalId);
        window.clearTimeout(timeoutId);
        this._audioEl.removeEventListener("playing", check);
        this._audioEl.removeEventListener("timeupdate", check);
        this._audioEl.removeEventListener("error", fail);
        this._audioEl.removeEventListener("stalled", fail);
      };
      const hasProgress = () => {
        const current = Number(this._audioEl.currentTime) || 0;
        return !this._audioEl.paused && current > initial + 0.04;
      };
      const check = () => {
        if (requestId !== this._playRequestId) {
          cleanup();
          resolve(false);
          return;
        }
        if (hasProgress()) {
          cleanup();
          resolve(true);
        }
      };
      const fail = () => {
        cleanup();
        resolve(false);
      };
      const timeoutId = window.setTimeout(fail, timeoutMs);
      const intervalId = window.setInterval(check, 120);
      this._audioEl.addEventListener("playing", check);
      this._audioEl.addEventListener("timeupdate", check);
      this._audioEl.addEventListener("error", fail, { once: true });
      this._audioEl.addEventListener("stalled", fail, { once: true });
      check();
    });
  }

  _resolvePlayStartSeconds(action, { switchingTarget }) {
    if (Number.isFinite(Number(action.start_seconds))) {
      return Math.max(0, Number(action.start_seconds));
    }
    if (switchingTarget) return 0;
    const status = String(this._current?.status || "").toLowerCase();
    if (status === "completed" || status === "stopped" || this._audioEl?.ended) {
      return 0;
    }
    if (status === "paused" || status === "interrupted") {
      const progress = Number(this._current?.progress_seconds ?? this._audioEl?.currentTime);
      return Number.isFinite(progress) ? Math.max(0, progress) : null;
    }
    return null;
  }

  _seekTo(seconds) {
    if (!this._audioEl) return;
    try {
      this._audioEl.currentTime = Math.max(0, Number(seconds) || 0);
    } catch (error) {
      console.warn("[AkanePet] audio seek failed:", error);
    }
  }

  _normalizeAudioUrl(value) {
    const raw = String(value || "").trim();
    if (!raw) return "";
    try {
      return new URL(raw, window.location.href).href;
    } catch {
      return raw;
    }
  }

  _sleep(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, Math.max(0, Number(ms) || 0)));
  }

  _syncProgress({ emit = false } = {}) {
    if (!this._current || !this._audioEl) return;
    const currentTime = Number(this._audioEl.currentTime);
    if (Number.isFinite(currentTime)) {
      const nextProgress = Math.max(0, Math.round(currentTime));
      const changed = nextProgress !== this._current.progress_seconds;
      this._current.progress_seconds = nextProgress;
      const now = Date.now();
      if (emit && changed && this._current.status === "running" && now - this._lastProgressEmitAt >= 1000) {
        this._lastProgressEmitAt = now;
        this._emitStatusChange();
      }
    }
  }

  _syncDuration() {
    if (!this._current || !this._audioEl) return;
    const duration = Number(this._audioEl.duration);
    if (Number.isFinite(duration) && duration > 0) {
      this._current.duration_seconds = Math.round(duration);
    }
  }

  _markCompleted() {
    if (!this._current) return;
    this._syncProgress();
    this._markStatus("completed");
  }

  _markStatus(status) {
    if (!this._current) return;
    const previous = this._current.status;
    this._current.status = status;
    if (previous !== status) this._emitStatusChange();
  }

  _emitStatusChange() {
    this._onStatusChange?.(this.getCurrentActivity());
  }

  _normalizeAction(value) {
    if (!value || typeof value !== "object") return null;
    const action = String(value.action || "").trim().toLowerCase();
    if (!["play", "pause", "resume", "stop"].includes(action)) return null;
    return {
      action,
      target: String(value.target || "current").trim() || "current",
      source_id: String(value.source_id || value.handle || "").trim(),
      start_seconds: this._finiteNumber(value.start_seconds ?? value.position_seconds),
      type: String(value.type || value.activity_type || "").trim().toLowerCase(),
    };
  }

  _isDuplicateAction(action) {
    const signature = JSON.stringify(action);
    const now = Date.now();
    if (signature === this._lastActionSignature && now - this._lastActionAt < ACTION_DEDUPE_MS) {
      return true;
    }
    this._lastActionSignature = signature;
    this._lastActionAt = now;
    return false;
  }

  _finiteNumber(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  _extensionOf(name) {
    const match = String(name || "").toLowerCase().match(/\.([a-z0-9]+)$/);
    return match ? match[1] : "";
  }

  _notice(text) {
    const message = String(text || "").trim();
    if (message) this._onNotice?.(message);
  }
}

export { ActivityRuntime };
