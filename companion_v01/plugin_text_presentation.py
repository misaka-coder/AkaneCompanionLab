from __future__ import annotations

import json
from typing import Any, Iterable


_LEADING_ADDRESS_SEPARATORS = " \t\r\n,，:：;；!！?？~～、。…—-"


def strip_leading_addresses(text: object, addresses: Iterable[object]) -> str:
    """Remove one plugin-declared direct address from the start of text."""

    original = str(text or "")
    value = original.lstrip()
    candidates = sorted(
        {
            str(item or "").strip()
            for item in addresses
            if str(item or "").strip()
        },
        key=len,
        reverse=True,
    )
    for address in candidates:
        if value.startswith(address):
            return value[len(address) :].lstrip(_LEADING_ADDRESS_SEPARATORS)
    return original


def apply_plugin_text_presentation_policy(
    frame: dict[str, Any],
    *,
    strip_leading_addresses_from: Iterable[object],
) -> dict[str, Any]:
    """Apply a bounded plugin-requested policy to final user-visible speech."""

    if isinstance(strip_leading_addresses_from, (str, bytes)):
        return frame
    addresses = tuple(strip_leading_addresses_from)
    if not addresses:
        return frame

    if "speech" in frame:
        frame["speech"] = strip_leading_addresses(frame.get("speech"), addresses)

    raw_segments = frame.get("speech_segments")
    if isinstance(raw_segments, (list, tuple)):
        segments = [str(item or "").strip() for item in raw_segments]
        for index, segment in enumerate(segments):
            if not segment:
                continue
            segments[index] = strip_leading_addresses(segment, addresses)
            break
        frame["speech_segments"] = [segment for segment in segments if segment]

    provider_output_raw = frame.get("_provider_output_raw")
    if isinstance(provider_output_raw, str) and provider_output_raw.strip():
        try:
            provider_output = json.loads(provider_output_raw)
        except (TypeError, ValueError):
            provider_output = None
        if isinstance(provider_output, dict):
            apply_plugin_text_presentation_policy(
                provider_output,
                strip_leading_addresses_from=addresses,
            )
            frame["_provider_output_raw"] = json.dumps(
                provider_output,
                ensure_ascii=False,
                separators=(",", ":"),
            )
    return frame


__all__ = ["apply_plugin_text_presentation_policy", "strip_leading_addresses"]
