const POLL_MIN_MS = 8000;
const POLL_MAX_MS = 15000;
const FETCH_TIMEOUT_MS = 8000;
const MAX_SEEN_RECORDS = 120;
const SEEN_STORAGE_KEY = "akane_pet_task_reminders_seen_v1";
const REMINDER_STATES = new Set(["completed", "blocked", "partial"]);
const LIGHTWEIGHT_AUDIO_TASK_PATTERNS = [
  /^(播放|放|放一下|播放一下)(歌曲|音乐|音频|这首歌|当前歌曲|当前音乐)?$/,
  /^(继续|恢复|暂停|停止|切换|切)(播放|歌曲|音乐|音频|歌)?$/,
  /^(听歌|放歌|切歌)$/,
];

class TaskWatcher {
  /**
   * @param {{
   *   getBackendUrl: () => string,
   *   getIdentity: () => ({ profileUserId: string, sessionId: string } | null),
   *   canNotify: () => boolean,
   *   onNotify: (message: string, item: object) => void
   * }} options
   */
  constructor({ getBackendUrl, getIdentity, canNotify, onNotify }) {
    this._getBackendUrl = getBackendUrl;
    this._getIdentity = getIdentity;
    this._canNotify = canNotify;
    this._onNotify = onNotify;
    this._running = false;
    this._polling = false;
    this._timer = 0;
    this._queue = [];
    this._seen = this._loadSeen();
  }

  start() {
    if (this._running) return;
    this._running = true;
    void this.pollNow();
  }

  stop() {
    this._running = false;
    window.clearTimeout(this._timer);
    this._timer = 0;
    this._queue = [];
  }

  async pollNow() {
    if (!this._running || this._polling) return;
    this._polling = true;
    try {
      const items = await this._fetchStatusItems();
      this._ingest(items);
    } catch {
      // Task reminders are passive; network failures should never disturb chat.
    } finally {
      this._polling = false;
      this._scheduleNextPoll();
    }
  }

  flush() {
    if (!this._canNotify?.()) return false;
    const entry = this._queue.shift();
    if (!entry) return false;
    this._markSeen(entry.key);
    this._onNotify?.(this._buildMessage(entry.item, entry.state), entry.item);
    return true;
  }

  async _fetchStatusItems() {
    const baseUrl = String(this._getBackendUrl?.() || "").trim().replace(/\/+$/, "");
    const identity = this._getIdentity?.();
    if (!baseUrl || !identity?.profileUserId || !identity?.sessionId) return [];

    const url = new URL("/task-workspace/status", `${baseUrl}/`);
    url.searchParams.set("user_id", identity.sessionId);
    url.searchParams.set("real_user_id", identity.profileUserId);
    url.searchParams.set("scope", "profile");
    url.searchParams.set("limit", "20");
    url.searchParams.set("t", String(Date.now()));

    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
    let response;
    try {
      response = await fetch(url.toString(), {
        cache: "no-store",
        signal: controller.signal,
      });
    } finally {
      window.clearTimeout(timeoutId);
    }

    if (!response.ok) return [];
    const payload = await response.json();
    return Array.isArray(payload?.items) ? payload.items : [];
  }

  _ingest(items) {
    for (const item of Array.isArray(items) ? items : []) {
      const state = this._getReminderState(item);
      if (!REMINDER_STATES.has(state)) continue;
      if (this._isLightweightAudioTask(item)) continue;

      const key = this._buildDedupeKey(item, state);
      if (!key || this._seen[key] || this._queue.some((entry) => entry.key === key)) {
        continue;
      }
      this._queue.push({ key, state, item });
    }

    this.flush();
  }

  _getReminderState(item) {
    const handoffState = String(item?.handoff?.state || "").trim().toLowerCase();
    if (handoffState) return handoffState;
    return String(item?.status || "").trim().toLowerCase();
  }

  _buildDedupeKey(item, state) {
    const taskId = String(item?.task_id || "").trim();
    const updatedAt = String(item?.updated_at || "0").trim() || "0";
    if (!taskId || !state) return "";
    return `${taskId}:${state}:${updatedAt}`;
  }

  _buildMessage(item, state) {
    const subject = this._buildSubject(item);
    if (state === "blocked") {
      return `主人，${subject}卡住了，好像需要你确认一下。`;
    }
    if (state === "partial") {
      return `主人，${subject}先做出一部分结果了，要不要先看看？`;
    }
    return `主人，${subject}做好啦。\n要不要我帮你看看结果？`;
  }

  _buildSubject(item) {
    const title = String(item?.title || "").trim();
    if (!title) return "后台任务";
    const shortTitle = title.length > 22 ? `${title.slice(0, 22)}...` : title;
    return `「${shortTitle}」`;
  }

  _isLightweightAudioTask(item) {
    const title = normalizeTaskTitle(item?.title || item?.summary || "");
    if (!title || title.length > 16) return false;
    const artifacts = item?.handoff?.artifacts || item?.artifacts || [];
    if (Array.isArray(artifacts) && artifacts.length > 0) return false;
    return LIGHTWEIGHT_AUDIO_TASK_PATTERNS.some((pattern) => pattern.test(title));
  }

  _scheduleNextPoll() {
    window.clearTimeout(this._timer);
    this._timer = 0;
    if (!this._running) return;

    const delay = POLL_MIN_MS + Math.floor(Math.random() * (POLL_MAX_MS - POLL_MIN_MS + 1));
    this._timer = window.setTimeout(() => {
      void this.pollNow();
    }, delay);
  }

  _loadSeen() {
    try {
      const raw = localStorage.getItem(SEEN_STORAGE_KEY);
      const parsed = raw ? JSON.parse(raw) : {};
      return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
    } catch {
      return {};
    }
  }

  _markSeen(key) {
    if (!key) return;
    delete this._seen[key];
    this._seen[key] = true;

    const keys = Object.keys(this._seen);
    if (keys.length > MAX_SEEN_RECORDS) {
      const trimmed = {};
      for (const item of keys.slice(-MAX_SEEN_RECORDS)) {
        trimmed[item] = true;
      }
      this._seen = trimmed;
    }

    try {
      localStorage.setItem(SEEN_STORAGE_KEY, JSON.stringify(this._seen));
    } catch {
      // localStorage may be unavailable; in-memory dedupe still works.
    }
  }
}

function normalizeTaskTitle(value) {
  return String(value || "")
    .trim()
    .replace(/[「」《》【】"'“”‘’\s，。！？!?、：:；;,.]+/g, "");
}

export { TaskWatcher };
