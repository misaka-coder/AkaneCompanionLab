

export function createCatalogControls({
  els,
  writeState,
  contextState,
  storiesState,
  isPackWriteBusy,
  updateCalibrationControls,
  updatePortraitControls,
}) {
  function updateCatalogControls() {
    for (const control of [els.createPackBtn, els.firstUseCreate, els.importPackBtn, els.firstUseImport]) {
      control.disabled = Boolean(writeState.catalogOperation);
    }
    for (const control of els.createForm.querySelectorAll('input, button[type="submit"]')) {
      control.disabled = Boolean(writeState.catalogOperation);
    }
    els.createForm.setAttribute("aria-busy", String(Boolean(writeState.catalogOperation)));
    els.createForm.querySelector('button[type="submit"]').textContent = writeState.catalogOperation ? "正在处理…" : "创建";
    els.createCancel.textContent = writeState.catalogOperation ? "关闭" : "取消";
    const contextBusy = Boolean(contextState.contextDialogTarget && isPackWriteBusy(contextState.contextDialogTarget.packId));
    for (const control of els.contextLibraryForm.querySelectorAll('input, textarea, button[type="submit"]')) {
      control.disabled = contextBusy;
    }
    els.contextLibraryForm.setAttribute("aria-busy", String(contextBusy));
    els.contextLibraryForm.querySelector('button[type="submit"]').textContent = contextBusy ? "正在处理…" : "创建资料库";
    els.contextLibraryCancel.textContent = contextBusy ? "关闭" : "取消";
    const storyBusy = Boolean(storiesState.storyDialogTarget && isPackWriteBusy(storiesState.storyDialogTarget.packId));
    for (const control of els.storyForm.querySelectorAll('input, button[type="submit"]')) {
      control.disabled = storyBusy;
    }
    els.storyForm.setAttribute("aria-busy", String(storyBusy));
    els.storyForm.querySelector('button[type="submit"]').textContent = storyBusy ? "正在处理…" : "创建";
    els.storyDialogCancel.textContent = storyBusy ? "关闭" : "取消";
    updateCalibrationControls();
    updatePortraitControls();
  }

  return { updateCatalogControls };
}
