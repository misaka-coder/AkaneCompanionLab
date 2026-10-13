"""Music search, remote-media acquisition and media-inspection tool contracts."""
from __future__ import annotations

from capcore import CapabilityToolSpec


OPEN_MUSIC_SEARCH_TOOL_SPEC = CapabilityToolSpec(
    capability_id="open_music_search",
    display_name="Open music search",
    description=(
        "Open a public music platform search page on the desktop browser for the user to find a song. "
        "Does not auto-play, log in, or download."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title": {"type": "string", "maxLength": 120, "description": "Song title to search for."},
            "artist": {"type": "string", "maxLength": 80, "description": "Optional artist name."},
            "platform": {"type": "string", "enum": ["qq_music", "netease_music", "bilibili", "youtube"], "description": "Music platform. Default qq_music."},
        },
        "required": ["title"],
    },
    risk="medium",
    confirm="first_time",
    effects=("browser_open",),
    visible_in=("desktop",),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=4096,
)
FETCH_MEDIA_FROM_URL_TOOL_SPEC = CapabilityToolSpec(
    capability_id="fetch_media_from_url",
    display_name="Fetch media from URL",
    description=(
        "Download a public video or audio URL into the current workspace so it can be inspected, "
        "transcribed, converted, or sent. Does not handle login-required, paid, or DRM-protected links."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "url": {"type": "string", "maxLength": 1600, "description": "Public media URL to download."},
            "urls": {"type": "array", "items": {"type": "string", "maxLength": 1600}, "maxItems": 5, "description": "Multiple public URLs for batch download."},
            "preferred_title": {"type": "string", "maxLength": 120, "description": "Optional title for the downloaded item."},
        },
        "required": [],
    },
    risk="medium",
    confirm="first_time",
    effects=("file_download",),
    visible_in=("desktop", "qq"),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="idempotent",
    max_result_bytes=8192,
)


INSPECT_MEDIA_INFO_TOOL_SPEC = CapabilityToolSpec(
    capability_id="inspect_media_info",
    display_name="Inspect media info",
    description="Read media specs (duration, codec, sample rate, channels, bitrate, resolution, fps) of an existing file. Read-only.",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "source_id": {"type": "string", "maxLength": 120, "description": "Handle of an existing media item, e.g. file_001 / audio_001 / gen_001."},
        },
        "required": ["source_id"],
    },
    risk="low",
    confirm="never",
    effects=(),
    visible_in=("desktop", "qq"),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="read_only",
    max_result_bytes=4096,
)
