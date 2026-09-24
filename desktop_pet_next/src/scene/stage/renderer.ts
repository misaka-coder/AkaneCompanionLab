import type { TouchRegion } from "../domain/types";

export interface StageFrame {
  background: string;
  portrait: string;
  position: "center" | "right";
  scale: number;
  anchorX: number;
  anchorY: number;
}
export interface StageRenderer {
  load(frame: StageFrame): Promise<void>;
  clear(): void;
  react(region: TouchRegion | "nod" | "shake" | "bounce"): void;
  pause(paused: boolean): void;
  dispose(): void;
}
