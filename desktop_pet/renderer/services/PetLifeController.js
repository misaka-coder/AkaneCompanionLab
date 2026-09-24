const MOTION = {
  IDLE: "idle",
  THINKING: "thinking",
  SPEAKING: "speaking",
  LISTENING: "listening",
  MUSIC: "music",
  CLICK: "click",
};

const EMOTION_CANDIDATES = {
  neutral: ["正常", "normal"],
  thinking: ["思考中", "正常", "无语", "得意", "thinking", "normal"],
  music: ["听歌中", "开心", "正常", "music", "happy", "normal"],
  listening: ["侧耳听", "正常", "listening", "recording", "hear", "normal"],
  click: ["求摸摸", "被摸头", "卖萌", "脸红", "开心", "cute", "shy", "happy"],
  confused: ["困惑", "无语", "气鼓鼓", "confused", "speechless", "pout", "normal"],
  success: ["开心", "得意", "卖萌", "happy", "smug", "cute", "normal"],
  sleepy: ["困困", "打哈欠", "sleepy", "yawn", "normal"],
  cheerful: ["开心", "卖萌", "得意", "happy", "cute", "smug"],
  snack: ["偷吃被抓", "卖萌", "开心", "snack", "hungry", "cute", "normal"],
};

const IDLE_MOOD_MIN_MS = 45 * 1000;
const IDLE_MOOD_SPREAD_MS = 35 * 1000;
const TRANSIENT_MOTION_MS = 620;
const TRANSIENT_EMOTION_MS = 3600;

class PetLifeController {
  constructor({ getAvailableEmotions, getCurrentEmotion, onEmotion } = {}) {
    this._getAvailableEmotions = getAvailableEmotions;
    this._getCurrentEmotion = getCurrentEmotion;
    this._onEmotion = onEmotion;
    this._petState = "idle";
    this._musicActive = false;
    this._listeningActive = false;
    this._transientMotionTimer = 0;
    this._transientEmotionTimer = 0;
    this._idleMoodTimer = 0;
    this._lastIdleMoodAt = 0;
  }

  setPetState(state) {
    this._petState = String(state || "idle").trim().toLowerCase() || "idle";
    if (this._listeningActive && this._petState === "idle") {
      this._setMotion(MOTION.LISTENING);
      return;
    }
    if (this._petState === "thinking") {
      this._setMotion(MOTION.THINKING);
      this._showBestEmotion("thinking", { soft: true });
      this._clearIdleMoodTimer();
      return;
    }
    if (this._petState === "speaking") {
      this._setMotion(MOTION.SPEAKING);
      this._clearIdleMoodTimer();
      return;
    }
    this._syncAmbientMotion();
    if (this._musicActive) {
      this._showBestEmotion("music", { soft: true });
      this._clearIdleMoodTimer();
      return;
    }
    this._scheduleIdleMood();
  }

  setListeningActive(active) {
    this._listeningActive = Boolean(active);
    if (this._listeningActive) {
      if (this._petState !== "idle") return;
      this._setMotion(MOTION.LISTENING);
      this._showBestEmotion("listening", { force: true });
      this._clearIdleMoodTimer();
      return;
    }
    if (this._petState !== "idle") return;
    this._syncAmbientMotion();
    this._scheduleEmotionRestore(900);
    this._scheduleIdleMood();
  }

  setMusicActive(active) {
    this._musicActive = Boolean(active);
    if (this._petState !== "idle") return;
    if (this._listeningActive) return;
    this._syncAmbientMotion();
    if (this._musicActive) {
      this._showBestEmotion("music", { soft: true });
      this._clearIdleMoodTimer();
    } else {
      this._scheduleIdleMood();
    }
  }

  reactToClick() {
    if (this._petState !== "idle") return;
    this._setMotion(MOTION.CLICK);
    this._showBestEmotion("click", { force: true });
    window.clearTimeout(this._transientMotionTimer);
    this._transientMotionTimer = window.setTimeout(() => {
      this._syncAmbientMotion();
    }, TRANSIENT_MOTION_MS);
    this._scheduleEmotionRestore();
  }

  showMoment(group, { force = true, durationMs = TRANSIENT_EMOTION_MS } = {}) {
    if (this._petState !== "idle" || this._listeningActive) return false;
    const shown = this._showBestEmotion(group, { force });
    if (shown) this._scheduleEmotionRestore(durationMs);
    return shown;
  }

  dispose() {
    window.clearTimeout(this._transientMotionTimer);
    window.clearTimeout(this._transientEmotionTimer);
    this._clearIdleMoodTimer();
  }

  _syncAmbientMotion() {
    if (this._listeningActive) {
      this._setMotion(MOTION.LISTENING);
      return;
    }
    this._setMotion(this._musicActive ? MOTION.MUSIC : MOTION.IDLE);
  }

  _setMotion(motion) {
    document.body.dataset.lifeMotion = motion || MOTION.IDLE;
  }

  _showBestEmotion(group, { force = false, soft = false } = {}) {
    const emotion = this._pickEmotion(EMOTION_CANDIDATES[group] || EMOTION_CANDIDATES.neutral);
    if (!emotion) return false;
    if (!force && emotion === this._getCurrentEmotion?.()) return false;
    if (soft && this._petState === "speaking") return false;
    this._onEmotion?.(emotion);
    return true;
  }

  _scheduleEmotionRestore(durationMs = TRANSIENT_EMOTION_MS) {
    window.clearTimeout(this._transientEmotionTimer);
    this._transientEmotionTimer = window.setTimeout(() => {
      if (this._petState !== "idle" || this._listeningActive) return;
      if (this._musicActive) {
        this._showBestEmotion("music", { soft: true });
      } else {
        this._showBestEmotion("neutral", { soft: true });
      }
    }, durationMs);
  }

  _scheduleIdleMood() {
    this._clearIdleMoodTimer();
    if (this._petState !== "idle" || this._musicActive || this._listeningActive) return;
    const delay = IDLE_MOOD_MIN_MS + Math.floor(Math.random() * IDLE_MOOD_SPREAD_MS);
    this._idleMoodTimer = window.setTimeout(() => this._runIdleMood(), delay);
  }

  _runIdleMood() {
    this._idleMoodTimer = 0;
    if (this._petState !== "idle" || this._musicActive || this._listeningActive) return;

    const now = Date.now();
    if (now - this._lastIdleMoodAt < IDLE_MOOD_MIN_MS) {
      this._scheduleIdleMood();
      return;
    }
    this._lastIdleMoodAt = now;

    const hour = new Date().getHours();
    const group = hour >= 23 || hour <= 6 ? "sleepy" : Math.random() < 0.16 ? "snack" : "cheerful";
    this._showBestEmotion(group, { soft: true });
    this._scheduleEmotionRestore();
    this._scheduleIdleMood();
  }

  _clearIdleMoodTimer() {
    if (!this._idleMoodTimer) return;
    window.clearTimeout(this._idleMoodTimer);
    this._idleMoodTimer = 0;
  }

  _pickEmotion(candidates) {
    const available = this._availableEmotions();
    if (!available.length) return "";
    for (const candidate of candidates) {
      const found = available.find((item) => item.key === normalizeKey(candidate));
      if (found) return found.id;
    }
    return available[0]?.id || "";
  }

  _availableEmotions() {
    return (this._getAvailableEmotions?.() || [])
      .map((id) => String(id || "").trim())
      .filter(Boolean)
      .map((id) => ({ id, key: normalizeKey(id) }));
  }
}

function normalizeKey(value) {
  return String(value || "").trim().toLowerCase().replace(/[-\s]+/g, "_");
}

export { PetLifeController };
