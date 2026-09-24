import { invoke } from "@tauri-apps/api/core";
import type { Connection } from "../domain/types";

export const isTauri = () => typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

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
  let saved: Partial<Connection> = {};
  try {
    saved = JSON.parse(localStorage.getItem("akane.scene.connection.v1") || "{}");
  } catch {
    /* invalid settings */
  }
  // 如果之前保存了旧的临时测试端口或占位身份，平滑迁移到真实小屋存档
  const isLegacyTest = saved.backend === "http://127.0.0.1:12001" || saved.profileId === "master";
  const effectiveSaved = isLegacyTest ? {} : saved;

  return {
    backend: params.get("backend") || effectiveSaved.backend || "http://127.0.0.1:14321",
    botId: params.get("bot") || effectiveSaved.botId || "local-default",
    profileId: params.get("profile") || effectiveSaved.profileId || "scene-qa",
    sessionId: params.get("session") || effectiveSaved.sessionId || "scene-ui-live",
    characterId: params.get("character") || effectiveSaved.characterId || "akane_v1",
    instanceId: params.get("instance") || effectiveSaved.instanceId || "",
  };
}

export function rememberConnection(connection: Connection) {
  if (!isTauri()) localStorage.setItem("akane.scene.connection.v1", JSON.stringify(connection));
}
