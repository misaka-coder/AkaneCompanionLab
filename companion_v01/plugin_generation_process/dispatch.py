"""Dispatch hooks and QQ commands through the shared child process."""

from __future__ import annotations

import asyncio
import queue
from ..plugin_api import PluginHookEnvelope, PluginQQCommandResult
from ..plugin_hooks import PluginHookDispatchResult
from ..plugin_generation_codec import PluginGenerationCodecError, plugin_hook_dispatch_result_from_wire, plugin_hook_envelope_to_wire, plugin_qq_command_result_from_wire, qq_command_dispatch_to_wire
from .contracts import PluginGenerationError, _generation_hook_failure, _generation_qq_command_failure


class GenerationDispatchMixin:
    """Dispatch hooks and QQ commands through the shared child process."""

    async def dispatch(self, hook: PluginHookEnvelope) -> PluginHookDispatchResult:
        """Dispatch one execution Hook through the active generation."""

        try:
            wire_hook = plugin_hook_envelope_to_wire(hook)
        except PluginGenerationCodecError as exc:
            return _generation_hook_failure("invalid_hook", exc.args[0])
        try:
            request_id, response_queue = self._send_request(
                "hook.dispatch",
                {"hook": wire_hook},
            )
        except PluginGenerationError as exc:
            return _generation_hook_failure("host_unavailable", exc.reason)
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
            try:
                self._validate_response(response, expected_type="response")
            except PluginGenerationError as exc:
                return _generation_hook_failure("host_unavailable", exc.reason)
            if response.get("request_id") != request_id:
                return _generation_hook_failure(
                    "host_unavailable",
                    "plugin_generation_protocol_invalid",
                )
            if not response.get("ok"):
                return _generation_hook_failure(
                    "host_unavailable",
                    str(response.get("reason") or "plugin_hook_dispatch_failed"),
                )
            try:
                return plugin_hook_dispatch_result_from_wire(response.get("result"))
            except PluginGenerationCodecError:
                return _generation_hook_failure(
                    "host_unavailable",
                    "plugin_generation_protocol_invalid",
                )
        except PluginGenerationError as exc:
            return _generation_hook_failure("host_unavailable", exc.reason)
        finally:
            self._finish_invocation()

    def dispatch_from_consumer(self, hook: PluginHookEnvelope) -> PluginHookDispatchResult:
        """Synchronous Hook boundary used by Engine and outbound workers."""

        if not isinstance(hook, PluginHookEnvelope):
            return _generation_hook_failure("invalid_hook", "invalid_hook")
        try:
            wire_hook = plugin_hook_envelope_to_wire(hook)
            request_id, response_queue = self._send_request(
                "hook.dispatch",
                {"hook": wire_hook},
            )
        except PluginGenerationCodecError as exc:
            return _generation_hook_failure("invalid_hook", exc.args[0])
        except PluginGenerationError as exc:
            return _generation_hook_failure("host_unavailable", exc.reason)
        try:
            try:
                response = self._next_response(response_queue, None)
            finally:
                self._discard_pending(request_id, response_queue)
            self._validate_response(response, expected_type="response")
            if response.get("request_id") != request_id or not response.get("ok"):
                return _generation_hook_failure(
                    "host_unavailable",
                    str(response.get("reason") or "plugin_generation_protocol_invalid"),
                )
            try:
                return plugin_hook_dispatch_result_from_wire(response.get("result"))
            except PluginGenerationCodecError:
                return _generation_hook_failure(
                    "host_unavailable",
                    "plugin_generation_protocol_invalid",
                )
        except PluginGenerationError as exc:
            return _generation_hook_failure("host_unavailable", exc.reason)
        finally:
            self._finish_invocation()

    async def dispatch_qq_command(
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
        conversation_ref: str = "",
    ) -> PluginQQCommandResult:
        try:
            wire_args = qq_command_dispatch_to_wire(
                command=command,
                args=args,
                qq_number=qq_number,
                group_id=group_id,
                is_group=is_group,
                idempotency_key=idempotency_key,
                sender_role=sender_role,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                conversation_ref=conversation_ref,
            )
        except PluginGenerationCodecError:
            return _generation_qq_command_failure("invalid_command_context")
        try:
            request_id, response_queue = self._send_request(
                "qq_command.dispatch",
                {"command_args": wire_args},
            )
        except PluginGenerationError as exc:
            return _generation_qq_command_failure(exc.reason)
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
            if response.get("request_id") != request_id or not response.get("ok"):
                return _generation_qq_command_failure(
                    str(response.get("reason") or "plugin_generation_protocol_invalid")
                )
            try:
                return plugin_qq_command_result_from_wire(response.get("result"))
            except PluginGenerationCodecError:
                return _generation_qq_command_failure(
                    "plugin_generation_protocol_invalid"
                )
        except PluginGenerationError as exc:
            return _generation_qq_command_failure(exc.reason)
        finally:
            self._finish_invocation()
