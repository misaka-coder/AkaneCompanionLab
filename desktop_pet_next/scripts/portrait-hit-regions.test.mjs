import assert from 'node:assert/strict';
import test from 'node:test';
import { buildAlphaHitMask, createPortraitHitMaskCache, containedPortraitRect, alphaMaskHitRegions } from '../src/portrait-hit-regions.js';

const pixels = rows => {
  const data = new Uint8ClampedArray(rows.length * rows[0].length * 4);
  rows.forEach((row, y) => [...row].forEach((cell, x) => data[(y * row.length + x) * 4 + 3] = cell === '#' ? 254 : 0));
  return { data, width: rows[0].length, height: rows.length };
};

test('alpha rectangles preserve exterior transparency and interior hair gaps', () => {
  const mask = buildAlphaHitMask(pixels(['.##..##.', '.##..##.', '.######.', '..####..']));
  assert.equal(mask.opaque, false);
  const hits = (x, y) => mask.rects.some(r => x >= r.x && x < r.x + r.width && y >= r.y && y < r.y + r.height);
  assert.equal(hits(0.01, 0.1), false);
  assert.equal(hits(0.5, 0.1), false);
  assert.equal(hits(0.2, 0.1), true);
  assert.equal(hits(0.5, 0.6), true);
  assert.equal(mask.rects[0].height, 0.5, 'equal runs in adjacent rows are merged');
  assert.equal(buildAlphaHitMask(pixels(['####', '####'])).opaque, true);
  assert.equal(buildAlphaHitMask(pixels(['....'])).rects.length, 0);
});

test('contain layout, portrait offset, scale and DPI map into native physical coordinates', () => {
  const rect = containedPortraitRect({ left: 20, top: 40, width: 200, height: 400 }, 100, 150);
  assert.deepEqual(rect, { x: 20, y: 90, width: 200, height: 300 });
  const regions = alphaMaskHitRegions({ rects: [{ x: 0.25, y: 0, width: 0.5, height: 0.5 }] }, rect, 1.5);
  assert.deepEqual(regions[0].rect, { x: 105, y: 135, width: 150, height: 225 });
});

test('image masks are read once, bounded and reused after scale changes', () => {
  let reads = 0, draws = 0;
  const cache = createPortraitHitMaskCache({ maxEntries: 2, document: { createElement() { return { getContext: () => ({ drawImage() { draws++; }, getImageData() { reads++; return pixels(['.##.']); } }) }; } } });
  const image = { complete: false, src: '/one.png', naturalWidth: 1024, naturalHeight: 1536 };
  assert.equal(cache.get(image), null);
  assert.equal(draws, 0, 'a slow or failed image does not replace the visible hit mask');
  image.complete = true;
  const first = cache.get(image);
  for (let i = 0; i < 100; i++) assert.equal(cache.get(image), first);
  assert.equal(reads, 1);
  image.src = '/two.png'; cache.get(image);
  image.src = '/three.png'; cache.get(image);
  image.src = '/one.png'; cache.get(image);
  assert.equal(reads, 4, 'old masks are evicted rather than retaining every visited pack');
  cache.clear(); cache.get(image); assert.equal(reads, 5);
});

test('tainted or unsupported canvases fail once and allow the existing fallback', () => {
  let errors = 0, attempts = 0;
  const cache = createPortraitHitMaskCache({ document: { createElement() { attempts++; throw new Error('SecurityError'); } }, onError: () => errors++ });
  const image = { complete: true, src: 'asset://one', naturalWidth: 100, naturalHeight: 200 };
  assert.equal(cache.get(image), null); assert.equal(cache.get(image), null);
  assert.equal(attempts, 1); assert.equal(errors, 1);
});

test('a CORS probe updates only its cached image and disposal rejects late loads', () => {
  const probes = []; let updates = 0;
  const cache = createPortraitHitMaskCache({ document: { createElement(tag) {
    if (tag === 'img') { const probe = { naturalWidth: 100, naturalHeight: 200, removeAttribute() {} }; probes.push(probe); return probe; }
    return { getContext: () => ({ drawImage(image) { this.image = image; }, getImageData() {
      if (!this.image.crossOrigin) throw Object.assign(new Error('tainted'), {name:'SecurityError'});
      return pixels(['.##.']);
    } }) };
  } }, onUpdate: () => updates++ });
  const image = { complete:true,src:'http://backend/one.png',naturalWidth:100,naturalHeight:200 };
  assert.equal(cache.get(image), null); assert.equal(cache.get(image), null);
  assert.equal(probes.length, 1); assert.equal(probes[0].crossOrigin, 'anonymous');
  probes[0].onload(); assert.equal(cache.get(image).rects.length, 1); assert.equal(updates, 1);
  image.src='http://backend/two.png'; cache.get(image); const late=probes[1].onload;
  cache.clear(); late(); assert.equal(updates, 1);
});
