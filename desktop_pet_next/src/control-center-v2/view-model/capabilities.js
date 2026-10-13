import { asObject, finitePercent, normalizeLabelValueRows, text } from "./shared.js";
import { normalizeAbilityProviders } from "./providers.js";
import { isLoopbackBackendUrl, normalizePluginMarket, normalizePluginRuntime } from "./plugins.js";

export function createAbilitiesViewModel(raw, { connected, liveSnapshotStatus }) {
  const abilities = normalizeAbilitiesRuntime(raw.abilitiesRuntime, connected);
  abilities.plugins = normalizePluginRuntime(raw.pluginRuntime, raw.pluginManagementRuntime, connected);
  abilities.plugins.market = normalizePluginMarket(raw.pluginMarketRuntime, connected);
  abilities.plugins.localPickerAvailable = connected && liveSnapshotStatus !== "not-applicable" && isLoopbackBackendUrl(raw.backendUrl);
  abilities.available = abilities.available || abilities.plugins.available;
  return abilities;
}

function normalizeAbilitiesRuntime(value, connected) {
  const source = asObject(value);
  const overview = asObject(source.overview);
  const safety = asObject(source.safety);
  const policy = asObject(safety.approvalPolicy);
  const families = (Array.isArray(policy.families) ? policy.families : [])
    .map((item) => {
      const entry = asObject(item);
      const id = text(entry.id);
      if (!["ops", "extensions"].includes(id)) return null;
      const mode = ["trusted_auto_allow", "ask_each_time", "disabled"].includes(text(entry.mode))
        ? text(entry.mode)
        : "ask_each_time";
      const modes = (Array.isArray(entry.availableModes) ? entry.availableModes : [])
        .map((modeItem) => {
          const option = asObject(modeItem);
          const optionId = text(option.id);
          if (!["trusted_auto_allow", "ask_each_time", "disabled"].includes(optionId)) return null;
          return { id: optionId, label: text(option.label), summary: text(option.summary) };
        })
        .filter(Boolean);
      return {
        id,
        label: text(entry.label) || (id === "ops" ? "本机与外部操作" : "扩展管理"),
        summary: text(entry.summary),
        mode,
        availableModes: modes
      };
    })
    .filter(Boolean);
  const allSameMode = families.length > 0 && families.every((family) => family.mode === families[0].mode);
  const policyLabel = allSameMode
    ? ({ trusted_auto_allow: "全部直接允许", ask_each_time: "全部每次询问", disabled: "全部关闭" }[families[0].mode] || "按能力分组设置")
    : families.length ? "按能力分组设置" : "策略不可用";
  return {
    available: connected && Object.keys(source).length > 0,
    availability: finitePercent(overview.availability),
    note: text(overview.note),
    stats: normalizeLabelValueRows(overview.stats, 4),
    modules: (Array.isArray(source.modules) ? source.modules : []).map((item) => {
      const entry = asObject(item);
      return {
        title: text(entry.title),
        description: text(entry.description),
        permission: text(entry.permission),
        count: text(entry.count),
        tone: text(entry.tone) || "blue",
        statusLabel: text(entry.statusLabel) || "待同步",
        statusTone: text(entry.statusTone) || "warning"
      };
    }).filter((item) => item.title),
    policy: {
      label: policyLabel,
      summary: families.length ? "本机操作与扩展管理分别授权。" : "后端未返回权限分组。",
      families
    },
    safetyStatus: text(safety.status) || (connected ? "已生效" : "待连接"),
    safetyItems: normalizeLabelValueRows(safety.items, 8),
    approvalRequests: normalizeApprovalRequests(safety.approvalRequests),
    skills: normalizeAbilitySkills(source.skills),
    providers: normalizeAbilityProviders(source.providers),
    mcpServers: normalizeAbilityMcpServers(source.mcpServers),
    workflows: normalizeAbilityWorkflows(source.workflows),
    integrations: normalizeAbilityIntegrations(source),
    execution: normalizeExecutionRuntime(source.execution, connected),
    calls: (Array.isArray(source.calls) ? source.calls : []).slice(0, 5).map((item) => {
      const entry = asObject(item);
      return {
        time: text(entry.time),
        module: text(entry.module),
        description: text(entry.description),
        status: text(entry.status),
        method: text(entry.method)
      };
    }).filter((item) => item.module || item.description)
  };
}

function normalizeExecutionRuntime(value, connected) {
  const source = asObject(value);
  const policyPayload = asObject(source.policy);
  const resourcePayload = asObject(source.resource);
  const policy = asObject(policyPayload.policy);
  const resource = asObject(resourcePayload.policy);
  const limits = asObject(resource.limits);
  const policyEntries = (Array.isArray(policy.policies) ? policy.policies : []).map((item) => {
    const entry = asObject(item);
    const stages = Array.isArray(entry.stages) ? entry.stages.map(text).filter(Boolean) : [];
    return {
      policyId: text(entry.policy_id),
      serviceId: text(entry.service_id),
      version: Number(entry.version) || 1,
      order: Number(entry.order) || 0,
      status: text(entry.status) || "unavailable",
      reason: text(entry.reason),
      stages
    };
  }).filter((item) => item.policyId || item.serviceId);
  const limitEntries = ["max_input_bytes", "max_dependency_depth", "max_dependency_calls"].map((key) => {
    const entry = asObject(limits[key]);
    return {
      key,
      value: entry.value === null || entry.value === undefined ? null : Number(entry.value),
      source: text(entry.source) || "deployment_default"
    };
  });
  const policyAvailable = connected && policyPayload.ok === true && Boolean(policyPayload.policy && typeof policyPayload.policy === "object");
  const resourceAvailable = connected && resourcePayload.ok === true && Boolean(resource.policy_id || resource.limits);
  return {
    available: policyAvailable || resourceAvailable,
    status: policyAvailable || resourceAvailable ? "available" : text(policyPayload.reason) || text(resourcePayload.reason) || "unavailable",
    policy: {
      available: policyAvailable,
      status: text(policyPayload.status) || (policyAvailable ? "available" : "unavailable"),
      policyId: text(policy.policy_id),
      entries: policyEntries,
      diagnostics: (Array.isArray(policy.diagnostics) ? policy.diagnostics : []).map((item) => {
        const entry = asObject(item);
        return { policyId: text(entry.policy_id), phase: text(entry.phase), target: text(entry.target), reason: text(entry.reason) };
      }).filter((item) => item.policyId || item.reason)
    },
    resource: {
      available: resourceAvailable,
      status: text(resourcePayload.status) || (resourceAvailable ? "available" : "unavailable"),
      policyId: text(resource.policy_id),
      limits: limitEntries
    }
  };
}

function normalizeAbilitySkills(value) {
  const source = asObject(value);
  return {
    status: text(source.status) || "unavailable",
    catalogRevision: text(source.catalogRevision),
    total: Number(source.total || 0),
    bundled: Number(source.bundled || 0),
    managed: Number(source.managed || 0),
    entries: (Array.isArray(source.entries) ? source.entries : []).map((item) => {
      const entry = asObject(item);
      return {
        name: text(entry.name),
        description: text(entry.description),
        source: text(entry.source) || "bundled",
        revision: text(entry.revision),
        requiredTools: (Array.isArray(entry.requiredTools) ? entry.requiredTools : []).map(text).filter(Boolean),
        resourceCount: Number(entry.resourceCount || 0)
      };
    }).filter((item) => item.name && item.description),
    diagnostics: (Array.isArray(source.diagnostics) ? source.diagnostics : []).map((item) => {
      const entry = asObject(item);
      return {
        name: text(entry.name),
        reason: text(entry.reason),
        fallback: text(entry.fallback)
      };
    }).filter((item) => item.name || item.reason)
  };
}

function normalizeApprovalRequests(value) {
  return (Array.isArray(value) ? value : []).map((item) => {
    const entry = asObject(item);
    return {
      requestId: text(entry.requestId),
      title: text(entry.title) || "能力请求",
      summary: text(entry.summary) || text(entry.approvalReason),
      risk: text(entry.risk) || "medium",
      requestedBy: text(entry.requestedBy) || "Akane",
      createdAt: text(entry.createdAt),
      status: text(entry.status) || "pending"
    };
  }).filter((item) => item.requestId && item.status === "pending");
}

function normalizeAbilityMcpServers(value) {
  return (Array.isArray(value) ? value : []).map((item) => {
    const entry = asObject(item);
    return {
      serverId: text(entry.serverId),
      title: text(entry.title) || "MCP 外部工具",
      status: text(entry.status) || "missing_config",
      statusLabel: text(entry.statusLabel) || "待配置",
      statusTone: text(entry.statusTone) || "warning",
      reason: text(entry.reason),
      enabled: Boolean(entry.enabled),
      configured: Boolean(entry.configured),
      transport: text(entry.transport) || "stdio",
      executionLocation: text(entry.executionLocation) || "host",
      executionLocationLabel: text(entry.executionLocationLabel) || "Akane Host",
      executionLocationDetail: text(entry.executionLocationDetail),
      commandName: text(entry.commandName),
      toolCount: Number(entry.toolCount || 0),
      safeToolLabels: (Array.isArray(entry.safeToolLabels) ? entry.safeToolLabels : []).map(text).filter(Boolean),
      approvalLabel: text(entry.approvalLabel),
      lastDiscoveryLabel: text(entry.lastDiscoveryLabel),
      actionsEnabled: entry.actionsEnabled !== false && Boolean(text(entry.serverId))
    };
  }).filter((item) => item.title);
}

function normalizeAbilityWorkflows(value) {
  return (Array.isArray(value) ? value : []).map((item) => {
    const entry = asObject(item);
    return {
      workflowId: text(entry.workflowId) || text(entry.id),
      title: text(entry.title) || "本地工作流",
      detail: text(entry.detail),
      statusLabel: text(entry.statusLabel) || "待配置",
      statusTone: text(entry.statusTone) || "warning",
      enabled: Boolean(entry.enabled),
      configured: Boolean(entry.configured),
      executionReady: Boolean(entry.executionReady),
      workflowPath: text(entry.workflowPath),
      defaultWorkflowPath: text(entry.defaultWorkflowPath),
      inputImageSlot: text(entry.inputImageSlot),
      outputImageSlot: text(entry.outputImageSlot),
      actionsEnabled: entry.actionsEnabled !== false && Boolean(text(entry.workflowId) || text(entry.id))
    };
  }).filter((item) => item.title);
}

function normalizeAbilityIntegrations(source) {
  const groups = [
    ["本地服务", source.providers],
    ["MCP 工具", source.mcpServers],
    ["工作流", source.workflows]
  ];
  return groups.flatMap(([group, values]) => (Array.isArray(values) ? values : []).map((item) => {
    const entry = asObject(item);
    return {
      group,
      title: text(entry.title) || text(entry.name) || text(entry.workflowId) || group,
      status: text(entry.statusLabel) || text(entry.status) || "待同步",
      detail: text(entry.reason) || text(entry.detail) || text(entry.description),
      ready: ["ready", "available", "已就绪", "就绪", "可用"].includes(
        text(entry.status).toLowerCase() || text(entry.statusLabel)
      )
    };
  })).slice(0, 8);
}
