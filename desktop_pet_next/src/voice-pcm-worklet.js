class AkaneVoicePcmCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const requestedFrameSamples = Number(options?.processorOptions?.frameSamples || 0);
    this.frameSamples = Math.max(128, Math.round(requestedFrameSamples || sampleRate * 0.02));
    this.pending = new Float32Array(this.frameSamples);
    this.pendingLength = 0;
    this.stopped = false;

    this.port.onmessage = (event) => {
      const type = String(event?.data?.type || "");
      if (type === "flush") {
        this.flush();
        this.port.postMessage({ type: "flushed" });
      } else if (type === "stop") {
        this.flush();
        this.stopped = true;
        this.port.postMessage({ type: "stopped" });
      }
    };
  }

  process(inputs) {
    if (this.stopped) return false;
    const input = inputs?.[0]?.[0];
    if (!input?.length) return true;

    let offset = 0;
    while (offset < input.length) {
      const available = this.frameSamples - this.pendingLength;
      const length = Math.min(available, input.length - offset);
      this.pending.set(input.subarray(offset, offset + length), this.pendingLength);
      this.pendingLength += length;
      offset += length;
      if (this.pendingLength === this.frameSamples) this.emitPending();
    }
    return true;
  }

  flush() {
    if (this.pendingLength > 0) this.emitPending();
  }

  emitPending() {
    const frame = this.pending.slice(0, this.pendingLength);
    this.pendingLength = 0;
    this.port.postMessage(
      {
        type: "pcm",
        frames: frame.length,
        buffer: frame.buffer
      },
      [frame.buffer]
    );
  }
}

registerProcessor("akane-voice-pcm-capture", AkaneVoicePcmCaptureProcessor);
