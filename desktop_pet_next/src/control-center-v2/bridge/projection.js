import { buildCharacterRuntimePatchFromSettingsSnapshot, buildMusicRuntimePatch } from "../../control-center/data-sources.js";

export function withLiveRuntime(rawSnapshot, runtimeSnapshot) {
  if (!runtimeSnapshot) return rawSnapshot;
  const musicRuntime = runtimeSnapshot.music ? buildMusicRuntimePatch({
    musicSnapshot: runtimeSnapshot.music,
    petState: runtimeSnapshot.state || {}
  }) : null;
  const characterPatch = buildCharacterRuntimePatchFromSettingsSnapshot(runtimeSnapshot);
  return {
    ...rawSnapshot,
    ...(musicRuntime ? { musicRuntime } : {}),
    ...(characterPatch ? { characterRuntime: { ...rawSnapshot.characterRuntime, ...characterPatch } } : {})
  };
}

export function withBridgeStatus(rawSnapshot, status, error) {
  return {
    ...rawSnapshot,
    controlCenterV2: {
      liveSnapshotStatus: status,
      ...(error ? { liveSnapshotError: error } : {})
    }
  };
}
