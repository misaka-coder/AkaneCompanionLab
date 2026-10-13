from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from companion_v01 import care_shop_settings as shop
from companion_v01 import tool_orchestration_engine as orchestration
from companion_v01.capability_discovery import CAPABILITY_DISCOVERY_TOOL_IDS
from companion_v01.channels.qq.cards.commerce import build_qq_commerce_card, render_qq_commerce_card
from companion_v01.channels.qq.context import QQMessageContext
from companion_v01.channels.qq.gateway import NapCatQQGateway
from companion_v01.channels.qq.turns.streaming import _process_qq_turn_streaming
from companion_v01.client_protocol import ClientMode, ClientProtocolContext
from companion_v01.desktop_pet_character_resources import (
    DesktopPetCharacterResourceService,
    load_character_care_shop_items,
)
from companion_v01.dialogue_targets import DialogueRegistry, DialogueTarget
from companion_v01.engine import PERSONA, AkaneMemoryEngine
from companion_v01.engine_services.response_builder import prepare_context
from companion_v01.memcore_integration.manager import MemcoreManager
from companion_v01.memory_read_policy import MemoryReadPolicy
from companion_v01.plugin_conversation_refs import PluginConversationReferenceAuthority
from companion_v01.prompt_builder import PromptBuilder
from companion_v01.prompt_profiles import PromptProfileRegistry
from companion_v01.tool_handlers.character_shop import CharacterShopToolHandler
from companion_v01.tool_handlers.core import BaseToolHandler, ToolExecutionContext
from companion_v01.tool_invocation import TOOL_CAPABILITY_SELECTION_FIELD
from tests.test_capability_adapter_mcp_orchestration import build_engine
from tests.test_memcore_integration import _FakeEmbeddingProvider, _FakeLLM, _PromptContextEngine
from tests.test_plugin_events import _Response


def make_resources(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    for pack_id, name in (("cecilia", "塞西莉亚"), ("reimu", "灵梦")):
        pack = root / pack_id
        pack.mkdir()
        (pack / "character.json").write_text(
            json.dumps(
                {
                    "identity": {"name": name},
                    "care": {
                        "enabled": True,
                        "shop_items": [
                            {
                                "id": "tea",
                                "name": "茶",
                                "price": 7,
                                "effects": {"hunger": 2, "energy": 4, "affection": 1},
                            }
                        ],
                    },
                }
            ),
            encoding="utf-8",
        )
    return DesktopPetCharacterResourceService(characters_dir=root)


def qq_protocol():
    return ClientProtocolContext(requested_mode=ClientMode.QQ_TEXT, effective_mode=ClientMode.QQ_TEXT)


class _TimelineHandler(BaseToolHandler):
    tool_type = "read_memory_timeline"


class ServiceCharacterShopTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(patch("config.DATA_DIR", str(self.root / "users")))
        self.resources = make_resources(self.root / "characters")
        self.engine = build_engine(self.root / "users")
        self.engine.base_dir = self.root / "users"
        self.engine.desktop_pet_character_resources = self.resources
        self.engine.dialogue_registry = DialogueRegistry(self.resources)
        self.manager = MemcoreManager(
            backend="memcore",
            storage_path=self.root / "memory.db",
            visible_scope="conversation",
            enable_flavor=True,
            shadow_compare=False,
            llm=_FakeLLM(),
            embedding_provider=_FakeEmbeddingProvider(),
        )
        self.addCleanup(self.manager.close)
        self.engine.memcore_manager = self.manager
        self.authority = PluginConversationReferenceAuthority(self.root / "ref.key", instance_id="fixture")
        self.engine.memory_read_policy = MemoryReadPolicy(
            store=self.manager._store,
            bot_id="10001",
            master_qq="10002",
            enabled=lambda: True,
            resolve_origin=self.authority.resolve,
            key=b"k" * 32,
        )
        self.reader = CharacterShopToolHandler(self.engine)
        self.writer = CharacterShopToolHandler(self.engine, write=True)
        self.engine.tool_handlers.update(
            read_character_shop=self.reader, edit_character_shop=self.writer, read_memory_timeline=_TimelineHandler()
        )
        self.target = DialogueTarget("service", "shopkeeper", "cecilia")
        self.context = self.context_for(self.target)

    def context_for(self, target, *, user_id=10002, group=False):
        profile = "qq_group_shared_42" if group else "master" if user_id == 10002 else f"qq_{user_id}"
        session = "qq_group_shared_42" if group else "master" if user_id == 10002 else f"qq_pri_{user_id}"
        reference = self.authority.issue_qq(
            profile_user_id=profile,
            session_id=session,
            character_pack_id=target.memory_domain,
            user_id=user_id,
            group_id=42 if group else 0,
            actor_stable_id=f"qq:{user_id}",
        )
        return ToolExecutionContext(
            profile_user_id=profile,
            session_id=session,
            now_ts=1790790000,
            visual_payload={},
            character_pack_id=target.memory_domain,
            current_user_source_id="input-1",
            client_mode="qq_text",
            invocation_id="call-1",
            dialogue_target=target,
            request_context={"_memory_qq_ref": reference, "_host_dialogue_target": target},
        )

    def test_reader_and_writer_metadata_project_their_own_specs(self):
        for handler, operation in ((self.reader, "read"), (self.writer, "write")):
            with self.subTest(operation=operation):
                metadata = handler.tool_metadata()
                spec = handler.tool_spec()
                self.assertEqual(metadata.family, "character_shop")
                self.assertEqual(metadata.operation, operation)
                self.assertEqual(metadata.input_schema, spec.input_schema)
                self.assertEqual(metadata.risk, spec.risk)
                self.assertEqual(metadata.requires_confirmation, spec.confirm != "never")

    def call(self, **values):
        return {
            "type": "edit_character_shop",
            "action": "create",
            "revision": shop.read_shop(self.resources, "cecilia")["revision"],
            "name": "草莓蛋糕",
            "description": "新鲜草莓和轻盈奶油。",
            "price": 12,
            "effects": {"hunger": 22, "energy": 6, "affection": 6},
            **values,
        }

    def execute(self, call, context=None):
        return json.loads(self.writer.execute(call=call, context=context or self.context).followup_context)

    def surface(self, pack_id="cecilia", *, session="master", profile="master"):
        result = self.manager.build_context_surface(
            provider_profile="openai", profile_user_id=profile, session_id=session, character_pack_id=pack_id
        )
        self.assertTrue(result["ok"], result)
        return result["history_messages"]

    def test_real_crud_appends_only_actual_changes_to_primary_history(self):
        scope = dict(profile_user_id="master", session_id="master", character_pack_id="cecilia")
        self.manager.append_standalone_message(
            {"source_id": "old-chat", "content": "我们聊过蛋糕。", "timestamp": 1790789999}, role="user", **scope
        )
        before = self.surface()
        created = self.execute(self.call())
        self.assertTrue(created["ok"], created)
        self.assertEqual(created["event_notification"], {"ok": True, "status": "recorded"})
        after = self.surface()
        self.assertEqual(after[: len(before)], before)
        self.assertIn("草莓蛋糕", str(after[len(before) :]))
        self.assertIn("22", str(after[len(before) :]))
        self.assertEqual(self.surface("reimu"), [])
        self.assertEqual(self.surface(self.target.memory_domain), [])
        item_id = created["item_id"]
        update = {
            "type": "edit_character_shop",
            "action": "update",
            "revision": created["revision"],
            "item_id": item_id,
            "description": "酸甜的草莓搭配清淡奶油。",
        }
        changed = self.execute(update)
        self.assertTrue(changed["ok"], changed)
        item = next(i for i in changed["items"] if i["id"] == item_id)
        self.assertEqual((item["price"], item["effects"]["hunger"]), (12, 22))
        self.assertIn("酸甜", str(self.surface()[-1]))
        unchanged = self.execute({**update, "revision": changed["revision"]})
        self.assertEqual(unchanged["event_notification"]["status"], "not_needed")
        self.assertEqual(len(self.surface()), len(after) + 1)
        deleted = self.execute(
            {"type": "edit_character_shop", "action": "delete", "item_id": item_id, "revision": unchanged["revision"]}
        )
        self.assertTrue(deleted["ok"], deleted)
        self.assertFalse(any(i["id"] == item_id for i in deleted["items"]))
        self.assertIn("下架", str(self.surface()[-1]))
        self.assertEqual(len(self.surface()), len(after) + 2)

    def test_create_replay_deduplicates_item_and_event_even_after_restart(self):
        call = self.call()
        first = self.execute(call)
        before = self.surface()
        self.engine.memcore_manager.close()
        self.engine.memcore_manager = self.manager = MemcoreManager(
            backend="memcore",
            storage_path=self.root / "memory.db",
            visible_scope="conversation",
            enable_flavor=True,
            shadow_compare=False,
            llm=_FakeLLM(),
            embedding_provider=_FakeEmbeddingProvider(),
        )
        self.addCleanup(self.manager.close)
        second = self.execute(call)
        self.assertEqual(second["status"], "already_created")
        self.assertEqual(second["item_id"], first["item_id"])
        self.assertEqual(second["event_notification"]["status"], "recorded")
        self.assertEqual(self.surface(), before)
        self.assertEqual(len(shop.read_shop(self.resources, "cecilia")["items"]), 2)

    def test_revision_conflict_cancellation_and_noncustom_item_have_no_event(self):
        call = self.call(revision="stale")
        self.assertEqual(self.execute(call)["status"], "conflict")
        self.assertEqual(
            self.execute(self.call(), replace(self.context, cancel_requested=lambda: True))["status"], "cancelled"
        )
        result = self.execute(
            {
                "type": "edit_character_shop",
                "action": "delete",
                "item_id": "tea",
                "revision": shop.read_shop(self.resources, "cecilia")["revision"],
            }
        )
        self.assertEqual(result["reason"], "shop_custom_item_required")
        self.assertEqual(self.surface(), [])
        self.assertEqual(len(shop.read_shop(self.resources, "cecilia")["items"]), 1)

    def test_origin_not_shared_group_profile_decides_write_permission(self):
        call = self.call()
        nonowner = self.context_for(self.target, user_id=10003, group=True)
        owner = self.context_for(self.target, group=True)
        self.assertEqual(nonowner.profile_user_id, owner.profile_user_id)
        self.assertEqual(self.execute(call, nonowner)["reason"], "shop_owner_required")
        self.assertEqual(
            self.execute(call, replace(owner, request_context={"user_id": "10002"}))["reason"],
            "shop_verified_qq_origin_required",
        )
        result = self.execute(call, owner)
        self.assertTrue(result["ok"], result)
        self.assertIn("草莓", str(self.surface(session=owner.session_id, profile=owner.profile_user_id)))
        self.assertEqual(self.surface(), [])

    def test_served_character_and_context_cannot_be_overridden_by_arguments(self):
        self.assertIsNone(self.writer.normalize_call(self.call(service_target="reimu")))
        self.assertEqual(
            self.execute(self.call(), replace(self.context, dialogue_target=None))["reason"],
            "shop_service_context_required",
        )
        self.assertEqual(
            self.execute(self.call(), replace(self.context, character_pack_id="cecilia"))["reason"],
            "shop_service_context_required",
        )
        self.assertEqual(
            self.execute(self.call(), replace(self.context, current_user_source_id=""))["reason"],
            "shop_operation_identity_required",
        )
        self.assertEqual(self.surface(), [])

    def test_memory_failure_reports_committed_shop_edit_honestly(self):
        with (
            self.assertLogs("companion_v01.tool_handlers.character_shop", level="WARNING"),
            patch.object(self.manager, "append_standalone_event", side_effect=RuntimeError("private-path")),
        ):
            result = self.execute(self.call())
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["event_notification"], {"ok": False, "status": "failed"})
        self.assertNotIn("private-path", str(result))
        self.assertEqual(len(shop.read_shop(self.resources, "cecilia")["items"]), 2)

    def test_partial_effect_update_and_description_clear_preserve_other_fields(self):
        created = self.execute(self.call())
        path = self.root / "characters" / "cecilia" / "character.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        item = next(i for i in data["care"]["shop_items"] if i["id"] == created["item_id"])
        item["effects"]["care_pause_seconds"] = 123
        path.write_text(json.dumps(data), encoding="utf-8")
        current = shop.read_shop(self.resources, "cecilia")
        result = self.execute(
            {
                "type": "edit_character_shop",
                "action": "update",
                "revision": current["revision"],
                "item_id": created["item_id"],
                "description": "",
                "effects": {"energy": 9},
            }
        )
        self.assertTrue(result["ok"], result)
        updated = next(i for i in result["items"] if i["id"] == created["item_id"])
        self.assertEqual(updated["description"], "")
        self.assertEqual(updated["price"], 12)
        self.assertEqual(updated["effects"]["hunger"], 22)
        self.assertEqual(updated["effects"]["energy"], 9)
        raw = json.loads(path.read_text(encoding="utf-8"))
        item = next(i for i in raw["care"]["shop_items"] if i["id"] == created["item_id"])
        self.assertEqual(item["effects"]["care_pause_seconds"], 123)
        self.assertIn("介绍:无", str(self.surface()[-1]))

    def test_scope_tools_use_normal_orchestration_and_discovery_cannot_expand_them(self):
        selection = self.engine._resolve_capability_selection(
            client_context=qq_protocol(),
            profile_user_id="master",
            session_id="master",
            character_pack_id=self.target.memory_domain,
        )
        allowed = set(self.engine.dialogue_registry.services["shopkeeper"].tools) | CAPABILITY_DISCOVERY_TOOL_IDS
        self.assertTrue(set(selection.tool_names) <= allowed, selection.tool_names)
        self.assertTrue({"read_character_shop", "edit_character_shop"} <= set(selection.schema_tool_names))
        self.assertNotIn("web_search", selection.capability_catalog.capability_ids)
        self.assertNotIn("web_search", selection.capability_catalog.current().capability_ids)
        call = {**self.call(), TOOL_CAPABILITY_SELECTION_FIELD: selection}
        result = orchestration.execute_tool_call(
            self.engine,
            tool_call=call,
            profile_user_id="master",
            session_id="master",
            visual_payload={},
            now_ts=self.context.now_ts,
            client_context=qq_protocol(),
            character_pack_id=self.target.memory_domain,
            current_user_source_id="input-1",
            request_context=self.context.request_context,
        )
        self.assertTrue(json.loads(result.followup_context)["ok"], result.followup_context)
        regular = self.engine._resolve_capability_selection(
            client_context=qq_protocol(), profile_user_id="master", session_id="master", character_pack_id="cecilia"
        )
        self.assertFalse({"read_character_shop", "edit_character_shop"} & set(regular.tool_names))
        self.assertNotIn("edit_character_shop", regular.capability_catalog.capability_ids)

    def test_shop_description_edit_updates_real_card_cache_and_desktop_metadata(self):
        first = self.execute(self.call())

        def card():
            payload = {
                "kind": "shop",
                "coins": 40,
                "redeem_quota": 20,
                "page": 1,
                "items": load_character_care_shop_items(self.resources, "cecilia"),
            }
            return build_qq_commerce_card(
                payload,
                user_label="用户",
                character_label="塞西莉亚",
                user_scope="fixture",
                character_pack_id="cecilia",
            )

        original = card()
        path = render_qq_commerce_card(original, self.root / "cards")
        result = self.execute(
            {
                "type": "edit_character_shop",
                "action": "update",
                "item_id": first["item_id"],
                "revision": first["revision"],
                "description": "酸甜的草莓搭配清淡奶油。",
            }
        )
        self.assertTrue(result["ok"], result)
        updated = card()
        self.assertNotEqual(original, updated)
        self.assertIn("酸甜", updated.as_text())
        self.assertNotEqual(path, render_qq_commerce_card(updated, self.root / "cards"))
        self.assertEqual(
            next(i for i in shop.read_shop(self.resources, "cecilia")["items"] if i["id"] == first["item_id"])[
                "description"
            ],
            "酸甜的草莓搭配清淡奶油。",
        )

    def test_real_prompt_builder_keeps_service_persona_and_append_only_cache_prefix(self):
        path = self.resources.characters_dir / "cecilia" / "character.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["dialogue_services"] = {"shopkeeper": {"name": "花店姐姐", "persona": "说话温柔，爱给甜点取可爱的名字。"}}
        path.write_text(json.dumps(data), encoding="utf-8")
        engine = _PromptContextEngine(memcore_manager=self.manager)
        engine.desktop_pet_character_resources = self.resources
        engine.dialogue_registry = self.engine.dialogue_registry
        engine._resolve_client_protocol_context = lambda _: qq_protocol()
        engine._get_prompt_profile_registry = lambda: PromptProfileRegistry()
        engine._resolve_capability_selection = self.engine._resolve_capability_selection
        engine._build_desktop_pet_character_pack_prompt_context = (
            AkaneMemoryEngine._build_desktop_pet_character_pack_prompt_context.__get__(engine)
        )
        engine.prompt_builder = PromptBuilder(PERSONA)
        scope = dict(profile_user_id="master", session_id="master", character_pack_id=self.target.memory_domain)
        self.manager.append_standalone_message(
            {"source_id": "service-old", "content": "我想买蛋糕。", "timestamp": 1790789999}, role="user", **scope
        )

        def prepare():
            return prepare_context(
                engine,
                user_message="继续看看",
                recent_raw=[],
                recent_episodic_summaries=[],
                recent_semantic_summaries=[],
                confirmed_snippets=[],
                now_ts=1790790000,
                current_input_transient=True,
                allow_tool_call=False,
                **scope,
            )

        with patch("config.MEMORY_BACKEND", "memcore"), patch("config.LLM_AUTO_COMPACT_TOKEN_LIMIT", 0):
            before = prepare()
            self.manager.append_standalone_message(
                {"source_id": "service-next", "content": "再加一点草莓。", "timestamp": 1790790000},
                role="user",
                **scope,
            )
            after = prepare()
        self.assertIn("你是花店姐姐", str(before))
        self.assertIn("修改前先查看商店", str(before))
        self.assertIn("爱给甜点取可爱的名字", str(before))
        self.assertEqual(before["fallback"]["persona"]["active"], "shopkeeper")
        self.assertEqual(before["system_prompt"], after["system_prompt"])
        self.assertEqual(before["native_tools"], after["native_tools"])
        self.assertEqual(before["history_turns"], after["history_turns"][: len(before["history_turns"])])
        self.assertEqual(
            AkaneMemoryEngine._final_prompt_cache_key(before), AkaneMemoryEngine._final_prompt_cache_key(after)
        )
        compact_persona = self.engine._build_memory_compaction_persona_context(**scope)
        self.assertIn("你是花店姐姐", compact_persona["system_context"])
        self.assertEqual(compact_persona["active_id"], "shopkeeper")

    def test_pack_service_customization_is_scoped_hot_read_and_cannot_grant_tools(self):
        path = self.resources.characters_dir / "cecilia" / "character.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["dialogue_services"] = {"shopkeeper": {"name": "花店姐姐", "persona": "喜欢温柔地解释商品。"}}
        path.write_text(json.dumps(data), encoding="utf-8")
        service = self.engine.dialogue_registry.service_persona(self.target)
        self.assertEqual(service.name, "花店姐姐")
        self.assertEqual(service.tools, self.engine.dialogue_registry.services["shopkeeper"].tools)
        self.assertEqual(service.command, "售货员")
        other = DialogueTarget("service", "shopkeeper", "reimu")
        self.assertEqual(self.engine.dialogue_registry.service_persona(other).name, "售货员")
        identity = self.engine._resolve_turn_speaker_identity(qq_protocol(), self.target.memory_domain)
        self.assertEqual(identity["assistant_name"], "花店姐姐")
        for invalid in ({"name": ""}, {"tools": ["web_search"]}, {"command": "姐姐"}, {"persona": "长" * 2001}):
            data["dialogue_services"]["shopkeeper"] = invalid
            path.write_text(json.dumps(data), encoding="utf-8")
            parsed = self.engine.dialogue_registry.parse("/售货员 你好", default_pack="cecilia")
            self.assertEqual(parsed.error, "service_customization_invalid", invalid)
            self.assertEqual(self.engine.dialogue_registry.parse("/reimu 你好", default_pack="cecilia").error, "")
        data.pop("dialogue_services")
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(self.engine.dialogue_registry.service_persona(self.target).name, "售货员")

    def test_preferences_share_real_persona_and_compaction_path_without_service_inheritance(self):
        path = self.resources.characters_dir / "cecilia" / "character.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["care"]["care_preferences"] = {
            "favorite_items": ["草莓蛋糕", "天妇罗乌冬面"], "disliked_items": ["苦咖啡"],
            "favorite_tags": ["sweet"], "disliked_tags": ["spicy"],
        }
        path.write_text(json.dumps(data), encoding="utf-8")
        before = self.engine._build_desktop_pet_character_pack_prompt_context(character_pack_id="cecilia", client_mode="qq_text")
        desktop = self.engine._build_desktop_pet_character_pack_prompt_context(character_pack_id="cecilia", client_mode="desktop_pet")
        for context in (before, desktop, self.engine._build_memory_compaction_persona_context(
            profile_user_id="master", session_id="master", character_pack_id="cecilia",
        )):
            self.assertIn("喜欢的食物或礼物：草莓蛋糕、天妇罗乌冬面。", context["system_context"])
            self.assertIn("不喜欢的食物或礼物：苦咖啡。", context["system_context"])
            self.assertIn("喜欢的类型：甜食。", context["system_context"])
        helper = self.engine.dialogue_registry.persona_context(self.target.memory_domain)
        self.assertNotIn("草莓蛋糕", helper["system_context"])
        other = self.engine._build_desktop_pet_character_pack_prompt_context(character_pack_id="reimu", client_mode="qq_text")
        self.assertNotIn("草莓蛋糕", other["system_context"])
        self.assertEqual(before, self.engine._build_desktop_pet_character_pack_prompt_context(character_pack_id="cecilia", client_mode="qq_text"))
        self.assertEqual(shop.read_shop(self.resources, "cecilia")["items"][0]["effects"]["affection"], 1)

    def test_success_confirmation_uses_real_gateway_and_stream_final_duplicates_send_once(self):
        result = self.writer.execute(call=self.call(), context=self.context)
        feedback = json.loads(result.followup_context)
        self.assertEqual(feedback["confirmation_card"], {"ok": True, "status": "ready"})
        self.assertNotIn("absolute_path", result.followup_context)
        self.assertTrue(Path(result.stream_events[0]["file"]["absolute_path"]).is_file())
        self.assertEqual(self.writer.execute(call=self.call(revision="stale"), context=self.context).stream_events, [])
        processed = []

        def stream(payload):
            processed.append(payload)
            yield from result.stream_events
            yield {"type": "final_ui", "payload": {"status": "ok", "speech": "已经上架啦。",
                "speech_segments": ["已经上架啦。"], "tool_events": result.stream_events,
                "reply_medium": "text", "_emotion_model_authored": False}}

        self.engine.process_turn_stream = stream
        self.engine._get_generated_file_service = lambda: None
        gateway = NapCatQQGateway(state_path=self.root / "qq-state.json")
        gateway.character_pack_overrides["master"] = "reimu"
        context = QQMessageContext(True, "qq_directed_dialogue", target_id=10002, user_id=10002,
            profile_user_id="master", session_id="master", character_pack_id=self.target.memory_domain,
            dialogue_target=self.target, reply_mode="text")
        with patch("companion_v01.channels.qq.onebot.transport.requests.Session.request", return_value=_Response()) as network:
            delivered = _process_qq_turn_streaming(engine=self.engine, qq_gateway=gateway, context=context,
                turn_payload={"message": "上架蛋糕"}, config_module=SimpleNamespace(QQ_STREAM_REPLIES_ENABLED=False))
        self.assertEqual(len(processed), 1)
        self.assertEqual(processed[0]["character_pack_id"], self.target.memory_domain)
        self.assertTrue(delivered["file_send_result"]["ok"], delivered)
        self.assertEqual(delivered["file_send_result"]["count"], 1)
        images = [call for call in network.call_args_list if '"type": "image"' in json.dumps(call.kwargs)]
        self.assertEqual(len(images), 1)

    def test_confirmation_failure_does_not_undo_committed_item_or_event(self):
        with patch("companion_v01.tool_handlers.character_shop.render_qq_shop_change_card", side_effect=OSError("private-path")):
            result = self.writer.execute(call=self.call(), context=self.context)
        feedback = json.loads(result.followup_context)
        self.assertTrue(feedback["ok"], feedback)
        self.assertEqual(feedback["event_notification"]["status"], "recorded")
        self.assertEqual(feedback["confirmation_card"]["reason"], "shop_card_unavailable")
        self.assertNotIn("private-path", result.followup_context)
        self.assertEqual(result.stream_events[0]["type"], "file_unavailable")
        self.assertNotIn("absolute_path", result.stream_events[0]["file"])
        self.assertEqual(len(shop.read_shop(self.resources, "cecilia")["items"]), 2)

    def test_service_display_rename_does_not_replay_primary_event_or_card(self):
        call = self.call()
        first = self.execute(call)
        before = self.surface()
        path = self.resources.characters_dir / "cecilia" / "character.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["dialogue_services"] = {"shopkeeper": {"name": "售货员姐姐"}}
        path.write_text(json.dumps(data), encoding="utf-8")
        replayed = self.writer.execute(call=call, context=self.context)
        feedback = json.loads(replayed.followup_context)
        self.assertEqual(feedback["status"], "already_created")
        self.assertEqual(feedback["event_notification"]["status"], "recorded")
        self.assertEqual(feedback["item_id"], first["item_id"])
        self.assertEqual(replayed.stream_events, [])
        self.assertEqual(self.surface(), before)

    def test_update_delete_and_noop_confirmation_follow_actual_shop_snapshot(self):
        created = self.execute(self.call())
        call = {"type": "edit_character_shop", "action": "update", "item_id": created["item_id"],
                "revision": created["revision"], "description": "改为清淡奶油。", "effects": {"energy": 9}}
        from companion_v01.channels.qq.cards.commerce import render_qq_shop_change_card
        with patch("companion_v01.tool_handlers.character_shop.render_qq_shop_change_card", wraps=render_qq_shop_change_card) as render:
            changed = self.writer.execute(call=call, context=self.context)
            doc = render.call_args.args[0]
            self.assertEqual(doc.action, "update")
            self.assertEqual(doc.item["effects"]["energy"], 9)
            self.assertEqual(doc.item["description"], "改为清淡奶油。")
            revision = json.loads(changed.followup_context)["revision"]
            noop = self.writer.execute(call={**call, "revision": revision}, context=self.context)
            self.assertEqual(noop.stream_events, [])
            self.assertEqual(render.call_count, 1)
            removed = self.writer.execute(call={"type": "edit_character_shop", "action": "delete",
                "item_id": created["item_id"], "revision": revision}, context=self.context)
            self.assertEqual(len(removed.stream_events), 1)
            doc = render.call_args.args[0]
            self.assertEqual(doc.action, "delete")
            self.assertEqual(doc.item["description"], "改为清淡奶油。")
            self.assertEqual(doc.item["effects"]["energy"], 9)

    def test_failed_confirmation_transport_sends_real_text_without_workspace_claim(self):
        self.check_confirmation_fallback()

    def test_unavailable_local_card_sends_text_without_attempting_image_transport(self):
        self.check_confirmation_fallback(render_failed=True)

    def check_confirmation_fallback(self, *, render_failed=False):
        with patch("companion_v01.tool_handlers.character_shop.render_qq_shop_change_card", side_effect=OSError("render_failed")) if render_failed else self.subTest():
            result = self.writer.execute(call=self.call(), context=self.context)
        self.engine._get_generated_file_service = lambda: None
        gateway = NapCatQQGateway(state_path=self.root / "qq-state.json")
        context = QQMessageContext(True, "qq_directed_dialogue", target_id=10002, user_id=10002,
            profile_user_id="master", session_id="master", character_pack_id=self.target.memory_domain,
            dialogue_target=self.target, reply_mode="text")
        with patch.object(gateway, "send_image", return_value={"ok": False, "reason": "image_send_failed"}) as image_send, \
             patch("companion_v01.channels.qq.onebot.transport.requests.Session.request", return_value=_Response()) as network:
            delivered = _process_qq_turn_streaming(engine=self.engine, qq_gateway=gateway, context=context,
                turn_payload={}, config_module=SimpleNamespace(), prepared_frame={"status": "ok", "speech": "",
                    "tool_events": result.stream_events, "_emotion_model_authored": False})
        self.assertFalse(delivered["file_send_result"]["ok"])
        self.assertEqual(delivered["file_delivery_feedback_result"]["status"], "text_fallback_sent")
        text = str(network.call_args_list)
        self.assertIn("塞西莉亚的商店", text)
        self.assertIn("草莓蛋糕", text)
        self.assertIn("QQ 12 额度", text)
        self.assertNotIn("工作台", text)
        if render_failed:
            image_send.assert_not_called()
        sends = [call for call in network.call_args_list if call.args[1].endswith("/send_private_msg")]
        self.assertEqual(len(sends), 1, sends)


if __name__ == "__main__":
    unittest.main()
