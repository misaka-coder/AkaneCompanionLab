export const CONTROL_CENTER_SCHEMA_VERSION = "control-center.snapshot.v0.1";

export const CONTROL_CENTER_PAGE_IDS = Object.freeze([
  "overview",
  "model",
  "character",
  "voice",
  "music",
  "context",
  "abilities",
  "advanced",
  "settings"
]);

export const CONTROL_CENTER_DATA_LAYERS = Object.freeze({
  backend: "backend",
  derived: "derived",
  ui: "ui",
  decorative: "decorative"
});

/**
 * @typedef {Object} ControlCenterSnapshot
 * @property {string} schemaVersion
 * @property {string} sourceKind
 * @property {string} generatedAt
 * @property {ControlCenterShell} shell
 * @property {ControlCenterPages} pages
 * @property {Record<string, unknown>} dataDomains
 * @property {Record<string, boolean>} featureFlags
 */

/**
 * @typedef {Object} ControlCenterShell
 * @property {Array<{id: string, label: string, icon: string, enabled?: boolean}>} navItems
 * @property {{defaultPage: string, version: string, status: string, statusDetail: string, footer: string}} labMeta
 * @property {string} backgroundAsset
 */

/**
 * @typedef {Object} ControlCenterPages
 * @property {Object} overview
 * @property {Object} model
 * @property {Object} character
 * @property {Object} voice
 * @property {Object} music
 * @property {Object} perception
 * @property {Object} abilities
 * @property {Object} advanced
 */

export function isKnownControlCenterPage(pageId) {
  return CONTROL_CENTER_PAGE_IDS.includes(pageId);
}
