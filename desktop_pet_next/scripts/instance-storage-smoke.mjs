import assert from "node:assert/strict";
import {
  bindInstanceStorage,
  getInstanceStorageItem,
  instanceStorageKey,
  setInstanceStorageItem
} from "../src/instance-storage.js";

class MemoryStorage {
  constructor() {
    this.values = new Map();
  }

  getItem(key) {
    return this.values.has(key) ? this.values.get(key) : null;
  }

  setItem(key, value) {
    this.values.set(key, String(value));
  }

  removeItem(key) {
    this.values.delete(key);
  }
}

const storage = new MemoryStorage();
const sharedIds = new Map([
  ["controlCenter.backendUrl", "http://127.0.0.1:9999"],
  ["controlCenter.sessionId", "same-session"],
  ["controlCenter.profileUserId", "same-profile"],
  ["character.activePackId", "same-character"],
  ["workshop.draft:same-character", JSON.stringify({ name: "same-character", value: "A draft" })]
]);

bindInstanceStorage("instance-a");
for (const [key, value] of sharedIds) {
  assert.equal(setInstanceStorageItem(key, value, { storage }), true);
}
const aSessionKey = instanceStorageKey("controlCenter.sessionId");

bindInstanceStorage("instance-b");
for (const key of sharedIds.keys()) {
  assert.equal(getInstanceStorageItem(key, { storage }), null);
}
setInstanceStorageItem("controlCenter.backendUrl", sharedIds.get("controlCenter.backendUrl"), { storage });
setInstanceStorageItem("controlCenter.sessionId", sharedIds.get("controlCenter.sessionId"), { storage });
setInstanceStorageItem("controlCenter.profileUserId", sharedIds.get("controlCenter.profileUserId"), { storage });
setInstanceStorageItem("character.activePackId", sharedIds.get("character.activePackId"), { storage });
setInstanceStorageItem("workshop.draft:same-character", JSON.stringify({ name: "same-character", value: "B draft" }), { storage });
const bSessionKey = instanceStorageKey("controlCenter.sessionId");
assert.notEqual(aSessionKey, bSessionKey);

bindInstanceStorage("instance-a");
assert.equal(getInstanceStorageItem("controlCenter.sessionId", { storage }), sharedIds.get("controlCenter.sessionId"));
assert.equal(JSON.parse(getInstanceStorageItem("workshop.draft:same-character", { storage })).value, "A draft");

bindInstanceStorage("instance-b");
assert.equal(getInstanceStorageItem("controlCenter.profileUserId", { storage }), sharedIds.get("controlCenter.profileUserId"));
assert.equal(JSON.parse(getInstanceStorageItem("workshop.draft:same-character", { storage })).value, "B draft");

console.log("instance storage smoke: ok");
