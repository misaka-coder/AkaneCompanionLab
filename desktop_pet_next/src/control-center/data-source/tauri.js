import { CONTROL_CENTER_ACTIONS, createNotImplementedActionResult } from "../action-router.js";
import { SETTINGS_COMMAND_BY_ACTION_ID as settingsCommandByActionId } from "../settings-command-contract.js";
import { DEFAULT_BACKEND_URL, normalizeBackendBaseUrl } from "./http.js";
import { SETTINGS_COMMAND_EVENT } from "../event-bridge.js";
import { formatDataSourceError } from "./shared.js";

const activeMediaActionByActionId = Object.freeze({
  [CONTROL_CENTER_ACTIONS.musicPrevious]: "previous",
  [CONTROL_CENTER_ACTIONS.musicNext]: "next",
  [CONTROL_CENTER_ACTIONS.musicTogglePlayback]: "toggle",
  [CONTROL_CENTER_ACTIONS.musicStop]: "stop"
});

const tauriInvokeByActionId = Object.freeze({
  [CONTROL_CENTER_ACTIONS.workspaceOpen]: "open_workspace_window",
  [CONTROL_CENTER_ACTIONS.characterOpenWorkshop]: "open_workshop_window",
  [CONTROL_CENTER_ACTIONS.characterOpenPackFolder]: "open_character_packs_folder",
  [CONTROL_CENTER_ACTIONS.abilitiesSkillsOpenFolder]: "open_managed_skills_folder",
  [CONTROL_CENTER_ACTIONS.windowClose]: "close_window"
});

const tauriWindowActionByActionId = Object.freeze({
  [CONTROL_CENTER_ACTIONS.windowMinimize]: "minimize",
  [CONTROL_CENTER_ACTIONS.windowMaximize]: "toggleMaximize"
});

function buildWorkspaceRecommendationValue(payload = {}) {
  const itemType = payload.itemType === "generated" ? "generated" : "attachment";
  const handle = String(payload.handle || "").trim();
  const title = String(payload.title || "").trim();
  if (!handle) return null;
  return { itemType, handle, title };
}

export async function runTauriControlCenterAction(actionId, payload, context, options) {
  const command = tauriInvokeByActionId[actionId];
  const settingsCommand = settingsCommandByActionId[actionId];
  const windowAction = tauriWindowActionByActionId[actionId];
  const bridge = await resolveTauriBridge(options);
  if (!bridge) {
    return { ok: false, status: "not-available", actionId };
  }

  try {
    if (actionId.startsWith("qq.setup.")) {
      if (typeof bridge.invoke !== "function") return { ok: false, status: "not-available", reason: "desktop_required", refresh: false };
      const result = await bridge.invoke("local_qq_setup", { request: {
        action: actionId.slice("qq.setup.".length),
        backendUrl: normalizeBackendBaseUrl(options.baseUrl || DEFAULT_BACKEND_URL),
        botId: String(options.botId || options.petState?.boundBotId || options.petState?.instanceId || "")
      } });
      return { ...result, ok: result?.ok === true, actionId, refresh: false };
    }
    if (actionId === CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileAssignToCurrentCharacter) {
      return await runTauriCharacterVoiceProfileAssignment(payload, context, options, bridge);
    }
    if (actionId === CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileClearCurrentCharacter) {
      return await runTauriCharacterVoiceProfileClear(payload, context, options, bridge);
    }
    if ([CONTROL_CENTER_ACTIONS.abilitiesPluginPickSource, CONTROL_CENTER_ACTIONS.abilitiesPluginPickWheel].includes(actionId)) {
      if (typeof bridge.invoke !== "function") return createNotImplementedActionResult(actionId);
      const kind = actionId === CONTROL_CENTER_ACTIONS.abilitiesPluginPickSource ? "source" : "wheel";
      const result = await bridge.invoke("pick_local_plugin_path", { kind });
      return {
        ...(result && typeof result === "object" ? result : {}),
        ok: Boolean(result?.ok),
        status: String(result?.status || (result?.ok ? "selected" : "failed")),
        actionId,
        refresh: false
      };
    }

    if (command && typeof bridge.invoke === "function") {
      await bridge.invoke(command, {});
      return { ok: true, status: "executed", actionId, payload, refresh: true };
    }

    if (settingsCommand && typeof bridge.emit === "function") {
      const activeMediaAction = activeMediaActionByActionId[actionId];
      if (activeMediaAction) {
        const value = { action: activeMediaAction };
        await bridge.emit(SETTINGS_COMMAND_EVENT, {
          ...payload,
          command: settingsCommand,
          action: activeMediaAction,
          value,
          source: context?.source || payload?.source || "control-center"
        });
        return { ok: true, status: "executed", actionId, payload: { ...payload, action: activeMediaAction }, refresh: true };
      }
      if (actionId === CONTROL_CENTER_ACTIONS.musicPlayWorkspaceRecommendation) {
        const value = buildWorkspaceRecommendationValue(payload);
        if (!value) {
          return { ok: false, status: "invalid-payload", actionId, error: "missing handle" };
        }
        await bridge.emit(SETTINGS_COMMAND_EVENT, { command: settingsCommand, value, source: context?.source || "control-center" });
        return { ok: true, status: "executed", actionId, payload: value, refresh: true };
      }
      await bridge.emit(SETTINGS_COMMAND_EVENT, {
        ...payload,
        command: settingsCommand,
        value: payload?.value ?? payload?.text ?? null,
        source: context?.source || payload?.source || "control-center"
      });
      return { ok: true, status: "executed", actionId, payload, refresh: true };
    }

    if (windowAction) {
      return runTauriWindowAction(actionId, windowAction, bridge);
    }

    return createNotImplementedActionResult(actionId);
  } catch (error) {
    return { ok: false, status: "failed", actionId, payload, error: formatDataSourceError(error), refresh: true };
  }
}

async function runTauriCharacterVoiceProfileAssignment(payload = {}, context = {}, options = {}, bridge) {
  if (typeof bridge.invoke !== "function") {
    return createNotImplementedActionResult(CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileAssignToCurrentCharacter);
  }
  const voiceProfileId = String(payload.voiceProfileId || payload.voice_profile_id || payload.profileId || payload.value || "").trim();
  const packId = resolveCharacterVoiceActionPackId(payload, options);
  if (!voiceProfileId || !packId) {
    return {
      ok: false,
      status: "invalid-payload",
      actionId: CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileAssignToCurrentCharacter,
      refresh: false,
      error: !voiceProfileId ? "voiceProfileId is required" : "characterPackId is required"
    };
  }
  const provider = String(payload.provider || payload.voiceProvider || payload.providerId || "gpt_sovits").trim();
  const notes = String(payload.notes || `控制中心声线 ${voiceProfileId}`).trim();
  const request = {
    packId,
    provider,
    profileId: voiceProfileId,
    ...(notes ? { notes } : {})
  };
  const result = await bridge.invoke("set_character_voice_profile", { request });
  if (typeof bridge.emit === "function") {
    await bridge.emit(SETTINGS_COMMAND_EVENT, {
      command: "refreshCharacterPacks",
      value: { selectPackId: packId, apply: true },
      source: context?.source || payload?.source || "control-center"
    });
  }
  return {
    ok: true,
    status: "assigned",
    actionId: CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileAssignToCurrentCharacter,
    provider,
    voiceProfileId,
    characterPackId: packId,
    characterName: String(result?.profile?.identity?.name || ""),
    refresh: true
  };
}

async function runTauriCharacterVoiceProfileClear(payload = {}, context = {}, options = {}, bridge) {
  if (typeof bridge.invoke !== "function") {
    return createNotImplementedActionResult(CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileClearCurrentCharacter);
  }
  const packId = resolveCharacterVoiceActionPackId(payload, options);
  if (!packId) {
    return {
      ok: false,
      status: "invalid-payload",
      actionId: CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileClearCurrentCharacter,
      refresh: false,
      error: "characterPackId is required"
    };
  }
  const result = await bridge.invoke("clear_character_voice_profile", { packId });
  if (typeof bridge.emit === "function") {
    await bridge.emit(SETTINGS_COMMAND_EVENT, {
      command: "refreshCharacterPacks",
      value: { selectPackId: packId, apply: true },
      source: context?.source || payload?.source || "control-center"
    });
  }
  return {
    ok: true,
    status: "cleared",
    actionId: CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileClearCurrentCharacter,
    characterPackId: packId,
    characterName: String(result?.profile?.identity?.name || ""),
    refresh: true
  };
}

function resolveCharacterVoiceActionPackId(payload = {}, options = {}) {
  return String(
    payload.characterPackId ||
      payload.character_pack_id ||
      payload.packId ||
      payload.pack_id ||
      options.characterPackId ||
      ""
  ).trim();
}

async function runTauriWindowAction(actionId, windowAction, bridge) {
  const winApi = bridge.window;
  if (winApi && typeof winApi[windowAction] === "function") {
    await winApi[windowAction]();
    return { ok: true, status: "executed", actionId, refresh: true };
  }

  try {
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    const win = getCurrentWindow();
    if (windowAction === "minimize") {
      await win.minimize();
    } else if (windowAction === "toggleMaximize") {
      await win.toggleMaximize();
    }
    return { ok: true, status: "executed", actionId, refresh: true };
  } catch {
    return createNotImplementedActionResult(actionId);
  }
}

async function resolveTauriBridge(options = {}) {
  const injectedBridge = options.tauriBridge || {};
  if (typeof injectedBridge.invoke === "function" || typeof injectedBridge.emit === "function" || typeof injectedBridge.window === "object") {
    return injectedBridge;
  }

  const windowBridge = globalThis.window?.__TAURI__ || globalThis.__TAURI__;
  const windowInvoke = windowBridge?.core?.invoke || windowBridge?.invoke;
  const windowEmit = windowBridge?.event?.emit || windowBridge?.emit;
  if (typeof windowInvoke === "function" || typeof windowEmit === "function") {
    return { invoke: windowInvoke, emit: windowEmit };
  }

  if (!isTauriRuntime()) {
    return null;
  }

  try {
    const [coreApi, eventApi] = await Promise.all([
      import("@tauri-apps/api/core"),
      import("@tauri-apps/api/event")
    ]);
    return {
      invoke: coreApi.invoke,
      emit: eventApi.emit
    };
  } catch {
    return null;
  }
}

function isTauriRuntime() {
  return Boolean(globalThis.window?.__TAURI_INTERNALS__ || globalThis.__TAURI_INTERNALS__);
}
