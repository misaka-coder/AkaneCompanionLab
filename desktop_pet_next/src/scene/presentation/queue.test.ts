import { describe, expect, it, vi } from "vitest";
import { PresentationQueue, type PlaybackView } from "./queue";
import type { Presentation } from "../domain/types";

function setup() {
  let view: PlaybackView;
  const receipt = vi.fn();
  const play = vi.fn();
  const stop = vi.fn();
  const queue = new PresentationQueue(
    (v) => {
      view = v;
    },
    receipt,
    play,
    stop,
  );
  const p: Presentation = {
    protocol: "scene_presentation_v1",
    turn_id: "test",
    generation: 7,
    resource_revision: "1",
    diagnostics: [],
    beats: ["你好。", "给你一杯茶。", "明天见。"].map((speech, sequence) => ({
      speech,
      sequence,
      beat_id: "beat" + sequence,
      emotion_id: ["normal", "smug", "shy"][sequence],
      motion_id: "idle",
      advance: "click",
    })),
  };
  queue.start(p);
  return { queue, receipt, play, stop, p, view: () => view! };
}
describe("presentation delivery", () => {
  it("click reveals then advances without a new model request", () => {
    const s = setup();
    expect(s.view().beat?.emotion_id).toBe("normal");
    s.queue.next();
    expect(s.view().visibleText).toBe("你好。");
    s.queue.next();
    expect(s.view().beat?.emotion_id).toBe("smug");
    s.queue.next();
    s.queue.next();
    expect(s.view().beat?.emotion_id).toBe("shy");
    expect(s.play).toHaveBeenCalledTimes(3);
    expect(s.receipt.mock.calls.filter((c) => c[0] === "text_revealed")).toHaveLength(2);
  });
  it("AUTO waits for the correct audio generation", () => {
    const s = setup();
    s.queue.next();
    s.queue.toggleAuto();
    for (let i = 0; i < 20; i++) s.queue.tick(100);
    expect(s.view().index).toBe(0);
    s.queue.audioFinished("beat0", 6);
    for (let i = 0; i < 20; i++) s.queue.tick(100);
    expect(s.view().index).toBe(0);
    s.queue.audioFinished("beat0", 7);
    for (let i = 0; i < 11; i++) s.queue.tick(100);
    expect(s.view().index).toBe(1);
  });
  it("stop prevents late advancement and resume skips completed text", () => {
    const s = setup();
    s.queue.stop();
    s.queue.audioFinished("beat0", 7);
    s.queue.next();
    expect(s.view().beat).toBeNull();
    s.queue.start(s.p, 2);
    expect(s.view().beat?.beat_id).toBe("beat2");
    expect(s.play).toHaveBeenCalledTimes(2);
  });
});
