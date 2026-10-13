"""Host-owned bilingual payload carried by the generic text artifact port."""
import json

from ..speech_units import normalize_unit_emotion

MEDIA_TYPE = "application/vnd.akane.speech-unit+json"


def encode_speech_artifact(event):
    emotion = normalize_unit_emotion(event.get("emotion"))
    return json.dumps({"text": str(event.get("display_text") or ""), "say": str(event["text"]),
                       "style": str(event.get("style") or ""), **({"emotion": emotion} if emotion else {})},
                      ensure_ascii=False, separators=(",", ":"))


def decode_speech_artifact(result):
    if getattr(result, "media_type", "") != MEDIA_TYPE:
        return {"text": result.text, "say": result.text, "style": "", "speech_protocol": ""}
    value = json.loads(result.text)
    if not isinstance(value, dict) or any(not isinstance(value.get(key), str) for key in ("text", "say", "style")):
        raise ValueError("voice_speech_artifact_invalid")
    emotion = normalize_unit_emotion(value.pop("emotion", None))
    if emotion:
        value["emotion"] = emotion
    return {**value, "speech_protocol": "speech_units_v1"}
