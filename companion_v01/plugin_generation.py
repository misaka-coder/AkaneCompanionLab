"""Versioned process boundary for one managed PluginHost generation.

The protocol exposes lifecycle control and public CapCore invocation values.
Runtime calls remain on the in-process PluginHost until every port used by a
plugin has an explicit projection. No host objects are pickled across this
boundary.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid
from importlib import metadata as importlib_metadata
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, TextIO

from capcore import CapabilityDescriptor, CapabilityResult, InvocationContext

from .instance_profile import PluginSelection
from .plugin_api import (
    AKANE_PLUGIN_ENTRYPOINT_GROUP,
    NotificationIntent,
    NotificationResult,
    PluginReasoningResult,
)
from .plugin_contribution_policy import TrustedStatefulPluginContributionPolicy
from .plugin_generation_artifacts import (
    GenerationArtifactOutboxSink,
    consume_generation_artifact,
    is_generation_artifact_reference,
)
from .plugin_generation_codec import (
    PluginGenerationCodecError,
    capability_descriptor_from_wire,
    capability_descriptor_to_wire,
    capability_result_from_wire,
    capability_result_to_wire,
    invocation_context_from_wire,
    invocation_context_to_wire,
    json_snapshot,
    notification_intent_from_wire,
    notification_intent_to_wire,
    notification_result_from_wire,
    notification_result_to_wire,
)
from .plugin_host import PluginHost
from .plugin_managed_artifacts import (
    ManagedArtifactError,
    ManagedArtifactSink,
    normalize_managed_artifact_reference,
)
from .plugin_storage import InstancePluginStorageService


PLUGIN_GENERATION_PROTOCOL = "akane.plugin-generation.v1"
PLUGIN_GENERATION_START_TIMEOUT_SECONDS = 45.0
PLUGIN_GENERATION_STOP_TIMEOUT_SECONDS: float | None = None


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
        stop_timeout_seconds: float | None = PLUGIN_GENERATION_STOP_TIMEOUT_SECONDS,
        managed_artifact_timeout_seconds: float = 5.0,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.site_dir = Path(site_dir).resolve()
        self.plugin_id = str(plugin_id or "").strip()
        self.work_dir = Path(work_dir).resolve()
        self.python_executable = str(python_executable or sys.executable)
        self.start_timeout_seconds = max(0.1, float(start_timeout_seconds))
        self.stop_timeout_seconds = (
            None
            if stop_timeout_seconds is None
            else max(0.1, float(stop_timeout_seconds))
        )
        self.managed_artifact_timeout_seconds = max(
            0.1,
            float(managed_artifact_timeout_seconds),
        )
        self.generation_id = uuid.uuid4().hex
        self._artifact_outbox_dir = self.work_dir / "outbox" / self.generation_id
        self._process: subprocess.Popen[str] | None = None
        self._ready: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._pending: dict[str, queue.Queue[dict[str, Any] | None]] = {}
        self._pending_lock = threading.Lock()
        self._active_invocation_condition = threading.Condition()
        self._active_invocations = 0
        self._reader: threading.Thread | None = None
        self._write_lock = threading.Lock()
        self._stop_lock = threading.Lock()
        self._capability_descriptors: Mapping[str, CapabilityDescriptor] = MappingProxyType({})
        self._managed_artifact_sink: ManagedArtifactSink | None = None
        self._notification_port: Any = None
        self._notification_loop: asyncio.AbstractEventLoop | None = None
        self._callback_loop: asyncio.AbstractEventLoop | None = None
        self._callback_thread: threading.Thread | None = None
        self._callback_loop_ready = threading.Event()
        self._callback_lock = threading.Lock()
        self._callback_futures: dict[str, Any] = {}
        self._stopping = False
        self._closed = False

    @property
    def running(self) -> bool:
        process = self._process
        return bool(process is not None and process.poll() is None and not self._closed)

    @property
    def capability_descriptors(self) -> Mapping[str, CapabilityDescriptor]:
        """Return the immutable public Capability snapshot published at ready."""

        return self._capability_descriptors

    def bind_managed_artifact_sink(self, sink: ManagedArtifactSink) -> None:
        if self._process is not None:
            raise RuntimeError("plugin_generation_already_started")
        if not callable(getattr(sink, "materialize", None)):
            raise TypeError("invalid_managed_artifact_sink")
        self._managed_artifact_sink = sink

    def bind_notification_port(self, port: Any) -> None:
        if self._process is not None:
            raise RuntimeError("plugin_generation_already_started")
        if not callable(getattr(port, "send", None)):
            raise TypeError("invalid_notification_port")
        self._notification_port = port
        try:
            self._notification_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._notification_loop = None

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
                    "--managed-artifact-timeout",
                    str(self.managed_artifact_timeout_seconds),
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
            ready = self._next_response(self._ready, self.start_timeout_seconds)
            self._validate_response(ready, expected_type="ready")
            if not ready.get("ok"):
                raise PluginGenerationError(
                    str(ready.get("reason") or "plugin_generation_start_failed")
                )
            self._capability_descriptors = _decode_capability_snapshot(
                ready.get("capabilities")
            )
            ready["startup_ms"] = round((time.perf_counter() - started_at) * 1000.0, 3)
            return ready
        except Exception:
            self._terminate()
            raise

    def health(self) -> dict[str, Any]:
        return self._request("health", timeout_seconds=self.start_timeout_seconds)

    async def invoke(
        self,
        capability_id: str,
        args: Mapping[str, Any],
        *,
        context: InvocationContext,
    ) -> CapabilityResult:
        """Invoke one public Capability without imposing a generation timeout."""

        try:
            wire_args = json_snapshot(dict(args))
            wire_context = invocation_context_to_wire(context)
        except (TypeError, ValueError, PluginGenerationCodecError) as exc:
            raise PluginGenerationError("plugin_generation_invocation_invalid") from exc
        request_id, response_queue = self._send_request(
            "invoke",
            {
                "capability_id": str(capability_id or ""),
                "args": wire_args,
                "context": wire_context,
            },
        )
        try:
            try:
                response = await asyncio.to_thread(
                    self._next_response,
                    response_queue,
                    None,
                )
            except asyncio.CancelledError:
                self._discard_pending(request_id, response_queue)
                try:
                    response_queue.put_nowait(None)
                except queue.Full:
                    pass
                self._send_cancel(request_id)
                raise
            finally:
                self._discard_pending(request_id, response_queue)
            self._validate_response(response, expected_type="response")
            if response.get("request_id") != request_id:
                raise PluginGenerationError("plugin_generation_protocol_invalid")
            if not response.get("ok"):
                raise PluginGenerationError(
                    str(response.get("reason") or "plugin_generation_invoke_failed")
                )
            try:
                result = capability_result_from_wire(response.get("result"))
            except PluginGenerationCodecError as exc:
                raise PluginGenerationError("plugin_generation_protocol_invalid") from exc
            return await self._materialize_generation_artifact(
                result,
                capability_id=str(capability_id or ""),
                context=context,
            )
        finally:
            self._finish_invocation()

    async def _materialize_generation_artifact(
        self,
        result: CapabilityResult,
        *,
        capability_id: str,
        context: InvocationContext,
    ) -> CapabilityResult:
        content = result.content
        if not isinstance(content, Mapping) or "managed_artifacts" not in content:
            return result
        references = content.get("managed_artifacts")
        if (
            not isinstance(references, list)
            or len(references) != 1
            or not is_generation_artifact_reference(references[0])
        ):
            return _generation_artifact_failure("managed_artifact_handoff_invalid")
        reference = references[0]
        try:
            staged = await asyncio.to_thread(
                consume_generation_artifact,
                self._artifact_outbox_dir,
                reference,
                capability_id=capability_id,
            )
        except ManagedArtifactError as exc:
            return _generation_artifact_failure(exc.reason)
        try:
            if self._managed_artifact_sink is None:
                return _generation_artifact_failure("managed_artifact_sink_unavailable")
            try:
                materialized = await asyncio.wait_for(
                    self._managed_artifact_sink.materialize(
                        staged.draft,
                        context=context,
                        capability_id=capability_id,
                    ),
                    timeout=self.managed_artifact_timeout_seconds,
                )
            except asyncio.CancelledError:
                raise
            except (TimeoutError, asyncio.TimeoutError):
                return _generation_artifact_failure("managed_artifact_write_timeout")
            except ManagedArtifactError as exc:
                return _generation_artifact_failure(exc.reason)
            except Exception:
                return _generation_artifact_failure("managed_artifact_write_failed")
            normalized = normalize_managed_artifact_reference(
                materialized,
                draft=staged.draft,
                capability_id=capability_id,
            )
            if normalized is None or is_generation_artifact_reference(normalized):
                return _generation_artifact_failure("managed_artifact_invalid_reference")
            projected_content = dict(content)
            projected_content["managed_artifacts"] = [normalized]
            return CapabilityResult(
                is_error=result.is_error,
                status=result.status,
                reason=result.reason,
                content=projected_content,
            )
        finally:
            await asyncio.to_thread(staged.cleanup)

    def stop(self) -> dict[str, Any]:
        with self._stop_lock:
            if self._closed:
                return {"ok": True, "status": "stopped", "reason": "already_stopped"}
            process = self._process
            if process is None:
                self._closed = True
                return {"ok": True, "status": "stopped", "reason": "not_started"}
            deadline = (
                None
                if self.stop_timeout_seconds is None
                else time.monotonic() + self.stop_timeout_seconds
            )
            try:
                if process.poll() is None:
                    response = self._request("stop", timeout_seconds=self.stop_timeout_seconds)
                    if response.get("ok") and not self._wait_for_invocations(deadline):
                        response = {
                            "ok": False,
                            "status": "failed",
                            "reason": "plugin_generation_timeout",
                        }
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

    def _request(
        self,
        command: str,
        *,
        timeout_seconds: float | None,
    ) -> dict[str, Any]:
        request_id, response_queue = self._send_request(command)
        try:
            response = self._next_response(response_queue, timeout_seconds)
        finally:
            self._discard_pending(request_id, response_queue)
        self._validate_response(response, expected_type="response")
        if response.get("request_id") != request_id:
            raise PluginGenerationError("plugin_generation_protocol_invalid")
        return response

    def _send_request(
        self,
        command: str,
        extra: Mapping[str, Any] | None = None,
    ) -> tuple[str, queue.Queue[dict[str, Any] | None]]:
        response_queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)
        request_id = uuid.uuid4().hex
        with self._write_lock:
            process = self._process
            if (
                self._closed
                or (self._stopping and command != "cancel")
                or process is None
                or process.poll() is not None
            ):
                raise PluginGenerationError("plugin_generation_unavailable")
            if process.stdin is None:
                raise PluginGenerationError("plugin_generation_unavailable")
            if command == "stop":
                self._stopping = True
            payload = {
                "protocol": PLUGIN_GENERATION_PROTOCOL,
                "type": "request",
                "request_id": request_id,
                "command": command,
                **dict(extra or {}),
            }
            with self._pending_lock:
                self._pending[request_id] = response_queue
            try:
                process.stdin.write(
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
                )
                process.stdin.flush()
                if command == "invoke":
                    with self._active_invocation_condition:
                        self._active_invocations += 1
            except (BrokenPipeError, OSError) as exc:
                self._discard_pending(request_id, response_queue)
                raise PluginGenerationError("plugin_generation_unavailable") from exc
        return request_id, response_queue

    def _finish_invocation(self) -> None:
        with self._active_invocation_condition:
            self._active_invocations -= 1
            self._active_invocation_condition.notify_all()

    def _wait_for_invocations(self, deadline: float | None) -> bool:
        with self._active_invocation_condition:
            while self._active_invocations:
                if deadline is None:
                    self._active_invocation_condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._active_invocation_condition.wait(timeout=remaining)
            return True

    def _next_response(
        self,
        response_queue: queue.Queue[dict[str, Any] | None],
        timeout_seconds: float | None,
    ) -> dict[str, Any]:
        try:
            if timeout_seconds is None:
                response = response_queue.get()
            else:
                response = response_queue.get(timeout=max(0.1, float(timeout_seconds)))
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
                    self._fail_pending({"protocol": "", "type": "invalid"})
                    continue
                if not isinstance(payload, Mapping):
                    self._fail_pending({"protocol": "", "type": "invalid"})
                    continue
                response = dict(payload)
                if response.get("type") == "callback_request":
                    self._dispatch_host_callback(response)
                    continue
                if response.get("type") == "callback_cancel":
                    self._cancel_host_callback(response)
                    continue
                if response.get("type") == "ready":
                    self._ready.put(response)
                    continue
                request_id = str(response.get("request_id") or "")
                with self._pending_lock:
                    response_queue = self._pending.get(request_id)
                if response_queue is not None:
                    try:
                        response_queue.put_nowait(response)
                    except queue.Full:
                        pass
        finally:
            self._ready.put(None)
            self._fail_pending(None)

    def _discard_pending(
        self,
        request_id: str,
        response_queue: queue.Queue[dict[str, Any] | None],
    ) -> None:
        with self._pending_lock:
            if self._pending.get(request_id) is response_queue:
                self._pending.pop(request_id, None)

    def _fail_pending(self, response: dict[str, Any] | None) -> None:
        with self._pending_lock:
            pending = tuple(self._pending.values())
        for response_queue in pending:
            try:
                response_queue.put_nowait(response)
            except queue.Full:
                pass

    def _send_cancel(self, target_request_id: str) -> None:
        try:
            cancel_id, cancel_queue = self._send_request(
                "cancel",
                {"target_request_id": target_request_id},
            )
        except PluginGenerationError:
            return
        self._discard_pending(cancel_id, cancel_queue)

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
        with self._write_lock:
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
        self._fail_pending(None)
        self._shutdown_callback_loop()
        shutil.rmtree(self._artifact_outbox_dir, ignore_errors=True)

    def _dispatch_host_callback(self, request: Mapping[str, Any]) -> None:
        callback_id = str(request.get("callback_id") or "")
        if (
            request.get("protocol") != PLUGIN_GENERATION_PROTOCOL
            or request.get("generation_id") != self.generation_id
            or len(callback_id) != 32
            or any(char not in "0123456789abcdef" for char in callback_id)
            or request.get("callback") != "notification.send"
        ):
            self._send_callback_failure(callback_id, "callback_protocol_invalid")
            return
        try:
            intent = notification_intent_from_wire(request.get("intent"))
        except PluginGenerationCodecError:
            self._send_callback_failure(callback_id, "notification_protocol_invalid")
            return
        if self._notification_port is None:
            self._send_callback_result(
                callback_id,
                NotificationResult(
                    ok=False,
                    status="not_configured",
                    reason="no_notification_port_bound",
                ),
            )
            return
        loop = self._notification_loop
        if loop is None or loop.is_closed() or not loop.is_running():
            loop = self._ensure_callback_loop()
        if loop is None:
            self._send_callback_failure(callback_id, "notification_host_unavailable")
            return
        with self._callback_lock:
            if callback_id in self._callback_futures:
                self._send_callback_failure(callback_id, "duplicate_callback_id")
                return
        coroutine = self._deliver_notification(callback_id, intent)
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        except RuntimeError:
            coroutine.close()
            self._send_callback_failure(callback_id, "notification_host_unavailable")
            return
        with self._callback_lock:
            self._callback_futures[callback_id] = future
        future.add_done_callback(
            lambda done, cid=callback_id: self._discard_callback(cid, done)
        )

    async def _deliver_notification(
        self,
        callback_id: str,
        intent: NotificationIntent,
    ) -> None:
        try:
            result = await self._notification_port.send(intent)
            if not isinstance(result, NotificationResult):
                result = NotificationResult(
                    ok=False,
                    status="error",
                    reason="invalid_notification_result",
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            result = NotificationResult(
                ok=False,
                status="error",
                reason="delivery_exception",
            )
        self._send_callback_result(callback_id, result)

    def _send_callback_result(
        self,
        callback_id: str,
        result: NotificationResult,
    ) -> None:
        try:
            wire_result = notification_result_to_wire(result)
        except PluginGenerationCodecError:
            wire_result = notification_result_to_wire(
                NotificationResult(
                    ok=False,
                    status="error",
                    reason="invalid_notification_result",
                )
            )
        self._write_callback_response(
            {
                "protocol": PLUGIN_GENERATION_PROTOCOL,
                "type": "callback_response",
                "generation_id": self.generation_id,
                "callback_id": callback_id,
                "ok": True,
                "result": wire_result,
            }
        )

    def _send_callback_failure(self, callback_id: str, reason: str) -> None:
        if not callback_id:
            return
        self._write_callback_response(
            {
                "protocol": PLUGIN_GENERATION_PROTOCOL,
                "type": "callback_response",
                "generation_id": self.generation_id,
                "callback_id": callback_id,
                "ok": False,
                "reason": str(reason or "notification_callback_failed"),
            }
        )

    def _write_callback_response(self, payload: Mapping[str, Any]) -> None:
        with self._write_lock:
            process = self._process
            if (
                self._closed
                or process is None
                or process.poll() is not None
                or process.stdin is None
            ):
                return
            try:
                process.stdin.write(
                    json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"))
                    + "\n"
                )
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                return

    def _cancel_host_callback(self, request: Mapping[str, Any]) -> None:
        if (
            request.get("protocol") != PLUGIN_GENERATION_PROTOCOL
            or request.get("generation_id") != self.generation_id
        ):
            return
        callback_id = str(request.get("callback_id") or "")
        with self._callback_lock:
            future = self._callback_futures.get(callback_id)
        if future is not None:
            future.cancel()

    def _discard_callback(self, callback_id: str, future: Any) -> None:
        with self._callback_lock:
            if self._callback_futures.get(callback_id) is future:
                self._callback_futures.pop(callback_id, None)

    def _ensure_callback_loop(self) -> asyncio.AbstractEventLoop | None:
        with self._callback_lock:
            loop = self._callback_loop
            thread = self._callback_thread
            if loop is not None and thread is not None and thread.is_alive():
                return loop
            self._callback_loop_ready.clear()
            thread = threading.Thread(
                target=self._run_callback_loop,
                name=f"plugin-generation-callback:{self.generation_id[:8]}",
                daemon=True,
            )
            self._callback_thread = thread
            thread.start()
        if not self._callback_loop_ready.wait(timeout=self.start_timeout_seconds):
            return None
        with self._callback_lock:
            return self._callback_loop

    def _run_callback_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with self._callback_lock:
            self._callback_loop = loop
        self._callback_loop_ready.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    def _shutdown_callback_loop(self) -> None:
        with self._callback_lock:
            futures = tuple(self._callback_futures.values())
            loop = self._callback_loop
            thread = self._callback_thread
        for future in futures:
            future.cancel()
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self._callback_lock:
            self._callback_futures.clear()
            self._callback_loop = None
            self._callback_thread = None


class _GenerationNotificationPort:
    def __init__(
        self,
        *,
        generation_id: str,
        protocol_stream: TextIO,
        pending: dict[str, asyncio.Future[Mapping[str, Any]]],
    ) -> None:
        self._generation_id = generation_id
        self._protocol_stream = protocol_stream
        self._pending = pending

    async def send(self, intent: NotificationIntent) -> NotificationResult:
        try:
            wire_intent = notification_intent_to_wire(intent)
        except PluginGenerationCodecError:
            return NotificationResult(
                ok=False,
                status="rejected",
                reason="invalid_notification_intent",
            )
        callback_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending[callback_id] = future
        _emit(
            self._protocol_stream,
            {
                "protocol": PLUGIN_GENERATION_PROTOCOL,
                "type": "callback_request",
                "generation_id": self._generation_id,
                "callback_id": callback_id,
                "callback": "notification.send",
                "intent": wire_intent,
            },
        )
        try:
            response = await future
        except asyncio.CancelledError:
            _emit(
                self._protocol_stream,
                {
                    "protocol": PLUGIN_GENERATION_PROTOCOL,
                    "type": "callback_cancel",
                    "generation_id": self._generation_id,
                    "callback_id": callback_id,
                },
            )
            raise
        finally:
            self._pending.pop(callback_id, None)
        if not response.get("ok"):
            return NotificationResult(
                ok=False,
                status="error",
                reason=str(response.get("reason") or "notification_callback_failed"),
            )
        try:
            return notification_result_from_wire(response.get("result"))
        except PluginGenerationCodecError:
            return NotificationResult(
                ok=False,
                status="error",
                reason="invalid_notification_result",
            )


class _GenerationReasoningPort:
    async def analyze(self, _request: Any) -> PluginReasoningResult:
        return PluginReasoningResult(
            ok=False,
            status="unavailable",
            reason="generation_probe",
        )


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


def _decode_capability_snapshot(value: object) -> Mapping[str, CapabilityDescriptor]:
    if not isinstance(value, list):
        raise PluginGenerationError("plugin_generation_protocol_invalid")
    descriptors: dict[str, CapabilityDescriptor] = {}
    try:
        for item in value:
            descriptor = capability_descriptor_from_wire(item)
            if descriptor.id in descriptors:
                raise PluginGenerationCodecError("duplicate_capability")
            descriptors[descriptor.id] = descriptor
    except PluginGenerationCodecError as exc:
        raise PluginGenerationError("plugin_generation_protocol_invalid") from exc
    return MappingProxyType(descriptors)


def _generation_artifact_failure(reason: str) -> CapabilityResult:
    return CapabilityResult(
        is_error=True,
        status="error",
        reason=str(reason or "managed_artifact_write_failed"),
    )


def _response_base(*, generation_id: str, request_id: str) -> dict[str, Any]:
    return {
        "protocol": PLUGIN_GENERATION_PROTOCOL,
        "type": "response",
        "generation_id": generation_id,
        "request_id": request_id,
    }


async def _handle_worker_request(
    host: PluginHost,
    request: Mapping[str, Any],
    *,
    generation_id: str,
    protocol_stream: TextIO,
) -> None:
    request_id = str(request.get("request_id") or "")
    base = _response_base(generation_id=generation_id, request_id=request_id)
    command = str(request.get("command") or "")
    try:
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
            return
        if command == "invoke":
            raw_args = request.get("args")
            if not isinstance(raw_args, Mapping) or any(
                not isinstance(key, str) for key in raw_args
            ):
                raise PluginGenerationCodecError("invocation_args_invalid")
            context = invocation_context_from_wire(request.get("context"))
            result = await host.invoke(
                str(request.get("capability_id") or ""),
                dict(raw_args),
                context=context,
            )
            _emit(
                protocol_stream,
                {
                    **base,
                    "ok": True,
                    "status": str(result.status or ""),
                    "reason": str(result.reason or ""),
                    "result": capability_result_to_wire(result),
                },
            )
            return
        _emit(
            protocol_stream,
            {
                **base,
                "ok": False,
                "status": "failed",
                "reason": "command_invalid",
            },
        )
    except asyncio.CancelledError:
        _emit(
            protocol_stream,
            {
                **base,
                "ok": True,
                "status": "cancelled",
                "reason": "plugin_invoke_cancelled",
                "result": capability_result_to_wire(
                    CapabilityResult(
                        is_error=True,
                        status="cancelled",
                        reason="plugin_invoke_cancelled",
                    )
                ),
            },
        )
        raise
    except PluginGenerationCodecError:
        _emit(
            protocol_stream,
            {
                **base,
                "ok": False,
                "status": "failed",
                "reason": "invocation_protocol_invalid",
            },
        )
    except Exception:
        _emit(
            protocol_stream,
            {
                **base,
                "ok": False,
                "status": "failed",
                "reason": "plugin_generation_exception",
            },
        )


async def _handle_worker_stop(
    host: PluginHost,
    *,
    generation_id: str,
    request_id: str,
    protocol_stream: TextIO,
) -> None:
    await asyncio.sleep(0)
    try:
        snapshot = await host.stop()
    except asyncio.CancelledError:
        raise
    except Exception:
        _emit(
            protocol_stream,
            {
                **_response_base(
                    generation_id=generation_id,
                    request_id=request_id,
                ),
                "ok": False,
                "status": "failed",
                "reason": "plugin_generation_exception",
            },
        )
        return
    _emit(
        protocol_stream,
        {
            **_response_base(
                generation_id=generation_id,
                request_id=request_id,
            ),
            "ok": snapshot.get("status") == "stopped",
            "status": str(snapshot.get("status") or "unknown"),
            "reason": str(snapshot.get("reason") or ""),
            "snapshot": snapshot,
        },
    )


async def _run_worker(args: argparse.Namespace, protocol_stream: TextIO) -> int:
    site_dir = Path(args.site).resolve()
    work_dir = Path(args.work_dir).resolve()
    plugin_id = str(args.plugin_id or "").strip()
    generation_id = str(args.generation_id or "").strip()
    sys.path.insert(0, str(site_dir))
    host: PluginHost | None = None
    ready_sent = False
    callback_responses: dict[str, asyncio.Future[Mapping[str, Any]]] = {}
    try:
        entries = _entry_points(site_dir, plugin_id)
        host = PluginHost(
            (PluginSelection(plugin_id, True),),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
            entry_points_provider=lambda: entries,
            managed_artifact_timeout_seconds=float(args.managed_artifact_timeout),
        )
        host.bind_plugin_storage_service(
            InstancePluginStorageService(
                work_dir / "storage",
                f"generation-{generation_id}",
            )
        )
        host.bind_notification_port(
            _GenerationNotificationPort(
                generation_id=generation_id,
                protocol_stream=protocol_stream,
                pending=callback_responses,
            )
        )
        host.bind_reasoning_port(_GenerationReasoningPort())
        host.bind_managed_artifact_sink(
            GenerationArtifactOutboxSink(work_dir / "outbox" / generation_id)
        )
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
                "capabilities": [
                    capability_descriptor_to_wire(descriptor)
                    for _capability_id, descriptor in sorted(
                        host.capability_descriptors.items()
                    )
                ],
            },
        )
        ready_sent = True
        if not ok:
            await host.stop()
            return 1
        active_requests: dict[str, asyncio.Task[None]] = {}
        stop_task: asyncio.Task[None] | None = None
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                if stop_task is None:
                    await host.stop()
                else:
                    await stop_task
                if active_requests:
                    await asyncio.gather(*active_requests.values(), return_exceptions=True)
                return 0
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(request, Mapping):
                continue
            if (
                request.get("protocol") == PLUGIN_GENERATION_PROTOCOL
                and request.get("type") == "callback_response"
                and request.get("generation_id") == generation_id
            ):
                callback_id = str(request.get("callback_id") or "")
                callback_future = callback_responses.get(callback_id)
                if callback_future is not None and not callback_future.done():
                    callback_future.set_result(dict(request))
                continue
            request_id = str(request.get("request_id") or "")
            base = _response_base(generation_id=generation_id, request_id=request_id)
            if (
                request.get("protocol") != PLUGIN_GENERATION_PROTOCOL
                or request.get("type") != "request"
                or not request_id
            ):
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
            if command == "cancel":
                target_request_id = str(request.get("target_request_id") or "")
                target = active_requests.get(target_request_id)
                if target is not None and not target.done():
                    target.cancel()
                    cancel_status = "cancel_requested"
                else:
                    cancel_status = "not_inflight"
                _emit(
                    protocol_stream,
                    {
                        **base,
                        "ok": True,
                        "status": cancel_status,
                        "reason": "",
                    },
                )
                continue
            if command == "stop":
                if stop_task is not None:
                    _emit(
                        protocol_stream,
                        {
                            **base,
                            "ok": False,
                            "status": "failed",
                            "reason": "plugin_generation_stopping",
                        },
                    )
                    continue
                # Keep the stdin lane alive while PluginHost drains so reverse
                # callback responses can complete accepted work.
                stop_task = asyncio.create_task(
                    _handle_worker_stop(
                        host,
                        generation_id=generation_id,
                        request_id=request_id,
                        protocol_stream=protocol_stream,
                    ),
                    name=f"plugin-generation-stop:{generation_id[:8]}",
                )
                continue
            if stop_task is not None:
                _emit(
                    protocol_stream,
                    {
                        **base,
                        "ok": False,
                        "status": "failed",
                        "reason": "plugin_generation_stopping",
                    },
                )
                continue
            if request_id in active_requests:
                _emit(
                    protocol_stream,
                    {
                        **base,
                        "ok": False,
                        "status": "failed",
                        "reason": "duplicate_request_id",
                    },
                )
                continue
            task = asyncio.create_task(
                _handle_worker_request(
                    host,
                    request,
                    generation_id=generation_id,
                    protocol_stream=protocol_stream,
                ),
                name=f"plugin-generation-request:{request_id[:8]}",
            )
            active_requests[request_id] = task
            task.add_done_callback(
                lambda done, rid=request_id: active_requests.pop(rid, None)
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
    parser.add_argument("--managed-artifact-timeout")
    args = parser.parse_args(argv)
    if not args.worker or not all(
        (
            args.site,
            args.plugin_id,
            args.work_dir,
            args.generation_id,
            args.managed_artifact_timeout,
        )
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
