import assert from "node:assert/strict";

import {
  attachDesktopCareContext,
  cloneCareState,
  createUnresolvedCareFeature,
  isCareFeatureEnabled,
  resolveCareFeatureFromHealth,
  clampCareValue,
  normalizeCareDecayConfig,
  normalizeCareFeedback,
  normalizeCareWorkConfig,
  normalizeCareAllowanceConfig,
  normalizeCareWorkTask,
  normalizeCareState,
  formatSignedCareDelta,
  formatCareFeedbackText
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

// Config normalization & helper contracts
assert.equal(clampCareValue(50, 0, 100), 50);
assert.equal(clampCareValue(-5, 0, 100), 0);
assert.equal(clampCareValue(150, 0, 100), 100);

const decay = normalizeCareDecayConfig({});
assert.equal(decay.hungerPerHour, 4);
assert.equal(decay.energyPerReply, 1);
assert.equal(decay.energyPerProactive, 0);

const feedback = normalizeCareFeedback({ emotion: "happy", bubble: { text: "谢谢", durationMs: 2000 } });
assert.equal(feedback.emotion, "happy");
assert.equal(feedback.bubble.text, "谢谢");
assert.equal(feedback.bubble.durationMs, 2000);

const work = normalizeCareWorkConfig({ enabled: true, rewardCoinsMin: 10, rewardCoinsMax: 5 });
assert.equal(work.rewardCoinsMin, 5);
assert.equal(work.rewardCoinsMax, 10);

const allowance = normalizeCareAllowanceConfig({ enabled: true, coins: 6 });
assert.equal(allowance.coins, 6);

assert.equal(normalizeCareWorkTask({}), null);
const task = normalizeCareWorkTask({ startedAt: 100, completeAt: 200, rewardCoins: 15 });
assert.equal(task?.status, "active");
assert.equal(task?.completeAt, 200);

const state = normalizeCareState({ coins: 50, hunger: 80, inventory: { tea: 2, invalid: 0 } }, { enabled: true, initialCoins: 10 });
assert.equal(state.coins, 50);
assert.equal(state.hunger, 80);
assert.deepEqual(state.inventory, { tea: 2 });

assert.equal(formatSignedCareDelta(5), "+5");
assert.equal(formatSignedCareDelta(-3), "-3");
assert.equal(formatSignedCareDelta(0), "0");

assert.equal(formatCareFeedbackText("获得 {reward} 金币，现有 {coins}", { reward: 10, coins: 50 }), "获得 10 金币，现有 50");

console.log("care feature smoke: ok");
