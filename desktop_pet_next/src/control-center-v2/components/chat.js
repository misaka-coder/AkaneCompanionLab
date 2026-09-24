import { escapeHtml, initial, safeStyleUrl } from "../dom.js";
import { imagePreviewKey } from "../../control-center/chat-image-preview.js";
import { chatTimeline, chatFileKind, chatFileSize } from "../chat-timeline.js";

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
  const canSend = Boolean(vm.actions?.["chat.send"]?.available) && !["pressed", "pending"].includes(sendState?.phase);
  const canCompose = Boolean(vm.actions?.["chat.attach"]?.available || vm.actions?.["chat.send"]?.available);
  const messages = Array.isArray(chat.messages) ? chat.messages : [];
  const timeline = chatTimeline(chat);
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
            ${timeline.length ? timeline.map((message) => renderMessage(message, chat, character, state)).join("") : renderEmptyChat(chat)}
            ${chat.latestOutcome && (chat.latestOutcome.events?.length || chat.latestOutcome.reason || ["warning", "danger"].includes(chat.latestOutcome.tone)) ? renderChatOutcome(chat.latestOutcome, state) : ""}
            ${sending ? renderWorkingMessage(character, activity) : ""}
            ${chat.outputsStatus === "unavailable" ? `<small role="status">生成文件列表暂不可用，请刷新重试。</small>` : ""}
          </div>
          <button class="chat-new-message" type="button" data-chat-jump-latest hidden>有新消息 ↓</button>
        </div>

        <div class="chat-attachments" aria-label="待发送附件">${(chat.pendingAttachments || []).map(item => renderPendingAttachment(item, state)).join("")}${chat.uploadingAttachments ? `<small role="status">正在接收附件…</small>` : ""}</div>
        <form class="chat-composer" data-chat-form>
          <div class="composer-input-wrap">
            <textarea data-chat-input rows="1" placeholder="和 ${escapeHtml(character.displayName || "桌宠")} 说点什么……" aria-label="输入消息"${canCompose ? "" : " disabled"}></textarea>
            <small>Enter ${sending ? "追加" : "发送"} · Shift + Enter 换行 · 拖入文件 / 粘贴图片</small>
            <button class="composer-attach" type="button" data-action="chat.attach" title="添加图片、文件或音频，随本条消息发送给模型"${vm.actions?.["chat.attach"]?.available ? "" : " disabled"}>＋ 图片 / 文件 / 音频</button>
          </div>
          <div class="composer-actions">
            <button class="composer-send" type="submit"${canSend ? "" : " disabled"}><span>➤</span><b>${sending ? "追加" : "发送"}</b></button>
            ${sending ? `<button class="composer-send is-stop" type="button" data-action="chat.stop"><span>■</span><b>停止</b></button>` : ""}
          </div>
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

function renderMessage(message, chat, character, state) {
  const assistant = message.role === "assistant";
  const avatarStyle = assistant ? safeStyleUrl(chat.characterAvatar || character.visuals?.avatar) : "";
  const speaker = assistant ? chat.characterName || character.displayName || "桌宠" : "你";
  return `
    <article class="chat-message is-${assistant ? "assistant" : "user"}${message.intermediate ? " is-intermediate" : ""}" data-message-id="${escapeHtml(message.id)}">
      <div class="message-avatar${avatarStyle ? "" : " is-fallback"}"${avatarStyle ? ` style="--avatar-image:${avatarStyle}"` : ""}>${avatarStyle ? "" : initial(speaker)}</div>
      <div class="message-content">
        <div class="message-meta"><strong>${escapeHtml(speaker)}</strong><time>${escapeHtml(formatMessageTime(message.timestamp))}</time>${message.intermediate ? "<em>处理中</em>" : message.artifact ? "<em>生成的文件</em>" : ""}</div>
        ${message.content ? `<p>${escapeHtml(message.content).replace(/\n/g, "<br>")}</p>` : ""}
        ${(message.attachments || []).map(item => renderChatFile(item, state)).join("")}
      </div>
    </article>`;
}

function fileAttributes(item) {
  return `data-file-handle="${escapeHtml(item.handle)}" data-file-type="${escapeHtml(item.itemType || "attachment")}"
    data-file-title="${escapeHtml(item.title)}" data-file-format="${escapeHtml(item.format || "")}"`;
}

function renderPendingAttachment(item, state) {
  const kind = chatFileKind(item);
  return `<div class="chat-attachment is-${kind}" ${fileAttributes(item)}>
    ${kind === "image" ? renderImagePreview(item, state, true) : `<span class="chat-file-icon" aria-hidden="true">${kind === "audio" ? "♪" : "▤"}</span>`}
    <span class="attachment-caption"><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml([kind === "audio" ? "音频附件" : kind === "image" ? "图片" : "文件", chatFileSize(item.sizeBytes)].filter(Boolean).join(" · "))}</small></span>
    ${kind === "audio" ? `<button type="button" data-chat-attachment-play="${escapeHtml(item.attachmentId)}" aria-label="试听 ${escapeHtml(item.title)}">▶</button>` : ""}
    <button class="attachment-remove" type="button" data-chat-attachment-remove="${escapeHtml(item.attachmentId)}" aria-label="移除 ${escapeHtml(item.title)}" title="移除附件">×</button></div>`;
}

function renderChatFile(item, state) {
  const actionState = state.actionStates?.["chat.fileAction"]?.phase;
  const enabled = item.canOpen && item.handle && state.viewModel.actions?.["chat.fileAction"]?.available
    && !["pressed", "pending"].includes(actionState);
  const kind = chatFileKind(item);
  const button = (action, label, title = label) => `<button type="button" data-chat-file-action="${action}" title="${title}"${enabled ? "" : " disabled"}>${label}</button>`;
  const detail = [String(item.format || kind).toUpperCase(), chatFileSize(item.sizeBytes), !item.canOpen ? "暂不可用" : ""].filter(Boolean).join(" · ");
  return `<div class="chat-file-card is-${kind}" ${fileAttributes(item)}>
    ${kind === "image" ? renderImagePreview(item, state) : ""}
    <div class="chat-file-info">${kind !== "image" ? `<span class="chat-file-icon" aria-hidden="true">${kind === "audio" ? "♪" : "▤"}</span>` : ""}
      <span><strong title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</strong><small>${escapeHtml(detail)}</small></span>
      ${button(kind === "audio" ? "play" : "open", kind === "audio" ? "▶ 播放" : kind === "image" ? "查看图片" : "打开")}
    </div>
    <div class="chat-file-actions">${button("save_desktop", "保存到桌面")}${button("reveal", "定位文件")}</div>
  </div>`;
}

function renderImagePreview(item, state, pending = false) {
  if (!item.handle || chatFileKind(item) !== "image") return "";
  const preview = state.viewModel.chat?.previews?.[imagePreviewKey(item)] || {};
  const attrs = `data-preview-handle="${escapeHtml(item.handle)}" data-preview-type="${escapeHtml(item.itemType || "attachment")}"`;
  const available = state.viewModel.shell?.connected && (pending || item.canOpen);
  const disabled = available ? "" : " disabled";
  if (preview.status === "ready" && /^blob:/i.test(preview.url)) {
    return `<div class="chat-image-preview"><button type="button" class="chat-image-open" data-chat-file-action="open" title="查看原图"${disabled}><img src="${escapeHtml(preview.url)}" alt="${escapeHtml(item.title)}" ${attrs} decoding="async"></button></div>`;
  }
  if (preview.status === "loading") return `<div class="chat-image-preview is-placeholder" ${attrs} role="status"><span class="typing-dots"><i></i><i></i><i></i></span><small>图片加载中…</small></div>`;
  const reason = { preview_too_large: "图片超过 8MB，请用打开查看", preview_format_unsupported: "该格式请用打开查看",
    preview_timeout: "预览超时，可重试", preview_decode_failed: "图片无法解码，可重试" }[preview.reason] || "图片暂不可用，可重试或打开文件";
  return `<div class="chat-image-preview is-placeholder">${preview.status === "failed" ? `<small role="status">${reason}</small>` : ""}<button type="button" data-chat-image-preview="open" ${attrs}${!preview.status && available ? ' data-chat-auto-preview' : ''}${disabled}>${!available ? "图片暂不可用" : preview.status === "failed" ? "重试加载" : "加载图片"}</button></div>`;
}

function renderChatOutcome(outcome, state = {}) {
  const events = Array.isArray(outcome.events) ? outcome.events : [];
  const summary = [
    outcome.toolCount ? `${outcome.toolCount} 项工具/插件调用` : "本轮处理",
    outcome.artifactCount ? `${outcome.artifactCount} 个产物` : ""
  ].filter(Boolean).join(" · ");
  const detail = outcome.reason || (outcome.artifactCount ? `本轮生成 ${outcome.artifactCount} 个产物，具体内容见聊天中的文件卡片。` : outcome.hasStructuredValue ? "已产生结构化结果，具体内容见回复或文件卡片。" : "");
  return `<section class="chat-outcome is-${escapeHtml(outcome.tone || "good")}" aria-label="最近一次调用回执">
    <header class="chat-outcome-head"><span><strong>最近一次调用回执</strong><small>${escapeHtml(summary)}</small></span><em>${escapeHtml(outcome.statusLabel || "已完成")}</em></header>
    ${events.length ? `<div class="chat-outcome-tools">${events.map((event) => `<div class="chat-outcome-tool"><span class="chat-outcome-dot" aria-hidden="true"></span><span><strong>${escapeHtml(event.toolType)}</strong><small>${escapeHtml(event.reason || event.status || event.type || "已返回")}</small>${renderJobControl(event, state)}</span></div>`).join("")}</div>` : ""}
    ${detail ? `<p>${escapeHtml(detail)}</p>` : ""}
  </section>`;
}

function renderJobControl(event, state) {
  const jobId = String(event?.jobId || "").trim();
  if (!jobId) return "";
  const remembered = state.chatJobControls?.[jobId] || {};
  const controlAvailable = state.viewModel?.actions?.["chat.jobControl"]?.available !== false
    && state.viewModel?.shell?.connected !== false;
  if (!controlAvailable) return `<div class="chat-job-control" role="status"><small>重新连接后才能控制任务</small></div>`;
  const jobStatus = String(remembered.jobStatus || event.jobStatus || "").trim().toLowerCase();
  const controlState = String(remembered.controlState || event.controlState || "").trim().toLowerCase();
  const effectiveState = controlState || jobStatus;
  const actionState = state.actionStates?.["chat.jobControl"] || {};
  const pending = actionState.phase === "pressed" || actionState.phase === "pending";
  const pendingThisJob = pending && actionState.jobId === jobId;
  const buttons = [];
  if (effectiveState === "paused" || jobStatus === "paused") {
    buttons.push(jobControlButton(jobId, "resume", "恢复", pending));
    buttons.push(jobControlButton(jobId, "stop", "停止", pending));
  } else if (["queued", "running", "accepted"].includes(jobStatus) || (!jobStatus && ["running", "queued"].includes(controlState))) {
    buttons.push(jobControlButton(jobId, "pause", "暂停", pending));
    buttons.push(jobControlButton(jobId, "stop", "停止", pending));
  } else if (["stopping", "cancelling"].includes(effectiveState)) {
    return `<div class="chat-job-control" role="status"><small>任务正在停止…</small></div>`;
  } else {
    return remembered.error
      ? `<div class="chat-job-control is-error" role="status"><small>${escapeHtml(remembered.error)}</small></div>`
      : "";
  }
  const feedback = remembered.error ? `<small class="chat-job-control-error" role="status">${escapeHtml(remembered.error)}</small>` : "";
  const statusLabel = effectiveState === "paused" ? "已暂停" : jobStatus === "queued" ? "已排队" : "执行中";
  return `<div class="chat-job-control" data-job-id="${escapeHtml(jobId)}"><small>${pendingThisJob ? "正在更新任务…" : statusLabel}</small><div>${buttons.join("")}</div>${feedback}</div>`;
}

function jobControlButton(jobId, action, label, disabled) {
  return `<button type="button" data-chat-job-control="${action}" data-job-id="${escapeHtml(jobId)}"${disabled ? " disabled" : ""}>${label}</button>`;
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
