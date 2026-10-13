// RealtimeVoiceCallSession: state stays on this session, with explicit protocol and audio dependencies.

import {
  VOICE_PLAYBACK_OUTPUT_MODE,
  VOICE_REALTIME_CALL_PROTOCOL_VERSION,
  DEFAULT_CALL_READY_TIMEOUT_MS,
  safeReason
} from "./protocol.js";
import { RealtimeVoiceCallTurn } from "./call-turn.js";

export class RealtimeVoiceCallSession {
  constructor({
    websocketUrl,
    getSocketProtocols = null,
    callResources,
    openPayload,
    workletModuleUrl,
    getVolume = () => 1,
    callbacks = {},
    callReadyTimeoutMs = DEFAULT_CALL_READY_TIMEOUT_MS,
    scope = globalThis
  }) {
    if (!callResources) throw new Error("voice_call_resources_missing");
    this.websocketUrl = websocketUrl;
    this.getSocketProtocols = getSocketProtocols;
    this.callResources = callResources;
    this.openPayload = openPayload || {};
    this.workletModuleUrl = workletModuleUrl;
    this.getVolume = getVolume;
    this.callbacks = callbacks;
    this.callReadyTimeoutMs = Math.max(1000, Number(callReadyTimeoutMs) || DEFAULT_CALL_READY_TIMEOUT_MS);
    this.scope = scope;
    this.socket = null;
    this.captureSampleRate = 0;
    this.startPromise = null;
    this.readyPromise = null;
    this.readyResolver = null;
    this.readyRejecter = null;
    this.readyTimeoutId = 0;
    this.ready = false;
    this.transportOpen = false;
    this.closed = false;
    this.failed = false;
    this.currentTurn = null;
    this.turns = new Map();
    this.pendingSpeechHeader = null;
  }

  createTurn({
    voiceTurnId,
    audioStreamId,
    endpointDetector,
    callbacks = {}
  }) {
    if (this.closed) throw new Error("voice_call_session_closed");
    const turnId = String(voiceTurnId || "").trim();
    const streamId = String(audioStreamId || "").trim();
    if (!turnId || !streamId) throw new Error("voice_call_turn_identity_missing");
    if (this.turns.has(turnId)) throw new Error("voice_call_turn_identity_reused");
    const turn = new RealtimeVoiceCallTurn({
      session: this,
      voiceTurnId: turnId,
      audioStreamId: streamId,
      endpointDetector,
      callbacks
    });
    this.turns.set(turnId, turn);
    return turn;
  }

  start() {
    if (this.startPromise) return this.startPromise;
    this.startPromise = this.startInternal();
    return this.startPromise;
  }

  async startInternal() {
    this.captureSampleRate = Number(
      await this.callResources.ensureCaptureStarted({
        scope: this.scope,
        workletModuleUrl: this.workletModuleUrl
      }) || 0
    );
    await this.openSocket();
    await this.waitUntilReady();
    return this;
  }

  async startTurn(turn) {
    if (!(turn instanceof RealtimeVoiceCallTurn) || turn.session !== this) {
      throw new Error("voice_call_turn_invalid");
    }
    if (this.currentTurn && this.currentTurn !== turn) {
      throw new Error("voice_call_input_turn_active");
    }
    this.currentTurn = turn;
    try {
      this.requireActiveTurn(turn);
      await this.callResources.ensureCaptureStarted({
        scope: this.scope,
        workletModuleUrl: this.workletModuleUrl
      });
      this.requireActiveTurn(turn);
      await this.callResources.acquireCapture(turn, {
        scope: this.scope,
        workletModuleUrl: this.workletModuleUrl,
        onPcm: (buffer, frames) => turn.acceptPcmFrame(buffer, frames),
        onError: (error) => turn.fail(String(error?.message || "voice_capture_failed"), true)
      });
      turn.captureAttached = true;
      this.requireActiveTurn(turn);
      turn.captureSampleRate = this.captureSampleRate || Number(this.callResources.captureContext?.sampleRate || 0);
      this.callResources.replayIdleCapture(turn);
      await this.start();
      if (!this.ready) await this.waitUntilReady();
      this.requireActiveTurn(turn);
      if (!this.sendJson({
        type: "client.turn.start",
        protocol_version: VOICE_REALTIME_CALL_PROTOCOL_VERSION,
        voice_turn_id: turn.voiceTurnId,
        audio_stream_id: turn.audioStreamId
      })) {
        throw new Error("voice_call_turn_start_send_failed");
      }
      const turnReady = await turn.waitUntilReady();
      if (!turnReady || turn.closed || turn.failed) {
        throw new Error("voice_call_turn_not_ready");
      }
      return turn;
    } catch (error) {
      await turn.releaseCapture(false);
      if (this.currentTurn === turn) this.currentTurn = null;
      throw error;
    }
  }

  requireActiveTurn(turn) {
    if (this.closed || this.failed || turn.closed || turn.failed || this.currentTurn !== turn) {
      throw new Error("voice_call_turn_not_ready");
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
      const settle = (error = null) => {
        if (settled) return;
        settled = true;
        this.scope.clearTimeout(timeoutId);
        if (error) reject(error);
        else resolve(this);
      };
      const timeoutId = this.scope.setTimeout(() => {
        this.fail("voice_call_websocket_open_timeout", true, true);
        settle(new Error("voice_call_websocket_open_timeout"));
      }, 5000);
      socket.addEventListener("open", () => {
        if (this.closed) return;
        this.transportOpen = true;
        if (!this.sendJson({
          ...this.openPayload,
          type: "client.call.open",
          protocol_version: VOICE_REALTIME_CALL_PROTOCOL_VERSION,
          input: {
            format: "f32le",
            sample_rate: Math.round(this.captureSampleRate),
            channels: 1
          },
          output: { mode: VOICE_PLAYBACK_OUTPUT_MODE }
        })) {
          settle(new Error("voice_call_open_send_failed"));
          return;
        }
        this.notify("onTransportOpen");
        settle();
      });
      socket.addEventListener("message", (event) => { void this.handleSocketMessage(event?.data); });
      socket.addEventListener("error", () => {
        const error = new Error("voice_call_websocket_failed");
        if (!settled) settle(error);
        this.fail("voice_call_websocket_failed", true, true);
      });
      socket.addEventListener("close", () => {
        if (!settled) settle(new Error("voice_call_closed_before_open"));
        if (!this.closed && !this.failed) this.fail("voice_call_connection_closed", true, true);
      });
    });
  }

  waitUntilReady() {
    if (this.ready) return Promise.resolve(true);
    if (this.failed || this.closed) return Promise.reject(new Error("voice_call_not_ready"));
    if (!this.readyPromise) {
      this.readyPromise = new Promise((resolve, reject) => {
        this.readyResolver = resolve;
        this.readyRejecter = reject;
      });
      this.readyTimeoutId = this.scope.setTimeout(() => {
        this.readyTimeoutId = 0;
        this.fail(
          "voice_call_ready_timeout",
          true,
          true,
          "实时识别服务没有及时就绪，已停止等待。"
        );
      }, this.callReadyTimeoutMs);
    }
    return this.readyPromise;
  }

  acceptPcmFrame(turn, buffer, frameCount) {
    if (this.closed || this.failed || turn.closed || turn.endpointSent) return;
    turn.retainFallbackPcmFrame(buffer, frameCount);
    try {
      turn.endpointDetector?.acceptPcmFrame?.(buffer, frameCount, turn.captureSampleRate);
    } catch (error) {
      turn.fail(String(error?.message || "voice_endpoint_detector_failed"), true);
      return;
    }
    if (!turn.ready) {
      turn.pendingFrames.push({ buffer, frameCount: Math.max(0, Math.round(frameCount)) });
      turn.pendingPcmFrameCount += Math.max(0, Math.round(frameCount));
      const maxPendingFrames = Math.max(
        1,
        Math.round(
          (Math.max(1, Number(turn.captureSampleRate || this.captureSampleRate || 0)) *
            turn.pendingPcmMaxMs) /
            1000
        )
      );
      if (turn.pendingPcmFrameCount > maxPendingFrames) {
        this.fail(
          "voice_call_provider_not_ready_for_input",
          true,
          true,
          "实时识别服务没有及时接住这句话，已停止等待。"
        );
      }
      return;
    }
    turn.sendPcmFrame({ buffer, frameCount });
  }

  handleSocketMessage(data) {
    if (this.closed) return;
    if (typeof data === "string") {
      let payload;
      try { payload = JSON.parse(data); } catch { this.fail("voice_call_server_json_invalid", true, false); return; }
      this.handleServerEvent(payload);
      return;
    }
    if (data instanceof ArrayBuffer) { this.handleSpeechBinary(data); return; }
    if (data?.arrayBuffer) {
      void Promise.resolve().then(() => data.arrayBuffer())
        .then(value => this.handleSpeechBinary(value))
        .catch(() => {
          if (!this.closed) this.fail("voice_call_server_frame_decode_failed", true, false);
        });
      return;
    }
    this.fail("voice_call_server_frame_invalid", true, false);
  }

  handleServerEvent(payload) {
    if (this.closed) return;
    const type = String(payload?.type || "");
    if (type === "server.call.ready") {
      if (this.closed || this.failed) return;
      this.ready = true;
      if (this.readyTimeoutId) this.scope.clearTimeout(this.readyTimeoutId);
      this.readyTimeoutId = 0;
      this.readyResolver?.(true);
      this.readyResolver = null;
      this.readyRejecter = null;
      this.notify("onReady", payload);
      return;
    }
    if (type === "server.turn.ready") {
      const turn = this.turns.get(String(payload?.voice_turn_id || ""));
      if (!turn) return;
      turn.ready = true;
      turn.readyAt = Date.now();
      turn.createPlaybackQueue();
      for (const frame of turn.pendingFrames.splice(0)) turn.sendPcmFrame(frame);
      turn.pendingPcmFrameCount = 0;
      turn.resolveReady(true);
      turn.notify("onReady", payload);
      return;
    }
    if (type === "server.turn.partial" || type === "server.turn.checkpoint") {
      const turn = this.turns.get(String(payload?.voice_turn_id || ""));
      if (!turn) return;
      turn.handleTranscript({
        kind: type === "server.turn.checkpoint" ? "checkpoint" : "partial",
        text: String(payload?.text || ""),
        unstableTail: String(payload?.unstable_tail || "")
      });
      return;
    }
    if (type === "server.turn.final") {
      const turn = this.turns.get(String(payload?.voice_turn_id || ""));
      if (!turn) return;
      turn.serverFinalCommitted = true;
      if (this.currentTurn === turn) this.currentTurn = null;
      turn.notify("onFinal", payload);
      return;
    }
    if (type === "server.turn.interaction") {
      const turn = this.turns.get(String(payload?.voice_turn_id || ""));
      if (!turn || payload?.state !== "committed" || payload?.disposition !== "interaction") return;
      turn.responseTerminal = true;
      turn.notify("onInteraction", payload);
      return;
    }
    if (type === "server.speech") {
      if (this.pendingSpeechHeader) { this.fail("voice_call_speech_header_pending", true, false); return; }
      this.pendingSpeechHeader = payload;
      return;
    }
    if (type === "server.playback.control") {
      const turn = this.turns.get(String(payload?.voice_turn_id || ""));
      try { turn?.playbackQueue?.applyControl(payload); } catch (error) { this.fail(String(error?.message || "voice_playback_control_failed"), false, false); }
      return;
    }
    if (type === "server.interruption.accepted" || type === "server.interruption.skipped") {
      this.turns.get(String(payload?.voice_turn_id || ""))?.notify(
        type === "server.interruption.accepted" ? "onInterruptionAccepted" : "onInterruptionSkipped",
        payload
      );
      return;
    }
    if (type === "server.response.completed" || type === "server.response.failed") {
      const turn = this.turns.get(String(payload?.voice_turn_id || ""));
      if (!turn) return;
      turn.responseTerminal = true;
      turn.notify(type === "server.response.completed" ? "onResponseCompleted" : "onResponseFailed", payload);
      return;
    }
    if (type === "server.response.presentation") {
      const turn = this.turns.get(String(payload?.voice_turn_id || ""));
      if (turn && !turn.responseTerminal) turn.notify("onPresentation", payload);
      return;
    }
    if (type === "server.response.cancelled" || type === "server.turn.cancelled") {
      const turn = this.turns.get(String(payload?.voice_turn_id || ""));
      if (!turn) return;
      turn.resolveCancel(true);
      turn.notify("onCancelled", payload);
      return;
    }
    if (type === "server.failed") {
      const turn = this.turns.get(String(payload?.voice_turn_id || ""));
      if (turn && !payload?.terminal) {
        const startupFailure = !turn.ready;
        turn.fail(
          String(payload?.reason || "voice_call_turn_failed"),
          startupFailure,
          Boolean(payload?.retryable),
          payload?.message
        );
      } else if (!turn && !payload?.terminal && this.currentTurn && !this.currentTurn.ready) {
        this.currentTurn.fail(
          String(payload?.reason || "voice_call_turn_failed"),
          true,
          Boolean(payload?.retryable),
          payload?.message
        );
      } else {
        this.fail(String(payload?.reason || "voice_call_failed"), Boolean(payload?.terminal), Boolean(payload?.retryable), payload?.message);
      }
      return;
    }
    if (type === "server.call.closed") {
      this.disposeTransport("voice_call_closed");
      this.notify("onCallClosed", payload);
      return;
    }
    this.notify("onProtocolEvent", payload);
  }

  handleSpeechBinary(buffer) {
    if (this.closed) return;
    const header = this.pendingSpeechHeader;
    this.pendingSpeechHeader = null;
    if (!header) { this.fail("voice_call_speech_header_missing", true, false); return; }
    const turn = this.turns.get(String(header?.voice_turn_id || ""));
    if (!turn) { this.fail("voice_call_speech_turn_unknown", true, false); return; }
    try { turn.enqueueSpeech(header, buffer); } catch (error) { this.fail(String(error?.message || "voice_playback_enqueue_failed"), true, false); }
  }

  sendJson(payload) {
    if (this.socket?.readyState !== 1) return false;
    this.socket.send(JSON.stringify(payload));
    return true;
  }

  async close(reason = "voice_call_closed") {
    if (this.closed) return;
    if (this.ready && this.socket?.readyState === 1) {
      this.sendJson({ type: "client.call.close", reason: safeReason(reason) });
    }
    this.disposeTransport(reason);
  }

  disposeTransport(reason) {
    if (this.closed) return;
    this.closed = true;
    this.pendingSpeechHeader = null;
    for (const turn of [...this.turns.values()]) turn.dispose(reason);
    this.readyRejecter?.(new Error(safeReason(reason)));
    this.readyRejecter = null;
    this.readyResolver = null;
    if (this.readyTimeoutId) this.scope.clearTimeout(this.readyTimeoutId);
    this.readyTimeoutId = 0;
    try { this.socket?.close(1000, "voice_call_closed"); } catch { /* socket already closed */ }
  }

  fail(reason, terminal = false, retryable = false, message = "") {
    if (terminal) this.failed = true;
    this.notify("onFailure", { reason: safeReason(reason), terminal, retryable, message });
    if (terminal) {
      this.readyRejecter?.(new Error(safeReason(reason)));
      this.readyRejecter = null;
      this.readyResolver = null;
      for (const turn of [...this.turns.values()]) {
        turn.fail(reason, true, retryable, message);
      }
    }
  }

  notify(name, ...args) {
    try { this.callbacks?.[name]?.(...args); } catch { /* presentation is observational */ }
  }
}
