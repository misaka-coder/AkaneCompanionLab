import { createPendingAttachments } from "../pending-attachments.js";
import { createWorkspaceImportQueue } from "../workspace-import.js";
import { botScopedPath } from "../bot-routing.js";
import { friendlyErrorMessage, formatError, generateSessionId } from './utils.js';

/** Owns attachments behavior and its temporary runtime state. */
export function createAttachmentsController({
  state,
  platform: platformPort,
  scope: runtimeScope,
  persistence: statePort,
  settings: settingsPort,
  status: statusPort,
  bubble: bubblePort,
  music: musicPort,
  chat: chatPort,
  input: inputPort,
  backend: backendPort,
  voice: voicePort,
  window: windowPort
}) {
  const { document } = platformPort;

  function attachmentDraftScope() {
    return {
      backendUrl: state.backendUrl,
      botId: state.boundBotId,
      sessionId: state.sessionId,
      realUserId: statePort.getProfileUserId(),
      characterPackId: String(state.characterPackId || "").trim(),
      revision: runtimeScope.capture().revision,
    };
  }

  const pendingAttachments = createPendingAttachments({ readScope: attachmentDraftScope,
    changed: () => { renderPendingAttachments(); settingsPort.scheduleSettingsSnapshot(); } });

  const enqueueWorkspaceImport = createWorkspaceImportQueue({
    readScope: attachmentDraftScope,
    run: (paths, scope) => typeof paths[0] === "object" ? uploadClipboardAttachments(paths, scope) : platformPort.invoke("import_dropped_files", {
      paths,
      backendUrl: scope.backendUrl,
      botId: scope.botId,
      userId: scope.sessionId,
      sessionId: scope.sessionId,
      realUserId: scope.realUserId,
      characterPackId: scope.characterPackId,
    }),
  });

  let desktopFileDeliveryHandled = new Set();

  function applyPayloadFileDeliveries(payload) {
    const events = Array.isArray(payload?.tool_events) ? payload.tool_events : [];
    for (const event of events) {
      const type = String(event?.type || "").trim().toLowerCase();
      if (type === "file_ready" || type === "generated_file_ready") {
        void handleDesktopFileDeliveryEvent(event);
      }
    }
  }

  function applyPayloadBrowserEvents(payload) {
    const events = Array.isArray(payload?.tool_events) ? payload.tool_events : [];
    for (const event of events) {
      const type = String(event?.type || "").trim().toLowerCase();
      if (type === "browser_open_requested") {
        void handleBrowserOpenEvent(event);
      }
    }
  }

  const browserOpenHandled = new Set();

  async function handleBrowserOpenEvent(event) {
    if (!event || typeof event !== "object") return;
    const url = normalizePublicBrowserUrl(event.url);
    if (!url) {
      statusPort.setRuntimeStatus("浏览器打开请求被拦截：网址不安全", { mode: "error" });
      bubblePort.showBubbleText("这个网址看起来不适合直接打开，我先拦住了。", {
        transient: true,
        durationMs: 2600,
        kind: "error"
      });
      return;
    }
    const key = `browser:${url}`;
    if (browserOpenHandled.has(key)) return;
    browserOpenHandled.add(key);
    const label = String(event.label || event.title || "").trim() || url;
    const result = await platformPort.tauriCall("open_external_url", { url }, { quiet: true });
    if (result !== null) {
      statusPort.setRuntimeStatus(`已打开网页：${label}`, { mode: "idle" });
    } else {
      statusPort.setRuntimeStatus("打开网页失败", { mode: "error" });
      bubblePort.showBubbleText("网页没有打开成功，可能是桌面端暂时接不上系统浏览器。", {
        transient: true,
        durationMs: 2600,
        kind: "error"
      });
    }
  }

  function normalizePublicBrowserUrl(value) {
    const raw = String(value || "").trim();
    if (!raw || raw.length > 1600 || /\s|[\u0000-\u001f]/.test(raw)) return "";
    let parsed;
    try {
      parsed = new URL(raw);
    } catch {
      return "";
    }
    if (!["http:", "https:"].includes(parsed.protocol)) return "";
    if (parsed.username || parsed.password) return "";
    const host = parsed.hostname.toLowerCase();
    if (!host || host === "localhost" || host.endsWith(".local")) return "";
    if (/^(127\.|10\.|0\.|169\.254\.|192\.168\.)/.test(host)) return "";
    const private172 = host.match(/^172\.(\d+)\./);
    if (private172 && Number(private172[1]) >= 16 && Number(private172[1]) <= 31) return "";
    if (host === "::1" || host.startsWith("fe80:") || host.startsWith("fc") || host.startsWith("fd")) return "";
    return parsed.href;
  }

  async function handleDesktopFileDeliveryEvent(event) {
    if (!event || typeof event !== "object" || !event.send_to_user) return;
    const fileRef = resolveDesktopFileDeliveryRef(event);
    if (!fileRef) return;
    const action = normalizeDesktopDeliveryAction(
      event.delivery_action || event.desktop_delivery?.action || event.handoff_action
    );
    const key = desktopFileDeliveryEventKey(event, fileRef, action);
    if (!key || desktopFileDeliveryHandled.has(key)) return;
    desktopFileDeliveryHandled.add(key);

    await musicPort.notifyWorkspaceRefresh();
    if (!action) {
      statusPort.setRuntimeStatus(`文件已放到手边：${fileRef.name || fileRef.handle || "成果"}`, { mode: "idle" });
      return;
    }

    // M66-D: Use handle-based open_workspace_item; no longer call /location to get
    // an absolute path. Tauri downloads bytes from /content and acts on staged file.
    const handle = String(fileRef.handle || event.desktop_delivery?.handle || "").trim();
    const itemType = String(fileRef.itemType || "generated").trim();
    const displayName = fileRef.name || handle || "文件";

    if (!handle) {
      statusPort.setRuntimeStatus("文件已生成，但暂时找不到 handle", { mode: "error" });
      bubblePort.showBubbleText("文件做好了，但找不到引用。", { transient: true, durationMs: 2400, kind: "error" });
      return;
    }

    const workspaceItemParams = {
      handle,
      backendUrl: state.backendUrl,
      botId: state.boundBotId,
      itemType,
      action,
      userId: state.sessionId || "",
      sessionId: state.sessionId || "",
      realUserId: statePort.getProfileUserId(),
      fileName: buildDesktopDeliveryFileName(fileRef) || "",
    };

    if (action === "open") {
      const result = await platformPort.tauriCall("open_workspace_item", workspaceItemParams, { quiet: true });
      announceDesktopFileDeliveryResult(result !== null, `已打开：${displayName}`, "文件做好了，我打开给你看啦。", "打开文件失败了。");
    } else if (action === "reveal") {
      const result = await platformPort.tauriCall("open_workspace_item", workspaceItemParams, { quiet: true });
      announceDesktopFileDeliveryResult(result !== null, `已定位：${displayName}`, "文件位置打开啦。", "打开文件位置失败了。");
    } else if (action === "save_desktop") {
      const result = await platformPort.tauriCall("open_workspace_item", workspaceItemParams, { quiet: true });
      const exportedPath = String(result?.path || "").trim();
      announceDesktopFileDeliveryResult(
        result !== null,
        exportedPath ? `已保存到桌面：${exportedPath}` : `已保存到桌面：${displayName}`,
        "文件已经放到桌面 Akane Outputs 里啦。",
        "保存到桌面失败了。"
      );
    } else if (action === "copy_path") {
      // copy_path is only meaningful locally; gracefully skip if handle has no local path.
      announceDesktopFileDeliveryResult(false, "", "", "当前模式不支持复制本地路径。");
    }
  }

  function resolveDesktopFileDeliveryRef(event) {
    const type = String(event?.type || "").trim().toLowerCase();
    const raw = type === "generated_file_ready"
      ? event.generated_file
      : event.file || event.generated_file;
    if (!raw || typeof raw !== "object") return null;
    const sourceType = String(raw.source_type || (type === "generated_file_ready" ? "generated" : "")).trim().toLowerCase();
    const handle = String(raw.handle || raw.generated_handle || raw.attachment_handle || "").trim();
    return {
      itemType: sourceType === "generated" || raw.generated_id || raw.generated_handle ? "generated" : "attachment",
      handle,
      path: String(raw.absolute_path || raw.path || raw.file_path || "").trim(),
      name: String(raw.name || raw.output_title || raw.title || raw.origin_name || handle || "").trim(),
      format: String(raw.file_ext || raw.output_format || raw.format || "").trim().replace(/^\.+/, "")
    };
  }

  function desktopFileDeliveryEventKey(event, fileRef, action) {
    const id = String(
      fileRef.path ||
        event?.file?.source_id ||
        event?.generated_file?.generated_id ||
        fileRef.handle ||
        ""
    ).trim();
    return id ? `${String(event?.type || "")}:${action || "workspace"}:${id}` : "";
  }

  function normalizeDesktopDeliveryAction(value) {
    const text = String(value || "").trim().toLowerCase().replace(/-/g, "_");
    if (["open", "open_file"].includes(text)) return "open";
    if (["reveal", "show", "show_in_folder", "show_folder", "folder", "location"].includes(text)) return "reveal";
    if (["save_desktop", "export_desktop", "save_to_desktop", "desktop"].includes(text)) return "save_desktop";
    if (["copy_path", "path", "clipboard"].includes(text)) return "copy_path";
    return "";
  }

  function buildDesktopDeliveryFileName(fileRef) {
    const name = String(fileRef?.name || fileRef?.handle || "akane-output").trim() || "akane-output";
    const format = String(fileRef?.format || "").trim().replace(/^\.+/, "");
    if (!format || name.toLowerCase().endsWith(`.${format.toLowerCase()}`)) return name;
    return `${name}.${format}`;
  }

  function announceDesktopFileDeliveryResult(ok, statusText, bubbleText, errorText) {
    if (ok) {
      statusPort.setRuntimeStatus(statusText, { mode: "idle" });
      if (bubbleText) bubblePort.showBubbleText(bubbleText, { transient: true, durationMs: 2600, kind: "status" });
    } else {
      statusPort.setRuntimeStatus(errorText || "文件交付失败", { mode: "error" });
      if (errorText) bubblePort.showBubbleText(errorText, { transient: true, durationMs: 2600, kind: "error" });
    }
  }

  async function handleDroppedFiles(paths) {
    const files = Array.isArray(paths) ? paths.map((item) => String(item || "")).filter(Boolean) : [];
    if (!files.length) return;
    statusPort.setRuntimeStatus("正在接收附件…", { mode: chatPort.sending ? "working" : "idle" });
    try {
      const importResult = await importDroppedFilesToWorkspace(files);
      if (importResult?.status === "cancelled") return;
      const imported = Array.isArray(importResult?.items) ? importResult.items.length : Number(importResult?.imported || 0);
      if (imported > 0) {
        const skipped = (importResult?.skipped || []).filter(item => item.reason !== "duplicate_source").length;
        statusPort.setRuntimeStatus(
          skipped > 0 ? `待发送：${imported} 个附件，跳过 ${skipped} 个` : `待发送：${imported} 个附件`,
          { mode: "idle" }
        );
        if (!chatPort.sending && !bubblePort.replyDisplayActive) bubblePort.showBubbleText(`收到 ${imported} 个附件，可以补充问题后发送。音频不会自动播放。`, {
          transient: true,
          durationMs: 2400,
          kind: "status"
        });
        inputPort.showChatInput();
        return;
      }

      bubblePort.showBubbleText("这个文件暂时还不能放进手边。", {
        transient: true,
        durationMs: 2400,
        kind: "status"
      });
      statusPort.setRuntimeStatus("拖入的文件不是当前支持的类型", { mode: "error" });
    } catch (error) {
      const importError = friendlyErrorMessage(formatError(error));
      bubblePort.showBubbleText(importError || "这个文件暂时还不能放进手边。", {
        transient: true,
        durationMs: 2400,
        kind: "status"
      });
      statusPort.setRuntimeStatus(importError || "拖入的文件不是当前支持的类型", { mode: "error" });
    }
  }

  async function importDroppedFilesToWorkspace(paths) {
    const normalizedPaths = Array.isArray(paths)
      ? paths.map((item) => String(item || "").trim()).filter(Boolean)
      : [];
    if (!normalizedPaths.length) return { ok: false, reason: "no_paths", imported: 0 };
    return importAttachmentEntries(normalizedPaths);
  }

  async function importClipboardAttachments(files) {
    if (!Array.isArray(files) || !files.length) return { ok: false, reason: "no_files" };
    return importAttachmentEntries(files);
  }

  async function importAttachmentEntries(entries) {
    if (!state.sessionId) {
      state.sessionId = generateSessionId();
      statePort.scheduleSave(0);
      settingsPort.scheduleSettingsSnapshot();
      void backendPort.ensureBackendSession();
    }
    const requestScope = runtimeScope.capture();
    const draftScope = pendingAttachments.scope();
    const finishImport = pendingAttachments.beginImport();
    try {
      const result = await enqueueWorkspaceImport(entries);
      if (!runtimeScope.isCurrent(requestScope)) return { ok: false, status: "cancelled", reason: "attachment_scope_changed" };
      if (result?.status === "cancelled") return result;
      if (!result?.ok && !Number(result?.imported || 0)) {
        throw new Error(musicPort.summarizeWorkspaceImportSkipped(result) || result?.reason || "没有可导入的文件");
      }
      pendingAttachments.add(result.items, draftScope);
      await musicPort.notifyWorkspaceRefresh();
      if (!runtimeScope.isCurrent(requestScope)) return { ok: false, status: "cancelled", reason: "attachment_scope_changed" };
      return result;
    } finally {
      finishImport();
    }
  }

  async function uploadClipboardAttachments(files, scope) {
    const requestScope = runtimeScope.capture();
    if (files.length > 40) throw new Error("too_many_files");
    const form = new FormData();
    form.append("user_id", scope.sessionId);
    form.append("session_id", scope.sessionId);
    form.append("real_user_id", scope.realUserId);
    form.append("character_pack_id", scope.characterPackId);
    let total = 0;
    for (const file of files) {
      const dataUrl = String(file?.dataUrl || "");
      const match = /^data:([^;,]*);base64,([A-Za-z0-9+/=]+)$/.exec(dataUrl);
      if (!match || match[2].length > 12 * 1024 * 1024) throw new Error("invalid_clipboard_file");
      const raw = atob(match[2]);
      total += raw.length;
      if (raw.length > 8 * 1024 * 1024 || total > 20 * 1024 * 1024) throw new Error("clipboard_files_too_large");
      form.append("files", new Blob([Uint8Array.from(raw, ch => ch.charCodeAt(0))], { type: match[1] || "application/octet-stream" }), String(file.name || "clipboard.png"));
    }
    const url = new URL(botScopedPath(scope.botId, "/desktop-pet/workspace/import-file"), `${scope.backendUrl.replace(/\/+$/, "")}/`);
    const response = await platformPort.backendFetch(url.toString(), { method: "POST", body: form, cache: "no-store", connectTimeout: 30_000 });
    if (!runtimeScope.isCurrent(requestScope)) return { ok: false, status: "cancelled", reason: "attachment_scope_changed" };
    if (!response.ok) throw new Error(await voicePort.readBackendErrorMessage(response, `HTTP ${response.status}`));
    return response.json();
  }

  function renderPendingAttachments() {
    const container = document.querySelector("#pending-attachments");
    if (!container) return;
    const items = pendingAttachments.list();
    container.replaceChildren();
    container.hidden = !items.length && !pendingAttachments.importing();
    if (pendingAttachments.importing()) {
      const status = document.createElement("div");
      status.textContent = "正在接收附件…";
      status.className = "pending-attachment";
      container.append(status);
    }
    for (const item of items) {
      const row = document.createElement("div");
      row.className = "pending-attachment";
      const label = document.createElement("span");
      label.textContent = `${item.kind === "audio" ? "音频材料" : "附件"} · ${item.title}`;
      label.title = item.title;
      row.append(label);
      if (item.kind === "audio") {
        const play = document.createElement("button");
        play.type = "button";
        play.textContent = "播放";
        play.addEventListener("click", () => { void musicPort.playWorkspaceAudioItem(item); });
        row.append(play);
      }
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "移除";
      remove.addEventListener("click", () => pendingAttachments.remove(item.attachmentId));
      row.append(remove);
      container.append(row);
    }
    windowPort.scheduleNativeHitTestSync();
  }

  function resetFileDeliveryTracking() { desktopFileDeliveryHandled.clear(); }

  return {
    resetFileDeliveryTracking,
    attachmentDraftScope,
    get pendingAttachments() { return pendingAttachments; },
    applyPayloadFileDeliveries,
    applyPayloadBrowserEvents,
    handleBrowserOpenEvent,
    handleDesktopFileDeliveryEvent,
    handleDroppedFiles,
    importDroppedFilesToWorkspace,
    importClipboardAttachments,
    renderPendingAttachments
  };
}
