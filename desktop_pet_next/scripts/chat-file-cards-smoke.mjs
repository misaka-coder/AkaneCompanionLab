import assert from "node:assert/strict";
import { performChatFileAction } from "../src/chat-file-action.js";
import { createControlCenterViewModel, isObservedActionConfirmation } from "../src/control-center-v2/view-model.js";
import { renderChat } from "../src/control-center-v2/components/chat.js";
import { mergeChatSessions } from "../src/control-center-v2/chat-history.js";
import { createBackendControlCenterSource } from "../src/control-center/data-sources.js";

const calls = [];
const scope = { backendUrl: "http://localhost:12001", botId: "b", sessionId: "s", realUserId: "u" };
let playing = false;
const deps = { scope, invoke: async (command, args) => { calls.push({ command, args }); return { ok: true, path: "private-cache" }; },
  play: async () => {}, isPlaying: () => playing };
const item = { handle: "gen_1", itemType: "generated", title: "Report", format: "md" };
for (const action of ["open", "reveal", "save_desktop"]) {
  const result = await performChatFileAction({ ...item, action }, deps);
  assert.equal(result.ok, true);
  assert.ok(!JSON.stringify(result).includes("private-cache"));
  assert.equal(calls.at(-1).args.fileName, "Report.md");
  assert.equal(calls.at(-1).args.sessionId, "s");
  assert.equal(calls.at(-1).args.botId, "b");
  assert.equal(calls.at(-1).command, "open_workspace_item");
}
await assert.rejects(performChatFileAction({ ...item, handle: "../../secret", action: "open" }, deps), /invalid_artifact_handle/);
await assert.rejects(performChatFileAction({ ...item, action: "delete" }, deps), /invalid_artifact_action/);
await assert.rejects(performChatFileAction({ ...item, action: "open" }, { ...deps, invoke: async () => ({ ok: false }) }), /file_delivery_failed/);
await assert.rejects(performChatFileAction({ ...item, action: "play" }, deps), /audio_playback_failed/);
playing = true;
assert.equal((await performChatFileAction({ ...item, action: "play" }, deps)).ok, true);

const card = { attachment_id: "id1", handle: "img_1", item_type: "attachment", kind: "image", title: '<img onerror="bad">', format: "png", status: "ready", can_open: true, storage_relpath: "private-cache" };
const viewModel = createControlCenterViewModel({ sourceKind: "backend", controlCenterV2: { liveSnapshotStatus: "connected" },
  chatSession: { session: { session_id: "s" }, messages: [{ role: "user", content: "check", attachments: [card, { title: "附件不可用", status: "unavailable" }] }],
    workspace_outputs: [{ handle: "gen_1", title: "song", kind: "audio", format: "wav", can_open: true, status: "ready" }] },
  overviewRuntime: { recentOutputs: [{ handle: "gen_1", title: "song", kind: "audio", format: "wav", canOpen: true, status: "ready" }] } },
  { state: { sessionId: "s" } });
assert.equal(viewModel.chat.messages[0].attachments[0].handle, "img_1");
assert.ok(!JSON.stringify(viewModel).includes("private-cache"));
const html = renderChat({ viewModel, actionStates: {} });
assert.match(html, /查看图片/);
assert.match(html, /data-chat-file-action="save_desktop"/);
assert.match(html, /data-chat-file-action="play"/);
assert.ok(!html.includes('<img onerror="bad">'));
assert.match(html, /data-chat-file-action="open"[^>]* disabled/);
const confirmation = { settingsCommandResult: { command: "chatFileAction", operationId: "a", ok: true, status: "completed" } };
assert.equal(isObservedActionConfirmation("chat.fileAction", {}, confirmation, { operationId: "a" }), true);
assert.equal(isObservedActionConfirmation("chat.fileAction", {}, confirmation, { operationId: "b" }), false);
assert.deepEqual(mergeChatSessions({ session_id: "s", bot_id: "a", messages: [{ content: "old" }] },
  { session_id: "s", bot_id: "b", messages: [] }).messages, []);
const urls = [];
const source = createBackendControlCenterSource({ baseUrl: "http://localhost:12001", sessionId: "old", botId: "b", profileUserId: "u",
  fetchImpl: async (url, options) => {
    urls.push(url);
    const body = url.includes("/health") ? { status: "ok", root_binding: "valid", instance_id: "host" }
      : url.includes("/sessions/ensure") ? { session: { session_id: JSON.parse(options.body).session_id }, messages: [] }
      : { sections: { outputs: [card] } };
    return { ok: true, status: 200, async json() { return body; } };
  } });
const session = await source.readChatSession({ sessionId: "fresh", characterPackId: "reimu" });
assert.equal(session.session.session_id, "fresh");
assert.equal(session.workspace_outputs[0].handle, "img_1");
assert.equal(session.bot_id, "b");
const workspaceUrl = new URL(urls.find(url => url.includes("/workspace/summary")));
assert.equal(workspaceUrl.searchParams.get("user_id"), "fresh");
assert.equal(workspaceUrl.searchParams.get("character_pack_id"), "reimu");
assert.match(workspaceUrl.pathname, /\/bots\/b\//);
console.log("chat file cards: actual projection/render/actions, safe handles, scoped native delivery, audio outcome and correlated confirmation passed");
