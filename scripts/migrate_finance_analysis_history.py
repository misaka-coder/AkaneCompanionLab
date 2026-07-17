#!/usr/bin/env python3
"""Offline migration of delivered finance analysis history into Memcore.

The legacy plugin database remains untouched.  Apply mode creates SQLite
backups first, then writes through ``MemcoreManager``'s public turn facade.
No prompt/message text, user id, session id, URL, or absolute path is printed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import uuid
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@dataclass(frozen=True, slots=True)
class LegacyDeliveredAnalysis:
    history_id: int
    profile_user_id: str
    session_id: str
    character_pack_id: str
    idempotency_key: str
    event_id: str
    published_at: int
    delivered_at: int
    analysis_text: str
    event_payload: dict[str, Any]


class TimelineFacade(Protocol):
    def inspect_turn_source(self, source_id: str, **scope: Any) -> dict[str, Any]: ...

    def record_user_turn(self, record: dict[str, Any], **scope: Any) -> dict[str, Any]: ...

    def record_assistant_turn(self, record: dict[str, Any], **scope: Any) -> dict[str, Any]: ...

    def compact_due_sync(self, **scope: Any) -> dict[str, Any]: ...


def load_delivered_history(source_db: Path) -> tuple[list[LegacyDeliveredAnalysis], dict[str, Any]]:
    source = Path(source_db).resolve(strict=True)
    uri = f"{source.as_uri()}?mode=ro"
    skipped: Counter[str] = Counter()
    candidates: list[LegacyDeliveredAnalysis] = []
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT
                h.history_id,
                h.subscription_id,
                h.recipient_id AS history_recipient_id,
                h.event_id,
                h.published_at,
                h.text AS history_text,
                h.delivered_at,
                d.recipient_id AS delivery_recipient_id,
                d.text AS delivery_text,
                d.idempotency_key,
                d.status AS delivery_status,
                d.payload_json,
                s.recipient_id AS subscription_recipient_id,
                s.profile_user_id,
                s.session_id,
                s.character_pack_id
            FROM analysis_history AS h
            LEFT JOIN delivery_outbox AS d
              ON d.subscription_id = h.subscription_id AND d.event_id = h.event_id
            LEFT JOIN subscriptions AS s ON s.subscription_id = h.subscription_id
            ORDER BY h.published_at ASC, h.history_id ASC
            """
        ).fetchall()
    for row in rows:
        reason = _candidate_rejection_reason(row)
        if reason:
            skipped[reason] += 1
            continue
        payload = json.loads(str(row["payload_json"]))
        candidates.append(
            LegacyDeliveredAnalysis(
                history_id=int(row["history_id"]),
                profile_user_id=str(row["profile_user_id"]).strip(),
                session_id=str(row["session_id"]).strip(),
                character_pack_id=str(row["character_pack_id"] or "").strip(),
                idempotency_key=str(row["idempotency_key"]).strip(),
                event_id=str(row["event_id"]).strip(),
                published_at=int(row["published_at"]),
                delivered_at=max(int(row["published_at"]), int(row["delivered_at"])),
                analysis_text=str(row["history_text"]).strip(),
                event_payload=dict(payload),
            )
        )
    return candidates, {
        "scanned": len(rows),
        "eligible": len(candidates),
        "skipped": sum(skipped.values()),
        "skip_reasons": dict(sorted(skipped.items())),
    }


def migrate_delivered_history(
    entries: Iterable[LegacyDeliveredAnalysis],
    *,
    timeline: TimelineFacade,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "eligible": 0,
        "pairs_completed": 0,
        "already_complete": 0,
        "turns_written": 0,
        "failed": 0,
        "reason": "",
    }
    for entry in entries:
        report["eligible"] += 1
        scope = {
            "profile_user_id": entry.profile_user_id,
            "session_id": entry.session_id,
            "character_pack_id": entry.character_pack_id,
        }
        event_source_id = _plugin_event_source_id(entry)
        analysis_source_id = _history_analysis_source_id(entry)
        event_state = timeline.inspect_turn_source(event_source_id, **scope)
        analysis_state = timeline.inspect_turn_source(analysis_source_id, **scope)
        for state in (event_state, analysis_state):
            if not bool(state.get("ok")):
                report["failed"] += 1
                report["reason"] = str(state.get("reason") or state.get("status") or "inspect_failed")
                return report
        event_exists = bool(event_state.get("exists"))
        analysis_exists = bool(analysis_state.get("exists"))
        if event_exists and analysis_exists:
            report["already_complete"] += 1
            report["pairs_completed"] += 1
            continue
        if not event_exists:
            result = timeline.record_user_turn(
                {
                    "source_id": event_source_id,
                    "content": _render_event_message(entry.event_payload),
                    "timestamp": entry.published_at,
                    "memory_metadata": {
                        "source": "finance_analysis_history_migration",
                        "categories": ["finance_event"],
                        "importance": 0.35,
                        "confidence": 1.0,
                    },
                    "index_in_vector": True,
                },
                **scope,
            )
            if not bool(result.get("ok")):
                report["failed"] += 1
                report["reason"] = str(result.get("reason") or result.get("status") or "event_write_failed")
                return report
            report["turns_written"] += 1
        if not analysis_exists:
            result = timeline.record_assistant_turn(
                {
                    "source_id": analysis_source_id,
                    "content": _render_historical_analysis(entry.analysis_text),
                    "timestamp": entry.delivered_at,
                    "memory_metadata": {
                        "source": "finance_analysis_history_migration",
                        "categories": ["finance_analysis", "historical_judgment"],
                        "importance": 0.4,
                        "confidence": 0.6,
                    },
                },
                **scope,
            )
            if not bool(result.get("ok")):
                report["failed"] += 1
                report["reason"] = str(result.get("reason") or result.get("status") or "analysis_write_failed")
                return report
            report["turns_written"] += 1
        report["pairs_completed"] += 1
    return report


def compact_migrated_namespaces(
    entries: Iterable[LegacyDeliveredAnalysis],
    *,
    timeline: TimelineFacade,
) -> dict[str, Any]:
    scopes = list(
        dict.fromkeys(
            (
                entry.profile_user_id,
                entry.session_id,
                entry.character_pack_id,
            )
            for entry in entries
        )
    )
    report = {
        "namespaces": len(scopes),
        "completed": 0,
        "summaries_created": 0,
        "semantic_created": 0,
        "reinforced": 0,
        "retry_pending": 0,
        "failed": 0,
        "reason": "",
    }
    for profile_user_id, session_id, character_pack_id in scopes:
        result = timeline.compact_due_sync(
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if not bool(result.get("ok")):
            report["failed"] += 1
            report["reason"] = str(result.get("reason") or result.get("status") or "compaction_failed")
            return report
        stats = result.get("stats") if isinstance(result.get("stats"), dict) else {}
        report["summaries_created"] += max(0, int(stats.get("summaries_created") or 0))
        report["semantic_created"] += max(0, int(stats.get("semantic_created") or 0))
        report["reinforced"] += max(0, int(stats.get("reinforced") or 0))
        retry_pending = max(0, int(stats.get("summary_retry_pending") or 0)) + max(
            0, int(stats.get("semantic_retry_pending") or 0)
        )
        report["retry_pending"] += retry_pending
        if retry_pending:
            report["failed"] += 1
            report["reason"] = "compaction_retry_pending"
            return report
        report["completed"] += 1
    return report


def create_backups(source_db: Path, memcore_db: Path, backup_dir: Path) -> dict[str, bool]:
    target_dir = Path(backup_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{uuid.uuid4().hex[:8]}"
    targets = (
        (Path(source_db).resolve(strict=True), target_dir / f"finance_state.before_history_migration.{suffix}.sqlite3"),
        (Path(memcore_db).resolve(strict=True), target_dir / f"memcore.before_history_migration.{suffix}.sqlite3"),
    )
    for source, target in targets:
        with (
            closing(sqlite3.connect(source)) as source_connection,
            closing(sqlite3.connect(target)) as target_connection,
        ):
            source_connection.backup(target_connection)
        with closing(sqlite3.connect(f"{target.as_uri()}?mode=ro", uri=True)) as verification:
            result = str(verification.execute("PRAGMA integrity_check").fetchone()[0])
        if result.lower() != "ok":
            raise RuntimeError("backup_integrity_check_failed")
    return {"source": True, "memcore": True}


def build_timeline_facade(memcore_db: Path, *, enable_compaction: bool = False) -> TimelineFacade:
    import config
    from companion_v01.embedding_provider import HashedEmbeddingProvider
    from companion_v01.llm_runtime import LLMRuntime
    from companion_v01.memcore_integration.manager import MemcoreManager

    llm: Any = _UnavailableMigrationLLM()
    if enable_compaction:
        llm = LLMRuntime(
            log_dir=Path(str(getattr(config, "LOG_DIR", "") or "logs")),
            instance_id=str(getattr(config, "AKANE_INSTANCE_ID", "") or "finance-maintenance"),
        )
    return MemcoreManager(
        backend="memcore",
        storage_path=Path(memcore_db).resolve(strict=True),
        visible_scope=str(getattr(config, "MEMCORE_VISIBLE_SCOPE", "user") or "user"),
        enable_flavor=bool(getattr(config, "MEMCORE_ENABLE_FLAVOR", True)),
        shadow_compare=False,
        llm=llm,
        embedding_provider=HashedEmbeddingProvider(),
    )


class _UnavailableMigrationLLM:
    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(f"migration_llm_unavailable:{name}")


def _candidate_rejection_reason(row: sqlite3.Row) -> str:
    if str(row["delivery_status"] or "") != "delivered":
        return "not_delivered"
    if (
        not str(row["history_text"] or "").strip()
        or str(row["history_text"]).strip() != str(row["delivery_text"] or "").strip()
    ):
        return "delivery_text_mismatch"
    recipients = {
        str(row["history_recipient_id"] or "").strip(),
        str(row["delivery_recipient_id"] or "").strip(),
        str(row["subscription_recipient_id"] or "").strip(),
    }
    if "" in recipients or len(recipients) != 1:
        return "recipient_scope_mismatch"
    if not str(row["profile_user_id"] or "").strip() or not str(row["session_id"] or "").strip():
        return "missing_memory_scope"
    if not str(row["idempotency_key"] or "").strip():
        return "missing_idempotency_key"
    try:
        payload = json.loads(str(row["payload_json"] or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return "invalid_event_payload"
    if not isinstance(payload, dict):
        return "invalid_event_payload"
    if str(payload.get("event_id") or "").strip() != str(row["event_id"] or "").strip():
        return "event_identity_mismatch"
    try:
        payload_published_at = int(payload.get("published_at") or 0)
    except (TypeError, ValueError, OverflowError):
        return "event_identity_mismatch"
    if payload_published_at != int(row["published_at"] or 0) or payload_published_at <= 0:
        return "event_identity_mismatch"
    if not str(payload.get("title") or payload.get("summary") or "").strip():
        return "empty_event_payload"
    return ""


def _plugin_event_source_id(entry: LegacyDeliveredAnalysis) -> str:
    from companion_v01.engine import AkaneMemoryEngine

    payload = {"memory_idempotency_key": entry.idempotency_key}
    return AkaneMemoryEngine._pop_user_memory_source_id(
        payload,
        profile_user_id=entry.profile_user_id,
        session_id=entry.session_id,
        character_pack_id=entry.character_pack_id,
    )


def _history_analysis_source_id(entry: LegacyDeliveredAnalysis) -> str:
    material = json.dumps(
        {
            "profile_user_id": entry.profile_user_id,
            "session_id": entry.session_id,
            "character_pack_id": entry.character_pack_id,
            "memory_idempotency_key": entry.idempotency_key,
            "role": "assistant",
            "migration": "finance_analysis_history_v1",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"finance-history-analysis:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


def _render_event_message(event: dict[str, Any]) -> str:
    return "\n".join(
        (
            "【外部财经快讯，不是用户发言】",
            f"发布时间：{_bounded_text(event.get('published_at_iso'), 80)}",
            f"标题：{_bounded_text(event.get('title'), 500)}",
            f"摘要：{_bounded_text(event.get('summary'), 1200)}",
            f"来源：{_bounded_text(event.get('source'), 120)}",
            f"原文：{_bounded_text(event.get('url'), 500)}",
        )
    )


def _render_historical_analysis(text: str) -> str:
    return "【历史已投递金融分析｜仅代表当时判断，不是当前事实更新】\n" + str(text or "").strip()


def _bounded_text(value: Any, maximum: int) -> str:
    return " ".join(str(value or "").strip().split())[:maximum]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Migrate delivered finance analysis history into Memcore offline")
    parser.add_argument("--finance-db", type=Path, required=True)
    parser.add_argument("--memcore-db", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--apply", action="store_true", help="Create backups and write; default is read-only dry run")
    parser.add_argument(
        "--compact-after",
        action="store_true",
        help="After a successful apply, run one synchronous due-compaction pass per migrated namespace",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    entries, scan = load_delivered_history(args.finance_db)
    report: dict[str, Any] = {"status": "dry_run", **scan}
    manager: Any = None
    try:
        if args.apply:
            if args.backup_dir is None:
                raise RuntimeError("backup_dir_required_for_apply")
            report["backups"] = create_backups(args.finance_db, args.memcore_db, args.backup_dir)
            manager = build_timeline_facade(args.memcore_db, enable_compaction=args.compact_after)
            if not bool(getattr(manager, "available", False)):
                raise RuntimeError("memcore_facade_unavailable")
            migrated = migrate_delivered_history(entries, timeline=manager)
            report.update(migrated)
            report["status"] = "completed" if not migrated["failed"] else "failed"
            if report["status"] == "completed" and args.compact_after:
                compaction = compact_migrated_namespaces(entries, timeline=manager)
                report["compaction"] = compaction
                if compaction["failed"]:
                    report["status"] = "failed"
                    report["reason"] = compaction["reason"]
    except Exception as exc:
        report["status"] = "failed"
        report["reason"] = str(exc) or exc.__class__.__name__
    finally:
        close = getattr(manager, "close", None)
        if callable(close):
            close()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] in {"dry_run", "completed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
