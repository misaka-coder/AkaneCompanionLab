"""Immutable text and audio envelopes through the existing artifact ports."""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from ..stream_bridge import VoiceTextArtifactResult
from .contracts import (
    _STORAGE_SCHEMA_VERSION,
    _TEXT_ARTIFACT_REF_PATTERN,
    _AUDIO_ARTIFACT_REF_PATTERN,
    _AUDIO_MEDIA_TYPE_PATTERN,
    _AUDIO_ARTIFACT_MAGIC,
    VoiceTextArtifactReadResult,
    VoiceAudioArtifactResult,
    VoiceAudioArtifactReadResult,
)
from .storage import (
    _conversation_storage_root,
    _required_identity,
    _required_generation,
    _canonical_json,
    _atomic_create_bytes,
    _storage_path_is_owned,
    _utf8_digest,
)


class FileVoiceTextArtifactPort:
    """Immutable, path-free text artifacts for VoiceCore speech units."""

    def __init__(
        self,
        *,
        state_dir: Path,
        conversation_id: str,
        conversation_generation: int,
    ) -> None:
        conversation_root = _conversation_storage_root(
            state_dir=state_dir,
            conversation_id=_required_identity(conversation_id, "conversation_id"),
            conversation_generation=_required_generation(conversation_generation),
        )
        self._artifact_dir = conversation_root / "text_artifacts"
        self._guard = threading.RLock()

    def put_text(
        self,
        *,
        artifact_key: str,
        text: str,
        media_type: str,
    ) -> VoiceTextArtifactResult:
        if not isinstance(artifact_key, str) or not artifact_key:
            return VoiceTextArtifactResult.failed("voice_text_artifact_key_invalid")
        if not isinstance(text, str):
            return VoiceTextArtifactResult.failed("voice_text_artifact_text_invalid")
        if not isinstance(media_type, str) or not media_type or _utf8_digest(media_type) is None:
            return VoiceTextArtifactResult.failed("voice_text_artifact_media_type_invalid")
        key_digest = _utf8_digest(artifact_key)
        if key_digest is None:
            return VoiceTextArtifactResult.failed("voice_text_artifact_key_invalid")
        artifact_ref = f"voice-text:{key_digest}"
        content_digest = _utf8_digest(text)
        if content_digest is None:
            return VoiceTextArtifactResult.failed("voice_text_artifact_text_invalid")
        payload = {
            "schema_version": _STORAGE_SCHEMA_VERSION,
            "artifact_ref": artifact_ref,
            "artifact_key_sha256": key_digest,
            "content_sha256": content_digest,
            "media_type": media_type,
            "text": text,
        }
        path = self._artifact_dir / f"{key_digest}.json"
        with self._guard:
            if not _storage_path_is_owned(path):
                return VoiceTextArtifactResult.failed("voice_text_artifact_unreadable")
            try:
                if not path.exists():
                    self._artifact_dir.mkdir(parents=True, exist_ok=True)
                    _atomic_create_bytes(path, (_canonical_json(payload) + "\n").encode("utf-8"))
                    return VoiceTextArtifactResult.succeeded(artifact_ref)
            except FileExistsError:
                pass  # Another instance published first; compare its complete envelope.
            except OSError:
                return VoiceTextArtifactResult.failed("voice_text_artifact_write_failed", retryable=True)
            if not _storage_path_is_owned(path):
                return VoiceTextArtifactResult.failed("voice_text_artifact_unreadable")
            existing = self._read_record(path=path, expected_ref=artifact_ref)
            if not existing.ok:
                return VoiceTextArtifactResult.failed(existing.reason)
            if existing.text == text and existing.media_type == media_type:
                return VoiceTextArtifactResult.duplicate(artifact_ref)
            return VoiceTextArtifactResult.failed("voice_text_artifact_conflict")

    def read_text(self, artifact_ref: str) -> VoiceTextArtifactReadResult:
        match = _TEXT_ARTIFACT_REF_PATTERN.fullmatch(str(artifact_ref or ""))
        if match is None:
            return VoiceTextArtifactReadResult(status="failed", reason="voice_text_artifact_ref_invalid")
        path = self._artifact_dir / f"{match.group('digest')}.json"
        with self._guard:
            if not _storage_path_is_owned(path):
                return VoiceTextArtifactReadResult(status="failed", reason="voice_text_artifact_unreadable")
            if not path.is_file():
                return VoiceTextArtifactReadResult(status="failed", reason="voice_text_artifact_missing")
            return self._read_record(path=path, expected_ref=str(artifact_ref))

    @staticmethod
    def _read_record(*, path: Path, expected_ref: str) -> VoiceTextArtifactReadResult:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return VoiceTextArtifactReadResult(status="failed", reason="voice_text_artifact_unreadable")
        required = {
            "schema_version",
            "artifact_ref",
            "artifact_key_sha256",
            "content_sha256",
            "media_type",
            "text",
        }
        if not isinstance(payload, dict) or set(payload) != required:
            return VoiceTextArtifactReadResult(status="failed", reason="voice_text_artifact_unreadable")
        text = payload.get("text")
        media_type = payload.get("media_type")
        digest = _utf8_digest(text) if isinstance(text, str) else None
        if (
            payload.get("schema_version") != _STORAGE_SCHEMA_VERSION
            or payload.get("artifact_ref") != expected_ref
            or not isinstance(text, str)
            or not isinstance(media_type, str)
            or not media_type
            or _utf8_digest(media_type) is None
            or digest is None
            or digest != payload.get("content_sha256")
        ):
            return VoiceTextArtifactReadResult(status="failed", reason="voice_text_artifact_unreadable")
        match = _TEXT_ARTIFACT_REF_PATTERN.fullmatch(expected_ref)
        if match is None or payload.get("artifact_key_sha256") != match.group("digest"):
            return VoiceTextArtifactReadResult(status="failed", reason="voice_text_artifact_unreadable")
        return VoiceTextArtifactReadResult(
            status="succeeded",
            text=text,
            media_type=media_type,
        )


class FileVoiceAudioArtifactPort:
    """Immutable, path-free synthesized audio artifacts.

    One atomic binary envelope keeps metadata and audio bytes together. This
    prevents a crash between an audio file and a sidecar from manufacturing a
    seemingly ready VoiceCore unit with incomplete storage.
    """

    def __init__(
        self,
        *,
        state_dir: Path,
        conversation_id: str,
        conversation_generation: int,
    ) -> None:
        conversation_root = _conversation_storage_root(
            state_dir=state_dir,
            conversation_id=_required_identity(conversation_id, "conversation_id"),
            conversation_generation=_required_generation(conversation_generation),
        )
        self._artifact_dir = conversation_root / "audio_artifacts"
        self._guard = threading.RLock()

    def put_audio(
        self,
        *,
        artifact_key: str,
        audio: bytes,
        media_type: str,
        origin: Mapping[str, str] | None = None,
    ) -> VoiceAudioArtifactResult:
        if not isinstance(artifact_key, str) or not artifact_key:
            return VoiceAudioArtifactResult.failed("voice_audio_artifact_key_invalid")
        if not isinstance(audio, bytes) or not audio:
            return VoiceAudioArtifactResult.failed("voice_audio_artifact_audio_invalid")
        if origin is not None and not _valid_audio_origin(origin):
            return VoiceAudioArtifactResult.failed("voice_audio_artifact_origin_invalid")
        normalized_media_type = str(media_type or "").strip().lower().split(";", 1)[0]
        if _AUDIO_MEDIA_TYPE_PATTERN.fullmatch(normalized_media_type) is None:
            return VoiceAudioArtifactResult.failed("voice_audio_artifact_media_type_invalid")

        key_digest = _utf8_digest(artifact_key)
        if key_digest is None:
            return VoiceAudioArtifactResult.failed("voice_audio_artifact_key_invalid")
        artifact_ref = f"voice-audio:{key_digest}"
        content_digest = sha256(audio).hexdigest()
        metadata = {
            "schema_version": _STORAGE_SCHEMA_VERSION,
            "artifact_ref": artifact_ref,
            "artifact_key_sha256": key_digest,
            "content_sha256": content_digest,
            "media_type": normalized_media_type,
            "byte_length": len(audio),
        }
        if origin:
            metadata["origin"] = dict(origin)
        encoded_metadata = _canonical_json(metadata).encode("utf-8")
        envelope = (
            _AUDIO_ARTIFACT_MAGIC
            + len(encoded_metadata).to_bytes(4, byteorder="big", signed=False)
            + encoded_metadata
            + audio
        )
        path = self._artifact_dir / f"{key_digest}.bin"
        with self._guard:
            if not _storage_path_is_owned(path):
                return VoiceAudioArtifactResult.failed("voice_audio_artifact_unreadable")
            try:
                if not path.exists():
                    self._artifact_dir.mkdir(parents=True, exist_ok=True)
                    _atomic_create_bytes(path, envelope)
                    return VoiceAudioArtifactResult.succeeded(artifact_ref)
            except FileExistsError:
                pass
            except OSError:
                return VoiceAudioArtifactResult.failed(
                    "voice_audio_artifact_write_failed",
                    retryable=True,
                )
            if not _storage_path_is_owned(path):
                return VoiceAudioArtifactResult.failed("voice_audio_artifact_unreadable")
            existing = self._read_record(path=path, expected_ref=artifact_ref)
            if not existing.ok:
                return VoiceAudioArtifactResult.failed(existing.reason)
            if (
                existing.audio == audio
                and existing.media_type == normalized_media_type
                and existing.origin == dict(origin or {})
            ):
                return VoiceAudioArtifactResult.duplicate(artifact_ref)
            return VoiceAudioArtifactResult.failed("voice_audio_artifact_conflict")

    def read_audio(self, artifact_ref: str) -> VoiceAudioArtifactReadResult:
        match = _AUDIO_ARTIFACT_REF_PATTERN.fullmatch(str(artifact_ref or ""))
        if match is None:
            return VoiceAudioArtifactReadResult(
                status="failed",
                reason="voice_audio_artifact_ref_invalid",
            )
        path = self._artifact_dir / f"{match.group('digest')}.bin"
        with self._guard:
            if not _storage_path_is_owned(path):
                return VoiceAudioArtifactReadResult(status="failed", reason="voice_audio_artifact_unreadable")
            if not path.is_file():
                return VoiceAudioArtifactReadResult(
                    status="failed",
                    reason="voice_audio_artifact_missing",
                )
            return self._read_record(path=path, expected_ref=str(artifact_ref))

    @staticmethod
    def _read_record(
        *,
        path: Path,
        expected_ref: str,
    ) -> VoiceAudioArtifactReadResult:
        try:
            envelope = path.read_bytes()
            prefix_length = len(_AUDIO_ARTIFACT_MAGIC)
            if envelope[:prefix_length] != _AUDIO_ARTIFACT_MAGIC:
                raise ValueError
            metadata_length_offset = prefix_length + 4
            if len(envelope) < metadata_length_offset:
                raise ValueError
            metadata_length = int.from_bytes(
                envelope[prefix_length:metadata_length_offset],
                byteorder="big",
                signed=False,
            )
            audio_offset = metadata_length_offset + metadata_length
            if metadata_length < 2 or audio_offset >= len(envelope):
                raise ValueError
            metadata = json.loads(envelope[metadata_length_offset:audio_offset].decode("utf-8"))
            audio = envelope[audio_offset:]
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            return VoiceAudioArtifactReadResult(
                status="failed",
                reason="voice_audio_artifact_unreadable",
            )

        required = {
            "schema_version",
            "artifact_ref",
            "artifact_key_sha256",
            "content_sha256",
            "media_type",
            "byte_length",
        }
        match = _AUDIO_ARTIFACT_REF_PATTERN.fullmatch(expected_ref)
        if (
            not isinstance(metadata, dict)
            or set(metadata) not in (required, required | {"origin"})
            or ("origin" in metadata and not _valid_audio_origin(metadata["origin"]))
            or match is None
            or metadata.get("schema_version") != _STORAGE_SCHEMA_VERSION
            or metadata.get("artifact_ref") != expected_ref
            or metadata.get("artifact_key_sha256") != match.group("digest")
            or not isinstance(metadata.get("media_type"), str)
            or _AUDIO_MEDIA_TYPE_PATTERN.fullmatch(metadata["media_type"]) is None
            or isinstance(metadata.get("byte_length"), bool)
            or not isinstance(metadata.get("byte_length"), int)
            or metadata.get("byte_length") != len(audio)
            or metadata.get("content_sha256") != sha256(audio).hexdigest()
        ):
            return VoiceAudioArtifactReadResult(
                status="failed",
                reason="voice_audio_artifact_unreadable",
            )
        return VoiceAudioArtifactReadResult(
            status="succeeded",
            audio=audio,
            media_type=metadata["media_type"],
            origin=dict(metadata.get("origin") or {}),
        )


def _valid_audio_origin(origin):
    keys = {"plugin_id", "generation_id", "capability_id", "invocation_id"}
    # Playback-only TTS has provenance but no durable workbench artifact.
    return (
        isinstance(origin, Mapping)
        and keys <= set(origin) <= keys | {"generated_handle"}
        and all(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_:.-]{1,256}", value) for value in origin.values())
    )
