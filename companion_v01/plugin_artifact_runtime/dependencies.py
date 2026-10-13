"""Wheel dependency preflight and path-free installer diagnostics."""

from __future__ import annotations


import hashlib
import re
import zipfile
from email.parser import Parser
from pathlib import Path
from typing import Any, Iterable, Mapping
from .contracts import PluginInstallationError, PLUGIN_PIP_DIAGNOSTIC_BYTES

try:
    from packaging.requirements import InvalidRequirement, Requirement
except ImportError:  # pragma: no cover - packaging is supplied by the host toolchain
    InvalidRequirement = ValueError
    Requirement = None


_HOST_PROVIDED_DISTRIBUTIONS = frozenset({"akane-plugin", "capcore"})


_PIP_MISSING_REQUIREMENT_RE = re.compile(
    r"(?:No matching distribution found for|Could not find a version that satisfies the requirement)\s+([^\s]+)",
    re.IGNORECASE,
)


def _wheel_runtime_requirements(wheel_path: Path) -> tuple[dict[str, Any], ...]:
    """Read applicable Requires-Dist entries without importing plugin code."""

    try:
        with zipfile.ZipFile(wheel_path) as archive:
            metadata_names = tuple(
                name for name in archive.namelist() if name.endswith(".dist-info/METADATA") and name.count("/") == 1
            )
            if len(metadata_names) != 1:
                raise PluginInstallationError("plugin_dependency_metadata_invalid", status="dependency_error")
            metadata = Parser().parsestr(archive.read(metadata_names[0]).decode("utf-8"))
    except PluginInstallationError:
        raise
    except (OSError, UnicodeError, zipfile.BadZipFile, KeyError) as exc:
        raise PluginInstallationError("plugin_dependency_metadata_invalid", status="dependency_error") from exc
    raw_requirements = tuple(metadata.get_all("Requires-Dist") or ())
    result: list[dict[str, Any]] = []
    for raw in raw_requirements:
        text = str(raw or "").strip()
        if not text or Requirement is None:
            raise PluginInstallationError(
                "plugin_dependency_metadata_invalid",
                status="dependency_error",
                diagnostics=(
                    {
                        "stage": "dependency_preflight",
                        "code": "requirement_parser_unavailable" if Requirement is None else "requirement_empty",
                        "diagnostic_id": _diagnostic_id(
                            "requirement_parser_unavailable" if Requirement is None else "requirement_empty"
                        ),
                    },
                ),
            )
        try:
            requirement = Requirement(text)
        except (InvalidRequirement, TypeError, ValueError) as exc:
            raise PluginInstallationError(
                "plugin_dependency_metadata_invalid",
                status="dependency_error",
                diagnostics=(
                    {
                        "stage": "dependency_preflight",
                        "code": "requirement_invalid",
                        "diagnostic_id": _diagnostic_id("requirement_invalid", text),
                    },
                ),
            ) from exc
        if requirement.url:
            raise PluginInstallationError(
                "plugin_dependency_requirement_unsupported",
                status="dependency_error",
                diagnostics=(
                    {
                        "stage": "dependency_preflight",
                        "code": "direct_url_requirement_not_allowed",
                        "package": _distribution_name(requirement.name),
                        "diagnostic_id": _diagnostic_id("direct_url_requirement_not_allowed", text),
                    },
                ),
            )
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        normalized_name = _distribution_name(requirement.name)
        result.append(
            {
                "raw": text,
                "name": normalized_name,
                "specifier": str(requirement.specifier),
                "host_provided": normalized_name in _HOST_PROVIDED_DISTRIBUTIONS,
            }
        )
    return tuple(result)


def _diagnostic_id(*parts: object) -> str:
    payload = "|".join(str(item or "") for item in parts).encode("utf-8", errors="replace")
    return "plugin-diag-" + hashlib.sha256(payload).hexdigest()[:12]


def _simple_diagnostic(stage: str, code: str) -> dict[str, str]:
    return {
        "stage": str(stage),
        "code": str(code),
        "diagnostic_id": _diagnostic_id(stage, code),
    }


def _pip_failure(
    reason: str,
    *,
    stage: str,
    output: object,
    status: str = "failed",
    requirements: Iterable[Mapping[str, Any]] = (),
) -> PluginInstallationError:
    text = output.decode("utf-8", errors="replace") if isinstance(output, bytes) else str(output or "")
    text = text[-PLUGIN_PIP_DIAGNOSTIC_BYTES:]
    missing = tuple(_PIP_MISSING_REQUIREMENT_RE.findall(text))
    declared = tuple(
        {
            "name": str(item.get("name") or ""),
            "specifier": str(item.get("specifier") or ""),
        }
        for item in requirements
        if isinstance(item, Mapping)
    )
    diagnostics: list[dict[str, Any]] = [
        {
            "stage": stage,
            "code": "pip_command_failed",
            "diagnostic_id": _diagnostic_id(stage, reason, text),
            "declared_requirements": list(declared),
        }
    ]
    for item in dict.fromkeys(missing):
        # Pip can echo a URL/path here. Only publish a parsed package name.
        try:
            package = _distribution_name(Requirement(item).name) if Requirement is not None else ""
        except (InvalidRequirement, TypeError, ValueError):
            continue
        if not package:
            continue
        diagnostics.append(
            {
                "stage": stage,
                "code": "requirement_unavailable",
                "package": package,
                "diagnostic_id": _diagnostic_id(stage, "requirement_unavailable", item),
            }
        )
    return PluginInstallationError(reason, status=status, diagnostics=diagnostics)


def _distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()
