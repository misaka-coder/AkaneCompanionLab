import { normalizePresentationPreferences, presentationCssVariables, resetPresentationFrame,
  resolveThemeMode, savePresentationPreferences, updatePresentationFrame } from "../presentation-preferences.js";

export function createPresentationController({ root, store, documentRef = document, windowRef = window }) {
  let live = null, drag = null, noticeTimer = null;
  let packId = store.getState().presentationPackId;
  const themeQuery = windowRef.matchMedia?.("(prefers-color-scheme: light)") || null;

  function apply(preferences) {
    const html = documentRef.documentElement;
    html.dataset.theme = resolveThemeMode(preferences?.themeMode, Boolean(themeQuery?.matches));
    html.dataset.themeMode = preferences?.themeMode || "system";
    html.dataset.accent = preferences?.accentPreset || "violet";
    html.dataset.font = preferences?.fontPreset || "system";
    html.dataset.reducedMotion = preferences?.reducedMotion ? "true" : "false";
    for (const [name, value] of Object.entries(presentationCssVariables(preferences))) html.style.setProperty(name, value);
  }
  function synchronizePack(next) {
    if (packId === next) return;
    packId = next; live = null; drag = null;
  }
  function previewFrame(patch) {
    const state = store.getState();
    live = updatePresentationFrame(live || state.presentationPreferences, state.framingTarget, patch);
    apply(live);
  }
  function previewPreferences(patch) {
    live = normalizePresentationPreferences({ ...(live || store.getState().presentationPreferences), ...patch });
    apply(live);
  }
  function save(preferences) {
    const state = store.getState(), normalized = normalizePresentationPreferences(preferences);
    live = normalized;
    const saved = savePresentationPreferences(state.presentationPackId || "default", normalized);
    const notice = saved ? { phase: "confirmed", label: "外观设置已保存", detail: "仅应用于这台设备" }
      : { phase: "failed", label: "外观设置未保存", detail: "本地存储当前不可用" };
    store.patch({ presentationPreferences: normalized, presentationNotice: notice });
    windowRef.clearTimeout(noticeTimer);
    noticeTimer = windowRef.setTimeout(() => {
      if (store.getState().presentationNotice === notice) store.patch({ presentationNotice: null });
    }, saved ? 1600 : 5000);
    live = null;
  }
  function handleClick(event) {
    for (const [selector, field, dataset] of [
      ["button[data-theme-mode]", "themeMode", "themeMode"],
      ["button[data-accent-preset]", "accentPreset", "accentPreset"],
      ["button[data-font-preset]", "fontPreset", "fontPreset"]
    ]) {
      const button = event.target.closest(selector);
      if (button) { save({ ...store.getState().presentationPreferences, [field]: button.dataset[dataset] }); return true; }
    }
    const toggle = event.target.closest("button[data-presentation-toggle]");
    if (toggle) {
      if (toggle.dataset.presentationToggle === "reducedMotion") {
        const preferences = store.getState().presentationPreferences;
        save({ ...preferences, reducedMotion: !preferences.reducedMotion });
      }
      return true;
    }
    const target = event.target.closest("[data-framing-target]");
    if (target) { store.patch({ framingTarget: target.dataset.framingTarget || "portrait" }); return true; }
    const reset = event.target.closest("[data-framing-reset]");
    if (!reset) return false;
    save(resetPresentationFrame(store.getState().presentationPreferences, store.getState().framingTarget));
    return true;
  }
  function handleInput(event) {
    if (event.target.matches("[data-presentation-range]")) {
      const field = event.target.dataset.presentationRange, value = Number(event.target.value);
      previewPreferences({ [field]: value });
      const label = root.querySelector(`[data-presentation-value="${field}"]`);
      if (label) label.textContent = `${Math.round(value)}${event.target.dataset.presentationUnit || ""}`;
    }
    if (event.target.matches("[data-framing-scale]")) {
      const scale = Number(event.target.value) / 100;
      previewFrame({ scale });
      const label = root.querySelector("[data-framing-scale-label]");
      if (label) label.textContent = `${Math.round(scale * 100)}%`;
    }
  }
  function handleChange(event) {
    if (live && event.target.matches("[data-presentation-range], [data-framing-scale]")) save(live);
  }
  function handleKeydown(event) {
    const stage = event.target.closest?.("[data-framing-stage]");
    if (!stage || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return false;
    event.preventDefault();
    const state = store.getState(), frame = state.presentationPreferences.frames?.[state.framingTarget] || { x: 50, y: 50 };
    const step = event.shiftKey ? 5 : 2;
    previewFrame({ x: frame.x + (event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0),
      y: frame.y + (event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0) });
    save(live);
    return true;
  }
  function updatePosition(event, stage) {
    const rect = stage.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const portrait = stage.dataset.target === "portrait";
    previewFrame({ x: Math.max(0, Math.min(100, ((event.clientX - rect.left) / rect.width) * 100)),
      y: (portrait ? 60 : 0) + Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)) * (portrait ? 80 : 100) });
  }
  function handlePointerdown(event) {
    const stage = event.target.closest("[data-framing-stage]");
    if (!stage || event.button !== 0) return;
    event.preventDefault(); drag = { pointerId: event.pointerId };
    stage.setPointerCapture?.(event.pointerId); updatePosition(event, stage);
  }
  function handlePointermove(event) {
    if (!drag || drag.pointerId !== event.pointerId) return;
    const stage = root.querySelector("[data-framing-stage]");
    if (stage) updatePosition(event, stage);
  }
  function handlePointerup(event) {
    if (!drag || drag.pointerId !== event.pointerId) return;
    drag = null; if (live) save(live);
  }
  function handlePointercancel(event) {
    if (!drag || drag.pointerId !== event.pointerId) return;
    drag = null; live = null; apply(store.getState().presentationPreferences);
  }
  const applyCurrent = () => apply(live || store.getState().presentationPreferences);
  themeQuery?.addEventListener?.("change", applyCurrent);
  function dispose() {
    windowRef.clearTimeout(noticeTimer); themeQuery?.removeEventListener?.("change", applyCurrent);
    live = null; drag = null;
  }
  return { handleClick, handleInput, handleChange, handleKeydown, handlePointerdown, handlePointermove,
    handlePointerup, handlePointercancel, synchronizePack, applyCurrent, isDragging: () => Boolean(drag), dispose };
}
