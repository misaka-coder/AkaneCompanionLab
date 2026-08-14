"""Pre-traffic maintenance: migrate legacy path-damage projections (MemCore V3).

Run during a release maintenance phase or before the service takes traffic.
Never run this against a database while the backend is actively serving
requests; SQLite transactions keep it safe against partial writes, but the
migration intentionally changes frozen history bytes.

Usage:
    python scripts/migrate_legacy_path_projections.py --db /path/to/memcore_v01.db
    python scripts/migrate_legacy_path_projections.py --db ... --dry-run
    python scripts/migrate_legacy_path_projections.py --db ... --apply

Without --apply the script only reports what it would change (dry run).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def build_report(db_path: Path, *, apply: bool, timezone: str) -> dict:
    from memcore import Namespace, SQLiteMemoryStore
    from memcore.projection import ProjectionAdapter, default_renderer_registry
    from memcore.projection_migration import migrate_legacy_path_projections

    store = SQLiteMemoryStore(str(db_path))
    try:
        namespaces = store.list_projection_namespaces()
    except Exception as exc:
        raise SystemExit(f"namespace listing failed: {type(exc).__name__}: {exc}") from exc
    adapter = ProjectionAdapter(renderer_registry=default_renderer_registry(), timezone=timezone)
    totals: dict[str, int] = {}
    reports: list[dict] = []
    for tenant_id, user_id, domain_id, conversation_id in namespaces:
        try:
            report = migrate_legacy_path_projections(
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
        for key in (
            "scanned_memcore_marker_rows",
            "scanned_host_local_path_rows",
            "scanned_host_tmpdir_rows",
            "migrated",
            "version_advanced_only",
            "preserved_irrecoverable_host_redaction",
            "preserved_without_raw_source",
            "settled_rebuilt",
            "settled_rebuilt_noop",
            "settled_rebuilt_fallback",
            "settled_rebuild_failed_dropped",
        ):
            if isinstance(report, dict):
                totals[key] = totals.get(key, 0) + int(report.get(key) or 0)
    store.close()
    return {
        "apply": bool(apply),
        "timezone": timezone,
        "namespace_count": len(namespaces),
        "totals": totals,
        "reports": reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True, help="Path to the MemCore SQLite database (memcore_v01.db).")
    parser.add_argument("--apply", action="store_true", help="Actually migrate; without this flag the run is a dry run.")
    parser.add_argument("--timezone", default="Asia/Shanghai", help="IANA timezone for projection rendering.")
    args = parser.parse_args()

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
