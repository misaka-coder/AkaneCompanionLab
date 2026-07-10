from __future__ import annotations

import difflib
import hashlib
import json
import math
import re
import sqlite3
import threading
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .normalizers import normalize_market_code
from .store_models import (
    DeliveryClaim,
    DeliveryReservation,
    EventUpsertResult,
    FinanceSubscription,
    MarketEventDelivery,
    MarketSecurity,
    StoredMarketEvent,
    WatchlistItem,
)
from .types import MarketDataValidationError, MarketEvent


MARKET_STORE_SCHEMA_VERSION = 4
EVENT_STATUSES = frozenset({"active", "updated", "archived"})
DELIVERY_STATUSES = frozenset({"pending", "processing", "delivered", "failed", "cancelled"})
RETRYABLE_DELIVERY_STATUSES = frozenset({"pending", "failed"})
SUBSCRIPTION_FILTER_KEYS = frozenset({"codes", "content_types", "sector_codes", "labels_any", "providers"})

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:@-]{1,200}$")
_RAW_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_TITLE_TOKEN_RE = re.compile(r"[^\w\u3400-\u9fff]+", re.UNICODE)

_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS market_events (
    event_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    published_at INTEGER NOT NULL,
    produced_at INTEGER,
    received_at INTEGER NOT NULL,
    code TEXT NOT NULL,
    content_type TEXT NOT NULL,
    title TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    sentiment TEXT NOT NULL DEFAULT 'unknown',
    labels_json TEXT NOT NULL DEFAULT '[]',
    sector_code TEXT NOT NULL DEFAULT '',
    raw_hash TEXT NOT NULL,
    cluster_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'updated', 'archived')),
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_market_events_code_time
ON market_events(code, published_at DESC);

CREATE INDEX IF NOT EXISTS idx_market_events_type_time
ON market_events(content_type, published_at DESC);

CREATE INDEX IF NOT EXISTS idx_market_events_cluster
ON market_events(cluster_id, published_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_market_events_raw_hash_unique
ON market_events(raw_hash);

CREATE TABLE IF NOT EXISTS finance_subscriptions (
    subscription_id TEXT PRIMARY KEY,
    client TEXT NOT NULL,
    target_id TEXT NOT NULL,
    is_group INTEGER NOT NULL DEFAULT 0 CHECK(is_group IN (0, 1)),
    session_id TEXT NOT NULL,
    profile_user_id TEXT NOT NULL,
    character_pack_id TEXT NOT NULL DEFAULT '',
    finance_mode TEXT NOT NULL CHECK(finance_mode IN ('qa', 'push')),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    filters_json TEXT NOT NULL DEFAULT '{{}}',
    delivery_policy_json TEXT NOT NULL DEFAULT '{{}}',
    created_by_actor_id TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_finance_subscriptions_target
ON finance_subscriptions(client, target_id, enabled, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_finance_subscriptions_profile
ON finance_subscriptions(profile_user_id, enabled, updated_at DESC);

CREATE TABLE IF NOT EXISTS watchlist_items (
    subscription_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    code TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    aliases_json TEXT NOT NULL DEFAULT '[]',
    priority REAL NOT NULL DEFAULT 0.5 CHECK(priority >= 0.0 AND priority <= 1.0),
    created_by_actor_id TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY(subscription_id, code),
    FOREIGN KEY(subscription_id) REFERENCES finance_subscriptions(subscription_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_watchlist_code
ON watchlist_items(code, priority DESC);

CREATE TABLE IF NOT EXISTS market_securities (
    provider TEXT NOT NULL,
    code TEXT NOT NULL,
    display_name TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    market TEXT NOT NULL DEFAULT '',
    security_type TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    as_of INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY(provider, code)
);

CREATE INDEX IF NOT EXISTS idx_market_securities_name
ON market_securities(display_name, provider, code);

CREATE TABLE IF NOT EXISTS market_security_aliases (
    provider TEXT NOT NULL,
    code TEXT NOT NULL,
    alias_norm TEXT NOT NULL,
    alias TEXT NOT NULL,
    PRIMARY KEY(provider, code, alias_norm),
    FOREIGN KEY(provider, code) REFERENCES market_securities(provider, code) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_market_security_alias_norm
ON market_security_aliases(alias_norm, provider, code);

CREATE TABLE IF NOT EXISTS market_event_deliveries (
    event_id TEXT NOT NULL,
    subscription_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'processing', 'delivered', 'failed', 'cancelled')),
    analysis_id TEXT NOT NULL DEFAULT '',
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
    last_attempt_at INTEGER,
    delivered_at INTEGER,
    reason TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY(event_id, subscription_id),
    FOREIGN KEY(event_id) REFERENCES market_events(event_id) ON DELETE CASCADE,
    FOREIGN KEY(subscription_id) REFERENCES finance_subscriptions(subscription_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_market_event_deliveries_status
ON market_event_deliveries(status, updated_at, event_id, subscription_id);

PRAGMA user_version = {MARKET_STORE_SCHEMA_VERSION};
"""


class MarketEventStore:
    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
        cluster_window_seconds: int = 6 * 60 * 60,
        cluster_similarity_threshold: float = 0.86,
    ) -> None:
        self.db_path = Path(db_path).expanduser()
        if not str(self.db_path).strip():
            raise MarketDataValidationError(field="db_path", reason="database path is required")
        if self.db_path.exists() and self.db_path.is_dir():
            raise MarketDataValidationError(field="db_path", reason="database path cannot be a directory")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self.cluster_window_seconds = max(60, int(cluster_window_seconds))
        self.cluster_similarity_threshold = min(1.0, max(0.5, float(cluster_similarity_threshold)))
        self._write_lock = threading.RLock()
        self._init_db()

    @contextmanager
    def _connect(self, *, write: bool = False):
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        except Exception:
            if write:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _init_db(self) -> None:
        with self._write_lock, self._connect(write=True) as connection:
            connection.executescript(_SCHEMA_SQL)
            self._migrate_schema(connection)
            try:
                connection.execute("PRAGMA journal_mode = WAL")
            except sqlite3.DatabaseError:
                pass

    @staticmethod
    def _migrate_schema(connection: sqlite3.Connection) -> None:
        subscription_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(finance_subscriptions)").fetchall()
        }
        if "is_group" not in subscription_columns:
            connection.execute(
                "ALTER TABLE finance_subscriptions ADD COLUMN is_group INTEGER NOT NULL DEFAULT 0"
            )
            connection.execute(
                """
                UPDATE finance_subscriptions
                SET is_group = 1
                WHERE session_id LIKE 'qq_group_%'
                """
            )
        watchlist_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(watchlist_items)").fetchall()}
        if "provider" not in watchlist_columns:
            connection.execute("ALTER TABLE watchlist_items ADD COLUMN provider TEXT NOT NULL DEFAULT 'choice_emquant'")
        connection.execute("DROP INDEX IF EXISTS idx_watchlist_code")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_watchlist_code ON watchlist_items(provider, code, priority DESC)"
        )
        connection.execute(f"PRAGMA user_version = {MARKET_STORE_SCHEMA_VERSION}")

    def schema_version(self) -> int:
        with self._connect() as connection:
            row = connection.execute("PRAGMA user_version").fetchone()
        return int(row[0] if row else 0)

    def upsert_event(self, event: MarketEvent, *, now_ts: int | None = None) -> EventUpsertResult:
        normalized = _validate_event(event)
        now = self._now(now_ts)
        with self._write_lock, self._connect(write=True) as connection:
            existing_row = connection.execute(
                "SELECT * FROM market_events WHERE event_id = ?",
                (normalized.event_id,),
            ).fetchone()
            if existing_row is not None and str(existing_row["raw_hash"]) == normalized.raw_hash:
                record = _row_to_stored_event(existing_row)
                return EventUpsertResult(
                    status="duplicate_event_id",
                    record=record,
                    canonical_event_id=record.event.event_id,
                    duplicate_of=record.event.event_id,
                )

            raw_duplicate_row = connection.execute(
                "SELECT * FROM market_events WHERE raw_hash = ? AND event_id != ?",
                (normalized.raw_hash, normalized.event_id),
            ).fetchone()
            if raw_duplicate_row is not None:
                record = _row_to_stored_event(raw_duplicate_row)
                return EventUpsertResult(
                    status="duplicate_raw_hash",
                    record=record,
                    canonical_event_id=record.event.event_id,
                    duplicate_of=record.event.event_id,
                )

            if existing_row is not None:
                cluster_id = str(existing_row["cluster_id"] or "") or self._resolve_cluster_id(
                    connection,
                    normalized,
                    exclude_event_id=normalized.event_id,
                )
                revision = max(1, int(existing_row["revision"] or 1)) + 1
                connection.execute(
                    """
                    UPDATE market_events
                    SET provider = ?, published_at = ?, produced_at = ?, received_at = ?,
                        code = ?, content_type = ?, title = ?, source = ?, url = ?,
                        sentiment = ?, labels_json = ?, sector_code = ?, raw_hash = ?,
                        cluster_id = ?, status = 'updated', revision = ?, updated_at = ?
                    WHERE event_id = ?
                    """,
                    _event_sql_values(normalized) + (cluster_id, revision, now, normalized.event_id),
                )
                row = connection.execute(
                    "SELECT * FROM market_events WHERE event_id = ?",
                    (normalized.event_id,),
                ).fetchone()
                record = _row_to_stored_event(row)
                return EventUpsertResult(
                    status="updated",
                    record=record,
                    canonical_event_id=normalized.event_id,
                )

            cluster_id = self._resolve_cluster_id(connection, normalized)
            try:
                connection.execute(
                    """
                    INSERT INTO market_events (
                        event_id, provider, published_at, produced_at, received_at,
                        code, content_type, title, source, url, sentiment, labels_json,
                        sector_code, raw_hash, cluster_id, status, revision, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 1, ?, ?)
                    """,
                    (normalized.event_id,) + _event_sql_values(normalized) + (cluster_id, now, now),
                )
            except sqlite3.IntegrityError:
                race_row = connection.execute(
                    "SELECT * FROM market_events WHERE event_id = ? OR raw_hash = ? ORDER BY event_id LIMIT 1",
                    (normalized.event_id, normalized.raw_hash),
                ).fetchone()
                if race_row is None:
                    raise
                record = _row_to_stored_event(race_row)
                status = "duplicate_event_id" if record.event.event_id == normalized.event_id else "duplicate_raw_hash"
                return EventUpsertResult(
                    status=status,
                    record=record,
                    canonical_event_id=record.event.event_id,
                    duplicate_of=record.event.event_id,
                )
            row = connection.execute(
                "SELECT * FROM market_events WHERE event_id = ?",
                (normalized.event_id,),
            ).fetchone()
            record = _row_to_stored_event(row)
            return EventUpsertResult(
                status="inserted",
                record=record,
                canonical_event_id=normalized.event_id,
            )

    def get_event(self, event_id: str) -> StoredMarketEvent | None:
        clean_id = _safe_id(event_id, field="event_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM market_events WHERE event_id = ?",
                (clean_id,),
            ).fetchone()
        return _row_to_stored_event(row) if row is not None else None

    def list_events(
        self,
        *,
        query: str = "",
        codes: Iterable[str] = (),
        content_types: Iterable[str] = (),
        date_from: int | None = None,
        date_to: int | None = None,
        limit: int = 50,
    ) -> tuple[StoredMarketEvent, ...]:
        clean_query = str(query or "").strip()
        if len(clean_query) > 500:
            raise _invalid_argument("query", "query is too long")
        clean_codes = _normalized_codes(codes, field="codes")
        clean_types = _normalized_text_values(content_types, field="content_types")
        start = _optional_positive_int(date_from, field="date_from")
        end = _optional_positive_int(date_to, field="date_to")
        if start is not None and end is not None and start > end:
            raise _invalid_argument("date_from", "date_from cannot be later than date_to")
        clean_limit = _bounded_int(limit, field="limit", lower=1, upper=500)

        clauses = ["1 = 1"]
        parameters: list[Any] = []
        if clean_query:
            escaped = _escape_like(clean_query)
            clauses.append("(title LIKE ? ESCAPE '\\' OR source LIKE ? ESCAPE '\\')")
            parameters.extend((f"%{escaped}%", f"%{escaped}%"))
        if clean_codes:
            placeholders = ",".join("?" for _ in clean_codes)
            clauses.append(f"code IN ({placeholders})")
            parameters.extend(clean_codes)
        if clean_types:
            placeholders = ",".join("?" for _ in clean_types)
            clauses.append(f"content_type IN ({placeholders})")
            parameters.extend(clean_types)
        if start is not None:
            clauses.append("published_at >= ?")
            parameters.append(start)
        if end is not None:
            clauses.append("published_at <= ?")
            parameters.append(end)
        parameters.append(clean_limit)

        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM market_events
                WHERE {" AND ".join(clauses)}
                ORDER BY published_at DESC, event_id DESC
                LIMIT ?
                """,
                tuple(parameters),
            ).fetchall()
        return tuple(_row_to_stored_event(row) for row in rows)

    def upsert_subscription(
        self,
        *,
        subscription_id: str,
        client: str,
        target_id: str,
        is_group: bool | None = None,
        session_id: str,
        profile_user_id: str,
        character_pack_id: str = "",
        finance_mode: str = "push",
        enabled: bool = True,
        filters: Mapping[str, Any] | None = None,
        delivery_policy: Mapping[str, Any] | None = None,
        created_by_actor_id: str = "",
        now_ts: int | None = None,
    ) -> FinanceSubscription:
        normalized = _normalize_subscription_input(
            subscription_id=subscription_id,
            client=client,
            target_id=target_id,
            is_group=is_group,
            session_id=session_id,
            profile_user_id=profile_user_id,
            character_pack_id=character_pack_id,
            finance_mode=finance_mode,
            enabled=enabled,
            filters=filters,
            delivery_policy=delivery_policy,
            created_by_actor_id=created_by_actor_id,
        )
        now = self._now(now_ts)
        with self._write_lock, self._connect(write=True) as connection:
            existing = connection.execute(
                "SELECT * FROM finance_subscriptions WHERE subscription_id = ?",
                (normalized["subscription_id"],),
            ).fetchone()
            if existing is not None:
                immutable_fields = ("client", "target_id", "is_group", "session_id", "profile_user_id")
                for field_name in immutable_fields:
                    existing_value = bool(existing[field_name]) if field_name == "is_group" else str(
                        existing[field_name] or ""
                    )
                    normalized_value = (
                        bool(normalized[field_name])
                        if field_name == "is_group"
                        else str(normalized[field_name] or "")
                    )
                    if existing_value != normalized_value:
                        raise _invalid_argument(
                            field_name,
                            f"cannot change subscription owner field {field_name}",
                        )
                actor_id = str(existing["created_by_actor_id"] or "")
                connection.execute(
                    """
                    UPDATE finance_subscriptions
                    SET character_pack_id = ?, finance_mode = ?, enabled = ?, filters_json = ?,
                        delivery_policy_json = ?, created_by_actor_id = ?, updated_at = ?
                    WHERE subscription_id = ?
                    """,
                    (
                        normalized["character_pack_id"],
                        normalized["finance_mode"],
                        int(normalized["enabled"]),
                        normalized["filters_json"],
                        normalized["delivery_policy_json"],
                        actor_id,
                        now,
                        normalized["subscription_id"],
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO finance_subscriptions (
                        subscription_id, client, target_id, is_group, session_id, profile_user_id,
                        character_pack_id, finance_mode, enabled, filters_json,
                        delivery_policy_json, created_by_actor_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized["subscription_id"],
                        normalized["client"],
                        normalized["target_id"],
                        int(normalized["is_group"]),
                        normalized["session_id"],
                        normalized["profile_user_id"],
                        normalized["character_pack_id"],
                        normalized["finance_mode"],
                        int(normalized["enabled"]),
                        normalized["filters_json"],
                        normalized["delivery_policy_json"],
                        normalized["created_by_actor_id"],
                        now,
                        now,
                    ),
                )
            if not normalized["enabled"]:
                self._cancel_open_deliveries(connection, normalized["subscription_id"], now)
            row = connection.execute(
                "SELECT * FROM finance_subscriptions WHERE subscription_id = ?",
                (normalized["subscription_id"],),
            ).fetchone()
        return _row_to_subscription(row)

    def get_subscription(self, subscription_id: str) -> FinanceSubscription | None:
        clean_id = _safe_id(subscription_id, field="subscription_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM finance_subscriptions WHERE subscription_id = ?",
                (clean_id,),
            ).fetchone()
        return _row_to_subscription(row) if row is not None else None

    def list_subscriptions(
        self,
        *,
        enabled: bool | None = None,
        client: str = "",
        target_id: str = "",
    ) -> tuple[FinanceSubscription, ...]:
        clauses = ["1 = 1"]
        parameters: list[Any] = []
        if enabled is not None:
            clauses.append("enabled = ?")
            parameters.append(int(bool(enabled)))
        clean_client = str(client or "").strip().lower()
        if clean_client:
            clauses.append("client = ?")
            parameters.append(clean_client)
        clean_target = str(target_id or "").strip()
        if clean_target:
            clauses.append("target_id = ?")
            parameters.append(clean_target)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM finance_subscriptions
                WHERE {" AND ".join(clauses)}
                ORDER BY updated_at DESC, subscription_id
                """,
                tuple(parameters),
            ).fetchall()
        return tuple(_row_to_subscription(row) for row in rows)

    def set_subscription_enabled(
        self,
        subscription_id: str,
        *,
        enabled: bool,
        now_ts: int | None = None,
    ) -> FinanceSubscription | None:
        clean_id = _safe_id(subscription_id, field="subscription_id")
        now = self._now(now_ts)
        with self._write_lock, self._connect(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM finance_subscriptions WHERE subscription_id = ?",
                (clean_id,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE finance_subscriptions SET enabled = ?, updated_at = ? WHERE subscription_id = ?",
                (int(bool(enabled)), now, clean_id),
            )
            if not enabled:
                self._cancel_open_deliveries(connection, clean_id, now)
            updated = connection.execute(
                "SELECT * FROM finance_subscriptions WHERE subscription_id = ?",
                (clean_id,),
            ).fetchone()
        return _row_to_subscription(updated)

    def upsert_watchlist_item(
        self,
        *,
        subscription_id: str,
        provider: str,
        code: str,
        display_name: str = "",
        aliases: Iterable[str] = (),
        priority: float = 0.5,
        created_by_actor_id: str = "",
        now_ts: int | None = None,
    ) -> WatchlistItem:
        clean_subscription_id = _safe_id(subscription_id, field="subscription_id")
        clean_provider = _safe_id(provider, field="provider")
        clean_code = normalize_market_code(
            code,
            field="code",
            status="invalid_arguments",
            error_code="invalid_arguments",
        )
        clean_name = _bounded_text(display_name, field="display_name", max_length=300)
        clean_aliases = _normalized_aliases(aliases)
        clean_priority = _bounded_float(priority, field="priority", lower=0.0, upper=1.0)
        clean_actor = _bounded_text(created_by_actor_id, field="created_by_actor_id", max_length=240)
        now = self._now(now_ts)
        with self._write_lock, self._connect(write=True) as connection:
            subscription = connection.execute(
                "SELECT subscription_id FROM finance_subscriptions WHERE subscription_id = ?",
                (clean_subscription_id,),
            ).fetchone()
            if subscription is None:
                raise _invalid_argument("subscription_id", "subscription does not exist")
            connection.execute(
                """
                INSERT INTO watchlist_items (
                    subscription_id, provider, code, display_name, aliases_json, priority,
                    created_by_actor_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(subscription_id, code) DO UPDATE SET
                    provider = excluded.provider,
                    display_name = excluded.display_name,
                    aliases_json = excluded.aliases_json,
                    priority = excluded.priority,
                    updated_at = excluded.updated_at
                """,
                (
                    clean_subscription_id,
                    clean_provider,
                    clean_code,
                    clean_name,
                    _json_dumps(list(clean_aliases), field="aliases"),
                    clean_priority,
                    clean_actor,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM watchlist_items WHERE subscription_id = ? AND code = ?",
                (clean_subscription_id, clean_code),
            ).fetchone()
        return _row_to_watchlist_item(row)

    def remove_watchlist_item(self, *, subscription_id: str, provider: str, code: str) -> bool:
        clean_subscription_id = _safe_id(subscription_id, field="subscription_id")
        clean_provider = _safe_id(provider, field="provider")
        clean_code = normalize_market_code(
            code,
            field="code",
            status="invalid_arguments",
            error_code="invalid_arguments",
        )
        with self._write_lock, self._connect(write=True) as connection:
            cursor = connection.execute(
                "DELETE FROM watchlist_items WHERE subscription_id = ? AND provider = ? AND code = ?",
                (clean_subscription_id, clean_provider, clean_code),
            )
        return int(cursor.rowcount or 0) > 0

    def list_watchlist(self, subscription_id: str) -> tuple[WatchlistItem, ...]:
        clean_id = _safe_id(subscription_id, field="subscription_id")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM watchlist_items
                WHERE subscription_id = ?
                ORDER BY priority DESC, created_at, code
                """,
                (clean_id,),
            ).fetchall()
        return tuple(_row_to_watchlist_item(row) for row in rows)

    def upsert_security(
        self,
        *,
        provider: str,
        code: str,
        display_name: str,
        aliases: Iterable[str] = (),
        market: str = "",
        security_type: str = "",
        source: str,
        as_of: int,
        now_ts: int | None = None,
    ) -> MarketSecurity:
        clean_provider = _safe_id(provider, field="provider")
        clean_code = normalize_market_code(
            code,
            field="code",
            status="invalid_arguments",
            error_code="invalid_arguments",
        )
        clean_name = _required_text(display_name, field="display_name", max_length=300)
        clean_aliases = _normalized_security_alias_values(aliases, max_items=64, max_length=300)
        clean_market = _bounded_text(market, field="market", max_length=120).upper()
        clean_type = _bounded_text(security_type, field="security_type", max_length=120).lower()
        clean_source = _required_text(source, field="source", max_length=500)
        clean_as_of = _positive_int(as_of, field="as_of")
        now = self._now(now_ts)
        alias_values = _unique_security_aliases((clean_code, clean_name, *clean_aliases))
        with self._write_lock, self._connect(write=True) as connection:
            existing = connection.execute(
                "SELECT created_at FROM market_securities WHERE provider = ? AND code = ?",
                (clean_provider, clean_code),
            ).fetchone()
            created_at = int(existing["created_at"]) if existing is not None else now
            connection.execute(
                """
                INSERT INTO market_securities (
                    provider, code, display_name, aliases_json, market, security_type,
                    source, as_of, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, code) DO UPDATE SET
                    display_name = excluded.display_name,
                    aliases_json = excluded.aliases_json,
                    market = excluded.market,
                    security_type = excluded.security_type,
                    source = excluded.source,
                    as_of = excluded.as_of,
                    updated_at = excluded.updated_at
                """,
                (
                    clean_provider,
                    clean_code,
                    clean_name,
                    _json_dumps(list(clean_aliases), field="aliases"),
                    clean_market,
                    clean_type,
                    clean_source,
                    clean_as_of,
                    created_at,
                    now,
                ),
            )
            connection.execute(
                "DELETE FROM market_security_aliases WHERE provider = ? AND code = ?",
                (clean_provider, clean_code),
            )
            connection.executemany(
                """
                INSERT INTO market_security_aliases (provider, code, alias_norm, alias)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (clean_provider, clean_code, _normalize_security_alias(alias), alias)
                    for alias in alias_values
                ],
            )
            row = connection.execute(
                "SELECT * FROM market_securities WHERE provider = ? AND code = ?",
                (clean_provider, clean_code),
            ).fetchone()
        return _row_to_market_security(row)

    def resolve_security(
        self,
        query: str,
        *,
        provider: str = "",
        profile_user_id: str = "",
        session_id: str = "",
        limit: int = 10,
    ) -> tuple[dict[str, Any], ...]:
        clean_query = _required_text(query, field="query", max_length=300)
        query_norm = _normalize_security_alias(clean_query)
        if not query_norm:
            raise _invalid_argument("query", "query must contain searchable characters")
        clean_provider = _safe_id(provider, field="provider") if str(provider or "").strip() else ""
        clean_profile = _safe_id(profile_user_id, field="profile_user_id") if str(profile_user_id or "").strip() else ""
        clean_session = _safe_id(session_id, field="session_id") if str(session_id or "").strip() else ""
        clean_limit = _bounded_int(limit, field="limit", lower=1, upper=20)
        matches: dict[tuple[str, str, str], dict[str, Any]] = {}
        provider_clause = "AND s.provider = ?" if clean_provider else ""
        provider_parameters: list[Any] = [query_norm]
        if clean_provider:
            provider_parameters.append(clean_provider)
        provider_parameters.append(clean_limit)
        with self._connect() as connection:
            exact_rows = connection.execute(
                f"""
                SELECT s.*, a.alias AS matched_alias
                FROM market_security_aliases a
                JOIN market_securities s ON s.provider = a.provider AND s.code = a.code
                WHERE a.alias_norm = ? {provider_clause}
                ORDER BY s.updated_at DESC, s.provider, s.code
                LIMIT ?
                """,
                tuple(provider_parameters),
            ).fetchall()
            partial_parameters: list[Any] = [f"%{_escape_like(query_norm)}%"]
            if clean_provider:
                partial_parameters.append(clean_provider)
            partial_parameters.append(clean_limit)
            partial_rows = connection.execute(
                f"""
                SELECT s.*, a.alias AS matched_alias
                FROM market_security_aliases a
                JOIN market_securities s ON s.provider = a.provider AND s.code = a.code
                WHERE a.alias_norm LIKE ? ESCAPE '\\' {provider_clause}
                ORDER BY length(a.alias_norm), s.updated_at DESC, s.provider, s.code
                LIMIT ?
                """,
                tuple(partial_parameters),
            ).fetchall()
            watch_rows = []
            if clean_profile and clean_session:
                watch_provider_clause = "AND w.provider = ?" if clean_provider else ""
                watch_parameters: list[Any] = [clean_profile, clean_session]
                if clean_provider:
                    watch_parameters.append(clean_provider)
                watch_rows = connection.execute(
                    f"""
                    SELECT w.*
                    FROM watchlist_items w
                    JOIN finance_subscriptions f ON f.subscription_id = w.subscription_id
                    WHERE f.profile_user_id = ? AND f.session_id = ? AND f.enabled = 1
                    {watch_provider_clause}
                    ORDER BY w.priority DESC, w.updated_at DESC, w.code
                    LIMIT 200
                    """,
                    tuple(watch_parameters),
                ).fetchall()
        for row in (*exact_rows, *partial_rows):
            security = _row_to_market_security(row)
            matched_alias = str(row["matched_alias"] or "")
            match_type = "exact" if _normalize_security_alias(matched_alias) == query_norm else "partial"
            key = (security.provider, security.code, "security_master")
            previous = matches.get(key)
            if previous is None or previous["match_type"] != "exact":
                matches[key] = {
                    **security.to_public_dict(),
                    "matched_alias": matched_alias,
                    "match_type": match_type,
                    "scope": "security_master",
                }
        for row in watch_rows:
            item = _row_to_watchlist_item(row)
            aliases = _unique_security_aliases((item.code, item.display_name, *item.aliases))
            exact_alias = next((alias for alias in aliases if _normalize_security_alias(alias) == query_norm), "")
            partial_alias = next((alias for alias in aliases if query_norm in _normalize_security_alias(alias)), "")
            matched_alias = exact_alias or partial_alias
            if not matched_alias:
                continue
            key = (item.provider, item.code, "session_watchlist")
            matches[key] = {
                "provider": item.provider,
                "code": item.code,
                "display_name": item.display_name,
                "aliases": list(item.aliases),
                "market": "",
                "security_type": "",
                "source": "session_watchlist",
                "as_of": item.updated_at,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
                "matched_alias": matched_alias,
                "match_type": "exact" if exact_alias else "partial",
                "scope": "session_watchlist",
            }
        ordered = sorted(
            matches.values(),
            key=lambda item: (
                0 if item["match_type"] == "exact" else 1,
                0 if item["scope"] == "session_watchlist" else 1,
                str(item.get("display_name") or ""),
                str(item.get("code") or ""),
            ),
        )
        return tuple(ordered[:clean_limit])

    def is_trusted_security_code(
        self,
        code: str,
        *,
        provider: str = "",
        profile_user_id: str = "",
        session_id: str = "",
    ) -> bool:
        clean_code = normalize_market_code(
            code,
            field="code",
            status="invalid_arguments",
            error_code="invalid_arguments",
        )
        clean_provider = _safe_id(provider, field="provider") if str(provider or "").strip() else ""
        with self._connect() as connection:
            if clean_provider:
                master = connection.execute(
                    "SELECT 1 FROM market_securities WHERE provider = ? AND code = ?",
                    (clean_provider, clean_code),
                ).fetchone()
            else:
                master = connection.execute(
                    "SELECT 1 FROM market_securities WHERE code = ? LIMIT 1",
                    (clean_code,),
                ).fetchone()
            if master is not None:
                return True
            if not str(profile_user_id or "").strip() or not str(session_id or "").strip():
                return False
            watch = connection.execute(
                f"""
                SELECT 1
                FROM watchlist_items w
                JOIN finance_subscriptions f ON f.subscription_id = w.subscription_id
                WHERE w.code = ? AND f.profile_user_id = ? AND f.session_id = ? AND f.enabled = 1
                {"AND w.provider = ?" if clean_provider else ""}
                LIMIT 1
                """,
                (
                    clean_code,
                    str(profile_user_id).strip(),
                    str(session_id).strip(),
                    *((clean_provider,) if clean_provider else ()),
                ),
            ).fetchone()
        return watch is not None

    def is_session_watchlist_code(
        self,
        code: str,
        *,
        provider: str,
        profile_user_id: str,
        session_id: str,
    ) -> bool:
        clean_code = normalize_market_code(
            code,
            field="code",
            status="invalid_arguments",
            error_code="invalid_arguments",
        )
        clean_provider = _safe_id(provider, field="provider")
        clean_profile = _safe_id(profile_user_id, field="profile_user_id")
        clean_session = _safe_id(session_id, field="session_id")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM watchlist_items w
                JOIN finance_subscriptions f ON f.subscription_id = w.subscription_id
                WHERE w.provider = ? AND w.code = ?
                  AND f.profile_user_id = ? AND f.session_id = ? AND f.enabled = 1
                LIMIT 1
                """,
                (clean_provider, clean_code, clean_profile, clean_session),
            ).fetchone()
        return row is not None

    def match_subscriptions(self, event: MarketEvent | StoredMarketEvent) -> tuple[FinanceSubscription, ...]:
        market_event = event.event if isinstance(event, StoredMarketEvent) else event
        normalized = _validate_event(market_event)
        with self._connect() as connection:
            subscription_rows = connection.execute(
                "SELECT * FROM finance_subscriptions WHERE enabled = 1 ORDER BY updated_at DESC, subscription_id"
            ).fetchall()
            watch_rows = connection.execute("SELECT subscription_id, provider, code FROM watchlist_items").fetchall()
        watch_by_subscription: dict[str, set[tuple[str, str]]] = {}
        for row in watch_rows:
            watch_by_subscription.setdefault(str(row["subscription_id"]), set()).add(
                (str(row["provider"]), str(row["code"]))
            )
        matches: list[FinanceSubscription] = []
        for row in subscription_rows:
            subscription = _row_to_subscription(row)
            watch_codes = {
                code
                for provider, code in watch_by_subscription.get(subscription.subscription_id, set())
                if provider == normalized.provider
            }
            if _subscription_matches_event(subscription, normalized, watch_codes=watch_codes):
                matches.append(subscription)
        return tuple(matches)

    def ensure_delivery(
        self,
        *,
        event_id: str,
        subscription_id: str,
        now_ts: int | None = None,
    ) -> DeliveryReservation:
        clean_event_id = _safe_id(event_id, field="event_id")
        clean_subscription_id = _safe_id(subscription_id, field="subscription_id")
        now = self._now(now_ts)
        with self._write_lock, self._connect(write=True) as connection:
            event_row = connection.execute(
                "SELECT event_id FROM market_events WHERE event_id = ?",
                (clean_event_id,),
            ).fetchone()
            if event_row is None:
                raise _invalid_argument("event_id", "event does not exist")
            subscription_row = connection.execute(
                "SELECT enabled FROM finance_subscriptions WHERE subscription_id = ?",
                (clean_subscription_id,),
            ).fetchone()
            if subscription_row is None:
                raise _invalid_argument("subscription_id", "subscription does not exist")
            existing = connection.execute(
                """
                SELECT * FROM market_event_deliveries
                WHERE event_id = ? AND subscription_id = ?
                """,
                (clean_event_id, clean_subscription_id),
            ).fetchone()
            if existing is not None:
                delivery = _row_to_delivery(existing)
                return DeliveryReservation(
                    created=False,
                    should_deliver=bool(subscription_row["enabled"]) and delivery.status in RETRYABLE_DELIVERY_STATUSES,
                    delivery=delivery,
                )
            status = "pending" if bool(subscription_row["enabled"]) else "cancelled"
            reason = "" if status == "pending" else "subscription_disabled"
            connection.execute(
                """
                INSERT INTO market_event_deliveries (
                    event_id, subscription_id, status, analysis_id, attempt_count,
                    last_attempt_at, delivered_at, reason, created_at, updated_at
                ) VALUES (?, ?, ?, '', 0, NULL, NULL, ?, ?, ?)
                """,
                (clean_event_id, clean_subscription_id, status, reason, now, now),
            )
            row = connection.execute(
                """
                SELECT * FROM market_event_deliveries
                WHERE event_id = ? AND subscription_id = ?
                """,
                (clean_event_id, clean_subscription_id),
            ).fetchone()
            delivery = _row_to_delivery(row)
            return DeliveryReservation(
                created=True,
                should_deliver=delivery.status == "pending",
                delivery=delivery,
            )

    def ensure_matching_deliveries(
        self,
        event_id: str,
        *,
        now_ts: int | None = None,
    ) -> tuple[DeliveryReservation, ...]:
        record = self.get_event(event_id)
        if record is None:
            raise _invalid_argument("event_id", "event does not exist")
        matches = self.match_subscriptions(record)
        return tuple(
            self.ensure_delivery(
                event_id=record.event.event_id,
                subscription_id=subscription.subscription_id,
                now_ts=now_ts,
            )
            for subscription in matches
        )

    def record_delivery_attempt(
        self,
        *,
        event_id: str,
        subscription_id: str,
        now_ts: int | None = None,
    ) -> MarketEventDelivery | None:
        claim = self.claim_delivery_attempt(
            event_id=event_id,
            subscription_id=subscription_id,
            now_ts=now_ts,
        )
        return claim.delivery if claim is not None else None

    def claim_delivery_attempt(
        self,
        *,
        event_id: str,
        subscription_id: str,
        now_ts: int | None = None,
    ) -> DeliveryClaim | None:
        clean_event_id = _safe_id(event_id, field="event_id")
        clean_subscription_id = _safe_id(subscription_id, field="subscription_id")
        now = self._now(now_ts)
        with self._write_lock, self._connect(write=True) as connection:
            row = connection.execute(
                """
                SELECT * FROM market_event_deliveries
                WHERE event_id = ? AND subscription_id = ?
                """,
                (clean_event_id, clean_subscription_id),
            ).fetchone()
            if row is None:
                return None
            delivery = _row_to_delivery(row)
            if delivery.status not in RETRYABLE_DELIVERY_STATUSES:
                return DeliveryClaim(acquired=False, delivery=delivery)
            enabled_row = connection.execute(
                "SELECT enabled FROM finance_subscriptions WHERE subscription_id = ?",
                (clean_subscription_id,),
            ).fetchone()
            if enabled_row is None or not bool(enabled_row["enabled"]):
                connection.execute(
                    """
                    UPDATE market_event_deliveries
                    SET status = 'cancelled', reason = 'subscription_disabled', updated_at = ?
                    WHERE event_id = ? AND subscription_id = ?
                    """,
                    (now, clean_event_id, clean_subscription_id),
                )
                acquired = False
            else:
                connection.execute(
                    """
                    UPDATE market_event_deliveries
                    SET status = 'processing', attempt_count = attempt_count + 1,
                        last_attempt_at = ?, reason = '', updated_at = ?
                    WHERE event_id = ? AND subscription_id = ?
                    """,
                    (now, now, clean_event_id, clean_subscription_id),
                )
                acquired = True
            updated = connection.execute(
                """
                SELECT * FROM market_event_deliveries
                WHERE event_id = ? AND subscription_id = ?
                """,
                (clean_event_id, clean_subscription_id),
            ).fetchone()
        return DeliveryClaim(acquired=acquired, delivery=_row_to_delivery(updated))

    def mark_delivery_delivered(
        self,
        *,
        event_id: str,
        subscription_id: str,
        analysis_id: str = "",
        now_ts: int | None = None,
    ) -> MarketEventDelivery | None:
        return self._finish_delivery(
            event_id=event_id,
            subscription_id=subscription_id,
            status="delivered",
            analysis_id=analysis_id,
            reason="",
            now_ts=now_ts,
        )

    def mark_delivery_failed(
        self,
        *,
        event_id: str,
        subscription_id: str,
        reason: str,
        analysis_id: str = "",
        now_ts: int | None = None,
    ) -> MarketEventDelivery | None:
        clean_reason = _bounded_text(reason, field="reason", max_length=2000)
        if not clean_reason:
            clean_reason = "delivery_failed"
        return self._finish_delivery(
            event_id=event_id,
            subscription_id=subscription_id,
            status="failed",
            analysis_id=analysis_id,
            reason=clean_reason,
            now_ts=now_ts,
        )

    def get_delivery(self, *, event_id: str, subscription_id: str) -> MarketEventDelivery | None:
        clean_event_id = _safe_id(event_id, field="event_id")
        clean_subscription_id = _safe_id(subscription_id, field="subscription_id")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM market_event_deliveries
                WHERE event_id = ? AND subscription_id = ?
                """,
                (clean_event_id, clean_subscription_id),
            ).fetchone()
        return _row_to_delivery(row) if row is not None else None

    def list_retryable_deliveries(self, *, limit: int = 100) -> tuple[MarketEventDelivery, ...]:
        clean_limit = _bounded_int(limit, field="limit", lower=1, upper=1000)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT d.*
                FROM market_event_deliveries AS d
                JOIN finance_subscriptions AS s ON s.subscription_id = d.subscription_id
                WHERE d.status IN ('pending', 'failed') AND s.enabled = 1
                ORDER BY d.updated_at, d.event_id, d.subscription_id
                LIMIT ?
                """,
                (clean_limit,),
            ).fetchall()
        return tuple(_row_to_delivery(row) for row in rows)

    def has_delivered_cluster(
        self,
        *,
        subscription_id: str,
        cluster_id: str,
        exclude_event_id: str = "",
    ) -> bool:
        clean_subscription_id = _safe_id(subscription_id, field="subscription_id")
        clean_cluster_id = _safe_id(cluster_id, field="cluster_id")
        clean_exclude = _safe_id(exclude_event_id, field="exclude_event_id") if exclude_event_id else ""
        clauses = [
            "d.subscription_id = ?",
            "d.status = 'delivered'",
            "e.cluster_id = ?",
        ]
        parameters: list[Any] = [clean_subscription_id, clean_cluster_id]
        if clean_exclude:
            clauses.append("e.event_id != ?")
            parameters.append(clean_exclude)
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT 1
                FROM market_event_deliveries AS d
                JOIN market_events AS e ON e.event_id = d.event_id
                WHERE {" AND ".join(clauses)}
                LIMIT 1
                """,
                tuple(parameters),
            ).fetchone()
        return row is not None

    def _finish_delivery(
        self,
        *,
        event_id: str,
        subscription_id: str,
        status: str,
        analysis_id: str,
        reason: str,
        now_ts: int | None,
    ) -> MarketEventDelivery | None:
        if status not in {"delivered", "failed"}:
            raise _invalid_argument("status", "unsupported final delivery status")
        clean_event_id = _safe_id(event_id, field="event_id")
        clean_subscription_id = _safe_id(subscription_id, field="subscription_id")
        clean_analysis_id = _bounded_text(analysis_id, field="analysis_id", max_length=240)
        now = self._now(now_ts)
        with self._write_lock, self._connect(write=True) as connection:
            row = connection.execute(
                """
                SELECT * FROM market_event_deliveries
                WHERE event_id = ? AND subscription_id = ?
                """,
                (clean_event_id, clean_subscription_id),
            ).fetchone()
            if row is None:
                return None
            current = _row_to_delivery(row)
            if current.status in {"delivered", "cancelled"}:
                return current
            delivered_at = now if status == "delivered" else None
            connection.execute(
                """
                UPDATE market_event_deliveries
                SET status = ?, analysis_id = ?, delivered_at = ?, reason = ?, updated_at = ?
                WHERE event_id = ? AND subscription_id = ?
                """,
                (
                    status,
                    clean_analysis_id or current.analysis_id,
                    delivered_at,
                    reason,
                    now,
                    clean_event_id,
                    clean_subscription_id,
                ),
            )
            updated = connection.execute(
                """
                SELECT * FROM market_event_deliveries
                WHERE event_id = ? AND subscription_id = ?
                """,
                (clean_event_id, clean_subscription_id),
            ).fetchone()
        return _row_to_delivery(updated)

    def _cancel_open_deliveries(
        self,
        connection: sqlite3.Connection,
        subscription_id: str,
        now_ts: int,
    ) -> None:
        connection.execute(
            """
            UPDATE market_event_deliveries
            SET status = 'cancelled', reason = 'subscription_disabled', updated_at = ?
            WHERE subscription_id = ? AND status IN ('pending', 'processing', 'failed')
            """,
            (now_ts, subscription_id),
        )

    def _resolve_cluster_id(
        self,
        connection: sqlite3.Connection,
        event: MarketEvent,
        *,
        exclude_event_id: str = "",
    ) -> str:
        lower = event.published_at - self.cluster_window_seconds
        upper = event.published_at + self.cluster_window_seconds
        parameters: list[Any] = [event.code, event.content_type, lower, upper]
        exclusion = ""
        if exclude_event_id:
            exclusion = "AND event_id != ?"
            parameters.append(exclude_event_id)
        rows = connection.execute(
            f"""
            SELECT event_id, title, cluster_id, published_at
            FROM market_events
            WHERE code = ? AND content_type = ? AND published_at BETWEEN ? AND ?
              {exclusion}
            ORDER BY ABS(published_at - ?) ASC, published_at DESC
            LIMIT 50
            """,
            tuple(parameters + [event.published_at]),
        ).fetchall()
        normalized_title = _normalize_title(event.title)
        best_cluster = ""
        best_score = 0.0
        for row in rows:
            candidate_title = _normalize_title(str(row["title"] or ""))
            score = difflib.SequenceMatcher(None, normalized_title, candidate_title).ratio()
            if score >= self.cluster_similarity_threshold and score > best_score:
                best_score = score
                best_cluster = str(row["cluster_id"] or "")
        if best_cluster:
            return best_cluster
        bucket = event.published_at // self.cluster_window_seconds
        digest = hashlib.sha256(
            f"{event.code}|{event.content_type}|{normalized_title}|{bucket}".encode("utf-8")
        ).hexdigest()[:24]
        return f"cluster:{digest}"

    def _now(self, value: int | None) -> int:
        raw = int(self._clock()) if value is None else value
        return _positive_int(raw, field="now_ts")


def _validate_event(event: MarketEvent) -> MarketEvent:
    if not isinstance(event, MarketEvent):
        raise MarketDataValidationError(field="event", reason="MarketEvent is required")
    provider = _bounded_text(event.provider, field="provider", max_length=120)
    event_id = _safe_id(event.event_id, field="event_id")
    published_at = _positive_int(event.published_at, field="published_at")
    produced_at = _optional_positive_int(event.produced_at, field="produced_at")
    received_at = _positive_int(event.received_at, field="received_at")
    code = normalize_market_code(event.code, field="code", provider=provider)
    content_type = _bounded_text(event.content_type, field="content_type", max_length=120).lower()
    title = _bounded_text(event.title, field="title", max_length=2000)
    if not provider or not content_type or not title:
        missing = "provider" if not provider else "content_type" if not content_type else "title"
        raise MarketDataValidationError(field=missing, reason="value is required", provider=provider)
    source = _bounded_text(event.source, field="source", max_length=500)
    url = _bounded_text(event.url, field="url", max_length=4000)
    sentiment = _bounded_text(event.sentiment, field="sentiment", max_length=80).lower() or "unknown"
    labels = _normalized_aliases(event.labels, field="labels", max_items=64, max_length=120)
    sector_code = _bounded_text(event.sector_code, field="sector_code", max_length=80).upper()
    raw_hash = str(event.raw_hash or "").strip().lower()
    if not _RAW_HASH_RE.fullmatch(raw_hash):
        raise MarketDataValidationError(field="raw_hash", reason="raw_hash must be a sha256 hex digest")
    return MarketEvent(
        provider=provider,
        event_id=event_id,
        published_at=published_at,
        produced_at=produced_at,
        received_at=received_at,
        code=code,
        content_type=content_type,
        title=title,
        source=source,
        url=url,
        sentiment=sentiment,
        labels=labels,
        sector_code=sector_code,
        raw_hash=raw_hash,
    )


def _event_sql_values(event: MarketEvent) -> tuple[Any, ...]:
    return (
        event.provider,
        event.published_at,
        event.produced_at,
        event.received_at,
        event.code,
        event.content_type,
        event.title,
        event.source,
        event.url,
        event.sentiment,
        _json_dumps(list(event.labels), field="labels"),
        event.sector_code,
        event.raw_hash,
    )


def _row_to_stored_event(row: sqlite3.Row) -> StoredMarketEvent:
    labels = _json_loads_list(row["labels_json"])
    event = MarketEvent(
        provider=str(row["provider"] or ""),
        event_id=str(row["event_id"] or ""),
        published_at=int(row["published_at"]),
        produced_at=int(row["produced_at"]) if row["produced_at"] is not None else None,
        received_at=int(row["received_at"]),
        code=str(row["code"] or ""),
        content_type=str(row["content_type"] or ""),
        title=str(row["title"] or ""),
        source=str(row["source"] or ""),
        url=str(row["url"] or ""),
        sentiment=str(row["sentiment"] or "unknown"),
        labels=tuple(str(item) for item in labels if str(item).strip()),
        sector_code=str(row["sector_code"] or ""),
        raw_hash=str(row["raw_hash"] or ""),
    )
    return StoredMarketEvent(
        event=event,
        cluster_id=str(row["cluster_id"] or ""),
        status=str(row["status"] or "active"),
        revision=max(1, int(row["revision"] or 1)),
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
    )


def _normalize_subscription_input(**values: Any) -> dict[str, Any]:
    finance_mode = str(values.get("finance_mode") or "push").strip().lower()
    if finance_mode not in {"qa", "push"}:
        raise _invalid_argument("finance_mode", "finance_mode must be qa or push")
    filters = _normalize_subscription_filters(values.get("filters"))
    delivery_policy = values.get("delivery_policy") or {}
    if not isinstance(delivery_policy, Mapping):
        raise _invalid_argument("delivery_policy", "delivery_policy must be an object")
    enabled = values.get("enabled")
    if not isinstance(enabled, bool):
        raise _invalid_argument("enabled", "enabled must be a boolean")
    session_id = _required_text(values.get("session_id"), field="session_id", max_length=240)
    is_group = values.get("is_group")
    if is_group is None:
        is_group = session_id.startswith("qq_group_")
    if not isinstance(is_group, bool):
        raise _invalid_argument("is_group", "is_group must be a boolean")
    return {
        "subscription_id": _safe_id(values.get("subscription_id"), field="subscription_id"),
        "client": _required_text(values.get("client"), field="client", max_length=40).lower(),
        "target_id": _required_text(values.get("target_id"), field="target_id", max_length=200),
        "is_group": is_group,
        "session_id": session_id,
        "profile_user_id": _required_text(values.get("profile_user_id"), field="profile_user_id", max_length=240),
        "character_pack_id": _bounded_text(values.get("character_pack_id"), field="character_pack_id", max_length=120),
        "finance_mode": finance_mode,
        "enabled": enabled,
        "filters_json": _json_dumps(filters, field="filters"),
        "delivery_policy_json": _json_dumps(dict(delivery_policy), field="delivery_policy"),
        "created_by_actor_id": _bounded_text(
            values.get("created_by_actor_id"), field="created_by_actor_id", max_length=240
        ),
    }


def _normalize_subscription_filters(value: Any) -> dict[str, list[str]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise _invalid_argument("filters", "filters must be an object")
    unknown = {str(key) for key in value.keys()} - SUBSCRIPTION_FILTER_KEYS
    if unknown:
        raise _invalid_argument("filters", f"unsupported filter keys: {', '.join(sorted(unknown))}")
    normalized: dict[str, list[str]] = {}
    if "codes" in value:
        normalized["codes"] = list(_normalized_codes(value.get("codes") or (), field="filters.codes"))
    for key in ("content_types", "labels_any", "providers"):
        if key in value:
            normalized[key] = list(_normalized_text_values(value.get(key) or (), field=f"filters.{key}"))
    if "sector_codes" in value:
        normalized["sector_codes"] = list(
            _normalized_codes(value.get("sector_codes") or (), field="filters.sector_codes")
        )
    return {key: items for key, items in normalized.items() if items}


def _subscription_matches_event(
    subscription: FinanceSubscription,
    event: MarketEvent,
    *,
    watch_codes: set[str],
) -> bool:
    if not subscription.enabled:
        return False
    filters = dict(subscription.filters)
    filter_codes = set(str(item) for item in filters.get("codes", []))
    code_scope = set(watch_codes) | filter_codes
    has_non_code_filter = any(filters.get(key) for key in ("content_types", "sector_codes", "labels_any", "providers"))
    if not code_scope and not has_non_code_filter:
        return False
    if code_scope and event.code not in code_scope:
        return False
    content_types = set(str(item) for item in filters.get("content_types", []))
    if content_types and event.content_type not in content_types:
        return False
    sector_codes = set(str(item) for item in filters.get("sector_codes", []))
    if sector_codes and event.sector_code not in sector_codes:
        return False
    providers = set(str(item) for item in filters.get("providers", []))
    if providers and event.provider.lower() not in providers:
        return False
    labels_any = set(str(item) for item in filters.get("labels_any", []))
    if labels_any and not labels_any.intersection(event.labels):
        return False
    return True


def _row_to_subscription(row: sqlite3.Row) -> FinanceSubscription:
    return FinanceSubscription(
        subscription_id=str(row["subscription_id"] or ""),
        client=str(row["client"] or ""),
        target_id=str(row["target_id"] or ""),
        is_group=bool(row["is_group"]),
        session_id=str(row["session_id"] or ""),
        profile_user_id=str(row["profile_user_id"] or ""),
        character_pack_id=str(row["character_pack_id"] or ""),
        finance_mode=str(row["finance_mode"] or ""),
        enabled=bool(row["enabled"]),
        filters=_json_loads_object(row["filters_json"]),
        delivery_policy=_json_loads_object(row["delivery_policy_json"]),
        created_by_actor_id=str(row["created_by_actor_id"] or ""),
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
    )


def _row_to_watchlist_item(row: sqlite3.Row) -> WatchlistItem:
    aliases = _json_loads_list(row["aliases_json"])
    return WatchlistItem(
        subscription_id=str(row["subscription_id"] or ""),
        provider=str(row["provider"] or ""),
        code=str(row["code"] or ""),
        display_name=str(row["display_name"] or ""),
        aliases=tuple(str(item) for item in aliases if str(item).strip()),
        priority=float(row["priority"]),
        created_by_actor_id=str(row["created_by_actor_id"] or ""),
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
    )


def _row_to_market_security(row: sqlite3.Row) -> MarketSecurity:
    aliases = _json_loads_list(row["aliases_json"])
    return MarketSecurity(
        provider=str(row["provider"] or ""),
        code=str(row["code"] or ""),
        display_name=str(row["display_name"] or ""),
        aliases=tuple(str(item) for item in aliases if str(item).strip()),
        market=str(row["market"] or ""),
        security_type=str(row["security_type"] or ""),
        source=str(row["source"] or ""),
        as_of=int(row["as_of"]),
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
    )


def _row_to_delivery(row: sqlite3.Row) -> MarketEventDelivery:
    status = str(row["status"] or "pending")
    if status not in DELIVERY_STATUSES:
        status = "failed"
    return MarketEventDelivery(
        event_id=str(row["event_id"] or ""),
        subscription_id=str(row["subscription_id"] or ""),
        status=status,
        analysis_id=str(row["analysis_id"] or ""),
        attempt_count=max(0, int(row["attempt_count"] or 0)),
        last_attempt_at=int(row["last_attempt_at"]) if row["last_attempt_at"] is not None else None,
        delivered_at=int(row["delivered_at"]) if row["delivered_at"] is not None else None,
        reason=str(row["reason"] or ""),
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
    )


def _normalize_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return _TITLE_TOKEN_RE.sub("", normalized)


def _normalize_security_alias(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return _TITLE_TOKEN_RE.sub("", normalized)


def _unique_security_aliases(values: Iterable[Any]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        normalized = _normalize_security_alias(text)
        if not text or not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(text)
    return tuple(result)


def _normalized_security_alias_values(
    values: Iterable[Any],
    *,
    max_items: int,
    max_length: int,
) -> tuple[str, ...]:
    if isinstance(values, str):
        raw_values = [values]
    else:
        try:
            raw_values = list(values)
        except TypeError as exc:
            raise _invalid_argument("aliases", "value must be a list") from exc
    if len(raw_values) > max_items:
        raise _invalid_argument("aliases", f"at most {max_items} values are allowed")
    result: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        text = _bounded_text(value, field="aliases", max_length=max_length)
        normalized = _normalize_security_alias(text)
        if not text or not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(text)
    return tuple(result)


def _json_dumps(value: Any, *, field: str) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise _invalid_argument(field, "value must be JSON serializable") from exc


def _json_loads_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _json_loads_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return []
    return list(parsed) if isinstance(parsed, list) else []


def _safe_id(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not _SAFE_ID_RE.fullmatch(text):
        raise _invalid_argument(field, "value contains unsupported characters or has an invalid length")
    return text


def _required_text(value: Any, *, field: str, max_length: int) -> str:
    text = _bounded_text(value, field=field, max_length=max_length)
    if not text:
        raise _invalid_argument(field, "value is required")
    return text


def _bounded_text(value: Any, *, field: str, max_length: int) -> str:
    text = str(value or "").strip()
    if len(text) > max_length:
        raise _invalid_argument(field, f"text exceeds maximum length of {max_length}")
    return text


def _normalized_codes(values: Iterable[Any], *, field: str) -> tuple[str, ...]:
    if isinstance(values, str):
        raw_values = [values]
    else:
        try:
            raw_values = list(values)
        except TypeError as exc:
            raise _invalid_argument(field, "value must be a list") from exc
    result: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        code = normalize_market_code(
            value,
            field=field,
            status="invalid_arguments",
            error_code="invalid_arguments",
        )
        if code in seen:
            continue
        seen.add(code)
        result.append(code)
    if len(result) > 200:
        raise _invalid_argument(field, "at most 200 codes are allowed")
    return tuple(result)


def _normalized_text_values(values: Iterable[Any], *, field: str) -> tuple[str, ...]:
    if isinstance(values, str):
        raw_values = [values]
    else:
        try:
            raw_values = list(values)
        except TypeError as exc:
            raise _invalid_argument(field, "value must be a list") from exc
    result: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        text = _bounded_text(value, field=field, max_length=120).lower()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    if len(result) > 100:
        raise _invalid_argument(field, "at most 100 values are allowed")
    return tuple(result)


def _normalized_aliases(
    values: Iterable[Any],
    *,
    field: str = "aliases",
    max_items: int = 32,
    max_length: int = 120,
) -> tuple[str, ...]:
    if isinstance(values, str):
        raw_values = [values]
    else:
        try:
            raw_values = list(values)
        except TypeError as exc:
            raise _invalid_argument(field, "value must be a list") from exc
    result: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        text = _bounded_text(value, field=field, max_length=max_length).lower()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    if len(result) > max_items:
        raise _invalid_argument(field, f"at most {max_items} values are allowed")
    return tuple(result)


def _positive_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise _invalid_argument(field, "boolean is not a positive integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise _invalid_argument(field, "value must be a positive integer") from exc
    if number <= 0:
        raise _invalid_argument(field, "value must be positive")
    return number


def _optional_positive_int(value: Any, *, field: str) -> int | None:
    if value is None or value == "":
        return None
    return _positive_int(value, field=field)


def _bounded_int(value: Any, *, field: str, lower: int, upper: int) -> int:
    if isinstance(value, bool):
        raise _invalid_argument(field, "boolean is not an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise _invalid_argument(field, "value must be an integer") from exc
    if number < lower or number > upper:
        raise _invalid_argument(field, f"value must be between {lower} and {upper}")
    return number


def _bounded_float(value: Any, *, field: str, lower: float, upper: float) -> float:
    if isinstance(value, bool):
        raise _invalid_argument(field, "boolean is not numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise _invalid_argument(field, "value must be numeric") from exc
    if not math.isfinite(number) or number < lower or number > upper:
        raise _invalid_argument(field, f"value must be between {lower} and {upper}")
    return number


def _escape_like(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _invalid_argument(field: str, reason: str) -> MarketDataValidationError:
    return MarketDataValidationError(
        field=field,
        reason=reason,
        code="invalid_arguments",
        status="invalid_arguments",
        provider="market_event_store",
    )
