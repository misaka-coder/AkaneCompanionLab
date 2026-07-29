const VALID_ENDPOINT_ACTIONS = new Set(["commit", "discard"]);
const DEFAULT_RECONNECT_DELAYS_MS = [400, 800, 1600, 3200, 4000];

export function hasRealtimeVoiceInputEvidence(snapshot = {}) {
  return Boolean(snapshot?.speech_started || snapshot?.has_transcript);
}

export function shouldReconnectPassiveVoiceFailure({
  committed = false,
  retryable = false,
  detectorSnapshot = {}
} = {}) {
  return Boolean(
    !committed &&
      retryable &&
      !hasRealtimeVoiceInputEvidence(detectorSnapshot)
  );
}

export class RealtimeVoiceReconnectBackoff {
  constructor({
    delaysMs = DEFAULT_RECONNECT_DELAYS_MS,
    schedule = (callback, delayMs) => setTimeout(callback, delayMs),
    cancel = (timer) => clearTimeout(timer),
    onReconnect = null,
    onExhausted = null
  } = {}) {
    this.delaysMs = [...delaysMs]
      .map((value) => Math.max(0, Math.round(Number(value) || 0)))
      .filter((value) => Number.isFinite(value));
    this.scheduleTimer = schedule;
    this.cancelTimer = cancel;
    this.onReconnect = onReconnect;
    this.onExhausted = onExhausted;
    this.timer = null;
    this.attempts = 0;
    this.stopped = false;
  }

  get pending() {
    return this.timer !== null;
  }

  schedule() {
    if (this.stopped) return "stopped";
    if (this.pending) return "pending";
    if (this.attempts >= this.delaysMs.length) {
      this.onExhausted?.({ attempts: this.attempts });
      return "exhausted";
    }
    const delayMs = this.delaysMs[this.attempts];
    const attempt = this.attempts + 1;
    this.attempts = attempt;
    this.timer = this.scheduleTimer(() => {
      this.timer = null;
      if (this.stopped) return;
      this.onReconnect?.({ attempt, delayMs });
    }, delayMs);
    return "scheduled";
  }

  reset() {
    if (this.stopped) return false;
    if (this.pending) {
      this.cancelTimer(this.timer);
      this.timer = null;
    }
    this.attempts = 0;
    return true;
  }

  stop() {
    if (this.stopped) return false;
    this.stopped = true;
    if (this.pending) this.cancelTimer(this.timer);
    this.timer = null;
    return true;
  }
}

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

  complete(turn, { requestNext = true } = {}) {
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
    if (requestNext) this.requestNextListeningTurn();
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
