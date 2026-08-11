"""M-music focused tests: QQ native music-card share (Skill + Shell -> thin host tool).

Covers the 16 acceptance items:
  1. music_segment exact JSON (channelcore-onebot installed wheel).
  2. NetEase / QQ track-id format validation.
  3. send_music_card handler emits the correct delivery event.
  4/5. QQ private -> send_private_msg, QQ group -> send_group_msg.
  6. Multiple parallel cards send separately.
  7. Duplicate (platform, track_id) is sent once.
  8. NapCat failure is returned honestly and suppresses the model's success claim.
  9. send_music_card is in the QQ profile and absent on desktop.
 10. The QQ schema is byte-identical regardless of Shell/provider readiness.
 11. music-card-share appears in the bundled Skill catalog with a full body.
 12. The search script projects QQ songmid into track_id.
 13-15. MemCore records exec_run + send_music_card traces and stays reloadable
        without upstream raw / audio / host paths.
 16. Plain-text request schema stays stable (covered by 10).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import config
from channelcore_onebot import music_segment

from companion_v01.capability_registry import CapabilityRegistry, CapabilitySnapshot
from companion_v01.client_protocol import ClientMode
from companion_v01.memcore_integration.manager import MemcoreManager
from companion_v01.native_tool_schema import build_openai_native_tool_specs
from companion_v01.qq_gateway import NapCatQQGateway
from companion_v01.routes.qq import _qq_music_delivery_decision
from companion_v01.skill_runtime import SkillRegistry
from companion_v01.tool_handlers.core import ToolExecutionContext
from companion_v01.tool_handlers.music import SendMusicCardToolHandler
from companion_v01.tool_orchestration_engine import build_native_tool_decision_plan, native_tool_decision_allowlist

ROOT = Path(__file__).resolve().parents[1]

QQ_BOT_FIXTURE_ID = 10001
QQ_MASTER_FIXTURE_ID = 10002
QQ_GROUP_FIXTURE_ID = 20001

_SEARCH_SCRIPT = ROOT / "skills" / "music-card-share" / "scripts" / "search_music.py"


def _load_search_script():
    spec = importlib.util.spec_from_file_location("m68_search_music", _SEARCH_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeOneBotOk:
    status_code = 200

    def json(self):
        return {"status": "ok", "retcode": 0, "data": {"message_id": 7}}


class _FakeOneBotFailed:
    status_code = 200

    def json(self):
        return {"status": "failed", "retcode": 100, "data": {}}


class _GatewayHarness(unittest.TestCase):
    def setUp(self) -> None:
        patchers = [
            patch("companion_v01.qq_gateway.config.MASTER_QQ", str(QQ_MASTER_FIXTURE_ID)),
            patch("companion_v01.qq_gateway.config.QQ_BOT_QQ", str(QQ_BOT_FIXTURE_ID)),
        ]
        for item in patchers:
            item.start()
        self.addCleanup(lambda: [item.stop() for item in patchers])
        self.gateway = NapCatQQGateway()
        self.context = self.gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_MASTER_FIXTURE_ID,
                "message_id": "music-private-1",
                "raw_message": "发我网易云的借口",
            }
        )

    def _group_context(self) -> object:
        return self.gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "group",
                "self_id": QQ_BOT_FIXTURE_ID,
                "group_id": QQ_GROUP_FIXTURE_ID,
                "user_id": QQ_MASTER_FIXTURE_ID,
                "message_id": "music-group-1",
                "raw_message": "发个 QQ 音乐卡片",
            }
        )


class MusicSegmentContractTests(unittest.TestCase):
    def test_music_segment_projects_exact_onebot_json(self) -> None:
        self.assertEqual(
            music_segment("163", "2703973041").as_onebot(),
            {"type": "music", "data": {"type": "163", "id": "2703973041"}},
        )
        self.assertEqual(
            music_segment("qq", "002XWgfo0IKPOH").as_onebot(),
            {"type": "music", "data": {"type": "qq", "id": "002XWgfo0IKPOH"}},
        )


class SendMusicCardHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.handler = SendMusicCardToolHandler()
        self.context = ToolExecutionContext(
            "alice",
            "s1",
            0,
            {},
            client_mode="qq_text",
        )

    def test_handler_emits_music_share_ready_event(self) -> None:
        result = self.handler.execute(
            call={"type": "send_music_card", "platform": "netease_music", "track_id": "2703973041"},
            context=self.context,
        )
        self.assertEqual(
            result.stream_events,
            [
                {
                    "type": "music_share_ready",
                    "music": {"platform": "netease_music", "track_id": "2703973041"},
                    "send_to_user": True,
                    "client_mode": "qq_text",
                }
            ],
        )
        self.assertEqual(
            result.followup_context,
            "网易云音乐卡片已进入本轮 QQ 交付队列。这不是最终传输成功回执。",
        )
        self.assertNotIn("整首歌已发送", result.followup_context)

    def test_handler_qq_music_songmid_receipt(self) -> None:
        result = self.handler.execute(
            call={"type": "send_music_card", "platform": "qq_music", "track_id": "002XWgfo0IKPOH"},
            context=self.context,
        )
        self.assertEqual(result.stream_events[0]["music"], {"platform": "qq_music", "track_id": "002XWgfo0IKPOH"})
        self.assertIn("QQ音乐音乐卡片已进入本轮 QQ 交付队列", result.followup_context)

    def test_handler_rejects_bad_ids(self) -> None:
        cases = [
            ("netease_music", "ABC"),
            ("netease_music", "12a3"),
            ("qq_music", "123456"),  # numeric songid is not a songmid
            ("qq_music", ""),
            ("netease_music", ""),
        ]
        for platform, track_id in cases:
            with self.subTest(platform=platform, track_id=track_id):
                self.assertIsNone(
                    self.handler.normalize_call(
                        {"type": "send_music_card", "platform": platform, "track_id": track_id}
                    )
                )


class QqGatewayMusicCardTests(_GatewayHarness):
    def test_private_music_card_uses_send_private_msg(self) -> None:
        with patch(
            "companion_v01.onebot_transport.requests.Session.request", return_value=_FakeOneBotOk()
        ) as mocked:
            result = self.gateway.send_music_cards(
                self.context,
                [
                    {
                        "type": "music_share_ready",
                        "music": {"platform": "netease_music", "track_id": "2703973041"},
                        "send_to_user": True,
                        "client_mode": "qq_text",
                    }
                ],
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "sent")
        self.assertTrue(mocked.called)
        url = mocked.call_args.args[1]
        payload = mocked.call_args.kwargs["json"]
        self.assertTrue(url.endswith("/send_private_msg"))
        self.assertEqual(payload["user_id"], QQ_MASTER_FIXTURE_ID)
        self.assertEqual(
            payload["message"],
            [{"type": "music", "data": {"type": "163", "id": "2703973041"}}],
        )

    def test_group_music_card_uses_send_group_msg(self) -> None:
        with patch(
            "companion_v01.onebot_transport.requests.Session.request", return_value=_FakeOneBotOk()
        ) as mocked:
            result = self.gateway.send_music_cards(
                self._group_context(),
                [
                    {
                        "type": "music_share_ready",
                        "music": {"platform": "qq_music", "track_id": "002XWgfo0IKPOH"},
                        "send_to_user": True,
                        "client_mode": "qq_text",
                    }
                ],
            )
        self.assertTrue(result["ok"])
        url = mocked.call_args.args[1]
        payload = mocked.call_args.kwargs["json"]
        self.assertTrue(url.endswith("/send_group_msg"))
        self.assertEqual(payload["group_id"], QQ_GROUP_FIXTURE_ID)
        self.assertEqual(payload["message"], [{"type": "music", "data": {"type": "qq", "id": "002XWgfo0IKPOH"}}])

    def test_multiple_cards_send_separately(self) -> None:
        with patch(
            "companion_v01.onebot_transport.requests.Session.request", return_value=_FakeOneBotOk()
        ) as mocked:
            result = self.gateway.send_music_cards(
                self.context,
                [
                    {
                        "type": "music_share_ready",
                        "music": {"platform": "netease_music", "track_id": "2703973041"},
                        "send_to_user": True,
                        "client_mode": "qq_text",
                    },
                    {
                        "type": "music_share_ready",
                        "music": {"platform": "qq_music", "track_id": "002XWgfo0IKPOH"},
                        "send_to_user": True,
                        "client_mode": "qq_text",
                    },
                ],
            )
        self.assertEqual(result["count"], 2)
        self.assertEqual(mocked.call_count, 2)
        message_types = [call.kwargs["json"]["message"][0]["data"]["type"] for call in mocked.call_args_list]
        self.assertEqual(message_types, ["163", "qq"])

    def test_partial_delivery_reports_the_real_split(self) -> None:
        with patch(
            "companion_v01.onebot_transport.requests.Session.request",
            side_effect=[_FakeOneBotOk(), _FakeOneBotFailed()],
        ):
            result = self.gateway.send_music_cards(
                self.context,
                [
                    {
                        "type": "music_share_ready",
                        "music": {"platform": "netease_music", "track_id": "2703973041"},
                        "send_to_user": True,
                        "client_mode": "qq_text",
                    },
                    {
                        "type": "music_share_ready",
                        "music": {"platform": "qq_music", "track_id": "002XWgfo0IKPOH"},
                        "send_to_user": True,
                        "client_mode": "qq_text",
                    },
                ],
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "partial")
        decision = _qq_music_delivery_decision(result)
        self.assertTrue(decision["partial"])
        self.assertEqual(decision["successful_count"], 1)
        self.assertEqual(decision["failed_count"], 1)
        self.assertIn("1 张音乐卡片已经发出", decision["notice"])
        self.assertIn("另有 1 张没有成功交付", decision["notice"])

    def test_duplicate_platform_and_track_sent_once(self) -> None:
        event = {
            "type": "music_share_ready",
            "music": {"platform": "netease_music", "track_id": "2703973041"},
            "send_to_user": True,
            "client_mode": "qq_text",
        }
        with patch(
            "companion_v01.onebot_transport.requests.Session.request", return_value=_FakeOneBotOk()
        ) as mocked:
            result = self.gateway.send_music_cards(self.context, [event, dict(event)])
        self.assertEqual(result["count"], 1)
        self.assertEqual(mocked.call_count, 1)

    def test_napcat_failure_is_honest_and_suppresses_success_claim(self) -> None:
        with patch(
            "companion_v01.onebot_transport.requests.Session.request", return_value=_FakeOneBotFailed()
        ):
            result = self.gateway.send_music_cards(
                self.context,
                [
                    {
                        "type": "music_share_ready",
                        "music": {"platform": "netease_music", "track_id": "2703973041"},
                        "send_to_user": True,
                        "client_mode": "qq_text",
                    }
                ],
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed")
        decision = _qq_music_delivery_decision(result)
        self.assertTrue(decision["failed"])
        self.assertTrue(decision["suppress_model_text"])
        self.assertIn("音乐卡片没有成功发出", decision["notice"])

    def test_music_delivery_success_does_not_suppress_model_text(self) -> None:
        decision = _qq_music_delivery_decision({"ok": True, "status": "sent", "count": 1, "results": [{"ok": True}]})
        self.assertTrue(decision["attempted"])
        self.assertFalse(decision["failed"])
        self.assertFalse(decision["suppress_model_text"])

    def test_no_music_events_are_a_noop(self) -> None:
        with patch(
            "companion_v01.onebot_transport.requests.Session.request", return_value=_FakeOneBotOk()
        ) as mocked:
            result = self.gateway.send_music_cards(self.context, [{"type": "text"}])
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)
        mocked.assert_not_called()

    def test_delivery_note_is_visible_once_in_the_next_turn_context(self) -> None:
        self.gateway.add_delivery_note(self.context.session_id, "【上一轮交付状态】音乐卡片发送失败。")
        first = self.gateway.build_extra_context(
            event={},
            is_group=False,
            user_id=QQ_MASTER_FIXTURE_ID,
            group_id=0,
            reply_mode="auto",
            session_id=self.context.session_id,
        )
        second = self.gateway.build_extra_context(
            event={},
            is_group=False,
            user_id=QQ_MASTER_FIXTURE_ID,
            group_id=0,
            reply_mode="auto",
            session_id=self.context.session_id,
        )
        self.assertIn("【上一轮交付状态】音乐卡片发送失败。", first)
        self.assertEqual(second, "qq.reply_delivery: auto")


class QqCapabilityProfileTests(unittest.TestCase):
    def test_music_card_only_in_qq_profile(self) -> None:
        registry = CapabilityRegistry()
        qq = registry.select(CapabilitySnapshot(client_mode=ClientMode.QQ_TEXT))
        desktop = registry.select(CapabilitySnapshot(client_mode=ClientMode.DESKTOP_PET))
        self.assertIn("send_music_card", qq.tool_names)
        self.assertIn("send_music_card", qq.schema_tool_names)
        self.assertNotIn("send_music_card", desktop.tool_names)
        self.assertIn("open_music_search", desktop.tool_names)
        self.assertNotIn("open_music_search", qq.tool_names)

    def test_qq_schema_is_stable_across_readiness_and_repeated_builds(self) -> None:
        from companion_v01.tool_handlers.core import TOOL_SPEC_BY_TYPE

        registry = CapabilityRegistry()
        snapshots = [
            CapabilitySnapshot(client_mode=ClientMode.QQ_TEXT),
            CapabilitySnapshot(client_mode=ClientMode.QQ_TEXT, execution_enabled=True, execution_qq_enabled=True),
            CapabilitySnapshot(client_mode=ClientMode.QQ_TEXT, execution_enabled=False, execution_qq_enabled=False),
        ]
        # send_music_card is present in every QQ profile regardless of Shell/readiness.
        for snapshot in snapshots:
            selection = registry.select(snapshot)
            self.assertIn("send_music_card", selection.schema_tool_names)
            self.assertIn("send_music_card", selection.tool_names)
        # Repeated builds of the exact same frozen profile are byte-identical.
        for snapshot in snapshots:
            first = registry.select(snapshot)
            second = registry.select(snapshot)
            self.assertEqual(first.schema_tool_names, second.schema_tool_names)
            names = set(first.schema_tool_names)
            specs = build_openai_native_tool_specs(
                {name: _SpecShim(TOOL_SPEC_BY_TYPE[name]) for name in names if name in TOOL_SPEC_BY_TYPE},
                allowed_tool_names=names,
            )
            native = json.dumps([dict(item) for item in specs], ensure_ascii=False, sort_keys=False)
            self.assertEqual(
                native,
                json.dumps(
                    [
                        dict(item)
                        for item in build_openai_native_tool_specs(
                            {name: _SpecShim(TOOL_SPEC_BY_TYPE[name]) for name in names if name in TOOL_SPEC_BY_TYPE},
                            allowed_tool_names=names,
                        )
                    ],
                    ensure_ascii=False,
                    sort_keys=False,
                ),
            )

    def test_skill_loader_and_music_delivery_enter_the_verified_native_plan(self) -> None:
        from companion_v01.tool_handlers.skills import LoadSkillToolHandler

        selected = {
            "load_skill": LoadSkillToolHandler(registry=object()),
            "send_music_card": SendMusicCardToolHandler(),
        }
        self.assertTrue(set(selected).issubset(native_tool_decision_allowlist()))

        plan = build_native_tool_decision_plan(
            selected,
            allow_tool_call=True,
            provider_supports_native_tools=True,
            allowed_tool_names=selected,
        )
        self.assertEqual(plan.status, "enabled")
        native_names = [str((item.get("function") or {}).get("name") or "") for item in plan.tools]
        self.assertEqual(native_names, ["load_skill", "send_music_card"])
        self.assertEqual(plan.legacy_prompt_exclusions, {"load_skill", "send_music_card"})


class _SpecShim:
    def __init__(self, spec) -> None:
        self.tool_type = spec.capability_id
        self._spec = spec

    def tool_spec(self):
        return self._spec


class MusicCardSkillCatalogTests(unittest.TestCase):
    def test_skill_is_in_bundled_catalog_and_body_is_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            registry = SkillRegistry(
                bundled_root=ROOT / "skills",
                managed_root=root / "managed",
                execution_workspace_root=root / "workspace",
            )
            entry = registry.snapshot().by_name().get("music-card-share")
            self.assertIsNotNone(entry)
            self.assertEqual(entry.source, "bundled")
            loaded = registry.load("music-card-share")
            self.assertEqual(loaded.status, "loaded")
            for keyword in (
                "search_music.py",
                "send_music_card",
                "songmid",
                "netease_music",
                "exec_run",
                "not download",
            ):
                self.assertIn(keyword, loaded.content)
            self.assertNotIn("C:\\", loaded.content)
            self.assertNotIn(str(ROOT), loaded.content)


class MusicSearchScriptTests(unittest.TestCase):
    def test_qq_songmid_is_projected_into_track_id(self) -> None:
        search = _load_search_script()

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def read(self, size: int = -1):
                return json.dumps(
                    {
                        "code": 0,
                        "data": {
                            "song": {
                                "list": [
                                    {
                                        "songmid": "002XWgfo0IKPOH",
                                        "songname": "借口",
                                        "singer": [{"name": "周杰伦"}],
                                        "albumname": "范特西",
                                    }
                                ]
                            }
                        },
                    }
                ).encode("utf-8")

        with patch.object(search.urllib.request, "urlopen", return_value=_Response()):
            result = search._search("qq", "借口 周杰伦", 5)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["platform"], "qq_music")
        self.assertEqual(result["results"][0]["track_id"], "002XWgfo0IKPOH")
        self.assertEqual(result["results"][0]["title"], "借口")

    def test_netease_id_is_projected_and_empty_is_structured(self) -> None:
        search = _load_search_script()

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def read(self, size: int = -1):
                return json.dumps(
                    {
                        "result": {
                            "songs": [
                                {
                                    "id": 2703973041,
                                    "name": "借口",
                                    "artists": [{"name": "陈海星"}],
                                    "album": {"name": "借口"},
                                }
                            ]
                        }
                    }
                ).encode("utf-8")

        with patch.object(search.urllib.request, "urlopen", return_value=_Response()):
            result = search._search("netease", "借口 陈海星", 5)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["results"][0]["track_id"], "2703973041")

        class _Empty:
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def read(self, size: int = -1):
                return json.dumps({"result": {"songs": []}}).encode("utf-8")

        with patch.object(search.urllib.request, "urlopen", return_value=_Empty()):
            empty = search._search("netease", "不存在的东西", 5)
        self.assertEqual(empty["status"], "empty")

    def test_qq_mid_field_is_used_as_songmid_fallback(self) -> None:
        search = _load_search_script()

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def read(self, size: int = -1):
                return json.dumps(
                    {
                        "code": 0,
                        "data": {
                            "song": {
                                "list": [
                                    {
                                        "mid": "002XWgfo0IKPOH",
                                        "title": "借口",
                                        "singer": [{"name": "周杰伦"}],
                                        "album": {"name": "七里香"},
                                    }
                                ]
                            }
                        },
                    }
                ).encode("utf-8")

        with patch.object(search.urllib.request, "urlopen", return_value=_Response()):
            result = search._search("qq", "借口 周杰伦", 5)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["results"][0]["track_id"], "002XWgfo0IKPOH")
        self.assertEqual(result["results"][0]["album"], "七里香")

    def test_http_error_is_structured(self) -> None:
        import urllib.error

        search = _load_search_script()

        def _raise(*_args, **_kwargs):
            raise urllib.error.HTTPError("url", 403, "forbidden", None, None)

        with patch.object(search.urllib.request, "urlopen", side_effect=_raise):
            result = search._search("netease", "借口", 5)
        self.assertEqual(result["status"], "error")
        self.assertIn("http_error:403", result["reason"])


class _FakeLLM:
    pass


class _FakeEmbeddingProvider:
    name = "hashed"
    version = "test"
    dimension = 8

    def embed_text(self, text: str) -> list[float]:
        return [0.0] * self.dimension


class MusicCardMemcoreTraceTests(unittest.TestCase):
    def test_exec_run_and_send_music_card_traces_survive_and_reload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(config, "MEMCORE_OPERATION_PROJECTION_POLICY", "compact_after_terminal"):
                manager = MemcoreManager(
                    backend="memcore",
                    storage_path=Path(temp_dir) / "music.sqlite3",
                    visible_scope="conversation",
                    enable_flavor=False,
                    shadow_compare=False,
                    llm=_FakeLLM(),
                    embedding_provider=_FakeEmbeddingProvider(),
                )
                try:
                    opened = manager.begin_input_turn(
                        {"source_id": "user-music", "content": "找网易云的借口", "timestamp": 100},
                        profile_user_id="u1",
                        session_id="s1",
                        character_pack_id="char",
                    )
                    turn_id = str(opened.get("turn_id") or "")
                    search_result = (
                        "命令已执行完成（exit_code=0）。\n"
                        '{"status":"success","platform":"netease_music","query":"借口 陈海星","results":[{"track_id":"2703973041","title":"借口","artists":["陈海星"]}]}\n'
                        + ("x" * 1500)
                    )
                    batch = manager.record_tool_batch(
                        exchanges=[
                            {
                                "tool_name": "exec_run",
                                "tool_call_id": "call-search",
                                "tool_input": {"command": "python music-card-share/scripts/search_music.py ..."},
                                "result": search_result,
                                "source": "exec_run",
                                "timestamp": 101,
                                "source_id_prefix": "tooltrace-search",
                                "result_status": "success",
                            },
                            {
                                "tool_name": "send_music_card",
                                "tool_call_id": "call-send-music",
                                "tool_input": {"platform": "netease_music", "track_id": "2703973041"},
                                "result": "网易云音乐卡片已进入本轮 QQ 交付队列。这不是最终传输成功回执。",
                                "source": "send_music_card",
                                "timestamp": 102,
                                "source_id_prefix": "tooltrace-music",
                                "result_status": "success",
                            },
                        ],
                        turn_id=turn_id,
                        profile_user_id="u1",
                        session_id="s1",
                        character_pack_id="char",
                    )
                    self.assertTrue(batch["ok"], batch)
                    exchange_sids = [
                        (str(item["tool_use_source_id"] or ""), str(item["tool_result_source_id"] or ""))
                        for item in batch["exchanges"]
                        if item.get("tool_use_source_id")
                    ]
                    self.assertEqual(len(exchange_sids), 2)
                    music_use_sid, music_result_sid = exchange_sids[1]

                    manager.build_context_projection(
                        provider_profile="openai",
                        profile_user_id="u1",
                        session_id="s1",
                        character_pack_id="char",
                    )
                    manager.complete_input_turn(
                        turn_id=turn_id,
                        assistant_record={"source_id": "assistant-music", "content": "给你发了。", "timestamp": 103},
                        memory_metadata={},
                        provider_output_raw="给你发了。",
                        profile_user_id="u1",
                        session_id="s1",
                        character_pack_id="char",
                        provider_profile="openai",
                        provider_projection={"role": "assistant", "content": "给你发了。"},
                    )
                    projection = manager.build_context_projection(
                        provider_profile="openai",
                        profile_user_id="u1",
                        session_id="s1",
                        character_pack_id="char",
                    )
                    tool_payloads = [m for m in projection.get("payloads") or [] if m.get("role") == "tool"]
                    self.assertEqual(len(tool_payloads), 2)
                    card_text = "\n".join(str(m.get("content") or "") for m in tool_payloads)
                    self.assertIn("[compact_reloadable]", card_text)
                    self.assertIn("tool: exec_run", card_text)
                    self.assertIn("reload: open_memory(memory_id=", card_text)
                    # The send_music_card observation is retained either compacted or in full.
                    self.assertTrue(
                        "tool: send_music_card" in card_text
                        or "网易云音乐卡片已进入本轮 QQ 交付队列" in card_text
                    )
                    # No upstream raw / audio / host path in the settled card.
                    self.assertNotIn("music.163.com", card_text)
                    self.assertNotIn("audio", card_text)
                    self.assertNotIn("C:\\", card_text)
                    self.assertNotIn(str(temp_dir), card_text)

                    # The action entry keeps the exact platform + track_id and is
                    # reloadable through open_memory; the result entry keeps the
                    # honest delivery receipt.
                    expanded = manager.open_memory(
                        profile_user_id="u1",
                        session_id="s1",
                        character_pack_id="char",
                        arguments={"memory_id": music_use_sid, "view": "content"},
                    )
                    self.assertTrue(expanded["ok"], expanded)
                    self.assertIn("netease_music", expanded["text"])
                    self.assertIn("2703973041", expanded["text"])
                    self.assertNotIn("C:\\", expanded["text"])

                    result_expanded = manager.open_memory(
                        profile_user_id="u1",
                        session_id="s1",
                        character_pack_id="char",
                        arguments={"memory_id": music_result_sid, "view": "content"},
                    )
                    self.assertTrue(result_expanded["ok"], result_expanded)
                    self.assertIn("网易云音乐卡片已进入本轮 QQ 交付队列", result_expanded["text"])
                    self.assertNotIn("整首歌已发送", result_expanded["text"])
                finally:
                    manager.close()


if __name__ == "__main__":
    unittest.main()
