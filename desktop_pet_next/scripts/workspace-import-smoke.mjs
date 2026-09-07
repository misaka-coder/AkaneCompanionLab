import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { createWorkspaceImportQueue } from "../src/workspace-import.js";
import { createPendingAttachments } from "../src/pending-attachments.js";

const main = fs.readFileSync(new URL("../src/main.js", import.meta.url), "utf8");
const workspace = fs.readFileSync(new URL("../src/workspace.js", import.meta.url), "utf8");
const rust = fs.readFileSync(new URL("../src-tauri/src/main.rs", import.meta.url), "utf8");
const between = (source, start, end) => {
  const first = source.indexOf(start), last = source.indexOf(end, first + start.length);
  assert.ok(first >= 0 && last > first, start);
  return source.slice(first, last);
};
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const camel = (text) => text.replace(/_([a-z])/g, (_, c) => c.toUpperCase());
const calls = [], refreshed = [], statuses = [];
let response = () => Promise.resolve({ ok: true, imported: 1, items: [{ handle: "att_1" }] });
const context = {
  createWorkspaceImportQueue,
  state: { backendUrl: "http://127.0.0.1:12001", boundBotId: "bot-a", sessionId: "session-a", characterPackId: "reimu" },
  getProfileUserId: () => "master",
  invoke: async (command, args) => {
    // Match default Tauri macro camelCase argument projection against the
    // production Rust signature, not a hand-maintained list of expected keys.
    const signature = rust.match(new RegExp(`#\\[tauri::command\\]\\s+async fn ${command}\\(([\\s\\S]*?)\\) ->`));
    assert.ok(signature, command);
    const keys = [...signature[1].matchAll(/\b([a-z_]+):\s*(?:String|Vec<String>)/g)].map((entry) => camel(entry[1]));
    assert.deepEqual(keys.filter((key) => !(key in args)), [], `${command} required keys`);
    calls.push({ command, args });
    return response();
  },
  notifyWorkspaceRefresh: async () => refreshed.push(true),
  setRuntimeStatus: (text) => statuses.push(text),
  showBubbleText() {}, friendlyErrorMessage: (value) => value,
  formatError: (error) => error.message || String(error),
  buildDroppedAudioItems: () => [],
  sending: false, replyDisplayActive: false, showChatInput() {},
};
vm.createContext(context);
vm.runInContext(between(main, "function attachmentDraftScope()", "const pendingAttachments ="), context);
context.pendingAttachments = createPendingAttachments({ readScope: context.attachmentDraftScope });
vm.runInContext(between(main, "const enqueueWorkspaceImport =", "\n});") + "\n});", context);
vm.runInContext(between(main, "async function importDroppedFilesToWorkspace(", "function buildWorkspaceAudioSourceId("), context);
vm.runInContext(between(main, "function summarizeWorkspaceImportSkipped(", "async function notifyWorkspaceRefresh("), context);
vm.runInContext(between(main, "async function handleDroppedFiles(", "// M66-D: importDroppedFilesToWorkspace"), context);

const first = deferred();
response = () => first.promise;
const a = context.importDroppedFilesToWorkspace(["image.png"]);
const b = context.importDroppedFilesToWorkspace(["notes.txt"]);
await Promise.resolve();
assert.equal(calls.length, 1, "second drop waits, not discarded");
first.resolve({ ok: true, imported: 1 });
await Promise.all([a, b]);
assert.equal(calls.length, 2);
assert.equal(calls[1].args.paths[0], "notes.txt");
assert.equal(refreshed.length, 2);

response = async () => { throw Error("native import failure"); };
await assert.rejects(context.importDroppedFilesToWorkspace(["bad.txt"]), /native import failure/);
response = async () => ({ ok: true, imported: 1, skipped_count: 1, skipped: [{ reason: "empty_file" }] });
await context.handleDroppedFiles(["good.txt", "empty.txt"]);
assert.ok(statuses.at(-1).includes("跳过 1 个"));
response = async () => ({ ok: true, imported: 1, skipped_count: 1, skipped: [{ reason: "duplicate_source" }],
  items: [{ attachment_id: "audio-id", handle: "aud_1", kind: "audio", reused: true }] });
await context.handleDroppedFiles(["same.wav"]);
assert.match(statuses.at(-1), /待发送：1 个附件/);
assert.ok(!statuses.at(-1).includes("跳过"), "reused audio is accepted, not reported as unsupported or skipped");
assert.equal(context.pendingAttachments.list()[0].attachmentId, "audio-id");
response = async () => ({ ok: false, imported: 0, skipped_count: 1, skipped: [{ reason: "empty_file" }] });
await assert.rejects(context.importDroppedFilesToWorkspace(["empty.txt"]), /文件是空的/);

const slow = deferred();
response = () => slow.promise;
const before = calls.length;
const old = context.importDroppedFilesToWorkspace(["old.txt"]);
const queued = context.importDroppedFilesToWorkspace(["queued.txt"]);
await Promise.resolve();
context.state.sessionId = "session-b";
slow.resolve({ ok: true, imported: 1 });
assert.equal((await old).status, "cancelled");
assert.equal((await queued).reason, "attachment_scope_changed");
assert.equal(calls.length, before + 1, "queued old-scope file never uploads");

response = async () => ({ ok: true });
context.workspaceRouteType = () => "attachments";
context.buildWorkspaceExportFileName = () => "note.txt";
vm.runInContext(between(workspace, "async function invokeWorkspaceItemAction(", "function workspaceRouteType("), context);
context.PROFILE_USER_ID = "master";
for (const action of ["open", "reveal", "save_desktop"]) {
  await context.invokeWorkspaceItemAction({ handle: "att_1" }, action);
  assert.equal(calls.at(-1).args.botId, "bot-a");
  assert.equal(calls.at(-1).args.sessionId, "session-b");
}
const conf = JSON.parse(fs.readFileSync(new URL("../src-tauri/tauri.conf.json", import.meta.url), "utf8"));
assert.ok(!conf.app.security.assetProtocol.scope.includes("$APPCACHE/audio/**"));
assert.ok(rust.includes('let audio_cache_dir = desktop_runtime_cache_dir("attachments/audio")?;'));
assert.ok(rust.includes('.allow_directory(&audio_cache_dir, true)'));
assert.ok(between(rust, "async fn import_dropped_files(", "fn list_character_packs(").includes('"skipped_count": skipped_items.len()'));
console.log("workspace-import: queue, scope isolation, native argument contracts, error recovery, partial feedback and audio scope passed (no live effects)");
