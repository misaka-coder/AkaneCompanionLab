"""Created-host service binding and generation-aware broker views."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..plugin_events import PluginEventBroker, _PluginEventRegistration
from ..plugin_hooks import PluginHookBroker, _PluginHookRegistration
from ..plugin_managed_artifacts import ManagedArtifactSink
from ..plugin_qq_commands import PluginQQCommandBroker, _PluginCommandRegistration
from ..plugin_storage import PluginStorageService

from .constants import _HOST_AVAILABLE_STATES


class PluginBindingMixin:
    def bind_managed_artifact_sink(self, sink: ManagedArtifactSink) -> None:
        """Bind the host-owned artifact sink before restart-only startup."""

        if self._state != "created":
            raise RuntimeError("plugin_host_already_started")
        if not callable(getattr(sink, "materialize", None)):
            raise TypeError("invalid_managed_artifact_sink")
        self._managed_artifact_sink = sink
        if self._event_broker is not None:
            self._event_broker.observations.sink = sink

    def bind_resource_provider(self, provider: Any) -> None:
        if self._state != "created":
            raise RuntimeError("plugin_host_already_started")
        if not callable(getattr(provider, "open", None)):
            raise TypeError("invalid_resource_provider")
        self._resource_provider = provider

    def bind_capability_provider(self, provider: Any) -> None:
        if self._state != "created":
            raise RuntimeError("plugin_host_already_started")
        if not callable(getattr(provider, "invoke", None)):
            raise TypeError("invalid_capability_provider")
        self._capability_provider = provider

    def bind_connection_provider(self, provider: Any) -> None:
        if self._state != "created":
            raise RuntimeError("plugin_host_already_started")
        if not callable(getattr(provider, "resolve", None)):
            raise TypeError("invalid_connection_provider")
        self._connection_provider = provider

    def bind_plugin_storage_service(self, storage_service: PluginStorageService) -> None:
        """Bind the host-owned scoped storage service before restart-only startup.

        Must be called before :meth:`start`.  Plugins that declare
        ``storage.write`` permission may call ``registrar.get_storage_dir()``
        during registration to receive their scoped data directory.
        """
        if self._state != "created":
            raise RuntimeError("plugin_host_already_started")
        if not callable(getattr(storage_service, "get_plugin_data_dir", None)):
            raise TypeError("invalid_plugin_storage_service")
        self._storage_service = storage_service

    def bind_notification_port(self, port: Any) -> None:
        """Bind the host-owned notification port before restart-only startup.

        Must be called before :meth:`start`.  Plugins that declare
        ``notification.send`` permission receive this port via
        ``registrar.get_notification_port()`` during registration.
        """
        if self._state != "created":
            raise RuntimeError("plugin_host_already_started")
        if not callable(getattr(port, "send", None)):
            raise TypeError("invalid_notification_port")
        self._notification_port = port

    def bind_background_service_invocation_factory(self, factory: Any) -> None:
        """Bind the runtime hook that gives supervised services an invocation.

        ``factory`` is called with a service id and returns an async context
        manager; the host exposes a zero-argument form to the SDK background
        adapter, which has no service id of its own.
        """

        if self._state != "created":
            raise RuntimeError("plugin_host_already_started")
        if not callable(factory):
            self._background_service_invocation_factory = None
            return
        self._background_service_invocation_factory = lambda: factory("")

    def bind_turn_router(self, router: Any) -> None:
        """Bind the host-owned turn-request router for declared subscriptions."""

        if self._state != "created":
            raise RuntimeError("plugin_host_already_started")
        if not callable(getattr(router, "request_turn", None)):
            raise TypeError("invalid_turn_router")
        self.build_event_broker().bind_turn_router(router)

    def build_qq_command_broker(
        self,
        host_registrations: tuple = (),
    ) -> PluginQQCommandBroker:
        """Build an immutable command broker from all activated plugin QQ commands.

        Call after :meth:`start`.  Registrations do not mutate at runtime, and
        the returned broker rejects dispatch once the host begins stopping.
        ``host_registrations`` may carry host builtin commands (e.g. ``/能力``);
        they take precedence over plugin registrations with the same token.
        """
        return PluginQQCommandBroker(
            self._qq_command_registrations_snapshot(),
            host_registrations=tuple(host_registrations or ()),
            availability_provider=lambda: self._state in _HOST_AVAILABLE_STATES,
            registrations_provider=self._qq_command_registrations_snapshot,
        )

    def build_event_broker(self) -> PluginEventBroker:
        """Build a generation-aware observer over active event handlers."""

        if self._event_broker is None:
            self._event_broker = PluginEventBroker(
                (), handler_timeout_seconds=self._event_handler_timeout_seconds,
                availability_provider=lambda: self._state in _HOST_AVAILABLE_STATES,
                registrations_provider=self._event_registrations_snapshot,
                owners_provider=lambda: {key: str(self._generation) for key in self._active_plugins},
                runtime_loop_provider=lambda: self._runtime_loop,
                executor=self._invoke_event_registration,
            )
            self._event_broker.observations.sink = self._managed_artifact_sink
        return self._event_broker

    def bind_events_provider(self, provider):
        if self._state != "created":
            raise RuntimeError("events_provider_binding_requires_created_host")
        if not callable(getattr(provider, "request", None)):
            raise TypeError("event_provider_invalid")
        self._events_provider = provider

    def bind_tasks_provider(self, provider):
        if self._state != "created":
            raise RuntimeError("tasks_provider_binding_requires_created_host")
        if not callable(getattr(provider, "request", None)):
            raise TypeError("task_provider_invalid")
        self._tasks_provider = provider

    def build_hook_broker(self) -> PluginHookBroker:
        """Build the generation-aware execution Hook observer."""

        broker = PluginHookBroker(
            self._hook_registrations_snapshot(),
            handler_timeout_seconds=self._hook_handler_timeout_seconds,
            availability_provider=lambda: self._state in _HOST_AVAILABLE_STATES,
            registrations_provider=self._hook_registrations_snapshot,
            runtime_loop_provider=lambda: self._runtime_loop,
        )
        self._hook_broker = broker
        return broker

    def _qq_command_registrations_snapshot(self) -> tuple[_PluginCommandRegistration, ...]:
        return tuple(
            registration
            for active in self._active_plugins.values()
            for registration in active.qq_command_registrations
        )

    def _event_registrations_snapshot(self) -> tuple[_PluginEventRegistration, ...]:
        return tuple(
            replace(registration, generation_id=str(self._generation))
            for active in self._active_plugins.values()
            for registration in active.event_registrations
        )

    def _hook_registrations_snapshot(self) -> tuple[_PluginHookRegistration, ...]:
        return tuple(
            registration
            for active in self._active_plugins.values()
            for registration in active.hook_registrations
        )
