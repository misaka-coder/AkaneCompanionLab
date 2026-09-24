const DEFAULT_SAMPLE_RATE = 16000;

export class RealtimeVoiceEndpointDetector {
  constructor({
    minRms = 0.012,
    noiseFloorRms = 0.003,
    noiseMultiplier = 3,
    transcriptNoiseMultiplier = 1.35,
    releaseRatio = 0.68,
    calibrationMs = 300,
    speechStartMs = 80,
    minSpeechMs = 180,
    checkpointSilenceMs = 360,
    partialSilenceMs = 680,
    noTextSilenceMs = 1100,
    endpointConfirmationMs = 280,
    noiseAdaptation = 0.06,
    onSpeechStarted = null,
    onEndpoint = null
  } = {}) {
    this.options = {
      minRms: positiveNumber(minRms, 0.012),
      initialNoiseFloorRms: positiveNumber(noiseFloorRms, 0.003),
      noiseMultiplier: positiveNumber(noiseMultiplier, 3),
      transcriptNoiseMultiplier: positiveNumber(transcriptNoiseMultiplier, 1.35),
      releaseRatio: clamp(Number(releaseRatio), 0.25, 0.95),
      calibrationMs: positiveNumber(calibrationMs, 300),
      speechStartMs: positiveNumber(speechStartMs, 80),
      minSpeechMs: positiveNumber(minSpeechMs, 180),
      checkpointSilenceMs: positiveNumber(checkpointSilenceMs, 360),
      partialSilenceMs: positiveNumber(partialSilenceMs, 680),
      noTextSilenceMs: positiveNumber(noTextSilenceMs, 1100),
      endpointConfirmationMs: positiveNumber(endpointConfirmationMs, 280),
      noiseAdaptation: clamp(Number(noiseAdaptation), 0.001, 0.5)
    };
    this.onSpeechStarted = onSpeechStarted;
    this.onEndpoint = onEndpoint;
    this.reset();
  }

  reset() {
    this.noiseFloorRms = this.options.initialNoiseFloorRms;
    this.lastRms = 0;
    this.totalAudioMs = 0;
    this.candidateVoiceMs = 0;
    this.speechDurationMs = 0;
    this.utteranceDurationMs = 0;
    this.trailingSilenceMs = 0;
    this.voiced = false;
    this.speechStarted = false;
    this.endpointEmitted = false;
    this.endpointPending = false;
    this.endpointPendingAction = "";
    this.endpointPendingReason = "";
    this.endpointPendingAtSilenceMs = 0;
    this.hasTranscript = false;
    this.hasStableCheckpoint = false;
    this.transcriptKind = "";
    return this.snapshot();
  }

  acceptPcmFrame(buffer, frameCount, sampleRate = DEFAULT_SAMPLE_RATE) {
    if (this.endpointEmitted) return this.snapshot();
    const frames = Math.max(0, Math.round(Number(frameCount) || 0));
    const rate = positiveNumber(sampleRate, DEFAULT_SAMPLE_RATE);
    if (!(buffer instanceof ArrayBuffer) || !frames) return this.snapshot();

    const durationMs = (frames * 1000) / rate;
    const rms = calculateRms(buffer, frames);
    this.lastRms = rms;
    this.totalAudioMs += durationMs;

    const noiseMultiplier = this.hasTranscript
      ? this.options.transcriptNoiseMultiplier
      : this.options.noiseMultiplier;
    const startThreshold = Math.max(
      this.options.minRms,
      this.noiseFloorRms * noiseMultiplier
    );
    const activeThreshold = this.voiced
      ? startThreshold * this.options.releaseRatio
      : startThreshold;
    const calibrating =
      this.totalAudioMs <= this.options.calibrationMs && !this.hasTranscript;
    const frameVoiced = !calibrating && rms >= activeThreshold;

    if (!this.speechStarted && (!frameVoiced || calibrating)) {
      this.updateNoiseFloor(rms, { calibrating });
    }

    if (frameVoiced) {
      this.clearPendingEndpoint();
      this.voiced = true;
      this.candidateVoiceMs += durationMs;
      this.trailingSilenceMs = 0;
      if (!this.speechStarted && this.candidateVoiceMs >= this.options.speechStartMs) {
        this.speechStarted = true;
        this.speechDurationMs = this.candidateVoiceMs;
        this.utteranceDurationMs = this.candidateVoiceMs;
        this.notify(this.onSpeechStarted, this.snapshot());
      } else if (this.speechStarted) {
        this.speechDurationMs += durationMs;
        this.utteranceDurationMs += durationMs;
      }
    } else {
      this.voiced = false;
      if (this.speechStarted) {
        this.trailingSilenceMs += durationMs;
        this.utteranceDurationMs += durationMs;
      } else {
        this.candidateVoiceMs = 0;
      }
    }

    this.evaluateEndpoint();
    return this.snapshot();
  }

  observeTranscript({ kind = "", text = "", unstableTail = "" } = {}) {
    if (this.endpointEmitted) return this.snapshot();
    const normalizedKind = String(kind || "").trim().toLowerCase();
    const combinedText = `${String(text || "")}${String(unstableTail || "")}`.trim();
    if (combinedText) {
      this.hasTranscript = true;
      this.transcriptKind = normalizedKind || this.transcriptKind || "partial";
      if (!this.speechStarted) {
        this.speechStarted = true;
        this.speechDurationMs = this.candidateVoiceMs;
        this.utteranceDurationMs = this.candidateVoiceMs;
        this.notify(this.onSpeechStarted, this.snapshot());
      }
    }
    if (normalizedKind === "checkpoint" && String(text || "").trim()) {
      this.hasStableCheckpoint = true;
      this.transcriptKind = "checkpoint";
    }
    this.evaluateEndpoint();
    return this.snapshot();
  }

  evaluateEndpoint() {
    if (!this.speechStarted || this.endpointEmitted) return;
    if (
      this.hasStableCheckpoint &&
      this.trailingSilenceMs >= this.options.checkpointSilenceMs
    ) {
      this.proposeEndpoint(
        "commit",
        "stable_checkpoint_silence",
        this.options.checkpointSilenceMs
      );
      return;
    }
    if (
      this.hasTranscript &&
      this.trailingSilenceMs >= this.options.partialSilenceMs
    ) {
      this.proposeEndpoint(
        "commit",
        "partial_transcript_silence",
        this.options.partialSilenceMs
      );
      return;
    }
    if (
      !this.hasTranscript &&
      this.trailingSilenceMs >= this.options.noTextSilenceMs
    ) {
      const reason =
        this.speechDurationMs >= this.options.minSpeechMs
          ? "speech_without_transcript"
          : "unconfirmed_acoustic_activity";
      this.proposeEndpoint("discard", reason, this.options.noTextSilenceMs);
    }
  }

  proposeEndpoint(action, reason, silenceThresholdMs) {
    if (
      !this.endpointPending ||
      this.endpointPendingAction !== action ||
      this.endpointPendingReason !== reason
    ) {
      this.endpointPending = true;
      this.endpointPendingAction = action;
      this.endpointPendingReason = reason;
      this.endpointPendingAtSilenceMs = Math.max(
        Number(silenceThresholdMs) || 0,
        this.trailingSilenceMs
      );
      return;
    }
    if (
      this.trailingSilenceMs - this.endpointPendingAtSilenceMs <
      this.options.endpointConfirmationMs
    ) {
      return;
    }
    this.emitEndpoint(action, reason);
  }

  clearPendingEndpoint() {
    this.endpointPending = false;
    this.endpointPendingAction = "";
    this.endpointPendingReason = "";
    this.endpointPendingAtSilenceMs = 0;
  }

  emitEndpoint(action, reason) {
    if (this.endpointEmitted) return;
    this.endpointEmitted = true;
    this.clearPendingEndpoint();
    this.notify(this.onEndpoint, {
      ...this.snapshot(),
      action,
      reason
    });
  }

  updateNoiseFloor(rms, { calibrating = false } = {}) {
    const alpha = calibrating
      ? Math.max(this.options.noiseAdaptation, 0.18)
      : this.options.noiseAdaptation;
    const boundedRms = Math.min(rms, 0.05);
    this.noiseFloorRms =
      this.noiseFloorRms * (1 - alpha) + boundedRms * alpha;
  }

  snapshot() {
    return {
      speech_started: this.speechStarted,
      endpoint_emitted: this.endpointEmitted,
      endpoint_pending: this.endpointPending,
      endpoint_pending_action: this.endpointPendingAction,
      endpoint_pending_reason: this.endpointPendingReason,
      has_transcript: this.hasTranscript,
      has_stable_checkpoint: this.hasStableCheckpoint,
      transcript_kind: this.transcriptKind,
      voiced: this.voiced,
      rms: roundMetric(this.lastRms, 6),
      noise_floor_rms: roundMetric(this.noiseFloorRms, 6),
      total_audio_ms: Math.round(this.totalAudioMs),
      speech_ms: Math.round(this.speechDurationMs),
      utterance_ms: Math.round(this.utteranceDurationMs),
      trailing_silence_ms: Math.round(this.trailingSilenceMs)
    };
  }

  notify(callback, payload) {
    if (typeof callback !== "function") return;
    callback(payload);
  }
}

function calculateRms(buffer, frameCount) {
  const samples = new Float32Array(buffer);
  const length = Math.min(samples.length, frameCount);
  if (!length) return 0;
  let sumSquares = 0;
  for (let index = 0; index < length; index += 1) {
    const sample = Number(samples[index] || 0);
    sumSquares += sample * sample;
  }
  return Math.sqrt(sumSquares / length);
}

function positiveNumber(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : fallback;
}

function clamp(value, minimum, maximum) {
  if (!Number.isFinite(value)) return minimum;
  return Math.max(minimum, Math.min(maximum, value));
}

function roundMetric(value, digits) {
  const scale = 10 ** digits;
  return Math.round(Number(value || 0) * scale) / scale;
}
