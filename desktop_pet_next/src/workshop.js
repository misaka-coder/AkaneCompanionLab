import { convertFileSrc, invoke } from "@tauri-apps/api/core";
import { emit, listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";

import "./workshop.css";

const SETTINGS_COMMAND_EVENT = "akane-next-settings-command";
const SETTINGS_SNAPSHOT_EVENT = "akane-next-settings-snapshot";
const DRAFT_STORAGE_PREFIX = "akane-workshop-draft:";

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
  activePack: document.querySelector("#active-pack"),
  activeSession: document.querySelector("#active-session"),
  /* character list */
  packCount: document.querySelector("#pack-count"),
  packDetailList: document.querySelector("#pack-detail-list"),
  packList: document.querySelector("#pack-list"),
  refreshPacks: document.querySelector("#refresh-packs"),
  openPacksFolder: document.querySelector("#open-packs-folder"),
  closeWindow: document.querySelector("#close-window"),
  /* tabs */
  tabButtons: document.querySelectorAll("[data-tab]"),
  tabPanels: document.querySelectorAll("[data-tab-panel]"),
  /* persona form */
  personaForm: document.querySelector("#persona-form"),
  fieldName: document.querySelector("#field-name"),
  fieldUserTitle: document.querySelector("#field-user-title"),
  fieldSelfReference: document.querySelector("#field-self-reference"),
  fieldRelationship: document.querySelector("#field-relationship"),
  fieldPersonalityKeywords: document.querySelector("#field-personality-keywords"),
  fieldSpeakingStyle: document.querySelector("#field-speaking-style"),
  fieldCatchphrases: document.querySelector("#field-catchphrases"),
  fieldBoundaries: document.querySelector("#field-boundaries"),
  fieldProactiveStyle: document.querySelector("#field-proactive-style"),
  fieldExtraSetting: document.querySelector("#field-extra-setting"),
  exampleLinesContainer: document.querySelector("#example-lines-container"),
  addExampleLine: document.querySelector("#add-example-line"),
  resetPersonaForm: document.querySelector("#reset-persona-form"),
  draftStatus: document.querySelector("#draft-status"),
  personaEditingPackName: document.querySelector("#persona-editing-pack-name"),
  personaEditingPackId: document.querySelector("#persona-editing-pack-id"),
  personaEmptyState: document.querySelector("#persona-empty-state"),
  /* portrait management */
  portraitsContent: document.querySelector("#portraits-content"),
  portraitsEmptyState: document.querySelector("#portraits-empty-state"),
  outfitsContainer: document.querySelector("#outfits-container"),
  addOutfitBtn: document.querySelector("#add-outfit-btn"),
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
  activeTab: "list",
  /* cached full profile of the pack being edited (character.json object) */
  editingPackId: "",
  editingProfile: null,
  draftDirty: false,
};

const fieldIds = [
  "field-name",
  "field-user-title",
  "field-self-reference",
  "field-relationship",
  "field-personality-keywords",
  "field-speaking-style",
  "field-catchphrases",
  "field-boundaries",
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
  await emit(SETTINGS_COMMAND_EVENT, { command: "requestSnapshot", value: null });
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
      if (tab) switchTab(tab, view.activePackId);
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

  for (const btn of els.tabButtons) {
    const isSelected = btn.dataset.tab === tab;
    btn.setAttribute("aria-selected", String(isSelected));
  }
  for (const panel of els.tabPanels) {
    panel.hidden = panel.dataset.tabPanel !== tab;
  }

  if (tab === "persona") {
    const targetId = String(packId || view.activePackId || "").trim();
    if (targetId) {
      startEditingPack(targetId);
    }
  }
  if (tab === "portraits") {
    const targetId = String(packId || view.activePackId || "").trim();
    if (targetId) {
      void loadPortraitsTab(targetId);
    } else {
      showPortraitsEmpty();
    }
  }
  if (tab === "calibration") {
    const targetId = String(packId || view.activePackId || "").trim();
    if (targetId) {
      void loadCalibrationTab(targetId);
    } else {
      showCalibrationEmpty();
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
    els.personaEditingPackId.textContent = packId || "";
  }
}

function loadPersonaForm(profile) {
  const identity = (profile && typeof profile === "object" ? profile.identity : null) || {};
  const persona = (profile && typeof profile === "object" ? profile.persona_form : null) || {};
  populateFormFields(identity, persona);
  view.draftDirty = false;
  updateDraftStatus(false);
}

function applyDraftToForm(draft) {
  if (!draft) return;
  const ident = draft.identity || {};
  const persona = draft.persona_form || {};
  populateFormFields(ident, persona);
}

/** one-stop form fill from identity + persona objects */
function populateFormFields(identity, persona) {
  identity = identity || {};
  persona = persona || {};

  setFieldValue(els.fieldName, identity.name || "");
  setFieldValue(els.fieldUserTitle, identity.user_title || identity.userTitle || "");
  setFieldValue(els.fieldSelfReference, identity.self_reference || identity.selfReference || "");
  setFieldValue(els.fieldRelationship, identity.relationship || "");
  setFieldValue(els.fieldPersonalityKeywords, asKeywordsInput(persona.personality_keywords));
  setFieldValue(els.fieldSpeakingStyle, persona.speaking_style || persona.speakingStyle || "");
  setFieldValue(els.fieldCatchphrases, asLinesInput(persona.catchphrases));
  setFieldValue(els.fieldBoundaries, persona.boundaries || "");
  setFieldValue(els.fieldProactiveStyle, persona.proactive_style || persona.proactiveStyle || "");
  setFieldValue(els.fieldExtraSetting, persona.extra_setting || persona.extraSetting || "");

  const lines = Array.isArray(persona.example_lines) ? persona.example_lines : [];
  renderExampleLines(lines);
}

function collectFormData() {
  const identity = {
    name: (els.fieldName?.value || "").trim(),
    user_title: (els.fieldUserTitle?.value || "").trim(),
    self_reference: (els.fieldSelfReference?.value || "").trim(),
    relationship: (els.fieldRelationship?.value || "").trim(),
  };

  const personaForm = {
    personality_keywords: splitKeywords(els.fieldPersonalityKeywords?.value || ""),
    speaking_style: (els.fieldSpeakingStyle?.value || "").trim(),
    catchphrases: splitLines(els.fieldCatchphrases?.value || ""),
    boundaries: (els.fieldBoundaries?.value || "").trim(),
    proactive_style: (els.fieldProactiveStyle?.value || "").trim(),
    extra_setting: (els.fieldExtraSetting?.value || "").trim(),
    example_lines: collectExampleLines(),
  };

  return {
    packId: view.editingPackId,
    identity,
    persona_form: personaForm,
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

async function saveDraft() {
  if (!view.editingPackId) {
    setStatus("请先从角色列表中选择一个角色包。");
    return;
  }
  const data = collectFormData();

  /* try Tauri backend save first; fall back to localStorage */
  if (isTauriRuntime) {
    try {
      const result = await invoke("save_character_pack", {
        packId: view.editingPackId,
        identity: data.identity,
        personaForm: data.persona_form,
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
      setStatus(`已保存至文件（${view.editingPackId}）。`);
      return;
    } catch (error) {
      /* backend save failed – fall back to localStorage */
      setStatus(`文件保存失败，已保存到本地草稿：${formatError(error)}`);
    }
  }

  /* localStorage fallback */
  try {
    persistDraft(view.editingPackId, data);
    view.draftDirty = false;
    updateDraftStatus(false);
    setStatus(`草稿已保存（${view.editingPackId}）。`);
  } catch (error) {
    setStatus(`保存失败：${formatError(error)}`);
  }
}

async function autoSaveDraft() {
  if (!view.editingPackId || !view.draftDirty) return;
  const data = collectFormData();
  persistDraft(view.editingPackId, data);
  view.draftDirty = false;
  updateDraftStatus(false);
}

/** synchronous save – used when switching packs inside a sync call chain */
function autoSaveDraftSync(packId) {
  if (!packId || !view.draftDirty) return;
  const data = collectFormData();
  persistDraft(packId, data);
  view.draftDirty = false;
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
    };
    localStorage.setItem(draftKey(packId), JSON.stringify(payload));
  } catch {
    /* storage full or unavailable – silently skip */
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

  const textInput = document.createElement("input");
  textInput.type = "text";
  textInput.className = "form-input example-line-text";
  textInput.placeholder = "台词内容…";
  textInput.value = text || "";
  textInput.maxLength = 200;
  textInput.addEventListener("input", () => updateDraftStatus(true));

  const emotionInput = document.createElement("input");
  emotionInput.type = "text";
  emotionInput.className = "form-input example-line-emotion";
  emotionInput.placeholder = "表情";
  emotionInput.value = emotion || "normal";
  emotionInput.maxLength = 40;
  emotionInput.addEventListener("input", () => updateDraftStatus(true));

  const removeBtn = document.createElement("button");
  removeBtn.type = "button";
  removeBtn.className = "btn-remove-line";
  removeBtn.setAttribute("data-remove-example", "");
  removeBtn.textContent = "✕";
  removeBtn.title = "删除这条示例台词";

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
    view.activeSessionId = String(petState?.sessionId || view.activeSessionId || "").trim();
    view.activeCharacterName = getPackName(findPack(view.activePackId)) || view.activeCharacterName;
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
  view.activePackId = String(character.packId || state.characterPackId || view.activePackId || "").trim();
  view.activeSessionId = String(state.sessionId || view.activeSessionId || "").trim();
  view.activeCharacterName = String(character.appName || character.name || view.activeCharacterName || "").trim();
  const available = Array.isArray(character.availablePacks) ? character.availablePacks : [];
  if (available.length) {
    view.packs = normalizePacks(available);
  }
  render();
}

async function applyPack(packId) {
  const pack = findPack(packId);
  setStatus(`正在应用：${getPackName(pack) || packId}`);
  try {
    await emit(SETTINGS_COMMAND_EVENT, { command: "setCharacterPack", value: packId });
    view.activePackId = packId;
    view.activeCharacterName = getPackName(pack) || view.activeCharacterName;
    render();
  } catch (error) {
    setStatus(`应用失败：${formatError(error)}`);
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
    setStatus(`正在导入：${file.name}…`);
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      const result = await invoke("install_character_pack_zip_bytes", {
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
  const packId = view.activePackId;
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

async function createCharacterPack() {
  const packId = (els.createPackId.value || "").trim();
  const name = (els.createName.value || "").trim();
  const appName = (els.createAppName.value || "").trim();
  const userTitle = (els.createUserTitle.value || "").trim();

  if (!packId) {
    els.createError.textContent = "请输入角色包 ID。";
    els.createError.hidden = false;
    return;
  }
  if (!name) {
    els.createError.textContent = "请输入角色名称。";
    els.createError.hidden = false;
    return;
  }

  setStatus(`正在创建角色包：${name}…`);
  try {
    const result = await invoke("create_character_pack", {
      packId,
      name,
      appName,
      userTitle,
    });
    els.createDialog.close();
    setStatus(`已创建角色包：${name}（${packId}）。`);
    await refreshPacks();
    /* switch to editing the new pack */
    if (result?.id) {
      switchTab("persona", result.id);
    }
  } catch (error) {
    els.createError.textContent = formatError(error);
    els.createError.hidden = false;
    setStatus(`创建失败：${formatError(error)}`);
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
  try {
    const outfits = await invoke("list_pack_assets", { packId });
    renderPortraitsView(packId, outfits);
  } catch (error) {
    setStatus(`读取立绘失败：${formatError(error)}`);
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

  /* render warnings section */
  const warnings = buildMissingEmotionWarnings(items, profile);
  if (warnings) {
    els.outfitsContainer.appendChild(warnings);
  }

  /* render outfit cards */
  for (const outfit of items) {
    els.outfitsContainer.appendChild(buildOutfitCard(packId, outfit, profile));
  }

  /* bind add-outfit button */
  els.addOutfitBtn.onclick = () => addOutfitDialog(packId);

  setStatus(`已加载 ${items.length} 套服装。`);
}

function buildMissingEmotionWarnings(outfits, profile) {
  const appearance = profile.appearance || {};
  const required = Array.isArray(appearance.required_emotions || appearance.requiredEmotions)
    ? (appearance.required_emotions || appearance.requiredEmotions) : [];
  const recommended = Array.isArray(appearance.recommended_emotions || appearance.recommendedEmotions)
    ? (appearance.recommended_emotions || appearance.recommendedEmotions) : [];

  /* collect all emotion IDs across all outfits */
  const available = new Set();
  for (const outfit of outfits) {
    for (const em of (outfit.emotions || [])) {
      available.add(em.id);
    }
  }

  const missingRequired = required.filter((id) => !available.has(id));
  const missingRecommended = recommended.filter((id) => !available.has(id));

  if (!missingRequired.length && !missingRecommended.length) return null;

  const box = document.createElement("div");
  box.className = "missing-emotions-warning glass-card";

  if (missingRequired.length) {
    const line = document.createElement("p");
    line.className = "warning-line warning-required";
    line.innerHTML = `⚠️ <strong>缺少必要表情：</strong>${missingRequired.join(", ")}`;
    box.appendChild(line);
  }
  if (missingRecommended.length) {
    const line = document.createElement("p");
    line.className = "warning-line warning-recommended";
    line.innerHTML = `💡 <strong>建议添加：</strong>${missingRecommended.join(", ")}`;
    box.appendChild(line);
  }

  return box;
}

function buildOutfitCard(packId, outfit, packProfile) {
  const card = document.createElement("div");
  card.className = "outfit-card glass-card";

  const header = document.createElement("div");
  header.className = "outfit-card-header";

  const titleGroup = document.createElement("div");
  titleGroup.className = "outfit-title-group";

  const title = document.createElement("h3");
  title.textContent = `👗 ${outfit.name || outfit.id}`;
  title.title = "双击重命名服装";

  title.addEventListener("dblclick", () => renameOutfitDialog(packId, outfit.id));

  titleGroup.append(title);

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
  addBtn.textContent = "＋ 上传表情";
  addBtn.addEventListener("click", () => uploadEmotionDialog(packId, outfit.id));

  actions.append(addBtn);
  header.append(titleGroup, actions);

  const grid = document.createElement("div");
  grid.className = "emotion-grid";

  const emotions = Array.isArray(outfit.emotions) ? outfit.emotions : [];
  const defaultEmotion = packProfile?.appearance?.default_emotion || packProfile?.appearance?.defaultEmotion || "";
  const musicEmotion = packProfile?.appearance?.music_emotion || packProfile?.appearance?.musicEmotion || "";

  grid.replaceChildren(
    ...emotions.map((em) => buildEmotionTile(packId, outfit.id, em, { defaultEmotion, musicEmotion }))
  );

  card.append(header, grid);
  return card;
}

function buildEmotionTile(packId, outfitId, emotion, flags = {}) {
  const tile = document.createElement("div");
  tile.className = "emotion-tile";
  tile.title = `${emotion.name || emotion.id} — 点击预览 | 右键删除 | 双击重命名`;

  const img = document.createElement("img");
  img.src = convertFileSrc(emotion.path || "");
  img.alt = emotion.name || emotion.id;
  img.loading = "lazy";

  const labelRow = document.createElement("div");
  labelRow.className = "emotion-label-row";

  const label = document.createElement("span");
  label.textContent = emotion.name || emotion.id;

  labelRow.append(label);

  /* default / music badges */
  if (emotion.id === flags.defaultEmotion) {
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

  tile.append(img, labelRow);

  tile.addEventListener("click", () => previewEmotion(emotion));
  tile.addEventListener("dblclick", () => renameEmotionDialog(packId, outfitId, emotion.id));
  tile.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    deleteEmotionConfirm(packId, outfitId, emotion.id);
  });

  return tile;
}

let previewingEmotion = null;

function previewEmotion(emotion) {
  if (!els.emotionPreview) return;
  previewingEmotion = emotion;
  els.emotionPreview.replaceChildren();

  const img = document.createElement("img");
  img.src = convertFileSrc(emotion.path || "");
  img.alt = emotion.name || emotion.id;
  img.className = "preview-image";

  const info = document.createElement("p");
  info.className = "preview-info";
  info.textContent = `${emotion.name || emotion.id} — ${formatFileSize(emotion.sizeBytes || 0)}`;

  const actions = document.createElement("div");
  actions.className = "preview-actions";

  const setDefaultBtn = document.createElement("button");
  setDefaultBtn.type = "button";
  setDefaultBtn.className = "btn-secondary";
  setDefaultBtn.textContent = "⭐ 设为默认表情";
  setDefaultBtn.style.cssText = "font-size:12px;padding:5px 10px;";
  setDefaultBtn.addEventListener("click", () => setAppearanceField("default_emotion", emotion.id));

  const setMusicBtn = document.createElement("button");
  setMusicBtn.type = "button";
  setMusicBtn.className = "btn-secondary";
  setMusicBtn.textContent = "🎵 设为听歌表情";
  setMusicBtn.style.cssText = "font-size:12px;padding:5px 10px;";
  setMusicBtn.addEventListener("click", () => setAppearanceField("music_emotion", emotion.id));

  actions.append(setDefaultBtn, setMusicBtn);

  els.emotionPreview.append(img, info, actions);
}

async function setAppearanceField(field, value) {
  const packId = view.editingPackId || view.activePackId;
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
  }
}

async function deleteEmotionConfirm(packId, outfitId, emotionId) {
  if (!confirm(`确定删除表情 "${emotionId}"？此操作不可恢复。`)) return;
  setStatus(`正在删除表情：${emotionId}…`);
  try {
    await invoke("delete_portrait_image", { packId, outfit: outfitId, emotion: emotionId });
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
    setStatus(`已重命名服装：${newId.trim()}`);
    await loadPortraitsTab(packId);
  } catch (error) {
    setStatus(`重命名失败：${formatError(error)}`);
  }
}

async function uploadEmotionDialog(packId, outfitId) {
  if (!isTauriRuntime) return;
  const emotion = prompt("表情 ID（例如 normal, happy, 开心）：", "normal");
  if (!emotion || !emotion.trim()) return;

  const fileInput = document.createElement("input");
  fileInput.type = "file";
  fileInput.accept = "image/png,image/jpeg,image/webp";
  fileInput.onchange = async () => {
    const file = fileInput.files?.[0];
    if (!file) return;
    setStatus(`正在上传：${file.name}…`);
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      await invoke("upload_portrait_image", {
        packId,
        outfit: outfitId,
        emotion: emotion.trim(),
        imageBytes: Array.from(bytes),
      });
      setStatus(`已上传：${emotion.trim()} → ${outfitId}`);
      await loadPortraitsTab(packId);
    } catch (error) {
      setStatus(`上传失败：${formatError(error)}`);
    }
  };
  fileInput.click();
}

function addOutfitDialog(packId) {
  const outfit = prompt("新服装 ID（例如 default, 校服, summer）：", "");
  if (!outfit || !outfit.trim()) return;
  /* creating a new outfit directory is done by uploading an image into it */
  uploadEmotionDialog(packId, outfit.trim());
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

    /* load first outfit's first emotion image */
    const defaultOutfit = items[0];
    const firstEmotion = defaultOutfit.emotions?.[0];
    if (!firstEmotion) { setStatus("没有可预览的表情。"); return; }

    els.calibrationImage.src = convertFileSrc(firstEmotion.path || "");
    els.calibrationImage.onload = () => {
      loadLayoutForOutfit(packId, defaultOutfit.id);
    };

    /* outfit switch */
    els.calibrationOutfitSelect.onchange = () => {
      const sel = els.calibrationOutfitSelect.value;
      const outfit = items.find((o) => o.id === sel);
      if (outfit?.emotions?.[0]) {
        els.calibrationImage.src = convertFileSrc(outfit.emotions[0].path || "");
        els.calibrationImage.onload = () => loadLayoutForOutfit(packId, sel);
      }
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
    setCalibrationSlider("cal-bubble-x", Math.round((outfitLayout.bubble?.anchor_x || 0.5) * 100));
    setCalibrationSlider("cal-bubble-y", Math.round((outfitLayout.bubble?.anchor_y || 0.12) * 100));
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

  setStatus("已应用自动布局。");
}

function setCalibrationSlider(id, value) {
  const el = els[id];
  if (!el) return;
  el.value = value;
  applyCalibrationPreview();
}

let calibrationSlidersBound = false;

function bindCalibrationSliders() {
  if (calibrationSlidersBound) return;
  calibrationSlidersBound = true;
  const ids = ["cal-win-w", "cal-win-h", "cal-scale", "cal-offset-x", "cal-offset-y", "cal-bubble-x", "cal-bubble-y"];
  for (const id of ids) {
    const el = els[id];
    if (!el) continue;
    el.addEventListener("input", () => applyCalibrationPreview());
  }
}

function applyCalibrationPreview() {
  const winW = Number(els.calWinW?.value) || 340;
  const winH = Number(els.calWinH?.value) || 560;
  const scale = (Number(els.calScale?.value) || 100) / 100;
  const offX = Number(els.calOffsetX?.value) || 0;
  const offY = Number(els.calOffsetY?.value) || 0;
  const bubbleX = (Number(els.calBubbleX?.value) || 50) / 100;
  const bubbleY = (Number(els.calBubbleY?.value) || 12) / 100;

  /* update frame size */
  if (els.calibrationFrame) {
    els.calibrationFrame.style.width = `${Math.min(winW, 600)}px`;
    els.calibrationFrame.style.height = `${Math.min(winH, 500)}px`;
  }

  /* update portrait */
  if (els.calibrationPortrait) {
    els.calibrationPortrait.style.transform = `translate(${offX}px, ${offY}px) scale(${scale})`;
  }

  /* update bubble dot */
  if (els.calibrationBubbleDot) {
    els.calibrationBubbleDot.style.left = `${bubbleX * 100}%`;
    els.calibrationBubbleDot.style.top = `${bubbleY * 100}%`;
  }

  /* update value labels */
  updateCalLabel("cal-win-w-val", `${winW}`);
  updateCalLabel("cal-win-h-val", `${winH}`);
  updateCalLabel("cal-scale-val", scale.toFixed(2));
  updateCalLabel("cal-offset-x-val", `${offX}`);
  updateCalLabel("cal-offset-y-val", `${offY}`);
  updateCalLabel("cal-bubble-x-val", bubbleX.toFixed(2));
  updateCalLabel("cal-bubble-y-val", bubbleY.toFixed(2));
}

function updateCalLabel(id, text) {
  const el = document.querySelector(`#${id}`);
  if (el) el.textContent = text;
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
      anchor_x: (Number(els.calBubbleX?.value) || 50) / 100,
      anchor_y: (Number(els.calBubbleY?.value) || 12) / 100,
      max_width: 300,
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
    setStatus(`校准已保存（${outfitId}）。`);
  } catch (error) {
    setStatus(`保存校准失败：${formatError(error)}`);
  }
}

/* ------------------------------------------------------------------ */
/*  Render                                                            */
/* ------------------------------------------------------------------ */

function render() {
  const activePack = findPack(view.activePackId);
  const activeName = view.activeCharacterName || getPackName(activePack) || "-";
  els.activeCharacter.textContent = activeName;
  els.activePack.textContent = view.activePackId || "-";
  els.activeSession.textContent = shortId(view.activeSessionId);
  els.summary.textContent = view.packs.length
    ? `${activeName} · ${view.packs.length} 个角色包`
    : "还没有读到角色包。";
  els.packCount.textContent = String(view.packs.length);
  renderPackDetails(activePack);
  renderPackList();

  /* if persona tab is active, keep form in sync with active pack */
  if (view.activeTab === "persona" && view.editingPackId !== view.activePackId) {
    /* auto-save current draft before switching */
    if (view.editingPackId && view.draftDirty) {
      autoSaveDraftSync(view.editingPackId);
    }
    if (view.activePackId) {
      startEditingPack(view.activePackId);
    }
  }
}

function renderPackList() {
  if (!view.packs.length) {
    els.packList.textContent = "暂无角色包。";
    return;
  }

  els.packList.replaceChildren(
    ...view.packs.map((pack) => {
      const card = document.createElement("article");
      card.className = "pack-card";
      card.classList.toggle("active", pack.id === view.activePackId || Boolean(pack.selected));

      const heading = document.createElement("div");
      heading.className = "pack-card-heading";
      heading.append(buildText("strong", getPackName(pack) || pack.id), buildText("span", pack.id));

      const meta = document.createElement("p");
      meta.textContent = buildPackMeta(pack);

      const actions = document.createElement("div");
      actions.className = "pack-card-actions";

      const editBtn = document.createElement("button");
      editBtn.type = "button";
      editBtn.className = "btn-edit-pack";
      editBtn.dataset.editPack = pack.id;
      editBtn.textContent = "编辑";

      const applyBtn = document.createElement("button");
      applyBtn.type = "button";
      applyBtn.dataset.applyPack = pack.id;
      applyBtn.disabled = pack.id === view.activePackId || Boolean(pack.selected);
      applyBtn.textContent = applyBtn.disabled ? "已启用" : "应用";

      actions.append(editBtn, applyBtn);
      card.append(heading, meta, actions);
      return card;
    })
  );
}

function renderPackDetails(pack) {
  const rows = [
    ["名称", getPackName(pack) || "-"],
    ["Pack ID", pack?.id || "-"],
    ["角色 ID", pack?.characterId || "-"],
    ["Schema", pack?.schemaVersion || "-"],
    ["默认服装", pack?.defaultOutfit || "-"],
    ["默认表情", pack?.defaultEmotion || "-"],
    ["资源数", pack?.assetCount ? String(pack.assetCount) : "-"],
    ["来源", pack?.source || "-"],
  ];
  els.packDetailList.replaceChildren(
    ...rows.map(([label, value]) => {
      const row = document.createElement("div");
      const key = document.createElement("dt");
      const data = document.createElement("dd");
      key.textContent = label;
      data.textContent = value;
      row.append(key, data);
      return row;
    })
  );
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
        selected: Boolean(pack.selected),
        /* keep the raw profile so the persona form can read it */
        _rawProfile: profile,
      };
    })
    .filter((pack) => pack.id)
    .sort((left, right) => getPackName(left).localeCompare(getPackName(right), "zh-Hans-CN"));
}

function findPack(packId) {
  const id = String(packId || "").trim();
  return view.packs.find((pack) => pack.id === id) || null;
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                           */
/* ------------------------------------------------------------------ */

function getPackName(pack) {
  return String(pack?.name || pack?.characterName || pack?.characterId || "").trim();
}

function buildPackMeta(pack) {
  const parts = [
    pack.characterName || pack.characterId || "",
    pack.defaultOutfit ? `服装 ${pack.defaultOutfit}` : "",
    pack.defaultEmotion ? `默认 ${pack.defaultEmotion}` : "",
    pack.schemaVersion || "",
    pack.assetCount ? `${pack.assetCount} 个文件` : "",
  ].filter(Boolean);
  return parts.join(" · ") || "角色包";
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
