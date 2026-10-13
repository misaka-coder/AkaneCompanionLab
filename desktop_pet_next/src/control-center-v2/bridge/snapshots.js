import { createControlCenterRuntimeSnapshot } from "../../control-center/data-sources.js";
import { formatError } from "./errors.js";

const PLUGIN_READS = [
  ["readPluginCatalog", "pluginRuntime", "plugin-catalog-unavailable"],
  ["readPluginManagement", "pluginManagementRuntime", "plugin-management-unavailable"],
  ["readPluginMarket", "pluginMarketRuntime", "plugin-market-unavailable"]
];
const loading = () => ({ status: "loading", reason: "", data: null });

export function createBridgeSnapshots({ captureBinding, isCurrentBinding, readSnapshot, writeSnapshot,
  publish, readViewModel, refreshChatSession }) {
  let pending = null, version = 0, publishedSource = null;
  function optionalRead(source, method, fallback) {
    return Promise.resolve().then(() => typeof source[method] === "function" ? source[method]() : fallback);
  }
  async function publishPlugin(ticket, request, field, fallback, sequence) {
    const result = await request;
    if (sequence !== version || !isCurrentBinding(ticket) || !readSnapshot()) return;
    writeSnapshot({ ...readSnapshot(), [field]: result?.ok && result.data && typeof result.data === "object"
      ? { status: "available", reason: "", data: result.data }
      : { status: "unavailable", reason: String(result?.error || result?.status || fallback), data: null } });
    publish();
  }
  async function refresh() {
    if (pending && isCurrentBinding(pending.ticket)) return pending.promise;
    const ticket = captureBinding(), requestSource = ticket.source;
    if (!isCurrentBinding(ticket) || typeof requestSource?.readSnapshot !== "function") throw Error("control_center_snapshot_source_unavailable");
    const operation = { ticket, promise: null }, sequence = ++version;
    pending = operation;
    operation.promise = (async () => {
      const plugins = PLUGIN_READS.map(([method, field, fallback]) => ({ field, fallback,
        request: optionalRead(requestSource, method, { ok: false, status: "not-supported", data: null })
          .catch(error => ({ ok: false, status: "request-failed", error: formatError(error), data: null })) }));
      try {
        const [next, modelRuntime, botCatalog] = await Promise.all([
          requestSource.readSnapshot(),
          optionalRead(requestSource, "readModelService", null).catch(() => null),
          optionalRead(requestSource, "readBotCatalog", null).catch(() => null)
        ]);
        if (!isCurrentBinding(ticket)) return null;
        if (!next) throw Error(requestSource.getFallbackReason?.() || requestSource.fallbackReason || "snapshot_unavailable");
        const previous = readSnapshot();
        writeSnapshot({ ...createControlCenterRuntimeSnapshot(next),
          modelRuntime: modelRuntime || previous?.modelRuntime || {}, botCatalog: botCatalog || previous?.botCatalog || {},
          ...Object.fromEntries(PLUGIN_READS.map(([, field]) => [field, publishedSource === requestSource ? previous?.[field] || loading() : loading()])),
          ...(previous?.chatSession ? { chatSession: previous.chatSession } : {}),
          ...(previous?.chatRuntime ? { chatRuntime: previous.chatRuntime } : {}) });
        publishedSource = requestSource;
        publish();
        for (const item of plugins) void publishPlugin(ticket, item.request, item.field, item.fallback, sequence);
      } catch (error) {
        if (!isCurrentBinding(ticket)) return null;
        writeSnapshot({ ...requestSource.readInitialState(), fallbackReason: formatError(error) });
        publish();
        throw error;
      } finally {
        // The chat tail has its own scoped queue; it must not hold the snapshot lock.
        if (pending === operation) pending = null;
      }
      await refreshChatSession();
      if (!isCurrentBinding(ticket)) return null;
      publish();
      return readViewModel();
    })();
    return operation.promise;
  }
  function reset() { version += 1; pending = null; publishedSource = null; }
  return { refresh, reset };
}
