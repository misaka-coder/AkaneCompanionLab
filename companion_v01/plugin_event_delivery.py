"""Delivery-only compatibility for historical notification decision labels.

The supported model silence protocol remains an explicit empty ``speech``.
This guard prevents old assistant examples still in a durable timeline from
turning an explicit non-delivery label into a notification. It is not a content
classifier, does not edit memory, and never runs for ordinary user replies.
"""

from __future__ import annotations

import re
from typing import Any


_LEGACY_NON_DELIVERY_LABEL = re.compile(
    r"^(?:【\s*(?:暂(?:时)?|本次)?不(?:作为[^，。；\n】]{1,12})?(?:推送|发送|播报|通知)\s*】"
    r"|\[\s*(?:暂(?:时)?|本次)?不(?:作为[^，。；\n\]]{1,12})?(?:推送|发送|播报|通知)\s*\])"
)


def plugin_notification_text_suppression(frame: dict[str, Any], turn_payload: dict[str, Any]) -> str:
    if (
        turn_payload.get("turn_kind") != "plugin_event"
        or str(turn_payload.get("client_mode") or frame.get("client_mode") or "") != "qq_text"
        or turn_payload.get("plugin_text_delivery") != "single_message"
        or not isinstance(turn_payload.get("plugin_external_event"), dict)
        or not turn_payload["plugin_external_event"].get("event_type")
        or frame.get("_transient_final_failure")
    ):
        return ""
    speech = frame.get("speech")
    if not isinstance(speech, str):
        return ""
    text = speech.strip()
    prefix = str(turn_payload.get("plugin_text_prefix") or "").strip()
    if prefix and text.startswith(prefix):
        text = text[len(prefix) :].lstrip()
    return "legacy_notification_silence" if _LEGACY_NON_DELIVERY_LABEL.match(text) else ""


def apply_plugin_notification_output_policy(frame: dict[str, Any], turn_payload: dict[str, Any]) -> dict[str, Any]:
    reason = plugin_notification_text_suppression(frame, turn_payload)
    if reason:
        # A host suppression is NOT a model-authored empty response. Keep the
        # raw provider envelope untouched, and finalize without an assistant
        # message. The engine records a typed non-delivery receipt instead.
        frame["_notification_suppressed"] = reason
        frame["speech"] = ""
        frame["speech_segments"] = []
    return frame
