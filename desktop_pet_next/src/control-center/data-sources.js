import * as mockData from "./mock-data.js";

export const CONTROL_CENTER_SOURCE_KIND = Object.freeze({
  mock: "mock",
  backend: "backend",
  tauri: "tauri"
});

export function createControlCenterDataSource(options = {}) {
  const kind = options.kind || CONTROL_CENTER_SOURCE_KIND.mock;
  if (kind === CONTROL_CENTER_SOURCE_KIND.backend) {
    return createBackendControlCenterSource(options);
  }
  return createMockControlCenterSource(options.mockData || mockData);
}

export function createMockControlCenterSource(data = mockData) {
  return {
    kind: CONTROL_CENTER_SOURCE_KIND.mock,
    readInitialState() {
      return {
        ...data,
        sourceKind: CONTROL_CENTER_SOURCE_KIND.mock
      };
    },
    subscribe() {
      return () => {};
    },
    async runAction(actionId, payload = {}) {
      return {
        ok: true,
        status: "mocked",
        actionId,
        payload
      };
    }
  };
}

export function createBackendControlCenterSource(options = {}) {
  const endpoint = options.endpoint || "/api/control-center";
  const fetchImpl = options.fetchImpl || globalThis.fetch;

  return {
    kind: CONTROL_CENTER_SOURCE_KIND.backend,
    async readSnapshot() {
      if (typeof fetchImpl !== "function") {
        return null;
      }
      const response = await fetchImpl(endpoint, { headers: { Accept: "application/json" } });
      if (!response.ok) {
        throw new Error(`Control center snapshot request failed: ${response.status}`);
      }
      return response.json();
    },
    readInitialState() {
      return {
        ...mockData,
        sourceKind: CONTROL_CENTER_SOURCE_KIND.mock,
        fallbackReason: "backend-source-not-connected"
      };
    },
    subscribe() {
      return () => {};
    },
    async runAction(actionId, payload = {}) {
      if (typeof fetchImpl !== "function") {
        return { ok: false, status: "missing-fetch", actionId };
      }
      const response = await fetchImpl(`${endpoint}/actions/${encodeURIComponent(actionId)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(payload)
      });
      if (!response.ok) {
        throw new Error(`Control center action failed: ${response.status}`);
      }
      return response.json();
    }
  };
}
