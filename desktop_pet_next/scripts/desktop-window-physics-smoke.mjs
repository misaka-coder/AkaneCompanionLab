import assert from "node:assert/strict";

import {
  PET_PHYSICS_FRAME_MS,
  PET_PHYSICS_GRAVITY,
  PET_HIT_POLYGON,
  rectToPhysical,
  isUsableRect,
  buildPetPolygonPoints,
  buildOverlayClipPath,
  normalizeMenuAnchor,
  resolveMenuAnchorPoint,
  calculateMenuPosition,
  calculateDragVelocity,
  stepPetPhysics
} from "../src/desktop-window-physics.js";

// 1. Constants
assert.equal(PET_PHYSICS_FRAME_MS, 20);
assert.equal(PET_PHYSICS_GRAVITY, 0.8);
assert.equal(PET_HIT_POLYGON.length, 12);

// 2. Rect conversion
const rect = { left: 10, top: 20, width: 100, height: 200 };
const physical = rectToPhysical(rect, 1.5);
assert.deepEqual(physical, { x: 15, y: 30, width: 150, height: 300 });
assert.equal(isUsableRect(physical), true);
assert.equal(isUsableRect({ width: 0, height: 10 }), false);

// 3. Polygon & clip path
const petPoly = buildPetPolygonPoints({ x: 0, y: 0, width: 100, height: 100 });
assert.equal(petPoly.length, 12);
assert.deepEqual(petPoly[0], { x: 32, y: 0 });

const clipPath = buildOverlayClipPath({
  rect: { x: 0, y: 0, width: 100, height: 100 },
  polygon: [{ x: 0, y: 0 }, { x: 100, y: 0 }, { x: 100, y: 100 }]
});
assert.match(clipPath, /^polygon\(/);

// 4. Menu positioning
const anchor = normalizeMenuAnchor({ x: 100, y: 200 });
assert.equal(anchor.x, 100);
assert.equal(anchor.y, 200);

const resolved = resolveMenuAnchorPoint({ x: 50, y: 60 });
assert.deepEqual(resolved, { x: 50, y: 60 });

const pos = calculateMenuPosition({
  x: 1000,
  y: 800,
  menuWidth: 150,
  menuHeight: 200,
  viewportWidth: 800,
  viewportHeight: 600,
  margin: 8
});
assert.equal(pos.left <= 800 - 150 - 8, true);
assert.equal(pos.top <= 600 - 200 - 8, true);

// 5. Drag velocity
const vel = calculateDragVelocity({ dx: 20, dy: -10, dt: 16 });
assert.equal(vel.vx, 8);
assert.equal(vel.vy, -4);
const tooFast = calculateDragVelocity({ dx: 20, dy: -10, dt: 2 });
assert.equal(tooFast.vx, 0);

// 6. Physics step
const bounds = { left: 0, top: 0, right: 1920, bottom: 1080 };
// Mid-air fall
const step1 = stepPetPhysics({
  x: 500,
  y: 200,
  vx: 5,
  vy: 0,
  width: 100,
  height: 100,
  bounds
});
assert.equal(step1.nextVy > 0, true); // gravity accelerated
assert.equal(step1.hitFloor, false);
assert.equal(step1.hitWall, false);

// Floor bounce
const floorY = bounds.bottom - 100 - 18;
const step2 = stepPetPhysics({
  x: 500,
  y: floorY - 5,
  vx: 5,
  vy: 20,
  width: 100,
  height: 100,
  bounds
});
assert.equal(step2.hitFloor, true);
assert.equal(step2.floorBounce, true);
assert.equal(step2.nextVy < 0, true); // bounced upwards

// Wall hit
const step3 = stepPetPhysics({
  x: bounds.right - 100 - 2,
  y: 500,
  vx: 15,
  vy: 0,
  width: 100,
  height: 100,
  bounds
});
assert.equal(step3.hitWall, true);
assert.equal(step3.nextVx < 0, true); // bounced leftwards

console.log("desktop window physics smoke: ok");
