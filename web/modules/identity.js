export function createIdentityHelpers({
  state,
  DEFAULT_SPEAKER,
  USER_SPEAKER,
  IDENTITY_STORAGE_KEY,
  IDENTITY_BACKUP_PREFIX,
  OWNER_PROFILE_USER_ID = "master",
  WEB_SESSION_ID_PREFIX = "web",
  BROWSER_PROFILE_ID_PREFIX = "browser",
  LEGACY_VISUAL_STATE_STORAGE_KEY,
  VISUAL_STATE_STORAGE_KEY_PREFIX,
  VOICE_ENABLED_STORAGE_KEY,
  formatModeLabel,
}) {
  const webSessionIdPrefix = String(WEB_SESSION_ID_PREFIX || "web").trim() || "web";
  const browserProfileIdPrefix = String(BROWSER_PROFILE_ID_PREFIX || "browser").trim() || "browser";
  const INVITE_CODE_STORAGE_KEY = `${IDENTITY_STORAGE_KEY}.invite_code`;

  function normalizeIdentityMode(mode) {
    const normalized = String(mode || "owner").trim().toLowerCase();
    return ["owner", "browser", "invite"].includes(normalized) ? normalized : "owner";
  }

  function getIdentityMode() {
    return normalizeIdentityMode(state.webIdentity?.mode || "owner");
  }

  function getOwnerProfileUserId() {
    return String(state.webIdentity?.ownerProfileUserId || OWNER_PROFILE_USER_ID || "master").trim() || "master";
  }

  function getLocalStorage() {
    try {
      return window.localStorage;
    } catch {
      return null;
    }
  }

  function createUuid() {
    try {
      if (window.crypto?.randomUUID) {
        return window.crypto.randomUUID();
      }
    } catch {
    }

    const randomPart = Math.random().toString(16).slice(2);
    return `anon-${Date.now().toString(16)}-${randomPart}`;
  }

  function createWebSessionId() {
    return `${webSessionIdPrefix}_${createUuid()}`;
  }

  function createBrowserProfileId() {
    return `${browserProfileIdPrefix}_${createUuid()}`;
  }

  function normalizeOptionalString(value) {
    return String(value || "").trim();
  }

  function sanitizeInviteCode(value) {
    const raw = String(value || "").trim().toLowerCase();
    const safe = raw.replace(/[^a-z0-9_-]/g, "_").replace(/_+/g, "_").replace(/^_+|_+$/g, "");
    return safe.slice(0, 48);
  }

  function loadInviteCode() {
    const storage = getLocalStorage();
    let queryCode = "";
    try {
      queryCode = new URLSearchParams(window.location.search).get("invite") || "";
    } catch {
      queryCode = "";
    }

    const normalizedQuery = sanitizeInviteCode(queryCode);
    if (normalizedQuery) {
      try {
        storage?.setItem(INVITE_CODE_STORAGE_KEY, normalizedQuery);
      } catch {
      }
      return normalizedQuery;
    }

    try {
      return sanitizeInviteCode(storage?.getItem(INVITE_CODE_STORAGE_KEY) || "");
    } catch {
      return "";
    }
  }

  function resolveInviteProfileUserId() {
    const code = loadInviteCode();
    return code ? `invite_${code}` : "";
  }

  function normalizeOwnerIdentity(value) {
    if (!value || typeof value !== "object") return null;

    const mode = getIdentityMode();
    const ownerProfileUserId = getOwnerProfileUserId();
    const profileUserId = normalizeOptionalString(value.profileUserId);
    const sessionId = normalizeOptionalString(value.sessionId);
    if (!profileUserId || !sessionId) {
      return null;
    }

    const rawPreviousMode = normalizeOptionalString(value.identityMode).toLowerCase();
    const previousMode = rawPreviousMode ? normalizeIdentityMode(rawPreviousMode) : "";
    const legacyProfileUserId = normalizeOptionalString(value.legacyProfileUserId);
    const legacySessionId = normalizeOptionalString(value.legacySessionId);

    if (mode === "owner") {
      if (profileUserId === ownerProfileUserId) {
        return {
          identityMode: mode,
          profileUserId,
          sessionId,
          ...(legacyProfileUserId ? { legacyProfileUserId } : {}),
          ...(legacySessionId ? { legacySessionId } : {}),
        };
      }

      // Old Web builds used a random profile id. Rotating the session avoids
      // colliding with the globally unique chat_sessions.session_id row.
      return {
        identityMode: mode,
        profileUserId: ownerProfileUserId,
        sessionId: createWebSessionId(),
        legacyProfileUserId: legacyProfileUserId || profileUserId,
        legacySessionId: legacySessionId || sessionId,
      };
    }

    if (mode === "invite") {
      const inviteProfileUserId = resolveInviteProfileUserId();
      if (!inviteProfileUserId) {
        return {
          identityMode: "browser",
          profileUserId: previousMode === "browser" && profileUserId !== ownerProfileUserId
            ? profileUserId
            : createBrowserProfileId(),
          sessionId: previousMode === "browser" && profileUserId !== ownerProfileUserId ? sessionId : createWebSessionId(),
          ...(legacyProfileUserId ? { legacyProfileUserId } : profileUserId ? { legacyProfileUserId: profileUserId } : {}),
          ...(legacySessionId ? { legacySessionId } : sessionId ? { legacySessionId: sessionId } : {}),
        };
      }
      if (previousMode === "invite" && profileUserId === inviteProfileUserId) {
        return {
          identityMode: mode,
          profileUserId,
          sessionId,
          ...(legacyProfileUserId ? { legacyProfileUserId } : {}),
          ...(legacySessionId ? { legacySessionId } : {}),
        };
      }
      return {
        identityMode: mode,
        profileUserId: inviteProfileUserId,
        sessionId: createWebSessionId(),
        ...(profileUserId ? { legacyProfileUserId: legacyProfileUserId || profileUserId } : {}),
        ...(sessionId ? { legacySessionId: legacySessionId || sessionId } : {}),
      };
    }

    if (
      previousMode === "browser" ||
      (!previousMode && profileUserId !== ownerProfileUserId)
    ) {
      return {
        identityMode: mode,
        profileUserId,
        sessionId,
        ...(legacyProfileUserId ? { legacyProfileUserId } : {}),
        ...(legacySessionId ? { legacySessionId } : {}),
      };
    }

    return {
      identityMode: mode,
      profileUserId: createBrowserProfileId(),
      sessionId: createWebSessionId(),
      legacyProfileUserId: legacyProfileUserId || profileUserId,
      legacySessionId: legacySessionId || sessionId,
    };
  }

  function normalizePersistedIdentity(value) {
    if (!value || typeof value !== "object") return null;
    const profileUserId = String(
      value.profile_user_id || value.profileUserId || value.real_user_id || ""
    ).trim();
    const sessionId = String(value.session_id || value.sessionId || value.user_id || "").trim();
    if (!profileUserId || !sessionId) {
      return null;
    }
    return normalizeOwnerIdentity({
      profileUserId,
      sessionId,
      identityMode: value.identity_mode || value.identityMode || "",
      legacyProfileUserId: value.legacy_profile_user_id || value.legacyProfileUserId || "",
      legacySessionId: value.legacy_session_id || value.legacySessionId || "",
    });
  }

  function loadPersistedIdentity() {
    const storage = getLocalStorage();
    if (!storage) return null;

    try {
      const raw = storage.getItem(IDENTITY_STORAGE_KEY);
      if (!raw) return null;
      return normalizePersistedIdentity(JSON.parse(raw));
    } catch {
      return null;
    }
  }

  function persistIdentity() {
    const storage = getLocalStorage();
    if (!storage) return;

    try {
      const payload = {
        identityMode: state.identity.identityMode || getIdentityMode(),
        profileUserId: state.identity.profileUserId,
        sessionId: state.identity.sessionId,
      };
      if (state.identity.legacyProfileUserId) {
        payload.legacyProfileUserId = state.identity.legacyProfileUserId;
      }
      if (state.identity.legacySessionId) {
        payload.legacySessionId = state.identity.legacySessionId;
      }
      storage.setItem(
        IDENTITY_STORAGE_KEY,
        JSON.stringify(payload)
      );
    } catch {
    }
  }

  function ensureIdentity() {
    const persisted = loadPersistedIdentity();
    if (persisted) {
      state.identity = persisted;
      persistIdentity();
      return state.identity;
    }

    const mode = getIdentityMode();
    const inviteProfileUserId = mode === "invite" ? resolveInviteProfileUserId() : "";
    const effectiveMode = mode === "invite" && !inviteProfileUserId ? "browser" : mode;
    state.identity = {
      identityMode: effectiveMode,
      profileUserId: effectiveMode === "owner"
        ? getOwnerProfileUserId()
        : effectiveMode === "invite"
          ? inviteProfileUserId
          : createBrowserProfileId(),
      sessionId: createWebSessionId(),
    };
    persistIdentity();
    return state.identity;
  }

  function getCurrentProfileUserId() {
    return String(state.identity?.profileUserId || ensureIdentity().profileUserId || "").trim();
  }

  function getCurrentSessionId() {
    return String(state.identity?.sessionId || ensureIdentity().sessionId || "").trim();
  }

  function encodeIdentityBackup(payload) {
    const json = JSON.stringify(payload);
    const bytes = new TextEncoder().encode(json);
    let binary = "";
    bytes.forEach((byte) => {
      binary += String.fromCharCode(byte);
    });
    return `${IDENTITY_BACKUP_PREFIX}${btoa(binary)}`;
  }

  function decodeIdentityBackup(token) {
    const normalizedToken = String(token || "").trim();
    if (!normalizedToken) {
      return null;
    }

    const rawPayload = normalizedToken.startsWith(IDENTITY_BACKUP_PREFIX)
      ? normalizedToken.slice(IDENTITY_BACKUP_PREFIX.length)
      : normalizedToken;

    try {
      const binary = atob(rawPayload);
      const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
      const json = new TextDecoder().decode(bytes);
      return normalizePersistedIdentity(JSON.parse(json));
    } catch {
      try {
        return normalizePersistedIdentity(JSON.parse(normalizedToken));
      } catch {
        return null;
      }
    }
  }

  function normalizeSessionRecord(value) {
    if (!value || typeof value !== "object") return null;
    const sessionId = String(value.session_id || value.sessionId || "").trim();
    if (!sessionId) return null;
    return {
      sessionId,
      displayTitle: String(value.display_title || value.displayTitle || "新的对话").trim() || "新的对话",
      createdAt: Number(value.created_at || value.createdAt || 0) || 0,
      updatedAt: Number(value.updated_at || value.updatedAt || 0) || 0,
    };
  }

  function getCurrentSessionRecord() {
    const currentSessionId = getCurrentSessionId();
    return state.sessions.find((item) => item.sessionId === currentSessionId) || null;
  }

  function mapRoleToSpeaker(role) {
    const normalized = String(role || "").trim();
    if (!normalized) return DEFAULT_SPEAKER;
    if (normalized === "user") return USER_SPEAKER;
    if (normalized.startsWith("npc:")) {
      return normalized.slice(4).trim() || "NPC";
    }
    if (normalized === "assistant") return "Akane";
    return DEFAULT_SPEAKER;
  }

  function buildHistoryEntriesFromMessages(messages) {
    if (!Array.isArray(messages)) return [];

    return messages
      .map((message) => {
        const role = String(message?.role || "").trim();
        const content = String(message?.content || "").trim();
        if (!content) return null;
        if (role !== "user" && role !== "assistant" && !role.startsWith("npc:")) {
          return null;
        }
        return {
          speaker: mapRoleToSpeaker(role),
          content,
          codeSnippet: "",
          kind: role === "user" ? "user" : "neutral",
        };
      })
      .filter(Boolean);
  }

  function createVisualStateStorageKey(profileUserId = getCurrentProfileUserId()) {
    const normalized = String(profileUserId || "").trim();
    return normalized
      ? `${VISUAL_STATE_STORAGE_KEY_PREFIX}${normalized}`
      : LEGACY_VISUAL_STATE_STORAGE_KEY;
  }

  function rotateSessionIdentity() {
    ensureIdentity();
    state.identity = {
      ...state.identity,
      sessionId: createWebSessionId(),
    };
    persistIdentity();
    return state.identity;
  }

  function loadVoiceEnabledPreference() {
    const storage = getLocalStorage();
    if (!storage) return true;

    try {
      const raw = storage.getItem(VOICE_ENABLED_STORAGE_KEY);
      if (raw == null) return true;
      return raw !== "false";
    } catch {
      return true;
    }
  }

  function persistVoiceEnabledPreference() {
    const storage = getLocalStorage();
    if (!storage) return;

    try {
      storage.setItem(VOICE_ENABLED_STORAGE_KEY, String(state.voiceEnabled));
    } catch {
    }
  }

  function normalizePersistedVisual(value) {
    if (!value || typeof value !== "object") return null;
    const scene = value.scene && typeof value.scene === "object" ? value.scene : {};
    const character = value.character && typeof value.character === "object" ? value.character : {};

    return {
      scene: {
        major: String(scene.major || ""),
        minor: String(scene.minor || ""),
        background: String(scene.background || ""),
        bgm: String(scene.bgm || ""),
      },
      character: {
        outfit: String(character.outfit || ""),
      },
      emotion: String(value.emotion || ""),
    };
  }

  function loadPersistedShellState() {
    const storage = getLocalStorage();
    if (!storage) return null;

    try {
      const profileKey = createVisualStateStorageKey();
      let raw = storage.getItem(profileKey);
      if (!raw) {
        raw = storage.getItem(LEGACY_VISUAL_STATE_STORAGE_KEY);
        if (raw) {
          storage.setItem(profileKey, raw);
          storage.removeItem(LEGACY_VISUAL_STATE_STORAGE_KEY);
        }
      }
      if (!raw) return null;

      const parsed = JSON.parse(raw);
      const currentVisual = normalizePersistedVisual(parsed?.currentVisual);
      if (!currentVisual) return null;

      return {
        currentVisual,
        mode: formatModeLabel(parsed?.mode || "gal"),
      };
    } catch {
      return null;
    }
  }

  function persistShellState() {
    const storage = getLocalStorage();
    if (!storage || !state.currentVisual) return;

    try {
      storage.setItem(
        createVisualStateStorageKey(),
        JSON.stringify({
          currentVisual: state.currentVisual,
          mode: state.currentMode || "gal",
        })
      );
    } catch {
    }
  }

  function clearPersistedShellState() {
    const storage = getLocalStorage();
    if (!storage) return;

    try {
      storage.removeItem(createVisualStateStorageKey());
    } catch {
    }
  }

  return {
    getLocalStorage,
    createUuid,
    normalizePersistedIdentity,
    loadPersistedIdentity,
    persistIdentity,
    ensureIdentity,
    getCurrentProfileUserId,
    getCurrentSessionId,
    encodeIdentityBackup,
    decodeIdentityBackup,
    normalizeSessionRecord,
    getCurrentSessionRecord,
    mapRoleToSpeaker,
    buildHistoryEntriesFromMessages,
    createVisualStateStorageKey,
    rotateSessionIdentity,
    loadVoiceEnabledPreference,
    persistVoiceEnabledPreference,
    normalizePersistedVisual,
    loadPersistedShellState,
    persistShellState,
    clearPersistedShellState,
  };
}
