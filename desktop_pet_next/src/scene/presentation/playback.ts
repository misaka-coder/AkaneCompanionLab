import { ref } from "vue";
import type { Beat, Presentation, Receipt, Snapshot } from "../domain/types";
import type { SceneClient } from "../bridges/client";
import { SceneDelivery } from "../bridges/delivery";
import { PlaybackCheckpoint } from "../bridges/checkpoint";
import { requestVoice } from "../bridges/voice";
import { friendlyError } from "../bridges/errors";
import { SceneVoice } from "../audio/voice";
import { validate } from "../contracts/validate";
import { PresentationQueue, type PlaybackView } from "./queue";
import { useRoom } from "../stores/room";

type Turn = { message: string; event_id: string; request_id: string; generation: number };

export function usePlayback(room: ReturnType<typeof useRoom>) {
  const pending = ref(false);
  const retryable = ref(false);
  const view = ref<PlaybackView>({ beat: null, visibleText: "", revealed: false, index: 0, total: 0, auto: false });
  const history = ref<{ name: string; text: string }[]>([]);
  const pinned = ref<Snapshot | null>(null);
  const speaking = ref(false);
  const voice = new SceneVoice();
  let client: SceneClient | null = null;
  let delivery: SceneDelivery | null = null;
  let playing: Presentation | null = null;
  let request: AbortController | null = null;
  let last: Turn | null = null;
  let generation = 0;
  let checkpoint: PlaybackCheckpoint | null = null;
  let cursor = 0;
  let audioStarted = false;
  const completionListeners: ((presentation: Presentation, auto: boolean) => void)[] = [];
  const record = (kind: Receipt["kind"], beat: Beat, p: Presentation) => {
    if (client)
      delivery?.add({
        identity: client.identity,
        turn_id: p.turn_id,
        beat_id: beat.beat_id,
        generation: p.generation,
        kind,
      });
  };
  function stopVoice() {
    if (audioStarted && view.value.beat && playing) record("audio_interrupted", view.value.beat, playing);
    audioStarted = false;
    speaking.value = false;
    voice.stop();
  }
  const queue = new PresentationQueue(
    (value) => {
      view.value = value;
      if (value.beat) room.emotion = value.beat.emotion_id;
      else pinned.value = null;
    },
    (kind, beat, p) => {
      if (kind === "text_revealed") {
        history.value.push({ name: room.snapshot?.character_name || "", text: beat.speech });
        cursor = beat.sequence + 1;
        if (pinned.value) checkpoint?.write(p, pinned.value, cursor);
      }
      record(kind, beat, p);
    },
    (beat, gen) => {
      audioStarted = false;
      if (!client || room.muted) {
        queue.audioFinished(beat.beat_id, gen);
        return;
      }
      const active = client;
      const presentation = playing!;
      void voice.play((signal) => requestVoice(active, beat, signal), {
        started: () => {
          if (!audioStarted) record("audio_started", beat, presentation);
          audioStarted = true;
          speaking.value = true;
        },
        completed: () => {
          audioStarted = false;
          speaking.value = false;
          record("audio_completed", beat, presentation);
          queue.audioFinished(beat.beat_id, gen);
        },
        failed: (reason) => {
          stopVoice();
          room.error = reason;
          queue.audioFinished(beat.beat_id, gen);
        },
      });
    },
    stopVoice,
    (p, auto) => {
      completionListeners.forEach((fn) => fn(p, auto));
    },
  );

  async function run(turn: Turn) {
    if (!client || pending.value) return;
    queue.stop();
    pending.value = true;
    room.error = "";
    retryable.value = false;
    last = turn;
    const gen = ++generation;
    const active = client;
    const resources = room.snapshot;
    const controller = new AbortController();
    request = controller;
    try {
      await delivery?.flush();
      if (controller.signal.aborted) return;
      const result = validate<Presentation>(
        "Presentation",
        await active.request("/scene/turn", { identity: active.identity, ...turn }, controller.signal),
      );
      if (gen !== generation) return;
      playing = result;
      queue.start(result);
      pinned.value = resources;
      cursor = 0;
      if (resources) checkpoint?.write(result, resources, cursor);
      last = null;
    } catch (error) {
      if (gen === generation && !controller.signal.aborted) {
        room.error = friendlyError(error);
        retryable.value = true;
        if (/model_response_failed|model_presentation_invalid|presentation_failed/.test(String(error))) {
          last = { ...turn, request_id: crypto.randomUUID(), generation: generation + 1 };
        }
      }
    } finally {
      if (gen === generation) pending.value = false;
    }
  }
  function send(message: string, eventId?: string) {
    if (pending.value) return Promise.resolve();
    if (message) history.value.push({ name: "你", text: message });
    return run({ message, event_id: eventId || "", request_id: crypto.randomUUID(), generation: generation + 1 });
  }
  function stop() {
    checkpoint?.clear();
    if (pending.value && last && client) {
      const owner = client;
      void client.request("/scene/cancel", { identity: client.identity, request_id: last.request_id }).catch(() => {
        if (client === owner) room.error = "停止指令尚未送达；已暂停本地播放，宿主可能仍在结束当前回复。";
      });
    }
    generation++;
    request?.abort();
    request = null;
    queue.stop();
    pending.value = false;
    retryable.value = false;
    last = null;
  }
  async function bind(next: SceneClient) {
    stop();
    client = next;
    const epoch = generation;
    history.value = [];
    delivery = new SceneDelivery(next, (text) => {
      if (client === next) room.error = text;
    });
    await delivery.flush();
    if (client !== next || epoch !== generation) return;
    checkpoint = new PlaybackCheckpoint(next);
    const saved = checkpoint.read();
    if (saved && saved.cursor < saved.presentation.beats.length) {
      playing = saved.presentation;
      cursor = saved.cursor;
      queue.start(playing, cursor);
      pinned.value = saved.snapshot;
    }
  }
  function mute() {
    stopVoice();
    const b = view.value.beat;
    if (b && playing) queue.audioFinished(b.beat_id, playing.generation);
  }
  function play(p: Presentation) {
    queue.stop();
    playing = p;
    generation = p.generation;
    cursor = 0;
    pinned.value = null;
    queue.start(p);
  }
  function playBeats(beats: Beat[]) {
    const p: Presentation = {
      protocol: "scene_presentation_v1",
      turn_id: `story_${Date.now()}`,
      generation: ++generation,
      resource_revision: "",
      beats,
      diagnostics: [],
    };
    play(p);
  }
  return {
    pending,
    speaking,
    view,
    history,
    pinned,
    retryable,
    send,
    stop,
    bind,
    mute,
    play,
    playBeats,
    retry: () => (last ? run(last) : Promise.resolve()),
    tick: (ms: number) => queue.tick(ms),
    advance: () => queue.next(),
    auto: () => queue.toggleAuto(),
    onComplete: (fn: (presentation: Presentation, auto: boolean) => void) => {
      completionListeners.push(fn);
      return () => {
        const idx = completionListeners.indexOf(fn);
        if (idx >= 0) completionListeners.splice(idx, 1);
      };
    },
  };
}
