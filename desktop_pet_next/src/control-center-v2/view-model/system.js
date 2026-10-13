import { asObject, text } from "./shared.js";

export function normalizeSystemRuntime(raw, live, options = {}) {
  const advanced = asObject(raw.advancedRuntime);
  const diagnostics = asObject(advanced.diagnostics);
  const liveState = asObject(live.state);
  const liveResource = asObject(live.resource);
  const control = asObject(raw.controlCenterRuntime);
  const connected = Boolean(options.connected);
  const services = normalizeSystemServices(control);
  const metrics = normalizeSystemMetrics(advanced, diagnostics, connected);
  const controlsAvailable = connected && Object.keys(liveState).length > 0;
  const settings = normalizeSystemSettings(advanced.coreSettings, liveState);
  const issues = [];

  if (!connected) {
    issues.push(systemIssue("danger", "桌宠服务未连接", "请确认本地后端已经启动，然后重新检查。"));
  }
  if (options.liveSnapshotStatus === "unavailable") {
    issues.push(systemIssue("warning", "桌面实时状态不可用", "后端仍可读取，但窗口状态和本地操作暂时无法确认。"));
  }
  const resourceHealth = text(liveResource.health);
  if (resourceHealth && !["online", "ok", "ready"].includes(resourceHealth.toLowerCase())) {
    issues.push(systemIssue("warning", "角色资源没有完全就绪", text(liveResource.healthMessage) || resourceHealth));
  }
  for (const warning of Array.isArray(options.characterWarnings) ? options.characterWarnings : []) {
    issues.push(systemIssue("warning", warning, "可前往角色与外观页检查资源。"));
  }
  for (const provider of [options.voice?.tts?.provider, options.voice?.asr?.provider]) {
    if (!provider || provider.ready || provider.status === "unknown") continue;
    issues.push(systemIssue(provider.status === "unavailable" ? "danger" : "warning", `${provider.name}：${provider.statusLabel}`, provider.reason));
  }
  for (const service of services) {
    if (service.ready || service.optional) continue;
    issues.push(systemIssue("warning", `${service.label}不可用`, service.detail || service.statusLabel));
  }

  const dedupedIssues = dedupeSystemIssues(issues).slice(0, 8);
  const overallTone = !connected || dedupedIssues.some((item) => item.tone === "danger")
    ? "danger"
    : dedupedIssues.length
      ? "warning"
      : "good";
  return {
    available: connected || Object.keys(advanced).length > 0,
    controlsAvailable,
    overallTone,
    overallLabel: overallTone === "good" ? "运行正常" : overallTone === "warning" ? "需要留意" : "连接异常",
    overallDetail: overallTone === "good"
      ? controlsAvailable ? "核心服务与桌面状态均已同步" : "后端检查正常；桌面操作仅在应用内可用"
      : dedupedIssues[0]?.title || "部分状态暂不可用",
    metrics,
    services,
    settings,
    issues: dedupedIssues,
    details: normalizeSystemDetails(raw, live, metrics)
  };
}

function normalizeSystemMetrics(advanced, diagnostics, connected) {
  const strip = asObject(advanced.systemStrip);
  const detailMetrics = asObject(diagnostics.metrics);
  const candidates = [
    metricFromMap("应用状态", detailMetrics, connected ? "运行中" : "等待连接", connected ? "good" : "danger"),
    metricFromMap("CPU", strip),
    metricFromMap("内存", strip),
    metricFromMap("内存占用", detailMetrics),
    metricFromMap("网络", strip, connected ? "良好" : "离线", connected ? "good" : "danger")
  ].filter(Boolean);
  const seen = new Set();
  return candidates.filter((item) => {
    const key = `${item.label}:${item.value}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 5);
}

function metricFromMap(label, source, fallbackValue = "", fallbackTone = "muted") {
  const entry = asObject(source[label]);
  const value = text(entry.value) || fallbackValue;
  if (!value) return null;
  return {
    label,
    value,
    tone: normalizeSystemTone(entry.tone || fallbackTone)
  };
}

function normalizeSystemServices(control) {
  const definitions = [
    ["health", "核心服务", false],
    ["diagnostics", "诊断接口", false],
    ["workspace", "工作区", false],
    ["metrics", "运行指标", true],
    ["capabilitiesCatalog", "能力目录", true]
  ];
  return definitions.map(([id, label, optional]) => {
    const source = asObject(control[id]);
    if (!Object.keys(source).length) return null;
    const ready = source.ok === true;
    const status = text(source.status);
    return {
      id,
      label,
      ready,
      optional,
      statusLabel: ready ? "可用" : status ? `状态 ${status}` : "不可用",
      detail: ready ? "最近一次读取成功" : "重新检查后仍不可用时，请查看服务启动状态"
    };
  }).filter(Boolean);
}

function normalizeSystemSettings(value, liveState) {
  const runtimeById = new Map((Array.isArray(value) ? value : []).map((item) => [text(item?.id), asObject(item)]));
  return [
    {
      id: "hitTest",
      title: "桌宠点击区域",
      description: "让鼠标能够命中桌宠的可交互区域。",
      actionId: "advanced.setHitTestEnabled",
      enabled: typeof liveState.hitTestEnabled === "boolean" ? liveState.hitTestEnabled : Boolean(runtimeById.get("hitTest")?.enabled)
    },
    {
      id: "hitbox",
      title: "显示点击边界",
      description: "排查点不到或误触时临时显示真实交互范围。",
      actionId: "advanced.setHitboxOverlay",
      enabled: typeof liveState.hitboxOverlay === "boolean" ? liveState.hitboxOverlay : Boolean(runtimeById.get("hitbox")?.enabled)
    }
  ];
}

function normalizeSystemDetails(raw, live, metrics) {
  const resource = asObject(live.resource);
  return [
    { label: "实例", value: text(asObject(live.state).instanceId) || "本地实例" },
    { label: "桌面运行状态", value: text(live.runtimeStatus) || "未提供" },
    { label: "角色资源", value: text(resource.health) || "未提供" },
    { label: "资源来源", value: text(resource.source) || "未提供" },
    { label: "最近同步", value: formatSystemTime(raw.generatedAt) },
    ...metrics.filter((item) => !["应用状态", "网络"].includes(item.label)).map((item) => ({ label: item.label, value: item.value }))
  ].filter((item, index, items) => item.value && items.findIndex((other) => other.label === item.label) === index);
}

function systemIssue(tone, title, detail = "") {
  return { tone: normalizeSystemTone(tone), title: text(title), detail: text(detail) };
}

function dedupeSystemIssues(value) {
  const seen = new Set();
  return value.filter((item) => {
    if (!item.title || seen.has(item.title)) return false;
    seen.add(item.title);
    return true;
  });
}

function normalizeSystemTone(value) {
  const tone = text(value).toLowerCase();
  if (["green", "good", "ready", "success"].includes(tone)) return "good";
  if (["danger", "error", "failed", "red"].includes(tone)) return "danger";
  if (["warning", "warn", "orange"].includes(tone)) return "warning";
  return "muted";
}

function formatSystemTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "未提供";
  return date.toLocaleString("zh-CN", { hour12: false });
}
