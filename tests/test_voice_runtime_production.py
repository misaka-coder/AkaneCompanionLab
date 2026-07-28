from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from capcore_adapter_speech import (
    ASRSessionMode,
    ASRSessionOpenResult,
    NormalizedASRSession,
)

from companion_v01.memcore_integration.manager import MemcoreManager
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.engine_services import response_builder
from companion_v01.voice_runtime import (
    AkaneVoiceRuntimeService,
    VoiceRealtimeOpenRequest,
)


class _FakeLLM:
    pass


class _FakeEmbeddingProvider:
    name = "hashed"
    version = "test"
    dimension = 8

    def embed_text(self, _text: str) -> list[float]:
        return [0.0] * self.dimension


class _ProviderSession:
    async def feed_audio(self, *, audio: bytes) -> dict[str, Any]:
        if not audio:
            raise ValueError("test audio must not be empty")
        return {
            "quality": "stable_checkpoint",
            "stable_text": "请继续讲这个方案",
            "provider_receipt_id": "checkpoint-1",
            "control_significant": True,
        }

    async def finalize(self) -> dict[str, Any]:
        return {
            "quality": "final",
            "stable_text": "请继续讲这个方案",
            "provider_receipt_id": "final-1",
        }

    async def cancel(self) -> None:
        return None


class _Adapter:
    def __init__(self) -> None:
        self.sessions: list[_ProviderSession] = []

    async def open_session(self, **_kwargs: Any) -> ASRSessionOpenResult:
        provider_session = _ProviderSession()
        self.sessions.append(provider_session)
        return ASRSessionOpenResult.succeeded(
            ASRSessionMode.STREAMING,
            NormalizedASRSession(
                provider_session=provider_session,
                mode=ASRSessionMode.STREAMING,
            ),
        )


class _ThinkingEngine:
    def __init__(self, manager: MemcoreManager, *, fail: bool = False) -> None:
        self.memcore_manager = manager
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def _memcore_manager_if_enabled(self) -> MemcoreManager:
        return self.memcore_manager

    def process_voice_turn_stream(self, **kwargs: Any):
        self.calls.append(dict(kwargs))
        if self.fail:
            raise RuntimeError("private model and path detail")
        yield {
            "type": "speech_segment",
            "index": 0,
            "text": "好，我从状态机的边界继续讲。",
        }
        yield {
            "type": "final",
            "payload": {
                "speech": "好，我从状态机的边界继续讲。",
                "memory_metadata": {
                    "memory_facets": ["knowledge"],
                    "about_roles": ["external"],
                    "topic_terms": ["状态机"],
                },
            },
        }


def _open_request(*, voice_turn_id: str = "voice-turn-1") -> VoiceRealtimeOpenRequest:
    return VoiceRealtimeOpenRequest(
        profile_user_id="profile-user",
        conversation_id="conversation-visible-id",
        session_id="session-visible-id",
        voice_turn_id=voice_turn_id,
        audio_stream_id=f"audio-{voice_turn_id}",
        disposition="message",
        language="zh",
        character_pack_id="character-pack",
        input_format="s16le",
        sample_rate=16000,
        channels=1,
    )


async def _commit_realtime_turn(coordinator: Any) -> Any:
    opened = await coordinator.open()
    if not opened.ok:
        raise AssertionError(opened)
    checkpoint = await coordinator.feed_pcm_frame(
        b"\x01\x00" * 160,
        sequence=0,
        audio_clock_ms=0,
    )
    if not checkpoint.ok or checkpoint.early_candidate is None:
        raise AssertionError(checkpoint)
    started = await coordinator.start_finalize_pcm()
    if not started.ok:
        raise AssertionError(started)
    settled = await coordinator.settle_finalize()
    if not settled.ok:
        raise AssertionError(settled)
    return settled


class VoiceRuntimeProductionTests(unittest.TestCase):
    def _manager(self, root: Path) -> MemcoreManager:
        manager = MemcoreManager(
            backend="memcore",
            storage_path=root / "memcore.sqlite3",
            visible_scope="conversation",
            enable_flavor=False,
            shadow_compare=False,
            llm=_FakeLLM(),
            embedding_provider=_FakeEmbeddingProvider(),
        )
        self.assertTrue(manager.available, manager.status())
        return manager

    @staticmethod
    def _service(
        *,
        root: Path,
        manager: MemcoreManager,
        adapter: _Adapter,
        engine: Any | None = None,
    ) -> AkaneVoiceRuntimeService:
        engine = engine or _ThinkingEngine(manager)
        return AkaneVoiceRuntimeService(
            engine=engine,
            settings=SimpleNamespace(),
            state_dir=root / "voice-state",
            instance_id="instance-private-id",
            bot_id="bot-private-id",
            default_character_pack_id="default-character",
            provider_builder=lambda _settings: SimpleNamespace(
                ready=True,
                adapter=adapter,
                provider_id="provider.asr.production-test",
            ),
        )

    def test_durable_realtime_projection_keeps_one_typed_turn_and_hidden_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._manager(root)
            first_service = self._service(root=root, manager=manager, adapter=_Adapter())
            try:
                request = _open_request()
                resolved = first_service.create_coordinator(request)
                self.assertEqual(resolved.status, "ready", resolved)
                self.assertTrue(resolved.voice_session_id.startswith("voice_session_"))
                settled = asyncio.run(_commit_realtime_turn(resolved.coordinator))
                self.assertEqual(settled.response_status, "started")
                self.assertTrue(settled.response_id.startswith("voice_response_"))
                self.assertTrue(first_service.wait_idle(timeout=5.0))

                system = manager._get_system(
                    profile_user_id=request.profile_user_id,
                    session_id=request.session_id,
                    character_pack_id=request.character_pack_id,
                )
                all_entries = system.store.get_unsummarized_messages(namespace=system.namespace)
                visible_entries = system.store.list_prompt_visible_entries(namespace=system.namespace)
                all_kinds = [str(entry.get("kind") or "") for entry in all_entries]
                visible_kinds = [entry.kind for entry in visible_entries]

                self.assertEqual(all_kinds.count("event.voice.asr_checkpoint"), 1)
                self.assertEqual(all_kinds.count("message.user.voice"), 1)
                self.assertEqual(all_kinds.count("message.assistant.voice"), 1)
                self.assertNotIn("event.voice.asr_checkpoint", visible_kinds)
                self.assertEqual(visible_kinds.count("message.user.voice"), 1)
                self.assertEqual(visible_kinds.count("message.assistant.voice"), 1)
                checkpoint = next(entry for entry in all_entries if entry.get("kind") == "event.voice.asr_checkpoint")
                self.assertFalse(checkpoint["prompt_visible"])
                self.assertEqual(checkpoint["retrieval_visibility"], "never")

                thinking_engine = first_service.engine
                self.assertIsInstance(thinking_engine, _ThinkingEngine)
                self.assertEqual(len(thinking_engine.calls), 1)
                thinking_call = thinking_engine.calls[0]
                self.assertEqual(thinking_call["message"], "请继续讲这个方案")
                self.assertTrue(str(thinking_call["source_id"]).startswith("voice-projection:"))
                self.assertEqual(
                    thinking_call["memcore_turn_id"],
                    next(entry.turn_id for entry in visible_entries if entry.kind == "message.user.voice"),
                )

                invalid_assistant = manager.record_voice_projection(
                    {
                        "projection_id": "assistant-invalid",
                        "target": "memcore",
                        "kind": "message.assistant.voice",
                        "source_event_id": "assistant-event-invalid",
                        "payload": {
                            "voice_turn_id": "voice-turn-manual",
                            "full_text": "这条不能入库。",
                            "delivered_units": "0",
                            "interrupted_units": [],
                        },
                    },
                    profile_user_id=request.profile_user_id,
                    session_id=request.session_id,
                    character_pack_id=request.character_pack_id,
                )
                self.assertFalse(invalid_assistant["ok"])
                self.assertEqual(
                    invalid_assistant["reason"],
                    "voice_assistant_projection_units_invalid",
                )

                manual_user = manager.record_voice_projection(
                    {
                        "projection_id": "user-manual",
                        "target": "memcore",
                        "kind": "message.user.voice",
                        "source_event_id": "user-event-manual",
                        "payload": {
                            "voice_turn_id": "voice-turn-manual",
                            "text": "手工幂等测试。",
                        },
                    },
                    profile_user_id=request.profile_user_id,
                    session_id=request.session_id,
                    character_pack_id=request.character_pack_id,
                )
                self.assertTrue(manual_user["ok"], manual_user)
                assistant_projection = {
                    "projection_id": "assistant-valid",
                    "target": "memcore",
                    "kind": "message.assistant.voice",
                    "source_event_id": "assistant-event-valid",
                    "payload": {
                        "voice_turn_id": "voice-turn-manual",
                        "response_id": "response-1",
                        "response_generation": 1,
                        "purpose": "content",
                        "full_text": "好，我从状态机的边界继续讲。",
                        "full_text_status": "complete",
                        "delivery_status": "interrupted",
                        "delivered_units": [0],
                        "interrupted_units": [1],
                        "memory_metadata": {
                            "memory_facets": ["knowledge"],
                            "about_roles": ["external"],
                            "topic_terms": ["状态机"],
                        },
                    },
                }
                completed = manager.record_voice_projection(
                    assistant_projection,
                    profile_user_id=request.profile_user_id,
                    session_id=request.session_id,
                    character_pack_id=request.character_pack_id,
                    timestamp=1_800_000_000,
                )
                repeated = manager.record_voice_projection(
                    assistant_projection,
                    profile_user_id=request.profile_user_id,
                    session_id=request.session_id,
                    character_pack_id=request.character_pack_id,
                    timestamp=1_900_000_000,
                )
                self.assertTrue(completed["ok"], completed)
                self.assertTrue(repeated["ok"], repeated)
                self.assertEqual(repeated["status"], "already_completed")
                conflicting_projection = {
                    **assistant_projection,
                    "payload": {
                        **assistant_projection["payload"],
                        "full_text": "同一个投影 id 不能换成另一段内容。",
                    },
                }
                conflict = manager.record_voice_projection(
                    conflicting_projection,
                    profile_user_id=request.profile_user_id,
                    session_id=request.session_id,
                    character_pack_id=request.character_pack_id,
                    timestamp=2_000_000_000,
                )
                self.assertFalse(conflict["ok"])
                self.assertEqual(
                    conflict["reason"],
                    "voice_projection_idempotency_conflict",
                )
                other_completion = manager.record_voice_projection(
                    {
                        **assistant_projection,
                        "projection_id": "assistant-other",
                        "source_event_id": "assistant-event-other",
                    },
                    profile_user_id=request.profile_user_id,
                    session_id=request.session_id,
                    character_pack_id=request.character_pack_id,
                    timestamp=2_100_000_000,
                )
                self.assertFalse(other_completion["ok"])
                self.assertEqual(
                    other_completion["reason"],
                    "voice_turn_completed_by_other_projection",
                )

                visible_entries = system.store.list_prompt_visible_entries(namespace=system.namespace)
                user_entry = next(
                    entry
                    for entry in visible_entries
                    if entry.kind == "message.user.voice"
                    and entry.payload.get("voice_turn_id") == request.voice_turn_id
                )
                turn_entries = system.store.get_turn_entries(
                    namespace=system.namespace,
                    turn_id=user_entry.turn_id,
                )
                self.assertEqual(
                    [entry.kind for entry in turn_entries],
                    ["message.user.voice", "message.assistant.voice"],
                )

                projection = manager.build_context_projection(
                    provider_profile="openai",
                    profile_user_id=request.profile_user_id,
                    session_id=request.session_id,
                    character_pack_id=request.character_pack_id,
                )
                self.assertTrue(projection["ok"], projection)
                projected_text = "\n".join(str(payload.get("content") or "") for payload in projection["payloads"])
                self.assertIn("message.user.voice", projected_text)
                self.assertIn("message.assistant.voice", projected_text)
                self.assertIn('"delivery_status":"text_only"', projected_text)
                self.assertIn('"delivery_status":"interrupted"', projected_text)
                self.assertNotIn("event.voice.asr_checkpoint", projected_text)
                self.assertNotIn("provider_output_raw", projected_text)
                projected_voice_user = next(
                    str(payload.get("content") or "")
                    for payload in projection["payloads"]
                    if payload.get("role") == "user" and "message.user.voice" in str(payload.get("content") or "")
                )
                current_voice_message = next(
                    message
                    for message in projection["messages"]
                    if user_entry.source_id in message.get("source_ids", [])
                )
                current_voice_user = response_builder._projected_current_message_text(
                    {"current_turn_messages": [current_voice_message]},
                    current_source_id=user_entry.source_id,
                )
                self.assertEqual(current_voice_user, projected_voice_user)

                first_service.close()
                second_service = self._service(root=root, manager=manager, adapter=_Adapter())
                try:
                    replayed = second_service.create_coordinator(_open_request(voice_turn_id="voice-turn-2"))
                    self.assertEqual(replayed.status, "ready", replayed)
                    self.assertIn(
                        request.voice_turn_id,
                        replayed.coordinator.bridge.host.snapshot.input_turns,
                    )
                    after_replay = system.store.get_unsummarized_messages(namespace=system.namespace)
                    self.assertEqual(len(after_replay), 5)
                finally:
                    second_service.close()
            finally:
                first_service.close()
                manager.close()

    def test_unavailable_memcore_and_provider_fail_before_opening_a_voice_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            no_memory = AkaneVoiceRuntimeService(
                engine=SimpleNamespace(memcore_manager=None),
                settings=SimpleNamespace(),
                state_dir=root / "no-memory",
                instance_id="instance",
                bot_id="bot",
                provider_builder=lambda _settings: SimpleNamespace(
                    ready=True,
                    adapter=_Adapter(),
                    provider_id="unused",
                ),
            )
            try:
                missing = no_memory.create_coordinator(_open_request())
                self.assertEqual(missing.status, "unavailable")
                self.assertEqual(missing.reason, "voice_memcore_unavailable")
                self.assertIn("没有开始", missing.safe_public_summary)
            finally:
                no_memory.close()

            manager = self._manager(root)
            with patch.object(
                manager,
                "_begin_voice_projection_turn",
                side_effect=OSError("private path and storage detail"),
            ):
                projection_failure = manager.record_voice_projection(
                    {
                        "projection_id": "projection-write-failure",
                        "target": "memcore",
                        "kind": "message.user.voice",
                        "source_event_id": "projection-write-event",
                        "payload": {
                            "voice_turn_id": "voice-turn-write-failure",
                            "text": "这条不会伪装成已写入。",
                        },
                    },
                    profile_user_id="profile-user",
                    session_id="session-visible-id",
                    character_pack_id="character-pack",
                )
            self.assertFalse(projection_failure["ok"])
            self.assertEqual(
                projection_failure["reason"],
                "voice_projection_write_failed",
            )
            self.assertNotIn("private path", str(projection_failure))

            disabled_provider = AkaneVoiceRuntimeService(
                engine=SimpleNamespace(
                    memcore_manager=manager,
                    _memcore_manager_if_enabled=lambda: manager,
                ),
                settings=SimpleNamespace(),
                state_dir=root / "disabled-provider",
                instance_id="instance",
                bot_id="bot",
                provider_builder=lambda _settings: SimpleNamespace(
                    ready=False,
                    adapter=None,
                    provider_id="",
                    status="disabled",
                    reason="fun_asr_realtime_disabled",
                ),
            )
            try:
                disabled = disabled_provider.create_coordinator(_open_request())
                self.assertEqual(disabled.status, "disabled")
                self.assertEqual(disabled.reason, "fun_asr_realtime_disabled")
                self.assertEqual(
                    disabled.safe_public_summary,
                    "实时语音识别尚未开启。",
                )
            finally:
                disabled_provider.close()

            initialization_failure = self._service(
                root=root,
                manager=manager,
                adapter=_Adapter(),
            )
            try:
                with patch(
                    "companion_v01.voice_runtime.production.SqliteVoiceRuntimeJournal",
                    side_effect=OSError("private path and storage detail"),
                ):
                    failed = initialization_failure.create_coordinator(
                        _open_request(voice_turn_id="voice-turn-storage-failure")
                    )
                self.assertEqual(failed.status, "unavailable")
                self.assertEqual(
                    failed.reason,
                    "voice_runtime_host_initialization_failed",
                )
                self.assertNotIn("private path", str(failed))
            finally:
                initialization_failure.close()
                manager.close()

    def test_thinking_failure_is_a_typed_event_instead_of_silent_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._manager(root)
            engine = _ThinkingEngine(manager, fail=True)
            service = self._service(
                root=root,
                manager=manager,
                adapter=_Adapter(),
                engine=engine,
            )
            try:
                request = _open_request(voice_turn_id="voice-turn-thinking-failure")
                resolved = service.create_coordinator(request)
                self.assertEqual(resolved.status, "ready", resolved)
                settled = asyncio.run(_commit_realtime_turn(resolved.coordinator))
                self.assertEqual(settled.response_status, "started")
                self.assertTrue(service.wait_idle(timeout=5.0))

                host_response = next(iter(resolved.coordinator.bridge.host.snapshot.responses.values()))
                self.assertEqual(host_response.state.value, "failed")
                system = manager._get_system(
                    profile_user_id=request.profile_user_id,
                    session_id=request.session_id,
                    character_pack_id=request.character_pack_id,
                )
                entries = system.store.get_unsummarized_messages(namespace=system.namespace)
                kinds = [str(entry.get("kind") or "") for entry in entries]
                self.assertEqual(kinds.count("message.user.voice"), 1)
                self.assertEqual(kinds.count("message.assistant.voice"), 0)
                self.assertEqual(kinds.count("event.voice.failure"), 1)
                failure = next(entry for entry in entries if entry.get("kind") == "event.voice.failure")
                self.assertEqual(
                    failure["payload"]["reason_code"],
                    "voice_thinking_generation_failed",
                )
                self.assertNotIn("private model", str(failure))
            finally:
                service.close()
                manager.close()

    def test_restart_recovers_a_committed_turn_before_starting_another_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._manager(root)
            first_service = self._service(
                root=root,
                manager=manager,
                adapter=_Adapter(),
            )
            request = _open_request(voice_turn_id="voice-turn-before-restart")
            try:
                first = first_service.create_coordinator(request)
                self.assertEqual(first.status, "ready", first)
                first.coordinator.response_starter = None
                settled = asyncio.run(_commit_realtime_turn(first.coordinator))
                self.assertEqual(settled.response_status, "")
                self.assertTrue(first.coordinator.bridge.host.snapshot.pending_commands)
            finally:
                first_service.close()

            second_engine = _ThinkingEngine(manager)
            second_service = self._service(
                root=root,
                manager=manager,
                adapter=_Adapter(),
                engine=second_engine,
            )
            try:
                second = second_service.create_coordinator(_open_request(voice_turn_id="voice-turn-after-restart"))
                self.assertEqual(second.status, "ready", second)
                self.assertTrue(second_service.wait_idle(timeout=5.0))
                old_response = next(
                    response
                    for response in second.coordinator.bridge.host.snapshot.responses.values()
                    if response.voice_turn_id == request.voice_turn_id
                )
                self.assertEqual(old_response.state.value, "completed")
                self.assertEqual(len(second_engine.calls), 1)

                system = manager._get_system(
                    profile_user_id=request.profile_user_id,
                    session_id=request.session_id,
                    character_pack_id=request.character_pack_id,
                )
                kinds = [
                    str(entry.get("kind") or "")
                    for entry in system.store.get_unsummarized_messages(namespace=system.namespace)
                ]
                self.assertEqual(kinds.count("message.user.voice"), 1)
                self.assertEqual(kinds.count("message.assistant.voice"), 1)
            finally:
                second_service.close()
                manager.close()

    def test_engine_voice_entry_uses_a_trusted_precommitted_turn(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        captured: dict[str, Any] = {}

        def process_turn_stream(payload: dict[str, Any], **kwargs: Any):
            captured["payload"] = dict(payload)
            captured["kwargs"] = dict(kwargs)
            return iter(({"type": "final", "payload": {"speech": "收到。"}},))

        engine.process_turn_stream = process_turn_stream  # type: ignore[method-assign]
        events = list(
            engine.process_voice_turn_stream(
                profile_user_id="profile-user",
                session_id="session-id",
                character_pack_id="character-pack",
                source_id="voice-projection:user-projection",
                memcore_turn_id="turn-precommitted",
                voice_turn_id="voice-turn-precommitted",
                message="请继续。",
                timestamp=1_800_000_000,
            )
        )

        self.assertEqual(events[0]["type"], "final")
        payload = captured["payload"]
        self.assertTrue(payload["transient_user_message"])
        self.assertTrue(payload["transient_assistant_message"])
        self.assertEqual(payload["client_mode"], "desktop_pet")
        self.assertEqual(
            captured["kwargs"]["_precommitted_memcore_turn"],
            {
                "source_id": "voice-projection:user-projection",
                "turn_id": "turn-precommitted",
                "voice_turn_id": "voice-turn-precommitted",
            },
        )


if __name__ == "__main__":
    unittest.main()
