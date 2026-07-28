const VOICE_REALTIME_PROTOCOL_VERSION = 1;
const VOICE_PLAYBACK_OUTPUT_MODE = "binary_audio_ack_v1";
const DEFAULT_READY_TIMEOUT_MS = 5000;
const CAPTURE_FLUSH_TIMEOUT_MS = 750;
const DEFAULT_DUCK_VOLUME_FACTOR = 0.24;

export function buildVoiceWebSocketUrl(endpoint) {
  const url = new URL(String(endpoint || ""));
  if (url.protocol === "http:") url.protocol = "ws:";
  else if (url.protocol === "https:") url.protocol = "wss:";
  else if (url.protocol !== "ws:" && url.protocol !== "wss:") {
    throw new Error("voice_realtime_endpoint_invalid");
  }
  return url.toString();
}

export function supportsRealtimeVoiceCapture(scope = globalThis) {
  const AudioContextImpl = scope.AudioContext || scope.webkitAudioContext;
  return Boolean(
    scope.WebSocket &&
      AudioContextImpl &&
      scope.AudioWorkletNode &&
      scope.URL?.createObjectURL &&
      scope.Blob
  );
}

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
      if (!item.startedAcknowledged) {
        item.endedBeforeStartAck = true;
        return;
      }
      this.finishCurrent("completed");
    };
    const handleError = () => this.finishCurrent("failed", "audio_element_error");
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

export class RealtimeVoiceCallResources {
  constructor({
    mediaStream,
    audioElement,
    captureReceiptTimeoutMs = CAPTURE_FLUSH_TIMEOUT_MS
  }) {
    if (!mediaStream) throw new Error("voice_call_media_stream_missing");
    if (!audioElement) throw new Error("voice_call_audio_element_missing");
    this.mediaStream = mediaStream;
    this.audioElement = audioElement;
    this.captureReceiptTimeoutMs = Math.max(1, Number(captureReceiptTimeoutMs) || CAPTURE_FLUSH_TIMEOUT_MS);
    this.playbackQueues = new Set();
    this.playbackWaiters = new Set();
    this.playbackOwner = null;
    this.captureScope = null;
    this.captureModuleUrl = "";
    this.captureStartTask = null;
    this.captureContext = null;
    this.captureSourceNode = null;
    this.captureWorkletNode = null;
    this.captureMuteGain = null;
    this.captureOwner = null;
    this.captureClaimOwner = null;
    this.captureReleaseTask = null;
    this.capturePcmSink = null;
    this.captureErrorSink = null;
    this.captureFlushResolver = null;
    this.captureResetResolver = null;
    this.closed = false;
  }

  async acquireCapture(owner, { scope = globalThis, workletModuleUrl, onPcm, onError = null }) {
    if (this.closed) throw new Error("voice_call_resources_closed");
    if (!owner || typeof onPcm !== "function") {
      throw new Error("voice_call_capture_lease_invalid");
    }
    if (this.captureReleaseTask) await this.captureReleaseTask;
    if (this.closed) throw new Error("voice_call_resources_closed");
    if (this.captureOwner === owner) {
      this.capturePcmSink = onPcm;
      this.captureErrorSink = onError;
      return Number(this.captureContext?.sampleRate || 0);
    }
    if (
      (this.captureOwner && this.captureOwner !== owner) ||
      (this.captureClaimOwner && this.captureClaimOwner !== owner)
    ) {
      throw new Error("voice_call_capture_busy");
    }

    this.captureClaimOwner = owner;
    try {
      await this.ensureCaptureStarted({ scope, workletModuleUrl });
      await this.requestCaptureReceipt("reset");
      if (this.closed) throw new Error("voice_call_resources_closed");
      if (this.captureOwner && this.captureOwner !== owner) {
        throw new Error("voice_call_capture_busy");
      }
      this.captureOwner = owner;
      this.capturePcmSink = onPcm;
      this.captureErrorSink = onError;
      return Number(this.captureContext?.sampleRate || 0);
    } finally {
      if (this.captureClaimOwner === owner) this.captureClaimOwner = null;
    }
  }

  releaseCapture(owner, { flush = false } = {}) {
    if (this.captureOwner !== owner) {
      return this.captureReleaseTask || Promise.resolve(false);
    }
    if (this.captureReleaseTask) return this.captureReleaseTask;
    const releaseTask = this.releaseCaptureInternal(owner, { flush });
    const trackedTask = releaseTask.finally(() => {
      if (this.captureReleaseTask === trackedTask) this.captureReleaseTask = null;
    });
    this.captureReleaseTask = trackedTask;
    return trackedTask;
  }

  async releaseCaptureInternal(owner, { flush }) {
    if (flush) {
      try {
        await this.requestCaptureReceipt("flush");
      } finally {
        if (this.captureOwner === owner) {
          this.captureOwner = null;
          this.capturePcmSink = null;
          this.captureErrorSink = null;
        }
      }
      return true;
    } else {
      this.captureOwner = null;
      this.capturePcmSink = null;
      this.captureErrorSink = null;
      await this.requestCaptureReceipt("reset");
      return true;
    }
  }

  async ensureCaptureStarted({ scope, workletModuleUrl }) {
    const normalizedModuleUrl = String(workletModuleUrl || "").trim();
    if (!normalizedModuleUrl) throw new Error("voice_call_capture_worklet_missing");
    if (this.captureModuleUrl && this.captureModuleUrl !== normalizedModuleUrl) {
      throw new Error("voice_call_capture_config_changed");
    }
    if (this.captureStartTask) return this.captureStartTask;
    this.captureScope = scope;
    this.captureModuleUrl = normalizedModuleUrl;
    this.captureStartTask = this.startCaptureEngine().catch((error) => {
      this.captureStartTask = null;
      throw error;
    });
    return this.captureStartTask;
  }

  async startCaptureEngine() {
    const AudioContextImpl = this.captureScope?.AudioContext || this.captureScope?.webkitAudioContext;
    const AudioWorkletNodeImpl = this.captureScope?.AudioWorkletNode;
    if (!AudioContextImpl || !AudioWorkletNodeImpl) {
      throw new Error("voice_realtime_audio_worklet_unavailable");
    }
    const context = new AudioContextImpl({ latencyHint: "interactive" });
    this.captureContext = context;
    try {
      await context.audioWorklet.addModule(this.captureModuleUrl);
      if (this.closed) throw new Error("voice_call_resources_closed");
      if (context.state === "suspended") await context.resume();
      const frameSamples = Math.max(128, Math.round(context.sampleRate * 0.02));
      const node = new AudioWorkletNodeImpl(context, "akane-voice-pcm-capture", {
        channelCount: 1,
        numberOfInputs: 1,
        numberOfOutputs: 1,
        outputChannelCount: [1],
        processorOptions: { frameSamples }
      });
      this.captureWorkletNode = node;
      node.port.onmessage = (event) => this.handleCaptureMessage(event?.data);
      this.captureSourceNode = context.createMediaStreamSource(this.mediaStream);
      this.captureMuteGain = context.createGain();
      this.captureMuteGain.gain.value = 0;
      this.captureSourceNode.connect(node);
      node.connect(this.captureMuteGain);
      this.captureMuteGain.connect(context.destination);
    } catch (error) {
      await this.stopCaptureEngine();
      throw error;
    }
  }

  handleCaptureMessage(payload) {
    const type = String(payload?.type || "");
    if (type === "pcm" && payload.buffer instanceof ArrayBuffer) {
      if (!this.captureOwner || !this.capturePcmSink) return;
      const frames = Number(payload.frames || payload.buffer.byteLength / 4);
      try {
        this.capturePcmSink(payload.buffer, frames);
      } catch (error) {
        this.captureErrorSink?.(error);
      }
      return;
    }
    if (type === "flushed" && this.captureFlushResolver) {
      this.captureFlushResolver();
    } else if (type === "reset" && this.captureResetResolver) {
      this.captureResetResolver();
    }
  }

  requestCaptureReceipt(action) {
    const node = this.captureWorkletNode;
    if (!node) return Promise.resolve();
    const resolverKey = action === "flush" ? "captureFlushResolver" : "captureResetResolver";
    if (this[resolverKey]) {
      return Promise.reject(new Error(`voice_call_capture_${action}_busy`));
    }
    return new Promise((resolve, reject) => {
      const finish = (error = null) => {
        if (this[resolverKey] === finish) this[resolverKey] = null;
        if (error) reject(error);
        else resolve();
      };
      this[resolverKey] = finish;
      node.port.postMessage({ type: action });
      const setTimeoutImpl = this.captureScope?.setTimeout?.bind(this.captureScope) || globalThis.setTimeout;
      setTimeoutImpl(
        () => finish(new Error(`voice_call_capture_${action}_timeout`)),
        this.captureReceiptTimeoutMs
      );
    });
  }

  createPlaybackQueue(options = {}) {
    if (this.closed) throw new Error("voice_call_resources_closed");
    const queue = new RealtimeVoicePlaybackQueue({
      ...options,
      audioElement: this.audioElement,
      playbackAuthority: this
    });
    this.playbackQueues.add(queue);
    return queue;
  }

  acquirePlayback(queue) {
    if (this.closed || !this.playbackQueues.has(queue) || queue?.closed) return false;
    if (!this.playbackOwner || this.playbackOwner === queue) {
      this.playbackOwner = queue;
      this.playbackWaiters.delete(queue);
      return true;
    }
    this.playbackWaiters.add(queue);
    return false;
  }

  releasePlayback(queue) {
    this.playbackWaiters.delete(queue);
    if (this.playbackOwner !== queue) return;
    this.playbackOwner = null;
    this.promotePlaybackWaiter();
  }

  unregisterPlaybackQueue(queue) {
    this.playbackQueues.delete(queue);
    this.playbackWaiters.delete(queue);
    if (this.playbackOwner === queue) {
      this.playbackOwner = null;
      this.promotePlaybackWaiter();
    }
  }

  promotePlaybackWaiter() {
    if (this.closed || this.playbackOwner) return;
    for (const queue of this.playbackWaiters) {
      this.playbackWaiters.delete(queue);
      if (queue?.closed || !this.playbackQueues.has(queue)) continue;
      void queue.playNext();
      return;
    }
  }

  close(reason = "voice_call_closed") {
    if (this.closed) return Promise.resolve();
    this.closed = true;
    this.captureOwner = null;
    this.captureClaimOwner = null;
    this.capturePcmSink = null;
    this.captureErrorSink = null;
    for (const queue of [...this.playbackQueues]) queue.close(reason);
    this.playbackQueues.clear();
    this.playbackWaiters.clear();
    this.playbackOwner = null;
    this.audioElement.pause();
    this.audioElement.removeAttribute("src");
    this.audioElement.load?.();
    for (const track of this.mediaStream?.getTracks?.() || []) track.stop();
    return this.stopCaptureEngine();
  }

  async stopCaptureEngine() {
    this.captureFlushResolver?.();
    this.captureResetResolver?.();
    this.captureWorkletNode?.port?.postMessage?.({ type: "stop" });
    this.captureSourceNode?.disconnect?.();
    this.captureWorkletNode?.disconnect?.();
    this.captureMuteGain?.disconnect?.();
    this.captureSourceNode = null;
    this.captureWorkletNode = null;
    this.captureMuteGain = null;
    const context = this.captureContext;
    this.captureContext = null;
    if (context && context.state !== "closed") {
      try {
        await context.close();
      } catch {
        // Device release must still stop the media tracks above.
      }
    }
  }
}

export class RealtimeVoiceSession {
  constructor({
    websocketUrl,
    mediaStream,
    audioElement,
    callResources = null,
    openPayload,
    workletModuleUrl,
    endpointDetector = null,
    getVolume = () => 1,
    callbacks = {},
    readyTimeoutMs = DEFAULT_READY_TIMEOUT_MS,
    scope = globalThis
  }) {
    this.websocketUrl = websocketUrl;
    this.callResources = callResources;
    this.mediaStream = callResources?.mediaStream || mediaStream;
    this.audioElement = callResources?.audioElement || audioElement;
    this.openPayload = openPayload;
    this.workletModuleUrl = workletModuleUrl;
    this.endpointDetector = endpointDetector;
    this.getVolume = getVolume;
    this.callbacks = callbacks;
    this.readyTimeoutMs = readyTimeoutMs;
    this.scope = scope;
    this.socket = null;
    this.audioContext = null;
    this.captureSampleRate = 0;
    this.callCaptureAttached = false;
    this.sourceNode = null;
    this.workletNode = null;
    this.muteGain = null;
    this.playbackQueue = null;
    this.ready = false;
    this.failed = false;
    this.closed = false;
    this.endpointSent = false;
    this.serverFinalCommitted = false;
    this.responseTerminal = false;
    this.pendingFrames = [];
    this.pendingSpeechHeader = null;
    this.sequence = 0;
    this.audioFramesSent = 0;
    this.flushResolver = null;
    this.startPromise = null;
  }

  start() {
    if (this.startPromise) return this.startPromise;
    this.startPromise = this.startInternal();
    return this.startPromise;
  }

  async startInternal() {
    await this.startCapture();
    await this.openSocket();
    return this;
  }

  async startCapture() {
    if (this.callResources) {
      this.captureSampleRate = await this.callResources.acquireCapture(this, {
        scope: this.scope,
        workletModuleUrl: this.workletModuleUrl,
        onPcm: (buffer, frames) => this.acceptPcmFrame(buffer, frames),
        onError: (error) => {
          this.fail(String(error?.message || "voice_realtime_capture_frame_failed"), {
            terminal: true
          });
        }
      });
      this.callCaptureAttached = true;
      if (this.closed) {
        await this.stopCapture();
        throw new Error("voice_realtime_session_closed");
      }
      return;
    }
    const AudioContextImpl = this.scope.AudioContext || this.scope.webkitAudioContext;
    const AudioWorkletNodeImpl = this.scope.AudioWorkletNode;
    if (!AudioContextImpl || !AudioWorkletNodeImpl) {
      throw new Error("voice_realtime_audio_worklet_unavailable");
    }
    const context = new AudioContextImpl({ latencyHint: "interactive" });
    this.audioContext = context;
    this.captureSampleRate = Number(context.sampleRate || 0);
    await context.audioWorklet.addModule(this.workletModuleUrl);
    if (context.state === "suspended") await context.resume();
    const frameSamples = Math.max(128, Math.round(context.sampleRate * 0.02));
    const node = new AudioWorkletNodeImpl(context, "akane-voice-pcm-capture", {
      channelCount: 1,
      numberOfInputs: 1,
      numberOfOutputs: 1,
      outputChannelCount: [1],
      processorOptions: { frameSamples }
    });
    this.workletNode = node;
    node.port.onmessage = (event) => this.handleWorkletMessage(event?.data);
    this.sourceNode = context.createMediaStreamSource(this.mediaStream);
    this.muteGain = context.createGain();
    this.muteGain.gain.value = 0;
    this.sourceNode.connect(node);
    node.connect(this.muteGain);
    this.muteGain.connect(context.destination);
  }

  openSocket() {
    return new Promise((resolve, reject) => {
      const WebSocketImpl = this.scope.WebSocket;
      if (!WebSocketImpl) {
        reject(new Error("voice_realtime_websocket_unavailable"));
        return;
      }
      let settled = false;
      const socket = new WebSocketImpl(this.websocketUrl);
      this.socket = socket;
      socket.binaryType = "arraybuffer";
      const timeoutId = this.scope.setTimeout(() => {
        if (settled) return;
        settled = true;
        this.fail("voice_realtime_ready_timeout", { terminal: true });
        reject(new Error("voice_realtime_ready_timeout"));
      }, this.readyTimeoutMs);

      socket.addEventListener("open", () => {
        this.sendJson({
          ...this.openPayload,
          type: "client.open",
          protocol_version: VOICE_REALTIME_PROTOCOL_VERSION,
          input: {
            format: "f32le",
            sample_rate: Math.round(this.captureSampleRate),
            channels: 1
          },
          output: { mode: VOICE_PLAYBACK_OUTPUT_MODE }
        });
      });
      socket.addEventListener("message", (event) => {
        void this.handleSocketMessage(event?.data);
      });
      socket.addEventListener("error", () => {
        if (!settled) {
          settled = true;
          this.scope.clearTimeout(timeoutId);
          reject(new Error("voice_realtime_websocket_failed"));
        }
        this.fail("voice_realtime_websocket_failed", { terminal: true });
      });
      socket.addEventListener("close", () => {
        this.scope.clearTimeout(timeoutId);
        if (!settled && !this.ready) {
          settled = true;
          reject(new Error("voice_realtime_closed_before_ready"));
        }
        if (!this.closed && !this.responseTerminal) {
          this.fail("voice_realtime_connection_closed", { terminal: true });
        }
      });
      this.resolveReady = () => {
        if (settled) return;
        settled = true;
        this.scope.clearTimeout(timeoutId);
        resolve(this);
      };
    });
  }

  async finishInput() {
    await this.flushAndStopCapture();
    await this.start();
    if (this.failed || !this.ready) throw new Error("voice_realtime_not_ready");
    if (!this.endpointSent) {
      this.endpointSent = true;
      if (!this.sendJson({ type: "client.endpoint" })) {
        throw new Error("voice_realtime_endpoint_send_failed");
      }
      this.notify("onFinalizing");
    }
  }

  async cancel(reason = "client_cancelled") {
    if (this.closed) return;
    this.playbackQueue?.interrupt(reason);
    await this.stopCapture();
    this.sendJson({ type: "client.cancel", reason: safeReason(reason) });
    this.scope.setTimeout(() => this.dispose(reason), 750);
  }

  dispose(reason = "client_closed") {
    if (this.closed) return;
    this.closed = true;
    this.playbackQueue?.close(reason);
    void this.stopCapture();
    try {
      this.socket?.close(1000, "voice_client_closed");
    } catch {
      // The browser may already have disposed the socket.
    }
  }

  handleWorkletMessage(payload) {
    const type = String(payload?.type || "");
    if (type === "pcm" && payload.buffer instanceof ArrayBuffer) {
      const frames = Number(payload.frames || payload.buffer.byteLength / 4);
      this.acceptPcmFrame(payload.buffer, frames);
    } else if ((type === "flushed" || type === "stopped") && this.flushResolver) {
      const resolve = this.flushResolver;
      this.flushResolver = null;
      resolve();
    }
  }

  acceptPcmFrame(buffer, frameCount) {
    if (this.closed || this.endpointSent || this.failed) return;
    try {
      this.endpointDetector?.acceptPcmFrame?.(
        buffer,
        frameCount,
        this.captureSampleRate
      );
    } catch (error) {
      this.fail(String(error?.message || "voice_realtime_endpoint_detector_failed"), {
        terminal: true
      });
      return;
    }
    if (this.closed || this.endpointSent || this.failed) return;
    const frame = { buffer, frameCount: Math.max(0, Math.round(frameCount)) };
    if (!this.ready) {
      this.pendingFrames.push(frame);
      return;
    }
    this.sendPcmFrame(frame);
  }

  sendPcmFrame(frame) {
    const sampleRate = Number(this.captureSampleRate || 1);
    const audioClockMs = Math.round((this.audioFramesSent * 1000) / sampleRate);
    this.sendJson({
      type: "client.audio",
      sequence: this.sequence,
      audio_clock_ms: audioClockMs
    });
    this.socket.send(frame.buffer);
    this.sequence += 1;
    this.audioFramesSent += frame.frameCount;
  }

  async handleSocketMessage(data) {
    if (typeof data === "string") {
      let payload;
      try {
        payload = JSON.parse(data);
      } catch {
        this.fail("voice_realtime_server_json_invalid", { terminal: true });
        return;
      }
      this.handleServerEvent(payload);
      return;
    }
    if (data instanceof ArrayBuffer) {
      this.handleSpeechBinary(data);
      return;
    }
    if (data?.arrayBuffer) {
      this.handleSpeechBinary(await data.arrayBuffer());
      return;
    }
    this.fail("voice_realtime_server_frame_invalid", { terminal: true });
  }

  handleServerEvent(payload) {
    const type = String(payload?.type || "");
    if (type === "server.ready") {
      this.ready = true;
      const playbackQueueOptions = {
        sendJson: (message) => this.sendJson(message),
        getVolume: this.getVolume,
        callbacks: this.callbacks,
        BlobImpl: this.scope.Blob,
        createObjectUrl: (blob) => this.scope.URL.createObjectURL(blob),
        revokeObjectUrl: (url) => this.scope.URL.revokeObjectURL(url),
        now: () => this.scope.performance?.now?.() ?? Date.now()
      };
      this.playbackQueue = this.callResources
        ? this.callResources.createPlaybackQueue(playbackQueueOptions)
        : new RealtimeVoicePlaybackQueue({
            ...playbackQueueOptions,
            audioElement: this.audioElement
          });
      for (const frame of this.pendingFrames.splice(0)) this.sendPcmFrame(frame);
      this.notify("onReady", payload);
      this.resolveReady?.();
      return;
    }
    if (type === "server.partial" || type === "server.checkpoint") {
      const transcript = {
        kind: type.slice("server.".length),
        text: String(payload.text || ""),
        unstableTail: String(payload.unstable_tail || "")
      };
      try {
        this.endpointDetector?.observeTranscript?.(transcript);
      } catch (error) {
        this.fail(String(error?.message || "voice_realtime_endpoint_detector_failed"), {
          terminal: true
        });
        return;
      }
      this.notify("onTranscript", transcript);
      return;
    }
    if (type === "server.final") {
      this.serverFinalCommitted = true;
      this.notify("onFinal", payload);
      return;
    }
    if (type === "server.speech") {
      if (this.pendingSpeechHeader) {
        this.fail("voice_realtime_speech_binary_missing", { terminal: true });
        return;
      }
      this.pendingSpeechHeader = payload;
      return;
    }
    if (type === "server.playback.control") {
      try {
        this.playbackQueue?.applyControl(payload);
      } catch (error) {
        this.fail(String(error?.message || "voice_playback_control_failed"), {
          terminal: false
        });
      }
      return;
    }
    if (type === "server.response.completed") {
      this.responseTerminal = true;
      this.notify("onResponseCompleted", payload);
      return;
    }
    if (type === "server.response.failed") {
      this.responseTerminal = true;
      this.notify("onResponseFailed", payload);
      return;
    }
    if (type === "server.failed") {
      this.fail(String(payload.reason || "voice_realtime_failed"), {
        terminal: Boolean(payload.terminal),
        message: String(payload.message || ""),
        retryable: Boolean(payload.retryable)
      });
      return;
    }
    if (type === "server.cancelled") {
      this.responseTerminal = true;
      this.notify("onCancelled", payload);
      this.dispose("server_cancelled");
      return;
    }
    if (type === "server.playback.ack" || type === "server.finalizing" || type === "server.audio_accepted") {
      this.notify("onProtocolEvent", payload);
    }
  }

  handleSpeechBinary(buffer) {
    const header = this.pendingSpeechHeader;
    this.pendingSpeechHeader = null;
    if (!header) {
      this.fail("voice_realtime_speech_header_missing", { terminal: true });
      return;
    }
    try {
      this.playbackQueue.enqueue(header, buffer);
    } catch (error) {
      this.fail(String(error?.message || "voice_playback_enqueue_failed"), { terminal: true });
    }
  }

  fail(reason, { terminal = false, message = "", retryable = false } = {}) {
    if (terminal) this.failed = true;
    this.notify("onFailure", {
      reason: safeReason(reason),
      message,
      retryable,
      terminal,
      committed: this.serverFinalCommitted
    });
    if (terminal) this.dispose(reason);
  }

  sendJson(payload) {
    if (this.socket?.readyState !== 1) return false;
    this.socket.send(JSON.stringify(payload));
    return true;
  }

  async flushAndStopCapture() {
    if (this.callResources) {
      if (!this.callCaptureAttached) return;
      this.callCaptureAttached = false;
      await this.callResources.releaseCapture(this, { flush: true });
      return;
    }
    if (!this.workletNode) return;
    const flushed = new Promise((resolve) => {
      this.flushResolver = resolve;
      this.workletNode.port.postMessage({ type: "stop" });
      this.scope.setTimeout(resolve, CAPTURE_FLUSH_TIMEOUT_MS);
    });
    await flushed;
    await this.stopCapture();
  }

  async stopCapture() {
    if (this.callResources) {
      if (!this.callCaptureAttached) return;
      this.callCaptureAttached = false;
      await this.callResources.releaseCapture(this, { flush: false });
      return;
    }
    this.sourceNode?.disconnect?.();
    this.workletNode?.disconnect?.();
    this.muteGain?.disconnect?.();
    this.sourceNode = null;
    this.workletNode = null;
    this.muteGain = null;
    const context = this.audioContext;
    this.audioContext = null;
    if (context && context.state !== "closed") {
      try {
        await context.close();
      } catch {
        // Closing capture must not hide the server-side result.
      }
    }
  }

  notify(name, ...args) {
    try {
      this.callbacks?.[name]?.(...args);
    } catch {
      // UI callbacks are observational; protocol state remains authoritative.
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

function safeReason(value) {
  const normalized = String(value || "voice_realtime_failed")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_.:-]+/g, "_")
    .slice(0, 96);
  return normalized || "voice_realtime_failed";
}
