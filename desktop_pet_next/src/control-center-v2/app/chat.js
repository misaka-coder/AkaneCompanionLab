import { createChatAutoPreviews } from "../chat-auto-previews.js";
import { chatTimeline } from "../chat-timeline.js";
import { prependedHistoryScrollTop } from "../chat-history.js";
import { chatScope, friendlyError } from "./scope.js";

export function createChatController({ root, store, bridge, actions, autoPreviews, documentRef = document }) {
  const previews = autoPreviews ?? createChatAutoPreviews({ root, open: item => bridge.previewChatImage(item) });
  let draft = "", draftRevision = 0, scrollTop = 0, wasAtBottom = true, lastMessageId = "", prependAnchor = null;
  let owner = chatScope(store.getState().viewModel), renderedOwner = owner, epoch = 0, loading = null, disposed = false;
  const nearBottom = viewport => viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 72;
  const hasNewMessage = state => {
    const next = chatTimeline(state.viewModel?.chat).at(-1)?.id || "";
    return Boolean(lastMessageId && next && next !== lastMessageId);
  };
  function synchronizeScope(viewModel) {
    const next = chatScope(viewModel);
    if (next === owner) return;
    owner = next; epoch += 1; draftRevision += 1;
    draft = ""; scrollTop = 0; wasAtBottom = true; lastMessageId = ""; prependAnchor = null; loading = null;
  }
  function handleInput(event) {
    if (!event.target.matches("[data-chat-input]")) return;
    draft = event.target.value; draftRevision += 1;
  }
  async function send(form) {
    const input = form.querySelector("[data-chat-input]");
    draft = String(input?.value ?? draft);
    const text = draft.trim();
    if (!text && !(store.getState().viewModel?.chat?.pendingAttachments || []).length) return null;
    const requestedEpoch = epoch, requestedRevision = draftRevision;
    const isCurrent = () => !disposed && requestedEpoch === epoch;
    const result = await actions.runAction("chat.send", { text }, { isCurrent });
    if (!result || !isCurrent() || result.status === "stale" || requestedRevision !== draftRevision) return result;
    if (result.ok) draft = "";
    const latest = root.querySelector("[data-chat-input]");
    if (latest) { latest.value = draft; if (!result.ok) latest.focus(); }
    return result;
  }
  function handleSubmit(event) {
    const form = event.target.closest("[data-chat-form]");
    if (!form) return false;
    event.preventDefault();
    if (!event.submitter?.disabled) void send(form);
    return true;
  }
  function handleKeydown(event) {
    if (!event.target.matches("[data-chat-input]") || event.isComposing) return;
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault(); event.target.closest("form")?.requestSubmit();
    }
  }
  function handleClick(event) {
    const preview = event.target.closest("[data-chat-image-preview]");
    if (preview) {
      if (!preview.disabled) void bridge.previewChatImage({ handle: preview.dataset.previewHandle, itemType: preview.dataset.previewType }, preview.dataset.chatImagePreview);
      return true;
    }
    const job = event.target.closest("[data-chat-job-control]");
    if (job) {
      if (!job.disabled) void actions.runAction("chat.jobControl", { jobId: job.dataset.jobId, action: job.dataset.chatJobControl });
      return true;
    }
    const file = event.target.closest("[data-chat-file-action]");
    if (file) {
      const card = file.closest("[data-file-handle]");
      if (!file.disabled && card) void actions.runAction("chat.fileAction", { action: file.dataset.chatFileAction,
        handle: card.dataset.fileHandle, itemType: card.dataset.fileType, title: card.dataset.fileTitle, format: card.dataset.fileFormat });
      return true;
    }
    const attachment = event.target.closest("[data-chat-attachment-remove], [data-chat-attachment-play]");
    if (attachment) {
      const remove = attachment.hasAttribute("data-chat-attachment-remove");
      if (!attachment.disabled) void actions.runAction(remove ? "chat.removeAttachment" : "chat.playAttachment", {
        attachmentId: remove ? attachment.dataset.chatAttachmentRemove : attachment.dataset.chatAttachmentPlay });
      return true;
    }
    const older = event.target.closest("button[data-chat-load-older]");
    if (older) { if (!older.disabled) void loadOlderChatHistory(); return true; }
    const jump = event.target.closest("[data-chat-jump-latest]");
    if (!jump) return false;
    const viewport = root.querySelector("[data-chat-viewport]");
    viewport?.scrollTo({ top: viewport.scrollHeight, behavior: "smooth" });
    jump.hidden = true; wasAtBottom = true;
    return true;
  }
  function attachPaths(paths) { if (store.getState().activePage === "chat") void actions.runAction("chat.attach", { paths }); }
  function handlePaste(event) {
    if (!event.target.closest("[data-chat-input]")) return;
    const files = Array.from(event.clipboardData?.files || []);
    if (files.length) { event.preventDefault(); void actions.runAction("chat.attach", { browserFiles: files }); }
  }
  function handleDragover(event) { if (event.target.closest(".chat-workspace")) event.preventDefault(); }
  function handleDrop(event) {
    if (!event.target.closest(".chat-workspace")) return;
    event.preventDefault();
    const files = Array.from(event.dataTransfer?.files || []);
    if (files.length) void actions.runAction("chat.attach", { browserFiles: files });
  }
  function handleError(event) {
    if (event.target.matches?.("img[data-preview-handle]")) void bridge.previewChatImage({
      handle: event.target.dataset.previewHandle, itemType: event.target.dataset.previewType }, "failed");
  }
  function handleScroll(event) {
    if (event.target.matches?.(".chat-attachments")) previews.refresh();
    if (!event.target.matches?.("[data-chat-viewport]")) return;
    const viewport = event.target;
    scrollTop = viewport.scrollTop; wasAtBottom = nearBottom(viewport);
    const jump = root.querySelector("[data-chat-jump-latest]");
    if (jump && wasAtBottom) jump.hidden = true;
    if (viewport.scrollTop <= 72) void loadOlderChatHistory(viewport);
    previews.refresh();
  }
  async function loadOlderChatHistory(viewport = root.querySelector("[data-chat-viewport]")) {
    const chat = store.getState().viewModel?.chat;
    if (disposed || !chat?.history?.hasMore || loading) return null;
    const beforeSeq = Number(chat.history.nextBeforeSeq);
    if (!Number.isFinite(beforeSeq) || beforeSeq <= 0) return null;
    const sessionId = String(chat.sessionId || ""), requestedEpoch = epoch;
    const request = { scope: chatScope(store.getState().viewModel) };
    loading = request;
    prependAnchor = viewport ? { scope: request.scope, sessionId, firstMessageId: String(chat.messages?.[0]?.id || ""),
      scrollHeight: viewport.scrollHeight, scrollTop: viewport.scrollTop } : null;
    store.patch({ chatHistory: { phase: "loading", error: "", sessionId } });
    let result;
    try { result = await bridge.loadOlderChatMessages({ beforeSeq, limit: 60 }); }
    catch (error) { result = { ok: false, status: "failed", reason: friendlyError(error) }; }
    if (disposed || requestedEpoch !== epoch || request.scope !== chatScope(store.getState().viewModel) || loading !== request) return result;
    loading = null;
    if (result?.status === "stale") {
      prependAnchor = null;
      store.patch({ chatHistory: { phase: "idle", error: "", sessionId } });
    } else if (result?.ok) store.patch({ chatHistory: { phase: "idle", error: "", sessionId } });
    else {
      prependAnchor = null;
      store.patch({ chatHistory: { phase: "failed", sessionId,
        error: result?.status === "no-more-history" ? "已经没有更早消息" : result?.reason || "更早消息暂时没有加载出来" } });
    }
    return result;
  }
  function beforeRender() {
    if (renderedOwner !== owner) return null;
    const viewport = root.querySelector("[data-chat-viewport]");
    if (viewport) { scrollTop = viewport.scrollTop; wasAtBottom = nearBottom(viewport); }
    const input = root.querySelector("[data-chat-input]");
    const selection = input && documentRef.activeElement === input ? { start: input.selectionStart, end: input.selectionEnd } : null;
    if (input) draft = input.value;
    return selection;
  }
  function afterRender(state, selection) {
    renderedOwner = owner;
    const input = root.querySelector("[data-chat-input]");
    if (input) {
      input.value = draft;
      if (selection && !input.disabled) { input.focus({ preventScroll: true }); input.setSelectionRange(selection.start, selection.end); }
    }
    const viewport = root.querySelector("[data-chat-viewport]");
    if (!viewport) return;
    const firstId = String(state.viewModel?.chat?.messages?.[0]?.id || "");
    const nextLastId = chatTimeline(state.viewModel?.chat).at(-1)?.id || "";
    const prepended = prependAnchor && prependAnchor.scope === owner && prependAnchor.firstMessageId && firstId && prependAnchor.firstMessageId !== firstId;
    if (prepended) {
      viewport.scrollTop = prependedHistoryScrollTop(prependAnchor, viewport.scrollHeight);
      scrollTop = viewport.scrollTop; wasAtBottom = nearBottom(viewport); prependAnchor = null;
    } else if (!lastMessageId || wasAtBottom) { viewport.scrollTop = viewport.scrollHeight; wasAtBottom = true; }
    else {
      viewport.scrollTop = scrollTop;
      const jump = root.querySelector("[data-chat-jump-latest]");
      if (jump) jump.hidden = !hasNewMessage(state);
    }
    lastMessageId = nextLastId;
    previews.refresh();
  }
  function dispose() { disposed = true; epoch += 1; loading = null; previews.dispose(); }
  return { handleClick, handleInput, handleSubmit, handleKeydown, handlePaste, handleDragover, handleDrop, handleError,
    handleScroll, attachPaths, send, loadOlderChatHistory, beforeRender, afterRender, hasNewMessage, synchronizeScope,
    refreshPreviews: previews.refresh, dispose };
}
