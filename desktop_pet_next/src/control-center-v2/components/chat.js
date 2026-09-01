import { escapeHtml, initial, safeStyleUrl } from "../dom.js";

export function renderChat(state) {
  const vm = state.viewModel;
  if (!vm) {
    return `<section class="empty-state glass-panel"><span>⋯</span><h2>正在读取对话</h2><p>连接成功后，这里会显示当前桌宠会话的真实消息。</p></section>`;
  }

  const chat = vm.chat || {};
  const character = vm.character || {};
  const activity = vm.activity || {};
  const portraitStyle = safeStyleUrl(character.visuals?.portrait || chat.characterAvatar);
  const sendState = state.actionStates["chat.send"];
  const sending = sendState?.phase === "pressed" || sendState?.phase === "pending" || activity.phase === "thinking" || activity.phase === "using_tool";
  const canSend = Boolean(vm.actions?.["chat.send"]?.available) && !sending;
  const messages = Array.isArray(chat.messages) ? chat.messages : [];
  const historyState = state.chatHistory || {};

  return `
    <section class="ccv2-chat" aria-labelledby="chat-heading">
      <aside class="chat-presence glass-panel">
        <div class="presence-light" aria-hidden="true"></div>
        <div class="presence-portrait-wrap">
          ${portraitStyle
            ? `<div class="presence-portrait" style="--portrait-image:${portraitStyle}" role="img" aria-label="${escapeHtml(character.displayName || "当前角色")}"></div>`
            : `<div class="presence-fallback">${initial(character.displayName)}</div>`}
        </div>
        <div class="presence-copy">
          <p class="eyebrow">LIVE PRESENCE</p>
          <h2>${escapeHtml(character.displayName || "当前角色")}</h2>
          <div class="presence-status" data-phase="${escapeHtml(activity.phase || "idle")}">
            <i aria-hidden="true"></i>
            <span><strong>${escapeHtml(activity.label || "空闲，随时可以开始")}</strong>${character.emotion ? `<small>${escapeHtml(character.emotion)}</small>` : ""}</span>
          </div>
          <p class="presence-note">这里跟随桌宠当前表情与回复状态，是对话中的状态镜头，不是第二个独立角色。</p>
        </div>
      </aside>

      <section class="chat-workspace glass-panel">
        <header class="chat-header">
          <div><p class="eyebrow">CURRENT SESSION</p><h2 id="chat-heading">${escapeHtml(chat.title || "当前对话")}</h2></div>
          <div class="chat-header-actions">
            <span class="session-chip">${chat.totalCount > messages.length ? `已加载 ${messages.length}/${chat.totalCount} 条` : `${messages.length} 条消息`}</span>
            <button class="chat-icon-button" type="button" data-action="chat.new" aria-label="新建对话" title="新建对话">＋</button>
          </div>
        </header>

        <div class="chat-message-viewport" data-chat-viewport tabindex="0" aria-label="聊天记录">
          <div class="chat-message-list">
            ${renderHistoryLoader(chat, historyState, messages.length)}
            ${messages.length ? messages.map((message) => renderMessage(message, chat, character)).join("") : renderEmptyChat(chat)}
            ${sending ? renderWorkingMessage(character, activity) : ""}
          </div>
          <button class="chat-new-message" type="button" data-chat-jump-latest hidden>有新消息 ↓</button>
        </div>

        <form class="chat-composer" data-chat-form>
          <div class="composer-input-wrap">
            <textarea data-chat-input rows="1" placeholder="和 ${escapeHtml(character.displayName || "桌宠")} 说点什么……" aria-label="输入消息"${canSend ? "" : " disabled"}></textarea>
            <small>Enter 发送 · Shift + Enter 换行</small>
          </div>
          ${sending
            ? `<button class="composer-send is-stop" type="button" data-action="chat.stop"><span>■</span><b>停止</b></button>`
            : `<button class="composer-send" type="submit"${canSend ? "" : " disabled"}><span>➤</span><b>${sendState?.phase === "confirmed" ? "已发送" : "发送"}</b></button>`}
        </form>
        ${!canSend && !sending ? `<p class="composer-hint">${escapeHtml(vm.actions?.["chat.send"]?.reason || "聊天发送暂不可用")}</p>` : ""}
      </section>
    </section>`;
}

function renderHistoryLoader(chat, historyState, messageCount) {
  if (!chat.history?.pageKnown || !messageCount) return "";
  if (historyState.phase === "loading") {
    return `<div class="chat-history-loader is-loading" role="status"><span class="typing-dots"><i></i><i></i><i></i></span>正在加载更早消息</div>`;
  }
  if (historyState.phase === "failed") {
    return `<div class="chat-history-loader is-error"><span>${escapeHtml(historyState.error || "更早消息暂时没有加载出来")}</span><button type="button" data-chat-load-older>重试</button></div>`;
  }
  if (chat.history.hasMore) {
    return `<div class="chat-history-loader"><button type="button" data-chat-load-older>加载更早消息</button></div>`;
  }
  return `<div class="chat-history-loader is-complete"><span>已到这轮对话的最早消息</span></div>`;
}

function renderMessage(message, chat, character) {
  const assistant = message.role === "assistant";
  const avatarStyle = assistant ? safeStyleUrl(chat.characterAvatar || character.visuals?.avatar) : "";
  const speaker = assistant ? chat.characterName || character.displayName || "桌宠" : "你";
  return `
    <article class="chat-message is-${assistant ? "assistant" : "user"}${message.intermediate ? " is-intermediate" : ""}" data-message-id="${escapeHtml(message.id)}">
      <div class="message-avatar${avatarStyle ? "" : " is-fallback"}"${avatarStyle ? ` style="--avatar-image:${avatarStyle}"` : ""}>${avatarStyle ? "" : initial(speaker)}</div>
      <div class="message-content">
        <div class="message-meta"><strong>${escapeHtml(speaker)}</strong><time>${escapeHtml(formatMessageTime(message.timestamp))}</time>${message.intermediate ? "<em>处理中</em>" : ""}</div>
        <p>${escapeHtml(message.content).replace(/\n/g, "<br>")}</p>
      </div>
    </article>`;
}

function renderWorkingMessage(character, activity) {
  const avatarStyle = safeStyleUrl(character.visuals?.avatar);
  return `
    <article class="chat-message is-assistant is-working" aria-live="polite">
      <div class="message-avatar${avatarStyle ? "" : " is-fallback"}"${avatarStyle ? ` style="--avatar-image:${avatarStyle}"` : ""}>${avatarStyle ? "" : initial(character.displayName)}</div>
      <div class="message-content"><div class="message-meta"><strong>${escapeHtml(character.displayName || "桌宠")}</strong><em>实时状态</em></div><p><span class="typing-dots"><i></i><i></i><i></i></span>${escapeHtml(activity.label || "正在处理请求")}</p></div>
    </article>`;
}

function renderEmptyChat(chat) {
  if (chat.loadStatus === "failed") {
    return `<div class="chat-empty is-error"><span>!</span><strong>对话记录暂时没有加载出来</strong><small>${escapeHtml(chat.loadError || "请刷新后重试")}</small></div>`;
  }
  const label = chat.matchesCurrentSession === false ? "正在切换会话" : "这轮对话还没有消息";
  return `<div class="chat-empty"><span>✦</span><strong>${label}</strong><small>从这里发送的内容会进入桌宠当前会话，并与桌宠气泡保持同一条回复主链。</small></div>`;
}

function formatMessageTime(timestamp) {
  const value = Number(timestamp);
  if (!Number.isFinite(value) || value <= 0) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  }).format(new Date(value * 1000));
}
