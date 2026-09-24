const MIME_TYPES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/ogg;codecs=opus",
  "audio/ogg",
  "audio/mp4",
];

const MIN_RECORDING_MS = 700;

class VoiceRecorderError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "VoiceRecorderError";
    this.code = code;
  }
}

class VoiceRecorder {
  constructor({ onStateChange } = {}) {
    this._onStateChange = onStateChange;
    this._recorder = null;
    this._stream = null;
    this._chunks = [];
    this._startedAt = 0;
    this._state = "idle";
    this._enabled = true;
    this._mimeType = this._selectMimeType();
  }

  setEnabled(enabled) {
    this._enabled = Boolean(enabled);
    if (!this._enabled && this.isRecording()) {
      void this.cancel();
    }
    this._setState(this._enabled ? "idle" : "disabled");
  }

  isEnabled() {
    return this._enabled;
  }

  isRecording() {
    return this._state === "recording";
  }

  getState() {
    return this._state;
  }

  getFilename() {
    if (this._mimeType.includes("ogg")) return "akane_voice_input.ogg";
    if (this._mimeType.includes("mp4")) return "akane_voice_input.m4a";
    return "akane_voice_input.webm";
  }

  async start() {
    if (!this._enabled) {
      throw new VoiceRecorderError("disabled", "语音输入已关闭");
    }
    if (this.isRecording()) return;
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      throw new VoiceRecorderError("unsupported", "当前环境不支持录音");
    }

    try {
      this._stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
    } catch (error) {
      throw new VoiceRecorderError("permission_denied", this._describePermissionError(error));
    }

    this._chunks = [];
    const options = this._mimeType ? { mimeType: this._mimeType } : undefined;
    try {
      this._recorder = new MediaRecorder(this._stream, options);
    } catch {
      this._recorder = new MediaRecorder(this._stream);
      this._mimeType = this._recorder.mimeType || this._mimeType;
    }

    this._recorder.addEventListener("dataavailable", (event) => {
      if (event.data?.size > 0) {
        this._chunks.push(event.data);
      }
    });
    this._startedAt = Date.now();
    this._recorder.start();
    this._setState("recording");
  }

  stop() {
    if (!this.isRecording() || !this._recorder) return Promise.resolve(null);

    const recorder = this._recorder;
    return new Promise((resolve, reject) => {
      recorder.addEventListener("stop", () => {
        const durationMs = Date.now() - this._startedAt;
        const mimeType = recorder.mimeType || this._mimeType || "audio/webm";
        const blob = new Blob(this._chunks, { type: mimeType });
        this._cleanup();
        this._setState("idle");

        if (durationMs < MIN_RECORDING_MS || blob.size < 512) {
          reject(new VoiceRecorderError("too_short", "录音太短啦，我没听清。"));
          return;
        }
        resolve(blob);
      }, { once: true });

      recorder.addEventListener("error", () => {
        this._cleanup();
        this._setState("idle");
        reject(new VoiceRecorderError("record_failed", "录音失败了"));
      }, { once: true });

      try {
        recorder.stop();
      } catch (error) {
        this._cleanup();
        this._setState("idle");
        reject(new VoiceRecorderError("record_failed", String(error?.message || "录音失败了")));
      }
    });
  }

  async cancel() {
    if (this._recorder && this._recorder.state !== "inactive") {
      try {
        this._recorder.stop();
      } catch {
        // Ignore cancellation errors.
      }
    }
    this._cleanup();
    this._setState(this._enabled ? "idle" : "disabled");
  }

  setProcessing(processing) {
    if (this.isRecording()) return;
    this._setState(processing ? "processing" : (this._enabled ? "idle" : "disabled"));
  }

  _cleanup() {
    for (const track of this._stream?.getTracks?.() || []) {
      track.stop();
    }
    this._recorder = null;
    this._stream = null;
    this._chunks = [];
    this._startedAt = 0;
  }

  _setState(state) {
    if (this._state === state) return;
    this._state = state;
    this._onStateChange?.(state);
  }

  _selectMimeType() {
    if (typeof MediaRecorder === "undefined" || !MediaRecorder.isTypeSupported) return "";
    return MIME_TYPES.find((type) => MediaRecorder.isTypeSupported(type)) || "";
  }

  _describePermissionError(error) {
    const name = String(error?.name || "");
    if (name === "NotAllowedError" || name === "SecurityError") {
      return "没有麦克风权限";
    }
    if (name === "NotFoundError" || name === "DevicesNotFoundError") {
      return "没有找到可用麦克风";
    }
    return String(error?.message || "无法打开麦克风");
  }
}

export { VoiceRecorder, VoiceRecorderError };
