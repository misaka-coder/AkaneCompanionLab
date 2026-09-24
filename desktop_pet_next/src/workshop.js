import { invoke } from "@tauri-apps/api/core";
import { emit, emitTo, listen } from "@tauri-apps/api/event";
import { fetch as tauriFetch } from "@tauri-apps/plugin-http";
import { getCurrentWindow } from "@tauri-apps/api/window";
import {
  bindInstanceStorage,
  getInstanceStorageItem,
  removeInstanceStorageItem,
  setInstanceStorageItem
} from "./instance-storage.js";
import {
  SETTINGS_COMMAND_EVENT,
  SETTINGS_SNAPSHOT_EVENT
} from "./control-center/event-bridge.js";

import { createScaledPetPreview } from "./scaled-pet-preview.js";

import "./workshop.css";

const CHARACTER_PACK_ACTIVATED_EVENT = "akane-next-character-pack-activated";
const DRAFT_STORAGE_PREFIX = "akane-workshop-draft:";
const DEFAULT_BACKEND_URL = "http://127.0.0.1:9999";
const CLIENT_MODE = "desktop_pet";
const WORKSHOP_TEST_SCOPE_PREFIX = "workshop_test";
const PORTRAIT_CUTOUT_WORKFLOW_ID = "workflow.workshop.portrait.cutout";
const PORTRAIT_CUTOUT_CAPABILITY_CACHE_MS = 30_000;
const DEFAULT_BUBBLE_STYLE = "soft";
const MAX_CHARACTER_PACK_ZIP_BYTES = 300 * 1024 * 1024;
const MAX_PORTRAIT_IMAGE_BYTES = 20 * 1024 * 1024;
const BUBBLE_STYLE_LABELS = {
  soft: "柔和",
  paper: "便签",
  clear: "透明",
  dark: "深色",
};

const isTauriRuntime = Boolean(window.__TAURI_INTERNALS__);
const appWindow = isTauriRuntime ? getCurrentWindow() : null;

/* ------------------------------------------------------------------ */
/*  DOM refs                                                          */
/* ------------------------------------------------------------------ */

const els = {
  /* header */
  summary: document.querySelector("#workshop-summary"),
  status: document.querySelector("#workshop-status"),
  activeCharacter: document.querySelector("#active-character"),
  editingCharacter: document.querySelector("#editing-character"),
  activePack: document.querySelector("#active-pack"),
  activeSession: document.querySelector("#active-session"),
  /* character list */
  packCount: document.querySelector("#pack-count"),
  packDetailList: document.querySelector("#pack-detail-list"),
  detailEditPack: document.querySelector("#detail-edit-pack"),
  detailTestPack: document.querySelector("#detail-test-pack"),
  packList: document.querySelector("#pack-list"),
  firstUsePanel: document.querySelector("#first-use-panel"),
  firstUseCreate: document.querySelector("#first-use-create"),
  firstUseImport: document.querySelector("#first-use-import"),
  refreshPacks: document.querySelector("#refresh-packs"),
  openPacksFolder: document.querySelector("#open-packs-folder"),
  closeWindow: document.querySelector("#close-window"),
  /* tabs */
  tabButtons: document.querySelectorAll("[data-tab]"),
  tabPanels: document.querySelectorAll("[data-tab-panel]"),
  /* persona form */
  personaForm: document.querySelector("#persona-form"),
  fieldName: document.querySelector("#field-name"),
  fieldAppName: document.querySelector("#field-app-name"),
  fieldUserTitle: document.querySelector("#field-user-title"),
  fieldSelfReference: document.querySelector("#field-self-reference"),
  fieldRelationship: document.querySelector("#field-relationship"),
  fieldPersonalityKeywords: document.querySelector("#field-personality-keywords"),
  fieldCharacterCore: document.querySelector("#field-character-core"),
  fieldBehaviorStyle: document.querySelector("#field-behavior-style"),
  fieldSpeakingStyle: document.querySelector("#field-speaking-style"),
  fieldCatchphrases: document.querySelector("#field-catchphrases"),
  fieldBoundaries: document.querySelector("#field-boundaries"),
  fieldInteractionPrinciples: document.querySelector("#field-interaction-principles"),
  fieldProactiveStyle: document.querySelector("#field-proactive-style"),
  fieldExtraSetting: document.querySelector("#field-extra-setting"),
  exampleLinesContainer: document.querySelector("#example-lines-container"),
  addExampleLine: document.querySelector("#add-example-line"),
  resetPersonaForm: document.querySelector("#reset-persona-form"),
  draftStatus: document.querySelector("#draft-status"),
  personaEditingPackName: document.querySelector("#persona-editing-pack-name"),
  personaEditingPackId: document.querySelector("#persona-editing-pack-id"),
  personaEmptyState: document.querySelector("#persona-empty-state"),
  /* context libraries */
  contextLibraryContent: document.querySelector("#context-library-content"),
  contextLibraryEmptyState: document.querySelector("#context-library-empty-state"),
  contextLibraryList: document.querySelector("#context-library-list"),
  contextLibraryStatus: document.querySelector("#context-library-status"),
  createContextLibrary: document.querySelector("#create-context-library"),
  contextLibraryDialog: document.querySelector("#context-library-dialog"),
  contextLibraryForm: document.querySelector("#context-library-form"),
  contextLibraryName: document.querySelector("#context-library-name"),
  contextLibraryFolder: document.querySelector("#context-library-folder"),
  contextLibraryDescription: document.querySelector("#context-library-description"),
  contextLibraryLoadWhen: document.querySelector("#context-library-load-when"),
  contextLibraryCancel: document.querySelector("#context-library-cancel"),
  contextLibraryError: document.querySelector("#context-library-error"),
  /* interactive stories */
  storiesPanel: document.querySelector(".stories-panel"),
  storiesContent: document.querySelector("#stories-content"),
  storiesEmptyState: document.querySelector("#stories-empty-state"),
  storiesList: document.querySelector("#stories-list"),
  storiesStatus: document.querySelector("#stories-status"),
  createStoryBtn: document.querySelector("#create-story-btn"),
  openStoriesFolderBtn: document.querySelector("#open-stories-folder-btn"),
  copyStoryPromptBtn: document.querySelector("#copy-story-prompt-btn"),
  storyDialog: document.querySelector("#story-dialog"),
  storyForm: document.querySelector("#story-form"),
  storyTitleInput: document.querySelector("#story-title-input"),
  storyIdInput: document.querySelector("#story-id-input"),
  storyDialogCancel: document.querySelector("#story-dialog-cancel"),
  storyDialogError: document.querySelector("#story-dialog-error"),
  detailStoriesPack: document.querySelector("#detail-stories-pack"),
  /* portrait management */
  portraitsContent: document.querySelector("#portraits-content"),
  portraitsEmptyState: document.querySelector("#portraits-empty-state"),
  outfitsContainer: document.querySelector("#outfits-container"),
  newOutfitId: document.querySelector("#new-outfit-id"),
  addOutfitBtn: document.querySelector("#add-outfit-btn"),
  portraitImportStatus: document.querySelector("#portrait-import-status"),
  portraitCutoutStatus: document.querySelector("#portrait-cutout-status"),
  portraitCutoutSummary: document.querySelector("#portrait-cutout-summary"),
  portraitCutoutConfig: document.querySelector("#portrait-cutout-config"),
  portraitCutoutRun: document.querySelector("#portrait-cutout-run"),
  emotionPreview: document.querySelector("#emotion-preview"),
  /* calibration */
  calibrationEmptyState: document.querySelector("#calibration-empty-state"),
  calibrationContent: document.querySelector("#calibration-content"),
  calibrationPreviewArea: document.querySelector("#calibration-preview-area"),
  calibrationPreviewScale: document.querySelector("#calibration-preview-scale"),
  calibrationFrame: document.querySelector("#calibration-frame"),
  calibrationPortrait: document.querySelector("#calibration-portrait"),
  calibrationImage: document.querySelector("#calibration-image"),
  calibrationBubbleDot: document.querySelector("#calibration-bubble-dot"),
  calibrationOutfitSelect: document.querySelector("#calibration-outfit-select"),
  calibrationAutoBtn: document.querySelector("#calibration-auto-btn"),
  calibrationSaveBtn: document.querySelector("#calibration-save-btn"),
  calWinW: document.querySelector("#cal-win-w"),
  calWinH: document.querySelector("#cal-win-h"),
  calScale: document.querySelector("#cal-scale"),
  calOffsetX: document.querySelector("#cal-offset-x"),
  calOffsetY: document.querySelector("#cal-offset-y"),
  calBubbleX: document.querySelector("#cal-bubble-x"),
  calBubbleY: document.querySelector("#cal-bubble-y"),
  calBubbleStyle: document.querySelector("#cal-bubble-style"),
  /* test chat */
  testChatEmptyState: document.querySelector("#test-chat-empty-state"),
  testChatContent: document.querySelector("#test-chat-content"),
  testChatLog: document.querySelector("#test-chat-log"),
  testChatForm: document.querySelector("#test-chat-form"),
  testChatInput: document.querySelector("#test-chat-input"),
  testChatSend: document.querySelector("#test-chat-send"),
  testChatClear: document.querySelector("#test-chat-clear"),
  testApplyPack: document.querySelector("#test-apply-pack"),
  testChatStatus: document.querySelector("#test-chat-status"),
  testScopeCharacter: document.querySelector("#test-scope-character"),
  testScopeSession: document.querySelector("#test-scope-session"),
  testScopeProfile: document.querySelector("#test-scope-profile"),
  testVisualOutfit: document.querySelector("#test-visual-outfit"),
  testVisualLayout: document.querySelector("#test-visual-layout"),
  testVisualPreview: document.querySelector("#test-visual-preview"),
  testPromptFields: document.querySelector("#test-prompt-fields"),
  testResponseEmotion: document.querySelector("#test-response-emotion"),
  testResponseSegments: document.querySelector("#test-response-segments"),
  /* create dialog */
  createDialog: document.querySelector("#create-dialog"),
  createForm: document.querySelector("#create-form"),
  createPackId: document.querySelector("#create-pack-id"),
  createName: document.querySelector("#create-name"),
  createAppName: document.querySelector("#create-app-name"),
  createUserTitle: document.querySelector("#create-user-title"),
  createCancel: document.querySelector("#create-cancel"),
  createError: document.querySelector("#create-error"),
  createPackBtn: document.querySelector("#create-character-pack"),
  importPackBtn: document.querySelector("#import-pack-btn"),
  exportPackBtn: document.querySelector("#export-pack-btn"),
};

/* ------------------------------------------------------------------ */
/*  View state                                                        */
/* ------------------------------------------------------------------ */

const view = {
  instanceId: "",
  packs: [],
  activePackId: "",
  activeSessionId: "",
  activeCharacterName: "",
  backendUrl: DEFAULT_BACKEND_URL,
  profileUserId: "master",
  activeTab: "list",
  desktopScale: 1,
  /* Stable workshop target; desktop character changes must not replace it. */
  workspacePackId: "",
  /* cached full profile of the pack being edited (character.json object) */
  editingPackId: "",
  editingProfile: null,
  draftDirty: false,
  testPackId: "",
  testRunning: false,
  testMessages: [],
  testScopes: {},
  testAssetsByPack: {},
  testLastEmotion: "",
  testLastSegments: [],
  pendingApplyPackId: "",
  portraitCutoutCapability: {
    status: "unknown",
    label: "能力状态待同步",
    detail: "进入立绘管理页后会读取本地能力注册表。",
    configured: false,
    executionReady: false,
    canConfigure: false,
  },
  portraitCutoutRunning: false,
};

let lastWorkshopSnapshotSignature = "";
let verifiedWorkshopBindingKey = "";
let verifiedWorkshopBindingAt = 0;
let registryRevision = 0;
let refreshRequestToken = 0;
let snapshotRevision = 0;
let workspaceVisit = 0;
let catalogOperation = "";
let createDialogVisit = 0;
let contextDialogTarget = null;
const contextSaves = new Set();
const packExports = new Set();

function isRegistryBusy() {
  return Boolean(catalogOperation || view.pendingApplyPackId || personaSaves.size || calibrationSaves.size
    || portraitMutations.size || contextSaves.size || packExports.size);
}

function updateCatalogControls() {
  for (const control of [els.createPackBtn, els.firstUseCreate, els.importPackBtn, els.firstUseImport]) {
    control.disabled = Boolean(catalogOperation);
  }
  for (const control of els.createForm.querySelectorAll('input, button[type="submit"]')) {
    control.disabled = Boolean(catalogOperation);
  }
  els.createForm.setAttribute("aria-busy", String(Boolean(catalogOperation)));
  els.createForm.querySelector('button[type="submit"]').textContent = catalogOperation ? "正在处理…" : "创建";
  els.createCancel.textContent = catalogOperation ? "关闭" : "取消";
  const contextBusy = Boolean(contextDialogTarget && isPackWriteBusy(contextDialogTarget.packId));
  for (const control of els.contextLibraryForm.querySelectorAll('input, textarea, button[type="submit"]')) {
    control.disabled = contextBusy;
  }
  els.contextLibraryForm.setAttribute("aria-busy", String(contextBusy));
  els.contextLibraryForm.querySelector('button[type="submit"]').textContent = contextBusy ? "正在处理…" : "创建资料库";
  els.contextLibraryCancel.textContent = contextBusy ? "关闭" : "取消";
  updateCalibrationControls();
  updatePortraitControls();
}

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

const calibrationInputs = Object.fromEntries([
  els.calWinW, els.calWinH, els.calScale, els.calOffsetX,
  els.calOffsetY, els.calBubbleX, els.calBubbleY,
].filter(Boolean).map((input) => [input.id, input]));
let calibrationSlidersBound = false;
let calibrationRequestToken = 0;
let calibrationImageToken = 0;
let calibrationReadyTarget = null;
const calibrationSaves = new Set();
const personaSaves = new Set();
const personaRevisions = new Map();
let activeTestRequest = null;
const testAssetRequests = new Map();
const portraitAssetRevisions = new Map();
let portraitRequestToken = 0;
let portraitVisitToken = 0;
let portraitLoadedPackId = "";
let previewingEmotion = null;
const portraitImageUrlCache = new Map();
const portraitMutations = new Set();

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
let testPreview = null;
let testPreviewTarget = "";
let testPreviewImageToken = 0;
window.addEventListener("pagehide", () => {
  calibrationPreview.dispose();
  testPreview?.dispose();
  cancelTestRequest();
}, { once: true });

/* ------------------------------------------------------------------ */
/*  Boot                                                              */
/* ------------------------------------------------------------------ */

boot();

async function boot() {
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

/* ------------------------------------------------------------------ */
/*  UI bindings                                                       */
/* ------------------------------------------------------------------ */

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

/* ------------------------------------------------------------------ */
/*  Tab switching                                                     */
/* ------------------------------------------------------------------ */

function switchTab(tab, packId) {
  const explicitTargetId = String(packId || "").trim();
  if (tab !== "list" && explicitTargetId) {
    if (!setWorkspacePack(explicitTargetId)) return;
  }
  invalidateCalibration();
  invalidatePortraits();
  ++workspaceVisit;
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

/* ------------------------------------------------------------------ */
/*  Persona form – load / collect / save                              */
/* ------------------------------------------------------------------ */

function startEditingPack(packId) {
  const pack = findPack(packId);
  if (pack && packId === view.editingPackId && view.editingProfile) return;
  if (!pack) {
    /* no pack to edit — show empty state, hide form */
    if (els.personaForm) els.personaForm.hidden = true;
    if (els.personaEmptyState) els.personaEmptyState.hidden = false;
    setEditingPackLabel("", "");
    view.editingPackId = "";
    view.editingProfile = null;
    return;
  }

  /* profile is the raw character.json stored on the registry item */
  const profile = pack._rawProfile && typeof pack._rawProfile === "object"
    ? pack._rawProfile
    : {};

  /* auto-save previous draft before switching packs */
  if (view.editingPackId && view.editingPackId !== packId && view.draftDirty) {
    if (!autoSaveDraftSync(view.editingPackId)) return;
  }

  if (!setWorkspacePack(packId)) return;
  view.editingPackId = packId;
  view.editingProfile = profile;

  /* show form, hide empty state */
  if (els.personaForm) els.personaForm.hidden = false;
  if (els.personaEmptyState) els.personaEmptyState.hidden = true;
  setEditingPackLabel(getPackName(pack), packId);

  loadPersonaForm(profile);

  /* try restoring a local draft if one exists */
  const draft = loadDraft(packId);
  if (draft) {
    applyDraftToForm(draft);
    updateDraftStatus(false);
    setStatus(`已恢复本地草稿（${packId}）。`);
  } else {
    setStatus(`正在编辑：${getPackName(pack)}`);
  }
}

function setEditingPackLabel(name, packId) {
  if (els.personaEditingPackName) {
    els.personaEditingPackName.textContent = name || "-";
  }
  if (els.personaEditingPackId) {
    els.personaEditingPackId.textContent = packId ? "保存到当前角色包文件" : "";
    els.personaEditingPackId.title = packId || "";
  }
}

function loadPersonaForm(profile) {
  personaRevisions.set(view.editingPackId, (personaRevisions.get(view.editingPackId) || 0) + 1);
  const identity = (profile && typeof profile === "object" ? profile.identity : null) || {};
  const persona = (profile && typeof profile === "object" ? profile.persona_form : null) || {};
  const dialogue = (profile && typeof profile === "object" ? profile.dialogue : null) || {};
  populateFormFields(identity, persona, dialogue);
  view.draftDirty = false;
  updateDraftStatus(false);
}

function applyDraftToForm(draft) {
  if (!draft) return;
  const ident = draft.identity || {};
  const persona = draft.persona_form || {};
  const dialogue = draft.dialogue || {};
  populateFormFields(ident, persona, dialogue);
}

/** one-stop form fill from identity + persona objects */
function populateFormFields(identity, persona, dialogue = {}) {
  identity = identity || {};
  persona = persona || {};
  dialogue = dialogue || {};

  setFieldValue(els.fieldName, identity.name || "");
  setFieldValue(els.fieldAppName, identity.app_name || identity.appName || "");
  setFieldValue(els.fieldUserTitle, identity.user_title || identity.userTitle || "");
  setFieldValue(els.fieldSelfReference, identity.self_reference || identity.selfReference || "");
  setFieldValue(els.fieldRelationship, identity.relationship || "");
  setFieldValue(els.fieldPersonalityKeywords, asKeywordsInput(persona.personality_keywords));
  setFieldValue(els.fieldCharacterCore, persona.character_core || persona.characterCore || "");
  setFieldValue(els.fieldBehaviorStyle, persona.behavior_style || persona.behaviorStyle || "");
  setFieldValue(els.fieldSpeakingStyle, persona.speaking_style || persona.speakingStyle || "");
  setFieldValue(els.fieldCatchphrases, asLinesInput(persona.catchphrases));
  setFieldValue(els.fieldBoundaries, persona.boundaries || "");
  setFieldValue(els.fieldInteractionPrinciples, persona.interaction_principles || persona.interactionPrinciples || "");
  setFieldValue(els.fieldProactiveStyle, persona.proactive_style || persona.proactiveStyle || "");
  setFieldValue(els.fieldExtraSetting, persona.extra_setting || persona.extraSetting || "");

  const personaLines = Array.isArray(persona.example_lines) ? persona.example_lines : [];
  const clickLines = Array.isArray(dialogue.local_click_lines) ? dialogue.local_click_lines : [];
  const lines = personaLines.length ? personaLines : clickLines;
  renderExampleLines(lines);
}

function collectFormData() {
  const identity = {
    name: (els.fieldName?.value || "").trim(),
    app_name: (els.fieldAppName?.value || "").trim(),
    user_title: (els.fieldUserTitle?.value || "").trim(),
    self_reference: (els.fieldSelfReference?.value || "").trim(),
    relationship: (els.fieldRelationship?.value || "").trim(),
  };

  const exampleLines = collectExampleLines();
  const personaForm = {
    personality_keywords: splitKeywords(els.fieldPersonalityKeywords?.value || ""),
    character_core: (els.fieldCharacterCore?.value || "").trim(),
    behavior_style: (els.fieldBehaviorStyle?.value || "").trim(),
    speaking_style: (els.fieldSpeakingStyle?.value || "").trim(),
    catchphrases: splitLines(els.fieldCatchphrases?.value || ""),
    boundaries: (els.fieldBoundaries?.value || "").trim(),
    interaction_principles: (els.fieldInteractionPrinciples?.value || "").trim(),
    proactive_style: (els.fieldProactiveStyle?.value || "").trim(),
    extra_setting: (els.fieldExtraSetting?.value || "").trim(),
    example_lines: exampleLines,
  };

  return {
    packId: view.editingPackId,
    identity,
    persona_form: personaForm,
    dialogue: exampleLines.length ? { local_click_lines: exampleLines } : null,
  };
}

function collectExampleLines() {
  const rows = els.exampleLinesContainer.querySelectorAll(".example-line-row");
  return Array.from(rows)
    .map((row) => {
      const textEl = row.querySelector(".example-line-text");
      const emotionEl = row.querySelector(".example-line-emotion");
      const text = (textEl?.value || "").trim();
      if (!text) return null;
      return {
        text,
        emotion: (emotionEl?.value || "normal").trim() || "normal",
      };
    })
    .filter(Boolean);
}

async function saveDraft(options = {}) {
  const allowLocalFallback = options.allowLocalFallback !== false;
  const packId = String(options.packId || view.editingPackId || "").trim();
  if (!packId) {
    setStatus("请先从角色列表中选择一个角色包。");
    return { ok: false, persisted: false, reason: "no-pack" };
  }
  if (isPackWriteBusy(packId)) return { ok: false, persisted: false, reason: "save-in-progress" };
  const data = packId === view.editingPackId ? collectFormData() : loadDraft(packId);
  if (!data) return { ok: false, persisted: false, packId, reason: "no-draft" };
  const revision = personaRevisions.get(packId) || 0;
  const unchanged = () => (personaRevisions.get(packId) || 0) === revision;
  const ownsForm = () => view.editingPackId === packId && unchanged();
  const ownsStatus = () => ownsForm() && view.activeTab === "persona";
  personaSaves.add(packId);
  ++registryRevision;
  updateCatalogControls();
  let fileError = "";
  try {
    if (isTauriRuntime) {
      try {
        const result = await invoke("save_character_pack", {
          request: {
            packId,
            identity: data.identity,
            personaForm: data.persona_form,
            dialogue: data.dialogue,
          },
        });
        if (!result || String(result.id || "").trim() !== packId || !result.profile) {
          throw new Error("角色包保存返回了无效结果");
        }
        const idx = view.packs.findIndex((p) => p.id === packId);
        if (idx >= 0) {
          view.packs[idx] = normalizePacks([result])[0] || view.packs[idx];
          view.packs[idx]._rawProfile = result.profile;
        }
        if (view.editingPackId === packId) view.editingProfile = result.profile;
        if (unchanged()) clearDraft(packId);
        if (ownsForm()) updateDraftStatus(false);
        if (ownsStatus()) {
          setStatus("已保存到角色包文件。");
          flashElement(els.draftStatus, "save-flash");
        }
        return { ok: unchanged(), persisted: true, source: "file", packId,
          reason: unchanged() ? "saved" : "newer-edits-pending" };
      } catch (error) {
        fileError = formatError(error);
        if (!allowLocalFallback) {
          if (ownsStatus()) setStatus(`文件保存失败：${fileError}`);
          return { ok: false, persisted: false, source: "file", packId, error: fileError };
        }
      }
    }
    if (!allowLocalFallback) {
      if (ownsStatus()) setStatus("文件保存不可用，未写入角色包文件。");
      return { ok: false, persisted: false, source: "file", packId, reason: "file-save-unavailable" };
    }
    // Never overwrite a newer local draft with an older failed file write.
    if (!unchanged()) return { ok: false, persisted: false, packId, reason: "newer-edits-pending", error: fileError };
    if (!persistDraft(packId, data)) throw new Error("localStorage unavailable");
    if (ownsForm()) updateDraftStatus(false);
    if (ownsStatus()) {
      setStatus(fileError ? `文件保存失败，已保存到本地草稿：${fileError}` : "草稿已保存。");
      flashElement(els.draftStatus, "save-flash");
    }
    return { ok: true, persisted: false, source: "localStorage", packId };
  } catch (error) {
    const message = formatError(error);
    if (ownsStatus()) setStatus(`保存失败：${message}`);
    return { ok: false, persisted: false, source: "localStorage", packId, error: message };
  } finally {
    personaSaves.delete(packId);
    ++registryRevision;
    updateCatalogControls();
  }
}

async function autoSaveDraft() {
  if (!view.editingPackId || !view.draftDirty) return;
  const data = collectFormData();
  if (persistDraft(view.editingPackId, data)) {
    view.draftDirty = false;
    updateDraftStatus(false);
  } else {
    updateDraftStatus(true);
    setStatus("自动保存草稿失败，请手动保存。");
  }
}

/** synchronous save – used when switching packs inside a sync call chain */
function autoSaveDraftSync(packId) {
  if (!packId || !view.draftDirty) return true;
  const data = collectFormData();
  if (persistDraft(packId, data)) {
    view.draftDirty = false;
    return true;
  }
  setStatus("本地草稿保存失败，已保留当前编辑内容。请保存成功后再切换角色。");
  return false;
}

/* ------------------------------------------------------------------ */
/*  Draft persistence (localStorage)                                   */
/* ------------------------------------------------------------------ */

function draftKey(packId) {
  return `workshop.draft:${String(packId || "").trim()}`;
}

function persistDraft(packId, data) {
  try {
    const payload = {
      packId,
      savedAt: new Date().toISOString(),
      identity: data.identity,
      persona_form: data.persona_form,
      dialogue: data.dialogue,
    };
    return setInstanceStorageItem(draftKey(packId), JSON.stringify(payload));
  } catch {
    return false;
  }
}

function loadDraft(packId) {
  try {
    const raw = getInstanceStorageItem(draftKey(packId), {
      legacyKey: `${DRAFT_STORAGE_PREFIX}${packId}`
    });
    if (!raw) return null;
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function clearDraft(packId) {
  try {
    removeInstanceStorageItem(draftKey(packId));
  } catch {
    /* ignore */
  }
}

function updateDraftStatus(dirty) {
  if (dirty) personaRevisions.set(view.editingPackId, (personaRevisions.get(view.editingPackId) || 0) + 1);
  view.draftDirty = dirty;
  if (!els.draftStatus) return;
  if (dirty) {
    els.draftStatus.textContent = "未保存的更改";
    els.draftStatus.className = "draft-indicator dirty";
  } else if (hasDraft(view.editingPackId)) {
    els.draftStatus.textContent = "已保存草稿";
    els.draftStatus.className = "draft-indicator saved";
  } else {
    els.draftStatus.textContent = "";
    els.draftStatus.className = "draft-indicator";
  }
}

function hasDraft(packId) {
  return Boolean(loadDraft(packId));
}

function hasBackendPendingPersonaChanges(packId) {
  const id = String(packId || "").trim();
  return Boolean(id && ((view.editingPackId === id && view.draftDirty) || hasDraft(id)));
}

/* ------------------------------------------------------------------ */
/*  Example lines rendering                                           */
/* ------------------------------------------------------------------ */

function renderExampleLines(lines) {
  if (!els.exampleLinesContainer) return;
  els.exampleLinesContainer.replaceChildren();
  const items = Array.isArray(lines) ? lines : [];
  for (const item of items) {
    const text = typeof item === "string" ? item : (item.text || "");
    const emotion = (item && typeof item === "object" ? item.emotion : "") || "normal";
    addExampleLineRow(text, emotion);
  }
  /* always keep at least one empty row so the creator sees the format */
  if (items.length === 0) {
    addExampleLineRow("", "normal");
  }
}

function addExampleLineRow(text, emotion) {
  const container = els.exampleLinesContainer;
  if (!container) return;

  const row = document.createElement("div");
  row.className = "example-line-row";

  const textInput = document.createElement("textarea");
  textInput.rows = 2;
  textInput.className = "form-input example-line-text";
  textInput.setAttribute("aria-label", "点击反馈台词内容");
  textInput.value = text || "";
  textInput.maxLength = 200;
  textInput.addEventListener("input", () => updateDraftStatus(true));

  const emotionInput = document.createElement("input");
  emotionInput.type = "text";
  emotionInput.className = "form-input example-line-emotion";
  emotionInput.setAttribute("aria-label", "点击反馈台词表情或立绘");
  emotionInput.value = emotion || "normal";
  emotionInput.maxLength = 40;
  emotionInput.addEventListener("input", () => updateDraftStatus(true));

  const removeBtn = document.createElement("button");
  removeBtn.type = "button";
  removeBtn.className = "btn-remove-line";
  removeBtn.setAttribute("data-remove-example", "");
  removeBtn.textContent = "✕";
  removeBtn.title = "删除这条台词";

  row.append(textInput, emotionInput, removeBtn);
  container.appendChild(row);
}

/* ------------------------------------------------------------------ */
/*  Pack list (existing + extended)                                     */
/* ------------------------------------------------------------------ */

async function refreshPacks() {
  if (!isTauriRuntime) return { ok: false, reason: "unavailable" };
  if (isRegistryBusy()) {
    setStatus("角色包操作尚未完成，请完成后再刷新。");
    return { ok: false, reason: "operation-in-progress" };
  }
  const token = ++refreshRequestToken;
  const revision = registryRevision;
  const stateRevision = snapshotRevision;
  const visit = workspaceVisit;
  const current = () => token === refreshRequestToken && revision === registryRevision;
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
    if (stateRevision === snapshotRevision) {
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
    if (visit === workspaceVisit) setStatus("角色包已刷新。");
    // Retry failed asset views while preserving a ready calibration's unsaved values.
    if (view.activeTab === "portraits") {
      if (view.workspacePackId) await loadPortraitsTab(view.workspacePackId);
      else showPortraitsEmpty();
    }
    if (view.activeTab === "calibration" && !calibrationReadyTarget) await loadCalibrationTab(view.workspacePackId);
    return { ok: true };
  } catch (error) {
    if (current() && visit === workspaceVisit) setStatus(`刷新失败：${formatError(error)}`);
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
  if (signature === lastWorkshopSnapshotSignature) {
    return;
  }
  lastWorkshopSnapshotSignature = signature;
  ++snapshotRevision;
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
  ++registryRevision;
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
    ++registryRevision;
    updateCatalogControls();
  }
}

async function importPack() {
  if (!isTauriRuntime) { setStatus("浏览器预览模式不支持导入。"); return; }
  if (isRegistryBusy()) { setStatus("请等待角色包操作完成后再导入。"); return; }
  catalogOperation = "import";
  ++registryRevision;
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
    catalogOperation = "";
    ++registryRevision;
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
  if (isPackWriteBusy(packId) || packExports.has(packId)) {
    setStatus("请等待当前角色包操作完成后再导出。"); return;
  }
  packExports.add(packId);
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
    packExports.delete(packId);
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
  ++createDialogVisit;
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
  const dialogVisit = createDialogVisit;
  const visit = workspaceVisit;
  const ownsDialog = () => dialogVisit === createDialogVisit && els.createDialog.open;
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
  catalogOperation = "create";
  ++registryRevision;
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
    const reportHere = ownsDialog() && visit === workspaceVisit;
    if (ownsDialog()) {
      els.createDialog.close();
      if (visit === workspaceVisit) switchTab("persona", pack.id);
    }
    render();
    if (reportHere) setStatus(`已创建角色包：${name}（${pack.id}）。`);
  } catch (error) {
    if (ownsDialog()) showCreateError(formatError(error));
    if (visit === workspaceVisit) setStatus(`创建失败：${formatError(error)}`);
  } finally {
    catalogOperation = "";
    ++registryRevision;
    updateCatalogControls();
  }
}

/* ------------------------------------------------------------------ */
/*  Character context libraries                                      */
/* ------------------------------------------------------------------ */

function loadContextLibrariesTab(packId) {
  const pack = findPack(packId);
  if (!pack) {
    if (els.contextLibraryContent) els.contextLibraryContent.hidden = true;
    if (els.contextLibraryEmptyState) els.contextLibraryEmptyState.hidden = false;
    return;
  }

  setWorkspacePack(pack.id);
  if (els.contextLibraryContent) els.contextLibraryContent.hidden = false;
  if (els.contextLibraryEmptyState) els.contextLibraryEmptyState.hidden = true;
  if (els.contextLibraryStatus) els.contextLibraryStatus.textContent = `资料库属于：${getPackName(pack)}。`;
  renderContextLibraries(pack);
}

function renderContextLibraries(pack) {
  if (!els.contextLibraryList) return;
  const profile = pack?._rawProfile && typeof pack._rawProfile === "object"
    ? pack._rawProfile
    : {};
  const libraries = Array.isArray(profile.context_libraries)
    ? profile.context_libraries.filter((item) => item && typeof item === "object")
    : [];

  els.contextLibraryList.replaceChildren();
  if (!libraries.length) {
    const empty = document.createElement("div");
    empty.className = "context-library-card context-library-card-empty";
    const title = document.createElement("strong");
    title.textContent = "还没有角色资料库";
    const note = document.createElement("p");
    note.textContent = "新建后，往对应文件夹放入以主题命名的 Markdown 文件即可。";
    empty.append(title, note);
    els.contextLibraryList.appendChild(empty);
    return;
  }

  for (const library of libraries) {
    const folder = String(library.folder || "").trim();
    const name = String(library.name || folder || "未命名资料库").trim();
    const description = String(library.description || "").trim();
    const loadWhen = String(library.load_when || library.loadWhen || "").trim();
    const card = document.createElement("article");
    card.className = "context-library-card";

    const heading = document.createElement("div");
    heading.className = "context-library-card-heading";
    const title = document.createElement("strong");
    title.textContent = name;
    const folderLabel = document.createElement("code");
    folderLabel.textContent = folder || "未设置文件夹";
    heading.append(title, folderLabel);

    const descriptionLine = document.createElement("p");
    descriptionLine.textContent = description || "尚未填写内容说明。";
    const loadLine = document.createElement("p");
    loadLine.className = "context-library-load-when";
    loadLine.textContent = `读取时机：${loadWhen || "尚未填写"}`;
    card.append(heading, descriptionLine, loadLine);
    els.contextLibraryList.appendChild(card);
  }
}

function openContextLibraryDialog() {
  const packId = String(view.workspacePackId || view.activePackId || "").trim();
  if (!packId) {
    setStatus("请先选择一个角色包。");
    return;
  }
  if (!isTauriRuntime) {
    setStatus("浏览器预览模式不支持创建资料库。");
    return;
  }
  contextDialogTarget = { packId, visit: workspaceVisit };
  setFieldValue(els.contextLibraryName, "");
  setFieldValue(els.contextLibraryFolder, "");
  setFieldValue(els.contextLibraryDescription, "");
  setFieldValue(els.contextLibraryLoadWhen, "");
  showContextLibraryError("");
  updateCatalogControls();
  els.contextLibraryDialog?.showModal();
}

function showContextLibraryError(message) {
  if (!els.contextLibraryError) return;
  els.contextLibraryError.textContent = String(message || "");
  els.contextLibraryError.hidden = !message;
  if (message) flashElement(els.contextLibraryForm, "invalid-flash");
}

async function createContextLibrary() {
  const target = contextDialogTarget;
  if (!target || !els.contextLibraryDialog.open || !findPack(target.packId)) return;
  const { packId } = target;
  const ownsDialog = () => contextDialogTarget === target && els.contextLibraryDialog.open;
  const ownsView = () => view.activeTab === "context" && view.workspacePackId === packId
    && workspaceVisit === target.visit;
  if (isPackWriteBusy(packId)) { showContextLibraryError("请等待当前角色包操作完成后再创建。"); return; }
  const name = String(els.contextLibraryName?.value || "").trim();
  const folder = String(els.contextLibraryFolder?.value || name).trim();
  const description = String(els.contextLibraryDescription?.value || "").trim();
  const loadWhen = String(els.contextLibraryLoadWhen?.value || "").trim();

  if (!name) {
    showContextLibraryError("请填写资料库名称。");
    return;
  }
  if (!description) {
    showContextLibraryError("请用一句话说明这里存什么。");
    return;
  }
  if (!loadWhen) {
    showContextLibraryError("请说明角色应在什么时候读取这组资料。");
    return;
  }

  contextSaves.add(packId);
  ++registryRevision;
  updateCatalogControls();
  showContextLibraryError("");
  if (ownsView() && els.contextLibraryStatus) {
    els.contextLibraryStatus.textContent = `正在创建：${name}…`;
  }
  try {
    const result = await invoke("create_character_context_library", {
      request: {
        packId,
        folder,
        name,
        description,
        loadWhen,
      },
    });
    const normalized = acceptPackResult(result, packId);
    if (ownsDialog()) els.contextLibraryDialog.close();
    if (ownsView()) {
      renderContextLibraries(normalized);
      if (els.contextLibraryStatus) {
        els.contextLibraryStatus.textContent = `已创建“${name}”。放入 .md 文件后，模型会在提示词中看到它的用途和读取时机。`;
      }
      setStatus(`已创建角色资料库：${name}`);
    }
  } catch (error) {
    const message = formatError(error);
    if (ownsDialog()) showContextLibraryError(message);
    if (ownsView() && els.contextLibraryStatus) {
      els.contextLibraryStatus.textContent = `创建失败：${message}`;
    }
  } finally {
    contextSaves.delete(packId);
    ++registryRevision;
    updateCatalogControls();
  }
}

/* ------------------------------------------------------------------ */
/*  Interactive Stories tab                                           */
/* ------------------------------------------------------------------ */

let storyDialogTarget = null;

const AI_STORY_PROMPT = `你是一个沉浸式桌宠互动剧本编剧。请根据我给定的主题与角色设定，编写一份符合 AkaneCompanionLab 互动剧本规范的 Markdown 格式剧本。

【剧本格式规范】：
必须以 YAML Frontmatter 开头：
---
story_id: <唯一ID，英文小写与下划线，例如 afternoon_tea>
title: <剧本标题>
description: <一句话剧本简介>
cover_image: scenes/家/白天客厅.png
initial_node_id: intro_1
---

节点格式：每个节点以 ## 节点ID [类型] 开头。完整支持 4 种类型：
1. [script]：剧情台词
   角色: Akane
   表情: normal (支持 normal/开心/卖萌/脸红/思考中/气鼓鼓/得意 等)
   动作: nod (支持 nod/shake/bounce/idle 等)
   背景: scenes/家/白天客厅.png
   下一幕: 下一个节点ID
   正文台词内容...

2. [choice]：分支选择
   标题: 选择肢提示
   角色: Akane
   表情: 卖萌
   动作: idle
   提示台词...
   ? 你的选择:
   - "选项A文案" -> target_node_A
   - "选项B文案" -> target_node_B
   - （自由对话）自由输入提示 -> agent_node

3. [agent]：自由对话交谈
   角色: Akane
   表情: 思考中
   动作: nod
   目标: 角色在此处与玩家自由互动的目标引导说明
   下一幕: 结算节点ID
   正文引导台词...

4. [ending]：结局结算
   结局标题: 结局一：标题
   结局总结: 本次故事的结局评语
   表情: 开心
   动作: bounce
   结局总结台词...

请保持剧情温馨治愈，角色性格鲜活生动。不要输出任何 Markdown 剧本代码块以外的额外解释废话。`;

function showStoriesEmpty() {
  if (els.storiesContent) els.storiesContent.hidden = true;
  if (els.storiesEmptyState) els.storiesEmptyState.hidden = false;
}

async function loadStoriesTab(packId) {
  const pack = findPack(packId);
  if (!pack) {
    showStoriesEmpty();
    return;
  }
  if (els.storiesEmptyState) els.storiesEmptyState.hidden = true;
  if (els.storiesContent) els.storiesContent.hidden = false;
  if (!els.storiesList) return;

  els.storiesList.replaceChildren(buildText("p", "正在扫描剧本列表…"));

  if (!isTauriRuntime) {
    els.storiesList.replaceChildren(buildText("p", "浏览器预览模式不支持读取本地剧本。"));
    return;
  }

  try {
    const stories = await invoke("list_character_stories", { packId });
    renderStoriesList(stories, packId);
  } catch (error) {
    els.storiesList.replaceChildren(buildText("p", `读取剧本失败：${formatError(error)}`));
  }
}

function renderStoriesList(stories, packId) {
  if (!els.storiesList) return;
  els.storiesList.replaceChildren();

  if (!Array.isArray(stories) || stories.length === 0) {
    const empty = document.createElement("div");
    empty.className = "story-card story-card-empty";
    const title = document.createElement("strong");
    title.textContent = "还没有互动剧本";
    const note = document.createElement("p");
    note.textContent = "点击上方「新建剧本草稿」，或点击「打开剧本目录」将写好的 .md 剧本放入 stories/ 文件夹中。";
    empty.append(title, note);
    els.storiesList.appendChild(empty);
    return;
  }

  for (const item of stories) {
    const card = document.createElement("article");
    card.className = "story-card";

    const header = document.createElement("div");
    header.className = "story-card-header";

    const titleEl = document.createElement("div");
    titleEl.className = "story-card-title";
    titleEl.textContent = item.title || item.story_id || item.file_name;

    const badge = document.createElement("span");
    badge.className = "story-card-badge";
    badge.textContent = item.file_name;
    header.append(titleEl, badge);

    const desc = document.createElement("p");
    desc.className = "story-card-desc";
    desc.textContent = item.description || "暂无简介说明";

    const footer = document.createElement("div");
    footer.className = "story-card-footer";

    const sizeKb = (item.size_bytes / 1024).toFixed(1);
    const meta = document.createElement("span");
    meta.textContent = `${sizeKb} KB · ID: ${item.story_id}`;

    const actions = document.createElement("div");
    actions.className = "story-card-actions";

    const openBtn = document.createElement("button");
    openBtn.type = "button";
    openBtn.className = "btn-secondary";
    openBtn.textContent = "编辑剧本";
    openBtn.addEventListener("click", async () => {
      try {
        await invoke("open_local_file", { path: item.path });
      } catch (err) {
        setStatus(`打开文件失败：${formatError(err)}`);
      }
    });

    const revealBtn = document.createElement("button");
    revealBtn.type = "button";
    revealBtn.className = "btn-secondary";
    revealBtn.textContent = "在目录中查看";
    revealBtn.addEventListener("click", async () => {
      try {
        await invoke("show_item_in_folder", { path: item.path });
      } catch (err) {
        setStatus(`打开目录失败：${formatError(err)}`);
      }
    });

    actions.append(openBtn, revealBtn);
    footer.append(meta, actions);
    card.append(header, desc, footer);
    els.storiesList.appendChild(card);
  }
}

async function openStoriesFolder() {
  const packId = String(view.workspacePackId || view.activePackId || "").trim();
  if (!packId) {
    setStatus("请先选择一个角色包。");
    return;
  }
  if (!isTauriRuntime) {
    setStatus("浏览器预览模式不支持打开本地目录。");
    return;
  }
  try {
    await invoke("open_character_stories_folder", { packId });
    setStatus("已在资源管理器中打开角色 stories 目录。");
  } catch (error) {
    setStatus(`打开目录失败：${formatError(error)}`);
  }
}

async function copyStoryPrompt() {
  try {
    await navigator.clipboard.writeText(AI_STORY_PROMPT);
    if (els.storiesStatus) {
      els.storiesStatus.textContent = "已复制 AI 创作提示词！直接发送给 DeepSeek、Claude 或 ChatGPT 即可生成小剧场。";
    }
    setStatus("已复制 AI 创作提示词！");
  } catch (error) {
    setStatus(`复制失败：${formatError(error)}`);
  }
}

function openStoryDialog() {
  const packId = String(view.workspacePackId || view.activePackId || "").trim();
  if (!packId) {
    setStatus("请先选择一个角色包。");
    return;
  }
  if (!isTauriRuntime) {
    setStatus("浏览器预览模式不支持创建剧本。");
    return;
  }
  storyDialogTarget = { packId, visit: workspaceVisit };
  setFieldValue(els.storyTitleInput, "");
  setFieldValue(els.storyIdInput, "");
  showStoryError("");
  els.storyDialog?.showModal();
}

function showStoryError(message) {
  if (!els.storyDialogError) return;
  els.storyDialogError.textContent = String(message || "");
  els.storyDialogError.hidden = !message;
  if (message) flashElement(els.storyForm, "invalid-flash");
}

async function createStory() {
  const target = storyDialogTarget;
  if (!target || !els.storyDialog?.open || !findPack(target.packId)) return;
  const { packId } = target;
  const title = String(els.storyTitleInput?.value || "").trim();
  const storyId = String(els.storyIdInput?.value || "").trim();

  if (!title) {
    showStoryError("请填写剧本标题。");
    return;
  }
  if (!storyId) {
    showStoryError("请填写剧本 ID (英文/数字/下划线)。");
    return;
  }

  try {
    const filePath = await invoke("create_character_story_template", {
      packId,
      storyId,
      title,
    });
    els.storyDialog.close();
    setStatus(`已成功创建剧本：${title}`);
    await loadStoriesTab(packId);
    try {
      await invoke("open_local_file", { path: filePath });
    } catch {
      /* opening editor is best effort */
    }
  } catch (error) {
    showStoryError(formatError(error));
  }
}

/* ------------------------------------------------------------------ */
/*  Portrait management                                               */
/* ------------------------------------------------------------------ */

async function loadPortraitsTab(packId) {
  if (!isPortraitTarget(packId)) return false;
  const token = ++portraitRequestToken;
  const visit = portraitVisitToken;
  const selected = previewingEmotion?.packId === packId ? previewingEmotion : null;
  portraitLoadedPackId = "";
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
    if (token !== portraitRequestToken || !isPortraitTarget(packId, visit)) return false;
    portraitLoadedPackId = packId;
    view.testAssetsByPack[packId] = Array.isArray(outfits) ? outfits : [];
    renderPortraitsView(packId, outfits);
    const outfit = view.testAssetsByPack[packId].find((item) => item.id === selected?.outfitId);
    const emotion = outfit?.emotions?.find((item) => item.id === selected?.emotion?.id);
    if (emotion) previewEmotion(packId, outfit.id, emotion);
    updatePortraitControls();
    return true;
  } catch (error) {
    if (token !== portraitRequestToken || !isPortraitTarget(packId, visit)) return false;
    setStatus(`读取立绘失败：${formatError(error)}`);
    setPortraitStatus(`读取失败：${formatError(error)}`, true);
    showPortraitsEmpty();
    return false;
  }
}

function showPortraitsEmpty() {
  portraitLoadedPackId = "";
  clearPortraitPreview();
  updatePortraitControls();
  if (els.portraitsContent) els.portraitsContent.hidden = true;
  if (els.portraitsEmptyState) els.portraitsEmptyState.hidden = false;
}

function isPortraitTarget(packId, visit = portraitVisitToken) {
  return view.activeTab === "portraits" && view.workspacePackId === packId
    && visit === portraitVisitToken && Boolean(findPack(packId));
}

function clearPortraitPreview() {
  previewingEmotion = null;
  els.emotionPreview?.replaceChildren(buildText("p", "选择一张图片，查看预览和可用操作。"));
  renderPortraitCutoutStatus();
}

function invalidatePortraits() {
  ++portraitVisitToken;
  ++portraitRequestToken;
  portraitLoadedPackId = "";
  clearPortraitPreview();
  updatePortraitControls();
}

function updatePortraitControls() {
  const enabled = Boolean(portraitLoadedPackId && isPortraitTarget(portraitLoadedPackId)
    && !isPackWriteBusy(portraitLoadedPackId));
  for (const control of [els.newOutfitId, els.addOutfitBtn,
    ...els.outfitsContainer.querySelectorAll("button"), ...els.emotionPreview.querySelectorAll("button")]) {
    control.disabled = !enabled;
  }
  renderPortraitCutoutStatus();
}

function isPackWriteBusy(packId) {
  return Boolean(catalogOperation || view.pendingApplyPackId === packId
    || personaSaves.has(packId) || calibrationSaves.has(packId)
    || portraitMutations.has(packId) || contextSaves.has(packId) || packExports.has(packId));
}

async function runPortraitMutation(packId, label, action) {
  if (!isTauriRuntime || !isPortraitTarget(packId) || portraitLoadedPackId !== packId) {
    return { ok: false, reason: "stale-target" };
  }
  if (isPackWriteBusy(packId)) return { ok: false, reason: "operation-in-progress" };
  const visit = portraitVisitToken;
  const current = () => isPortraitTarget(packId, visit);
  const report = (message, error = false) => {
    if (!current()) return;
    setStatus(message);
    setPortraitStatus(message, error);
  };
  portraitMutations.add(packId);
  ++registryRevision;
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
    portraitMutations.delete(packId);
    ++registryRevision;
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

let lastCutoutCapabilitySignature = "";
let lastCutoutCapabilitySyncedAt = 0;
let cutoutCapabilityRequest = null;
let cutoutCapabilityGeneration = 0;

async function refreshPortraitCutoutCapability(options = {}) {
  if (!isTauriRuntime) {
    applyPortraitCutoutCapability({
      status: "unavailable",
      label: "浏览器预览不可用",
      detail: "请在桌宠窗口中读取本地能力状态。",
    });
    return view.portraitCutoutCapability;
  }
  const signature = stableSignature({
    backendUrl: normalizeBackendUrl(view.backendUrl),
    profileUserId: view.profileUserId || "master",
  });
  const cacheIsFresh = signature === lastCutoutCapabilitySignature &&
    Date.now() - lastCutoutCapabilitySyncedAt < PORTRAIT_CUTOUT_CAPABILITY_CACHE_MS;
  if (!options.force && cacheIsFresh) {
    renderPortraitCutoutStatus();
    return view.portraitCutoutCapability;
  }
  if (cutoutCapabilityRequest && signature === lastCutoutCapabilitySignature) {
    return cutoutCapabilityRequest;
  }
  lastCutoutCapabilitySignature = signature;
  const generation = ++cutoutCapabilityGeneration;
  cutoutCapabilityRequest = (async () => {
    try {
      const payload = await readWorkflowCatalog();
      if (generation !== cutoutCapabilityGeneration) return view.portraitCutoutCapability;
      const workflows = Array.isArray(payload?.workflows) ? payload.workflows : [];
      const workflow = workflows.find((item) => String(item?.id || "").trim() === PORTRAIT_CUTOUT_WORKFLOW_ID);
      applyPortraitCutoutCapability(workflow
        ? normalizePortraitCutoutCapability(workflow)
        : {
            status: "unavailable",
            label: "未找到透明背景处理入口",
            detail: "能力注册表暂未提供角色立绘透明背景处理。",
          });
      lastCutoutCapabilitySyncedAt = Date.now();
    } catch (error) {
      if (generation !== cutoutCapabilityGeneration) return view.portraitCutoutCapability;
      applyPortraitCutoutCapability({
        status: "unavailable",
        label: "能力状态未同步",
        detail: "立绘管理仍可正常使用；稍后可到设置里的能力页检查本地环境。",
        reason: formatError(error),
      });
      lastCutoutCapabilitySyncedAt = Date.now();
      if (!options.silent && view.activeTab === "portraits") {
        renderPortraitCutoutStatus();
      }
    } finally {
      if (generation === cutoutCapabilityGeneration) cutoutCapabilityRequest = null;
    }
    return view.portraitCutoutCapability;
  })();
  return cutoutCapabilityRequest;
}

async function readWorkflowCatalog() {
  const profileId = view.profileUserId || "master";
  const response = await backendFetch(buildBackendUrl("/capabilities/workflows", {
    user_id: "desktop",
    session_id: "desktop",
    real_user_id: profileId,
    client: CLIENT_MODE,
    t: Date.now(),
  }), {
    method: "GET",
    headers: { Accept: "application/json" },
    cache: "no-store",
    ...(isTauriRuntime ? { connectTimeout: 3_500 } : {}),
  });
  if (!response.ok) {
    throw new Error(await readResponseError(response, `HTTP ${response.status}`));
  }
  return response.json();
}

function applyPortraitCutoutCapability(next) {
  view.portraitCutoutCapability = {
    status: String(next?.status || "unknown").trim() || "unknown",
    label: String(next?.label || "能力状态待同步").trim() || "能力状态待同步",
    detail: String(next?.detail || "").trim(),
    configured: Boolean(next?.configured),
    enabled: Boolean(next?.enabled),
    executionReady: Boolean(next?.executionReady),
    canConfigure: Boolean(next?.canConfigure),
    reason: String(next?.reason || "").trim(),
  };
  if (view.activeTab === "portraits") {
    renderPortraitCutoutStatus();
  }
}

function normalizePortraitCutoutCapability(workflow) {
  const status = String(workflow?.status || "").trim() || "unknown";
  const reason = String(workflow?.reason || "").trim();
  const reasonDetail = describePortraitCutoutReason(reason);
  const labels = {
    configured: "已绑定，等待执行器",
    validated_config: "已绑定，等待执行器",
    ready: "自动抠图已可用",
    missing_config: "需要配置本地 ComfyUI",
    missing_workflow: "需要绑定抠图工作流",
    missing_slot_mapping: "需要补齐输入输出槽位",
    disabled: "自动抠图绑定未启用",
    unreachable: "ComfyUI 暂时未连接",
    invalid_config: "本地能力配置异常",
    invalid_workflow_config: "工作流绑定异常",
  };
  const details = {
    configured: "配置已经保存，但后端还没有绑定真实执行器。",
    validated_config: "配置已经通过基础校验，但后端还没有绑定真实执行器。",
    ready: "先在右侧预览一张表情图，再点击自动抠图生成透明背景版本。",
    missing_config: "可以先到设置的能力页填写本地 ComfyUI 地址并做探活。",
    missing_workflow: "本地服务已配置，下一步是在能力页绑定透明背景处理工作流。",
    missing_slot_mapping: "工作流引用已保存，还需要补齐输入图片和输出图片的槽位名。",
    disabled: "绑定存在但未启用，可以到能力页重新启用。",
    unreachable: "请确认 ComfyUI 正在运行，然后在能力页重新探活。",
    invalid_config: "能力配置文件需要修复，立绘管理本身不会受影响。",
    invalid_workflow_config: "工作流绑定需要修复，立绘管理本身不会受影响。",
  };
  return {
    status,
    label: labels[status] || "能力状态待确认",
    detail: reasonDetail || details[status] || "立绘管理可继续使用；自动处理入口会等真实执行边界完成后开放。",
    configured: Boolean(workflow?.configured),
    enabled: Boolean(workflow?.enabled),
    executionReady: Boolean(workflow?.executionReady),
    canConfigure: workflow?.configurable !== false,
    reason,
  };
}

function renderPortraitCutoutStatus() {
  if (!els.portraitCutoutStatus || !els.portraitCutoutSummary) return;
  const state = view.portraitCutoutCapability || {};
  const status = String(state.status || "unknown").trim() || "unknown";
  els.portraitCutoutStatus.dataset.state = portraitCutoutTone(status);
  els.portraitCutoutSummary.textContent = state.label || "能力状态待同步";
  els.portraitCutoutStatus.title = state.detail || "";
  if (els.portraitCutoutConfig) {
    els.portraitCutoutConfig.hidden = state.canConfigure === false;
    els.portraitCutoutConfig.textContent = state.configured ? "查看配置" : "去配置";
  }
  if (els.portraitCutoutRun) {
    const canRun = status === "ready" && state.executionReady && !view.portraitCutoutRunning
      && previewingEmotion?.packId === portraitLoadedPackId
      && Boolean(portraitLoadedPackId) && isPortraitTarget(portraitLoadedPackId)
      && !isPackWriteBusy(portraitLoadedPackId);
    els.portraitCutoutRun.hidden = !(status === "ready" && state.executionReady);
    els.portraitCutoutRun.disabled = !canRun;
    els.portraitCutoutRun.textContent = view.portraitCutoutRunning ? "处理中..." : "自动抠图";
  }
}

function portraitCutoutTone(status) {
  if (status === "ready") return "ready";
  if (status === "configured" || status === "validated_config") return "configured";
  if (status === "invalid_config" || status === "invalid_workflow_config") return "error";
  if (status === "missing_config" || status === "missing_workflow" || status === "missing_slot_mapping" || status === "disabled" || status === "unreachable") {
    return "attention";
  }
  return "unknown";
}

async function openCapabilitySettings() {
  if (!isTauriRuntime) {
    setPortraitStatus("浏览器预览模式无法打开设置窗口。", true);
    return;
  }
  try {
    await invoke("open_settings_window");
    setPortraitStatus("已打开设置窗口。请在“能力”页查看本地能力环境。");
  } catch (error) {
    setPortraitStatus(`打开设置失败：${formatError(error)}`, true);
  }
}

async function runPortraitCutoutForPreview() {
  if (!isTauriRuntime) { setPortraitStatus("浏览器预览模式无法执行自动抠图。", true); return; }
  if (view.portraitCutoutRunning) return;
  const target = previewingEmotion;
  const packId = String(target?.packId || "").trim();
  const outfitId = String(target?.outfitId || "").trim();
  const emotion = target?.emotion || null;
  const emotionId = String(emotion?.id || "").trim();
  if (!packId || !outfitId || !emotionId) {
    setPortraitStatus("请先在右侧预览一张要处理的表情图。", true);
    return;
  }
  return runPortraitMutation(packId, "自动抠图", async ({ current, report }) => {
    view.portraitCutoutRunning = true;
    renderPortraitCutoutStatus();
    try {
      const capability = await refreshPortraitCutoutCapability({ force: true, silent: true });
      if (!current()) return { cancelled: true };
      if (capability.status !== "ready" || !capability.executionReady) {
        report("自动抠图还没准备好，请先在能力页完成 ComfyUI 和工作流绑定。", true);
        return { cancelled: true };
      }
      const generatedEmotion = buildGeneratedCutoutEmotionId(emotionId);
      const outputHandle = `portrait_cutout_${Date.now().toString(36)}`;
      report(`正在处理：${outfitId} / ${emotionId}…`);
      const imageBytes = await readPortraitImageBytes(packId, outfitId, emotionId);
      if (!current()) return { cancelled: true };
      const job = await startPortraitCutoutJob({ inputImageHandle: "portrait_source",
        outputImageHandle: outputHandle, imageBytes, mimeType: portraitMimeType(emotion?.path) });
      const completed = await waitForPortraitCutoutJob(job.jobId);
      const output = Array.isArray(completed?.job?.outputs) ? completed.job.outputs[0] : null;
      const outputBytes = await fetchPortraitCutoutOutput(job.jobId, output?.handle || outputHandle);
      await invoke("import_generated_portrait_image", { packId, outfit: outfitId, emotion: generatedEmotion,
        imageBytes: Array.from(outputBytes), mimeType: output?.contentType || "image/png", overwrite: false });
      return { message: `已生成透明背景版本：${outfitId} / ${generatedEmotion}` };
    } catch (error) {
      throw new Error(formatPortraitCutoutError(error));
    } finally {
      view.portraitCutoutRunning = false;
      renderPortraitCutoutStatus();
    }
  });
}

async function startPortraitCutoutJob({ inputImageHandle, outputImageHandle, imageBytes, mimeType }) {
  const response = await backendFetch(buildBackendUrl(`/capabilities/workflows/${PORTRAIT_CUTOUT_WORKFLOW_ID}/jobs`, {
    user_id: "desktop",
    session_id: "desktop",
    real_user_id: view.profileUserId || "master",
    client: CLIENT_MODE,
  }), {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({
      inputImageHandle,
      outputImageHandle,
      inputImageBytes: Array.from(imageBytes),
      inputImageContentType: mimeType,
    }),
    cache: "no-store",
    ...(isTauriRuntime ? { connectTimeout: 3_500 } : {}),
  });
  if (!response.ok) {
    throw new Error(await readResponseError(response, `HTTP ${response.status}`));
  }
  const payload = await response.json();
  if (!payload?.ok || !payload?.jobId) {
    throw new Error(payload?.reason || payload?.status || "workflow_job_start_failed");
  }
  return payload;
}

async function waitForPortraitCutoutJob(jobId) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < 90_000) {
    const response = await backendFetch(buildBackendUrl(`/capabilities/workflow-jobs/${jobId}`, {
      user_id: "desktop",
      session_id: "desktop",
      real_user_id: view.profileUserId || "master",
      client: CLIENT_MODE,
      t: Date.now(),
    }), {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
      ...(isTauriRuntime ? { connectTimeout: 3_500 } : {}),
    });
    if (!response.ok) {
      throw new Error(await readResponseError(response, `HTTP ${response.status}`));
    }
    const payload = await response.json();
    const status = String(payload?.status || "").trim();
    if (status === "completed") return payload;
    if (status === "failed") {
      throw new Error(payload?.reason || "workflow_job_failed");
    }
    setPortraitStatus(`自动抠图处理中：${status || "running"}…`);
    await delay(900);
  }
  throw new Error("workflow_job_timeout");
}

async function fetchPortraitCutoutOutput(jobId, outputHandle) {
  const response = await backendFetch(buildBackendUrl(`/capabilities/workflow-jobs/${jobId}/outputs/${outputHandle}`, {
    user_id: "desktop",
    session_id: "desktop",
    real_user_id: view.profileUserId || "master",
    client: CLIENT_MODE,
  }), {
    method: "GET",
    headers: { Accept: "image/png,image/webp,image/jpeg,*/*" },
    cache: "no-store",
    ...(isTauriRuntime ? { connectTimeout: 3_500 } : {}),
  });
  if (!response.ok) {
    throw new Error(await readResponseError(response, `HTTP ${response.status}`));
  }
  return new Uint8Array(await response.arrayBuffer());
}

function buildGeneratedCutoutEmotionId(emotionId) {
  const base = String(emotionId || "normal").trim() || "normal";
  return base.endsWith("_cutout") ? base : `${base}_cutout`;
}

function delay(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function formatPortraitCutoutError(error) {
  const raw = formatError(error);
  return describePortraitCutoutReason(raw) || raw;
}

function describePortraitCutoutReason(reason) {
  const key = String(reason || "").trim();
  const messages = {
    workflow_file_missing: "找不到工作流 JSON。请把文件放到当前用户能力目录下，例如 capabilities/workflows/comfyui/portrait_cutout.json。",
    workflow_file_invalid_json: "工作流 JSON 解析失败，请确认导出的是 ComfyUI API workflow JSON。",
    workflow_file_invalid_encoding: "工作流文件编码无法读取，请保存为 UTF-8 JSON。",
    workflow_file_too_large: "工作流文件过大，请检查是否误放了图片或模型文件。",
    workflow_file_unreadable: "工作流文件暂时无法读取，请检查文件是否被占用或权限不足。",
    workflow_json_invalid: "工作流内容不是有效的 ComfyUI 工作流对象。",
    workflow_path_must_be_safe_relative_json: "工作流引用只能填写能力目录内的相对 JSON 路径。",
    workflow_path_required: "还没有填写工作流 JSON 引用。",
    required_slot_mapping_missing: "还需要填写输入图片和输出文件名的 ComfyUI 节点槽位。",
    slot_mapping_path_invalid: "槽位需要填写成 ComfyUI 节点输入路径，例如 12.inputs.image。",
    slot_mapping_node_missing: "槽位指向的节点在工作流 JSON 里不存在。",
    slot_mapping_inputs_missing: "槽位指向的节点没有 inputs 区域，请换成可写入的输入节点。",
    slot_mapping_target_missing: "槽位指向的输入字段不存在，请核对节点编号和字段名。",
    input_image_bytes_required: "没有读到当前预览立绘的图片数据，请重新点选一张表情图。",
    workflow_slot_mapping_invalid: "工作流槽位映射失败，请核对节点路径是否仍然匹配当前 JSON。",
    comfyui_request_failed: "ComfyUI 请求失败，请确认服务正在运行，且工作流依赖节点和模型可用。",
    workflow_runtime_config_invalid: "工作流运行配置无效，请回到能力页检查 ComfyUI 地址、JSON 文件和槽位。",
    workflow_runner_failed: "本地工作流执行失败，请检查 ComfyUI 控制台输出。",
    workflow_job_failed: "工作流任务失败，请检查能力配置和 ComfyUI 状态。",
    workflow_job_timeout: "工作流处理超时，可以稍后重试或检查 ComfyUI 是否卡住。",
    workflow_output_not_found: "工作流完成了，但没有找到可导入的输出图片。",
    workflow_job_output_not_ready: "输出还没准备好，请稍后再试。",
    connection_failed: "无法连接本地 ComfyUI，请确认它正在运行。",
    provider_unavailable: "本地 ComfyUI 暂时不可用，请在能力页重新探活。",
  };
  return messages[key] || "";
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
  if (!els.emotionPreview || !isPortraitTarget(packId) || portraitLoadedPackId !== packId) return;
  previewingEmotion = { packId, outfitId, emotion };
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

async function getPortraitImageUrl(packId, outfitId, emotion) {
  const emotionId = String(emotion?.id || "").trim();
  const key = `${packId}\u0000${outfitId}\u0000${emotionId}`;
  if (portraitImageUrlCache.has(key)) {
    return portraitImageUrlCache.get(key);
  }

  const promise = (async () => {
    const bytes = await readPortraitImageBytes(packId, outfitId, emotionId);
    return URL.createObjectURL(new Blob([bytes], { type: portraitMimeType(emotion?.path) }));
  })();

  portraitImageUrlCache.set(key, promise);
  try {
    return await promise;
  } catch (error) {
    portraitImageUrlCache.delete(key);
    throw error;
  }
}

async function readPortraitImageBytes(packId, outfitId, emotionId) {
  const payload = await invoke("read_portrait_image", {
    packId,
    outfit: outfitId,
    emotion: emotionId,
  });
  const bytes = payload instanceof Uint8Array
    ? payload
    : payload instanceof ArrayBuffer
      ? new Uint8Array(payload)
      : new Uint8Array(payload || []);
  if (!bytes.length) {
    throw new Error("图片数据为空");
  }
  if (bytes.length > MAX_PORTRAIT_IMAGE_BYTES) {
    throw new Error("图片大小不能超过 20 MB。");
  }
  return bytes;
}

function portraitMimeType(path) {
  const extension = String(path || "").split(".").pop().toLowerCase();
  if (extension === "jpg" || extension === "jpeg") return "image/jpeg";
  if (extension === "webp") return "image/webp";
  return "image/png";
}

function clearPortraitImageCache(packId) {
  portraitAssetRevisions.set(packId, (portraitAssetRevisions.get(packId) || 0) + 1);
  testAssetRequests.delete(packId);
  const prefix = `${packId}\u0000`;
  for (const [key, value] of portraitImageUrlCache.entries()) {
    if (!key.startsWith(prefix)) continue;
    portraitImageUrlCache.delete(key);
    Promise.resolve(value).then((url) => URL.revokeObjectURL(url)).catch(() => {});
  }
}

function buildImageLoadError(text, className = "image-load-error") {
  const fallback = document.createElement("div");
  fallback.className = className;
  fallback.textContent = text;
  return fallback;
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
  if (!isPortraitTarget(packId) || portraitMutations.has(packId)) return;
  if (!confirm(`确定删除表情 "${emotionId}"？此操作不可恢复。`)) return;
  return runPortraitMutation(packId, "删除表情", async () => {
    await invoke("delete_portrait_image", { packId, outfit: outfitId, emotion: emotionId });
    return { message: `已删除表情：${emotionId}` };
  });
}

async function renameEmotionDialog(packId, outfitId, oldId) {
  if (!isPortraitTarget(packId) || portraitMutations.has(packId)) return;
  const newId = prompt(`重命名表情 "${oldId}" 为：`, oldId);
  if (!newId || !newId.trim() || newId.trim() === oldId) return;
  return runPortraitMutation(packId, "重命名表情", async () => {
    await invoke("rename_portrait_emotion", { packId, outfit: outfitId, oldEmotion: oldId, newEmotion: newId.trim() });
    return { message: `已重命名：${newId.trim()}` };
  });
}

async function renameOutfitDialog(packId, oldId) {
  if (!isPortraitTarget(packId) || portraitMutations.has(packId)) return;
  const newId = prompt(`重命名服装 "${oldId}" 为：`, oldId);
  if (!newId || !newId.trim() || newId.trim() === oldId) return;
  return runPortraitMutation(packId, "重命名服装", async () => {
    await invoke("rename_portrait_outfit", { packId, oldOutfit: oldId, newOutfit: newId.trim() });
    return { message: `已重命名服装：${newId.trim()}` };
  });
}

function inferEmotionNameFromFileName(fileName, fallback = "normal") {
  const baseName = String(fileName || "")
    .replace(/\\/g, "/")
    .split("/")
    .pop()
    .replace(/\.[^.]+$/, "")
    .trim();
  return baseName || fallback;
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

function chooseLocalFiles({ accept = "", multiple = false } = {}) {
  return new Promise((resolve) => {
    const fileInput = document.createElement("input");
    fileInput.type = "file";
    fileInput.accept = accept;
    fileInput.multiple = multiple;
    fileInput.hidden = true;
    document.body.appendChild(fileInput);

    let settled = false;
    const finish = (files = []) => {
      if (settled) return;
      settled = true;
      fileInput.remove();
      resolve(Array.from(files || []));
    };

    fileInput.addEventListener("change", () => finish(fileInput.files), { once: true });
    fileInput.addEventListener("cancel", () => finish(), { once: true });
  fileInput.click();
});
}

function yieldToUiForLargeFileRead() {
  return new Promise((resolve) => {
    window.setTimeout(resolve, 0);
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

function formatFileSize(bytes) {
  if (!bytes || bytes < 1024) return `${bytes || 0} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/* ------------------------------------------------------------------ */
/*  Display calibration                                               */
/* ------------------------------------------------------------------ */

function showCalibrationEmpty() {
  invalidateCalibration();
  if (els.calibrationContent) els.calibrationContent.hidden = true;
  if (els.calibrationEmptyState) els.calibrationEmptyState.hidden = false;
}

function updateCalibrationControls() {
  const ready = calibrationReadyTarget;
  const enabled = Boolean(ready && isCalibrationCurrent(ready.packId, ready.requestToken)
    && !isPackWriteBusy(ready.packId));
  for (const input of [...Object.values(calibrationInputs), ...getCalibrationBubbleStyleButtons(),
    els.calibrationAutoBtn, els.calibrationSaveBtn]) {
    if (input) input.disabled = !enabled;
  }
  els.calibrationBubbleDot?.setAttribute("aria-disabled", String(!enabled));
}

function clearCalibrationPreview() {
  ++calibrationImageToken;
  calibrationReadyTarget = null;
  els.calibrationImage.onload = null;
  els.calibrationImage.onerror = null;
  calibrationPreview.clear();
  delete els.calibrationBubbleDot.dataset.dragging;
  updateCalibrationControls();
}

function invalidateCalibration() {
  ++calibrationRequestToken;
  clearCalibrationPreview();
  els.calibrationOutfitSelect.disabled = true;
}

function isCalibrationCurrent(packId, requestToken) {
  return requestToken === calibrationRequestToken && view.activeTab === "calibration"
    && view.workspacePackId === packId && Boolean(findPack(packId));
}

async function loadCalibrationTab(packId) {
  invalidateCalibration();
  const requestToken = calibrationRequestToken;
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

async function loadCalibrationOutfitPreview(packId, outfit, profile, requestToken = calibrationRequestToken) {
  if (!isCalibrationCurrent(packId, requestToken)) return false;
  clearCalibrationPreview();
  const token = calibrationImageToken;
  const current = () => token === calibrationImageToken && isCalibrationCurrent(packId, requestToken);
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
      calibrationReadyTarget = { packId, outfitId: outfit.id, requestToken, token };
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
  if (!calibrationReadyTarget) return;
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
  if (calibrationSlidersBound) return;
  calibrationSlidersBound = true;
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
  const target = calibrationReadyTarget;
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

function normalizeBubbleStyle(value) {
  const style = String(value || "").trim().toLowerCase();
  return Object.prototype.hasOwnProperty.call(BUBBLE_STYLE_LABELS, style) ? style : DEFAULT_BUBBLE_STYLE;
}

function readSliderUnit(input, fallback) {
  const raw = Number(input?.value);
  const percent = Number.isFinite(raw) ? raw : fallback * 100;
  return clampNumber(percent / 100, 0, 1);
}

function readUnitValue(value, fallback) {
  const next = Number(value);
  if (!Number.isFinite(next)) return fallback;
  return clampNumber(next, 0, 1);
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

function clampNumber(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

async function saveCalibration(packId) {
  const target = calibrationReadyTarget;
  if (!target || target.packId !== packId || !isCalibrationCurrent(packId, target.requestToken)
    || target.outfitId !== els.calibrationOutfitSelect.value || isPackWriteBusy(packId)) return;
  const { outfitId } = target;
  calibrationSaves.add(packId);
  ++registryRevision;
  updateCatalogControls();
  const ownsUi = () => calibrationReadyTarget === target && isCalibrationCurrent(packId, target.requestToken);
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
    calibrationSaves.delete(packId);
    ++registryRevision;
    updateCatalogControls();
  }
}

async function emitCharacterPackActivated(activePackId) {
  try {
    await emitTo("main", CHARACTER_PACK_ACTIVATED_EVENT, { packId: activePackId });
  } catch {
    await emit(CHARACTER_PACK_ACTIVATED_EVENT, { packId: activePackId });
  }
}

/* ------------------------------------------------------------------ */
/*  Test chat                                                         */
/* ------------------------------------------------------------------ */

function loadTestChatTab(packId) {
  const pack = findPack(packId);
  if (!pack) {
    showTestChatEmpty();
    return;
  }
  if (view.testPackId && view.testPackId !== packId) {
    cancelTestRequest();
    view.testMessages = [];
    view.testLastEmotion = "";
    view.testLastSegments = [];
    els.testChatInput.value = "";
    setTestStatus("等待测试输入。");
  }
  view.testPackId = packId;
  ensureTestScope(packId);
  if (els.testChatContent) els.testChatContent.hidden = false;
  if (els.testChatEmptyState) els.testChatEmptyState.hidden = true;
  renderTestChatView(packId);
  if (!view.testAssetsByPack[packId]) {
    void refreshTestVisualAssets(packId);
  }
}

function showTestChatEmpty() {
  cancelTestRequest();
  view.testPackId = "";
  if (els.testChatContent) els.testChatContent.hidden = true;
  if (els.testChatEmptyState) els.testChatEmptyState.hidden = false;
  renderTestDiagnostics(null, null);
  updateTestChatControls();
}

function renderTestChatView(packId = view.testPackId) {
  if (packId !== view.testPackId || view.activeTab !== "test") return;
  const pack = findPack(packId);
  if (!pack) {
    showTestChatEmpty();
    return;
  }
  const scope = ensureTestScope(packId);
  renderTestDiagnostics(pack, scope);
  renderTestChatTranscript();
  updateTestChatControls();
}

function renderTestDiagnostics(pack, scope) {
  const packId = pack?.id || "";
  if (els.testScopeCharacter) {
    els.testScopeCharacter.textContent = getPackName(pack) || "未选择";
    els.testScopeCharacter.title = packId || "";
  }
  if (els.testScopeSession) {
    els.testScopeSession.textContent = scope?.sessionId ? "独立测试会话" : "-";
    els.testScopeSession.title = scope?.sessionId || "";
  }
  if (els.testScopeProfile) {
    els.testScopeProfile.textContent = scope?.profileUserId ? "不会混入正式记忆" : "-";
    els.testScopeProfile.title = scope?.profileUserId || "";
  }
  if (els.testResponseEmotion) {
    els.testResponseEmotion.textContent = view.testLastEmotion || "-";
  }
  const visual = pack ? getTestVisualState(pack) : null;
  if (els.testVisualOutfit) {
    els.testVisualOutfit.textContent = visual?.outfit || "-";
    els.testVisualOutfit.title = visual?.outfit || "";
  }
  if (els.testVisualLayout) {
    els.testVisualLayout.textContent = visual?.layout
      ? formatTestLayoutSummary(visual.layout)
      : "未保存";
  }
  renderTestVisualPreview(pack, visual);
  renderPromptFieldSummary(pack);
  renderTestResponseSegments(view.testLastSegments);
}

function renderPromptFieldSummary(pack) {
  if (!els.testPromptFields) return;
  if (!pack) {
    els.testPromptFields.textContent = "请选择角色包。";
    return;
  }
  const profile = getEffectiveTestProfile(pack);
  const identity = profile.identity || {};
  const persona = profile.persona_form || {};
  const rows = [
    ["角色名称", identity.name || pack.name || "-"],
    ["应用显示名", identity.app_name || identity.appName || pack.name || "-"],
    ["用户称呼", identity.user_title || identity.userTitle || "-"],
    ["自称", identity.self_reference || identity.selfReference || "-"],
    ["关系", identity.relationship || "-"],
    ["性格关键词", summarizeList(persona.personality_keywords)],
    ["角色核心", summarizeText(persona.character_core || persona.characterCore)],
    ["行为倾向", summarizeText(persona.behavior_style || persona.behaviorStyle)],
    ["说话风格", summarizeText(persona.speaking_style || persona.speakingStyle)],
    ["口头禅", `${asArray(persona.catchphrases).length} 条`],
    ["边界", summarizeText(persona.boundaries)],
    ["互动原则", summarizeText(persona.interaction_principles || persona.interactionPrinciples)],
    ["点击反馈台词", `${asArray(persona.example_lines).length} 条`],
  ];
  els.testPromptFields.replaceChildren(
    ...rows.map(([label, value]) => {
      const row = document.createElement("div");
      row.append(buildText("span", label), buildText("strong", value));
      return row;
    })
  );
}

function renderTestResponseSegments(segments) {
  if (!els.testResponseSegments) return;
  const items = Array.isArray(segments) ? segments.filter(Boolean) : [];
  if (!items.length) {
    els.testResponseSegments.textContent = "-";
    return;
  }
  els.testResponseSegments.replaceChildren(
    ...items.map((segment) => buildText("p", segment))
  );
}

async function refreshTestVisualAssets(packId) {
  if (!isTauriRuntime || !packId) return;
  if (testAssetRequests.has(packId)) return testAssetRequests.get(packId);
  const revision = portraitAssetRevisions.get(packId) || 0;
  const request = Promise.resolve().then(async () => {
  try {
    const items = await invoke("list_pack_assets", { packId });
    if ((portraitAssetRevisions.get(packId) || 0) !== revision) return;
    view.testAssetsByPack[packId] = Array.isArray(items) ? items : [];
  } catch (error) {
    if ((portraitAssetRevisions.get(packId) || 0) !== revision) return;
    // Keep a failed read retryable instead of caching a false empty state.
    if (view.activeTab === "test" && view.testPackId === packId) {
      setTestStatus(`立绘预览读取失败：${formatError(error)}。重新进入此页可重试。`);
    }
  } finally {
    if (testAssetRequests.get(packId) === request) testAssetRequests.delete(packId);
  }
  if (view.activeTab === "test" && view.testPackId === packId) {
    renderTestChatView(packId);
  }
  });
  testAssetRequests.set(packId, request);
  return request;
}

function renderTestVisualPreview(pack, visual = null) {
  if (!els.testVisualPreview) return;
  if (!testPreview) {
    const host = document.createElement("div");
    host.className = "test-preview-host";
    const info = document.createElement("p");
    info.className = "test-visual-info";
    els.testVisualPreview.replaceChildren(host, info);
    testPreview = createScaledPetPreview({ host });
  }
  const token = ++testPreviewImageToken;
  const info = els.testVisualPreview.querySelector(".test-visual-info");
  if (!pack) {
    testPreview.clear("请选择角色包。");
    testPreviewTarget = "";
    info.textContent = "";
    return;
  }
  const state = visual || getTestVisualState(pack);
  const emotion = findTestVisualEmotionAsset(pack, state);
  const target = `${pack.id}::${state.outfit}`;
  if (testPreviewTarget !== target) testPreview.clear();
  testPreviewTarget = target;
  testPreview.setLayout(state.layout || null, { petScale: view.desktopScale });
  testPreview.setText(view.testLastSegments[0]);
  info.textContent = `${state.outfit || "-"} / ${state.emotion || "-"} · ${state.layout ? "已加载校准" : "未保存校准"}`;
  if (!emotion?.id) {
    testPreview.clear("未找到默认立绘资源");
    return;
  }
  void getPortraitImageUrl(pack.id, state.outfit, emotion).then((url) => {
    if (token !== testPreviewImageToken) return;
    testPreview.setExpression({ id: emotion.id, name: emotion.name || emotion.id, url });
  }).catch((error) => {
    if (token !== testPreviewImageToken) return;
    testPreview.clear(`立绘读取失败：${formatError(error)}`);
  });
}

function renderTestChatTranscript() {
  if (!els.testChatLog) return;
  if (!view.testMessages.length) {
    const empty = document.createElement("p");
    empty.className = "test-chat-placeholder";
    empty.textContent = "还没有测试消息。";
    els.testChatLog.replaceChildren(empty);
    return;
  }
  els.testChatLog.replaceChildren(
    ...view.testMessages.map((message) => {
      const item = document.createElement("article");
      item.className = `test-message ${message.role === "user" ? "user" : "assistant"}`;
      const title = document.createElement("header");
      title.append(
        buildText("strong", message.role === "user" ? "你" : getPackName(findPack(view.testPackId)) || "角色"),
        buildText("span", message.status || "")
      );
      item.append(title);
      const segments = Array.isArray(message.segments) && message.segments.length
        ? message.segments
        : [message.text || ""].filter(Boolean);
      for (const segment of segments) {
        item.append(buildText("p", segment));
      }
      if (message.emotion) {
        const footer = document.createElement("footer");
        footer.textContent = `表情：${message.emotion}`;
        item.append(footer);
      }
      if (message.error) item.append(buildText("p", message.error));
      return item;
    })
  );
  els.testChatLog.scrollTop = els.testChatLog.scrollHeight;
}

function beginTestRequest(packId) {
  if (activeTestRequest || view.testRunning || view.testPackId !== packId
    || view.workspacePackId !== packId || view.activeTab !== "test") return null;
  const request = { packId, scope: ensureTestScope(packId), controller: new AbortController(), message: null };
  activeTestRequest = request;
  view.testRunning = true;
  updateTestChatControls();
  return request;
}

function isTestRequestCurrent(request) {
  return activeTestRequest === request && !request.controller.signal.aborted
    && view.testPackId === request.packId && view.workspacePackId === request.packId
    && view.testScopes[request.packId] === request.scope;
}

function cancelTestRequest() {
  const request = activeTestRequest;
  activeTestRequest = null;
  if (request) {
    request.controller.abort();
    if (request.message) request.message.status = "已停止接收";
  }
  view.testRunning = false;
  updateTestChatControls();
}

function finishTestRequest(request) {
  if (activeTestRequest !== request) return;
  activeTestRequest = null;
  view.testRunning = false;
  renderTestChatView(request.packId);
}

async function applyTestPackToDesktop() {
  const packId = String(view.testPackId || "").trim();
  const pack = findPack(packId);
  if (!pack) { setTestStatus("请先选择一个角色包。"); return; }
  if (!isTauriRuntime) { setTestStatus("浏览器预览模式不支持应用到桌宠。"); return; }
  const request = beginTestRequest(packId);
  if (!request) return;
  try {
    if (hasBackendPendingPersonaChanges(packId)) {
      setTestStatus("正在保存当前人设字段。");
      const saveResult = await saveDraft({ allowLocalFallback: false, packId });
      if (!isTestRequestCurrent(request)) return;
      if (!saveResult?.ok) {
        setTestStatus("应用已暂停：当前人设没有写入角色包文件。");
        return;
      }
    }
    if (!isTestRequestCurrent(request)) return;
    setTestStatus(`正在应用到桌宠：${getPackName(pack) || packId}`);
    const result = await applyPack(packId);
    if (!isTestRequestCurrent(request)) return;
    setTestStatus(result?.ok ? `已应用到桌宠：${getPackName(pack) || packId}`
      : `应用失败：${result?.error || result?.reason || "unknown error"}`);
  } catch (error) {
    if (isTestRequestCurrent(request)) setTestStatus(`应用失败：${formatError(error)}`);
  } finally {
    finishTestRequest(request);
  }
}

async function sendTestChatMessage() {
  const packId = String(view.testPackId || "").trim();
  const pack = findPack(packId);
  const text = String(els.testChatInput?.value || "").trim();
  if (!pack) { setTestStatus("请先选择一个角色包。"); return; }
  if (!text) { setTestStatus("请输入测试内容。"); return; }
  const request = beginTestRequest(packId);
  if (!request) return;
  try {
    if (hasBackendPendingPersonaChanges(packId)) {
      setTestStatus("正在保存当前人设字段。");
      const saveResult = await saveDraft({ allowLocalFallback: false, packId });
      if (!isTestRequestCurrent(request)) return;
      if (!saveResult?.ok) {
        setTestStatus("测试已暂停：当前人设没有写入角色包文件。");
        return;
      }
    }
    if (!isTestRequestCurrent(request)) return;
    const requestPack = findPack(packId) || pack;
    view.testMessages.push({ role: "user", text, status: "已发送" });
    const assistantMessage = { role: "assistant", text: "", segments: [], emotion: "", status: "等待回复" };
    request.message = assistantMessage;
    view.testMessages.push(assistantMessage);
    view.testLastEmotion = "";
    view.testLastSegments = [];
    els.testChatInput.value = "";
    renderTestChatView(packId);
    setTestStatus("正在请求 /think。");
    const response = await requestWorkshopThink({ pack: requestPack, scope: request.scope,
      message: text, signal: request.controller.signal });
    if (!isTestRequestCurrent(request)) { await response.body?.cancel?.(); return; }
    if (!response.ok) throw new Error(await readResponseError(response, `HTTP ${response.status}`));
    await consumeWorkshopThinkStream(response, assistantMessage, request);
    if (!isTestRequestCurrent(request)) return;
    if (!assistantMessage.text && !assistantMessage.segments.length) throw new Error("未收到回复内容");
    assistantMessage.status = "完成";
    setTestStatus("测试回复完成。");
  } catch (error) {
    if (!isTestRequestCurrent(request)) return;
    if (request.message) {
      request.message.status = "失败";
      request.message.error = `请求失败：${formatError(error)}`;
    }
    setTestStatus(`测试失败：${formatError(error)}`);
  } finally {
    finishTestRequest(request);
  }
}

async function requestWorkshopThink({ pack, scope, message, signal }) {
  const payload = {
    user_id: scope.sessionId,
    session_id: scope.sessionId,
    real_user_id: scope.profileUserId,
    message,
    turn_kind: "workshop_test_chat",
    client_mode: CLIENT_MODE,
    character_pack_id: pack.id,
    client_capabilities: ["speech_segments"],
    current_visual: buildTestCurrentVisual(pack),
    desktop_context: {},
    desktop_screen_frames: [],
    desktop_activity: {},
    workshop_test: true,
  };
  return backendFetch(buildBackendUrl("/think", { t: Date.now() }), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
    body: JSON.stringify(payload),
    signal,
    ...(isTauriRuntime ? { connectTimeout: 30_000 } : {}),
  });
}

async function consumeWorkshopThinkStream(response, assistantMessage, request) {
  let partialSpeech = "";
  for await (const event of readNdjsonEvents(response)) {
    if (!isTestRequestCurrent(request)) return;
    const type = String(event?.type || "").trim().toLowerCase();
    if (type === "speech_chunk") {
      partialSpeech += String(event.text || "");
      assistantMessage.text = partialSpeech.trim();
      assistantMessage.status = "生成中";
    } else if (type === "speech_segment") {
      const text = String(event.text || "").trim();
      if (text) {
        assistantMessage.segments.push(text);
        assistantMessage.status = "生成中";
      }
    } else if (type === "speech_reset") {
      assistantMessage.segments = [];
      partialSpeech = String(event.speech || "");
      assistantMessage.text = partialSpeech.trim();
      assistantMessage.status = "重新生成中";
    } else if (type === "ui") {
      const emotion = String(event.emotion || "").trim();
      if (emotion) assistantMessage.emotion = emotion;
    } else if (type === "final" || type === "final_ui" || type === "npc_turn") {
      applyTestPayload(event.payload || event, assistantMessage);
    } else if (type === "stream_error" || type === "error") {
      if (event.partial) applyTestPayload(event.partial, assistantMessage);
      throw new Error(String(event.message || event.error || "stream error"));
    } else if (type === "stream_end" && event.partial) {
      applyTestPayload(event.partial, assistantMessage);
    }
    updateTestMessageDiagnostics(assistantMessage);
    if (view.activeTab === "test") renderTestChatTranscript();
  }
  if (!assistantMessage.text && !assistantMessage.segments.length && partialSpeech.trim()) {
    assistantMessage.text = partialSpeech.trim();
  }
  if (isTestRequestCurrent(request)) updateTestMessageDiagnostics(assistantMessage);
}

function applyTestPayload(payload, assistantMessage) {
  if (!payload || typeof payload !== "object") return;
  const segments = normalizeResponseSegments(payload.speech_segments || payload.segments);
  if (segments.length) {
    assistantMessage.segments = segments;
    assistantMessage.text = segments.join("");
  } else {
    const speech = String(payload.speech || payload.text || "").trim();
    if (speech) {
      assistantMessage.text = speech;
      assistantMessage.segments = [];
    }
  }
  const emotion = String(payload.emotion || "").trim();
  if (emotion) assistantMessage.emotion = emotion;
}

function updateTestMessageDiagnostics(message) {
  view.testLastEmotion = String(message.emotion || view.testLastEmotion || "").trim();
  view.testLastSegments = Array.isArray(message.segments) && message.segments.length
    ? message.segments
    : splitDisplayText(message.text);
  if (els.testResponseEmotion) els.testResponseEmotion.textContent = view.testLastEmotion || "-";
  renderTestResponseSegments(view.testLastSegments);
}

async function* readNdjsonEvents(response) {
  const reader = response.body?.getReader?.();
  if (!reader) {
    const raw = await response.text();
    for (const line of raw.split(/\r?\n/)) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try { yield JSON.parse(trimmed); } catch { /* skip malformed line */ }
    }
    return;
  }

  try {
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() || "";
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        try { yield JSON.parse(trimmed); } catch { /* skip malformed line */ }
      }
    }
    buffer += decoder.decode();
    const tail = buffer.trim();
    if (tail) {
      try { yield JSON.parse(tail); } catch { /* skip malformed tail */ }
    }
  } finally {
    try { await reader.cancel(); } finally { reader.releaseLock(); }
  }
}

async function readResponseError(response, fallback) {
  try {
    const contentType = String(response.headers?.get?.("content-type") || "").toLowerCase();
    if (contentType.includes("json")) {
      const payload = await response.json();
      return String(payload?.detail || payload?.message || payload?.error || payload?.reason || payload?.status || fallback);
    }
    const text = String(await response.text()).trim();
    return text || fallback;
  } catch {
    return fallback;
  }
}

function resetTestChatSession() {
  cancelTestRequest();
  const packId = String(view.testPackId || view.activePackId || "").trim();
  if (!packId) {
    showTestChatEmpty();
    return;
  }
  delete view.testScopes[packId];
  view.testMessages = [];
  view.testLastEmotion = "";
  view.testLastSegments = [];
  ensureTestScope(packId);
  renderTestChatView(packId);
  setTestStatus("已创建新的隔离测试会话。");
}

function ensureTestScope(packId) {
  const id = sanitizeScopePart(packId || "character");
  if (!view.testScopes[packId]) {
    const suffix = `${Date.now().toString(36)}_${crypto.randomUUID().slice(0, 8)}`;
    view.testScopes[packId] = {
      sessionId: `${WORKSHOP_TEST_SCOPE_PREFIX}_${id}_${suffix}`,
      profileUserId: `${WORKSHOP_TEST_SCOPE_PREFIX}_profile_${id}_${suffix}`,
    };
  }
  return view.testScopes[packId];
}

function buildTestCurrentVisual(pack) {
  const visual = getTestVisualState(pack);
  return {
    character_pack_id: pack.id,
    emotion: visual.emotion,
    character: {
      character_pack_id: pack.id,
      outfit: visual.outfit,
      layout: visual.layout || null,
      available_emotions: visual.availableEmotions,
    },
    scene: {},
    available_emotions: visual.availableEmotions.map((item) => item.id),
  };
}

function getTestVisualState(pack) {
  const profile = pack?._rawProfile && typeof pack._rawProfile === "object" ? pack._rawProfile : {};
  const appearance = profile.appearance && typeof profile.appearance === "object" ? profile.appearance : {};
  const layoutOutfits = profile.layout?.outfits && typeof profile.layout.outfits === "object"
    ? profile.layout.outfits
    : {};
  const assets = Array.isArray(view.testAssetsByPack[pack?.id]) ? view.testAssetsByPack[pack.id] : [];
  const defaultOutfit = String(pack?.defaultOutfit || appearance.default_outfit || appearance.defaultOutfit || "").trim();
  const assetOutfit = findTestOutfitAsset(assets, defaultOutfit) || assets[0] || null;
  const firstLayoutOutfit = Object.keys(layoutOutfits)[0] || "";
  const outfit = String(defaultOutfit || assetOutfit?.id || firstLayoutOutfit || "").trim();
  const resolvedOutfitAsset = findTestOutfitAsset(assets, outfit) || assetOutfit;
  const defaultEmotion = String(
    view.testLastEmotion ||
      pack?.defaultEmotion ||
      appearance.default_emotion ||
      appearance.defaultEmotion ||
      ""
  ).trim();
  const firstAssetEmotion = String(resolvedOutfitAsset?.emotions?.[0]?.id || "").trim();
  const emotion = String(defaultEmotion || firstAssetEmotion || "normal").trim();
  const layout = layoutOutfits[outfit] || layoutOutfits[defaultOutfit] || null;
  return {
    outfit,
    emotion,
    layout: layout && typeof layout === "object" ? layout : null,
    availableEmotions: collectTestAvailableEmotions(appearance, resolvedOutfitAsset, emotion),
  };
}

function collectTestAvailableEmotions(appearance, outfitAsset, defaultEmotion) {
  const entries = [];
  const seen = new Set();
  const add = (id, name = "") => {
    const cleanId = String(id || "").trim();
    if (!cleanId || seen.has(cleanId)) return;
    seen.add(cleanId);
    entries.push({ id: cleanId, name: String(name || cleanId).trim() || cleanId });
  };

  add(defaultEmotion);
  for (const item of asArray(outfitAsset?.emotions)) {
    add(item?.id || item?.name, item?.name || item?.id);
  }
  add(appearance.default_emotion || appearance.defaultEmotion);
  add(appearance.music_emotion || appearance.musicEmotion);
  for (const id of asArray(appearance.required_emotions || appearance.requiredEmotions)) add(id);
  for (const id of asArray(appearance.recommended_emotions || appearance.recommendedEmotions)) add(id);
  return entries;
}

function findTestOutfitAsset(assets, outfitId) {
  const id = String(outfitId || "").trim();
  if (!id) return null;
  return (Array.isArray(assets) ? assets : []).find((item) => String(item?.id || "").trim() === id) || null;
}

function findTestVisualEmotionAsset(pack, visual) {
  const assets = Array.isArray(view.testAssetsByPack[pack?.id]) ? view.testAssetsByPack[pack.id] : [];
  const outfit = findTestOutfitAsset(assets, visual?.outfit) || assets[0] || null;
  const emotions = Array.isArray(outfit?.emotions) ? outfit.emotions : [];
  const id = String(visual?.emotion || "").trim();
  return emotions.find((item) => String(item?.id || "").trim() === id) || emotions[0] || null;
}

function formatTestLayoutSummary(layout) {
  const width = Number(layout?.window?.width) || 0;
  const height = Number(layout?.window?.height) || 0;
  if (width > 0 && height > 0) return `${Math.round(width)} × ${Math.round(height)}`;
  return "已保存";
}

function getEffectiveTestProfile(pack) {
  if (hasBackendPendingPersonaChanges(pack?.id)) {
    const data = pack.id === view.editingPackId ? collectFormData() : loadDraft(pack.id);
    return {
      ...(pack?._rawProfile || {}),
      identity: { ...((pack?._rawProfile || {}).identity || {}), ...data.identity },
      persona_form: { ...((pack?._rawProfile || {}).persona_form || {}), ...data.persona_form },
    };
  }
  return pack?._rawProfile || {};
}

function normalizeResponseSegments(value) {
  return asArray(value)
    .map((item) => {
      if (typeof item === "string") return item.trim();
      if (item && typeof item === "object") {
        return String(item.text || item.speech || item.content || "").trim();
      }
      return "";
    })
    .filter(Boolean);
}

function splitDisplayText(value) {
  const text = String(value || "").trim();
  if (!text) return [];
  return text
    .split(/(?<=[。！？!?])\s*/)
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(0, 4);
}

function setTestStatus(message) {
  if (els.testChatStatus) els.testChatStatus.textContent = String(message || "");
}

function updateTestChatControls() {
  const disabled = view.testRunning || !view.testPackId;
  if (els.testChatSend) els.testChatSend.disabled = disabled;
  if (els.testChatInput) els.testChatInput.disabled = view.testRunning || !view.testPackId;
  if (els.testChatClear) {
    els.testChatClear.disabled = !view.testPackId;
    els.testChatClear.textContent = view.testRunning ? "停止接收并新建会话" : "新测试会话";
  }
  if (els.testApplyPack) els.testApplyPack.disabled = disabled || !isTauriRuntime;
}

/* ------------------------------------------------------------------ */
/*  Render                                                            */
/* ------------------------------------------------------------------ */

function render() {
  const activePack = findPack(view.activePackId);
  ensureWorkspacePack();
  const workspacePack = findPack(view.workspacePackId);
  const activeName = view.activeCharacterName || getPackName(activePack) || "-";
  const workspaceName = getPackName(workspacePack) || "-";
  els.activeCharacter.textContent = activeName;
  els.activeCharacter.title = activePack?.id || "";
  if (els.editingCharacter) {
    els.editingCharacter.textContent = workspaceName;
    els.editingCharacter.title = workspacePack?.id || "";
  }
  els.activePack.textContent = workspacePack ? getPackReadinessSummary(workspacePack) : "-";
  els.activePack.title = workspacePack?.id || "";
  els.activeSession.textContent = view.activeSessionId ? "已隔离" : "-";
  els.activeSession.title = view.activeSessionId || "";
  els.summary.textContent = view.packs.length
    ? `桌宠：${activeName} · 正在编辑：${workspaceName} · ${view.packs.length} 个角色`
    : "还没有读到角色包。";
  els.packCount.textContent = String(view.packs.length);
  renderPackDetails(workspacePack);
  renderPackList();

  if (view.activeTab === "persona" && view.editingPackId !== view.workspacePackId) {
    startEditingPack(view.workspacePackId);
  }
  if (view.activeTab === "context") {
    loadContextLibrariesTab(view.workspacePackId || view.activePackId);
  }
  if (view.activeTab === "calibration" && calibrationReadyTarget) applyCalibrationPreview();
  if (view.activeTab === "test") {
    const targetId = view.workspacePackId || view.testPackId || view.activePackId;
    if (targetId) {
      if (view.testPackId !== targetId) loadTestChatTab(targetId);
      else renderTestChatView(targetId);
    } else {
      showTestChatEmpty();
    }
  }
}

function renderPackList() {
  renderFirstUsePanel();
  if (!view.packs.length) {
    els.packList.replaceChildren(buildPackListEmptyState());
    return;
  }

  els.packList.replaceChildren(
    ...view.packs.map((pack) => {
      const card = document.createElement("article");
      card.className = "pack-card";
      card.dataset.packId = pack.id;
      card.dataset.selectPack = pack.id;
      card.tabIndex = 0;
      const isActive = pack.id === view.activePackId;
      const isEditing = pack.id === view.workspacePackId;
      card.classList.toggle("active", isActive);
      card.classList.toggle("editing", isEditing);
      card.setAttribute("aria-label", `选择角色 ${getPackName(pack) || pack.id}`);
      card.setAttribute("aria-current", isEditing ? "true" : "false");

      const identity = document.createElement("div");
      identity.className = "pack-card-identity";
      const avatar = document.createElement("span");
      avatar.className = "pack-avatar";
      avatar.setAttribute("aria-hidden", "true");
      const heading = document.createElement("div");
      heading.className = "pack-card-heading";
      const name = getPackName(pack) || "未命名角色";
      avatar.textContent = Array.from(name)[0] || "角";
      const packId = document.createElement("code");
      packId.textContent = pack.id;
      const subtitle = document.createElement("span");
      subtitle.textContent = buildPackRoleLabel(pack, { isActive, isEditing });
      subtitle.className = isActive || isEditing ? "pack-row-active-note" : "";
      heading.append(buildText("strong", name), packId, subtitle);
      identity.append(avatar, heading);

      const meta = document.createElement("p");
      const issues = buildPackReadinessIssues(pack);
      meta.textContent = issues.length
        ? `还差：${issues.slice(0, 2).map((item) => item.label).join("、")} · ${buildPackMeta(pack)}`
        : buildPackMeta(pack);

      const actions = document.createElement("div");
      actions.className = "pack-card-actions";

      const editBtn = document.createElement("button");
      editBtn.type = "button";
      editBtn.className = "btn-edit-pack";
      editBtn.dataset.editPack = pack.id;
      editBtn.textContent = isEditing ? "继续编辑" : "编辑";

      const applyBtn = document.createElement("button");
      applyBtn.type = "button";
      applyBtn.dataset.applyPack = pack.id;
      applyBtn.disabled = Boolean(view.pendingApplyPackId);
      applyBtn.textContent = pack.id === view.pendingApplyPackId ? "应用中" : isActive ? "重新应用" : "应用";

      actions.append(editBtn, applyBtn);
      card.append(identity, meta, actions);
      return card;
    })
  );
}

function renderFirstUsePanel() {
  if (!els.firstUsePanel) return;
  const shouldShow = view.packs.length <= 1;
  els.firstUsePanel.hidden = !shouldShow;
  if (shouldShow) {
    els.firstUsePanel.dataset.state = view.packs.length ? "starter-pack" : "empty";
  } else {
    delete els.firstUsePanel.dataset.state;
  }
}

function buildPackListEmptyState() {
  const box = document.createElement("div");
  box.className = "pack-list-empty glass-card";
  const title = document.createElement("strong");
  title.textContent = "暂无角色包";
  const note = document.createElement("p");
  note.textContent = "可以先新建一个草稿角色，再逐步补人设、立绘和校准。";
  box.append(title, note);
  return box;
}

function renderPackDetails(pack) {
  const readiness = buildPackReadinessIssues(pack);
  if (els.detailEditPack) els.detailEditPack.disabled = !pack;
  if (els.detailTestPack) els.detailTestPack.disabled = !pack;
  if (!pack) {
    els.packDetailList.replaceChildren(buildReadinessRow({
      ok: false,
      label: "选择一个角色",
      detail: "从左侧列表选择角色后，这里会显示接下来要补什么。",
    }));
    return;
  }
  const rows = [
    { ok: true, label: "角色名称", detail: getPackName(pack) || "未命名角色" },
    { ok: Boolean(pack.defaultOutfit), label: "默认服装", detail: pack.defaultOutfit ? displayAssetName(pack.defaultOutfit) : "还没指定" },
    { ok: Boolean(pack.defaultEmotion), label: "默认表情", detail: pack.defaultEmotion ? displayAssetName(pack.defaultEmotion) : "还没指定" },
    { ok: Number(pack.assetCount || 0) > 0, label: "立绘资源", detail: pack.assetCount ? `${pack.assetCount} 个文件` : "还没有可用图片" },
    { ok: !readiness.length, label: "可用状态", detail: readiness.length ? `建议先补：${readiness.map((item) => item.label).join("、")}` : "可以继续编辑或应用到桌宠" },
  ];
  const technical = [
    ["角色包 ID", pack.id || "-"],
    ["角色 ID", pack.characterId || "-"],
    ["结构版本", pack.schemaVersion || "-"],
    ["文件来源", pack.source || "-"],
  ];
  els.packDetailList.replaceChildren(
    ...rows.map((row) => buildReadinessRow(row)),
    buildTechnicalDetails(technical)
  );
}

function selectWorkshopPack(packId) {
  const id = String(packId || "").trim();
  if (!setWorkspacePack(id)) return;
  render();
  setStatus(`已选择：${getPackName(findPack(id)) || id}`);
}

function buildPackReadinessIssues(pack) {
  if (!pack) return [];
  const issues = [];
  if (!getPackName(pack)) issues.push({ key: "name", label: "名称" });
  if (!pack.schemaVersion) issues.push({ key: "schema", label: "结构版本" });
  if (!pack.defaultOutfit) issues.push({ key: "outfit", label: "默认服装" });
  if (!pack.defaultEmotion) issues.push({ key: "emotion", label: "默认表情" });
  if (!Number(pack.assetCount || 0)) issues.push({ key: "assets", label: "立绘资源" });
  return issues;
}

/* ------------------------------------------------------------------ */
/*  Normalize                                                         */
/* ------------------------------------------------------------------ */

function normalizePacks(value) {
  return (Array.isArray(value) ? value : [])
    .map((item) => {
      const pack = item && typeof item === "object" ? item : {};
      const profile = pack.profile && typeof pack.profile === "object" ? pack.profile : {};
      const identity = profile.identity && typeof profile.identity === "object" ? profile.identity : {};
      const appearance = profile.appearance && typeof profile.appearance === "object" ? profile.appearance : {};
      return {
        id: String(pack.id || pack.packId || "").trim(),
        name: String(pack.appName || pack.name || identity.app_name || identity.appName || identity.name || "").trim(),
        characterId: String(pack.characterId || identity.id || "").trim(),
        characterName: String(pack.characterName || identity.name || "").trim(),
        schemaVersion: String(pack.schemaVersion || profile.schema_version || profile.schemaVersion || "").trim(),
        defaultOutfit: String(pack.defaultOutfit || appearance.default_outfit || appearance.defaultOutfit || "").trim(),
        defaultEmotion: String(pack.defaultEmotion || appearance.default_emotion || appearance.defaultEmotion || "").trim(),
        assetCount: Number(pack.assetCount || 0),
        source: String(pack.source || "").trim(),
        selected: false,
        /* keep the raw profile so the persona form can read it */
        _rawProfile: profile,
      };
    })
    .filter((pack) => pack.id)
    .sort((left, right) => getPackName(left).localeCompare(getPackName(right), "zh-Hans-CN"));
}

function mergeSnapshotPacks(currentPacks, snapshotPacks) {
  const merged = new Map();
  for (const pack of Array.isArray(currentPacks) ? currentPacks : []) {
    if (pack?.id) merged.set(pack.id, pack);
  }
  for (const pack of Array.isArray(snapshotPacks) ? snapshotPacks : []) {
    if (!pack?.id) continue;
    const existing = merged.get(pack.id);
    if (!existing) {
      merged.set(pack.id, pack);
      continue;
    }
    merged.set(pack.id, {
      ...existing,
      ...pack,
      selected: false,
      _rawProfile: hasProfilePayload(pack._rawProfile) ? pack._rawProfile : existing._rawProfile,
    });
  }
  return [...merged.values()]
    .filter((pack) => pack.id)
    .sort((left, right) => getPackName(left).localeCompare(getPackName(right), "zh-Hans-CN"));
}

function hasProfilePayload(profile) {
  return Boolean(profile && typeof profile === "object" && !Array.isArray(profile) && Object.keys(profile).length);
}

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
    ++workspaceVisit;
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
    ++workspaceVisit;
    invalidateCalibration();
    invalidatePortraits();
    cancelTestRequest();
  }
  view.workspacePackId = fallbackId;
  return fallbackId;
}

function buildPackRoleLabel(pack, { isActive = false, isEditing = false } = {}) {
  if (isActive && isEditing) return "桌宠正在使用 · 工坊正在编辑";
  if (isActive) return "桌宠正在使用";
  if (isEditing) return "工坊正在编辑";
  return getPackReadinessSummary(pack);
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                           */
/* ------------------------------------------------------------------ */

function getPackName(pack) {
  return String(pack?.name || pack?.characterName || pack?.characterId || "").trim();
}

function getPackReadinessSummary(pack) {
  if (!pack) return "-";
  const issues = buildPackReadinessIssues(pack).filter((item) => item.key !== "schema");
  if (!issues.length) return "素材已就绪";
  return `待补：${issues.slice(0, 2).map((item) => item.label).join("、")}`;
}

function buildPackMeta(pack) {
  const parts = [
    pack.defaultOutfit ? `服装：${displayAssetName(pack.defaultOutfit)}` : "未设默认服装",
    pack.defaultEmotion ? `表情：${displayAssetName(pack.defaultEmotion)}` : "未设默认表情",
    pack.assetCount ? `素材：${pack.assetCount} 个` : "暂无立绘素材",
  ].filter(Boolean);
  return parts.join(" · ");
}

function displayAssetName(value) {
  const text = String(value || "").trim();
  if (!text) return "-";
  const key = text.toLowerCase();
  const aliases = {
    default: "默认",
    normal: "普通",
    neutral: "普通",
    thinking: "思考",
    happy: "开心",
    shy: "害羞",
    angry: "生气",
    pout: "气鼓鼓",
    confused: "困惑",
    sleepy: "困倦",
    tired: "疲惫",
    listening: "倾听",
    music: "听歌",
    touched: "被摸头",
  };
  return aliases[key] || text;
}

function buildReadinessRow({ ok, label, detail }) {
  const row = document.createElement("div");
  row.className = `readiness-row ${ok ? "ok" : "todo"}`;

  const mark = document.createElement("span");
  mark.className = "readiness-mark";
  mark.setAttribute("aria-hidden", "true");
  mark.textContent = ok ? "✓" : "!";

  const body = document.createElement("div");
  const title = document.createElement("strong");
  const desc = document.createElement("span");
  title.textContent = label;
  desc.textContent = detail;
  body.append(title, desc);
  row.append(mark, body);
  return row;
}

function buildTechnicalDetails(rows) {
  const details = document.createElement("details");
  details.className = "technical-details";
  const summary = document.createElement("summary");
  summary.textContent = "技术信息";
  const list = document.createElement("div");
  list.className = "technical-details-list";
  list.append(
    ...rows.map(([label, value]) => {
      const row = document.createElement("p");
      row.append(buildText("span", label), buildText("code", value || "-"));
      return row;
    })
  );
  details.append(summary, list);
  return details;
}

function buildText(tagName, text) {
  const element = document.createElement(tagName);
  element.textContent = text;
  return element;
}

function shortId(value) {
  const text = String(value || "").trim();
  if (!text) return "-";
  return text.length <= 14 ? text : `${text.slice(0, 7)}...${text.slice(-5)}`;
}

function setFieldValue(el, value) {
  if (el) el.value = String(value ?? "");
}

function setStatus(message) {
  els.status.textContent = String(message || "");
}

function formatError(error) {
  return error instanceof Error ? error.message : String(error || "unknown error");
}

function stableSignature(value) {
  try {
    return JSON.stringify(value);
  } catch {
    return String(Date.now());
  }
}

function flashElement(element, className) {
  if (!element || !className) return;
  element.classList.remove(className);
  void element.offsetWidth;
  element.classList.add(className);
}

function pulsePackCard(packId) {
  const id = String(packId || "").trim();
  if (!id || !els.packList) return;
  const card = Array.from(els.packList.querySelectorAll("[data-pack-id]"))
    .find((item) => item.dataset.packId === id);
  flashElement(card, "switch-flash");
}

async function backendFetch(input, init = {}) {
  if (!isTauriRuntime) return window.fetch(input, init);
  await ensureWorkshopBackendBinding();
  const url = typeof input === "string" ? input : String(input?.url || input || "");
  const method = String(init?.method || "GET").trim().toUpperCase();
  const target = new URL(url);
  if (method === "POST" && target.pathname.startsWith("/capabilities/")) {
    const result = await invoke("backend_admin_request", {
      request: {
        url,
        body: typeof init?.body === "string" ? init.body : "{}"
      }
    });
    return new Response(String(result?.body || ""), {
      status: Number(result?.httpStatus || 502),
      headers: { "Content-Type": String(result?.contentType || "application/json") }
    });
  }
  return tauriFetch(input, init);
}

async function ensureWorkshopBackendBinding() {
  const key = `${view.instanceId}|${normalizeBackendUrl(view.backendUrl)}`;
  if (verifiedWorkshopBindingKey === key && Date.now() - verifiedWorkshopBindingAt < 3000) return;
  const result = await invoke("verify_backend_instance", {
    backendUrl: normalizeBackendUrl(view.backendUrl)
  });
  if (!result?.ok || String(result.instanceId || "") !== view.instanceId) {
    verifiedWorkshopBindingKey = "";
    verifiedWorkshopBindingAt = 0;
    const actual = String(result?.actualInstanceId || "").trim();
    throw new Error(actual
      ? `后端实例不匹配：当前 ${view.instanceId}，目标 ${actual}`
      : `无法验证实例 ${view.instanceId} 的后端身份`);
  }
  verifiedWorkshopBindingKey = key;
  verifiedWorkshopBindingAt = Date.now();
}

function buildBackendUrl(path, params = null) {
  const base = `${normalizeBackendUrl(view.backendUrl).replace(/\/+$/, "")}/`;
  const url = new URL(String(path || "/").replace(/^\/+/, ""), base);
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

function normalizeBackendUrl(value) {
  const raw = String(value || "").trim() || DEFAULT_BACKEND_URL;
  try {
    return new URL(raw).toString().replace(/\/+$/, "");
  } catch {
    return DEFAULT_BACKEND_URL;
  }
}

function sanitizeScopePart(value) {
  const raw = String(value || "").trim().toLowerCase();
  const encoded = encodeURIComponent(raw).replace(/%/g, "_").replace(/[^a-z0-9_-]+/g, "_");
  const text = encoded || raw.replace(/[^a-z0-9_-]+/g, "_");
  return text.replace(/^_+|_+$/g, "").slice(0, 64) || "character";
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function summarizeList(value) {
  const items = asArray(value).map((item) => String(item || "").trim()).filter(Boolean);
  if (!items.length) return "-";
  return items.slice(0, 4).join("、") + (items.length > 4 ? ` 等 ${items.length} 项` : "");
}

function summarizeText(value) {
  const text = String(value || "").trim();
  if (!text) return "-";
  return text.length <= 34 ? text : `${text.slice(0, 34)}...`;
}

/* JSON array <-> text input helpers */

function asKeywordsInput(value) {
  if (Array.isArray(value)) return value.join("、");
  return String(value ?? "");
}

function splitKeywords(value) {
  return String(value ?? "")
    .split(/[,，、\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function asLinesInput(value) {
  if (Array.isArray(value)) return value.join("\n");
  return String(value ?? "");
}

function splitLines(value) {
  return String(value ?? "")
    .split(/\n/)
    .map((s) => s.trim())
    .filter(Boolean);
}
