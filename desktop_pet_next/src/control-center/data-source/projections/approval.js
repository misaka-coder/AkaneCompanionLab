import { asArray, asObject, positiveNumber, stringValue } from "../shared.js";
import { normalizeApprovalMode } from "./capability-common.js";

export function normalizeApprovalRequestsCatalog(catalog) {
  const payload = asObject(catalog);
  const requests = asArray(payload.approvalRequests)
    .filter((entry) => entry && typeof entry === "object")
    .map((entry) => ({
      requestId: stringValue(entry.requestId),
      status: stringValue(entry.status || "pending"),
      decision: stringValue(entry.decision),
      capabilityId: stringValue(entry.capabilityId),
      actionId: stringValue(entry.actionId),
      title: stringValue(entry.title || "能力请求"),
      summary: stringValue(entry.summary),
      risk: stringValue(entry.risk || "medium"),
      approvalMode: normalizeApprovalMode(entry.approvalMode, entry),
      approvalReason: stringValue(entry.approvalReason),
      requestedBy: stringValue(entry.requestedBy || "akane"),
      createdAt: stringValue(entry.createdAt),
      expiresAt: stringValue(entry.expiresAt)
    }))
    .filter((entry) => entry.requestId || entry.capabilityId || entry.actionId);
  return {
    pendingCount: positiveNumber(payload.pendingCount ?? requests.filter((entry) => entry.status === "pending").length),
    approvalRequests: requests,
    status: stringValue(payload.status || "available")
  };
}

export function normalizeApprovalPolicyEntry(entry) {
  const payload = asObject(entry);
  const families = asArray(payload.families)
    .map((item) => asObject(item))
    .map((item) => ({
      id: stringValue(item.id),
      label: stringValue(item.label),
      summary: stringValue(item.summary),
      mode: normalizeApprovalMode(item.mode, { requiresConfirmation: true, risk: "high" }),
      availableModes: asArray(item.availableModes)
        .map((modeItem) => asObject(modeItem))
        .map((modeItem) => ({
          id: stringValue(modeItem.id),
          label: stringValue(modeItem.label),
          summary: stringValue(modeItem.summary)
        }))
        .filter((modeItem) => ["trusted_auto_allow", "ask_each_time", "disabled"].includes(modeItem.id))
    }))
    .filter((item) => ["ops", "extensions"].includes(item.id));
  return {
    updatedAt: stringValue(payload.updatedAt),
    families
  };
}

export function buildAbilitySafetyPanel(safety, serviceOk, approvalRequests = {}, approvalPolicy = null) {
  const pendingApprovalCount = positiveNumber(approvalRequests.pendingCount);
  const latestRequest = asArray(approvalRequests.approvalRequests).find((item) => item.status === "pending") || null;
  const policy = approvalPolicy || normalizeApprovalPolicyEntry(null);
  const familyModes = asArray(policy.families).map((item) => stringValue(item.mode)).filter(Boolean);
  const familyPolicyLabel = familyModes.length && familyModes.every((mode) => mode === familyModes[0])
    ? ({ trusted_auto_allow: "全部直接允许", ask_each_time: "全部每次询问", disabled: "全部关闭" }[familyModes[0]] || "策略不可用")
    : familyModes.length ? "按能力分组设置" : "策略不可用";
  return {
    status: pendingApprovalCount ? `${pendingApprovalCount} 项待确认` : serviceOk ? "已生效" : "待连接",
    approvalPolicy: policy,
    pendingCount: pendingApprovalCount,
    approvalRequests: asArray(approvalRequests.approvalRequests),
    items: [
      {
        label: "当前访问策略",
        status: familyPolicyLabel
      },
      {
        label: "审批请求队列",
        status: pendingApprovalCount
          ? `${pendingApprovalCount} 项待确认`
          : "空闲"
      },
      {
        label: "桌面动作执行",
        status: safety?.desktop_actions_require_client === false ? "自动执行" : "客户端确认"
      },
      {
        label: "密钥与敏感信息",
        status: safety?.secrets_exposed ? "已拦截" : "未暴露"
      },
      {
        label: "全盘扫描",
        status: safety?.full_disk_scan ? "需审批" : "关闭"
      },
      {
        label: "外部网络与危险操作",
        status: latestRequest ? latestRequest.title : familyPolicyLabel
      }
    ]
  };
}

export function buildSafetyDescription(safety, approvalRequests = {}, approvalPolicy = null) {
  const pendingApprovalCount = positiveNumber(approvalRequests.pendingCount);
  if (pendingApprovalCount) return `有 ${pendingApprovalCount} 项能力请求等待用户确认`;
  const familyModes = asArray(approvalPolicy?.families).map((item) => stringValue(item.mode)).filter(Boolean);
  if (familyModes.length && familyModes.every((mode) => mode === "trusted_auto_allow")) return "两组有副作用的能力已直接允许，硬安全校验保持开启";
  if (safety?.secrets_exposed) return "检测到敏感信息暴露风险，已进入保护状态";
  if (safety?.full_disk_scan) return "全盘扫描能力需要审批后才可执行";
  return "桌面危险动作保持客户端确认，敏感信息未暴露";
}

export function countPendingSafetyItems(safety) {
  const data = asObject(safety);
  let count = 1; // external/dangerous operations still require approval.
  if (data.desktop_actions_require_client !== false) count += 1;
  if (data.full_disk_scan) count += 1;
  return count;
}
