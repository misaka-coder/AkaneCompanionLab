import { RendererInterface } from "./RendererInterface.js";
import { DEFAULT_EMOTION, resolveManifestSprite } from "../services/EmotionMapper.js";

/**
 * Renders Akane using two overlapping <img> layers for smooth crossfade.
 */

const IMAGE_CACHE_MAX = 12;
const CROSSFADE_MS = 600;
const HIT_MAP_MAX_SIZE = 512;
const HIT_ALPHA_THRESHOLD = 20;
const HIT_CACHE_MAX = 6;

class StaticSpriteRenderer extends RendererInterface {
  constructor() {
    super();
    this._container = null;
    this._frontLayer = null;
    this._backLayer = null;
    this._frontImg = null;
    this._backImg = null;
    this._activeOnFront = true;
    this._cache = new Map();
    this._assetVersion = "";
    this._manifest = null;
    this._lastResolution = null;
    this._hitCanvas = document.createElement("canvas");
    this._hitContext = this._hitCanvas.getContext("2d", { willReadFrequently: true });
    this._hitCache = new Map();
    this._hitWarningShown = false;
  }

  async init(container) {
    this._container = container;

    // Back layer (behind, fades in)
    this._backLayer = document.createElement("div");
    this._backLayer.className = "sprite-layer back";
    this._backImg = document.createElement("img");
    this._backImg.alt = "Akane";
    this._backImg.draggable = false;
    this._backLayer.appendChild(this._backImg);
    container.appendChild(this._backLayer);

    // Front layer (on top, fades out)
    this._frontLayer = document.createElement("div");
    this._frontLayer.className = "sprite-layer front";
    this._frontImg = document.createElement("img");
    this._frontImg.alt = "Akane";
    this._frontImg.draggable = false;
    this._frontLayer.appendChild(this._frontImg);
    container.appendChild(this._frontLayer);
  }

  async showEmotion(emotion, outfit, backendUrl) {
    if (!this._frontImg || !this._backImg) return null;

    const resolved = this._resolveSprite(emotion, outfit, backendUrl);
    const url = resolved.url;
    if (!url) return null;
    this._lastResolution = resolved;

    // If already showing this emotion on the active layer, skip
    const activeImg = this._activeOnFront ? this._frontImg : this._backImg;
    if (activeImg.src === url) return resolved;

    // Load into the inactive layer
    const targetImg = this._activeOnFront ? this._backImg : this._frontImg;
    const targetLayer = this._activeOnFront ? this._backLayer : this._frontLayer;

    const cached = this._cache.get(url);
    if (cached && cached.complete) {
      this._crossfade(targetImg, targetLayer, url);
      return;
    }

    // Preload then crossfade
    const preload = new Image();
    preload.src = url;
    preload.onload = () => {
      this._addToCache(url, preload);
      if (targetImg === (this._activeOnFront ? this._backImg : this._frontImg)) {
        this._crossfade(targetImg, targetLayer, url);
      }
    };
    preload.onerror = () => {
      const fallback = this._resolveSprite(DEFAULT_EMOTION, resolved.outfitId || outfit, backendUrl).url;
      if (url !== fallback) {
        const fbImg = new Image();
        fbImg.src = fallback;
        fbImg.onload = () => {
          this._addToCache(fallback, fbImg);
          if (targetImg === (this._activeOnFront ? this._backImg : this._frontImg)) {
            this._crossfade(targetImg, targetLayer, fallback);
          }
        };
      }
    };
    return resolved;
  }

  async reloadEmotion(emotion, outfit, backendUrl) {
    this._cache.clear();
    this._assetVersion = String(Date.now());
    return this.showEmotion(emotion, outfit, backendUrl);
  }

  setManifest(manifest) {
    this._manifest = manifest && typeof manifest === "object" ? manifest : null;
    this._cache.clear();
  }

  getLastResolution() {
    return this._lastResolution ? { ...this._lastResolution } : null;
  }

  _crossfade(targetImg, targetLayer, url) {
    targetImg.src = url;
    targetImg.classList.remove("fading-out");
    targetLayer.style.zIndex = "2";
    this._ensureHitMap(url);

    const oldImg = this._activeOnFront ? this._frontImg : this._backImg;
    const oldLayer = this._activeOnFront ? this._frontLayer : this._backLayer;
    oldLayer.style.zIndex = "1";

    // Start the crossfade: fade out old, fade in new
    requestAnimationFrame(() => {
      oldImg.classList.add("fading-out");
    });

    // After transition completes, swap which layer is "active".
    // Keep the previous layer faded out so transparent regions in the new pose
    // do not reveal the old sprite underneath.
    setTimeout(() => {
      this._activeOnFront = !this._activeOnFront;
    }, CROSSFADE_MS);
  }

  _resolveSprite(emotion, outfit, backendUrl) {
    return resolveManifestSprite({
      manifest: this._manifest,
      outfit,
      emotion,
      backendUrl,
      assetVersion: this._assetVersion,
    });
  }

  _addToCache(url, img) {
    this._cache.set(url, img);
    if (this._cache.size > IMAGE_CACHE_MAX) {
      const first = this._cache.keys().next().value;
      this._cache.delete(first);
    }
  }

  getElement() {
    return this._frontImg;
  }

  /** Returns the DOM node that should receive drag/click events. */
  getDragTarget() {
    return this._frontImg;
  }

  isOpaqueAtPoint(clientX, clientY, threshold = HIT_ALPHA_THRESHOLD) {
    const x = Number(clientX);
    const y = Number(clientY);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return false;

    for (const img of this._getHitCandidates()) {
      const result = this._isImageOpaqueAtPoint(img, x, y, threshold);
      if (result) return true;
    }
    return false;
  }

  _getHitCandidates() {
    const candidates = [
      { img: this._frontImg, layer: this._frontLayer },
      { img: this._backImg, layer: this._backLayer },
    ].filter((item) => item.img?.src && item.layer);

    return candidates
      .filter((item) => !item.img.classList.contains("fading-out"))
      .sort((a, b) => Number(b.layer.style.zIndex || 0) - Number(a.layer.style.zIndex || 0))
      .map((item) => item.img);
  }

  _isImageOpaqueAtPoint(img, clientX, clientY, threshold) {
    const rect = img.getBoundingClientRect();
    if (!rect.width || !rect.height) return false;
    if (clientX < rect.left || clientX > rect.right || clientY < rect.top || clientY > rect.bottom) {
      return false;
    }

    const hitMap = this._getHitMap(img.src);
    if (!hitMap || hitMap.status === "loading" || hitMap.status === "failed") {
      return true;
    }

    const u = Math.min(0.999999, Math.max(0, (clientX - rect.left) / rect.width));
    const v = Math.min(0.999999, Math.max(0, (clientY - rect.top) / rect.height));
    const px = Math.floor(u * hitMap.width);
    const py = Math.floor(v * hitMap.height);
    const alpha = hitMap.alpha[py * hitMap.width + px] || 0;
    return alpha >= threshold;
  }

  _getHitMap(url) {
    const key = String(url || "");
    if (!key) return null;

    const cached = this._hitCache.get(key);
    if (cached) return cached;

    const pending = { status: "loading", width: 0, height: 0, alpha: null };
    this._hitCache.set(key, pending);
    this._trimHitCache();
    this._buildHitMap(key)
      .then((hitMap) => {
        this._hitCache.set(key, hitMap);
        this._trimHitCache();
      })
      .catch((error) => {
        if (!this._hitWarningShown) {
          this._hitWarningShown = true;
          console.warn("[AkanePet] pixel hit-test unavailable, falling back to sprite rectangle:", error);
        }
        this._hitCache.set(key, { status: "failed", width: 0, height: 0, alpha: null });
        this._trimHitCache();
      });
    return pending;
  }

  _ensureHitMap(url) {
    this._getHitMap(url);
  }

  async _buildHitMap(url) {
    const response = await fetch(url, { cache: "force-cache" });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const blob = await response.blob();
    const bitmap = await createImageBitmap(blob);
    const scale = Math.min(1, HIT_MAP_MAX_SIZE / Math.max(bitmap.width, bitmap.height));
    const width = Math.max(1, Math.round(bitmap.width * scale));
    const height = Math.max(1, Math.round(bitmap.height * scale));

    this._hitCanvas.width = width;
    this._hitCanvas.height = height;
    this._hitContext.clearRect(0, 0, width, height);
    this._hitContext.drawImage(bitmap, 0, 0, width, height);
    bitmap.close?.();

    const pixels = this._hitContext.getImageData(0, 0, width, height).data;
    const alpha = new Uint8ClampedArray(width * height);
    for (let i = 0, j = 3; i < alpha.length; i += 1, j += 4) {
      alpha[i] = pixels[j];
    }

    return {
      status: "ready",
      width,
      height,
      alpha,
    };
  }

  _trimHitCache() {
    while (this._hitCache.size > HIT_CACHE_MAX) {
      const first = this._hitCache.keys().next().value;
      this._hitCache.delete(first);
    }
  }

  destroy() {
    if (this._container) {
      if (this._frontLayer) this._container.removeChild(this._frontLayer);
      if (this._backLayer) this._container.removeChild(this._backLayer);
    }
    this._frontImg = null;
    this._backImg = null;
    this._cache.clear();
    this._hitCache.clear();
  }
}

export { StaticSpriteRenderer };
