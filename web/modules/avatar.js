const LIVE2D_MODEL_URL = "/assets/live2d/koharu/hiyori_free_t08.model3.json";
const MOUTH_PARAM_ID = "ParamMouthOpenY";
const LIVE2D_SCRIPTS = [
  "/vendor/live2d/pixi.min.js",
  "/vendor/live2d/live2dcubismcore.min.js",
  "/vendor/live2d/cubism4.min.js",
];

const DEFAULT_EMOTION = "normal";
const MOTION_REPEAT_GUARD_MS = 700;

const LIVE2D_EMOTION_ALIASES = {
  happy: "smug",
  joy: "smug",
  smile: "smug",
  cry: "sad",
  quiet: "normal",
  angry: "dizzy",
  rage_burst: "dizzy",
  battle_focus: "smug",
  surprise: "dizzy",
  surprised: "dizzy",
  sumg: "smug",
};

const LIVE2D_EMOTION_BEHAVIOR = {
  normal: {
    enterMotion: "Idle",
    endMotion: "Idle",
  },
  smug: {
    enterMotion: "Tap",
    endMotion: "Idle",
  },
  shy: {
    enterMotion: "Tap@Body",
    endMotion: "Idle",
  },
  sad: {
    enterMotion: "FlickDown",
    endMotion: "Idle",
  },
  dizzy: {
    enterMotion: "Flick",
    endMotion: "Idle",
  },
  sleeping: {
    enterMotion: "Idle",
    endMotion: "Idle",
  },
};

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function normalizeMode(mode) {
  return String(mode || "").trim().toLowerCase() === "live2d" ? "live2d" : "static";
}

function normalizeEmotion(emotion) {
  const key = String(emotion || DEFAULT_EMOTION).trim().toLowerCase() || DEFAULT_EMOTION;
  return LIVE2D_EMOTION_ALIASES[key] || key;
}

function getEmotionBehavior(emotion) {
  return LIVE2D_EMOTION_BEHAVIOR[normalizeEmotion(emotion)] || LIVE2D_EMOTION_BEHAVIOR[DEFAULT_EMOTION];
}

function nextAnimationFrame() {
  return new Promise((resolve) => {
    requestAnimationFrame(() => resolve());
  });
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[src="${src}"]`);
    if (existing?.dataset.loaded === "true") {
      resolve();
      return;
    }

    const script = existing || document.createElement("script");
    script.src = src;
    script.async = false;
    script.onload = () => {
      script.dataset.loaded = "true";
      resolve();
    };
    script.onerror = () => reject(new Error(`Live2D runtime load failed: ${src}`));
    if (!existing) {
      document.head.appendChild(script);
    }
  });
}

async function ensureLive2dRuntime() {
  if (window.PIXI?.live2d?.Live2DModel) {
    return;
  }
  for (const src of LIVE2D_SCRIPTS) {
    await loadScript(src);
  }
  if (!window.PIXI?.live2d?.Live2DModel) {
    throw new Error("pixi-live2d-display is unavailable");
  }
}

export function createAvatarController({ appEl, canvasEl, debugOutputEl, wait }) {
  const state = {
    mode: "static",
    app: null,
    model: null,
    loadPromise: null,
    modeToken: 0,
    mouthValue: 0,
    talking: false,
    talkToken: 0,
    emotionToken: 0,
    lastEmotion: DEFAULT_EMOTION,
    lastMotionAt: 0,
    lastMotionKey: "",
    pendingEndMotion: "Idle",
    resizeHandler: null,
  };

  function setDebug(message) {
    if (debugOutputEl && message) {
      debugOutputEl.textContent = message;
    }
  }

  function applyMouthParameter(value = state.mouthValue) {
    if (!state.model?.internalModel?.coreModel) {
      return;
    }
    state.model.internalModel.coreModel.setParameterValueById(MOUTH_PARAM_ID, value);
  }

  function resizeLive2d() {
    if (!state.app || !state.model || !canvasEl) {
      return;
    }

    const rect = canvasEl.getBoundingClientRect();
    const width = Math.max(1, Math.round(rect.width));
    const height = Math.max(1, Math.round(rect.height));
    state.app.renderer.resize(width, height);

    const bounds = state.model.getLocalBounds();
    const naturalWidth = Math.max(1, bounds.width);
    const naturalHeight = Math.max(1, bounds.height);
    const fitScale = Math.min((width * 0.74) / naturalWidth, (height * 0.92) / naturalHeight);

    state.model.scale.set(clamp(fitScale, 0.06, 0.5));
    state.model.x = width * 0.5;
    state.model.y = height * 0.96;
  }

  async function playMotion(group, index = 0, options = {}) {
    if (state.mode !== "live2d" || !state.model || !group) {
      return;
    }

    const motionKey = `${group}:${index}`;
    const now = performance.now();
    if (!options.force && state.lastMotionKey === motionKey && now - state.lastMotionAt < MOTION_REPEAT_GUARD_MS) {
      return;
    }

    try {
      state.lastMotionKey = motionKey;
      state.lastMotionAt = now;
      canvasEl.dataset.motion = group;
      await state.model.motion(group, index);
    } catch (error) {
      console.warn("live2d motion failed", group, error);
    }
  }

  async function initializeLive2d() {
    if (state.model) {
      return state.model;
    }
    if (state.loadPromise) {
      return state.loadPromise;
    }

    state.loadPromise = (async () => {
      canvasEl.dataset.live2dReady = "false";
      await ensureLive2dRuntime();
      state.app = new PIXI.Application({
        view: canvasEl,
        autoStart: true,
        transparent: true,
        antialias: true,
        resolution: Math.min(window.devicePixelRatio || 1, 2),
      });

      const model = await PIXI.live2d.Live2DModel.from(LIVE2D_MODEL_URL, {
        autoInteract: true,
      });
      model.anchor.set(0.5, 1);
      model.interactive = true;
      model.buttonMode = true;
      model.on("hit", (hitAreas) => {
        const areas = Array.isArray(hitAreas) ? hitAreas.join(", ") : String(hitAreas || "");
        void playMotion(areas.includes("Body") ? "Tap@Body" : "Tap", 0, { force: true });
      });

      state.app.stage.addChild(model);
      state.app.ticker.add(() => applyMouthParameter());
      state.model = model;
      resizeLive2d();
      await playMotion("Idle", 0, { force: true });
      canvasEl.dataset.live2dReady = "true";
      return model;
    })();

    try {
      return await state.loadPromise;
    } finally {
      state.loadPromise = null;
    }
  }

  async function setMode(mode) {
    const nextMode = normalizeMode(mode);
    const token = state.modeToken + 1;
    state.modeToken = token;
    state.mode = nextMode;
    if (nextMode === "static") {
      appEl.dataset.avatarMode = "static";
      stopTalking();
      return;
    }

    try {
      await initializeLive2d();
      if (token !== state.modeToken || state.mode !== "live2d") {
        return;
      }
      appEl.dataset.avatarMode = "live2d";
      await nextAnimationFrame();
      state.app?.start();
      resizeLive2d();
      state.app?.renderer?.render(state.app.stage);
      if (!state.resizeHandler) {
        state.resizeHandler = () => resizeLive2d();
        window.addEventListener("resize", state.resizeHandler);
      }
      if (state.lastEmotion) {
        await showEmotion(state.lastEmotion);
      }
    } catch (error) {
      canvasEl.dataset.live2dReady = "false";
      state.mode = "static";
      appEl.dataset.avatarMode = "static";
      setDebug(`Live2D 加载失败，已回退静态立绘：${error?.message || error}`);
      throw error;
    }
  }

  async function showEmotion(emotion, options = {}) {
    const normalized = normalizeEmotion(emotion);
    const behavior = getEmotionBehavior(normalized);
    const token = state.emotionToken + 1;
    state.emotionToken = token;
    state.lastEmotion = normalized;
    state.pendingEndMotion = behavior.endMotion || "Idle";
    canvasEl.dataset.emotion = normalized;
    if (state.mode !== "live2d") {
      return;
    }
    await initializeLive2d();
    if (token !== state.emotionToken || state.mode !== "live2d") {
      return;
    }
    await playMotion(behavior.enterMotion || "Idle", 0, { force: options.force === true });
  }

  async function playEmotionEndMotion(options = {}) {
    const behavior = getEmotionBehavior(state.lastEmotion);
    const group = state.pendingEndMotion || behavior.endMotion || "Idle";
    if (state.mode !== "live2d" || !group || canvasEl.dataset.talking === "true") {
      return;
    }

    const token = state.emotionToken + 1;
    state.emotionToken = token;
    await initializeLive2d();
    if (token !== state.emotionToken || state.mode !== "live2d" || canvasEl.dataset.talking === "true") {
      return;
    }
    await playMotion(group, 0, { force: options.force === true });
  }

  function setMouth(value) {
    state.mouthValue = clamp(Number(value) || 0, 0, 1);
    applyMouthParameter();
  }

  async function beginExternalLipSync(label = "external") {
    if (state.mode !== "live2d") {
      return false;
    }
    state.talkToken += 1;
    state.talking = false;
    await initializeLive2d();
    if (state.mode !== "live2d") {
      return false;
    }
    canvasEl.dataset.talking = "true";
    canvasEl.dataset.lipSync = label;
    return true;
  }

  function endExternalLipSync() {
    stopTalking();
  }

  async function startTalking() {
    if (state.mode !== "live2d" || state.talking) {
      return;
    }
    const token = state.talkToken + 1;
    state.talkToken = token;
    await initializeLive2d();
    if (token !== state.talkToken || state.mode !== "live2d") {
      return;
    }
    state.talking = true;
    canvasEl.dataset.talking = "true";
    canvasEl.dataset.lipSync = "fallback";

    async function tick() {
      while (state.talking) {
        const wave = 0.28 + Math.random() * 0.62;
        setMouth(wave);
        await wait(70 + Math.random() * 90);
      }
      setMouth(0);
    }

    void tick();
  }

  function stopTalking() {
    state.talkToken += 1;
    state.talking = false;
    canvasEl.dataset.talking = "false";
    canvasEl.dataset.lipSync = "off";
    setMouth(0);
  }

  async function preload() {
    try {
      await initializeLive2d();
    } catch (error) {
      console.warn("live2d preload failed", error);
    }
  }

  return {
    setMode,
    preload,
    showEmotion,
    playEmotionEndMotion,
    beginExternalLipSync,
    endExternalLipSync,
    startTalking,
    stopTalking,
    setMouth,
  };
}
