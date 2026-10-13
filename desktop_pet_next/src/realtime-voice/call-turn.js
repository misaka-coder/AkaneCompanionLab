// RealtimeVoiceCallTurn: state stays on this session, with explicit protocol and audio dependencies.

import {
  DEFAULT_FALLBACK_PCM_MAX_MS,
  VOICE_REALTIME_CALL_PROTOCOL_VERSION,
  DEFAULT_CALL_PENDING_PCM_MAX_MS,
  CALL_RESPONSE_CANCEL_TIMEOUT_MS,
  safeReason
} from "./protocol.js";
import { retainFallbackPcmFrame, buildFallbackPcmBlob, releaseFallbackPcm } from "./pcm.js";

export class RealtimeVoiceCallTurn {
  constructor({ session, voiceTurnId, audioStreamId, endpointDetector, callbacks }) {
    this.session = session;
    this.voiceTurnId = voiceTurnId;
    this.audioStreamId = audioStreamId;
    this.endpointDetector = endpointDetector;
    this.callbacks = callbacks || {};
    this.ready = false;
    this.readyAt = 0;
    this.captureAttached = false;
    this.captureSampleRate = 0;
    this.pendingFrames = [];
    this.pendingPcmFrameCount = 0;
    this.pendingPcmMaxMs = DEFAULT_CALL_PENDING_PCM_MAX_MS;
    this.sequence = 0;
    this.audioFramesSent = 0;
    this.endpointSent = false;
    this.serverFinalCommitted = false;
    this.responseTerminal = false;
    this.closed = false;
    this.failed = false;
    this.readyPromise = new Promise((resolve) => { this.readyResolver = resolve; });
    this.cancelPromise = null;
    this.cancelResolver = null;
    this.cancelTimeoutId = 0;
    this.fallbackPcmFrames = [];
    this.fallbackPcmFrameCount = 0;
    this.fallbackPcmDroppedFrames = 0;
    this.fallbackPcmMaxMs = DEFAULT_FALLBACK_PCM_MAX_MS;
    this.playbackQueue = null;
  }

  start() { return this.session.startTurn(this); }

  waitUntilReady() {
    if (this.ready) return Promise.resolve(true);
    if (this.failed || this.closed) return Promise.resolve(false);
    return this.readyPromise;
  }

  resolveReady(ok) {
    this.readyResolver?.(Boolean(ok));
    this.readyResolver = null;
  }

  createPlaybackQueue() {
    if (this.playbackQueue || this.session.closed) return this.playbackQueue;
    this.playbackQueue = this.session.callResources.createPlaybackQueue({
      sendJson: (payload) => this.session.sendJson(payload),
      getVolume: this.session.getVolume,
      callbacks: this.callbacks,
      BlobImpl: this.session.scope.Blob,
      createObjectUrl: (blob) => this.session.scope.URL.createObjectURL(blob),
      revokeObjectUrl: (url) => this.session.scope.URL.revokeObjectURL(url),
      now: () => this.session.scope.performance?.now?.() ?? Date.now()
    });
    return this.playbackQueue;
  }

  enqueueSpeech(header, audioBytes) {
    this.createPlaybackQueue()?.enqueue(header, audioBytes);
  }

  handleTranscript(transcript) {
    try { this.endpointDetector?.observeTranscript?.(transcript); } catch (error) { this.fail(String(error?.message || "voice_endpoint_detector_failed"), true); return; }
    this.notify("onTranscript", transcript);
  }

  acceptPcmFrame(buffer, frameCount) {
    this.session.acceptPcmFrame(this, buffer, frameCount);
  }

  retainFallbackPcmFrame(buffer, frameCount) {
    return retainFallbackPcmFrame(this, buffer, frameCount);
  }

  buildFallbackPcmBlob() {
    return buildFallbackPcmBlob(this, this.session.scope);
  }

  releaseFallbackPcm() {
    return releaseFallbackPcm(this);
  }

  sendPcmFrame(frame) {
    const sampleRate = Number(this.captureSampleRate || 1);
    const audioClockMs = Math.round((this.audioFramesSent * 1000) / sampleRate);
    if (!this.session.sendJson({
      type: "client.audio",
      voice_turn_id: this.voiceTurnId,
      sequence: this.sequence,
      audio_clock_ms: audioClockMs
    })) return false;
    this.session.socket.send(frame.buffer);
    this.sequence += 1;
    this.audioFramesSent += Math.max(0, Math.round(Number(frame.frameCount) || 0));
    return true;
  }

  async flushAndStopCapture() { await this.releaseCapture(true); }
  async stopCapture() { await this.releaseCapture(false); }

  async releaseCapture(flush) {
    if (!this.captureAttached) return;
    this.captureAttached = false;
    await this.session.callResources.releaseCapture(this, { flush });
  }

  async finishInput() {
    await this.session.start();
    await this.waitUntilReady();
    if (this.closed || this.failed || this.endpointSent) return;
    this.endpointSent = true;
    if (!this.session.sendJson({
      type: "client.turn.endpoint",
      protocol_version: VOICE_REALTIME_CALL_PROTOCOL_VERSION,
      voice_turn_id: this.voiceTurnId
    })) throw new Error("voice_call_turn_endpoint_send_failed");
    this.notify("onFinalizing");
  }

  reportInterruptionSuspected() {
    if (this.closed || this.endpointSent || this.failed) return false;
    const sampleRate = Number(this.captureSampleRate || 1);
    return this.session.sendJson({
      type: "client.interruption.suspected",
      voice_turn_id: this.voiceTurnId,
      audio_clock_ms: Math.max(0, Math.round((this.audioFramesSent * 1000) / sampleRate))
    });
  }

  async cancel(reason = "client_cancelled") {
    if (this.cancelPromise) return this.cancelPromise;
    this.cancelPromise = this.cancelInternal(reason);
    return this.cancelPromise;
  }

  async cancelInternal(reason) {
    if (this.closed) return false;
    this.playbackQueue?.interrupt(reason);
    await this.releaseCapture(false);
    if (!this.serverFinalCommitted) {
      await this.session.close(reason);
      return true;
    }
    if (!this.session.sendJson({
      type: "client.response.cancel",
      protocol_version: VOICE_REALTIME_CALL_PROTOCOL_VERSION,
      voice_turn_id: this.voiceTurnId,
      reason: safeReason(reason)
    })) {
      this.dispose(reason);
      return false;
    }
    return new Promise((resolve) => {
      this.cancelResolver = resolve;
      this.cancelTimeoutId = this.session.scope.setTimeout(() => {
        this.resolveCancel(false);
        this.dispose("voice_call_cancel_timeout");
      }, CALL_RESPONSE_CANCEL_TIMEOUT_MS);
    });
  }

  resolveCancel(acknowledged) {
    if (this.cancelTimeoutId) this.session.scope.clearTimeout(this.cancelTimeoutId);
    this.cancelTimeoutId = 0;
    const resolve = this.cancelResolver;
    this.cancelResolver = null;
    resolve?.(Boolean(acknowledged));
  }

  fail(reason, terminal = false, retryable = false, message = "") {
    if (terminal) this.failed = true;
    this.notify("onFailure", { reason: safeReason(reason), terminal, retryable, message, committed: this.serverFinalCommitted });
    if (terminal) this.resolveReady(false);
  }

  dispose(reason = "voice_turn_closed") {
    if (this.closed) return;
    this.closed = true;
    this.resolveReady(false);
    this.resolveCancel(false);
    void this.releaseCapture(false);
    this.playbackQueue?.close(reason);
    this.playbackQueue = null;
    this.pendingFrames = [];
    this.pendingPcmFrameCount = 0;
    this.session.turns.delete(this.voiceTurnId);
    if (this.session.currentTurn === this) this.session.currentTurn = null;
  }

  notify(name, ...args) {
    try { this.callbacks?.[name]?.(...args); } catch { /* presentation is observational */ }
  }
}
