

export function createWorkshopSelection({
  view,
  autoSaveDraftSync,
  navigationState,
  invalidateCalibration,
  invalidatePortraits,
  cancelTestRequest,
}) {
  function findPack(packId) {
    const id = String(packId || "").trim();
    return view.packs.find((pack) => pack.id === id) || null;
  }

  function setWorkspacePack(packId) {
    const id = String(packId || "").trim();
    if (!id || !findPack(id)) return false;
    if (id !== view.editingPackId && view.editingPackId && view.draftDirty
      && !autoSaveDraftSync(view.editingPackId)) return false;
    if (view.workspacePackId !== id) {
      ++navigationState.workspaceVisit;
      invalidateCalibration();
      invalidatePortraits();
      cancelTestRequest();
    }
    view.workspacePackId = id;
    return true;
  }

  function ensureWorkspacePack() {
    if (findPack(view.workspacePackId)) return view.workspacePackId;
    const fallbackId = findPack(view.activePackId)?.id || view.packs[0]?.id || "";
    if (view.workspacePackId !== fallbackId) {
      ++navigationState.workspaceVisit;
      invalidateCalibration();
      invalidatePortraits();
      cancelTestRequest();
    }
    view.workspacePackId = fallbackId;
    return fallbackId;
  }

  return { findPack, setWorkspacePack, ensureWorkspacePack };
}
