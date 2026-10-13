/** Transport and browser/native capabilities supplied by the entry point. */
import { createDesktopConversationFetch } from "../desktop-conversation-transport.js";

export function createPlatform({ window, document, ...native }) {
  const isTauriRuntime = Boolean(window.__TAURI_INTERNALS__);
  const backendFetch = isTauriRuntime
    ? createDesktopConversationFetch({ fetch: native.tauriFetch, invoke: native.invoke })
    : (input, init) => window.fetch(input, init);
  return {
    window, document,
    navigator: window.navigator,
    MediaRecorder: window.MediaRecorder,
    ...native,
    isTauriRuntime,
    appWindow: isTauriRuntime ? native.getCurrentWindow() : null,
    VOICE_PCM_WORKLET_URL: native.voicePcmWorkletUrl,
    backendFetch,
    voiceSocketProtocols: backendFetch.websocketProtocols || (async () => []),
    async tauriCall(command, args, { quiet = false } = {}) {
      if (!isTauriRuntime) return null;
      try {
        return await native.invoke(command, args);
      } catch (error) {
        if (!quiet) this.onError?.(command, error);
        return null;
      }
    }
  };
}
