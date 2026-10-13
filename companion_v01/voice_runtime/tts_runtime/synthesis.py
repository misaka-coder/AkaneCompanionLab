"""Provider synthesis followed by immutable audio publication."""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import threading
import uuid
from collections.abc import Mapping
from typing import Any

from ...tts_service import TTSServiceError
from ..host import VoiceCommandExecutionResult


class TTSSynthesis:
    def _synthesize(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        context = self._command_context(command_record, snapshot_record)
        if isinstance(context, str):
            return VoiceCommandExecutionResult.failed(context)
        command, payload, common = context
        with self._guard:
            if self._closed:
                return self._failed_observation(
                    command=command, common=common, reason="voice_tts_executor_closed", started=False,
                )
        text_result = self.text_artifacts.read_text(str(payload.get("text_artifact_ref") or ""))
        if not text_result.ok:
            return self._failed_observation(
                command=command,
                common=common,
                reason="voice_tts_text_artifact_unavailable",
                started=False,
            )
        if self.tts_client is None or not callable(getattr(self.tts_client, "synthesize", None)):
            return self._failed_observation(
                command=command,
                common=common,
                reason="voice_tts_provider_unavailable",
                started=False,
            )

        if self._command_cancelled(str(command["command_id"])):
            return self._failed_observation(command=command, common=common,
                reason="invocation_cancelled", started=False)
        try:
            from ..speech_artifact import decode_speech_artifact
            try:
                unit = decode_speech_artifact(text_result)
            except (ValueError, TypeError):
                raise TTSServiceError("voice_speech_artifact_invalid", outcome_known=True) from None
            contextual = getattr(self.tts_client, "synthesize_command", None)
            if self._command_cancelled(str(command["command_id"])):
                return self._failed_observation(
                    command=command, common=common, reason="invocation_cancelled", started=False,
                )
            if callable(contextual):
                synthesized = _resolve_awaitable(contextual(unit["say"],
                    **({"speech_protocol": unit["speech_protocol"], "voice_style": unit["style"]} if unit["speech_protocol"] else {}),
                    invocation_id="voice-tts-" + hashlib.sha256(self._artifact_key(command).encode()).hexdigest(),
                    cancel_requested=lambda: self._command_cancelled(str(command["command_id"]))))
            else:
                # Generic effect port used by non-plugin embedders and tests.
                if unit["speech_protocol"]:
                    raise TTSServiceError("voice_speech_style_unsupported", outcome_known=True)
                synthesized = _resolve_awaitable(self.tts_client.synthesize(unit["say"]))
            audio, media_type = _coerce_synthesized_audio(synthesized)
        except TTSServiceError as exc:
            if not exc.outcome_known:
                return VoiceCommandExecutionResult.unknown("voice_tts_outcome_unconfirmed")
            return self._failed_observation(command=command, common=common, reason=exc.reason,
                started=bool(exc.origin), origin=exc.origin)
        except Exception:
            if callable(getattr(self.tts_client, "synthesize_command", None)):
                # An untyped service/transport exception is not a confirmed
                # provider failure. Keep the executing receipt for recovery.
                return VoiceCommandExecutionResult.unknown("voice_tts_outcome_unconfirmed")
            return self._failed_observation(
                command=command,
                common=common,
                reason="voice_tts_synthesis_failed",
                started=True,
            )

        artifact_key = self._artifact_key(command)
        try:
            origin = getattr(synthesized, "origin", None)
            stored = self.audio_artifacts.put_audio(
                artifact_key=artifact_key,
                audio=audio,
                media_type=media_type,
                **({"origin": origin} if origin else {}),
            )
        except Exception:
            # Publication may have committed before acknowledgement was lost.
            return VoiceCommandExecutionResult.unknown("voice_tts_outcome_unconfirmed")
        if not stored.ok:
            return self._failed_observation(
                command=command,
                common=common,
                reason=(
                    stored.reason
                    if stored.reason.startswith("voice_audio_artifact_")
                    else "voice_audio_artifact_write_failed"
                ),
                started=True,
            )
        return self._audio_result(command=command, common=common, artifact_ref=stored.artifact_ref, origin=origin)


def _coerce_synthesized_audio(value: Any) -> tuple[bytes, str]:
    if isinstance(value, bytes):
        audio = value
        media_type = "audio/mpeg"
    else:
        audio = getattr(value, "audio", None)
        media_type = getattr(value, "media_type", None)
    if not isinstance(audio, bytes) or not audio:
        raise ValueError("voice_tts_audio_invalid")
    normalized_media_type = str(media_type or "audio/mpeg").strip().lower()
    return audio, normalized_media_type

def _resolve_awaitable(value: Any) -> Any:
    if not inspect.isawaitable(value):
        return value
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)

    result: list[Any] = []
    failure: list[BaseException] = []

    def run() -> None:
        try:
            result.append(asyncio.run(value))
        except BaseException as exc:  # preserved and reraised on the caller
            failure.append(exc)

    worker = threading.Thread(
        target=run,
        name=f"voice-tts-await-{uuid.uuid4().hex[:8]}",
        daemon=True,
    )
    worker.start()
    worker.join()
    if failure:
        raise failure[0]
    if not result:
        raise RuntimeError("voice_tts_awaitable_no_result")
    return result[0]
