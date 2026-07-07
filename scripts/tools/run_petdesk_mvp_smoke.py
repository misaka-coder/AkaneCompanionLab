from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "http://127.0.0.1:9999"
DEFAULT_TEXT = "测试一下 petdesk MVP 语音链路。"
DEFAULT_TIMEOUT_SECONDS = 90.0
DEFAULT_MAX_AUDIO_BYTES = 5 * 1024 * 1024
SAFE_AUDIO_HANDLE_RE = re.compile(r"^akane/tts/[0-9a-f]{32}$")


class PetdeskSmokeError(RuntimeError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class SseEvent:
    event: str
    payload: Any
    raw_data: str


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a live Akane /pet MVP smoke: health, turn stream, and optional TTS audio fetch.",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Akane backend base URL.")
    parser.add_argument("--text", default=DEFAULT_TEXT, help="Turn text sent to /pet/turn.")
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Per-request timeout in seconds.",
    )
    parser.add_argument(
        "--max-audio-bytes",
        type=int,
        default=DEFAULT_MAX_AUDIO_BYTES,
        help="Reject audio responses larger than this many bytes.",
    )
    parser.add_argument(
        "--no-require-audio",
        action="store_true",
        help="Allow the smoke to pass without TTS audio. This is not full audio acceptance.",
    )
    return parser


def run_smoke(
    *,
    base_url: str,
    text: str,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    require_audio: bool = True,
    max_audio_bytes: int = DEFAULT_MAX_AUDIO_BYTES,
) -> dict[str, Any]:
    normalized_base_url = normalize_base_url(base_url)
    prompt = str(text or "").strip() or DEFAULT_TEXT
    health = fetch_json(normalized_base_url, "/pet/health", timeout_seconds=timeout_seconds)
    validate_health(health)

    turn_payload = {
        "text": prompt,
        "turnId": f"mvp_smoke_{int(time.time())}",
        "metadata": {
            "user_id": "petdesk_mvp_smoke",
            "real_user_id": "petdesk_mvp_smoke",
            "source": "petdesk_mvp_smoke",
        },
    }
    stream_text = post_json_text(
        normalized_base_url,
        "/pet/turn",
        turn_payload,
        timeout_seconds=timeout_seconds,
    )
    events = parse_sse_events(stream_text)
    event_order = [event.event for event in events]
    displays = [event.payload for event in events if event.event == "display" and isinstance(event.payload, dict)]
    manifests = [
        event.payload for event in events if event.event == "resource_manifest" and isinstance(event.payload, dict)
    ]
    if not manifests:
        raise PetdeskSmokeError("missing_resource_manifest", "The /pet/turn stream did not include resource_manifest.")
    if not displays:
        raise PetdeskSmokeError("missing_display", "The /pet/turn stream did not include display.")
    if "done" not in event_order:
        raise PetdeskSmokeError("missing_done", "The /pet/turn stream did not include done.")

    final_display = displays[-1]
    speech = str(final_display.get("speech") or "").strip()
    if not speech:
        raise PetdeskSmokeError("missing_speech", "The final display did not include visible speech.")

    audio_summary = resolve_audio_summary(
        base_url=normalized_base_url,
        final_display=final_display,
        manifests=manifests,
        timeout_seconds=timeout_seconds,
        require_audio=require_audio,
        max_audio_bytes=max_audio_bytes,
    )

    return {
        "status": "ok",
        "base_url": normalized_base_url,
        "bridge_version": str(health.get("version") or ""),
        "default_character_pack_id": str(health.get("defaultCharacterPackId") or ""),
        "character_pack_count": len(health.get("characterPacks") or []),
        "event_order": event_order,
        "display_count": len(displays),
        "resource_manifest_count": len(manifests),
        "speech_chars": len(speech),
        **audio_summary,
    }


def resolve_audio_summary(
    *,
    base_url: str,
    final_display: dict[str, Any],
    manifests: list[dict[str, Any]],
    timeout_seconds: float,
    require_audio: bool,
    max_audio_bytes: int,
) -> dict[str, Any]:
    audio_section = final_display.get("audio")
    audio_section = audio_section if isinstance(audio_section, dict) else {}
    tts = audio_section.get("tts")
    tts = tts if isinstance(tts, dict) else {}
    audio_handle = str(tts.get("audioHandle") or "").strip()

    if not audio_handle:
        if require_audio:
            raise PetdeskSmokeError("missing_audio_handle", "The final display did not include audio.tts.audioHandle.")
        return {
            "audio_required": False,
            "audio_present": False,
            "audio_handle": "",
            "audio_url": "",
            "audio_bytes": 0,
            "audio_content_type": "",
        }

    if not SAFE_AUDIO_HANDLE_RE.fullmatch(audio_handle):
        raise PetdeskSmokeError("unsafe_audio_handle", "The audio handle was not an Akane petdesk TTS handle.")

    audio_entry = find_audio_entry(manifests, audio_handle)
    audio_url = str(audio_entry.get("url") or "").strip()
    if not audio_url.startswith("/audio/petdesk/"):
        raise PetdeskSmokeError("unsafe_audio_url", "The audio manifest URL was not under /audio/petdesk/.")

    content, content_type = fetch_bytes(
        base_url,
        audio_url,
        timeout_seconds=timeout_seconds,
        max_bytes=max_audio_bytes,
    )
    if not content:
        raise PetdeskSmokeError("empty_audio", "The audio endpoint returned an empty body.")
    if not content_type.lower().split(";", 1)[0].startswith("audio/"):
        raise PetdeskSmokeError("invalid_audio_content_type", "The audio endpoint did not return audio/* content.")

    return {
        "audio_required": require_audio,
        "audio_present": True,
        "audio_handle": audio_handle,
        "audio_url": audio_url,
        "audio_bytes": len(content),
        "audio_content_type": content_type,
    }


def find_audio_entry(manifests: list[dict[str, Any]], audio_handle: str) -> dict[str, Any]:
    for manifest in reversed(manifests):
        audio_bucket = manifest.get("audio")
        if not isinstance(audio_bucket, dict):
            continue
        entry = audio_bucket.get(audio_handle)
        if isinstance(entry, dict):
            if entry.get("kind") != "audio" or entry.get("handle") != audio_handle:
                break
            return entry
    raise PetdeskSmokeError(
        "missing_audio_manifest_entry", "No resource_manifest audio entry matched the display handle."
    )


def validate_health(health: dict[str, Any]) -> None:
    if health.get("ok") is not True:
        raise PetdeskSmokeError("health_not_ok", "/pet/health did not return ok=true.")
    if str(health.get("status") or "") != "ready":
        raise PetdeskSmokeError("health_not_ready", "/pet/health did not report status=ready.")
    if health.get("snapshot") != "/pet/snapshot" or health.get("turn") != "/pet/turn":
        raise PetdeskSmokeError("health_contract_mismatch", "/pet/health endpoints did not match the petdesk bridge.")


def parse_sse_events(stream_text: str) -> list[SseEvent]:
    events: list[SseEvent] = []
    normalized = stream_text.replace("\r\n", "\n").replace("\r", "\n")
    for frame in normalized.split("\n\n"):
        frame = frame.strip("\n")
        if not frame:
            continue
        event_name = "message"
        data_lines: list[str] = []
        for line in frame.split("\n"):
            if not line or line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip() or "message"
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].lstrip())
        raw_data = "\n".join(data_lines)
        payload: Any = {}
        if raw_data:
            try:
                payload = json.loads(raw_data)
            except json.JSONDecodeError:
                payload = raw_data
        events.append(SseEvent(event=event_name, payload=payload, raw_data=raw_data))
    return events


def fetch_json(base_url: str, path: str, *, timeout_seconds: float) -> dict[str, Any]:
    body = request_bytes(
        build_url(base_url, path),
        timeout_seconds=timeout_seconds,
        headers={"Accept": "application/json", "Cache-Control": "no-store"},
    )[0]
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise PetdeskSmokeError("invalid_json_response", f"{path} did not return valid JSON.") from exc
    if not isinstance(payload, dict):
        raise PetdeskSmokeError("invalid_json_response", f"{path} did not return a JSON object.")
    return payload


def post_json_text(base_url: str, path: str, payload: dict[str, Any], *, timeout_seconds: float) -> str:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    content, _content_type = request_bytes(
        build_url(base_url, path),
        data=body,
        timeout_seconds=timeout_seconds,
        headers={
            "Accept": "text/event-stream",
            "Cache-Control": "no-store",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    return content.decode("utf-8", errors="replace")


def fetch_bytes(base_url: str, path: str, *, timeout_seconds: float, max_bytes: int) -> tuple[bytes, str]:
    return request_bytes(
        build_url(base_url, path),
        timeout_seconds=timeout_seconds,
        headers={"Accept": "audio/*", "Cache-Control": "no-store"},
        max_bytes=max_bytes,
    )


def request_bytes(
    url: str,
    *,
    data: bytes | None = None,
    method: str = "GET",
    timeout_seconds: float,
    headers: dict[str, str],
    max_bytes: int = DEFAULT_MAX_AUDIO_BYTES,
) -> tuple[bytes, str]:
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=max(1.0, timeout_seconds)) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > max_bytes:
                raise PetdeskSmokeError("response_too_large", f"Response from {url} exceeded max bytes.")
            content = response.read(max_bytes + 1)
            if len(content) > max_bytes:
                raise PetdeskSmokeError("response_too_large", f"Response from {url} exceeded max bytes.")
            return content, response.headers.get("Content-Type", "")
    except PetdeskSmokeError:
        raise
    except HTTPError as exc:
        raise PetdeskSmokeError("http_error", f"{url} returned HTTP {exc.code}.") from exc
    except (OSError, URLError) as exc:
        raise PetdeskSmokeError("request_failed", f"Request failed for {url}: {exc}") from exc


def normalize_base_url(raw: str) -> str:
    value = str(raw or "").strip().rstrip("/")
    if not value.startswith("http://") and not value.startswith("https://"):
        raise PetdeskSmokeError("invalid_base_url", "Base URL must start with http:// or https://.")
    return value


def build_url(base_url: str, path: str) -> str:
    return urljoin(f"{base_url}/", path.lstrip("/"))


def main() -> int:
    args = build_arg_parser().parse_args()
    try:
        summary = run_smoke(
            base_url=args.base_url,
            text=args.text,
            timeout_seconds=args.timeout_seconds,
            require_audio=not args.no_require_audio,
            max_audio_bytes=max(1, args.max_audio_bytes),
        )
    except PetdeskSmokeError as exc:
        print("AKANE_PETDESK_MVP_SMOKE_FAILED")
        print(json.dumps({"status": "error", "reason": exc.reason, "message": exc.message}, ensure_ascii=False))
        return 1

    print("AKANE_PETDESK_MVP_SMOKE_OK")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
