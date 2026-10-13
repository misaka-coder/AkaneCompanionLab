import { portraitMimeType } from "./portrait-model.js";
import { MAX_PORTRAIT_IMAGE_BYTES } from "./constants.js";

export function createPortraitAssetCache({
  assetState,
  invoke,
}) {
  async function getPortraitImageUrl(packId, outfitId, emotion) {
    const emotionId = String(emotion?.id || "").trim();
    const key = `${packId}\u0000${outfitId}\u0000${emotionId}`;
    if (assetState.portraitImageUrlCache.has(key)) {
      return assetState.portraitImageUrlCache.get(key);
    }

    const promise = (async () => {
      const bytes = await readPortraitImageBytes(packId, outfitId, emotionId);
      return URL.createObjectURL(new Blob([bytes], { type: portraitMimeType(emotion?.path) }));
    })();

    assetState.portraitImageUrlCache.set(key, promise);
    try {
      return await promise;
    } catch (error) {
      if (assetState.portraitImageUrlCache.get(key) === promise) assetState.portraitImageUrlCache.delete(key);
      throw error;
    }
  }

  async function readPortraitImageBytes(packId, outfitId, emotionId) {
    const payload = await invoke("read_portrait_image", {
      packId,
      outfit: outfitId,
      emotion: emotionId,
    });
    const bytes = payload instanceof Uint8Array
      ? payload
      : payload instanceof ArrayBuffer
        ? new Uint8Array(payload)
        : new Uint8Array(payload || []);
    if (!bytes.length) {
      throw new Error("图片数据为空");
    }
    if (bytes.length > MAX_PORTRAIT_IMAGE_BYTES) {
      throw new Error("图片大小不能超过 20 MB。");
    }
    return bytes;
  }

  function clearPortraitImageCache(packId) {
    assetState.portraitAssetRevisions.set(packId, (assetState.portraitAssetRevisions.get(packId) || 0) + 1);
    assetState.testAssetRequests.delete(packId);
    const prefix = `${packId}\u0000`;
    for (const [key, value] of assetState.portraitImageUrlCache.entries()) {
      if (!key.startsWith(prefix)) continue;
      assetState.portraitImageUrlCache.delete(key);
      Promise.resolve(value).then((url) => URL.revokeObjectURL(url)).catch(() => {});
    }
  }

  return { getPortraitImageUrl, readPortraitImageBytes, clearPortraitImageCache };
}
