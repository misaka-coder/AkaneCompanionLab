// Observe only on-screen placeholders. Four automatic reads leave room in the
// six-entry scoped cache for explicit previews without an eviction/reload loop.
export function createChatAutoPreviews({ root, open, schedule = requestAnimationFrame, cancel = cancelAnimationFrame }) {
  let frame = 0, disposed = false;
  function scan() {
    frame = 0;
    if (disposed) return;
    const viewport = root.querySelector("[data-chat-viewport]");
    if (!viewport) return;
    const bounds = viewport.getBoundingClientRect();
    const draftBounds = root.querySelector(".chat-attachments")?.getBoundingClientRect();
    const pending = [...root.querySelectorAll(".chat-attachments [data-preview-handle]")].filter(node => {
      const rect = node.getBoundingClientRect();
      return draftBounds && rect.right > draftBounds.left && rect.left < draftBounds.right;
    });
    const messages = [...viewport.querySelectorAll("[data-preview-handle]")].filter(node => {
      const rect = node.getBoundingClientRect();
      return rect.bottom > bounds.top && rect.top < bounds.bottom && rect.right > bounds.left && rect.left < bounds.right;
    });
    const keys = new Set();
    for (const node of [...pending, ...messages]) {
      const item = { handle: node.dataset.previewHandle, itemType: node.dataset.previewType };
      const key = `${item.itemType}:${item.handle}`;
      if (keys.has(key)) continue;
      if (keys.size >= 4) break;
      keys.add(key);
      if (node.hasAttribute("data-chat-auto-preview")) void open(item);
    }
  }
  return {
    refresh() { if (!frame && !disposed) frame = schedule(scan); },
    dispose() { disposed = true; if (frame) cancel(frame); }
  };
}
