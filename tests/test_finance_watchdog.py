from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_watchdog() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "deploy" / "watchdog" / "akane-host-finance-watchdog.py"
    spec = importlib.util.spec_from_file_location("akane_host_finance_watchdog", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_finance_watchdog_uses_canonical_background_services() -> None:
    watchdog = _load_watchdog()
    payload = {
        "ok": True,
        "status": "active",
        "background_service_count": 1,
        "plugins": [
            {
                "plugin_id": "akane.finance",
                "status": "active",
                "contribution_snapshot": {
                    "background_services": ["public-news-push"],
                },
            }
        ],
    }

    assert watchdog.job_is_healthy(payload)
    assert watchdog.job_failure_reason(payload) == "plugin_status=active reason="


def test_finance_watchdog_rejects_removed_legacy_job_view() -> None:
    watchdog = _load_watchdog()
    payload = {
        "ok": True,
        "status": "active",
        "jobs": [{"plugin_id": "akane.finance", "status": "running"}],
    }

    assert not watchdog.job_is_healthy(payload)
    assert watchdog.job_failure_reason(payload) == "finance_plugin_missing"
