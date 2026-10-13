import { normalizePacks, hasProfilePayload, getPackName, mergeSnapshotPacks } from "./pack-model.js";
import { bindInstanceStorage } from "../instance-storage.js";
import { normalizeBackendUrl, formatError, stableSignature } from "./shared.js";
import { chooseLocalFiles, yieldToUiForLargeFileRead } from "./file-input.js";
import { MAX_CHARACTER_PACK_ZIP_BYTES } from "./constants.js";
import { flashElement } from "./dom.js";

export function createCatalogController({
  view,
  isTauriRuntime,
  isRegistryBusy,
  setStatus,
  registryState,
  writeState,
  navigationState,
  invoke,
  autoSaveDraftSync,
  ensureWorkspacePack,
  findPack,
  refreshPortraitCutoutCapability,
  render,
  loadPortraitsTab,
  showPortraitsEmpty,
  calibrationState,
  loadCalibrationTab,
  isPackWriteBusy,
  updateCatalogControls,
  renderPackList,
  emitCharacterPackActivated,
  pulsePackCard,
  els,
  switchTab,
}) {
  function acceptPackResult(result, expectedId = "") {
    const pack = normalizePacks([result])[0];
    if (!pack || !hasProfilePayload(result?.profile) || (expectedId && pack.id !== expectedId)) {
      throw new Error("角色包操作返回了无效结果，请刷新列表确认文件状态。");
    }
    const index = view.packs.findIndex(item => item.id === pack.id);
    if (index >= 0) view.packs[index] = pack;
    else view.packs.push(pack);
    if (view.editingPackId === pack.id) view.editingProfile = result.profile;
    return pack;
  }

  async function refreshPacks() {
    if (!isTauriRuntime) return { ok: false, reason: "unavailable" };
    if (isRegistryBusy()) {
      setStatus("角色包操作尚未完成，请完成后再刷新。");
      return { ok: false, reason: "operation-in-progress" };
    }
    const token = ++registryState.refreshRequestToken;
    const revision = writeState.registryRevision;
    const stateRevision = registryState.snapshotRevision;
    const visit = navigationState.workspaceVisit;
    const current = () => token === registryState.refreshRequestToken && revision === writeState.registryRevision;
    setStatus("正在刷新角色包。");
    try {
      const [packs, persistedState, launchBinding] = await Promise.all([
        invoke("list_character_packs"),
        invoke("load_pet_state"),
        invoke("get_client_launch_binding"),
      ]);
      if (!current()) return { ok: false, reason: "superseded" };
      const petState = {
        ...persistedState,
        backendUrl: launchBinding?.hasBackendOverride
          ? launchBinding.backendUrl
          : persistedState?.backendUrl
      };
      if (String(launchBinding?.instanceId || "") !== String(petState.instanceId || "")
        || (view.instanceId && view.instanceId !== String(petState.instanceId || ""))) {
        throw new Error("桌面客户端实例绑定与状态文件不一致。");
      }
      view.instanceId = String(petState?.instanceId || "").trim();
      bindInstanceStorage(view.instanceId);
      if (view.draftDirty && view.editingPackId && !autoSaveDraftSync(view.editingPackId)) {
        return { ok: false, reason: "draft-storage-unavailable" };
      }
      view.packs = normalizePacks(packs);
      if (stateRevision === registryState.snapshotRevision) {
        view.activePackId = String(petState?.characterPackId || view.activePackId || "").trim();
        view.desktopScale = Number(petState?.scale) || 1;
        view.activeSessionId = String(petState?.sessionId || view.activeSessionId || "").trim();
        view.backendUrl = normalizeBackendUrl(petState?.backendUrl || view.backendUrl);
        view.profileUserId = String(petState?.profileUserId || view.profileUserId || "master").trim() || "master";
      }
      ensureWorkspacePack();
      view.activeCharacterName = getPackName(findPack(view.activePackId)) || view.activeCharacterName;
      void refreshPortraitCutoutCapability({ force: true });
      render();
      if (visit === navigationState.workspaceVisit) setStatus("角色包已刷新。");
      // Retry failed asset views while preserving a ready calibration's unsaved values.
      if (view.activeTab === "portraits") {
        if (view.workspacePackId) await loadPortraitsTab(view.workspacePackId);
        else showPortraitsEmpty();
      }
      if (view.activeTab === "calibration" && !calibrationState.calibrationReadyTarget) await loadCalibrationTab(view.workspacePackId);
      return { ok: true };
    } catch (error) {
      if (current() && visit === navigationState.workspaceVisit) setStatus(`刷新失败：${formatError(error)}`);
      return { ok: false, reason: "refresh-failed", error: formatError(error) };
    }
  }

  function applySnapshot(snapshot) {
    if (!snapshot || typeof snapshot !== "object") return;
    const state = snapshot.state || {};
    const character = snapshot.character || {};
    const snapshotInstanceId = String(state.instanceId || "").trim();
    if (view.instanceId && snapshotInstanceId && snapshotInstanceId !== view.instanceId) {
      setStatus(`已忽略来自实例 ${snapshotInstanceId} 的旧窗口状态。`);
      return;
    }
    if (snapshotInstanceId && !view.instanceId) {
      view.instanceId = snapshotInstanceId;
      bindInstanceStorage(snapshotInstanceId);
    }
    const signature = buildWorkshopSnapshotSignature(state, character);
    if (signature === registryState.lastWorkshopSnapshotSignature) {
      return;
    }
    registryState.lastWorkshopSnapshotSignature = signature;
    ++registryState.snapshotRevision;
    view.activePackId = String(state.characterPackId || character.packId || view.activePackId || "").trim();
    view.desktopScale = Number(state.scale) || view.desktopScale;
    view.activeSessionId = String(state.sessionId || view.activeSessionId || "").trim();
    view.backendUrl = normalizeBackendUrl(state.backendUrl || view.backendUrl);
    view.profileUserId = String(state.profileUserId || view.profileUserId || "master").trim() || "master";
    const available = Array.isArray(character.availablePacks) ? character.availablePacks : [];
    if (available.length) {
      view.packs = mergeSnapshotPacks(view.packs, normalizePacks(available));
    }
    const canonicalActivePack = findPack(view.activePackId);
    view.activeCharacterName = getPackName(canonicalActivePack) ||
      (String(character.packId || "").trim() === view.activePackId
        ? String(character.appName || character.name || "").trim()
        : view.activeCharacterName);
    ensureWorkspacePack();
    void refreshPortraitCutoutCapability({ silent: true });
    render();
  }

  function buildWorkshopSnapshotSignature(state, character) {
    const packs = Array.isArray(character.availablePacks) ? character.availablePacks : [];
    return stableSignature({
      instanceId: String(state.instanceId || view.instanceId || "").trim(),
      activePackId: String(state.characterPackId || character.packId || "").trim(),
      scale: Number(state.scale) || view.desktopScale,
      sessionId: String(state.sessionId || "").trim(),
      backendUrl: normalizeBackendUrl(state.backendUrl || view.backendUrl),
      profileUserId: String(state.profileUserId || view.profileUserId || "master").trim() || "master",
      packs: packs.map((pack) => ({
        id: String(pack?.id || pack?.packId || "").trim(),
        name: String(pack?.name || pack?.characterName || pack?.appName || "").trim(),
        selected: Boolean(pack?.selected),
        schemaVersion: String(pack?.schemaVersion || "").trim(),
        assetCount: Number(pack?.assetCount || pack?.asset_count || 0) || 0,
        defaultOutfit: String(pack?.defaultOutfit || pack?.default_outfit || "").trim(),
        defaultEmotion: String(pack?.defaultEmotion || pack?.default_emotion || "").trim(),
      })),
    });
  }

  async function applyPack(packId) {
    const pack = findPack(packId);
    if (!pack) {
      setStatus(`应用失败：角色包 ${packId || "未知"} 不存在。`);
      return { ok: false, packId, reason: "missing-pack" };
    }
    if (isPackWriteBusy(packId)) {
      setStatus("当前角色包正在保存，请完成后再应用到桌宠。");
      return { ok: false, packId, reason: "save-in-progress" };
    }
    if (view.pendingApplyPackId) {
      setStatus(`正在等待 ${getPackName(findPack(view.pendingApplyPackId)) || view.pendingApplyPackId} 完成切换。`);
      return { ok: false, packId, reason: "apply-pending" };
    }
    setStatus(`正在应用：${getPackName(pack) || packId}`);
    view.pendingApplyPackId = packId;
    ++writeState.registryRevision;
    updateCatalogControls();
    renderPackList();
    try {
      const result = await invoke("activate_character_pack", { packId });
      const activePackId = String(result?.packId || "").trim();
      if (activePackId !== packId) {
        throw new Error(`桌面端返回的角色不一致：请求 ${packId}，实际 ${activePackId || "未知"}`);
      }
      await emitCharacterPackActivated(activePackId);
      view.activePackId = activePackId;
      view.activeCharacterName = getPackName(findPack(view.activePackId)) || view.activeCharacterName;
      view.pendingApplyPackId = "";
      render();
      pulsePackCard(packId);
      setStatus(`已应用到桌宠：${getPackName(pack) || packId}`);
      return { ok: true, packId };
    } catch (error) {
      view.pendingApplyPackId = "";
      renderPackList();
      const message = formatError(error);
      setStatus(`应用失败：${message}`);
      return { ok: false, packId, error: message };
    } finally {
      ++writeState.registryRevision;
      updateCatalogControls();
    }
  }

  async function importPack() {
    if (!isTauriRuntime) { setStatus("浏览器预览模式不支持导入。"); return; }
    if (isRegistryBusy()) { setStatus("请等待角色包操作完成后再导入。"); return; }
    writeState.catalogOperation = "import";
    ++writeState.registryRevision;
    updateCatalogControls();
    let importedName = "";
    try {
      const [file] = await chooseLocalFiles({ accept: ".zip" });
      if (!file) return;
      if (Number(file.size || 0) > MAX_CHARACTER_PACK_ZIP_BYTES) {
        setStatus("角色包 zip 暂时请控制在 300 MB 以内。");
        return;
      }
      setStatus(`正在导入：${file.name}…`);
      await yieldToUiForLargeFileRead();
      const bytes = new Uint8Array(await file.arrayBuffer());
      const result = await invoke("install_character_pack_zip_bytes", {
        fileName: file.name,
        bytes: Array.from(bytes),
        overwrite: false,
      });
      if (!result?.packId) throw new Error("导入返回了无效结果，请刷新列表确认文件状态。");
      importedName = result.characterName || result.packId;
    } catch (error) {
      setStatus(`导入失败：${formatError(error)}`);
    } finally {
      writeState.catalogOperation = "";
      ++writeState.registryRevision;
      updateCatalogControls();
    }
    if (importedName) {
      const refreshed = await refreshPacks();
      setStatus(`已导入：${importedName}。${refreshed.ok ? "" : "列表未刷新，请稍后重试刷新。"}`);
    }
  }

  async function exportPack() {
    const packId = view.workspacePackId || view.activePackId;
    if (!packId) { setStatus("请先选择一个角色包。"); return; }
    if (!isTauriRuntime) { setStatus("浏览器预览模式不支持导出。"); return; }
    if (isPackWriteBusy(packId) || writeState.packExports.has(packId)) {
      setStatus("请等待当前角色包操作完成后再导出。"); return;
    }
    writeState.packExports.add(packId);
    updateCatalogControls();
    setStatus(`正在导出：${packId}…`);
    try {
      const result = await invoke("export_character_pack", { packId });
      if (!result?.ok || !result.fileName) throw new Error("导出返回了无效结果，请检查桌面文件。");
      const fileName = result?.fileName || `${packId}.zip`;
      setStatus(`已导出到桌面：${fileName}`);
      /* try to open the containing folder */
      if (result?.path) {
        try { await invoke("show_item_in_folder", { path: result.path }); }
        catch (error) { setStatus(`已导出 ${fileName}，但无法打开所在目录：${formatError(error)}`); }
      }
    } catch (error) {
      setStatus(`导出失败：${formatError(error)}`);
    } finally {
      writeState.packExports.delete(packId);
      updateCatalogControls();
    }
  }

  async function openPacksFolder() {
    try {
      await invoke("open_character_packs_folder");
      setStatus("已打开角色包目录。");
    } catch (error) {
      setStatus(`打开失败：${formatError(error)}`);
    }
  }

  function openCreateDialog() {
    if (!isTauriRuntime) {
      setStatus("浏览器预览模式不支持创建角色包。请在 Tauri 环境中使用。");
      return;
    }
    ++registryState.createDialogVisit;
    els.createPackId.value = "";
    els.createName.value = "";
    els.createAppName.value = "";
    els.createUserTitle.value = "";
    els.createError.hidden = true;
    els.createError.textContent = "";
    updateCatalogControls();
    els.createDialog.showModal();
  }

  function showCreateError(message) {
    els.createError.textContent = String(message || "");
    els.createError.hidden = !message;
    if (message) {
      flashElement(els.createForm, "invalid-flash");
    }
  }

  async function createCharacterPack() {
    if (!els.createDialog.open) return;
    if (isRegistryBusy()) { showCreateError("请等待角色包操作完成后再创建。"); return; }
    const dialogVisit = registryState.createDialogVisit;
    const visit = navigationState.workspaceVisit;
    const ownsDialog = () => dialogVisit === registryState.createDialogVisit && els.createDialog.open;
    const packId = (els.createPackId.value || "").trim();
    const name = (els.createName.value || "").trim();
    const appName = (els.createAppName.value || "").trim();
    const userTitle = (els.createUserTitle.value || "").trim();

    if (!packId) {
      showCreateError("请输入保存名。");
      return;
    }
    if (!name) {
      showCreateError("请输入角色名称。");
      return;
    }

    setStatus(`正在创建角色包：${name}…`);
    writeState.catalogOperation = "create";
    ++writeState.registryRevision;
    updateCatalogControls();
    try {
      const result = await invoke("create_character_pack", {
        request: {
          packId,
          name,
          appName,
          userTitle,
        },
      });
      const pack = acceptPackResult(result);
      const reportHere = ownsDialog() && visit === navigationState.workspaceVisit;
      if (ownsDialog()) {
        els.createDialog.close();
        if (visit === navigationState.workspaceVisit) switchTab("persona", pack.id);
      }
      render();
      if (reportHere) setStatus(`已创建角色包：${name}（${pack.id}）。`);
    } catch (error) {
      if (ownsDialog()) showCreateError(formatError(error));
      if (visit === navigationState.workspaceVisit) setStatus(`创建失败：${formatError(error)}`);
    } finally {
      writeState.catalogOperation = "";
      ++writeState.registryRevision;
      updateCatalogControls();
    }
  }

  return { acceptPackResult, refreshPacks, applySnapshot, applyPack, importPack, exportPack, openPacksFolder, openCreateDialog, createCharacterPack };
}
