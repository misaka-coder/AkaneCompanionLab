export class SceneVoice {
  private version = 0;
  private audio: HTMLAudioElement | null = null;
  private url = "";
  private request: AbortController | null = null;

  async play(
    load: (signal: AbortSignal) => Promise<Blob>,
    callbacks: { started: () => void; completed: () => void; failed: (reason: string) => void },
  ) {
    this.stop();
    const version = this.version;
    const controller = new AbortController();
    this.request = controller;
    try {
      const blob = await load(controller.signal);
      if (version !== this.version) return;
      this.url = URL.createObjectURL(blob);
      const audio = new Audio(this.url);
      this.audio = audio;
      audio.onplaying = () => {
        if (version === this.version) callbacks.started();
      };
      audio.onended = () => {
        if (version === this.version) {
          callbacks.completed();
          this.stop();
        }
      };
      audio.onerror = () => {
        if (version === this.version) {
          callbacks.failed("音频无法播放，可以继续阅读文字。");
          this.stop();
        }
      };
      await audio.play();
    } catch (error) {
      if (version === this.version && !controller.signal.aborted) {
        callbacks.failed((error as Error).message);
        this.stop();
      }
    }
  }

  stop() {
    this.version++;
    this.request?.abort();
    this.request = null;
    if (this.audio) {
      this.audio.onplaying = null;
      this.audio.onended = null;
      this.audio.onerror = null;
      this.audio.pause();
      this.audio.removeAttribute("src");
      this.audio.load();
      this.audio = null;
    }
    if (this.url) URL.revokeObjectURL(this.url);
    this.url = "";
  }
}
