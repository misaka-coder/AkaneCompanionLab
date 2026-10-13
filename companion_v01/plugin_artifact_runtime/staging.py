"""Wheel staging, local source builds and bounded source-test evidence."""

from __future__ import annotations


import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Mapping
from .contracts import (
    PluginInstallationError,
    StagedPluginArtifact,
    PLUGIN_STAGE_METADATA_FILENAME,
    PLUGIN_SOURCE_BUILD_TIMEOUT_SECONDS,
    PLUGIN_SOURCE_TEST_TIMEOUT_SECONDS,
    PLUGIN_SOURCE_TEST_OUTPUT_BYTES,
    _normalize_permissions,
)
from .dependencies import _simple_diagnostic, _pip_failure
from .storage import _copy_and_hash, _inspect_installed_site, _write_json_atomic


_SENSITIVE_ENV_NAME_RE = re.compile(r"KEY|PASSWORD|SECRET|TOKEN", re.IGNORECASE)


class ArtifactStaging:
    __slots__ = ()

    def stage_wheel(self, wheel_path: Path) -> dict[str, Any]:
        """Install and fully probe one local wheel without publishing it."""

        source = Path(wheel_path)
        try:
            before = source.stat()
        except OSError as exc:
            raise PluginInstallationError("plugin_wheel_unavailable") from exc
        if not source.is_file() or source.suffix.lower() != ".whl":
            raise PluginInstallationError("plugin_wheel_required", status="invalid_request")
        if before.st_size <= 0:
            raise PluginInstallationError("plugin_wheel_empty", status="invalid_request")
        stage_id = uuid.uuid4().hex
        stage_dir = (self.staging_root / stage_id).resolve()
        stage_dir.relative_to(self.staging_root)
        site_dir = stage_dir / "site"
        # pip validates wheel compatibility from the standardized archive
        # filename. Preserve the basename inside the host-owned staging
        # directory; it is never projected in public status.
        staged_wheel = stage_dir / source.name
        with self._lock:
            self.staging_root.mkdir(parents=True, exist_ok=True)
            stage_dir.mkdir(parents=False, exist_ok=False)
        try:
            digest = _copy_and_hash(source, staged_wheel)
            after = source.stat()
            if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
                raise PluginInstallationError("plugin_wheel_changed_during_stage")
            self._install_wheel(staged_wheel, site_dir)
            plugin_id, distribution_name, version = _inspect_installed_site(site_dir)
            probe = self._probe(site_dir, plugin_id, stage_dir / "probe-data")
            permissions = _normalize_permissions(probe.get("permissions"))
            contribution = probe.get("contribution_snapshot")
            if not isinstance(contribution, Mapping):
                raise PluginInstallationError("plugin_probe_result_invalid")
            staged = StagedPluginArtifact(
                stage_id=stage_id,
                plugin_id=plugin_id,
                distribution_name=distribution_name,
                version=version,
                digest=digest,
                permissions=permissions,
                contribution_snapshot=dict(contribution),
            )
            _write_json_atomic(stage_dir / PLUGIN_STAGE_METADATA_FILENAME, staged.as_dict())
            return staged.as_dict()
        except Exception:
            shutil.rmtree(stage_dir, ignore_errors=True)
            raise

    def stage_source(self, source_path: Path) -> dict[str, Any]:
        """Build one local Python project into an immutable wheel, then stage it.

        This is the development-mode path.  It still enters the same wheel
        audit and activation probe as a downloaded artifact; the source tree is
        never added to ``sys.path`` and is not a second runtime authority.
        """

        source = Path(source_path)
        try:
            source = source.resolve(strict=True)
        except OSError as exc:
            raise PluginInstallationError("plugin_source_unavailable") from exc
        if not source.is_dir() or not (source / "pyproject.toml").is_file():
            raise PluginInstallationError(
                "plugin_source_project_required",
                status="invalid_request",
            )
        build_id = uuid.uuid4().hex
        build_root = (self.root / "source-builds" / build_id).resolve()
        build_root.relative_to(self.root)
        # Configuration failures happen before creating temporary build data.
        source_args = (
            self._pip_dependency_source_args()
            if self.dependency_wheelhouse is not None or self.dependency_index_url
            else ["--no-index", "--no-build-isolation"]
        )
        wheelhouse = build_root / "wheelhouse"
        wheelhouse.mkdir(parents=True, exist_ok=False)
        env = _scrubbed_plugin_subprocess_env()
        env.update(
            {
                "PIP_NO_INPUT": "1",
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            }
        )
        build_args = [
            self.python_executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--disable-pip-version-check",
            "--wheel-dir",
            str(wheelhouse),
        ]
        build_args.extend(source_args)
        try:
            try:
                completed = subprocess.run(
                    [*build_args, str(source)],
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=PLUGIN_SOURCE_BUILD_TIMEOUT_SECONDS,
                    env=env,
                )
            except subprocess.TimeoutExpired as exc:
                raise PluginInstallationError(
                    "plugin_source_build_timeout",
                    diagnostics=(_simple_diagnostic("build", "timeout"),),
                ) from exc
            except OSError as exc:
                raise PluginInstallationError(
                    "plugin_source_builder_unavailable",
                    diagnostics=(_simple_diagnostic("build", "installer_unavailable"),),
                ) from exc
            if completed.returncode != 0:
                raise _pip_failure(
                    "plugin_source_build_failed",
                    stage="build",
                    output=completed.stdout,
                )
            wheels = tuple(wheelhouse.glob("*.whl"))
            if len(wheels) != 1:
                raise PluginInstallationError("plugin_source_build_result_invalid")
            return self.stage_wheel(wheels[0])
        finally:
            shutil.rmtree(build_root, ignore_errors=True)

    def test_source(self, source_path: Path) -> dict[str, Any]:
        """Run source logic tests against this release's real public SDK.

        The project remains an ordinary source tree and is neither staged nor
        activated.  Tests run in a child process using the same interpreter
        and public contracts as the plugin probe, so projects never need to
        fake ``capcore`` or ``companion_v01.plugin_api`` in ``sys.modules``.
        """

        source = Path(source_path)
        try:
            source = source.resolve(strict=True)
        except OSError as exc:
            raise PluginInstallationError("plugin_source_unavailable") from exc
        if not source.is_dir() or not (source / "pyproject.toml").is_file():
            raise PluginInstallationError(
                "plugin_source_project_required",
                status="invalid_request",
            )
        tests_dir = source / "tests"
        if not tests_dir.is_dir():
            raise PluginInstallationError(
                "plugin_source_tests_required",
                status="invalid_request",
            )

        env = _scrubbed_plugin_subprocess_env()
        python_paths = [source / "src", source, self.project_root]
        packaged_dependencies = self.project_root / ".packages"
        if packaged_dependencies.is_dir():
            python_paths.append(packaged_dependencies)
        inherited_pythonpath = str(env.get("PYTHONPATH") or "").strip()
        if inherited_pythonpath:
            python_paths.extend(Path(item) for item in inherited_pythonpath.split(os.pathsep) if item)
        env["PYTHONPATH"] = os.pathsep.join(str(item) for item in python_paths)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        started_at = time.monotonic()
        try:
            with tempfile.TemporaryFile() as output:
                try:
                    completed = subprocess.run(
                        [
                            self.python_executable,
                            "-m",
                            "unittest",
                            "discover",
                            "-s",
                            "tests",
                            "-v",
                        ],
                        cwd=str(source),
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                        check=False,
                        timeout=PLUGIN_SOURCE_TEST_TIMEOUT_SECONDS,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise PluginInstallationError("plugin_source_test_timeout", status="timeout") from exc
                except OSError as exc:
                    raise PluginInstallationError("plugin_source_test_runner_unavailable") from exc
                output.flush()
                output.seek(0, os.SEEK_END)
                output_size = output.tell()
                output_text, output_truncated = _read_test_output(output, output_size)
        except PluginInstallationError:
            raise
        except OSError as exc:
            raise PluginInstallationError("plugin_source_test_output_failed") from exc

        passed = completed.returncode == 0
        return {
            "ok": passed,
            "status": "passed" if passed else "failed",
            "reason": "" if passed else "plugin_source_tests_failed",
            "test_framework": "unittest",
            "exit_code": int(completed.returncode),
            "duration_ms": round((time.monotonic() - started_at) * 1000, 1),
            "output": output_text,
            "output_bytes": output_size,
            "output_truncated": output_truncated,
        }


def _read_test_output(handle: Any, output_size: int) -> tuple[str, bool]:
    """Return useful bounded test evidence, preserving both setup and failure tail."""

    size = max(0, int(output_size))
    if size <= PLUGIN_SOURCE_TEST_OUTPUT_BYTES:
        handle.seek(0)
        return handle.read().decode("utf-8", errors="replace"), False
    head_bytes = 4 * 1024
    tail_bytes = PLUGIN_SOURCE_TEST_OUTPUT_BYTES - head_bytes
    handle.seek(0)
    head = handle.read(head_bytes)
    handle.seek(max(0, size - tail_bytes))
    tail = handle.read(tail_bytes)
    marker = f"\n... test output omitted ({size - len(head) - len(tail)} bytes) ...\n".encode()
    return (head + marker + tail).decode("utf-8", errors="replace"), True


def _scrubbed_plugin_subprocess_env() -> dict[str, str]:
    """Keep normal toolchain discovery without exposing host credentials."""

    return {
        str(name): str(value)
        for name, value in os.environ.items()
        if not _SENSITIVE_ENV_NAME_RE.search(str(name)) and not str(name).upper().startswith("AKANE_")
    }
