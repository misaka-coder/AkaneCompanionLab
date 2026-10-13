import assert from 'node:assert/strict';
import test, { after } from 'node:test';
import { createRestingExpressionResolver, normalizeRestingEmotions } from '../src/resting-expression.js';
import { createLifecycle } from '../src/pet/lifecycle.js';
import { loadPetRuntime } from './pet-runtime-harness.mjs';

const rules = normalizeRestingEmotions({ hungry: { emotion: 'hungry' }, tired: { emotion: 'tired' } });
const runtime = await loadPetRuntime();
const { selectCharacterPack } = await runtime.load('/src/character-profile.js');
const { createMusicEmotionController } = await runtime.load('/src/pet/music/emotion.js');
after(() => runtime.close());

test('care hysteresis is local to each owner and tired takes priority', () => {
  const resolver = createRestingExpressionResolver();
  const select = (hunger, energy, scopeKey = 'one') => resolver.resolve({ scopeKey, rules, care: { hunger, energy }, defaultEmotion: 'normal', musicEmotion: 'music' });
  assert.equal(select(25, 50), 'music');
  assert.equal(select(24, 50), 'hungry');
  assert.equal(select(34, 50), 'hungry');
  assert.equal(select(35, 50), 'music');
  assert.equal(select(20, 20), 'tired');
  assert.equal(select(20, 35), 'hungry');
  assert.equal(select(30, 30, 'other'), 'music', 'another character or user does not inherit the latch');
  assert.equal(select(null, NaN), 'music', 'missing numbers are not low care values');
  assert.equal(resolver.resolve({ scopeKey: 'one', rules, care: null, defaultEmotion: 'normal' }), 'normal');
});

test('packs can choose different expressions and thresholds or omit the feature', () => {
  const rules = normalizeRestingEmotions({ hungry: { emotion: 'snack', enter_below: 15, recover_at: 22 }, tired: { emotion: 'sleep', enter_below: 10, recover_at: 20 } });
  const resolver = createRestingExpressionResolver();
  assert.equal(resolver.resolve({ rules, care: { hunger: 14, energy: 50 }, defaultEmotion: 'plain' }), 'snack');
  assert.equal(resolver.resolve({ rules, care: { hunger: 21, energy: 50 }, defaultEmotion: 'plain' }), 'snack');
  assert.equal(resolver.resolve({ rules, care: { hunger: 22, energy: 50 }, defaultEmotion: 'plain' }), 'plain');
  assert.deepEqual(normalizeRestingEmotions({ hungry: {}, tired: null }), { hungry: null, tired: null });
  assert.equal(resolver.resolve({ rules: {}, care: { hunger: 0, energy: 0 }, defaultEmotion: 'plain' }), 'plain');
});

function fixture(t) {
  selectCharacterPack('cecilia');
  const timers = new Map(), effects = [];
  let id = 0, active = false, authoritative = true;
  const lifecycle = createLifecycle({ setTimeout(callback) { const token = ++id; timers.set(token, callback); return token; }, clearTimeout(token) { timers.delete(token); } });
  const state = { instanceId: 'local', characterPackId: 'cecilia', currentEmotion: 'normal', care: { hunger: 60, energy: 60 } };
  const els = { chatForm: { hidden: true }, menu: { hidden: true }, bubble: { classList: { contains: () => false } } };
  const chat = { sending: false }, tts = { ttsActive: false, ttsQueue: [] }, voice = { voiceInputState: 'idle' }, window = { playState: {} }, interaction = {}, bubble = { replyDisplayActive: false };
  const available = new Set(['normal', 'hungry', 'tired', 'music', 'serious']);
  const character = {
    resolveEmotionEntry(value) { const id = available.has(value) ? value : 'normal'; return { id, url: `/${state.characterPackId}/${id}.png` }; },
    setPetEmotion(value, options) { const id = this.resolveEmotionEntry(value).id; state.currentEmotion = id; effects.push({ id, options }); return id; }
  };
  const controller = createMusicEmotionController({ state, els, lifecycle, character, chat, tts, voice, window, interaction, bubble,
    care: { isCareRuntimeActive: () => authoritative }, systemMedia: { isMusicEmotionSourceActive: () => active }
  });
  const run = () => { for (const [id, callback] of [...timers]) { if (timers.has(id)) { timers.delete(id); callback(); } } };
  t.after(() => lifecycle.dispose());
  return { controller, state, els, chat, tts, voice, window, interaction, character, bubble, effects, available, timers, run,
    music: value => active = value, authority: value => authoritative = value };
}

test('reply, voice, dragging, local feedback and preview finish before idle restoration', t => {
  const f = fixture(t);
  f.music(true);
  f.state.care.energy = 20;
  const blockers = [
    [f.chat, 'sending', true], [f.tts, 'ttsActive', true], [f.tts, 'ttsQueue', ['pending']],
    [f.voice, 'voiceInputState', 'processing'], [f.voice, 'realtimeVoiceCall', {}],
    [f.bubble, 'replyDisplayActive', true], [f.window, 'dragState', {}], [f.window, 'physicsTimer', 1],
    [f.window.playState, 'heldEmotion', 'hurt'], [f.interaction, 'localInteractionActive', true],
    [f.interaction, 'previewEmotionRestore', 'normal'], [f.character, 'transientEmotionTimer', 1],
    [f.els.chatForm, 'hidden', false], [f.els.menu, 'hidden', false]
  ];
  for (const [owner, key, value] of blockers) {
    const previous = owner[key]; owner[key] = value;
    f.state.currentEmotion = 'serious';
    f.controller.scheduleMusicEmotionRestore({ delayMs: 0 }); f.run();
    assert.equal(f.state.currentEmotion, 'serious', key);
    owner[key] = previous; f.run();
    assert.equal(f.state.currentEmotion, 'tired', `${key} released`);
  }
  assert.ok(f.effects.every(effect => effect.options.persist === false), 'idle state is not a manual expression preference');
});

test('new care wins over a deferred music restore, and unavailable care falls back', t => {
  const f = fixture(t);
  f.music(true); f.controller.setMusicEmotion(true);
  f.state.care.hunger = 20; f.run();
  assert.equal(f.state.currentEmotion, 'hungry');
  f.state.care.hunger = 35; f.controller.scheduleMusicEmotionRestore({ delayMs: 0 }); f.run();
  assert.equal(f.state.currentEmotion, 'music');
  f.music(false); f.controller.setMusicEmotion(false);
  assert.equal(f.state.currentEmotion, 'normal');
  assert.equal(f.timers.size, 0, 'stopping idle playback does not start background polling');
  f.state.care.energy = 0; f.authority(false); f.controller.setRestingPetEmotion();
  assert.equal(f.state.currentEmotion, 'normal', 'cached or fabricated care is never used');
  f.authority(true); f.controller.setRestingPetEmotion();
  assert.equal(f.state.currentEmotion, 'tired');
  f.state.characterPackId = 'other'; f.state.care.energy = 30; f.controller.setRestingPetEmotion();
  assert.equal(f.state.currentEmotion, 'normal', 'a previous owner cannot keep the hysteresis active');
});

test('a missing or failed automatic portrait falls back and can retry after reload', t => {
  const f = fixture(t);
  f.state.care.hunger = 20; f.available.delete('hungry'); f.controller.setRestingPetEmotion();
  assert.equal(f.state.currentEmotion, 'normal');
  f.available.add('hungry'); f.controller.setRestingPetEmotion();
  f.controller.handleRestingEmotionLoadError({ id: 'hungry', url: '/cecilia/hungry.png' });
  assert.equal(f.state.currentEmotion, 'normal');
  f.controller.scheduleMusicEmotionRestore({ delayMs: 0 }); f.run();
  assert.equal(f.state.currentEmotion, 'normal', 'failed image is not requested at each care tick');
  f.controller.resetRestingEmotionFailures(); f.controller.setRestingPetEmotion();
  assert.equal(f.state.currentEmotion, 'hungry');
  f.controller.handleRestingEmotionLoadError({ id: 'music' });
  assert.equal(f.state.currentEmotion, 'hungry', 'old or unrelated failures do not reset a newer selection');
});
