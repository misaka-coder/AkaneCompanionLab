import { fetch as nativeFetch } from "@tauri-apps/plugin-http";
import { isTauri } from "./connection";
import type { SceneClient } from "./client";
import type { Beat } from "../domain/types";

export async function requestVoice(client: SceneClient, beat: Beat, signal: AbortSignal): Promise<Blob> {
  const response = await (isTauri() ? nativeFetch : fetch)(client.base + "/tts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    signal,
    body: JSON.stringify({
      text: beat.speech,
      user_id: client.identity.session_id,
      real_user_id: client.identity.profile_user_id,
      character_pack_id: client.identity.character_pack_id,
      client_mode: "desktop_pet",
      emotion: beat.emotion_id,
    }),
  });
  if (!response.ok) throw Error("语音暂不可用，可以继续阅读文字。");
  return response.blob();
}
