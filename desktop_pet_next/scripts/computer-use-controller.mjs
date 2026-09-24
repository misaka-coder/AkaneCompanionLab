import assert from "node:assert/strict";
import { createComputerUseController } from "../src/control-center-v2/computer-use.js";
let resolveEnable;
let status = "idle";
const calls = [];
const invoke = async (command, args) => {
  calls.push([command, args]);
  if (command === "configure_computer_use") {
    assert.deepEqual(args, { inputEnabled: true });
    return new Promise(resolve => { resolveEnable = resolve; });
  }
  if (command === "stop_computer_use") { status = "stopped"; return { ok: true }; }
  if (command === "get_computer_use_status") return { ok: true, status, input_enabled: false };
  if (command === "get_personal_browser_status") return { ok: true, enabled: true, connected: false, status: "connecting" };
  if (command === "get_computer_use_targets") return { ok: true, targets: [{ id: "local_target_1", title: "Acceptance" }] };
  if (command === "configure_computer_use_target") { assert.equal(args.targetId, "local_target_1"); status = "stopped"; return { ok: true }; }
  throw new Error("fixture_failure");
};
const controller = createComputerUseController({ invoke, available: true });
const enabling = controller.run("enable");
assert.equal(controller.snapshot().busy, true);
assert.equal((await controller.run("refresh")).reason, "operation_in_progress");
assert.equal((await controller.run("stop")).ok, true);
assert.equal(controller.snapshot().status, "stopped");
resolveEnable({ ok: true });
await enabling;
assert.equal(controller.snapshot().status, "stopped", "an older command cannot overwrite stop");
assert.equal(controller.snapshot().chrome_status, "connecting");
assert.equal((await controller.run("chrome_disable")).ok, false);
assert.equal(controller.snapshot().error, "fixture_failure");
assert.equal((await controller.run("refresh_targets")).ok, true);
assert.equal(controller.snapshot().targets[0].id, "local_target_1");
assert.equal((await controller.run("set_target", { targetId: "local_target_1" })).ok, true);
assert.equal(controller.snapshot().status, "stopped");
controller.stop();
const offline = createComputerUseController({ invoke: () => assert.fail("offline must not invoke"), available: false });
assert.equal((await offline.run("enable")).reason, "desktop_required");
console.log("computer-use controller: stale completion, stop while pending, failure, and unavailable states passed");
let nextPoll;
let lost = false;
const owner = createComputerUseController({ available: true,
  invoke: async command => lost ? {ok:false,reason:"local_executor_unavailable"}
    : command === "get_computer_use_status" ? {ok:true,status:"idle",input_enabled:true}
    : {ok:true,enabled:false,status:"idle"},
  schedule: fn => { nextPoll = fn; return 1; }, cancel: () => {},
});
owner.start();
await new Promise(resolve => setTimeout(resolve,0));
assert.equal(owner.snapshot().input_enabled,true);
lost=true;
await nextPoll();
assert.equal(owner.snapshot().status,"unavailable");
assert.equal(owner.snapshot().input_enabled,null,"offline must not retain an enabled claim");
assert.equal(owner.snapshot().error,"local_executor_unavailable");
lost=false;
await nextPoll();
assert.equal(owner.snapshot().input_enabled,true);
owner.stop();
assert.equal(owner.snapshot().error,"");
console.log("computer-use controller: executor loss clears stale permission; reconnect reads owner state");
const unsaved = createComputerUseController({ available: true, invoke: async command => {
  if (command === "configure_computer_use") return {ok:false,reason:"device_preferences_save_failed",input_enabled:true};
  if (command === "get_computer_use_status") return {ok:true,input_enabled:true,status:"idle",preference_error:"device_preferences_save_failed"};
  return {ok:true,enabled:false,status:"idle"};
}});
assert.equal((await unsaved.run("enable")).ok,false);
assert.equal(unsaved.snapshot().input_enabled,true,"a failed save must still show the actual live setting");
assert.equal(unsaved.snapshot().preference_error,"device_preferences_save_failed");
assert.equal(unsaved.snapshot().error,"device_preferences_save_failed");
unsaved.stop();
console.log("computer-use controller: failed persistence preserves truthful live state and visible error");
