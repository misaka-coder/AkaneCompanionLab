"""Short-lived full-activation probe for one staged plugin artifact."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Mapping

from .instance_profile import PluginSelection
from .plugin_api import (
    AKANE_PLUGIN_ENTRYPOINT_GROUP,
    NotificationResult,
    PluginReasoningResult,
)
from .plugin_contribution_policy import TrustedStatefulPluginContributionPolicy
from .plugin_host import PluginHost
from .plugin_storage import InstancePluginStorageService


class _ProbeNotificationPort:
    async def send(self, _intent: Any) -> NotificationResult:
        return NotificationResult(ok=False, status="not_configured", reason="installation_probe")


class _ProbeReasoningPort:
    async def analyze(self, _request: Any) -> PluginReasoningResult:
        return PluginReasoningResult(ok=False, status="unavailable", reason="installation_probe")


class _ProbeArtifactSink:
    async def materialize(self, _draft: Any, *, context: Any, capability_id: str) -> Mapping[str, Any]:
        del context, capability_id
        return {}


def _entry_points(site_dir: Path, plugin_id: str) -> tuple[Any, ...]:
    entries: list[Any] = []
    for distribution in importlib_metadata.distributions(path=[str(site_dir)]):
        for entry_point in distribution.entry_points:
            if entry_point.group == AKANE_PLUGIN_ENTRYPOINT_GROUP and entry_point.name == plugin_id:
                entries.append(entry_point)
    return tuple(entries)


async def _probe(site_dir: Path, plugin_id: str, work_dir: Path) -> dict[str, Any]:
    sys.path.insert(0, str(site_dir))
    try:
        entries = _entry_points(site_dir, plugin_id)
        host = PluginHost(
            (PluginSelection(plugin_id, True),),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
            entry_points_provider=lambda: entries,
        )
        host.bind_plugin_storage_service(
            InstancePluginStorageService(work_dir / "storage", "probe-instance")
        )
        host.bind_notification_port(_ProbeNotificationPort())
        host.bind_reasoning_port(_ProbeReasoningPort())
        host.bind_managed_artifact_sink(_ProbeArtifactSink())
        status = await host.start()
        plugin_status = next(
            (
                item
                for item in status.get("plugins", ())
                if isinstance(item, Mapping) and item.get("plugin_id") == plugin_id
            ),
            {},
        )
        ok = plugin_status.get("status") == "active"
        result = {
            "ok": ok,
            "status": "validated" if ok else "failed",
            "reason": "" if ok else str(plugin_status.get("reason") or "plugin_probe_failed"),
            "plugin_id": plugin_id,
            "plugin_version": str(plugin_status.get("plugin_version") or ""),
            "permissions": list(plugin_status.get("permissions") or ()),
            "contribution_snapshot": dict(plugin_status.get("contribution_snapshot") or {}),
        }
        await host.stop()
        return result
    finally:
        try:
            sys.path.remove(str(site_dir))
        except ValueError:
            pass


def _write_result(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(
            json.dumps(dict(payload), ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--site", required=True)
    parser.add_argument("--plugin-id", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args(argv)
    result_path = Path(args.result)
    try:
        payload = asyncio.run(
            _probe(
                Path(args.site).resolve(),
                str(args.plugin_id or "").strip(),
                Path(args.work_dir).resolve(),
            )
        )
    except Exception:
        payload = {"ok": False, "status": "failed", "reason": "plugin_probe_exception"}
    try:
        _write_result(result_path, payload)
    except OSError:
        return 2
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
