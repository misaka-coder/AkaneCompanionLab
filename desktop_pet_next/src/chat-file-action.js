// Shared file delivery entry for historical attachment and generated-output cards.
// No caller-supplied URL/path is accepted; the native broker resolves scoped handles.
export async function performChatFileAction(payload, { scope, invoke, play, isPlaying }) {
  const handle = String(payload.handle || "").trim();
  if (!/^[a-zA-Z0-9_-][a-zA-Z0-9_.-]{0,127}$/.test(handle)) throw Error("invalid_artifact_handle");
  if (!["attachment", "generated"].includes(payload.itemType)) throw Error("invalid_artifact_type");
  if (!["open", "reveal", "save_desktop", "play"].includes(payload.action)) throw Error("invalid_artifact_action");
  const item = { handle, itemType: payload.itemType, title: String(payload.title || handle), format: String(payload.format || "") };
  if (payload.action === "play") {
    await play(item);
    if (!isPlaying(item)) throw Error("audio_playback_failed");
  } else {
    const fileName = item.format && !item.title.toLowerCase().endsWith(`.${item.format.toLowerCase()}`)
      ? `${item.title}.${item.format}` : item.title;
    const result = await invoke("open_workspace_item", { handle, itemType: item.itemType === "generated" ? "generated" : "attachments",
      action: payload.action, backendUrl: scope.backendUrl, botId: scope.botId,
      userId: scope.sessionId, sessionId: scope.sessionId, realUserId: scope.realUserId, fileName });
    if (!result?.ok) throw Error(result?.reason || "file_delivery_failed");
  }
  return { ok: true, status: "completed" }; // Never forward native cache/export paths into snapshots.
}
