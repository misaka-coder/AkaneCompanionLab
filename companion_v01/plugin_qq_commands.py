"""Host-owned broker for QQ command contributions from supervised plugins.

After PluginHost.start() activates all plugins, the composition root calls
PluginHost.build_qq_command_broker() to obtain an immutable PluginQQCommandBroker.
The QQ route calls broker.dispatch() before starting an LLM turn; if any plugin
handles the command the LLM turn is skipped and the plugin's reply (if any) is
sent directly.

Command matching is exact-string on the normalised command token (leading slash,
Chinese command verbs included).  The broker never raises; all errors are
returned as safe PluginQQCommandResult values.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from .plugin_api import (
    PluginQQCommandHandler,
    PluginQQCommandRequest,
    PluginQQCommandResult,
)


MAX_COMMAND_REPLY_CHARS = 2000
MAX_COMMAND_ARGS_CHARS = 2000
DEFAULT_HANDLER_TIMEOUT_SECONDS = 10.0
COMMAND_FAILURE_REPLY = "这个命令暂时没有执行成功，请稍后再试。"
_SAFE_REASON_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_GROUP_SENDER_ROLES = frozenset({"owner", "admin", "member"})


@dataclass(frozen=True, slots=True)
class _PluginCommandRegistration:
    plugin_id: str
    command: str          # normalised lowercase
    handler: PluginQQCommandHandler


class PluginQQCommandBroker:
    """Immutable command index built from activated plugin registrations.

    Constructed by PluginHost.build_qq_command_broker() after start().  It holds
    references to handler objects but never to raw adapters or the gateway.
    """

    def __init__(
        self,
        registrations: tuple[_PluginCommandRegistration, ...],
        *,
        handler_timeout_seconds: float = DEFAULT_HANDLER_TIMEOUT_SECONDS,
        availability_provider: Callable[[], bool] | None = None,
    ) -> None:
        self._handler_timeout_seconds = max(0.01, float(handler_timeout_seconds))
        self._availability_provider = availability_provider or (lambda: True)
        # Fast lookup: normalised command → first matching registration
        self._index: dict[str, _PluginCommandRegistration] = {}
        for reg in registrations:
            if reg.command not in self._index:
                self._index[reg.command] = reg

    @property
    def registered_commands(self) -> tuple[str, ...]:
        return tuple(self._index)

    def handles(self, command: str) -> bool:
        """True if any plugin handles this exact command."""
        return _normalise_command(command) in self._index

    async def dispatch(
        self,
        *,
        command: str,
        args: str,
        qq_number: int,
        group_id: int,
        is_group: bool,
        idempotency_key: str = "",
        sender_role: str = "",
        profile_user_id: str = "",
        session_id: str = "",
        character_pack_id: str = "",
    ) -> PluginQQCommandResult:
        """Dispatch to the first matching plugin handler.

        Returns PluginQQCommandResult(handled=False) when no plugin matches.
        Never raises; handler exceptions are caught and returned as errors.
        """
        key = _normalise_command(command)
        registration = self._index.get(key)
        if registration is None:
            return PluginQQCommandResult(handled=False, reason="no_matching_command")
        try:
            available = bool(self._availability_provider())
        except Exception:
            available = False
        if not available:
            return PluginQQCommandResult(
                handled=True,
                reply_text=COMMAND_FAILURE_REPLY,
                reason="host_unavailable",
            )

        args_text = str(args or "").strip()
        if len(args_text) > MAX_COMMAND_ARGS_CHARS:
            return PluginQQCommandResult(
                handled=True,
                reply_text=COMMAND_FAILURE_REPLY,
                reason="command_args_too_large",
            )
        try:
            normalized_qq_number = int(qq_number or 0)
            normalized_group_id = int(group_id or 0)
        except (TypeError, ValueError, OverflowError):
            return PluginQQCommandResult(
                handled=True,
                reply_text=COMMAND_FAILURE_REPLY,
                reason="invalid_command_context",
            )
        if not (0 <= normalized_qq_number < 10**20 and 0 <= normalized_group_id < 10**20):
            return PluginQQCommandResult(
                handled=True,
                reply_text=COMMAND_FAILURE_REPLY,
                reason="invalid_command_context",
            )
        normalized_sender_role = str(sender_role or "").strip().lower()
        if normalized_sender_role not in _GROUP_SENDER_ROLES:
            normalized_sender_role = ""
        if not is_group:
            normalized_sender_role = ""

        request = PluginQQCommandRequest(
            command=command,
            args=args_text,
            qq_number=normalized_qq_number,
            group_id=normalized_group_id,
            is_group=bool(is_group),
            idempotency_key=_make_idempotency_key(
                command,
                normalized_qq_number,
                normalized_group_id,
                source_key=idempotency_key,
            ),
            sender_role=normalized_sender_role,
            profile_user_id=_bounded_context_value(profile_user_id, maximum=200),
            session_id=_bounded_context_value(session_id, maximum=200),
            character_pack_id=_bounded_context_value(character_pack_id, maximum=120),
        )
        try:
            result = await asyncio.wait_for(
                registration.handler.handle(request),
                timeout=self._handler_timeout_seconds,
            )
        except (asyncio.TimeoutError, TimeoutError):
            return PluginQQCommandResult(
                handled=True,
                reply_text=COMMAND_FAILURE_REPLY,
                reason="handler_timeout",
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return PluginQQCommandResult(
                handled=True,
                reply_text=COMMAND_FAILURE_REPLY,
                reason="handler_exception",
            )

        if not isinstance(result, PluginQQCommandResult):
            return PluginQQCommandResult(
                handled=True,
                reply_text=COMMAND_FAILURE_REPLY,
                reason="invalid_handler_result",
            )

        # Bound reply text
        reply_text = str(result.reply_text or "").strip()
        if len(reply_text) > MAX_COMMAND_REPLY_CHARS:
            reply_text = reply_text[:MAX_COMMAND_REPLY_CHARS]
        return PluginQQCommandResult(
            handled=result.handled,
            reply_text=reply_text,
            reason=_safe_reason(result.reason),
        )


def _normalise_command(command: str) -> str:
    return str(command or "").strip().lower()


def _safe_reason(reason: object) -> str:
    candidate = str(reason or "").strip().lower()
    if not candidate:
        return ""
    if _SAFE_REASON_PATTERN.fullmatch(candidate) is None:
        return "plugin_reported_error"
    return candidate


def _bounded_context_value(value: object, *, maximum: int) -> str:
    text = str(value or "").strip()
    if "\x00" in text:
        return ""
    return text[:maximum]


def _make_idempotency_key(
    command: str,
    qq_number: int,
    group_id: int,
    *,
    source_key: str = "",
) -> str:
    source = str(source_key or "").strip()
    if not source:
        source = f"{command}:{qq_number}:{group_id}:{time.time_ns()}"
    return hashlib.sha256(source.encode()).hexdigest()[:32]


__all__ = ["PluginQQCommandBroker", "_PluginCommandRegistration"]
