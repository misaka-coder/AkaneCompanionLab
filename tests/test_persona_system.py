from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from companion_v01.engine import AkaneMemoryEngine
from companion_v01.persona_system import PersonaCardService
from companion_v01.store import MemoryStore
from companion_v01.tool_runtime import ManagePersonaToolHandler, ToolExecutionContext


class PersonaCardServiceTests(unittest.TestCase):
    def test_create_card_defaults_to_active_and_prompt_includes_exit_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            service = PersonaCardService(store=store)

            card = service.create_card(
                profile_user_id="user-1",
                session_id="session-1",
                name="猫娘",
                summary="更亲昵、更轻快，偶尔带一点喵感。",
                speech_style="语气更软。",
                interaction_bias="更主动贴近。",
                resource_preference="偏好温暖轻快的 BGM。",
                switch_hint="适合轻松玩闹。",
                unsuitable_contexts="不适合严肃或悲伤话题。",
                reason="当前氛围更亲昵。",
                timestamp=100,
                source_id="msg-1",
            )
            context = service.build_prompt_context(
                profile_user_id="user-1",
                session_id="session-1",
            )

            self.assertEqual(card["status"], "active")
            self.assertEqual(context["active_id"], card["card_id"])
            self.assertIn("你现在的模样", context["system_context"])
            self.assertIn("关于你的情绪与姿态", context["system_context"])
            self.assertIn("不必刻意停在原来的模样", context["system_context"])
            self.assertIn("主动迈出一小步", context["system_context"])
            self.assertIn("正在变得清晰", context["system_context"])
            self.assertIn("不适合严肃或悲伤话题", context["system_context"])
            self.assertIn("你熟悉的其他模样", context["reference_context"])

    def test_create_second_card_deactivates_previous_and_update_only_touches_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            service = PersonaCardService(store=store)

            first = service.create_card(
                profile_user_id="user-1",
                session_id="session-1",
                name="猫娘",
                summary="更亲昵。",
                timestamp=100,
            )
            second = service.create_card(
                profile_user_id="user-1",
                session_id="session-1",
                name="共创伙伴",
                summary="更适合讨论代码和架构。",
                timestamp=120,
            )
            updated = service.update_active_card(
                profile_user_id="user-1",
                session_id="session-1",
                fields={"speech_style": "更清晰、更协作。"},
                reason="讨论项目时更自然。",
                timestamp=130,
            )

            reloaded_first = store.get_persona_card(
                profile_user_id="user-1",
                session_id="session-1",
                card_id=first["card_id"],
            )
            self.assertEqual(reloaded_first["status"], "inactive")
            self.assertEqual(updated["card_id"], second["card_id"])
            self.assertEqual(updated["speech_style"], "更清晰、更协作。")

    def test_final_persona_request_switches_existing_card_and_ignores_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            service = PersonaCardService(store=store)
            cat = service.create_card(
                profile_user_id="user-1",
                session_id="session-1",
                name="猫娘",
                summary="更亲昵。",
                timestamp=100,
            )
            quiet = service.create_card(
                profile_user_id="user-1",
                session_id="session-1",
                name="安静陪伴",
                summary="更轻声、更慢。",
                timestamp=110,
            )

            switched = service.apply_final_persona_request(
                profile_user_id="user-1",
                session_id="session-1",
                requested_active=cat["card_id"],
                request_present=True,
                timestamp=120,
                source_id="msg-switch",
            )
            ignored = service.apply_final_persona_request(
                profile_user_id="user-1",
                session_id="session-1",
                requested_active="missing_card",
                request_present=True,
                timestamp=130,
            )

            self.assertEqual(quiet["status"], "active")
            self.assertEqual(switched["active_id"], cat["card_id"])
            self.assertTrue(switched["changed"])
            self.assertEqual(ignored["active_id"], cat["card_id"])
            self.assertTrue(ignored["ignored"])

    def test_persona_cards_are_profile_shared_but_active_state_is_session_local(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            service = PersonaCardService(store=store)

            cat = service.create_card(
                profile_user_id="master",
                session_id="web-session",
                name="猫娘",
                summary="更亲昵、更轻快。",
                timestamp=100,
            )
            other_context = service.build_prompt_context(
                profile_user_id="master",
                session_id="qq-private",
            )

            self.assertEqual(other_context["active_id"], "")
            self.assertIn("猫娘", other_context["reference_context"])

            switched = service.apply_final_persona_request(
                profile_user_id="master",
                session_id="qq-private",
                requested_active=cat["card_id"],
                request_present=True,
                timestamp=120,
            )

            self.assertEqual(switched["active_id"], cat["card_id"])
            self.assertEqual(
                store.get_active_persona_card(profile_user_id="master", session_id="web-session")["card_id"],
                cat["card_id"],
            )
            self.assertEqual(
                store.get_active_persona_card(profile_user_id="master", session_id="qq-private")["card_id"],
                cat["card_id"],
            )

            service.apply_final_persona_request(
                profile_user_id="master",
                session_id="qq-private",
                requested_active="",
                request_present=True,
                timestamp=130,
            )

            self.assertEqual(
                store.get_active_persona_card(profile_user_id="master", session_id="web-session")["card_id"],
                cat["card_id"],
            )
            self.assertIsNone(store.get_active_persona_card(profile_user_id="master", session_id="qq-private"))


class ManagePersonaToolHandlerTests(unittest.TestCase):
    def test_manage_persona_create_is_silent_state_update(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            service = PersonaCardService(store=store)
            handler = ManagePersonaToolHandler(persona_service=service)

            result = handler.execute(
                call={
                    "type": "manage_persona",
                    "action": "create",
                    "name": "猫娘",
                    "summary": "更亲昵、更轻快。",
                    "unsuitable_contexts": "严肃话题。",
                },
                context=ToolExecutionContext(
                    profile_user_id="user-1",
                    session_id="session-1",
                    now_ts=100,
                    visual_payload={},
                    current_user_source_id="msg-1",
                ),
            )
            active = store.get_active_persona_card(profile_user_id="user-1", session_id="session-1")

            self.assertEqual(result.tool_type, "manage_persona")
            self.assertEqual(result.stream_events[0]["type"], "persona_state")
            self.assertTrue(result.stream_events[0]["silent"])
            self.assertTrue(result.state_updates["persona_state_changed"])
            self.assertIsNotNone(active)
            self.assertEqual(active["name"], "猫娘")


class EnginePersonaStateTests(unittest.TestCase):
    def test_apply_persona_state_to_final_output_sets_actual_active_card(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
            engine.store = MemoryStore(Path(temp_dir))
            engine.persona_card_service = PersonaCardService(store=engine.store)
            card = engine.persona_card_service.create_card(
                profile_user_id="user-1",
                session_id="session-1",
                name="猫娘",
                summary="更亲昵。",
                timestamp=100,
            )
            engine.persona_card_service.create_card(
                profile_user_id="user-1",
                session_id="session-1",
                name="安静陪伴",
                summary="更安静。",
                timestamp=110,
            )

            final = engine._apply_persona_state_to_final_output(
                profile_user_id="user-1",
                session_id="session-1",
                final_output={
                    "speech": "嗯。",
                    "persona": {"active": card["card_id"]},
                    "_persona_request": {"present": True, "active": card["card_id"]},
                },
                now_ts=120,
                source_id="msg-2",
            )

            self.assertEqual(final["persona"]["active"], card["card_id"])
            self.assertNotIn("_persona_request", final)


if __name__ == "__main__":
    unittest.main()
