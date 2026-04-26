class WorkspacePanel {
  constructor(root, { backendClient, getIdentity, getCurrentActivity, onNotice } = {}) {
    this._root = root;
    this._backendClient = backendClient;
    this._getIdentity = getIdentity;
    this._getCurrentActivity = getCurrentActivity;
    this._onNotice = onNotice;
    this._visible = false;
    this._items = new Map();

    this._root.addEventListener("click", (event) => {
      void this._handleClick(event);
    });
    this._root.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        this.close();
      }
    });
  }

  isVisible() {
    return this._visible;
  }

  async toggle() {
    if (this._visible) {
      this.close();
      return;
    }
    await this.open();
  }

  async open() {
    this._visible = true;
    this._root.classList.add("visible");
    this._root.setAttribute("aria-hidden", "false");
    this._root.tabIndex = -1;
    this._root.focus();
    this._renderShell({ loading: true });
    await this.refresh();
  }

  close() {
    this._visible = false;
    this._root.classList.remove("visible");
    this._root.setAttribute("aria-hidden", "true");
  }

  async refresh() {
    if (!this._visible) return;
    const identity = this._getIdentity?.();
    if (!identity?.profileUserId || !identity?.sessionId) {
      this._renderShell({ error: "会话还没准备好。" });
      return;
    }
    try {
      const payload = await this._backendClient.fetchWorkspaceSummary({
        profileUserId: identity.profileUserId,
        sessionId: identity.sessionId,
        limit: 24,
      });
      this._renderPayload(payload || {});
    } catch (error) {
      console.warn("[AkanePet] workspace panel refresh failed:", error);
      this._renderShell({ error: "手边物品暂时打不开，确认后端已经启动。" });
    }
  }

  _renderShell({ loading = false, error = "" } = {}) {
    this._items.clear();
    this._root.innerHTML = `
      <section class="workspace-panel" role="dialog" aria-modal="true" aria-label="Akane 手边物品">
        <div class="workspace-panel__glow"></div>
        <header class="workspace-panel__header">
          <div>
            <p class="workspace-panel__eyebrow">Akane Tray</p>
            <h2>手边物品</h2>
            <p class="workspace-panel__subtitle">只整理你和 Akane 当前正在用的东西。</p>
          </div>
          <button class="workspace-panel__icon-btn" type="button" data-action="close" aria-label="关闭">×</button>
        </header>
        <main class="workspace-panel__body">
          ${
            loading
              ? '<div class="workspace-panel__empty">我在翻翻手边的小托盘……</div>'
              : `<div class="workspace-panel__empty">${this._escape(error || "现在手边还很清爽。")}</div>`
          }
        </main>
      </section>
    `;
  }

  _renderPayload(payload) {
    this._items.clear();
    const sections = payload.sections && typeof payload.sections === "object" ? payload.sections : {};
    const files = Array.isArray(sections.files) ? sections.files : [];
    const outputs = Array.isArray(sections.outputs) ? sections.outputs : [];
    const tasks = Array.isArray(sections.tasks) ? sections.tasks : [];
    const currentActivity = this._getCurrentActivity?.() || null;

    const fileCount = files.length;
    const outputCount = outputs.length;
    const taskCount = tasks.length;
    const hasAny = fileCount || outputCount || taskCount || currentActivity;

    this._root.innerHTML = `
      <section class="workspace-panel" role="dialog" aria-modal="true" aria-label="Akane 手边物品">
        <div class="workspace-panel__glow"></div>
        <header class="workspace-panel__header">
          <div>
            <p class="workspace-panel__eyebrow">Akane Tray</p>
            <h2>手边物品</h2>
            <p class="workspace-panel__subtitle">文件、成果、任务，都先放在这只小托盘里。</p>
          </div>
          <button class="workspace-panel__icon-btn" type="button" data-action="close" aria-label="关闭">×</button>
        </header>
        <div class="workspace-panel__stats">
          <span>${fileCount} 个文件</span>
          <span>${outputCount} 个成果</span>
          <span>${taskCount} 件事</span>
        </div>
        <main class="workspace-panel__body">
          ${currentActivity ? this._renderActivity(currentActivity) : ""}
          ${
            hasAny
              ? [
                  this._renderSection("手边文件", "刚递给 Akane 的原始材料", files),
                  this._renderSection("Akane 做好的东西", "文档、音频和其他生成物", outputs),
                  this._renderSection("正在进行", "后台任务只显示给用户看的状态", tasks, { taskSection: true }),
                ].join("")
              : '<div class="workspace-panel__empty">现在手边还很清爽。</div>'
          }
        </main>
        <footer class="workspace-panel__footer">
          <button type="button" class="workspace-panel__ghost-btn" data-action="refresh">刷新</button>
          <button type="button" class="workspace-panel__ghost-btn" data-action="clear-completed">清理完成任务</button>
        </footer>
      </section>
    `;
  }

  _renderActivity(activity) {
    const title = this._escape(activity.title || "当前音频");
    const status = this._escape(this._activityStatusLabel(activity.status));
    const progress = this._formatTime(activity.progress_seconds);
    const duration = this._formatTime(activity.duration_seconds);
    const timeText = progress ? `${progress}${duration ? ` / ${duration}` : ""}` : "刚刚放在手边";
    return `
      <section class="workspace-section workspace-section--activity">
        <div class="workspace-section__title-row">
          <div>
            <h3>当前播放</h3>
            <p>这只是桌宠播放器状态，不会写进长期记忆。</p>
          </div>
        </div>
        <article class="workspace-card workspace-card--activity">
          <div class="workspace-card__mark">♪</div>
          <div class="workspace-card__content">
            <h4>${title}</h4>
            <p>${status} · ${this._escape(timeText)}</p>
          </div>
        </article>
      </section>
    `;
  }

  _renderSection(title, subtitle, items, { taskSection = false } = {}) {
    if (!items.length) return "";
    return `
      <section class="workspace-section">
        <div class="workspace-section__title-row">
          <div>
            <h3>${this._escape(title)}</h3>
            <p>${this._escape(subtitle)}</p>
          </div>
        </div>
        <div class="workspace-section__grid">
          ${items.map((item) => this._renderItem(item, { taskSection })).join("")}
        </div>
      </section>
    `;
  }

  _renderItem(item, { taskSection = false } = {}) {
    if (!item || typeof item !== "object") return "";
    const key = this._itemKey(item);
    if (!key) return "";
    this._items.set(key, item);
    const status = this._escape(item.status_label || item.status || "已放好");
    const title = this._escape(item.title || item.handle || "未命名");
    const subtitle = this._escape(item.subtitle || "");
    const size = item.size_bytes ? ` · ${this._formatSize(item.size_bytes)}` : "";
    const updated = item.updated_at ? ` · ${this._formatUpdatedAt(item.updated_at)}` : "";
    const meta = `${subtitle}${size}${updated}`;
    const mark = taskSection ? "✓" : item.item_type === "generated" ? "✦" : this._kindMark(item.kind || item.format);
    return `
      <article class="workspace-card" data-key="${this._escapeAttr(key)}">
        <div class="workspace-card__mark">${this._escape(mark)}</div>
        <div class="workspace-card__content">
          <div class="workspace-card__topline">
            <h4>${title}</h4>
            <span>${status}</span>
          </div>
          <p>${this._escape(meta || "放在 Akane 手边")}</p>
          <div class="workspace-card__actions">
            ${item.can_open ? '<button type="button" data-action="open-item">打开</button>' : ""}
            <button type="button" data-action="copy-item">复制编号</button>
            ${item.can_clear ? '<button type="button" data-action="clear-item">收起来</button>' : ""}
          </div>
        </div>
      </article>
    `;
  }

  async _handleClick(event) {
    const action = event.target?.dataset?.action;
    if (!action) return;
    event.preventDefault();

    if (action === "close") {
      this.close();
      return;
    }
    if (action === "refresh") {
      await this.refresh();
      return;
    }
    if (action === "clear-completed") {
      await this._clearCompletedTasks();
      return;
    }

    const card = event.target.closest?.("[data-key]");
    const key = card?.dataset?.key || "";
    const item = this._items.get(key);
    if (!item) return;
    if (action === "open-item") {
      await this._openItem(item);
    } else if (action === "copy-item") {
      await this._copyItem(item);
    } else if (action === "clear-item") {
      await this._clearItem(item);
    }
  }

  async _openItem(item) {
    const url = this._backendClient.resolveUrl(item.url || "");
    if (!url) {
      this._notice("这个物品暂时不能直接打开。");
      return;
    }
    const result = await window.akaneAPI?.openExternal?.(url);
    if (!result?.ok) this._notice("打开失败了，可能是系统拦截了。");
  }

  async _copyItem(item) {
    const text = String(item.handle || item.id || "").trim();
    if (!text) return;
    await window.akaneAPI?.copyText?.(text);
    this._notice(`已复制编号：${text}`);
  }

  async _clearItem(item) {
    const identity = this._getIdentity?.();
    if (!identity?.profileUserId || !identity?.sessionId) return;
    try {
      const result = await this._backendClient.workspaceAction({
        profileUserId: identity.profileUserId,
        sessionId: identity.sessionId,
        action: "clear",
        itemType: item.item_type,
        target: item.handle || item.id,
      });
      if (!result?.ok) {
        this._notice("这个物品暂时收不起来。");
        return;
      }
      this._notice("已经收起来了。");
      await this.refresh();
    } catch (error) {
      console.warn("[AkanePet] workspace clear failed:", error);
      this._notice("收起来失败了。");
    }
  }

  async _clearCompletedTasks() {
    const identity = this._getIdentity?.();
    if (!identity?.profileUserId || !identity?.sessionId) return;
    try {
      const result = await this._backendClient.workspaceAction({
        profileUserId: identity.profileUserId,
        sessionId: identity.sessionId,
        action: "clear_completed_tasks",
      });
      const count = Array.isArray(result?.managed) ? result.managed.length : 0;
      this._notice(count ? `清理了 ${count} 个完成任务。` : "没有需要清理的完成任务。");
      await this.refresh();
    } catch (error) {
      console.warn("[AkanePet] workspace clear completed failed:", error);
      this._notice("清理完成任务失败了。");
    }
  }

  _itemKey(item) {
    return `${item.item_type || "item"}:${item.handle || item.id || ""}`;
  }

  _kindMark(value) {
    const text = String(value || "").toLowerCase();
    if (["audio", "mp3", "wav", "flac", "m4a", "aac", "ogg", "opus"].includes(text)) return "♪";
    if (["image", "png", "jpg", "jpeg", "gif", "webp"].includes(text)) return "◌";
    if (["pdf", "docx", "txt", "md"].includes(text)) return "文";
    return "□";
  }

  _activityStatusLabel(value) {
    return {
      ready: "已放在手边",
      running: "正在播放",
      paused: "已暂停",
      interrupted: "暂停等确认",
      stopped: "已停止",
      completed: "已播放结束",
    }[String(value || "").toLowerCase()] || "手边音频";
  }

  _formatTime(value) {
    const seconds = Number(value);
    if (!Number.isFinite(seconds) || seconds <= 0) return "";
    const total = Math.max(0, Math.round(seconds));
    const minutes = Math.floor(total / 60);
    const secs = total % 60;
    return `${minutes}:${String(secs).padStart(2, "0")}`;
  }

  _formatSize(value) {
    const bytes = Number(value);
    if (!Number.isFinite(bytes) || bytes <= 0) return "";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  _formatUpdatedAt(value) {
    const ts = Number(value);
    if (!Number.isFinite(ts) || ts <= 0) return "";
    const delta = Math.max(0, Math.floor(Date.now() / 1000 - ts));
    if (delta < 60) return "刚刚";
    if (delta < 3600) return `${Math.floor(delta / 60)} 分钟前`;
    if (delta < 86400) return `${Math.floor(delta / 3600)} 小时前`;
    return `${Math.floor(delta / 86400)} 天前`;
  }

  _notice(text) {
    const message = String(text || "").trim();
    if (message) this._onNotice?.(message);
  }

  _escape(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  _escapeAttr(value) {
    return this._escape(value).replace(/'/g, "&#39;");
  }
}

export { WorkspacePanel };
