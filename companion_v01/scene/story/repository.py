from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ..contracts import Identity, Presentation, StoryPack, StoryRunState
from ..repositories.room import room_key


class StoryRepository:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS story_runs (
                    scope TEXT NOT NULL,
                    story_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    current_node_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    state TEXT NOT NULL,
                    PRIMARY KEY (scope, story_id)
                );
                CREATE TABLE IF NOT EXISTS story_completions (
                    scope TEXT NOT NULL,
                    story_id TEXT NOT NULL,
                    ending_id TEXT NOT NULL,
                    completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (scope, story_id, ending_id)
                );
                CREATE TABLE IF NOT EXISTS story_run_packs (
                    run_id TEXT PRIMARY KEY, pack TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scene_turns (
                    turn_id TEXT PRIMARY KEY, scope TEXT NOT NULL, request_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, status TEXT NOT NULL, presentation TEXT,
                    UNIQUE(scope, request_id)
                );
                CREATE TABLE IF NOT EXISTS scene_receipts (
                    turn_id TEXT NOT NULL, beat_id TEXT NOT NULL, kind TEXT NOT NULL,
                    PRIMARY KEY(turn_id, beat_id, kind)
                );
                CREATE TABLE IF NOT EXISTS outbox (
                    event_id TEXT PRIMARY KEY, identity TEXT NOT NULL,
                    fact TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0
                );
            """)

    @contextmanager
    def read_connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def pin_pack(db, run_id: str, pack: StoryPack):
        db.execute("INSERT OR IGNORE INTO story_run_packs VALUES (?,?)", (run_id, pack.model_dump_json()))

    @staticmethod
    def get_pack(db, run_id: str) -> StoryPack | None:
        row = db.execute("SELECT pack FROM story_run_packs WHERE run_id=?", (run_id,)).fetchone()
        return StoryPack.model_validate_json(row[0]) if row else None

    @staticmethod
    def get_run(db: sqlite3.Connection, identity: Identity, story_id: str) -> StoryRunState | None:
        row = db.execute(
            "SELECT state FROM story_runs WHERE scope=? AND story_id=?",
            (room_key(identity), story_id),
        ).fetchone()
        if not row:
            return None
        return StoryRunState.model_validate_json(row[0])

    @staticmethod
    def save_run(db: sqlite3.Connection, identity: Identity, state: StoryRunState):
        db.execute(
            "INSERT OR REPLACE INTO story_runs (scope, story_id, run_id, current_node_id, status, state) VALUES (?,?,?,?,?,?)",
            (room_key(identity), state.story_id, state.run_id, state.current_node_id, state.status, state.model_dump_json()),
        )

    @staticmethod
    def delete_run(db: sqlite3.Connection, identity: Identity, story_id: str):
        db.execute("DELETE FROM story_runs WHERE scope=? AND story_id=?", (room_key(identity), story_id))

    @staticmethod
    def list_runs(db: sqlite3.Connection, identity: Identity) -> dict[str, StoryRunState]:
        rows = db.execute("SELECT story_id, state FROM story_runs WHERE scope=?", (room_key(identity),)).fetchall()
        return {r[0]: StoryRunState.model_validate_json(r[1]) for r in rows}

    @staticmethod
    def record_completion(db: sqlite3.Connection, identity: Identity, story_id: str, ending_id: str):
        db.execute(
            "INSERT OR IGNORE INTO story_completions (scope, story_id, ending_id) VALUES (?,?,?)",
            (room_key(identity), story_id, ending_id),
        )

    @staticmethod
    def get_completed_endings(db: sqlite3.Connection, identity: Identity, story_id: str) -> list[str]:
        rows = db.execute(
            "SELECT ending_id FROM story_completions WHERE scope=? AND story_id=?",
            (room_key(identity), story_id),
        ).fetchall()
        return [r[0] for r in rows]

    @staticmethod
    def record_turn_presentation(
        db: sqlite3.Connection,
        identity: Identity,
        turn_id: str,
        presentation: Presentation,
        fingerprint: str = "",
    ):
        scope = identity.model_dump_json()
        db.execute(
            """
            INSERT OR REPLACE INTO scene_turns (turn_id, scope, request_id, fingerprint, status, presentation)
            VALUES (?, ?, ?, ?, 'completed', ?)
            """,
            (turn_id, scope, turn_id, fingerprint or turn_id, presentation.model_dump_json()),
        )
