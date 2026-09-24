export const SETTINGS_COMMAND_EVENT = "akane-next-settings-command";
export const SETTINGS_SNAPSHOT_EVENT = "akane-next-settings-snapshot";

export function createTargetedEventEmitter({ targetLabel, eventName, emitTo, emit }) {
  const target = String(targetLabel || "").trim();
  const expectedEvent = String(eventName || "").trim();
  if (!target || !expectedEvent || typeof emitTo !== "function" || typeof emit !== "function") {
    throw new Error("invalid_targeted_event_bridge");
  }

  return async function emitTargetedEvent(requestedEvent, payload) {
    const actualEvent = String(requestedEvent || "").trim();
    if (actualEvent !== expectedEvent) {
      throw new Error(`unexpected_targeted_event:${actualEvent || "missing"}`);
    }
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new Error("invalid_targeted_event_payload");
    }
    try {
      await emitTo(target, expectedEvent, payload);
    } catch {
      await emit(expectedEvent, payload);
    }
  };
}
