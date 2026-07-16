"""Public M65-C contract for trusted, in-process Akane plugins.

This module intentionally contains contracts only.  It does not discover,
load, activate, or retain plugins and must remain safe to import from an
installed plugin artifact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from capcore import CapabilityAdapter


AKANE_PLUGIN_API_VERSION = 1
AKANE_PLUGIN_ENTRYPOINT_GROUP = "akane.plugins.v1"
DIAGNOSTICS_INVOKE_PERMISSION = "diagnostics.invoke"
CAPABILITY_PROMPT_INVOKE_PERMISSION = "capability.prompt.invoke"
NETWORK_READ_PERMISSION = "network.read"
MANAGED_ARTIFACT_WRITE_PERMISSION = "artifact.write"
PLUGIN_STORAGE_WRITE_PERMISSION = "storage.write"
BACKGROUND_JOB_PERMISSION = "job.run"
NOTIFICATION_SEND_PERMISSION = "notification.send"
PLUGIN_QQ_COMMAND_PERMISSION = "qq.command.register"
MODEL_REASONING_PERMISSION = "model.reasoning"
MAX_MANAGED_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_PLUGIN_ID_LENGTH = 64
MAX_CAPABILITY_ID_LENGTH = 128
MAX_PERMISSION_ID_LENGTH = 64

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
class PluginManifest:
    plugin_id: str
    plugin_version: str
    plugin_api_version: int
    permissions: tuple[str, ...]


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
    """Path-free bytes proposed by a trusted plugin for host-owned storage."""

    data: bytes
    title: str
    output_format: str
    mime_type: str
    summary: str = ""
    send_to_user: bool = True


@dataclass(frozen=True, slots=True)
class ManagedArtifactPayload:
    """Capability content plus at most one artifact committed by PluginHost."""

    content: Any
    artifact: ManagedArtifactDraft


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
    """A supervised, restart-only background task contributed by a plugin.

    The plugin registers one job via ``registrar.add_background_job(job)``.
    The host wraps ``start(controller)`` in an asyncio task on the lifecycle
    loop after all capability adapters are activated.  The job runs until
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
    """A bounded proactive delivery request from a plugin.

    channel:
        Delivery channel identifier.  "qq_text" is the only built-in channel;
        others must be explicitly supported by the bound NotificationPort.
    recipient_id:
        Channel-specific recipient identifier.  For "qq_text":
            "group:<group_id>"   — QQ group message
            "user:<qq_number>"   — QQ private message
    text:
        The message text.  It is bounded by the host before delivery; text that
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
    """Host-owned port for proactive delivery from a supervised plugin job.

    The plugin obtains this port via ``registrar.get_notification_port()``.
    The port does NOT expose the QQ gateway, desktop runtime, or any other
    concrete channel implementation.  The host decides the actual delivery path
    based on channel, recipient_id, and the active instance's channel bindings.
    """

    async def send(self, intent: NotificationIntent) -> NotificationResult:
        """Send one notification intent; never raises, always returns a result."""
        ...


# ---------------------------------------------------------------------------
# Model reasoning port contracts
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PluginReasoningRequest:
    """Bounded proactive reasoning request using the host's normal model/tool loop."""

    trace_id: str
    profile_user_id: str
    session_id: str
    message: str
    extra_context: str = ""
    character_pack_id: str = ""
    timestamp: int = 0


@dataclass(frozen=True, slots=True)
class PluginReasoningResult:
    """Safe reasoning projection returned to a trusted plugin."""

    ok: bool
    status: str
    text: str = ""
    reason: str = ""
    evidence_events: tuple[dict[str, Any], ...] = ()


class PluginReasoningPort(Protocol):
    """Host-owned access to Akane's model and registered read-only tools."""

    async def analyze(self, request: PluginReasoningRequest) -> PluginReasoningResult:
        """Run one bounded proactive turn; never expose raw Engine state."""
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

    def get_storage_dir(self) -> Path:
        """Return the plugin's host-owned scoped data directory.

        The directory is guaranteed to exist when returned.  The plugin must
        declare ``storage.write`` permission in its manifest; calling this
        method without that permission raises RuntimeError at activation time.
        """
        ...

    def add_background_job(self, job: "PluginBackgroundJob") -> None:
        """Register one supervised background job.

        The host starts the job (as an asyncio task on the lifecycle loop) after
        all capabilities are activated, and stops it before closing adapters.
        The plugin must declare ``job.run`` permission; calling this method
        without that permission raises RuntimeError at activation time.
        At most one job per plugin is accepted by the host.
        """
        ...

    def get_notification_port(self) -> "NotificationPort":
        """Return the host-owned notification port for proactive delivery.

        The plugin must declare ``notification.send`` permission; calling this
        method without that permission raises RuntimeError at activation time.
        """
        ...

    def get_reasoning_port(self) -> "PluginReasoningPort":
        """Return the host-owned model/tool reasoning port.

        The plugin must declare ``model.reasoning``.  The port accepts bounded
        proactive requests and returns only user-facing text plus sanitized
        evidence metadata; it never exposes Engine, model credentials, or
        local paths.
        """
        ...

    def add_qq_command(self, command: str, handler: "PluginQQCommandHandler") -> None:
        """Register one exact-match QQ slash command.

        The plugin must declare ``qq.command.register``.  Commands are bounded,
        restart-only contributions; duplicates within or across active plugins
        fail that plugin's activation instead of silently choosing a winner.
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
    "MANAGED_ARTIFACT_WRITE_PERMISSION",
    "MAX_MANAGED_ARTIFACT_BYTES",
    "MODEL_REASONING_PERMISSION",
    "NETWORK_READ_PERMISSION",
    "NOTIFICATION_SEND_PERMISSION",
    "PLUGIN_QQ_COMMAND_PERMISSION",
    "PLUGIN_STORAGE_WRITE_PERMISSION",
    "AkanePlugin",
    "ManagedArtifactDraft",
    "ManagedArtifactPayload",
    "NotificationIntent",
    "NotificationPort",
    "NotificationResult",
    "PluginBackgroundJob",
    "PluginJobController",
    "PluginManifest",
    "PluginQQCommandHandler",
    "PluginQQCommandRequest",
    "PluginQQCommandResult",
    "PluginReasoningPort",
    "PluginReasoningRequest",
    "PluginReasoningResult",
    "PluginRegistrar",
    "PluginResultExperience",
    "PluginResultPayload",
    "is_valid_capability_id",
    "is_valid_permission_id",
    "is_valid_plugin_id",
]
