import assert from "node:assert/strict";
import test from "node:test";
import { createWorkshopState } from "../src/workshop/state.js";
import { createPortraitAssetCache } from "../src/workshop/asset-cache.js";

function deferred() {
  let resolve, reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return { promise, resolve, reject };
}

test("workshop instances own their view, revisions, pending writes, and image caches", () => {
  const first = createWorkshopState(), second = createWorkshopState();
  first.view.packs.push({ id: "fixture" });
  first.navigationState.workspaceVisit++;
  first.writeState.storySaves.add("fixture");
  first.personaState.personaRevisions.set("fixture", 1);
  first.assetState.portraitImageUrlCache.set("fixture", "fixture-url");
  assert.deepEqual(second.view.packs, []);
  assert.equal(second.navigationState.workspaceVisit, 0);
  assert.equal(second.writeState.storySaves.size, 0);
  assert.equal(second.personaState.personaRevisions.size, 0);
  assert.equal(second.assetState.portraitImageUrlCache.size, 0);
  for (const [bucket, state] of Object.entries(first)) {
    assert.notEqual(state, second[bucket], bucket);
    for (const [key, value] of Object.entries(state)) {
      if (value instanceof Set || value instanceof Map) assert.notEqual(value, second[bucket][key], `${bucket}.${key}`);
    }
  }
});

test("a failed invalidated portrait read cannot delete a newer cached request", async () => {
  const { assetState } = createWorkshopState();
  const previous = deferred(), current = deferred();
  let reads = 0;
  const cache = createPortraitAssetCache({ assetState, invoke: async (command, args) => {
    assert.equal(command, "read_portrait_image");
    assert.deepEqual(args, { packId: "fixture", outfit: "default", emotion: "normal" });
    return ++reads === 1 ? previous.promise : current.promise;
  } });
  const oldRead = cache.getPortraitImageUrl("fixture", "default", { id: "normal", path: "normal.png" });
  oldRead.catch(() => {});
  cache.clearPortraitImageCache("fixture");
  const freshRead = cache.getPortraitImageUrl("fixture", "default", { id: "normal", path: "normal.png" });
  const key = "fixture\u0000default\u0000normal";
  const freshCachedRequest = assetState.portraitImageUrlCache.get(key);
  previous.reject(new Error("fixture old image read rejected"));
  await assert.rejects(oldRead, /fixture old image read rejected/);
  assert.equal(assetState.portraitImageUrlCache.get(key), freshCachedRequest);
  current.resolve([1, 2, 3]);
  const url = await freshRead;
  assert.equal(await cache.getPortraitImageUrl("fixture", "default", { id: "normal" }), url);
  assert.equal(reads, 2, "fresh request remains deduplicated");
  cache.clearPortraitImageCache("fixture");
});
