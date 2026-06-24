const root = document.getElementById("debug-root");
let state = null;

function requestState() {
  window.akaneAPI?.requestDebugState?.();
}

function render(nextState) {
  state = nextState || null;
  const emotions = Array.isArray(state?.availableEmotions) ? state.availableEmotions : [];
  const activity = state?.currentActivity || null;
  const currentEmotion = state?.resolvedEmotion || state?.currentEmotion || "normal";

  root.innerHTML = `
    <section class="debug-panel" role="dialog" aria-label="Akane 状态预览器">
      <header class="debug-panel__header">
        <div>
          <p class="debug-panel__eyebrow">Akane Inspector</p>
          <h1>状态预览器</h1>
          <p>只看表现层状态，不写记忆，也不改后端。</p>
        </div>
        <button class="debug-panel__icon-btn" type="button" data-action="close" aria-label="关闭">×</button>
      </header>

      <main class="debug-panel__body">
        ${state ? renderSummary(state, currentEmotion) : renderEmpty()}
        ${renderHealth(state)}
        ${renderActivity(activity)}
        ${renderEmotionGrid(emotions, currentEmotion)}
        ${renderTestTools()}
      </main>
    </section>
  `;
}

function renderEmpty() {
  return `<div class="debug-panel__empty">正在向桌宠本体要状态……</div>`;
}

function renderSummary(value, currentEmotion) {
  const rows = [
    ["状态", value.petState || "idle"],
    ["动效", value.lifeMotion || "idle"],
    ["表情", currentEmotion],
    ["服装", value.outfit || "猫娘"],
    ["TTS", value.voiceEnabled ? "开" : "关"],
    ["语音输入", value.voiceInputEnabled ? "开" : "关"],
  ];

  return `
    <section class="debug-section">
      <div class="debug-section__title-row">
        <h2>当前状态</h2>
        <button type="button" data-action="refresh">刷新</button>
      </div>
      <div class="debug-kv-grid">
        ${rows.map(([label, data]) => `
          <div class="debug-kv">
            <span>${escapeHtml(label)}</span>
            <strong>${escapeHtml(data)}</strong>
          </div>
        `).join("")}
      </div>
    </section>
  `;
}

function renderHealth(value) {
  const report = value?.healthReport || null;
  const running = Boolean(value?.healthCheckRunning);
  const status = String(report?.status || (running ? "running" : "unknown")).toLowerCase();
  const items = Array.isArray(report?.items) ? report.items : [];
  const checked = report?.checkedAt ? formatCheckedAt(report.checkedAt) : "尚未完成";
  return `
    <section class="debug-section">
      <div class="debug-section__title-row">
        <h2>启动自检</h2>
        <button type="button" data-action="run-health-check">${running ? "检查中" : "重新检查"}</button>
      </div>
      <article class="debug-health-card debug-health-card--${escapeAttr(status)}">
        <div class="debug-health-card__head">
          <strong>${escapeHtml(statusLabel(status))}</strong>
          <span>${escapeHtml(checked)}</span>
        </div>
        <p>${escapeHtml(report?.summary || (running ? "正在检查桌宠启动状态……" : "还没有自检结果。"))}</p>
      </article>
      ${items.length ? `
        <div class="debug-health-list">
          ${items.map((item) => `
            <div class="debug-health-item debug-health-item--${escapeAttr(item.status || "unknown")}">
              <span>${escapeHtml(item.label || item.id || "检查项")}</span>
              <strong>${escapeHtml(item.message || "")}</strong>
              ${item.detail ? `<small>${escapeHtml(item.detail)}</small>` : ""}
            </div>
          `).join("")}
        </div>
      ` : ""}
    </section>
  `;
}

function renderActivity(activity) {
  if (!activity) {
    return `
      <section class="debug-section">
        <div class="debug-section__title-row">
          <h2>当前播放</h2>
        </div>
        <div class="debug-panel__empty debug-panel__empty--compact">现在没有桌宠音频活动。</div>
      </section>
    `;
  }

  return `
    <section class="debug-section">
      <div class="debug-section__title-row">
        <h2>当前播放</h2>
      </div>
      <article class="debug-activity-card">
        <h3>${escapeHtml(activity.title || "当前音频")}</h3>
        <p>${escapeHtml(activity.status || "ready")} · ${escapeHtml(formatTime(activity.progress_seconds))}${activity.duration_seconds ? ` / ${escapeHtml(formatTime(activity.duration_seconds))}` : ""}</p>
        <small>${escapeHtml(activity.source_id || activity.attachment_handle || activity.generated_handle || "")}</small>
      </article>
    </section>
  `;
}

function renderEmotionGrid(emotions, currentEmotion) {
  const items = emotions.length ? emotions : [{ id: "正常", name: "正常" }];
  return `
    <section class="debug-section">
      <div class="debug-section__title-row">
        <h2>表情预览</h2>
        <span>${items.length} 个</span>
      </div>
      <div class="debug-emotion-grid">
        ${items.map((emotion) => {
          const id = String(emotion.id || emotion.name || "").trim();
          const label = String(emotion.name || id).trim();
          const active = id === currentEmotion || label === currentEmotion;
          return `
            <button type="button" class="${active ? "active" : ""}" data-action="preview-emotion" data-emotion="${escapeAttr(id)}">
              ${escapeHtml(label || id)}
            </button>
          `;
        }).join("")}
      </div>
    </section>
  `;
}

function renderTestTools() {
  return `
    <section class="debug-section">
      <div class="debug-section__title-row">
        <h2>测试动作</h2>
      </div>
      <div class="debug-tool-row">
        <button type="button" data-action="test-bubble">测试气泡</button>
        <button type="button" data-action="test-tts">测试语音</button>
      </div>
    </section>
  `;
}

root.addEventListener("click", (event) => {
  const target = event.target?.closest?.("[data-action]");
  if (!target) return;
  event.preventDefault();

  const action = target.dataset.action;
  if (action === "close") {
    window.close();
    return;
  }
  if (action === "refresh") {
    requestState();
    return;
  }
  if (action === "run-health-check") {
    window.akaneAPI?.sendDebugAction?.({ type: "run-health-check" });
    return;
  }
  if (action === "preview-emotion") {
    window.akaneAPI?.sendDebugAction?.({
      type: "preview-emotion",
      emotion: target.dataset.emotion || "",
    });
    return;
  }
  if (action === "test-bubble" || action === "test-tts") {
    window.akaneAPI?.sendDebugAction?.({ type: action });
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") window.close();
});

window.akaneAPI?.onDebugInit?.(() => {
  render(null);
  requestState();
});

window.akaneAPI?.onDebugState?.((nextState) => {
  render(nextState);
});

render(null);
requestState();

function formatTime(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds <= 0) return "0:00";
  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  const secs = total % 60;
  return `${minutes}:${String(secs).padStart(2, "0")}`;
}

function formatCheckedAt(value) {
  const timestamp = Number(value);
  if (!Number.isFinite(timestamp) || timestamp <= 0) return "尚未完成";
  return new Date(timestamp).toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function statusLabel(status) {
  if (status === "ok") return "状态良好";
  if (status === "warning") return "需要留意";
  if (status === "error") return "需要处理";
  if (status === "running") return "检查中";
  return "未检查";
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/'/g, "&#39;");
}
