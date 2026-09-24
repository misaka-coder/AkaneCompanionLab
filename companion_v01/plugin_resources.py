"""Invocation-scoped access to the existing host resource resolver.

No resource registry or user-selected identity is introduced. Public plugin
ports accept only a target; the host owns the lifetime and conversation scope.
"""

from __future__ import annotations
from .execution_resource_policy import current_resource_budget
from .execution_policies import policy_evaluation

import asyncio
import logging
import re
import json
import sys
import tempfile
import time
import uuid
from copy import deepcopy
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from capcore import InvocationContext

from .plugin_api import PluginResourceResult
from .plugin_file_io import PluginFileCopyError, copy_file, run_cancellable_copy
from .plugin_subprocess import PluginProcessRunner, drain
from .plugin_invocation_scope import generation_scopes


_logger = logging.getLogger(__name__)
_SAFE_SUFFIX = re.compile(r"^\.[a-zA-Z0-9]{1,16}$")
generation_request_id: ContextVar[str] = ContextVar("plugin_generation_request_id", default="")
dependency_chain: ContextVar[tuple[str, ...]] = ContextVar("plugin_dependency_chain", default=())
service_call_chain: ContextVar[bool] = ContextVar("plugin_service_call_chain", default=False)
host_service_parameters: ContextVar[dict] = ContextVar("host_service_parameters", default={})


@dataclass(eq=False)
class ResourceInvocation:
    plugin_id: str
    context: InvocationContext
    active: bool = True
    loop: asyncio.AbstractEventLoop = field(default_factory=asyncio.get_running_loop)
    temporary: tempfile.TemporaryDirectory | None = None
    pending: set[asyncio.Task] = field(default_factory=set)
    capability_id: str = ""
    can_invoke_capabilities: bool = False
    dependency_chain: tuple[str, ...] = field(default_factory=dependency_chain.get)
    resource_budget: Any = field(default_factory=current_resource_budget.get, repr=False)
    policy_evaluation: bool = field(default_factory=policy_evaluation.get, repr=False)
    connection_names: tuple[str, ...] = ()
    can_read_resources: bool = True
    # Exact values obtained over private ports, never a business-key blacklist.
    private_values: set[str] = field(default_factory=set, repr=False)
    generation_id: str = ""
    event_delivery_id: str = ""
    # Routing provenance, not execution authority. Never sent to worker code.
    event_origin_context: InvocationContext | None = field(default=None, repr=False)
    can_emit_events: bool = False
    can_bind_events: bool = False
    can_observe_context: bool = False
    can_request_turn: bool = False
    turn_epoch: int | None = None
    origin_request_id: str = ""
    generation_scopes: tuple = field(default_factory=generation_scopes.get, repr=False)
    service_call_chain: bool = field(default_factory=service_call_chain.get, repr=False)
    dependency_result_receipts: set[str] = field(default_factory=set, repr=False)
    connection_specs: tuple = ()
    connection_snapshots: dict = field(default_factory=dict, repr=False)
    connection_snapshot_lock: Any = field(default=None, repr=False)
    # Host-captured validated arguments, for request-dependent connection sources.
    arguments: dict = field(default_factory=dict, repr=False)
    # Ephemeral resources issued by an authorized connection source. Paths stay
    # in this host scope and use the same copy/lifetime policy as ordinary input.
    issued_resources: dict = field(default_factory=dict, repr=False)
    host_parameters: dict = field(default_factory=lambda: deepcopy(host_service_parameters.get()), repr=False)

    def revoke(self) -> None:
        """Withdraw new calls immediately; retain files until existing work drains."""
        self.active = False

    async def run(self, awaitable, *, timeout_seconds=None):
        """Supervise cancellation outside plugin code that may catch/uncancel it."""
        work = asyncio.ensure_future(awaitable)
        try:
            return await asyncio.wait_for(asyncio.shield(work), timeout=timeout_seconds)
        except TimeoutError:
            self.revoke()
            work.cancel()
            try:
                result, _ = await drain(work)
                return result
            except asyncio.CancelledError:
                raise TimeoutError() from None
        except asyncio.CancelledError:
            self.revoke()
            work.cancel()
            # A signal is not an outcome: preserve a real terminal result,
            # including unconfirmed remote completion, before cleaning files.
            result, _ = await drain(work)
            return result

    async def aclose(self) -> None:
        self.revoke()
        pending = tuple(self.pending)
        for task in pending:
            task.cancel()
        if pending:
            await drain(asyncio.ensure_future(asyncio.gather(*pending, return_exceptions=True)))
        self.dependency_result_receipts.clear()
        self.connection_snapshots.clear()
        self.issued_resources.clear()
        self.host_parameters.clear()
        if self.temporary is not None:
            try:
                self.temporary.cleanup()
            except OSError:
                _logger.warning("plugin_resource_cleanup_failed")
            self.temporary = None


current_resource_invocation: ContextVar[ResourceInvocation | None] = ContextVar(
    "plugin_resource_invocation",
    default=None,
)


class ScopedPluginResourcePort:
    def __init__(self, plugin_id: str, provider: Any) -> None:
        self._plugin_id = plugin_id
        self._provider = provider

    async def work_directory(self) -> Path:
        invocation = current_resource_invocation.get()
        if invocation is None or not invocation.active or invocation.plugin_id != self._plugin_id:
            raise RuntimeError("resource_invocation_required")
        if getattr(invocation.context, "global_scope", False):
            raise RuntimeError("resource_context_required")
        if asyncio.current_task().cancelling():
            raise asyncio.CancelledError()
        if invocation.temporary is None:
            invocation.temporary = tempfile.TemporaryDirectory(prefix="akane-plugin-work-")
        return Path(invocation.temporary.name)

    async def open(self, target: str, *, representation: str = "original") -> PluginResourceResult:
        invocation = current_resource_invocation.get()
        if invocation is None or not invocation.active or invocation.plugin_id != self._plugin_id:
            return PluginResourceResult(False, "rejected", "resource_invocation_required")
        task = asyncio.current_task()
        if task.cancelling():
            invocation.revoke()
            raise asyncio.CancelledError()
        invocation.pending.add(task)
        try:
            if representation not in ("original", "document"):
                return PluginResourceResult(False, "rejected", "resource_representation_invalid")
            options = {} if representation == "original" else {"representation": representation}
            result = await self._provider.open(target, invocation=invocation, **options)
            if not isinstance(result, PluginResourceResult):
                return PluginResourceResult(False, "error", "resource_result_invalid")
            if result.ok and result.representation != representation:
                return PluginResourceResult(False, "error", "resource_representation_mismatch")
            return result
        except asyncio.CancelledError:
            raise
        except Exception:
            return PluginResourceResult(False, "error", "resource_open_failed")
        finally:
            invocation.pending.discard(task)


class GeneratedFileResourceProvider:
    """Stage private copies using GeneratedFileService's sole input resolver."""

    def __init__(self, service: Any, *, work_root: Path) -> None:
        if not callable(getattr(service, "resolve_input_resource", None)):
            raise TypeError("resource_resolver_required")
        self._service = service
        self._work_root = Path(work_root).resolve()

    async def open(self, target: str, *, invocation: ResourceInvocation,
                   representation: str = "original") -> PluginResourceResult:
        if not invocation.active:
            return PluginResourceResult(False, "rejected", "resource_invocation_expired")
        context = invocation.context
        if not context.profile_user_id or not context.session_id:
            return PluginResourceResult(False, "rejected", "resource_context_required")
        if not isinstance(target, str) or not target.strip() or len(target) > 512:
            return PluginResourceResult(False, "rejected", "resource_target_invalid")
        if representation not in ("original", "document"):
            return PluginResourceResult(False, "rejected", "resource_representation_invalid")
        task = asyncio.current_task()
        invocation.pending.add(task)
        destination = None
        try:
            resource = invocation.issued_resources.get(target.strip())
            if resource is None:
                resource = self._service.resolve_input_resource(
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    target=target.strip(),
                    timestamp=int(time.time()),
                )
            if not resource:
                return PluginResourceResult(False, "not_found", "resource_not_found")
            source = Path(resource["absolute_path"])
            if not source.is_file():
                return PluginResourceResult(False, "not_found", "resource_unavailable")
            size = source.stat().st_size
            if representation == "document" and size > 50_000_000:
                return PluginResourceResult(False, "rejected", "document_source_too_large")
            if invocation.temporary is None:
                self._work_root.mkdir(parents=True, exist_ok=True)
                invocation.temporary = tempfile.TemporaryDirectory(prefix="input-", dir=self._work_root)
            suffix = source.suffix if _SAFE_SUFFIX.fullmatch(source.suffix) else ""
            destination = Path(invocation.temporary.name) / f"{uuid.uuid4().hex}{suffix}"
            await run_cancellable_copy(copy_file, source, destination, expected_size=size)
            if representation == "document":
                document = await self._document(destination)
                destination.unlink(missing_ok=True)
                if not document.ok:
                    return document
                destination, size = document.path, document.file_size
            if not invocation.active:
                destination.unlink(missing_ok=True)
                return PluginResourceResult(False, "rejected", "resource_invocation_expired")
            return PluginResourceResult(
                True,
                "ready",
                path=destination,
                handle=str(resource.get("handle") or target),
                name=Path(str(resource.get("name") or source.name)).name,
                file_size=size,
                representation=representation,
            )
        except asyncio.CancelledError:
            if destination is not None:
                destination.unlink(missing_ok=True)
            raise
        except (OSError, KeyError, PluginFileCopyError):
            if destination is not None:
                try:
                    destination.unlink(missing_ok=True)
                except OSError:
                    _logger.warning("plugin_resource_cleanup_failed")
            return PluginResourceResult(False, "error", "resource_copy_failed")
        finally:
            invocation.pending.discard(task)

    async def _document(self, source: Path) -> PluginResourceResult:
        """Bound parsing in an owned child; cancellation drains before cleanup."""
        output = source.with_name(f"{uuid.uuid4().hex}.json")
        runner = PluginProcessRunner()
        keep_output = False
        try:
            code, wire = await runner.run(
                [sys.executable, Path(__file__).with_name("document_material.py"), source, output],
                capture=True, timeout=60,
            )
            try:
                result = json.loads(wire)
            except (ValueError, UnicodeError):
                result = {}
            if code or result != {"ok": True} or not output.is_file():
                from .document_material import MATERIAL_REASONS
                reason = result.get("reason") if isinstance(result, dict) else None
                return PluginResourceResult(False, "error", reason if isinstance(reason, str) and reason in MATERIAL_REASONS
                                            else "document_read_failed")
            keep_output = True
            return PluginResourceResult(True, "ready", path=output, file_size=output.stat().st_size,
                                        representation="document")
        except asyncio.TimeoutError:
            return PluginResourceResult(False, "error", "document_read_timeout")
        finally:
            _, cancelled = await drain(asyncio.create_task(runner.aclose()))
            # The invocation owns successful output; failed/cancelled workers
            # can never publish a late file after this point.
            if cancelled or not keep_output:
                output.unlink(missing_ok=True)
            if cancelled:
                raise asyncio.CancelledError()
