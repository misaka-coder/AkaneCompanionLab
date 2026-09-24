/** Small original interface chimes; independent from character speech. */
export class RoomEffects {
  private context: AudioContext | null = null;
  private muted = true;
  setMuted(value: boolean) {
    this.muted = value;
    if (!value) {
      this.context ||= new AudioContext();
      void this.context.resume().catch(() => {});
    }
  }
  play(kind: "head" | "hand" | "shoulder" | "success") {
    const context = this.context;
    if (this.muted || context?.state !== "running") return;
    const pitch = { head: 660, hand: 784, shoulder: 523, success: 880 }[kind];
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.type = "sine";
    oscillator.frequency.value = pitch;
    gain.gain.setValueAtTime(0.025, context.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + 0.17);
    oscillator.connect(gain);
    gain.connect(context.destination);
    oscillator.onended = () => {
      oscillator.disconnect();
      gain.disconnect();
    };
    oscillator.start();
    oscillator.stop(context.currentTime + 0.18);
  }
  dispose() {
    void this.context?.close();
    this.context = null;
  }
}
