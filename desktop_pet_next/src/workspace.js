import { invoke } from "@tauri-apps/api/core";
import { emit, emitTo, listen } from "@tauri-apps/api/event";
import { fetch as tauriFetch } from "@tauri-apps/plugin-http";

import "./workspace.css";

const SETTINGS_COMMAND_EVENT = "akane-next-settings-command";
const SETTINGS_SNAPSHOT_EVENT = "akane-next-settings-snapshot";
const WORKSPACE_REFRESH_EVENT = "akane-next-workspace-refresh";
const DEFAULT_BACKEND_URL = "http://127.0.0.1:9999";
const PROFILE_USER_ID = "master";
const SUMMARY_LIMIT = 24;
const TASK_AUTO_REFRESH_MS = 6500;

const els = {
  summary: document.querySelector("#workspace-summary"),
  clearFiles: document.querySelector("#clear-workspace-files"),
  refresh: document.querySelector("#refresh-workspace"),
  close: document.querySelector("#close-workspace"),
  alert: document.querySelector("#workspace-alert"),
  fileCount: document.querySelector("#file-count"),
  outputCount: document.querySelector("#output-count"),
  taskCount: document.querySelector("#task-count"),
  music: document.querySelector("#workspace-music"),
  content: document.querySelector("#workspace-content"),
  session: document.querySelector("#workspace-session"),
  updated: document.querySelector("#workspace-updated"),
  status: document.querySelector("#workspace-status")
};

let state = null;
let music = null;
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
  els.refresh.title = "刷新手边物品 (F5 / Ctrl+R)";
  els.refresh.addEventListener("click", () => {
    void refreshWorkspace();
  });
  els.clearFiles?.addEventListener("click", () => {
    void clearWorkspaceFiles();
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
    if (button.dataset.action === "open-file" && item) {
      void openWorkspaceItem(item);
    }
    if (button.dataset.action === "reveal-file" && item) {
      void revealWorkspaceItem(item);
    }
    if (button.dataset.action === "copy-path" && item) {
      void copyWorkspaceItemPath(item);
    }
    if (button.dataset.action === "export-desktop" && item) {
      void exportWorkspaceItemToDesktop(item);
    }
    if (button.dataset.action === "play-audio" && item) {
      void playWorkspaceAudio(item);
    }
    if (button.dataset.action === "clear-item" && item) {
      void clearWorkspaceItem(item);
    }
  });
  els.music.addEventListener("click", (event) => {
    const button = event.target.closest("[data-music-action]");
    if (!button) return;
    const sourceId = String(button.closest("[data-source-id]")?.dataset.sourceId || "").trim();
    const action = button.dataset.musicAction;
    if (action === "previous") sendCommand("previousMusic");
    if (action === "next") sendCommand("nextMusic");
    if (action === "toggle") sendCommand("toggleMusic");
    if (action === "stop") sendCommand("stopMusic");
    if (action === "play" && sourceId) sendCommand("playMusicTrack", sourceId);
    if (action === "remove" && sourceId) sendCommand("removeMusicTrack", sourceId);
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
    await listen(WORKSPACE_REFRESH_EVENT, () => {
      scheduleWorkspaceRefresh(120);
    });
    await sendCommand("requestSnapshot");
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
  if ("music" in snapshot) {
    music = snapshot.music || null;
    renderMusicPanel();
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
  renderMusicPanel();
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
    setUpdated(Date.now());
    setStatus("已刷新");
  } catch (error) {
    renderEmpty("手边物品暂时打不开，等后端回来后再刷新。");
    setAlert(`确认后端已经启动：${formatError(error)}`, "error");
    setUpdated(Date.now(), { failed: true });
    setStatus("刷新失败");
  } finally {
    loading = false;
    els.refresh.disabled = false;
    updateWorkspaceActions();
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
  scheduleTaskAutoRefresh(tasks);

  els.fileCount.textContent = String(files.length);
  els.outputCount.textContent = String(outputs.length);
  els.taskCount.textContent = String(tasks.length);
  els.summary.textContent = `${files.length} 文件 · ${outputs.length} 成果 · ${tasks.length} 任务`;
  updateWorkspaceActions({ files, outputs });
  setAlert("");

  if (!files.length && !outputs.length && !tasks.length) {
    renderEmpty("现在手边还很清爽，没有文件、成果或任务。");
    return;
  }

  els.content.replaceChildren(
    ...[
      renderSection("手边文件", "刚递给当前角色的原始材料", files, "file"),
      renderSection("做好的东西", "文档、音频和其他生成物", outputs, "output"),
      renderSection("后台任务", "排队、进行、完成和等待确认", tasks, "task")
    ].filter(Boolean)
  );
}

function renderMusicPanel() {
  const payload = music && typeof music === "object" ? music : {};
  const queue = Array.isArray(payload.queue) ? payload.queue : [];
  const current = payload.track && typeof payload.track === "object" ? payload.track : null;
  const hasTrack = Boolean(current || payload.displayName);
  const name = String(payload.displayName || current?.displayName || current?.fileName || "").trim();
  const queueCount = Number(payload.queueCount || queue.length || 0);
  const queueIndex = Number(payload.queueIndex || -1);
  const queueLabel = queueCount > 1 && queueIndex >= 0 ? `${queueIndex + 1}/${queueCount}` : "";
  const progressLabel = formatMusicProgress(payload.progressSeconds, payload.durationSeconds);

  els.music.replaceChildren();
  const header = document.createElement("header");
  header.className = "music-panel-header";
  const title = document.createElement("div");
  title.append(buildText("h2", "手边音乐"));
  const status = document.createElement("p");
  if (payload.loading) {
    status.textContent = "正在准备音乐……";
  } else if (payload.playing) {
    status.textContent = `正在播放：${name || "未命名音乐"}${queueLabel ? ` · ${queueLabel}` : ""}${progressLabel ? ` · ${progressLabel}` : ""}`;
  } else if (payload.paused) {
    status.textContent = `已暂停：${name || "未命名音乐"}${queueLabel ? ` · ${queueLabel}` : ""}${progressLabel ? ` · ${progressLabel}` : ""}`;
  } else {
    status.textContent = hasTrack ? `已停止：${name || "未命名音乐"}` : "把本地音乐拖到桌宠身上，就会放到这里。";
  }
  title.append(status);
  const lyricLine = buildMusicLyricText(payload, hasTrack);
  if (lyricLine) {
    const lyric = document.createElement("p");
    lyric.className = "music-panel-lyric";
    lyric.textContent = lyricLine;
    title.append(lyric);
  }

  const controls = document.createElement("div");
  controls.className = "music-panel-actions";
  controls.append(
    buildMusicButton("上一首", "previous", Boolean(payload.loading) || !payload.hasPrevious),
    buildMusicButton(payload.playing ? "暂停" : hasTrack ? "继续" : "播放", "toggle", Boolean(payload.loading) || !hasTrack),
    buildMusicButton("下一首", "next", Boolean(payload.loading) || !payload.hasNext),
    buildMusicButton("停止", "stop", Boolean(payload.loading) || !hasTrack)
  );
  header.append(title, controls);
  els.music.append(header);

  const list = document.createElement("div");
  list.className = "music-panel-list";
  if (!queue.length) {
    const empty = document.createElement("div");
    empty.className = "music-panel-empty";
    empty.textContent = "当前没有音乐队列。";
    list.append(empty);
  } else {
    const currentId = String(current?.sourceId || "").trim();
    for (const [index, track] of queue.slice(0, 8).entries()) {
      list.append(renderMusicQueueItem(track, index, currentId));
    }
  }
  els.music.append(list);
}

function renderMusicQueueItem(track, index, currentId) {
  const sourceId = String(track?.sourceId || "").trim();
  const row = document.createElement("article");
  row.className = "music-panel-item";
  if (sourceId) row.dataset.sourceId = sourceId;
  const isCurrent = sourceId && sourceId === currentId;
  if (isCurrent) row.classList.add("is-current");

  const mark = document.createElement("span");
  mark.className = "music-panel-index";
  mark.textContent = String(index + 1);

  const body = document.createElement("div");
  body.className = "music-panel-body";
  const title = document.createElement("strong");
  title.textContent = String(track?.displayName || track?.fileName || "未命名音乐");
  const meta = document.createElement("span");
  meta.textContent = [
    isCurrent ? "当前" : "",
    track?.extension ? String(track.extension).toUpperCase() : "",
    track?.lyricLineCount ? `LRC ${track.lyricLineCount} 行` : "",
    track?.timelineLyricLineCount ? `后端 ${track.timelineLyricLineCount} 行` : "",
    track?.timelineLoading || ["uploading", "pending", "processing"].includes(String(track?.timelineStatus || "")) ? "准备歌词中" : ""
  ]
    .filter(Boolean)
    .join(" · ");
  body.append(title, meta);

  const actions = document.createElement("div");
  actions.className = "music-panel-item-actions";
  actions.append(
    buildMusicButton(isCurrent ? "当前" : "播放", "play", isCurrent || !sourceId),
    buildMusicButton("移除", "remove", !sourceId)
  );

  row.append(mark, body, actions);
  return row;
}

function buildMusicButton(label, action, disabled = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.dataset.musicAction = action;
  button.textContent = label;
  button.disabled = Boolean(disabled);
  return button;
}

function buildMusicLyricText(payload, hasTrack) {
  const lyric = payload.currentLyric && typeof payload.currentLyric === "object" ? payload.currentLyric : {};
  const current = String(lyric.text || "").trim();
  const next = String(lyric.nextText || "").trim();
  const track = payload.track && typeof payload.track === "object" ? payload.track : {};
  const lineCount = Number(track.lyricLineCount || lyric.lineCount || track.timelineLyricLineCount || 0);
  const timelineStatus = String(track.timelineStatus || "").trim();
  if (current) return `歌词：${current}`;
  if (next && hasTrack) return `下一句：${next}`;
  if (hasTrack && lineCount > 0) return `已载入歌词 ${lineCount} 行。`;
  if (hasTrack && (track.timelineLoading || ["uploading", "pending", "processing"].includes(timelineStatus))) return "正在准备后端歌词线索……";
  return "";
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
  const statusGroup = resolveItemStatusGroup(item, kind);
  article.dataset.statusGroup = statusGroup;
  if (key) article.dataset.key = key;

  const mark = document.createElement("div");
  mark.className = "item-mark";
  mark.textContent = kind === "task" ? taskMark(item) : kind === "output" ? "✦" : kindMark(item.kind || item.format);

  const body = document.createElement("div");
  body.className = "item-body";

  const top = document.createElement("div");
  top.className = "item-topline";
  const statusBadge = buildText("span", item.status_label || item.status || "已放好");
  statusBadge.className = "item-status";
  statusBadge.dataset.statusGroup = statusGroup;
  top.append(buildText("h3", item.title || item.handle || "未命名"), statusBadge);

  const meta = buildItemMetaParts(item, kind).filter(Boolean).join(" · ");
  const detail = buildText("p", meta || "放在当前角色手边");

  const nextAction = kind === "task" && item.next_action ? buildText("p", `下一步：${item.next_action}`) : null;
  if (nextAction) nextAction.className = "item-next-action";

  const actions = document.createElement("div");
  actions.className = "item-actions";
  if (kind !== "task" && item.can_open) {
    actions.append(
      buildItemActionButton("open-file", "打开"),
      buildItemActionButton("reveal-file", "位置"),
      buildItemActionButton("export-desktop", "存桌面"),
      buildItemActionButton("copy-path", "复制路径")
    );
  }
  if (kind !== "task" && isPlayableAudioItem(item)) {
    const play = document.createElement("button");
    play.type = "button";
    play.dataset.action = "play-audio";
    play.textContent = "播放";
    actions.append(play);
  }
  const copy = document.createElement("button");
  copy.type = "button";
  copy.dataset.action = "copy-id";
  copy.textContent = "复制编号";
  actions.append(copy);
  if (item.can_clear) {
    const clear = document.createElement("button");
    clear.type = "button";
    clear.dataset.action = "clear-item";
    clear.className = "danger-button";
    clear.textContent = kind === "task" ? "清理" : "收起";
    actions.append(clear);
  }

  body.append(top, detail);
  if (nextAction) body.append(nextAction);
  body.append(actions);
  article.append(mark, body);
  return article;
}

function buildItemActionButton(action, label) {
  const button = document.createElement("button");
  button.type = "button";
  button.dataset.action = action;
  button.textContent = label;
  return button;
}

function scheduleTaskAutoRefresh(tasks) {
  if (Array.isArray(tasks) && tasks.some(isActiveTaskItem)) {
    scheduleWorkspaceRefresh(TASK_AUTO_REFRESH_MS);
  }
}

function isActiveTaskItem(item) {
  const status = String(item?.status || "").trim().toLowerCase();
  const group = String(item?.status_group || "").trim().toLowerCase();
  return group === "active" || ["queued", "running"].includes(status);
}

function resolveItemStatusGroup(item, kind) {
  if (kind !== "task") return String(item?.status || "").trim().toLowerCase() === "failed" ? "failed" : "idle";
  const group = String(item?.status_group || "").trim().toLowerCase();
  if (group) return group;
  const status = String(item?.status || "").trim().toLowerCase();
  if (["queued", "running"].includes(status)) return "active";
  if (status === "completed") return "done";
  if (status === "failed") return "failed";
  if (["blocked", "waiting_user", "partial"].includes(status)) return "attention";
  return "idle";
}

function taskMark(item) {
  const status = String(item?.status || "").trim().toLowerCase();
  if (status === "queued") return "队";
  if (status === "running") return "做";
  if (status === "completed") return "成";
  if (status === "failed") return "错";
  if (["blocked", "waiting_user", "partial"].includes(status)) return "问";
  return "任";
}

function buildItemMetaParts(item, kind) {
  if (kind === "task") {
    return [
      item.subtitle || "",
      item.artifact_count ? `${Number(item.artifact_count)} 个产物` : "",
      item.updated_at ? formatUpdatedAt(item.updated_at) : ""
    ];
  }
  return [
    item.subtitle || "",
    item.size_bytes ? formatSize(item.size_bytes) : "",
    item.updated_at ? formatUpdatedAt(item.updated_at) : ""
  ];
}

function isPlayableAudioItem(item) {
  const kind = String(item?.kind || "").toLowerCase();
  const format = String(item?.format || "").toLowerCase();
  const subtitle = String(item?.subtitle || "").toLowerCase();
  return (
    kind === "audio" ||
    subtitle.includes("音频") ||
    ["mp3", "wav", "flac", "ogg", "oga", "m4a", "aac", "opus", "webm"].includes(format)
  );
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

async function openWorkspaceItem(item) {
  try {
    const filePath = await resolveWorkspaceItemPath(item);
    setStatus("正在打开文件");
    await invoke("open_local_file", { path: filePath });
    setStatus("已打开文件");
  } catch (error) {
    setStatus(`打开失败：${formatError(error)}`);
    setAlert(`打开失败：${formatError(error)}`, "error");
  }
}

async function revealWorkspaceItem(item) {
  try {
    const filePath = await resolveWorkspaceItemPath(item);
    setStatus("正在打开位置");
    await invoke("show_item_in_folder", { path: filePath });
    setStatus("已打开所在位置");
  } catch (error) {
    setStatus(`打开位置失败：${formatError(error)}`);
    setAlert(`打开位置失败：${formatError(error)}`, "error");
  }
}

async function copyWorkspaceItemPath(item) {
  try {
    const filePath = await resolveWorkspaceItemPath(item);
    await navigator.clipboard.writeText(filePath);
    setStatus("文件路径已复制");
  } catch (error) {
    setStatus(`复制路径失败：${formatError(error)}`);
    setAlert(`复制路径失败：${formatError(error)}`, "error");
  }
}

async function exportWorkspaceItemToDesktop(item) {
  try {
    const filePath = await resolveWorkspaceItemPath(item);
    setStatus("正在保存到桌面");
    const result = await invoke("export_file_to_desktop", {
      path: filePath,
      fileName: buildWorkspaceExportFileName(item)
    });
    const exportedPath = String(result?.path || "").trim();
    setStatus(exportedPath ? "已保存到桌面" : "已保存");
    if (exportedPath) setAlert(`已保存到桌面：${exportedPath}`, "info");
  } catch (error) {
    setStatus(`保存失败：${formatError(error)}`);
    setAlert(`保存失败：${formatError(error)}`, "error");
  }
}

async function resolveWorkspaceItemPath(item) {
  const handle = String(item?.handle || item?.id || "").trim();
  if (!handle) throw new Error("没有可定位的编号");
  const sessionId = String(state?.sessionId || "").trim();
  if (!sessionId) throw new Error("会话还没准备好");

  const routeType = workspaceRouteType(item);
  const query = new URLSearchParams({
    user_id: sessionId,
    real_user_id: String(state?.profileUserId || PROFILE_USER_ID),
    t: String(Date.now())
  });
  const response = await tauriFetch(
    `${normalizeBackendUrl(state?.backendUrl || DEFAULT_BACKEND_URL)}/desktop-pet/workspace/${routeType}/${encodeURIComponent(handle)}/location?${query}`,
    {
      method: "GET",
      cache: "no-store",
      connectTimeout: 5000
    }
  );
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload?.ok || !payload?.path) {
    throw new Error(extractWorkspaceError(payload) || `HTTP ${response.status}`);
  }
  return String(payload.path);
}

function workspaceRouteType(item) {
  const type = String(item?.item_type || item?.type || "").trim().toLowerCase();
  return type === "generated" || type === "output" ? "generated" : "attachments";
}

function buildWorkspaceExportFileName(item) {
  const title = String(item?.title || item?.handle || item?.id || "akane-output").trim();
  const format = String(item?.format || "").trim().replace(/^\.+/, "");
  if (!format || title.toLowerCase().endsWith(`.${format.toLowerCase()}`)) return title;
  return `${title}.${format}`;
}

async function playWorkspaceAudio(item) {
  const handle = String(item?.handle || item?.id || "").trim();
  if (!handle) {
    setStatus("没有可播放的编号");
    return;
  }
  setStatus("准备播放");
  await sendCommand("playWorkspaceAudio", {
    itemType: item.item_type || item.type || "attachment",
    handle,
    title: item.title || "",
    format: item.format || "",
    kind: item.kind || ""
  });
}

async function clearWorkspaceItem(item) {
  const handle = String(item?.handle || item?.id || "").trim();
  if (!handle) {
    setStatus("没有可收起的编号");
    return;
  }
  setStatus("正在收起");
  try {
    await postWorkspaceAction({
      action: "clear",
      item_type: item.item_type || item.type || "attachment",
      target: handle
    });
    setStatus("已收起");
    await notifyMainWorkspaceRefresh();
    await refreshWorkspace({ reload: false });
  } catch (error) {
    setStatus(`收起失败：${formatError(error)}`);
    setAlert(`收起失败：${formatError(error)}`, "error");
  }
}

async function clearWorkspaceFiles() {
  if (loading) return;
  if (!confirm("确认清理所有手边物品？文件和生成物会被移除，但不影响聊天记录。")) return;
  setStatus("正在清理文件");
  try {
    const result = await postWorkspaceAction({ action: "clear_files" });
    const count = Number(result?.managed?.length || 0);
    setStatus(count ? `已清理 ${count} 项` : "没有可清理的文件");
    await notifyMainWorkspaceRefresh();
    await refreshWorkspace({ reload: false });
  } catch (error) {
    setStatus(`清理失败：${formatError(error)}`);
    setAlert(`清理失败：${formatError(error)}`, "error");
  }
}

async function postWorkspaceAction(payload) {
  const sessionId = String(state?.sessionId || "").trim();
  if (!sessionId) throw new Error("会话还没准备好");
  const response = await tauriFetch(`${normalizeBackendUrl(state?.backendUrl || DEFAULT_BACKEND_URL)}/desktop-pet/workspace/action`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
    body: JSON.stringify({
      user_id: sessionId,
      session_id: sessionId,
      real_user_id: state?.profileUserId || PROFILE_USER_ID,
      ...payload
    }),
    connectTimeout: 10000
  });
  const result = await response.json().catch(() => null);
  if (!response.ok || !result?.ok) {
    throw new Error(extractWorkspaceError(result) || `HTTP ${response.status}`);
  }
  return result;
}

async function notifyMainWorkspaceRefresh() {
  try {
    await emit(WORKSPACE_REFRESH_EVENT, { t: Date.now() });
  } catch {
    // The main window may not be open.
  }
}

function extractWorkspaceError(payload) {
  if (!payload || typeof payload !== "object") return "";
  const detail = payload.detail;
  if (typeof detail === "string" && detail.trim()) return detail.trim();
  for (const key of ["message", "error", "reason"]) {
    const value = String(payload[key] || "").trim();
    if (value) return value;
  }
  return "";
}

async function sendCommand(command, value = null) {
  const payload = { command, value };
  try {
    await emitTo("main", SETTINGS_COMMAND_EVENT, payload);
  } catch (error) {
    try {
      await emit(SETTINGS_COMMAND_EVENT, payload);
    } catch {
      setStatus(`命令发送失败：${formatError(error)}`);
    }
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
  updateWorkspaceActions({ files: [], outputs: [] });
}

function updateWorkspaceActions({ files = null, outputs = null } = {}) {
  if (!els.clearFiles) return;
  const fileCount = Array.isArray(files) ? files.length : Number(els.fileCount?.textContent || 0);
  const outputCount = Array.isArray(outputs) ? outputs.length : Number(els.outputCount?.textContent || 0);
  els.clearFiles.disabled = loading || fileCount + outputCount <= 0;
}

function updateIdentityUi() {
  const sessionId = String(state?.sessionId || "").trim();
  const short = sessionId ? `${sessionId.slice(0, 10)}…${sessionId.slice(-6)}` : "-";
  els.session.textContent = `Session: ${short}`;
  els.session.title = sessionId;
}

function setUpdated(value, { failed = false } = {}) {
  const timestamp = Number(value || 0);
  if (!Number.isFinite(timestamp) || timestamp <= 0) {
    els.updated.textContent = "Last refresh: -";
    return;
  }
  const label = failed ? "Last attempt" : "Last refresh";
  els.updated.textContent = `${label}: ${new Date(timestamp).toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  })}`;
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

function formatMusicProgress(progressValue, durationValue) {
  const progress = Number(progressValue || 0);
  const duration = Number(durationValue || 0);
  if (!Number.isFinite(progress) || progress <= 0) return "";
  const current = formatDuration(progress);
  if (!Number.isFinite(duration) || duration <= 0) return current;
  return `${current}/${formatDuration(duration)}`;
}

function formatDuration(value) {
  const total = Math.max(0, Math.floor(Number(value || 0)));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
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
