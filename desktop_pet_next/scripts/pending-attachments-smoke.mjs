import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { createPendingAttachments } from "../src/pending-attachments.js";
import { renderChat } from "../src/control-center-v2/components/chat.js";

let scope = { bot: "a", session: "one", character: "akane" };
const draft = createPendingAttachments({ readScope: () => scope });
const file = { attachment_id: "opaque-id", handle: "img_1", title: '<img src=x onerror="bad()">', kind: "image", status: "ready", storage_relpath: "private", absolute_path: "private" };
draft.add([file, file]);
assert.equal(draft.list().length, 1);
assert.throws(() => draft.add([{ handle: "display-only" }]), /附件 ID/);
assert.throws(() => draft.add([{ ...file, status: "failed" }]), /尚未就绪/);
assert.equal(draft.list().length, 1, "invalid cards cannot replace the draft");
assert.throws(() => draft.add(Array.from({ length: 41 }, (_, i) => ({ attachment_id: `id-${i}` }))), /40/);
assert.equal(draft.list().length, 1, "over-limit draft mutation is atomic");
assert.ok(!JSON.stringify(draft.list()).includes("private"));
const batch = draft.take();
assert.equal(draft.list().length, 0);
assert.equal(draft.restore(batch), true);
const finish = draft.beginImport();
assert.equal(draft.importing(), 1);
const token = draft.token();
scope = { ...scope, character: "reimu" };
assert.equal(draft.list().length, 0);
assert.notEqual(draft.token(), token);
assert.equal(draft.restore(batch), false);
finish();
assert.equal(draft.importing(), 0);

const main = fs.readFileSync(new URL("../src/main.js", import.meta.url), "utf8");
const between = (start, end) => main.slice(main.indexOf(start), main.indexOf(end, main.indexOf(start) + start.length));
const sent = [];
let accepted = false;
const context = { pendingAttachments: draft, sending: true, proactiveWakeRunning: false,
  rememberInputHistory() {}, restoreFailedInput() {}, setRuntimeStatus() {},
  submitTurnSteer: async (text, ids) => { sent.push({ text, ids }); return accepted; },
};
vm.createContext(context);
vm.runInContext(between("async function sendMessage(", "function createDesktopSourceMessageId("), context);
draft.add([file]);
await context.sendMessage("再看附件");
assert.equal(sent[0].ids[0], "opaque-id");
assert.equal(draft.list().length, 1, "failed steer restores attachment");
accepted = true;
await context.sendMessage("");
assert.equal(sent[1].text, "请查看这些附件。");
assert.equal(draft.list().length, 0);
const pending = draft.beginImport();
assert.equal(await context.sendMessage("too soon"), false);
assert.equal(sent.length, 2, "upload not ready must not send text without attachments");
pending();
draft.add(Array.from({ length: 40 }, (_, i) => ({ attachment_id: `full-${i}` })));
assert.equal(draft.restore({ scope: draft.scope(), items: [{ attachmentId: "old-inflight", handle: "old" }] }), false);
assert.equal(draft.list().length, 40, "failed restore cannot throw or discard newly composed attachments");

const html = renderChat({ viewModel: { actions: { "chat.send": { available: true }, "chat.attach": { available: true } },
  activity: { phase: "using_tool" }, character: { displayName: "Akane" },
  chat: { messages: [], pendingAttachments: [{ attachmentId: "x", ...file, kind: "audio" }] } }, actionStates: {} });
assert.match(html, /<b>追加<\/b>/);
assert.match(html, /<b>停止<\/b>/);
assert.ok(!/<textarea[^>]*disabled/.test(html));
assert.ok(!html.includes('<img src=x onerror="bad()">'));
assert.match(html, /data-chat-attachment-play/);
assert.match(html, /data-action="chat.attach"/);
assert.ok(!between("async function handleDroppedFiles(", "// M66-D: importDroppedFilesToWorkspace").includes("addDroppedAudioFiles"));
assert.match(main, /addElementHitRegion\(regions, attachments, "pending-attachments"\)/);
console.log("pending attachments: production busy-send, upload gate, failure restore, scope isolation, safe rendering and explicit audio controls passed");
