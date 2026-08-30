"""Pre-traffic maintenance: upgrade ordinary chat projections to MemCore V5.

Run this while the backend is stopped.  Without ``--apply`` the command is a
read-only dry run.  Reports contain counts and namespace identifiers only;
message bodies and provider raw output are never printed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def build_report(db_path: Path, *, apply: bool, timezone: str) -> dict:
    from memcore import Namespace, SQLiteMemoryStore
    from memcore.chat_projection_migration import migrate_chat_projections_v5
    from memcore.projection import ProjectionAdapter, default_renderer_registry

    store = SQLiteMemoryStore(str(db_path))
    try:
        namespaces = store.list_projection_namespaces()
        adapter = ProjectionAdapter(renderer_registry=default_renderer_registry(), timezone=timezone)
        totals: dict[str, int] = {}
        reports: list[dict] = []
        for tenant_id, user_id, domain_id, conversation_id in namespaces:
            try:
                report = migrate_chat_projections_v5(
                    store=store,
                    adapter=adapter,
                    namespace=Namespace(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        domain_id=domain_id,
                        conversation_id=conversation_id,
                    ),
                    dry_run=not apply,
                )
            except Exception as exc:
                report = {"status": "failed", "reason": type(exc).__name__}
            reports.append(
                {
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "domain_id": domain_id,
                    "conversation_id": conversation_id,
                    "report": report,
                }
            )
            if isinstance(report, dict):
                for key in (
                    "scanned_projection_rows",
                    "affected_chat_rows",
                    "migrated",
                    "version_advanced_only",
                    "preserved_without_raw_source",
                    "preserved_shape_mismatch",
                    "preserved_reprojection_failed",
                    "settlements_affected",
                    "settled_rebuilt",
                    "settled_rebuilt_noop",
                    "settled_rebuilt_fallback",
                    "settled_rebuild_failed_dropped",
                ):
                    totals[key] = totals.get(key, 0) + int(report.get(key) or 0)
        return {
            "apply": bool(apply),
            "timezone": timezone,
            "namespace_count": len(namespaces),
            "totals": totals,
            "reports": reports,
        }
    finally:
        store.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True, help="Path to the MemCore SQLite database (memcore_v01.db).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Apply the migration; the default is a dry run.")
    mode.add_argument("--dry-run", action="store_true", help="Explicitly request the default read-only report.")
    parser.add_argument("--timezone", default="Asia/Shanghai", help="IANA timezone used by chat projection rendering.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    db_path = Path(args.db).resolve(strict=True)
    report = build_report(db_path, apply=args.apply, timezone=args.timezone)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    failures = sum(1 for item in report["reports"] if str(item["report"].get("status") or "") == "failed")
    if failures:
        print(f"ERROR: {failures} namespace(s) failed to migrate", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
