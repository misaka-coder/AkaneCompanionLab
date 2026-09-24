import { afterEach, expect, it, vi } from "vitest";
import { SceneVoice } from "./voice";

afterEach(() => vi.unstubAllGlobals());
it("a late synthesis response cannot create audio after stop", async () => {
  const Audio = vi.fn();
  vi.stubGlobal("Audio", Audio);
  let finish!: (blob: Blob) => void;
  let signal!: AbortSignal;
  const voice = new SceneVoice();
  const callbacks = { started: vi.fn(), completed: vi.fn(), failed: vi.fn() };
  const playing = voice.play((s) => {
    signal = s;
    return new Promise((resolve) => {
      finish = resolve;
    });
  }, callbacks);
  voice.stop();
  finish(new Blob(["audio"]));
  await playing;
  expect(signal.aborted).toBe(true);
  expect(Audio).not.toHaveBeenCalled();
  expect(callbacks.completed).not.toHaveBeenCalled();
});
it("a failed service keeps its reason without claiming audio delivery", async () => {
  const voice = new SceneVoice();
  const callbacks = { started: vi.fn(), completed: vi.fn(), failed: vi.fn() };
  await voice.play(async () => {
    throw Error("service offline");
  }, callbacks);
  expect(callbacks.failed).toHaveBeenCalledWith("service offline");
  expect(callbacks.started).not.toHaveBeenCalled();
  expect(callbacks.completed).not.toHaveBeenCalled();
});
