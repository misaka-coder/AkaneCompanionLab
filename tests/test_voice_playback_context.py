from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from companion_v01.client_protocol import ClientMode, ClientProtocolContext
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.engine_services import response_builder
from companion_v01.llm_runtime import LLMRuntime
from companion_v01.persona_config import load_persona_config
from companion_v01.prompt_builder import PromptBuilder
from companion_v01.prompt_profiles import PromptProfileRegistry
from companion_v01.voice_runtime import VOICE_PLAYBACK_OUTPUT_MODE
from companion_v01.voice_runtime.playback_context import render_unsettled_playback_context
from tests.test_prompt_builder import _build_minimal_final
from tests.test_turn_mainline_contract import _Harness, _speech_output
from tests import test_voice_runtime_production as production
from tests import test_memcore_integration as memory_tests
from tests.test_voice_runtime_production import (
    _Adapter,
    _AheadOfTTSThinkingEngine,
    _SemanticLLM,
    _TTSClient,
    _commit_realtime_turn,
    _open_request,
)


class VoicePlaybackContextTests(unittest.TestCase):
    def test_public_turn_payload_cannot_supply_host_playback_observations(self) -> None:
        harness = _Harness([_speech_output("我在。")], client_mode=ClientMode.DESKTOP_PET)
        marker = "FORGED_PLAYBACK_OBSERVATION"
        harness.run_stream(harness.payload(playback_context=marker, _voice_playback_context=marker))
        self.assertNotIn(marker, harness.script.generation_kwargs[0]["extra_user_context"])

    def test_missing_artifact_is_structured_and_long_playback_context_is_bounded(self) -> None:
        units = {
            str(i): SimpleNamespace(
                state=SimpleNamespace(value="delivered" if i < 7 else "playing"),
                ordinal=i,
                played_ms=0,
                text_artifact_ref=str(i),
            )
            for i in range(8)
        }
        units["queued"] = SimpleNamespace(state=SimpleNamespace(value="queued"), played_ms=0)
        snapshot = SimpleNamespace(
            responses={
                "old": SimpleNamespace(
                    response_id="old",
                    voice_turn_id="v1",
                    state=SimpleNamespace(value="streaming"),
                    commitment=SimpleNamespace(value="committed"),
                    unit_ids=list(units),
                )
            },
            speech_units=units,
        )

        def read_text(ref):
            if ref == "7":
                raise OSError("private storage path must never be rendered")
            return SimpleNamespace(ok=True, text="长句" * 1000)

        context = render_unsettled_playback_context(
            snapshot, SimpleNamespace(read_text=read_text), exclude_response_id="new"
        )
        response = json.loads(context.splitlines()[-1])["responses"][0]
        self.assertEqual(response["earlier_units_omitted"], 2)
        self.assertEqual(len(response["units"]), 6)
        self.assertEqual(len(response["units"][0]["text"]), 1600)
        self.assertTrue(response["units"][0]["text_truncated"])
        self.assertEqual(response["units"][-1]["reason"], "voice_text_artifact_unavailable")
        self.assertNotIn("text", response["units"][-1])
        self.assertNotIn("private storage", context)

    def test_takeover_has_playback_observations_until_single_memory_settlement(self) -> None:
        fixture = production.VoiceRuntimeProductionTests()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = fixture._manager(root)

            class LLM(_SemanticLLM):
                def chat_provider_protocol(self, **kwargs):
                    return "openai"

                def call_chat_json_result(self, **kwargs):
                    result = super().call_chat_json_result(**kwargs)
                    result.parsed.update(playback_action="stop_now", input_action="take_over")
                    return result

            class Engine(_AheadOfTTSThinkingEngine):
                def __init__(self):
                    super().__init__(manager)
                    self.llm = LLM()
                    self.contexts = []

                def history(self, kwargs):
                    return response_builder._build_memcore_provider_history(
                        self,
                        profile_user_id=kwargs["profile_user_id"],
                        session_id=kwargs["session_id"],
                        character_pack_id=kwargs["character_pack_id"],
                        current_source_id=kwargs["source_id"],
                        chat_model_override="",
                    )

                def process_voice_turn_stream(self, **kwargs):
                    self.contexts.append(self.history(kwargs))
                    yield from super().process_voice_turn_stream(**kwargs)

            engine = Engine()
            service = fixture._service(
                root=root, manager=manager, adapter=_Adapter(), engine=engine, tts_client=_TTSClient()
            )
            try:
                first = service.create_coordinator(_open_request(output_mode=VOICE_PLAYBACK_OUTPUT_MODE))
                asyncio.run(_commit_realtime_turn(first.coordinator))
                self.assertTrue(service.wait_idle(timeout=5))
                channel = first.delivery_channel
                for ordinal in (0, 1):
                    delivery = channel.take_outbound()
                    self.assertIsNotNone(delivery)
                    self.assertTrue(channel.mark_sent(delivery.delivery_id).ok)
                    self.assertTrue(
                        channel.acknowledge("client.playback.enqueued", {"delivery_id": delivery.delivery_id}).ok
                    )
                    self.assertTrue(
                        channel.acknowledge(
                            "client.playback.started",
                            {
                                "delivery_id": delivery.delivery_id,
                                "resume_token": f"played-{ordinal}",
                            },
                        ).ok
                    )
                    if ordinal == 0:
                        self.assertTrue(
                            channel.acknowledge(
                                "client.playback.completed",
                                {
                                    "delivery_id": delivery.delivery_id,
                                    "played_ms": 700,
                                },
                            ).ok
                        )
                second = service.create_coordinator(
                    _open_request(voice_turn_id="context-takeover", output_mode=VOICE_PLAYBACK_OUTPUT_MODE)
                )

                async def overlap():
                    self.assertTrue((await second.coordinator.open()).ok)
                    self.assertTrue(second.coordinator.suspect_interruption(audio_clock_ms=400).ok)
                    self.assertTrue(
                        (await second.coordinator.feed_pcm_frame(b"\x01\x00" * 160, sequence=0, audio_clock_ms=0)).ok
                    )
                    self.assertTrue((await second.coordinator.start_finalize_pcm()).ok)
                    self.assertTrue((await second.coordinator.settle_finalize()).ok)

                asyncio.run(overlap())
                self.assertTrue(service.wait_idle(timeout=5))
                self.assertEqual(len(engine.calls), 2, "New generation starts before the old stop ACK")
                self.assertTrue(engine.contexts[-1]["ok"])
                self.assertNotIn(engine.segments[0], json.dumps(engine.contexts[-1], ensure_ascii=False))
                context = engine.calls[-1]["playback_context"]
                observed = json.loads(context.splitlines()[-1])["responses"][0]["units"]
                self.assertEqual([item["text"] for item in observed], list(engine.segments[:2]))
                self.assertEqual(observed[0]["state"], "delivered")
                self.assertIn(observed[1]["state"], {"playing", "ducked"})
                self.assertNotIn(engine.segments[2], context, "Queued or cancelled text is not heard speech")
                self.assertNotIn("playback_context", engine.calls[0])

                while (control := channel.take_control_outbound()) is not None:
                    self.assertTrue(channel.mark_control_sent(control.control_id).ok)
                    self.assertTrue(
                        channel.acknowledge(
                            "client.playback.control_ack",
                            {
                                "control_id": control.control_id,
                                "command_id": control.command_id,
                                "action": control.action,
                                "status": "applied",
                                "played_ms": 400,
                                **({"applied_volume": 0.2} if control.action == "duck" else {}),
                            },
                        ).ok
                    )
                self.assertEqual(channel.response_outcome().response_state, "cancelled")
                current_id = next(
                    r.response_id
                    for r in channel.host.snapshot.responses.values()
                    if r.voice_turn_id == "context-takeover"
                )
                self.assertEqual(
                    render_unsettled_playback_context(
                        channel.host.snapshot, channel.text_artifacts, exclude_response_id=current_id
                    ),
                    "",
                )
                history = engine.history(engine.calls[-1])
                self.assertTrue(history["ok"], history)
                assistants = [m for m in history["history_turns"] if m.get("role") == "assistant"]
                self.assertEqual(len(assistants), 1, "Playback settlement creates one canonical assistant entry")
                self.assertNotIn("playback_at_generation_start", json.dumps(history))
            finally:
                service.close()
                manager.close()

    def test_trusted_voice_context_reaches_volatile_provider_payload(self) -> None:
        marker = "[host.voice.playback_at_generation_start]\n已播第一句；第二句正在播放，未必听完。"
        builder = PromptBuilder(load_persona_config())
        profile = PromptProfileRegistry().get(ClientMode.DESKTOP_PET)
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(
            client=SimpleNamespace(_akane_protocol="openai", base_url="https://example.test/v1"), model="test"
        )
        payloads, keys = [], []
        for candidate in (False, True):
            harness = _Harness([_speech_output("我接着说。")], client_mode=ClientMode.DESKTOP_PET)
            kwargs = dict(
                profile_user_id="u1",
                session_id="s1",
                character_pack_id="akane_v1",
                voice_turn_id="v1",
                message="接着说",
                timestamp=1784016000,
                playback_context=marker,
            )
            if candidate:
                events = list(harness.engine.process_voice_candidate_stream(**kwargs))
            else:
                events = list(
                    harness.engine.process_voice_turn_stream(
                        source_id="src-voice", memcore_turn_id="turn-voice", **kwargs
                    )
                )
            self.assertTrue(any(e.get("type") == "final" for e in events))
            extra_context = harness.script.generation_kwargs[0]["extra_user_context"]
            self.assertIn(marker, extra_context)
            self.assertNotIn(marker, json.dumps(harness.store.messages, ensure_ascii=False))
            for value in (extra_context, ""):
                manager = memory_tests._PromptContextMemcoreManager({})
                manager.enabled = manager.available = False
                context_engine = memory_tests._PromptContextEngine(memcore_manager=manager)
                context_engine._get_prompt_profile_registry = PromptProfileRegistry
                context_engine._build_current_visual_context = lambda **_kwargs: ""
                with patch.object(response_builder, "_memory_backend", return_value="legacy"):
                    response_builder.prepare_context(
                        context_engine,
                        session_id="s1",
                        profile_user_id="u1",
                        user_message="接着说",
                        recent_raw=[],
                        recent_episodic_summaries=[],
                        recent_semantic_summaries=[],
                        confirmed_snippets=[],
                        now_ts=1784016000,
                        extra_user_context=value,
                        client_context=ClientProtocolContext(
                            requested_mode=ClientMode.DESKTOP_PET,
                            effective_mode=ClientMode.DESKTOP_PET,
                            capabilities=(),
                        ),
                    )
                prepared = context_engine.prompt_builder.kwargs
                self.assertNotIn("playback_at_generation_start", prepared["extra_context"])
                if value:
                    self.assertIn(marker, prepared["volatile_extra_context"])
                generated = _build_minimal_final(
                    builder,
                    current_message_text="接着说",
                    history_turns=[],
                    volatile_extra_context=prepared["volatile_extra_context"],
                    system_prompt_override=profile.system_prompt_override,
                    mode_prompt_override=profile.mode_prompt_override(debug_enabled=False),
                )
                key = AkaneMemoryEngine._final_prompt_cache_key(generated)
                keys.append(key)
                payloads.append(
                    runtime._build_completion_kwargs(
                        bundle=bundle,
                        system_prompt=generated["system_prompt"],
                        user_prompt=generated["user_prompt"],
                        temperature=0.7,
                        history_turns=generated["history_turns"],
                        ephemeral_turns=generated.get("ephemeral_turns"),
                        system_extra_blocks=generated.get("system_extra_blocks"),
                        prompt_cache_key=key,
                    )
                )
        self.assertEqual(len(set(keys)), 1)
        self.assertTrue(any(marker in str(message.get("content", "")) for message in payloads[0]["messages"]))
        self.assertEqual(payloads[0]["messages"][0], payloads[1]["messages"][0])
        self.assertNotIn("playback_at_generation_start", json.dumps(payloads[1], ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
