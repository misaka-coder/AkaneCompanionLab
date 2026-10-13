export function createWorkshopBindings({
  els,
  refreshPacks,
  openPacksFolder,
  view,
  autoSaveDraftSync,
  isTauriRuntime,
  invoke,
  appWindow,
  openCreateDialog,
  importPack,
  openCapabilitySettings,
  runPortraitCutoutForPreview,
  applyPack,
  switchTab,
  selectWorkshopPack,
  saveDraft,
  addExampleLineRow,
  updateDraftStatus,
  clearDraft,
  loadPersonaForm,
  autoSaveDraft,
  exportPack,
  createCharacterPack,
  openContextLibraryDialog,
  createContextLibrary,
  openStoryDialog,
  openStoriesFolder,
  copyStoryPrompt,
  createStory,
  sendTestChatMessage,
  resetTestChatSession,
  applyTestPackToDesktop,
}) {
  function bindUi() {
    /* header */
    els.refreshPacks.addEventListener("click", () => refreshPacks());
    els.openPacksFolder.addEventListener("click", () => openPacksFolder());
    els.closeWindow.addEventListener("click", () => {
      if (view.draftDirty && view.editingPackId) {
        autoSaveDraftSync(view.editingPackId);
      }
      if (isTauriRuntime) {
        void invoke("close_window").catch(() => appWindow?.close?.());
      } else {
        void appWindow?.close?.();
      }
    });
    els.firstUseCreate.addEventListener("click", () => openCreateDialog());
    els.firstUseImport.addEventListener("click", () => importPack());
    els.portraitCutoutConfig?.addEventListener("click", () => openCapabilitySettings());
    els.portraitCutoutRun?.addEventListener("click", () => {
      void runPortraitCutoutForPreview();
    });

    /* pack list */
    els.packList.addEventListener("click", (event) => {
      const applyBtn = event.target.closest("[data-apply-pack]");
      if (applyBtn) {
        const packId = String(applyBtn.dataset.applyPack || "").trim();
        if (packId) void applyPack(packId);
        return;
      }
      const editBtn = event.target.closest("[data-edit-pack]");
      if (editBtn) {
        const packId = String(editBtn.dataset.editPack || "").trim();
        if (packId) switchTab("persona", packId);
        return;
      }
      const packRow = event.target.closest("[data-select-pack]");
      if (packRow) {
        selectWorkshopPack(packRow.dataset.selectPack);
      }
    });
    els.packList.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      if (event.target.closest("button")) return;
      const packRow = event.target.closest("[data-select-pack]");
      if (!packRow) return;
      event.preventDefault();
      selectWorkshopPack(packRow.dataset.selectPack);
    });
    els.detailEditPack?.addEventListener("click", () => {
      if (view.workspacePackId) switchTab("persona", view.workspacePackId);
    });
    els.detailStoriesPack?.addEventListener("click", () => {
      if (view.workspacePackId) switchTab("stories", view.workspacePackId);
    });
    els.detailTestPack?.addEventListener("click", () => {
      if (view.workspacePackId) switchTab("test", view.workspacePackId);
    });

    /* tabs */
    for (const btn of els.tabButtons) {
      btn.addEventListener("click", () => {
        const tab = String(btn.dataset.tab || "").trim();
        if (tab) switchTab(tab, view.workspacePackId || view.activePackId);
      });
    }

    /* persona form */
    els.personaForm.addEventListener("submit", (event) => {
      event.preventDefault();
      void saveDraft();
    });

    els.addExampleLine.addEventListener("click", () => {
      addExampleLineRow("", "normal");
      updateDraftStatus(true);
    });

    els.exampleLinesContainer.addEventListener("click", (event) => {
      const removeBtn = event.target.closest("[data-remove-example]");
      if (removeBtn) {
        removeBtn.closest(".example-line-row")?.remove();
        updateDraftStatus(true);
      }
    });

    els.resetPersonaForm.addEventListener("click", () => {
      if (view.editingPackId) {
        clearDraft(view.editingPackId);
        loadPersonaForm(view.editingProfile);
      }
    });

    /* mark dirty on any field change */
    els.personaForm.addEventListener("input", () => updateDraftStatus(true));

    /* auto-save on field blur (only for fields inside the persona form) */
    els.personaForm.addEventListener("focusout", (event) => {
      if (event.target.closest(".form-input") && view.draftDirty) {
        void autoSaveDraft();
      }
    });

    /* Ctrl+S / Cmd+S to save draft */
    document.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key === "s") {
        event.preventDefault();
        if (view.activeTab === "persona" && view.draftDirty) {
          void saveDraft();
        }
      }
    });

    /* create dialog */
    els.createPackBtn.addEventListener("click", () => openCreateDialog());
    els.importPackBtn.addEventListener("click", () => importPack());
    els.exportPackBtn.addEventListener("click", () => exportPack());
    els.createCancel.addEventListener("click", () => els.createDialog.close());
    els.createDialog.addEventListener("click", (event) => {
      if (event.target === els.createDialog) els.createDialog.close();
    });
    els.createForm.addEventListener("submit", (event) => {
      event.preventDefault();
      void createCharacterPack();
    });

    /* context libraries */
    els.createContextLibrary?.addEventListener("click", () => openContextLibraryDialog());
    els.contextLibraryCancel?.addEventListener("click", () => els.contextLibraryDialog?.close());
    els.contextLibraryDialog?.addEventListener("click", (event) => {
      if (event.target === els.contextLibraryDialog) els.contextLibraryDialog.close();
    });
    els.contextLibraryForm?.addEventListener("submit", (event) => {
      event.preventDefault();
      void createContextLibrary();
    });

    /* interactive stories */
    els.createStoryBtn?.addEventListener("click", () => openStoryDialog());
    els.openStoriesFolderBtn?.addEventListener("click", () => openStoriesFolder());
    els.copyStoryPromptBtn?.addEventListener("click", () => copyStoryPrompt());
    els.storyDialogCancel?.addEventListener("click", () => els.storyDialog?.close());
    els.storyDialog?.addEventListener("click", (event) => {
      if (event.target === els.storyDialog) els.storyDialog?.close();
    });
    els.storyForm?.addEventListener("submit", (event) => {
      event.preventDefault();
      void createStory();
    });

    /* test chat */
    els.testChatForm.addEventListener("submit", (event) => {
      event.preventDefault();
      void sendTestChatMessage();
    });
    els.testChatClear.addEventListener("click", () => resetTestChatSession());
    els.testApplyPack.addEventListener("click", () => {
      void applyTestPackToDesktop();
    });

    /* Tauri system close (X button / Alt+F4): auto-save before exit */
    if (appWindow) {
      try {
        appWindow.onCloseRequested?.((_event) => {
          if (view.draftDirty && view.editingPackId) {
            autoSaveDraftSync(view.editingPackId);
          }
        });
      } catch {
        /* onCloseRequested may not be available in all Tauri versions */
      }
    }
  }

  return { bindUi };
}
