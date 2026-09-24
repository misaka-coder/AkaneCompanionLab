import assert from "node:assert/strict";

import {
  buildCharacterRuntimePatchFromSettingsSnapshot,
  createBackendControlCenterSource,
  createControlCenterDataSource,
  createControlCenterRuntimeSnapshot,
  CONTROL_CENTER_SOURCE_KIND,
} from "../src/control-center/data-sources.js";

function jsonResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => "application/json" },
    async json() { return body; },
    async text() { return typeof body === "string" ? body : JSON.stringify(body); },
  };
}

function textResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => "text/plain" },
    async json() { return body; },
    async text() { return String(body); },
  };
}

const runtime = {
  health: {
    status: "ok",
    pid: 1234,
    python: "python3",
    contracts: { desktop_pet: { tts: true, health: true } },
  },
  diagnostics: {
    status: "ok",
    resources: {
      resource_manifest_ok: true,
      character_pack_id: "runtime_pack",
      outfit: "cat",
      default_emotion: "happy",
      emotion_count: 3,
    },
    capabilities: {
      declared: ["tts", "workspace_summary"],
      effective_modules: ["audio", "files"],
      tool_names: ["retrieve_memory", "send_file"],
    },
    workspace: { files: 2, outputs: 1 },
    runtime: {
      pid: 1234,
      python: "python3",
      metrics: {
        "control_center.snapshot_duration_ms_total": 84,
        "control_center.snapshot_requests_total": 2,
      },
    },
    safety: { secrets_exposed: false },
    server_time: 1_787_000_000,
  },
  workspace: {
    ok: true,
    counts: { files: 2, outputs: 1 },
    sections: {
      files: [{ id: "file-1", title: "notes.txt" }],
      outputs: [{ id: "output-1", title: "report.md" }],
    },
  },
  resourceManifest: {
    schema_version: 2,
    defaults: { outfit: "cat", emotion: "happy" },
    clients: { desktop_pet: { contract_version: "v0.1" } },
    characters: {
      outfits: [
        {
          id: "cat",
          name: "Cat",
          emotions: [
            { id: "happy", name: "Happy", path: "/assets/cat/happy.png" },
            { id: "sad", name: "Sad", path: "/assets/cat/sad.png" },
          ],
        },
        {
          id: "casual",
          name: "Casual",
          emotions: [{ id: "normal", name: "Normal", path: "/assets/casual/normal.png" }],
        },
      ],
    },
  },
  metrics: "akane_tracemalloc_current_bytes 1048576\n",
};

const unifiedSnapshot = {
  ok: true,
  status: "available",
  schemaVersion: 1,
  sourceKind: "backend",
  generatedAt: "2026-09-01T00:00:00Z",
  runtime,
};

function capabilityCatalog() {
  return {
    ok: true,
    status: "available",
    schemaVersion: 1,
    capabilities: [
      {
        id: "tool.send_file",
        kind: "tool",
        type: "tool",
        adapter: "tool_runtime",
        name: "Send File",
        enabled: true,
        status: "ready",
        risk: "medium",
      },
    ],
  };
}

function makeFetch({ unified = true, health = true } = {}) {
  const requests = [];
  const fetchImpl = async (url, options = {}) => {
    const value = String(url);
    requests.push({ url: value, options });
    const path = new URL(value).pathname;
    if (path === "/health") {
      return jsonResponse(health
        ? { status: "ok", root_binding: "valid", instance_id: "local-default" }
        : { status: "failed" }, health ? 200 : 503);
    }
    if (path === "/control-center/snapshot") {
      return unified ? jsonResponse(unifiedSnapshot) : jsonResponse({ ok: false }, 404);
    }
    if (path === "/capabilities") return jsonResponse(capabilityCatalog());
    if (path === "/capabilities/voice-profiles") {
      return jsonResponse({ ok: true, status: "available", voiceProfiles: [] });
    }
    if (path === "/capabilities/approval-requests") {
      return jsonResponse({ ok: true, status: "available", approvalRequests: [] });
    }
    if (path === "/desktop-pet/diagnostics") return jsonResponse(runtime.diagnostics);
    if (path === "/desktop-pet/workspace/summary") return jsonResponse(runtime.workspace);
    if (path === "/resource-manifest") return jsonResponse(runtime.resourceManifest);
    if (path === "/metrics") return textResponse(runtime.metrics);
    if (path === "/sessions/ensure") {
      return jsonResponse({
        ok: true,
        session_id: "control-center-lab",
        messages: [{ seq: 1, role: "assistant", content: "你好" }],
      });
    }
    if (path === "/sessions/messages") {
      return jsonResponse({ ok: true, messages: [{ seq: 0, role: "user", content: "更早的消息" }] });
    }
    return jsonResponse({ ok: false }, 404);
  };
  return { fetchImpl, requests };
}

const { fetchImpl, requests } = makeFetch();
const source = createBackendControlCenterSource({
  baseUrl: "http://control-center-runtime-probe",
  sessionId: "control-center-lab",
  profileUserId: "master",
  characterPackId: "runtime_pack",
  outfit: "cat",
  emotion: "happy",
  availableCharacterPacks: [{
    id: "runtime_pack",
    source: "F:\\private\\runtime_pack\\character.json",
    installedPath: "F:\\private\\runtime_pack",
    assetCount: 3,
    profile: {
      schema_version: "v0.2",
      identity: { id: "runtime_character", name: "Runtime Character", app_name: "Runtime Pack" },
      appearance: { default_outfit: "cat", default_emotion: "happy" },
    },
  }],
  fetchImpl,
});

const snapshot = await source.readSnapshot();
assert.ok(snapshot);
assert.equal(snapshot.sourceKind, CONTROL_CENTER_SOURCE_KIND.backend);
assert.equal(snapshot.backendUrl, "http://control-center-runtime-probe");
assert.equal(snapshot.fallbackReason, null);
assert.equal(snapshot.overviewRuntime.shell.status, "Akane 在线");
assert.equal(snapshot.overviewRuntime.connectionBadge, "连接正常");
assert.equal(snapshot.characterRuntime.selectedPackId, "runtime_pack");
assert.equal(snapshot.characterRuntime.selectedPack, "Runtime Pack");
assert.deepEqual(snapshot.characterRuntime.outfits.map((item) => item.id), ["cat", "casual"]);
assert.equal(snapshot.characterRuntime.emotions[0].id, "happy");
assert.equal("installedPath" in snapshot.characterRuntime.availablePacks[0], false);
assert.equal("source" in snapshot.characterRuntime.availablePacks[0], false);
assert.equal(snapshot.voiceRuntime.tts.enabled, true);
assert.ok(snapshot.abilitiesRuntime.modules.length > 0);
assert.ok(Object.keys(snapshot.advancedRuntime.systemStrip).length > 0);
assert.equal(snapshot.controlCenterRuntime.health.ok, true);
assert.ok(requests.some((item) => item.url.includes("/control-center/snapshot")));
assert.equal(
  requests.some((item) => item.url.includes("/desktop-pet/diagnostics")),
  false,
  "usable unified snapshot must not fan out to legacy runtime endpoints",
);

const chat = await source.readChatSession();
assert.equal(chat.messages[0].content, "你好");
assert.equal(source.getChatSessionError(), "");
const older = await source.readChatHistoryPage({ beforeSeq: 1, limit: 20 });
assert.equal(older.messages[0].content, "更早的消息");
assert.ok(requests.some((item) => item.url.includes("/sessions/ensure")));
assert.ok(requests.some((item) => item.url.includes("/sessions/messages")));

const { fetchImpl: fallbackFetch, requests: fallbackRequests } = makeFetch({ unified: false });
const fallbackSource = createBackendControlCenterSource({
  baseUrl: "http://control-center-fallback-probe",
  fetchImpl: fallbackFetch,
});
const fallbackSnapshot = await fallbackSource.readSnapshot();
assert.ok(fallbackSnapshot);
assert.equal(fallbackSnapshot.sourceKind, CONTROL_CENTER_SOURCE_KIND.backend);
assert.equal(fallbackSnapshot.fallbackReason, null);
assert.ok(fallbackRequests.some((item) => item.url.includes("/desktop-pet/diagnostics")));
assert.ok(fallbackRequests.some((item) => item.url.includes("/resource-manifest")));

const { fetchImpl: unavailableFetch } = makeFetch({ health: false });
const unavailableSource = createBackendControlCenterSource({
  baseUrl: "http://control-center-unavailable-probe",
  fetchImpl: unavailableFetch,
});
assert.equal(await unavailableSource.readSnapshot(), null);
assert.equal(unavailableSource.getFallbackReason(), "instance-health-unavailable");

const initial = source.readInitialState();
assert.equal(initial.sourceKind, CONTROL_CENTER_SOURCE_KIND.backend);
assert.equal(initial.fallbackReason, "backend-source-not-connected");
assert.equal(createControlCenterDataSource({ fetchImpl }).kind, CONTROL_CENTER_SOURCE_KIND.backend);
assert.throws(() => createControlCenterDataSource({ kind: "mock" }), /unsupported_control_center_source:mock/);

const normalized = createControlCenterRuntimeSnapshot({
  sourceKind: "backend",
  characterRuntime: null,
});
assert.deepEqual(normalized.characterRuntime, {});
assert.ok(normalized.generatedAt);

const settingsPatch = buildCharacterRuntimePatchFromSettingsSnapshot({
  state: { characterPackId: "mika", currentEmotion: "smile" },
  character: {
    packId: "mika",
    name: "Mika",
    appName: "Mika Companion",
    schemaVersion: "v0.2",
    defaultOutfit: "casual",
    defaultEmotion: "normal",
    availablePacks: [{
      id: "mika",
      name: "Mika",
      appName: "Mika Companion",
      installedPath: "F:\\private\\mika",
      source: "F:\\private\\mika\\character.json",
    }],
  },
  resource: {
    emotions: [{ id: "smile", name: "Smile", image: "asset://mika/smile.png" }],
  },
});
assert.equal(settingsPatch.selectedPackId, "mika");
assert.equal(settingsPatch.emotions[0].id, "smile");
assert.equal("installedPath" in settingsPatch.availablePacks[0], false);
assert.equal("source" in settingsPatch.availablePacks[0], false);

const publicText = JSON.stringify({
  snapshot,
  initial,
  settingsPatch,
});
assert.equal(publicText.includes("F:\\private"), false);

console.log(
  "control-center runtime probe passed: unified backend snapshot, legacy endpoint fallback, " +
  "chat hydration/history, unavailable backend, settings live patch, and private path filtering",
);
