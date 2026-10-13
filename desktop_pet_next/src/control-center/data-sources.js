/** Stable control-center data-source API. Implementation lives in data-source/. */
export { CONTROL_CENTER_SOURCE_KIND, createControlCenterRuntimeSnapshot } from './data-source/snapshot-shape.js';
export { createControlCenterDataSource, createBackendControlCenterSource } from './data-source/source.js';
export { buildCharacterRuntimePatchFromSettingsSnapshot } from './data-source/projections/characters.js';
export { buildMusicRuntimePatch } from './data-source/projections/music.js';
