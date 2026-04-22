function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function getAudioContextConstructor() {
  return window.AudioContext || window.webkitAudioContext || null;
}

function withTimeout(promise, timeoutMs, message) {
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      window.setTimeout(() => reject(new Error(message)), timeoutMs);
    }),
  ]);
}

export function createVoiceLipSyncController({ voicePlayerEl, avatarController }) {
  const state = {
    audioContext: null,
    source: null,
    analyser: null,
    samples: null,
    rafId: 0,
    active: false,
    usingFallback: false,
    smoothMouth: 0,
  };

  async function ensureAudioGraph() {
    const AudioContextCtor = getAudioContextConstructor();
    if (!AudioContextCtor) {
      throw new Error("Web Audio API is not available");
    }

    if (!state.audioContext) {
      state.audioContext = new AudioContextCtor();
    }

    if (state.audioContext.state === "suspended") {
      await withTimeout(state.audioContext.resume(), 900, "AudioContext resume timed out");
    }

    if (!state.source) {
      state.source = state.audioContext.createMediaElementSource(voicePlayerEl);
      state.analyser = state.audioContext.createAnalyser();
      state.analyser.fftSize = 1024;
      state.analyser.smoothingTimeConstant = 0.22;
      state.samples = new Uint8Array(state.analyser.fftSize);
      state.source.connect(state.analyser);
      state.analyser.connect(state.audioContext.destination);
    }
  }

  function readRmsVolume() {
    if (!state.analyser || !state.samples) {
      return 0;
    }

    state.analyser.getByteTimeDomainData(state.samples);
    let sumSquares = 0;
    for (let index = 0; index < state.samples.length; index += 1) {
      const centered = (state.samples[index] - 128) / 128;
      sumSquares += centered * centered;
    }
    return Math.sqrt(sumSquares / state.samples.length);
  }

  function mapVolumeToMouth(rms) {
    const noiseFloor = 0.012;
    const gain = 8.2;
    const raw = clamp((rms - noiseFloor) * gain, 0, 1);
    return Math.pow(raw, 0.72);
  }

  function tick() {
    if (!state.active) {
      return;
    }

    if (voicePlayerEl.paused || voicePlayerEl.ended) {
      stop();
      return;
    }

    const target = mapVolumeToMouth(readRmsVolume());
    state.smoothMouth = state.smoothMouth * 0.64 + target * 0.36;
    avatarController.setMouth(clamp(state.smoothMouth, 0, 0.95));
    state.rafId = window.requestAnimationFrame(tick);
  }

  async function start() {
    stop();

    try {
      const canSync = await avatarController.beginExternalLipSync?.("analyser");
      if (!canSync) {
        return;
      }
      await ensureAudioGraph();
      state.active = true;
      state.usingFallback = false;
      state.smoothMouth = 0;
      tick();
    } catch (error) {
      console.warn("voice lip sync analyser failed, falling back", error);
      state.active = false;
      state.usingFallback = true;
      void avatarController.startTalking();
    }
  }

  function stop() {
    if (state.rafId) {
      window.cancelAnimationFrame(state.rafId);
      state.rafId = 0;
    }
    state.active = false;
    state.smoothMouth = 0;
    if (state.usingFallback) {
      state.usingFallback = false;
      avatarController.stopTalking();
      return;
    }
    avatarController.endExternalLipSync?.();
  }

  return {
    start,
    stop,
  };
}
