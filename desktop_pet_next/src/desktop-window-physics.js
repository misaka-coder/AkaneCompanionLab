/**
 * Desktop Window Physics & Native HitTest Geometry.
 * Pure calculations for pet drag/throw physics, floor & wall collision,
 * native hit-test polygon scaling, and context menu viewport clamping.
 */

export const PET_PHYSICS_FRAME_MS = 20;
export const PET_PHYSICS_GRAVITY = 0.8;
export const PET_PHYSICS_GROUND_FRICTION = 0.94;
export const PET_PHYSICS_AIR_FRICTION = 0.99;
export const PET_PHYSICS_BOUNCE = 0.4;
export const PET_DRAG_THRESHOLD_PX = 5;
export const PET_DRAG_VELOCITY_FACTOR = 0.4;
export const PET_THROW_THRESHOLD = 8;
export const PET_WALL_PAIN_THRESHOLD = 7;
export const PET_PHYSICS_MIN_SPEED = 0.2;
export const PET_PHYSICS_FLOOR_CLEARANCE = 18;
export const MENU_VIEWPORT_MARGIN = 8;

export const PET_HIT_POLYGON = Object.freeze([
  [32, 0],
  [72, 0],
  [88, 12],
  [94, 32],
  [100, 66],
  [92, 98],
  [74, 100],
  [28, 100],
  [10, 98],
  [0, 66],
  [6, 32],
  [12, 12]
]);

export function rectToPhysical(rect, ratio = 1) {
  const scale = Number.isFinite(Number(ratio)) && ratio > 0 ? Number(ratio) : 1;
  return {
    x: Math.round(Number(rect?.left ?? rect?.x ?? 0) * scale),
    y: Math.round(Number(rect?.top ?? rect?.y ?? 0) * scale),
    width: Math.round(Number(rect?.width ?? 0) * scale),
    height: Math.round(Number(rect?.height ?? 0) * scale)
  };
}

export function isUsableRect(rect) {
  return Boolean(rect && Number(rect.width) > 1 && Number(rect.height) > 1);
}

export function buildPetPolygonPoints(rect, polygon = PET_HIT_POLYGON) {
  const width = Number(rect?.width) || 0;
  const height = Number(rect?.height) || 0;
  const rx = Number(rect?.x) || 0;
  const ry = Number(rect?.y) || 0;
  return polygon.map(([x, y]) => ({
    x: Math.round(rx + (width * x) / 100),
    y: Math.round(ry + (height * y) / 100)
  }));
}

export function buildOverlayClipPath(region) {
  if (!Array.isArray(region?.polygon) || region.polygon.length < 3) return "";
  const width = Math.max(1, Number(region?.rect?.width) || 1);
  const height = Math.max(1, Number(region?.rect?.height) || 1);
  const rx = Number(region?.rect?.x) || 0;
  const ry = Number(region?.rect?.y) || 0;
  const points = region.polygon.map(({ x, y }) => {
    const px = (((x - rx) / width) * 100).toFixed(2);
    const py = (((y - ry) / height) * 100).toFixed(2);
    return `${px}% ${py}%`;
  });
  return `polygon(${points.join(", ")})`;
}

export function normalizeMenuAnchor(anchor, defaultMargin = MENU_VIEWPORT_MARGIN) {
  const source = anchor && typeof anchor === "object" ? anchor : {};
  return {
    x: Number(source.x) || defaultMargin,
    y: Number(source.y) || defaultMargin,
    anchor: source.anchor && typeof source.anchor === "object" ? source.anchor : null,
    source: source.source === "anchor" ? "anchor" : "pointer"
  };
}

export function resolveMenuAnchorPoint(anchor, margin = MENU_VIEWPORT_MARGIN) {
  if (anchor?.anchor && typeof anchor.anchor.getBoundingClientRect === "function") {
    const rect = anchor.anchor.getBoundingClientRect();
    return { x: rect.right - 2, y: rect.bottom + margin };
  }
  return { x: Number(anchor?.x) || margin, y: Number(anchor?.y) || margin };
}

export function calculateMenuPosition({
  x,
  y,
  menuWidth = 0,
  menuHeight = 0,
  viewportWidth = 800,
  viewportHeight = 600,
  margin = MENU_VIEWPORT_MARGIN
}) {
  const maxX = viewportWidth - menuWidth - margin;
  const maxY = viewportHeight - menuHeight - margin;
  const clampedX = Math.min(Math.max(margin, maxX), Math.max(margin, Number(x) || margin));
  const clampedY = Math.min(Math.max(margin, maxY), Math.max(margin, Number(y) || margin));
  return {
    left: Math.round(clampedX),
    top: Math.round(clampedY),
    maxHeight: Math.max(96, Math.min(340, viewportHeight - margin * 2))
  };
}

export function calculateDragVelocity({ dx, dy, dt, factor = PET_DRAG_VELOCITY_FACTOR }) {
  if (dt <= 5) return { vx: 0, vy: 0 };
  return {
    vx: (Number(dx) || 0) * factor,
    vy: (Number(dy) || 0) * factor
  };
}

export function stepPetPhysics({
  x,
  y,
  vx,
  vy,
  width,
  height,
  bounds,
  floorClearance = PET_PHYSICS_FLOOR_CLEARANCE,
  gravity = PET_PHYSICS_GRAVITY,
  bounce = PET_PHYSICS_BOUNCE,
  groundFriction = PET_PHYSICS_GROUND_FRICTION,
  airFriction = PET_PHYSICS_AIR_FRICTION,
  minSpeed = PET_PHYSICS_MIN_SPEED
}) {
  const floorY = Math.max(bounds.top, bounds.bottom - height - floorClearance);
  let curX = Number(x) || 0;
  let curY = Number(y) || 0;
  let curVx = Number(vx) || 0;
  let curVy = Number(vy) || 0;

  if (curY < floorY) {
    curVy += gravity;
  }

  let hitFloor = false;
  let floorBounce = false;

  if (curY + curVy >= floorY) {
    curY = floorY;
    hitFloor = true;
    if (Math.abs(curVy) > 2) {
      curVy = -curVy * bounce;
      floorBounce = true;
    } else {
      curVy = 0;
    }
    curVx *= groundFriction;
  } else {
    curVx *= airFriction;
    curVy *= airFriction;
  }

  let nextX = curX + curVx;
  let nextY = curY + curVy;
  let hitWall = false;
  let wallImpactSpeed = 0;
  const maxX = Math.max(bounds.left, bounds.right - width);

  if (nextX <= bounds.left) {
    nextX = bounds.left;
    wallImpactSpeed = Math.abs(curVx);
    curVx = -curVx * bounce;
    hitWall = true;
  } else if (nextX >= maxX) {
    nextX = maxX;
    wallImpactSpeed = Math.abs(curVx);
    curVx = -curVx * bounce;
    hitWall = true;
  }

  const settledOnFloor =
    Math.abs(curVx) < minSpeed &&
    Math.abs(curVy) < minSpeed &&
    Math.abs(nextY - floorY) <= 1;

  return {
    nextX,
    nextY,
    nextVx: curVx,
    nextVy: curVy,
    hitWall,
    wallImpactSpeed,
    hitFloor,
    floorBounce,
    floorY,
    settledOnFloor
  };
}
