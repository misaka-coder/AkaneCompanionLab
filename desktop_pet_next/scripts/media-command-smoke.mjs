import assert from "node:assert/strict";
import { createMediaCommandQueue } from "../src/media-control.js";

let track = 0, release;
const calls = [];
const blocked = new Promise(resolve => { release = resolve; });
const command = createMediaCommandQueue(async (action, payload) => {
  calls.push(payload.operationId);
  if (payload.operationId === "first") await blocked;
  if (action === "next") track += 1;
  return { ok: true, track };
});
const first = command("next", { operationId: "first" });
const duplicate = command("next", { operationId: "first" });
const second = command("next", { operationId: "second" });
assert.equal(first, duplicate);
await Promise.resolve();
await Promise.resolve();
assert.deepEqual(calls, ["first"]);
assert.equal((await command("pause", { operationId: "first" })).reason, "operation_id_conflict");
release();
assert.equal((await first).track, 1);
assert.equal((await second).track, 2);
assert.deepEqual(calls, ["first", "second"]);
await command("next", { operationId: "first" });
assert.equal(track, 2, "replayed completed command must not skip again");
let attempts = 0;
const failures = createMediaCommandQueue(async () => {
  if (++attempts === 1) throw new Error("transport failed");
  return { ok: false, status: "execution_unknown" };
});
await assert.rejects(failures("next", { operationId: "failed" }));
assert.equal((await failures("next", { operationId: "unknown" })).status, "execution_unknown");
await failures("next", { operationId: "unknown" });
assert.equal(attempts, 2, "unknown outcome must not cause automatic resend");
console.log("media command ordering, replay, conflict, failure and unknown outcome: passed");
