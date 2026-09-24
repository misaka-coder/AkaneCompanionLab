const MAX_PREVIEW_BYTES = 8 * 1024 * 1024;
const RASTER_MIMES = new Set(["image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp"]);

// Read the existing artifact content endpoint; never follow a material-provided URL.
export async function readChatImageBlob({ fetchImpl, baseUrl, instanceId, profileUserId, sessionId,
  characterPackId, handle, itemType, signal }) {
  if (!/^[a-zA-Z0-9_-][a-zA-Z0-9_.-]{0,127}$/.test(String(handle || ""))) throw Error("invalid_artifact_handle");
  if (!["attachment", "generated"].includes(itemType)) throw Error("invalid_artifact_type");
  const url = new URL(`${baseUrl.replace(/\/+$/, "")}/desktop-pet/workspace/${itemType === "generated" ? "generated" : "attachments"}/${encodeURIComponent(handle)}/content`);
  for (const [key, value] of Object.entries({ user_id: sessionId, session_id: sessionId, real_user_id: profileUserId, character_pack_id: characterPackId })) {
    url.searchParams.set(key, String(value || ""));
  }
  const response = await fetchImpl(url.toString(), { cache: "no-store", redirect: "error", maxRedirections: 0, signal });
  try {
    if (response.url && response.url !== url.toString()) throw Error("preview_redirect_rejected");
    if (!response.ok) throw Error(response.status === 404 ? "preview_unavailable" : "preview_read_failed");
    if (!instanceId || response.headers.get("x-akane-artifact-instance") !== instanceId) throw Error("preview_instance_mismatch");
    const size = Number(response.headers.get("x-akane-artifact-size"));
    const mime = String(response.headers.get("content-type") || "").split(";")[0].trim().toLowerCase();
    if (!RASTER_MIMES.has(mime)) throw Error("preview_format_unsupported");
    if (response.headers.get("x-akane-artifact-mime") !== mime) throw Error("preview_format_mismatch");
    if (!Number.isSafeInteger(size) || size <= 0 || size > MAX_PREVIEW_BYTES) throw Error("preview_too_large");
    const hash = String(response.headers.get("x-akane-artifact-sha256") || "").toLowerCase();
    if (!/^[a-f0-9]{64}$/.test(hash)) throw Error("preview_integrity_missing");
    const reader = response.body?.getReader();
    if (!reader) throw Error("preview_stream_unavailable");
    const chunks = [];
    let count = 0;
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        count += value.byteLength;
        if (count > size || count > MAX_PREVIEW_BYTES) throw Error("preview_size_mismatch");
        chunks.push(value);
      }
      if (count !== size) throw Error("preview_size_mismatch");
    } catch (error) {
      await reader.cancel().catch(() => {});
      throw error;
    } finally { reader.releaseLock(); }
    const blob = new Blob(chunks, { type: mime });
    const actual = [...new Uint8Array(await crypto.subtle.digest("SHA-256", await blob.arrayBuffer()))]
      .map(value => value.toString(16).padStart(2, "0")).join("");
    if (actual !== hash) throw Error("preview_integrity_mismatch");
    return blob;
  } catch (error) {
    await response.body?.cancel().catch(() => {});
    throw error;
  }
}

export const imagePreviewKey = item => `${item.itemType || "attachment"}:${item.handle}`;

// Preview URLs stay in this window only. Scope changes abort reads and revoke all URLs.
export function createChatImagePreviews({ readScope, load, changed, createUrl = blob => URL.createObjectURL(blob),
  revokeUrl = url => URL.revokeObjectURL(url), maxEntries = 6 }) {
  let scope = "";
  const entries = new Map();
  function clear() {
    for (const entry of entries.values()) { entry.controller?.abort(); if (entry.url) revokeUrl(entry.url); }
    entries.clear();
  }
  function sync() { const next = readScope(); if (scope !== next) { clear(); scope = next; } return next; }
  function remove(key) {
    const entry = entries.get(key);
    entry?.controller?.abort();
    if (entry?.url) revokeUrl(entry.url);
    entries.delete(key);
  }
  return {
    snapshot() {
      sync();
      return Object.fromEntries([...entries].map(([key, entry]) => [key, { status: entry.status, url: entry.url || "", reason: entry.reason || "" }]));
    },
    async open(item) {
      const expected = sync(), key = imagePreviewKey(item);
      if (["loading", "ready"].includes(entries.get(key)?.status)) return;
      if (entries.size >= maxEntries && !entries.has(key)) remove(entries.keys().next().value);
      const entry = { status: "loading", controller: new AbortController() };
      entries.set(key, entry); changed();
      const timer = setTimeout(() => entry.controller.abort(), 20_000);
      try {
        const blob = await load(item, entry.controller.signal);
        if (sync() !== expected || entries.get(key) !== entry) return;
        if (entry.controller.signal.aborted) throw Error("preview_timeout");
        entry.url = createUrl(blob); entry.status = "ready";
      } catch (error) {
        if (sync() !== expected || entries.get(key) !== entry) return;
        entry.status = "failed";
        entry.reason = entry.controller.signal.aborted ? "preview_timeout"
          : /^preview_[a-z_]+$/.test(error?.message) ? error.message : "preview_read_failed";
      } finally { clearTimeout(timer); }
      changed();
    },
    close(item) { sync(); remove(imagePreviewKey(item)); changed(); },
    failed(item) {
      sync(); const key = imagePreviewKey(item); remove(key);
      entries.set(key, { status: "failed", reason: "preview_decode_failed" }); changed();
    },
    dispose() { clear(); },
  };
}
