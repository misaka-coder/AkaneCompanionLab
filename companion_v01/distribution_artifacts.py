"""Single authority for auditing installed Python distribution artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.metadata import Distribution
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class DistributionArtifactAudit:
    ok: bool
    status: str
    reason: str
    distribution_name: str = ""
    version: str = ""


def audit_distribution_artifact(distribution: Distribution | Any | None) -> DistributionArtifactAudit:
    """Reject unverifiable, editable, and source-directory installations.

    The caller must run this gate before importing an entry point.  No path or
    raw metadata is returned so the result is safe to retain in host status.
    """

    if distribution is None:
        return _failure("distribution_metadata_unavailable")

    try:
        metadata = distribution.metadata
        distribution_name = str(metadata.get("Name") or "").strip()
        version = str(distribution.version or "").strip()
    except Exception:
        return _failure("distribution_metadata_unavailable")
    if not distribution_name or not version:
        return _failure("distribution_metadata_unavailable")

    try:
        raw_direct_url = distribution.read_text("direct_url.json")
    except Exception:
        return _failure("distribution_metadata_unavailable")

    if raw_direct_url:
        direct_url = _parse_direct_url(raw_direct_url)
        if direct_url is None:
            return _failure(
                "invalid_direct_url_metadata",
                distribution_name=distribution_name,
                version=version,
            )
        if not isinstance(direct_url.get("url"), str) or not str(direct_url.get("url") or "").strip():
            return _failure(
                "invalid_direct_url_metadata",
                distribution_name=distribution_name,
                version=version,
            )
        dir_info = direct_url.get("dir_info")
        if "dir_info" in direct_url and not isinstance(dir_info, Mapping):
            return _failure(
                "invalid_direct_url_metadata",
                distribution_name=distribution_name,
                version=version,
            )
        if isinstance(dir_info, Mapping):
            if bool(dir_info.get("editable")):
                return _failure(
                    "editable_install_forbidden",
                    distribution_name=distribution_name,
                    version=version,
                )
            return _failure(
                "source_directory_install_forbidden",
                distribution_name=distribution_name,
                version=version,
            )

    return DistributionArtifactAudit(
        ok=True,
        status="available",
        reason="",
        distribution_name=distribution_name,
        version=version,
    )


def _parse_direct_url(raw: str) -> Mapping[str, Any] | None:
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, Mapping) else None


def _failure(
    reason: str,
    *,
    distribution_name: str = "",
    version: str = "",
) -> DistributionArtifactAudit:
    return DistributionArtifactAudit(
        ok=False,
        status="failed",
        reason=reason,
        distribution_name=distribution_name,
        version=version,
    )


__all__ = ["DistributionArtifactAudit", "audit_distribution_artifact"]
