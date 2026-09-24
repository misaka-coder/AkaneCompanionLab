"""Copy a test checkpoint using SQLite backup rather than locked WAL sidecars."""
import shutil
import sqlite3
from contextlib import closing


def copy_voice_state(source, target):
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("*.sqlite3", "*.sqlite3-wal", "*.sqlite3-shm"))
    for database in source.rglob("*.sqlite3"):
        destination = target / database.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(database)) as reader, closing(sqlite3.connect(destination)) as writer:
            reader.backup(writer)
