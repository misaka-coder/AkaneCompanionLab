export function createAudioController({
  state,
  bgmPlayerEl,
  bgmPillEl,
  voicePlayerEl,
  debugOutputEl,
  wait,
  BASE_BGM_VOLUME,
  DUCKED_BGM_VOLUME,
  MIN_TTS_SEGMENT_CHARS,
}) {
  async function fadeAudioTo(targetVolume) {
    const currentVolume = Number.isFinite(bgmPlayerEl.volume) ? bgmPlayerEl.volume : 0;
    if (Math.abs(currentVolume - targetVolume) < 0.01) {
      bgmPlayerEl.volume = targetVolume;
      return;
    }

    const steps = 8;
    for (let step = 1; step <= steps; step += 1) {
      bgmPlayerEl.volume = currentVolume + ((targetVolume - currentVolume) * step) / steps;
      await wait(42);
    }
  }

  async function playBgmWithFade() {
    if (!state.audioUnlocked || !state.currentTrackPath) return;

    try {
      await bgmPlayerEl.play();
      await fadeAudioTo(BASE_BGM_VOLUME);
    } catch (error) {
      bgmPillEl.textContent = `${bgmPillEl.textContent.replace(" · 等待交互", "")} · 等待交互`;
    }
  }

  async function updateBgm(bgmEntry, label) {
    bgmPillEl.textContent = label;
    const nextPath = bgmEntry?.path || "";
    if (!nextPath) {
      if (state.currentTrackPath) {
        await fadeAudioTo(0);
        bgmPlayerEl.pause();
      }
      bgmPlayerEl.removeAttribute("src");
      state.currentTrackPath = "";
      return;
    }

    if (state.currentTrackPath === nextPath) {
      return;
    }

    if (state.currentTrackPath) {
      await fadeAudioTo(0);
      bgmPlayerEl.pause();
    }

    state.currentTrackPath = nextPath;
    bgmPlayerEl.src = nextPath;
    bgmPlayerEl.currentTime = 0;
    bgmPlayerEl.volume = 0;
    await playBgmWithFade();
  }

  function revokeVoiceUrl() {
    if (!state.currentVoiceUrl) return;
    URL.revokeObjectURL(state.currentVoiceUrl);
    state.currentVoiceUrl = "";
  }

  function revokeQueuedVoiceUrls() {
    for (const item of state.voiceQueueItems.values()) {
      if (item?.url) {
        URL.revokeObjectURL(item.url);
      }
    }
    state.voiceQueueItems.clear();
  }

  function forgetVoiceController(controller) {
    state.voiceRequestControllers = state.voiceRequestControllers.filter((item) => item !== controller);
  }

  async function duckBgmForVoice() {
    if (!state.currentTrackPath || bgmPlayerEl.paused) return;
    await fadeAudioTo(DUCKED_BGM_VOLUME);
  }

  async function restoreBgmAfterVoice() {
    if (!state.currentTrackPath || bgmPlayerEl.paused) return;
    await fadeAudioTo(BASE_BGM_VOLUME);
  }

  function cleanupVoicePlayer() {
    voicePlayerEl.pause();
    voicePlayerEl.removeAttribute("src");
    voicePlayerEl.load();
    revokeVoiceUrl();
  }

  async function stopVoicePlayback(options = {}) {
    const shouldAbort = options.abortFetch !== false;
    state.voicePlaybackToken += 1;
    state.voiceQueueToken += 1;
    state.voicePlaybackActive = false;
    state.voiceStreamComplete = false;
    state.voiceNextSequence = 0;
    state.voiceNextPlaySequence = 0;
    state.ttsTextBuffer = "";
    state.ttsRequestQueue = [];
    state.ttsRequestActive = false;

    if (shouldAbort) {
      if (state.voiceAbortController) {
        state.voiceAbortController.abort();
        state.voiceAbortController = null;
      }
      for (const controller of state.voiceRequestControllers) {
        controller.abort();
      }
      state.voiceRequestControllers = [];
    }

    revokeQueuedVoiceUrls();
    cleanupVoicePlayer();
    await restoreBgmAfterVoice();
  }

  function countSpeakableChars(text) {
    return String(text || "").replace(/\s+/g, "").length;
  }

  function findTtsSplitIndex(text) {
    const normalized = String(text || "");
    if (!normalized) return -1;

    const punctuation = new Set(["。", "！", "？", "!", "?", "；", ";"]);
    for (let index = 0; index < normalized.length; index += 1) {
      const char = normalized[index];
      let endIndex = -1;
      if (punctuation.has(char)) {
        endIndex = index + 1;
      } else if (char === "…") {
        let cursor = index + 1;
        while (normalized[cursor] === "…") {
          cursor += 1;
        }
        endIndex = cursor;
      }

      if (endIndex > 0 && countSpeakableChars(normalized.slice(0, endIndex)) >= MIN_TTS_SEGMENT_CHARS) {
        return endIndex;
      }
    }
    return -1;
  }

  function enqueueSpeechSegment(text) {
    const normalized = String(text || "").trim();
    if (!normalized || !state.voiceEnabled || !state.audioUnlocked) {
      return;
    }

    const sequence = state.voiceNextSequence;
    state.voiceNextSequence += 1;
    const token = state.voiceQueueToken;
    const controller = new AbortController();
    state.voiceRequestControllers.push(controller);
    state.voiceQueueItems.set(sequence, {
      sequence,
      status: "loading",
      url: "",
      text: normalized,
    });
    state.ttsRequestQueue.push({
      sequence,
      text: normalized,
      token,
      controller,
    });
    void processTtsRequestQueue();
  }

  async function processTtsRequestQueue() {
    if (state.ttsRequestActive) {
      return;
    }
    state.ttsRequestActive = true;

    try {
      while (state.ttsRequestQueue.length) {
        const task = state.ttsRequestQueue.shift();
        if (!task) {
          continue;
        }

        const { sequence, text, token, controller } = task;
        try {
          const response = await fetch(`/tts?t=${Date.now()}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            cache: "no-store",
            signal: controller.signal,
            body: JSON.stringify({ text }),
          });
          if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
          }

          const audioBlob = await response.blob();
          if (controller.signal.aborted || token !== state.voiceQueueToken) {
            continue;
          }

          const item = state.voiceQueueItems.get(sequence);
          if (!item) {
            continue;
          }

          item.status = "ready";
          item.url = URL.createObjectURL(audioBlob);
          item.text = text;
          void pumpVoiceQueue();
        } catch (error) {
          if (controller.signal.aborted || token !== state.voiceQueueToken) {
            continue;
          }

          const item = state.voiceQueueItems.get(sequence);
          if (item) {
            item.status = "error";
          }
          debugOutputEl.textContent = `语音播放失败：${error}`;
          void pumpVoiceQueue();
        } finally {
          forgetVoiceController(controller);
          if (state.voiceAbortController === controller) {
            state.voiceAbortController = null;
          }
        }
      }
    } finally {
      state.ttsRequestActive = false;
    }
  }

  function flushBufferedSpeechSegments(force = false) {
    if (!state.voiceEnabled || !state.audioUnlocked) {
      state.ttsTextBuffer = "";
      return;
    }

    let remaining = String(state.ttsTextBuffer || "");
    while (remaining) {
      const splitIndex = findTtsSplitIndex(remaining);
      if (splitIndex < 0) {
        break;
      }

      const segment = remaining.slice(0, splitIndex).trim();
      if (segment) {
        enqueueSpeechSegment(segment);
      }
      remaining = remaining.slice(splitIndex).replace(/^\s+/, "");
    }

    if (force) {
      const tail = remaining.trim();
      if (tail) {
        enqueueSpeechSegment(tail);
      }
      remaining = "";
    }

    state.ttsTextBuffer = remaining;
  }

  function appendSpeechForTts(text) {
    const normalized = String(text || "");
    if (!normalized) {
      return;
    }
    state.ttsTextBuffer += normalized;
    flushBufferedSpeechSegments(false);
  }

  function enqueueDirectSpeech(text) {
    flushBufferedSpeechSegments(true);
    const normalized = String(text || "").trim();
    if (!normalized) {
      return;
    }
    enqueueSpeechSegment(normalized);
  }

  async function pumpVoiceQueue() {
    if (!state.voiceEnabled || !state.audioUnlocked || state.voicePlaybackActive) {
      return;
    }

    while (!state.voicePlaybackActive) {
      const item = state.voiceQueueItems.get(state.voiceNextPlaySequence);
      if (!item) {
        if (state.voiceStreamComplete) {
          await restoreBgmAfterVoice();
        }
        return;
      }

      if (item.status === "loading") {
        return;
      }

      state.voiceQueueItems.delete(state.voiceNextPlaySequence);
      state.voiceNextPlaySequence += 1;

      if (item.status !== "ready" || !item.url) {
        continue;
      }

      const playbackToken = state.voiceQueueToken;
      state.voicePlaybackActive = true;
      state.currentVoiceUrl = item.url;
      voicePlayerEl.src = item.url;
      voicePlayerEl.currentTime = 0;

      try {
        await duckBgmForVoice();
        await voicePlayerEl.play();
        await new Promise((resolve) => {
          const handleEnd = () => {
            cleanup();
            resolve();
          };
          const handleError = () => {
            cleanup();
            resolve();
          };
          const cleanup = () => {
            voicePlayerEl.removeEventListener("ended", handleEnd);
            voicePlayerEl.removeEventListener("error", handleError);
          };

          voicePlayerEl.addEventListener("ended", handleEnd, { once: true });
          voicePlayerEl.addEventListener("error", handleError, { once: true });
        });
      } catch (error) {
        debugOutputEl.textContent = `语音播放失败：${error}`;
      } finally {
        cleanupVoicePlayer();
        if (playbackToken !== state.voiceQueueToken) {
          return;
        }
        state.voicePlaybackActive = false;
      }
    }
  }

  async function markVoiceStreamComplete() {
    flushBufferedSpeechSegments(true);
    state.voiceStreamComplete = true;
    await pumpVoiceQueue();
    if (!state.voicePlaybackActive && state.voiceQueueItems.size === 0) {
      await restoreBgmAfterVoice();
    }
  }

  async function speakText(text) {
    const normalized = String(text || "").trim();
    if (!normalized || !state.voiceEnabled || !state.audioUnlocked) {
      return;
    }

    await stopVoicePlayback();
    appendSpeechForTts(normalized);
    await markVoiceStreamComplete();
  }

  async function finalizeStreamAudioIfIdle() {
    if (!state.voicePlaybackActive && state.voiceQueueItems.size === 0) {
      await restoreBgmAfterVoice();
    }
  }

  return {
    fadeAudioTo,
    playBgmWithFade,
    updateBgm,
    revokeVoiceUrl,
    revokeQueuedVoiceUrls,
    forgetVoiceController,
    duckBgmForVoice,
    restoreBgmAfterVoice,
    cleanupVoicePlayer,
    stopVoicePlayback,
    countSpeakableChars,
    findTtsSplitIndex,
    enqueueSpeechSegment,
    processTtsRequestQueue,
    flushBufferedSpeechSegments,
    appendSpeechForTts,
    enqueueDirectSpeech,
    pumpVoiceQueue,
    markVoiceStreamComplete,
    speakText,
    finalizeStreamAudioIfIdle,
  };
}
