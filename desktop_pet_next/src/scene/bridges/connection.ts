import { invoke } from "@tauri-apps/api/core";
import type { Connection } from "../domain/types";

export const isTauri = () => typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

export const DEFAULT_SCENE_CONNECTION: Readonly<Connection> = Object.freeze({
  backend: "http://127.0.0.1:9999",
  botId: "",
  profileId: "",
  sessionId: "",
  characterId: "akane_v1",
  instanceId: "",
});

export function normalizeConnection(value: unknown): Connection {
  const source = value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
  const field = (key: keyof Connection) =>
    typeof source[key] === "string" ? source[key].trim() : "";
  return {
    backend: field("backend") || DEFAULT_SCENE_CONNECTION.backend,
    botId: field("botId"),
    profileId: field("profileId"),
    sessionId: field("sessionId"),
    characterId: field("characterId") || DEFAULT_SCENE_CONNECTION.characterId,
    instanceId: field("instanceId"),
  };
}

export async function readConnection(): Promise<Connection> {
  if (isTauri()) {
    const [binding, state] = await Promise.all([
      invoke<Record<string, unknown>>("get_client_launch_binding"),
      invoke<Record<string, unknown>>("load_pet_state"),
    ]);
    return {
      backend: String(binding.hasBackendOverride ? binding.backendUrl : state.backendUrl || binding.backendUrl),
      botId: String(state.boundBotId || binding.boundBotId || ""),
      profileId: String(state.profileUserId || ""),
      sessionId: String(state.sessionId || ""),
      characterId: String(state.characterPackId || ""),
      instanceId: String(binding.instanceId || ""),
    };
  }
  const params = new URLSearchParams(location.search);
  let saved = normalizeConnection(null);
  try {
    saved = normalizeConnection(JSON.parse(localStorage.getItem("akane.scene.connection.v1") || "{}"));
  } catch {
    /* invalid settings */
  }
  return normalizeConnection({
    backend: params.get("backend") ?? saved.backend,
    botId: params.get("bot") ?? saved.botId,
    profileId: params.get("profile") ?? saved.profileId,
    sessionId: params.get("session") ?? saved.sessionId,
    characterId: params.get("character") ?? saved.characterId,
    instanceId: params.get("instance") ?? saved.instanceId,
  });
}

export function rememberConnection(connection: Connection) {
  if (!isTauri()) localStorage.setItem("akane.scene.connection.v1", JSON.stringify(connection));
}
