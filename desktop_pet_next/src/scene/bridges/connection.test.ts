import { afterEach, describe, expect, it, vi } from "vitest";
import { invoke } from "@tauri-apps/api/core";
import { DEFAULT_SCENE_CONNECTION, normalizeConnection, readConnection, rememberConnection } from "./connection";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

function browser(saved: unknown, search = "") {
  const storage = {
    getItem: vi.fn(() => typeof saved === "string" ? saved : JSON.stringify(saved)),
    setItem: vi.fn(),
  };
  vi.stubGlobal("window", {});
  vi.stubGlobal("location", { search });
  vi.stubGlobal("localStorage", storage);
  return storage;
}

describe("scene connection", () => {
  const owner = {
    backend: "http://127.0.0.1:12001", botId: "cecilia", profileId: "master",
    sessionId: "desktop-owner", characterId: "cecilia", instanceId: "local-qq-test",
  };

  it("keeps a saved owner connection instead of replacing it with a QA identity", async () => {
    browser(owner);
    expect(await readConnection()).toEqual(owner);
  });

  it("overrides only the explicitly supplied query fields, including an empty default-bot selection", async () => {
    browser(owner, "?backend=http%3A%2F%2F127.0.0.1%3A9999&bot=&session=other");
    expect(await readConnection()).toEqual({ ...owner, backend: DEFAULT_SCENE_CONNECTION.backend, botId: "", sessionId: "other" });
  });

  it.each([null, [], 42, { profileId: 123 }, "invalid json"])(
    "does not invent a user or a session for invalid saved settings (%j)", async (saved) => {
      browser(saved);
      expect(await readConnection()).toEqual(DEFAULT_SCENE_CONNECTION);
    },
  );

  it("trims fields without filling an empty bot, user, or session", () => {
    expect(normalizeConnection({ ...owner, backend: "  " + owner.backend, profileId: " master ", botId: " " }))
      .toEqual({ ...owner, botId: "" });
  });

  it("uses the native launch override and saved desktop identity without browser defaults", async () => {
    const storage = browser(owner);
    vi.stubGlobal("window", { __TAURI_INTERNALS__: {} });
    vi.mocked(invoke).mockImplementation(async (command) => {
      if (command === "get_client_launch_binding") return {
        hasBackendOverride: true, backendUrl: owner.backend, boundBotId: owner.botId, instanceId: owner.instanceId,
      };
      if (command === "load_pet_state") return {
        backendUrl: "http://127.0.0.1:9999", profileUserId: owner.profileId,
        sessionId: owner.sessionId, characterPackId: owner.characterId,
      };
      throw Error(`unexpected command: ${command}`);
    });
    expect(await readConnection()).toEqual(owner);
    rememberConnection(owner);
    expect(storage.getItem).not.toHaveBeenCalled();
    expect(storage.setItem).not.toHaveBeenCalled();
  });
});
