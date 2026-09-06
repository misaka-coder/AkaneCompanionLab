"""Invocation-scoped access to the existing host resource resolver.

No resource registry or user-selected identity is introduced. Public plugin
ports accept only a target; the host owns the lifetime and conversation scope.
"""

from __future__ import annotations

import asyncio
import logging
import re
import tempfile
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from capcore import InvocationContext

from .plugin_api import PluginResourceResult
from .plugin_file_io import PluginFileCopyError, copy_file, run_cancellable_copy
from .plugin_subprocess import drain


_logger = logging.getLogger(__name__)
_SAFE_SUFFIX = re.compile(r"^\.[a-zA-Z0-9]{1,16}$")
generation_request_id: ContextVar[str] = ContextVar("plugin_generation_request_id", default="")
dependency_chain: ContextVar[tuple[str, ...]] = ContextVar("plugin_dependency_chain", default=())


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
    dependency_calls: int = 0

    async def aclose(self) -> None:
        self.active = False
        pending = tuple(self.pending)
        for task in pending:
            task.cancel()
        if pending:
            await drain(asyncio.ensure_future(asyncio.gather(*pending, return_exceptions=True)))
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

    async def open(self, target: str) -> PluginResourceResult:
        invocation = current_resource_invocation.get()
        if invocation is None or not invocation.active or invocation.plugin_id != self._plugin_id:
            return PluginResourceResult(False, "rejected", "resource_invocation_required")
        task = asyncio.current_task()
        invocation.pending.add(task)
        try:
            result = await self._provider.open(target, invocation=invocation)
            if not isinstance(result, PluginResourceResult):
                return PluginResourceResult(False, "error", "resource_result_invalid")
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

    async def open(self, target: str, *, invocation: ResourceInvocation) -> PluginResourceResult:
        if not invocation.active:
            return PluginResourceResult(False, "rejected", "resource_invocation_expired")
        context = invocation.context
        if not context.profile_user_id or not context.session_id:
            return PluginResourceResult(False, "rejected", "resource_context_required")
        if not isinstance(target, str) or not target.strip() or len(target) > 512:
            return PluginResourceResult(False, "rejected", "resource_target_invalid")
        task = asyncio.current_task()
        invocation.pending.add(task)
        destination = None
        try:
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
            if invocation.temporary is None:
                self._work_root.mkdir(parents=True, exist_ok=True)
                invocation.temporary = tempfile.TemporaryDirectory(prefix="input-", dir=self._work_root)
            suffix = source.suffix if _SAFE_SUFFIX.fullmatch(source.suffix) else ""
            destination = Path(invocation.temporary.name) / f"{uuid.uuid4().hex}{suffix}"
            await run_cancellable_copy(copy_file, source, destination, expected_size=size)
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
