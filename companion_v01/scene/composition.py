"""Composition root: the sole scene module allowed to receive the host runtime."""
from pathlib import Path
import asyncio

from .actions.service import RoomService
from .adapters.care import CarePort
from .repositories.room import RoomRepository
from .resources.catalog import ResourceCatalog
from .presentation.service import PresentationService


from .story.loader import StoryLoader


def compose_room(runtime, route_prefix: str) -> RoomService:
    engine = runtime.engine
    story_loader = StoryLoader(
        characters_dir=getattr(runtime.runtime_layout, "characters_dir", None),
        user_stories_dir=Path(runtime.runtime_layout.users_data_dir) / "stories",
    )
    room = RoomService(
        repository=RoomRepository(Path(runtime.runtime_layout.users_data_dir) / "scene" / "rooms.sqlite3"),
        care=CarePort(engine.get_care_module(), runtime.desktop_pet_character_resources),
        resources=ResourceCatalog(runtime.desktop_pet_character_resources,
                                  assets_root=Path(__file__).resolve().parents[2] / "web" / "assets",
                                  characters_dir=getattr(runtime.runtime_layout, "characters_dir", None),
                                  route_prefix=route_prefix),
        record_event=engine.record_plugin_timeline_event,
        story_loader=story_loader,
    )
    async def generate(payload):
        guard = runtime.public_guard.try_acquire()
        if not guard.allowed:
            raise ValueError("model_busy")
        try:
            async with runtime.turn_coordinator.hold(payload["real_user_id"], payload["user_id"], channel="scene") as token:
                payload["_turn_control_id"] = token
                worker = asyncio.create_task(asyncio.to_thread(engine.process_turn, payload))
                try:
                    return await asyncio.shield(worker)
                except asyncio.CancelledError:
                    runtime.turn_coordinator.request_stop_token(token)
                    # The worker owns the conversation until it reaches a safe
                    # cancellation boundary. Never release its lock early.
                    await worker
                    raise
        finally:
            if guard.acquired:
                runtime.public_guard.release()
    room.presentation = PresentationService(room=room, generate=generate)
    room.story.generate = generate
    return room
