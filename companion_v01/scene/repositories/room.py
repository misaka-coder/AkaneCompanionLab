from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ..contracts import Identity, RoomState


def room_key(identity: Identity) -> str:
    # Equipment is shared across sessions of this bound character/profile.
    return json.dumps([identity.profile_user_id, identity.character_pack_id], ensure_ascii=False)


class RoomRepository:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS rooms (scope TEXT PRIMARY KEY, state TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS actions (
                    scope TEXT NOT NULL, request_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    result TEXT NOT NULL, PRIMARY KEY(scope, request_id));
                CREATE TABLE IF NOT EXISTS outbox (
                    event_id TEXT PRIMARY KEY, identity TEXT NOT NULL,
                    fact TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0);
            """)

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
    def read(db, identity: Identity) -> RoomState:
        row = db.execute("SELECT state FROM rooms WHERE scope=?", (room_key(identity),)).fetchone()
        return RoomState.model_validate_json(row[0]) if row else RoomState()

    @staticmethod
    def save(db, identity: Identity, state: RoomState):
        db.execute("INSERT OR REPLACE INTO rooms VALUES (?,?)", (room_key(identity), state.model_dump_json()))
