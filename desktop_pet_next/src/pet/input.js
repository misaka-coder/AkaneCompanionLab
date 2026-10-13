import { CHAT_INPUT_IDLE_HIDE_MS, INPUT_HISTORY_LIMIT } from './constants.js';
import { readCssPx } from './utils.js';

/** Owns input behavior and its temporary runtime state. */
export function createInputController({
  els,
  lifecycle,
  visualRenderer,
  interaction: interactionPort,
  window: windowPort,
  attachments: attachmentsPort,
  chat: chatPort,
  voice: voicePort
}) {
  let inputHistory = [];

  let inputHistoryIndex = -1;

  let inputHistoryDraft = "";

  let applyingInputHistory = false;

  let chatInputIdleTimer = 0;

  function showChatInput() {
    interactionPort.cancelLocalClick();
    windowPort.closeMenu();
    els.chatForm.hidden = false;
    visualRenderer?.syncIdleAnimation();
    attachmentsPort.renderPendingAttachments();
    autoResizeChatInput();
    scheduleChatInputAutoHide();
    windowPort.scheduleNativeHitTestSync({ force: true });
    lifecycle.setTimeout(() => {
      els.chatInput.focus();
      els.chatInput.select();
    }, 0);
  }

  function submitChatInput() {
    const text = els.chatInput.value;
    hideChatInput({ clear: true });
    void chatPort.sendMessage(text);
  }

  function hideChatInput({ clear = false } = {}) {
    if (els.chatForm.hidden && !clear) return;
    clearChatInputAutoHide();
    els.chatForm.hidden = true;
    visualRenderer?.syncIdleAnimation();
    if (clear) els.chatInput.value = "";
    resetInputHistoryCursor();
    autoResizeChatInput();
    els.chatInput.blur();
    windowPort.scheduleNativeHitTestSync({ force: true });
  }

  function scheduleChatInputAutoHide(delayMs = CHAT_INPUT_IDLE_HIDE_MS) {
    lifecycle.clearTimeout(chatInputIdleTimer);
    if (els.chatForm.hidden) return;
    chatInputIdleTimer = lifecycle.setTimeout(() => {
      chatInputIdleTimer = 0;
      if (els.chatForm.hidden) return;
      if (chatPort.sending || ["opening", "recording", "processing"].includes(voicePort.voiceInputState)) {
        scheduleChatInputAutoHide();
        return;
      }
      if (els.chatInput.value.trim()) return;
      if (els.chatForm.matches?.(":hover")) {
        scheduleChatInputAutoHide();
        return;
      }
      hideChatInput();
    }, delayMs);
  }

  function clearChatInputAutoHide() {
    lifecycle.clearTimeout(chatInputIdleTimer);
    chatInputIdleTimer = 0;
  }

  function setChatInputText(text, { append = false } = {}) {
    const value = String(text || "").trim();
    if (!value) return;
    const current = els.chatInput.value.trim();
    els.chatInput.value = append && current ? `${current} ${value}` : value;
    showChatInput();
    autoResizeChatInput();
    scheduleChatInputAutoHide();
  }

  function shouldNavigateInputHistory(event, direction) {
    if (event.ctrlKey || event.altKey || event.metaKey || !inputHistory.length) return false;
    const input = els.chatInput;
    const value = input.value || "";
    const start = Number(input.selectionStart ?? value.length);
    const end = Number(input.selectionEnd ?? value.length);
    if (start !== end) return false;
    if (!value.includes("\n")) return true;
    return direction === "up" ? start === 0 : end === value.length;
  }

  function navigateInputHistory(direction) {
    if (!inputHistory.length) return;
    if (inputHistoryIndex === -1) inputHistoryDraft = els.chatInput.value;

    if (direction === "up") {
      inputHistoryIndex =
        inputHistoryIndex === -1 ? inputHistory.length - 1 : Math.max(0, inputHistoryIndex - 1);
    } else if (inputHistoryIndex >= inputHistory.length - 1) {
      inputHistoryIndex = -1;
    } else {
      inputHistoryIndex += 1;
    }

    applyChatInputValue(inputHistoryIndex === -1 ? inputHistoryDraft : inputHistory[inputHistoryIndex]);
  }

  function applyChatInputValue(value) {
    applyingInputHistory = true;
    els.chatInput.value = String(value || "");
    autoResizeChatInput();
    const end = els.chatInput.value.length;
    els.chatInput.setSelectionRange(end, end);
    applyingInputHistory = false;
  }

  function rememberInputHistory(text) {
    const value = String(text || "").trim();
    if (!value) return;
    inputHistory = inputHistory.filter((item) => item !== value);
    inputHistory.push(value);
    if (inputHistory.length > INPUT_HISTORY_LIMIT) {
      inputHistory = inputHistory.slice(-INPUT_HISTORY_LIMIT);
    }
    resetInputHistoryCursor();
  }

  function resetInputHistoryCursor() {
    inputHistoryIndex = -1;
    inputHistoryDraft = "";
  }

  function restoreFailedInput(text) {
    const value = String(text || "").trim();
    if (!value) return;
    showChatInput();
    els.chatInput.value = value;
    autoResizeChatInput();
    lifecycle.setTimeout(() => {
      els.chatInput.focus();
      const end = els.chatInput.value.length;
      els.chatInput.setSelectionRange(end, end);
    }, 0);
  }

  function autoResizeChatInput() {
    const input = els.chatInput;
    if (!input) return;
    const minHeight = readCssPx("--chat-input-min-height", 42);
    const maxHeight = readCssPx("--chat-input-max-height", 96);
    input.style.height = `${minHeight}px`;
    const nextHeight = Math.min(input.scrollHeight, maxHeight);
    input.style.height = `${Math.max(minHeight, nextHeight)}px`;
  }

  return {
    get applyingInputHistory() { return applyingInputHistory; },
    showChatInput,
    submitChatInput,
    hideChatInput,
    scheduleChatInputAutoHide,
    setChatInputText,
    shouldNavigateInputHistory,
    navigateInputHistory,
    rememberInputHistory,
    resetInputHistoryCursor,
    restoreFailedInput,
    autoResizeChatInput
  };
}
