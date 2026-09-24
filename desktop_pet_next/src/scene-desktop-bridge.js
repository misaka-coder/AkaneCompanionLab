import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

let sceneOwns = false;
export const sceneOwnsPlayback = () => sceneOwns;

export async function registerSceneDesktopBridge({ stop, scope, equip }) {
  await listen("scene-playback-claim", async ({ payload }) => {
    sceneOwns = true;
    await stop();
    await invoke("ack_scene_playback", { revision: payload });
  });
  await listen("scene-playback-release", () => {
    sceneOwns = false;
  });
  await listen("scene-outfit-changed", async ({ payload }) => {
    const current = scope();
    if (payload?.profile !== current.profile || payload?.pack !== current.pack || payload?.bot !== current.bot) return;
    await equip(payload.outfit);
  });
  sceneOwns = await invoke("scene_playback_active");
  if (sceneOwns) await stop();
}
