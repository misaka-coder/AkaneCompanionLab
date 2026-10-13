import { asObject, formatBytes, stringValue } from "../shared.js";
import { parsePrometheusMetrics } from "./metrics.js";

export function buildAdvancedRuntimePatch({ health, diagnostics, metricsText, petState }) {
  const healthData = asObject(health);
  const diagnosticsData = asObject(diagnostics);
  const metrics = parsePrometheusMetrics(metricsText);
  const serviceOk = stringValue(healthData?.status) === "ok" || stringValue(diagnosticsData?.status) === "ok";

  // systemStrip — patch by label
  const cpuPercent = metrics?.cpu_percent ? `${Math.round(metrics.cpu_percent)}%` : undefined;
  const memPercent = metrics?.memory_percent ? `${Math.round(metrics.memory_percent)}%` : undefined;
  const systemStrip = {
    "运行中": { tone: serviceOk ? "green" : "muted" },
    "CPU": { value: cpuPercent },
    "内存": { value: memPercent },
    "网络": { value: serviceOk ? "良好" : "离线", tone: serviceOk ? "green" : "warning" }
  };

  // diagnostics.metrics — patch by label
  const currentMemoryBytes = metrics?.akane_tracemalloc_current_bytes;
  const memoryDisplay = currentMemoryBytes ? formatBytes(currentMemoryBytes) : undefined;
  const diagnosticsMetrics = {
    "应用状态": { value: serviceOk ? "运行中" : "等待连接", tone: serviceOk ? "green" : "warning" },
    "后端健康": { value: serviceOk ? "良好" : "异常", tone: serviceOk ? "green" : "danger" },
    "内存占用": { value: memoryDisplay }
  };

  const coreSettings = [
    { id: "hitTest", enabled: Boolean(petState?.hitTestEnabled) },
    { id: "hitbox", enabled: Boolean(petState?.hitboxOverlay) }
  ];

  return {
    systemStrip,
    coreSettings,
    diagnostics: { metrics: diagnosticsMetrics }
  };
}
