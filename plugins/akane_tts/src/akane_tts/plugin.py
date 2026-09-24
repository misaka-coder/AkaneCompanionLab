from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import shutil
import subprocess
import wave

from akane_plugin import (
    AKANE_PLUGIN_API_VERSION, CapabilityDescriptor, CapabilityIOSlot,
    CapabilityResult, HealthStatus, ManagedArtifactDraft,
    ManagedArtifactPayload, PluginManifest, PluginResultPayload, PluginResultExperience,
)
from capcore_adapter_speech import (
    EdgeTTSClient, GptSovitsTTSClient, OpenAICompatTTSAdapter, resolve_emotion_voice_profile,
)

PLUGIN_ID = "akane.tts"
CAPABILITY_ID = "akane.tts.service.tts.v1.synthesize"
PERMISSIONS = ("service.provide", "resource.read", "artifact.write", "network.read", "connection.tts.read")
EDGE = "provider.tts.edge"
GPT = "provider.tts.gpt_sovits.local"
MAX_AUDIO = 16 * 1024 * 1024
OUTPUT_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["provider_id", "voice_profile_id", "emotion", "emotion_voice_id", "profile_fingerprint", "media_type"],
    "properties": {key: {"type": "string"} for key in (
        "provider_id", "voice_profile_id", "emotion", "emotion_voice_id", "profile_fingerprint", "media_type")}}


def descriptor():
    return CapabilityDescriptor(id=CAPABILITY_ID, display_name="语音合成服务", short_hint="按授权声线合成音频。",
        prompt_exposed=False, visible_in=("web", "desktop", "qq"), risk="medium", confirm="first_time",
        effects=("network", "filesystem"), trigger=None,
        inputs=(CapabilityIOSlot("text", "string", required=True, raw={"minLength": 1, "maxLength": 12000}),
            CapabilityIOSlot("voice", "object", required=True, raw={"additionalProperties": False,
                "required": ["provider", "profile_id"], "properties": {
                    "provider": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$"},
                    "profile_id": {"type": "string", "maxLength": 120}}}),
            CapabilityIOSlot("emotion", "string", raw={"maxLength": 64})),
        outputs=(CapabilityIOSlot("audio", "file", required=True, max_bytes=MAX_AUDIO, delivery="generated_file"),),
        output_schema=OUTPUT_SCHEMA, raw={"service": {"service_id": "tts", "version": 1, "method": "synthesize"},
            "followup": "none", "idempotency": "effectful"})


def inspect_audio(audio, media_type):
    if not isinstance(audio, bytes) or not 0 < len(audio) <= MAX_AUDIO:
        raise ValueError("tts_audio_size_invalid")
    if media_type in ("audio/wav", "audio/x-wav", "audio/wave"):
        with wave.open(io.BytesIO(audio), "rb") as decoded:
            frames = decoded.getnframes()
            expected = frames * decoded.getnchannels() * decoded.getsampwidth()
            if frames <= 0 or decoded.getframerate() <= 0 or len(decoded.readframes(frames)) != expected:
                raise ValueError("tts_audio_invalid")
        return "wav", "audio/wav"
    if media_type in ("audio/mpeg", "audio/mp3"):
        decoder = shutil.which("ffmpeg")
        if not decoder:
            raise ValueError("tts_audio_decoder_unavailable")
        decoded = subprocess.run([decoder, "-v", "error", "-xerror", "-i", "pipe:0", "-t", "0.1",
            "-f", "s16le", "-ac", "1", "-ar", "8000", "pipe:1"], input=audio,
            capture_output=True, timeout=15)
        if decoded.returncode or not decoded.stdout:
            raise ValueError("tts_audio_invalid")
        return "mp3", "audio/mpeg"
    raise ValueError("tts_media_type_unsupported")


class TTSAdapter:
    provider_id = "provider.akane.tts"

    def __init__(self, resources, connections):
        self.resources, self.connections = resources, connections
        self.clients = {}
        self.client_users = {}
        self.closed = False

    async def health(self):
        # Installation resolves dependency versions. Check presence here without
        # initializing the unused Edge HTTP stack for a GPT-only request.
        # The shared Edge client imports its backend on actual Edge invocation.
        if importlib.util.find_spec("edge_tts") is None:
            return HealthStatus(False, "unavailable", "edge_tts_not_installed")
        if not shutil.which("ffmpeg"):
            return HealthStatus(False, "unavailable", "tts_audio_decoder_unavailable")
        return HealthStatus(not self.closed, "ready" if not self.closed else "unavailable")

    async def list_capabilities(self):
        return (descriptor(),)

    def _profile_handles(self, value):
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if key == "refAudioHandle":
                    # The shared emotion resolver treats this field as opaque.
                    # It is converted to a scoped path only after selection.
                    result["refAudioPath"] = item
                else:
                    result[key] = self._profile_handles(item)
            return result
        if isinstance(value, list):
            return [self._profile_handles(item) for item in value]
        return value

    async def invoke(self, capability_id, args, context):
        if capability_id != CAPABILITY_ID:
            return CapabilityResult(is_error=True, status="not_found", reason="unknown_capability")
        client_key = None
        try:
            if self.closed:
                raise ValueError("tts_client_closed")
            provider = args["voice"]["provider"]
            if provider not in (EDGE, GPT):
                return CapabilityResult(is_error=True, status="unavailable", reason="requested_provider_unknown")
            connection = await self.connections.resolve("tts")
            if not connection.ok:
                return CapabilityResult(is_error=True, status=connection.status, reason=connection.reason)
            if connection.model != provider:
                raise ValueError("tts_connection_provider_mismatch")
            options = connection.options.get("client", {})
            key = json.dumps([context.profile_user_id, provider, connection.base_url, options], sort_keys=True)
            retired = None
            if key not in self.clients:
                if len(self.clients) >= 8:
                    idle = next((candidate for candidate in self.clients if not self.client_users.get(candidate)), None)
                    if idle is None:
                        return CapabilityResult(is_error=True, status="resource_exhausted",
                            reason="tts_connection_pool_capacity", content={"outcome_known": True})
                    retired = self.clients.pop(idle)
                    self.client_users.pop(idle, None)
                self.clients[key] = (GptSovitsTTSClient(connection.base_url, **options) if provider == GPT
                                     else EdgeTTSClient(**options))
            client_key = key
            self.client_users[key] = self.client_users.get(key, 0) + 1
            self.clients[key] = self.clients.pop(key)
            close = getattr(retired, "aclose", None)
            if close:
                await close()
            profile_source = connection.options.get("profile", {})
            emotion = resolve_emotion_voice_profile(self._profile_handles(profile_source), args.get("emotion", ""))
            profile = dict(emotion.profile)
            handle = profile.get("refAudioPath")
            if provider == GPT and handle:
                source = await self.resources.open(handle)
                if not source.ok or not 0 < source.file_size <= MAX_AUDIO:
                    raise ValueError("tts_reference_audio_unavailable")
                inspect_audio(source.path.read_bytes(), "audio/wav")
                profile["refAudioPath"] = str(source.path)
            adapter = OpenAICompatTTSAdapter(provider_id=provider, client=self.clients[key],
                default_media_type="audio/wav" if provider == GPT else "audio/mpeg")
            invocation = {"text": args["text"], "emotion": args.get("emotion", "")}
            if provider == GPT:
                invocation.update(profile=profile, voice_profile_id=args["voice"]["profile_id"])
            work = asyncio.create_task(adapter.invoke("tts.synthesize", invocation, context))
            cancelled = False
            while True:
                try:
                    result = await asyncio.shield(work)
                    break
                except asyncio.CancelledError:
                    # The HTTP client uses a worker thread. Cancelling its
                    # asyncio await does not prove that remote inference ended.
                    cancelled = True
            if cancelled:
                return CapabilityResult(is_error=True, status="cancelled", reason="invocation_cancelled",
                    content={"retryable": False, "outcome_known": not result.is_error})
            if result.is_error:
                return result
            content = result.content
            audio = content["audio"]
            output_format, media_type = inspect_audio(audio, content["mediaType"])
            value = {"provider_id": provider, "voice_profile_id": str(content.get("voiceProfileId") or ""),
                "emotion": emotion.requested_emotion, "emotion_voice_id": emotion.matched_emotion,
                "profile_fingerprint": emotion.profile_fingerprint, "media_type": media_type}
            return CapabilityResult(is_error=False, status="ok", content=ManagedArtifactPayload(
                artifacts=(ManagedArtifactDraft(data=audio, title="合成语音", output_format=output_format,
                    mime_type=media_type, summary="已合成音频，尚未确认播放。", send_to_user=False),),
                content=PluginResultPayload(content=value,
                    experience=PluginResultExperience(summary="语音合成完成，尚未确认播放。"))))
        except asyncio.CancelledError:
            raise
        except (ValueError, OSError, wave.Error) as exc:
            reason = str(exc)
            return CapabilityResult(is_error=True, status="error",
                reason=reason if reason.startswith("tts_") and len(reason) < 80 else "tts_audio_invalid")
        except Exception:
            # Provider exceptions may contain endpoints or private configuration.
            return CapabilityResult(is_error=True, status="error", reason="tts_synthesis_failed",
                content={"retryable": False, "outcome_known": False})
        finally:
            if client_key is not None:
                self.client_users[client_key] -= 1

    async def aclose(self):
        self.closed = True
        for client in self.clients.values():
            close = getattr(client, "aclose", None)
            if close:
                await close()
        self.clients.clear()
        self.client_users.clear()


class TTSPlugin:
    manifest = PluginManifest(PLUGIN_ID, "0.1.0", AKANE_PLUGIN_API_VERSION, PERMISSIONS)

    def register(self, registrar):
        registrar.add_capability_adapter(TTSAdapter(registrar.get_resource_port(), registrar.get_connection_port()))


def create_plugin():
    return TTSPlugin()
