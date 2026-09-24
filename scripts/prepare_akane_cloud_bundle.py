#!/usr/bin/env python3
"""Build a secret-free, uploadable Akane named-instance data bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tarfile
import uuid
from pathlib import Path
from typing import Any, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from companion_v01.instance_profile import InstanceProfileError, resolve_instance_context
from scripts.migrate_akane_instance import MigrationError, migrate_instance


BUNDLE_SCHEMA_VERSION = 1


class CloudBundleError(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = str(reason or "cloud_bundle_failed")
        super().__init__(self.reason)


def prepare_cloud_bundle(
    *,
    source_root: Path,
    output_dir: Path,
    instance_id: str,
    character_pack_id: str,
    qq_profile_ref: str,
    source_instance_id: str = "local-default",
    source_workspace: Path | None = None,
    care_enabled: bool = True,
    port: int = 10001,
    archive_path: Path | None = None,
    additional_character_pack_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Create one minimal continuity bundle without local secrets or caches.

    The source must be offline.  Only the selected character pack and the
    authoritative memory/state files copied by ``migrate_instance`` enter the
    bundle.  QQ delivery state and plugin state always start fresh because this
    workflow targets a new cloud Bot binding.
    """

    output = _new_output_directory(output_dir)
    incomplete = output / "bundle-incomplete.json"
    _atomic_json(
        incomplete,
        {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "instance_id": str(instance_id or "").strip(),
            "status": "incomplete",
            "reason": "bundle_preparation_in_progress",
        },
    )
    data_root = output / "data"
    try:
        migration = migrate_instance(
            source_root=source_root,
            target_root=data_root,
            instance_id=instance_id,
            character_pack_id=character_pack_id,
            source_instance_id=source_instance_id,
            source_workspace=source_workspace,
            care_enabled=care_enabled,
            qq_profile_ref=qq_profile_ref,
            copy_qq_state=False,
            copy_all_character_packs=False,
            copy_plugin_state=False,
            additional_character_pack_ids=additional_character_pack_ids,
        )
        character_pack_ids = list(migration["character_pack_ids"])
        verification = verify_cloud_data_root(
            data_root,
            expected_instance_id=instance_id,
            expected_character_pack_id=character_pack_id,
            expected_qq_profile_ref=qq_profile_ref,
            expected_character_pack_ids=character_pack_ids,
        )
        _atomic_text(
            output / "instance.env.example",
            _build_environment_template(
                instance_id=instance_id,
                qq_profile_ref=qq_profile_ref,
                port=port,
            ),
        )
        _atomic_text(
            output / "DEPLOY.txt",
            _build_deploy_instructions(instance_id=instance_id),
        )
        manifest = {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "instance_id": instance_id,
            "character_pack_id": character_pack_id,
            "character_pack_ids": character_pack_ids,
            "qq_profile_ref": qq_profile_ref,
            "data_profile": "core-continuity",
            "migration": migration,
            "verification": verification,
            "excluded": [
                "local_secrets",
                "saved_model_service_config",
                "qq_runtime_state",
                "plugin_state",
                "chroma_derived_index",
                "logs_and_caches",
                "character_local_state",
                "historical_attachment_blobs",
                "historical_generated_file_blobs",
            ],
            "files": _build_inventory(data_root),
        }
        _atomic_json(output / "bundle-manifest.json", manifest)
        incomplete.unlink(missing_ok=True)

        archive_result: dict[str, Any] = {}
        if archive_path is not None:
            archive_result = _create_archive(
                output,
                archive_path=archive_path,
                archive_root_name=instance_id,
            )
        return {
            "ok": True,
            "status": "completed",
            "instance_id": instance_id,
            "character_pack_id": character_pack_id,
            "character_pack_ids": character_pack_ids,
            "file_count": verification["file_count"],
            "total_bytes": verification["total_bytes"],
            "output_dir": str(output),
            **archive_result,
        }
    except (CloudBundleError, MigrationError, InstanceProfileError) as exc:
        reason = str(getattr(exc, "reason", "cloud_bundle_failed") or "cloud_bundle_failed")
        _mark_incomplete(incomplete, instance_id=instance_id, reason=reason)
        raise CloudBundleError(reason) from exc
    except Exception as exc:
        _mark_incomplete(incomplete, instance_id=instance_id, reason="unexpected_cloud_bundle_failure")
        raise CloudBundleError("unexpected_cloud_bundle_failure") from exc


def verify_cloud_data_root(
    data_root: Path,
    *,
    expected_instance_id: str,
    expected_character_pack_id: str,
    expected_qq_profile_ref: str,
    expected_character_pack_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    try:
        candidate = Path(data_root).expanduser()
        if candidate.is_symlink() or not candidate.is_dir():
            raise CloudBundleError("bundle_data_root_invalid")
        root = candidate.resolve(strict=True)
    except CloudBundleError:
        raise
    except OSError as exc:
        raise CloudBundleError("bundle_data_root_invalid") from exc

    forbidden = (
        root / "migration-incomplete.json",
        root / "users_data" / "_local",
        root / "state" / "qq_gateway_state.json",
        root / "instances" / expected_instance_id / "plugins",
        root / "cache",
        root / "logs",
        root / "run",
        root / "users_data" / "akane_memory_v01" / "chroma",
        root / "users_data" / "akane_memory_v01" / "attachment_inbox_files",
        root / "users_data" / "akane_memory_v01" / "generated_files",
        root / "users_data" / "akane_memory_v01" / "generated_work",
    )
    if any(path.exists() or path.is_symlink() for path in forbidden):
        raise CloudBundleError("bundle_contains_excluded_runtime_data")

    files: list[Path] = []
    for entry in root.rglob("*"):
        if entry.is_symlink():
            raise CloudBundleError("bundle_contains_symlink")
        if entry.is_file():
            if "_local" in entry.relative_to(root).parts or entry.name == ".env":
                raise CloudBundleError("bundle_contains_local_config")
            files.append(entry)

    try:
        binding = json.loads((root / "instance-binding.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CloudBundleError("bundle_binding_invalid") from exc
    if binding != {"instance_id": expected_instance_id, "schema_version": 1}:
        raise CloudBundleError("bundle_binding_mismatch")

    try:
        context = resolve_instance_context(
            data_root=root,
            selected_instance_id=expected_instance_id,
        )
    except InstanceProfileError as exc:
        raise CloudBundleError(str(exc.reason or "bundle_manifest_invalid")) from exc
    if context.character_pack_id != expected_character_pack_id:
        raise CloudBundleError("bundle_character_pack_mismatch")
    qq = context.channels.qq
    if not qq.enabled or qq.profile_ref != expected_qq_profile_ref:
        raise CloudBundleError("bundle_qq_profile_mismatch")

    characters_root = root / "characters"
    character_dirs = sorted(path.name for path in characters_root.iterdir() if path.is_dir())
    expected_character_dirs = sorted(
        set(expected_character_pack_ids or (expected_character_pack_id,))
    )
    if expected_character_pack_id not in expected_character_dirs:
        raise CloudBundleError("bundle_default_character_missing")
    if character_dirs != expected_character_dirs:
        raise CloudBundleError("bundle_character_scope_invalid")
    for pack_id in expected_character_dirs:
        if not (characters_root / pack_id / "character.json").is_file():
            raise CloudBundleError("bundle_character_manifest_missing")

    engine_root = root / "users_data" / "akane_memory_v01"
    main_db = engine_root / "akane_memory_v01.db"
    memcore_db = engine_root / "memcore_v01.db"
    if not main_db.is_file():
        raise CloudBundleError("bundle_main_database_missing")
    counts = {
        "chat_messages": _sqlite_count(main_db, "chat_messages"),
        "chat_sessions": _sqlite_count(main_db, "chat_sessions"),
        "memory_summaries": _sqlite_count(main_db, "memory_summaries"),
        "memcore_messages": _sqlite_count(memcore_db, "messages") if memcore_db.is_file() else 0,
    }
    return {
        "status": "valid",
        "file_count": len(files),
        "total_bytes": sum(path.stat().st_size for path in files),
        "counts": counts,
        "fresh_qq_state": True,
        "fresh_plugin_state": True,
        "character_pack_ids": expected_character_dirs,
    }


def verify_prepared_bundle(bundle_dir: Path) -> dict[str, Any]:
    """Verify an extracted bundle and every checksummed data file."""

    bundle = Path(bundle_dir).expanduser()
    try:
        if bundle.is_symlink() or not bundle.is_dir():
            raise CloudBundleError("bundle_directory_invalid")
        bundle = bundle.resolve(strict=True)
        if (bundle / "bundle-incomplete.json").exists():
            raise CloudBundleError("bundle_preparation_incomplete")
        manifest_path = bundle / "bundle-manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise CloudBundleError("bundle_manifest_missing")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except CloudBundleError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CloudBundleError("bundle_manifest_invalid") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != BUNDLE_SCHEMA_VERSION:
        raise CloudBundleError("bundle_manifest_invalid")

    instance_id = str(manifest.get("instance_id") or "")
    character_pack_id = str(manifest.get("character_pack_id") or "")
    raw_character_pack_ids = manifest.get("character_pack_ids")
    if not isinstance(raw_character_pack_ids, list) or not all(
        isinstance(item, str) for item in raw_character_pack_ids
    ):
        raise CloudBundleError("bundle_character_scope_invalid")
    qq_profile_ref = str(manifest.get("qq_profile_ref") or "")
    verification = verify_cloud_data_root(
        bundle / "data",
        expected_instance_id=instance_id,
        expected_character_pack_id=character_pack_id,
        expected_qq_profile_ref=qq_profile_ref,
        expected_character_pack_ids=raw_character_pack_ids,
    )
    recorded_inventory = manifest.get("files")
    if not isinstance(recorded_inventory, list):
        raise CloudBundleError("bundle_inventory_invalid")
    try:
        current_inventory = _build_inventory(bundle / "data")
    except OSError as exc:
        raise CloudBundleError("bundle_inventory_unavailable") from exc
    if recorded_inventory != current_inventory:
        raise CloudBundleError("bundle_inventory_mismatch")
    return {
        "ok": True,
        "status": "valid",
        "instance_id": instance_id,
        "character_pack_id": character_pack_id,
        "character_pack_ids": verification["character_pack_ids"],
        "file_count": verification["file_count"],
        "total_bytes": verification["total_bytes"],
        "counts": verification["counts"],
    }


def _sqlite_count(path: Path, table: str) -> int:
    allowed = {"chat_messages", "chat_sessions", "memory_summaries", "messages"}
    if table not in allowed:
        raise CloudBundleError("bundle_database_query_invalid")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path.resolve(strict=True).as_uri() + "?mode=ro", uri=True)
        row = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
    except (OSError, sqlite3.Error) as exc:
        raise CloudBundleError("bundle_database_invalid") from exc
    finally:
        if connection is not None:
            connection.close()
    return int(row[0]) if row else 0


def _build_inventory(root: Path) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
        inventory.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return inventory


def _build_environment_template(
    *,
    instance_id: str,
    qq_profile_ref: str,
    port: int,
) -> str:
    return (
        "# Fill secrets on the server. Never commit the completed file.\n"
        "RUN_MODE=CLOUD\n"
        f"AKANE_INSTANCE_ID={instance_id}\n"
        f"AKANE_DATA_ROOT=/var/lib/akane/{instance_id}\n"
        "HOST=127.0.0.1\n"
        f"PORT={int(port)}\n\n"
        "AKANE_ADMIN_TOKEN=replace-with-a-long-random-admin-token\n"
        "AKANE_DESKTOP_SATELLITE_TOKEN=replace-with-a-distinct-device-token\n\n"
        f"QQ_CHANNEL_PROFILE_REF={qq_profile_ref}\n"
        "QQ_BOT_QQ=replace-with-new-bot-qq-number\n"
        "QQ_ONEBOT_HTTP_URL=http://127.0.0.1:3001\n"
        "QQ_WEBHOOK_SECRET=replace-with-a-long-random-webhook-secret\n"
        "QQ_ONEBOT_ACCESS_TOKEN=replace-with-the-napcat-access-token\n"
        "MASTER_QQ=replace-with-owner-qq-number\n"
        "# The named instance Manifest is authoritative for the character pack.\n"
        "WEB_OWNER_PROFILE_USER_ID=master\n\n"
        "CHAT_API_KEY=\n"
        "CHAT_BASE_URL=\n"
        "CHAT_MODEL_NAME=\n"
        "CHAT_API_PROTOCOL=auto\n"
        "TEXT_API_KEY=\n"
        "TEXT_BASE_URL=\n"
        "TEXT_MODEL_NAME=deepseek-chat\n"
        "AUX_API_KEY=\n"
        "AUX_BASE_URL=\n"
        "AUX_MODEL_NAME=deepseek-chat\n"
        "VISION_API_KEY=\n"
        "VISION_BASE_URL=\n"
        "VISION_MODEL_NAME=\n\n"
        "EMBEDDING_PROVIDER=auto\n"
        "STREAMING_TTS_ENABLED=false\n"
        "PUBLIC_GUARD_ENABLED=false\n"
        "MAX_CONCURRENT_THINKS=2\n"
        "DAILY_THINK_LIMIT=200\n"
    )


def _build_deploy_instructions(*, instance_id: str) -> str:
    return (
        "Akane cloud instance data bundle\n\n"
        "1. Verify the archive SHA-256 before extraction.\n"
        f"2. Install data/ as /var/lib/akane/{instance_id}.\n"
        f"3. Install and complete instance.env.example as /etc/akane/instances/{instance_id}.env.\n"
        "4. Keep secrets only in that server environment file.\n"
        f"5. Start akane@{instance_id}.service and verify /health before connecting QQ ingress.\n"
        "6. Rebuild the derived embedding index on the server; it is intentionally absent here.\n"
        "7. The new QQ Bot starts with fresh delivery/idempotency state.\n"
    )


def _create_archive(output: Path, *, archive_path: Path, archive_root_name: str) -> dict[str, Any]:
    archive = Path(archive_path).expanduser()
    if archive.exists() or archive.is_symlink():
        raise CloudBundleError("bundle_archive_already_exists")
    try:
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive = archive.resolve()
        archive.relative_to(output.resolve())
    except ValueError:
        pass
    else:
        raise CloudBundleError("bundle_archive_must_be_outside_output")
    with tarfile.open(archive, mode="w:gz", format=tarfile.PAX_FORMAT) as bundle:
        bundle.add(output, arcname=archive_root_name, recursive=True)
    digest = _sha256(archive)
    _atomic_text(archive.with_suffix(archive.suffix + ".sha256"), f"{digest}  {archive.name}\n")
    return {
        "archive_path": str(archive),
        "archive_size_bytes": archive.stat().st_size,
        "archive_sha256": digest,
    }


def _new_output_directory(value: Path) -> Path:
    output = Path(value).expanduser()
    try:
        if output.exists() or output.is_symlink():
            raise CloudBundleError("bundle_output_must_not_exist")
        parent = output.parent.resolve(strict=True)
        output = (parent / output.name).resolve()
        output.mkdir()
        return output
    except OSError as exc:
        raise CloudBundleError("bundle_output_unavailable") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _mark_incomplete(path: Path, *, instance_id: str, reason: str) -> None:
    try:
        _atomic_json(
            path,
            {
                "schema_version": BUNDLE_SCHEMA_VERSION,
                "instance_id": str(instance_id or "").strip(),
                "status": "incomplete",
                "reason": str(reason or "cloud_bundle_failed")[:120],
            },
        )
    except OSError:
        pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare a secret-free Akane cloud instance bundle")
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--character-pack-id", required=True)
    parser.add_argument("--qq-profile-ref", required=True)
    parser.add_argument("--source-instance-id", default="local-default")
    parser.add_argument("--source-workspace", type=Path)
    parser.add_argument("--disable-care", action="store_true")
    parser.add_argument("--port", type=int, default=10001)
    parser.add_argument("--archive", type=Path)
    parser.add_argument(
        "--include-character-pack",
        action="append",
        default=[],
        help="Also include this switchable character pack (repeatable).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = prepare_cloud_bundle(
            source_root=args.source_root,
            output_dir=args.output_dir,
            instance_id=args.instance_id,
            character_pack_id=args.character_pack_id,
            qq_profile_ref=args.qq_profile_ref,
            source_instance_id=args.source_instance_id,
            source_workspace=args.source_workspace,
            care_enabled=not args.disable_care,
            port=max(1, min(65535, int(args.port))),
            archive_path=args.archive,
            additional_character_pack_ids=args.include_character_pack,
        )
    except CloudBundleError as exc:
        print(json.dumps({"ok": False, "status": "failed", "reason": exc.reason}, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
