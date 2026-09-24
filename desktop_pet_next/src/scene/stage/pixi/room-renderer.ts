import { Application, Container, Graphics, Sprite, Texture } from "pixi.js";
import type { StageFrame, StageRenderer } from "../renderer";
import type { TouchRegion } from "../../domain/types";
import { touchRegion } from "../../domain/touch";
import { TexturePool } from "./texture-pool";

export async function createRoomRenderer(
  element: HTMLElement,
  touch: (region: TouchRegion) => void,
): Promise<StageRenderer> {
  const app = new Application();
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  await app.init({
    resizeTo: element,
    backgroundAlpha: 0,
    antialias: true,
    resolution: Math.min(devicePixelRatio, 1.5),
    autoDensity: true,
    preference: "webgl",
  });
  element.appendChild(app.canvas);

  // Dual-layer backdrops for smooth cross-dissolve transitions (~350ms)
  const backdropPrev = new Sprite(Texture.EMPTY);
  const backdropCurr = new Sprite(Texture.EMPTY);
  const actor = new Container();

  // Dual-layer portraits for smooth expression cross-dissolve (~200ms)
  const portraitPrev = new Sprite(Texture.EMPTY);
  const portraitCurr = new Sprite(Texture.EMPTY);
  portraitPrev.anchor.set(0.5, 1);
  portraitCurr.anchor.set(0.5, 1);

  const motes = new Graphics();
  app.stage.addChild(backdropPrev, backdropCurr, actor, motes);
  actor.addChild(portraitPrev, portraitCurr);

  actor.eventMode = "static";
  actor.cursor = "pointer";
  actor.on("pointertap", (event) => {
    const p = portraitCurr.toLocal(event.global);
    const h = portraitCurr.texture.height || 1;
    const w = portraitCurr.texture.width || 1;
    const y = p.y / h + portraitCurr.anchor.y;
    const region = touchRegion(p.x / w + portraitCurr.anchor.x, y);
    if (region) touch(region);
  });

  const textures = new TexturePool();
  let wanted = new Set<string>();
  let generation = 0;
  let disposed = false;
  let current: StageFrame | null = null;
  let motion = "";
  let motionStart = 0;
  let frameTime = 0;

  // Cross-fade state
  const PORTRAIT_FADE_DURATION = 200;
  let isPortraitFading = false;
  let portraitFadeStart = 0;

  const BACKDROP_FADE_DURATION = 350;
  let isBackdropFading = false;
  let backdropFadeStart = 0;

  function layout() {
    if (!current) return;
    const { width, height } = app.screen;

    // Layout current backdrop
    if (backdropCurr.texture.width > 1) {
      const cover = Math.max(width / backdropCurr.texture.width, height / backdropCurr.texture.height);
      backdropCurr.scale.set(cover);
      backdropCurr.position.set((width - backdropCurr.width) / 2, (height - backdropCurr.height) / 2);
    }
    // Layout previous backdrop if fading
    if (isBackdropFading && backdropPrev.texture.width > 1) {
      const coverPrev = Math.max(width / backdropPrev.texture.width, height / backdropPrev.texture.height);
      backdropPrev.scale.set(coverPrev);
      backdropPrev.position.set((width - backdropPrev.width) / 2, (height - backdropPrev.height) / 2);
    }

    // Layout portraits
    if (portraitCurr.texture.height > 1) {
      portraitCurr.scale.set(((height * 1.03) / portraitCurr.texture.height) * current.scale);
      portraitCurr.anchor.set(current.anchorX, current.anchorY);
    }
    if (isPortraitFading && portraitPrev.texture.height > 1) {
      portraitPrev.scale.set(((height * 1.03) / portraitPrev.texture.height) * current.scale);
      portraitPrev.anchor.set(current.anchorX, current.anchorY);
    }

    actor.position.set(width * (current.position === "right" ? 0.66 : 0.51), height * 1.13);
  }

  const observer = new ResizeObserver(layout);
  observer.observe(element);

  const particles = Array.from({ length: reduced ? 0 : 16 }, (_, i) => ({
    x: ((i * 47) % 101) / 101,
    y: ((i * 31) % 97) / 97,
    radius: 0.9 + (i % 3) * 0.4,
  }));

  app.ticker.maxFPS = 60;
  app.ticker.add((ticker) => {
    frameTime += ticker.deltaMS;

    // 1. Backdrop cross-fade animation
    if (isBackdropFading) {
      const progress = Math.min(1, (performance.now() - backdropFadeStart) / BACKDROP_FADE_DURATION);
      backdropCurr.alpha = progress;
      backdropPrev.alpha = 1 - progress;
      if (progress >= 1) {
        isBackdropFading = false;
        backdropPrev.texture = Texture.EMPTY;
        backdropPrev.alpha = 0;
      }
    }

    // 2. Portrait expression cross-fade animation
    if (isPortraitFading) {
      const progress = Math.min(1, (performance.now() - portraitFadeStart) / PORTRAIT_FADE_DURATION);
      portraitCurr.alpha = progress;
      portraitPrev.alpha = 1 - progress;
      if (progress >= 1) {
        isPortraitFading = false;
        portraitPrev.texture = Texture.EMPTY;
        portraitPrev.alpha = 0;
      }
    }

    // 3. Motion and Idle Breathing Cycle
    if (motion) {
      const t = Math.min(1, (performance.now() - motionStart) / 500);
      actor.rotation = !reduced && motion === "shake" ? Math.sin(t * Math.PI * 4) * 0.025 * (1 - t) : 0;
      actor.scale.y =
        1 - Math.sin(t * Math.PI) * (reduced ? 0 : motion === "hand" || motion === "bounce" ? -0.022 : 0.014);
      actor.scale.x = 1;
      if (t === 1) {
        motion = "";
        actor.rotation = 0;
        actor.scale.set(1);
      }
    } else if (!reduced && current) {
      // Subtle natural breathing cycle when idle (~3.6s cycle, ±0.28% amplitude)
      const breath = Math.sin(frameTime / 580) * 0.0028;
      actor.scale.y = 1 + breath;
      actor.scale.x = 1 - breath * 0.45; // volume preservation
    }

    // 4. Ambient particles
    if (!particles.length) return;
    motes.clear();
    for (const p of particles) {
      const x = p.x * app.screen.width + Math.sin(frameTime / 7000 + p.y * 10) * 12;
      const y = ((p.y + frameTime / 160000) % 1) * app.screen.height;
      motes.circle(x, y, p.radius).fill({ color: 0xffeed0, alpha: 0.38 });
    }
  });

  function prune() {
    textures.retain(new Set([...wanted, ...(current ? [current.background, current.portrait] : [])]));
  }

  return {
    async load(frame) {
      const revision = ++generation;
      wanted = new Set([frame.background, frame.portrait]);
      let bg: Texture, image: Texture;
      try {
        [bg, image] = await Promise.all([textures.load(frame.background), textures.load(frame.portrait)]);
      } catch (error) {
        if (revision === generation) wanted.clear();
        prune();
        throw error;
      }
      if (disposed) return;
      if (revision !== generation) {
        prune();
        return;
      }

      // Smooth backdrop transition if previous background was loaded and different
      if (!reduced && backdropCurr.texture !== Texture.EMPTY && backdropCurr.texture !== bg) {
        backdropPrev.texture = backdropCurr.texture;
        backdropPrev.scale.copyFrom(backdropCurr.scale);
        backdropPrev.position.copyFrom(backdropCurr.position);
        backdropPrev.alpha = 1;
        backdropCurr.texture = bg;
        backdropCurr.alpha = 0;
        backdropFadeStart = performance.now();
        isBackdropFading = true;
      } else {
        backdropCurr.texture = bg;
        backdropCurr.alpha = 1;
        backdropPrev.texture = Texture.EMPTY;
        backdropPrev.alpha = 0;
        isBackdropFading = false;
      }

      // Smooth portrait expression transition if previous portrait was loaded and different
      if (!reduced && portraitCurr.texture !== Texture.EMPTY && portraitCurr.texture !== image) {
        portraitPrev.texture = portraitCurr.texture;
        portraitPrev.scale.copyFrom(portraitCurr.scale);
        portraitPrev.anchor.copyFrom(portraitCurr.anchor);
        portraitPrev.alpha = 1;
        portraitCurr.texture = image;
        portraitCurr.alpha = 0;
        portraitFadeStart = performance.now();
        isPortraitFading = true;
      } else {
        portraitCurr.texture = image;
        portraitCurr.alpha = 1;
        portraitPrev.texture = Texture.EMPTY;
        portraitPrev.alpha = 0;
        isPortraitFading = false;
      }

      current = frame;
      layout();
      prune();
    },
    clear() {
      generation++;
      current = null;
      wanted.clear();
      isBackdropFading = false;
      isPortraitFading = false;
      backdropPrev.texture = Texture.EMPTY;
      backdropCurr.texture = Texture.EMPTY;
      portraitPrev.texture = Texture.EMPTY;
      portraitCurr.texture = Texture.EMPTY;
      textures.dispose();
    },
    react(region) {
      motion = region === "shoulder" ? "shake" : region;
      motionStart = performance.now();
    },
    pause(paused) {
      if (paused) app.stop();
      else app.start();
    },
    dispose() {
      disposed = true;
      generation++;
      observer.disconnect();
      app.destroy(true, { children: true, texture: false });
      textures.dispose();
    },
  };
}
