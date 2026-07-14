import assert from "node:assert/strict";

import {
  attachDesktopCareContext,
  cloneCareState,
  createUnresolvedCareFeature,
  isCareFeatureEnabled,
  resetCareEvaluationBaseline,
  resolveCareFeatureFromHealth
} from "../src/care-feature.js";

const unresolved = createUnresolvedCareFeature();
assert.equal(isCareFeatureEnabled(unresolved), false);

const persisted = {
  hunger: 73,
  energy: 44,
  affection: 21,
  coins: 12,
  lastDecayAt: 100,
  inventory: { tea: 2 },
  workTask: { completeAt: 500 }
};
assert.deepEqual(cloneCareState(persisted), persisted);
const reenabled = resetCareEvaluationBaseline(persisted, 1000);
assert.equal(reenabled.hunger, 73);
assert.equal(reenabled.energy, 44);
assert.equal(reenabled.affection, 21);
assert.equal(reenabled.coins, 12);
assert.deepEqual(reenabled.inventory, { tea: 2 });
assert.deepEqual(reenabled.workTask, { completeAt: 500 });
assert.equal(reenabled.lastDecayAt, 1000);
assert.equal(persisted.lastDecayAt, 100);

const disabled = resolveCareFeatureFromHealth({
  features: { care: { enabled: false, reason: "feature_disabled" } }
});
assert.deepEqual(disabled, {
  enabled: false,
  status: "disabled",
  reason: "feature_disabled",
  resetBaselineOnStart: false,
  contractSource: "host"
});
assert.deepEqual(
  attachDesktopCareContext({ message: "hi", desktop_care: { hunger: 1 } }, { hunger: 2 }, disabled),
  { message: "hi" }
);

const enabled = resolveCareFeatureFromHealth({
  features: { care: { enabled: true, reset_baseline_on_start: true } }
});
assert.equal(isCareFeatureEnabled(enabled), true);
assert.equal(enabled.resetBaselineOnStart, true);
assert.deepEqual(
  attachDesktopCareContext({ message: "hi" }, { enabled: true, hunger: 80 }, enabled),
  { message: "hi", desktop_care: { enabled: true, hunger: 80 } }
);

const legacy = resolveCareFeatureFromHealth({ status: "ok" });
assert.equal(isCareFeatureEnabled(legacy), true);
assert.equal(legacy.reason, "legacy_contract");

console.log("care feature smoke: ok");
