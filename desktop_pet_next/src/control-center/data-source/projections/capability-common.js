import {
  asArray,
  asObject,
  normalizeStringList,
  numberOrFallback,
  positiveNumber,
  safeDisplayBasename,
  stringValue
} from "../shared.js";

export function normalizeCapabilityCatalogEntries(catalog) {
  const payload = asObject(catalog);
  return asArray(payload.capabilities)
    .filter((entry) => entry && typeof entry === "object")
    .map((entry) => ({
      id: stringValue(entry.id),
      kind: stringValue(entry.kind),
      type: stringValue(entry.type),
      source: stringValue(entry.source),
      adapter: stringValue(entry.adapter),
      executionMode: stringValue(entry.executionMode),
      executionLocation: stringValue(entry.executionLocation),
      capabilityId: stringValue(entry.capabilityId),
      workflowId: stringValue(entry.workflowId),
      providerId: stringValue(entry.providerId),
      serverId: stringValue(entry.serverId),
      toolType: stringValue(entry.toolType),
      group: stringValue(entry.group),
      name: stringValue(entry.name),
      description: stringValue(entry.description),
      enabled: entry.enabled !== false,
      status: stringValue(entry.status || (entry.enabled === false ? "disabled" : "ready")),
      reason: stringValue(entry.reason),
      risk: stringValue(entry.risk),
      requiresConfirmation: Boolean(entry.requiresConfirmation),
      approvalMode: normalizeApprovalMode(entry.approvalMode, entry),
      approvalReason: stringValue(entry.approvalReason),
      configured: Boolean(entry.configured),
      configurable: Boolean(entry.configurable),
      executionReady: Boolean(entry.executionReady),
      endpoint: stringValue(entry.endpoint),
      defaultEndpoint: stringValue(entry.defaultEndpoint),
      transport: stringValue(entry.transport),
      commandName: safeDisplayBasename(entry.commandName),
      argsCount: positiveNumber(entry.argsCount),
      envCount: positiveNumber(entry.envCount),
      toolCount: positiveNumber(entry.toolCount),
      lastDiscovery: asObject(entry.lastDiscovery),
      inputSchema: asObject(entry.inputSchema),
      exposedToPrompt: Boolean(entry.exposedToPrompt),
      workflowPath: stringValue(entry.workflowPath),
      defaultWorkflowPath: stringValue(entry.defaultWorkflowPath),
      usedBy: normalizeStringList(entry.usedBy),
      toolTypes: normalizeStringList(entry.toolTypes),
      target: stringValue(entry.target),
      output: stringValue(entry.output),
      slots: asObject(entry.slots)
    }))
    .filter((entry) => entry.id || entry.name || entry.group || entry.type);
}

export function normalizeApprovalMode(mode, entry = {}) {
  const normalized = stringValue(mode);
  if (["trusted_auto_allow", "ask_each_time", "disabled"].includes(normalized)) return normalized;
  const status = stringValue(entry.status || (entry.enabled === false ? "disabled" : "ready"));
  const unavailableStatuses = new Set([
    "configured",
    "disabled",
    "error",
    "invalid_config",
    "misconfigured",
    "missing_config",
    "missing_executor",
    "missing_model",
    "missing_slot_mapping",
    "missing_workflow",
    "unavailable",
    "unreachable",
    "unsupported_platform"
  ]);
  if (entry.enabled === false || unavailableStatuses.has(status)) return "disabled";
  if (entry.requiresConfirmation || entry.risk === "high") return "ask_each_time";
  return "trusted_auto_allow";
}

export function approvalModeLabel(mode) {
  const labels = {
    trusted_auto_allow: "自动允许",
    ask_each_time: "每次确认",
    disabled: "暂不可用"
  };
  return labels[mode] || "待确认";
}

export function summarizeCapabilityCatalogEntries(entries) {
  const total = entries.length;
  const ready = entries.filter((entry) => isCapabilityReady(entry)).length;
  const needsAttention = entries.filter((entry) => !isCapabilityReady(entry)).length;
  return {
    total,
    ready,
    needsAttention,
    availability: total ? Math.round((ready / total) * 100) : 0
  };
}

export function capabilityEntryText(entry) {
  return [
    entry.id,
    entry.kind,
    entry.type,
    entry.source,
    entry.adapter,
    entry.executionMode,
    entry.toolType,
    entry.group,
    entry.name,
    ...entry.usedBy,
    ...entry.toolTypes
  ].join(" ").toLowerCase();
}

export function summarizeCapabilityModuleStatus(entries) {
  if (!entries.length) return { label: "可用", tone: "ready" };
  const ready = entries.filter((entry) => isCapabilityReady(entry)).length;
  if (ready === entries.length) return { label: "可用", tone: "ready" };
  if (ready > 0) return { label: "部分可用", tone: "warning" };
  const status = entries.map((entry) => entry.status).find(Boolean) || "unavailable";
  return mapCapabilityStatus(status);
}

export function isCapabilityReady(entry) {
  return entry.enabled !== false && ["ready", "available", "checked", "ok"].includes(entry.status || "ready");
}

export function mapCapabilityStatus(status) {
  const normalized = stringValue(status);
  const labels = {
    ready: { label: "可用", tone: "ready" },
    available: { label: "可用", tone: "ready" },
    ok: { label: "可用", tone: "ready" },
    checked: { label: "可用", tone: "ready" },
    missing_executor: { label: "缺少本地运行环境", tone: "warning" },
    missing_model: { label: "缺少模型", tone: "warning" },
    missing_config: { label: "未配置", tone: "warning" },
    configured: { label: "待检查", tone: "warning" },
    missing_workflow: { label: "待绑定", tone: "warning" },
    disabled: { label: "未启用", tone: "muted" },
    unreachable: { label: "未连接", tone: "warning" },
    invalid_config: { label: "配置异常", tone: "danger" },
    unavailable: { label: "不可用", tone: "danger" }
  };
  return labels[normalized] || { label: "待确认", tone: "warning" };
}

export function mergeWorkspaceCounts(...sources) {
  return {
    files: numberOrFallback(...sources.map((item) => asObject(item).files), 0),
    outputs: numberOrFallback(...sources.map((item) => asObject(item).outputs), 0)
  };
}

export function buildAbilityLabels({ tools, workspaceCounts }) {
  const labels = [];
  if (tools.some((name) => /file|attachment|compose|send/i.test(name))) labels.push("文件处理");
  if (tools.some((name) => /send_file/i.test(name))) labels.push("文档交付");
  if (tools.some((name) => /media|audio|voice|transcribe/i.test(name))) labels.push("媒体工具");
  if (tools.some((name) => /memory/i.test(name))) labels.push("记忆检索");
  if (workspaceCounts.files > 0) labels.push(`手边文件 ${workspaceCounts.files}`);
  if (workspaceCounts.outputs > 0) labels.push(`生成文件 ${workspaceCounts.outputs}`);
  if (!labels.length) labels.push("能力目录尚未同步");
  return labels.slice(0, 8);
}
