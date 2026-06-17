import { invoke } from "@tauri-apps/api/core";
import { emit, emitTo, listen } from "@tauri-apps/api/event";
import { fetch as tauriFetch } from "@tauri-apps/plugin-http";
import { getCurrentWindow } from "@tauri-apps/api/window";

import "./workshop.css";

const SETTINGS_COMMAND_EVENT = "akane-next-settings-command";
const SETTINGS_SNAPSHOT_EVENT = "akane-next-settings-snapshot";
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
  packs: [],
  activePackId: "",
  activeSessionId: "",
  activeCharacterName: "",
  backendUrl: DEFAULT_BACKEND_URL,
  profileUserId: "master",
  activeTab: "list",
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

const fieldIds = [
  "field-name",
  "field-app-name",
  "field-user-title",
  "field-self-reference",
  "field-relationship",
  "field-personality-keywords",
  "field-character-core",
  "field-behavior-style",
  "field-speaking-style",
  "field-catchphrases",
  "field-boundaries",
  "field-interaction-principles",
  "field-proactive-style",
  "field-extra-setting",
];

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
    view.draftDirty = true;
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
      loadPersonaForm(view.editingProfile);
      clearDraft(view.editingPackId);
    }
  });

  /* mark dirty on any field change */
  for (const id of fieldIds) {
    const el = els[id];
    if (el) {
      el.addEventListener("input", () => updateDraftStatus(true));
    }
  }

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
  view.activeTab = tab;
  const explicitTargetId = String(packId || "").trim();
  if (tab !== "list" && explicitTargetId) {
    setWorkspacePack(explicitTargetId);
  }

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
    autoSaveDraftSync(view.editingPackId);
  }

  setWorkspacePack(packId);
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
  if (!view.editingPackId) {
    setStatus("请先从角色列表中选择一个角色包。");
    return { ok: false, persisted: false, reason: "no-pack" };
  }
  const data = collectFormData();

  /* try Tauri backend save first; fall back to localStorage */
  if (isTauriRuntime) {
    try {
      const result = await invoke("save_character_pack", {
        request: {
          packId: view.editingPackId,
          identity: data.identity,
          personaForm: data.persona_form,
          dialogue: data.dialogue,
        },
      });
      view.editingPackId = String(result?.id || view.editingPackId).trim();

      /* update packs list + editing profile with latest data from disk */
      const idx = view.packs.findIndex((p) => p.id === view.editingPackId);
      if (idx >= 0 && result) {
        view.packs[idx] = normalizePacks([result])[0] || view.packs[idx];
        view.packs[idx]._rawProfile = result.profile || view.packs[idx]._rawProfile;
      }
      if (result?.profile) {
        view.editingProfile = result.profile;
      }

      view.draftDirty = false;
      clearDraft(view.editingPackId);
      updateDraftStatus(false);
      setStatus("已保存到角色包文件。");
      flashElement(els.draftStatus, "save-flash");
      return { ok: true, persisted: true, source: "file", packId: view.editingPackId };
    } catch (error) {
      if (!allowLocalFallback) {
        const message = formatError(error);
        setStatus(`文件保存失败：${message}`);
        return { ok: false, persisted: false, source: "file", error: message };
      }
      /* backend save failed – fall back to localStorage */
      setStatus(`文件保存失败，已保存到本地草稿：${formatError(error)}`);
    }
  }

  if (!allowLocalFallback) {
    setStatus("文件保存不可用，未写入角色包文件。");
    return { ok: false, persisted: false, source: "file", reason: "file-save-unavailable" };
  }

  /* localStorage fallback */
  try {
    if (!persistDraft(view.editingPackId, data)) {
      throw new Error("localStorage unavailable");
    }
    view.draftDirty = false;
    updateDraftStatus(false);
    setStatus("草稿已保存。");
    flashElement(els.draftStatus, "save-flash");
    return { ok: true, persisted: false, source: "localStorage", packId: view.editingPackId };
  } catch (error) {
    const message = formatError(error);
    setStatus(`保存失败：${message}`);
    return { ok: false, persisted: false, source: "localStorage", error: message };
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
  if (!packId || !view.draftDirty) return;
  const data = collectFormData();
  if (persistDraft(packId, data)) {
    view.draftDirty = false;
  }
}

/* ------------------------------------------------------------------ */
/*  Draft persistence (localStorage)                                   */
/* ------------------------------------------------------------------ */

function draftKey(packId) {
  return `${DRAFT_STORAGE_PREFIX}${packId}`;
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
    localStorage.setItem(draftKey(packId), JSON.stringify(payload));
    return true;
  } catch {
    return false;
  }
}

function loadDraft(packId) {
  try {
    const raw = localStorage.getItem(draftKey(packId));
    if (!raw) return null;
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function clearDraft(packId) {
  try {
    localStorage.removeItem(draftKey(packId));
  } catch {
    /* ignore */
  }
}

function updateDraftStatus(dirty) {
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
  return Boolean(id && view.editingPackId === id && (view.draftDirty || hasDraft(id)));
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
  if (!isTauriRuntime) return;
  setStatus("正在刷新角色包。");
  try {
    const [packs, petState] = await Promise.all([
      invoke("list_character_packs"),
      invoke("load_pet_state"),
    ]);
    view.packs = normalizePacks(packs);
    view.activePackId = String(petState?.characterPackId || view.activePackId || "").trim();
    ensureWorkspacePack();
    view.activeSessionId = String(petState?.sessionId || view.activeSessionId || "").trim();
    view.backendUrl = normalizeBackendUrl(petState?.backendUrl || view.backendUrl);
    view.profileUserId = String(petState?.profileUserId || view.profileUserId || "master").trim() || "master";
    view.activeCharacterName = getPackName(findPack(view.activePackId)) || view.activeCharacterName;
    void refreshPortraitCutoutCapability({ force: true });
    render();
    setStatus("角色包已刷新。");
  } catch (error) {
    setStatus(`刷新失败：${formatError(error)}`);
  }
}

function applySnapshot(snapshot) {
  if (!snapshot || typeof snapshot !== "object") return;
  const state = snapshot.state || {};
  const character = snapshot.character || {};
  const signature = buildWorkshopSnapshotSignature(state, character);
  if (signature === lastWorkshopSnapshotSignature) {
    return;
  }
  lastWorkshopSnapshotSignature = signature;
  view.activePackId = String(state.characterPackId || character.packId || view.activePackId || "").trim();
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
    activePackId: String(state.characterPackId || character.packId || "").trim(),
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
  if (view.pendingApplyPackId) {
    setStatus(`正在等待 ${getPackName(findPack(view.pendingApplyPackId)) || view.pendingApplyPackId} 完成切换。`);
    return { ok: false, packId, reason: "apply-pending" };
  }
  setStatus(`正在应用：${getPackName(pack) || packId}`);
  view.pendingApplyPackId = packId;
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
  }
}

async function importPack() {
  if (!isTauriRuntime) { setStatus("浏览器预览模式不支持导入。"); return; }
  const fileInput = document.createElement("input");
  fileInput.type = "file";
  fileInput.accept = ".zip";
  fileInput.onchange = async () => {
    const file = fileInput.files?.[0];
    if (!file) return;
    if (Number(file.size || 0) > MAX_CHARACTER_PACK_ZIP_BYTES) {
      setStatus("角色包 zip 暂时请控制在 300 MB 以内。");
      return;
    }
    setStatus(`正在导入：${file.name}…`);
    try {
      await yieldToUiForLargeFileRead();
      const bytes = new Uint8Array(await file.arrayBuffer());
      const result = await invoke("install_character_pack_zip_bytes", {
        fileName: file.name,
        bytes: Array.from(bytes),
        overwrite: false,
      });
      setStatus(`已导入：${result?.characterName || result?.packId || file.name}。`);
      await refreshPacks();
    } catch (error) {
      setStatus(`导入失败：${formatError(error)}`);
    }
  };
  fileInput.click();
}

async function exportPack() {
  const packId = view.workspacePackId || view.activePackId;
  if (!packId) { setStatus("请先选择一个角色包。"); return; }
  if (!isTauriRuntime) { setStatus("浏览器预览模式不支持导出。"); return; }
  setStatus(`正在导出：${packId}…`);
  try {
    const result = await invoke("export_character_pack", { packId });
    setStatus(`已导出到桌面：${result?.fileName || packId}.zip`);
    /* try to open the containing folder */
    if (result?.path) {
      void invoke("show_item_in_folder", { path: result.path }).catch(() => {});
    }
  } catch (error) {
    setStatus(`导出失败：${formatError(error)}`);
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
  els.createPackId.value = "";
  els.createName.value = "";
  els.createAppName.value = "";
  els.createUserTitle.value = "";
  els.createError.hidden = true;
  els.createError.textContent = "";
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
  try {
    const result = await invoke("create_character_pack", {
      request: {
        packId,
        name,
        appName,
        userTitle,
      },
    });
    els.createDialog.close();
    setStatus(`已创建角色包：${name}（${packId}）。`);
    await refreshPacks();
    /* switch to editing the new pack */
    if (result?.id) {
      switchTab("persona", result.id);
    }
  } catch (error) {
    showCreateError(formatError(error));
    setStatus(`创建失败：${formatError(error)}`);
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
  setFieldValue(els.contextLibraryName, "");
  setFieldValue(els.contextLibraryFolder, "");
  setFieldValue(els.contextLibraryDescription, "");
  setFieldValue(els.contextLibraryLoadWhen, "");
  showContextLibraryError("");
  els.contextLibraryDialog?.showModal();
}

function showContextLibraryError(message) {
  if (!els.contextLibraryError) return;
  els.contextLibraryError.textContent = String(message || "");
  els.contextLibraryError.hidden = !message;
  if (message) flashElement(els.contextLibraryForm, "invalid-flash");
}

async function createContextLibrary() {
  const packId = String(view.workspacePackId || view.activePackId || "").trim();
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

  if (els.contextLibraryStatus) {
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
    const normalized = normalizePacks([result])[0];
    const index = view.packs.findIndex((pack) => pack.id === packId);
    if (index >= 0 && normalized) view.packs[index] = normalized;
    if (view.editingPackId === packId && result?.profile) {
      view.editingProfile = result.profile;
    }
    els.contextLibraryDialog?.close();
    renderContextLibraries(normalized || findPack(packId));
    if (els.contextLibraryStatus) {
      els.contextLibraryStatus.textContent = `已创建“${name}”。放入 .md 文件后，模型会在提示词中看到它的用途和读取时机。`;
    }
    setStatus(`已创建角色资料库：${name}`);
  } catch (error) {
    const message = formatError(error);
    showContextLibraryError(message);
    if (els.contextLibraryStatus) {
      els.contextLibraryStatus.textContent = `创建失败：${message}`;
    }
  }
}

/* ------------------------------------------------------------------ */
/*  Portrait management                                               */
/* ------------------------------------------------------------------ */

async function loadPortraitsTab(packId) {
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
  void refreshPortraitCutoutCapability();
  try {
    const outfits = await invoke("list_pack_assets", { packId });
    renderPortraitsView(packId, outfits);
  } catch (error) {
    setStatus(`读取立绘失败：${formatError(error)}`);
    setPortraitStatus(`读取失败：${formatError(error)}`, true);
    showPortraitsEmpty();
  }
}

function showPortraitsEmpty() {
  if (els.portraitsContent) els.portraitsContent.hidden = true;
  if (els.portraitsEmptyState) els.portraitsEmptyState.hidden = false;
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
  cutoutCapabilityRequest = (async () => {
    try {
      const payload = await readWorkflowCatalog();
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
      cutoutCapabilityRequest = null;
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
    const canRun = status === "ready" && state.executionReady && !view.portraitCutoutRunning;
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
  if (!isTauriRuntime) {
    setPortraitStatus("浏览器预览模式无法执行自动抠图。", true);
    return;
  }
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

  const capability = await refreshPortraitCutoutCapability({ force: true, silent: true });
  if (capability.status !== "ready" || !capability.executionReady) {
    setPortraitStatus("自动抠图还没准备好，请先在能力页完成 ComfyUI 和工作流绑定。", true);
    return;
  }

  view.portraitCutoutRunning = true;
  renderPortraitCutoutStatus();
  const generatedEmotion = buildGeneratedCutoutEmotionId(emotionId);
  const outputHandle = `portrait_cutout_${Date.now().toString(36)}`;
  setPortraitStatus(`正在处理：${outfitId} / ${emotionId}…`);
  try {
    const imageBytes = await readPortraitImageBytes(packId, outfitId, emotionId);
    const mimeType = portraitMimeType(emotion?.path);
    const job = await startPortraitCutoutJob({
      inputImageHandle: "portrait_source",
      outputImageHandle: outputHandle,
      imageBytes,
      mimeType,
    });
    const completed = await waitForPortraitCutoutJob(job.jobId);
    const output = Array.isArray(completed?.job?.outputs) ? completed.job.outputs[0] : null;
    const outputBytes = await fetchPortraitCutoutOutput(job.jobId, output?.handle || outputHandle);
    await invoke("import_generated_portrait_image", {
      packId,
      outfit: outfitId,
      emotion: generatedEmotion,
      imageBytes: Array.from(outputBytes),
      mimeType: output?.contentType || "image/png",
      overwrite: false,
    });
    clearPortraitImageCache(packId);
    await loadPortraitsTab(packId);
    setPortraitStatus(`已生成透明背景版本：${outfitId} / ${generatedEmotion}`);
    setStatus(`自动抠图完成：${generatedEmotion}`);
  } catch (error) {
    const message = `自动抠图失败：${formatPortraitCutoutError(error)}`;
    setPortraitStatus(message, true);
    setStatus(message);
  } finally {
    view.portraitCutoutRunning = false;
    renderPortraitCutoutStatus();
  }
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

let previewingEmotion = null;
const portraitImageUrlCache = new Map();

function previewEmotion(packId, outfitId, emotion) {
  if (!els.emotionPreview) return;
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

async function setDefaultPortrait(packId, outfitId, emotionId) {
  if (!packId || !isTauriRuntime) return;
  setPortraitStatus(`正在设置默认立绘：${outfitId} / ${emotionId}…`);
  try {
    await invoke("set_default_portrait", {
      packId,
      outfit: outfitId,
      emotion: emotionId,
    });
    const pack = findPack(packId);
    if (pack?._rawProfile?.appearance) {
      pack._rawProfile.appearance.default_outfit = outfitId;
      pack._rawProfile.appearance.default_emotion = emotionId;
    }
    setStatus(`默认立绘已设为：${outfitId} / ${emotionId}`);
    await loadPortraitsTab(packId);
    setPortraitStatus(`默认立绘已设为：${outfitId} / ${emotionId}`);
  } catch (error) {
    setStatus(`设置默认立绘失败：${formatError(error)}`);
    setPortraitStatus(`设置默认立绘失败：${formatError(error)}`, true);
  }
}

async function setAppearanceField(packId, field, value) {
  if (!packId || !isTauriRuntime) return;
  try {
    await invoke("set_default_emotion", { packId, field, value });
    setStatus(`已更新：${field} → ${value}`);
    /* update local pack profile cache */
    const pack = findPack(packId);
    if (pack?._rawProfile?.appearance) {
      pack._rawProfile.appearance[field] = value;
    }
    await loadPortraitsTab(packId);
  } catch (error) {
    setStatus(`更新失败：${formatError(error)}`);
    setPortraitStatus(`更新失败：${formatError(error)}`, true);
  }
}

async function deleteEmotionConfirm(packId, outfitId, emotionId) {
  if (!confirm(`确定删除表情 "${emotionId}"？此操作不可恢复。`)) return;
  setStatus(`正在删除表情：${emotionId}…`);
  try {
    await invoke("delete_portrait_image", { packId, outfit: outfitId, emotion: emotionId });
    clearPortraitImageCache(packId);
    setStatus(`已删除表情：${emotionId}`);
    await loadPortraitsTab(packId);
  } catch (error) {
    setStatus(`删除失败：${formatError(error)}`);
  }
}

async function renameEmotionDialog(packId, outfitId, oldId) {
  const newId = prompt(`重命名表情 "${oldId}" 为：`, oldId);
  if (!newId || !newId.trim() || newId.trim() === oldId) return;
  setStatus(`正在重命名：${oldId} → ${newId.trim()}…`);
  try {
    await invoke("rename_portrait_emotion", { packId, outfit: outfitId, oldEmotion: oldId, newEmotion: newId.trim() });
    clearPortraitImageCache(packId);
    setStatus(`已重命名：${newId.trim()}`);
    await loadPortraitsTab(packId);
  } catch (error) {
    setStatus(`重命名失败：${formatError(error)}`);
  }
}

async function renameOutfitDialog(packId, oldId) {
  const newId = prompt(`重命名服装 "${oldId}" 为：`, oldId);
  if (!newId || !newId.trim() || newId.trim() === oldId) return;
  setStatus(`正在重命名服装：${oldId} → ${newId.trim()}…`);
  try {
    await invoke("rename_portrait_outfit", { packId, oldOutfit: oldId, newOutfit: newId.trim() });
    clearPortraitImageCache(packId);
    setStatus(`已重命名服装：${newId.trim()}`);
    await loadPortraitsTab(packId);
  } catch (error) {
    setStatus(`重命名失败：${formatError(error)}`);
  }
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
  if (!isTauriRuntime) return;

  setPortraitStatus(`请选择要导入到“${outfitId}”的图片。`);
  const files = await chooseLocalFiles({
    accept: "image/png,image/jpeg,image/webp",
    multiple: true,
  });
  if (!files.length) {
    setPortraitStatus(`未选择图片，“${outfitId}”没有发生变化。`);
    return;
  }

  const existingEmotionIds = new Set(options.existingEmotionIds || []);
  const fallbackEmotion = String(options.defaultEmotion || "normal").trim() || "normal";
  let successCount = 0;
  let overwriteCount = 0;
  const failures = [];

  setStatus(`正在导入 ${files.length} 张表情图到 ${outfitId}…`);
  setPortraitStatus(`正在导入 ${files.length} 张图片到“${outfitId}”…`);
  for (const file of files) {
    const emotion = inferEmotionNameFromFileName(file.name, fallbackEmotion);
    try {
      if (Number(file.size || 0) > MAX_PORTRAIT_IMAGE_BYTES) {
        failures.push(`${file.name}: 图片大小不能超过 20 MB。`);
        continue;
      }
      await yieldToUiForLargeFileRead();
      const bytes = new Uint8Array(await file.arrayBuffer());
      await invoke("upload_portrait_image", {
        packId,
        outfit: outfitId,
        emotion,
        imageBytes: Array.from(bytes),
      });
      if (existingEmotionIds.has(emotion)) overwriteCount += 1;
      successCount += 1;
    } catch (error) {
      failures.push(`${file.name}: ${formatError(error)}`);
    }
  }

  clearPortraitImageCache(packId);
  await loadPortraitsTab(packId);
  if (failures.length) {
    const message = `导入完成：成功 ${successCount} 张，覆盖 ${overwriteCount} 张，失败 ${failures.length} 张。${failures.slice(0, 2).join("；")}`;
    setStatus(message);
    setPortraitStatus(message, true);
    return;
  }
  const message = `已导入 ${successCount} 张图片到“${outfitId}”${overwriteCount ? `，其中覆盖 ${overwriteCount} 张` : ""}。`;
  setStatus(message);
  setPortraitStatus(message);
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
    setStatus("请先填写新服装名。");
    setPortraitStatus("请先填写新服装名。", true);
    els.newOutfitId?.focus();
    flashElement(els.newOutfitId, "invalid-flash");
    return;
  }
  setStatus(`正在创建服装：${outfit}…`);
  setPortraitStatus(`正在创建服装目录“${outfit}”…`);
  try {
    await invoke("create_portrait_outfit", { packId, outfit });
    els.newOutfitId.value = "";
    setStatus(`已创建服装：${outfit}`);
    await loadPortraitsTab(packId);
    setPortraitStatus(`已创建服装“${outfit}”。现在可以在它的卡片中导入图片。`);
  } catch (error) {
    setStatus(`创建服装失败：${formatError(error)}`);
    setPortraitStatus(`创建服装失败：${formatError(error)}`, true);
  }
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
  if (els.calibrationContent) els.calibrationContent.hidden = true;
  if (els.calibrationEmptyState) els.calibrationEmptyState.hidden = false;
}

async function loadCalibrationTab(packId) {
  const pack = findPack(packId);
  if (!pack) { showCalibrationEmpty(); return; }

  setStatus(`正在加载校准数据：${getPackName(pack)}`);
  try {
    const outfits = await invoke("list_pack_assets", { packId });
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
    await loadCalibrationOutfitPreview(packId, defaultOutfit, profile);

    /* outfit switch */
    els.calibrationOutfitSelect.onchange = async () => {
      const sel = els.calibrationOutfitSelect.value;
      const outfit = items.find((o) => o.id === sel);
      if (outfit) await loadCalibrationOutfitPreview(packId, outfit, profile);
    };

    /* slider bindings */
    bindCalibrationSliders();

    /* auto layout button */
    els.calibrationAutoBtn.onclick = () => applyAutoLayout();

    /* save button */
    els.calibrationSaveBtn.onclick = () => saveCalibration(packId);

  } catch (error) {
    setStatus(`加载校准失败：${formatError(error)}`);
    showCalibrationEmpty();
  }
}

async function loadCalibrationOutfitPreview(packId, outfit, profile) {
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

  const token = ++calibrationImageToken;
  setStatus(`正在加载校准预览：${outfit.name || outfit.id} / ${emotion.name || emotion.id}`);
  try {
    const url = await getPortraitImageUrl(packId, outfit.id, emotion);
    if (token !== calibrationImageToken) return false;
    els.calibrationImage.onload = () => {
      if (token !== calibrationImageToken) return;
      loadLayoutForOutfit(packId, outfit.id);
      setStatus(`正在校准：${outfit.name || outfit.id} / ${emotion.name || emotion.id}`);
    };
    els.calibrationImage.onerror = () => {
      if (token !== calibrationImageToken) return;
      setStatus(`校准预览加载失败：${outfit.name || outfit.id} / ${emotion.name || emotion.id}`);
    };
    els.calibrationImage.src = url;
    return true;
  } catch (error) {
    if (token === calibrationImageToken) {
      els.calibrationImage.removeAttribute("src");
      setStatus(`校准预览加载失败：${formatError(error)}`);
    }
    return false;
  }
}

function loadLayoutForOutfit(packId, outfitId) {
  const pack = findPack(packId);
  const profile = pack?._rawProfile || {};
  const outfitLayout = profile?.layout?.outfits?.[outfitId] || null;

  if (outfitLayout) {
    setCalibrationSlider("cal-win-w", outfitLayout.window?.width || 340);
    setCalibrationSlider("cal-win-h", outfitLayout.window?.height || 560);
    setCalibrationSlider("cal-scale", Math.round((outfitLayout.portrait?.scale || 1) * 100));
    setCalibrationSlider("cal-offset-x", outfitLayout.portrait?.offset_x || 0);
    setCalibrationSlider("cal-offset-y", outfitLayout.portrait?.offset_y || 0);
    setCalibrationSlider("cal-bubble-x", Math.round(readUnitValue(outfitLayout.bubble?.anchor_x, 0.5) * 100));
    setCalibrationSlider("cal-bubble-y", Math.round(readUnitValue(outfitLayout.bubble?.anchor_y, 0.12) * 100));
    setCalibrationBubbleStyle(outfitLayout.bubble?.style || outfitLayout.bubble?.theme || DEFAULT_BUBBLE_STYLE);
  } else {
    applyAutoLayout();
  }
}

function applyAutoLayout() {
  const img = els.calibrationImage;
  if (!img?.naturalWidth) return;

  const aspect = img.naturalWidth / img.naturalHeight;
  let winW = Math.round(340 * aspect);
  let winH = 560;
  if (winW < 240) { winW = 240; winH = Math.round(240 / aspect); }
  if (winW > 600) { winW = 600; winH = Math.round(600 / aspect); }
  if (winH < 260) winH = 260;
  if (winH > 800) winH = 800;

  setCalibrationSlider("cal-win-w", winW);
  setCalibrationSlider("cal-win-h", winH);
  setCalibrationSlider("cal-scale", 100);
  setCalibrationSlider("cal-offset-x", 0);
  setCalibrationSlider("cal-offset-y", 0);
  setCalibrationSlider("cal-bubble-x", 50);
  setCalibrationSlider("cal-bubble-y", 12);
  setCalibrationBubbleStyle(DEFAULT_BUBBLE_STYLE);

  setStatus("已应用自动布局。");
}

function setCalibrationSlider(id, value) {
  const el = els[id];
  if (!el) return;
  el.value = value;
  applyCalibrationPreview();
}

let calibrationSlidersBound = false;
let calibrationImageToken = 0;

function bindCalibrationSliders() {
  if (calibrationSlidersBound) return;
  calibrationSlidersBound = true;
  const ids = ["cal-win-w", "cal-win-h", "cal-scale", "cal-offset-x", "cal-offset-y", "cal-bubble-x", "cal-bubble-y"];
  for (const id of ids) {
    const el = els[id];
    if (!el) continue;
    el.addEventListener("input", () => applyCalibrationPreview());
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
    event.preventDefault();
    bubble.setPointerCapture?.(event.pointerId);
    bubble.dataset.dragging = "true";
    setBubbleAnchorFromPointer(event);
  });
  bubble.addEventListener("pointermove", (event) => {
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
    const step = event.shiftKey ? 5 : 1;
    if (event.key === "ArrowLeft") adjustBubbleAnchor(-step, 0, event);
    if (event.key === "ArrowRight") adjustBubbleAnchor(step, 0, event);
    if (event.key === "ArrowUp") adjustBubbleAnchor(0, -step, event);
    if (event.key === "ArrowDown") adjustBubbleAnchor(0, step, event);
  });
}

function applyCalibrationPreview() {
  const winW = Number(els.calWinW?.value) || 340;
  const winH = Number(els.calWinH?.value) || 560;
  const scale = (Number(els.calScale?.value) || 100) / 100;
  const offX = Number(els.calOffsetX?.value) || 0;
  const offY = Number(els.calOffsetY?.value) || 0;
  const bubbleX = readSliderUnit(els.calBubbleX, 0.5);
  const bubbleY = readSliderUnit(els.calBubbleY, 0.12);
  const bubbleStyle = getCalibrationBubbleStyle();

  /* update frame size */
  if (els.calibrationFrame) {
    els.calibrationFrame.style.width = `${Math.min(winW, 600)}px`;
    els.calibrationFrame.style.height = `${Math.min(winH, 500)}px`;
    els.calibrationFrame.dataset.bubbleStyle = bubbleStyle;
  }

  /* update portrait */
  if (els.calibrationPortrait) {
    els.calibrationPortrait.style.transform = `translate(${offX}px, ${offY}px) scale(${scale})`;
  }

  /* update bubble dot */
  if (els.calibrationBubbleDot) {
    els.calibrationBubbleDot.style.left = `${bubbleX * 100}%`;
    els.calibrationBubbleDot.style.top = `${bubbleY * 100}%`;
    els.calibrationBubbleDot.dataset.bubbleStyle = bubbleStyle;
  }

  /* update value labels */
  updateCalLabel("cal-win-w-val", `${winW}`);
  updateCalLabel("cal-win-h-val", `${winH}`);
  updateCalLabel("cal-scale-val", scale.toFixed(2));
  updateCalLabel("cal-offset-x-val", `${offX}`);
  updateCalLabel("cal-offset-y-val", `${offY}`);
  updateCalLabel("cal-bubble-x-val", bubbleX.toFixed(2));
  updateCalLabel("cal-bubble-y-val", bubbleY.toFixed(2));
  updateCalLabel("cal-bubble-style-val", BUBBLE_STYLE_LABELS[bubbleStyle] || BUBBLE_STYLE_LABELS[DEFAULT_BUBBLE_STYLE]);
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
  const outfitId = els.calibrationOutfitSelect?.value || "default";
  const layout = {
    window: {
      width: Number(els.calWinW?.value) || 340,
      height: Number(els.calWinH?.value) || 560,
    },
    portrait: {
      scale: (Number(els.calScale?.value) || 100) / 100,
      offset_x: Number(els.calOffsetX?.value) || 0,
      offset_y: Number(els.calOffsetY?.value) || 0,
      fit: "contain",
      anchor: "bottom_center",
    },
    bubble: {
      anchor_x: readSliderUnit(els.calBubbleX, 0.5),
      anchor_y: readSliderUnit(els.calBubbleY, 0.12),
      max_width: 300,
      style: getCalibrationBubbleStyle(),
    },
  };

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
    setStatus(`校准已保存（${outfitId}）。`);
  } catch (error) {
    setStatus(`保存校准失败：${formatError(error)}`);
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
    view.testMessages = [];
    view.testLastEmotion = "";
    view.testLastSegments = [];
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
  view.testPackId = "";
  if (els.testChatContent) els.testChatContent.hidden = true;
  if (els.testChatEmptyState) els.testChatEmptyState.hidden = false;
  renderTestDiagnostics(null, null);
  updateTestChatControls();
}

function renderTestChatView(packId = view.testPackId) {
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
  try {
    const items = await invoke("list_pack_assets", { packId });
    view.testAssetsByPack[packId] = Array.isArray(items) ? items : [];
  } catch {
    view.testAssetsByPack[packId] = [];
  }
  if (view.activeTab === "test" && view.testPackId === packId) {
    renderTestChatView(packId);
  }
}

function renderTestVisualPreview(pack, visual = null) {
  if (!els.testVisualPreview) return;
  if (!pack) {
    els.testVisualPreview.textContent = "-";
    return;
  }

  const state = visual || getTestVisualState(pack);
  const emotion = findTestVisualEmotionAsset(pack, state);
  const layout = state.layout || {};
  const winW = Math.max(200, Number(layout.window?.width) || 340);
  const winH = Math.max(200, Number(layout.window?.height) || 520);
  const offsetX = Number(layout.portrait?.offset_x) || 0;
  const offsetY = Number(layout.portrait?.offset_y) || 0;
  const portraitScale = Number(layout.portrait?.scale) || 1;
  const bubbleX = readUnitValue(layout.bubble?.anchor_x, 0.5);
  const bubbleY = readUnitValue(layout.bubble?.anchor_y, 0.12);
  const bubbleStyle = normalizeBubbleStyle(layout.bubble?.style || layout.bubble?.theme);

  const stage = document.createElement("div");
  stage.className = "test-visual-stage";
  stage.dataset.bubbleStyle = bubbleStyle;
  stage.style.aspectRatio = `${winW} / ${winH}`;
  stage.style.setProperty("--test-offset-x", `${(offsetX / winW) * 100}%`);
  stage.style.setProperty("--test-offset-y", `${(offsetY / winH) * 100}%`);
  stage.style.setProperty("--test-scale", String(portraitScale));
  stage.style.setProperty("--test-bubble-x", `${Math.min(1, Math.max(0, bubbleX)) * 100}%`);
  stage.style.setProperty("--test-bubble-y", `${Math.min(1, Math.max(0, bubbleY)) * 100}%`);

  const portrait = document.createElement("div");
  portrait.className = "test-visual-portrait";
  if (emotion?.id) {
    const img = document.createElement("img");
    img.alt = emotion.name || emotion.id || state.emotion || "portrait";
    portrait.appendChild(img);
    void loadPortraitImage(img, pack.id, state.outfit, emotion, "test-visual-image-error");
  } else {
    const missing = document.createElement("span");
    missing.textContent = "未找到默认立绘资源";
    portrait.appendChild(missing);
  }

  const bubble = document.createElement("div");
  bubble.className = "test-visual-bubble";
  bubble.title = "气泡锚点";
  bubble.textContent = "示例";
  stage.append(portrait, bubble);

  const info = document.createElement("p");
  info.className = "test-visual-info";
  info.textContent = `${state.outfit || "-"} / ${state.emotion || "-"} · ${state.layout ? "已加载校准" : "未保存校准"}`;

  els.testVisualPreview.replaceChildren(stage, info);
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
      return item;
    })
  );
  els.testChatLog.scrollTop = els.testChatLog.scrollHeight;
}

async function applyTestPackToDesktop() {
  const packId = String(view.testPackId || view.activePackId || "").trim();
  const pack = findPack(packId);
  if (!pack) {
    setTestStatus("请先选择一个角色包。");
    return;
  }
  if (!isTauriRuntime) {
    setTestStatus("浏览器预览模式不支持应用到桌宠。");
    return;
  }
  if (view.testRunning) return;

  if (hasBackendPendingPersonaChanges(packId)) {
    setTestStatus("正在保存当前人设字段。");
    const saveResult = await saveDraft({ allowLocalFallback: false });
    if (!saveResult?.ok) {
      setTestStatus("应用已暂停：当前人设没有写入角色包文件。");
      return;
    }
  }

  setTestStatus(`正在应用到桌宠：${getPackName(pack) || packId}`);
  const result = await applyPack(packId);
  if (result?.ok) {
    setTestStatus(`已应用到桌宠：${getPackName(pack) || packId}`);
  } else {
    setTestStatus(`应用失败：${result?.error || "unknown error"}`);
  }
}

async function sendTestChatMessage() {
  const packId = String(view.testPackId || view.activePackId || "").trim();
  const pack = findPack(packId);
  const text = String(els.testChatInput?.value || "").trim();
  if (!pack) {
    setTestStatus("请先选择一个角色包。");
    return;
  }
  if (!text) {
    setTestStatus("请输入测试内容。");
    return;
  }
  if (view.testRunning) return;

  if (hasBackendPendingPersonaChanges(packId)) {
    setTestStatus("正在保存当前人设字段。");
    const saveResult = await saveDraft({ allowLocalFallback: false });
    if (!saveResult?.ok) {
      setTestStatus("测试已暂停：当前人设没有写入角色包文件。");
      return;
    }
  }

  const scope = ensureTestScope(packId);
  const requestPack = findPack(packId) || pack;
  view.testMessages.push({ role: "user", text, status: "已发送" });
  const assistantMessage = {
    role: "assistant",
    text: "",
    segments: [],
    emotion: "",
    status: "等待回复",
  };
  view.testMessages.push(assistantMessage);
  view.testRunning = true;
  view.testLastEmotion = "";
  view.testLastSegments = [];
  els.testChatInput.value = "";
  renderTestChatView(packId);
  setTestStatus("正在请求 /think。");

  try {
    const response = await requestWorkshopThink({ pack: requestPack, scope, message: text });
    if (!response.ok) {
      throw new Error(await readResponseError(response, `HTTP ${response.status}`));
    }
    await consumeWorkshopThinkStream(response, assistantMessage);
    if (!assistantMessage.text && !assistantMessage.segments.length) {
      throw new Error("未收到回复内容");
    }
    assistantMessage.status = "完成";
    setTestStatus("测试回复完成。");
  } catch (error) {
    assistantMessage.status = "失败";
    assistantMessage.text = `请求失败：${formatError(error)}`;
    setTestStatus(`测试失败：${formatError(error)}`);
  } finally {
    view.testRunning = false;
    renderTestChatView(packId);
  }
}

async function requestWorkshopThink({ pack, scope, message }) {
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
    ...(isTauriRuntime ? { connectTimeout: 30_000 } : {}),
  });
}

async function consumeWorkshopThinkStream(response, assistantMessage) {
  let partialSpeech = "";
  for await (const event of readNdjsonEvents(response)) {
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
    renderTestChatTranscript();
  }
  if (!assistantMessage.text && !assistantMessage.segments.length && partialSpeech.trim()) {
    assistantMessage.text = partialSpeech.trim();
  }
  updateTestMessageDiagnostics(assistantMessage);
}

function applyTestPayload(payload, assistantMessage) {
  if (!payload || typeof payload !== "object") return;
  const segments = normalizeResponseSegments(payload.speech_segments || payload.segments);
  if (segments.length) {
    assistantMessage.segments = segments;
    assistantMessage.text = segments.join("");
  } else {
    const speech = String(payload.speech || payload.text || "").trim();
    if (speech) assistantMessage.text = speech;
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
    const suffix = Date.now().toString(36);
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
    const data = collectFormData();
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
  if (els.testChatClear) els.testChatClear.disabled = view.testRunning || !view.testPackId;
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
  if (view.activeTab === "test") {
    const targetId = view.workspacePackId || view.testPackId || view.activePackId;
    if (targetId) {
      view.testPackId = targetId;
      renderTestChatView(targetId);
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
      const isActive = pack.id === view.activePackId;
      const isEditing = pack.id === view.workspacePackId;
      card.classList.toggle("active", isActive);
      card.classList.toggle("editing", isEditing);

      const heading = document.createElement("div");
      heading.className = "pack-card-heading";
      const name = getPackName(pack) || "未命名角色";
      const subtitle = document.createElement("span");
      subtitle.textContent = buildPackRoleLabel(pack, { isActive, isEditing });
      subtitle.className = isActive || isEditing ? "pack-row-active-note" : "";
      heading.append(buildText("strong", name), subtitle);

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
      card.append(heading, meta, actions);
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
  view.workspacePackId = id;
  return true;
}

function ensureWorkspacePack() {
  if (findPack(view.workspacePackId)) return view.workspacePackId;
  const fallbackId = findPack(view.activePackId)?.id || view.packs[0]?.id || "";
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

function backendFetch(input, init) {
  if (isTauriRuntime) {
    return tauriFetch(input, init);
  }
  return window.fetch(input, init);
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
