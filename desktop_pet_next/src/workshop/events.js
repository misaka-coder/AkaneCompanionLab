import { CHARACTER_PACK_ACTIVATED_EVENT } from "./constants.js";

export function createWorkshopEvents({
  emitTo,
  emit,
}) {
  async function emitCharacterPackActivated(activePackId) {
    try {
      await emitTo("main", CHARACTER_PACK_ACTIVATED_EVENT, { packId: activePackId });
    } catch {
      await emit(CHARACTER_PACK_ACTIVATED_EVENT, { packId: activePackId });
    }
  }

  return { emitCharacterPackActivated };
}
