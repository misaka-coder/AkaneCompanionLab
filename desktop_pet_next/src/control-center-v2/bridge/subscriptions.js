import { formatError } from "./errors.js";

export function createBridgeSubscriptions({ host, onRuntime, onFileDrop, onUnavailable }) {
  let epoch = 0, closed = true;
  const disposers = new Set();
  const isCurrent = ticket => !closed && ticket === epoch;
  function stop() {
    closed = true;
    epoch += 1;
    for (const dispose of disposers) dispose();
    disposers.clear();
  }
  function begin() { stop(); closed = false; return epoch; }
  function adopt(ticket, dispose) {
    if (!isCurrent(ticket)) { dispose?.(); return false; }
    if (dispose) disposers.add(dispose);
    return true;
  }
  async function connect(ticket) {
    if (!host.isTauri || !isCurrent(ticket)) return;
    try {
      if (!adopt(ticket, await host.listenSnapshots(payload => { if (isCurrent(ticket)) onRuntime(payload); }))) return;
      await host.requestSnapshot();
      if (!isCurrent(ticket)) return;
      adopt(ticket, await host.listenFileDrops(paths => { if (isCurrent(ticket)) onFileDrop(paths); }));
    } catch (error) {
      if (!isCurrent(ticket)) return;
      for (const dispose of disposers) dispose();
      disposers.clear();
      onUnavailable(formatError(error));
    }
  }
  return { begin, connect, stop, isCurrent, capture: () => epoch };
}
