"""Music control permissions — per-profile store for "让她也能" toggles.

Each of the four controls (pause / next / prev / recommend) can be
independently revoked by the user. Default-open semantics: if no row
exists for a control, it is treated as enabled. Only an explicit
``enabled=0`` row revokes.

Design mirrors ``co_listen_store.py``: self-contained module, callers
pass in an ``sqlite3`` connection (typically via ``MemoryStore._connect()``).
``ALLOWED_CONTROL_NAMES`` is the single source of truth for valid control
names; front-end, route layer, and assembler all reference this value.
"""

from __future__ import annotations

import sqlite3

ALLOWED_CONTROL_NAMES: frozenset[str] = frozenset({"pause", "next", "prev", "recommend"})

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS music_control_permissions (
    profile_user_id TEXT NOT NULL,
    control_name    TEXT NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    updated_at      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (profile_user_id, control_name)
);
"""


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(_SCHEMA_SQL)


def get_enabled_controls(
    connection: sqlite3.Connection,
    *,
    profile_user_id: str,
) -> set[str]:
    """Return the set of currently-enabled control names for *profile_user_id*.

    Default-open: a missing row is treated as enabled=1. Only an explicit
    ``enabled=0`` row revokes a control. The returned set is always a subset
    of ``ALLOWED_CONTROL_NAMES``.
    """
    profile_user_id = str(profile_user_id or "").strip()
    if not profile_user_id:
        return set(ALLOWED_CONTROL_NAMES)
    rows = connection.execute(
        "SELECT control_name, enabled FROM music_control_permissions WHERE profile_user_id = ?",
        (profile_user_id,),
    ).fetchall()
    disabled = {str(row[0]) for row in rows if not int(row[1] or 0)}
    return ALLOWED_CONTROL_NAMES - disabled


def set_control_enabled(
    connection: sqlite3.Connection,
    *,
    profile_user_id: str,
    control_name: str,
    enabled: bool,
    now_ts: int,
) -> None:
    """Upsert a single control permission row.

    Raises ``ValueError`` for any *control_name* not in
    ``ALLOWED_CONTROL_NAMES``.
    """
    control_name = str(control_name or "").strip()
    if control_name not in ALLOWED_CONTROL_NAMES:
        raise ValueError(f"Unknown control name: {control_name!r}")
    profile_user_id = str(profile_user_id or "").strip()
    if not profile_user_id:
        return
    connection.execute(
        """
        INSERT INTO music_control_permissions (profile_user_id, control_name, enabled, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(profile_user_id, control_name)
        DO UPDATE SET enabled = excluded.enabled, updated_at = excluded.updated_at
        """,
        (profile_user_id, control_name, int(bool(enabled)), int(now_ts)),
    )


def bulk_set_controls(
    connection: sqlite3.Connection,
    *,
    profile_user_id: str,
    controls: dict[str, bool],
    now_ts: int,
) -> None:
    """Atomically apply a partial update.

    Keys not in *controls* are untouched. Keys not in
    ``ALLOWED_CONTROL_NAMES`` are silently skipped (no error), allowing the
    route layer to forward raw user input without pre-filtering.
    """
    for control_name, enabled in controls.items():
        if control_name not in ALLOWED_CONTROL_NAMES:
            continue
        set_control_enabled(
            connection,
            profile_user_id=profile_user_id,
            control_name=control_name,
            enabled=bool(enabled),
            now_ts=int(now_ts),
        )
