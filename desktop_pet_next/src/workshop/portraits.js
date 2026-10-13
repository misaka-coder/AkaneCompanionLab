import { getPackName } from "./pack-model.js";
import { buildText, flashElement, buildImageLoadError } from "./dom.js";
import { formatError, formatFileSize } from "./shared.js";
import { chooseLocalFiles, yieldToUiForLargeFileRead } from "./file-input.js";
import { inferEmotionNameFromFileName } from "./portrait-model.js";
import { MAX_PORTRAIT_IMAGE_BYTES } from "./constants.js";

export function createPortraitController({
  portraitState,
  isTauriRuntime,
  setStatus,
  findPack,
  els,
  refreshPortraitCutoutCapability,
  invoke,
  view,
  renderPortraitCutoutStatus,
  isPackWriteBusy,
  writeState,
  updateCatalogControls,
  clearPortraitImageCache,
  getPortraitImageUrl,
}) {
  async function loadPortraitsTab(packId) {
    if (!isPortraitTarget(packId)) return false;
    const token = ++portraitState.portraitRequestToken;
    const visit = portraitState.portraitVisitToken;
    const selected = portraitState.previewingEmotion?.packId === packId ? portraitState.previewingEmotion : null;
    portraitState.portraitLoadedPackId = "";
    clearPortraitPreview();
    updatePortraitControls();
    if (!isTauriRuntime) {
      showPortraitsEmpty();
      setStatus("浏览器预览模式不支持立绘管理。");
      return;
    }
    const pack = findPack(packId);
    if (!pack) {
      showPortraitsEmpty();
      return;
    }

    setStatus(`正在读取立绘数据：${getPackName(pack)}`);
    setPortraitStatus("正在读取角色包中的服装和图片…");
    els.portraitsContent.hidden = false;
    els.portraitsEmptyState.hidden = true;
    els.outfitsContainer.replaceChildren(buildText("p", "正在读取服装和图片…"));
    void refreshPortraitCutoutCapability();
    try {
      const outfits = await invoke("list_pack_assets", { packId });
      if (token !== portraitState.portraitRequestToken || !isPortraitTarget(packId, visit)) return false;
      portraitState.portraitLoadedPackId = packId;
      view.testAssetsByPack[packId] = Array.isArray(outfits) ? outfits : [];
      renderPortraitsView(packId, outfits);
      const outfit = view.testAssetsByPack[packId].find((item) => item.id === selected?.outfitId);
      const emotion = outfit?.emotions?.find((item) => item.id === selected?.emotion?.id);
      if (emotion) previewEmotion(packId, outfit.id, emotion);
      updatePortraitControls();
      return true;
    } catch (error) {
      if (token !== portraitState.portraitRequestToken || !isPortraitTarget(packId, visit)) return false;
      setStatus(`读取立绘失败：${formatError(error)}`);
      setPortraitStatus(`读取失败：${formatError(error)}`, true);
      showPortraitsEmpty();
      return false;
    }
  }

  function showPortraitsEmpty() {
    portraitState.portraitLoadedPackId = "";
    clearPortraitPreview();
    updatePortraitControls();
    if (els.portraitsContent) els.portraitsContent.hidden = true;
    if (els.portraitsEmptyState) els.portraitsEmptyState.hidden = false;
  }

  function isPortraitTarget(packId, visit = portraitState.portraitVisitToken) {
    return view.activeTab === "portraits" && view.workspacePackId === packId
      && visit === portraitState.portraitVisitToken && Boolean(findPack(packId));
  }

  function clearPortraitPreview() {
    portraitState.previewingEmotion = null;
    els.emotionPreview?.replaceChildren(buildText("p", "选择一张图片，查看预览和可用操作。"));
    renderPortraitCutoutStatus();
  }

  function invalidatePortraits() {
    ++portraitState.portraitVisitToken;
    ++portraitState.portraitRequestToken;
    portraitState.portraitLoadedPackId = "";
    clearPortraitPreview();
    updatePortraitControls();
  }

  function updatePortraitControls() {
    const enabled = Boolean(portraitState.portraitLoadedPackId && isPortraitTarget(portraitState.portraitLoadedPackId)
      && !isPackWriteBusy(portraitState.portraitLoadedPackId));
    for (const control of [els.newOutfitId, els.addOutfitBtn,
      ...els.outfitsContainer.querySelectorAll("button"), ...els.emotionPreview.querySelectorAll("button")]) {
      control.disabled = !enabled;
    }
    renderPortraitCutoutStatus();
  }

  async function runPortraitMutation(packId, label, action) {
    if (!isTauriRuntime || !isPortraitTarget(packId) || portraitState.portraitLoadedPackId !== packId) {
      return { ok: false, reason: "stale-target" };
    }
    if (isPackWriteBusy(packId)) return { ok: false, reason: "operation-in-progress" };
    const visit = portraitState.portraitVisitToken;
    const current = () => isPortraitTarget(packId, visit);
    const report = (message, error = false) => {
      if (!current()) return;
      setStatus(message);
      setPortraitStatus(message, error);
    };
    writeState.portraitMutations.add(packId);
    ++writeState.registryRevision;
    updateCatalogControls();
    report(`${label}…`);
    try {
      const result = await action({ current, report });
      if (result?.cancelled) return { ok: false, reason: "cancelled" };
      clearPortraitImageCache(packId);
      delete view.testAssetsByPack[packId];
      if (current()) {
        const refreshed = await loadPortraitsTab(packId);
        if (refreshed) report(result?.message || `${label}完成。`, Boolean(result?.error));
        else report(`${label}已完成，但列表刷新失败。请重新进入立绘管理。`, true);
      }
      return { ok: !result?.error, ...result };
    } catch (error) {
      report(`${label}失败：${formatError(error)}`, true);
      return { ok: false, reason: "operation-failed", error: formatError(error) };
    } finally {
      writeState.portraitMutations.delete(packId);
      ++writeState.registryRevision;
      updateCatalogControls();
    }
  }

  function renderPortraitsView(packId, outfits) {
    if (els.portraitsContent) els.portraitsContent.hidden = false;
    if (els.portraitsEmptyState) els.portraitsEmptyState.hidden = true;
    if (!els.outfitsContainer) return;

    const items = Array.isArray(outfits) ? outfits : [];

    /* get pack profile for default emotion / music emotion info */
    const pack = findPack(packId);
    const profile = pack?._rawProfile || {};

    els.outfitsContainer.replaceChildren();
    renderPortraitCutoutStatus();

    /* render warnings section */
    const warnings = buildMissingEmotionWarnings(items, profile);
    if (warnings) {
      els.outfitsContainer.appendChild(warnings);
    }

    if (!items.length) {
      els.outfitsContainer.appendChild(buildNoOutfitsState(packId, profile));
      els.addOutfitBtn.onclick = () => createOutfit(packId);
      setStatus("还没有立绘资源。");
      setPortraitStatus("还没有服装。请先在上方输入服装名并创建。");
      return;
    }

    /* render outfit cards */
    for (const outfit of items) {
      els.outfitsContainer.appendChild(buildOutfitCard(packId, outfit, profile));
    }

    /* bind add-outfit button */
    els.addOutfitBtn.onclick = () => createOutfit(packId);

    const imageCount = items.reduce((total, outfit) => total + (outfit.emotions?.length || 0), 0);
    setStatus(`已加载 ${items.length} 套服装、${imageCount} 张图片。`);
    setPortraitStatus(`角色包中现有 ${items.length} 套服装、${imageCount} 张已导入图片。`);
  }

  function buildMissingEmotionWarnings(outfits, profile) {
    const appearance = profile.appearance || {};
    const defaultOutfit = String(appearance.default_outfit || appearance.defaultOutfit || "").trim();
    const defaultEmotion = String(appearance.default_emotion || appearance.defaultEmotion || "").trim();

    const outfitIds = new Set();
    const availableByOutfit = new Map();
    for (const outfit of outfits) {
      const outfitId = String(outfit?.id || "").trim();
      if (outfitId) {
        outfitIds.add(outfitId);
        availableByOutfit.set(outfitId, new Set());
      }
      for (const em of (outfit.emotions || [])) {
        const emotionId = String(em?.id || "").trim();
        if (!emotionId) continue;
        if (outfitId) availableByOutfit.get(outfitId)?.add(emotionId);
      }
    }

    const defaultOutfitMissing = Boolean(defaultOutfit && outfits.length && !outfitIds.has(defaultOutfit));
    const defaultPortraitMissing = Boolean(
      defaultEmotion &&
        (!outfits.length ||
          (defaultOutfit
            ? !availableByOutfit.get(defaultOutfit)?.has(defaultEmotion)
            : true))
    );

    if (!defaultOutfitMissing && !defaultPortraitMissing) return null;

    const box = document.createElement("div");
    box.className = "missing-emotions-warning glass-card";

    const value = defaultOutfit && defaultEmotion ? `${defaultOutfit} / ${defaultEmotion}` : "尚未设置";
    box.appendChild(buildWarningLine("warning-required", "当前默认立绘无效：", value));
    const guidance = document.createElement("p");
    guidance.className = "warning-guidance";
    guidance.textContent = "这不是待导入清单。请点击下方任一已有表情，再在右侧选择“设为默认立绘”。";
    box.appendChild(guidance);

    return box;
  }

  function buildWarningLine(className, label, value) {
    const line = document.createElement("p");
    line.className = `warning-line ${className}`;
    const strong = document.createElement("strong");
    strong.textContent = label;
    line.append(strong, document.createTextNode(value));
    return line;
  }

  function buildNoOutfitsState(packId, profile) {
    const state = document.createElement("div");
    state.className = "asset-empty-state glass-card";
    const title = document.createElement("strong");
    title.textContent = "还没有服装";
    const note = document.createElement("p");
    note.textContent = "在上方输入服装名并点击“创建服装”。创建后，再向该服装导入表情图片。";
    state.append(title, note);
    return state;
  }

  function buildOutfitCard(packId, outfit, packProfile) {
    const card = document.createElement("div");
    card.className = "outfit-card glass-card";

    const header = document.createElement("div");
    header.className = "outfit-card-header";

    const titleGroup = document.createElement("div");
    titleGroup.className = "outfit-title-group";

    const title = document.createElement("h3");
    const emotions = Array.isArray(outfit.emotions) ? outfit.emotions : [];
    title.textContent = `${outfit.name || outfit.id}`;
    title.title = "双击重命名服装";

    title.addEventListener("dblclick", () => renameOutfitDialog(packId, outfit.id));

    titleGroup.append(title);
    const count = document.createElement("span");
    count.className = "outfit-image-count";
    count.textContent = `${emotions.length} 张已导入`;
    titleGroup.append(count);

    /* default outfit badge */
    const defaultOutfit = packProfile?.appearance?.default_outfit || packProfile?.appearance?.defaultOutfit || "";
    if (outfit.id === defaultOutfit) {
      const badge = document.createElement("span");
      badge.className = "default-badge";
      badge.textContent = "默认";
      titleGroup.append(badge);
    }

    const actions = document.createElement("div");
    actions.className = "outfit-actions";

    const addBtn = document.createElement("button");
    addBtn.type = "button";
    addBtn.className = "btn-add-line";
    addBtn.textContent = "导入图片";
    addBtn.addEventListener("click", () => uploadEmotionImages(packId, outfit.id, {
      existingEmotionIds: emotions.map((emotion) => emotion.id),
    }));

    actions.append(addBtn);
    header.append(titleGroup, actions);

    const grid = document.createElement("div");
    grid.className = "emotion-grid";

    const defaultEmotion = packProfile?.appearance?.default_emotion || packProfile?.appearance?.defaultEmotion || "";
    const musicEmotion = packProfile?.appearance?.music_emotion || packProfile?.appearance?.musicEmotion || "";

    if (emotions.length) {
      grid.replaceChildren(
        ...emotions.map((em) => buildEmotionTile(packId, outfit.id, em, {
          defaultEmotion,
          defaultOutfit,
          musicEmotion,
        }))
      );
    } else {
      grid.appendChild(buildNoEmotionsState(packId, outfit.id));
    }

    card.append(header, grid);
    return card;
  }

  function buildNoEmotionsState(packId, outfitId) {
    const state = document.createElement("div");
    state.className = "emotion-empty-state";
    const text = document.createElement("span");
    text.textContent = "服装已创建，目前没有图片。";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn-secondary";
    button.textContent = "选择图片";
    button.addEventListener("click", () => uploadEmotionImages(packId, outfitId));
    state.append(text, button);
    return state;
  }

  function buildEmotionTile(packId, outfitId, emotion, flags = {}) {
    const tile = document.createElement("div");
    tile.className = "emotion-tile";
    tile.title = `${emotion.name || emotion.id} — 点击预览 | 右键删除 | 双击重命名`;

    const img = document.createElement("img");
    img.alt = emotion.name || emotion.id;
    img.loading = "lazy";
    tile.append(img);
    void loadPortraitImage(img, packId, outfitId, emotion);

    const labelRow = document.createElement("div");
    labelRow.className = "emotion-label-row";

    const label = document.createElement("span");
    label.textContent = emotion.name || emotion.id;

    labelRow.append(label);

    /* default / music badges */
    if (outfitId === flags.defaultOutfit && emotion.id === flags.defaultEmotion) {
      const defBadge = document.createElement("span");
      defBadge.className = "emotion-flag";
      defBadge.textContent = "默认";
      labelRow.append(defBadge);
    }
    if (emotion.id === flags.musicEmotion) {
      const musicBadge = document.createElement("span");
      musicBadge.className = "emotion-flag music-flag";
      musicBadge.textContent = "听歌";
      labelRow.append(musicBadge);
    }

    tile.append(labelRow);

    tile.addEventListener("click", () => previewEmotion(packId, outfitId, emotion));
    tile.addEventListener("dblclick", () => renameEmotionDialog(packId, outfitId, emotion.id));
    tile.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      deleteEmotionConfirm(packId, outfitId, emotion.id);
    });

    return tile;
  }

  function previewEmotion(packId, outfitId, emotion) {
    if (!els.emotionPreview || !isPortraitTarget(packId) || portraitState.portraitLoadedPackId !== packId) return;
    portraitState.previewingEmotion = { packId, outfitId, emotion };
    els.emotionPreview.replaceChildren();

    const previewAsset = document.createElement("img");
    previewAsset.alt = emotion.name || emotion.id;
    previewAsset.className = "preview-image";
    void loadPortraitImage(previewAsset, packId, outfitId, emotion, "preview-image-load-error");

    const info = document.createElement("p");
    info.className = "preview-info";
    info.textContent = `${emotion.name || emotion.id} — ${formatFileSize(emotion.sizeBytes || 0)}`;

    const actions = document.createElement("div");
    actions.className = "preview-actions";

    const setDefaultBtn = document.createElement("button");
    setDefaultBtn.type = "button";
    setDefaultBtn.className = "btn-primary";
    setDefaultBtn.textContent = "设为默认立绘";
    setDefaultBtn.addEventListener("click", () => setDefaultPortrait(packId, outfitId, emotion.id));

    const setMusicBtn = document.createElement("button");
    setMusicBtn.type = "button";
    setMusicBtn.className = "btn-secondary";
    setMusicBtn.textContent = "🎵 设为听歌表情";
    setMusicBtn.style.cssText = "font-size:12px;padding:5px 10px;";
    setMusicBtn.addEventListener("click", () => setAppearanceField(packId, "music_emotion", emotion.id));

    actions.append(setDefaultBtn, setMusicBtn);

    els.emotionPreview.append(previewAsset, info, actions);
    updatePortraitControls();
    flashElement(els.emotionPreview, "preview-flash");
  }

  async function loadPortraitImage(img, packId, outfitId, emotion, errorClassName = "image-load-error") {
    try {
      const url = await getPortraitImageUrl(packId, outfitId, emotion);
      if (!img.isConnected) return;
      img.src = url;
    } catch (error) {
      if (!img.isConnected) return;
      img.replaceWith(buildImageLoadError(`图片读取失败\n${formatError(error)}`, errorClassName));
    }
  }

  function setPortraitStatus(message, isError = false) {
    if (!els.portraitImportStatus) return;
    els.portraitImportStatus.textContent = String(message || "");
    els.portraitImportStatus.classList.toggle("error", Boolean(isError));
  }

  function updatePackAppearance(packId, fields) {
    const pack = findPack(packId);
    if (!pack) return;
    pack._rawProfile ||= {};
    pack._rawProfile.appearance = { ...pack._rawProfile.appearance, ...fields };
    if (fields.default_outfit !== undefined) pack.defaultOutfit = fields.default_outfit;
    if (fields.default_emotion !== undefined) pack.defaultEmotion = fields.default_emotion;
  }

  async function setDefaultPortrait(packId, outfitId, emotionId) {
    return runPortraitMutation(packId, "设置默认立绘", async () => {
      await invoke("set_default_portrait", { packId, outfit: outfitId, emotion: emotionId });
      updatePackAppearance(packId, { default_outfit: outfitId, default_emotion: emotionId });
      return { message: `默认立绘已设为：${outfitId} / ${emotionId}` };
    });
  }

  async function setAppearanceField(packId, field, value) {
    return runPortraitMutation(packId, "更新表情设置", async () => {
      await invoke("set_default_emotion", { packId, field, value });
      updatePackAppearance(packId, { [field]: value });
      return { message: `已更新：${field === "music_emotion" ? "听歌表情" : field} → ${value}` };
    });
  }

  async function deleteEmotionConfirm(packId, outfitId, emotionId) {
    if (!isPortraitTarget(packId) || writeState.portraitMutations.has(packId)) return;
    if (!confirm(`确定删除表情 "${emotionId}"？此操作不可恢复。`)) return;
    return runPortraitMutation(packId, "删除表情", async () => {
      await invoke("delete_portrait_image", { packId, outfit: outfitId, emotion: emotionId });
      return { message: `已删除表情：${emotionId}` };
    });
  }

  async function renameEmotionDialog(packId, outfitId, oldId) {
    if (!isPortraitTarget(packId) || writeState.portraitMutations.has(packId)) return;
    const newId = prompt(`重命名表情 "${oldId}" 为：`, oldId);
    if (!newId || !newId.trim() || newId.trim() === oldId) return;
    return runPortraitMutation(packId, "重命名表情", async () => {
      await invoke("rename_portrait_emotion", { packId, outfit: outfitId, oldEmotion: oldId, newEmotion: newId.trim() });
      return { message: `已重命名：${newId.trim()}` };
    });
  }

  async function renameOutfitDialog(packId, oldId) {
    if (!isPortraitTarget(packId) || writeState.portraitMutations.has(packId)) return;
    const newId = prompt(`重命名服装 "${oldId}" 为：`, oldId);
    if (!newId || !newId.trim() || newId.trim() === oldId) return;
    return runPortraitMutation(packId, "重命名服装", async () => {
      await invoke("rename_portrait_outfit", { packId, oldOutfit: oldId, newOutfit: newId.trim() });
      return { message: `已重命名服装：${newId.trim()}` };
    });
  }

  async function uploadEmotionImages(packId, outfitId, options = {}) {
    return runPortraitMutation(packId, "导入表情图", async ({ current, report }) => {
      report(`请选择要导入到“${outfitId}”的图片。`);
      const files = await chooseLocalFiles({ accept: "image/png,image/jpeg,image/webp", multiple: true });
      if (!files.length || !current()) {
        report(`未导入图片，“${outfitId}”没有发生变化。`);
        return { cancelled: true };
      }
      const existingEmotionIds = new Set(options.existingEmotionIds || []);
      const fallbackEmotion = String(options.defaultEmotion || "normal").trim() || "normal";
      let successCount = 0;
      let overwriteCount = 0;
      const failures = [];
      report(`正在导入 ${files.length} 张图片到“${outfitId}”…`);
      for (const [index, file] of files.entries()) {
        const emotion = inferEmotionNameFromFileName(file.name, fallbackEmotion);
        try {
          if (Number(file.size || 0) > MAX_PORTRAIT_IMAGE_BYTES) throw new Error("图片大小不能超过 20 MB。");
          await yieldToUiForLargeFileRead();
          const bytes = new Uint8Array(await file.arrayBuffer());
          await invoke("upload_portrait_image", { packId, outfit: outfitId, emotion, imageBytes: Array.from(bytes) });
          if (existingEmotionIds.has(emotion)) overwriteCount += 1;
          existingEmotionIds.add(emotion);
          successCount += 1;
        } catch (error) {
          failures.push(`${file.name}: ${formatError(error)}`);
        }
        report(`已处理 ${index + 1} / ${files.length} 张：成功 ${successCount}，失败 ${failures.length}。`, Boolean(failures.length));
      }
      const message = failures.length
        ? `导入完成：成功 ${successCount} 张，覆盖 ${overwriteCount} 张，失败 ${failures.length} 张。${failures.slice(0, 2).join("；")}`
        : `已导入 ${successCount} 张图片到“${outfitId}”${overwriteCount ? `，其中覆盖 ${overwriteCount} 张` : ""}。`;
      return { message, error: failures.length > 0 };
    });
  }

  async function createOutfit(packId) {
    const outfit = String(els.newOutfitId?.value || "").trim();
    if (!outfit) {
      if (!isPortraitTarget(packId)) return;
      setStatus("请先填写新服装名。");
      setPortraitStatus("请先填写新服装名。", true);
      els.newOutfitId?.focus();
      flashElement(els.newOutfitId, "invalid-flash");
      return;
    }
    return runPortraitMutation(packId, "创建服装", async ({ current }) => {
      await invoke("create_portrait_outfit", { packId, outfit });
      if (current() && els.newOutfitId.value.trim() === outfit) els.newOutfitId.value = "";
      return { message: `已创建服装“${outfit}”。现在可以在它的卡片中导入图片。` };
    });
  }

  return { loadPortraitsTab, showPortraitsEmpty, isPortraitTarget, invalidatePortraits, updatePortraitControls, runPortraitMutation, setPortraitStatus, createOutfit, setDefaultPortrait, previewEmotion };
}
