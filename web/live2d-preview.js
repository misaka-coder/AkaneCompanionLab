const MODEL_URL = "/assets/live2d/koharu/hiyori_free_t08.model3.json";
const MOUTH_PARAM_ID = "ParamMouthOpenY";

const statusLine = document.getElementById("status-line");
const mouthRange = document.getElementById("mouth-range");
const buttons = Array.from(document.querySelectorAll("button"));

const state = {
  app: null,
  model: null,
  talking: false,
  mouthValue: 0,
};

function setStatus(message) {
  statusLine.textContent = message;
}

function setControlsEnabled(enabled) {
  buttons.forEach((button) => {
    button.disabled = !enabled;
  });
  mouthRange.disabled = !enabled;
}

function resizeRenderer() {
  if (!state.app || !state.model) {
    return;
  }
  const width = window.innerWidth;
  const height = window.innerHeight;
  state.app.renderer.resize(width, height);

  const model = state.model;
  const bounds = model.getLocalBounds();
  const naturalWidth = Math.max(1, bounds.width);
  const naturalHeight = Math.max(1, bounds.height);
  const compact = width < 720;
  const maxModelWidth = width * (compact ? 0.82 : 0.42);
  const maxModelHeight = height * (compact ? 0.52 : 0.78);
  const fitScale = Math.min(maxModelWidth / naturalWidth, maxModelHeight / naturalHeight);

  model.scale.set(Math.max(0.08, Math.min(0.5, fitScale)));
  model.x = width * (compact ? 0.5 : 0.62);
  model.y = height * (compact ? 0.62 : 0.9);
}

function setMouth(value) {
  const normalized = Math.max(0, Math.min(1, Number(value) || 0));
  state.mouthValue = normalized;
  mouthRange.value = String(normalized);
  applyMouthParameter(normalized);
}

function applyMouthParameter(value = state.mouthValue) {
  if (!state.model?.internalModel?.coreModel) {
    return;
  }
  state.model.internalModel.coreModel.setParameterValueById(MOUTH_PARAM_ID, value);
}

async function playMotion(group, index = 0) {
  if (!state.model) {
    return;
  }
  try {
    await state.model.motion(group, index);
    setStatus(`已播放 motion: ${group}`);
  } catch (error) {
    setStatus(`motion ${group} 播放失败：${error}`);
  }
}

async function runTalkDemo() {
  if (!state.model || state.talking) {
    return;
  }
  state.talking = true;
  setStatus("正在模拟说话口型...");
  const startedAt = performance.now();
  const durationMs = 2600;

  function tick(now) {
    if (!state.talking) {
      setMouth(0);
      return;
    }
    const elapsed = now - startedAt;
    const progress = elapsed / durationMs;
    if (progress >= 1) {
      state.talking = false;
      setMouth(0);
      setStatus("口型测试完成。");
      return;
    }
    const envelope = Math.sin(progress * Math.PI);
    const wave = 0.5 + Math.sin(elapsed * 0.028) * 0.5;
    setMouth(Math.max(0.04, envelope * (0.24 + wave * 0.72)));
    requestAnimationFrame(tick);
  }

  requestAnimationFrame(tick);
}

async function initialize() {
  setControlsEnabled(false);

  if (!window.PIXI) {
    throw new Error("PIXI 未加载。");
  }
  if (!window.PIXI.live2d?.Live2DModel) {
    throw new Error("pixi-live2d-display 未加载。");
  }

  state.app = new PIXI.Application({
    view: document.getElementById("live2d-canvas"),
    autoStart: true,
    resizeTo: window,
    transparent: true,
    antialias: true,
    resolution: Math.min(window.devicePixelRatio || 1, 2),
  });

  setStatus("正在加载模型文件...");
  state.model = await PIXI.live2d.Live2DModel.from(MODEL_URL, {
    autoInteract: true,
  });
  state.model.anchor.set(0.5, 1);
  state.model.interactive = true;
  state.model.buttonMode = true;
  state.model.on("hit", (hitAreas) => {
    const areas = Array.isArray(hitAreas) ? hitAreas.join(", ") : String(hitAreas || "");
    void playMotion(areas.includes("Body") ? "Tap@Body" : "Tap");
  });
  state.app.stage.addChild(state.model);
  state.app.ticker.add(() => applyMouthParameter());
  resizeRenderer();
  setMouth(0);
  setStatus("模型已加载。可以测试 motion 和嘴型。");
  setControlsEnabled(true);
  void playMotion("Idle", 0);
}

document.getElementById("motion-idle").addEventListener("click", () => {
  void playMotion("Idle", 0);
});

document.getElementById("motion-tap").addEventListener("click", () => {
  void playMotion("Tap", 0);
});

document.getElementById("motion-body").addEventListener("click", () => {
  void playMotion("Tap@Body", 0);
});

document.getElementById("motion-flick").addEventListener("click", () => {
  void playMotion("Flick", 0);
});

document.getElementById("talk-demo").addEventListener("click", () => {
  void runTalkDemo();
});

document.getElementById("reset-mouth").addEventListener("click", () => {
  state.talking = false;
  setMouth(0);
});

mouthRange.addEventListener("input", (event) => {
  state.talking = false;
  setMouth(event.target.value);
});

window.addEventListener("resize", resizeRenderer);

initialize().catch((error) => {
  setStatus(`加载失败：${error.message || error}`);
  setControlsEnabled(false);
});
