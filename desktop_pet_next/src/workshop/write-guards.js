

export function createWriteGuards({
  writeState,
  view,
}) {
  function isRegistryBusy() {
    return Boolean(writeState.catalogOperation || view.pendingApplyPackId || writeState.personaSaves.size || writeState.calibrationSaves.size
      || writeState.portraitMutations.size || writeState.contextSaves.size || writeState.storySaves.size || writeState.packExports.size);
  }

  function isPackWriteBusy(packId) {
    return Boolean(writeState.catalogOperation || view.pendingApplyPackId === packId
      || writeState.personaSaves.has(packId) || writeState.calibrationSaves.has(packId)
      || writeState.portraitMutations.has(packId) || writeState.contextSaves.has(packId)
      || writeState.storySaves.has(packId) || writeState.packExports.has(packId));
  }

  return { isRegistryBusy, isPackWriteBusy };
}
