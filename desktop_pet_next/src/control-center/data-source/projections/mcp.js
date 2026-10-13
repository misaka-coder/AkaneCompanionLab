import {
  approvalModeLabel,
  capabilityEntryText,
  isCapabilityReady,
  mapCapabilityStatus,
  normalizeApprovalMode
} from "./capability-common.js";
import { asObject, stringValue } from "../shared.js";

export function buildAbilityMcpServerCards(entries) {
  const toolsByProvider = new Map();
  for (const entry of entries) {
    if (entry.kind !== "mcp_tool") continue;
    const providerId = entry.providerId || (entry.serverId ? `provider.mcp.${entry.serverId}` : "");
    if (!providerId) continue;
    if (!toolsByProvider.has(providerId)) toolsByProvider.set(providerId, []);
    toolsByProvider.get(providerId).push(entry);
  }
  return entries
    .filter((entry) => entry.kind === "provider" && (entry.source === "mcp" || entry.adapter === "mcp_stdio"))
    .map((entry) => {
      const status = mapCapabilityStatus(entry.status);
      const tools = toolsByProvider.get(entry.id) || [];
      const highRiskCount = tools.filter((tool) => tool.risk === "high" || tool.requiresConfirmation).length;
      const discoveredAt = stringValue(entry.lastDiscovery?.discoveredAt);
      const approvalMode = mcpServerApprovalMode(entry, tools);
      return {
        id: entry.id,
        serverId: entry.serverId,
        title: entry.name || "MCP 外部工具",
        status: entry.status || "missing_config",
        statusLabel: status.label,
        statusTone: status.tone,
        reason: mcpServerReasonLabel(entry),
        enabled: Boolean(entry.enabled),
        configured: Boolean(entry.configured),
        transport: entry.transport || "stdio",
        executionLocation: entry.executionLocation || (entry.transport === "streamable_http" ? "remote" : "host"),
        executionLocationLabel: mcpExecutionLocationLabel(entry.executionLocation, entry.transport),
        executionLocationDetail: mcpExecutionLocationDetail(entry.executionLocation, entry.transport),
        commandName: entry.commandName || "",
        toolCount: entry.toolCount || tools.length,
        safeToolLabels: summarizeMcpToolLabels(tools),
        toolDetails: summarizeMcpToolDetails(tools),
        highRiskCount,
        promptExposedCount: tools.filter((tool) => tool.exposedToPrompt).length,
        requiresConfirmation: Boolean(highRiskCount),
        approvalMode,
        approvalLabel: approvalModeLabel(approvalMode),
        lastDiscoveryLabel: discoveredAt ? "已发现工具" : "未执行发现"
      };
    });
}

function mcpServerApprovalMode(entry, tools = []) {
  const baseMode = normalizeApprovalMode(entry.approvalMode, entry);
  if (baseMode === "disabled" || !isCapabilityReady(entry)) return "disabled";
  if (tools.some((tool) => normalizeApprovalMode(tool.approvalMode, tool) === "ask_each_time")) {
    return "ask_each_time";
  }
  return baseMode;
}

function summarizeMcpToolLabels(tools) {
  const labels = [];
  for (const tool of tools) {
    const label = mcpToolCapabilityLabel(tool);
    if (label && !labels.includes(label)) labels.push(label);
    if (labels.length >= 4) break;
  }
  return labels.length ? labels : ["等待工具发现"];
}

function summarizeMcpToolDetails(tools) {
  const seen = new Map();
  const details = [];
  for (const tool of tools) {
    const label = mcpToolCapabilityLabel(tool);
    const count = (seen.get(label) || 0) + 1;
    seen.set(label, count);
    const approvalMode = normalizeApprovalMode(tool.approvalMode, tool);
    const riskLabel = tool.risk === "high" || tool.requiresConfirmation ? "高风险" : "普通";
    details.push({
      title: count > 1 ? `${label} ${count}` : label,
      riskLabel,
      approvalLabel: approvalModeLabel(approvalMode),
      promptLabel: tool.exposedToPrompt ? "已进提示词" : "默认不进提示词",
      statusLabel: mapCapabilityStatus(tool.status).label,
      schemaFields: Object.keys(asObject(tool.inputSchema?.properties)).slice(0, 6)
    });
    if (details.length >= 8) break;
  }
  return details;
}

function mcpToolCapabilityLabel(tool) {
  const text = capabilityEntryText(tool);
  if (/shell|terminal|cmd|powershell|exec|command|process|delete|remove|write|modify|click/.test(text)) return "需确认的操作";
  if (/browser|page|tab|url|web|navigate|read_page|search/.test(text)) return "浏览器上下文";
  if (/screenshot|screen|image|vision|ocr/.test(text)) return "屏幕与图像";
  if (/file|folder|workspace|path|document/.test(text)) return "文件上下文";
  if (/memory|retrieve|query/.test(text)) return "检索资料";
  return "外部工具";
}

function mcpServerReasonLabel(entry) {
  const status = entry.status || "";
  if (status === "ready") return "工具已发现";
  if (status === "configured") return "已保存，等待发现工具";
  if (status === "missing_config") {
    return entry.transport === "streamable_http" ? "需要配置远程 MCP 地址" : "需要配置 Akane Host 上的 MCP 启动命令";
  }
  if (status === "disabled") return "已配置但未启用";
  if (status === "invalid_config") return "配置需要修复";
  return entry.reason || "等待同步";
}

function mcpExecutionLocationLabel(location, transport) {
  return location === "remote" || transport === "streamable_http" ? "远程服务" : "Akane Host";
}

function mcpExecutionLocationDetail(location, transport) {
  if (location === "remote" || transport === "streamable_http") {
    return "通过网络连接独立 MCP 服务；不会在当前设备启动本地进程。";
  }
  return "MCP 进程由 Akane Host 启动；Host 在本机时运行于本机，Host 在云端时运行于云端。";
}
