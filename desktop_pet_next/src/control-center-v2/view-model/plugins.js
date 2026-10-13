import { asObject, text } from "./shared.js";
import { normalizePluginConnectionConfigs, normalizePluginContributions } from "./plugin-connections.js";

export function normalizePluginRuntime(value, managementValue, connected) {
  const runtime = asObject(value);
  const payload = asObject(runtime.data);
  const status = text(runtime.status) || "not-requested";
  const catalogAvailable = connected && status === "available" && payload.ok === true;
  const managementRuntime = asObject(managementValue);
  const managementPayload = asObject(managementRuntime.data);
  const management = asObject(managementPayload.management);
  const artifacts = asObject(managementPayload.artifacts);
  const managementStatus = text(managementRuntime.status) || "not-requested";
  const dependencySupply = normalizePluginDependencySupply(payload.dependency_supply || artifacts.dependency_supply);
  const supports = (Array.isArray(management.supports) ? management.supports : []).map(text).filter(Boolean);
  const entries = (Array.isArray(payload.plugins) ? payload.plugins : [])
    .map((item) => {
      const entry = asObject(item);
      const pluginId = text(entry.plugin_id);
      if (!pluginId) return null;
      const runtimeStatus = text(entry.runtime_status) || "unavailable";
      const enabled = Boolean(entry.enabled);
      const pendingActivation = Boolean(entry.pending_activation);
      const presentation = pluginStatusPresentation(runtimeStatus, enabled, pendingActivation);
      const normalizedContributions = normalizePluginContributions(entry.contributions);
      const connectionConfigs = normalizePluginConnectionConfigs(entry.connection_configs, normalizedContributions.connections);
      return {
        pluginId,
        version: text(entry.version),
        source: text(entry.source) === "managed" ? "managed" : "bundled",
        manageable: entry.manageable === true,
        enabled,
        runtimeStatus,
        statusLabel: presentation.label,
        statusTone: presentation.tone,
        reason: text(entry.reason),
        dependencyErrors: normalizePluginDependencyErrors(entry.dependency_errors),
        generation: Number(entry.generation || 0),
        surfaces: (Array.isArray(entry.surfaces) ? entry.surfaces : [])
          .map(text)
          .filter((surface) => ["desktop", "qq"].includes(surface)),
        contributions: normalizedContributions,
        connectionConfigs,
        contributionCount: Object.values(normalizedContributions)
          .reduce((total, values) => total + values.length, 0),
        permissions: (Array.isArray(entry.permissions) ? entry.permissions : []).map(text).filter(Boolean),
        declaredOnly: Boolean(entry.declared_only),
        pendingActivation,
        rollbackAvailable: Boolean(entry.rollback_available)
      };
    })
    .filter(Boolean)
    .map((entry) => ({
      ...entry,
      actionsEnabled: catalogAvailable && entry.manageable && !entry.pendingActivation
    }));
  return {
    available: catalogAvailable,
    status,
    reason: text(runtime.reason) || text(payload.reason),
    generation: Number(payload.generation || 0),
    managementAvailable: connected && managementStatus === "available" && text(management.status) === "ready",
    managementStatus,
    managementReason: text(managementRuntime.reason) || text(managementPayload.reason),
    supports,
    dependencySupply,
    stages: (Array.isArray(artifacts.stages) ? artifacts.stages : []).map((item) => {
      const entry = asObject(item);
      const pluginId = text(entry.plugin_id);
      const stageId = text(entry.stage_id);
      if (!stageId) return null;
      const contributions = normalizePluginContributions(entry.contribution_snapshot);
      return {
        ok: entry.ok === true,
        stageId,
        pluginId,
        version: text(entry.version),
        distributionName: text(entry.distribution_name),
        status: text(entry.status),
        reason: text(entry.reason),
        permissions: (Array.isArray(entry.permissions) ? entry.permissions : []).map(text).filter(Boolean),
        contributions,
        contributionCount: Object.values(contributions).reduce((total, values) => total + values.length, 0)
      };
    }).filter(Boolean),
    total: entries.length,
    active: entries.filter((item) => item.enabled && item.runtimeStatus === "active").length,
    entries
  };
}

export function normalizePluginMarket(value, connected) {
  const runtime = asObject(value);
  const payload = asObject(runtime.data);
  return {
    status: text(runtime.status) || "not-requested",
    reason: text(runtime.reason) || text(payload.reason),
    sourceKind: text(payload.source_kind),
    entries: connected && runtime.status === "available" && payload.ok === true
      ? (Array.isArray(payload.plugins) ? payload.plugins : []).map((entry) => ({
          pluginId: text(entry.plugin_id), version: text(entry.version), displayName: text(entry.display_name),
          summary: text(entry.summary), digest: text(entry.sha256), sizeBytes: Number(entry.size_bytes || 0),
          installedVersion: text(entry.installed_version), installedStatus: text(entry.installed_status),
          permissions: (Array.isArray(entry.permissions) ? entry.permissions : []).map(text).filter(Boolean),
          requirements: (Array.isArray(entry.requirements) ? entry.requirements : []).map(text).filter(Boolean)
        })).filter((entry) => entry.pluginId && /^[a-f0-9]{64}$/.test(entry.digest))
      : []
  };
}

export function isLoopbackBackendUrl(value) {
  try {
    const hostname = new URL(text(value)).hostname.replace(/^\[|\]$/g, "").toLowerCase();
    return hostname === "localhost" || /^127(?:\.\d{1,3}){3}$/.test(hostname) || hostname === "::1";
  } catch {
    return false;
  }
}

function pluginStatusPresentation(status, enabled, pendingActivation) {
  if (pendingActivation) return { label: "等待激活", tone: "warning" };
  if (!enabled || status === "disabled") return { label: "已停用", tone: "muted" };
  if (status === "active") return { label: "运行中", tone: "ready" };
  if (status === "waiting_dependency") return { label: "等待依赖", tone: "warning" };
  if (["loading", "starting"].includes(status)) return { label: "启动中", tone: "warning" };
  return { label: "不可用", tone: "danger" };
}

function normalizePluginDependencySupply(value) {
  const source = asObject(value);
  const kind = text(source.kind);
  const status = text(source.status);
  if (!kind && !status) return null;
  const kindLabels = {
    wheelhouse: "离线 wheelhouse",
    package_index: "受控包索引",
    host_only: "仅宿主提供"
  };
  const statusLabels = {
    ready: "已就绪",
    configured: "已配置",
    unconfigured: "未配置",
    unavailable: "不可用"
  };
  const tone = ["ready", "configured"].includes(status) ? "ready" : ["unavailable"].includes(status) ? "danger" : "warning";
  return {
    kind,
    kindLabel: kindLabels[kind] || "依赖供应",
    configured: source.configured === true,
    status,
    statusLabel: statusLabels[status] || (status ? `状态：${status}` : "待确认"),
    tone
  };
}

function normalizePluginDependencyErrors(value) {
  return (Array.isArray(value) ? value : []).slice(0, 8).map((item) => {
    const entry = asObject(item);
    return {
      reason: text(entry.reason),
      serviceId: text(entry.service_id),
      selectedProvider: text(entry.selected_provider),
      requiredService: text(entry.required_service),
      diagnosticId: text(entry.diagnostic_id)
    };
  }).filter((item) => item.reason || item.serviceId || item.selectedProvider || item.requiredService);
}
