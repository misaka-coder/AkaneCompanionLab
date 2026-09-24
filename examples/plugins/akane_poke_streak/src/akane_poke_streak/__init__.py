"""Event-only SDK plugin: turn repeated QQ pokes into one current observation."""

from __future__ import annotations

from collections import OrderedDict
from threading import Lock
from typing import Any

from akane_plugin import POKE_CONVERSATION_EVENT, Plugin, ToolContext


PLUGIN_ID = "akane.sample.poke-streak"
PLUGIN_VERSION = "0.2.0"
POKE_STREAK_WINDOW_SECONDS = 90
POKE_STREAK_OBSERVATION_KEY = "poke.streak"
RECENT_EVENT_ID_CAPACITY = 2_048

plugin = Plugin(PLUGIN_ID, version=PLUGIN_VERSION, permissions=("context.observe",))


class PokeStreakTracker:
    """Count consecutive pokes per conversation and actor without a model turn."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._recent_event_ids: OrderedDict[str, None] = OrderedDict()
        self._streaks: dict[tuple[str, str], tuple[int, int]] = {}

    def record(self, *, event_id: str, conversation_id: str, actor_id: str, occurred_at_ms: int) -> int:
        """Return the current consecutive count for this conversation/actor."""

        key = (conversation_id, actor_id)
        with self._lock:
            if event_id in self._recent_event_ids:
                self._recent_event_ids.move_to_end(event_id)
                return 0
            self._recent_event_ids[event_id] = None
            while len(self._recent_event_ids) > RECENT_EVENT_ID_CAPACITY:
                self._recent_event_ids.popitem(last=False)
            previous_at_ms, previous_count = self._streaks.get(key, (0, 0))
            elapsed_ms = occurred_at_ms - previous_at_ms
            count = (
                previous_count + 1
                if previous_count > 0 and 0 <= elapsed_ms <= POKE_STREAK_WINDOW_SECONDS * 1000
                else 1
            )
            self._streaks[key] = (occurred_at_ms, count)
            return count


tracker = PokeStreakTracker()


@plugin.tool
async def bind(ctx: ToolContext) -> dict[str, Any]:
    """Bind this conversation so its poke events reach the streak observer."""

    return (await ctx.events.bind("akane.sample.poke-streak.streak")).as_dict()


@plugin.on(POKE_CONVERSATION_EVENT, name="streak", scope="conversation")
async def track_poke(event, ctx):
    """Expose the streak as a current fact; never request a model turn."""

    data = event.data if isinstance(event.data, dict) else {}
    actor_id = str(data.get("actor_id") or "").strip()
    conversation_id = str(data.get("conversation_id") or "").strip()
    if not actor_id or not conversation_id or not event.event_id:
        return None
    count = tracker.record(
        event_id=event.event_id,
        conversation_id=conversation_id,
        actor_id=actor_id,
        occurred_at_ms=event.occurred_at_ms,
    )
    if count < 2:
        return None
    return await ctx.observe(POKE_STREAK_OBSERVATION_KEY, {
        "actor_id": actor_id,
        "conversation_kind": str(data.get("conversation_kind") or "direct"),
        "consecutive_count": count,
        "window_seconds": POKE_STREAK_WINDOW_SECONDS,
    })


def create_plugin() -> Plugin:
    return plugin


__all__ = [
    "PLUGIN_ID",
    "PLUGIN_VERSION",
    "POKE_STREAK_WINDOW_SECONDS",
    "POKE_STREAK_OBSERVATION_KEY",
    "RECENT_EVENT_ID_CAPACITY",
    "PokeStreakTracker",
    "bind",
    "create_plugin",
    "plugin",
    "track_poke",
]
