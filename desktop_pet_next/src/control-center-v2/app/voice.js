import { capabilityScope } from "./capability-drafts.js";
import { friendlyError } from "./scope.js";

export function createVoiceController({ root, store, actions, drafts,
  AudioClass = Audio, urls = URL, decode = atob, requestFrame = requestAnimationFrame }) {
  let voicePreviewDraft = "";
  let owner = capabilityScope(store.getState().viewModel), epoch = 0, audio = null, disposed = false;
  let renderedOwner = owner;

  function stopAudio() {
    const current = audio;
    audio = null;
    if (!current) return;
    current.element.pause();
    current.release();
  }
  function synchronizeScope(viewModel) {
    const next = capabilityScope(viewModel);
    if (owner === next) return;
    owner = next; epoch += 1;
    voicePreviewDraft = "";
    stopAudio();
  }
  function handleInput(event) {
    if (event.target.matches("[data-voice-preview-input]")) voicePreviewDraft = event.target.value;
    if (event.target.matches("[data-voice-range]")) {
      const field = event.target.dataset.voiceRange, value = Number(event.target.value);
      const label = root.querySelector(`[data-voice-range-value="${field}"]`);
      if (label) label.textContent = field === "volume" ? `${Math.round(value)}%` : String(value);
    }
  }
  function handleChange(event) {
    if (event.target.matches("[data-voice-provider-select]")) {
      epoch += 1;
      stopAudio();
      drafts.discard("voice-profile:editor");
      store.patch({ voiceProfileSuggestion: { providerId: event.target.value }, voiceProfileInspection: null,
                    voiceProfileEditorOpen: false });
    }
    if (event.target.matches('[data-voice-range="volume"]')) {
      void actions.runAction("voice.setVolume", { value: Number(event.target.value) / 100 });
    }
  }
  function previewPayload() {
    const text = String(root.querySelector("[data-voice-preview-input]")?.value ?? voicePreviewDraft).trim();
    return text ? { text } : {};
  }
  function beforeRender() {
    if (renderedOwner !== owner) return;
    const preview = root.querySelector("[data-voice-preview-input]");
    if (preview) voicePreviewDraft = preview.value;
  }
  function afterRender() {
    renderedOwner = owner;
    const preview = root.querySelector("[data-voice-preview-input]");
    if (preview) preview.value = voicePreviewDraft;
  }
  function handleClick(event) {
    const edit = event.target.closest("button[data-voice-profile-edit]");
    if (edit) {
      if (!edit.disabled) editVoiceProfile(edit.dataset.providerId, edit.dataset.voiceProfileEdit);
      return true;
    }
    const button = event.target.closest("button[data-voice-profile-action]");
    if (!button) return false;
    if (button.disabled) return true;
    const operation = button.dataset.voiceProfileAction;
    const payload = { providerId: button.dataset.providerId || "", voiceProfileId: button.dataset.voiceProfileId || "",
      emotion: button.dataset.emotion || "", characterPackId: store.getState().viewModel?.character?.packId || "" };
    if (operation === "test") void runVoiceProfileTest(payload);
    if (operation === "bind") void actions.runAction("abilities.provider.voiceProfile.assignToCurrentCharacter", payload);
    if (operation === "clear") void actions.runAction("abilities.provider.voiceProfile.clearCurrentCharacter", payload);
    return true;
  }
  function editVoiceProfile(providerId, voiceProfileId) {
    const provider = store.getState().viewModel?.abilities?.providers?.find(item => item.id === providerId);
    const profile = provider?.voiceProfiles?.find(item => item.voiceProfileId === voiceProfileId);
    if (!profile) return;
    drafts.discard("voice-profile:editor"); drafts.open("voice-profile:editor");
    const suggestion = Object.fromEntries(["voiceProfileId", "enabled", "textLang", "promptLang", "mediaType", "irodoriVoiceId",
      "parallelInfer", "splitBucket", "batchSize", "topK", "topP", "temperature", "speedFactor",
      "fragmentInterval", "textSplitMethod", "emotionSamples"].map(key => [key, profile[key]]));
    store.patch({ voiceProfileSuggestion: { ...suggestion, providerId, displayName: profile.name },
      voiceProfileInspection: null, voiceProfileEditorOpen: true });
    requestFrame(() => root.querySelector(".voice-profile-editor")?.scrollIntoView({ behavior: "smooth", block: "nearest" }));
  }
  async function inspectFolder(payload, ticket) {
    const result = await actions.runAction("abilities.provider.voiceProfile.inspectFolder", payload, ticket);
    if (!result?.ok || !result.suggestedProfile || !ticket.isCurrent()) return result;
    drafts.discard("voice-profile:editor"); drafts.open("voice-profile:editor");
    store.patch({
      voiceProfileSuggestion: { ...result.suggestedProfile, providerId: payload.providerId, folderPath: payload.folderPath },
      voiceProfileInspection: { detected: result.detected || {}, warnings: Array.isArray(result.warnings) ? result.warnings : [] },
      voiceProfileEditorOpen: true
    });
    return result;
  }
  async function runVoiceProfileTest(payload) {
    const actionId = "abilities.provider.ttsTest", requestedEpoch = epoch;
    const isCurrent = () => !disposed && requestedEpoch === epoch;
    const text = String(previewPayload().text || (payload.providerId === "provider.tts.irodori.local"
      ? "おかえりなさい。今日もお疲れさまでした。" : "你好呀，今天也请多关照。")).trim();
    const result = await actions.runAction(actionId, { ...payload, text }, { isCurrent });
    if (!result?.ok || !result.audioBase64 || !isCurrent()) return result;
    const feedback = store.getState().actionStates[actionId];
    const ownsFeedback = () => isCurrent() && store.getState().actionStates[actionId] === feedback;
    try {
      await playVoiceProfileAudio(result.audioBase64, result.mediaType);
      if (ownsFeedback()) actions.present(actionId, { phase: "confirmed", label: "正在试听", detail: "服务已返回真实音频" });
    } catch (error) {
      if (ownsFeedback()) actions.present(actionId, { phase: "failed", label: "音频未能播放", detail: friendlyError(error) });
    }
    return result;
  }
  async function playVoiceProfileAudio(base64, mediaType) {
    stopAudio();
    const binary = decode(String(base64)), bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    const mime = String(mediaType || "audio/wav").includes("/") ? String(mediaType) : `audio/${String(mediaType || "wav")}`;
    const objectUrl = urls.createObjectURL(new Blob([bytes], { type: mime }));
    const element = new AudioClass(objectUrl);
    let released = false;
    const release = () => { if (!released) { urls.revokeObjectURL(objectUrl); released = true; } };
    const current = { element, release };
    audio = current;
    element.addEventListener("ended", () => { release(); if (audio === current) audio = null; }, { once: true });
    try { await element.play(); }
    catch (error) { release(); if (audio === current) audio = null; throw error; }
  }
  function dispose() { disposed = true; epoch += 1; stopAudio(); }
  return { handleClick, handleInput, handleChange, beforeRender, afterRender,
    previewPayload, inspectFolder, editVoiceProfile, runVoiceProfileTest, synchronizeScope, dispose };
}
