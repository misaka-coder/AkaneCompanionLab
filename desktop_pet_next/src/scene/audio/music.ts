export class RoomMusic {
  private current: HTMLAudioElement | null = null;
  private retiring = new Set<HTMLAudioElement>();
  private timer = 0;
  private muted = true;
  private target = "";
  private version = 0;
  private gain = 0.22;
  setDucked(ducked: boolean) {
    this.gain = ducked ? 0.065 : 0.22;
    if (this.current) this.current.volume = this.gain;
  }
  setMuted(muted: boolean) {
    this.muted = muted;
    if (this.current) this.current.muted = muted;
    for (const a of this.retiring) a.muted = muted;
  }
  async change(url: string) {
    if (url === this.target) return;
    this.target = url;
    const version = ++this.version;
    const previous = this.current;
    const next = url ? new Audio(url) : null;
    this.current = next;
    if (previous) this.retiring.add(previous);
    cancelAnimationFrame(this.timer);
    try {
      if (next) {
        next.loop = true;
        next.volume = 0;
        next.muted = this.muted;
        await next.play();
      }
    } catch (error) {
      if (version === this.version) this.dispose();
      throw error;
    }
    if (version !== this.version) return;
    const start = performance.now();
    const fade = (now: number) => {
      const progress = Math.min(1, (now - start) / 450);
      if (next) next.volume = this.gain * progress;
      for (const audio of this.retiring) audio.volume = Math.min(audio.volume, 0.22 * (1 - progress));
      if (progress < 1) this.timer = requestAnimationFrame(fade);
      else {
        for (const audio of this.retiring) {
          audio.pause();
          audio.removeAttribute("src");
          audio.load();
        }
        this.retiring.clear();
      }
    };
    this.timer = requestAnimationFrame(fade);
  }
  dispose() {
    this.version++;
    cancelAnimationFrame(this.timer);
    if (this.current) this.retiring.add(this.current);
    for (const audio of this.retiring) {
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
    }
    this.retiring.clear();
    this.current = null;
    this.target = "";
  }
}
