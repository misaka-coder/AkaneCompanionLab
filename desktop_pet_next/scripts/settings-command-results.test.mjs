import assert from "node:assert/strict";
import test from "node:test";
import { loadPetRuntime, createEnvironment } from "./pet-runtime-harness.mjs";
import {
  isObservedActionConfirmation,
  observedActionOutcome
} from "../src/control-center-v2/view-model/action-results.js";
import {
  CONTROL_CENTER_ACTIONS,
  CONTROL_CENTER_BRIDGED_ACTION_IDS
} from "../src/control-center/action-router.js";
import {
  SETTINGS_ACTION_SPECS,
  SETTINGS_COMMAND_BY_ACTION_ID,
  OBSERVED_SETTINGS_ACTION_IDS,
  getSettingsActionTimeout,
  DEFAULT_CONFIRMATION_TIMEOUT_MS
} from "../src/control-center/settings-command-contract.js";

test("action contract consistency and derivation", () => {
  assert.equal(CONTROL_CENTER_BRIDGED_ACTION_IDS.length, 87);
  assert.deepEqual([...CONTROL_CENTER_BRIDGED_ACTION_IDS].sort(), Object.values(CONTROL_CENTER_ACTIONS).sort());

  assert.equal(Object.keys(SETTINGS_COMMAND_BY_ACTION_ID).length, 44);
  assert.equal(OBSERVED_SETTINGS_ACTION_IDS.size, 34);
  assert.deepEqual(SETTINGS_COMMAND_BY_ACTION_ID, Object.fromEntries(
    Object.entries(SETTINGS_ACTION_SPECS).map(([actionId, spec]) => [actionId, spec.command])
  ));
  assert.deepEqual([...OBSERVED_SETTINGS_ACTION_IDS].sort(), Object.entries(SETTINGS_ACTION_SPECS)
    .filter(([, spec]) => spec.observed).map(([actionId]) => actionId).sort());

  const unobserved = Object.keys(SETTINGS_COMMAND_BY_ACTION_ID).filter(id => !OBSERVED_SETTINGS_ACTION_IDS.has(id));
  assert.deepEqual(unobserved.sort(), [
    "advanced.probeClickThrough",
    "advanced.resetWindow",
    "advanced.toggleWebgl",
    "music.clear",
    "music.playWorkspaceRecommendation",
    "music.seek",
    "music.selectQueueItem",
    "music.setPlayMode",
    "music.setVolumeNormalization",
    "workspace.open"
  ].sort());

  assert.equal(getSettingsActionTimeout("chat.attach"), 180000);
  assert.equal(getSettingsActionTimeout("chat.fileAction"), 60000);
  assert.equal(getSettingsActionTimeout("chat.playAttachment"), 30000);
  assert.equal(getSettingsActionTimeout("voice.test"), 12000);
  assert.equal(getSettingsActionTimeout("voice.previewPlay"), 12000);
  assert.equal(getSettingsActionTimeout("music.previous"), 4000);
  assert.equal(getSettingsActionTimeout("music.next"), 4000);
  assert.equal(getSettingsActionTimeout("music.togglePlayback"), 4000);
  assert.equal(getSettingsActionTimeout("music.stop"), 4000);
  assert.equal(getSettingsActionTimeout("character.selectPack"), 4000);
  assert.equal(getSettingsActionTimeout("perception.screenVision.setEnabled", { value: true }), 60000);
  assert.equal(getSettingsActionTimeout("perception.screenVision.setEnabled", { value: false }), DEFAULT_CONFIRMATION_TIMEOUT_MS);
  assert.equal(getSettingsActionTimeout("voice.stop"), DEFAULT_CONFIRMATION_TIMEOUT_MS);
  assert.equal(getSettingsActionTimeout("character.refresh"), DEFAULT_CONFIRMATION_TIMEOUT_MS);
});

test("idle stop confirmation confirms when stopped without requiring active before-state", () => {
  const idleBefore = { active: { speaking: false, sending: false, replyDisplayActive: false } };
  const stoppedAfter = {
    active: { speaking: false, sending: false, replyDisplayActive: false },
    settingsCommandResult: { command: "stopTts", operationId: "voice-op-1", ok: true, status: "completed" }
  };

  // voice.stop with operationId confirms when pet is already idle
  assert.equal(
    isObservedActionConfirmation("voice.stop", idleBefore, stoppedAfter, { operationId: "voice-op-1" }),
    true
  );

  // chat.stop with operationId confirms when pet is already idle
  const chatStoppedAfter = {
    active: { speaking: false, sending: false, replyDisplayActive: false },
    settingsCommandResult: { command: "stopReply", operationId: "chat-op-1", ok: true, status: "completed" }
  };
  assert.equal(
    isObservedActionConfirmation("chat.stop", idleBefore, chatStoppedAfter, { operationId: "chat-op-1" }),
    true
  );
  for (const [actionId, snapshot, operationId] of [
    ["voice.stop", stoppedAfter, "voice-op-1"],
    ["chat.stop", chatStoppedAfter, "chat-op-1"]
  ]) {
    assert.equal(isObservedActionConfirmation(actionId, idleBefore, snapshot, { operationId: "new-op" }), false);
    for (const status of ["queued", "running", "accepted"]) {
      const pending = { ...snapshot, settingsCommandResult: { ...snapshot.settingsCommandResult, status } };
      assert.equal(isObservedActionConfirmation(actionId, idleBefore, pending, { operationId }), false);
    }
    const requiredFields = actionId === "voice.stop" ? ["speaking"] : ["sending", "replyDisplayActive"];
    for (const field of requiredFields) {
      const incomplete = { ...snapshot, active: { ...snapshot.active } };
      delete incomplete.active[field];
      assert.equal(isObservedActionConfirmation(actionId, idleBefore, incomplete, { operationId }), false);
    }
  }

  // Still active state cannot confirm
  const stillSpeakingAfter = {
    active: { speaking: true, sending: false, replyDisplayActive: false },
    settingsCommandResult: { command: "stopTts", operationId: "voice-op-1", ok: true, status: "completed" }
  };
  assert.equal(
    isObservedActionConfirmation("voice.stop", idleBefore, stillSpeakingAfter, { operationId: "voice-op-1" }),
    false
  );

  // Missing active state fields cannot confirm
  const missingStateAfter = {
    active: {},
    settingsCommandResult: { command: "stopTts", operationId: "voice-op-1", ok: true, status: "completed" }
  };
  assert.equal(
    isObservedActionConfirmation("voice.stop", idleBefore, missingStateAfter, { operationId: "voice-op-1" }),
    false
  );

  // Failed receipt terminates wait with failed outcome
  const failedAfter = {
    active: { speaking: false },
    settingsCommandResult: { command: "stopTts", operationId: "voice-op-1", ok: false, status: "failed", reason: "device_busy" }
  };
  assert.equal(
    isObservedActionConfirmation("voice.stop", idleBefore, failedAfter, { operationId: "voice-op-1" }),
    true
  );
  assert.deepEqual(
    observedActionOutcome("voice.stop", { operationId: "voice-op-1" }, failedAfter),
    { ok: false, status: "failed", reason: "device_busy" }
  );

  // Legacy call without operationId still requires wasActive
  assert.equal(
    isObservedActionConfirmation("voice.stop", idleBefore, stoppedAfter, {}),
    false
  );
  const activeBefore = { active: { speaking: true, sending: true, replyDisplayActive: true } };
  assert.equal(
    isObservedActionConfirmation("voice.stop", activeBefore, stoppedAfter, {}),
    true
  );
  assert.equal(
    isObservedActionConfirmation("chat.stop", activeBefore, chatStoppedAfter, {}),
    true
  );
});

test("settings receipt uses the requested command when real media handlers return their domain command", async () => {
  const runtime = await loadPetRuntime();
  const env = createEnvironment({ tauri: true });
  const app = runtime.createPetApplication({ ...env, bundledCharacterAssets: {} });
  try {
    for (const [command, action] of [["previousMusic", "previous"], ["nextMusic", "next"], ["stopMusic", "stop"]]) {
      const operationId = `receipt-${command}`;
      const result = await app.controllers.settings.handleSettingsCommand({ command, operationId });
      assert.equal(result.command, command);
      assert.equal(result.operationId, operationId);
      assert.equal(result.ok, false);
      assert.equal(result.status, "unavailable");
      assert.equal(result.reason, "no_active_media");
      assert.equal(result.action, action);
      assert.equal(result.target, "none");
      assert.equal(app.controllers.settings.lastSettingsCommandResult, result);
    }
  } finally {
    app.dispose();
    await runtime.close();
  }
});

test("settings command receipt preserves business failure and non-terminal status when operationId is missing", async () => {
  const runtime = await loadPetRuntime();
  const apps = [];
  try {
    const env = createEnvironment({ tauri: true });
    const app = runtime.createPetApplication({ ...env, bundledCharacterAssets: {} });
    apps.push(app);

    // Business returns failure without operationId
    Object.defineProperty(app.controllers.systemMedia, "controlActiveMusic", {
      value: async () => ({
        ok: false,
        status: "failed",
        reason: "local_fixture_failure"
      }),
      configurable: true
    });

    const failureResult = await app.controllers.settings.handleSettingsCommand({
      command: "controlActiveMusic",
      action: "toggle",
      operationId: "media-op-1"
    });

    assert.equal(failureResult.ok, false);
    assert.equal(failureResult.status, "failed");
    assert.equal(failureResult.reason, "local_fixture_failure");
    assert.equal(failureResult.operationId, "media-op-1");
    assert.equal(failureResult.command, "controlActiveMusic");

    // Business returns non-terminal status (e.g. queued / pending) without operationId
    Object.defineProperty(app.controllers.systemMedia, "controlActiveMusic", {
      value: async () => ({
        ok: true,
        status: "queued",
        reason: "queued_for_track"
      }),
      configurable: true
    });

    const queuedResult = await app.controllers.settings.handleSettingsCommand({
      command: "controlActiveMusic",
      action: "next",
      operationId: "media-op-2"
    });

    assert.equal(queuedResult.ok, true);
    assert.equal(queuedResult.status, "queued");
    assert.equal(queuedResult.reason, "queued_for_track");
    assert.equal(queuedResult.operationId, "media-op-2");

    // Unknown command preserves not-supported and associates operationId
    const unknownResult = await app.controllers.settings.handleSettingsCommand({
      command: "nonExistentCommand",
      operationId: "unknown-op"
    });
    assert.equal(unknownResult.ok, false);
    assert.equal(unknownResult.status, "not-supported");
    assert.equal(unknownResult.operationId, "unknown-op");

    // Old windows cannot report successful changes to settings with no implementation.
    Object.assign(app.state, { voiceSpeed: "1.30x", wakeWord: "old", wakeSensitivity: "high" });
    const restored = app.controllers.persistence.normalizeState(app.state);
    for (const field of ["voiceSpeed", "wakeWord", "wakeSensitivity"]) {
      assert.equal(Object.hasOwn(restored, field), false);
    }
    for (const [command, actionId] of [
      ["setVoiceSpeed", "voice.setSpeed"], ["setWakeWord", "voice.setWakeWord"], ["setWakeSensitivity", "voice.setWakeSensitivity"]
    ]) {
      const result = await app.controllers.settings.handleSettingsCommand({ command, value: "new", operationId: command });
      assert.equal(result.ok, false);
      assert.equal(result.status, "not-supported");
      assert.equal(result.operationId, command);
      assert.equal(CONTROL_CENTER_BRIDGED_ACTION_IDS.includes(actionId), false);
    }

    // A stale window cannot silently fork the continuous desktop conversation.
    const sessionId = app.state.sessionId;
    const retiredResult = await app.controllers.settings.handleSettingsCommand({ command: "newSession", operationId: "retired-op" });
    assert.equal(retiredResult.status, "not-supported");
    assert.equal(retiredResult.ok, false);
    assert.equal(app.state.sessionId, sessionId);
    assert.ok(!CONTROL_CENTER_BRIDGED_ACTION_IDS.includes("chat.new"));
    await app.controllers.panel.registerPanelBridge();
    env.nativeHandlers.get("panel:action")({ payload: { action: "new-session" } });
    assert.equal(app.state.sessionId, sessionId);

    // Synchronous void command completes cleanly with operationId
    const syncResult = await app.controllers.settings.handleSettingsCommand({
      command: "stopTts",
      operationId: "stop-op"
    });
    assert.equal(syncResult.ok, true);
    assert.equal(syncResult.status, "completed");
    assert.equal(syncResult.operationId, "stop-op");
  } finally {
    for (const app of apps) {
      app.dispose();
    }
    await runtime.close();
  }
});
