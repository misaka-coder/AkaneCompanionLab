/**
 * HTTP client for the Akane backend.
 * Handles session management and NDJSON streaming over /think.
 */

const THINK_TIMEOUT_MS = 5 * 60 * 1000;
const ASR_TIMEOUT_MS = 2 * 60 * 1000;
const AUDIO_UPLOAD_TIMEOUT_MS = 5 * 60 * 1000;
const MUSIC_TIMELINE_TIMEOUT_MS = 20 * 1000;
const WORKSPACE_TIMEOUT_MS = 30 * 1000;
const QUICK_CHECK_TIMEOUT_MS = 5000;

class BackendClient {
  /**
   * @param {string} baseUrl - e.g. "http://127.0.0.1:9999"
   */
  constructor(baseUrl) {
    this.setBaseUrl(baseUrl);
  }

  setBaseUrl(baseUrl) {
    this.baseUrl = String(baseUrl || "").trim().replace(/\/+$/, "");
  }

  resolveUrl(url) {
    const raw = String(url || "").trim();
    if (!raw) return "";
    if (/^(https?:|file:|data:|blob:)/i.test(raw)) return raw;
    if (!this.baseUrl) return raw;
    if (raw.startsWith("/")) return `${this.baseUrl}${raw}`;
    return `${this.baseUrl}/${raw.replace(/^\/+/, "")}`;
  }

  async fetchHealth() {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), QUICK_CHECK_TIMEOUT_MS);
    let response;
    try {
      response = await fetch(`${this.baseUrl}/health?t=${Date.now()}`, {
        cache: "no-store",
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timeoutId);
    }
    if (!response.ok) {
      throw new Error(`Health check failed: HTTP ${response.status}`);
    }
    return response.json();
  }

  async fetchAppConfig() {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), QUICK_CHECK_TIMEOUT_MS);
    let response;
    try {
      response = await fetch(`${this.baseUrl}/app-config?t=${Date.now()}`, {
        cache: "no-store",
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timeoutId);
    }
    if (!response.ok) {
      throw new Error(`App config check failed: HTTP ${response.status}`);
    }
    return response.json();
  }

  buildGeneratedAudioUrl({ profileUserId, sessionId, generatedHandle }) {
    const handle = encodeURIComponent(String(generatedHandle || "").trim());
    if (!handle) return "";
    const query = new URLSearchParams({
      user_id: String(sessionId || ""),
      real_user_id: String(profileUserId || ""),
    });
    return this.resolveUrl(`/desktop-pet/generated/${handle}/content?${query.toString()}`);
  }

  buildAttachmentAudioUrl({ profileUserId, sessionId, attachmentHandle }) {
    const handle = encodeURIComponent(String(attachmentHandle || "").trim());
    if (!handle) return "";
    const query = new URLSearchParams({
      user_id: String(sessionId || ""),
      real_user_id: String(profileUserId || ""),
    });
    return this.resolveUrl(`/desktop-pet/attachments/${handle}/content?${query.toString()}`);
  }

  /**
   * Ensure a session exists. Returns the full session bundle.
   */
  async ensureSession({ profileUserId, sessionId, displayTitle }) {
    const res = await fetch(`${this.baseUrl}/sessions/ensure?t=${Date.now()}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      body: JSON.stringify({
        user_id: sessionId,
        real_user_id: profileUserId,
        display_title: displayTitle || "",
      }),
    });
    if (!res.ok) {
      throw new Error(`Session ensure failed: HTTP ${res.status}`);
    }
    return res.json();
  }

  /**
   * Send a message and consume the NDJSON stream.
   * Returns an async generator yielding parsed event objects.
   *
   * Event types (matching /think NDJSON protocol):
   *   stream_start, turn_start, ui, speech_chunk, final, final_ui,
   *   npc_turn, stream_error, stream_end,
   *   reminder_set, reminder_list, reminder_cancelled,
   *   inventory_snapshot, gift_updated, artifact_updated, persona_state
   */
  async *sendMessage({
    profileUserId,
    sessionId,
    message,
    clientMode,
    capabilities,
    currentVisual,
    desktopContext,
    currentActivity,
  }) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), THINK_TIMEOUT_MS);

    let response;
    try {
      response = await fetch(`${this.baseUrl}/think?t=${Date.now()}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        signal: controller.signal,
        body: JSON.stringify({
          user_id: sessionId,
          real_user_id: profileUserId,
          message,
          client_mode: clientMode,
          client_capabilities: capabilities,
          current_visual: currentVisual || {},
          desktop_context: desktopContext || null,
          desktop_activity: currentActivity || null,
        }),
      });
    } finally {
      clearTimeout(timeoutId);
    }

    if (!response.ok) {
      throw new Error(`Think request failed: HTTP ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        while (true) {
          const nl = buffer.indexOf("\n");
          if (nl < 0) break;
          const line = buffer.slice(0, nl).trim();
          buffer = buffer.slice(nl + 1);
          if (!line) continue;
          try {
            yield JSON.parse(line);
          } catch {
            // Skip malformed lines
          }
        }
      }

      // Drain tail
      const tail = buffer.trim();
      if (tail) {
        try {
          yield JSON.parse(tail);
        } catch {
          // skip
        }
      }
    } finally {
      reader.releaseLock();
    }
  }

  /**
   * Poll due reminders.
   */
  async pollReminders({ profileUserId, sessionId }) {
    const res = await fetch(
      `${this.baseUrl}/reminders/due?user_id=${encodeURIComponent(sessionId)}&real_user_id=${encodeURIComponent(profileUserId)}&t=${Date.now()}`,
      { cache: "no-store" }
    );
    if (!res.ok) return [];
    const payload = await res.json();
    return Array.isArray(payload?.notifications) ? payload.notifications : [];
  }

  /**
   * Fetch the resource manifest.
   */
  async fetchManifest({ profileUserId, sessionId }) {
    const res = await fetch(
      `${this.baseUrl}/resource-manifest?user_id=${encodeURIComponent(sessionId)}&real_user_id=${encodeURIComponent(profileUserId)}&t=${Date.now()}`,
      { cache: "no-store" }
    );
    if (!res.ok) return null;
    return res.json();
  }

  async transcribeAudio({ audioBlob, filename = "akane_voice_input.webm", language = "zh" }) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), ASR_TIMEOUT_MS);
    const form = new FormData();
    form.append("file", audioBlob, filename);
    form.append("language", language);

    let response;
    try {
      response = await fetch(`${this.baseUrl}/asr?t=${Date.now()}`, {
        method: "POST",
        cache: "no-store",
        signal: controller.signal,
        body: form,
      });
    } finally {
      clearTimeout(timeoutId);
    }

    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (!response.ok) {
      const message = payload?.message || payload?.error || `ASR request failed: HTTP ${response.status}`;
      throw new Error(message);
    }
    return payload || { ok: false, error: "invalid_asr_response", message: "语音识别返回格式异常" };
  }

  async uploadAudioAttachment({ profileUserId, sessionId, file }) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), AUDIO_UPLOAD_TIMEOUT_MS);
    const form = new FormData();
    form.append("file", file, file?.name || "akane_audio.mp3");
    form.append("user_id", sessionId);
    form.append("real_user_id", profileUserId);
    form.append("client_mode", "desktop_pet");

    let response;
    try {
      response = await fetch(`${this.baseUrl}/desktop-pet/attachments/audio?t=${Date.now()}`, {
        method: "POST",
        cache: "no-store",
        signal: controller.signal,
        body: form,
      });
    } finally {
      clearTimeout(timeoutId);
    }

    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (!response.ok) {
      const message = payload?.detail || payload?.message || payload?.error || `Audio upload failed: HTTP ${response.status}`;
      throw new Error(message);
    }
    return payload || { ok: false, error: "invalid_audio_upload_response" };
  }

  async prepareMusicTimeline({ profileUserId, sessionId, activity }) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), MUSIC_TIMELINE_TIMEOUT_MS);

    let response;
    try {
      response = await fetch(`${this.baseUrl}/desktop-pet/music-timeline/prepare?t=${Date.now()}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        signal: controller.signal,
        body: JSON.stringify({
          user_id: sessionId,
          real_user_id: profileUserId,
          activity: activity || null,
        }),
      });
    } finally {
      clearTimeout(timeoutId);
    }

    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (!response.ok) {
      const message = payload?.detail || payload?.message || payload?.error || `Timeline prepare failed: HTTP ${response.status}`;
      throw new Error(message);
    }
    return payload || { ok: false, error: "invalid_timeline_prepare_response" };
  }

  async fetchWorkspaceSummary({ profileUserId, sessionId, limit = 24 }) {
    const query = new URLSearchParams({
      user_id: String(sessionId || ""),
      real_user_id: String(profileUserId || ""),
      limit: String(limit || 24),
      t: String(Date.now()),
    });
    const response = await fetch(`${this.baseUrl}/desktop-pet/workspace/summary?${query.toString()}`, {
      cache: "no-store",
    });
    if (!response.ok) {
      throw new Error(`Workspace summary failed: HTTP ${response.status}`);
    }
    return response.json();
  }

  async workspaceAction({ profileUserId, sessionId, action, itemType = "", target = "" }) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), WORKSPACE_TIMEOUT_MS);

    let response;
    try {
      response = await fetch(`${this.baseUrl}/desktop-pet/workspace/action?t=${Date.now()}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        signal: controller.signal,
        body: JSON.stringify({
          user_id: sessionId,
          real_user_id: profileUserId,
          action,
          item_type: itemType,
          target,
        }),
      });
    } finally {
      clearTimeout(timeoutId);
    }

    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (!response.ok) {
      const message = payload?.detail || payload?.message || payload?.error || `Workspace action failed: HTTP ${response.status}`;
      throw new Error(message);
    }
    return payload || { ok: false, error: "invalid_workspace_action_response" };
  }

  async fetchWorkspaceItemLocation({ profileUserId, sessionId, itemType, target }) {
    const normalizedType = String(itemType || "").trim().toLowerCase();
    const handle = encodeURIComponent(String(target || "").trim());
    if (!handle) return { ok: false, error: "missing_target" };
    const endpoint =
      normalizedType === "generated" || normalizedType === "output"
        ? `/desktop-pet/workspace/generated/${handle}/location`
        : `/desktop-pet/workspace/attachments/${handle}/location`;
    const query = new URLSearchParams({
      user_id: String(sessionId || ""),
      real_user_id: String(profileUserId || ""),
      t: String(Date.now()),
    });
    const response = await fetch(`${this.baseUrl}${endpoint}?${query.toString()}`, {
      cache: "no-store",
    });
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (!response.ok) {
      const message = payload?.detail || payload?.message || payload?.error || `Workspace location failed: HTTP ${response.status}`;
      throw new Error(message);
    }
    return payload || { ok: false, error: "invalid_workspace_location_response" };
  }
}

export { BackendClient };
