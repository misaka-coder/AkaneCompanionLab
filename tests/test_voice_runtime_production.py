from __future__ import annotations

import asyncio
import shutil
import tempfile
import threading
import time
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
from voicecore import snapshot_to_dict
from voicecore.testing import EventFactory

from companion_v01.memcore_integration.manager import MemcoreManager
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.engine_services import response_builder
from companion_v01.voice_runtime import (
    AkaneVoiceRuntimeService,
    VOICE_PLAYBACK_OUTPUT_MODE,
    VoiceRealtimeOpenRequest,
)


class _FakeLLM:
    pass


class _SemanticLLM:
    def __init__(self, *, error: str = "") -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    @staticmethod
    def chat_provider_protocol() -> str:
        return "openai"

    def call_chat_json_result(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        return SimpleNamespace(
            parsed={
                "playback_action": "resume",
                "input_action": "treat_as_interaction",
                "response_action": "none",
                "reason_summary": "用户是在补充，不需要抢走当前回复。",
                "confidence_hint": 0.86,
            },
            fallback_used=False,
            error=self.error,
        )


class _CandidateSemanticLLM(_SemanticLLM):
    def call_chat_json_result(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        system_prompt = str(kwargs.get("system_prompt") or "")
        if "候选回复校验器" in system_prompt:
            parsed = {
                "compatible": True,
                "reason_code": "same_request_with_more_detail",
            }
        else:
            parsed = {
                "playback_action": "stop_now",
                "input_action": "take_over",
                "response_action": "prepare_candidate",
                "reason_summary": "用户正在纠正并接管当前回复。",
                "confidence_hint": 0.92,
            }
        return SimpleNamespace(
            parsed=parsed,
            fallback_used=False,
            error=self.error,
        )


class _FakeEmbeddingProvider:
    name = "hashed"
    version = "test"
    dimension = 8

    def embed_text(self, _text: str) -> list[float]:
        return [0.0] * self.dimension


class _ProviderSession:
    def __init__(self) -> None:
        self.cancelled = False

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
        self.cancelled = True
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


class _CallProviderSession:
    def __init__(self) -> None:
        self.turn_index = 0
        self.commit_count = 0
        self.finish_count = 0
        self.cancel_count = 0
        self.finalize_count = 0

    async def feed_audio(self, *, audio: bytes) -> dict[str, Any]:
        if not audio:
            raise ValueError("test audio must not be empty")
        return {
            "quality": "stable_checkpoint",
            "stable_text": f"第{self.turn_index + 1}轮语音",
            "provider_receipt_id": f"checkpoint-{self.turn_index + 1}",
            "control_significant": True,
        }

    async def commit_turn(self) -> dict[str, Any]:
        self.turn_index += 1
        self.commit_count += 1
        return {
            "quality": "final",
            "stable_text": f"第{self.turn_index}轮语音",
            "provider_receipt_id": f"final-{self.turn_index}",
        }

    async def finish_call(self) -> None:
        self.finish_count += 1

    async def finalize(self) -> dict[str, Any]:
        self.finalize_count += 1
        raise AssertionError("call-scoped ASR must not finalize per input turn")

    async def cancel(self) -> None:
        self.cancel_count += 1


class _CallAdapter:
    def __init__(self) -> None:
        self.open_count = 0
        self.provider_session = _CallProviderSession()

    async def open_session(self, **_kwargs: Any) -> ASRSessionOpenResult:
        self.open_count += 1
        return ASRSessionOpenResult.succeeded(
            ASRSessionMode.STREAMING,
            NormalizedASRSession(
                provider_session=self.provider_session,
                mode=ASRSessionMode.STREAMING,
            ),
        )


class _LegacyNormalizedSession:
    """Shape shipped by the pre call-scoped speech adapter."""

    mode = ASRSessionMode.STREAMING

    def __init__(self) -> None:
        self.cancel_count = 0

    async def cancel(self) -> None:
        self.cancel_count += 1


class _LegacyCallAdapter:
    def __init__(self) -> None:
        self.session = _LegacyNormalizedSession()

    async def open_session(self, **_kwargs: Any) -> ASRSessionOpenResult:
        return ASRSessionOpenResult(
            status="succeeded",
            mode=ASRSessionMode.STREAMING,
            session=self.session,
        )


class _ThinkingEngine:
    def __init__(self, manager: MemcoreManager, *, fail: bool = False) -> None:
        self.memcore_manager = manager
        self.fail = fail
        self.calls: list[dict[str, Any]] = []
        self.candidate_calls: list[dict[str, Any]] = []

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

    def process_voice_candidate_stream(self, **kwargs: Any):
        self.candidate_calls.append(dict(kwargs))
        if self.fail:
            raise RuntimeError("private speculative model detail")
        yield {
            "type": "speech_segment",
            "index": 0,
            "text": "好，我按你刚才的纠正重新说明。",
        }
        yield {
            "type": "final",
            "payload": {
                "speech": "好，我按你刚才的纠正重新说明。",
                "memory_metadata": {
                    "memory_facets": ["knowledge"],
                    "topic_terms": ["候选回复"],
                },
            },
        }


class _SemanticThinkingEngine(_ThinkingEngine):
    def __init__(self, manager: MemcoreManager, *, llm: _SemanticLLM) -> None:
        super().__init__(manager)
        self.llm = llm


class _BlockingThinkingEngine(_ThinkingEngine):
    def __init__(self, manager: MemcoreManager) -> None:
        super().__init__(manager)
        self.segment_ready = threading.Event()
        self.release_final = threading.Event()

    def process_voice_turn_stream(self, **kwargs: Any):
        self.calls.append(dict(kwargs))
        self.segment_ready.set()
        yield {
            "type": "speech_segment",
            "index": 0,
            "text": "第一句现在就可以开始合成。",
        }
        if not self.release_final.wait(timeout=5.0):
            raise RuntimeError("test final release timeout")
        yield {
            "type": "final",
            "payload": {
                "speech": "第一句现在就可以开始合成。",
                "memory_metadata": {"topic_terms": ["实时语音"]},
            },
        }


class _MultiSegmentThinkingEngine(_ThinkingEngine):
    def process_voice_turn_stream(self, **kwargs: Any):
        self.calls.append(dict(kwargs))
        for index, text in enumerate(("第一句先播放。", "第二句随后播放。")):
            yield {"type": "speech_segment", "index": index, "text": text}
        yield {
            "type": "final",
            "payload": {
                "speech": "第一句先播放。第二句随后播放。",
                "memory_metadata": {"topic_terms": ["顺序播放"]},
            },
        }


class _TTSClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def synthesize(self, text: str) -> bytes:
        self.calls.append(text)
        return b"ID3-production-tts-audio"


def _open_request(
    *,
    voice_turn_id: str = "voice-turn-1",
    output_mode: str = "text_only",
) -> VoiceRealtimeOpenRequest:
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
        output_mode=output_mode,
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
    def test_engine_candidate_boundary_is_transient_and_tool_free(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        captured: list[dict[str, Any]] = []

        def process_turn_stream(payload: dict[str, Any]):
            captured.append(dict(payload))
            return iter(({"type": "final", "payload": {"speech": "候选"}},))

        engine.process_turn_stream = process_turn_stream
        events = list(
            engine.process_voice_candidate_stream(
                profile_user_id="profile-user",
                session_id="session-visible-id",
                character_pack_id="character-pack",
                voice_turn_id="voice-turn-candidate-boundary",
                message="我先说到这里",
                timestamp=123,
            )
        )

        self.assertEqual(events[0]["payload"]["speech"], "候选")
        self.assertEqual(len(captured), 1)
        self.assertTrue(captured[0]["transient_user_message"])
        self.assertTrue(captured[0]["transient_assistant_message"])
        self.assertTrue(captured[0]["voice_speculative_candidate"])
        self.assertEqual(captured[0]["client_mode"], "desktop_pet")

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
        adapter: Any,
        engine: Any | None = None,
        tts_client: Any = None,
        tts_client_resolver: Any = None,
    ) -> AkaneVoiceRuntimeService:
        engine = engine or _ThinkingEngine(manager)
        return AkaneVoiceRuntimeService(
            engine=engine,
            settings=SimpleNamespace(),
            state_dir=root / "voice-state",
            instance_id="instance-private-id",
            bot_id="bot-private-id",
            default_character_pack_id="default-character",
            tts_client=tts_client,
            tts_client_resolver=tts_client_resolver,
            provider_builder=lambda _settings: SimpleNamespace(
                ready=True,
                adapter=adapter,
                provider_id="provider.asr.production-test",
            ),
        )

    def test_call_scoped_asr_reuses_one_provider_session_across_two_turns(self) -> None:
        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                manager = self._manager(root)
                adapter = _CallAdapter()
                engine = _ThinkingEngine(manager)
                service = self._service(
                    root=root,
                    manager=manager,
                    adapter=adapter,
                    engine=engine,
                )
                try:
                    first_request = _open_request(
                        voice_turn_id="voice-turn-call-1",
                    )
                    opened = await service.open_call(first_request)
                    self.assertTrue(opened.ready, opened)
                    assert opened.call is not None

                    first = opened.call.create_turn(first_request)
                    self.assertTrue(first.ready, first)
                    overlapping = opened.call.create_turn(_open_request(voice_turn_id="voice-turn-call-overlap"))
                    self.assertEqual(overlapping.status, "conflict")
                    self.assertEqual(
                        overlapping.reason,
                        "voice_realtime_call_input_turn_active",
                    )
                    first_settled = await _commit_realtime_turn(first.coordinator)
                    self.assertEqual(first_settled.response_status, "started")
                    self.assertTrue(service.wait_idle(timeout=5.0))
                    self.assertIn(first_request.voice_turn_id, opened.call._turn_coordinators)
                    opened.call.release_response(first_request.voice_turn_id)
                    self.assertNotIn(first_request.voice_turn_id, opened.call._turn_coordinators)
                    reused = opened.call.create_turn(first_request)
                    self.assertEqual(reused.status, "conflict")
                    self.assertEqual(
                        reused.reason,
                        "voice_realtime_call_turn_identity_reused",
                    )

                    second_request = _open_request(
                        voice_turn_id="voice-turn-call-2",
                    )
                    second = opened.call.create_turn(second_request)
                    self.assertTrue(second.ready, second)
                    second_settled = await _commit_realtime_turn(second.coordinator)
                    self.assertEqual(second_settled.response_status, "started")
                    self.assertTrue(service.wait_idle(timeout=5.0))

                    finished = await opened.call.finish()
                    self.assertTrue(finished.ok, finished)
                    self.assertTrue(opened.call.closed)
                    self.assertEqual(adapter.open_count, 1)
                    self.assertEqual(adapter.provider_session.commit_count, 2)
                    self.assertEqual(adapter.provider_session.finish_count, 1)
                    self.assertEqual(adapter.provider_session.finalize_count, 0)
                    self.assertEqual(adapter.provider_session.cancel_count, 0)
                    self.assertEqual(first.voice_session_id, second.voice_session_id)
                    self.assertEqual(first.voice_session_id, opened.call.voice_session_id)
                    self.assertEqual(len(engine.calls), 2)
                    self.assertEqual(
                        [call["message"] for call in engine.calls],
                        ["第1轮语音", "第2轮语音"],
                    )
                    after_finish = opened.call.create_turn(_open_request(voice_turn_id="voice-turn-call-after-finish"))
                    self.assertEqual(after_finish.status, "unavailable")
                    self.assertEqual(
                        after_finish.reason,
                        "voice_realtime_call_closed",
                    )
                finally:
                    service.close()
                    manager.close()

        asyncio.run(exercise())

    def test_call_scoped_asr_rejects_legacy_session_without_capability_attribute(self) -> None:
        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                manager = self._manager(root)
                adapter = _LegacyCallAdapter()
                service = self._service(
                    root=root,
                    manager=manager,
                    adapter=adapter,
                )
                try:
                    opened = await service.open_call(
                        _open_request(voice_turn_id="voice-turn-legacy-call")
                    )
                    self.assertFalse(opened.ready)
                    self.assertEqual(opened.status, "unsupported")
                    self.assertEqual(
                        opened.reason,
                        "asr_provider_commit_turn_unsupported",
                    )
                    self.assertEqual(adapter.session.cancel_count, 1)
                finally:
                    service.close()
                    manager.close()

        asyncio.run(exercise())

    def test_call_scoped_asr_rejects_a_provider_that_only_finalizes_sessions(self) -> None:
        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                manager = self._manager(root)
                adapter = _Adapter()
                service = self._service(
                    root=root,
                    manager=manager,
                    adapter=adapter,
                )
                try:
                    opened = await service.open_call(_open_request(voice_turn_id="voice-turn-unsupported-call"))
                    self.assertFalse(opened.ready)
                    self.assertEqual(opened.status, "unsupported")
                    self.assertEqual(
                        opened.reason,
                        "asr_provider_commit_turn_unsupported",
                    )
                    self.assertEqual(len(adapter.sessions), 1)
                    self.assertTrue(adapter.sessions[0].cancelled)
                finally:
                    service.close()
                    manager.close()

        asyncio.run(exercise())

    def test_playback_uses_character_resolved_tts_instead_of_global_edge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._manager(root)
            edge = _TTSClient()
            character_tts = _TTSClient()
            character_tts.provider_id = "provider.tts.gpt_sovits.local"
            resolver_calls: list[dict[str, str]] = []

            def resolve_tts(**identity: str) -> _TTSClient:
                resolver_calls.append(dict(identity))
                return character_tts

            service = self._service(
                root=root,
                manager=manager,
                adapter=_Adapter(),
                tts_client=edge,
                tts_client_resolver=resolve_tts,
            )
            try:
                request = _open_request(
                    voice_turn_id="voice-turn-character-tts",
                    output_mode=VOICE_PLAYBACK_OUTPUT_MODE,
                )
                resolved = service.create_coordinator(request)
                self.assertEqual(resolved.status, "ready", resolved)
                settled = asyncio.run(_commit_realtime_turn(resolved.coordinator))
                self.assertEqual(settled.response_status, "started")
                self.assertTrue(service.wait_idle(timeout=3.0))
                self.assertEqual(edge.calls, [])
                self.assertEqual(
                    character_tts.calls,
                    ["好，我从状态机的边界继续讲。"],
                )
                self.assertEqual(
                    resolver_calls,
                    [
                        {
                            "profile_user_id": request.profile_user_id,
                            "session_id": request.session_id,
                            "character_pack_id": request.character_pack_id,
                        }
                    ],
                )
                loaded = resolved.coordinator.bridge.host._host.journal.load_events()
                self.assertTrue(loaded.ok, loaded)
                ready = next(event for event in loaded.events if event.event_kind == "voice.tts.ready")
                self.assertEqual(
                    ready.payload["provider_id"],
                    "provider.tts.gpt_sovits.local",
                )
            finally:
                service.close()
                manager.close()

    def test_generation_cancel_command_stops_old_stream_without_assistant_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._manager(root)
            engine = _BlockingThinkingEngine(manager)
            service = self._service(
                root=root,
                manager=manager,
                adapter=_Adapter(),
                engine=engine,
            )
            try:
                request = _open_request(voice_turn_id="voice-turn-cancel-generation")
                resolved = service.create_coordinator(request)
                self.assertEqual(resolved.status, "ready", resolved)
                asyncio.run(_commit_realtime_turn(resolved.coordinator))
                self.assertTrue(engine.segment_ready.wait(timeout=2.0))

                host = resolved.coordinator.bridge.host
                response = next(
                    item for item in host.snapshot.responses.values() if item.voice_turn_id == request.voice_turn_id
                )
                self.assertEqual(response.state.value, "generating")
                cancelled = resolved.coordinator.cancel_response(reason="new_user_turn")
                self.assertEqual(cancelled.status, "accepted", cancelled)
                self.assertEqual(
                    host.snapshot.responses[response.response_id].state.value,
                    "cancelled",
                )
                self.assertFalse(host.snapshot.pending_commands)

                engine.release_final.set()
                self.assertTrue(service.wait_idle(timeout=5.0))
                self.assertEqual(
                    host.snapshot.responses[response.response_id].state.value,
                    "cancelled",
                )
                self.assertIsNone(host.snapshot.responses[response.response_id].memory_projection_id)
            finally:
                engine.release_final.set()
                service.close()
                manager.close()

    def test_production_host_synthesizes_before_playback_without_fake_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._manager(root)
            engine = _BlockingThinkingEngine(manager)
            tts_client = _TTSClient()
            service = self._service(
                root=root,
                manager=manager,
                adapter=_Adapter(),
                engine=engine,
                tts_client=tts_client,
            )
            try:
                request = _open_request(
                    voice_turn_id="voice-turn-production-tts",
                    output_mode=VOICE_PLAYBACK_OUTPUT_MODE,
                )
                resolved = service.create_coordinator(request)
                self.assertEqual(resolved.status, "ready", resolved)
                settled = asyncio.run(_commit_realtime_turn(resolved.coordinator))
                self.assertEqual(settled.response_status, "started")
                self.assertTrue(engine.segment_ready.wait(timeout=2.0))
                deadline = time.monotonic() + 2.0
                while not tts_client.calls and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(
                    tts_client.calls,
                    ["第一句现在就可以开始合成。"],
                )
                host = resolved.coordinator.bridge.host
                response = next(
                    response
                    for response in host.snapshot.responses.values()
                    if response.voice_turn_id == request.voice_turn_id
                )
                while time.monotonic() < deadline:
                    unit = next(iter(host.snapshot.speech_units.values()))
                    if unit.state.value == "ready":
                        break
                    time.sleep(0.01)
                unit = next(iter(host.snapshot.speech_units.values()))
                self.assertEqual(unit.state.value, "ready")
                self.assertTrue(unit.audio_artifact_ref.startswith("voice-audio:"))
                self.assertEqual(
                    [command.command_kind for command in host.snapshot.pending_commands.values()],
                    ["enqueue_playback"],
                )
                deadline = time.monotonic() + 2.0
                delivery = None
                while delivery is None and time.monotonic() < deadline:
                    delivery = resolved.delivery_channel.take_outbound()
                    if delivery is None:
                        time.sleep(0.01)
                self.assertIsNotNone(delivery)
                self.assertEqual(delivery.text, "第一句现在就可以开始合成。")
                self.assertEqual(delivery.audio, b"ID3-production-tts-audio")

                system = manager._get_system(
                    profile_user_id=request.profile_user_id,
                    session_id=request.session_id,
                    character_pack_id=request.character_pack_id,
                )
                entries = system.store.get_unsummarized_messages(namespace=system.namespace)
                self.assertNotIn(
                    "message.assistant.voice",
                    [str(entry.get("kind") or "") for entry in entries],
                )

                engine.release_final.set()
                self.assertTrue(service.wait_idle(timeout=5.0))
                completed_response = host.snapshot.responses[response.response_id]
                completed_unit = host.snapshot.speech_units[unit.speech_unit_id]
                self.assertEqual(completed_response.state.value, "generated")
                self.assertEqual(completed_unit.state.value, "ready")
            finally:
                engine.release_final.set()
                service.close()
                manager.close()

    def test_client_acknowledgements_are_the_only_delivery_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._manager(root)
            tts_client = _TTSClient()
            service = self._service(
                root=root,
                manager=manager,
                adapter=_Adapter(),
                tts_client=tts_client,
            )
            try:
                request = _open_request(
                    voice_turn_id="voice-turn-playback-acks",
                    output_mode=VOICE_PLAYBACK_OUTPUT_MODE,
                )
                resolved = service.create_coordinator(request)
                self.assertEqual(resolved.status, "ready", resolved)
                settled = asyncio.run(_commit_realtime_turn(resolved.coordinator))
                self.assertEqual(settled.response_status, "started")

                deadline = time.monotonic() + 3.0
                delivery = None
                while delivery is None and time.monotonic() < deadline:
                    delivery = resolved.delivery_channel.take_outbound()
                    if delivery is None:
                        time.sleep(0.01)
                self.assertIsNotNone(delivery)
                host = resolved.coordinator.bridge.host
                unit = host.snapshot.speech_units[delivery.speech_unit_id]
                self.assertEqual(unit.state.value, "ready")

                sent = resolved.delivery_channel.mark_sent(delivery.delivery_id)
                self.assertEqual(sent.status, "sent", sent)
                self.assertEqual(
                    host.snapshot.speech_units[delivery.speech_unit_id].state.value,
                    "ready",
                )
                enqueued = resolved.delivery_channel.acknowledge(
                    "client.playback.enqueued",
                    {"delivery_id": delivery.delivery_id},
                )
                self.assertTrue(enqueued.ok, enqueued)
                self.assertEqual(
                    host.snapshot.speech_units[delivery.speech_unit_id].state.value,
                    "queued",
                )

                too_early = resolved.delivery_channel.acknowledge(
                    "client.playback.completed",
                    {"delivery_id": delivery.delivery_id, "played_ms": 320},
                )
                self.assertEqual(too_early.status, "failed")
                self.assertEqual(
                    too_early.reason,
                    "voice_playback_terminal_ack_out_of_order",
                )
                started = resolved.delivery_channel.acknowledge(
                    "client.playback.started",
                    {
                        "delivery_id": delivery.delivery_id,
                        "resume_token": "client-playback-1",
                    },
                )
                self.assertTrue(started.ok, started)
                completed = resolved.delivery_channel.acknowledge(
                    "client.playback.completed",
                    {"delivery_id": delivery.delivery_id, "played_ms": 640},
                )
                self.assertTrue(completed.ok, completed)
                self.assertTrue(service.wait_idle(timeout=5.0))

                outcome = resolved.delivery_channel.response_outcome()
                self.assertTrue(outcome.response_terminal, outcome)
                self.assertEqual(outcome.response_state, "completed")
                self.assertEqual(outcome.delivery_status, "delivered")
                self.assertEqual(
                    host.snapshot.speech_units[delivery.speech_unit_id].state.value,
                    "delivered",
                )
                duplicate = resolved.delivery_channel.acknowledge(
                    "client.playback.completed",
                    {"delivery_id": delivery.delivery_id, "played_ms": 640},
                )
                conflict = resolved.delivery_channel.acknowledge(
                    "client.playback.completed",
                    {"delivery_id": delivery.delivery_id, "played_ms": 641},
                )
                self.assertEqual(duplicate.status, "duplicate")
                self.assertEqual(conflict.status, "failed")
                self.assertEqual(
                    conflict.reason,
                    "voice_playback_terminal_ack_conflict",
                )

                system = manager._get_system(
                    profile_user_id=request.profile_user_id,
                    session_id=request.session_id,
                    character_pack_id=request.character_pack_id,
                )
                entries = system.store.get_unsummarized_messages(namespace=system.namespace)
                assistant = next(entry for entry in entries if entry.get("kind") == "message.assistant.voice")
                self.assertEqual(
                    assistant["payload"]["delivery_status"],
                    "delivered",
                )
            finally:
                service.close()
                manager.close()

    def test_realtime_acoustic_signal_drives_duck_to_the_active_delivery_channel(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._manager(root)
            semantic_llm = _SemanticLLM()
            service = self._service(
                root=root,
                manager=manager,
                adapter=_Adapter(),
                engine=_SemanticThinkingEngine(manager, llm=semantic_llm),
                tts_client=_TTSClient(),
            )
            try:
                response_request = _open_request(
                    voice_turn_id="voice-turn-playing-response",
                    output_mode=VOICE_PLAYBACK_OUTPUT_MODE,
                )
                response = service.create_coordinator(response_request)
                self.assertEqual(response.status, "ready", response)
                asyncio.run(_commit_realtime_turn(response.coordinator))
                self.assertTrue(service.wait_idle(timeout=5.0))

                delivery = None
                deadline = time.monotonic() + 3.0
                while delivery is None and time.monotonic() < deadline:
                    delivery = response.delivery_channel.take_outbound()
                    if delivery is None:
                        time.sleep(0.01)
                self.assertIsNotNone(delivery)
                response.delivery_channel.mark_sent(delivery.delivery_id)
                self.assertTrue(
                    response.delivery_channel.acknowledge(
                        "client.playback.enqueued",
                        {"delivery_id": delivery.delivery_id},
                    ).ok
                )
                self.assertTrue(
                    response.delivery_channel.acknowledge(
                        "client.playback.started",
                        {
                            "delivery_id": delivery.delivery_id,
                            "resume_token": "desktop-overlap-resume",
                        },
                    ).ok
                )

                overlap_request = _open_request(
                    voice_turn_id="voice-turn-overlap-input",
                    output_mode=VOICE_PLAYBACK_OUTPUT_MODE,
                )
                overlap = service.create_coordinator(overlap_request)
                self.assertEqual(overlap.status, "ready", overlap)
                self.assertTrue(asyncio.run(overlap.coordinator.open()).ok)

                suspected = overlap.coordinator.suspect_interruption(
                    audio_clock_ms=360,
                )

                self.assertTrue(suspected.ok, suspected)
                duck = response.delivery_channel.take_control_outbound()
                self.assertIsNotNone(duck)
                self.assertEqual(duck.action, "duck")
                self.assertEqual(duck.delivery_id, delivery.delivery_id)
                self.assertEqual(duck.speech_unit_id, delivery.speech_unit_id)
                self.assertEqual(
                    response.coordinator.bridge.host.snapshot.speech_units[delivery.speech_unit_id].state.value,
                    "ducked",
                )
                self.assertIsNone(overlap.delivery_channel.take_control_outbound())
                response.delivery_channel.mark_control_sent(duck.control_id)
                self.assertTrue(
                    response.delivery_channel.acknowledge(
                        "client.playback.control_ack",
                        {
                            "control_id": duck.control_id,
                            "command_id": duck.command_id,
                            "action": "duck",
                            "status": "applied",
                            "played_ms": 360,
                            "applied_volume": 0.2,
                        },
                    ).ok
                )

                checkpoint = asyncio.run(
                    overlap.coordinator.feed_pcm_frame(
                        b"\x01\x00" * 160,
                        sequence=0,
                        audio_clock_ms=0,
                    )
                )
                self.assertTrue(checkpoint.ok, checkpoint)
                self.assertTrue(service.wait_idle(timeout=5.0))
                self.assertEqual(len(semantic_llm.calls), 1)
                resume = response.delivery_channel.take_control_outbound()
                self.assertIsNotNone(resume)
                self.assertEqual(resume.action, "resume")
                self.assertEqual(resume.speech_unit_id, delivery.speech_unit_id)
            finally:
                service.close()
                manager.close()

    def test_playback_control_commands_wait_for_real_client_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._manager(root)
            service = self._service(
                root=root,
                manager=manager,
                adapter=_Adapter(),
                tts_client=_TTSClient(),
            )
            try:
                request = _open_request(
                    voice_turn_id="voice-turn-control-receipts",
                    output_mode=VOICE_PLAYBACK_OUTPUT_MODE,
                )
                resolved = service.create_coordinator(request)
                self.assertEqual(resolved.status, "ready", resolved)
                asyncio.run(_commit_realtime_turn(resolved.coordinator))
                self.assertTrue(service.wait_idle(timeout=5.0))

                deadline = time.monotonic() + 3.0
                delivery = None
                while delivery is None and time.monotonic() < deadline:
                    delivery = resolved.delivery_channel.take_outbound()
                    if delivery is None:
                        time.sleep(0.01)
                self.assertIsNotNone(delivery)
                resolved.delivery_channel.mark_sent(delivery.delivery_id)
                resolved.delivery_channel.acknowledge(
                    "client.playback.enqueued",
                    {"delivery_id": delivery.delivery_id},
                )
                resolved.delivery_channel.acknowledge(
                    "client.playback.started",
                    {
                        "delivery_id": delivery.delivery_id,
                        "resume_token": "desktop-control-resume",
                    },
                )

                host = resolved.coordinator.bridge.host
                factory = EventFactory(
                    conversation_id=host.snapshot.conversation_id,
                    voice_session_id="voice-session-control-test",
                    conversation_generation=host.snapshot.conversation_generation,
                )
                opened = host.accept_event(
                    factory.make(
                        "voice.input.activity_started",
                        sequence=None,
                        voice_turn_id="voice-turn-interruption-probe",
                        audio_stream_id="audio-interruption-probe",
                    )
                )
                self.assertTrue(opened.accepted, opened)
                suspected = host.accept_event(
                    factory.make(
                        "voice.interruption.suspected",
                        sequence=None,
                        voice_turn_id="voice-turn-interruption-probe",
                        response_id=delivery.response_id,
                        speech_unit_id=delivery.speech_unit_id,
                        audio_stream_id="audio-interruption-probe",
                        audio_clock_ms=480,
                        payload={"interruption_id": "interruption-control-test"},
                    )
                )
                self.assertTrue(suspected.accepted, suspected)
                self.assertEqual(
                    host.snapshot.speech_units[delivery.speech_unit_id].state.value,
                    "ducked",
                )
                host.drive_once()
                duck = resolved.delivery_channel.take_control_outbound()
                self.assertIsNotNone(duck)
                self.assertEqual(duck.action, "duck")
                resolved.delivery_channel.mark_control_sent(duck.control_id)
                duck_ack = resolved.delivery_channel.acknowledge(
                    "client.playback.control_ack",
                    {
                        "control_id": duck.control_id,
                        "command_id": duck.command_id,
                        "action": "duck",
                        "status": "applied",
                        "played_ms": 480,
                        "applied_volume": 0.2,
                    },
                )
                self.assertTrue(duck_ack.ok, duck_ack)
                self.assertEqual(
                    host.snapshot.speech_units[delivery.speech_unit_id].state.value,
                    "ducked",
                )

                stop_command = {
                    "command_id": "command-stop-control-test",
                    "command_kind": "stop_playback",
                    "idempotency_key": "voicecore:stop-control-test",
                    "causation_id": "event-stop-control-test",
                    "payload": {
                        "speech_unit_id": delivery.speech_unit_id,
                        "reason": "takeover",
                        "interruption_id": "interruption-control-test",
                    },
                }
                executor = service._playback_executors[host.snapshot.conversation_id]
                offered = executor.execute(stop_command, snapshot_to_dict(host.snapshot))
                self.assertEqual(offered.status, "deferred", offered)
                stop = resolved.delivery_channel.take_control_outbound()
                self.assertIsNotNone(stop)
                self.assertEqual(stop.action, "stop")
                self.assertEqual(
                    host.snapshot.speech_units[delivery.speech_unit_id].state.value,
                    "ducked",
                )
                resolved.delivery_channel.mark_control_sent(stop.control_id)
                stopped = resolved.delivery_channel.acknowledge(
                    "client.playback.control_ack",
                    {
                        "control_id": stop.control_id,
                        "command_id": stop.command_id,
                        "action": "stop",
                        "status": "applied",
                        "played_ms": 620,
                    },
                )
                self.assertTrue(stopped.ok, stopped)
                self.assertEqual(
                    host.snapshot.speech_units[delivery.speech_unit_id].state.value,
                    "interrupted",
                )
                self.assertNotEqual(
                    resolved.delivery_channel.response_outcome().delivery_status,
                    "delivered",
                )
            finally:
                service.close()
                manager.close()

    def test_stable_interruption_checkpoint_runs_read_only_semantic_control(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._manager(root)
            semantic_llm = _SemanticLLM()
            engine = _SemanticThinkingEngine(manager, llm=semantic_llm)
            service = self._service(
                root=root,
                manager=manager,
                adapter=_Adapter(),
                engine=engine,
                tts_client=_TTSClient(),
            )
            try:
                request = _open_request(
                    voice_turn_id="voice-turn-semantic-source",
                    output_mode=VOICE_PLAYBACK_OUTPUT_MODE,
                )
                resolved = service.create_coordinator(request)
                self.assertEqual(resolved.status, "ready", resolved)
                asyncio.run(_commit_realtime_turn(resolved.coordinator))
                self.assertTrue(service.wait_idle(timeout=5.0))

                delivery = None
                deadline = time.monotonic() + 3.0
                while delivery is None and time.monotonic() < deadline:
                    delivery = resolved.delivery_channel.take_outbound()
                    if delivery is None:
                        time.sleep(0.01)
                self.assertIsNotNone(delivery)
                resolved.delivery_channel.mark_sent(delivery.delivery_id)
                self.assertTrue(
                    resolved.delivery_channel.acknowledge(
                        "client.playback.enqueued",
                        {"delivery_id": delivery.delivery_id},
                    ).ok
                )
                self.assertTrue(
                    resolved.delivery_channel.acknowledge(
                        "client.playback.started",
                        {
                            "delivery_id": delivery.delivery_id,
                            "resume_token": "semantic-resume-token",
                        },
                    ).ok
                )

                host = resolved.coordinator.bridge.host
                factory = EventFactory(
                    conversation_id=host.snapshot.conversation_id,
                    voice_session_id="voice-session-semantic-control",
                    conversation_generation=host.snapshot.conversation_generation,
                )
                opened = host.accept_event(
                    factory.make(
                        "voice.input.activity_started",
                        sequence=None,
                        voice_turn_id="voice-turn-semantic-interruption",
                        audio_stream_id="audio-semantic-interruption",
                    )
                )
                self.assertTrue(opened.accepted, opened)
                suspected = host.accept_event(
                    factory.make(
                        "voice.interruption.suspected",
                        sequence=None,
                        voice_turn_id="voice-turn-semantic-interruption",
                        response_id=delivery.response_id,
                        speech_unit_id=delivery.speech_unit_id,
                        audio_stream_id="audio-semantic-interruption",
                        audio_clock_ms=420,
                        payload={"interruption_id": "interruption-semantic-control"},
                    )
                )
                self.assertTrue(suspected.accepted, suspected)
                self.assertFalse(
                    [
                        command
                        for command in host.snapshot.pending_commands.values()
                        if command.command_kind == "request_semantic_pulse"
                    ]
                )

                host.drive_once()
                duck = resolved.delivery_channel.take_control_outbound()
                self.assertIsNotNone(duck)
                self.assertEqual(duck.action, "duck")
                resolved.delivery_channel.mark_control_sent(duck.control_id)
                self.assertTrue(
                    resolved.delivery_channel.acknowledge(
                        "client.playback.control_ack",
                        {
                            "control_id": duck.control_id,
                            "command_id": duck.command_id,
                            "action": "duck",
                            "status": "applied",
                            "played_ms": 420,
                            "applied_volume": 0.2,
                        },
                    ).ok
                )

                checkpoint = host.accept_event(
                    factory.make(
                        "voice.asr.checkpoint",
                        sequence=None,
                        voice_turn_id="voice-turn-semantic-interruption",
                        turn_revision=1,
                        payload={
                            "stable_text": "不对，我想补充一下",
                            "unstable_tail": "",
                            "control_significant": True,
                        },
                    )
                )
                self.assertTrue(checkpoint.accepted, checkpoint)
                pulse = next(
                    command
                    for command in host.snapshot.pending_commands.values()
                    if command.command_kind == "request_semantic_pulse"
                )
                self.assertEqual(pulse.payload["turn_revision"], 1)

                system = manager._get_system(
                    profile_user_id=request.profile_user_id,
                    session_id=request.session_id,
                    character_pack_id=request.character_pack_id,
                )
                before_dialogue_ids = {
                    str(entry.get("source_id") or "")
                    for entry in system.store.get_unsummarized_messages(namespace=system.namespace)
                    if str(entry.get("role") or "") in {"user", "assistant"}
                }

                driven = host.drive_once()
                self.assertEqual(driven.status, "deferred", driven)
                self.assertTrue(service.wait_idle(timeout=5.0))
                self.assertEqual(len(semantic_llm.calls), 1)
                call = semantic_llm.calls[0]
                self.assertIsNone(call["native_tools"])
                self.assertEqual(call["native_tool_choice"], "")
                self.assertTrue(call["history_turns"])
                self.assertTrue(str(call["prompt_cache_key"]).startswith("voice-semantic-"))
                self.assertIn("好，我从状态机的边界继续讲。", call["user_prompt"])
                self.assertIn("不对，我想补充一下", call["user_prompt"])

                after_dialogue_ids = {
                    str(entry.get("source_id") or "")
                    for entry in system.store.get_unsummarized_messages(namespace=system.namespace)
                    if str(entry.get("role") or "") in {"user", "assistant"}
                }
                self.assertEqual(after_dialogue_ids, before_dialogue_ids)

                resume = resolved.delivery_channel.take_control_outbound()
                self.assertIsNotNone(resume)
                self.assertEqual(resume.action, "resume")
                resolved.delivery_channel.mark_control_sent(resume.control_id)
                acknowledged = resolved.delivery_channel.acknowledge(
                    "client.playback.control_ack",
                    {
                        "control_id": resume.control_id,
                        "command_id": resume.command_id,
                        "action": "resume",
                        "status": "applied",
                        "played_ms": 520,
                        "applied_volume": 1.0,
                    },
                )
                self.assertTrue(acknowledged.ok, acknowledged)
                self.assertEqual(
                    host.snapshot.speech_units[delivery.speech_unit_id].state.value,
                    "playing",
                )

                finalized = host.accept_event(
                    factory.make(
                        "voice.asr.finalized",
                        sequence=None,
                        voice_turn_id="voice-turn-semantic-interruption",
                        turn_revision=2,
                        payload={
                            "stable_text": "不对，我想补充一下",
                            "unstable_tail": "",
                            "supersedes_revision": 1,
                        },
                    )
                )
                self.assertTrue(finalized.accepted, finalized)
                self.assertNotIn(
                    "request_semantic_pulse",
                    [command.command_kind for command in finalized.transition.commands],
                )
                committed = host.accept_event(
                    factory.make(
                        "voice.turn.commit_requested",
                        sequence=None,
                        voice_turn_id="voice-turn-semantic-interruption",
                        turn_revision=2,
                        payload={"disposition": "message"},
                    )
                )
                self.assertTrue(committed.accepted, committed)
                interruption_turn = host.snapshot.input_turns["voice-turn-semantic-interruption"]
                self.assertEqual(interruption_turn.state.value, "committed")
                self.assertEqual(interruption_turn.disposition.value, "interaction")
                self.assertFalse(
                    [
                        command
                        for command in host.snapshot.pending_commands.values()
                        if command.command_kind == "start_response_generation"
                        and command.payload.get("voice_turn_id") == "voice-turn-semantic-interruption"
                    ]
                )
                self.assertEqual(len(engine.calls), 1)
                interaction_entries = [
                    entry
                    for entry in system.store.get_unsummarized_messages(namespace=system.namespace)
                    if entry.get("kind") == "event.voice.interaction"
                    and entry.get("payload", {}).get("voice_turn_id") == "voice-turn-semantic-interruption"
                ]
                self.assertEqual(len(interaction_entries), 1)
            finally:
                service.close()
                manager.close()

    def test_speculative_candidate_waits_for_final_transcript_before_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._manager(root)
            semantic_llm = _CandidateSemanticLLM()
            engine = _SemanticThinkingEngine(manager, llm=semantic_llm)
            tts_client = _TTSClient()
            service = self._service(
                root=root,
                manager=manager,
                adapter=_Adapter(),
                engine=engine,
                tts_client=tts_client,
            )
            try:
                source = service.create_coordinator(
                    _open_request(
                        voice_turn_id="voice-turn-candidate-source",
                        output_mode=VOICE_PLAYBACK_OUTPUT_MODE,
                    )
                )
                self.assertEqual(source.status, "ready", source)
                asyncio.run(_commit_realtime_turn(source.coordinator))
                self.assertTrue(service.wait_idle(timeout=5.0))

                source_delivery = source.delivery_channel.take_outbound()
                self.assertIsNotNone(source_delivery)
                source.delivery_channel.mark_sent(source_delivery.delivery_id)
                self.assertTrue(
                    source.delivery_channel.acknowledge(
                        "client.playback.enqueued",
                        {"delivery_id": source_delivery.delivery_id},
                    ).ok
                )
                self.assertTrue(
                    source.delivery_channel.acknowledge(
                        "client.playback.started",
                        {
                            "delivery_id": source_delivery.delivery_id,
                            "resume_token": "candidate-source-resume",
                        },
                    ).ok
                )

                candidate = service.create_coordinator(
                    _open_request(
                        voice_turn_id="voice-turn-candidate-takeover",
                        output_mode=VOICE_PLAYBACK_OUTPUT_MODE,
                    )
                )
                self.assertEqual(candidate.status, "ready", candidate)
                self.assertTrue(asyncio.run(candidate.coordinator.open()).ok)
                suspected = candidate.coordinator.suspect_interruption(
                    audio_clock_ms=420,
                )
                self.assertTrue(suspected.ok, suspected)

                duck = source.delivery_channel.take_control_outbound()
                self.assertIsNotNone(duck)
                self.assertEqual(duck.action, "duck")
                source.delivery_channel.mark_control_sent(duck.control_id)
                self.assertTrue(
                    source.delivery_channel.acknowledge(
                        "client.playback.control_ack",
                        {
                            "control_id": duck.control_id,
                            "command_id": duck.command_id,
                            "action": "duck",
                            "status": "applied",
                            "played_ms": 420,
                            "applied_volume": 0.2,
                        },
                    ).ok
                )

                system = manager._get_system(
                    profile_user_id="profile-user",
                    session_id="session-visible-id",
                    character_pack_id="character-pack",
                )
                before_dialogue_ids = {
                    str(entry.get("source_id") or "")
                    for entry in system.store.get_unsummarized_messages(namespace=system.namespace)
                    if str(entry.get("kind") or "") in {"message.user.voice", "message.assistant.voice"}
                    and entry.get("payload", {}).get("voice_turn_id") == "voice-turn-candidate-takeover"
                }

                checkpoint = asyncio.run(
                    candidate.coordinator.feed_pcm_frame(
                        b"\x01\x00" * 160,
                        sequence=0,
                        audio_clock_ms=0,
                    )
                )
                self.assertTrue(checkpoint.ok, checkpoint)
                self.assertTrue(service.wait_idle(timeout=5.0))
                self.assertEqual(len(semantic_llm.calls), 1)

                stop = source.delivery_channel.take_control_outbound()
                self.assertIsNotNone(stop)
                self.assertEqual(stop.action, "stop")
                source.delivery_channel.mark_control_sent(stop.control_id)
                self.assertTrue(
                    source.delivery_channel.acknowledge(
                        "client.playback.control_ack",
                        {
                            "control_id": stop.control_id,
                            "command_id": stop.command_id,
                            "action": "stop",
                            "status": "applied",
                            "played_ms": 520,
                        },
                    ).ok
                )
                self.assertTrue(service.wait_idle(timeout=5.0))
                self.assertEqual(len(engine.candidate_calls), 1)
                self.assertEqual(len(engine.calls), 1)
                self.assertIsNone(candidate.delivery_channel.take_outbound())

                host = candidate.coordinator.bridge.host
                candidate_response = next(
                    response
                    for response in host.snapshot.responses.values()
                    if response.voice_turn_id == "voice-turn-candidate-takeover"
                    and response.commitment.value == "speculative"
                )
                self.assertFalse(candidate_response.playable)
                self.assertEqual(candidate_response.state.value, "generated")
                after_candidate_dialogue_ids = {
                    str(entry.get("source_id") or "")
                    for entry in system.store.get_unsummarized_messages(namespace=system.namespace)
                    if str(entry.get("kind") or "") in {"message.user.voice", "message.assistant.voice"}
                    and entry.get("payload", {}).get("voice_turn_id") == "voice-turn-candidate-takeover"
                }
                self.assertEqual(after_candidate_dialogue_ids, before_dialogue_ids)

                started = asyncio.run(candidate.coordinator.start_finalize_pcm())
                self.assertTrue(started.ok, started)
                settled = asyncio.run(candidate.coordinator.settle_finalize())
                self.assertTrue(settled.ok, settled)
                self.assertTrue(service.wait_idle(timeout=5.0))
                self.assertEqual(len(semantic_llm.calls), 2)
                validator_call = semantic_llm.calls[-1]
                self.assertIn(
                    "候选回复校验器",
                    str(validator_call.get("system_prompt") or ""),
                )
                self.assertIsNone(validator_call["native_tools"])
                self.assertTrue(str(validator_call["prompt_cache_key"]).startswith("voice-candidate-validator-"))

                adopted = host.snapshot.responses[candidate_response.response_id]
                self.assertEqual(adopted.commitment.value, "committed")
                self.assertTrue(adopted.playable)
                self.assertEqual(adopted.candidate_status, "adopted")
                self.assertEqual(len(engine.candidate_calls), 1)
                self.assertEqual(len(engine.calls), 1)

                adopted_delivery = candidate.delivery_channel.take_outbound()
                self.assertIsNotNone(adopted_delivery)
                self.assertEqual(
                    adopted_delivery.response_id,
                    candidate_response.response_id,
                )
                candidate.delivery_channel.mark_sent(adopted_delivery.delivery_id)
                self.assertTrue(
                    candidate.delivery_channel.acknowledge(
                        "client.playback.enqueued",
                        {"delivery_id": adopted_delivery.delivery_id},
                    ).ok
                )
                self.assertTrue(
                    candidate.delivery_channel.acknowledge(
                        "client.playback.started",
                        {
                            "delivery_id": adopted_delivery.delivery_id,
                            "resume_token": "candidate-adopted-resume",
                        },
                    ).ok
                )
                self.assertTrue(
                    candidate.delivery_channel.acknowledge(
                        "client.playback.completed",
                        {
                            "delivery_id": adopted_delivery.delivery_id,
                            "played_ms": 780,
                        },
                    ).ok
                )
                entries = system.store.get_unsummarized_messages(namespace=system.namespace)
                self.assertTrue(
                    any(
                        entry.get("kind") == "message.user.voice"
                        and entry.get("payload", {}).get("voice_turn_id") == "voice-turn-candidate-takeover"
                        for entry in entries
                    )
                )
                self.assertTrue(
                    any(
                        entry.get("kind") == "message.assistant.voice"
                        and entry.get("payload", {}).get("voice_turn_id") == "voice-turn-candidate-takeover"
                        for entry in entries
                    )
                )
            finally:
                service.close()
                manager.close()

    def test_restart_discards_unconfirmed_candidate_without_replaying_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._manager(root)
            semantic_llm = _CandidateSemanticLLM()
            first_engine = _SemanticThinkingEngine(manager, llm=semantic_llm)
            first_service: AkaneVoiceRuntimeService | None = self._service(
                root=root,
                manager=manager,
                adapter=_Adapter(),
                engine=first_engine,
                tts_client=_TTSClient(),
            )
            recovered_service: AkaneVoiceRuntimeService | None = None
            try:
                source = first_service.create_coordinator(
                    _open_request(
                        voice_turn_id="voice-turn-restart-candidate-source",
                        output_mode=VOICE_PLAYBACK_OUTPUT_MODE,
                    )
                )
                self.assertEqual(source.status, "ready", source)
                asyncio.run(_commit_realtime_turn(source.coordinator))
                self.assertTrue(first_service.wait_idle(timeout=5.0))
                source_delivery = source.delivery_channel.take_outbound()
                self.assertIsNotNone(source_delivery)
                source.delivery_channel.mark_sent(source_delivery.delivery_id)
                self.assertTrue(
                    source.delivery_channel.acknowledge(
                        "client.playback.enqueued",
                        {"delivery_id": source_delivery.delivery_id},
                    ).ok
                )
                self.assertTrue(
                    source.delivery_channel.acknowledge(
                        "client.playback.started",
                        {
                            "delivery_id": source_delivery.delivery_id,
                            "resume_token": "restart-candidate-source",
                        },
                    ).ok
                )

                candidate = first_service.create_coordinator(
                    _open_request(
                        voice_turn_id="voice-turn-restart-candidate",
                        output_mode=VOICE_PLAYBACK_OUTPUT_MODE,
                    )
                )
                self.assertEqual(candidate.status, "ready", candidate)
                self.assertTrue(asyncio.run(candidate.coordinator.open()).ok)
                self.assertTrue(
                    candidate.coordinator.suspect_interruption(
                        audio_clock_ms=300,
                    ).ok
                )
                duck = source.delivery_channel.take_control_outbound()
                self.assertIsNotNone(duck)
                source.delivery_channel.mark_control_sent(duck.control_id)
                self.assertTrue(
                    source.delivery_channel.acknowledge(
                        "client.playback.control_ack",
                        {
                            "control_id": duck.control_id,
                            "command_id": duck.command_id,
                            "action": "duck",
                            "status": "applied",
                            "played_ms": 300,
                            "applied_volume": 0.2,
                        },
                    ).ok
                )
                checkpoint = asyncio.run(
                    candidate.coordinator.feed_pcm_frame(
                        b"\x01\x00" * 160,
                        sequence=0,
                        audio_clock_ms=0,
                    )
                )
                self.assertTrue(checkpoint.ok, checkpoint)
                self.assertTrue(first_service.wait_idle(timeout=5.0))
                stop = source.delivery_channel.take_control_outbound()
                self.assertIsNotNone(stop)
                source.delivery_channel.mark_control_sent(stop.control_id)
                self.assertTrue(
                    source.delivery_channel.acknowledge(
                        "client.playback.control_ack",
                        {
                            "control_id": stop.control_id,
                            "command_id": stop.command_id,
                            "action": "stop",
                            "status": "applied",
                            "played_ms": 420,
                        },
                    ).ok
                )
                self.assertTrue(first_service.wait_idle(timeout=5.0))
                self.assertEqual(len(first_engine.candidate_calls), 1)
                old_host = candidate.coordinator.bridge.host
                old_candidate = next(
                    response
                    for response in old_host.snapshot.responses.values()
                    if response.voice_turn_id == "voice-turn-restart-candidate"
                )
                self.assertEqual(old_candidate.state.value, "generated")
                self.assertFalse(old_candidate.playable)
                old_response_id = old_candidate.response_id

                first_service.close()
                first_service = None
                recovered_engine = _SemanticThinkingEngine(
                    manager,
                    llm=_CandidateSemanticLLM(),
                )
                recovered_service = self._service(
                    root=root,
                    manager=manager,
                    adapter=_Adapter(),
                    engine=recovered_engine,
                    tts_client=_TTSClient(),
                )
                recovered = recovered_service.create_coordinator(
                    _open_request(
                        voice_turn_id="voice-turn-after-candidate-restart",
                        output_mode=VOICE_PLAYBACK_OUTPUT_MODE,
                    )
                )
                self.assertEqual(recovered.status, "ready", recovered)
                recovered_candidate = recovered.coordinator.bridge.host.snapshot.responses[old_response_id]
                self.assertEqual(recovered_candidate.state.value, "discarded")
                self.assertEqual(
                    recovered_candidate.candidate_status,
                    "rejected",
                )
                self.assertFalse(recovered_candidate.playable)
                self.assertEqual(recovered_engine.candidate_calls, [])
                self.assertEqual(recovered_engine.calls, [])
                self.assertIsNone(recovered.delivery_channel.take_outbound())
            finally:
                if first_service is not None:
                    first_service.close()
                if recovered_service is not None:
                    recovered_service.close()
                manager.close()

    def test_restart_skips_semantic_pulse_bound_to_the_old_playback_channel(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._manager(root)
            semantic_llm = _SemanticLLM()
            engine = _SemanticThinkingEngine(manager, llm=semantic_llm)
            first_service: AkaneVoiceRuntimeService | None = self._service(
                root=root,
                manager=manager,
                adapter=_Adapter(),
                engine=engine,
                tts_client=_TTSClient(),
            )
            recovered_service: AkaneVoiceRuntimeService | None = None
            try:
                request = _open_request(
                    voice_turn_id="voice-turn-semantic-before-restart",
                    output_mode=VOICE_PLAYBACK_OUTPUT_MODE,
                )
                resolved = first_service.create_coordinator(request)
                self.assertEqual(resolved.status, "ready", resolved)
                asyncio.run(_commit_realtime_turn(resolved.coordinator))
                self.assertTrue(first_service.wait_idle(timeout=5.0))

                delivery = None
                deadline = time.monotonic() + 3.0
                while delivery is None and time.monotonic() < deadline:
                    delivery = resolved.delivery_channel.take_outbound()
                    if delivery is None:
                        time.sleep(0.01)
                self.assertIsNotNone(delivery)
                resolved.delivery_channel.mark_sent(delivery.delivery_id)
                self.assertTrue(
                    resolved.delivery_channel.acknowledge(
                        "client.playback.enqueued",
                        {"delivery_id": delivery.delivery_id},
                    ).ok
                )
                self.assertTrue(
                    resolved.delivery_channel.acknowledge(
                        "client.playback.started",
                        {
                            "delivery_id": delivery.delivery_id,
                            "resume_token": "semantic-restart-resume-token",
                        },
                    ).ok
                )

                host = resolved.coordinator.bridge.host
                factory = EventFactory(
                    conversation_id=host.snapshot.conversation_id,
                    voice_session_id="voice-session-semantic-restart",
                    conversation_generation=host.snapshot.conversation_generation,
                )
                self.assertTrue(
                    host.accept_event(
                        factory.make(
                            "voice.input.activity_started",
                            sequence=None,
                            voice_turn_id="voice-turn-semantic-restart-interruption",
                            audio_stream_id="audio-semantic-restart-interruption",
                        )
                    ).accepted
                )
                self.assertTrue(
                    host.accept_event(
                        factory.make(
                            "voice.interruption.suspected",
                            sequence=None,
                            voice_turn_id="voice-turn-semantic-restart-interruption",
                            response_id=delivery.response_id,
                            speech_unit_id=delivery.speech_unit_id,
                            audio_stream_id="audio-semantic-restart-interruption",
                            audio_clock_ms=360,
                            payload={"interruption_id": "interruption-semantic-restart"},
                        )
                    ).accepted
                )
                host.drive_once()
                duck = resolved.delivery_channel.take_control_outbound()
                self.assertIsNotNone(duck)
                resolved.delivery_channel.mark_control_sent(duck.control_id)
                self.assertTrue(
                    resolved.delivery_channel.acknowledge(
                        "client.playback.control_ack",
                        {
                            "control_id": duck.control_id,
                            "command_id": duck.command_id,
                            "action": "duck",
                            "status": "applied",
                            "played_ms": 360,
                            "applied_volume": 0.2,
                        },
                    ).ok
                )
                self.assertTrue(
                    host.accept_event(
                        factory.make(
                            "voice.asr.checkpoint",
                            sequence=None,
                            voice_turn_id="voice-turn-semantic-restart-interruption",
                            turn_revision=1,
                            payload={
                                "stable_text": "等一下，我想补充",
                                "unstable_tail": "",
                                "control_significant": True,
                            },
                        )
                    ).accepted
                )
                self.assertEqual(
                    [command.command_kind for command in host.snapshot.pending_commands.values()],
                    ["request_semantic_pulse"],
                )
                self.assertEqual(semantic_llm.calls, [])

                recovered_root = root / "recovered-process"
                shutil.copytree(root / "voice-state", recovered_root / "voice-state")
                first_service.close()
                first_service = None

                recovered_service = self._service(
                    root=recovered_root,
                    manager=manager,
                    adapter=_Adapter(),
                    engine=engine,
                    tts_client=_TTSClient(),
                )
                recovered = recovered_service.create_coordinator(
                    _open_request(
                        voice_turn_id="voice-turn-after-semantic-restart",
                        output_mode=VOICE_PLAYBACK_OUTPUT_MODE,
                    )
                )
                self.assertEqual(recovered.status, "ready", recovered)
                recovered_host = recovered.coordinator.bridge.host
                self.assertEqual(semantic_llm.calls, [])
                self.assertFalse(
                    [
                        command.command_kind
                        for command in recovered_host.snapshot.pending_commands.values()
                        if command.command_kind
                        in {
                            "request_semantic_pulse",
                            "resume_playback",
                            "stop_playback",
                        }
                    ]
                )
                self.assertEqual(
                    recovered_host.snapshot.speech_units[delivery.speech_unit_id].state.value,
                    "ducked",
                )
            finally:
                if first_service is not None:
                    first_service.close()
                if recovered_service is not None:
                    recovered_service.close()
                manager.close()

    def test_multiple_speech_units_are_offered_only_in_playback_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._manager(root)
            service = self._service(
                root=root,
                manager=manager,
                adapter=_Adapter(),
                engine=_MultiSegmentThinkingEngine(manager),
                tts_client=_TTSClient(),
            )
            try:
                resolved = service.create_coordinator(
                    _open_request(
                        voice_turn_id="voice-turn-two-segments",
                        output_mode=VOICE_PLAYBACK_OUTPUT_MODE,
                    )
                )
                self.assertEqual(resolved.status, "ready", resolved)
                asyncio.run(_commit_realtime_turn(resolved.coordinator))
                self.assertTrue(service.wait_idle(timeout=5.0))

                deadline = time.monotonic() + 3.0
                first = None
                while first is None and time.monotonic() < deadline:
                    first = resolved.delivery_channel.take_outbound()
                    if first is None:
                        time.sleep(0.01)
                self.assertIsNotNone(first)
                self.assertEqual(first.ordinal, 0)
                self.assertEqual(first.text, "第一句先播放。")
                self.assertIsNone(resolved.delivery_channel.take_outbound())

                resolved.delivery_channel.mark_sent(first.delivery_id)
                resolved.delivery_channel.acknowledge(
                    "client.playback.enqueued",
                    {"delivery_id": first.delivery_id},
                )
                resolved.delivery_channel.acknowledge(
                    "client.playback.started",
                    {"delivery_id": first.delivery_id},
                )
                resolved.delivery_channel.acknowledge(
                    "client.playback.completed",
                    {"delivery_id": first.delivery_id},
                )

                second = None
                deadline = time.monotonic() + 2.0
                while second is None and time.monotonic() < deadline:
                    second = resolved.delivery_channel.take_outbound()
                    if second is None:
                        time.sleep(0.01)
                self.assertIsNotNone(second)
                self.assertEqual(second.ordinal, 1)
                self.assertEqual(second.text, "第二句随后播放。")
                resolved.delivery_channel.mark_sent(second.delivery_id)
                resolved.delivery_channel.acknowledge(
                    "client.playback.enqueued",
                    {"delivery_id": second.delivery_id},
                )
                resolved.delivery_channel.acknowledge(
                    "client.playback.started",
                    {"delivery_id": second.delivery_id},
                )
                completed = resolved.delivery_channel.acknowledge(
                    "client.playback.completed",
                    {"delivery_id": second.delivery_id},
                )
                self.assertTrue(completed.response_terminal, completed)
                self.assertEqual(completed.delivery_status, "delivered")
            finally:
                service.close()
                manager.close()

    def test_client_disconnect_marks_started_audio_interrupted_not_delivered(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._manager(root)
            engine = _BlockingThinkingEngine(manager)
            service = self._service(
                root=root,
                manager=manager,
                adapter=_Adapter(),
                engine=engine,
                tts_client=_TTSClient(),
            )
            try:
                request = _open_request(
                    voice_turn_id="voice-turn-disconnect",
                    output_mode=VOICE_PLAYBACK_OUTPUT_MODE,
                )
                resolved = service.create_coordinator(request)
                asyncio.run(_commit_realtime_turn(resolved.coordinator))
                deadline = time.monotonic() + 3.0
                delivery = None
                while delivery is None and time.monotonic() < deadline:
                    delivery = resolved.delivery_channel.take_outbound()
                    if delivery is None:
                        time.sleep(0.01)
                self.assertIsNotNone(delivery)
                resolved.delivery_channel.mark_sent(delivery.delivery_id)
                resolved.delivery_channel.acknowledge(
                    "client.playback.enqueued",
                    {"delivery_id": delivery.delivery_id},
                )
                resolved.delivery_channel.acknowledge(
                    "client.playback.started",
                    {"delivery_id": delivery.delivery_id},
                )

                closed = resolved.delivery_channel.close(reason="client_disconnected")
                self.assertFalse(closed.response_terminal)
                host = resolved.coordinator.bridge.host
                self.assertEqual(
                    host.snapshot.speech_units[delivery.speech_unit_id].state.value,
                    "interrupted",
                )
                engine.release_final.set()
                self.assertTrue(service.wait_idle(timeout=5.0))

                outcome = resolved.delivery_channel.response_outcome()
                self.assertTrue(outcome.response_terminal)
                self.assertEqual(outcome.delivery_status, "partial")
                self.assertNotEqual(outcome.delivery_status, "delivered")
                system = manager._get_system(
                    profile_user_id=request.profile_user_id,
                    session_id=request.session_id,
                    character_pack_id=request.character_pack_id,
                )
                assistant = next(
                    entry
                    for entry in system.store.get_unsummarized_messages(namespace=system.namespace)
                    if entry.get("kind") == "message.assistant.voice"
                )
                self.assertEqual(
                    assistant["payload"]["delivery_status"],
                    "partial",
                )
                self.assertEqual(
                    assistant["payload"]["interrupted_units"],
                    [0],
                )
            finally:
                engine.release_final.set()
                service.close()
                manager.close()

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

    def test_requested_playback_fails_before_asr_when_tts_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._manager(root)
            adapter = _Adapter()
            service = self._service(
                root=root,
                manager=manager,
                adapter=adapter,
                tts_client=None,
            )
            try:
                result = service.create_coordinator(
                    _open_request(
                        voice_turn_id="voice-turn-no-tts",
                        output_mode=VOICE_PLAYBACK_OUTPUT_MODE,
                    )
                )
                self.assertEqual(result.status, "unavailable")
                self.assertEqual(result.reason, "voice_tts_provider_unavailable")
                self.assertEqual(adapter.sessions, [])
            finally:
                service.close()
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

    def test_restart_fails_an_already_generating_response_without_replaying_the_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._manager(root)
            first_service = self._service(
                root=root,
                manager=manager,
                adapter=_Adapter(),
            )
            request = _open_request(voice_turn_id="voice-turn-generating-before-restart")
            try:
                first = first_service.create_coordinator(request)
                self.assertEqual(first.status, "ready", first)
                first.coordinator.response_starter = None
                settled = asyncio.run(_commit_realtime_turn(first.coordinator))
                self.assertEqual(settled.response_status, "")
                host = first.coordinator.bridge.host
                while host.snapshot.pending_commands:
                    driven = host.drive_once()
                    self.assertEqual(driven.status, "succeeded", driven)
                old_response = next(
                    response
                    for response in host.snapshot.responses.values()
                    if response.voice_turn_id == request.voice_turn_id
                )
                self.assertEqual(old_response.state.value, "generating")
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
                second = second_service.create_coordinator(
                    _open_request(voice_turn_id="voice-turn-after-stale-generation")
                )
                self.assertEqual(second.status, "ready", second)
                recovered_response = next(
                    response
                    for response in second.coordinator.bridge.host.snapshot.responses.values()
                    if response.voice_turn_id == request.voice_turn_id
                )
                self.assertEqual(recovered_response.state.value, "failed")
                assert recovered_response.failure is not None
                self.assertEqual(
                    recovered_response.failure.reason_code,
                    "voice_thinking_runtime_restarted",
                )
                self.assertEqual(second_engine.calls, [])
            finally:
                second_service.close()
                manager.close()

    def test_restart_settles_playback_bound_to_the_old_client_channel(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._manager(root)
            first_engine = _BlockingThinkingEngine(manager)
            first_service = self._service(
                root=root,
                manager=manager,
                adapter=_Adapter(),
                engine=first_engine,
                tts_client=_TTSClient(),
            )
            recovered_service: AkaneVoiceRuntimeService | None = None
            try:
                request = _open_request(
                    voice_turn_id="voice-turn-playback-before-restart",
                    output_mode=VOICE_PLAYBACK_OUTPUT_MODE,
                )
                first = first_service.create_coordinator(request)
                self.assertEqual(first.status, "ready", first)
                asyncio.run(_commit_realtime_turn(first.coordinator))
                self.assertTrue(first_engine.segment_ready.wait(timeout=2.0))

                deadline = time.monotonic() + 3.0
                old_host = first.coordinator.bridge.host
                while time.monotonic() < deadline:
                    if any(
                        command.command_kind == "enqueue_playback"
                        for command in old_host.snapshot.pending_commands.values()
                    ):
                        break
                    time.sleep(0.01)
                self.assertTrue(
                    any(
                        command.command_kind == "enqueue_playback"
                        for command in old_host.snapshot.pending_commands.values()
                    )
                )
                first_engine.release_final.set()
                self.assertTrue(first_service.wait_idle(timeout=5.0))

                recovered_service = self._service(
                    root=root,
                    manager=manager,
                    adapter=_Adapter(),
                    tts_client=_TTSClient(),
                )
                recovered = recovered_service.create_coordinator(
                    _open_request(voice_turn_id="voice-turn-playback-after-restart")
                )

                self.assertEqual(recovered.status, "ready", recovered)
                recovered_host = recovered.coordinator.bridge.host
                old_unit = next(
                    unit
                    for unit in recovered_host.snapshot.speech_units.values()
                    if unit.response_id
                    in {
                        response.response_id
                        for response in recovered_host.snapshot.responses.values()
                        if response.voice_turn_id == request.voice_turn_id
                    }
                )
                self.assertEqual(old_unit.state.value, "failed")
                self.assertEqual(
                    old_unit.failure_reason,
                    "voice_playback_runtime_restarted",
                )
                self.assertFalse(
                    [
                        command
                        for command in recovered_host.snapshot.pending_commands.values()
                        if command.command_kind == "enqueue_playback"
                        and command.payload.get("speech_unit_id") == old_unit.speech_unit_id
                    ]
                )
            finally:
                first_engine.release_final.set()
                if recovered_service is not None:
                    recovered_service.close()
                first_service.close()
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
