const VALID_ENDPOINT_ACTIONS = new Set(["commit", "discard"]);

export class RealtimeVoiceCallFlow {
  constructor({
    onListenRequested = null,
    schedule = (callback) => queueMicrotask(callback)
  } = {}) {
    this.onListenRequested = onListenRequested;
    this.schedule = schedule;
    this.active = false;
    this.currentTurn = null;
    this.nextRevision = 1;
    this.listenRequestScheduled = false;
  }

  start() {
    if (this.active) return false;
    this.active = true;
    return true;
  }

  beginListening() {
    if (!this.active || this.currentTurn) return null;
    const turn = {
      revision: this.nextRevision,
      phase: "listening",
      endpointAccepted: false
    };
    this.nextRevision += 1;
    this.currentTurn = turn;
    return turn;
  }

  acceptEndpoint(turn, action) {
    const normalizedAction = String(action || "").trim();
    if (
      !this.active ||
      this.currentTurn !== turn ||
      turn.endpointAccepted ||
      !VALID_ENDPOINT_ACTIONS.has(normalizedAction)
    ) {
      return false;
    }
    turn.endpointAccepted = true;
    turn.phase = normalizedAction === "commit" ? "finalizing" : "discarding";
    return true;
  }

  markResponding(turn) {
    if (
      !this.active ||
      this.currentTurn !== turn ||
      turn.phase !== "finalizing"
    ) {
      return false;
    }
    turn.phase = "responding";
    return true;
  }

  allowOverlapListening(turn) {
    if (
      !this.active ||
      this.currentTurn !== turn ||
      turn.phase !== "responding"
    ) {
      return false;
    }
    this.currentTurn = null;
    this.requestNextListeningTurn();
    return true;
  }

  complete(turn) {
    if (
      !this.active ||
      !turn ||
      turn.phase === "completed" ||
      turn.phase === "stopped"
    ) {
      return false;
    }
    if (this.currentTurn !== turn && turn.phase !== "responding") return false;
    turn.phase = "completed";
    if (this.currentTurn === turn) this.currentTurn = null;
    this.requestNextListeningTurn();
    return true;
  }

  requestNextListeningTurn() {
    if (
      !this.active ||
      this.currentTurn ||
      this.listenRequestScheduled ||
      typeof this.onListenRequested !== "function"
    ) {
      return false;
    }
    this.listenRequestScheduled = true;
    this.schedule(() => {
      this.listenRequestScheduled = false;
      if (!this.active || this.currentTurn) return;
      this.onListenRequested();
    });
    return true;
  }

  stop() {
    if (!this.active) return false;
    this.active = false;
    this.listenRequestScheduled = false;
    if (this.currentTurn) this.currentTurn.phase = "stopped";
    this.currentTurn = null;
    return true;
  }
}
