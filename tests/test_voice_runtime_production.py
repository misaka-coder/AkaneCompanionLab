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


async def _commit_realtime_turn(coordinator: Any) -> None:
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
    ) -> AkaneVoiceRuntimeService:
        engine = SimpleNamespace(
            memcore_manager=manager,
            _memcore_manager_if_enabled=lambda: manager,
        )
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
                asyncio.run(_commit_realtime_turn(resolved.coordinator))

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
                self.assertNotIn("event.voice.asr_checkpoint", visible_kinds)
                self.assertEqual(visible_kinds.count("message.user.voice"), 1)
                checkpoint = next(entry for entry in all_entries if entry.get("kind") == "event.voice.asr_checkpoint")
                self.assertFalse(checkpoint["prompt_visible"])
                self.assertEqual(checkpoint["retrieval_visibility"], "never")

                invalid_assistant = manager.record_voice_projection(
                    {
                        "projection_id": "assistant-invalid",
                        "target": "memcore",
                        "kind": "message.assistant.voice",
                        "source_event_id": "assistant-event-invalid",
                        "payload": {
                            "voice_turn_id": request.voice_turn_id,
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

                assistant_projection = {
                    "projection_id": "assistant-valid",
                    "target": "memcore",
                    "kind": "message.assistant.voice",
                    "source_event_id": "assistant-event-valid",
                    "payload": {
                        "voice_turn_id": request.voice_turn_id,
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

                user_entry = next(entry for entry in visible_entries if entry.kind == "message.user.voice")
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
                self.assertIn('"delivery_status":"interrupted"', projected_text)
                self.assertNotIn("event.voice.asr_checkpoint", projected_text)
                self.assertNotIn("provider_output_raw", projected_text)

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
                    self.assertEqual(len(after_replay), 3)
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


if __name__ == "__main__":
    unittest.main()
