import assert from "node:assert/strict";
import { test } from "node:test";
import { createWorkspaceItemsView, itemKey } from "../src/workspace/items.js";
import { createWorkspaceMusicView } from "../src/workspace/music.js";
import { testView } from "./workspace-fixture.mjs";

test("file and output cards with the same ID retain their own action target", () => {
  const view = testView(), items = createWorkspaceItemsView(view);
  const file = { id: "same", title: "raw", can_open: true }, output = { id: "same", title: "result", can_clear: true };
  items.renderPayload({ sections: { files: [file], outputs: [output] } });
  assert.equal(items.findItem("file:same"), file);
  assert.equal(items.findItem("output:same"), output);
  assert.equal(itemKey(output), "same");
  assert.equal(view.els.fileCount.textContent, "1");
  assert.equal(view.els.outputCount.textContent, "1");
});

test("loading and empty views immediately discard former actionable cards", () => {
  const view = testView(), items = createWorkspaceItemsView(view);
  items.renderPayload({ sections: { files: [{ id: "old", can_clear: true }] } });
  items.renderLoading();
  assert.equal(items.findItem("file:old"), undefined);
  assert.equal(view.els.clearFiles.disabled, true);
  assert.equal(view.els.fileCount.textContent, "0");
});

test("music queue index zero displays the first track and preserves controls and lyrics", () => {
  const view = testView(), renderMusicPanel = createWorkspaceMusicView(view);
  renderMusicPanel({ playing: true, queueIndex: 0, queueCount: 2, hasNext: true,
    track: { sourceId: "a", displayName: "Song" }, queue: [{ sourceId: "a" }, { sourceId: "b" }], currentLyric: { text: "line" } });
  assert.match(view.els.music.textContent, /Song · 1\/2/);
  assert.match(view.els.music.textContent, /歌词：line/);
  const buttons = view.els.music.querySelectorAll("button");
  assert.equal(buttons.find(button => button.dataset.musicAction === "next").disabled, false);
  assert.equal(buttons.filter(button => button.dataset.musicAction === "play")[0].disabled, true);
  renderMusicPanel(null);
  assert.match(view.els.music.textContent, /当前没有音乐队列/);
});

test("file titles and error strings remain literal text", () => {
  const view = testView(), items = createWorkspaceItemsView(view);
  const title = '<img src=x onerror="throw 1">';
  items.renderPayload({ sections: { files: [{ id: "literal", title }] } });
  view.setAlert(title, "error");
  assert.ok(view.els.content.textContent.includes(title));
  assert.equal(view.els.alert.textContent, title);
});
