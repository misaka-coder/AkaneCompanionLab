// Capture lease and idle PCM; audio playback shares the same call-owned resources.

import { RealtimeVoicePlaybackQueue } from "./playback.js";
import { CAPTURE_FLUSH_TIMEOUT_MS, DEFAULT_IDLE_PRE_ROLL_MS, DEFAULT_IDLE_CAPTURE_MAX_MS } from "./protocol.js";

export class RealtimeVoiceCallResources {
  constructor({
    mediaStream,
    audioElement,
    captureReceiptTimeoutMs = CAPTURE_FLUSH_TIMEOUT_MS,
    idlePreRollMs = DEFAULT_IDLE_PRE_ROLL_MS,
    idleCaptureMaxMs = DEFAULT_IDLE_CAPTURE_MAX_MS
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
    this.idlePreRollMs = Math.max(0, Number(idlePreRollMs) || 0);
    this.idleCaptureMaxMs = Math.max(
      this.idlePreRollMs,
      Number(idleCaptureMaxMs) || DEFAULT_IDLE_CAPTURE_MAX_MS
    );
    this.idleCaptureFrames = [];
    this.idleCaptureFrameCount = 0;
    this.idleCaptureHeld = false;
    this.idleCaptureOverflowed = false;
    this.idleCapturePcmSink = null;
    this.idleCaptureErrorSink = null;
    this.closed = false;
  }

  setIdleCaptureObserver({ onPcm = null, onError = null } = {}) {
    this.idleCapturePcmSink = typeof onPcm === "function" ? onPcm : null;
    this.idleCaptureErrorSink = typeof onError === "function" ? onError : null;
  }

  holdIdleCapture() {
    if (this.closed) return false;
    this.idleCaptureHeld = true;
    return true;
  }

  resetIdleCapture() {
    this.idleCaptureFrames = [];
    this.idleCaptureFrameCount = 0;
    this.idleCaptureHeld = false;
    this.idleCaptureOverflowed = false;
  }

  replayIdleCapture(owner) {
    if (this.captureOwner !== owner || typeof this.capturePcmSink !== "function") {
      return 0;
    }
    const frames = this.idleCaptureFrames;
    const frameCount = this.idleCaptureFrameCount;
    this.resetIdleCapture();
    for (const frame of frames) {
      try {
        this.capturePcmSink(frame.buffer, frame.frameCount);
      } catch (error) {
        this.captureErrorSink?.(error);
        break;
      }
    }
    return frameCount;
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
    if (this.closed) throw new Error("voice_call_resources_closed");
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
      if (this.closed) throw new Error("voice_call_resources_closed");
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
      return Number(context.sampleRate || 0);
    } catch (error) {
      await this.stopCaptureEngine();
      throw error;
    }
  }

  handleCaptureMessage(payload) {
    if (this.closed) return;
    const type = String(payload?.type || "");
    if (type === "pcm" && payload.buffer instanceof ArrayBuffer) {
      const frames = Number(payload.frames || payload.buffer.byteLength / 4);
      if (!this.captureOwner || !this.capturePcmSink) {
        this.retainIdleCaptureFrame(payload.buffer, frames);
        try {
          this.idleCapturePcmSink?.(
            payload.buffer,
            frames,
            Number(this.captureContext?.sampleRate || 0)
          );
        } catch (error) {
          this.idleCaptureErrorSink?.(error);
        }
        return;
      }
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

  retainIdleCaptureFrame(buffer, frameCount) {
    const frames = Math.max(0, Math.round(Number(frameCount) || 0));
    if (!(buffer instanceof ArrayBuffer) || !frames) return;
    this.idleCaptureFrames.push({ buffer, frameCount: frames });
    this.idleCaptureFrameCount += frames;
    const sampleRate = Math.max(1, Number(this.captureContext?.sampleRate || 0));
    const maxMs = this.idleCaptureHeld
      ? this.idleCaptureMaxMs
      : this.idlePreRollMs;
    const maxFrames = Math.max(1, Math.round((sampleRate * maxMs) / 1000));
    while (
      this.idleCaptureFrameCount > maxFrames &&
      this.idleCaptureFrames.length > 1
    ) {
      const removed = this.idleCaptureFrames.shift();
      this.idleCaptureFrameCount -= Number(removed?.frameCount || 0);
      if (this.idleCaptureHeld && !this.idleCaptureOverflowed) {
        this.idleCaptureOverflowed = true;
        this.idleCaptureErrorSink?.(
          new Error("voice_call_idle_capture_overflow")
        );
      }
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
    this.idleCapturePcmSink = null;
    this.idleCaptureErrorSink = null;
    this.resetIdleCapture();
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
