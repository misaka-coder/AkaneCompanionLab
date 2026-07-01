const STATIC_RENDERER_MODE = "static_portrait";
const LIVE2D_PENDING_MODE = "live2d_pending";
const DEFAULT_MOTION = "idle";
const VALID_MOTIONS = new Set([
  "idle",
  "thinking",
  "speaking",
  "click",
  "dragging",
  "drag-release",
  "thrown",
  "land",
  "hit-wall",
  "jump"
]);
const DEFAULT_BUBBLE_STYLE = "soft";
const VALID_BUBBLE_STYLES = new Set([DEFAULT_BUBBLE_STYLE, "paper", "clear", "dark"]);

export function createVisualRenderer({ stage, image, onImageLoadError } = {}) {
  let mode = STATIC_RENDERER_MODE;
  let characterLabel = "";
  let currentExpression = null;
  let expressionLoadToken = 0;
  let currentMotion = DEFAULT_MOTION;

  bindImageState();
  setRendererMode(STATIC_RENDERER_MODE);
  setMotion(DEFAULT_MOTION);

  function setRendererMode(nextMode) {
    mode = nextMode === "live2d" ? LIVE2D_PENDING_MODE : STATIC_RENDERER_MODE;
    if (stage) {
      stage.dataset.visualRenderer = mode;
    }
    return mode;
  }

  function setCharacterLabel(label) {
    characterLabel = String(label || "").trim();
    if (image && characterLabel) {
      image.alt = characterLabel;
      image.title = characterLabel;
    }
    if (stage && characterLabel) {
      stage.setAttribute("aria-label", `${characterLabel} desktop pet`);
    }
  }

  function setExpression(entry, { force = false } = {}) {
    const next = normalizeExpressionEntry(entry);
    if (!next.url) return currentExpression;
    if (!force && currentExpression?.id === next.id && image?.src) return currentExpression;

    const token = ++expressionLoadToken;
    if (!image) {
      currentExpression = next;
      if (stage) {
        stage.dataset.expression = next.id;
      }
      return currentExpression;
    }
    const hasVisibleImage = image.dataset.imageState === "ready" && image.naturalWidth > 0;

    image.alt = characterLabel || next.name || next.id;
    if (stage) {
      stage.dataset.expressionPending = next.id;
    }
    setImageState(hasVisibleImage ? "ready" : "loading");

    preloadImage(next.url)
      .then(() => {
        if (token !== expressionLoadToken) return;
        currentExpression = next;
        image.src = next.url;
        image.dataset.emotion = next.id;
        image.alt = characterLabel || next.name || next.id;
        if (stage) {
          stage.dataset.expression = next.id;
          delete stage.dataset.expressionPending;
        }
        setImageState("ready");
      })
      .catch((error) => {
        if (token !== expressionLoadToken) return;
        if (stage) {
          delete stage.dataset.expressionPending;
        }
        setImageState(hasVisibleImage ? "ready" : "error");
        notifyImageLoadError(next, error);
      });

    return next;
  }

  function setMotion(motion, { restart = false } = {}) {
    const next = normalizeMotion(motion);
    if (restart && next === "click" && stage?.dataset.motion === "click") {
      stage.dataset.motion = DEFAULT_MOTION;
      void stage.offsetWidth;
    }
    currentMotion = next;
    if (stage) {
      stage.dataset.motion = next;
    }
    return currentMotion;
  }

  function bindImageState() {
    if (!image) return;
    setImageState(image.getAttribute("src") ? "loading" : "empty");
    image.addEventListener("load", () => {
      setImageState("ready");
    });
    image.addEventListener("error", () => {
      setImageState("error");
    });
  }

  function setImageState(state) {
    if (image) {
      image.dataset.imageState = state;
    }
    if (stage) {
      stage.dataset.imageState = state;
    }
  }

  function notifyImageLoadError(expression, error) {
    if (typeof onImageLoadError !== "function") return;
    onImageLoadError({
      expression: { ...expression },
      error
    });
  }

  function setLayout(layout) {
    if (!stage) return;
    if (!layout || typeof layout !== "object") {
      if (image) {
        image.style.transform = "";
        image.style.transformOrigin = "";
      }
      resetBubbleLayout(stage);
      delete stage.dataset.layoutApplied;
      return;
    }
    const portrait = layout.portrait || {};
    const bubble = layout.bubble || {};

    const scale = Number(portrait.scale ?? 1) || 1;
    const offX = Number(portrait.offset_x ?? 0) || 0;
    const offY = Number(portrait.offset_y ?? 0) || 0;

    if (image) {
      image.style.transform = `translate(${offX}px, ${offY}px) scale(${scale})`;
      image.style.transformOrigin = normalizeTransformOrigin(portrait.anchor);
    }

    applyBubbleLayout(stage, bubble);
    stage.dataset.layoutApplied = "true";
  }

  function getStatus() {
    return {
      mode,
      characterLabel,
      expression: currentExpression ? { ...currentExpression } : null,
      motion: currentMotion,
      live2dReady: false,
      capabilities: {
        expressionImages: true,
        cssMotion: true,
        live2d: false,
        lipSync: false,
        motionGroups: false
      }
    };
  }

  return {
    getStatus,
    setCharacterLabel,
    setExpression,
    setMotion,
    setRendererMode,
    setLayout
  };
}

export function normalizeMotion(value) {
  const motion = String(value || "").trim();
  return VALID_MOTIONS.has(motion) ? motion : DEFAULT_MOTION;
}

function normalizeExpressionEntry(entry) {
  const source = entry && typeof entry === "object" ? entry : {};
  const id = String(source.id || source.name || "").trim();
  const name = String(source.name || source.id || id).trim();
  const url = String(source.url || source.path || "").trim();
  return { id, name, url };
}

function preloadImage(url) {
  return new Promise((resolve, reject) => {
    const probe = new Image();
    let settled = false;

    const cleanup = () => {
      probe.onload = null;
      probe.onerror = null;
    };
    const finish = () => {
      if (settled) return;
      if (probe.naturalWidth > 0 && probe.naturalHeight > 0) {
        settled = true;
        cleanup();
        resolve(probe);
        return;
      }
      fail(new Error("image_decoded_with_empty_size"));
    };
    const fail = (error) => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(error instanceof Error ? error : new Error("image_load_failed"));
    };
    const decode = () => {
      if (typeof probe.decode === "function") {
        probe.decode().then(finish).catch(fail);
        return;
      }
      finish();
    };

    probe.decoding = "async";
    probe.onload = decode;
    probe.onerror = () => fail(new Error("image_load_failed"));
    probe.src = url;
    if (probe.complete) {
      window.queueMicrotask(decode);
    }
  });
}

function normalizeTransformOrigin(value) {
  const origin = String(value || "").trim().replace(/_/g, " ");
  return origin || "bottom center";
}

function applyBubbleLayout(stage, bubble) {
  const source = bubble && typeof bubble === "object" ? bubble : {};
  const anchorX = normalizeUnit(source.anchor_x, 0.5);
  const anchorY = normalizeUnit(source.anchor_y, 0.12);
  const maxWidth = normalizePx(source.max_width, 300, 160, 520);
  const style = normalizeBubbleStyle(source.style || source.theme);

  stage.style.setProperty("--bubble-anchor-x", `${anchorX * 100}%`);
  stage.style.setProperty("--bubble-anchor-y", `${anchorY * 100}%`);
  stage.style.setProperty("--bubble-max-width", `${maxWidth}px`);
  stage.dataset.bubbleStyle = style;
  stage.dataset.bubbleSide = anchorX >= 0.5 ? "left" : "right";
}

function resetBubbleLayout(stage) {
  stage.style.removeProperty("--bubble-anchor-x");
  stage.style.removeProperty("--bubble-anchor-y");
  stage.style.removeProperty("--bubble-max-width");
  delete stage.dataset.bubbleStyle;
  delete stage.dataset.bubbleSide;
}

function normalizeUnit(value, fallback) {
  const next = Number(value);
  if (!Number.isFinite(next)) return fallback;
  return Math.min(1, Math.max(0, next));
}

function normalizePx(value, fallback, min, max) {
  const next = Number(value);
  if (!Number.isFinite(next)) return fallback;
  return Math.min(max, Math.max(min, next));
}

function normalizeBubbleStyle(value) {
  const style = String(value || "").trim().toLowerCase();
  return VALID_BUBBLE_STYLES.has(style) ? style : DEFAULT_BUBBLE_STYLE;
}
