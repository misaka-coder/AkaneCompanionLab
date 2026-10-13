import { CONTROL_CENTER_SOURCE_KIND, createControlCenterRuntimeSnapshot } from "./snapshot-shape.js";
import { DEFAULT_BACKEND_URL, normalizeBackendBaseUrl } from "./http.js";
import { buildBotBackendBaseUrl, normalizeBotId } from "../../bot-routing.js";
import { createBackendHealthVerifier } from './health.js';
import { createExposureMethods } from './readers/exposure.js';
import { createSnapshotMethods } from './readers/snapshot.js';
import { createChatMethods } from './readers/chat.js';
import { createCatalogsMethods } from './readers/catalogs.js';
import { createSettingActionMethods } from './actions/settings.js';
import { createSourceActionMethods } from './actions/source.js';
import { createMemorySettingMethods } from './memory-settings.js';

export function createControlCenterDataSource(options = {}) {
  const kind = options.kind || CONTROL_CENTER_SOURCE_KIND.backend;
  if (kind === CONTROL_CENTER_SOURCE_KIND.backend) {
    return createBackendControlCenterSource(options);
  }
  throw new Error(`unsupported_control_center_source:${String(kind)}`);
}

export function createBackendControlCenterSource(options = {}) {
  const baseUrl = normalizeBackendBaseUrl(options.baseUrl || options.endpoint || DEFAULT_BACKEND_URL);
  const fetchImpl = options.fetchImpl || globalThis.fetch;
  const expectedInstanceId = String(options.expectedInstanceId || "").trim();
  const sessionId = options.sessionId || "control-center-lab";
  const profileUserId = options.profileUserId || "master";
  const client = options.client || "desktop_pet";
  const characterPackId = options.characterPackId || "";
  const outfit = options.outfit || "";
  const emotion = options.emotion || "";
  const petState = options.petState && typeof options.petState === "object" ? options.petState : {};
  const boundBotId = normalizeBotId(options.botId || petState.boundBotId || petState.instanceId);
  const botBaseUrl = buildBotBackendBaseUrl(baseUrl, boundBotId);
  const availableCharacterPacks = Array.isArray(options.availableCharacterPacks) ? options.availableCharacterPacks : [];
  const musicSnapshot = options.musicSnapshot && typeof options.musicSnapshot === "object" ? options.musicSnapshot : null;
  let lastFallbackReason = null;
  let lastChatSessionError = "";
  function setFallbackReason(reason) {
    lastFallbackReason = reason;
  }
  const setChatSessionError = (value) => { lastChatSessionError = value; };
  const ensureVerifiedBackend = createBackendHealthVerifier({ baseUrl, fetchImpl, expectedInstanceId, setFallbackReason });
  const bindings = Object.freeze({
    baseUrl,
    botBaseUrl,
    fetchImpl,
    expectedInstanceId,
    sessionId,
    profileUserId,
    client,
    characterPackId,
    outfit,
    emotion,
    petState,
    boundBotId,
    availableCharacterPacks,
    musicSnapshot,
    options,
    setFallbackReason,
    setChatSessionError,
    ensureVerifiedBackend
  });
  return {
    kind: CONTROL_CENTER_SOURCE_KIND.backend,
    backendUrl: baseUrl,
    instanceId: expectedInstanceId,
    botId: boundBotId,
    profileUserId,
    sessionId,
    characterPackId,
    ...createExposureMethods(bindings),
    get fallbackReason() {
      return lastFallbackReason;
    },
    getFallbackReason() {
      return lastFallbackReason;
    },
    getChatSessionError() {
      return lastChatSessionError;
    },
    ...createSnapshotMethods(bindings),
    ...createChatMethods(bindings),
    ...createCatalogsMethods(bindings),
    ...createMemorySettingMethods(bindings),
    ...createSettingActionMethods(bindings),
    readInitialState() {
      return createControlCenterRuntimeSnapshot({
        sourceKind: CONTROL_CENTER_SOURCE_KIND.backend,
        backendUrl: baseUrl,
        fallbackReason: "backend-source-not-connected"
      });
    },
    subscribe() {
      return () => {};
    },
    ...createSourceActionMethods(bindings)
  };
}
