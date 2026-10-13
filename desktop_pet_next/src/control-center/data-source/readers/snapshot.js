import { buildBackendUrl, fetchJson, fetchText } from "../http.js";
import {
  readApprovalRequestsCatalog,
  readCapabilitiesCatalog,
  readVoiceProfilesCatalog
} from "../catalog-reads.js";
import { CONTROL_CENTER_SOURCE_KIND } from "../snapshot-shape.js";
import { buildOverviewRuntimePatch } from "../projections/overview.js";
import { buildCharacterRuntimePatch } from "../projections/characters.js";
import { buildVoiceRuntimePatch } from "../projections/voice.js";
import { buildMusicRuntimePatch } from "../projections/music.js";
import { buildAbilitiesRuntimePatch } from "../projections/abilities.js";
import { buildAdvancedRuntimePatch } from "../projections/advanced.js";

const unifiedSnapshotRuntimeFields = Object.freeze([
  "health",
  "diagnostics",
  "workspace",
  "resourceManifest",
  "metrics"
]);

async function tryReadUnifiedSnapshot(fetchImpl, baseUrl, scope = {}) {
  const {
    petState,
    musicSnapshot,
    availableCharacterPacks,
    characterPackId,
    outfit,
    emotion,
    requestParams
  } = scope;
  try {
    const response = await fetchJson(
      fetchImpl,
      buildBackendUrl(baseUrl, "/control-center/snapshot", requestParams || { t: String(Date.now()) })
    );
    const snapshot = response.data;
    if (!response.ok || !snapshot || snapshot.ok !== true) {
      return null;
    }
    if (
      typeof snapshot.schemaVersion !== "number" ||
      snapshot.sourceKind !== CONTROL_CENTER_SOURCE_KIND.backend ||
      typeof snapshot.generatedAt !== "string"
    ) {
      return null;
    }
    const runtime = snapshot.runtime;
    if (!runtime || typeof runtime !== "object" || !unifiedSnapshotRuntimeFields.every((field) => field in runtime)) {
      return null;
    }
    const health = unpackUnifiedSnapshotField(runtime.health);
    const diagnostics = unpackUnifiedSnapshotField(runtime.diagnostics);
    const workspace = unpackUnifiedSnapshotField(runtime.workspace);
    const resourceManifest = unpackUnifiedSnapshotField(runtime.resourceManifest);
    const metrics = unpackUnifiedSnapshotField(runtime.metrics);
    const executionPolicy = unpackUnifiedSnapshotField(runtime.executionPolicy);
    const resourcePolicy = unpackUnifiedSnapshotField(runtime.resourcePolicy);
    const hasSomeData = [health, diagnostics, workspace, resourceManifest, metrics].some((item) => item.ok);
    if (!hasSomeData) {
      return null;
    }
    const catalogParams = requestParams || { t: String(Date.now()) };
    const [capabilitiesCatalog, voiceProfilesCatalog, approvalRequestsCatalog] = await Promise.all([
      readCapabilitiesCatalog(fetchImpl, baseUrl, catalogParams),
      readVoiceProfilesCatalog(fetchImpl, baseUrl, catalogParams),
      readApprovalRequestsCatalog(fetchImpl, baseUrl, catalogParams)
    ]);
    const metricsText = typeof metrics.data === "string" ? metrics.data : "";
    return {
      sourceKind: CONTROL_CENTER_SOURCE_KIND.backend,
      backendUrl: baseUrl,
      fallbackReason: null,
      controlCenterRuntime: {
        backendBaseUrl: baseUrl,
        health,
        diagnostics,
        workspace,
        resourceManifest,
        metrics,
        executionPolicy,
        resourcePolicy,
        capabilitiesCatalog,
        voiceProfilesCatalog,
        approvalRequestsCatalog
      },
      overviewRuntime: buildOverviewRuntimePatch({
        health: health.data,
        diagnostics: diagnostics.data,
        workspace: workspace.data,
        metricsText,
        petState,
        baseUrl,
        resourceManifest: resourceManifest.data,
        connected: health.ok || diagnostics.ok
      }),
      characterRuntime: buildCharacterRuntimePatch({
        baseUrl,
        resourceManifest: resourceManifest.data,
        diagnostics: diagnostics.data,
        characterPackId,
        outfit,
        emotion,
        petState,
        availableCharacterPacks
      }),
      voiceRuntime: buildVoiceRuntimePatch({
        health: health.data,
        diagnostics: diagnostics.data,
        petState,
        capabilitiesCatalog: capabilitiesCatalog.data
      }),
      musicRuntime: buildMusicRuntimePatch({ musicSnapshot, petState }),
      abilitiesRuntime: buildAbilitiesRuntimePatch({
        diagnostics: diagnostics.data,
        workspace: workspace.data,
        capabilitiesCatalog: capabilitiesCatalog.data,
        voiceProfilesCatalog: voiceProfilesCatalog.data,
        approvalRequestsCatalog: approvalRequestsCatalog.data,
        executionPolicy: executionPolicy.data,
        resourcePolicy: resourcePolicy.data,
        connected: health.ok || diagnostics.ok || capabilitiesCatalog.ok || voiceProfilesCatalog.ok || approvalRequestsCatalog.ok
      }),
      advancedRuntime: buildAdvancedRuntimePatch({
        health: health.data,
        diagnostics: diagnostics.data,
        workspace: workspace.data,
        metricsText,
        petState
      })
    };
  } catch {
    return null;
  }
}

function unpackUnifiedSnapshotField(entry) {
  if (entry && typeof entry === "object" && !Array.isArray(entry) && "ok" in entry) {
    if (entry.ok === false) {
      return { ok: false, data: null, status: entry.status || "unavailable" };
    }
    if ("data" in entry) {
      return { ok: true, data: entry.data, status: entry.status || "available" };
    }
  }
  if (typeof entry === "string") {
    return { ok: Boolean(entry), data: entry };
  }
  return { ok: true, data: entry || null };
}

export function createSnapshotMethods({
  fetchImpl,
  setFallbackReason,
  sessionId,
  profileUserId,
  client,
  characterPackId,
  outfit,
  emotion,
  ensureVerifiedBackend,
  botBaseUrl,
  petState,
  musicSnapshot,
  availableCharacterPacks,
  baseUrl
}) {
  return {
    async readSnapshot() {
      if (typeof fetchImpl !== "function") {
        setFallbackReason("no-fetch-impl");
        return null;
      }

      const commonParams = {
        user_id: sessionId,
        real_user_id: profileUserId,
        client,
        character_pack_id: characterPackId,
        outfit,
        emotion,
        t: String(Date.now())
      };

      const health = await ensureVerifiedBackend({ force: true });
      if (!health) return null;

      // Try unified snapshot endpoint first
      const snapshotResult = await tryReadUnifiedSnapshot(fetchImpl, botBaseUrl, {
        requestParams: commonParams,
        petState,
        musicSnapshot,
        availableCharacterPacks,
        characterPackId,
        outfit,
        emotion
      });
      if (snapshotResult) {
        setFallbackReason(null);
        return snapshotResult;
      }

      setFallbackReason("unified-snapshot-unavailable");
      const [diagnostics, workspace, resourceManifest, metrics, capabilitiesCatalog, voiceProfilesCatalog, approvalRequestsCatalog] = await Promise.all([
        fetchJson(fetchImpl, buildBackendUrl(botBaseUrl, "/desktop-pet/diagnostics", commonParams)),
        fetchJson(fetchImpl, buildBackendUrl(botBaseUrl, "/desktop-pet/workspace/summary", commonParams)),
        fetchJson(fetchImpl, buildBackendUrl(botBaseUrl, "/resource-manifest", commonParams)),
        fetchText(fetchImpl, buildBackendUrl(botBaseUrl, "/metrics", { t: commonParams.t })),
        readCapabilitiesCatalog(fetchImpl, botBaseUrl, commonParams),
        readVoiceProfilesCatalog(fetchImpl, botBaseUrl, commonParams),
        readApprovalRequestsCatalog(fetchImpl, botBaseUrl, commonParams)
      ]);
      if (![health, diagnostics, workspace, resourceManifest, metrics, capabilitiesCatalog, voiceProfilesCatalog, approvalRequestsCatalog].some((item) => item.ok)) {
        setFallbackReason("all-backend-endpoints-failed");
        return null;
      }
      setFallbackReason(null);
      return {
        sourceKind: CONTROL_CENTER_SOURCE_KIND.backend,
        backendUrl: baseUrl,
        fallbackReason: null,
        controlCenterRuntime: {
          backendBaseUrl: baseUrl,
          health,
          diagnostics,
          workspace,
          resourceManifest,
          metrics,
          executionPolicy: { ok: false, status: "unavailable", data: null },
          resourcePolicy: { ok: false, status: "unavailable", data: null },
          capabilitiesCatalog,
          voiceProfilesCatalog,
          approvalRequestsCatalog
        },
        overviewRuntime: buildOverviewRuntimePatch({
          health: health.data,
          diagnostics: diagnostics.data,
          workspace: workspace.data,
          metricsText: metrics.data,
          petState,
          baseUrl,
          resourceManifest: resourceManifest.data,
          connected: health.ok || diagnostics.ok
        }),
        characterRuntime: buildCharacterRuntimePatch({
          baseUrl,
          resourceManifest: resourceManifest.data,
          diagnostics: diagnostics.data,
          characterPackId,
          outfit,
          emotion,
          petState,
          availableCharacterPacks
        }),
        voiceRuntime: buildVoiceRuntimePatch({
          health: health.data,
          diagnostics: diagnostics.data,
          petState,
          capabilitiesCatalog: capabilitiesCatalog.data
        }),
        musicRuntime: buildMusicRuntimePatch({ musicSnapshot, petState }),
        abilitiesRuntime: buildAbilitiesRuntimePatch({
          diagnostics: diagnostics.data,
          workspace: workspace.data,
          capabilitiesCatalog: capabilitiesCatalog.data,
          voiceProfilesCatalog: voiceProfilesCatalog.data,
          approvalRequestsCatalog: approvalRequestsCatalog.data,
          executionPolicy: null,
          resourcePolicy: null,
          connected: health.ok || diagnostics.ok || capabilitiesCatalog.ok || voiceProfilesCatalog.ok || approvalRequestsCatalog.ok
        }),
        advancedRuntime: buildAdvancedRuntimePatch({
          health: health.data,
          diagnostics: diagnostics.data,
          workspace: workspace.data,
          metricsText: metrics.data,
          petState
        })
      };
    }
  };
}
