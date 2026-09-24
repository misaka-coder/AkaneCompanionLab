import { invoke } from "@tauri-apps/api/core";
import { emitTo } from "@tauri-apps/api/event";
import { isTauri } from "./connection";
import type { SceneClient } from "./client";
import type { Snapshot } from "../domain/types";

export async function enterNativeRoom(client: SceneClient, snapshot: Snapshot): Promise<Snapshot> {
  if (!isTauri()) return snapshot;
  await invoke("claim_scene_playback");
  const state = await invoke<Record<string, string>>("load_pet_state");
  if (
    state.profileUserId === client.identity.profile_user_id &&
    state.characterPackId === client.identity.character_pack_id &&
    (state.boundBotId || "") === client.connection.botId &&
    state.outfit !== snapshot.room.outfit_id &&
    snapshot.catalog.outfits.some((o) => o.id === state.outfit)
  ) {
    const result = await client.action("equip", state.outfit, snapshot.room.revision, crypto.randomUUID());
    return result.snapshot;
  }
  return snapshot;
}

export async function shareOutfit(client: SceneClient, outfit: string) {
  if (!isTauri()) return;
  await emitTo("main", "scene-outfit-changed", {
    profile: client.identity.profile_user_id,
    pack: client.identity.character_pack_id,
    bot: client.connection.botId,
    outfit,
  });
}

export async function releaseNativePlayback() {
  if (!isTauri()) return;
  try {
    await invoke("release_scene_playback");
  } catch {
    /* superseded or not running in Tauri */
  }
}

export async function openWorkshopWindow(): Promise<boolean> {
  if (!isTauri()) return false;
  try {
    await invoke("open_workshop_window");
    return true;
  } catch {
    return false;
  }
}

export async function openShopWindow(): Promise<boolean> {
  if (!isTauri()) return false;
  try {
    await invoke("open_shop_window");
    return true;
  } catch {
    return false;
  }
}

export async function openCharacterStoriesFolder(packId: string): Promise<boolean> {
  if (!isTauri()) return false;
  try {
    await invoke("open_character_stories_folder", { packId });
    return true;
  } catch {
    return false;
  }
}
