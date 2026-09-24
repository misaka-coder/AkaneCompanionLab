"""Function declarations compiled to CapCore; the host still owns execution."""

from __future__ import annotations

import asyncio
import inspect
import re
import types
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, Mapping, Union, get_args, get_origin, get_type_hints

from capcore import CapabilityDescriptor, CapabilityResult, HealthStatus, build_tool_spec

from .contracts import (
    AKANE_PLUGIN_API_VERSION,
    BACKGROUND_JOB_PERMISSION,
    CAPABILITY_INVOKE_PERMISSION,
    CAPABILITY_PROMPT_INVOKE_PERMISSION,
    PLUGIN_QQ_COMMAND_PERMISSION,
    RESOURCE_READ_PERMISSION,
    EVENT_EMIT_PERMISSION,
    CONTEXT_OBSERVE_PERMISSION,
    AGENT_TURN_REQUEST_PERMISSION,
    EVENT_SUBSCRIBE_PERMISSION,
    SKILL_CONTRIBUTION_PERMISSION,
    PluginManifest,
    PluginQQCommandResult,
    is_valid_capability_id,
    is_valid_plugin_id,
)
from .observations import ObservationContext, ObservationReceipt
from .results import Result, validate_followup
from .service_contracts import SERVICE_PROVIDE_PERMISSION, service_target, service_discovery_target, validate_service_dependencies
from .turns import TurnContext, TurnReceipt
from .tasks import TaskContext, TaskReceipt
from .events import EventBinding, EventContext, EventReceipt, EventSubscription, Events
from .connections import ConnectionSpec, Connections, ConnectionError, connection_name_from_permission, connection_permission


_QQ_COMMAND_PATTERN = re.compile(r"^/[^\s/]{1,63}$")
_BACKGROUND_SERVICE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class BackgroundContext(TurnContext):
    """Host-bound ports and shutdown signal for one supervised background service.

    A service runs between turns, so it may request a normal Agent turn but has
    no conversation identity of its own: ``request_turn`` still needs the host's
    signed scope and returns ``context_unbound`` when there is none.
    """

    def __init__(self, controller, tools, resources, events, connections=None, tasks=None):
        self._controller = controller
        self.tools = tools
        self.events = events
        self.connections = connections
        self._tasks = tasks
        self._resources = resources

    @property
    def shutdown_requested(self) -> bool:
        return bool(getattr(self._controller, "shutdown_requested", False))

    async def wait_for_shutdown(self, timeout: float | None = None) -> bool:
        return bool(await self._controller.wait_for_shutdown(timeout))

    async def sleep(self, seconds: float) -> bool:
        """Sleep, returning False as soon as shutdown is requested."""

        try:
            await asyncio.wait_for(self._controller.wait_for_shutdown(float(seconds)), timeout=max(0.0, float(seconds)))
            return False
        except asyncio.TimeoutError:
            return True

    @property
    def resources(self):
        return self._resources

    @property
    def task(self) -> TaskContext:
        return TaskContext(self._tasks)


class ToolCallError(RuntimeError):
    """A failed dependency with its complete structured result and receipt."""

    def __init__(self, capability_id: str, result: CapabilityResult):
        self.capability_id = capability_id
        self.result = result
        super().__init__(f"{capability_id}: {result.status}/{result.reason}")


class Tools:
    """Program consumer: calls never create an implicit model followup."""

    def __init__(self, port):
        self._port = port

    async def budget(self):
        """Read the current deployment limits and shared execution-chain allowance."""
        result = await Tools.call_result(self, {"operation": "resource_budget"}, {})
        if result.is_error or not result.has_value:
            raise ToolCallError("tools.budget", result)
        return result.value

    async def policies(self):
        """Inspect deployment policy order and diagnostics without running a tool."""
        result = await Tools.call_result(self, {"operation": "execution_policies"}, {})
        if result.is_error or not result.has_value:
            raise ToolCallError("tools.policies", result)
        return result.value

    async def call_result(self, capability_id: str, arguments: dict[str, Any]) -> CapabilityResult:
        if self._port is None:
            return CapabilityResult(
                is_error=True, status="rejected", reason="capability_invoke_permission_required",
            )
        return await self._port.call_result(capability_id, arguments)

    async def call(self, capability_id: str, arguments: dict[str, Any]) -> Any:
        result = await self.call_result(capability_id, arguments)
        if result.is_error:
            raise ToolCallError(capability_id, result)
        if not result.has_value:
            raise ToolCallError(capability_id, CapabilityResult(
                is_error=True, status="error", reason="capability_result_value_missing",
            ))
        return result.value


class Services(Tools):
    """Call a versioned service contract through the ordinary program port."""

    async def list(self, service_id=None):
        """Inspect active providers and full method schemas; this grants no execution permission."""
        target = service_discovery_target(service_id)
        result = await super().call_result(target, {})
        if result.is_error or not result.has_value:
            raise ToolCallError("services.list", result)
        return result.value

    async def call_result(self, service_id, method, arguments, *, version=1):
        return await super().call_result(service_target(service_id, method, version), arguments)

    async def call(self, service_id, method, arguments, *, version=1):
        result = await self.call_result(service_id, method, arguments, version=version)
        if result.is_error or not result.has_value:
            raise ToolCallError(f"{service_id}.{method}", result)
        return result.value


@dataclass(frozen=True)
class ToolContext(ObservationContext, TurnContext):
    """Host-bound ports for one function call; never supply conversation IDs."""

    tools: Tools
    invocation: Any
    _resources: Any = None
    events: Any = None
    _connections: Any = None
    _tasks: Any = None

    @property
    def connections(self):
        return Connections(self._connections)

    @property
    def services(self):
        return Services(self.tools._port)

    @property
    def resources(self):
        if self._resources is None:
            raise RuntimeError("resource_read_permission_required")
        return self._resources

    @property
    def task(self) -> TaskContext:
        return TaskContext(self._tasks)


def _type_schema(annotation: Any) -> dict[str, Any]:
    if annotation is Result:
        return {}
    if annotation in (EventReceipt, EventBinding, ObservationReceipt, TurnReceipt, TaskReceipt):
        return annotation.json_schema()
    primitives = {str: "string", int: "integer", float: "number", bool: "boolean", type(None): "null"}
    if annotation in primitives:
        return {"type": primitives[annotation]}
    if annotation is Any:
        return {}
    origin, args = get_origin(annotation), get_args(annotation)
    if origin in (Union, types.UnionType):
        return {"anyOf": [_type_schema(item) for item in args]}
    if origin is Literal:
        if not all(item is None or type(item) in (str, int, float, bool) for item in args):
            raise TypeError("tool_literal_requires_json_scalars")
        return {"enum": list(args)}
    if origin is list and len(args) == 1:
        return {"type": "array", "items": _type_schema(args[0])}
    if origin is dict and len(args) == 2 and args[0] is str:
        return {"type": "object", "additionalProperties": _type_schema(args[1])}
    raise TypeError("tool_annotation_unsupported: use a typed JSON value or an explicit JSON Schema")


@dataclass(frozen=True)
class _FunctionTool:
    function: Any
    descriptor: CapabilityDescriptor
    context_parameter: str | None
    payload_parameter: str | None


class _FunctionAdapter:
    def __init__(self, plugin_id: str, functions: tuple[_FunctionTool, ...], tools: Tools, resources, events, connections=None, tasks=None):
        self.provider_id = f"plugin.{plugin_id}"
        self._functions = {item.descriptor.id: item for item in functions}
        self._tools, self._resources = tools, resources
        self._events = events
        self._connections = connections
        self._tasks = tasks

    async def health(self):
        return HealthStatus(ok=True, status="ready")

    async def list_capabilities(self):
        return tuple(item.descriptor for item in self._functions.values())

    async def invoke(self, capability_id, arguments, context):
        item = self._functions[capability_id]
        kwargs = {item.payload_parameter: dict(arguments)} if item.payload_parameter else dict(arguments)
        if item.context_parameter:
            kwargs[item.context_parameter] = ToolContext(self._tools, context, self._resources, self._events, self._connections, self._tasks)
        try:
            value = item.function(**kwargs)
            if inspect.isawaitable(value):
                value = await value
        except ToolCallError as exc:
            # Preserve the failed dependency's result instead of masking it as
            # a generic plugin exception or fabricating a successful parent.
            return exc.result
        except ConnectionError as exc:
            return CapabilityResult(is_error=True, status=exc.result.status, reason=exc.result.reason)
        if isinstance(value, (EventReceipt, EventBinding, ObservationReceipt, TurnReceipt, TaskReceipt)):
            return CapabilityResult(is_error=value.status in {"rejected", "failed", "partially_failed", "cancelled", "superseded"},
                                    status=value.status, reason=value.reason, content=value.as_dict())
        return value if isinstance(value, CapabilityResult) else CapabilityResult(
            is_error=False, status="ok", content=value,
        )

    async def aclose(self):
        return None


class _FunctionBackgroundService:
    """Adapt one plain function to the host's supervised background job protocol."""

    def __init__(self, function, tools, resources, events, connections=None, tasks=None):
        self.function = function
        self._context_args = (tools, resources, events, connections, tasks)

    async def start(self, controller):
        context = BackgroundContext(controller, *self._context_args)
        value = self.function(context)
        if inspect.isawaitable(value):
            await value

    async def stop(self):
        return None


class _BackgroundServiceAdapter:
    """Run one SDK-declared service under a host-owned invocation.

    A supervised service runs between turns, so it has no per-command request
    id. ``invocation_factory`` is supplied by the host runtime: it registers one
    invocation for the whole service lifetime and returns the request id the
    host's callback lane must use. Without a factory the service still runs, but
    host ports that require an invocation reject with their real reason.
    """

    def __init__(self, service, invocation_factory=None):
        self._service = service
        self._invocation_factory = invocation_factory
        self._scope = None

    async def start(self, controller):
        service = self._service
        if self._invocation_factory is not None:
            self._scope = self._invocation_factory()
            # The runtime hook may return a port bound to this service's own
            # invocation; use it instead of the plugin-wide port.
            port = await self._scope.__aenter__()
            if port is not None:
                tools, resources, events, connections, tasks = self._service._context_args
                service = _FunctionBackgroundService(
                    self._service.function, tools, resources, Events(port), connections, tasks,
                )
        try:
            await service.start(controller)
        finally:
            if self._scope is not None:
                await self._scope.__aexit__(None, None, None)

    async def stop(self):
        await self._service.stop()


class _FunctionCommandHandler:
    """Adapt one plain function to the host's QQ command handler protocol."""

    def __init__(self, function):
        self.function = function

    async def handle(self, request):
        try:
            value = self.function(request)
            if inspect.isawaitable(value):
                value = await value
        except Exception:
            return PluginQQCommandResult(handled=True, reply_text="", reason="qq_command_handler_failed")
        if isinstance(value, PluginQQCommandResult):
            return value
        if isinstance(value, Mapping):
            return PluginQQCommandResult(
                handled=bool(value.get("handled", True)),
                reply_text=str(value.get("reply_text") or ""),
                reason=str(value.get("reason") or ""),
            )
        if isinstance(value, str):
            return PluginQQCommandResult(handled=True, reply_text=value)
        if value is None:
            return PluginQQCommandResult(handled=True)
        return PluginQQCommandResult(handled=True, reply_text=str(value))


class _FunctionEventHandler:
    def __init__(self, function, tools, resources, events, connections=None, tasks=None):
        self.function, self.tools, self.resources, self.events = function, tools, resources, events
        self.connections = connections
        self.tasks = tasks

    async def handle_event(self, event, scope_id):
        try:
            value = self.function(event, EventContext(self.tools, self.events, scope_id, self.resources, self.connections, self.tasks))
            if inspect.isawaitable(value):
                value = await value
        except ToolCallError as exc:
            return exc.result
        except ConnectionError as exc:
            return CapabilityResult(is_error=True, status=exc.result.status, reason=exc.result.reason)
        if isinstance(value, TurnReceipt):
            return CapabilityResult(is_error=not bool(value.request_id),
                                    status="turn_deferred" if value.request_id else "rejected",
                                    reason=value.reason, content=value.as_dict())
        if isinstance(value, ObservationReceipt):
            return CapabilityResult(is_error=value.status in {"rejected", "superseded"},
                                    status=value.status, reason=value.reason, content=value.as_dict())
        if isinstance(value, EventReceipt):
            if value.status == "rejected":
                return CapabilityResult(is_error=True, status=value.status, reason=value.reason, content=value.as_dict())
            return CapabilityResult(is_error=False, status="event_deferred", content=value.as_dict())
        return value if isinstance(value, CapabilityResult) else CapabilityResult(is_error=False, status="ok", content=value)


class Plugin:
    """Declare simple functions or full-schema tools in a normal Python module.

    Expose ``create_plugin`` as an ``akane.plugins.v1`` entry point. Registration
    contributes one ordinary adapter to the existing host; no second registry,
    validator, process manager or execution loop is created by this helper.
    """

    def __init__(self, plugin_id: str, *, version: str = "0.1.0", permissions: tuple[str, ...] = (),
                 requires_services: tuple = (), setup: Any = None):
        if not is_valid_plugin_id(plugin_id):
            raise ValueError("plugin_id_invalid")
        validate_service_dependencies(requires_services)
        self.manifest = PluginManifest(
            plugin_id, version, AKANE_PLUGIN_API_VERSION,
            tuple(dict.fromkeys(permissions)),
            requires_services=requires_services,
        )
        self._setup = setup
        self._functions: list[_FunctionTool] = []
        self._subscriptions: list[tuple[EventSubscription, Any]] = []
        self._qq_commands: list[tuple[str, str, Any]] = []
        self._background_services: list[tuple[str, Any]] = []
        self._skills: list[tuple[str, Path]] = []

    def on(self, event_type: str, *, name: str | None = None, sources: tuple[str, ...] = (),
           scope: str = "global", coalesce: str = "queue",
           persistence: str = "none", request_turn: bool = False):
        """Subscribe to one event type.

        ``persistence`` is ``none``, ``observation`` or ``timeline``;
        ``request_turn`` asks the host to queue one normal Agent turn after this
        handler finishes. The two are independent and never happen implicitly.
        """

        if persistence not in {"none", "observation", "timeline"}:
            raise ValueError("event_persistence_invalid")
        if not isinstance(request_turn, bool):
            raise ValueError("event_request_turn_invalid")

        def declare(fn):
            subscription = EventSubscription(f"{self.manifest.plugin_id}.{name or fn.__name__}", event_type,
                                             (sources,) if isinstance(sources, str) else tuple(sources), scope, coalesce,
                                             persistence, request_turn)
            if not is_valid_capability_id(subscription.subscription_id):
                raise ValueError("event_subscription_id_invalid")
            if len(inspect.signature(fn).parameters) != 2:
                raise TypeError("event_handler_requires_event_and_context")
            if any(item.subscription_id == subscription.subscription_id for item, _ in self._subscriptions):
                raise ValueError("event_subscription_duplicate")
            permissions = [*self.manifest.permissions, EVENT_SUBSCRIBE_PERMISSION]
            if persistence == "timeline":
                permissions.append(CONTEXT_OBSERVE_PERMISSION)
            if request_turn:
                permissions.append(AGENT_TURN_REQUEST_PERMISSION)
            self.manifest = replace(self.manifest, permissions=tuple(dict.fromkeys(permissions)))
            self._subscriptions.append((subscription, fn))
            return fn
        return declare

    def background(self, service_id: str):
        """Declare one supervised background service.

        The function receives a ``BackgroundContext`` and runs until shutdown is
        requested or it returns. Declaring the service adds the background job
        permission; the host still owns the task lifetime, restart policy and
        shutdown timeout.
        """

        def declare(fn):
            normalized = str(service_id or "").strip()
            if _BACKGROUND_SERVICE_ID_PATTERN.fullmatch(normalized) is None:
                raise ValueError("background_service_id_invalid")
            if any(item[0] == normalized for item in self._background_services):
                raise ValueError("background_service_duplicate")
            self.manifest = replace(self.manifest, permissions=tuple(dict.fromkeys(
                # A supervised service may request a normal turn through the host
                # queue; it still has no conversation identity of its own.
                (*self.manifest.permissions, BACKGROUND_JOB_PERMISSION, AGENT_TURN_REQUEST_PERMISSION),
            )))
            self._background_services.append((normalized, fn))
            return fn

        return declare

    def skill(self, name: str, *, root: str | Path | None = None):
        """Declare one read-only Skill package shipped inside this plugin.

        ``root`` defaults to ``skills/<name>`` next to the calling module, which
        is where a packaged plugin normally keeps it. Declaring the skill adds
        the skill contribution permission; the host still validates and publishes
        the package during activation.
        """

        normalized = str(name or "").strip()
        if not normalized:
            raise ValueError("skill_name_invalid")
        if any(item[0] == normalized for item in self._skills):
            raise ValueError("skill_declaration_duplicate")
        if root is None:
            frame = inspect.currentframe()
            caller = frame.f_back if frame is not None else None
            module_file = getattr(inspect.getmodule(caller), "__file__", "") if caller is not None else ""
            if not module_file:
                raise ValueError("skill_root_required")
            resolved = Path(module_file).resolve().parent / "skills" / normalized
        else:
            resolved = Path(root)
        self.manifest = replace(self.manifest, permissions=tuple(dict.fromkeys(
            (*self.manifest.permissions, SKILL_CONTRIBUTION_PERMISSION),
        )))
        self._skills.append((normalized, resolved))
        return resolved

    def tool(
        self, function=None, *, name: str | None = None, description: str | None = None,
        input_schema: Mapping[str, Any] | None = None, output_schema: Mapping[str, Any] | None = None,
        effects: tuple[str, ...] = (), risk: str = "low", confirm: str = "never",
        outputs: tuple = (), visible_in: tuple[str, ...] = ("base", "web", "desktop", "qq"),
        execution_class: str = "sync", followup: str = "required", owner_only: bool = False,
    ):
        return self._declare_function(function, name=name, description=description,
            input_schema=input_schema, output_schema=output_schema, effects=effects, risk=risk,
            confirm=confirm, outputs=outputs, visible_in=visible_in, execution_class=execution_class, followup=followup,
            owner_only=owner_only)

    def service(self, service_id: str, *, version: int = 1):
        service_target(service_id, "validate", version)
        return Service(self, service_id, version)

    def qq_command(self, command: str, *, name: str | None = None):
        """Declare one QQ command handler.

        The function receives the host's ``PluginQQCommandRequest`` and returns a
        ``PluginQQCommandResult`` (or an equivalent mapping). Declaring the
        command adds the QQ command permission; the host still decides which
        sender may use it.
        """

        def declare(fn):
            normalized = str(command or "").strip()
            # Fail at declaration time with the host's own rule instead of
            # deferring to activation, where the reason is only visible in the
            # plugin status.
            if _QQ_COMMAND_PATTERN.fullmatch(normalized) is None:
                raise ValueError("qq_command_invalid")
            key = (name or fn.__name__).strip()
            if any(item[0] == normalized.lower() for item in self._qq_commands):
                raise ValueError("qq_command_duplicate")
            self.manifest = replace(self.manifest, permissions=tuple(dict.fromkeys(
                (*self.manifest.permissions, PLUGIN_QQ_COMMAND_PERMISSION),
            )))
            self._qq_commands.append((normalized, key, fn))
            return fn

        return declare

    def policy(self, policy_id: str):
        from .policies import Policy, POLICY_PROVIDE_PERMISSION
        policy = Policy(self, policy_id)
        self.manifest = replace(self.manifest, permissions=tuple(dict.fromkeys(
            (*self.manifest.permissions, POLICY_PROVIDE_PERMISSION))))
        return policy

    def _declare_function(
        self, function=None, *, name: str | None = None, description: str | None = None,
        input_schema: Mapping[str, Any] | None = None, output_schema: Mapping[str, Any] | None = None,
        effects: tuple[str, ...] = (), risk: str = "low", confirm: str = "never",
        outputs: tuple = (), visible_in: tuple[str, ...] = ("base", "web", "desktop", "qq"),
        execution_class: str = "sync", followup: str = "required", owner_only: bool = False, _service=None,
    ):
        validate_followup(followup)
        if type(owner_only) is not bool:
            raise ValueError("tool_owner_only_invalid")
        def declare(fn):
            capability_id = f"{self.manifest.plugin_id}.{name or fn.__name__}"
            if _service is not None:
                info = service_target(_service.service_id, name or fn.__name__, _service.version)
                capability_id = f"{self.manifest.plugin_id}.service.{_service.service_id}.v{_service.version}.{info['method']}"
            if not is_valid_capability_id(capability_id):
                raise ValueError("capability_id_invalid")
            if any(item.descriptor.id == capability_id for item in self._functions):
                raise ValueError("capability_id_duplicate")
            signature = inspect.signature(fn)
            hints = get_type_hints(fn)
            context_parameters = [key for key, value in hints.items() if key != "return" and value is ToolContext]
            if len(context_parameters) > 1:
                raise TypeError("tool_context_parameter_ambiguous")
            context_parameter = context_parameters[0] if context_parameters else None
            payload_parameter = None
            if input_schema is not None:
                business_parameters = [key for key in signature.parameters if key != context_parameter]
                if len(business_parameters) != 1:
                    raise TypeError("tool_full_schema_requires_one_argument_object")
                payload_parameter = business_parameters[0]
            properties, required = {}, []
            for key, parameter in signature.parameters.items():
                if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.VAR_POSITIONAL):
                    raise TypeError("tool_requires_keyword_parameters")
                if parameter.kind == parameter.VAR_KEYWORD and input_schema is not None:
                    raise TypeError("tool_full_schema_requires_one_argument_object")
                if key == context_parameter or input_schema is not None:
                    continue
                if parameter.kind == parameter.VAR_KEYWORD:
                    raise TypeError("tool_kwargs_requires_input_schema")
                properties[key] = _type_schema(hints.get(key, inspect.Parameter.empty))
                if parameter.default is inspect.Parameter.empty:
                    required.append(key)
                else:
                    properties[key]["default"] = parameter.default
            schema = input_schema if input_schema is not None else {
                "type": "object", "properties": properties, "required": required, "additionalProperties": False,
            }
            returns = output_schema
            if returns is None and "return" in hints:
                returns = _type_schema(hints["return"])
            descriptor = CapabilityDescriptor(
                id=capability_id, display_name=name or fn.__name__, short_hint=description or inspect.getdoc(fn) or fn.__name__,
                visible_in=visible_in, prompt_exposed=_service is None, risk=risk, confirm=confirm, effects=effects,
                trigger=None, inputs=(), outputs=outputs, raw={"execution_class": execution_class, "followup": followup,
                    **({"owner_only": True} if owner_only else {}), **({"service": info} if _service is not None else {})},
                input_schema=schema, output_schema=returns,
            )
            # The one canonical compiler checks complete schemas at declaration
            # time too; execution still uses the host's CapCore admission.
            spec = build_tool_spec(descriptor)
            descriptor = replace(descriptor, input_schema=spec.input_schema, output_schema=spec.output_schema)
            self.manifest = replace(self.manifest, permissions=tuple(dict.fromkeys(
                (*self.manifest.permissions, SERVICE_PROVIDE_PERMISSION if _service is not None else CAPABILITY_PROMPT_INVOKE_PERMISSION),
            )))
            self._functions.append(_FunctionTool(fn, descriptor, context_parameter, payload_parameter))
            return fn

        return declare(function) if function is not None else declare

    def register(self, registrar):
        permissions = self.manifest.permissions
        port = registrar.get_capability_port() if CAPABILITY_INVOKE_PERMISSION in permissions else None
        resources = registrar.get_resource_port() if RESOURCE_READ_PERMISSION in permissions else None
        connections = registrar.get_connection_port() if any(connection_name_from_permission(p) for p in permissions) else None
        tasks = getattr(registrar, "get_task_port", lambda: None)()
        events = Events(registrar.get_events_port() if (
            EVENT_EMIT_PERMISSION in permissions or EVENT_SUBSCRIBE_PERMISSION in permissions or CONTEXT_OBSERVE_PERMISSION in permissions or AGENT_TURN_REQUEST_PERMISSION in permissions
        ) else None)
        tools = Tools(port)
        if self._setup is not None:
            # Author setup runs against the real registrar, so it can obtain the
            # plugin's own storage directory or scoped ports once.
            self._setup(registrar)
        if self._functions:
            registrar.add_capability_adapter(_FunctionAdapter(
                self.manifest.plugin_id, tuple(self._functions), tools, resources, events, connections, tasks,
            ))
        for subscription, function in self._subscriptions:
            registrar.add_event_subscription(subscription, _FunctionEventHandler(function, tools, resources, events, connections, tasks))
        for command, _key, function in self._qq_commands:
            registrar.add_qq_command(command, _FunctionCommandHandler(function))
        invocation_factory = getattr(registrar, "background_service_invocation_factory", None)
        for service_id, function in self._background_services:
            registrar.add_background_service(
                service_id,
                _BackgroundServiceAdapter(
                    _FunctionBackgroundService(function, tools, resources, events, connections, tasks),
                    invocation_factory if callable(invocation_factory) else None,
                ),
            )
        for _name, root in self._skills:
            registrar.add_skill(root)

    def connection(self, name, *, schema, private_fields=(), description="", version=1):
        spec = ConnectionSpec(name, schema, private_fields, description, version)
        if any(item.name == name for item in self.manifest.connections):
            raise ValueError("connection_declarations_duplicate")
        self.manifest = replace(self.manifest, connections=(*self.manifest.connections, spec),
            permissions=tuple(dict.fromkeys((*self.manifest.permissions, connection_permission(name)))))
        return spec


@dataclass(frozen=True)
class Service:
    _plugin: Plugin
    service_id: str
    version: int

    def method(self, function=None, **kwargs):
        return self._plugin._declare_function(function, _service=self, followup="none", **kwargs)


ServiceContext = ToolContext
