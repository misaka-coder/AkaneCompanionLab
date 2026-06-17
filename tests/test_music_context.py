"""Tests for the listening-together MusicContext assembler + co-listen store."""

from __future__ import annotations

import sqlite3
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone

from companion_v01 import co_listen_store
from companion_v01.music_context import (
    LyricConfidence,
    ListeningPattern,
    MusicContext,
    MusicContextAssembler,
    MusicControl,
    MusicSource,
    TrackIdentity,
    build_co_listen_memory_prompt,
    infer_music_source,
    normalize_artist,
    normalize_title,
    strip_artist_suffix,
)


# ---------------------------------------------------------------------------
# Test fakes
# ---------------------------------------------------------------------------


class _InMemoryStore:
    """Minimum surface area required by `MusicContextAssembler`."""

    def __init__(self) -> None:
        self._connection = sqlite3.connect(":memory:")
        self._connection.row_factory = sqlite3.Row

    @contextmanager
    def _connect(self):
        try:
            yield self._connection
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def close(self) -> None:
        self._connection.close()


def _system_media_activity(**overrides):
    base = {
        "type": "audio_playback",
        "title": "晴天 - 周杰伦",
        "artist": "周杰伦",
        "album": "叶惠美",
        "source_id": "system_media:qqmusic::晴天::周杰伦::叶惠美",
        "handle": "system_media_current",
        "status": "running",
        "progress_seconds": 65.0,
        "duration_seconds": 269.0,
        "source_kind": "system_media",
        "source_app": "QQMusic.exe",
        "system_media": True,
        "lyric_status": "ready",
        "lyric_confidence": "medium",
        "lyric_current": "故事的小黄花",
        "lyric_previous": "",
        "lyric_next": "从出生那年就飘着",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


class NormalizationTests(unittest.TestCase):
    def test_strip_artist_suffix_removes_join(self) -> None:
        self.assertEqual(strip_artist_suffix("晴天 - 周杰伦", "周杰伦"), "晴天")

    def test_strip_artist_suffix_leaves_unrelated_titles(self) -> None:
        self.assertEqual(strip_artist_suffix("晴天", "周杰伦"), "晴天")
        self.assertEqual(strip_artist_suffix("Other - Person", "周杰伦"), "Other - Person")

    def test_normalize_title_folds_case_and_brackets(self) -> None:
        self.assertEqual(normalize_title("Sunny Day (Live)"), "sunny day")
        self.assertEqual(normalize_title("晴天(Remix)"), "晴天")
        self.assertEqual(normalize_title("  HELLO   WORLD  "), "hello world")

    def test_normalize_artist_sorts_multi(self) -> None:
        self.assertEqual(normalize_artist("A & B"), normalize_artist("B / A"))
        self.assertEqual(normalize_artist("周杰伦"), "周杰伦")

    def test_infer_music_source_qq_music(self) -> None:
        source = infer_music_source(
            source_kind="system_media",
            source_app="QQMusic.exe",
            has_system_media=True,
            is_local_akane=False,
        )
        self.assertEqual(source, MusicSource.QQ_MUSIC)

    def test_infer_music_source_netease(self) -> None:
        source = infer_music_source(
            source_kind="system_media",
            source_app="cloudmusic.exe",
            has_system_media=True,
            is_local_akane=False,
        )
        self.assertEqual(source, MusicSource.NETEASE_MUSIC)

    def test_infer_music_source_falls_back_to_system_media_unknown(self) -> None:
        source = infer_music_source(
            source_kind="system_media",
            source_app="WeirdPlayer.exe",
            has_system_media=True,
            is_local_akane=False,
        )
        self.assertEqual(source, MusicSource.SYSTEM_MEDIA_UNKNOWN)

    def test_infer_music_source_local_akane(self) -> None:
        source = infer_music_source(
            source_kind="local_file",
            source_app="",
            has_system_media=False,
            is_local_akane=True,
        )
        self.assertEqual(source, MusicSource.LOCAL_AKANE)


# ---------------------------------------------------------------------------
# Assembler
# ---------------------------------------------------------------------------


class AssemblerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _InMemoryStore()
        self.assembler = MusicContextAssembler(store=self.store)
        self.addCleanup(self.store.close)

    def test_assemble_skips_non_audio_activity(self) -> None:
        result = self.assembler.assemble(
            activity={"type": "vision_observation"},
            profile_user_id="alice",
        )
        self.assertIsNone(result)

    def test_assemble_skips_none(self) -> None:
        self.assertIsNone(self.assembler.assemble(activity=None, profile_user_id="alice"))

    def test_assemble_yields_basic_fields(self) -> None:
        ctx = self.assembler.assemble(
            activity=_system_media_activity(),
            profile_user_id="alice",
            now_ts=1_700_000_000,
        )
        self.assertIsNotNone(ctx)
        assert ctx is not None
        self.assertTrue(ctx.is_playing)
        self.assertEqual(ctx.source, MusicSource.QQ_MUSIC)
        self.assertIsNotNone(ctx.track)
        assert ctx.track is not None
        self.assertEqual(ctx.track.title, "晴天")
        self.assertEqual(ctx.track.artist, "周杰伦")
        self.assertEqual(ctx.track.album, "叶惠美")
        self.assertEqual(ctx.progress_seconds, 65.0)
        self.assertEqual(ctx.lyric_confidence, LyricConfidence.MEDIUM)
        self.assertGreaterEqual(len(ctx.lyric_window), 1)
        # Defaults that v1 does not infer:
        self.assertEqual(ctx.user_session_pattern, ListeningPattern.UNKNOWN)

    def test_lyric_low_confidence_strips_lyric_window(self) -> None:
        ctx = self.assembler.assemble(
            activity=_system_media_activity(
                lyric_status="low-confidence",
                lyric_confidence="low",
            ),
            profile_user_id="alice",
        )
        assert ctx is not None
        self.assertEqual(ctx.lyric_confidence, LyricConfidence.LOW)
        self.assertEqual(ctx.lyric_window, ())

    def test_lyric_unavailable_yields_confidence_none(self) -> None:
        ctx = self.assembler.assemble(
            activity=_system_media_activity(
                lyric_status="unavailable",
                lyric_confidence="",
                lyric_current="",
                lyric_previous="",
                lyric_next="",
            ),
            profile_user_id="alice",
        )
        assert ctx is not None
        self.assertEqual(ctx.lyric_confidence, LyricConfidence.NONE)
        self.assertEqual(ctx.lyric_window, ())

    def test_identity_key_stable_across_sources(self) -> None:
        """A track from QQ Music and from local file should hash to the same identity."""
        qq_ctx = self.assembler.assemble(
            activity=_system_media_activity(),
            profile_user_id="alice",
            now_ts=1_700_000_000,
        )
        local_ctx = self.assembler.assemble(
            activity={
                "type": "audio_playback",
                "title": "晴天",
                "artist": "周杰伦",
                "album": "叶惠美",
                "status": "running",
                "progress_seconds": 65.0,
                "source_kind": "local_file",
                "handle": "workspace:attachment:abc",
            },
            profile_user_id="alice",
            now_ts=1_700_000_500,
        )
        assert qq_ctx is not None and local_ctx is not None
        self.assertEqual(qq_ctx.track_identity, local_ctx.track_identity)
        self.assertEqual(local_ctx.source, MusicSource.LOCAL_AKANE)

    def test_default_enabled_controls(self) -> None:
        ctx = self.assembler.assemble(
            activity=_system_media_activity(),
            profile_user_id="alice",
        )
        assert ctx is not None
        self.assertIn(MusicControl.PAUSE, ctx.enabled_music_controls)
        self.assertIn(MusicControl.NEXT, ctx.enabled_music_controls)

    def test_external_unknown_source_disables_controls(self) -> None:
        ctx = self.assembler.assemble(
            activity={
                "type": "audio_playback",
                "title": "Mystery Song",
                "artist": "",
                "status": "running",
                "source_kind": "",
                "system_media": False,
                "handle": "external:somehow",
            },
            profile_user_id="alice",
        )
        assert ctx is not None
        self.assertEqual(ctx.source, MusicSource.EXTERNAL_UNKNOWN)
        self.assertFalse(ctx.control_session_writable)


# ---------------------------------------------------------------------------
# Co-listen persistence + cooldown
# ---------------------------------------------------------------------------


class CoListenPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _InMemoryStore()
        self.assembler = MusicContextAssembler(
            store=self.store,
            min_listen_seconds=30.0,
            repeat_cooldown_seconds=300.0,
        )
        self.addCleanup(self.store.close)

    def _assemble_at(self, *, progress: float, now_ts: int) -> MusicContext:
        ctx = self.assembler.assemble(
            activity=_system_media_activity(progress_seconds=progress),
            profile_user_id="alice",
            now_ts=now_ts,
        )
        assert ctx is not None
        return ctx

    def test_short_play_does_not_count(self) -> None:
        ctx = self._assemble_at(progress=10.0, now_ts=1_700_000_000)
        self.assertEqual(ctx.co_listen_count, 0)

    def test_long_play_commits_one_count(self) -> None:
        ctx = self._assemble_at(progress=45.0, now_ts=1_700_000_000)
        self.assertEqual(ctx.co_listen_count, 1)
        self.assertIsNotNone(ctx.last_listened_together)

    def test_cooldown_blocks_repeat_count(self) -> None:
        first = self._assemble_at(progress=45.0, now_ts=1_700_000_000)
        # Same track touched again 60s later — still inside the 5min cooldown.
        second = self._assemble_at(progress=120.0, now_ts=1_700_000_060)
        self.assertEqual(first.co_listen_count, 1)
        self.assertEqual(second.co_listen_count, 1)

    def test_cooldown_passes_after_threshold(self) -> None:
        self._assemble_at(progress=45.0, now_ts=1_700_000_000)
        # 6 minutes later — cooldown should have passed.
        third = self._assemble_at(progress=45.0, now_ts=1_700_000_000 + 360)
        self.assertEqual(third.co_listen_count, 2)

    def test_cross_source_shares_identity(self) -> None:
        # First time: QQ Music
        self.assembler.assemble(
            activity=_system_media_activity(progress_seconds=45.0),
            profile_user_id="alice",
            now_ts=1_700_000_000,
        )
        # Six minutes later: same song from local file
        ctx_local = self.assembler.assemble(
            activity={
                "type": "audio_playback",
                "title": "晴天",
                "artist": "周杰伦",
                "album": "叶惠美",
                "status": "running",
                "progress_seconds": 45.0,
                "source_kind": "local_file",
                "handle": "workspace:attachment:abc",
            },
            profile_user_id="alice",
            now_ts=1_700_000_000 + 360,
        )
        assert ctx_local is not None
        self.assertEqual(ctx_local.co_listen_count, 2)
        # The source recorded should now reflect the local play
        self.assertEqual(ctx_local.source, MusicSource.LOCAL_AKANE)

    def test_recent_co_listened_excludes_current_track(self) -> None:
        # Record one track at t0
        self.assembler.assemble(
            activity=_system_media_activity(
                title="晴天 - 周杰伦",
                artist="周杰伦",
                album="叶惠美",
                progress_seconds=45.0,
            ),
            profile_user_id="alice",
            now_ts=1_700_000_000,
        )
        # Record a different track at t1
        ctx_other = self.assembler.assemble(
            activity=_system_media_activity(
                title="七里香 - 周杰伦",
                artist="周杰伦",
                album="七里香",
                progress_seconds=45.0,
            ),
            profile_user_id="alice",
            now_ts=1_700_000_000 + 1000,
        )
        assert ctx_other is not None
        # 七里香 is the current track, so it should NOT appear in
        # recent_co_listened; 晴天 should.
        identity_keys = {item.key for item in ctx_other.recent_co_listened}
        current_key = ctx_other.track_identity.key if ctx_other.track_identity else ""
        self.assertNotIn(current_key, identity_keys)
        self.assertEqual(len(ctx_other.recent_co_listened), 1)

    def test_profile_isolation(self) -> None:
        self.assembler.assemble(
            activity=_system_media_activity(progress_seconds=45.0),
            profile_user_id="alice",
            now_ts=1_700_000_000,
        )
        ctx_bob = self.assembler.assemble(
            activity=_system_media_activity(progress_seconds=45.0),
            profile_user_id="bob",
            now_ts=1_700_000_000,
        )
        assert ctx_bob is not None
        self.assertEqual(ctx_bob.co_listen_count, 1)


# ---------------------------------------------------------------------------
# Prompt projection
# ---------------------------------------------------------------------------


class CoListenPromptTests(unittest.TestCase):
    def _make_context(self, **overrides) -> MusicContext:
        defaults = dict(
            is_playing=True,
            source=MusicSource.QQ_MUSIC,
            track=None,
            progress_seconds=60.0,
            duration_seconds=270.0,
            lyric_window=(),
            lyric_confidence=LyricConfidence.NONE,
            track_identity=TrackIdentity(
                title_normalized="晴天",
                artist_normalized="周杰伦",
                album_hint="叶惠美",
            ),
            co_listen_count=3,
            last_listened_together=datetime.fromtimestamp(1_700_000_000, tz=timezone.utc),
            recent_co_listened=(),
            user_session_pattern=ListeningPattern.UNKNOWN,
            current_loop_count=0,
            enabled_music_controls=frozenset(),
            control_session_writable=True,
        )
        defaults.update(overrides)
        return MusicContext(**defaults)

    def test_first_listen_returns_empty(self) -> None:
        ctx = self._make_context(co_listen_count=0, recent_co_listened=())
        self.assertEqual(build_co_listen_memory_prompt(ctx), "")

    def test_no_identity_returns_empty(self) -> None:
        ctx = self._make_context(track_identity=None)
        self.assertEqual(build_co_listen_memory_prompt(ctx), "")

    def test_multi_listen_mentions_count(self) -> None:
        ctx = self._make_context(co_listen_count=3)
        prompt = build_co_listen_memory_prompt(ctx, now_ts=1_700_000_000 + 60)
        self.assertIn("3", prompt)
        self.assertIn("一起听过", prompt)

    def test_single_listen_uses_softer_wording(self) -> None:
        ctx = self._make_context(co_listen_count=1)
        prompt = build_co_listen_memory_prompt(ctx, now_ts=1_700_000_000 + 60)
        self.assertIn("一起听", prompt)
        # Should not blurt "1 次".
        self.assertNotIn("1 次", prompt)

    def test_recent_track_appears_in_block(self) -> None:
        ctx = self._make_context(
            co_listen_count=2,
            recent_co_listened=(
                TrackIdentity(
                    title_normalized="七里香",
                    artist_normalized="周杰伦",
                    album_hint=None,
                ),
            ),
        )
        prompt = build_co_listen_memory_prompt(ctx, now_ts=1_700_000_000 + 60)
        self.assertIn("七里香", prompt)
        self.assertIn("最近也一起听过", prompt)

    def test_external_unknown_source_warns_against_naming(self) -> None:
        ctx = self._make_context(
            source=MusicSource.EXTERNAL_UNKNOWN,
            co_listen_count=2,
        )
        prompt = build_co_listen_memory_prompt(ctx, now_ts=1_700_000_000 + 60)
        self.assertIn("不要", prompt)


# ---------------------------------------------------------------------------
# Direct co_listen_store table tests
# ---------------------------------------------------------------------------


class CoListenStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        co_listen_store.ensure_schema(self.conn)
        self.addCleanup(self.conn.close)

    def _record(self, **overrides):
        defaults = dict(
            profile_user_id="alice",
            identity_key="晴天|周杰伦|叶惠美",
            title_normalized="晴天",
            artist_normalized="周杰伦",
            album_hint="叶惠美",
            display_title="晴天",
            display_artist="周杰伦",
            source=MusicSource.QQ_MUSIC.value,
            progress_seconds=45.0,
            now_ts=1_700_000_000,
        )
        defaults.update(overrides)
        return co_listen_store.record_co_listen_event(self.conn, **defaults)

    def test_first_record_below_threshold_creates_zero_count(self) -> None:
        summary = self._record(progress_seconds=10.0)
        self.assertEqual(summary.co_listen_count, 0)

    def test_first_record_above_threshold_creates_count_one(self) -> None:
        summary = self._record(progress_seconds=45.0)
        self.assertEqual(summary.co_listen_count, 1)
        self.assertGreater(summary.first_listened_at, 0)

    def test_get_summary_returns_none_for_missing(self) -> None:
        self.assertIsNone(
            co_listen_store.get_co_listen_summary(
                self.conn,
                profile_user_id="alice",
                identity_key="nope|nope|",
            )
        )

    def test_list_recent_orders_by_last_listened(self) -> None:
        self._record(
            identity_key="晴天|周杰伦|叶惠美",
            display_title="晴天",
            now_ts=1_700_000_000,
        )
        self._record(
            identity_key="七里香|周杰伦|七里香",
            display_title="七里香",
            now_ts=1_700_000_100,
        )
        recent = co_listen_store.list_recent_co_listened(
            self.conn,
            profile_user_id="alice",
            limit=5,
        )
        titles = [entry.display_title for entry in recent]
        self.assertEqual(titles[0], "七里香")
        self.assertEqual(titles[1], "晴天")

    def test_list_recent_excludes_specified_key(self) -> None:
        self._record(identity_key="a|a|", progress_seconds=45.0)
        self._record(identity_key="b|b|", progress_seconds=45.0, now_ts=1_700_000_100)
        recent = co_listen_store.list_recent_co_listened(
            self.conn,
            profile_user_id="alice",
            exclude_identity_key="b|b|",
        )
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0].identity_key, "a|a|")


# ---------------------------------------------------------------------------
# T4: controls_provider injection + permission prompt tests
# ---------------------------------------------------------------------------


class ControlsProviderAssemblerTests(unittest.TestCase):
    """T4 §7.4: controls_provider respected over hard-coded default."""

    def setUp(self) -> None:
        self.store = _InMemoryStore()
        self.addCleanup(self.store.close)

    def test_controls_provider_overrides_default(self) -> None:
        assembler = MusicContextAssembler(
            store=self.store,
            controls_provider=lambda uid: frozenset({MusicControl.PAUSE}),
        )
        ctx = assembler.assemble(
            activity=_system_media_activity(),
            profile_user_id="alice",
        )
        assert ctx is not None
        self.assertEqual(ctx.enabled_music_controls, frozenset({MusicControl.PAUSE}))
        self.assertNotIn(MusicControl.NEXT, ctx.enabled_music_controls)
        self.assertNotIn(MusicControl.PREV, ctx.enabled_music_controls)
        self.assertNotIn(MusicControl.RECOMMEND, ctx.enabled_music_controls)

    def test_explicit_enabled_controls_kwarg_beats_provider(self) -> None:
        assembler = MusicContextAssembler(
            store=self.store,
            controls_provider=lambda uid: frozenset({MusicControl.PAUSE}),
        )
        explicit = frozenset({MusicControl.NEXT})
        ctx = assembler.assemble(
            activity=_system_media_activity(),
            profile_user_id="alice",
            enabled_controls=explicit,
        )
        assert ctx is not None
        self.assertEqual(ctx.enabled_music_controls, explicit)


class PermissionPromptTests(unittest.TestCase):
    """T4 §7.4: build_co_listen_memory_prompt emits correct permission text."""

    def _make_context(self, **overrides) -> MusicContext:
        defaults = dict(
            is_playing=True,
            source=MusicSource.QQ_MUSIC,
            track=None,
            progress_seconds=60.0,
            duration_seconds=270.0,
            lyric_window=(),
            lyric_confidence=LyricConfidence.NONE,
            track_identity=TrackIdentity(
                title_normalized="晴天",
                artist_normalized="周杰伦",
                album_hint="叶惠美",
            ),
            co_listen_count=3,
            last_listened_together=None,
            recent_co_listened=(),
            user_session_pattern=ListeningPattern.UNKNOWN,
            current_loop_count=0,
            enabled_music_controls=frozenset({
                MusicControl.PAUSE,
                MusicControl.NEXT,
                MusicControl.PREV,
                MusicControl.RECOMMEND,
            }),
            control_session_writable=True,
        )
        defaults.update(overrides)
        return MusicContext(**defaults)

    def test_revoked_controls_appear_in_prompt(self) -> None:
        ctx = self._make_context(
            enabled_music_controls=frozenset({MusicControl.PAUSE})
        )
        prompt = build_co_listen_memory_prompt(ctx, now_ts=1_700_000_060)
        self.assertIn("没让你主动切下一首", prompt)
        self.assertIn("没让你回到上一首", prompt)
        self.assertIn("没让你主动推歌", prompt)

    def test_all_enabled_no_permission_section(self) -> None:
        ctx = self._make_context(
            enabled_music_controls=frozenset({
                MusicControl.PAUSE,
                MusicControl.NEXT,
                MusicControl.PREV,
                MusicControl.RECOMMEND,
            })
        )
        prompt = build_co_listen_memory_prompt(ctx, now_ts=1_700_000_060)
        self.assertNotIn("她现在能做什么", prompt)

    def test_each_disabled_control_produces_its_own_line(self) -> None:
        ctx = self._make_context(
            enabled_music_controls=frozenset({MusicControl.PAUSE, MusicControl.NEXT})
        )
        prompt = build_co_listen_memory_prompt(ctx, now_ts=1_700_000_060)
        self.assertIn("没让你回到上一首", prompt)
        self.assertIn("没让你主动推歌", prompt)
        # Enabled controls must NOT appear as restrictions
        self.assertNotIn("没让你主动切下一首", prompt)
        self.assertNotIn("没让你主动暂停", prompt)

    def test_recommend_enabled_nudge_appears_in_prompt(self) -> None:
        ctx = self._make_context(
            enabled_music_controls=frozenset({
                MusicControl.PAUSE, MusicControl.NEXT,
                MusicControl.PREV, MusicControl.RECOMMEND,
            })
        )
        prompt = build_co_listen_memory_prompt(ctx, now_ts=1_700_000_060)
        self.assertIn("主动提议", prompt)

    def test_recommend_disabled_no_nudge(self) -> None:
        ctx = self._make_context(
            enabled_music_controls=frozenset({
                MusicControl.PAUSE, MusicControl.NEXT, MusicControl.PREV,
            })
        )
        prompt = build_co_listen_memory_prompt(ctx, now_ts=1_700_000_060)
        self.assertNotIn("可以主动提议推荐", prompt)


if __name__ == "__main__":
    unittest.main()
