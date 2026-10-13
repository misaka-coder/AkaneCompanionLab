import { createChatImagePreviews } from "../../control-center/chat-image-preview.js";

export function createBridgeImagePreviews({ readSource, readRuntime, readViewModel, publish,
  isActive = () => true, readScope }) {
  const imagePreviews = createChatImagePreviews({
    readScope: readScope || (() => {
      const source = readSource(), live = readRuntime()?.state || {};
      return JSON.stringify([source?.backendUrl, source?.botId, source?.profileUserId,
        source?.sessionId, source?.characterPackId, live.backendUrl, live.boundBotId,
        live.profileUserId, live.sessionId, live.characterPackId]);
    }),
    load(item, signal) {
      const source = readSource(), live = readRuntime()?.state || {};
      if (!isActive() || (live.boundBotId && live.boundBotId !== source?.botId)
        || (live.profileUserId && live.profileUserId !== source?.profileUserId)
        || (live.backendUrl && live.backendUrl.replace(/\/+$/, "") !== source?.backendUrl?.replace(/\/+$/, ""))) {
        throw Error("preview_scope_changed");
      }
      return source.readChatImagePreview(item, { sessionId: live.sessionId, characterPackId: live.characterPackId }, signal);
    },
    changed: publish,
  });

  async function previewChatImage(item, action = "open") {
    const viewModel = readViewModel();
    const candidates = [...(viewModel?.chat?.pendingAttachments || []), ...(viewModel?.chat?.outputs || []),
      ...(viewModel?.chat?.messages || []).flatMap(message => message.attachments || [])];
    const card = candidates.find(card => card.handle === item.handle && (card.itemType || "attachment") === item.itemType);
    if (!card || !(card.kind === "image" || /^(png|jpe?g|webp|gif|bmp)$/i.test(card.format))) return;
    if (action === "close") return imagePreviews.close(item);
    if (action === "failed") return imagePreviews.failed(item);
    if (!isActive() || !viewModel?.shell.connected || !readSource()?.readChatImagePreview) return;
    return imagePreviews.open({ ...card, itemType: item.itemType });
  }

  return { snapshot: imagePreviews.snapshot, dispose: imagePreviews.dispose, previewChatImage };
}
