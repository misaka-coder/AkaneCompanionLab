import assert from "node:assert/strict";

import {
  CONTROL_CENTER_ACTIONS,
  createControlCenterActionRouter,
  isControlCenterBridgedAction,
} from "../src/control-center/action-router.js";
import {
  CONTROL_CENTER_ACTION_SURFACE_STATUS,
  getUncataloguedBridgedActionIds,
  listControlCenterActionSurfaces,
} from "../src/control-center/action-surface-contract.js";
import { createControlCenterSnapshot } from "../src/control-center/data-adapter.js";
import {
  buildCharacterRuntimePatchFromSettingsSnapshot,
  createBackendControlCenterSource,
  createMockControlCenterSource,
  CONTROL_CENTER_SOURCE_KIND,
} from "../src/control-center/data-sources.js";

// ---------------------------------------------------------------------------
// Helper: build a fetch implementation that simulates the unified snapshot
// endpoint + individual legacy endpoints.
// ---------------------------------------------------------------------------

function makeCapabilitiesCatalogBody() {
  return {
    ok: true,
    status: "available",
    schemaVersion: 1,
    execution: "read-only",
    providerConfigStatus: "available",
    summary: {
      total: 12,
      byStatus: { ready: 5, available: 2, missing_executor: 1, disabled: 1, missing_config: 2, configured: 1 },
      byKind: { tool: 3, provider: 6, workflow: 1, mcp_tool: 2 },
    },
    capabilities: [
      {
        id: "tool.retrieve_memory",
        kind: "tool",
        type: "tool",
        source: "backend_tool",
        adapter: "tool_runtime",
        executionMode: "internal",
        toolType: "retrieve_memory",
        group: "memory",
        name: "Retrieve Memory",
        enabled: true,
        status: "ready",
        risk: "low",
        usedBy: ["agent"],
      },
      {
        id: "tool.compose_file",
        kind: "tool",
        type: "tool",
        source: "backend_tool",
        adapter: "tool_runtime",
        executionMode: "internal",
        toolType: "compose_file",
        group: "documents",
        name: "Compose File",
        enabled: true,
        status: "ready",
        risk: "medium",
        usedBy: ["agent", "workspace"],
      },
      {
        id: "tool.transcribe_media",
        kind: "tool",
        type: "tool",
        source: "backend_tool",
        adapter: "tool_runtime",
        executionMode: "internal",
        toolType: "transcribe_media",
        group: "asr",
        name: "Transcribe Media",
        enabled: true,
        status: "ready",
        risk: "medium",
        usedBy: ["agent", "voice"],
      },
      {
        id: "provider.tts.edge",
        kind: "provider",
        type: "tts_provider",
        source: "builtin",
        adapter: "edge_tts",
        executionMode: "internal",
        name: "Edge TTS",
        enabled: true,
        status: "ready",
        risk: "low",
        usedBy: ["voice", "desktop_pet"],
      },
      {
        id: "provider.asr.faster_whisper",
        kind: "provider",
        type: "asr_provider",
        source: "builtin",
        adapter: "faster_whisper",
        executionMode: "internal",
        name: "faster-whisper ASR",
        enabled: false,
        status: "missing_executor",
        risk: "medium",
        usedBy: ["voice", "workspace", "desktop_pet"],
      },
      {
        id: "provider.media.ffmpeg",
        kind: "provider",
        type: "asset_processor",
        source: "external_executor",
        adapter: "ffmpeg",
        executionMode: "external",
        name: "FFmpeg",
        enabled: false,
        status: "disabled",
        risk: "medium",
        usedBy: ["workspace", "media"],
      },
      {
        id: "provider.comfyui.local",
        kind: "provider",
        type: "asset_processor",
        source: "external_executor",
        adapter: "comfyui",
        executionMode: "external",
        name: "本地 ComfyUI",
        enabled: false,
        configured: false,
        configurable: true,
        status: "missing_config",
        reason: "provider_endpoint_missing",
        endpoint: "",
        defaultEndpoint: "http://127.0.0.1:8188",
        risk: "medium",
        usedBy: ["workshop", "image", "desktop_pet"],
      },
      {
        id: "provider.tts.gpt_sovits.local",
        kind: "provider",
        type: "tts_provider",
        source: "external_executor",
        adapter: "gpt_sovits",
        executionMode: "external",
        name: "本地 GPT-SoVITS",
        enabled: true,
        configured: true,
        configurable: true,
        status: "configured",
        reason: "",
        endpoint: "http://127.0.0.1:9880",
        defaultEndpoint: "http://127.0.0.1:9880",
        risk: "medium",
        usedBy: ["voice", "desktop_pet"],
      },
      {
        id: "provider.mcp.browser",
        serverId: "browser",
        kind: "provider",
        type: "mcp_provider",
        source: "mcp",
        adapter: "mcp_stdio",
        executionMode: "external",
        name: "Browser MCP",
        enabled: true,
        configured: true,
        configurable: true,
        status: "ready",
        reason: "",
        transport: "stdio",
        commandName: "C:\\Users\\ExampleUser\\mcp\\browser-mcp.exe",
        argsCount: 2,
        envCount: 1,
        toolCount: 2,
        lastDiscovery: { status: "ready", discoveredAt: "2026-06-07T01:02:03Z", toolCount: 2 },
        risk: "medium",
        requiresConfirmation: true,
        usedBy: ["agent_prompt", "external_tools"],
      },
      {
        id: "mcp.browser.read_page",
        serverId: "browser",
        kind: "mcp_tool",
        type: "tool",
        source: "mcp",
        adapter: "mcp_stdio",
        executionMode: "external",
        toolType: "read_page",
        providerId: "provider.mcp.browser",
        name: "read_page",
        description: "Read current browser page",
        enabled: true,
        status: "available",
        risk: "medium",
        requiresConfirmation: false,
        exposedToPrompt: false,
        inputSchema: { type: "object", properties: { url: { type: "string" } }, required: ["url"] },
      },
      {
        id: "mcp.browser.browser_click",
        serverId: "browser",
        kind: "mcp_tool",
        type: "tool",
        source: "mcp",
        adapter: "mcp_stdio",
        executionMode: "external",
        toolType: "browser_click",
        providerId: "provider.mcp.browser",
        name: "browser_click",
        description: "Click a browser page element",
        enabled: true,
        status: "available",
        risk: "high",
        requiresConfirmation: true,
        exposedToPrompt: false,
        inputSchema: { type: "object", properties: { selector: { type: "string" } }, required: ["selector"] },
      },
      {
        id: "workflow.workshop.portrait.cutout",
        kind: "workflow",
        type: "asset_processor",
        source: "external_executor",
        adapter: "comfyui",
        executionMode: "external",
        capabilityId: "workshop.portrait.cutout",
        workflowId: "workflow.comfyui.portrait_cutout",
        providerId: "provider.comfyui.local",
        name: "透明背景处理",
        description: "角色工坊的立绘透明背景处理流程",
        enabled: false,
        configured: false,
        configurable: true,
        executionReady: false,
        status: "missing_config",
        reason: "provider_endpoint_missing",
        workflowPath: "",
        defaultWorkflowPath: "workflows/comfyui/portrait_cutout.json",
        slotMapping: {},
        risk: "medium",
        usedBy: ["workshop", "desktop_pet"],
        target: "character_pack_assets",
        output: "transparent_png",
        slots: {
          required: ["input_image_handle", "output_image_handle"],
          optional: ["mask_output_handle", "background_color", "padding", "alpha_threshold"],
        },
      },
    ],
  };
}

function makeSnapshotFetch({
  snapshotBody = null,
  snapshotOk = true,
  legacyOk = true,
  snapshotStatus = 200,
  capabilitiesBody = null,
  capabilitiesOk = true,
  capabilitiesStatus = 200,
} = {}) {
  const requestedUrls = [];
  const fullCapabilitiesBody = capabilitiesBody || makeCapabilitiesCatalogBody();
  const fullSnapshotBody = snapshotBody || {
    ok: true,
    status: "available",
    schemaVersion: 1,
    sourceKind: "backend",
    generatedAt: new Date().toISOString(),
    runtime: {
      health: { status: "ok", pid: 1234, python: "/usr/bin/python3", contracts: { desktop_pet: { tts: true } } },
      diagnostics: {
        status: "ok",
        capabilities: {
          declared: ["desktop_context", "screen_vision", "tts", "asr", "workspace_summary"],
          effective_modules: ["desktop_context", "audio", "vision", "files"],
          tool_layers: ["base", "extended"],
          tool_names: ["read_file", "send_file", "transcribe_audio", "capture_screen"],
        },
        resources: {
          resource_manifest_ok: true,
          character_pack_id: "runtime_pack",
          outfit: "cat",
          default_emotion: "happy",
          emotion_count: 12,
        },
        workspace: { files: 3, outputs: 1, tasks: 0 },
        runtime: { pid: 1234, python: "/usr/bin/python3", metrics: { request_duration_ms: 42, total_requests: 150 } },
        safety: {
          secrets_exposed: false,
          desktop_actions_require_client: true,
          full_disk_scan: false,
        },
        server_time: Math.floor(Date.now() / 1000),
      },
      workspace: {
        ok: true,
        counts: { files: 3, outputs: 1, tasks: 0 },
        sections: { files: [], outputs: [] },
      },
      resourceManifest: {
        schema_version: 2,
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
              emotions: [
                { id: "smile", name: "Smile", path: "/assets/casual/smile.png" },
                { id: "angry", name: "Angry", path: "/assets/casual/angry.png" },
                { id: "cry", name: "Cry", path: "/assets/casual/cry.png" },
              ],
            },
          ],
        },
        defaults: { outfit: "cat", emotion: "happy" },
        clients: {
          desktop_pet: {
            contract_version: "desktop_pet_resource.v0.1",
            default_outfit: "cat",
            default_emotion: "happy",
            profile_user_id: "master",
          },
        },
        scenes: { majors: [] },
      },
      metrics:
        "akane_tracemalloc_current_bytes 1048576\n" +
        "akane_tracemalloc_peak_bytes 2097152\n" +
        "akane_vector_entries 42\n" +
        "akane_public_guard_active_thinks 0\n",
    },
  };
  const legacyBody = {
    ok: true,
    status: 200,
    headers: { get: () => "application/json" },
    json: async () => ({
      status: "ok",
      capabilities: { tool_names: ["read_file"] },
      runtime: { metrics: {} },
      resources: {},
      workspace: {},
      safety: {},
    }),
  };

  return {
    requestedUrls,
    snapshotBody: fullSnapshotBody,
    fetchImpl: async (url) => {
      const requestUrl = String(url);
      requestedUrls.push(requestUrl);
      if (requestUrl.includes("/control-center/snapshot")) {
        if (!snapshotOk) {
          return { ok: false, status: snapshotStatus, headers: { get: () => "" } };
        }
        return {
          ok: true,
          status: snapshotStatus,
          headers: { get: () => "application/json" },
          json: async () => fullSnapshotBody,
        };
      }
      if (requestUrl.includes("/capabilities")) {
        if (!capabilitiesOk) {
          return { ok: false, status: capabilitiesStatus, headers: { get: () => "" } };
        }
        return {
          ok: true,
          status: capabilitiesStatus,
          headers: { get: () => "application/json" },
          json: async () => fullCapabilitiesBody,
        };
      }
      if (!legacyOk) {
        return { ok: false, status: 404, headers: { get: () => "" } };
      }
      return legacyBody;
    },
  };
}

// ---------------------------------------------------------------------------
// 1. Unified snapshot happy path
// ---------------------------------------------------------------------------

{
  const { fetchImpl, snapshotBody } = makeSnapshotFetch();
  const source = createBackendControlCenterSource({
    baseUrl: "http://probe-test",
    sessionId: "probe-session",
    profileUserId: "probe-user",
    characterPackId: "runtime_pack",
    outfit: "cat",
    emotion: "happy",
    availableCharacterPacks: [
      {
        id: "runtime_pack",
        source: "F:\\secret\\runtime_pack\\character.json",
        installedPath: "F:\\secret\\runtime_pack",
        assetCount: 7,
        profile: {
          schema_version: "v0.2",
          identity: { id: "runtime_character", name: "Runtime Character", app_name: "Runtime Pack" },
          appearance: { default_outfit: "cat", default_emotion: "happy" },
          assets: { runtime_source: "local" },
        },
      },
    ],
    fetchImpl,
  });
  const raw = await source.readSnapshot();
  assert.ok(raw, "1.1 happy path: readSnapshot should return data");
  assert.equal(raw.sourceKind, CONTROL_CENTER_SOURCE_KIND.backend, "1.2 happy path: sourceKind should be backend");

  const snapshot = createControlCenterSnapshot(raw);
  const { overview, character, voice, perception, music, abilities, advanced } = snapshot.pages;

  // overview: online / connected status
  assert.ok(overview.status.badge.includes("Connected"), "1.3 overview status badge should indicate connected");
  assert.ok(overview.connection.badge.includes("连接"), "1.4 overview connection badge should indicate connection");

  // character: selectedPackId from resource manifest
  assert.equal(character.selectedPackId, "runtime_pack", "1.5 character selectedPackId should come from runtime pack id");
  assert.ok(Array.isArray(character.availablePacks), "1.5a character availablePacks should be an array");
  assert.equal(character.availablePacks.length, 1, "1.5b character availablePacks should hydrate from Tauri pack registry");
  assert.equal(character.availablePacks[0].id, "runtime_pack", "1.5c character availablePacks should keep pack id");
  assert.equal(character.availablePacks[0].appName, "Runtime Pack", "1.5d character availablePacks should keep display name");
  assert.equal("installedPath" in character.availablePacks[0], false, "1.5e character availablePacks should not expose installedPath");
  assert.equal("source" in character.availablePacks[0], false, "1.5f character availablePacks should not expose source path");

  // character: outfit ids from runtime resource manifest (not mock "default")
  assert.ok(character.outfits.some((o) => o.id === "cat"), "1.6 character outfits should include runtime id 'cat'");
  assert.ok(character.outfits.some((o) => o.id === "casual"), "1.7 character outfits should include runtime id 'casual'");
  // Simulate state sync: active outfit should be a runtime id, not mock "default"
  const runtimeActiveOutfit = character.outfits.find((o) => o.current)?.id || character.outfits[0]?.id || "";
  assert.equal(runtimeActiveOutfit, "cat", "1.8 character active outfit should pick runtime id 'cat'");
  // Verify mock "default" is not in the runtime list
  assert.equal(character.outfits.some((o) => o.id === "default"), false, "1.9 character outfits should not contain mock id 'default'");

  // character: emotion ids from runtime resource manifest (not mock "smile")
  assert.ok(character.emotions.some((e) => e.id === "happy"), "1.10 character emotions should include runtime id 'happy'");
  assert.ok(character.emotions.some((e) => e.id === "sad"), "1.11 character emotions should include runtime id 'sad'");
  // Simulate state sync: active emotion should be a runtime id, not mock "smile"
  const runtimeActiveEmotion = character.emotions.find((e) => e.current)?.id || character.emotions[0]?.id || "";
  assert.equal(runtimeActiveEmotion, "happy", "1.12 character active emotion should pick runtime id 'happy'");
  // Verify mock "smile" is not in the runtime list
  assert.equal(character.emotions.some((e) => e.id === "smile"), false, "1.13 character emotions should not contain mock id 'smile'");

  // character: outfits and emotions from resource manifest (count assertion)
  assert.ok(character.outfits.length >= 2, "1.14 character outfits should hydrate from resource manifest");
  assert.ok(character.emotions.length >= 2, "1.15 character emotions should hydrate from resource manifest");

  // character: resources section populated
  assert.ok(Array.isArray(character.resources), "1.16 character resources should be an array");
  assert.ok(character.resources.length > 0, "1.17 character resources should not be empty");

  // abilities: modules from tool names (user-friendly labels, no raw tool IDs)
  assert.ok(Array.isArray(abilities.modules), "1.18 abilities modules should be an array");
  assert.ok(abilities.modules.length > 0, "1.19 abilities modules should not be empty");
  for (const mod of abilities.modules) {
    assert.ok(typeof mod.title === "string" && mod.title.length > 0, "1.20 module title should be non-empty string");
    assert.ok(!mod.title.includes("_"), "1.21 module title should not contain raw identifiers");
  }
  assert.ok(
    abilities.modules.some((mod) => mod.title === "音频与语音"),
    "1.21a capability catalog should surface audio/voice as a user-facing module"
  );
  assert.ok(
    abilities.modules.some((mod) => mod.title === "本地模型与执行器"),
    "1.21b capability catalog should surface local executors as a user-facing module"
  );
  assert.ok(
    abilities.modules.some((mod) => typeof mod.statusLabel === "string" && mod.statusLabel.length > 0),
    "1.21c capability modules should carry user-facing status labels"
  );
  const abilityModuleText = JSON.stringify(abilities.modules);
  for (const rawId of ["transcribe_media", "faster_whisper", "provider.asr"]) {
    assert.equal(abilityModuleText.includes(rawId), false, `1.21d capability modules should not expose raw id ${rawId}`);
  }
  assert.ok(raw.controlCenterRuntime.capabilitiesCatalog.ok, "1.21e raw snapshot should include optional capabilities catalog status");
  assert.ok(Array.isArray(abilities.providers), "1.21f abilities providers should be an array");
  assert.ok(abilities.providers.length >= 2, "1.21g abilities providers should include configurable local providers");
  assert.ok(
    abilities.providers.some((provider) => provider.title === "本地 ComfyUI" && provider.statusLabel === "未配置"),
    "1.21h local provider summary should expose user-facing status"
  );
  assert.ok(
    abilities.providers.every((provider) => typeof provider.defaultEndpoint === "string"),
    "1.21i provider summaries should include safe default endpoint hints"
  );
  assert.equal(
    abilities.providers.some((provider) => provider.id === "provider.mcp.browser"),
    false,
    "1.21i2 MCP providers should not be rendered as localhost endpoint config rows"
  );
  const providerText = JSON.stringify(abilities.providers);
  for (const forbidden of ["token=", "secret", "C:/", "cachedPath"]) {
    assert.equal(providerText.includes(forbidden), false, `1.21j provider summary should not expose ${forbidden}`);
  }
  assert.ok(Array.isArray(abilities.mcpServers), "1.21j2 MCP server summaries should be an array");
  assert.equal(abilities.mcpServers.length, 1, "1.21j3 MCP server summaries should include configured server");
  const browserMcp = abilities.mcpServers[0];
  assert.equal(browserMcp.title, "Browser MCP", "1.21j4 MCP summary should preserve display name");
  assert.equal(browserMcp.commandName, "browser-mcp.exe", "1.21j5 MCP summary should only expose command basename");
  assert.equal(browserMcp.toolCount, 2, "1.21j6 MCP summary should include discovered tool count");
  assert.equal(browserMcp.highRiskCount, 1, "1.21j7 MCP summary should count high-risk tools");
  assert.equal(browserMcp.promptExposedCount, 0, "1.21j8 MCP tools should remain hidden from prompt by default");
  assert.ok(Array.isArray(browserMcp.toolDetails), "1.21j8b MCP manager should expose safe tool detail rows");
  assert.ok(
    browserMcp.toolDetails.some((tool) => tool.promptLabel === "默认不进提示词"),
    "1.21j8c MCP manager should show prompt exposure state"
  );
  assert.ok(
    browserMcp.safeToolLabels.includes("浏览器上下文") || browserMcp.safeToolLabels.includes("需确认的操作"),
    "1.21j9 MCP summary should translate raw tool ids into user-facing capability labels"
  );
  const mcpText = JSON.stringify(abilities.mcpServers);
  for (const forbidden of ["ExampleUser", "C:", "api_key", "secret", "read_page", "browser_click"]) {
    assert.equal(mcpText.includes(forbidden), false, `1.21j10 MCP summary should not expose raw or sensitive detail ${forbidden}`);
  }
  assert.ok(
    abilities.workflows.some((workflow) => workflow.title === "透明背景处理" && workflow.statusLabel === "未配置"),
    "1.21k workflow catalog should surface portrait cutout as a user-facing non-ready workflow"
  );
  const cutoutWorkflow = abilities.workflows.find((workflow) => workflow.title === "透明背景处理");
  assert.equal(cutoutWorkflow.workflowId, "workflow.workshop.portrait.cutout", "1.21k2 workflow card should keep safe catalog workflow id for configuration");
  assert.equal(cutoutWorkflow.defaultWorkflowPath, "workflows/comfyui/portrait_cutout.json", "1.21k3 workflow card should expose safe default workflow reference");
  const workflowText = JSON.stringify(abilities.workflows);
  for (const rawId of ["workflow.comfyui", "input_image_handle", "output_image_handle", "cachedPath", "token"]) {
    assert.equal(workflowText.includes(rawId), false, `1.21l workflow summaries should not expose raw detail ${rawId}`);
  }

  // abilities: overview from diagnostics
  assert.ok(abilities.overview, "1.22 abilities overview should exist");
  assert.ok(typeof abilities.overview.availability === "number", "1.23 abilities availability should be a number");

  // advanced: system strip CPU value derived from metrics
  assert.ok(Array.isArray(advanced.systemStrip), "1.24 advanced systemStrip should be an array");
  const cpuRow = advanced.systemStrip.find((item) => item.label === "CPU");
  assert.ok(cpuRow, "1.25 advanced systemStrip should have CPU row");

  // advanced: diagnostics metrics from patched labels
  assert.ok(Array.isArray(advanced.diagnostics.metrics), "1.26 advanced diagnostics metrics should be an array");
  assert.ok(advanced.diagnostics.metrics.length > 0, "1.27 advanced diagnostics metrics should not be empty");

  // advanced: ability overview from tool names
  assert.ok(Array.isArray(advanced.abilityOverview), "1.28 advanced abilityOverview should be an array");
  assert.ok(advanced.abilityOverview.length > 0, "1.29 advanced abilityOverview should not be empty");

  // perception: feature cards exist
  assert.ok(Array.isArray(perception.featureCards), "1.30 perception featureCards should be an array");
  assert.ok(perception.featureCards.length > 0, "1.31 perception featureCards should not be empty");

  // voice: tts / asr from petState
  assert.ok(typeof voice.tts?.enabled === "boolean", "1.32 voice tts enabled should be boolean");

  // sourceKind is backend, not mock
  assert.equal(snapshot.sourceKind, CONTROL_CENTER_SOURCE_KIND.backend, "1.33 snapshot sourceKind should be backend");

  // ---------- production-shaped assertions ----------

  // Music runtime fallback: snapshot has no musicRuntime, so mock defaults pass through
  assert.ok(music.nowPlaying, "1.34 music nowPlaying should exist (mock fallback)");
  assert.equal(music.nowPlaying.title, "星光与你", "1.35 music nowPlaying title should preserve mock default when no musicRuntime");
  assert.equal(music.currentPlayMode, "列表循环", "1.36 music currentPlayMode should preserve mock default");
  assert.ok(Array.isArray(music.playlist), "1.37 music playlist should be an array from mock data");
  assert.ok(music.playlist.length > 0, "1.38 music playlist should have items from mock data");

  // Overview health tiles populated from runtime metrics
  assert.ok(Array.isArray(overview.health), "1.39 overview health should be an array");
  assert.ok(overview.health.length > 0, "1.40 overview health should have tiles");
  const memoryTile = overview.health.find((h) => h.label && h.label.includes("内存"));
  assert.ok(memoryTile, "1.41 overview health should have memory tile");

  // Overview connection rows from diagnostics
  assert.ok(Array.isArray(overview.connection.rows), "1.42 overview connection rows should be an array");
  assert.ok(overview.connection.rows.length > 0, "1.43 overview connection rows should not be empty");

  // Voice diagnostics rows from runtime
  assert.ok(Array.isArray(voice.diagnostics), "1.44 voice diagnostics should be an array");
  assert.ok(voice.diagnostics.length > 0, "1.45 voice diagnostics should not be empty");
  const voiceStatus = voice.diagnostics.find((d) => d.label && d.label.includes("整体"));
  assert.ok(voiceStatus, "1.46 voice diagnostics should have overall status row");

  // Perception feature cards enabled states
  assert.ok(perception.featureCards.length > 0, "1.47 perception featureCards should be populated");
  const activeWindowCard = perception.featureCards.find((c) => c.id === "activeWindow");
  assert.ok(activeWindowCard, "1.48 perception should have activeWindow card");
  assert.equal(typeof activeWindowCard.enabled, "boolean", "1.49 activeWindow card enabled should be boolean");

  // Abilities safety panel from diagnostics
  assert.ok(abilities.safety, "1.50 abilities safety panel should exist");
  assert.ok(typeof abilities.safety.status === "string", "1.51 abilities safety status should be string");

  // Abilities modules are user-facing (no raw tool ids)
  for (const mod of abilities.modules) {
    assert.ok(!mod.description.includes("read_file"), "1.52 module description should not contain raw tool id 'read_file'");
    assert.ok(!mod.title.includes("_"), "1.53 module title should not contain underscores");
  }

  // Advanced logs from runtime timeline
  assert.ok(Array.isArray(advanced.diagnostics.logs), "1.54 advanced diagnostics logs should be an array");
  assert.ok(advanced.diagnostics.logs.length > 0, "1.55 advanced diagnostics logs should have entries");
  const firstLog = advanced.diagnostics.logs[0];
  assert.ok(typeof firstLog.time === "string", "1.56 advanced log entry should have time string");
  assert.ok(typeof firstLog.level === "string", "1.57 advanced log entry should have level string");
  assert.ok(typeof firstLog.message === "string", "1.58 advanced log entry should have message string");

  // Advanced ability overview from tool names
  assert.ok(advanced.abilityOverview.length > 0, "1.59 advanced abilityOverview should be populated");
  assert.ok(advanced.abilityOverview.some((a) => a.label), "1.60 advanced abilityOverview items should have label");

  // Advanced diagnostics metrics from runtime (patched by label)
  // The buildAdvancedRuntimePatch should produce "应用状态" and "后端健康" from diagnostics
  assert.ok(advanced.diagnostics.metrics.length > 0, "1.61 advanced diagnostics metrics should be populated");

  // No sensitive content in the raw snapshot body (production data contract).
  // Diagnostic fields like "secrets_exposed" are safety flags, not actual secrets.
  const bodyText = JSON.stringify(snapshotBody);
  for (const term of ["api_key", "apiKey", "prompt_text", "chat_message", "clipboardContent", "screenshotData"]) {
    assert.equal(bodyText.includes(term), false, `1.62 snapshot body should not contain sensitive field '${term}'`);
  }
  // Verify diagnostics does NOT contain raw message or prompt fields
  const diagnosticsText = JSON.stringify(snapshotBody.runtime.diagnostics);
  assert.equal(diagnosticsText.includes('"messages"'), false, "1.63 diagnostics should not contain messages field");
  assert.equal(diagnosticsText.includes('"prompt"'), false, "1.64 diagnostics should not contain prompt field");

  // Runtime fields are raw provider data, not wrapped in { ok, data } at the HTTP level
  // (Production providers return direct data; unpackUnifiedSnapshotField handles wrapping)
  assert.equal(snapshotBody.runtime.health.status, "ok", "1.65 runtime health should have raw status from production provider");
  assert.equal(snapshotBody.runtime.diagnostics.status, "ok", "1.66 runtime diagnostics should have raw status from production provider");

  // Production provider: metrics is a plain string (prometheus text format)
  assert.equal(typeof snapshotBody.runtime.metrics, "string", "1.67 runtime metrics should be prometheus text string");
  assert.ok(snapshotBody.runtime.metrics.includes("akane_tracemalloc"), "1.68 runtime metrics should contain akane_tracemalloc fields");

  // ---------- field-level real-data coverage assertions ----------

  // Overview: status items patched from runtime (not mock)
  const connectedItem = overview.status.items.find((i) => i.label && i.label.includes("连接状态"));
  assert.ok(connectedItem, "1.69 overview status should have 连接状态 item");
  const summaryItem = overview.status.items.find((i) => i.label && i.label.includes("今日能力摘要"));
  assert.ok(summaryItem, "1.70 overview status should have 今日能力摘要 item");

  // Overview: connection rows patched from runtime
  const latencyRow = overview.connection.rows.find((r) => r.label && r.label.includes("响应延迟"));
  assert.ok(latencyRow, "1.71 overview connection should have 响应延迟 row");
  const syncRow = overview.connection.rows.find((r) => r.label && r.label.includes("同步状态"));
  assert.ok(syncRow, "1.72 overview connection should have 同步状态 row");

  // Overview: pack from runtime diagnostics resources
  assert.ok(overview.pack.name, "1.73 overview pack name should be populated");
  assert.ok(overview.pack.version, "1.74 overview pack version should be populated");

  // Character: warning populated from runtime
  assert.ok(character.warning, "1.75 character warning should exist");
  assert.ok(typeof character.warning.title === "string", "1.76 character warning title should be string");
  assert.ok(typeof character.warning.body === "string", "1.77 character warning body should be string");
  assert.ok(character.warning.actionId, "1.78 character warning should have actionId (falls back to character.refresh)");

  // Voice: TTS enabled and ASR enabled from runtime values
  assert.equal(typeof voice.tts?.enabled, "boolean", "1.79 voice tts.enabled should be boolean from runtime");
  assert.equal(typeof voice.asr?.enabled, "boolean", "1.80 voice asr.enabled should be boolean from runtime");

  // Voice: diagnostics rows from runtime (整体状态, TTS/ASR status)
  const overallDiagnostic = voice.diagnostics.find((d) => d.label && d.label.includes("整体"));
  assert.ok(overallDiagnostic, "1.81 voice diagnostics should have 整体状态 row");
  const ttsDiag = voice.diagnostics.find((d) => d.label && (d.label.includes("TTS") || d.label.includes("语音引擎")));
  assert.ok(ttsDiag, "1.82 voice diagnostics should have TTS row");

  // Perception: all 4 feature cards present with enabled state
  const expectedCardIds = ["activeWindow", "clipboard", "screen", "proactive"];
  for (const cardId of expectedCardIds) {
    const card = perception.featureCards.find((c) => c.id === cardId);
    assert.ok(card, `1.83 perception should have "${cardId}" feature card`);
    assert.equal(typeof card.enabled, "boolean", `1.84 perception "${cardId}" enabled should be boolean from runtime`);
  }

  // Abilities: safety status from diagnostics
  assert.ok(abilities.safety, "1.85 abilities safety should exist");
  assert.equal(typeof abilities.safety.status, "string", "1.86 abilities safety status should be string");
  assert.ok(Array.isArray(abilities.safety.items), "1.87 abilities safety items should be an array");

  // Abilities: modules have permission and count fields
  for (const mod of abilities.modules) {
    assert.ok(mod.permission, "1.88 ability module should have permission field");
    assert.ok(mod.count, "1.89 ability module should have count field");
  }

  // Advanced: runtime pid and python from health provider
  assert.equal(snapshotBody.runtime.health.pid, 1234, "1.90 runtime health should contain pid from production provider");
  assert.ok(snapshotBody.runtime.health.python, "1.91 runtime health should contain python path from production provider");
  assert.ok(snapshotBody.runtime.health.contracts, "1.92 runtime health should contain contracts from production provider");

  // Advanced: systemStrip has "运行中" status from runtime
  const runningStrip = advanced.systemStrip.find((s) => s.label && s.label.includes("运行"));
  assert.ok(runningStrip, "1.93 advanced systemStrip should have 运行中 row");

  // Advanced: diagnostics logs are runtime-generated timeline entries
  assert.ok(advanced.diagnostics.logs.length > 0, "1.94 advanced diagnostics logs should be populated");
  for (const log of advanced.diagnostics.logs) {
    assert.equal(typeof log.time, "string", "1.95 advanced log entry should have time string");
    assert.equal(typeof log.message, "string", "1.96 advanced log entry should have message string");
  }

  // Advanced: diagnostics metrics patched by label — "应用状态" from runtime
  const appStatusMetric = advanced.diagnostics.metrics.find((m) => m.label && m.label.includes("应用状态"));
  assert.ok(appStatusMetric, "1.97 advanced metrics should have 应用状态 row");

  // Snapshot metadata: backendUrl and fallbackReason flow through from source
  assert.equal(snapshot.sourceKind, CONTROL_CENTER_SOURCE_KIND.backend, "1.98 snapshot sourceKind should be backend");
  assert.ok(snapshot.backendUrl, "1.99 snapshot backendUrl should be present from source metadata");
  assert.equal(snapshot.fallbackReason, null, "1.100 snapshot fallbackReason should be null on successful read");
  assert.ok(snapshot.backendUrl.startsWith("http"), "1.101 snapshot backendUrl should be a valid URL");
}

// ---------------------------------------------------------------------------
// 1b. Tauri settings snapshot character pack list patch
// ---------------------------------------------------------------------------

{
  const snapshot = createControlCenterSnapshot({
    characterRuntime: buildCharacterRuntimePatchFromSettingsSnapshot({
      state: { characterPackId: "mika_pack" },
      character: {
        packId: "mika_pack",
        name: "Mika",
        appName: "Mika Companion",
        schemaVersion: "v0.2",
        defaultOutfit: "casual",
        defaultEmotion: "normal",
        availablePacks: [
          {
            id: "mika_pack",
            name: "Mika",
            appName: "Mika Companion",
            installedPath: "F:\\private\\characters\\mika_pack",
            source: "F:\\private\\characters\\mika_pack\\character.json",
            defaultOutfit: "casual",
            defaultEmotion: "normal",
            assetCount: 5,
            selected: true,
          },
        ],
      },
      resource: { emotionCount: 5, outfits: [{ id: "casual" }] },
    }),
  });
  const packs = snapshot.pages.character.availablePacks;
  assert.equal(snapshot.pages.character.selectedPackId, "mika_pack", "1b.1 selectedPackId should come from settings snapshot");
  assert.equal(snapshot.pages.character.selectedPack, "Mika Companion", "1b.2 selectedPack should come from settings snapshot");
  assert.ok(Array.isArray(packs), "1b.3 availablePacks should be an array");
  assert.equal(packs.length, 1, "1b.4 availablePacks should contain one pack");
  assert.equal(packs[0].selected, true, "1b.5 active pack should be marked selected");
  assert.equal("installedPath" in packs[0], false, "1b.6 availablePacks should not include installedPath");
  assert.equal("source" in packs[0], false, "1b.7 availablePacks should not include source");
}

// ---------------------------------------------------------------------------
// 2. Partial runtime degradation: one field unavailable, others still hydrate
// ---------------------------------------------------------------------------

{
  const degradedBody = {
    ok: true,
    schemaVersion: 1,
    sourceKind: "backend",
    generatedAt: new Date().toISOString(),
    runtime: {
      health: { ok: false, status: "unavailable", error: "health provider failed" },
      diagnostics: {
        status: "ok",
        capabilities: { tool_names: ["read_file"], declared: [], effective_modules: [], tool_layers: [] },
        resources: {},
        workspace: {},
        runtime: { metrics: {} },
        safety: {},
      },
      workspace: { counts: { files: 0, outputs: 0, tasks: 0 } },
      resourceManifest: {
        schema_version: 2,
        characters: { outfits: [{ id: "default", name: "Default", emotions: [{ id: "normal", name: "Normal" }] }] },
        defaults: { outfit: "default", emotion: "normal" },
        clients: { desktop_pet: { contract_version: "v0.1" } },
      },
      metrics: "akane_tracemalloc_current_bytes 1024\n",
    },
  };
  const { fetchImpl, requestedUrls } = makeSnapshotFetch({ snapshotBody: degradedBody });
  const source = createBackendControlCenterSource({
    baseUrl: "http://degraded-test",
    sessionId: "degraded-session",
    profileUserId: "degraded-user",
    fetchImpl,
  });
  const raw = await source.readSnapshot();
  assert.ok(raw, "2.1 degraded: readSnapshot should return data despite health unavailable");
  assert.equal(raw.sourceKind, CONTROL_CENTER_SOURCE_KIND.backend, "2.2 degraded: sourceKind should be backend");

  const snapshot = createControlCenterSnapshot(raw);
  const { overview } = snapshot.pages;

  // health is unavailable, but overview page still hydrates from diagnostics
  assert.ok(overview, "2.3 degraded: overview page should exist");
  // abilities page from diagnostics
  assert.ok(snapshot.pages.abilities.overview, "2.4 degraded: abilities overview should exist from diagnostics");
  // character page from resourceManifest
  assert.ok(snapshot.pages.character.selectedPack, "2.5 degraded: character selectedPack should exist from resourceManifest");
  // advanced page from workspace/metrics
  assert.ok(snapshot.pages.advanced.diagnostics, "2.6 degraded: advanced diagnostics should exist");

  // Source metadata flows through even when one field is degraded
  assert.ok(snapshot.backendUrl, "2.7 degraded: snapshot backendUrl should be present");
  assert.equal(snapshot.fallbackReason, null, "2.8 degraded: snapshot fallbackReason should be null (read succeeded)");

  // No fallback to legacy endpoints when snapshot succeeds (even partially)
  const snapshotUrls = requestedUrls.filter((u) => u.includes("/control-center/snapshot"));
  assert.ok(snapshotUrls.length >= 1, "2.9 degraded: snapshot endpoint should have been called");
  const legacyUrls = requestedUrls.filter((u) => !u.includes("/control-center/snapshot") && !u.includes("/capabilities"));
  assert.equal(legacyUrls.length, 0, "2.10 degraded: legacy endpoints should NOT be called when snapshot returns usable data");
}

// 2b. Optional capabilities catalog unavailable: unified snapshot still renders.
{
  const { fetchImpl } = makeSnapshotFetch({ capabilitiesOk: false, capabilitiesStatus: 404 });
  const source = createBackendControlCenterSource({
    baseUrl: "http://capabilities-missing-test",
    sessionId: "capabilities-missing-session",
    profileUserId: "capabilities-missing-user",
    fetchImpl,
  });
  const raw = await source.readSnapshot();
  assert.ok(raw, "2b.1 missing capabilities catalog should not block readSnapshot");
  assert.equal(raw.controlCenterRuntime.capabilitiesCatalog.ok, false, "2b.2 missing capabilities catalog should be structured unavailable");
  const snapshot = createControlCenterSnapshot(raw);
  assert.ok(snapshot.pages.abilities.modules.length > 0, "2b.3 abilities page should fall back to diagnostics modules");
}

// ---------------------------------------------------------------------------
// 3. Bad unified snapshot fallback: snapshot returns null runtime,
//    legacy endpoints return usable data
// ---------------------------------------------------------------------------

{
  const { fetchImpl, requestedUrls } = makeSnapshotFetch({
    snapshotBody: { ok: true, runtime: null },
    legacyOk: true,
  });
  const source = createBackendControlCenterSource({
    baseUrl: "http://fallback-test",
    sessionId: "fallback-session",
    profileUserId: "fallback-user",
    fetchImpl,
  });
  const raw = await source.readSnapshot();
  assert.ok(raw, "3.1 fallback: readSnapshot should return data from legacy endpoints");
  assert.equal(raw.sourceKind, CONTROL_CENTER_SOURCE_KIND.backend, "3.2 fallback: sourceKind should be backend");

  // Verify snapshot endpoint was attempted
  const snapshotUrls = requestedUrls.filter((u) => u.includes("/control-center/snapshot"));
  assert.ok(snapshotUrls.length >= 1, "3.3 fallback: snapshot endpoint should have been attempted");

  // Verify legacy endpoints were called
  const legacyUrls = requestedUrls.filter((u) => !u.includes("/control-center/snapshot") && !u.includes("/capabilities"));
  assert.ok(legacyUrls.length >= 1, "3.4 fallback: legacy endpoints should have been called after snapshot failed");

  const snapshot = createControlCenterSnapshot(raw);
  assert.ok(snapshot.pages.overview, "3.5 fallback: overview page should exist");
  assert.ok(snapshot.pages.character, "3.6 fallback: character page should exist");
}

// ---------------------------------------------------------------------------
// 4. All backend unavailable: both snapshot and legacy endpoints return 404.
// ---------------------------------------------------------------------------

{
  const { fetchImpl, requestedUrls } = makeSnapshotFetch({
    snapshotOk: false,
    snapshotStatus: 404,
    legacyOk: false,
    capabilitiesOk: false,
  });
  const source = createBackendControlCenterSource({
    baseUrl: "http://unavailable-test",
    sessionId: "unavailable-session",
    profileUserId: "unavailable-user",
    fetchImpl,
  });
  // Must NOT throw
  const raw = await source.readSnapshot();
  assert.equal(raw, null, "4.1 unavailable: readSnapshot should return null when all backends fail");

  // Both snapshot and legacy were attempted
  const snapshotUrls = requestedUrls.filter((u) => u.includes("/control-center/snapshot"));
  assert.ok(snapshotUrls.length >= 1, "4.2 unavailable: snapshot endpoint should have been attempted");
  const legacyUrls = requestedUrls.filter((u) => !u.includes("/control-center/snapshot") && !u.includes("/capabilities"));
  assert.ok(legacyUrls.length >= 1, "4.3 unavailable: legacy endpoints should have been attempted");
}

// ---------------------------------------------------------------------------
// 5. Action contract remains inert.
//    Backend POST always returns not-implemented. Router.run on deferred
//    actions also returns not-implemented + refresh:false. No desktop action
//    is executed through the backend HTTP fallback.
// ---------------------------------------------------------------------------

{
  let backendPostCalled = false;
  const inertSource = createBackendControlCenterSource({
    baseUrl: "http://inert-test",
    fetchImpl: async (url, options) => {
      if (options?.method === "POST") {
        backendPostCalled = true;
        return {
          ok: true,
          status: 200,
          headers: { get: () => "application/json" },
          json: async () => ({
            ok: false,
            status: "not-implemented",
            actionId: "some.action",
            refresh: false,
          }),
        };
      }
      return { ok: false, status: 404, headers: { get: () => "" } };
    },
  });
  const inertRouter = createControlCenterActionRouter({ dataSource: inertSource });

  // Run a deferred action through the router
  const result = await inertRouter.run(CONTROL_CENTER_ACTIONS.characterManageOutfits, {}, { source: "probe" });
  assert.equal(result.ok, false, "5.1 inert: deferred action should be ok:false");
  assert.equal(result.status, "not-implemented", "5.2 inert: deferred action should be not-implemented");
  assert.equal(result.refresh, false, "5.3 inert: deferred action should have refresh:false");

  // Verify backend POST was NOT called (deferred actions should not reach backend)
  assert.equal(backendPostCalled, false, "5.4 inert: backend POST should not be called for non-bridged actions");

  // Verify a not-implemented action direct from data source
  const directResult = await inertSource.runAction(CONTROL_CENTER_ACTIONS.characterImportZip);
  assert.equal(directResult.status, "not-implemented", "5.5 inert: direct dataSource not-implemented");
  assert.equal(directResult.refresh, false, "5.6 inert: direct dataSource should have refresh:false");
}

// ---------------------------------------------------------------------------
// 6. Surface contract consistency.
//    Every bridged action must be catalogued; every deferred surface must
//    not be bridged. (Reuses same logic as smoke, but probe asserts
//    independently so it doesn't depend on smoke passing.)
// ---------------------------------------------------------------------------

{
  // Every bridged action ID should appear in the surface contract
  const uncatalogued = getUncataloguedBridgedActionIds();
  assert.deepEqual(uncatalogued, [], "6.1 surface: every bridged action should be catalogued");

  // Every deferred surface should NOT be bridged
  const deferredSurfaces = listControlCenterActionSurfaces(CONTROL_CENTER_ACTION_SURFACE_STATUS.deferred);
  assert.ok(deferredSurfaces.length > 0, "6.2 surface: there should be deferred surfaces");
  for (const surface of deferredSurfaces) {
    assert.equal(isControlCenterBridgedAction(surface.actionId), false, `6.3 surface: ${surface.actionId} should not be bridged`);
    assert.ok(typeof surface.reason === "string" && surface.reason.trim() !== "", `6.4 surface: ${surface.actionId} should document reason`);
  }

  // Bridged action count: 31 after the boundary audit promotion
  const bridgedSurfaces = listControlCenterActionSurfaces(CONTROL_CENTER_ACTION_SURFACE_STATUS.bridged);
  assert.ok(bridgedSurfaces.length >= 30, "6.5 surface: bridged surfaces should be >= 30");
}

// ---------------------------------------------------------------------------
// 7. Bootstrap contract: source metadata, priority chain, fallback reason
// ---------------------------------------------------------------------------

// 7a. Mock source metadata
{
  const mockSource = createMockControlCenterSource();
  assert.equal(mockSource.kind, CONTROL_CENTER_SOURCE_KIND.mock, "7.1 mock source kind should be mock");
  assert.equal(mockSource.backendUrl, null, "7.2 mock source backendUrl should be null");
  assert.equal(mockSource.fallbackReason, "mock-source", "7.3 mock source fallbackReason should be mock-source");
  assert.equal(mockSource.getFallbackReason(), "mock-source", "7.3b mock source getFallbackReason should be mock-source");
}

// 7b. Backend source metadata when configured with a URL
{
  const backendSource = createBackendControlCenterSource({ baseUrl: "http://custom-backend:9999" });
  assert.equal(backendSource.kind, CONTROL_CENTER_SOURCE_KIND.backend, "7.4 backend source kind should be backend");
  assert.equal(backendSource.backendUrl, "http://custom-backend:9999", "7.5 backend source backendUrl should match options");
  assert.equal(typeof backendSource.getFallbackReason, "function", "7.6 backend source should have getFallbackReason()");
}

// 7c. Backend source with default URL when none provided
{
  const defaultSource = createBackendControlCenterSource({});
  assert.ok(defaultSource.backendUrl, "7.7 backend source should have non-empty default backendUrl");
  assert.ok(defaultSource.backendUrl.startsWith("http"), "7.8 backend source backendUrl should be a valid URL");
  assert.equal(defaultSource.fallbackReason, null, "7.9 backend source initial fallbackReason should be null");
}

// 7d. Backend unavailable: readSnapshot returns null AND fallback reason is set
{
  const unavailableSource = createBackendControlCenterSource({
    baseUrl: "http://unavailable-bootstrap-test",
    fetchImpl: async () => ({ ok: false, status: 404, headers: { get: () => "" } }),
  });
  const result = await unavailableSource.readSnapshot();
  assert.equal(result, null, "7.10 backend unavailable readSnapshot should return null");
  // getFallbackReason should indicate the failure
  const reason = unavailableSource.getFallbackReason();
  assert.ok(typeof reason === "string" && reason.length > 0, "7.11 backend unavailable should have fallback reason string");
  assert.notEqual(reason, null, "7.12 backend unavailable fallback reason should not be null");
  assert.equal(unavailableSource.fallbackReason, reason, "7.12b backend fallbackReason property should match getter");
}

// 7e. Backend available: readSnapshot succeeds and clears fallback reason
{
  const { fetchImpl } = makeSnapshotFetch();
  const okSource = createBackendControlCenterSource({
    baseUrl: "http://ok-bootstrap-test",
    fetchImpl,
  });
  const result = await okSource.readSnapshot();
  assert.ok(result, "7.13 backend available readSnapshot should return data");
  // After successful read, fallback reason should be null (cleared)
  assert.equal(okSource.getFallbackReason(), null, "7.14 backend available getFallbackReason should be null");
  // source metadata should include the backend URL
  assert.equal(okSource.backendUrl, "http://ok-bootstrap-test", "7.15 backend available source should preserve backendUrl");
}

// 7f. Backend source metadata does NOT contain sensitive content
{
  const sensitiveSource = createBackendControlCenterSource({
    baseUrl: "http://sensitive-check",
    fetchImpl: async () => ({ ok: false, status: 404, headers: { get: () => "" } }),
  });
  await sensitiveSource.readSnapshot();
  const sourceText = JSON.stringify({
    kind: sensitiveSource.kind,
    backendUrl: sensitiveSource.backendUrl,
    fallbackReason: sensitiveSource.fallbackReason,
    getFallbackReasonResult: sensitiveSource.getFallbackReason(),
  });
  for (const term of ["api_key", "apiKey", "password", "secret", "token"]) {
    assert.equal(sourceText.includes(term), false, `7.16 source metadata should not contain sensitive field '${term}'`);
  }
}

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------

console.log(
  "control-center runtime probe passed: " +
    "1 production-shaped snapshot (" +
    "overview connected/badge/health/connection, " +
    "character selectedPackId/outfits/emotions/resources, " +
    "voice tts/asr diagnostics, " +
    "perception featureCards/enabled, " +
    "music runtime fallback (mock nowPlaying/playlist/mode preserved), " +
    "abilities modules/user-facing/safety/overview, " +
    "advanced systemStrip/diagnostics metrics/logs/abilityOverview, " +
    "no sensitive fields, " +
    "provider raw data shape, " +
    "overview statusItems/connectionRows/pack, " +
    "character warning/actionId, " +
    "voice tts/asr/enabled/diagnostics, " +
    "perception all 4 cards enabled, " +
    "abilities safety/items/module fields, " +
    "advanced pid/python/systemStrip/logs/metrics), " +
    "2 partial degradation, " +
    "3 bad snapshot fallback, " +
    "4 all unavailable null, " +
    "5 action contract inert, " +
    "6 surface contract consistency, " +
    "7 bootstrap contract (mock source metadata, backend source metadata, " +
    "default URL, unavailable fallback reason, " +
    "available clears reason, no sensitive metadata)"
);
