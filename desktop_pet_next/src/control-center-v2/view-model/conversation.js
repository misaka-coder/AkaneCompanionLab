import { asObject, finiteNumber, safeAssetUrl, text } from "./shared.js";

export function createChatViewModel(value, live, character, runtime) {
  const petState = asObject(live.state);
  return {
    ...normalizeChatSession(value, {
      sessionId: text(petState.sessionId), botId: text(petState.boundBotId),
      characterPackId: text(petState.characterPackId), characterName: character.displayName,
      characterAvatar: character.visuals.portrait, runtime
    }),
    pendingAttachments: Array.isArray(live.pendingAttachments) ? live.pendingAttachments : [],
    uploadingAttachments: Number(live.attachmentUploadCount) || 0
  };
}

function normalizeChatSession(value, options = {}) {
  const source = asObject(value);
  const runtime = asObject(options.runtime);
  const session = asObject(source.session);
  const messagePage = asObject(source.message_page);
  const expectedSessionId = text(options.sessionId);
  const actualSessionId = text(session.session_id) || text(session.sessionId);
  const matchesCurrentSession = runtime.status !== "switching" && (!expectedSessionId || !actualSessionId || expectedSessionId === actualSessionId)
    && (!text(source.bot_id) || !text(options.botId) || text(source.bot_id) === text(options.botId))
    && (!text(session.character_pack_id) || !text(options.characterPackId) || text(session.character_pack_id) === text(options.characterPackId));
  const messages = matchesCurrentSession
    ? (Array.isArray(source.messages) ? source.messages : [])
      .map((item) => normalizeChatMessage(item))
      .filter(Boolean)
    : [];
  const latestOutcome = matchesCurrentSession ? normalizeChatOutcome(source.latest_final_json) : null;
  return {
    sessionId: matchesCurrentSession ? actualSessionId || expectedSessionId : expectedSessionId,
    title: matchesCurrentSession
      ? text(session.display_title) || text(session.displayTitle) || "当前对话"
      : "正在切换会话",
    messages,
    latestOutcome,
    outputs: matchesCurrentSession ? (Array.isArray(source.workspace_outputs) ? source.workspace_outputs : []).slice(0, 12)
      .map(item => normalizeChatFile({ ...item, item_type: "generated" })) : [],
    outputsStatus: matchesCurrentSession ? text(source.workspace_outputs_status) : "switching",
    totalCount: matchesCurrentSession ? Math.max(messages.length, finiteNumber(session.message_count, messages.length)) : 0,
    history: {
      pageKnown: matchesCurrentSession && Object.keys(messagePage).length > 0,
      hasMore: matchesCurrentSession && Boolean(messagePage.has_more),
      nextBeforeSeq: matchesCurrentSession ? finiteNumber(messagePage.next_before_seq, 0) : 0
    },
    characterName: text(options.characterName) || "桌宠",
    characterAvatar: safeAssetUrl(text(options.characterAvatar)),
    hasHistory: messages.length > 0,
    matchesCurrentSession,
    loadStatus: matchesCurrentSession ? text(runtime.status) || "idle" : "switching",
    loadError: text(runtime.reason)
  };
}

function normalizeChatOutcome(value) {
  const source = asObject(value);
  if (!Object.keys(source).length) return null;
  const events = (Array.isArray(source.tool_events) ? source.tool_events : [])
    .slice(0, 16)
    .map((item) => {
      const entry = asObject(item);
      const type = text(entry.type);
      const toolType = text(entry.tool_type) || text(entry.capabilityId) || text(entry.capability_id);
      const status = text(entry.status) || (entry.is_error ? "error" : "completed");
      const jobId = text(entry.job_id) || text(entry.jobId);
      const explicitJobStatus = text(entry.job_status) || text(entry.jobStatus);
      const explicitControlState = text(entry.control_state) || text(entry.controlState);
      const jobStatus = explicitJobStatus || (jobId && type === "background_job_accepted" ? "queued" : "");
      const controlState = explicitControlState || (jobStatus === "queued" ? "running" : "");
      if (!toolType && !type) return null;
      return {
        type,
        toolType: toolType || outcomeEventLabel(type),
        status,
        reason: text(entry.reason),
        jobId,
        jobStatus,
        controlState,
        tone: outcomeTone(status, entry.is_error === true)
      };
    })
    .filter(Boolean);
  const rawStatus = text(source.status);
  const reason = text(source.reason) || text(source.error);
  const hasStructuredValue = Object.hasOwn(source, "value") || Object.hasOwn(source, "data") || Object.hasOwn(source, "content");
  const hasFailure = Boolean(reason) || events.some((item) => item.tone === "danger");
  const status = rawStatus || (hasFailure ? "failed" : events.length || hasStructuredValue ? "completed" : "");
  if (!status && !events.length && !hasStructuredValue) return null;
  return {
    status,
    statusLabel: outcomeStatusLabel(status),
    tone: outcomeTone(status, hasFailure),
    reason: outcomeReasonLabel(reason),
    events,
    hasStructuredValue,
    artifactCount: events.filter((item) => ["generated_file_ready", "file_ready", "artifact_ready"].includes(item.type)).length,
    toolCount: events.filter((item) => item.toolType && !["工具", "文件产物", "状态更新"].includes(item.toolType)).length
  };
}

function outcomeEventLabel(type) {
  if (["generated_file_ready", "file_ready", "artifact_ready"].includes(type)) return "文件产物";
  if (["state_update", "status_update"].includes(type)) return "状态更新";
  if (["capability_execution_result", "tool_completed"].includes(type)) return "工具调用";
  return "工具";
}

function outcomeTone(status, explicitFailure = false) {
  const normalized = text(status).toLowerCase();
  if (explicitFailure || ["error", "failed", "failure", "blocked", "rejected", "policy_failed", "policy_rejected", "resource_exhausted", "cancelled", "timed_out", "execution_unknown", "unavailable"].includes(normalized)) return "danger";
  if (["ask", "approval_required", "pending", "running", "accepted", "queued", "paused", "stopping", "cancelling"].includes(normalized)) return "warning";
  return "good";
}

function outcomeStatusLabel(status) {
  const normalized = text(status).toLowerCase();
  return {
    ok: "已完成",
    completed: "已完成",
    success: "已完成",
    succeeded: "已完成",
    executed: "已执行",
    accepted: "已排队",
    queued: "已排队",
    running: "进行中",
    paused: "已暂停",
    stopping: "停止中",
    cancelling: "停止中",
    pending: "等待处理",
    ask: "等待确认",
    approval_required: "等待确认",
    resource_exhausted: "资源不足",
    policy_rejected: "策略阻止",
    policy_failed: "策略失败",
    blocked: "已阻止",
    cancelled: "已取消",
    timed_out: "已超时",
    execution_unknown: "结果未确认",
    failed: "执行失败",
    error: "执行失败"
  }[normalized] || (status ? `状态：${status}` : "已完成");
}

function outcomeReasonLabel(reason) {
  const normalized = text(reason);
  return {
    execution_resource_limit_exceeded: "本次调用达到部署资源限额。",
    requires_user_decision: "需要先完成能力审批。",
    capability_disabled_by_policy: "当前能力策略没有允许这项调用。",
    plugin_capability_revoked: "插件能力已撤销，调用没有继续执行。"
  }[normalized] || normalized;
}

function normalizeChatMessage(value) {
  const source = asObject(value);
  const role = text(source.role).toLowerCase();
  const content = text(source.content);
  if ((!content && !source.attachments?.length) || !["user", "assistant"].includes(role)) return null;
  const metadata = asObject(source.memory_metadata);
  const timestamp = Number(source.timestamp);
  return {
    id: text(source.source_id) || `${role}-${text(source.seq_no)}-${timestamp || 0}`,
    seqNo: finiteNumber(source.seq_no, 0),
    role,
    content,
    timestamp: Number.isFinite(timestamp) && timestamp > 0 ? timestamp : 0,
    intermediate: text(metadata.turn_role).toLowerCase() === "intermediate",
    attachments: (Array.isArray(source.attachments) ? source.attachments : []).slice(0, 40).map(normalizeChatFile)
  };
}

function normalizeChatFile(item) {
  return { handle: text(item?.handle), attachmentId: text(item?.attachment_id),
    itemType: item?.item_type === "generated" ? "generated" : "attachment",
    title: text(item?.title) || "附件", kind: text(item?.kind), format: text(item?.format),
    status: text(item?.status) || "unavailable", canOpen: item?.can_open === true,
    sizeBytes: Math.max(0, finiteNumber(item?.size_bytes, 0)),
    durationSeconds: Math.max(0, finiteNumber(item?.duration_seconds, 0)),
    timestamp: Math.max(0, finiteNumber(item?.created_at || item?.updated_at, 0)) };
}

export function normalizeBotCatalog(value, activeBotId) {
  const source = asObject(value);
  const active = text(activeBotId) || text(source.defaultBotId) || text(source.default_bot_id);
  const items = (Array.isArray(source.bots) ? source.bots : []).map((item) => {
    const entry = asObject(item);
    const id = text(entry.botId) || text(entry.bot_id);
    if (!id) return null;
    return {
      id,
      displayName: text(entry.displayName) || text(entry.display_name) || id,
      available: entry.available !== false,
      isDefault: Boolean(entry.default),
      selected: id === active
    };
  }).filter(Boolean);
  return { activeId: active, items };
}
