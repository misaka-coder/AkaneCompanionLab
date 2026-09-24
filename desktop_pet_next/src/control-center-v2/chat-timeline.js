import { imagePreviewKey } from "../control-center/chat-image-preview.js";

// Artifact timestamps are independent events, not a guessed association with
// the last assistant sentence. A handle already present on a message appears once.
export function chatTimeline(chat = {}) {
  const messages = [...(chat.messages || [])];
  const seen = new Set(messages.flatMap(message => (message.attachments || []).map(imagePreviewKey)));
  const outputs = (chat.outputs || []).filter(item => {
    if (!item.handle || seen.has(imagePreviewKey(item))) return false;
    seen.add(imagePreviewKey(item));
    return true;
  }).sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0));
  for (const item of outputs) {
    const entry = { id: `artifact:${imagePreviewKey(item)}`, role: "assistant", content: "",
      timestamp: item.timestamp || 0, artifact: true, attachments: [item] };
    const next = item.timestamp > 0 ? messages.findIndex(message => message.timestamp > item.timestamp) : -1;
    messages.splice(next < 0 ? messages.length : next, 0, entry);
  }
  return messages;
}

export function chatFileKind(item = {}) {
  if (item.kind === "image" || /^(png|jpe?g|webp|gif|bmp)$/i.test(item.format)) return "image";
  if (item.kind === "audio" || /^(mp3|wav|flac|ogg|m4a|aac|opus)$/i.test(item.format)) return "audio";
  return "file";
}

export function chatFileSize(bytes) {
  const size = Number(bytes);
  if (!(size > 0)) return "";
  return size >= 1048576 ? `${(size / 1048576).toFixed(1)} MB`
    : size >= 1024 ? `${Math.ceil(size / 1024)} KB` : `${Math.ceil(size)} B`;
}
