import type { SceneClient } from "../bridges/client";
import { friendlyError } from "../bridges/errors";
import type { Beat, StoryRunState } from "../domain/types";
import type { usePlayback } from "../presentation/playback";
import type { useRoom } from "../stores/room";

export function useStoryController(
  getClient: () => SceneClient | null,
  room: ReturnType<typeof useRoom>,
  playback: ReturnType<typeof usePlayback>,
  notify: (text: string) => void,
) {
  let storyScope = 0;
  let activeAbort: AbortController | null = null;
  let ownsBusy = false;

  function applyStoryRun(run: StoryRunState) {
    const node = run.current_node;
    if (node.background_id) room.storyBackground = node.background_id;
    if (node.music_id) room.storyMusic = node.music_id;
    if (node.outfit_id) room.storyOutfit = node.outfit_id;
    if (node.emotion_id) {
      room.emotion = node.emotion_id;
    }
    if (run.presentation) {
      playback.play(run.presentation);
    } else if (node.beats && node.beats.length > 0) {
      const beatsToPlay: Beat[] = node.beats.map((b, idx) => ({
        beat_id: `${node.node_id}_${idx}`,
        sequence: idx,
        speech: b.speech,
        emotion_id: b.emotion_id || node.emotion_id || "normal",
        motion_id: b.motion_id || node.motion_id || "idle",
        advance: b.advance || "click",
      }));
      playback.playBeats(beatsToPlay);
    } else if (node.speech) {
      playback.playBeats([
        {
          beat_id: `beat_${node.node_id}`,
          sequence: 0,
          speech: node.speech,
          emotion_id: node.emotion_id || "normal",
          motion_id: node.motion_id || "idle",
          advance: "click",
        },
      ]);
    }
  }

  playback.onComplete((presentation, _auto) => {
    if (!room.activeStoryRun || room.busy) return;
    if (room.activeStoryRun.presentation && presentation.turn_id !== room.activeStoryRun.presentation.turn_id) return;
    const node = room.activeStoryRun.current_node;
    if (node.kind === "script") {
      void stepStory();
    } else if (node.kind === "ending") {
      void confirmEnding(node.node_id);
    }
  });

  async function confirmEnding(endingId: string) {
    const client = getClient();
    const run = room.activeStoryRun;
    if (!client || !run) return;
    try {
      const updated = await client.storyCompleteEnding(run.run_id, endingId);
      room.activeStoryRun = updated;
      notify(`达成结局：${run.current_node.ending_title || "故事完结"}`);
      void loadCatalog();
    } catch {
      /* ignore */
    }
  }

  async function loadCatalog() {
    const client = getClient();
    if (!client) return;
    try {
      const res = await client.storyCatalog();
      room.stories = res.stories;
    } catch {
      /* ignore */
    }
  }

  async function startStory(storyId: string, forceRestart = false) {
    const client = getClient();
    if (!client || room.busy) return;
    playback.stop();
    storyScope++;
    activeAbort?.abort();
    activeAbort = null;
    const currentScope = storyScope;
    ownsBusy = true;
    room.busy = true;
    room.error = "";
    try {
      const run = await client.storyStart(storyId, forceRestart);
      if (currentScope !== storyScope) return;
      room.activeStoryRun = run;
      room.storyBackground = "";
      room.storyMusic = "";
      room.storyOutfit = "";
      room.drawer = null;
      applyStoryRun(run);
      notify(forceRestart ? "故事重新开始" : "进入剧场");
    } catch (e) {
      if (currentScope === storyScope) room.error = friendlyError(e);
    } finally {
      if (currentScope === storyScope) { room.busy = false; ownsBusy = false; }
    }
  }

  async function stepStory(choiceId = "", userMessage = "", advanceOnly = false) {
    const client = getClient();
    if (!client || !room.activeStoryRun || room.busy) return;
    storyScope++;
    activeAbort?.abort();
    const controller = new AbortController();
    activeAbort = controller;
    const currentScope = storyScope;
    const currentRunId = room.activeStoryRun.run_id;

    ownsBusy = true;
    room.busy = true;
    room.error = "";
    if (userMessage) {
      playback.history?.value?.push({ name: "你", text: userMessage });
      if (playback.pending) playback.pending.value = true;
    }
    try {
      const run = await client.storyStep(
        room.activeStoryRun.run_id,
        room.activeStoryRun.current_node_id,
        choiceId,
        userMessage,
        advanceOnly,
        controller.signal,
      );
      if (
        currentScope !== storyScope ||
        controller.signal.aborted ||
        !room.activeStoryRun ||
        room.activeStoryRun.run_id !== currentRunId
      ) {
        return;
      }
      room.activeStoryRun = run;
      applyStoryRun(run);
    } catch (e) {
      if (currentScope === storyScope && !controller.signal.aborted) {
        room.error = friendlyError(e);
      }
    } finally {
      if (currentScope === storyScope) {
        activeAbort = null;
        ownsBusy = false;
        room.busy = false;
        if (playback.pending) playback.pending.value = false;
      }
    }
  }

  async function advanceStory() {
    await stepStory("", "", true);
  }

  function stopStory() {
    const client = getClient();
    const run = room.activeStoryRun;
    const wasPending = !!activeAbort;
    storyScope++;
    activeAbort?.abort();
    activeAbort = null;
    if (ownsBusy) room.busy = false;
    ownsBusy = false;
    playback.stop();
    if (wasPending && client && run) {
      void client.request("/scene/story/cancel", {
        identity: client.identity, run_id: run.run_id, expected_node_id: run.current_node_id,
      }).catch(() => {
        if (getClient() === client) room.error = "已停止本地播放，但宿主未确认停止剧情回复。";
      });
    }
  }

  function finishStory() {
    stopStory();
    room.activeStoryRun = null;
    room.storyBackground = "";
    room.storyMusic = "";
    room.storyOutfit = "";
    room.drawer = null;
    room.emotion = "normal";
    notify("返回日常房间");
    void loadCatalog();
  }

  async function importStory(fileName: string, content: string) {
    const client = getClient();
    if (!client || room.busy) return;
    room.busy = true;
    room.error = "";
    try {
      const res = await client.importStory(fileName, content);
      room.stories = res.stories;
      notify("剧本导入成功！");
    } catch (e) {
      room.error = friendlyError(e);
    } finally {
      room.busy = false;
    }
  }

  return {
    loadCatalog,
    startStory,
    stepStory,
    advanceStory,
    confirmEnding,
    finishStory,
    stopStory,
    importStory,
  };
}
