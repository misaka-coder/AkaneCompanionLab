import type { Receipt } from "../domain/types";
import type { SceneClient } from "./client";
import { validate } from "../contracts/validate";

const order = [
  "display_started",
  "text_revealed",
  "audio_started",
  "audio_completed",
  "audio_interrupted",
  "interrupted",
];
export class SceneDelivery {
  private pending: Receipt[] = [];
  private running: Promise<void> | null = null;
  private key: string;
  constructor(
    private client: SceneClient,
    private failed: (message: string) => void,
  ) {
    this.key = "akane.scene.delivery.v2:" + client.base + ":" + JSON.stringify(client.identity) + ":";
    const stored: { receipt: Receipt; time: number }[] = [];
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (!key?.startsWith(this.key)) continue;
      try {
        const value = JSON.parse(localStorage.getItem(key)!);
        stored.push({ receipt: validate<Receipt>("Receipt", value.receipt), time: value.time });
      } catch {
        this.failed("一条本地播放记录已损坏，无法同步。");
      }
    }
    stored.sort((a, b) => a.time - b.time || order.indexOf(a.receipt.kind) - order.indexOf(b.receipt.kind));
    this.pending = stored.map((value) => value.receipt);
  }
  private id(receipt: Receipt) {
    return this.key + receipt.turn_id + ":" + receipt.beat_id + ":" + receipt.kind;
  }
  add(receipt: Receipt) {
    this.pending.push(receipt);
    try {
      localStorage.setItem(this.id(receipt), JSON.stringify({ receipt, time: Date.now() }));
    } catch {
      this.failed("播放记录无法保存在本机，请保持此页面直到同步完成。");
    }
    void this.flush().catch(() => this.failed("播放记录尚未同步；恢复连接后会重试。"));
  }
  flush(): Promise<void> {
    if (this.running) return this.running;
    this.running = this.drain().finally(() => {
      this.running = null;
    });
    return this.running;
  }
  private async drain() {
    while (this.pending.length) {
      const receipt = this.pending[0];
      try {
        await this.client.request("/scene/receipts", receipt);
      } catch (error) {
        const msg = String(error);
        if (
          msg.includes("presentation_not_found") ||
          msg.includes("stale_presentation") ||
          msg.includes("delivery_order_invalid")
        ) {
          // Terminal receipt rejection: safely drop it to avoid poison-pill loops
          console.warn("Discarding invalid receipt:", receipt, error);
        } else {
          throw error;
        }
      }
      this.pending.shift();
      localStorage.removeItem(this.id(receipt));
    }
  }
}
