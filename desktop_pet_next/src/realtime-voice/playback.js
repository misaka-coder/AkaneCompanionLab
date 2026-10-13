// Ordered audio playback and receipts; capture and sockets are owned by sessions.

const DEFAULT_DUCK_VOLUME_FACTOR = 0.24;

export class RealtimeVoicePlaybackQueue {
  constructor({
    audioElement,
    sendJson,
    getVolume = () => 1,
    callbacks = {},
    duckVolumeFactor = DEFAULT_DUCK_VOLUME_FACTOR,
    createObjectUrl = (blob) => URL.createObjectURL(blob),
    revokeObjectUrl = (url) => URL.revokeObjectURL(url),
    BlobImpl = Blob,
    now = () => performance.now(),
    playbackAuthority = null
  }) {
    if (!audioElement) throw new Error("voice_playback_audio_element_missing");
    this.audioElement = audioElement;
    this.sendJson = sendJson;
    this.getVolume = getVolume;
    this.callbacks = callbacks;
    this.duckVolumeFactor = clampVolume(duckVolumeFactor);
    this.createObjectUrl = createObjectUrl;
    this.revokeObjectUrl = revokeObjectUrl;
    this.BlobImpl = BlobImpl;
    this.now = now;
    this.playbackAuthority = playbackAuthority;
    this.queue = [];
    this.current = null;
    this.controlReceipts = new Map();
    this.closed = false;
  }

  enqueue(header, audioBytes) {
    if (this.closed) throw new Error("voice_playback_queue_closed");
    const deliveryId = String(header?.delivery_id || "").trim();
    const mediaType = String(header?.media_type || "audio/mpeg").trim();
    const expectedLength = Number(header?.byte_length);
    const actualLength = Number(audioBytes?.byteLength || 0);
    if (!deliveryId || !header?.binary_follows || !actualLength) {
      throw new Error("voice_playback_payload_invalid");
    }
    if (Number.isFinite(expectedLength) && expectedLength !== actualLength) {
      this.sendTerminal("client.playback.failed", header, {
        reason: "audio_byte_length_mismatch"
      });
      throw new Error("voice_playback_byte_length_mismatch");
    }

    const objectUrl = this.createObjectUrl(new this.BlobImpl([audioBytes], { type: mediaType }));
    const item = {
      header,
      objectUrl,
      startedAt: 0,
      startedAcknowledged: false,
      endedBeforeStartAck: false,
      ducked: false
    };
    this.queue.push(item);
    this.sendJson({
      type: "client.playback.enqueued",
      delivery_id: deliveryId
    });
    this.notify("onPlaybackEnqueued", header);
    void this.playNext();
  }

  async playNext() {
    if (this.closed || this.current || !this.queue.length) return;
    if (this.playbackAuthority && !this.playbackAuthority.acquirePlayback(this)) return;
    const item = this.queue.shift();
    this.current = item;
    const { audioElement } = this;
    const handleEnded = () => {
      if (this.current !== item) return;
      if (!item.startedAcknowledged) {
        item.endedBeforeStartAck = true;
        return;
      }
      this.finishCurrent("completed");
    };
    const handleError = () => {
      if (this.current === item) this.finishCurrent("failed", "audio_element_error");
    };
    item.cleanupListeners = () => {
      audioElement.removeEventListener("ended", handleEnded);
      audioElement.removeEventListener("error", handleError);
    };
    audioElement.addEventListener("ended", handleEnded, { once: true });
    audioElement.addEventListener("error", handleError, { once: true });
    audioElement.src = item.objectUrl;
    audioElement.currentTime = 0;
    audioElement.volume = clampVolume(this.getVolume());

    try {
      await audioElement.play();
      if (this.current !== item || this.closed) return;
      item.startedAt = this.now();
      item.startedAcknowledged = true;
      this.sendJson({
        type: "client.playback.started",
        delivery_id: String(item.header.delivery_id),
        resume_token: `desktop-pet-next:${String(item.header.delivery_id).slice(0, 120)}`
      });
      this.notify("onPlaybackStarted", item.header);
      if (item.endedBeforeStartAck) this.finishCurrent("completed");
    } catch {
      if (this.current === item) this.finishCurrent("failed", "audio_play_rejected");
    }
  }

  interrupt(reason = "user_interrupted") {
    const wasClosed = this.closed;
    this.closed = true;
    if (this.current) {
      this.audioElement.pause();
      this.finishCurrent("interrupted", reason);
    }
    while (this.queue.length) {
      const item = this.queue.shift();
      this.sendTerminal("client.playback.interrupted", item.header, { reason, played_ms: 0 });
      this.releaseItem(item);
      this.notify("onPlaybackInterrupted", item.header, reason);
    }
    this.closed = wasClosed;
  }

  applyControl(control) {
    const controlId = String(control?.control_id || "").trim();
    const commandId = String(control?.command_id || "").trim();
    const action = String(control?.action || "").trim();
    const deliveryId = String(control?.delivery_id || "").trim();
    if (!controlId || !commandId || !deliveryId || !["duck", "resume", "stop"].includes(action)) {
      throw new Error("voice_playback_control_invalid");
    }
    const existing = this.controlReceipts.get(controlId);
    if (existing) {
      if (existing.command_id !== commandId || existing.action !== action) {
        throw new Error("voice_playback_control_conflict");
      }
      this.sendJson(existing);
      return existing.status === "applied";
    }

    const currentMatches = String(this.current?.header?.delivery_id || "") === deliveryId;
    const queuedIndex = this.queue.findIndex(
      (item) => String(item?.header?.delivery_id || "") === deliveryId
    );
    let receipt;
    if (action === "duck") {
      if (!currentMatches || !this.current?.startedAcknowledged) {
        receipt = this.buildControlReceipt(control, "failed", {
          reason: "target_not_playing"
        });
      } else {
        this.current.ducked = true;
        const appliedVolume = clampVolume(this.getVolume()) * this.duckVolumeFactor;
        this.audioElement.volume = clampVolume(appliedVolume);
        receipt = this.buildControlReceipt(control, "applied", {
          played_ms: measurePlayedMs(this.audioElement, this.current.startedAt, this.now),
          applied_volume: this.audioElement.volume
        });
        this.notify("onPlaybackDucked", this.current.header, this.audioElement.volume);
      }
    } else if (action === "resume") {
      if (!currentMatches || !this.current?.ducked) {
        receipt = this.buildControlReceipt(control, "failed", {
          reason: "target_not_ducked"
        });
      } else {
        this.current.ducked = false;
        this.audioElement.volume = clampVolume(this.getVolume());
        receipt = this.buildControlReceipt(control, "applied", {
          played_ms: measurePlayedMs(this.audioElement, this.current.startedAt, this.now),
          applied_volume: this.audioElement.volume
        });
        this.notify("onPlaybackResumed", this.current.header, this.audioElement.volume);
      }
    } else if (currentMatches) {
      const item = this.current;
      const playedMs = measurePlayedMs(this.audioElement, item.startedAt, this.now);
      this.audioElement.pause();
      this.current = null;
      this.releaseItem(item);
      receipt = this.buildControlReceipt(control, "applied", { played_ms: playedMs });
      this.notify("onPlaybackInterrupted", item.header, String(control?.reason || "interrupted"));
    } else if (queuedIndex >= 0) {
      const [item] = this.queue.splice(queuedIndex, 1);
      this.releaseItem(item);
      receipt = this.buildControlReceipt(control, "applied", { played_ms: 0 });
      this.notify("onPlaybackInterrupted", item.header, String(control?.reason || "interrupted"));
    } else {
      receipt = this.buildControlReceipt(control, "failed", {
        reason: "target_not_available"
      });
    }

    this.controlReceipts.set(controlId, receipt);
    this.sendJson(receipt);
    if (receipt.status === "failed") {
      this.notify("onPlaybackControlFailed", control, receipt.reason);
    }
    if (action === "stop" && receipt.status === "applied") this.continuePlayback();
    return receipt.status === "applied";
  }

  buildControlReceipt(control, status, extra = {}) {
    return {
      type: "client.playback.control_ack",
      control_id: String(control.control_id),
      command_id: String(control.command_id),
      action: String(control.action),
      status,
      ...extra
    };
  }

  close(reason = "client_closed") {
    if (this.closed) return;
    this.closed = true;
    this.interrupt(reason);
    if (this.playbackAuthority) {
      this.playbackAuthority.unregisterPlaybackQueue(this);
    } else {
      this.audioElement.pause();
      this.audioElement.removeAttribute("src");
      this.audioElement.load?.();
    }
  }

  finishCurrent(kind, reason = "") {
    const item = this.current;
    if (!item) return;
    this.current = null;
    const playedMs = measurePlayedMs(this.audioElement, item.startedAt, this.now);
    if (kind === "completed") {
      this.sendTerminal("client.playback.completed", item.header, { played_ms: playedMs });
      this.notify("onPlaybackCompleted", item.header, playedMs);
    } else if (kind === "interrupted") {
      this.sendTerminal("client.playback.interrupted", item.header, {
        reason: reason || "user_interrupted",
        played_ms: playedMs
      });
      this.notify("onPlaybackInterrupted", item.header, reason || "user_interrupted");
    } else {
      this.sendTerminal("client.playback.failed", item.header, {
        reason: reason || "audio_playback_failed",
        played_ms: playedMs
      });
      this.notify("onPlaybackFailed", item.header, reason || "audio_playback_failed");
    }
    this.releaseItem(item);
    this.continuePlayback();
  }

  continuePlayback() {
    if (!this.closed && this.queue.length) {
      void this.playNext();
      return;
    }
    this.playbackAuthority?.releasePlayback(this);
  }

  sendTerminal(type, header, extra) {
    this.sendJson({
      type,
      delivery_id: String(header?.delivery_id || ""),
      ...extra
    });
  }

  releaseItem(item) {
    item?.cleanupListeners?.();
    if (item?.objectUrl) this.revokeObjectUrl(item.objectUrl);
  }

  notify(name, ...args) {
    try {
      this.callbacks?.[name]?.(...args);
    } catch {
      // Presentation callbacks must not corrupt delivery acknowledgement order.
    }
  }
}

function measurePlayedMs(audioElement, startedAt, now) {
  const currentTimeMs = Math.round(Math.max(0, Number(audioElement?.currentTime || 0)) * 1000);
  if (currentTimeMs > 0) return currentTimeMs;
  return startedAt ? Math.max(0, Math.round(now() - startedAt)) : 0;
}

function clampVolume(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 1;
  return Math.max(0, Math.min(1, number));
}
