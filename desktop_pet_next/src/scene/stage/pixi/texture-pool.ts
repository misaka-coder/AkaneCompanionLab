import { ImageSource, Texture } from "pixi.js";
import { loadArtwork } from "../../bridges/artwork";

/** A stage owns its textures; destroying one window cannot evict another stage. */
export class TexturePool {
  private values = new Map<string, Promise<Texture>>();
  private accessOrder: string[] = [];
  private readonly maxCached = 12;

  load(url: string): Promise<Texture> {
    const cached = this.values.get(url);
    if (cached) {
      this.touch(url);
      return cached;
    }
    const pending = loadArtwork(url)
      .then((blob) => createImageBitmap(blob))
      .then((image) => new Texture({ source: new ImageSource({ resource: image }) }))
      .catch((error) => {
        if (this.values.get(url) === pending) {
          this.values.delete(url);
          this.removeAccess(url);
        }
        throw error;
      });
    this.values.set(url, pending);
    this.touch(url);
    return pending;
  }

  private touch(url: string) {
    this.removeAccess(url);
    this.accessOrder.push(url);
  }

  private removeAccess(url: string) {
    const idx = this.accessOrder.indexOf(url);
    if (idx !== -1) this.accessOrder.splice(idx, 1);
  }

  retain(urls: Set<string>) {
    for (const url of urls) {
      if (this.values.has(url)) this.touch(url);
    }
    // Evict oldest unpinned textures when exceeding capacity
    while (this.values.size > this.maxCached && this.accessOrder.length > 0) {
      const oldest = this.accessOrder.find((u) => !urls.has(u));
      if (!oldest) break;
      this.evict(oldest);
    }
  }

  private evict(url: string) {
    const pending = this.values.get(url);
    this.values.delete(url);
    this.removeAccess(url);
    if (pending) {
      void pending.then(
        (texture) => {
          const bitmap = texture.source.resource as ImageBitmap;
          texture.destroy(true);
          bitmap?.close?.();
        },
        () => {},
      );
    }
  }

  dispose() {
    for (const url of [...this.values.keys()]) {
      this.evict(url);
    }
  }
}

