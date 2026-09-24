import { createVisualRenderer } from "./visual-renderer.js";
import { observeBubbleLayout } from "./bubble-layout.js";

// The preview keeps desktop coordinates intact and scales the whole window once.
// Image placement and speech bubble geometry remain owned by the real renderer/CSS.
export function createScaledPetPreview({ host, ids = {}, editableBubble = false, onScale } = {}) {
  const viewport = document.createElement("div");
  viewport.className = "pet-preview-viewport";
  const stage = document.createElement("div");
  stage.className = "stage pet-preview-stage";
  stage.dataset.previewStatic = "true";
  const portrait = document.createElement("div");
  portrait.className = "pet-hitbox";
  const motion = document.createElement("div");
  motion.className = "portrait-motion";
  const image = document.createElement("img");
  image.alt = "立绘预览";
  image.draggable = false;
  motion.append(image);
  portrait.append(motion);
  const bubble = document.createElement("div");
  bubble.className = "bubble visible";
  bubble.dataset.size = "short";
  const text = document.createElement("span");
  text.className = "bubble-text";
  text.textContent = "晚上好呀。今天想聊些什么？";
  bubble.append(text);
  if (editableBubble) {
    bubble.classList.add("calibration-bubble-dot");
    bubble.setAttribute("role", "button");
    bubble.tabIndex = 0;
    bubble.title = "拖动或使用方向键调整气泡位置";
    bubble.setAttribute("aria-label", bubble.title);
  }
  const notice = document.createElement("p");
  notice.className = "pet-preview-notice";
  notice.hidden = true;
  for (const [key, element] of Object.entries({ stage, portrait, image, bubble })) {
    if (ids[key]) element.id = ids[key];
  }
  stage.append(portrait, bubble);
  viewport.append(stage);
  host.classList.add("pet-preview-host");
  host.replaceChildren(viewport, notice);
  const renderer = createVisualRenderer({ stage, image, onImageLoadError: () => {
    notice.textContent = renderer.getStatus().expression ? "新图片加载失败，暂保留上一张立绘。" : "立绘图片加载失败。";
    notice.hidden = false;
  } });
  let width = 340;
  let height = 560;
  let fitScale = 1;
  const fit = () => {
    const style = getComputedStyle(host);
    const availableWidth = host.clientWidth - Number.parseFloat(style.paddingLeft || 0) - Number.parseFloat(style.paddingRight || 0);
    const availableHeight = host.clientHeight - Number.parseFloat(style.paddingTop || 0) - Number.parseFloat(style.paddingBottom || 0);
    if (availableWidth <= 0 || availableHeight <= 0) return;
    fitScale = Math.min(1, availableWidth / width, availableHeight / height);
    viewport.style.width = `${width * fitScale}px`;
    viewport.style.height = `${height * fitScale}px`;
    stage.style.transform = `scale(${fitScale})`;
    onScale?.({ width, height, scale: fitScale });
  };
  const stopBubble = observeBubbleLayout({ stage, bubble });
  const observer = new ResizeObserver(fit);
  observer.observe(host);
  const api = {
    stage, portrait, image, bubble, text,
    setLayout(layout, { petScale = 1 } = {}) {
      width = boundedDimension(layout?.window?.width, 340, 1200);
      height = boundedDimension(layout?.window?.height, 560, 1600);
      stage.style.width = `${width}px`;
      stage.style.height = `${height}px`;
      stage.style.setProperty("--pet-viewport-width", `${width}px`);
      stage.style.setProperty("--pet-viewport-height", `${height}px`);
      stage.style.setProperty("--pet-scale", String(petScale));
      renderer.setLayout(layout);
      fit();
    },
    setExpression(expression) {
      notice.hidden = true;
      renderer.setExpression(expression);
    },
    clear(message = "") {
      renderer.clearExpression();
      notice.textContent = message;
      notice.hidden = !message;
    },
    setText(value) {
      text.textContent = String(value || "晚上好呀。今天想聊些什么？");
      bubble.dataset.size = text.textContent.length > 50 ? "long" : "short";
    },
    getGeometry: () => ({ width, height, scale: fitScale }),
    dispose() { observer.disconnect(); stopBubble(); renderer.dispose(); },
  };
  api.setLayout(null);
  return api;
}

function boundedDimension(value, fallback, max) {
  const number = Number(value);
  return Number.isFinite(number) && number >= 200 ? Math.min(max, Math.round(number)) : fallback;
}
