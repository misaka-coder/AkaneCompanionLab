const VOICE_REALTIME_PROTOCOL_VERSION = 1;
const VOICE_PLAYBACK_OUTPUT_MODE = "binary_audio_ack_v1";
const DEFAULT_READY_TIMEOUT_MS = 5000;
const CAPTURE_FLUSH_TIMEOUT_MS = 750;

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
    createObjectUrl = (blob) => URL.createObjectURL(blob),
    revokeObjectUrl = (url) => URL.revokeObjectURL(url),
    BlobImpl = Blob,
    now = () => performance.now()
  }) {
    if (!audioElement) throw new Error("voice_playback_audio_element_missing");
    this.audioElement = audioElement;
    this.sendJson = sendJson;
    this.getVolume = getVolume;
    this.callbacks = callbacks;
    this.createObjectUrl = createObjectUrl;
    this.revokeObjectUrl = revokeObjectUrl;
    this.BlobImpl = BlobImpl;
    this.now = now;
    this.queue = [];
    this.current = null;
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
      endedBeforeStartAck: false
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

  close(reason = "client_closed") {
    if (this.closed) return;
    this.closed = true;
    this.interrupt(reason);
    this.audioElement.pause();
    this.audioElement.removeAttribute("src");
    this.audioElement.load?.();
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
    void this.playNext();
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

export class RealtimeVoiceSession {
  constructor({
    websocketUrl,
    mediaStream,
    audioElement,
    openPayload,
    workletModuleUrl,
    getVolume = () => 1,
    callbacks = {},
    readyTimeoutMs = DEFAULT_READY_TIMEOUT_MS,
    scope = globalThis
  }) {
    this.websocketUrl = websocketUrl;
    this.mediaStream = mediaStream;
    this.audioElement = audioElement;
    this.openPayload = openPayload;
    this.workletModuleUrl = workletModuleUrl;
    this.getVolume = getVolume;
    this.callbacks = callbacks;
    this.readyTimeoutMs = readyTimeoutMs;
    this.scope = scope;
    this.socket = null;
    this.audioContext = null;
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
    const AudioContextImpl = this.scope.AudioContext || this.scope.webkitAudioContext;
    const AudioWorkletNodeImpl = this.scope.AudioWorkletNode;
    if (!AudioContextImpl || !AudioWorkletNodeImpl) {
      throw new Error("voice_realtime_audio_worklet_unavailable");
    }
    const context = new AudioContextImpl({ latencyHint: "interactive" });
    this.audioContext = context;
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
            sample_rate: Math.round(this.audioContext.sampleRate),
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
      this.sendJson({ type: "client.endpoint" });
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
    const frame = { buffer, frameCount: Math.max(0, Math.round(frameCount)) };
    if (!this.ready) {
      this.pendingFrames.push(frame);
      return;
    }
    this.sendPcmFrame(frame);
  }

  sendPcmFrame(frame) {
    const sampleRate = Number(this.audioContext?.sampleRate || 1);
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
      this.playbackQueue = new RealtimeVoicePlaybackQueue({
        audioElement: this.audioElement,
        sendJson: (message) => this.sendJson(message),
        getVolume: this.getVolume,
        callbacks: this.callbacks,
        BlobImpl: this.scope.Blob,
        createObjectUrl: (blob) => this.scope.URL.createObjectURL(blob),
        revokeObjectUrl: (url) => this.scope.URL.revokeObjectURL(url),
        now: () => this.scope.performance?.now?.() ?? Date.now()
      });
      for (const frame of this.pendingFrames.splice(0)) this.sendPcmFrame(frame);
      this.notify("onReady", payload);
      this.resolveReady?.();
      return;
    }
    if (type === "server.partial" || type === "server.checkpoint") {
      this.notify("onTranscript", {
        kind: type.slice("server.".length),
        text: String(payload.text || ""),
        unstableTail: String(payload.unstable_tail || "")
      });
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
