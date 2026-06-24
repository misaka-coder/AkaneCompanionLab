/**
 * Session identity manager.
 *
 * Principle: "Same Akane, different bodies."
 *   profileUserId is shared (default "master") for long-term memory continuity.
 *   sessionId is unique to this desktop pet instance (stable across restarts).
 */

const STORAGE_KEY = "akane_pet_identity_v1";
const BACKEND_URL_KEY = "akane_pet_backend_url_v1";
const OUTFIT_KEY = "akane_pet_outfit_v1";

const SHARED_PROFILE_USER_ID = "master";
const DEFAULT_BACKEND_URL = "http://127.0.0.1:9999";
const DEFAULT_OUTFIT = "猫娘";
const OBSOLETE_OUTFITS = new Set(["水手服", "睡衣"]);

class SessionManager {
  constructor() {
    this._identity = null;
    this._backendUrl = DEFAULT_BACKEND_URL;
    this._outfit = DEFAULT_OUTFIT;
  }

  /** Load or create identity from localStorage. */
  init(settings = {}) {
    this._backendUrl = this._normalizeBackendUrl(settings.backendUrl || this._loadBackendUrl());
    this._outfit = this._normalizeOutfit(settings.outfit || this._loadOutfit());

    const stored = this._loadIdentity();
    if (stored && stored.profileUserId === SHARED_PROFILE_USER_ID && stored.sessionId) {
      this._identity = stored;
    } else {
      // Create fresh desktop pet session; profileUserId is always "master"
      this._identity = {
        profileUserId: SHARED_PROFILE_USER_ID,
        sessionId: `desktop_pet_${this._generateId()}`,
      };
      this._saveIdentity();
    }
    return this._identity;
  }

  /** @returns {{ profileUserId: string, sessionId: string }} */
  getIdentity() {
    return this._identity;
  }

  /** @returns {string} */
  getBackendUrl() {
    return this._backendUrl;
  }

  /** @param {string} url */
  setBackendUrl(url) {
    this._backendUrl = this._normalizeBackendUrl(url);
    try {
      localStorage.setItem(BACKEND_URL_KEY, this._backendUrl);
    } catch { /* localStorage unavailable */ }
  }

  /** @returns {string} */
  getOutfit() {
    return this._outfit;
  }

  /** @param {string} outfit */
  setOutfit(outfit) {
    this._outfit = this._normalizeOutfit(outfit);
    try {
      localStorage.setItem(OUTFIT_KEY, this._outfit);
    } catch { /* */ }
  }

  _loadIdentity() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  }

  _saveIdentity() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(this._identity));
    } catch { /* */ }
  }

  _loadBackendUrl() {
    try {
      const raw = localStorage.getItem(BACKEND_URL_KEY);
      return raw || DEFAULT_BACKEND_URL;
    } catch {
      return DEFAULT_BACKEND_URL;
    }
  }

  _loadOutfit() {
    try {
      const raw = localStorage.getItem(OUTFIT_KEY);
      return raw || DEFAULT_OUTFIT;
    } catch {
      return DEFAULT_OUTFIT;
    }
  }

  _generateId() {
    if (typeof crypto !== "undefined" && crypto.randomUUID) {
      return crypto.randomUUID();
    }
    return Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
  }

  _normalizeBackendUrl(url) {
    return String(url || "").trim().replace(/\/+$/, "") || DEFAULT_BACKEND_URL;
  }

  _normalizeOutfit(outfit) {
    const normalized = String(outfit || "").trim();
    if (!normalized || OBSOLETE_OUTFITS.has(normalized)) return DEFAULT_OUTFIT;
    return normalized;
  }
}

export { SessionManager };
