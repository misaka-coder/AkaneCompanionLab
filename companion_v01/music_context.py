"""MusicContext — unified "what we're listening to" snapshot for prompt use.

This is the v1 implementation of `MusicContext` described in
`docs/listening_together_demo_v1.md` §7. It consolidates the SMTC system
media path, the local Akane audio path, lyric confidence, and the
cross-source co-listen memory into a single read-only object that the
prompt layer consumes — instead of each injection point scraping
`desktop_activity` independently.

The assembler is intentionally tolerant: missing fields become `None` /
empty tuples / sensible defaults. It never raises for normal absence —
the goal is "she has nothing to add" not "she crashed".

This module only owns the *assembly* and the *co-listen memory* projection.
The existing lyric line and timeline prompt paths in
`desktop_context_engine.py` and `desktop_music_timeline.py` continue to
work; the listening-together demo adds a co-listen memory block alongside
them, it does not replace them.
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Mapping

from . import co_listen_store


# ---------------------------------------------------------------------------
# Enums & dataclasses
# ---------------------------------------------------------------------------


class MusicSource(str, Enum):
    QQ_MUSIC = "qq_music"
    NETEASE_MUSIC = "netease_music"
    SPOTIFY = "spotify"
    YOUTUBE_MUSIC = "youtube_music"
    APPLE_MUSIC = "apple_music"
    LOCAL_AKANE = "local_akane"
    SYSTEM_MEDIA_UNKNOWN = "system_media_unknown"
    EXTERNAL_UNKNOWN = "external_unknown"


class LyricConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


class ListeningPattern(str, Enum):
    REPEATING = "repeating"
    RANDOM = "random"
    PICKY = "picky"
    STEADY = "steady"
    UNKNOWN = "unknown"


class MusicControl(str, Enum):
    PLAY = "play"
    PAUSE = "pause"
    NEXT = "next"
    PREV = "prev"
    RECOMMEND = "recommend"


@dataclass(frozen=True)
class TrackIdentity:
    """Cross-source stable key for a track.

    v1 matching rule (`listening_together_demo_v1.md` §7.3): exact match on
    (title_normalized, artist_normalized). `album_hint` is only consulted
    when same-name songs collide; v2 may add fuzzy / fingerprint matching.
    """

    title_normalized: str
    artist_normalized: str
    album_hint: str | None = None

    @property
    def key(self) -> str:
        return "|".join(
            (
                self.title_normalized,
                self.artist_normalized,
                self.album_hint or "",
            )
        )


@dataclass(frozen=True)
class TrackInfo:
    title: str = ""
    artist: str = ""
    album: str = ""


@dataclass(frozen=True)
class LyricLine:
    text: str
    start_seconds: float | None = None
    end_seconds: float | None = None


@dataclass(frozen=True)
class MusicContext:
    is_playing: bool
    source: MusicSource
    track: TrackInfo | None
    progress_seconds: float | None
    duration_seconds: float | None

    lyric_window: tuple[LyricLine, ...]
    lyric_confidence: LyricConfidence

    track_identity: TrackIdentity | None
    co_listen_count: int
    last_listened_together: datetime | None
    recent_co_listened: tuple[TrackIdentity, ...]

    user_session_pattern: ListeningPattern
    current_loop_count: int

    enabled_music_controls: frozenset[MusicControl]
    control_session_writable: bool


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


_PAREN_RE = re.compile(r"[\(\[（【][^\)\]）】]*[\)\]）】]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_title(value: str) -> str:
    """Title normalization for cross-source identity matching.

    Folds case, normalizes width (NFKC), strips bracketed annotations such
    as "(Live)" / "(Remix)" that vary between platforms, and collapses
    whitespace.
    """

    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = _PAREN_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip().lower()
    return text


def normalize_artist(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        return ""
    # Split on common multi-artist separators, then re-join sorted so that
    # "A & B" and "B / A" hash to the same key.
    parts = [
        _WHITESPACE_RE.sub(" ", part).strip().lower()
        for part in re.split(r"[/,&、,;；]+", text)
        if part.strip()
    ]
    parts = [p for p in parts if p]
    if not parts:
        return ""
    return " & ".join(sorted(parts))


def normalize_album_hint(value: str) -> str | None:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        return None
    return _WHITESPACE_RE.sub(" ", text).lower()


def strip_artist_suffix(combined_title: str, artist: str) -> str:
    """Undo the frontend's `"<title> - <artist>"` join when re-deriving raw title."""
    title = str(combined_title or "").strip()
    artist = str(artist or "").strip()
    if not title or not artist:
        return title
    suffix = f" - {artist}"
    if title.endswith(suffix):
        return title[: -len(suffix)].strip()
    return title


# ---------------------------------------------------------------------------
# Source / lyric inference
# ---------------------------------------------------------------------------


_SOURCE_APP_HINTS: tuple[tuple[tuple[str, ...], MusicSource], ...] = (
    (("qqmusic", "qq_music", "qq music"), MusicSource.QQ_MUSIC),
    (("cloudmusic", "netease", "网易云"), MusicSource.NETEASE_MUSIC),
    (("spotify",), MusicSource.SPOTIFY),
    (("youtube music", "ytmdesktop", "youtubemusic"), MusicSource.YOUTUBE_MUSIC),
    (("apple music", "applemusic", "itunes"), MusicSource.APPLE_MUSIC),
)


def infer_music_source(
    *,
    source_kind: str,
    source_app: str,
    has_system_media: bool,
    is_local_akane: bool,
) -> MusicSource:
    if is_local_akane:
        return MusicSource.LOCAL_AKANE
    if not has_system_media:
        return MusicSource.EXTERNAL_UNKNOWN
    app = str(source_app or "").strip().lower()
    if app:
        for needles, source in _SOURCE_APP_HINTS:
            if any(needle in app for needle in needles):
                return source
    return MusicSource.SYSTEM_MEDIA_UNKNOWN


def infer_lyric_confidence(
    *,
    raw_confidence: str,
    lyric_status: str,
    lyric_current: str,
    lyric_next: str,
) -> LyricConfidence:
    """Map the frontend's `lyric_*` fields to a single confidence enum."""
    confidence = str(raw_confidence or "").strip().lower()
    if confidence in {"high", "medium", "low"}:
        return LyricConfidence(confidence)

    status = str(lyric_status or "").strip().lower()
    if status in {"low-confidence", "ambiguous", "ambiguous_match"}:
        return LyricConfidence.LOW
    if status in {"not-found", "disabled", "unavailable"}:
        return LyricConfidence.NONE

    has_any_line = bool(str(lyric_current or "").strip()) or bool(str(lyric_next or "").strip())
    if status == "ready" and has_any_line:
        return LyricConfidence.MEDIUM
    if has_any_line:
        return LyricConfidence.MEDIUM
    return LyricConfidence.NONE


def _to_lyric_lines(activity: Mapping[str, Any]) -> tuple[LyricLine, ...]:
    lines: list[LyricLine] = []
    prev_text = str(activity.get("lyric_previous") or activity.get("lyricPrevious") or "").strip()
    if prev_text:
        lines.append(LyricLine(text=prev_text[:200]))
    current_text = str(activity.get("lyric_current") or activity.get("lyricCurrent") or "").strip()
    if current_text:
        lines.append(LyricLine(text=current_text[:200]))
    next_text = str(activity.get("lyric_next") or activity.get("lyricNext") or "").strip()
    if next_text:
        lines.append(LyricLine(text=next_text[:200]))
    return tuple(lines)


# ---------------------------------------------------------------------------
# Assembler
# ---------------------------------------------------------------------------


def _coerce_optional_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result < 0:
        return None
    return result


_LOCAL_AKANE_SOURCE_KINDS = frozenset(
    {"local_file", "attachment", "generated", "workspace", "vocal_performance"}
)
_LOCAL_AKANE_HANDLE_PREFIXES = (
    "workspace:",
    "file:",
    "audio:",
    "gen:",
    "attachment:",
    "generated:",
)


def _is_local_akane_activity(activity: Mapping[str, Any]) -> bool:
    if activity.get("system_media") is True:
        return False
    source_kind = str(activity.get("source_kind") or activity.get("sourceKind") or "").strip().lower()
    if source_kind == "system_media":
        return False
    if source_kind in _LOCAL_AKANE_SOURCE_KINDS:
        return True
    handle = str(activity.get("handle") or "").strip().lower()
    if handle.startswith("system_media"):
        return False
    if handle.startswith(_LOCAL_AKANE_HANDLE_PREFIXES):
        return True
    return False


def _is_playing(activity: Mapping[str, Any]) -> bool:
    status = str(activity.get("status") or "").strip().lower()
    return status == "running"


class MusicContextAssembler:
    """Assemble a `MusicContext` from a desktop activity payload.

    The assembler is owned by the engine. It coordinates the activity
    payload (from the desktop frontend) with `co_listen_store` for the
    cross-source history fields.
    """

    def __init__(
        self,
        *,
        store: Any,
        controls_provider: Callable[[str], frozenset[MusicControl]] | None = None,
        min_listen_seconds: float = 30.0,
        repeat_cooldown_seconds: float = 300.0,
        recent_limit: int = 5,
    ) -> None:
        self.store = store
        self.controls_provider = controls_provider
        self.min_listen_seconds = float(min_listen_seconds)
        self.repeat_cooldown_seconds = float(repeat_cooldown_seconds)
        self.recent_limit = int(recent_limit)
        self._schema_ready = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def assemble(
        self,
        *,
        activity: Mapping[str, Any] | None,
        profile_user_id: str,
        enabled_controls: frozenset[MusicControl] | None = None,
        now_ts: int | None = None,
    ) -> MusicContext | None:
        if not isinstance(activity, Mapping):
            return None
        activity_type = str(activity.get("type") or "").strip().lower()
        if activity_type != "audio_playback":
            return None

        artist = str(activity.get("artist") or "").strip()
        album = str(activity.get("album") or "").strip()
        raw_title = strip_artist_suffix(
            str(activity.get("title") or "").strip(),
            artist,
        )
        if not raw_title and not artist:
            return None

        is_local_akane = _is_local_akane_activity(activity)
        has_system_media = bool(activity.get("system_media")) or str(
            activity.get("source_kind") or activity.get("sourceKind") or ""
        ).strip().lower() == "system_media"
        source = infer_music_source(
            source_kind=str(activity.get("source_kind") or activity.get("sourceKind") or ""),
            source_app=str(activity.get("source_app") or activity.get("sourceApp") or ""),
            has_system_media=has_system_media,
            is_local_akane=is_local_akane,
        )

        identity: TrackIdentity | None = None
        title_norm = normalize_title(raw_title)
        artist_norm = normalize_artist(artist)
        if title_norm or artist_norm:
            identity = TrackIdentity(
                title_normalized=title_norm,
                artist_normalized=artist_norm,
                album_hint=normalize_album_hint(album),
            )

        lyric_window = _to_lyric_lines(activity)
        lyric_confidence = infer_lyric_confidence(
            raw_confidence=str(activity.get("lyric_confidence") or activity.get("lyricConfidence") or ""),
            lyric_status=str(activity.get("lyric_status") or activity.get("lyricStatus") or ""),
            lyric_current=str(activity.get("lyric_current") or activity.get("lyricCurrent") or ""),
            lyric_next=str(activity.get("lyric_next") or activity.get("lyricNext") or ""),
        )
        # When confidence is low / none we strip the lyric text — the prompt
        # layer should not see lyric content we don't trust.
        if lyric_confidence in {LyricConfidence.LOW, LyricConfidence.NONE}:
            lyric_window = ()

        progress_seconds = _coerce_optional_float(activity.get("progress_seconds"))
        duration_seconds = _coerce_optional_float(activity.get("duration_seconds"))
        is_playing = _is_playing(activity)
        timestamp = int(now_ts if now_ts is not None else time.time())

        co_listen_count = 0
        last_listened_together: datetime | None = None
        recent_co_listened: tuple[TrackIdentity, ...] = ()
        if identity is not None and profile_user_id:
            summary = self._record_and_summarize(
                profile_user_id=profile_user_id,
                identity=identity,
                source=source,
                progress_seconds=progress_seconds or 0.0,
                is_playing=is_playing,
                display_title=raw_title,
                display_artist=artist,
                now_ts=timestamp,
            )
            if summary is not None:
                co_listen_count = summary.co_listen_count
                if summary.last_listened_at:
                    last_listened_together = datetime.fromtimestamp(
                        summary.last_listened_at, tz=timezone.utc
                    )
            recent_co_listened = self._fetch_recent_identities(
                profile_user_id=profile_user_id,
                exclude_identity_key=identity.key,
            )

        if enabled_controls is not None:
            controls = enabled_controls
        elif self.controls_provider is not None:
            controls = self.controls_provider(profile_user_id)
        else:
            controls = frozenset({
                MusicControl.PAUSE,
                MusicControl.NEXT,
                MusicControl.PREV,
                MusicControl.RECOMMEND,
            })
        control_session_writable = source != MusicSource.EXTERNAL_UNKNOWN and bool(activity.get("system_media") or is_local_akane)

        track = TrackInfo(title=raw_title, artist=artist, album=album)

        return MusicContext(
            is_playing=is_playing,
            source=source,
            track=track if (track.title or track.artist) else None,
            progress_seconds=progress_seconds,
            duration_seconds=duration_seconds,
            lyric_window=lyric_window,
            lyric_confidence=lyric_confidence,
            track_identity=identity,
            co_listen_count=co_listen_count,
            last_listened_together=last_listened_together,
            recent_co_listened=recent_co_listened,
            user_session_pattern=ListeningPattern.UNKNOWN,
            current_loop_count=0,
            enabled_music_controls=controls,
            control_session_writable=control_session_writable,
        )

    # ------------------------------------------------------------------
    # Co-listen persistence helpers
    # ------------------------------------------------------------------

    def _ensure_schema(self, connection: Any) -> None:
        if self._schema_ready:
            return
        co_listen_store.ensure_schema(connection)
        self._schema_ready = True

    def _record_and_summarize(
        self,
        *,
        profile_user_id: str,
        identity: TrackIdentity,
        source: MusicSource,
        progress_seconds: float,
        is_playing: bool,
        display_title: str,
        display_artist: str,
        now_ts: int,
    ) -> co_listen_store.CoListenSummary | None:
        store = self.store
        if store is None:
            return None
        connect = getattr(store, "_connect", None)
        if connect is None:
            return None
        try:
            with connect() as connection:
                self._ensure_schema(connection)
                if is_playing:
                    return co_listen_store.record_co_listen_event(
                        connection,
                        profile_user_id=profile_user_id,
                        identity_key=identity.key,
                        title_normalized=identity.title_normalized,
                        artist_normalized=identity.artist_normalized,
                        album_hint=identity.album_hint or "",
                        display_title=display_title,
                        display_artist=display_artist,
                        source=source.value,
                        progress_seconds=progress_seconds,
                        now_ts=now_ts,
                        min_listen_seconds=self.min_listen_seconds,
                        repeat_cooldown_seconds=self.repeat_cooldown_seconds,
                    )
                return co_listen_store.get_co_listen_summary(
                    connection,
                    profile_user_id=profile_user_id,
                    identity_key=identity.key,
                )
        except Exception:
            return None

    def _fetch_recent_identities(
        self,
        *,
        profile_user_id: str,
        exclude_identity_key: str,
    ) -> tuple[TrackIdentity, ...]:
        store = self.store
        if store is None:
            return ()
        connect = getattr(store, "_connect", None)
        if connect is None:
            return ()
        try:
            with connect() as connection:
                self._ensure_schema(connection)
                entries = co_listen_store.list_recent_co_listened(
                    connection,
                    profile_user_id=profile_user_id,
                    limit=self.recent_limit,
                    exclude_identity_key=exclude_identity_key,
                )
        except Exception:
            return ()
        result: list[TrackIdentity] = []
        for entry in entries:
            parts = entry.identity_key.split("|")
            title_norm = parts[0] if len(parts) > 0 else ""
            artist_norm = parts[1] if len(parts) > 1 else ""
            album_hint = parts[2] if len(parts) > 2 else ""
            result.append(
                TrackIdentity(
                    title_normalized=title_norm,
                    artist_normalized=artist_norm,
                    album_hint=album_hint or None,
                )
            )
        return tuple(result)


# ---------------------------------------------------------------------------
# Prompt projection
# ---------------------------------------------------------------------------


def _format_friendly_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "刚刚"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} 分钟前"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} 小时前"
    days = hours // 24
    if days < 7:
        return f"{days} 天前"
    weeks = days // 7
    if weeks < 5:
        return f"{weeks} 周前"
    months = days // 30
    if months < 12:
        return f"{months} 个月前"
    years = days // 365
    return f"{years} 年前"


def _display_for_identity(identity: TrackIdentity) -> str:
    title = identity.title_normalized.strip()
    artist = identity.artist_normalized.strip()
    if title and artist:
        return f"{title} - {artist}"
    return title or artist or "某首歌"


def summarize_listening_together(
    *,
    store: Any,
    profile_user_id: str,
    title: str,
    artist: str = "",
    album: str = "",
    source_kind: str = "",
    source_app: str = "",
    system_media: bool = False,
    recent_limit: int = 5,
    now_ts: int | None = None,
) -> dict[str, Any]:
    """Read-only projection for the control-center "我们的共听" card.

    Does NOT record a co-listen event — this is the UI read path, not the
    prompt-time write path. Returns a JSON-friendly dict with keys
    `ok` / `now` / `recent` so the route layer can pass it through.
    """

    profile_user_id = str(profile_user_id or "").strip()
    if not profile_user_id:
        return {"ok": False, "status": "missing_profile", "now": None, "recent": []}

    now_int = int(now_ts if now_ts is not None else time.time())
    raw_title = strip_artist_suffix(str(title or "").strip(), str(artist or "").strip())
    title_norm = normalize_title(raw_title)
    artist_norm = normalize_artist(str(artist or ""))
    album_hint = normalize_album_hint(str(album or ""))

    identity: TrackIdentity | None = None
    if title_norm or artist_norm:
        identity = TrackIdentity(
            title_normalized=title_norm,
            artist_normalized=artist_norm,
            album_hint=album_hint,
        )

    has_system_media = bool(system_media) or str(source_kind or "").strip().lower() == "system_media"
    fake_activity_for_source = {
        "system_media": has_system_media,
        "source_kind": source_kind,
        "source_app": source_app,
        "handle": "" if has_system_media else "workspace:placeholder",
    }
    is_local_akane = _is_local_akane_activity(fake_activity_for_source)
    source = infer_music_source(
        source_kind=source_kind,
        source_app=source_app,
        has_system_media=has_system_media,
        is_local_akane=is_local_akane,
    )

    now_block: dict[str, Any] | None = None
    recent_block: list[dict[str, Any]] = []

    connect = getattr(store, "_connect", None)
    if connect is None:
        return {"ok": False, "status": "store_unavailable", "now": None, "recent": []}

    try:
        with connect() as connection:
            co_listen_store.ensure_schema(connection)
            if identity is not None:
                summary = co_listen_store.get_co_listen_summary(
                    connection,
                    profile_user_id=profile_user_id,
                    identity_key=identity.key,
                )
                now_block = {
                    "title": raw_title,
                    "artist": str(artist or ""),
                    "album": str(album or ""),
                    "source": source.value,
                    "co_listen_count": summary.co_listen_count if summary else 0,
                    "last_listened_at": summary.last_listened_at if summary else 0,
                    "last_listened_label": _format_friendly_duration(
                        max(0, now_int - summary.last_listened_at)
                    )
                    if summary and summary.last_listened_at
                    else "",
                    "is_first_listen": summary is None or (summary.co_listen_count or 0) == 0,
                }

            entries = co_listen_store.list_recent_co_listened(
                connection,
                profile_user_id=profile_user_id,
                limit=max(1, int(recent_limit)),
                exclude_identity_key=identity.key if identity else "",
            )
            for entry in entries:
                recent_block.append(
                    {
                        "title": entry.display_title,
                        "artist": entry.display_artist,
                        "source": entry.last_source,
                        "co_listen_count": entry.co_listen_count,
                        "last_listened_at": entry.last_listened_at,
                        "last_listened_label": _format_friendly_duration(
                            max(0, now_int - entry.last_listened_at)
                        )
                        if entry.last_listened_at
                        else "",
                    }
                )
    except Exception as exc:
        return {
            "ok": False,
            "status": "query_failed",
            "reason": str(exc)[:200],
            "now": None,
            "recent": [],
        }

    return {
        "ok": True,
        "status": "ready",
        "now": now_block,
        "recent": recent_block,
    }


def build_co_listen_memory_prompt(
    context: MusicContext | None,
    *,
    now_ts: int | None = None,
) -> str:
    """Render the "我们的共听记忆" prompt block.

    Returns "" when there's nothing worth saying — first listen, missing
    identity, or all numbers zero. The block is meant to be *appended*
    after `build_desktop_activity_prompt`'s existing output, not to
    replace it.
    """

    if context is None or context.track_identity is None:
        return ""

    has_history = context.co_listen_count >= 1 or bool(context.recent_co_listened)
    if not has_history:
        return ""

    lines = ["【我们的共听记忆】"]

    if context.co_listen_count >= 2:
        suffix = ""
        if context.last_listened_together is not None:
            now = int(now_ts if now_ts is not None else time.time())
            delta = now - int(context.last_listened_together.timestamp())
            if delta > 0:
                suffix = f"，上次是{_format_friendly_duration(delta)}"
        lines.append(
            f"- 这首歌你们已经一起听过 {context.co_listen_count} 次{suffix}。"
        )
        lines.append(
            "- 不要罗列次数；如果合适，可以自然地接一句\"又是这首啊\"或者带点共感。"
        )
    elif context.co_listen_count == 1:
        if context.last_listened_together is not None:
            now = int(now_ts if now_ts is not None else time.time())
            delta = now - int(context.last_listened_together.timestamp())
            if delta > 600:
                lines.append(f"- 这首歌之前一起听过一次（{_format_friendly_duration(delta)}）。")
            else:
                lines.append("- 这首歌你们刚刚一起听到过。")
        else:
            lines.append("- 这首歌之前一起听过一次。")

    recent = [item for item in context.recent_co_listened if item is not None][:3]
    if recent:
        snippets = [_display_for_identity(item) for item in recent if (item.title_normalized or item.artist_normalized)]
        snippets = [s for s in snippets if s and s != "某首歌"]
        if snippets:
            lines.append("- 最近也一起听过：" + " / ".join(snippets) + "。")
            lines.append("- 上面这些只是底牌，不要每首都念出来；只在自然的时候用一首做钩子。")

    if context.source == MusicSource.EXTERNAL_UNKNOWN:
        lines.append("- 当前播放器你看不清楚，不要在 speech 里说出来源名。")

    _all_known_controls = frozenset({
        MusicControl.PAUSE, MusicControl.NEXT,
        MusicControl.PREV, MusicControl.RECOMMEND,
    })
    disabled = _all_known_controls - context.enabled_music_controls
    if disabled:
        lines.append("【她现在能做什么】")
        if MusicControl.PAUSE in disabled:
            lines.append("- 主人这会儿没让你主动暂停；想停的话先问一句，等他点了再动。")
        if MusicControl.NEXT in disabled:
            lines.append("- 主人这会儿没让你主动切下一首；想换的话先用气泡提议，等他同意再切。")
        if MusicControl.PREV in disabled:
            lines.append("- 主人这会儿没让你回到上一首；想回去的话先问一句。")
        if MusicControl.RECOMMEND in disabled:
            lines.append("- 主人这会儿没让你主动推歌；除非他先问，否则别甩推荐。")

    if MusicControl.RECOMMEND in context.enabled_music_controls:
        lines.append("如果你有想法，可以主动提议推荐或换歌。")

    return "\n".join(lines)
