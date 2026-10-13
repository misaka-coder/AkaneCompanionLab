import { CLIENT_MODE } from "./constants.js";

export function createTestChatTransport({
  buildTestCurrentVisual,
  backendFetch,
  buildBackendUrl,
  isTauriRuntime,
  readNdjsonEvents,
  isTestRequestCurrent,
  applyTestPayload,
  updateTestMessageDiagnostics,
  view,
  renderTestChatTranscript,
}) {
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

  return { requestWorkshopThink, consumeWorkshopThinkStream };
}
