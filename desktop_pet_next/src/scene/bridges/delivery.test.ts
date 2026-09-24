import { afterEach, expect, it, vi } from "vitest";
import { SceneDelivery } from "./delivery";
import type { SceneClient } from "./client";
import type { Receipt } from "../domain/types";

afterEach(() => vi.unstubAllGlobals());
it("pending receipts survive closing the client and retain causal order", async () => {
  const data = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    get length() {
      return data.size;
    },
    key: (i: number) => [...data.keys()][i],
    getItem: (key: string) => data.get(key) || null,
    setItem: (key: string, value: string) => data.set(key, value),
    removeItem: (key: string) => data.delete(key),
  });
  const identity = { profile_user_id: "qa", session_id: "qa-session", character_pack_id: "qa" };
  const client = { base: "http://localhost", identity, request: vi.fn().mockRejectedValue(Error("offline")) };
  const receipt: Receipt = { identity, turn_id: "one", beat_id: "one:0", generation: 1, kind: "display_started" };
  const delivery = new SceneDelivery(client as unknown as SceneClient, vi.fn());
  delivery.add(receipt);
  delivery.add({ ...receipt, kind: "text_revealed" });
  await delivery.flush().catch(() => {});
  expect(data.size).toBe(2);
  client.request = vi.fn().mockResolvedValue({ ok: true });
  await new SceneDelivery(client as unknown as SceneClient, vi.fn()).flush();
  expect(client.request.mock.calls.map((c) => (c[1] as Receipt).kind)).toEqual(["display_started", "text_revealed"]);
  expect(data.size).toBe(0);
});

it("drops terminal rejected receipts (delivery_order_invalid, stale_presentation, presentation_not_found) without throwing", async () => {
  const data = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    get length() {
      return data.size;
    },
    key: (i: number) => [...data.keys()][i],
    getItem: (key: string) => data.get(key) || null,
    setItem: (key: string, value: string) => data.set(key, value),
    removeItem: (key: string) => data.delete(key),
  });
  const identity = { profile_user_id: "qa", session_id: "qa-session", character_pack_id: "qa" };
  const client = {
    base: "http://localhost",
    identity,
    request: vi.fn().mockRejectedValue(Error("delivery_order_invalid")),
  };
  const receipt: Receipt = { identity, turn_id: "one", beat_id: "one:0", generation: 1, kind: "audio_completed" };
  const failed = vi.fn();
  const delivery = new SceneDelivery(client as unknown as SceneClient, failed);
  delivery.add(receipt);
  expect(data.size).toBe(1);

  // flush must NOT throw when error is delivery_order_invalid
  await expect(delivery.flush()).resolves.toBeUndefined();
  // receipt must be removed from storage to avoid endless retry loop
  expect(data.size).toBe(0);
});

