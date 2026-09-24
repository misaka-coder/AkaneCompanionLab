import hashlib
import io
import math
import struct
import wave

from akane_plugin import (
    Plugin, ServiceContext, CapabilityIOSlot, CapabilityResult,
    ManagedArtifactDraft, ManagedArtifactPayload, PluginResultPayload, PluginResultExperience,
)

plugin = Plugin("example.tts-tone", permissions=("resource.read", "artifact.write"))
metadata_keys = ("provider_id", "voice_profile_id", "emotion", "emotion_voice_id", "profile_fingerprint", "media_type")


@plugin.service("tts", version=1).method(
    risk="medium", confirm="first_time", effects=("filesystem",),
    outputs=(CapabilityIOSlot("audio", "file", required=True, max_bytes=1024 * 1024, delivery="generated_file"),),
    output_schema={"type": "object", "required": list(metadata_keys), "additionalProperties": False,
                   "properties": {key: {"type": "string"} for key in metadata_keys}},
)
async def synthesize(text: str, voice: dict[str, str], emotion: str, ctx: ServiceContext):
    if not text or set(voice) != {"provider", "profile_id"}:
        return CapabilityResult(is_error=True, status="rejected", reason="tts_voice_request_invalid")
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        output.writeframes(b"".join(struct.pack("<h", int(4000 * math.sin(2 * math.pi * 440 * i / 24000)))
                                   for i in range(4800)))
    audio = stream.getvalue()
    root = await ctx.resources.work_directory()
    path = root / "tone.wav"
    path.write_bytes(audio)
    return CapabilityResult(is_error=False, status="ok", content=ManagedArtifactPayload(
        artifacts=(ManagedArtifactDraft(path=path, title="TTS 测试音", output_format="wav", mime_type="audio/wav",
            summary="用于替换测试的提示音，不是语音。", send_to_user=False),),
        content=PluginResultPayload(content={"provider_id": voice["provider"], "voice_profile_id": voice["profile_id"],
            "emotion": emotion, "emotion_voice_id": "", "profile_fingerprint": hashlib.sha256(audio).hexdigest(),
            "media_type": "audio/wav"}, experience=PluginResultExperience(summary="测试音已生成，未播放。"))))


def create_plugin():
    return plugin
