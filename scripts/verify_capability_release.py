from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


DEFAULT_REQUIREMENTS_PATH = Path(__file__).resolve().parents[1] / "deploy" / "capability_requirements.json"


def load_required_capabilities(path: Path, *, bot_id: str) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or int(payload.get("schema_version") or 0) != 1:
        raise ValueError("unsupported_requirements_schema")
    bots = payload.get("bots")
    bot = bots.get(bot_id) if isinstance(bots, dict) else None
    required = bot.get("required") if isinstance(bot, dict) else None
    if not isinstance(required, list):
        raise ValueError("bot_requirements_missing")
    normalized = sorted({str(item or "").strip() for item in required if str(item or "").strip()})
    if not normalized:
        raise ValueError("bot_requirements_empty")
    return normalized


def validate_capability_catalog(
    catalog: dict[str, Any],
    *,
    required_ids: list[str],
) -> list[dict[str, str]]:
    capabilities = catalog.get("capabilities")
    if not isinstance(capabilities, list):
        return [{"id": "catalog", "reason": "capabilities_missing"}]
    by_id = {
        str(item.get("id") or "").strip(): item
        for item in capabilities
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    failures: list[dict[str, str]] = []
    for capability_id in required_ids:
        entry = by_id.get(capability_id)
        if entry is None:
            failures.append({"id": capability_id, "reason": "not_registered"})
            continue
        status = str(entry.get("status") or "unknown").strip() or "unknown"
        if not bool(entry.get("enabled")) or status != "ready":
            reason = str(entry.get("reason") or status).strip()[:160] or "not_ready"
            failures.append({"id": capability_id, "reason": reason})

    for capability_id, entry in by_id.items():
        if str(entry.get("kind") or "") != "prompt_module":
            continue
        if not bool(entry.get("enabled")) or str(entry.get("status") or "") != "ready":
            continue
        for tool_name in entry.get("toolTypes") or []:
            normalized_tool_name = str(tool_name or "").strip()
            if not normalized_tool_name:
                continue
            tool_id = f"tool.{normalized_tool_name}"
            tool_entry = by_id.get(tool_id)
            if (
                tool_entry is None
                or not bool(tool_entry.get("enabled"))
                or str(tool_entry.get("status") or "") != "ready"
            ):
                failures.append(
                    {
                        "id": capability_id,
                        "reason": f"ready_prompt_references_unready_tool:{normalized_tool_name}",
                    }
                )
    return sorted(failures, key=lambda item: (item["id"], item["reason"]))


def fetch_catalog(*, base_url: str, bot_id: str, timeout_seconds: float) -> dict[str, Any]:
    endpoint = (
        f"{base_url.rstrip('/')}/api/bots/{quote(bot_id, safe='')}/capabilities"
        "?user_id=capability_release_gate&real_user_id=master"
    )
    request = Request(endpoint, headers={"Accept": "application/json", "Cache-Control": "no-store"})
    with urlopen(request, timeout=max(1.0, timeout_seconds)) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("catalog_not_object")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify required Akane capabilities after a release.")
    parser.add_argument("--base-url", default="http://127.0.0.1:9999")
    parser.add_argument("--bot-id", required=True)
    parser.add_argument("--requirements", type=Path, default=DEFAULT_REQUIREMENTS_PATH)
    parser.add_argument("--require", action="append", default=[], dest="extra_required")
    parser.add_argument("--timeout", type=float, default=20.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        required = load_required_capabilities(args.requirements, bot_id=args.bot_id)
        required = sorted(set(required).union(str(item or "").strip() for item in args.extra_required if str(item or "").strip()))
        catalog = fetch_catalog(base_url=args.base_url, bot_id=args.bot_id, timeout_seconds=args.timeout)
        failures = validate_capability_catalog(catalog, required_ids=required)
        output = {
            "ok": not failures,
            "bot_id": args.bot_id,
            "checked": len(required),
            "required": required,
            "failures": failures,
        }
        print(json.dumps(output, ensure_ascii=False, sort_keys=True))
        return 0 if not failures else 1
    except HTTPError as exc:
        reason = f"http_{int(exc.code)}"
    except (URLError, TimeoutError) as exc:
        reason = type(exc).__name__
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        reason = str(exc)[:160] or type(exc).__name__
    print(
        json.dumps(
            {
                "ok": False,
                "bot_id": args.bot_id,
                "checked": 0,
                "required": [],
                "failures": [{"id": "release_gate", "reason": reason}],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
