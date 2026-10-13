import { createScaledPetPreview } from "../scaled-pet-preview.js";
import { getPackName } from "./pack-model.js";
import { formatError, clampNumber } from "./shared.js";
import { readUnitValue, readSliderUnit, normalizeBubbleStyle } from "./calibration-model.js";
import { DEFAULT_BUBBLE_STYLE, BUBBLE_STYLE_LABELS } from "./constants.js";

export function createCalibrationController({
  els,
  calibrationState,
  isPackWriteBusy,
  view,
  findPack,
  setStatus,
  invoke,
  getPortraitImageUrl,
  writeState,
  updateCatalogControls,
  emitCharacterPackActivated,
}) {
  const calibrationInputs = Object.fromEntries([
    els.calWinW, els.calWinH, els.calScale, els.calOffsetX,
    els.calOffsetY, els.calBubbleX, els.calBubbleY,
  ].filter(Boolean).map((input) => [input.id, input]));

  const calibrationPreview = createScaledPetPreview({
    host: els.calibrationPreviewArea,
    ids: { stage: "calibration-frame", portrait: "calibration-portrait", image: "calibration-image", bubble: "calibration-bubble-dot" },
    editableBubble: true,
    onScale: ({ width, height, scale }) => {
      els.calibrationPreviewScale.textContent = `${width} × ${height} · 预览 ${Math.round(scale * 100)}%`;
    },
  });

  Object.assign(els, { calibrationFrame: calibrationPreview.stage, calibrationPortrait: calibrationPreview.portrait,
    calibrationImage: calibrationPreview.image, calibrationBubbleDot: calibrationPreview.bubble });

  function showCalibrationEmpty() {
    invalidateCalibration();
    if (els.calibrationContent) els.calibrationContent.hidden = true;
    if (els.calibrationEmptyState) els.calibrationEmptyState.hidden = false;
  }

  function updateCalibrationControls() {
    const ready = calibrationState.calibrationReadyTarget;
    const enabled = Boolean(ready && isCalibrationCurrent(ready.packId, ready.requestToken)
      && !isPackWriteBusy(ready.packId));
    for (const input of [...Object.values(calibrationInputs), ...getCalibrationBubbleStyleButtons(),
      els.calibrationAutoBtn, els.calibrationSaveBtn]) {
      if (input) input.disabled = !enabled;
    }
    els.calibrationBubbleDot?.setAttribute("aria-disabled", String(!enabled));
  }

  function clearCalibrationPreview() {
    ++calibrationState.calibrationImageToken;
    calibrationState.calibrationReadyTarget = null;
    els.calibrationImage.onload = null;
    els.calibrationImage.onerror = null;
    calibrationPreview.clear();
    delete els.calibrationBubbleDot.dataset.dragging;
    updateCalibrationControls();
  }

  function invalidateCalibration() {
    ++calibrationState.calibrationRequestToken;
    clearCalibrationPreview();
    els.calibrationOutfitSelect.disabled = true;
  }

  function isCalibrationCurrent(packId, requestToken) {
    return requestToken === calibrationState.calibrationRequestToken && view.activeTab === "calibration"
      && view.workspacePackId === packId && Boolean(findPack(packId));
  }

  async function loadCalibrationTab(packId) {
    invalidateCalibration();
    const requestToken = calibrationState.calibrationRequestToken;
    const pack = findPack(packId);
    if (!pack) { showCalibrationEmpty(); return; }

    setStatus(`正在加载校准数据：${getPackName(pack)}`);
    try {
      const outfits = await invoke("list_pack_assets", { packId });
      if (!isCalibrationCurrent(packId, requestToken)) return;
      const items = Array.isArray(outfits) ? outfits : [];
      if (!items.length) {
        setStatus("请先上传立绘图片。");
        showCalibrationEmpty();
        return;
      }

      /* populate outfit selector */
      els.calibrationOutfitSelect.replaceChildren(
        ...items.map((o) => {
          const opt = document.createElement("option");
          opt.value = o.id;
          opt.textContent = o.name || o.id;
          return opt;
        })
      );

      /* show calibration UI */
      if (els.calibrationContent) els.calibrationContent.hidden = false;
      if (els.calibrationEmptyState) els.calibrationEmptyState.hidden = true;

      const profile = pack?._rawProfile || {};
      const configuredOutfit = String(
        profile?.appearance?.default_outfit || profile?.appearance?.defaultOutfit || ""
      ).trim();
      const defaultOutfit = items.find((item) => item.id === configuredOutfit) || items[0];
      els.calibrationOutfitSelect.value = defaultOutfit.id;
      els.calibrationOutfitSelect.disabled = false;

      /* outfit switch */
      els.calibrationOutfitSelect.onchange = async () => {
        if (!isCalibrationCurrent(packId, requestToken)) return;
        const sel = els.calibrationOutfitSelect.value;
        const outfit = items.find((o) => o.id === sel);
        if (outfit) await loadCalibrationOutfitPreview(packId, outfit, profile, requestToken);
      };

      /* slider bindings */
      bindCalibrationSliders();

      /* auto layout button */
      els.calibrationAutoBtn.onclick = () => resetCalibrationLayout();

      /* save button */
      els.calibrationSaveBtn.onclick = () => saveCalibration(packId);
      await loadCalibrationOutfitPreview(packId, defaultOutfit, profile, requestToken);

    } catch (error) {
      if (!isCalibrationCurrent(packId, requestToken)) return;
      setStatus(`加载校准失败：${formatError(error)}`);
      showCalibrationEmpty();
    }
  }

  async function loadCalibrationOutfitPreview(packId, outfit, profile, requestToken = calibrationState.calibrationRequestToken) {
    if (!isCalibrationCurrent(packId, requestToken)) return false;
    clearCalibrationPreview();
    const token = calibrationState.calibrationImageToken;
    const current = () => token === calibrationState.calibrationImageToken && isCalibrationCurrent(packId, requestToken);
    const emotions = Array.isArray(outfit?.emotions) ? outfit.emotions : [];
    const configuredEmotion = String(
      profile?.appearance?.default_emotion || profile?.appearance?.defaultEmotion || ""
    ).trim();
    const emotion = emotions.find((item) => item.id === configuredEmotion) || emotions[0];
    if (!emotion) {
      els.calibrationImage.removeAttribute("src");
      setStatus(`服装“${outfit?.name || outfit?.id || "-"}”没有可预览的图片。`);
      return false;
    }

    setStatus(`正在加载校准预览：${outfit.name || outfit.id} / ${emotion.name || emotion.id}`);
    try {
      const url = await getPortraitImageUrl(packId, outfit.id, emotion);
      if (!current()) return false;
      els.calibrationImage.onload = () => {
        if (!current()) return;
        calibrationState.calibrationReadyTarget = { packId, outfitId: outfit.id, requestToken, token };
        loadLayoutForOutfit(packId, outfit.id);
        updateCalibrationControls();
        setStatus(`正在校准：${outfit.name || outfit.id} / ${emotion.name || emotion.id}`);
      };
      els.calibrationImage.onerror = () => {
        if (!current()) return;
        clearCalibrationPreview();
        setStatus(`校准预览加载失败：${outfit.name || outfit.id} / ${emotion.name || emotion.id}`);
      };
      els.calibrationImage.src = url;
      return true;
    } catch (error) {
      if (current()) {
        clearCalibrationPreview();
        setStatus(`校准预览加载失败：${formatError(error)}`);
      }
      return false;
    }
  }

  function loadLayoutForOutfit(packId, outfitId) {
    const layout = findPack(packId)?._rawProfile?.layout?.outfits?.[outfitId];
    fillCalibrationControls(layout || {});
  }

  function fillCalibrationControls(layout) {
    setCalibrationSlider("cal-win-w", layout.window?.width ?? 340);
    setCalibrationSlider("cal-win-h", layout.window?.height ?? 560);
    setCalibrationSlider("cal-scale", (layout.portrait?.scale ?? 1) * 100);
    setCalibrationSlider("cal-offset-x", layout.portrait?.offset_x ?? 0);
    setCalibrationSlider("cal-offset-y", layout.portrait?.offset_y ?? 0);
    setCalibrationSlider("cal-bubble-x", readUnitValue(layout.bubble?.anchor_x, 0.5) * 100);
    setCalibrationSlider("cal-bubble-y", readUnitValue(layout.bubble?.anchor_y, 0.12) * 100);
    setCalibrationBubbleStyle(layout.bubble?.style || layout.bubble?.theme || DEFAULT_BUBBLE_STYLE, { updatePreview: false });
    applyCalibrationPreview();
  }

  function resetCalibrationLayout() {
    if (!calibrationState.calibrationReadyTarget) return;
    fillCalibrationControls({});
    setStatus("已恢复默认布局，保存后应用到角色包。");
  }

  function setCalibrationSlider(id, value) {
    const el = calibrationInputs[id];
    if (!el || !Number.isFinite(Number(value))) return;
    // Loading an existing pack must not snap its saved values to UI step limits.
    el.min = String(Math.min(Number(el.min), Number(value)));
    el.max = String(Math.max(Number(el.max), Number(value)));
    el.step = "any";
    el.value = String(value);
  }

  function bindCalibrationSliders() {
    if (calibrationState.calibrationSlidersBound) return;
    calibrationState.calibrationSlidersBound = true;
    for (const el of Object.values(calibrationInputs)) {
      const step = Number(el.step) || 1;
      el.addEventListener("input", () => {
        el.value = String(Math.round(Number(el.value) / step) * step);
        applyCalibrationPreview();
      });
    }
    bindCalibrationBubbleControls();
  }

  function bindCalibrationBubbleControls() {
    for (const button of getCalibrationBubbleStyleButtons()) {
      button.addEventListener("click", () => {
        setCalibrationBubbleStyle(button.dataset.calBubbleStyle);
      });
    }

    const bubble = els.calibrationBubbleDot;
    if (!bubble) return;

    bubble.addEventListener("pointerdown", (event) => {
      if (bubble.getAttribute("aria-disabled") === "true") return;
      event.preventDefault();
      bubble.setPointerCapture?.(event.pointerId);
      bubble.dataset.dragging = "true";
      setBubbleAnchorFromPointer(event);
    });
    bubble.addEventListener("pointermove", (event) => {
      if (bubble.getAttribute("aria-disabled") === "true") return;
      if (bubble.dataset.dragging !== "true") return;
      setBubbleAnchorFromPointer(event);
    });
    const stopDrag = (event) => {
      if (bubble.dataset.dragging !== "true") return;
      delete bubble.dataset.dragging;
      try {
        bubble.releasePointerCapture?.(event.pointerId);
      } catch {
        /* pointer capture may already be released */
      }
    };
    bubble.addEventListener("pointerup", stopDrag);
    bubble.addEventListener("pointercancel", stopDrag);
    bubble.addEventListener("keydown", (event) => {
      if (bubble.getAttribute("aria-disabled") === "true") return;
      const step = event.shiftKey ? 5 : 1;
      if (event.key === "ArrowLeft") adjustBubbleAnchor(-step, 0, event);
      if (event.key === "ArrowRight") adjustBubbleAnchor(step, 0, event);
      if (event.key === "ArrowUp") adjustBubbleAnchor(0, -step, event);
      if (event.key === "ArrowDown") adjustBubbleAnchor(0, step, event);
    });
  }

  function collectCalibrationLayout() {
    const target = calibrationState.calibrationReadyTarget;
    const saved = findPack(target?.packId)?._rawProfile?.layout?.outfits?.[target?.outfitId] || {};
    return {
      ...saved,
      window: { ...saved.window, width: Number(els.calWinW?.value) || 340, height: Number(els.calWinH?.value) || 560 },
      portrait: { ...saved.portrait, scale: (Number(els.calScale?.value) || 100) / 100,
        offset_x: Number(els.calOffsetX?.value) || 0, offset_y: Number(els.calOffsetY?.value) || 0,
        fit: saved.portrait?.fit || "contain", anchor: saved.portrait?.anchor || "bottom_center" },
      bubble: { ...saved.bubble, anchor_x: readSliderUnit(els.calBubbleX, 0.5), anchor_y: readSliderUnit(els.calBubbleY, 0.12),
        max_width: saved.bubble?.max_width ?? 300, style: getCalibrationBubbleStyle() },
    };
  }

  function applyCalibrationPreview() {
    const layout = collectCalibrationLayout();
    calibrationPreview.setLayout(layout, { petScale: view.desktopScale });
    updateCalLabel("cal-win-w-val", String(layout.window.width));
    updateCalLabel("cal-win-h-val", String(layout.window.height));
    updateCalLabel("cal-scale-val", layout.portrait.scale.toFixed(2));
    updateCalLabel("cal-offset-x-val", String(layout.portrait.offset_x));
    updateCalLabel("cal-offset-y-val", String(layout.portrait.offset_y));
    updateCalLabel("cal-bubble-x-val", layout.bubble.anchor_x.toFixed(2));
    updateCalLabel("cal-bubble-y-val", layout.bubble.anchor_y.toFixed(2));
    updateCalLabel("cal-bubble-style-val", BUBBLE_STYLE_LABELS[layout.bubble.style]);
  }

  function updateCalLabel(id, text) {
    const el = document.querySelector(`#${id}`);
    if (el) el.textContent = text;
  }

  function getCalibrationBubbleStyleButtons() {
    return Array.from(els.calBubbleStyle?.querySelectorAll("[data-cal-bubble-style]") || []);
  }

  function getCalibrationBubbleStyle() {
    return normalizeBubbleStyle(els.calBubbleStyle?.dataset.activeStyle);
  }

  function setCalibrationBubbleStyle(value, { updatePreview = true } = {}) {
    const style = normalizeBubbleStyle(value);
    if (els.calBubbleStyle) {
      els.calBubbleStyle.dataset.activeStyle = style;
    }
    for (const button of getCalibrationBubbleStyleButtons()) {
      const active = normalizeBubbleStyle(button.dataset.calBubbleStyle) === style;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    }
    updateCalLabel("cal-bubble-style-val", BUBBLE_STYLE_LABELS[style] || BUBBLE_STYLE_LABELS[DEFAULT_BUBBLE_STYLE]);
    if (updatePreview) applyCalibrationPreview();
  }

  function setBubbleAnchorFromPointer(event) {
    const rect = els.calibrationFrame?.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return;
    const x = clampNumber(((event.clientX - rect.left) / rect.width) * 100, 0, 100);
    const y = clampNumber(((event.clientY - rect.top) / rect.height) * 100, 0, 100);
    if (els.calBubbleX) els.calBubbleX.value = String(Math.round(x));
    if (els.calBubbleY) els.calBubbleY.value = String(Math.round(y));
    applyCalibrationPreview();
  }

  function adjustBubbleAnchor(dx, dy, event) {
    event.preventDefault();
    const x = Number(els.calBubbleX?.value);
    const y = Number(els.calBubbleY?.value);
    if (els.calBubbleX) els.calBubbleX.value = String(clampNumber((Number.isFinite(x) ? x : 50) + dx, 0, 100));
    if (els.calBubbleY) els.calBubbleY.value = String(clampNumber((Number.isFinite(y) ? y : 12) + dy, 0, 100));
    applyCalibrationPreview();
  }

  async function saveCalibration(packId) {
    const target = calibrationState.calibrationReadyTarget;
    if (!target || target.packId !== packId || !isCalibrationCurrent(packId, target.requestToken)
      || target.outfitId !== els.calibrationOutfitSelect.value || isPackWriteBusy(packId)) return;
    const { outfitId } = target;
    writeState.calibrationSaves.add(packId);
    ++writeState.registryRevision;
    updateCatalogControls();
    const ownsUi = () => calibrationState.calibrationReadyTarget === target && isCalibrationCurrent(packId, target.requestToken);
    setStatus(`正在保存校准（${outfitId}）…`);
    const layout = collectCalibrationLayout();

    try {
      await invoke("save_calibration", { packId, outfitId, layout });
      /* update local cache */
      const pack = findPack(packId);
      if (pack?._rawProfile) {
        if (!pack._rawProfile.layout) pack._rawProfile.layout = { outfits: {} };
        if (!pack._rawProfile.layout.outfits) pack._rawProfile.layout.outfits = {};
        pack._rawProfile.layout.outfits[outfitId] = layout;
      }
      if (String(view.activePackId || "").trim() === String(packId || "").trim()) {
        await emitCharacterPackActivated(packId);
      }
      if (ownsUi()) setStatus(`校准已保存（${outfitId}）。`);
    } catch (error) {
      if (ownsUi()) setStatus(`保存校准失败：${formatError(error)}`);
    } finally {
      writeState.calibrationSaves.delete(packId);
      ++writeState.registryRevision;
      updateCatalogControls();
    }
  }

  return { calibrationPreview, showCalibrationEmpty, updateCalibrationControls, invalidateCalibration, loadCalibrationTab, applyCalibrationPreview, saveCalibration, loadCalibrationOutfitPreview };
}
