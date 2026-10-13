import assert from 'node:assert/strict';
import { createActivityController } from '../src/pet/activity.js';
import { createPluginsController } from '../src/pet/plugins.js';

let playing = false, calls = 0, changed = false, fail = false;
const receipts = [];
const identity = { real_user_id: 'master', user_id: 'desk', character_pack_id: 'cecilia' };
const character = { buildBackendCharacterContext: () => identity };
const platform = { backendFetch: async (_, options) => {
  const receipt = JSON.parse(options.body); receipts.push(receipt);
  return { ok: true, json: async () => ({ ok: true }) };
} };
const music = {
  playNextMusicTrack: async () => { calls++; if (fail) throw Error('no_next_track'); playing = true; return true; },
  stopMusic: () => { calls++; playing = false; },
  get musicTrack() { return { displayName: '测试音乐' }; }
};
const activity = createActivityController({
  platform, character, music, els: { musicPlayer: { get paused() { return !playing; } } },
  scope: { capture: () => 1, isCurrent: () => !changed },
  backend: { buildBackendEndpointUrl: (_, path) => path },
  status: { updateActivityControls() {} }, settings: { scheduleSettingsSnapshot() {} }
});
const command = { operation_id: 'one', profile_user_id: 'master', session_id: 'desk', character_pack_id: 'cecilia', action: 'next', expires_at: Date.now()/1000+10 };
await Promise.all([activity.executeLocalMusicControl(command), activity.executeLocalMusicControl(command)]);
assert.equal(calls, 1);
assert.equal(receipts.at(-1).ok, true);
fail = true;
await activity.executeLocalMusicControl({ ...command, operation_id: 'two' });
assert.equal(receipts.at(-1).reason, 'no_next_track');
changed = true;
await activity.executeLocalMusicControl({ ...command, operation_id: 'stale' });
assert.equal(calls, 2);
changed = false;
let listener, dispatched = 0;
const plugins = createPluginsController({
  platform: { isTauriRuntime: true, listen: async (_, callback) => { listener = callback; return () => {}; }, invoke: async () => [] },
  lifecycle: { track() {}, clearTimeout() {}, setTimeout() { throw Error('control_must_not_wait_for_chat'); } },
  chat: { sending: true }, tts: { ttsActive: true, ttsQueue: [] },
  activity: { executeLocalMusicControl: async () => { dispatched++; } }
});
await plugins.registerPluginAgentEventBridge();
listener({ payload: { deliveryId: 'host-tool', payload: { _desktop_music_control: command } } });
await Promise.resolve();
assert.equal(dispatched, 1);
assert.equal(activity.applyPayloadActivity, undefined);
console.log('Local music tool: receipt, playback failure, deduplication, stale scope and in-turn dispatch passed.');
