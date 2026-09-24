from __future__ import annotations

import contextlib
import hashlib
import importlib
import importlib.util
import io
import json
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping


LYRICS_PROVIDER_ID = "provider.music.lyrics.online"
LYRICS_PROVIDER_SOURCE = "syncedlyrics"
LYRICS_CACHE_SCHEMA_VERSION = 1
LYRICS_CACHE_PATH_TEMPLATE = "users_data/<profile_user_id>/music/lyrics_cache/"
PROFILE_ID_SAFE_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
DEFAULT_ONLINE_LYRICS_PROVIDERS = ("Lrclib", "NetEase", "Musixmatch", "Megalobiz")
MAX_LRC_SEGMENTS = 1200
MAX_LRC_LINE_TEXT = 160

LRC_TIMESTAMP_RE = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
LRC_METADATA_ONLY_RE = re.compile(r"^\[(?:ar|ti|al|by|offset|length|re|ve|tool|la|language):[^\]]*\]$", re.IGNORECASE)
LRC_METADATA_TAG_RE = re.compile(r"\[(?:ar|ti|al|by|offset|length|re|ve|tool|la|language):[^\]]*\]", re.IGNORECASE)
LRC_WORD_TIMESTAMP_RE = re.compile(r"<\d{1,3}:\d{2}(?:[.:]\d{1,3})?>")

LyricsSearchFunc = Callable[[str, list[str]], str | None]


def parse_lrc_segments(
    value: Any,
    *,
    max_segments: int = MAX_LRC_SEGMENTS,
    line_text_limit: int = MAX_LRC_LINE_TEXT,
    default_line_duration: float = 5.0,
) -> list[dict[str, Any]]:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return []

    raw_segments: list[dict[str, Any]] = []
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line or LRC_METADATA_ONLY_RE.match(line):
            continue
        matches = list(LRC_TIMESTAMP_RE.finditer(line))
        if not matches:
            continue
        lyric_text = LRC_TIMESTAMP_RE.sub("", line)
        lyric_text = LRC_METADATA_TAG_RE.sub("", lyric_text)
        lyric_text = LRC_WORD_TIMESTAMP_RE.sub("", lyric_text)
        lyric_text = _safe_text(lyric_text, line_text_limit)
        if not lyric_text:
            continue
        for match in matches:
            start = _timestamp_match_seconds(match)
            if start is None:
                continue
            raw_segments.append({"start": start, "text": lyric_text})

    raw_segments.sort(key=lambda item: (float(item["start"]), str(item["text"])))
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[float, str]] = set()
    for segment in raw_segments:
        start = round(float(segment["start"]), 3)
        text_value = str(segment["text"] or "")
        key = (start, text_value)
        if key in seen:
            continue
        seen.add(key)
        deduped.append({"start": start, "text": text_value})
        if len(deduped) >= max(1, int(max_segments or MAX_LRC_SEGMENTS)):
            break

    segments: list[dict[str, Any]] = []
    for index, segment in enumerate(deduped):
        start = float(segment["start"])
        next_start = None
        for next_segment in deduped[index + 1 :]:
            candidate = float(next_segment["start"])
            if candidate > start:
                next_start = candidate
                break
        end = next_start if next_start is not None else start + max(0.5, float(default_line_duration or 5.0))
        segments.append(
            {
                "start": round(max(0.0, start), 3),
                "end": round(max(start, end), 3),
                "text": str(segment["text"] or ""),
            }
        )
    return segments


def build_music_lyrics_provider_status(config_module: Any = None) -> dict[str, Any]:
    enabled = online_lyrics_enabled(config_module)
    dependency_available = syncedlyrics_available()
    if not enabled:
        status = "disabled"
        reason = "network_lyrics_disabled"
    elif not dependency_available:
        status = "unavailable"
        reason = "syncedlyrics_missing"
    else:
        status = "ready"
        reason = ""
    return {
        "enabled": bool(enabled and dependency_available),
        "status": status,
        "reason": reason,
        "providers": online_lyrics_provider_names(config_module),
    }


def online_lyrics_enabled(config_module: Any = None) -> bool:
    return bool(getattr(config_module, "MUSIC_ONLINE_LYRICS_ENABLED", True))


def online_lyrics_provider_names(config_module: Any = None) -> list[str]:
    raw = getattr(config_module, "MUSIC_ONLINE_LYRICS_PROVIDERS", "")
    if isinstance(raw, str) and raw.strip():
        providers = [item.strip() for item in re.split(r"[,;\s]+", raw) if item.strip()]
    elif isinstance(raw, (list, tuple)):
        providers = [str(item or "").strip() for item in raw if str(item or "").strip()]
    else:
        providers = list(DEFAULT_ONLINE_LYRICS_PROVIDERS)
    return providers[:8]


def syncedlyrics_available() -> bool:
    importlib.invalidate_caches()
    return importlib.util.find_spec("syncedlyrics") is not None


class OnlineLyricsService:
    def __init__(
        self,
        *,
        base_dir: Path | str | None,
        config_module: Any = None,
        search_func: LyricsSearchFunc | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.base_dir = Path(base_dir).resolve() if base_dir is not None else None
        self.config_module = config_module
        self.search_func = search_func
        self.clock = clock or time.time

    def resolve_lyrics(self, *, profile_user_id: str, payload: Mapping[str, Any] | None) -> dict[str, Any]:
        request = payload if isinstance(payload, Mapping) else {}
        track_key = _safe_text(request.get("trackKey") or request.get("track_key"), 220)
        title = _safe_text(request.get("title"), 160)
        artist = _safe_text(request.get("artist"), 160)
        album = _safe_text(request.get("album"), 160)
        title, artist = _derive_title_artist(title=title, artist=artist)
        lookup_title = _normalize_title_for_search(title) or title
        lookup_artist = _normalize_artist_for_search(artist) or artist
        source = _safe_text(request.get("source"), 40) or "system_media"
        provider_names = online_lyrics_provider_names(self.config_module)

        if not online_lyrics_enabled(self.config_module):
            return self._failure(
                status="disabled",
                reason="network_lyrics_disabled",
                track_key=track_key,
                provider_names=provider_names,
            )
        if not title:
            return self._failure(
                status="invalid_request",
                reason="track_title_required",
                track_key=track_key,
                provider_names=provider_names,
            )

        confidence = _estimate_track_confidence(title=lookup_title, artist=lookup_artist, album=album)
        cache_path = self._cache_path(
            profile_user_id=profile_user_id,
            title=lookup_title,
            artist=lookup_artist,
            album=album,
            provider=LYRICS_PROVIDER_SOURCE,
        )
        cached = self._read_cache(cache_path)
        if cached:
            return self._public_cache_result(cached, track_key=track_key, cache_hit=True)

        if confidence == "low":
            result = self._failure(
                status="low-confidence",
                reason="ambiguous_track_metadata",
                track_key=track_key,
                provider_names=provider_names,
                confidence=confidence,
            )
            self._write_cache(cache_path, self._cache_payload(result, title=title, artist=artist, album=album, source=source))
            return result

        if self.search_func is None and not syncedlyrics_available():
            return self._failure(
                status="unavailable",
                reason="syncedlyrics_missing",
                track_key=track_key,
                provider_names=provider_names,
                confidence=confidence,
            )

        try:
            lrc_text = self._search_online_lrc(_search_query(title=title, artist=artist), provider_names)
        except Exception:
            return self._failure(
                status="unavailable",
                reason="lyrics_provider_failed",
                track_key=track_key,
                provider_names=provider_names,
                confidence=confidence,
            )

        segments = parse_lrc_segments(lrc_text)
        if not segments:
            result = self._failure(
                status="not-found",
                reason="lyrics_not_found",
                track_key=track_key,
                provider_names=provider_names,
                confidence=confidence,
            )
            self._write_cache(cache_path, self._cache_payload(result, title=title, artist=artist, album=album, source=source))
            return result
        if len(segments) < 2:
            result = self._failure(
                status="low-confidence",
                reason="insufficient_synced_lines",
                track_key=track_key,
                provider_names=provider_names,
                confidence=confidence,
            )
            self._write_cache(cache_path, self._cache_payload(result, title=title, artist=artist, album=album, source=source))
            return result

        now = int(self.clock())
        result = {
            "ok": True,
            "status": "ready",
            "reason": "",
            "trackKey": track_key,
            "source": LYRICS_PROVIDER_SOURCE,
            "provider": LYRICS_PROVIDER_SOURCE,
            "confidence": confidence,
            "segments": segments,
            "lineCount": len(segments),
            "cached": False,
            "updatedAt": now,
            "providers": provider_names,
        }
        self._write_cache(cache_path, self._cache_payload(result, title=title, artist=artist, album=album, source=source))
        return result

    def _search_online_lrc(self, query: str, provider_names: list[str]) -> str | None:
        if self.search_func is not None:
            return self.search_func(query, provider_names)

        module = importlib.import_module("syncedlyrics")
        search = getattr(module, "search", None)
        if not callable(search):
            raise RuntimeError("syncedlyrics_search_missing")
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            try:
                return search(query, synced_only=True, providers=provider_names)
            except TypeError:
                try:
                    return search(query, allow_plain_format=False, providers=provider_names)
                except TypeError:
                    return search(query, providers=provider_names)

    def _cache_path(
        self,
        *,
        profile_user_id: str,
        title: str,
        artist: str,
        album: str,
        provider: str,
    ) -> Path | None:
        if self.base_dir is None:
            return None
        root = self.base_dir
        profile = _safe_profile_id(profile_user_id)
        key = _cache_key(title=title, artist=artist, album=album, provider=provider)
        path = (root / profile / "music" / "lyrics_cache" / f"{key}.json").resolve()
        if root not in path.parents:
            return None
        return path

    def _read_cache(self, path: Path | None) -> dict[str, Any] | None:
        if path is None or not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(data, dict) or int(data.get("schemaVersion") or 0) != LYRICS_CACHE_SCHEMA_VERSION:
            return None
        status = str(data.get("status") or "").strip()
        if status not in {"ready", "not-found", "low-confidence"}:
            return None
        return data

    def _write_cache(self, path: Path | None, payload: dict[str, Any]) -> None:
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as handle:
                tmp_path = Path(handle.name)
                handle.write(data)
                handle.write("\n")
            tmp_path.replace(path)
        except Exception:
            return

    def _cache_payload(
        self,
        result: dict[str, Any],
        *,
        title: str,
        artist: str,
        album: str,
        source: str,
    ) -> dict[str, Any]:
        now = int(self.clock())
        return {
            "schemaVersion": LYRICS_CACHE_SCHEMA_VERSION,
            "trackKey": str(result.get("trackKey") or "")[:220],
            "title": title,
            "artist": artist,
            "album": album,
            "requestSource": source,
            "provider": LYRICS_PROVIDER_SOURCE,
            "status": str(result.get("status") or ""),
            "reason": str(result.get("reason") or "")[:120],
            "confidence": str(result.get("confidence") or ""),
            "segments": list(result.get("segments") or [])[:MAX_LRC_SEGMENTS],
            "lineCount": int(result.get("lineCount") or 0),
            "createdAt": now,
            "updatedAt": now,
        }

    def _public_cache_result(self, cache_payload: dict[str, Any], *, track_key: str, cache_hit: bool) -> dict[str, Any]:
        status = str(cache_payload.get("status") or "not-found")
        segments = list(cache_payload.get("segments") or []) if status == "ready" else []
        return {
            "ok": status == "ready",
            "status": status,
            "reason": str(cache_payload.get("reason") or "")[:120],
            "trackKey": track_key,
            "source": LYRICS_PROVIDER_SOURCE,
            "provider": LYRICS_PROVIDER_SOURCE,
            "confidence": str(cache_payload.get("confidence") or ""),
            "segments": segments[:MAX_LRC_SEGMENTS],
            "lineCount": len(segments) if segments else int(cache_payload.get("lineCount") or 0),
            "cached": bool(cache_hit),
        }

    def _failure(
        self,
        *,
        status: str,
        reason: str,
        track_key: str,
        provider_names: list[str],
        confidence: str = "",
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "status": status,
            "reason": reason,
            "trackKey": track_key,
            "source": LYRICS_PROVIDER_SOURCE,
            "provider": LYRICS_PROVIDER_SOURCE,
            "confidence": confidence,
            "segments": [],
            "lineCount": 0,
            "cached": False,
            "providers": provider_names,
        }


def _timestamp_match_seconds(match: re.Match[str]) -> float | None:
    try:
        minutes = int(match.group(1) or 0)
        seconds = int(match.group(2) or 0)
        if seconds > 59:
            return None
        fraction = str(match.group(3) or "")
        millis = int(fraction.ljust(3, "0")[:3]) if fraction else 0
        return minutes * 60 + seconds + millis / 1000
    except Exception:
        return None


def _safe_text(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    if limit > 0 and len(text) > limit:
        return text[:limit]
    return text


def _safe_profile_id(profile_user_id: str) -> str:
    raw = str(profile_user_id or "").strip() or "default"
    safe = "".join(ch if ch in PROFILE_ID_SAFE_CHARS else "_" for ch in raw)
    safe = safe.strip("._-") or "default"
    return safe[:120]


def _normalized_cache_part(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def _cache_key(*, title: str, artist: str, album: str, provider: str) -> str:
    material = "\n".join(
        [
            _normalized_cache_part(title),
            _normalized_cache_part(artist),
            _normalized_cache_part(album),
            _normalized_cache_part(provider),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _estimate_track_confidence(*, title: str, artist: str, album: str) -> str:
    if title and artist and album:
        return "high"
    if title and artist:
        return "medium"
    return "low"


def _search_query(*, title: str, artist: str) -> str:
    parts = [_normalize_title_for_search(title), _normalize_artist_for_search(artist)]
    return " ".join(part for part in parts if part).strip()


def _normalize_title_for_search(value: str) -> str:
    return _safe_text(_strip_title_suffix_noise(value), 160)


def _normalize_artist_for_search(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"\([^)]*\)|（[^）]*）|\[[^\]]*\]|【[^】]*】", " ", text)
    text = re.sub(r"\s*(?:/|／|,|，|;|；|&|＆|\+|、)\s*", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return _safe_text(text, 160)


def _derive_title_artist(*, title: str, artist: str) -> tuple[str, str]:
    if artist or not title:
        return title, artist
    normalized = title.strip()
    separators = (" - ", " – ", " — ", " | ", " / ", "／")
    for separator in separators:
        if separator not in normalized:
            continue
        left, right = normalized.split(separator, 1)
        left = _safe_text(left, 160)
        right = _safe_text(_strip_title_suffix_noise(right), 160)
        if left and right and 1 <= len(right) <= 80:
            return left, right
    return title, artist


def _strip_title_suffix_noise(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s*[-–—|/／]\s*(qq音乐|spotify|youtube|网易云音乐|酷狗音乐|酷我音乐)\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*\([^)]*(official|mv|audio|lyrics?|歌词|完整版)[^)]*\)\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*（[^）]*(official|mv|audio|lyrics?|歌词|完整版)[^）]*）\s*$", "", text, flags=re.IGNORECASE)
    return text.strip()
