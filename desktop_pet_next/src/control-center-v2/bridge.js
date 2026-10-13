import { createControlCenterHost } from "./bridge/host.js";
import { createControlCenterRuntime } from "./bridge/runtime.js";

export { shouldUseInstanceAdminProxy } from "./bridge/host.js";

export function createControlCenterBridge(options = {}) {
  return createControlCenterRuntime({ host: createControlCenterHost(options) });
}
