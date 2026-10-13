import assert from 'node:assert/strict';
import test from 'node:test';
import { normalizeIdleBlink, createPortraitIdleAnimation } from '../src/portrait-idle-animation.js';

const regions = [{ x: .3, y: .25, width: .2, height: .1 }];
const config = { asset: 'animations/default/blink.png', regions, url: '/blink.png' };
const tick = () => new Promise(resolve => setImmediate(resolve));

function fixture(t) {
  let nextId = 0, busy = false, updates = 0;
  const timers = new Map(), probes = [], children = [], errors = [];
  const reduced = new EventTarget(); reduced.matches = false;
  const document = new EventTarget(); document.visibilityState = 'visible';
  document.defaultView = {
    matchMedia: () => reduced,
    setTimeout(fn, delay) { const id = ++nextId; timers.set(id, { fn, delay }); return id; },
    clearTimeout(id) { timers.delete(id); }
  };
  document.createElement = () => {
    const img = { style: {}, naturalWidth: 1024, naturalHeight: 1536,
      setAttribute() {}, removeAttribute(name) { delete this[name]; }, remove() { children.splice(children.indexOf(this), 1); } };
    probes.push(img); return img;
  };
  const image = { ownerDocument: document, parentElement: { append: img => children.push(img) }, style: {} };
  const stage = { dataset: { motion: 'idle', expression: 'normal' } };
  const animation = createPortraitIdleAnimation({ stage, image, canIdle: () => !busy, random: () => .5,
    onError: e => errors.push(e.message), onMotionUpdate: () => updates++ });
  const run = delay => {
    const entry = [...timers].find(([, timer]) => timer.delay === delay);
    assert.ok(entry, `timer ${delay} exists`);
    timers.delete(entry[0]); entry[1].fn();
  };
  const ready = async (value = config) => {
    animation.setExpression(value, { width: 1024, height: 1536 });
    probes.at(-1).onload(); await tick();
  };
  t.after(() => animation.dispose());
  return { animation, stage, image, document, reduced, timers, probes, children, errors, run, ready,
    busy: value => busy = value, updates: () => updates };
}

test('blink assets are relative and eye regions must fit the canvas', () => {
  assert.deepEqual(normalizeIdleBlink(config), { asset: config.asset, regions, emotion: 'normal', outfit: '' });
  for (const asset of ['../blink.png', '/blink.png', 'C:/blink.png', 'x\\blink.png', 'x/%2e/blink.png', 'x//blink.png'])
    assert.equal(normalizeIdleBlink({ ...config, asset }), null, asset);
  for (const regions of [[], [{x: .9, y: .1, width: .2, height: .1}], [{x: 0, y: 0, width: Infinity, height: 1}]])
    assert.equal(normalizeIdleBlink({ ...config, regions }), null);
});

test('a short blink leaves the source image and layout untouched, pauses for interactions', async t => {
  const f = fixture(t); await f.ready();
  assert.equal(f.animation.getStatus().blink, 'ready');
  f.image.style.transform = 'translate(7px, -4px) scale(.8)'; f.image.style.transformOrigin = 'bottom center';
  f.animation.setLayout(); assert.deepEqual(f.children[0].style.transform, f.image.style.transform);
  f.run(6500); assert.equal(f.stage.dataset.blink, 'closed');
  f.stage.dataset.motion = 'speaking'; f.animation.sync();
  assert.equal(f.stage.dataset.blink, 'open'); assert.ok(![...f.timers.values()].some(t => t.delay === 135));
  f.stage.dataset.motion = 'idle'; f.busy(true); f.run(1600);
  assert.equal(f.stage.dataset.blink, 'open');
  f.busy(false); f.run(1600); assert.equal(f.stage.dataset.blink, 'closed');
  f.run(135); assert.equal(f.stage.dataset.blink, 'open');
  assert.equal(f.image.src, undefined, 'the original image never changes during a blink');
});

test('late assets cannot revive a replaced expression and matching textures preload once', async t => {
  const f = fixture(t);
  f.animation.setExpression(config, { width:1024, height:1536 }); const old = f.probes.at(-1);
  f.animation.setExpression(null); old.onload(); await tick();
  assert.equal(f.animation.getStatus().blink, 'disabled'); assert.equal(f.children[0].src, undefined);
  const count = f.probes.length;
  f.animation.setExpression(config, { width:1024, height:1536 }); await tick();
  assert.equal(f.probes.length, count); assert.equal(f.animation.getStatus().blink, 'ready');
  f.animation.setExpression({...config,url:'/wrong-size.png'}, {width:16,height:16}); f.probes.at(-1).onload(); await tick();
  assert.deepEqual(f.errors, ['idle_blink_canvas_mismatch']); assert.equal(f.stage.dataset.blink, 'open');
  f.animation.setExpression({...config,url:''}); assert.equal(f.errors.at(-1), 'idle_blink_asset_unavailable');
});

test('music only runs on its idle expression and stops in hidden or reduced-motion windows', async t => {
  const f = fixture(t); await f.ready();
  f.animation.setMusic({active:true,emotion:'music'}); assert.equal(f.animation.getStatus().music, false);
  f.stage.dataset.expression = 'music'; f.animation.sync(); assert.equal(f.animation.getStatus().music, true);
  f.run(200); assert.equal(f.updates(), 1);
  f.busy(true); f.run(200); assert.equal(f.animation.getStatus().music, false);
  assert.ok(![...f.timers.values()].some(t => t.delay === 200));
  f.busy(false); f.animation.sync();
  f.document.visibilityState = 'hidden'; f.document.dispatchEvent(new Event('visibilitychange'));
  assert.equal(f.animation.getStatus().music, false); assert.equal(f.timers.size, 0);
  f.document.visibilityState = 'visible'; f.document.dispatchEvent(new Event('visibilitychange'));
  assert.equal(f.animation.getStatus().music, true);
  f.reduced.matches = true; f.reduced.dispatchEvent(new Event('change'));
  assert.equal(f.timers.size, 0); assert.equal(f.stage.dataset.blink, 'open');
  f.reduced.matches = false; f.reduced.dispatchEvent(new Event('change'));
  f.animation.setMusic({active:false,emotion:'music'}); assert.equal(f.animation.getStatus().music, false);
  f.animation.dispose(); assert.equal(f.children.length, 0); assert.equal(f.timers.size, 0);
});
