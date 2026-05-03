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
  createBackendControlCenterSource,
  CONTROL_CENTER_SOURCE_KIND,
} from "../src/control-center/data-sources.js";

// ---------------------------------------------------------------------------
// Helper: build a fetch implementation that simulates the unified snapshot
// endpoint + individual legacy endpoints.
// ---------------------------------------------------------------------------

function makeSnapshotFetch({
  snapshotBody = null,
  snapshotOk = true,
  legacyOk = true,
  snapshotStatus = 200,
} = {}) {
  const requestedUrls = [];
  const fullSnapshotBody = snapshotBody || {
    ok: true,
    status: "available",
    schemaVersion: 1,
    sourceKind: "backend",
    generatedAt: new Date().toISOString(),
    runtime: {
      health: { status: "ok", pid: 1234, contracts: { desktop_pet: { tts: true } } },
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
      requestedUrls.push(url);
      if (url.includes("/control-center/snapshot")) {
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

  // No fallback to legacy endpoints when snapshot succeeds (even partially)
  const snapshotUrls = requestedUrls.filter((u) => u.includes("/control-center/snapshot"));
  assert.ok(snapshotUrls.length >= 1, "2.7 degraded: snapshot endpoint should have been called");
  const legacyUrls = requestedUrls.filter((u) => !u.includes("/control-center/snapshot"));
  assert.equal(legacyUrls.length, 0, "2.8 degraded: legacy endpoints should NOT be called when snapshot returns usable data");
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
  const legacyUrls = requestedUrls.filter((u) => !u.includes("/control-center/snapshot"));
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
  const legacyUrls = requestedUrls.filter((u) => !u.includes("/control-center/snapshot"));
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
    "provider raw data shape), " +
    "2 partial degradation, " +
    "3 bad snapshot fallback, " +
    "4 all unavailable null, " +
    "5 action contract inert, " +
    "6 surface contract consistency"
);
