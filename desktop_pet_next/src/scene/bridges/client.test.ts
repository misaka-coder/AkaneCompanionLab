import { afterEach, describe, expect, it, vi } from "vitest";
import { SceneClient } from "./client";

afterEach(() => vi.unstubAllGlobals());

describe("SceneClient", () => {
  it("trims connection identifiers to prevent pydantic validation errors", () => {
    const client = new SceneClient({
      backend: "http://127.0.0.1:9999",
      botId: "test_bot",
      profileId: "  master  ",
      sessionId: "  session-123  ",
      characterId: "  akane_v1  ",
      instanceId: "inst",
    });
    expect(client.identity.profile_user_id).toBe("master");
    expect(client.identity.session_id).toBe("session-123");
    expect(client.identity.character_pack_id).toBe("akane_v1");
  });

  it("omits count when count <= 1 and includes count when count > 1 for backward compatibility", async () => {
    const client = new SceneClient({
      backend: "http://127.0.0.1:9999",
      botId: "",
      profileId: "master",
      sessionId: "s1",
      characterId: "akane_v1",
      instanceId: "",
    });
    const captured: Record<string, unknown>[] = [];
    vi.spyOn(client, "request").mockImplementation(async (_path, payload) => {
      captured.push(payload as Record<string, unknown>);
      return {
        ok: true,
        reason: "",
        duplicate: false,
        event: null,
        snapshot: {
          protocol: "scene_v1",
          identity: client.identity,
          character_name: "Akane",
          room: { revision: 1, outfit_id: "default", background_id: "bg", music_id: "", position: "center" },
          care: { enabled: true, reason: "", coins: 0, hunger: 0, energy: 0, affection: 0, inventory: {} },
          shop: [],
          catalog: { revision: "1", backgrounds: [], outfits: [], music: [] },
        },
      };
    });

    await client.action("touch", "head", 0, "req-12345678", 1);
    expect(captured[0].count).toBeUndefined();

    await client.action("feed", "cookie", 1, "req-12345679", 5);
    expect(captured[1].count).toBe(5);
  });

  it("formats FastAPI 422 array details into a readable error message", async () => {
    const client = new SceneClient({
      backend: "http://127.0.0.1:9999",
      botId: "",
      profileId: "master",
      sessionId: "s1",
      characterId: "akane_v1",
      instanceId: "",
    });

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        json: async () => ({
          detail: [
            { loc: ["body", "count"], msg: "Extra inputs are not permitted", type: "extra_forbidden" },
          ],
        }),
      }),
    );

    await expect(client.request("/scene/actions", { test: true })).rejects.toThrow(
      "count: Extra inputs are not permitted",
    );
  });
});
