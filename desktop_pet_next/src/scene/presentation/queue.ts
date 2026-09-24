import type { Beat, Presentation, Receipt } from "../domain/types";

export interface PlaybackView {
  beat: Beat | null;
  visibleText: string;
  revealed: boolean;
  index: number;
  total: number;
  auto: boolean;
}

export class PresentationQueue {
  private presentation: Presentation | null = null;
  private index = 0;
  private chars = 0;
  private elapsed = 0;
  private hold = 0;
  private audioDone = true;
  private interrupted = false;
  private auto = false;
  private completed = false;
  constructor(
    private changed: (view: PlaybackView) => void,
    private receipt: (kind: Receipt["kind"], beat: Beat, presentation: Presentation) => void,
    private play: (beat: Beat, generation: number) => void,
    private stopAudio: () => void,
    private onComplete?: (presentation: Presentation, auto: boolean) => void,
  ) {}

  start(presentation: Presentation, startIndex = 0) {
    this.stop();
    this.presentation = presentation;
    this.index = startIndex;
    this.enter();
  }
  private enter() {
    const beat = this.current();
    this.chars = 0;
    this.elapsed = 0;
    this.hold = 0;
    this.audioDone = false;
    this.interrupted = false;
    this.completed = false;
    if (beat && this.presentation) {
      this.receipt("display_started", beat, this.presentation);
      this.play(beat, this.presentation.generation);
    }
    this.publish();
  }
  current() {
    return this.presentation?.beats[this.index] || null;
  }
  toggleAuto() {
    this.auto = !this.auto;
    this.publish();
  }
  isAuto() {
    return this.auto;
  }
  tick(ms: number) {
    const beat = this.current();
    if (!beat || this.interrupted || this.completed) return;
    const size = [...beat.speech].length;
    if (this.chars < size) {
      this.elapsed += Math.min(ms, 100);
      const next = Math.min(size, Math.floor(this.elapsed / 32));
      if (next !== this.chars) {
        this.chars = next;
        if (next === size) this.receipt("text_revealed", beat, this.presentation!);
        this.publish();
      }
    } else if ((this.auto || beat.advance !== "click") && this.audioDone) {
      this.hold += Math.min(ms, 100);
      if (this.hold >= 1100) this.next();
    }
  }
  next() {
    const beat = this.current();
    if (!beat || this.interrupted) return;
    const size = [...beat.speech].length;
    if (this.chars < size) {
      this.chars = size;
      this.receipt("text_revealed", beat, this.presentation!);
      this.publish();
      return;
    }
    if (this.presentation && this.index >= this.presentation.beats.length - 1) {
      this.completed = true;
      this.onComplete?.(this.presentation, this.auto);
      return;
    }
    this.stopAudio();
    this.index++;
    this.enter();
  }
  audioFinished(beatId: string, generation: number) {
    if (this.presentation?.generation === generation && this.current()?.beat_id === beatId) this.audioDone = true;
  }
  stop() {
    this.stopAudio();
    if (this.current() && !this.interrupted && !this.completed) this.receipt("interrupted", this.current()!, this.presentation!);
    this.interrupted = true;
    this.presentation = null;
    this.publish();
  }
  private publish() {
    const beat = this.current();
    this.changed({
      beat,
      visibleText: beat ? [...beat.speech].slice(0, this.chars).join("") : "",
      revealed: !!beat && this.chars >= [...beat.speech].length,
      index: this.index,
      total: this.presentation?.beats.length || 0,
      auto: this.auto,
    });
  }
}
