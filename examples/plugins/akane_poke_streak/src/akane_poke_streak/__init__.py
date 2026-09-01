"""Real event-only Akane plugin example for repeated QQ poke awareness."""

from __future__ import annotations

from collections import OrderedDict
from threading import Lock

from companion_v01.plugin_api import (
    AKANE_PLUGIN_API_VERSION,
    DIRECT_CONVERSATION_EVENT,
    EVENT_SUBSCRIBE_PERMISSION,
    GROUP_CONVERSATION_EVENT,
    PluginEventEnvelope,
    PluginEventResult,
    PluginExternalEvent,
    PluginManifest,
    PluginRegistrar,
)


PLUGIN_ID = "akane.sample.poke-streak"
PLUGIN_VERSION = "0.1.0"
POKE_STREAK_WINDOW_SECONDS = 90
RECENT_EVENT_ID_CAPACITY = 2_048


class PokeStreakEventHandler:
    """Turn duplicate QQ pokes into one bounded, request-local fact."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._recent_event_ids: OrderedDict[str, None] = OrderedDict()
        self._streaks: dict[tuple[str, str], tuple[int, int]] = {}

    async def handle_event(self, event: PluginEventEnvelope) -> PluginEventResult:
        if event.source != "channelcore-onebot":
            return PluginEventResult()
        fields = dict(event.fields)
        if fields.get("trigger_reason") != "qq_poke":
            return PluginEventResult()

        event_id = str(event.event_id or "").strip()
        actor_id = str(fields.get("actor_id") or "").strip()
        subject = str(event.subject or "").strip()
        if not event_id or not actor_id or not subject:
            return PluginEventResult()

        occurred_at = int(event.occurred_at)
        key = (subject, actor_id)
        with self._lock:
            if event_id in self._recent_event_ids:
                self._recent_event_ids.move_to_end(event_id)
                return PluginEventResult()
            self._recent_event_ids[event_id] = None
            while len(self._recent_event_ids) > RECENT_EVENT_ID_CAPACITY:
                self._recent_event_ids.popitem(last=False)

            previous_at, previous_count = self._streaks.get(key, (0, 0))
            elapsed = occurred_at - previous_at
            count = (
                previous_count + 1
                if previous_count > 0 and 0 <= elapsed <= POKE_STREAK_WINDOW_SECONDS
                else 1
            )
            self._streaks[key] = (occurred_at, count)

        if count < 2:
            return PluginEventResult()
        conversation_kind = str(fields.get("conversation_kind") or "direct").strip() or "direct"
        return PluginEventResult(
            delivery="current_turn",
            event=PluginExternalEvent(
                event_type="interaction.qq_poke_streak",
                fields=(
                    ("actor_id", actor_id),
                    ("conversation_kind", conversation_kind),
                    ("consecutive_count", str(count)),
                    ("window_seconds", str(POKE_STREAK_WINDOW_SECONDS)),
                ),
                source=PLUGIN_ID,
            ),
        )


class PokeStreakPlugin:
    manifest = PluginManifest(
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        plugin_api_version=AKANE_PLUGIN_API_VERSION,
        permissions=(EVENT_SUBSCRIBE_PERMISSION,),
    )

    def register(self, registrar: PluginRegistrar) -> None:
        handler = PokeStreakEventHandler()
        registrar.add_event_handler(DIRECT_CONVERSATION_EVENT, handler)
        registrar.add_event_handler(GROUP_CONVERSATION_EVENT, handler)


def create_plugin() -> PokeStreakPlugin:
    return PokeStreakPlugin()


__all__ = [
    "PLUGIN_ID",
    "PLUGIN_VERSION",
    "POKE_STREAK_WINDOW_SECONDS",
    "RECENT_EVENT_ID_CAPACITY",
    "PokeStreakEventHandler",
    "PokeStreakPlugin",
    "create_plugin",
]
