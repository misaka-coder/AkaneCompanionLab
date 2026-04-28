import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { fetch as tauriFetch } from "@tauri-apps/plugin-http";

import "./workspace.css";

const SETTINGS_SNAPSHOT_EVENT = "akane-next-settings-snapshot";
const DEFAULT_BACKEND_URL = "http://127.0.0.1:9999";
const PROFILE_USER_ID = "master";
const SUMMARY_LIMIT = 24;

const els = {
  summary: document.querySelector("#workspace-summary"),
  refresh: document.querySelector("#refresh-workspace"),
  close: document.querySelector("#close-workspace"),
  alert: document.querySelector("#workspace-alert"),
  fileCount: document.querySelector("#file-count"),
  outputCount: document.querySelector("#output-count"),
  taskCount: document.querySelector("#task-count"),
  content: document.querySelector("#workspace-content"),
  session: document.querySelector("#workspace-session"),
  status: document.querySelector("#workspace-status")
};

let state = null;
let loading = false;
let refreshTimer = 0;
const itemMap = new Map();

boot();

async function boot() {
  bindUi();
  await bindStateSync();
  await reloadState();
  await refreshWorkspace({ reload: false });
}

function bindUi() {
  els.refresh.addEventListener("click", () => {
    void refreshWorkspace();
  });
  els.close.addEventListener("click", () => {
    void closeWindow();
  });
  els.content.addEventListener("click", (event) => {
    const button = event.target.closest("[data-action]");
    if (!button) return;
    const key = button.closest("[data-key]")?.dataset.key || "";
    const item = itemMap.get(key);
    if (button.dataset.action === "copy-id" && item) {
      void copyItemId(item);
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      void closeWindow();
    }
    if (event.key === "F5" || (event.ctrlKey && event.key.toLowerCase() === "r")) {
      event.preventDefault();
      void refreshWorkspace();
    }
  });
  window.addEventListener("beforeunload", () => {
    window.clearTimeout(refreshTimer);
  });
}

async function bindStateSync() {
  try {
    await listen(SETTINGS_SNAPSHOT_EVENT, (event) => {
      applySettingsSnapshot(event.payload);
    });
  } catch {
    // The workspace can still operate by loading persisted state.
  }
}

function applySettingsSnapshot(snapshot) {
  if (!snapshot?.state) return;
  const previousKey = stateIdentityKey(state);
  state = { ...(state || {}), ...snapshot.state };
  updateIdentityUi();

  const nextKey = stateIdentityKey(state);
  if (previousKey && previousKey !== nextKey) {
    scheduleWorkspaceRefresh();
  }
}

function stateIdentityKey(value) {
  if (!value) return "";
  return [
    normalizeBackendUrl(value.backendUrl || DEFAULT_BACKEND_URL),
    String(value.profileUserId || PROFILE_USER_ID),
    String(value.sessionId || "")
  ].join("|");
}

function scheduleWorkspaceRefresh(delay = 220) {
  window.clearTimeout(refreshTimer);
  refreshTimer = window.setTimeout(() => {
    refreshTimer = 0;
    void refreshWorkspace({ reload: false });
  }, delay);
}

async function reloadState() {
  try {
    state = await invoke("load_pet_state");
  } catch (error) {
    state = {};
    setAlert(`读取设置失败：${formatError(error)}`, "error");
  }
  updateIdentityUi();
}

async function refreshWorkspace({ reload = true } = {}) {
  if (loading) return;
  loading = true;
  els.refresh.disabled = true;
  setStatus("刷新中");
  setAlert("");
  renderLoading();

  try {
    if (reload || !state) await reloadState();
    const sessionId = String(state?.sessionId || "").trim();
    if (!sessionId) {
      renderEmpty("会话还没准备好，先在桌宠里发一条消息。");
      setStatus("等待会话");
      return;
    }

    const payload = await fetchWorkspaceSummary({
      backendUrl: state?.backendUrl || DEFAULT_BACKEND_URL,
      profileUserId: state?.profileUserId || PROFILE_USER_ID,
      sessionId
    });
    renderPayload(payload || {});
    setStatus("已刷新");
  } catch (error) {
    renderEmpty("手边物品暂时打不开。");
    setAlert(`确认后端已经启动：${formatError(error)}`, "error");
    setStatus("刷新失败");
  } finally {
    loading = false;
    els.refresh.disabled = false;
  }
}

async function fetchWorkspaceSummary({ backendUrl, profileUserId, sessionId }) {
  const query = new URLSearchParams({
    user_id: String(sessionId || ""),
    real_user_id: String(profileUserId || PROFILE_USER_ID),
    limit: String(SUMMARY_LIMIT),
    t: String(Date.now())
  });
  const response = await tauriFetch(`${normalizeBackendUrl(backendUrl)}/desktop-pet/workspace/summary?${query}`, {
    method: "GET",
    cache: "no-store",
    connectTimeout: 5000
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

function renderPayload(payload) {
  itemMap.clear();
  const sections = payload.sections && typeof payload.sections === "object" ? payload.sections : {};
  const files = normalizeItems(sections.files);
  const outputs = normalizeItems(sections.outputs);
  const tasks = normalizeItems(sections.tasks);

  els.fileCount.textContent = String(files.length);
  els.outputCount.textContent = String(outputs.length);
  els.taskCount.textContent = String(tasks.length);
  els.summary.textContent = `${files.length} 文件 · ${outputs.length} 成果 · ${tasks.length} 任务`;
  setAlert("");

  if (!files.length && !outputs.length && !tasks.length) {
    renderEmpty("现在手边还很清爽。");
    return;
  }

  els.content.replaceChildren(
    ...[
      renderSection("手边文件", "刚递给 Akane 的原始材料", files, "file"),
      renderSection("Akane 做好的东西", "文档、音频和其他生成物", outputs, "output"),
      renderSection("正在进行", "后台任务只显示给用户看的状态", tasks, "task")
    ].filter(Boolean)
  );
}

function renderSection(title, subtitle, items, kind) {
  if (!items.length) return null;
  const section = document.createElement("section");
  section.className = "workspace-section";

  const header = document.createElement("header");
  header.className = "section-header";
  header.append(buildText("h2", title), buildText("p", subtitle));

  const grid = document.createElement("div");
  grid.className = "item-grid";
  for (const item of items) {
    grid.append(renderItem(item, kind));
  }

  section.append(header, grid);
  return section;
}

function renderItem(item, kind) {
  const key = itemKey(item);
  if (key) itemMap.set(key, item);

  const article = document.createElement("article");
  article.className = "workspace-item";
  if (key) article.dataset.key = key;

  const mark = document.createElement("div");
  mark.className = "item-mark";
  mark.textContent = kind === "task" ? "✓" : kind === "output" ? "✦" : kindMark(item.kind || item.format);

  const body = document.createElement("div");
  body.className = "item-body";

  const top = document.createElement("div");
  top.className = "item-topline";
  top.append(buildText("h3", item.title || item.handle || "未命名"), buildText("span", item.status_label || item.status || "已放好"));

  const meta = [
    item.subtitle || "",
    item.size_bytes ? formatSize(item.size_bytes) : "",
    item.updated_at ? formatUpdatedAt(item.updated_at) : ""
  ]
    .filter(Boolean)
    .join(" · ");
  const detail = buildText("p", meta || "放在 Akane 手边");

  const actions = document.createElement("div");
  actions.className = "item-actions";
  const copy = document.createElement("button");
  copy.type = "button";
  copy.dataset.action = "copy-id";
  copy.textContent = "复制编号";
  actions.append(copy);

  body.append(top, detail, actions);
  article.append(mark, body);
  return article;
}

function normalizeItems(value) {
  return Array.isArray(value) ? value.filter((item) => item && typeof item === "object") : [];
}

function itemKey(item) {
  return String(item.id || item.handle || item.target || item.path || item.title || "").trim();
}

async function copyItemId(item) {
  const value = itemKey(item);
  if (!value) {
    setStatus("没有可复制的编号");
    return;
  }
  try {
    await navigator.clipboard.writeText(value);
    setStatus("编号已复制");
  } catch (error) {
    setStatus(`复制失败：${formatError(error)}`);
  }
}

function renderLoading() {
  renderEmpty("我在翻翻手边的小托盘……");
}

function renderEmpty(message) {
  els.content.replaceChildren();
  const empty = document.createElement("div");
  empty.className = "empty-state";
  empty.textContent = message;
  els.content.append(empty);
  els.summary.textContent = "手边物品";
  els.fileCount.textContent = "0";
  els.outputCount.textContent = "0";
  els.taskCount.textContent = "0";
}

function updateIdentityUi() {
  const sessionId = String(state?.sessionId || "").trim();
  const short = sessionId ? `${sessionId.slice(0, 10)}…${sessionId.slice(-6)}` : "-";
  els.session.textContent = `Session: ${short}`;
  els.session.title = sessionId;
}

function setAlert(message, status = "info") {
  const text = String(message || "").trim();
  els.alert.hidden = !text;
  els.alert.textContent = text;
  els.alert.dataset.status = status;
}

function setStatus(message) {
  els.status.textContent = message;
}

async function closeWindow() {
  try {
    await invoke("close_window");
  } catch {
    window.close();
  }
}

function normalizeBackendUrl(url) {
  return String(url || "").trim().replace(/\/+$/, "") || DEFAULT_BACKEND_URL;
}

function buildText(tagName, text) {
  const element = document.createElement(tagName);
  element.textContent = text;
  return element;
}

function kindMark(value) {
  const kind = String(value || "").toLowerCase();
  if (kind.includes("audio") || kind.includes("music") || kind.includes("mp3") || kind.includes("wav")) return "♪";
  if (kind.includes("image") || kind.includes("png") || kind.includes("jpg")) return "图";
  if (kind.includes("text") || kind.includes("md") || kind.includes("doc")) return "文";
  return "物";
}

function formatSize(bytes) {
  const size = Number(bytes || 0);
  if (!Number.isFinite(size) || size <= 0) return "";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function formatUpdatedAt(value) {
  let timestamp = Number(value || 0);
  if (!Number.isFinite(timestamp) || timestamp <= 0) {
    const parsed = Date.parse(String(value || ""));
    if (!Number.isFinite(parsed)) return "";
    timestamp = parsed;
  }
  return new Date(timestamp < 10_000_000_000 ? timestamp * 1000 : timestamp).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}

function formatError(error) {
  return error instanceof Error ? error.message : String(error);
}
