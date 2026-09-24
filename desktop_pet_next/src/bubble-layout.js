// Measure the rendered bubble, so anchors remain usable at every window edge.
// This owns geometry only; reply timing and audio remain in the delivery layer.
export function observeBubbleLayout({ stage, bubble, obstruction, onResize } = {}) {
  if (!stage || !bubble) return () => {};
  const update = () => {
    stage.style.setProperty("--bubble-half-width", `${bubble.offsetWidth / 2}px`);
    stage.style.setProperty("--bubble-height", `${bubble.offsetHeight}px`);
    if (obstruction) {
      const rect = obstruction.getBoundingClientRect();
      const stageRect = stage.getBoundingClientRect();
      const style = getComputedStyle(obstruction);
      if (rect.height > 0 && style.visibility !== "hidden" && style.display !== "none") {
        stage.style.setProperty("--bubble-bottom-limit", `${rect.top - stageRect.top}px`);
      } else {
        stage.style.removeProperty("--bubble-bottom-limit");
      }
    }
    onResize?.();
  };
  const observer = new ResizeObserver(update);
  const onTransitionEnd = (event) => {
    if (event.target === bubble && event.propertyName === "transform") update();
  };
  bubble.addEventListener("transitionend", onTransitionEnd);
  observer.observe(bubble);
  observer.observe(stage);
  if (obstruction) observer.observe(obstruction);
  update();
  return () => {
    observer.disconnect();
    bubble.removeEventListener("transitionend", onTransitionEnd);
  };
}
