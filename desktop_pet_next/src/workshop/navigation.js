export function createWorkshopNavigation({
  setWorkspacePack,
  invalidateCalibration,
  invalidatePortraits,
  navigationState,
  view,
  els,
  startEditingPack,
  loadContextLibrariesTab,
  loadStoriesTab,
  showStoriesEmpty,
  loadPortraitsTab,
  showPortraitsEmpty,
  loadCalibrationTab,
  showCalibrationEmpty,
  loadTestChatTab,
  showTestChatEmpty,
}) {
  function switchTab(tab, packId) {
    const explicitTargetId = String(packId || "").trim();
    if (tab !== "list" && explicitTargetId) {
      if (!setWorkspacePack(explicitTargetId)) return;
    }
    invalidateCalibration();
    invalidatePortraits();
    ++navigationState.workspaceVisit;
    view.activeTab = tab;

    for (const btn of els.tabButtons) {
      const isSelected = btn.dataset.tab === tab;
      btn.setAttribute("aria-selected", String(isSelected));
    }
    for (const panel of els.tabPanels) {
      panel.hidden = panel.dataset.tabPanel !== tab;
    }

    if (tab === "persona") {
      const targetId = String(view.workspacePackId || view.activePackId || "").trim();
      if (targetId) {
        startEditingPack(targetId);
      }
    }
    if (tab === "context") {
      const targetId = String(view.workspacePackId || view.activePackId || "").trim();
      loadContextLibrariesTab(targetId);
    }
    if (tab === "stories") {
      const targetId = String(view.workspacePackId || view.activePackId || "").trim();
      if (targetId) {
        void loadStoriesTab(targetId);
      } else {
        showStoriesEmpty();
      }
    }
    if (tab === "portraits") {
      const targetId = String(view.workspacePackId || view.activePackId || "").trim();
      if (targetId) {
        void loadPortraitsTab(targetId);
      } else {
        showPortraitsEmpty();
      }
    }
    if (tab === "calibration") {
      const targetId = String(view.workspacePackId || view.activePackId || "").trim();
      if (targetId) {
        void loadCalibrationTab(targetId);
      } else {
        showCalibrationEmpty();
      }
    }
    if (tab === "test") {
      const targetId = String(view.workspacePackId || view.activePackId || "").trim();
      if (targetId) {
        loadTestChatTab(targetId);
      } else {
        showTestChatEmpty();
      }
    }
  }

  return { switchTab };
}
