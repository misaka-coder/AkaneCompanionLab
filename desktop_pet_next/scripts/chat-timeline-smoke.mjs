import assert from "node:assert/strict";
import { chatTimeline, chatFileKind, chatFileSize } from "../src/control-center-v2/chat-timeline.js";
import { createChatAutoPreviews } from "../src/control-center-v2/chat-auto-previews.js";
import { renderChat } from "../src/control-center-v2/components/chat.js";

const pic = { itemType: "generated", handle: "image", timestamp: 20, format: "png", title: "截图.png", canOpen: true };
const chat = { messages: [{ id: "first", timestamp: 10, role: "user", content: "截一张图" },
  { id: "last", timestamp: 30, role: "assistant", content: "已完成" }], outputs: [pic, pic,
  { ...pic, handle: "earlier", timestamp: 5 }, { ...pic, handle: "legacy", timestamp: 0 }] };
assert.deepEqual(chatTimeline(chat).map(x => x.id), ["artifact:generated:earlier", "first", "artifact:generated:image", "last", "artifact:generated:legacy"]);
assert.equal(chat.messages.length, 2, "timeline must not mutate retained history");
assert.equal(chatTimeline({ messages: [{ id: "attached", attachments: [pic] }], outputs: [pic] }).length, 1);
assert.equal(chatFileKind(pic), "image", "generated artifacts need extension-based image detection");
assert.equal(chatFileKind({ format: "WAV" }), "audio");
assert.equal(chatFileKind({ format: "docx" }), "file");
assert.equal(chatFileSize(1048576), "1.0 MB");
const state = { actionStates: {}, viewModel: { shell: { connected: true }, actions: {
  "chat.attach": { available: true }, "chat.send": { available: true }, "chat.fileAction": { available: true }
}, chat: { ...chat, latestOutcome: { statusLabel: "final", tone: "good", events: [] } } } };
let html = renderChat(state);
assert.equal((html.match(/data-message-id="artifact:generated:image"/g) || []).length, 1);
assert.ok(!html.includes("最近一次调用回执"), "empty successful receipts should not take up the message viewport");
assert.match(html, /data-chat-auto-preview/);
assert.match(html, /aria-label="待发送附件"><\/div>/, "empty attachment strip can collapse");
state.viewModel.chat.previews = { "generated:image": { status: "failed", reason: "preview_timeout" } };
html = renderChat(state);
assert.match(html, /预览超时，可重试/);
assert.ok(!/data-preview-handle="image"[^>]*data-chat-auto-preview/.test(html), "failed loads require explicit retry");
state.viewModel.shell.connected = false;
html = renderChat(state);
assert.ok(!html.includes("data-chat-auto-preview"), "offline cards cannot trigger image downloads");

let callback, scheduled = 0, canceled = 0;
const reads = [];
const rect = { left: 0, right: 500, top: 0, bottom: 500 };
const node = (handle, top = 10, status = "idle", left = 0) => ({
  dataset: { previewHandle: handle, previewType: "attachment" },
  getBoundingClientRect: () => ({ left, right: left + 100, top, bottom: top + 80 }),
  hasAttribute: name => name === "data-chat-auto-preview" && status === "idle"
});
let pending = [node("draft")], messages = [node("outside", 600), node("ready", 20, "ready"),
  node("draft"), node("loading", 30, "loading"), node("visible"), node("fifth")];
const viewport = { getBoundingClientRect: () => rect, querySelectorAll: () => messages };
const root = { querySelector: selector => selector === "[data-chat-viewport]" ? viewport : { getBoundingClientRect: () => rect },
  querySelectorAll: () => pending };
const loader = createChatAutoPreviews({ root, open: item => reads.push(item.handle),
  schedule: fn => { callback = fn; return ++scheduled; }, cancel: () => canceled++ });
loader.refresh(); loader.refresh();
assert.equal(scheduled, 1, "repeated state publications share a single frame");
callback();
assert.deepEqual(reads, ["draft", "visible"], "visible ready/loading entries count toward the four-image cache budget");
pending = [node("offscreen-draft", 10, "idle", 600)];
messages = [node("failed", 30, "failed"), node("new-visible")];
loader.refresh(); callback();
assert.deepEqual(reads, ["draft", "visible", "new-visible"]);
loader.refresh(); loader.dispose(); callback(); loader.refresh();
assert.equal(canceled, 1);
assert.equal(reads.length, 3, "disposed page cannot start reads");
console.log("chat timeline: chronological artifacts, deduplication, no empty receipt, retry/offline states and bounded visible auto-previews passed");
