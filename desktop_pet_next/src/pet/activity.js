/** Execute host-issued music tools; ordinary reply JSON has no side effects. */
export function createActivityController({
  els, platform: platformPort, scope: runtimeScope, music: musicPort,
  character: characterPort, backend: backendPort, status: statusPort, settings: settingsPort
}) {
  const operations = new Map();
  const executedActivityOperationIds = new Set();
  function rememberActivityOperation(id) {
    executedActivityOperationIds.add(id);
    if (executedActivityOperationIds.size > 128) executedActivityOperationIds.delete(executedActivityOperationIds.values().next().value);
  }
  function hasExecutedActivityOperation(id) { return executedActivityOperationIds.has(id); }
  async function receipt(command, body) {
    const response = await platformPort.backendFetch(backendPort.buildBackendEndpointUrl("local_music_control", "/desktop-pet/music/control-result"), {
      method: "POST", headers: { "Content-Type": "application/json" }, connectTimeout: 5000,
      body: JSON.stringify({ ...characterPort.buildBackendCharacterContext(), operation_id: command.operation_id, ...body })
    });
    if (!response.ok) throw Error("desktop_music_receipt_failed");
    return response.json();
  }
  async function execute(command) {
    const identity = characterPort.buildBackendCharacterContext();
    const scope = runtimeScope.capture();
    const valid = () => runtimeScope.isCurrent(scope) && Date.now() < Number(command.expires_at) * 1000;
    if (command.profile_user_id !== identity.real_user_id || command.session_id !== identity.user_id || command.character_pack_id !== identity.character_pack_id || !valid()) return;
    if (!(await receipt(command, { phase: "claim" })).ok) return;
    let result;
    try {
      if (!valid()) throw Error("desktop_music_scope_changed");
      let ok = false;
      const action = command.action;
      const source = String(command.source_id || "").trim();
      if (action === "next") ok = await musicPort.playNextMusicTrack();
      else if (action === "previous") ok = await musicPort.playPreviousMusicTrack();
      else if (action === "play" && source) {
        const index = musicPort.findMusicTrackIndexBySourceId(source);
        if (index >= 0) ok = await musicPort.playMusicQueueIndex(index);
        else {
          const match = source.match(/^workspace:(attachment|generated):(.+)$/);
          const item = musicPort.workspaceAudioCatalog.find(item => item.sourceId === source || item.handle === source);
          if (match) ok = await musicPort.playWorkspaceAudioItem({ itemType: match[1], handle: match[2] });
          else if (item) ok = await musicPort.playWorkspaceAudioItem(item);
          else throw Error("audio_source_unavailable");
        }
      } else if (action === "play" || action === "resume") {
        if (musicPort.musicPlaying) ok = true;
        else if (musicPort.musicPaused && musicPort.musicTrack) ok = await musicPort.toggleMusicPlayback();
        else ok = await musicPort.playMusicQueueIndex(musicPort.getSafeMusicQueueIndex());
      } else if (action === "pause" && musicPort.musicTrack) { musicPort.pauseMusic(); ok = true; }
      else if (action === "stop") { musicPort.stopMusic({ announce: false }); ok = true; }
      else throw Error("no_active_local_music");
      if (!runtimeScope.isCurrent(scope)) throw Error("desktop_music_scope_changed");
      const wantsPaused = action === "pause" || action === "stop";
      ok = Boolean(ok && (wantsPaused ? els.musicPlayer.paused : !els.musicPlayer.paused));
      result = { ok, reason: ok ? "" : "desktop_music_state_unconfirmed", title: musicPort.musicTrack?.displayName || "" };
    } catch (error) {
      result = { ok: false, reason: String(error?.message || "desktop_music_execution_failed").slice(0, 100) };
    }
    statusPort.updateActivityControls();
    settingsPort.scheduleSettingsSnapshot();
    await receipt(command, { phase: "complete", ...result });
  }
  function executeLocalMusicControl(command) {
    const id = String(command?.operation_id || "");
    if (!id) return Promise.resolve();
    if (operations.has(id)) return operations.get(id);
    const pending = execute(command).catch(() => {});
    operations.set(id, pending);
    if (operations.size > 128) operations.delete(operations.keys().next().value);
    return pending;
  }
  return { executeLocalMusicControl, rememberActivityOperation, hasExecutedActivityOperation };
}
