from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from capcore import CapabilityIOSlot
from capcore_adapter_python import PythonCapabilityAdapter, PythonCapabilitySpec
from channelcore_onebot import build_message_action, normalize_action_response, normalize_inbound_event

from companion_v01.client_protocol import ClientMode, ClientProtocolContext
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.memcore_integration.manager import MemcoreManager
from companion_v01.native_tool_schema import build_openai_native_tool_specs
from companion_v01.qq_gateway import NapCatQQGateway
from companion_v01.tool_invocation import (
    NATIVE_OPENAI,
    TOOL_INVOCATION_ID_FIELD,
    TOOL_MODEL_NAME_FIELD,
    TOOL_SOURCE_FIELD,
)
from companion_v01.tool_runtime import AdapterCapabilityToolHandler, ToolExecutionContext


BOT_ID = "10000001"
USER_ID = "20000001"
GROUP_ID = "30000001"
LOCAL_PATH = "F:/workspace/demo project/STATE.md"


class _FakeLLM:
    pass


class _FakeEmbeddingProvider:
    name = "hashed"
    version = "test"
    dimension = 8

    def embed_text(self, _text: str) -> list[float]:
        return [0.0] * self.dimension


def _inspect_work_state(local_path: str) -> dict[str, object]:
    return {
        "local_path": local_path,
        "exists": True,
        "details": "verified-work-state\n" * 96,
    }


class ThreeCoreProductionSliceTests(unittest.TestCase):
    def test_group_native_tool_memory_and_reply_use_one_authority_chain(self) -> None:
        event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": BOT_ID,
            "user_id": USER_ID,
            "group_id": GROUP_ID,
            "message_id": "three-core-production-1",
            "sender": {"card": "伙伴", "role": "owner"},
            "message": [
                {"type": "at", "data": {"qq": BOT_ID}},
                {"type": "text", "data": {"text": " 检查工作状态"}},
            ],
        }

        with patch("companion_v01.qq_gateway.config.QQ_BOT_QQ", BOT_ID):
            gateway = NapCatQQGateway(wake_words=("Akane",))
            with patch(
                "companion_v01.qq_gateway.normalize_inbound_event",
                wraps=normalize_inbound_event,
            ) as package_inbound:
                context = gateway.build_message_context(event)
        package_inbound.assert_called_once_with(event, bot_account_id=BOT_ID, wake_words=("Akane",))
        self.assertTrue(context.should_respond)
        self.assertEqual(context.reason, "group_mention")
        self.assertEqual(context.sender_label, "伙伴")

        adapter = PythonCapabilityAdapter(
            provider_id="provider.python.three_core_production_test",
            capabilities=(
                PythonCapabilitySpec.from_callable(
                    _inspect_work_state,
                    capability_id="python.test.inspect_work_state",
                    display_name="Inspect Work State",
                    short_hint="Inspect one explicit work-state file.",
                    visible_in=("qq",),
                    prompt_exposed=True,
                    risk="low",
                    confirm="never",
                    inputs=(CapabilityIOSlot(name="local_path", kind="string", required=True),),
                ),
            ),
        )
        descriptor = asyncio.run(adapter.list_capabilities())[0]

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch("config.DATA_DIR", temp_dir),
            patch(
                "config.MEMCORE_OPERATION_PROJECTION_POLICY",
                "compact_after_terminal",
            ),
        ):
            handler = AdapterCapabilityToolHandler(
                capability_id=descriptor.id,
                adapter=adapter,
                descriptor=descriptor,
                config_base_dir=temp_dir,
            )
            native_tools = build_openai_native_tool_specs({descriptor.id: handler})
            self.assertEqual(len(native_tools), 1)
            model_name = native_tools[0]["function"]["name"]

            manager = MemcoreManager(
                backend="memcore",
                storage_path=Path(temp_dir) / "production-slice.sqlite3",
                visible_scope="conversation",
                enable_flavor=False,
                shadow_compare=False,
                llm=_FakeLLM(),
                embedding_provider=_FakeEmbeddingProvider(),
            )
            try:
                opened = manager.begin_input_turn(
                    {
                        "source_id": "qq:three-core-production-1",
                        "content": context.clean_message,
                        "timestamp": 1_777_000_000,
                    },
                    profile_user_id=f"qq_group_shared_{GROUP_ID}",
                    session_id=f"qq_group_shared_{GROUP_ID}",
                    character_pack_id="reimu",
                    actor_stable_id=USER_ID,
                    actor_display_name="伙伴",
                    target_actor_id=BOT_ID,
                    target_actor_display_name="Akane",
                )
                self.assertTrue(opened["ok"], opened)
                turn_id = str(opened["turn_id"])

                engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
                engine.memcore_manager = manager
                engine._record_tool_result_artifacts_in_task_workspace = lambda **_kwargs: ([], "")
                engine._resolve_tool_handlers = lambda **_kwargs: {descriptor.id: handler}

                def execute_tool(**kwargs):
                    call = handler.normalize_call(kwargs["tool_call"])
                    self.assertIsNotNone(call)
                    assert call is not None
                    return handler.execute(
                        call=call,
                        context=ToolExecutionContext(
                            profile_user_id=f"qq_group_shared_{GROUP_ID}",
                            session_id=f"qq_group_shared_{GROUP_ID}",
                            now_ts=1_777_000_001,
                            visual_payload={},
                            character_pack_id="reimu",
                            current_user_source_id="qq:three-core-production-1",
                            client_mode="qq",
                        ),
                    )

                engine._execute_tool_call = execute_tool
                native_call = {
                    "type": descriptor.id,
                    "arguments": {"local_path": LOCAL_PATH},
                    TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                    TOOL_INVOCATION_ID_FIELD: "call_three_core_production_1",
                    TOOL_MODEL_NAME_FIELD: model_name,
                }
                history: list[dict[str, object]] = []
                excluded_source_ids: list[str] = []
                results, _events = engine._execute_and_record_tool_batch(
                    tool_calls=[native_call],
                    final_output={"speech": "", "tool_call": None},
                    tool_results=[],
                    tool_events=[],
                    tool_followups=[],
                    tool_turns=[],
                    recent_raw_for_turn=[],
                    profile_user_id=f"qq_group_shared_{GROUP_ID}",
                    session_id=f"qq_group_shared_{GROUP_ID}",
                    character_pack_id="reimu",
                    now_ts=1_777_000_001,
                    current_user_source_id="qq:three-core-production-1",
                    client_context=ClientProtocolContext(
                        requested_mode=ClientMode.QQ_TEXT,
                        effective_mode=ClientMode.QQ_TEXT,
                    ),
                    memory_exclude_source_ids=[],
                    request_context={},
                    tool_history_turns=history,
                    prompt_exclude_source_ids=excluded_source_ids,
                    recorded_tool_call_ids=set(),
                    memcore_turn_id=turn_id,
                )
                self.assertEqual(len(results), 1)
                self.assertEqual([item["role"] for item in history], ["assistant", "tool"])
                projected_call = history[0]["tool_calls"][0]
                self.assertEqual(projected_call["id"], "call_three_core_production_1")
                self.assertEqual(projected_call["function"]["name"], model_name)
                self.assertEqual(json.loads(projected_call["function"]["arguments"]), {"local_path": LOCAL_PATH})
                self.assertIn(LOCAL_PATH, str(history[1]["content"]))
                self.assertEqual(len(excluded_source_ids), 2)

                completed = manager.complete_input_turn(
                    turn_id=turn_id,
                    assistant_record={
                        "source_id": "qq:three-core-production-1:assistant",
                        "content": "检查完成。",
                        "timestamp": 1_777_000_002,
                    },
                    memory_metadata={},
                    provider_output_raw='{"speech":"检查完成。"}',
                    profile_user_id=f"qq_group_shared_{GROUP_ID}",
                    session_id=f"qq_group_shared_{GROUP_ID}",
                    character_pack_id="reimu",
                    provider_profile=NATIVE_OPENAI,
                    provider_projection={"role": "assistant", "content": "检查完成。"},
                )
                self.assertTrue(completed["ok"], completed)
                settled = manager.build_context_projection(
                    provider_profile=NATIVE_OPENAI,
                    profile_user_id=f"qq_group_shared_{GROUP_ID}",
                    session_id=f"qq_group_shared_{GROUP_ID}",
                    character_pack_id="reimu",
                )
                tool_payload = next(payload for payload in settled["payloads"] if payload.get("role") == "tool")
                self.assertIn("[compact_reloadable]", tool_payload["content"])
                observation_source_id = excluded_source_ids[1]
                self.assertIn(f"source_id: {observation_source_id}", tool_payload["content"])
                reopened = manager.open_memory(
                    profile_user_id=f"qq_group_shared_{GROUP_ID}",
                    session_id=f"qq_group_shared_{GROUP_ID}",
                    character_pack_id="reimu",
                    arguments={"memory_id": observation_source_id, "view": "content"},
                )
                self.assertTrue(reopened["ok"], reopened)
                self.assertIn(LOCAL_PATH, reopened["text"])
                self.assertIn("verified-work-state", reopened["text"])
            finally:
                manager.close()
                asyncio.run(adapter.aclose())

        class FakeResponse:
            status_code = 200

            @staticmethod
            def json():
                return {"status": "ok", "retcode": 0, "data": {"message_id": "sent-production-1"}}

        with (
            patch("companion_v01.qq_gateway.build_message_action", wraps=build_message_action) as package_outbound,
            patch(
                "companion_v01.onebot_transport.normalize_action_response",
                wraps=normalize_action_response,
            ) as package_result,
            patch("companion_v01.onebot_transport.requests.Session.request", return_value=FakeResponse()) as request,
        ):
            sent = gateway.send_reply(context, "检查完成。")

        self.assertTrue(sent["ok"], sent)
        package_outbound.assert_called_once()
        package_result.assert_called_once()
        self.assertEqual(
            request.call_args.kwargs["json"]["message"],
            [
                {"type": "reply", "data": {"id": "three-core-production-1"}},
                {"type": "text", "data": {"text": "检查完成"}},
            ],
        )


if __name__ == "__main__":
    unittest.main()
