import {
  applyModelProvider,
  applySubagentModelProvider,
  createModelServiceDraft,
  modelServicePayload,
  MODEL_SERVICE_ACTIONS
} from "../model-service.js";
import { serviceScope } from "./scope.js";

export function createModelController({ root, store, actions }) {
  let revision = 0;
  let owner = serviceScope(store.getState().viewModel);

  function synchronizeScope(viewModel) {
    const next = serviceScope(viewModel);
    if (next === owner) return false;
    owner = next;
    revision += 1;
    return true;
  }

  function handleClick(event) {
    const subagentToggle = event.target.closest("button[data-subagent-toggle]");
    if (subagentToggle) {
      if (!subagentToggle.disabled) {
        revision += 1;
        const draft = readModelServiceForm(store.getState().modelDraft);
        const field = subagentToggle.dataset.subagentToggle;
        if (draft.subagent) {
          draft.subagent[field] = !Boolean(draft.subagent[field]);
          store.patch({ modelDraft: draft });
        }
      }
      return true;
    }
    const toggle = event.target.closest("button[data-model-toggle]");
    if (toggle) {
      if (!toggle.disabled) {
        revision += 1;
        const draft = readModelServiceForm(store.getState().modelDraft);
        const field = toggle.dataset.modelToggle;
        store.patch({ modelDraft: { ...draft, [field]: !Boolean(draft[field]) }, modelVisionModels: [] });
      }
      return true;
    }
    const button = event.target.closest("button[data-action^='model.']");
    if (!button) return false;
    if (!button.disabled) void runModelAction(button.dataset.action);
    return true;
  }

  function handleInput(event) {
    if (event.target.matches("[data-subagent-field]")) {
      const field = event.target.dataset.subagentField;
      const currentDraft = store.getState().modelDraft;
      if (field && currentDraft?.subagent) {
        revision += 1;
        currentDraft.subagent[field] = event.target.dataset.modelValueType === "number"
          ? (event.target.value === "" ? "" : Number(event.target.value)) : event.target.value;
      }
      return;
    }
    if (!event.target.matches("[data-model-field]")) return;
    const field = event.target.dataset.modelField;
    const currentDraft = store.getState().modelDraft;
    if (field && currentDraft) {
      revision += 1;
      // No render during typing: the focused DOM node owns the current edit/IME composition.
      currentDraft[field] = event.target.dataset.modelValueType === "number"
        ? (event.target.value === "" ? "" : Number(event.target.value)) : event.target.value;
    }
  }

  function handleChange(event) {
    if (event.target.matches('[data-model-field="providerId"]')) {
      revision += 1;
      const state = store.getState();
      store.patch({
        modelDraft: applyModelProvider(state.viewModel?.model, readModelServiceForm(state.modelDraft), event.target.value),
        modelModels: [], modelVisionModels: []
      });
    } else if (event.target.matches('[data-subagent-field="providerId"]')) {
      revision += 1;
      const state = store.getState();
      store.patch({
        modelDraft: applySubagentModelProvider(state.viewModel?.model, readModelServiceForm(state.modelDraft), event.target.value),
        modelSubagentModels: []
      });
    } else if (event.target.matches('[data-subagent-field="mode"]')) {
      revision += 1;
      const draft = readModelServiceForm(store.getState().modelDraft);
      if (draft.subagent) {
        draft.subagent.mode = event.target.value;
      }
      store.patch({
        modelDraft: draft,
        modelSubagentModels: []
      });
    } else handleInput(event);
  }

  async function runModelAction(actionId) {
    const draft = readModelServiceForm(store.getState().modelDraft);
    store.patch({ modelDraft: draft });
    const requestedRevision = revision;
    const requestedScope = serviceScope(store.getState().viewModel);
    const isCurrent = () => requestedRevision === revision && requestedScope === serviceScope(store.getState().viewModel);
    const isSubagent = actionId === MODEL_SERVICE_ACTIONS.subagentModels || actionId === MODEL_SERVICE_ACTIONS.subagentTest;
    const isVision = actionId === MODEL_SERVICE_ACTIONS.visionModels || actionId === MODEL_SERVICE_ACTIONS.visionTest;
    const payload = modelServicePayload(draft, isSubagent ? "subagent" : isVision ? "vision" : "");
    const result = await actions.runAction(actionId, payload, { isCurrent });
    if (!isCurrent()) return result;
    if (actionId === MODEL_SERVICE_ACTIONS.models && result?.ok) {
      const models = Array.isArray(result.models) ? result.models.map(item => String(item || "").trim()).filter(Boolean) : [];
      const nextDraft = { ...draft };
      if (!nextDraft.chatModel && models.length) nextDraft.chatModel = models[0];
      store.patch({ modelDraft: nextDraft, modelModels: models });
    } else if (actionId === MODEL_SERVICE_ACTIONS.subagentModels && result?.ok) {
      const models = Array.isArray(result.models) ? result.models.map(item => String(item || "").trim()).filter(Boolean) : [];
      const nextDraft = { ...draft };
      if (nextDraft.subagent && !nextDraft.subagent.model && models.length) {
        nextDraft.subagent.model = models[0];
      }
      store.patch({ modelDraft: nextDraft, modelSubagentModels: models });
    } else if (actionId === MODEL_SERVICE_ACTIONS.visionModels && result?.ok) {
      const models = Array.isArray(result.models) ? result.models.map(item => String(item || "").trim()).filter(Boolean) : [];
      store.patch({ modelDraft: { ...draft, visionModel: draft.visionModel || models[0] || "" }, modelVisionModels: models });
    } else if (actionId === MODEL_SERVICE_ACTIONS.save && result?.ok) {
      store.patch({
        modelDraft: createModelServiceDraft(store.getState().viewModel?.model || draft),
        modelModels: [],
        modelSubagentModels: [], modelVisionModels: []
      });
    }
    return result;
  }

  function readModelServiceForm(fallback = {}) {
    fallback ||= {};
    const read = field => root.querySelector(`[data-model-field="${field}"]`);
    const readSub = field => root.querySelector(`[data-subagent-field="${field}"]`);
    const fallbackSub = fallback.subagent || {};

    const subagentDraft = {
      mode: String(readSub("mode")?.value ?? fallbackSub.mode ?? "inherit"),
      providerId: String(readSub("providerId")?.value ?? fallbackSub.providerId ?? "openai_compatible"),
      protocol: String(readSub("protocol")?.value ?? fallbackSub.protocol ?? "openai"),
      baseUrl: String(readSub("baseUrl")?.value ?? fallbackSub.baseUrl ?? "").trim(),
      apiKey: String(readSub("apiKey")?.value ?? fallbackSub.apiKey ?? "").trim(),
      model: String(readSub("model")?.value ?? fallbackSub.model ?? "").trim(),
      thinkingMode: String(readSub("thinkingMode")?.value ?? fallbackSub.thinkingMode ?? "").trim(),
      reasoningEffort: String(readSub("reasoningEffort")?.value ?? fallbackSub.reasoningEffort ?? "").trim(),
      maxOutputTokens: Number(readSub("maxOutputTokens")?.value ?? fallbackSub.maxOutputTokens ?? 0),
      timeoutSeconds: Number(readSub("timeoutSeconds")?.value ?? fallbackSub.timeoutSeconds ?? 120),
      clearApiKey: Boolean(fallbackSub.clearApiKey)
    };

    return {
      ...createModelServiceDraft(fallback), ...fallback,
      providerId: String(read("providerId")?.value ?? fallback.providerId ?? "openai_compatible"),
      protocol: String(read("protocol")?.value ?? fallback.protocol ?? "openai"),
      baseUrl: String(read("baseUrl")?.value ?? fallback.baseUrl ?? "").trim(),
      apiKey: String(read("apiKey")?.value ?? fallback.apiKey ?? "").trim(),
      chatModel: String(read("chatModel")?.value ?? fallback.chatModel ?? "").trim(),
      chatThinkingMode: String(read("chatThinkingMode")?.value ?? fallback.chatThinkingMode ?? "").trim(),
      chatReasoningEffort: String(read("chatReasoningEffort")?.value ?? fallback.chatReasoningEffort ?? "").trim(),
      chatMaxOutputTokens: Number(read("chatMaxOutputTokens")?.value ?? fallback.chatMaxOutputTokens ?? 0),
      visionModel: String(read("visionModel")?.value ?? fallback.visionModel ?? "").trim(),
      visionBaseUrl: String(read("visionBaseUrl")?.value ?? fallback.visionBaseUrl ?? "").trim(),
      visionApiKey: String(read("visionApiKey")?.value ?? fallback.visionApiKey ?? "").trim(),
      visionApiProtocol: String(read("visionApiProtocol")?.value ?? fallback.visionApiProtocol ?? "openai"),
      timeoutSeconds: Number(read("timeoutSeconds")?.value ?? fallback.timeoutSeconds ?? 120),
      clearApiKey: Boolean(fallback.clearApiKey),
      subagent: subagentDraft
    };
  }

  function captureFocusedModelField() {
    const active = document.activeElement;
    if (!active) return null;
    if (active.matches?.("[data-subagent-field]")) {
      return {
        scope: serviceScope(store.getState().viewModel),
        isSubagent: true,
        field: active.dataset.subagentField || "",
        start: typeof active.selectionStart === "number" ? active.selectionStart : null,
        end: typeof active.selectionEnd === "number" ? active.selectionEnd : null,
        direction: active.selectionDirection || "none"
      };
    }
    if (!active.matches?.("[data-model-field]")) return null;
    return {
      scope: serviceScope(store.getState().viewModel),
      isSubagent: false,
      field: active.dataset.modelField || "",
      start: typeof active.selectionStart === "number" ? active.selectionStart : null,
      end: typeof active.selectionEnd === "number" ? active.selectionEnd : null,
      direction: active.selectionDirection || "none"
    };
  }

  function restoreFocusedModelField(snapshot) {
    if (!snapshot?.field || snapshot.scope !== serviceScope(store.getState().viewModel)) return;
    const selector = snapshot.isSubagent
      ? `[data-subagent-field="${snapshot.field}"]`
      : `[data-model-field="${snapshot.field}"]`;
    const next = root.querySelector(selector);
    if (!next || next.disabled) return;
    next.focus({ preventScroll: true });
    if (snapshot.start === null || typeof next.setSelectionRange !== "function") return;
    const length = String(next.value || "").length;
    const start = Math.min(snapshot.start, length);
    next.setSelectionRange(start, Math.min(snapshot.end ?? start, length), snapshot.direction);
  }

  return { handleClick, handleInput, handleChange, runModelAction, readModelServiceForm,
    captureFocusedModelField, restoreFocusedModelField, synchronizeScope };
}
