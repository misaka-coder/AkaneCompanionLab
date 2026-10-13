import { createControlCenterActionRouter } from "../../control-center/action-router.js";
import { createControlCenterDataSource } from "../../control-center/data-sources.js";
import { createControlCenterViewModel } from "../view-model.js";
import { createQqSetupController, QQ_SETUP_ACTIONS } from "../qq-setup.js";
import { createToolExposureController } from "../tool-exposure.js";
import { createComputerUseController } from "../computer-use.js";
import { createMemorySettingsController } from "../memory-settings.js";
import { createBridgeImagePreviews } from "./previews.js";
import { createRuntimeConfirmation } from "./confirmation.js";
import { createBridgeChat } from "./chat.js";
import { createBridgeSnapshots } from "./snapshots.js";
import { createBridgeSubscriptions } from "./subscriptions.js";
import { createBridgeActions } from "./actions.js";
import { matchesLiveSource } from "./scope.js";
import { withLiveRuntime, withBridgeStatus } from "./projection.js";

export function createControlCenterRuntime({ host, createDataSource = createControlCenterDataSource,
  createRouter = createControlCenterActionRouter, serializeAttachments }) {
  const listeners = new Set();
  let source = null, actionRouter = null, rawSnapshot = null, runtimeSnapshot = null, publishedViewModel = null;
  let stopped = true, bindingEpoch = 0, publishing = false, fileDropHandler = null;
  let liveSnapshotStatus = host.isTauri ? "connecting" : "not-applicable", liveSnapshotError = "", qqSetupRuntime = {};
  const isActive = () => !stopped;
  const toolExposure = createToolExposureController({ onChange: publish });
  const memorySettings = createMemorySettingsController({ onChange: publish, invoke: host.invoke, desktop: host.isTauri });
  const newComputerUse = () => createComputerUseController({ invoke: host.invoke, available: host.isTauri, onChange: publish });
  let computerUse = newComputerUse();
  const confirmations = createRuntimeConfirmation({ readSnapshot: () => runtimeSnapshot });
  const qqSetup = createQqSetupController({ onChange(value) { qqSetupRuntime = value; publish(); } });
  const chat = createBridgeChat({ readSource: () => source, readRuntime: () => runtimeSnapshot, readSnapshot: () => rawSnapshot,
    updateSnapshot: update => { if (rawSnapshot) rawSnapshot = update(rawSnapshot); }, isActive, publish });
  const imagePreviews = createBridgeImagePreviews({ readSource: () => source, readRuntime: () => runtimeSnapshot,
    readViewModel: () => publishedViewModel, readScope: () => String(chat.captureScope().version), isActive, publish });
  const subscriptions = createBridgeSubscriptions({ host,
    onRuntime(payload) {
      runtimeSnapshot = payload && typeof payload === "object" ? payload : null;
      liveSnapshotStatus = runtimeSnapshot ? "connected" : "connecting";
      liveSnapshotError = "";
      publish();
      chat.schedule();
    },
    onFileDrop: paths => fileDropHandler?.(paths),
    onUnavailable(error) {
      liveSnapshotStatus = "unavailable";
      liveSnapshotError = error;
      publish();
    }
  });
  const captureBinding = () => ({ source, epoch: bindingEpoch, lifetime: subscriptions.capture() });
  const isCurrentBinding = ticket => isActive() && source === ticket.source && bindingEpoch === ticket.epoch && subscriptions.isCurrent(ticket.lifetime);
  const snapshots = createBridgeSnapshots({ captureBinding, isCurrentBinding, readSnapshot: () => rawSnapshot,
    writeSnapshot: value => { rawSnapshot = value; }, publish, readViewModel: () => publishedViewModel, refreshChatSession: chat.refresh });
  const actions = createBridgeActions({ host, readRouter: () => actionRouter, readRuntime: () => runtimeSnapshot,
    captureBinding, isCurrentBinding, chat, confirmations, readComputerUse: () => computerUse,
    toolExposure, memorySettings, qqSetup, rebindSource, refresh, serializeAttachments });

  function synchronizeToolExposureScope() {
    const live = runtimeSnapshot?.state || {};
    return toolExposure.bind(matchesLiveSource(source, live) ? source : null,
      { sessionId: live.sessionId || source?.sessionId, characterPackId: live.characterPackId ?? source?.characterPackId });
  }
  function publish() {
    if (stopped || !rawSnapshot || publishing) return;
    publishing = true;
    try {
      chat.synchronize();
      synchronizeToolExposureScope();
      const live = runtimeSnapshot?.state || {};
      memorySettings.bind(matchesLiveSource(source, live) ? source : null,
        { characterPackId: live.characterPackId ?? source?.characterPackId });
      const viewModel = createControlCenterViewModel(withBridgeStatus(withLiveRuntime({ ...rawSnapshot, qqSetupRuntime }, runtimeSnapshot),
        liveSnapshotStatus, liveSnapshotError), runtimeSnapshot);
      viewModel.chat.previews = imagePreviews.snapshot();
      viewModel.abilities.toolExposure = toolExposure.snapshot();
      viewModel.computerUse = computerUse.snapshot();
      viewModel.system.memorySettings = memorySettings.snapshot();
      publishedViewModel = viewModel;
      for (const listener of listeners) listener(viewModel);
    } finally { publishing = false; }
  }
  function disposeControllers() {
    confirmations.stop();
    chat.stop();
    toolExposure.stop();
    memorySettings.stop();
    imagePreviews.dispose();
    qqSetup.stop();
    computerUse.stop();
  }
  function bindSource(sourceOptions) {
    snapshots.reset();
    bindingEpoch += 1;
    source = createDataSource(sourceOptions);
    actionRouter = createRouter({ dataSource: source });
    rawSnapshot = source.readInitialState();
    qqSetup.bind(source);
    publish();
    if (host.isTauri) void qqSetup.run(QQ_SETUP_ACTIONS.detect);
  }
  async function start() {
    const lifetime = subscriptions.begin();
    stopped = true;
    disposeControllers();
    bindingEpoch += 1;
    source = null;
    actionRouter = null;
    rawSnapshot = null;
    runtimeSnapshot = null;
    publishedViewModel = null;
    qqSetupRuntime = {};
    snapshots.reset();
    computerUse = newComputerUse();
    stopped = false;
    liveSnapshotStatus = host.isTauri ? "connecting" : "not-applicable";
    liveSnapshotError = "";
    let sourceOptions;
    try { sourceOptions = await host.createSourceOptions(); } catch (error) {
      if (!subscriptions.isCurrent(lifetime)) return null;
      throw error;
    }
    if (!subscriptions.isCurrent(lifetime)) return null;
    bindSource(sourceOptions);
    computerUse.start();
    await subscriptions.connect(lifetime);
    if (!subscriptions.isCurrent(lifetime)) return null;
    return refresh();
  }
  async function rebindSource(isCurrent, botId) {
    const sourceOptions = await host.createSourceOptions();
    if (!isCurrent() || String(runtimeSnapshot?.state?.boundBotId || "") !== botId
      || String(sourceOptions.botId || "") !== botId) return false;
    bindSource(sourceOptions);
    return true;
  }
  function refresh() {
    if (isActive() && !synchronizeToolExposureScope()) void toolExposure.refresh();
    return snapshots.refresh();
  }
  function subscribe(listener) {
    listeners.add(listener);
    if (publishedViewModel && isActive()) listener(publishedViewModel);
    return () => listeners.delete(listener);
  }
  function stop() {
    stopped = true;
    bindingEpoch += 1;
    subscriptions.stop();
    snapshots.reset();
    disposeControllers();
    source = null;
    actionRouter = null;
    rawSnapshot = null;
    runtimeSnapshot = null;
    publishedViewModel = null;
    fileDropHandler = null;
    listeners.clear();
  }
  return { start, refresh, runAction: actions.runAction, subscribe, stop,
    loadOlderChatMessages: chat.loadOlderChatMessages, previewChatImage: imagePreviews.previewChatImage,
    onFileDrop(handler) { fileDropHandler = handler; } };
}
