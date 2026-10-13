import { formatBytes } from "../shared.js";

export function buildHealthTiles({ metrics, runtimeMetrics, workspaceCounts, health, toolCount, serviceOk }) {
  const currentMemoryBytes = metrics.akane_tracemalloc_current_bytes;
  const peakMemoryBytes = metrics.akane_tracemalloc_peak_bytes;
  const vectorEntries = metrics.akane_vector_entries;
  const activeThinks = metrics.akane_public_guard_active_thinks;
  const cpuPercent = metrics.cpu_percent;
  const contractVersion = health?.contracts?.desktop_pet?.version || "";
  const cpuValue = Number(cpuPercent);
  const hasCpuPercent = Number.isFinite(cpuValue);
  return {
    "CPU 占用": hasCpuPercent ? `${Math.round(cpuValue)}%` : (serviceOk ? "运行中" : "待连接"),
    "内存占用": currentMemoryBytes ? formatBytes(currentMemoryBytes) : "-",
    "记忆容量": vectorEntries ? `${Math.round(vectorEntries)} 条记忆` : `${workspaceCounts.files} 个文件`,
    "峰值内存": peakMemoryBytes ? `峰值 ${formatBytes(peakMemoryBytes)}` : "待采集",
    "错误数": String(countMetricErrors(runtimeMetrics)),
    "活跃守护": activeThinks ? `${activeThinks} 个会话` : "0",
    "协议版本": contractVersion || "待同步",
    "能力注册": toolCount ? `${toolCount} 工具` : "等待诊断"
  };
}

function countMetricErrors(metrics) {
  return Object.entries(metrics || {}).reduce((total, [key, value]) => {
    if (!/_error|errors|failed|failures/i.test(key)) return total;
    const number = Number(value || 0);
    return Number.isFinite(number) ? total + number : total;
  }, 0);
}

export function inferLatencyLabel(metrics, preferredPrefixes = []) {
  const source = metrics && typeof metrics === "object" ? metrics : {};
  for (const prefix of preferredPrefixes) {
    const duration = Number(source[`${prefix}_duration_ms_total`]);
    const requests = Number(source[`${prefix}_requests_total`]);
    if (Number.isFinite(duration) && duration >= 0 && Number.isFinite(requests) && requests > 0) {
      return `${Math.round(duration / requests)} ms`;
    }
  }

  const directMilliseconds = Object.entries(source).find(([key, value]) =>
    /(?:duration|latency).*_ms(?:$|_)/i.test(key) &&
    !/_total$/i.test(key) &&
    Number.isFinite(Number(value))
  );
  if (directMilliseconds) return `${Math.round(Number(directMilliseconds[1]))} ms`;

  const directSeconds = Object.entries(source).find(([key, value]) =>
    /(?:duration|latency).*_seconds?(?:$|_)/i.test(key) &&
    !/_total$/i.test(key) &&
    Number.isFinite(Number(value))
  );
  if (directSeconds) return `${Math.round(Number(directSeconds[1]) * 1000)} ms`;
  return "已连接";
}

export function parsePrometheusMetrics(text) {
  const metrics = {};
  for (const line of String(text || "").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const match = /^([a-zA-Z_:][\w:]*)(?:\{[^}]*\})?\s+(-?\d+(?:\.\d+)?)$/.exec(trimmed);
    if (!match) continue;
    metrics[match[1]] = Number(match[2]);
  }
  return metrics;
}
