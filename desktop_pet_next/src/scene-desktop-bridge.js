import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

let sceneOwns = false;
let bridgeRevision = 0;
export const sceneOwnsPlayback = () => sceneOwns;

export async function registerSceneDesktopBridge({ stop, scope, equip, platform = { invoke, listen }, isActive = () => true }) {
  const revision = ++bridgeRevision;
  let disposed = false;
  const isCurrent = () => !disposed && revision === bridgeRevision && isActive();
  const unlisten = [];
  const dispose = () => {
    disposed = true;
    unlisten.splice(0).forEach(fn => fn());
    if (revision === bridgeRevision) sceneOwns = false;
  };
  try {
    unlisten.push(await platform.listen("scene-playback-claim", async ({ payload }) => {
      if (!isCurrent()) return;
      sceneOwns = true;
      await stop();
      if (isCurrent()) await platform.invoke("ack_scene_playback", { revision: payload });
    }));
    unlisten.push(await platform.listen("scene-playback-release", () => {
      if (!isCurrent()) return;
      sceneOwns = false;
    }));
    unlisten.push(await platform.listen("scene-outfit-changed", async ({ payload }) => {
      if (!isCurrent()) return;
      const current = scope();
      if (payload?.profile !== current.profile || payload?.pack !== current.pack || payload?.bot !== current.bot) return;
      await equip(payload.outfit);
    }));
    const active = await platform.invoke("scene_playback_active");
    if (!isCurrent()) { dispose(); return dispose; }
    sceneOwns = Boolean(active);
    if (sceneOwns) await stop();
    return dispose;
  } catch (error) {
    dispose();
    throw error;
  }
}
