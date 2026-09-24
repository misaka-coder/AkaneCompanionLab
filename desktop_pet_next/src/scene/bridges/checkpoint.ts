import type { Presentation, Snapshot } from "../domain/types";
import type { SceneClient } from "./client";
import { validate } from "../contracts/validate";

export class PlaybackCheckpoint {
  private key: string;
  constructor(client: SceneClient) {
    this.key = "akane.scene.playback:" + client.base + ":" + JSON.stringify(client.identity);
  }
  write(presentation: Presentation, snapshot: Snapshot, cursor: number) {
    try {
      localStorage.setItem(this.key, JSON.stringify({ presentation, snapshot, cursor }));
    } catch {
      /* Delivery receipts remain the authority; resume is optional. */
    }
  }
  read(): { presentation: Presentation; snapshot: Snapshot; cursor: number } | null {
    try {
      const raw = JSON.parse(localStorage.getItem(this.key) || "null");
      if (!raw || !Number.isInteger(raw.cursor) || raw.cursor < 0) return null;
      return {
        presentation: validate("Presentation", raw.presentation),
        snapshot: validate("Snapshot", raw.snapshot),
        cursor: raw.cursor,
      };
    } catch {
      return null;
    }
  }
  clear() {
    localStorage.removeItem(this.key);
  }
}
