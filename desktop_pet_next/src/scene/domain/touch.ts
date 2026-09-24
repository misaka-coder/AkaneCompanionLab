import type { TouchRegion } from "./types";

// Normalized image coordinates: stage position/scale/anchor share this transform.
export function touchRegion(x: number, y: number): TouchRegion | null {
  if (x >= 0.26 && x <= 0.76 && y >= 0.06 && y <= 0.3) return "head";
  if (y >= 0.3 && y <= 0.47 && ((x >= 0.2 && x <= 0.4) || (x >= 0.6 && x <= 0.82))) return "shoulder";
  if (y >= 0.47 && y <= 0.72 && ((x >= 0.08 && x <= 0.32) || (x >= 0.68 && x <= 0.93))) return "hand";
  return null;
}
