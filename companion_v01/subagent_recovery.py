"""One request retry at most; tools are executed only after a usable response."""
from __future__ import annotations

import re
from typing import Callable, Any


def model_failure_reason(error: str) -> str:
    text = str(error or "").lower()
    if re.search(r"timeout|timed.out|deadline", text):
        return "request_timeout"
    if re.search(r"401|403|unauthor|forbidden|api.?key|authentication", text):
        return "authentication"
    if re.search(r"400|422|invalid.request|bad.request|context.{0,15}(length|limit)|too.many.tokens", text):
        return "invalid_request"
    if re.search(r"429|rate.limit|overload|502|503|504|connection|connecterror|connectionerror|server.error|temporar", text):
        return "transient"
    if "cancel" in text:
        return "cancelled"
    return "provider_failure"


def call_with_recovery(call: Callable[[], Any], *, cancelled: Callable[[], bool]) -> tuple[Any, int]:
    response = call()
    if (response.error and getattr(response, "request_attempts", 1) < 2
            and model_failure_reason(response.error) == "transient" and not cancelled()):
        return call(), 1
    return response, 0
