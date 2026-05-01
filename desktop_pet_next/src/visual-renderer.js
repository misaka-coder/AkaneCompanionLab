const STATIC_RENDERER_MODE = "static_portrait";
const LIVE2D_PENDING_MODE = "live2d_pending";
const DEFAULT_MOTION = "idle";
const VALID_MOTIONS = new Set(["idle", "thinking", "speaking", "click"]);

export function createVisualRenderer({ stage, image } = {}) {
  let mode = STATIC_RENDERER_MODE;
  let characterLabel = "";
  let currentExpression = null;
  let currentMotion = DEFAULT_MOTION;

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

    currentExpression = next;
    if (image) {
      image.src = next.url;
      image.dataset.emotion = next.id;
      image.alt = characterLabel || next.name || next.id;
    }
    if (stage) {
      stage.dataset.expression = next.id;
    }
    return currentExpression;
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
    setRendererMode
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
  const url = String(source.url || "").trim();
  return { id, name, url };
}
