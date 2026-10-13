import { createWorkshopBindings } from "./bindings.js";
import { createWorkshopNavigation } from "./navigation.js";
import { createWorkshopRenderer } from "./render.js";
import { createPortraitAssetCache } from "./asset-cache.js";
import { createPortraitController } from "./portraits.js";
import { createPortraitCutoutJobs } from "./cutout-jobs.js";
import { createPortraitCutoutController } from "./portrait-cutout.js";
import { createWorkshopEvents } from "./events.js";
import { createCalibrationController } from "./calibration.js";
import { createTestChatTransport } from "./test-transport.js";
import { createTestChatController } from "./test-chat.js";
import { createWriteGuards } from "./write-guards.js";
import { createCatalogControls } from "./catalog-controls.js";
import { createWorkshopSelection } from "./selection.js";
import { createPersonaController } from "./persona.js";
import { createCatalogController } from "./catalog.js";
import { createContextLibraryController } from "./context-libraries.js";
import { createStoryController } from "./stories.js";
import { createWorkshopState } from "./state.js";
import { createWorkshopBackendClient } from "./transport.js";
import { SETTINGS_COMMAND_EVENT, SETTINGS_SNAPSHOT_EVENT } from "../control-center/event-bridge.js";
import { createWorkshopElements } from "./elements.js";

// Each window owns its state and controllers. Deferred callbacks connect controllers
// initialized later; native I/O is supplied by the entry's platform boundary.
export function createWorkshopApplication({ platform }) {
  const { invoke, emit, emitTo, listen, tauriFetch, isTauriRuntime, appWindow } = platform;
  const els = createWorkshopElements(document);

  const state = createWorkshopState();
  const { view, registryState, navigationState, writeState, personaState, contextState, storiesState,
    calibrationState, assetState, portraitState, cutoutState, testState } = state;

  const { backendFetch, buildBackendUrl } = createWorkshopBackendClient({ view, isTauriRuntime, invoke, tauriFetch });

  const writeGuards = createWriteGuards({
    writeState, view,
  });
  const { isRegistryBusy, isPackWriteBusy } = writeGuards;

  const catalogControls = createCatalogControls({
    els, writeState, contextState, storiesState, isPackWriteBusy,
    updateCalibrationControls: (...args) => updateCalibrationControls(...args),
    updatePortraitControls: (...args) => updatePortraitControls(...args),
  });
  const { updateCatalogControls } = catalogControls;

  const selection = createWorkshopSelection({
    view,
    autoSaveDraftSync: (...args) => autoSaveDraftSync(...args),
    navigationState,
    invalidateCalibration: (...args) => invalidateCalibration(...args),
    invalidatePortraits: (...args) => invalidatePortraits(...args),
    cancelTestRequest: (...args) => cancelTestRequest(...args),
  });
  const { findPack, setWorkspacePack, ensureWorkspacePack } = selection;

  const persona = createPersonaController({
    findPack, view, els, setWorkspacePack,
    setStatus: (...args) => setStatus(...args),
    personaState, isPackWriteBusy, writeState, updateCatalogControls, isTauriRuntime, invoke,
  });
  const { startEditingPack, loadPersonaForm, collectFormData, saveDraft, autoSaveDraft,
    autoSaveDraftSync, loadDraft, clearDraft, updateDraftStatus, hasBackendPendingPersonaChanges,
    addExampleLineRow } = persona;

  const catalog = createCatalogController({
    view, isTauriRuntime, isRegistryBusy,
    setStatus: (...args) => setStatus(...args),
    registryState, writeState, navigationState, invoke, autoSaveDraftSync, ensureWorkspacePack,
    findPack,
    refreshPortraitCutoutCapability: (...args) => refreshPortraitCutoutCapability(...args),
    render: (...args) => render(...args),
    loadPortraitsTab: (...args) => loadPortraitsTab(...args),
    showPortraitsEmpty: (...args) => showPortraitsEmpty(...args),
    calibrationState,
    loadCalibrationTab: (...args) => loadCalibrationTab(...args),
    isPackWriteBusy, updateCatalogControls,
    renderPackList: (...args) => renderPackList(...args),
    emitCharacterPackActivated: (...args) => emitCharacterPackActivated(...args),
    pulsePackCard: (...args) => pulsePackCard(...args),
    els,
    switchTab: (...args) => switchTab(...args),
  });
  const { acceptPackResult, refreshPacks, applySnapshot, applyPack, importPack, exportPack,
    openPacksFolder, openCreateDialog, createCharacterPack } = catalog;

  const contextLibraries = createContextLibraryController({
    findPack, els, setWorkspacePack, view,
    setStatus: (...args) => setStatus(...args),
    isTauriRuntime, contextState, navigationState, updateCatalogControls, isPackWriteBusy, writeState,
    invoke, acceptPackResult,
  });
  const { loadContextLibrariesTab, openContextLibraryDialog, createContextLibrary } = contextLibraries;

  const stories = createStoryController({
    els, findPack, isTauriRuntime, invoke,
    setStatus: (...args) => setStatus(...args),
    view, storiesState, writeState, isPackWriteBusy, updateCatalogControls, navigationState,
  });
  const { showStoriesEmpty, loadStoriesTab, openStoriesFolder, copyStoryPrompt, openStoryDialog,
    createStory } = stories;

  const assets = createPortraitAssetCache({
    assetState, invoke,
  });
  const { getPortraitImageUrl, readPortraitImageBytes, clearPortraitImageCache } = assets;

  const portraits = createPortraitController({
    portraitState, isTauriRuntime,
    setStatus: (...args) => setStatus(...args),
    findPack, els,
    refreshPortraitCutoutCapability: (...args) => refreshPortraitCutoutCapability(...args),
    invoke, view,
    renderPortraitCutoutStatus: (...args) => renderPortraitCutoutStatus(...args),
    isPackWriteBusy, writeState, updateCatalogControls, clearPortraitImageCache, getPortraitImageUrl,
  });
  const { loadPortraitsTab, showPortraitsEmpty, isPortraitTarget, invalidatePortraits,
    updatePortraitControls, runPortraitMutation, setPortraitStatus } = portraits;

  const cutoutJobs = createPortraitCutoutJobs({
    backendFetch, buildBackendUrl, view, isTauriRuntime, setPortraitStatus,
  });
  const { startPortraitCutoutJob, waitForPortraitCutoutJob, fetchPortraitCutoutOutput } = cutoutJobs;

  const portraitCutout = createPortraitCutoutController({
    isTauriRuntime, view, cutoutState, backendFetch, buildBackendUrl, els, portraitState,
    isPortraitTarget, isPackWriteBusy, setPortraitStatus, invoke, runPortraitMutation,
    readPortraitImageBytes, startPortraitCutoutJob, waitForPortraitCutoutJob, fetchPortraitCutoutOutput,
  });
  const { refreshPortraitCutoutCapability, renderPortraitCutoutStatus, openCapabilitySettings,
    runPortraitCutoutForPreview } = portraitCutout;

  const events = createWorkshopEvents({
    emitTo, emit,
  });
  const { emitCharacterPackActivated } = events;

  const calibration = createCalibrationController({
    els, calibrationState, isPackWriteBusy, view, findPack,
    setStatus: (...args) => setStatus(...args),
    invoke, getPortraitImageUrl, writeState, updateCatalogControls, emitCharacterPackActivated,
  });
  const { calibrationPreview, showCalibrationEmpty, updateCalibrationControls, invalidateCalibration,
    loadCalibrationTab, applyCalibrationPreview } = calibration;

  const testTransport = createTestChatTransport({
    buildTestCurrentVisual: (...args) => buildTestCurrentVisual(...args),
    backendFetch, buildBackendUrl, isTauriRuntime,
    readNdjsonEvents: (...args) => readNdjsonEvents(...args),
    isTestRequestCurrent: (...args) => isTestRequestCurrent(...args),
    applyTestPayload: (...args) => applyTestPayload(...args),
    updateTestMessageDiagnostics: (...args) => updateTestMessageDiagnostics(...args),
    view,
    renderTestChatTranscript: (...args) => renderTestChatTranscript(...args),
  });
  const { requestWorkshopThink, consumeWorkshopThinkStream } = testTransport;

  const testChat = createTestChatController({
    findPack, view, els, isTauriRuntime, assetState, invoke, testState, getPortraitImageUrl,
    hasBackendPendingPersonaChanges, saveDraft, applyPack, requestWorkshopThink,
    consumeWorkshopThinkStream, collectFormData, loadDraft,
  });
  const { loadTestChatTab, showTestChatEmpty, renderTestChatView, renderTestChatTranscript,
    isTestRequestCurrent, cancelTestRequest, applyTestPackToDesktop, sendTestChatMessage,
    applyTestPayload, updateTestMessageDiagnostics, readNdjsonEvents, resetTestChatSession,
    buildTestCurrentVisual } = testChat;

  const bindings = createWorkshopBindings({
    els, refreshPacks, openPacksFolder, view, autoSaveDraftSync, isTauriRuntime, invoke, appWindow,
    openCreateDialog, importPack, openCapabilitySettings, runPortraitCutoutForPreview, applyPack,
    switchTab: (...args) => switchTab(...args),
    selectWorkshopPack: (...args) => selectWorkshopPack(...args),
    saveDraft, addExampleLineRow, updateDraftStatus, clearDraft, loadPersonaForm, autoSaveDraft,
    exportPack, createCharacterPack, openContextLibraryDialog, createContextLibrary, openStoryDialog,
    openStoriesFolder, copyStoryPrompt, createStory, sendTestChatMessage, resetTestChatSession,
    applyTestPackToDesktop,
  });
  const { bindUi } = bindings;

  const navigation = createWorkshopNavigation({
    setWorkspacePack, invalidateCalibration, invalidatePortraits, navigationState, view, els,
    startEditingPack, loadContextLibrariesTab, loadStoriesTab, showStoriesEmpty, loadPortraitsTab,
    showPortraitsEmpty, loadCalibrationTab, showCalibrationEmpty, loadTestChatTab, showTestChatEmpty,
  });
  const { switchTab } = navigation;

  const renderer = createWorkshopRenderer({
    findPack, view, ensureWorkspacePack, els, startEditingPack, loadContextLibrariesTab,
    calibrationState, applyCalibrationPreview, loadTestChatTab, renderTestChatView, showTestChatEmpty,
    setWorkspacePack,
  });
  const { render, renderPackList, selectWorkshopPack, setStatus, pulsePackCard } = renderer;

  async function start() {
    bindUi();
    if (!isTauriRuntime) {
      setStatus("浏览器预览模式。");
      render();
      return;
    }

    await listen(SETTINGS_SNAPSHOT_EVENT, (event) => {
      applySnapshot(event.payload);
    });
    await refreshPacks();
    await emitTo("main", SETTINGS_COMMAND_EVENT, { command: "requestSnapshot", value: null });
  }

  function dispose() {
    calibrationPreview.dispose();
    testState.testPreview?.dispose();
    cancelTestRequest();
  }

  window.addEventListener("pagehide", dispose, { once: true });

  return {
    view, state, start, dispose,
    features: {
      writeGuards, catalogControls, selection, persona, catalog, contextLibraries, stories, assets,
      portraits, cutoutJobs, portraitCutout, events, calibration, testTransport, testChat, bindings,
      navigation, renderer,
    },
  };
}
