"""Host-owned Agent-turn router shared by every channel and plugin path."""

from __future__ import annotations

from .plugin_turn_requests import HostTurnRequests


class HostAgentEventRouter(HostTurnRequests):
    """Compatibility class name for the canonical channel turn-request router."""


__all__ = ["HostAgentEventRouter"]
