from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from companion_v01.voice_runtime import (
    AkaneVoiceTTSCommandExecutor,
    FileVoiceAudioArtifactPort,
    FileVoiceTextArtifactPort,
    VoiceCommandExecutionResult,
    VoiceCommandRouterExecutor,
)


class _TTSClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.failure: Exception | None = None

    async def synthesize(self, text: str) -> Any:
        self.calls.append(text)
        if self.failure is not None:
            raise self.failure
        return SimpleNamespace(
            audio=b"ID3-real-synthesized-audio",
            media_type="audio/mpeg",
        )


class _RecordingExecutor:
    def __init__(self) -> None:
        self.execute_calls = 0
        self.recover_calls = 0

    def execute(self, _command: Any, _snapshot: Any) -> VoiceCommandExecutionResult:
        self.execute_calls += 1
        return VoiceCommandExecutionResult.failed("recording_execute")

    def recover(self, _command: Any, _snapshot: Any) -> VoiceCommandExecutionResult:
        self.recover_calls += 1
        return VoiceCommandExecutionResult.not_started("recording_recover")


def _command() -> dict[str, Any]:
    return {
        "command_id": "command-start-tts-1",
        "command_kind": "start_tts",
        "idempotency_key": "idempotency-start-tts-1",
        "causation_id": "speech-declared-event-1",
        "payload": {
            "speech_unit_id": "speech-unit-1",
            "response_id": "response-1",
            "response_generation": 1,
            "ordinal": 0,
            "text_artifact_ref": "",
        },
    }


def _snapshot() -> dict[str, Any]:
    return {
        "responses": {
            "response-1": {
                "response_id": "response-1",
                "voice_turn_id": "voice-turn-1",
                "source_turn_revision": 2,
                "response_generation": 1,
            }
        },
        "input_turns": {
            "voice-turn-1": {
                "voice_turn_id": "voice-turn-1",
                "voice_session_id": "voice-session-1",
            }
        },
    }


class VoiceRuntimeTTSExecutorTests(unittest.TestCase):
    def _ports(
        self,
        root: Path,
    ) -> tuple[FileVoiceTextArtifactPort, FileVoiceAudioArtifactPort]:
        kwargs = {
            "state_dir": root,
            "conversation_id": "conversation-1",
            "conversation_generation": 1,
        }
        return FileVoiceTextArtifactPort(**kwargs), FileVoiceAudioArtifactPort(**kwargs)

    def test_real_tts_result_is_persisted_before_ready_and_recoverable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            text_artifacts, audio_artifacts = self._ports(Path(temp_dir))
            text = "第一句出来后就可以合成。"
            text_result = text_artifacts.put_text(
                artifact_key="response-1:0",
                text=text,
                media_type="text/plain; charset=utf-8",
            )
            command = _command()
            command["payload"]["text_artifact_ref"] = text_result.artifact_ref
            client = _TTSClient()
            executor = AkaneVoiceTTSCommandExecutor(
                tts_client=client,
                text_artifacts=text_artifacts,
                audio_artifacts=audio_artifacts,
                conversation_id="conversation-1",
                conversation_generation=1,
            )

            executed = executor.execute(command, _snapshot())

            self.assertEqual(executed.status, "succeeded", executed)
            self.assertEqual(client.calls, [text])
            self.assertEqual(
                [event.event_kind for event in executed.observations],
                ["voice.tts.started", "voice.tts.ready"],
            )
            ready = executed.observations[-1]
            self.assertEqual(ready.payload["command_id"], command["command_id"])
            self.assertNotIn("command_id", executed.observations[0].payload)
            stored = audio_artifacts.read_audio(ready.payload["audio_artifact_ref"])
            self.assertTrue(stored.ok, stored)
            self.assertEqual(stored.audio, b"ID3-real-synthesized-audio")

            recovered = executor.recover(command, _snapshot())
            self.assertEqual(recovered.status, "succeeded", recovered)
            self.assertEqual(client.calls, [text])
            self.assertEqual(
                [event.event_id for event in recovered.observations],
                [event.event_id for event in executed.observations],
            )
            self.assertEqual(
                recovered.observations[-1].payload["audio_artifact_ref"],
                ready.payload["audio_artifact_ref"],
            )

    def test_tts_failure_becomes_terminal_observation_without_private_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            text_artifacts, audio_artifacts = self._ports(Path(temp_dir))
            text_result = text_artifacts.put_text(
                artifact_key="response-1:0",
                text="这次 provider 会失败。",
                media_type="text/plain",
            )
            command = _command()
            command["payload"]["text_artifact_ref"] = text_result.artifact_ref
            client = _TTSClient()
            client.failure = RuntimeError("private endpoint and key detail")
            executor = AkaneVoiceTTSCommandExecutor(
                tts_client=client,
                text_artifacts=text_artifacts,
                audio_artifacts=audio_artifacts,
                conversation_id="conversation-1",
                conversation_generation=1,
            )

            result = executor.execute(command, _snapshot())

            self.assertEqual(result.status, "succeeded", result)
            self.assertEqual(
                [event.event_kind for event in result.observations],
                ["voice.tts.started", "voice.tts.failed"],
            )
            failure = result.observations[-1]
            self.assertEqual(
                failure.payload["reason_code"],
                "voice_tts_synthesis_failed",
            )
            self.assertEqual(failure.payload["command_id"], command["command_id"])
            self.assertNotIn("private endpoint", str(result))

    def test_preflight_failure_does_not_fake_tts_started(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            text_artifacts, audio_artifacts = self._ports(Path(temp_dir))
            command = _command()
            command["payload"]["text_artifact_ref"] = "voice-text:" + "0" * 64
            client = _TTSClient()
            executor = AkaneVoiceTTSCommandExecutor(
                tts_client=client,
                text_artifacts=text_artifacts,
                audio_artifacts=audio_artifacts,
                conversation_id="conversation-1",
                conversation_generation=1,
            )

            result = executor.execute(command, _snapshot())

            self.assertEqual(result.status, "succeeded", result)
            self.assertEqual(
                [event.event_kind for event in result.observations],
                ["voice.tts.failed"],
            )
            self.assertEqual(
                result.observations[0].payload["reason_code"],
                "voice_tts_text_artifact_unavailable",
            )
            self.assertEqual(client.calls, [])

    def test_recovery_without_durable_audio_stays_unknown_instead_of_resynthesizing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            text_artifacts, audio_artifacts = self._ports(Path(temp_dir))
            text_result = text_artifacts.put_text(
                artifact_key="response-1:0",
                text="不能盲目重复计费。",
                media_type="text/plain",
            )
            command = _command()
            command["payload"]["text_artifact_ref"] = text_result.artifact_ref
            client = _TTSClient()
            executor = AkaneVoiceTTSCommandExecutor(
                tts_client=client,
                text_artifacts=text_artifacts,
                audio_artifacts=audio_artifacts,
                conversation_id="conversation-1",
                conversation_generation=1,
            )

            recovered = executor.recover(command, _snapshot())

            self.assertEqual(recovered.status, "failed")
            self.assertFalse(recovered.outcome_known)
            self.assertTrue(recovered.retryable)
            self.assertEqual(recovered.reason, "voice_tts_outcome_unconfirmed")
            self.assertEqual(client.calls, [])

    def test_command_router_keeps_one_executor_authority_per_kind(self) -> None:
        response_executor = _RecordingExecutor()
        tts_executor = _RecordingExecutor()
        router = VoiceCommandRouterExecutor(
            {
                "start_response_generation": response_executor,
                "start_tts": tts_executor,
            }
        )

        routed = router.execute(_command(), _snapshot())
        recovered = router.recover(
            {
                **_command(),
                "command_kind": "start_response_generation",
            },
            _snapshot(),
        )
        unsupported = router.execute(
            {**_command(), "command_kind": "enqueue_playback"},
            _snapshot(),
        )

        self.assertEqual(routed.reason, "recording_execute")
        self.assertEqual(recovered.reason, "recording_recover")
        self.assertEqual(response_executor.recover_calls, 1)
        self.assertEqual(tts_executor.execute_calls, 1)
        self.assertEqual(unsupported.status, "deferred")
        self.assertEqual(
            unsupported.reason,
            "voice_command_not_connected:enqueue_playback",
        )


if __name__ == "__main__":
    unittest.main()
