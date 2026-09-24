"""Bounded, observable stdin writes. A receipt means pipe delivery, not execution."""
from __future__ import annotations

import hashlib
import threading


class InputChannel:
    def __init__(self, pipe):
        self.pipe = pipe
        self.lock = threading.Lock()
        self.sequence = 0
        self.fingerprint = ""
        self.ended = False
        self.result = {"status": "idle", "sequence": 0, "written_bytes": 0, "reason": ""}

    def submit(self, *, sequence: int, text: str, close: bool) -> dict:
        try:
            payload = text.encode("utf-8")
        except UnicodeEncodeError:
            with self.lock:
                return self._rejected("input_requires_valid_utf8")
        fingerprint = hashlib.sha256(payload + (b"\x01" if close else b"\x00")).hexdigest()
        with self.lock:
            if sequence == self.sequence:
                if fingerprint == self.fingerprint:
                    return {**self.result, "stdin_closed": self.pipe.closed, "next_sequence": self.sequence + 1}
                return self._rejected("input_sequence_conflict")
            if sequence != self.sequence + 1:
                return self._rejected("input_sequence_out_of_order")
            if self.result["status"] == "pending":
                return self._rejected("input_write_pending")
            if self.ended or self.pipe.closed:
                return self._rejected("stdin_closed")
            self.sequence = sequence
            self.fingerprint = fingerprint
            self.result = {"status": "pending", "sequence": sequence, "written_bytes": 0, "reason": ""}
        # Never hold the executor or channel lock while a pipe write blocks.
        threading.Thread(target=self._write, args=(payload, close), daemon=True).start()
        return self.status()

    def _write(self, payload: bytes, close: bool):
        written = 0
        try:
            while written < len(payload):
                count = self.pipe.write(payload[written:written + 4096])
                if not count:
                    raise BrokenPipeError()
                written += count
                with self.lock:
                    self.result["written_bytes"] = written
            if close:
                self.pipe.close()
            with self.lock:
                self.result.update(status="written", stdin_closed=self.pipe.closed)
        except (OSError, ValueError):
            with self.lock:
                self.result.update(status="input_unknown", reason="pipe_write_failed_do_not_resend")
        finally:
            with self.lock:
                if self.ended and not self.pipe.closed:
                    self.pipe.close()

    def finish(self):
        with self.lock:
            self.ended = True
            if self.result["status"] != "pending" and not self.pipe.closed:
                self.pipe.close()

    def status(self) -> dict:
        with self.lock:
            return {**self.result, "stdin_closed": self.pipe.closed, "next_sequence": self.sequence + 1}

    def _rejected(self, reason: str) -> dict:
        return {"status": "rejected", "reason": reason, "sequence": self.sequence,
                "next_sequence": self.sequence + 1, "written_bytes": 0}
