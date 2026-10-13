import { getPackName, normalizePacks } from "./pack-model.js";
import { setFieldValue, flashElement } from "./dom.js";
import { asKeywordsInput, asLinesInput, splitKeywords, splitLines, formatError } from "./shared.js";
import { setInstanceStorageItem, getInstanceStorageItem, removeInstanceStorageItem } from "../instance-storage.js";
import { DRAFT_STORAGE_PREFIX } from "./constants.js";

export function createPersonaController({
  findPack,
  view,
  els,
  setWorkspacePack,
  setStatus,
  personaState,
  isPackWriteBusy,
  writeState,
  updateCatalogControls,
  isTauriRuntime,
  invoke,
}) {
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
    personaState.personaRevisions.set(view.editingPackId, (personaState.personaRevisions.get(view.editingPackId) || 0) + 1);
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
    const revision = personaState.personaRevisions.get(packId) || 0;
    const unchanged = () => (personaState.personaRevisions.get(packId) || 0) === revision;
    const ownsForm = () => view.editingPackId === packId && unchanged();
    const ownsStatus = () => ownsForm() && view.activeTab === "persona";
    writeState.personaSaves.add(packId);
    ++writeState.registryRevision;
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
      writeState.personaSaves.delete(packId);
      ++writeState.registryRevision;
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
    if (dirty) personaState.personaRevisions.set(view.editingPackId, (personaState.personaRevisions.get(view.editingPackId) || 0) + 1);
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

  return { startEditingPack, loadPersonaForm, collectFormData, saveDraft, autoSaveDraft, autoSaveDraftSync, loadDraft, clearDraft, updateDraftStatus, hasBackendPendingPersonaChanges, addExampleLineRow };
}
