import { SCREEN_OBSERVATION_COMMANDS } from "../../screen-observation.js";
import { SETTINGS_COMMAND_BY_ACTION_ID } from "../../control-center/settings-command-contract.js";
import { asObject, optionalFiniteNumber, text } from "./shared.js";

const SUCCESS_STATUSES = new Set(["executed", "completed", "available", "connected", "configured", "already-playing", "already-paused", "already-stopped"]);

export function normalizeActionPresentation(result) {
  const value = asObject(result);
  const status = text(value.status) || (value.ok ? "executed" : "failed");
  if (status === "cancelled") {
    return { phase: "confirmed", label: "已取消", detail: "" };
  }
  if (value.ok && status === "executed" && text(value.actionId) === "advanced.resetWindow") {
    return { phase: "unknown", label: "重置请求已发送", detail: "请观察桌宠窗口是否已经恢复" };
  }
  const submittedLabel = { accepted: "已接收", queued: "已排队", pending: "等待处理", running: "执行中" }[status];
  if (value.ok && typeof submittedLabel === "string") {
    return { phase: "confirmed", label: submittedLabel, detail: friendlyActionDetail(text(value.reason)) };
  }
  if (["failed", "error", "busy", "blocked", "rejected", "timed_out", "not-supported", "unavailable"].includes(status)) {
    return { phase: "failed", label: "操作失败", detail: friendlyActionDetail(text(value.reason) || text(value.error) || status) };
  }
  if (value.ok && (SUCCESS_STATUSES.has(status) || status === "handled")) {
    return { phase: "confirmed", label: "已完成", detail: "" };
  }
  if (status === "execution_unknown" || status === "unknown") {
    const reason = friendlyActionDetail(text(value.reason) || text(value.error));
    return {
      phase: "unknown",
      label: "已发送，未确认",
      detail: reason === "runtime_state_not_confirmed" ? "指令已发出，但没有在时限内观察到状态变化" : reason
    };
  }
  if (status === "not-implemented" || status === "not-available") {
    return { phase: "failed", label: "当前不可用", detail: friendlyActionDetail(text(value.reason)) || "该入口尚未接通" };
  }
  if (value.ok) {
    return { phase: "confirmed", label: status === "mocked" ? "仅模拟" : "已完成", detail: "" };
  }
  return { phase: "failed", label: "操作失败", detail: friendlyActionDetail(text(value.reason) || text(value.error) || status) };
}

function friendlyActionDetail(value) {
  return {
    admin_auth_required: "此操作只能从桌面控制中心执行",
    request_failed: "请求没有完成，请检查服务连接后重试",
    "request-failed": "请求没有完成，请检查服务连接后重试"
  }[value] || value;
}

export function isObservedActionConfirmation(actionId, beforeSnapshot, afterSnapshot, payload = {}) {
  const before = asObject(beforeSnapshot);
  const after = asObject(afterSnapshot);
  const beforeState = asObject(before.state);
  const afterState = asObject(after.state);
  const beforeActive = asObject(before.active);
  const afterActive = asObject(after.active);
  const commandOutcome = observedActionOutcome(actionId, payload, after);
  if (text(payload.operationId) && !commandOutcome) return false;
  if (commandOutcome && commandOutcome.ok === false) return true;
  if (["chat.attach", "chat.removeAttachment", "chat.playAttachment", "chat.fileAction"].includes(actionId)) return Boolean(commandOutcome?.ok);
  const observationCommand = SCREEN_OBSERVATION_COMMANDS[actionId];
  if (observationCommand) {
    if (!commandOutcome?.ok) return false;
    if (!observationCommand.field) return Number(afterActive.screenVisionBufferRevision) > Number(beforeActive.screenVisionBufferRevision || 0);
    const actual = afterState[observationCommand.field];
    return typeof actual === "number" ? numbersClose(actual, payload.value) : actual === payload.value;
  }

  if (actionId === "chat.send") {
    const commandResult = asObject(after.settingsCommandResult);
    return (
      text(commandResult.command) === "sendChatMessage" &&
      (!text(payload.operationId) || text(commandResult.operationId) === text(payload.operationId)) &&
      text(commandResult.status) === "accepted" && Boolean(commandResult.ok)
    );
  }
  if (actionId === "chat.stop") {
    if (text(payload.operationId)) {
      return Boolean(
        commandOutcome?.ok &&
        typeof afterActive.sending === "boolean" &&
        typeof afterActive.replyDisplayActive === "boolean" &&
        !afterActive.sending &&
        !afterActive.replyDisplayActive
      );
    }
    const wasActive = Boolean(beforeActive.sending || beforeActive.replyDisplayActive);
    return wasActive && !afterActive.sending && !afterActive.replyDisplayActive;
  }
  if (["music.previous", "music.next", "music.togglePlayback", "music.stop"].includes(actionId)) {
    if (text(payload.operationId)) {
      return Boolean(observedActionOutcome(actionId, payload, after));
    }
    return musicPlaybackSignature(before) !== musicPlaybackSignature(after);
  }
  if (actionId === "voice.test" || actionId === "voice.previewPlay") {
    return Boolean(commandOutcome?.ok) && Boolean(afterActive.speaking);
  }
  if (actionId === "voice.stop") {
    if (text(payload.operationId)) {
      return Boolean(commandOutcome?.ok && typeof afterActive.speaking === "boolean" && !afterActive.speaking);
    }
    return Boolean(beforeActive.speaking) && !Boolean(afterActive.speaking);
  }
  if (actionId === "voice.setTtsEnabled") {
    return typeof afterState.voiceEnabled === "boolean" && afterState.voiceEnabled === Boolean(payload.value);
  }
  if (actionId === "voice.setAsrEnabled") {
    return typeof afterState.voiceInputEnabled === "boolean" && afterState.voiceInputEnabled === Boolean(payload.value);
  }
  if (actionId === "voice.setVolume") {
    return numbersClose(afterState.voiceVolume, payload.value);
  }
  if (actionId === "advanced.setHitTestEnabled") {
    return typeof afterState.hitTestEnabled === "boolean" && afterState.hitTestEnabled === Boolean(payload.value);
  }
  if (actionId === "advanced.setHitboxOverlay") {
    return typeof afterState.hitboxOverlay === "boolean" && afterState.hitboxOverlay === Boolean(payload.value);
  }
  if (actionId === "settings.selectBot") {
    return Boolean(text(afterState.boundBotId) && text(afterState.boundBotId) === text(payload.value));
  }
  const expected = text(payload.value);
  if (actionId === "character.selectPack") {
    const beforePack = text(beforeState.characterPackId) || text(asObject(before.character).packId);
    const afterPack = text(afterState.characterPackId) || text(asObject(after.character).packId);
    return Boolean(afterPack && (!expected || afterPack === expected) && (afterPack !== beforePack || commandOutcome?.ok));
  }
  if (actionId === "character.setOutfit") {
    const beforeOutfit = text(beforeState.outfit);
    const afterOutfit = text(afterState.outfit);
    return Boolean(afterOutfit && (!expected || afterOutfit === expected) && (afterOutfit !== beforeOutfit || commandOutcome?.ok));
  }
  if (actionId === "character.previewEmotion") {
    const beforeExpression = text(asObject(before.currentExpression).id) || text(asObject(before.currentExpression).name) || text(beforeState.currentEmotion);
    const afterExpression = text(asObject(after.currentExpression).id) || text(asObject(after.currentExpression).name) || text(afterState.currentEmotion);
    return Boolean(
      commandOutcome?.ok &&
      afterExpression &&
      (!expected || afterExpression === expected) &&
      (afterExpression !== beforeExpression || text(asObject(after.settingsCommandResult).status) === "completed")
    );
  }
  if (actionId === "character.refresh" || actionId === "perception.runDiagnostics") return Boolean(commandOutcome?.ok);
  return false;
}

export function observedActionOutcome(actionId, payload = {}, snapshot = {}) {
  const commandResult = asObject(asObject(snapshot).settingsCommandResult);
  const expectedOperationId = text(payload.operationId);
  const expectedCommand = SETTINGS_COMMAND_BY_ACTION_ID[actionId];
  if (!expectedCommand || text(commandResult.command) !== expectedCommand) return null;
  if (expectedOperationId && text(commandResult.operationId) !== expectedOperationId) return null;
  const status = text(commandResult.status);
  if (!status || typeof commandResult.ok !== "boolean") return null;
  if (["pending", "queued", "running", "loading", "starting"].includes(status)) return null;
  if (status === "accepted" && actionId !== "chat.send") return null;
  const outcome = {
    ok: Boolean(commandResult.ok),
    status: text(commandResult.status),
    reason: text(commandResult.reason)
  };
  if (["music.previous", "music.next", "music.togglePlayback", "music.stop"].includes(actionId)) {
    return {
      ...outcome,
      target: text(commandResult.target),
      targetId: text(commandResult.targetId),
      mediaAction: text(commandResult.action)
    };
  }
  return outcome;
}

function musicPlaybackSignature(snapshot) {
  const value = asObject(snapshot);
  const active = asObject(value.active);
  const music = asObject(value.music);
  const localTrack = asObject(music.track);
  const localStatePresent = Object.prototype.hasOwnProperty.call(active, "musicPlaying")
    || Object.prototype.hasOwnProperty.call(active, "musicPaused");
  const localAvailable = Boolean(text(localTrack.displayName) || text(music.displayName) || Number(music.queueCount));
  const systemMedia = asObject(music.systemMedia);
  const systemAvailable = Boolean(text(systemMedia.playbackStatus) || text(systemMedia.title));
  const controlTarget = text(asObject(music.control).target);
  if (controlTarget === "local" || (controlTarget !== "system" && (localAvailable || (localStatePresent && !systemAvailable)))) {
    return JSON.stringify([
      "local", text(localTrack.sourceId) || text(localTrack.workspaceHandle) || text(localTrack.displayName) || text(music.displayName),
      typeof active.musicPlaying === "boolean" ? active.musicPlaying : Boolean(music.playing),
      typeof active.musicPaused === "boolean" ? active.musicPaused : Boolean(music.paused)
    ]);
  }
  return JSON.stringify(["system", text(systemMedia.trackKey) || text(systemMedia.title), text(systemMedia.artist),
    text(systemMedia.playbackStatus), Boolean(systemMedia.isPlaying)]);
}

function numbersClose(left, right) {
  const a = optionalFiniteNumber(left);
  const b = optionalFiniteNumber(right);
  return Number.isFinite(a) && Number.isFinite(b) && Math.abs(a - b) < 0.001;
}
