from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import config
from companion_v01.capability_registry import CapabilityRegistry, CapabilitySnapshot
from companion_v01.client_protocol import ClientMode, ClientProtocolContext, QQ_TEXT_DEFAULT_CAPABILITIES
from companion_v01.domain_profiles import (
    DEFAULT_DOMAIN_PROFILE_ID,
    FINANCE_DOMAIN_PROFILE_ID,
    FINANCE_PROMPT_BLOCK,
    DomainProfileRegistry,
    build_domain_profile_prompt,
)
from companion_v01.persona_config import load_persona_config
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.prompt_builder import PromptBuilder
from companion_v01.prompt_profiles import PromptModule, PromptProfileRegistry
from companion_v01.qq_gateway import NapCatQQGateway


class FinanceDomainProfileTests(unittest.TestCase):
    def _build_prompt(self, *, domain_profile_context: str) -> dict:
        profile = PromptProfileRegistry().get(ClientMode.QQ_TEXT)
        return PromptBuilder(load_persona_config()).build_final_generation_context(
            now_ts=1_712_400_000,
            raw_text="User: 贵州茅台现在怎么看？",
            current_message_text="User: 贵州茅台现在怎么看？",
            episodic_summary_text="",
            semantic_summary_text="",
            memory_text="",
            current_visual_context="(QQ 文字端无完整演出状态)",
            resource_context="QQ 端不渲染立绘。",
            extra_context="【QQ 客户端上下文】",
            visual_defaults={
                "major": "default",
                "minor": "default",
                "background": "evening_classroom",
                "bgm": "",
                "outfit": "default",
                "emotion": "normal",
            },
            allow_tool_call=True,
            tool_prompt_context="- web_search",
            debug_enabled=False,
            persona_system_context="当前角色身份：测试角色；继续使用她原本的称呼和说话风格。",
            domain_profile_context=domain_profile_context,
            system_prompt_override=profile.system_prompt_override,
            mode_prompt_override=profile.mode_prompt_override(debug_enabled=False),
        )

    def test_off_mode_has_no_finance_prompt_block(self) -> None:
        registry = DomainProfileRegistry(finance_enabled=True)
        profile = registry.resolve(profile_id=FINANCE_DOMAIN_PROFILE_ID, finance_mode="off")
        result = self._build_prompt(domain_profile_context=build_domain_profile_prompt(profile))

        self.assertEqual(profile.id, DEFAULT_DOMAIN_PROFILE_ID)
        self.assertNotIn("金融领域档案 finance_v1", result["system_prompt"])
        self.assertNotIn(FINANCE_PROMPT_BLOCK, result["system_extra_blocks"])

    def test_qa_and_push_share_stable_finance_prompt_without_replacing_persona(self) -> None:
        registry = DomainProfileRegistry(finance_enabled=True, finance_push_enabled=True)
        qa_profile = registry.resolve(profile_id=FINANCE_DOMAIN_PROFILE_ID, finance_mode="qa")
        push_profile = registry.resolve(profile_id=FINANCE_DOMAIN_PROFILE_ID, finance_mode="push")
        qa_block = build_domain_profile_prompt(qa_profile)
        push_block = build_domain_profile_prompt(push_profile)
        result = self._build_prompt(domain_profile_context=qa_block)

        self.assertEqual(qa_profile.id, FINANCE_DOMAIN_PROFILE_ID)
        self.assertEqual(push_profile.id, FINANCE_DOMAIN_PROFILE_ID)
        self.assertEqual(qa_block, push_block)
        self.assertIn("当前模式：qq_text", result["system_prompt"])
        self.assertIn("当前角色身份：测试角色", result["system_prompt"])
        self.assertEqual(result["system_extra_blocks"][0], FINANCE_PROMPT_BLOCK)
        self.assertIn("不要自称另一个金融机器人", result["system_extra_blocks"][0])

    def test_finance_profile_filters_unrelated_qq_tools(self) -> None:
        profile = DomainProfileRegistry(finance_enabled=True).get(FINANCE_DOMAIN_PROFILE_ID)
        selection = CapabilityRegistry().select(
            CapabilitySnapshot(client_mode=ClientMode.QQ_TEXT),
            allowed_tool_names=profile.allowed_tool_names,
            hidden_tool_names=profile.hidden_tool_names,
        )

        self.assertIn("retrieve_memory", selection.tool_names)
        self.assertIn("read_memory_timeline", selection.tool_names)
        self.assertIn("web_search", selection.tool_names)
        self.assertIn("compose_file", selection.tool_names)
        self.assertNotIn("send_sticker", selection.tool_names)
        self.assertNotIn("fetch_media_from_url", selection.tool_names)
        self.assertNotIn("manage_persona", selection.tool_names)
        self.assertNotIn("convert_media_file", selection.tool_names)

    def test_qq_prompt_profile_declares_domain_profile_module(self) -> None:
        qq_profile = PromptProfileRegistry().get(ClientMode.QQ_TEXT)
        self.assertTrue(qq_profile.includes(PromptModule.DOMAIN_PROFILE))

    def test_engine_prompt_and_dispatch_share_finance_tool_filter(self) -> None:
        class _Handler:
            def __init__(self, tool_type: str) -> None:
                self.tool_type = tool_type

            def build_prompt_instruction(self) -> str:
                return f"TOOL::{self.tool_type}"

            def normalize_call(self, value):
                return dict(value) if str((value or {}).get("type") or "") == self.tool_type else None

        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.capability_registry = CapabilityRegistry()
        engine.tool_handlers = {
            name: _Handler(name)
            for name in (
                "retrieve_memory",
                "read_memory_timeline",
                "web_search",
                "compose_file",
                "send_sticker",
                "fetch_media_from_url",
            )
        }
        client_context = ClientProtocolContext(
            requested_mode=ClientMode.QQ_TEXT,
            effective_mode=ClientMode.QQ_TEXT,
            capabilities=QQ_TEXT_DEFAULT_CAPABILITIES,
        )

        with patch.object(config, "FINANCE_ASSISTANT_ENABLED", True, create=True):
            prompt = engine._build_tool_prompt_context(
                allow_tool_call=True,
                client_context=client_context,
                domain_profile_id=FINANCE_DOMAIN_PROFILE_ID,
            )
            allowed = engine._normalize_tool_call(
                {"type": "web_search", "query": "贵州茅台 最新公告"},
                client_context=client_context,
                domain_profile_id=FINANCE_DOMAIN_PROFILE_ID,
            )
            rejected = engine._normalize_tool_call(
                {"type": "send_sticker"},
                client_context=client_context,
                domain_profile_id=FINANCE_DOMAIN_PROFILE_ID,
            )

        self.assertIn("TOOL::web_search", prompt)
        self.assertIn("TOOL::compose_file", prompt)
        self.assertNotIn("TOOL::send_sticker", prompt)
        self.assertNotIn("TOOL::fetch_media_from_url", prompt)
        self.assertIsNotNone(allowed)
        self.assertIsNone(rejected)


class QQFinanceModeTests(unittest.TestCase):
    @staticmethod
    def _private_event(*, message_id: str, raw_message: str) -> dict:
        return {
            "post_type": "message",
            "message_type": "private",
            "self_id": 90001,
            "user_id": 10001,
            "message_id": message_id,
            "raw_message": raw_message,
            "sender": {"nickname": "张三"},
        }

    @staticmethod
    def _group_event(*, message_id: str, raw_message: str, role: str) -> dict:
        return {
            "post_type": "message",
            "message_type": "group",
            "self_id": 90001,
            "user_id": 10001,
            "group_id": 20001,
            "message_id": message_id,
            "raw_message": raw_message,
            "sender": {"nickname": "张三", "role": role},
        }

    def test_qa_command_persists_and_enriches_next_turn_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            config,
            "FINANCE_ASSISTANT_ENABLED",
            True,
            create=True,
        ), patch.object(config, "FINANCE_DEFAULT_MODE", "off", create=True), patch.object(
            config,
            "QQ_FINANCE_MODE_COMMANDS_ENABLED",
            True,
            create=True,
        ), patch.object(config, "QQ_FINANCE_PUSH_ENABLED", False, create=True):
            state_path = Path(temp_dir) / "qq_gateway_state.json"
            gateway = NapCatQQGateway(state_path=state_path)
            command_context = gateway.build_message_context(
                self._private_event(message_id="finance-qa-1", raw_message="开启金融模式")
            )

            result = gateway.handle_finance_mode_command(command_context)

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["finance_mode"], "qa")
            next_context = gateway.build_message_context(
                self._private_event(message_id="finance-qa-2", raw_message="贵州茅台现在怎么看？")
            )
            payload = next_context.to_turn_payload()
            self.assertEqual(payload["client_mode"], "qq_text")
            self.assertEqual(payload["finance_mode"], "qa")
            self.assertEqual(payload["domain_profile"], FINANCE_DOMAIN_PROFILE_ID)

            restored = NapCatQQGateway(state_path=state_path)
            self.assertEqual(restored.resolve_finance_mode(next_context.session_id), "qa")

    def test_group_push_requires_admin_or_owner(self) -> None:
        with patch.object(config, "FINANCE_ASSISTANT_ENABLED", True, create=True), patch.object(
            config,
            "QQ_FINANCE_MODE_COMMANDS_ENABLED",
            True,
            create=True,
        ), patch.object(config, "QQ_FINANCE_PUSH_ENABLED", True, create=True), patch.object(
            config,
            "MASTER_QQ",
            "",
        ):
            member_gateway = NapCatQQGateway()
            member_event = self._group_event(
                message_id="finance-push-member",
                raw_message="Akane 开启财经推送",
                role="member",
            )
            member_context = member_gateway.build_message_context(member_event)
            forbidden = member_gateway.handle_finance_mode_command(member_context, event=member_event)

            admin_gateway = NapCatQQGateway()
            admin_event = self._group_event(
                message_id="finance-push-admin",
                raw_message="Akane 开启财经推送",
                role="admin",
            )
            admin_context = admin_gateway.build_message_context(admin_event)
            allowed = admin_gateway.handle_finance_mode_command(admin_context, event=admin_event)

            self.assertEqual(forbidden["status"], "forbidden")
            self.assertEqual(member_gateway.resolve_finance_mode(member_context.session_id), "off")
            self.assertTrue(allowed["ok"], allowed)
            self.assertEqual(allowed["finance_mode"], "push")
            self.assertEqual(admin_gateway.resolve_finance_mode(admin_context.session_id), "push")


if __name__ == "__main__":
    unittest.main()
