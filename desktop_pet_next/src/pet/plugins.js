import { PLUGIN_AGENT_EVENT_FRAME_EVENT } from './constants.js';

/** Owns plugins behavior and its temporary runtime state. */
export function createPluginsController({
  platform: platformPort,
  lifecycle,
  chat: chatPort,
  tts: ttsPort,
  bubble: bubblePort,
  settings: settingsPort,
  activity: activityPort
}) {
  let pluginAgentEventBridgeRegistered = false;

  const pluginAgentEventFrames = [];

  const queuedPluginAgentEventIds = new Set();

  let pluginAgentEventDrainTimer = 0;

  async function registerPluginAgentEventBridge() {
    if (!platformPort.isTauriRuntime || pluginAgentEventBridgeRegistered) return;
    const unlisten = await platformPort.listen(PLUGIN_AGENT_EVENT_FRAME_EVENT, (event) => {
      enqueuePluginAgentEventFrame(event?.payload);
    });
    lifecycle.track(unlisten);
    pluginAgentEventBridgeRegistered = true;
    const pending = await platformPort.invoke("list_pending_plugin_agent_event_frames");
    for (const item of Array.isArray(pending) ? pending : []) {
      enqueuePluginAgentEventFrame(item);
    }
  }

  function enqueuePluginAgentEventFrame(item) {
    const deliveryId = String(item?.deliveryId || "").trim();
    const payload = item?.payload;
    if (!deliveryId || !payload || typeof payload !== "object" || queuedPluginAgentEventIds.has(deliveryId)) return;
    queuedPluginAgentEventIds.add(deliveryId);
    if (payload._desktop_turn_event) {
      // Waiting for chat or this stream's own TTS to become idle would
      // deadlock the final event behind its intermediate presentation.
      chatPort.receiveBackgroundTurnEvents(payload._desktop_turn_event);
      void platformPort.invoke("acknowledge_plugin_agent_event_frame", { deliveryId })
        .catch(() => {})
        .finally(() => queuedPluginAgentEventIds.delete(deliveryId));
      return;
    }
    if (payload._desktop_music_control) {
      // A tool is awaiting this receipt while chat is still generating.
      void activityPort.executeLocalMusicControl(payload._desktop_music_control)
        .finally(() => platformPort.invoke("acknowledge_plugin_agent_event_frame", { deliveryId })
          .finally(() => queuedPluginAgentEventIds.delete(deliveryId)));
      return;
    }
    pluginAgentEventFrames.push({ deliveryId, payload });
    drainPluginAgentEventFrames();
  }

  function drainPluginAgentEventFrames() {
    lifecycle.clearTimeout(pluginAgentEventDrainTimer);
    if (!pluginAgentEventFrames.length) return;
    if (chatPort.sending || ttsPort.ttsActive || ttsPort.ttsQueue.length > 0) {
      pluginAgentEventDrainTimer = lifecycle.setTimeout(drainPluginAgentEventFrames, 200);
      return;
    }
    const item = pluginAgentEventFrames.shift();
    bubblePort.renderPayload(item.payload, { source: "live", force: true });
    settingsPort.scheduleSettingsSnapshot();
    void platformPort.invoke("acknowledge_plugin_agent_event_frame", { deliveryId: item.deliveryId })
      .finally(() => queuedPluginAgentEventIds.delete(item.deliveryId));
    if (pluginAgentEventFrames.length) {
      pluginAgentEventDrainTimer = lifecycle.setTimeout(drainPluginAgentEventFrames, 250);
    }
  }

  return {
    get pluginAgentEventDrainTimer() { return pluginAgentEventDrainTimer; },
    registerPluginAgentEventBridge
  };
}
