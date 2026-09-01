"""Versioned process boundary for one managed PluginHost generation.

This first M67-F slice stays intentionally small: start one candidate, inspect
its public contribution snapshot, query health, and drain it. Runtime calls
remain on the in-process PluginHost until each public port has an explicit JSON
projection. No host objects are pickled across this boundary.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import queue
import subprocess
import sys
import threading
import time
import uuid
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Mapping, TextIO

from .instance_profile import PluginSelection
from .plugin_api import (
    AKANE_PLUGIN_ENTRYPOINT_GROUP,
    NotificationResult,
    PluginReasoningResult,
)
from .plugin_contribution_policy import TrustedStatefulPluginContributionPolicy
from .plugin_host import PluginHost
from .plugin_storage import InstancePluginStorageService


PLUGIN_GENERATION_PROTOCOL = "akane.plugin-generation.v1"
PLUGIN_GENERATION_START_TIMEOUT_SECONDS = 45.0
PLUGIN_GENERATION_STOP_TIMEOUT_SECONDS = 15.0


class PluginGenerationError(RuntimeError):
    """A public, path-free generation lifecycle failure."""

    def __init__(self, reason: str) -> None:
        self.reason = str(reason or "plugin_generation_failed")
        super().__init__(self.reason)


class PluginGenerationProcess:
    """Own one exact child process and its line-delimited JSON control lane."""

    def __init__(
        self,
        *,
        project_root: Path,
        site_dir: Path,
        plugin_id: str,
        work_dir: Path,
        python_executable: str = sys.executable,
        start_timeout_seconds: float = PLUGIN_GENERATION_START_TIMEOUT_SECONDS,
        stop_timeout_seconds: float = PLUGIN_GENERATION_STOP_TIMEOUT_SECONDS,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.site_dir = Path(site_dir).resolve()
        self.plugin_id = str(plugin_id or "").strip()
        self.work_dir = Path(work_dir).resolve()
        self.python_executable = str(python_executable or sys.executable)
        self.start_timeout_seconds = max(0.1, float(start_timeout_seconds))
        self.stop_timeout_seconds = max(0.1, float(stop_timeout_seconds))
        self.generation_id = uuid.uuid4().hex
        self._process: subprocess.Popen[str] | None = None
        self._responses: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._reader: threading.Thread | None = None
        self._request_lock = threading.Lock()
        self._closed = False

    @property
    def running(self) -> bool:
        process = self._process
        return bool(process is not None and process.poll() is None and not self._closed)

    def start(self) -> dict[str, Any]:
        if self._process is not None:
            raise PluginGenerationError("plugin_generation_already_started")
        self.work_dir.mkdir(parents=True, exist_ok=True)
        started_at = time.perf_counter()
        try:
            self._process = subprocess.Popen(
                [
                    self.python_executable,
                    "-u",
                    "-m",
                    "companion_v01.plugin_generation",
                    "--worker",
                    "--site",
                    str(self.site_dir),
                    "--plugin-id",
                    self.plugin_id,
                    "--work-dir",
                    str(self.work_dir),
                    "--generation-id",
                    self.generation_id,
                ],
                cwd=str(self.project_root),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except OSError as exc:
            raise PluginGenerationError("plugin_generation_unavailable") from exc
        assert self._process.stdout is not None
        self._reader = threading.Thread(
            target=self._read_responses,
            args=(self._process.stdout,),
            name=f"plugin-generation-reader:{self.generation_id[:8]}",
            daemon=True,
        )
        self._reader.start()
        try:
            ready = self._next_response(self.start_timeout_seconds)
            self._validate_response(ready, expected_type="ready")
            if not ready.get("ok"):
                raise PluginGenerationError(
                    str(ready.get("reason") or "plugin_generation_start_failed")
                )
            ready["startup_ms"] = round((time.perf_counter() - started_at) * 1000.0, 3)
            return ready
        except Exception:
            self._terminate()
            raise

    def health(self) -> dict[str, Any]:
        return self._request("health", timeout_seconds=self.start_timeout_seconds)

    def stop(self) -> dict[str, Any]:
        if self._closed:
            return {"ok": True, "status": "stopped", "reason": "already_stopped"}
        process = self._process
        if process is None:
            self._closed = True
            return {"ok": True, "status": "stopped", "reason": "not_started"}
        try:
            if process.poll() is None:
                response = self._request("stop", timeout_seconds=self.stop_timeout_seconds)
            else:
                response = {
                    "ok": False,
                    "status": "failed",
                    "reason": "plugin_generation_exited",
                }
        except PluginGenerationError as exc:
            response = {"ok": False, "status": "failed", "reason": exc.reason}
        finally:
            self._terminate()
        return response

    def _request(self, command: str, *, timeout_seconds: float) -> dict[str, Any]:
        with self._request_lock:
            process = self._process
            if self._closed or process is None or process.poll() is not None:
                raise PluginGenerationError("plugin_generation_unavailable")
            if process.stdin is None:
                raise PluginGenerationError("plugin_generation_unavailable")
            request_id = uuid.uuid4().hex
            payload = {
                "protocol": PLUGIN_GENERATION_PROTOCOL,
                "type": "request",
                "request_id": request_id,
                "command": command,
            }
            try:
                process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise PluginGenerationError("plugin_generation_unavailable") from exc
            response = self._next_response(timeout_seconds)
            self._validate_response(response, expected_type="response")
            if response.get("request_id") != request_id:
                raise PluginGenerationError("plugin_generation_protocol_invalid")
            return response

    def _next_response(self, timeout_seconds: float) -> dict[str, Any]:
        try:
            response = self._responses.get(timeout=max(0.1, float(timeout_seconds)))
        except queue.Empty as exc:
            raise PluginGenerationError("plugin_generation_timeout") from exc
        if response is None:
            raise PluginGenerationError("plugin_generation_exited")
        return response

    def _read_responses(self, stream: TextIO) -> None:
        try:
            for line in stream:
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    self._responses.put({"protocol": "", "type": "invalid"})
                    continue
                self._responses.put(dict(payload) if isinstance(payload, Mapping) else {})
        finally:
            self._responses.put(None)

    def _validate_response(
        self,
        response: Mapping[str, Any],
        *,
        expected_type: str,
    ) -> None:
        if (
            response.get("protocol") != PLUGIN_GENERATION_PROTOCOL
            or response.get("type") != expected_type
            or response.get("generation_id") != self.generation_id
        ):
            raise PluginGenerationError("plugin_generation_protocol_invalid")

    def _terminate(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = self._process
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=2.0)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                    process.wait(timeout=2.0)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        else:
            try:
                process.wait(timeout=0.1)
            except (OSError, subprocess.TimeoutExpired):
                pass
        try:
            if process.stdout is not None:
                process.stdout.close()
        except OSError:
            pass
        reader = self._reader
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=1.0)


class _GenerationNotificationPort:
    async def send(self, _intent: Any) -> NotificationResult:
        return NotificationResult(
            ok=False,
            status="not_configured",
            reason="generation_probe",
        )


class _GenerationReasoningPort:
    async def analyze(self, _request: Any) -> PluginReasoningResult:
        return PluginReasoningResult(
            ok=False,
            status="unavailable",
            reason="generation_probe",
        )


class _GenerationArtifactSink:
    async def materialize(
        self,
        _draft: Any,
        *,
        context: Any,
        capability_id: str,
    ) -> Mapping[str, Any]:
        del context, capability_id
        return {}


def _entry_points(site_dir: Path, plugin_id: str) -> tuple[Any, ...]:
    entries: list[Any] = []
    for distribution in importlib_metadata.distributions(path=[str(site_dir)]):
        for entry_point in distribution.entry_points:
            if (
                entry_point.group == AKANE_PLUGIN_ENTRYPOINT_GROUP
                and entry_point.name == plugin_id
            ):
                entries.append(entry_point)
    return tuple(entries)


def _emit(stream: TextIO, payload: Mapping[str, Any]) -> None:
    stream.write(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True) + "\n")
    stream.flush()


async def _run_worker(args: argparse.Namespace, protocol_stream: TextIO) -> int:
    site_dir = Path(args.site).resolve()
    work_dir = Path(args.work_dir).resolve()
    plugin_id = str(args.plugin_id or "").strip()
    generation_id = str(args.generation_id or "").strip()
    sys.path.insert(0, str(site_dir))
    host: PluginHost | None = None
    ready_sent = False
    try:
        entries = _entry_points(site_dir, plugin_id)
        host = PluginHost(
            (PluginSelection(plugin_id, True),),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
            entry_points_provider=lambda: entries,
        )
        host.bind_plugin_storage_service(
            InstancePluginStorageService(
                work_dir / "storage",
                f"generation-{generation_id}",
            )
        )
        host.bind_notification_port(_GenerationNotificationPort())
        host.bind_reasoning_port(_GenerationReasoningPort())
        host.bind_managed_artifact_sink(_GenerationArtifactSink())
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
        _emit(
            protocol_stream,
            {
                "protocol": PLUGIN_GENERATION_PROTOCOL,
                "type": "ready",
                "generation_id": generation_id,
                "ok": ok,
                "status": "active" if ok else "failed",
                "reason": ""
                if ok
                else str(plugin_status.get("reason") or "plugin_probe_failed"),
                "plugin_id": plugin_id,
                "plugin_version": str(plugin_status.get("plugin_version") or ""),
                "permissions": list(plugin_status.get("permissions") or ()),
                "contribution_snapshot": dict(
                    plugin_status.get("contribution_snapshot") or {}
                ),
            },
        )
        ready_sent = True
        if not ok:
            await host.stop()
            return 1
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                await host.stop()
                return 0
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(request, Mapping):
                continue
            request_id = str(request.get("request_id") or "")
            base = {
                "protocol": PLUGIN_GENERATION_PROTOCOL,
                "type": "response",
                "generation_id": generation_id,
                "request_id": request_id,
            }
            if request.get("protocol") != PLUGIN_GENERATION_PROTOCOL:
                _emit(
                    protocol_stream,
                    {
                        **base,
                        "ok": False,
                        "status": "failed",
                        "reason": "protocol_invalid",
                    },
                )
                continue
            command = str(request.get("command") or "")
            if command == "health":
                snapshot = host.status_snapshot()
                _emit(
                    protocol_stream,
                    {
                        **base,
                        "ok": snapshot.get("status") in {"active", "degraded"},
                        "status": str(snapshot.get("status") or "unknown"),
                        "reason": str(snapshot.get("reason") or ""),
                        "snapshot": snapshot,
                    },
                )
                continue
            if command == "stop":
                snapshot = await host.stop()
                _emit(
                    protocol_stream,
                    {
                        **base,
                        "ok": snapshot.get("status") == "stopped",
                        "status": str(snapshot.get("status") or "unknown"),
                        "reason": str(snapshot.get("reason") or ""),
                        "snapshot": snapshot,
                    },
                )
                return 0
            _emit(
                protocol_stream,
                {
                    **base,
                    "ok": False,
                    "status": "failed",
                    "reason": "command_invalid",
                },
            )
    except Exception:
        if not ready_sent:
            _emit(
                protocol_stream,
                {
                    "protocol": PLUGIN_GENERATION_PROTOCOL,
                    "type": "ready",
                    "generation_id": generation_id,
                    "ok": False,
                    "status": "failed",
                    "reason": "plugin_generation_exception",
                },
            )
        if host is not None:
            try:
                await host.stop()
            except Exception:
                pass
        return 1
    finally:
        try:
            sys.path.remove(str(site_dir))
        except ValueError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--site")
    parser.add_argument("--plugin-id")
    parser.add_argument("--work-dir")
    parser.add_argument("--generation-id")
    args = parser.parse_args(argv)
    if not args.worker or not all(
        (args.site, args.plugin_id, args.work_dir, args.generation_id)
    ):
        return 2
    protocol_stream = sys.stdout
    # Plugin prints must not share the versioned control lane.
    sys.stdout = sys.stderr
    return asyncio.run(_run_worker(args, protocol_stream))


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "PLUGIN_GENERATION_PROTOCOL",
    "PLUGIN_GENERATION_START_TIMEOUT_SECONDS",
    "PLUGIN_GENERATION_STOP_TIMEOUT_SECONDS",
    "PluginGenerationError",
    "PluginGenerationProcess",
]
