"""Verify owner-private migration on isolated SQLite backup copies only.

Run from the repository root. Never enables the feature or modifies source data.
The destination contains private data and must stay outside version control.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from companion_v01.care_runtime import CareRuntimeStore
from companion_v01.care_state_migration import ACCOUNT_FIELDS, RELATION_FIELDS, migrate_care_state
from companion_v01.memcore_integration.manager import MemcoreManager
from companion_v01.owner_private_session import OwnerPrivateSessionPolicy
from companion_v01.plugin_conversation_refs import PluginConversationReferenceAuthority
from companion_v01.store import MemoryStore


class NoNetworkEmbedding:
    name = "migration-check"
    dimension = 1536

    def embed(self, text):
        raise AssertionError("migration verification must not call embedding")


def verify(source: Path, destination: Path) -> dict:
    source, destination = source.resolve(strict=True), destination.resolve()
    if destination == source or destination.is_relative_to(source) or destination.exists():
        raise ValueError("choose a new isolated destination outside the runtime data directory")
    destination.mkdir(parents=True)
    started = time.monotonic()
    for name in ("akane_memory_v01.db", "memcore_v01.db"):
        with sqlite3.connect((source / name).as_uri() + "?mode=ro", uri=True) as src:
            with sqlite3.connect(destination / name) as dst:
                def progress(*_):
                    if time.monotonic() - started > 90:
                        raise TimeoutError("database backup timeout; source remains untouched")
                src.backup(dst, pages=1024, progress=progress)
                assert dst.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    care_bytes = (source / "care_runtime.json").read_bytes()
    original = json.loads(care_bytes)
    (destination / "care_runtime.json").write_bytes(care_bytes)
    host = MemoryStore(destination)
    origins = host.list_care_conversation_origins()
    migrated = migrate_care_state(deepcopy(original), known_conversations=origins)
    assert migrate_care_state(deepcopy(migrated), known_conversations=origins) == migrated
    if original.get("schema_version") != "akane.care_runtime.v3":
        expected_accounts = {
            role: {user: {k: v for k, v in record.items() if k in ACCOUNT_FIELDS}
                   for user, record in users.items() if isinstance(record, dict)}
            for role, users in original.get("relations", {}).items() if isinstance(users, dict)
        }
        assert migrated["accounts"] == expected_accounts, "account values changed or duplicated"
        assert migrated["legacy_v2"]["characters"] == original.get("characters", {})
        assert migrated["legacy_v2"]["relations"] == original.get("relations", {})
        for char, conversations in migrated["relationships"].items():
            for users in conversations.values():
                for user, relationship in users.items():
                    old = original["relations"][char][user]
                    assert relationship == {key: value for key, value in old.items() if key in RELATION_FIELDS}, \
                        "relationship history changed during migration"
    care = CareRuntimeStore(destination / "care_runtime.json", known_conversations=lambda: origins)
    with care._lock:
        actual = care._load()
    assert actual == migrated
    if original.get("schema_version") != "akane.care_runtime.v3":
        assert (destination / "care_runtime.json.pre-v3.bak").read_bytes() == care_bytes
    manager = MemcoreManager(backend="memcore", storage_path=destination / "memcore_v01.db",
        visible_scope="conversation", enable_flavor=True, shadow_compare=False,
        llm=object(), embedding_provider=NoNetworkEmbedding())
    refs = PluginConversationReferenceAuthority(destination / "verification.key", instance_id="isolated-check")
    policy = OwnerPrivateSessionPolicy(enabled=True, master_qq="1", references=refs, manager=manager, store=host)
    verified_roles = 0
    try:
        roles = sorted({x["character_pack_id"] for x in origins if x["session_id"] == "master"})
        for role in roles:
            if role.startswith("service_"):
                continue
            qq = manager._get_system(profile_user_id="master", session_id="master", character_pack_id=role, warm_index=False)
            before = qq.build_context_projection(provider_profile="openai_chat").payloads
            payload = {"real_user_id": "master", "user_id": "isolated-desktop", "client_mode": "desktop_pet", "character_pack_id": role}
            policy.stamp_desktop(payload, owner_authenticated=True, character=role)
            with policy.scope(payload):
                desktop = manager._get_system(profile_user_id="master", session_id="isolated-desktop", character_pack_id=role, warm_index=False)
                assert desktop is qq
                assert desktop.build_context_projection(provider_profile="openai_chat").payloads == before
            verified_roles += 1
    finally:
        manager.close()
    report = {
        "source_schema": original.get("schema_version"), "destination_schema": actual["schema_version"],
        "known_conversations": len(origins),
        "accounts_preserved": sum(len(x) for x in migrated["accounts"].values()),
        "seeded_relationships": sum(len(users) for scopes in migrated["relationships"].values() for users in scopes.values()),
        "shared_history_roles_verified": verified_roles,
        "migration_idempotent": True, "legacy_desktop_assets_preserved": True,
        "sqlite_quick_check": "ok", "source_care_sha256": hashlib.sha256(care_bytes).hexdigest(),
        "note": "Per-database consistent backups of live data; repeat after quiescing before deployment.",
    }
    (destination / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.source, args.destination), ensure_ascii=False, indent=2))
