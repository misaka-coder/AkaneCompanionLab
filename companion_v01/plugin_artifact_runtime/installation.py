"""Explicit dependency supply, isolated wheel installation and preparation probes."""

from __future__ import annotations


import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any
from ..plugin_generation import PluginGenerationError, PluginGenerationProcess
from .contracts import PluginInstallationError, PLUGIN_INSTALL_TIMEOUT_SECONDS, PLUGIN_PROBE_TIMEOUT_SECONDS
from .dependencies import _wheel_runtime_requirements, _diagnostic_id, _simple_diagnostic, _pip_failure
from .staging import _scrubbed_plugin_subprocess_env


class ArtifactInstaller:
    __slots__ = ()

    def _dependency_supply_snapshot(self) -> dict[str, Any]:
        if self.dependency_wheelhouse is not None:
            return {
                "kind": "wheelhouse",
                "configured": self.dependency_wheelhouse.is_dir(),
                "status": "ready" if self.dependency_wheelhouse.is_dir() else "unavailable",
            }
        if self.dependency_index_url:
            return {"kind": "package_index", "configured": True, "status": "configured"}
        return {"kind": "host_only", "configured": False, "status": "unconfigured"}

    def _pip_dependency_source_args(self) -> list[str]:
        if self.dependency_wheelhouse is not None:
            if not self.dependency_wheelhouse.is_dir():
                raise PluginInstallationError(
                    "plugin_dependency_wheelhouse_unavailable",
                    status="dependency_error",
                    diagnostics=(
                        {
                            "stage": "dependency_preflight",
                            "code": "wheelhouse_unavailable",
                            "diagnostic_id": _diagnostic_id("wheelhouse_unavailable", str(self.dependency_wheelhouse)),
                        },
                    ),
                )
            return ["--no-index", "--find-links", str(self.dependency_wheelhouse)]
        if self.dependency_index_url:
            return ["--index-url", self.dependency_index_url]
        return []

    def _install_wheel(self, wheel_path: Path, site_dir: Path) -> None:
        # Let pip retain its canonical wheel error for non-archives.  Dependency
        # metadata diagnostics apply only after the artifact is structurally a
        # wheel and can be inspected safely.
        requirements = _wheel_runtime_requirements(wheel_path) if zipfile.is_zipfile(wheel_path) else ()
        external = [item for item in requirements if item["host_provided"] is False]
        if external and self.dependency_wheelhouse is None and not self.dependency_index_url:
            raise PluginInstallationError(
                "plugin_dependencies_unconfigured",
                status="dependency_error",
                diagnostics=tuple(
                    {
                        "stage": "dependency_preflight",
                        "code": "dependency_source_required",
                        "package": item["name"],
                        "specifier": item["specifier"],
                        "diagnostic_id": _diagnostic_id("dependency_source_required", item["name"], item["specifier"]),
                    }
                    for item in external
                ),
            )
        source_args = self._pip_dependency_source_args() if external else ["--no-index"]
        env = _scrubbed_plugin_subprocess_env()
        env.update(
            {
                "PIP_NO_INPUT": "1",
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            }
        )
        try:
            # Install the plugin payload itself without asking pip to resolve
            # the host SDK. Runtime dependencies are installed separately into
            # the same isolated target after the preflight above.
            completed = subprocess.run(
                [
                    self.python_executable,
                    "-m",
                    "pip",
                    "install",
                    "--no-deps",
                    "--disable-pip-version-check",
                    "--no-compile",
                    "--target",
                    str(site_dir),
                    str(wheel_path),
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=PLUGIN_INSTALL_TIMEOUT_SECONDS,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise PluginInstallationError(
                "plugin_wheel_install_timeout",
                diagnostics=(_simple_diagnostic("plugin_install", "timeout"),),
            ) from exc
        except OSError as exc:
            raise PluginInstallationError(
                "plugin_wheel_installer_unavailable",
                diagnostics=(_simple_diagnostic("plugin_install", "installer_unavailable"),),
            ) from exc
        if completed.returncode != 0:
            raise _pip_failure(
                "plugin_wheel_install_failed",
                stage="plugin_install",
                output=completed.stdout,
            )
        if not external:
            return
        requirement_args = [item["raw"] for item in external]
        try:
            completed = subprocess.run(
                [
                    self.python_executable,
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "--no-compile",
                    "--target",
                    str(site_dir),
                    *source_args,
                    *requirement_args,
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=PLUGIN_INSTALL_TIMEOUT_SECONDS,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise PluginInstallationError(
                "plugin_dependency_install_timeout",
                status="dependency_error",
                diagnostics=(_simple_diagnostic("dependency_install", "timeout"),),
            ) from exc
        except OSError as exc:
            raise PluginInstallationError(
                "plugin_dependency_installer_unavailable",
                status="dependency_error",
                diagnostics=(_simple_diagnostic("dependency_install", "installer_unavailable"),),
            ) from exc
        if completed.returncode != 0:
            raise _pip_failure(
                "plugin_dependency_install_failed",
                stage="dependency_install",
                output=completed.stdout,
                status="dependency_error",
                requirements=external,
            )

    def _probe(self, site_dir: Path, plugin_id: str, work_dir: Path) -> dict[str, Any]:
        generation = PluginGenerationProcess(
            project_root=self.project_root,
            site_dir=site_dir,
            plugin_id=plugin_id,
            work_dir=work_dir,
            python_executable=self.python_executable,
            start_timeout_seconds=PLUGIN_PROBE_TIMEOUT_SECONDS,
        )
        try:
            return generation.prepare()
        except PluginGenerationError as exc:
            reason = exc.reason
            if reason == "plugin_generation_timeout":
                reason = "plugin_probe_timeout"
            elif reason in {
                "plugin_generation_unavailable",
                "plugin_generation_exited",
            }:
                reason = "plugin_probe_unavailable"
            elif reason.startswith("plugin_generation_"):
                reason = "plugin_probe_failed"
            raise PluginInstallationError(reason, schema_errors=exc.schema_errors) from exc
        finally:
            generation.stop()
            shutil.rmtree(work_dir, ignore_errors=True)
