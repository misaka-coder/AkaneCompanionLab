// RealtimeVoiceSession: state stays on this session, with explicit protocol and audio dependencies.

import {
  VOICE_REALTIME_PROTOCOL_VERSION,
  VOICE_PLAYBACK_OUTPUT_MODE,
  DEFAULT_SOCKET_OPEN_TIMEOUT_MS,
  DEFAULT_PROVIDER_READY_TIMEOUT_MS,
  CAPTURE_FLUSH_TIMEOUT_MS,
  DEFAULT_FALLBACK_PCM_MAX_MS,
  CANCEL_RECEIPT_TIMEOUT_MS,
  safeReason
} from "./protocol.js";
import { retainFallbackPcmFrame, buildFallbackPcmBlob, releaseFallbackPcm } from "./pcm.js";
import { RealtimeVoicePlaybackQueue } from "./playback.js";

export class RealtimeVoiceSession {
  constructor({
    websocketUrl,
    getSocketProtocols = null,
    mediaStream,
    audioElement,
    callResources = null,
    openPayload,
    workletModuleUrl,
    endpointDetector = null,
    getVolume = () => 1,
    callbacks = {},
    socketOpenTimeoutMs = DEFAULT_SOCKET_OPEN_TIMEOUT_MS,
    readyTimeoutMs = DEFAULT_PROVIDER_READY_TIMEOUT_MS,
    fallbackPcmMaxMs = DEFAULT_FALLBACK_PCM_MAX_MS,
    scope = globalThis
  }) {
    this.websocketUrl = websocketUrl;
    this.getSocketProtocols = getSocketProtocols;
    this.callResources = callResources;
    this.mediaStream = callResources?.mediaStream || mediaStream;
    this.audioElement = callResources?.audioElement || audioElement;
    this.openPayload = openPayload;
    this.workletModuleUrl = workletModuleUrl;
    this.endpointDetector = endpointDetector;
    this.getVolume = getVolume;
    this.callbacks = callbacks;
    this.socketOpenTimeoutMs = socketOpenTimeoutMs;
    this.readyTimeoutMs = readyTimeoutMs;
    this.fallbackPcmMaxMs = Math.max(
      1000,
      Number(fallbackPcmMaxMs) || DEFAULT_FALLBACK_PCM_MAX_MS
    );
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
    this.transportOpen = false;
    this.failed = false;
    this.closed = false;
    this.endpointSent = false;
    this.serverFinalCommitted = false;
    this.responseTerminal = false;
    this.interruptionSent = false;
    this.pendingInterruptionClockMs = null;
    this.pendingFrames = [];
    this.pendingSpeechHeader = null;
    this.sequence = 0;
    this.audioFramesSent = 0;
    this.fallbackPcmFrames = [];
    this.fallbackPcmFrameCount = 0;
    this.fallbackPcmDroppedFrames = 0;
    this.flushResolver = null;
    this.cancelPromise = null;
    this.cancelResolver = null;
    this.cancelTimeoutId = 0;
    this.startPromise = null;
    this.readyTimeoutId = 0;
    this.readyWaiters = new Set();
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
    if (this.closed) throw new Error("voice_realtime_session_closed");
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
      this.callResources.replayIdleCapture(this);
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
    try {
      await context.audioWorklet.addModule(this.workletModuleUrl);
      if (this.closed || this.audioContext !== context) throw new Error("voice_realtime_session_closed");
      if (context.state === "suspended") await context.resume();
      if (this.closed || this.audioContext !== context) throw new Error("voice_realtime_session_closed");
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
    } catch (error) {
      if (this.audioContext === context) {
        await this.stopCapture();
      } else if (context.state !== "closed") {
        try { await context.close(); } catch { /* Release only this superseded initialization. */ }
      }
      throw error;
    }
  }

  async openSocket() {
    const protocols = this.getSocketProtocols ? await this.getSocketProtocols(this.websocketUrl) : [];
    if (this.closed) throw new Error("voice_realtime_session_closed");
    return new Promise((resolve, reject) => {
      const WebSocketImpl = this.scope.WebSocket;
      if (!WebSocketImpl) {
        reject(new Error("voice_realtime_websocket_unavailable"));
        return;
      }
      let settled = false;
      const socket = protocols.length ? new WebSocketImpl(this.websocketUrl, protocols) : new WebSocketImpl(this.websocketUrl);
      this.socket = socket;
      socket.binaryType = "arraybuffer";
      const timeoutId = this.scope.setTimeout(() => {
        if (settled) return;
        settled = true;
        this.fail("voice_realtime_websocket_open_timeout", {
          terminal: true,
          retryable: true
        });
        reject(new Error("voice_realtime_websocket_open_timeout"));
      }, this.socketOpenTimeoutMs);

      socket.addEventListener("open", () => {
        if (settled || this.closed) return;
        settled = true;
        this.transportOpen = true;
        this.scope.clearTimeout(timeoutId);
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
        this.armProviderReadyTimeout();
        this.notify("onTransportOpen");
        resolve(this);
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
        this.fail("voice_realtime_websocket_failed", {
          terminal: true,
          retryable: true
        });
      });
      socket.addEventListener("close", () => {
        this.scope.clearTimeout(timeoutId);
        if (!settled) {
          settled = true;
          reject(new Error("voice_realtime_closed_before_ready"));
        }
        this.settleReadyWaiters(false);
        if (!this.closed && !this.responseTerminal) {
          this.fail("voice_realtime_connection_closed", {
            terminal: true,
            retryable: true
          });
        }
      });
    });
  }

  async finishInput() {
    await this.flushAndStopCapture();
    await this.start();
    if (!this.ready && !(await this.waitUntilReady())) {
      throw new Error("voice_realtime_not_ready");
    }
    if (this.failed || !this.ready) throw new Error("voice_realtime_not_ready");
    if (!this.endpointSent) {
      this.endpointSent = true;
      if (!this.sendJson({ type: "client.endpoint" })) {
        throw new Error("voice_realtime_endpoint_send_failed");
      }
      this.notify("onFinalizing");
    }
  }

  reportInterruptionSuspected() {
    if (this.closed || this.endpointSent || this.failed || this.interruptionSent) {
      return false;
    }
    const sampleRate = Number(this.captureSampleRate || 1);
    const audioClockMs = Math.max(
      0,
      Math.round((this.audioFramesSent * 1000) / sampleRate)
    );
    if (!this.ready) {
      this.pendingInterruptionClockMs = audioClockMs;
      return true;
    }
    return this.sendInterruptionSignal(audioClockMs);
  }

  sendInterruptionSignal(audioClockMs) {
    if (this.interruptionSent) return false;
    const sent = this.sendJson({
      type: "client.interruption.suspected",
      audio_clock_ms: Math.max(0, Math.round(Number(audioClockMs) || 0))
    });
    if (sent) {
      this.interruptionSent = true;
      this.pendingInterruptionClockMs = null;
    }
    return sent;
  }

  async cancel(reason = "client_cancelled") {
    if (this.cancelPromise) return this.cancelPromise;
    this.cancelPromise = this.cancelInternal(reason);
    return this.cancelPromise;
  }

  async cancelInternal(reason) {
    if (this.closed) return false;
    this.playbackQueue?.interrupt(reason);
    await this.stopCapture();
    if (
      this.closed ||
      !this.sendJson({ type: "client.cancel", reason: safeReason(reason) })
    ) {
      this.dispose(reason);
      return false;
    }
    return new Promise((resolve) => {
      this.cancelResolver = resolve;
      this.cancelTimeoutId = this.scope.setTimeout(() => {
        this.resolveCancel(false);
        this.dispose("voice_cancel_receipt_timeout");
      }, CANCEL_RECEIPT_TIMEOUT_MS);
    });
  }

  dispose(reason = "client_closed") {
    if (this.closed) return;
    this.closed = true;
    this.clearProviderReadyTimeout();
    this.settleReadyWaiters(false);
    this.resolveCancel(false);
    this.pendingFrames = [];
    this.pendingSpeechHeader = null;
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
    this.retainFallbackPcmFrame(buffer, frameCount);
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

  retainFallbackPcmFrame(buffer, frameCount) {
    return retainFallbackPcmFrame(this, buffer, frameCount);
  }

  buildFallbackPcmBlob() {
    return buildFallbackPcmBlob(this, this.scope);
  }

  releaseFallbackPcm() {
    return releaseFallbackPcm(this);
  }

  resolveCancel(acknowledged) {
    if (this.cancelTimeoutId) {
      this.scope.clearTimeout(this.cancelTimeoutId);
      this.cancelTimeoutId = 0;
    }
    const resolve = this.cancelResolver;
    this.cancelResolver = null;
    resolve?.(Boolean(acknowledged));
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
    if (this.closed) return;
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
      try {
        this.handleSpeechBinary(await data.arrayBuffer());
      } catch {
        if (!this.closed) this.fail("voice_realtime_server_frame_decode_failed", { terminal: true });
      }
      return;
    }
    this.fail("voice_realtime_server_frame_invalid", { terminal: true });
  }

  handleServerEvent(payload) {
    if (this.closed) return;
    const type = String(payload?.type || "");
    if (type === "server.ready") {
      this.ready = true;
      this.clearProviderReadyTimeout();
      this.settleReadyWaiters(true);
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
      if (this.pendingInterruptionClockMs !== null) {
        this.sendInterruptionSignal(this.pendingInterruptionClockMs);
      }
      for (const frame of this.pendingFrames.splice(0)) this.sendPcmFrame(frame);
      this.notify("onReady", payload);
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
    if (type === "server.turn.interaction") {
      if (payload?.state !== "committed" || payload?.disposition !== "interaction") return;
      this.responseTerminal = true;
      this.notify("onInteraction", payload);
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
    if (
      type === "server.interruption.accepted" ||
      type === "server.interruption.skipped"
    ) {
      this.notify(
        type === "server.interruption.accepted"
          ? "onInterruptionAccepted"
          : "onInterruptionSkipped",
        payload
      );
      return;
    }
    if (type === "server.response.completed") {
      this.responseTerminal = true;
      this.notify("onResponseCompleted", payload);
      return;
    }
    if (type === "server.response.presentation") {
      if (!this.responseTerminal) this.notify("onPresentation", payload);
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
      this.resolveCancel(true);
      this.notify("onCancelled", payload);
      this.dispose("server_cancelled");
      return;
    }
    if (type === "server.playback.ack" || type === "server.finalizing" || type === "server.audio_accepted") {
      this.notify("onProtocolEvent", payload);
    }
  }

  handleSpeechBinary(buffer) {
    if (this.closed) return;
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

  armProviderReadyTimeout() {
    this.clearProviderReadyTimeout();
    if (this.ready || this.closed) return;
    this.readyTimeoutId = this.scope.setTimeout(() => {
      this.readyTimeoutId = 0;
      if (this.ready || this.closed) return;
      this.fail("voice_realtime_provider_ready_timeout", {
        terminal: true,
        retryable: true
      });
    }, this.readyTimeoutMs);
  }

  clearProviderReadyTimeout() {
    if (!this.readyTimeoutId) return;
    this.scope.clearTimeout(this.readyTimeoutId);
    this.readyTimeoutId = 0;
  }

  waitUntilReady() {
    if (this.ready) return Promise.resolve(true);
    if (this.failed || this.closed) return Promise.resolve(false);
    return new Promise((resolve) => {
      this.readyWaiters.add(resolve);
    });
  }

  settleReadyWaiters(ready) {
    if (!this.readyWaiters.size) return;
    for (const resolve of this.readyWaiters) resolve(Boolean(ready));
    this.readyWaiters.clear();
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
