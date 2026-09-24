import { fetch as nativeFetch } from "@tauri-apps/plugin-http";
import { isTauri } from "./connection";

export async function loadArtwork(url: string): Promise<Blob> {
  // Revalidate: ordinary <img> thumbnails may have cached a response without
  // CORS headers. The GPU loader needs a fresh CORS-aware response.
  const response = await (isTauri() ? nativeFetch : fetch)(url, { cache: "no-cache" });
  if (!response.ok) throw Error("image_unavailable");
  return response.blob();
}
