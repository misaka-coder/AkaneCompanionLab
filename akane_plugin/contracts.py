"""Public M65-C contract for trusted, in-process Akane plugins.

This module intentionally contains contracts only.  It does not discover,
load, activate, or retain plugins and must remain safe to import from an
installed plugin artifact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from capcore import CapabilityAdapter, CapabilityResult, InvocationContext


AKANE_PLUGIN_API_VERSION = 1
AKANE_PLUGIN_ENTRYPOINT_GROUP = "akane.plugins.v1"
DIAGNOSTICS_INVOKE_PERMISSION = "diagnostics.invoke"
CAPABILITY_PROMPT_INVOKE_PERMISSION = "capability.prompt.invoke"
NETWORK_READ_PERMISSION = "network.read"
MANAGED_ARTIFACT_WRITE_PERMISSION = "artifact.write"
RESOURCE_READ_PERMISSION = "resource.read"
CAPABILITY_INVOKE_PERMISSION = "capability.invoke"
IMAGE_CONNECTION_READ_PERMISSION = "connection.image_generation.read"
RVC_CONNECTION_READ_PERMISSION = "connection.rvc.read"
PLUGIN_STORAGE_WRITE_PERMISSION = "storage.write"
BACKGROUND_JOB_PERMISSION = "job.run"
NOTIFICATION_SEND_PERMISSION = "notification.send"
PLUGIN_QQ_COMMAND_PERMISSION = "qq.command.register"
SYSTEM_PROMPT_CONTRIBUTION_PERMISSION = "prompt.system.contribute"
SKILL_CONTRIBUTION_PERMISSION = "skill.contribute"
EVENT_SUBSCRIBE_PERMISSION = "event.subscribe"
EVENT_EMIT_PERMISSION = "event.emit"
CONTEXT_OBSERVE_PERMISSION = "context.observe"
AGENT_TURN_REQUEST_PERMISSION = "agent.turn.request"
HOOK_SUBSCRIBE_PERMISSION = "hook.subscribe"
DIRECT_CONVERSATION_EVENT = "conversation.direct.inbound"
GROUP_CONVERSATION_EVENT = "conversation.group.inbound"
POKE_CONVERSATION_EVENT = "conversation.poke.inbound"
BEFORE_TOOL_CALL_HOOK = "before_tool_call"
AFTER_TOOL_CALL_HOOK = "after_tool_call"
BEFORE_OUTBOUND_PLAN_HOOK = "before_outbound_plan"
AFTER_DELIVERY_HOOK = "after_delivery"
MAX_MANAGED_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_PLUGIN_ID_LENGTH = 64
MAX_CAPABILITY_ID_LENGTH = 128
MAX_PERMISSION_ID_LENGTH = 64
PLUGIN_STATE_EFFECT = "plugin_state"

_PLUGIN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_CAPABILITY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_PERMISSION_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def is_valid_plugin_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= MAX_PLUGIN_ID_LENGTH
        and _PLUGIN_ID_PATTERN.fullmatch(value) is not None
    )


def is_valid_capability_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= MAX_CAPABILITY_ID_LENGTH
        and _CAPABILITY_ID_PATTERN.fullmatch(value) is not None
    )


def is_valid_permission_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= MAX_PERMISSION_ID_LENGTH
        and _PERMISSION_ID_PATTERN.fullmatch(value) is not None
    )


@dataclass(frozen=True, slots=True)
class ServiceDependency:
    service_id: str
    version: int = 1

    def __post_init__(self):
        from .service_contracts import service_target
        service_target(self.service_id, "validate", self.version)


@dataclass(frozen=True, slots=True)
class PluginManifest:
    plugin_id: str
    plugin_version: str
    plugin_api_version: int
    permissions: tuple[str, ...]
    requires_services: tuple[ServiceDependency, ...] = ()
    connections: tuple = ()


@dataclass(frozen=True, slots=True)
class PluginResultExperience:
    """Domain semantics for Akane to project; never a free-form prompt."""

    summary: str
    facts: tuple[str, ...] = ()
    as_of: str = ""
    warnings: tuple[str, ...] = ()
    interpretation_notes: tuple[str, ...] = ()
    suggested_next_actions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PluginResultPayload:
    """Public capability data plus bounded domain semantics for Akane."""

    content: Any
    experience: PluginResultExperience


@dataclass(frozen=True, slots=True)
class ManagedArtifactDraft:
    """One artifact for host storage: small bytes or a plugin-local file.

    Provide exactly one of ``data`` or ``path``. A path is a private worker
    input, never a public result or host storage locator. Files are copied in
    chunks; the producer keeps them available until its invocation completes.
    """

    data: bytes = b""
    title: str = ""
    output_format: str = ""
    mime_type: str = ""
    summary: str = ""
    send_to_user: bool = True
    path: Path | None = None
    # Delivery is a request, never evidence of transport success. Voice/both
    # require a supported audio artifact; defaults preserve API-v1 wheels.
    delivery_mode: str = "file"
    # Existing opaque handles only. Host resolves them in the trusted calling
    # conversation and owns provenance/version numbering, never the plugin.
    source_handles: tuple[str, ...] = ()
    revision_of: str = ""

    @classmethod
    def from_file(cls, path: str | Path, *, mime_type: str = "", title: str = "",
                  summary: str = "", send_to_user: bool = False, delivery_mode: str = "file",
                  source_handles: tuple[str, ...] = (), revision_of: str = "") -> ManagedArtifactDraft:
        """Hand an existing producer-local file to the host, without a public path.

        Resolves the path now, before a later working-directory change. The host
        still enforces artifact.write, a generated_file output slot and its byte
        budget. Keep the file alive until the invocation finishes. Registration
        defaults to no delivery; pass send_to_user=True for an explicit request.
        """
        import mimetypes

        source = Path(path).resolve(strict=True)
        if not source.is_file():
            raise ValueError("managed_artifact_source_unavailable")
        output_format = source.suffix.lstrip(".").lower()
        if not re.fullmatch(r"[a-z0-9]{1,16}", output_format):
            raise ValueError("managed_artifact_format_unsupported")
        # Match canonical host MIME aliases where platform registries differ.
        canonical = {"md": "text/markdown", "csv": "text/csv", "wav": "audio/wav",
                     "mp3": "audio/mpeg", "m4a": "audio/mp4", "flac": "audio/flac",
                     "ogg": "audio/ogg", "opus": "audio/ogg", "lrc": "text/plain",
                     "srt": "application/x-subrip", "vtt": "text/vtt"}
        return cls(path=source, title=title or source.stem, output_format=output_format,
                   mime_type=mime_type or canonical.get(output_format) or
                   mimetypes.guess_type(source.name)[0] or "application/octet-stream",
                   summary=summary, send_to_user=send_to_user, delivery_mode=delivery_mode,
                   source_handles=source_handles, revision_of=revision_of)


@dataclass(frozen=True, slots=True, init=False)
class ManagedArtifactPayload:
    """Capability content plus ordered artifacts committed by PluginHost.

    The generated-file output slot's max_bytes budgets their combined size.
    """

    content: Any
    artifacts: tuple[ManagedArtifactDraft, ...]

    def __init__(
        self, content: Any, artifacts: tuple[ManagedArtifactDraft, ...] | ManagedArtifactDraft | None = None,
        *, artifact: ManagedArtifactDraft | None = None,
    ) -> None:
        # API v1 wheels may still use the original singular constructor.
        # Normalize at this boundary; the runtime only owns the tuple path.
        if artifact is not None:
            if artifacts is not None:
                raise TypeError("managed_artifact_payload_ambiguous")
            artifacts = (artifact,)
        elif isinstance(artifacts, ManagedArtifactDraft):
            artifacts = (artifacts,)
        object.__setattr__(self, "content", content)
        object.__setattr__(self, "artifacts", artifacts if artifacts is not None else ())


# ---------------------------------------------------------------------------
# Invocation resource contracts
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PluginResourceResult:
    """Private input work copy; valid only until the current invocation ends.

    The source is resolved in the host-bound conversation, never one supplied
    by the plugin. Do not return this object/path as public capability content.
    """

    ok: bool
    status: str
    reason: str = ""
    path: Path | None = None
    handle: str = ""
    name: str = ""
    file_size: int = 0
    representation: str = "original"


class PluginResourcePort(Protocol):
    async def open(self, target: str, *, representation: str = "original") -> PluginResourceResult:
        """Copy one existing attachment/generated handle for this invocation.

        original returns the unchanged bytes. document returns a private UTF-8
        JSON file (schema akane.document-material.v1) produced by the host's
        parser: source_format, blocks, complete, limitations. Blocks preserve
        source order: text/paragraph(text), table(rows), sheet(name, rows), or
        page(number, text). Cells retain JSON scalar types; formula/date cells
        are tagged objects. No preview fallback or silent size truncation.
        complete=False means unsupported content was detected: consumers must
        not use that extraction as a complete replacement of the original.
        Even complete=True describes content extraction, not layout fidelity.
        """
        ...

    async def work_directory(self) -> Path:
        """Private scratch directory owned until artifact handoff completes.

        Works without an input resource (e.g. text-to-image or documents).
        Requires the captured resource.read port and a live invocation; raises
        RuntimeError with a stable reason outside that scope. Never publish the
        directory as content. artifact.write is still required to register files.
        The host removes it after success, failure or fully drained cancellation.
        """
        ...


class PluginCapabilityPort(Protocol):
    async def call_result(self, capability_id: str | dict[str, Any], arguments: dict[str, Any]) -> CapabilityResult:
        """Call an enabled tool or service through normal program admission.

        A tool target is its ID. ``Services`` supplies a service target as
        ``{service_id, method, version}`` using an exact integer version.
        Service discovery uses ``{operation: "services.list", service_id: str | None}``
        with empty arguments, under the same live scope and capability.invoke permission.

        Success ``value`` is the complete canonical JSON value. ``content``
        retains the public result metadata, including managed_artifacts when
        present. No model followup is implicitly created. The host binds scope,
        permissions and cancellation; supplied arguments cannot override them.
        """
        ...

    async def invoke(self, capability_id: str, arguments: dict[str, Any]) -> CapabilityResult:
        """Invoke an enabled artifact capability within the current conversation.

        Requires capability.invoke. Ordinary target permissions still apply;
        no auto-approval, extra Job or delivery. Targets must expose send_to_user,
        which is false for this operation. Success content contains an ordered
        `artifacts` list of existing generated handles; open them via resources.
        No raw paths, identity override or background use. Cycles/depth/call
        budgets are host-enforced. Cancellation waits for actual dependency
        cleanup; a real failure after cancellation is returned, not hidden.
        """
        ...


@dataclass(frozen=True, slots=True)
class PluginConnectionResult:
    """Private, invocation-only connection snapshot; never capability content.

    Legacy fields retain their existing host settings as authority. Declared
    connections return configuration in options; private_option_fields names
    whole private values that must not enter public results or logs.
    """

    ok: bool
    status: str
    reason: str = ""
    base_url: str = field(default="", repr=False)
    model: str = ""
    api_key: str = field(default="", repr=False)
    options: dict[str, Any] = field(default_factory=dict, repr=False)
    private_option_fields: tuple[str, ...] = ()


class PluginConnectionPort(Protocol):
    async def resolve(self, name: str) -> PluginConnectionResult:
        """Resolve only an explicitly permitted named connection in a live call.

        Not available at registration/health/staging time. Resolve afresh per
        invocation so setting changes and revocation take effect without a
        second plugin configuration file. No caller-selected identity.
        """
        ...


# ---------------------------------------------------------------------------
# Background job contracts
# ---------------------------------------------------------------------------

class PluginJobController(Protocol):
    """What the host provides to a running PluginBackgroundJob.

    The job polls ``shutdown_requested`` or awaits ``wait_for_shutdown()`` to
    know when to exit its main loop.  The host sets the shutdown signal when
    ``PluginHost.stop()`` is called; the host then awaits the job task with a
    bounded timeout before proceeding to close adapters.
    """

    @property
    def shutdown_requested(self) -> bool:
        """True once the host has signalled shutdown."""
        ...

    async def wait_for_shutdown(self, timeout: float | None = None) -> bool:
        """Block until shutdown is signalled.  Returns True if signalled, False on timeout."""
        ...


class PluginBackgroundJob(Protocol):
    """A supervised, restart-only background service contributed by a plugin.

    Plugins register one or more named services with
    ``registrar.add_background_service(service_id, service)``.
    The host wraps ``start(controller)`` in an asyncio task on the lifecycle
    loop after the plugin's staged contributions are published. A plugin may
    contribute this job without registering a capability adapter. The job runs until
    ``controller.shutdown_requested`` becomes True or ``stop()`` is called.

    Constraints:
    - ``start()`` must honour the controller's shutdown signal and exit cleanly
      within the host's job_stop_timeout.
    - ``start()`` must NOT hold a reference to Engine, QQ gateway, Care
      runtime, or any other host singleton.  It interacts with the host only
      through contracts provided via ``PluginRegistrar`` (storage dir,
      notification port, capabilities).
    - ``stop()`` is called by the host as a secondary signal if ``start()``
      has not exited; it must be idempotent.
    """

    async def start(self, controller: PluginJobController) -> None:
        """Run the job until controller.shutdown_requested is True."""
        ...

    async def stop(self) -> None:
        """Signal the job to stop if it has not already exited."""
        ...


# ---------------------------------------------------------------------------
# Notification port contracts
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class NotificationIntent:
    """A bounded fixed-text delivery request from a plugin.

    channel:
        Delivery channel identifier.  "qq_text" is the only built-in channel;
        others must be explicitly supported by the bound NotificationPort.
    recipient_id:
        Channel-specific recipient identifier.  For "qq_text":
            "group:<group_id>"   — QQ group message
            "user:<qq_number>"   — QQ private message
    text:
        The already-final message text.  It is bounded by the host before delivery; text that
        exceeds the limit is truncated and the result reason records that fact.
    idempotency_key:
        A stable, plugin-chosen key for deduplication within one plugin's bounded
        recent delivery history.  The host treats successfully delivered
        duplicate keys as already-delivered and returns status
        "already_delivered" without re-sending.  Plugins that need durable or
        longer-lived domain deduplication must also persist their delivery claim
        in plugin-owned storage.
    """

    channel: str
    recipient_id: str
    text: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class NotificationResult:
    """Outcome of one proactive delivery attempt."""

    ok: bool
    status: str   # delivered/queued/already_delivered/not_configured/host_unavailable/rejected/error
    reason: str = ""


class NotificationPort(Protocol):
    """Host-owned port for fixed-text delivery from a supervised plugin job.

    The plugin obtains this port via ``registrar.get_notification_port()``.
    The port does NOT expose the QQ gateway, desktop runtime, or any other
    concrete channel implementation.  The host decides the actual delivery path
    based on channel, recipient_id, and the active instance's channel bindings.
    """

    async def send(self, intent: NotificationIntent) -> NotificationResult:
        """Send one notification intent; never raises, always returns a result."""
        ...


# ---------------------------------------------------------------------------
# Contextual Agent contracts
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PluginInvocationContext(InvocationContext):
    """Capability context enriched with a host-issued opaque conversation ref."""

    conversation_ref: str = ""
    global_scope: bool = False
    character_pack_id: str = ""
    # Host-resolved actor principal. A shared group profile is not its sender.
    # Empty on older hosts and unbound work; plugins must not infer authority
    # from tool arguments or from the contents of a conversation reference.
    authorization_profile_user_id: str = ""


# ---------------------------------------------------------------------------
# Execution hook contracts
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PluginToolCallSnapshot:
    """Immutable, provider-neutral view of one tool request.

    ``arguments_json`` contains the complete non-secret tool arguments as
    canonical JSON. Credential values are represented as configured;
    provider-private ``_tool_*`` sidecars and the legacy ``type`` field are not
    included. The string form keeps nested input immutable without handing a
    plugin a live Engine-owned dictionary.
    """

    invocation_id: str
    tool_name: str
    source: str
    profile_user_id: str
    session_id: str
    character_pack_id: str
    arguments_json: str


@dataclass(frozen=True, slots=True)
class PluginToolResultSnapshot:
    """Immutable public outcome of one completed tool request."""

    invocation_id: str
    tool_name: str
    status: str
    duration_ms: float
    reason: str = ""
    model_feedback: str = ""
    event_types: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PluginOutboundPlanSnapshot:
    """Immutable, locator-free view of one user-visible outbound action."""

    delivery_id: str
    channel: str
    action: str
    conversation_kind: str
    target_id: str
    segment_types: tuple[str, ...]
    text: str = ""
    reply_to_message_id: str = ""
    text_decoratable: bool = False


@dataclass(frozen=True, slots=True)
class PluginDeliverySnapshot:
    """Immutable public outcome of one user-visible delivery attempt."""

    delivery_id: str
    channel: str
    action: str
    conversation_kind: str
    target_id: str
    segment_types: tuple[str, ...]
    status: str
    duration_ms: float
    reason: str = ""
    message_id: str = ""


@dataclass(frozen=True, slots=True)
class PluginOutboundDecoration:
    """The intentionally small mutable surface of ``before_outbound_plan``.

    It can add visible text around text that already exists. It cannot replace
    message content, target, reply relationship, media locator or action.
    """

    text_prefix: str = ""
    text_suffix: str = ""


@dataclass(frozen=True, slots=True)
class PluginHookEnvelope:
    """One immutable lifecycle observation delivered to a plugin Hook."""

    hook_id: str
    hook_type: str
    occurred_at: int
    subject: str
    payload: (
        PluginToolCallSnapshot
        | PluginToolResultSnapshot
        | PluginOutboundPlanSnapshot
        | PluginDeliverySnapshot
    )


@dataclass(frozen=True, slots=True)
class PluginHookResult:
    """Typed Hook outcome.

    ``diagnostics`` contains stable machine-readable codes for host status and
    debugging. Only ``before_outbound_plan`` accepts ``outbound_decoration``;
    every other Hook remains observational.
    """

    diagnostics: tuple[str, ...] = ()
    outbound_decoration: PluginOutboundDecoration | None = None


class PluginHookHandler(Protocol):
    """Observe one exact execution Hook registered during activation."""

    async def handle_hook(self, hook: PluginHookEnvelope) -> PluginHookResult:
        """Return typed diagnostics; exceptions are isolated by PluginHost."""
        ...


# ---------------------------------------------------------------------------
# QQ command contracts
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PluginQQCommandRequest:
    """Inbound QQ command dispatched to a plugin handler.

    command:
        The raw command token as seen by the broker (e.g. "/balance").
    args:
        Everything after the command token, stripped.
    qq_number:
        Sender QQ number; 0 when unavailable.
    group_id:
        Group ID; 0 for private messages.
    is_group:
        True for group messages, False for private.
    sender_role:
        Host-normalized group role: "owner", "admin", "member", or empty
        when unavailable/not a group message. Plugins must fail closed when a
        group mutation requires administrator authority and this field is not
        owner/admin.
    idempotency_key:
        Per-delivery deduplication key set by the broker.
    """

    command: str
    args: str
    qq_number: int
    group_id: int
    is_group: bool
    idempotency_key: str = ""
    sender_role: str = ""
    profile_user_id: str = ""
    session_id: str = ""
    character_pack_id: str = ""
    conversation_ref: str = ""


@dataclass(frozen=True, slots=True)
class PluginQQCommandResult:
    """Result returned by a plugin command handler.

    handled:
        True if the plugin consumed this command (even on error).
        False to pass through to the next handler / LLM turn.
    reply_text:
        Optional reply text; empty string means no reply.
    reason:
        Machine-readable reason code for logging; empty on success.
    """

    handled: bool
    reply_text: str = ""
    reason: str = ""


class PluginQQCommandHandler(Protocol):
    """A single QQ command handler contributed by a plugin.

    Registered via ``registrar.add_qq_command(command, handler)`` during
    ``plugin.register(registrar)``.  The host dispatches matching commands
    to ``handle()`` inside the broker's ``dispatch()`` call.
    """

    async def handle(self, request: PluginQQCommandRequest) -> PluginQQCommandResult:
        """Handle one QQ command; must not raise."""
        ...


class PluginRegistrar(Protocol):
    def add_capability_adapter(self, adapter: CapabilityAdapter) -> None: ...

    def get_events_port(self) -> Any:
        """Capture scoped event operations; requires event.emit or event.subscribe."""
        ...

    def get_task_port(self) -> Any:
        """Capture the host-owned Task lifecycle port for the current plugin."""
        ...

    def add_event_subscription(self, subscription: Any, handler: Any) -> None:
        """Register an EventSubscription and its async handle_event(event, scope_id)."""
        ...

    def get_capability_port(self) -> PluginCapabilityPort:
        """Capture the invocation-scoped dependency port; requires capability.invoke."""
        ...

    def get_connection_port(self) -> PluginConnectionPort:
        """Requires the explicit read permission for a named connection."""
        ...

    def get_resource_port(self) -> PluginResourcePort:
        """Capture the current-invocation resource port; requires resource.read.

        Call open(target) while a capability runs. Background services and
        completed invocations have no implicit conversation resource access.
        """
        ...

    def add_skill(self, skill_root: Path) -> None:
        """Register one read-only Skill package shipped by this plugin.

        ``skill_root`` is the directory containing ``SKILL.md`` and any
        referenced scripts/resources.  The plugin must declare
        ``skill.contribute``.  The host validates the package during plugin
        activation and publishes it through Akane's existing ``load_skill``
        registry; this does not create a second Skill system or copy files into
        the user's managed Skill directory.
        """
        ...

    def add_prompt_block(self, block_id: str, text: str) -> None:
        """Register one stable system-prompt contribution at startup.

        The plugin must declare ``prompt.system.contribute``.  Blocks are
        bounded, restart-only contributions: the host validates and publishes
        them transactionally with the plugin's capability adapters.
        """
        ...

    def get_storage_dir(self) -> Path:
        """Return the plugin's host-owned scoped data directory.

        The directory is guaranteed to exist when returned.  The plugin must
        declare ``storage.write`` permission in its manifest; calling this
        method without that permission raises RuntimeError at activation time.
        """
        ...

    def add_background_service(
        self,
        service_id: str,
        service: "PluginBackgroundJob",
    ) -> None:
        """Register one independently supervised named background service.

        ``service_id`` is stable within the plugin and is used for lifecycle
        status and diagnostics.  A plugin may register any number of services;
        duplicate or malformed ids fail plugin activation.  Every service has
        its own controller and task, so one service failing does not stop its
        siblings.  The plugin must declare ``job.run`` permission.
        """
        ...

    def get_notification_port(self) -> "NotificationPort":
        """Return the host-owned port for fixed-text delivery.

        The plugin must declare ``notification.send`` permission; calling this
        method without that permission raises RuntimeError at activation time.
        This port does not run the model or render a character response.
        """
        ...

    def add_qq_command(self, command: str, handler: "PluginQQCommandHandler") -> None:
        """Register one exact-match QQ slash command.

        The plugin must declare ``qq.command.register``.  Commands are bounded,
        restart-only contributions; duplicates within or across active plugins
        fail that plugin's activation instead of silently choosing a winner.
        """
        ...

    def add_hook_handler(self, hook_type: str, handler: "PluginHookHandler") -> None:
        """Register one exact execution lifecycle Hook.

        The plugin must declare ``hook.subscribe``. Executable Hook types are
        ``before_tool_call``, ``after_tool_call``, ``before_outbound_plan`` and
        ``after_delivery``. Only the outbound-plan Hook may return the narrow
        text decoration contract.
        """
        ...


class AkanePlugin(Protocol):
    manifest: PluginManifest

    def register(self, registrar: PluginRegistrar) -> None: ...


__all__ = [
    "AKANE_PLUGIN_API_VERSION",
    "AKANE_PLUGIN_ENTRYPOINT_GROUP",
    "BACKGROUND_JOB_PERMISSION",
    "CAPABILITY_PROMPT_INVOKE_PERMISSION",
    "DIAGNOSTICS_INVOKE_PERMISSION",
    "DIRECT_CONVERSATION_EVENT",
    "EVENT_SUBSCRIBE_PERMISSION",
    "EVENT_EMIT_PERMISSION",
    "CONTEXT_OBSERVE_PERMISSION",
    "AGENT_TURN_REQUEST_PERMISSION",
    "HOOK_SUBSCRIBE_PERMISSION",
    "GROUP_CONVERSATION_EVENT",
    "POKE_CONVERSATION_EVENT",
    "BEFORE_TOOL_CALL_HOOK",
    "AFTER_TOOL_CALL_HOOK",
    "BEFORE_OUTBOUND_PLAN_HOOK",
    "AFTER_DELIVERY_HOOK",
    "MANAGED_ARTIFACT_WRITE_PERMISSION",
    "MAX_MANAGED_ARTIFACT_BYTES",
    "NETWORK_READ_PERMISSION",
    "NOTIFICATION_SEND_PERMISSION",
    "PLUGIN_QQ_COMMAND_PERMISSION",
    "PLUGIN_STORAGE_WRITE_PERMISSION",
    "PLUGIN_STATE_EFFECT",
    "SKILL_CONTRIBUTION_PERMISSION",
    "SYSTEM_PROMPT_CONTRIBUTION_PERMISSION",
    "AkanePlugin",
    "ManagedArtifactDraft",
    "ManagedArtifactPayload",
    "RESOURCE_READ_PERMISSION",
    "PluginResourcePort",
    "PluginResourceResult",
    "CAPABILITY_INVOKE_PERMISSION",
    "PluginCapabilityPort",
    "IMAGE_CONNECTION_READ_PERMISSION",
    "RVC_CONNECTION_READ_PERMISSION",
    "PluginConnectionPort",
    "PluginConnectionResult",
    "NotificationIntent",
    "NotificationPort",
    "NotificationResult",
    "PluginBackgroundJob",
    "PluginInvocationContext",
    "PluginHookEnvelope",
    "PluginHookHandler",
    "PluginHookResult",
    "PluginOutboundDecoration",
    "PluginOutboundPlanSnapshot",
    "PluginDeliverySnapshot",
    "PluginJobController",
    "PluginManifest",
    "ServiceDependency",
    "PluginQQCommandHandler",
    "PluginQQCommandRequest",
    "PluginQQCommandResult",
    "PluginRegistrar",
    "PluginResultExperience",
    "PluginResultPayload",
    "PluginToolCallSnapshot",
    "PluginToolResultSnapshot",
    "is_valid_capability_id",
    "is_valid_permission_id",
    "is_valid_plugin_id",
]
