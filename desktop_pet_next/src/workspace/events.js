export function createWorkspaceBindings({ host, view, items, files, projects, refreshWorkspace, closeWindow, dispose,
  applySettingsSnapshot, scheduleWorkspaceRefresh, isActive, sendCommand }) {
  const cleanups = [], nativeCleanups = [];
  let disposed = false;
  const { document, els } = view;
  function on(target, event, callback) {
    if (!target) return;
    target.addEventListener(event, callback);
    cleanups.push(() => target.removeEventListener(event, callback));
  }
  els.refresh.title = "刷新手边物品 (F5 / Ctrl+R)";
  on(els.refresh, "click", () => void refreshWorkspace());
  on(els.close, "click", () => void closeWindow());
  on(els.clearFiles, "click", () => void files.clearWorkspaceFiles());
  on(els.bindProjectDirectory, "click", () => void projects.bindProjectDirectory());
  on(els.createProjectForm, "submit", event => { event.preventDefault(); void projects.createManagedProject(); });
  on(els.projectList, "click", event => {
    const button = event.target.closest("[data-project-action]");
    if (!button || button.disabled) return;
    const id = String(button.dataset.workspaceId || "");
    if (button.dataset.projectAction === "select") void projects.runProjectAction("select", id);
    if (button.dataset.projectAction === "archive") void projects.archiveProject(id);
  });
  const fileActions = { "copy-id": files.copyItemId, "open-file": files.openWorkspaceItem, "reveal-file": files.revealWorkspaceItem,
    "copy-path": files.copyWorkspaceItemPath, "export-desktop": files.exportWorkspaceItemToDesktop,
    "play-audio": files.playWorkspaceAudio, "clear-item": files.clearWorkspaceItem };
  on(els.content, "click", event => {
    const button = event.target.closest("[data-action]");
    if (!button || button.disabled) return;
    const item = items.findItem(button.closest("[data-key]")?.dataset.key || "");
    if (item) void fileActions[button.dataset.action]?.(item);
  });
  on(els.music, "click", event => {
    const button = event.target.closest("[data-music-action]");
    if (!button || button.disabled) return;
    const sourceId = String(button.closest("[data-source-id]")?.dataset.sourceId || "").trim();
    const action = button.dataset.musicAction;
    if (["previous", "next", "toggle", "stop"].includes(action)) void sendCommand("controlActiveMusic", { action });
    if (action === "play" && sourceId) void sendCommand("playMusicTrack", sourceId);
    if (action === "remove" && sourceId) void sendCommand("removeMusicTrack", sourceId);
  });
  on(document, "keydown", event => {
    if (event.key === "Escape") { event.preventDefault(); void closeWindow(); }
    if (event.key === "F5" || (event.ctrlKey && event.key.toLowerCase() === "r")) { event.preventDefault(); void refreshWorkspace(); }
  });
  on(host.window, "beforeunload", dispose);

  async function startSync() {
    try {
      for (const register of [
        () => host.listenSnapshots(snapshot => { if (isActive()) applySettingsSnapshot(snapshot); }),
        () => host.listenRefresh(payload => { if (isActive() && payload?.source !== "workspace") scheduleWorkspaceRefresh(120); })
      ]) {
        const release = await register();
        if (disposed) { release(); return; }
        nativeCleanups.push(release);
      }
      if (!disposed) await host.sendCommand("requestSnapshot", null, { isCurrent: () => !disposed });
    } catch (error) {
      for (const release of nativeCleanups.splice(0)) release();
      return error;
    }
  }

  function releaseBindings() {
    if (disposed) return;
    disposed = true;
    for (const release of [...cleanups.splice(0), ...nativeCleanups.splice(0)]) release();
  }
  return { startSync, dispose: releaseBindings };
}
