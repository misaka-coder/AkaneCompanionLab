from __future__ import annotations

import asyncio
import importlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from companion_v01.attachment_inbox import AttachmentInboxService
from companion_v01.client_protocol import ClientMode, ClientProtocolContext
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.generated_files import GeneratedFileService
from companion_v01.instance_profile import PluginSelection
from companion_v01.memcore_integration.manager import MemcoreManager
from companion_v01.native_tool_schema import build_openai_native_tool_specs
from companion_v01.plugin_contribution_policy import TrustedReadNetworkContributionPolicy
from companion_v01.plugin_host import PluginHost
from companion_v01.plugin_managed_artifacts import GeneratedFileManagedArtifactSink
from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge
from companion_v01.store import MemoryStore
from companion_v01.tool_invocation import (
    NATIVE_OPENAI,
    TOOL_INVOCATION_ID_FIELD,
    TOOL_MODEL_NAME_FIELD,
    TOOL_SOURCE_FIELD,
)
from companion_v01.tool_runtime import ToolExecutionContext


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_ROOT = PROJECT_ROOT / "examples" / "plugins" / "akane_hacker_news_report"
PLUGIN_ID = "akane.sample.hacker-news-report"
CAPABILITY_ID = f"{PLUGIN_ID}.fetch.v1"


class _FakeLLM:
    pass


class _FakeEmbeddingProvider:
    name = "hashed"
    version = "test"
    dimension = 8

    def embed_text(self, _text: str) -> list[float]:
        return [0.0] * self.dimension


class _Headers(dict[str, str]):
    pass


class _Response:
    def __init__(self, payload: object) -> None:
        self._body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.headers = _Headers({"Content-Length": str(len(self._body))})

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, limit: int = -1) -> bytes:
        return self._body if limit < 0 else self._body[:limit]


def _story(item_id: int, *, title: str, score: int) -> dict[str, object]:
    return {
        "id": item_id,
        "type": "story",
        "by": f"author-{item_id}",
        "time": 1_777_000_000 + item_id,
        "title": title,
        "url": f"https://example.test/story/{item_id}",
        "score": score,
        "descendants": item_id,
    }


def _fake_urlopen(request: object, *, timeout: float) -> _Response:
    del timeout
    url = str(getattr(request, "full_url", ""))
    if url.endswith("/topstories.json"):
        return _Response([101, 102, 103])
    if url.endswith("/item/101.json"):
        return _Response(_story(101, title="A real &amp; useful story", score=321))
    if url.endswith("/item/102.json"):
        return _Response(_story(102, title="Second story", score=210))
    if url.endswith("/item/103.json"):
        return _Response(_story(103, title="Third story", score=109))
    raise URLError("unexpected test URL")


class InstalledHackerNewsReportSampleTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._install_temp = tempfile.TemporaryDirectory()
        temp_root = Path(cls._install_temp.name)
        build_source = temp_root / "source"
        wheelhouse = temp_root / "wheelhouse"
        cls.install_root = temp_root / "installed"
        shutil.copytree(SAMPLE_ROOT, build_source)
        wheelhouse.mkdir()
        subprocess.run(
            [
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--outdir",
                str(wheelhouse),
                str(build_source),
            ],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        wheels = tuple(wheelhouse.glob("akane_hacker_news_report-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("sample_plugin_wheel_not_built")
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-deps",
                "--target",
                str(cls.install_root),
                str(wheels[0]),
            ],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        sys.path.insert(0, str(cls.install_root))
        importlib.invalidate_caches()
        cls.plugin_module = importlib.import_module("akane_hacker_news_report")

    @classmethod
    def tearDownClass(cls) -> None:
        sys.path.remove(str(cls.install_root))
        sys.modules.pop("akane_hacker_news_report", None)
        importlib.invalidate_caches()
        cls._install_temp.cleanup()

    async def asyncSetUp(self) -> None:
        self._runtime_temp = tempfile.TemporaryDirectory()
        root = Path(self._runtime_temp.name)
        self.store = MemoryStore(root / "store")
        self.generated_service = GeneratedFileService(
            base_dir=root / "outputs",
            store=self.store,
            attachment_service=AttachmentInboxService(
                store=self.store,
                base_dir=root / "attachments",
            ),
        )
        self.host = PluginHost(
            (PluginSelection(PLUGIN_ID, True),),
            contribution_policy=TrustedReadNetworkContributionPolicy(),
        )
        self.host.bind_managed_artifact_sink(
            GeneratedFileManagedArtifactSink(self.generated_service)
        )
        self.started = await self.host.start()
        self.bridge = PluginCapabilityToolBridge(self.host, config_base_dir=root)

    async def asyncTearDown(self) -> None:
        await self.host.stop()
        self._runtime_temp.cleanup()

    async def test_real_wheel_enters_native_schema_and_creates_host_managed_report(self) -> None:
        handlers = self.bridge.build_tool_handlers(
            client_context=ClientProtocolContext(
                requested_mode=ClientMode.QQ_TEXT,
                effective_mode=ClientMode.QQ_TEXT,
            )
        )
        schemas = build_openai_native_tool_specs(handlers)

        with patch.object(self.plugin_module, "urlopen", side_effect=_fake_urlopen):
            result = await asyncio.to_thread(
                handlers[CAPABILITY_ID].execute,
                call={"type": CAPABILITY_ID, "arguments": {"feed": "top", "max_items": 2}},
                context=ToolExecutionContext(
                    profile_user_id="user-1",
                    session_id="session-1",
                    now_ts=1_777_000_000,
                    visual_payload={},
                    client_mode="qq_text",
                ),
            )

        self.assertEqual(self.started["status"], "active")
        self.assertEqual(tuple(handlers), (CAPABILITY_ID,))
        self.assertEqual(len(schemas), 1)
        parameters = schemas[0]["function"]["parameters"]
        self.assertEqual(parameters["properties"]["max_items"]["maximum"], 10)
        self.assertNotIn("url", parameters["properties"])
        self.assertIn("A real & useful story", result.followup_context)
        self.assertEqual(result.state_updates["adapter_capability_status"], "ok")
        self.assertEqual(result.state_updates["plugin_managed_artifact_count"], 1)
        event = next(item for item in result.stream_events if item["type"] == "generated_file_ready")
        self.assertEqual(event["type"], "generated_file_ready")
        self.assertTrue(event["send_to_user"])
        generated = event["generated_file"]
        self.assertNotIn("absolute_path", generated)
        resolved = self.generated_service.resolve_generated_artifact(
            profile_user_id="user-1",
            session_id="session-1",
            target=generated["generated_id"],
        )
        self.assertIsNotNone(resolved)
        assert resolved is not None
        report = Path(resolved["absolute_path"]).read_text(encoding="utf-8")
        self.assertIn("# Hacker News top report", report)
        self.assertIn("A real & useful story", report)
        self.assertIn("Second story", report)

    async def test_tool_call_and_full_result_are_reloadable_from_memcore(self) -> None:
        handlers = self.bridge.build_tool_handlers()
        model_name = build_openai_native_tool_specs(handlers)[0]["function"]["name"]
        root = Path(self._runtime_temp.name)
        manager = MemcoreManager(
            backend="memcore",
            storage_path=root / "sample-tool-memory.sqlite3",
            visible_scope="conversation",
            enable_flavor=False,
            shadow_compare=False,
            llm=_FakeLLM(),
            embedding_provider=_FakeEmbeddingProvider(),
        )
        try:
            opened = manager.begin_input_turn(
                {
                    "source_id": "desktop:hn-report-request",
                    "content": "整理两条 Hacker News 热门内容并给我报告",
                    "timestamp": 1_777_000_000,
                },
                profile_user_id="user-1",
                session_id="session-1",
                character_pack_id="reimu",
                actor_stable_id="user-1",
                actor_display_name="伙伴",
                target_actor_id="akane",
                target_actor_display_name="Akane",
            )
            self.assertTrue(opened["ok"], opened)
            turn_id = str(opened["turn_id"])
            engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
            engine.memcore_manager = manager
            engine._record_tool_result_artifacts_in_task_workspace = lambda **_kwargs: ([], "")
            engine._resolve_tool_handlers = lambda **_kwargs: handlers
            native_call = {
                "type": CAPABILITY_ID,
                "arguments": {"feed": "top", "max_items": 2},
                TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                TOOL_INVOCATION_ID_FIELD: "call_hn_report_1",
                TOOL_MODEL_NAME_FIELD: model_name,
            }
            history: list[dict[str, object]] = []
            excluded_source_ids: list[str] = []

            with patch.object(self.plugin_module, "urlopen", side_effect=_fake_urlopen):
                results, events = await asyncio.to_thread(
                    engine._execute_and_record_tool_batch,
                    tool_calls=[native_call],
                    final_output={"speech": "", "tool_call": None},
                    tool_results=[],
                    tool_events=[],
                    tool_followups=[],
                    tool_turns=[],
                    recent_raw_for_turn=[],
                    profile_user_id="user-1",
                    session_id="session-1",
                    character_pack_id="reimu",
                    now_ts=1_777_000_001,
                    current_user_source_id="desktop:hn-report-request",
                    client_context=ClientProtocolContext(
                        requested_mode=ClientMode.DESKTOP_PET,
                        effective_mode=ClientMode.DESKTOP_PET,
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
            self.assertEqual(len(excluded_source_ids), 2)
            self.assertTrue(any(event["type"] == "generated_file_ready" for event in events))
            observation_source_id = excluded_source_ids[1]
            reopened = manager.open_memory(
                profile_user_id="user-1",
                session_id="session-1",
                character_pack_id="reimu",
                arguments={"memory_id": observation_source_id, "view": "content"},
            )
            self.assertTrue(reopened["ok"], reopened)
            self.assertIn("A real & useful story", reopened["text"])
            self.assertIn("hacker-news-top-report", reopened["text"])
        finally:
            manager.close()

    async def test_partial_network_result_is_honest_and_total_failure_creates_no_artifact(self) -> None:
        handlers = self.bridge.build_tool_handlers()
        original = _fake_urlopen

        def partial(request: object, *, timeout: float) -> _Response:
            url = str(getattr(request, "full_url", ""))
            if url.endswith("/item/102.json"):
                raise URLError("temporary failure")
            return original(request, timeout=timeout)

        with patch.object(self.plugin_module, "urlopen", side_effect=partial):
            partial_result = await asyncio.to_thread(
                handlers[CAPABILITY_ID].execute,
                call={"type": CAPABILITY_ID, "arguments": {"feed": "top", "max_items": 2}},
                context=ToolExecutionContext(
                    profile_user_id="user-2",
                    session_id="session-2",
                    now_ts=1_777_000_000,
                    visual_payload={},
                    client_mode="web",
                ),
            )

        # Engine-level status means the tool completed successfully; data
        # completeness remains explicit in the structured warning/result.
        self.assertEqual(partial_result.state_updates["adapter_capability_status"], "ok")
        self.assertIn("1 个条目在读取时不可用", partial_result.followup_context)
        self.assertIn('"omitted_count": 1', partial_result.followup_context)
        self.assertEqual(partial_result.state_updates["plugin_managed_artifact_count"], 1)

        def unavailable(request: object, *, timeout: float) -> _Response:
            url = str(getattr(request, "full_url", ""))
            if url.endswith("/topstories.json"):
                return _Response([101])
            raise URLError("offline")

        with patch.object(self.plugin_module, "urlopen", side_effect=unavailable):
            failed = await asyncio.to_thread(
                handlers[CAPABILITY_ID].execute,
                call={"type": CAPABILITY_ID, "arguments": {"feed": "top", "max_items": 1}},
                context=ToolExecutionContext(
                    profile_user_id="user-3",
                    session_id="session-3",
                    now_ts=1_777_000_000,
                    visual_payload={},
                    client_mode="web",
                ),
            )

        self.assertEqual(failed.state_updates["adapter_capability_status"], "unavailable")
        self.assertIn("provider_items_unavailable", failed.followup_context)
        self.assertNotIn("plugin_managed_artifact_count", failed.state_updates)
        self.assertFalse(any(event.get("type") == "generated_file_ready" for event in failed.stream_events))


if __name__ == "__main__":
    unittest.main()
