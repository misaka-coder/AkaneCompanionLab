"""Stable reverse callback router and worker port imports.

Implementation lives in plugin_callback_runtime.
"""

from __future__ import annotations

from dataclasses import replace as replace
import asyncio as asyncio
import threading as threading
import uuid as uuid
from collections.abc import Callable as Callable
from typing import Any as Any, Mapping as Mapping
from .plugin_api import (
    NotificationIntent as NotificationIntent,
    NotificationResult as NotificationResult,
    PluginResourceResult as PluginResourceResult,
    CAPABILITY_INVOKE_PERMISSION as CAPABILITY_INVOKE_PERMISSION,
    RESOURCE_READ_PERMISSION as RESOURCE_READ_PERMISSION,
    EVENT_EMIT_PERMISSION as EVENT_EMIT_PERMISSION,
    CONTEXT_OBSERVE_PERMISSION as CONTEXT_OBSERVE_PERMISSION,
    AGENT_TURN_REQUEST_PERMISSION as AGENT_TURN_REQUEST_PERMISSION,
    EVENT_SUBSCRIBE_PERMISSION as EVENT_SUBSCRIBE_PERMISSION,
)
from pathlib import Path as Path
from capcore import CapabilityResult as CapabilityResult, InvocationContext as InvocationContext
from .plugin_resources import (
    ResourceInvocation as ResourceInvocation,
    current_resource_invocation as current_resource_invocation,
    generation_request_id as generation_request_id,
)
from .plugin_generation_codec import (
    PluginGenerationCodecError as PluginGenerationCodecError,
    notification_intent_from_wire as notification_intent_from_wire,
    notification_intent_to_wire as notification_intent_to_wire,
    notification_result_from_wire as notification_result_from_wire,
    notification_result_to_wire as notification_result_to_wire,
    capability_result_to_wire as capability_result_to_wire,
    capability_result_from_wire as capability_result_from_wire,
)
from .plugin_result_projection import sanitize_capability_result as sanitize_capability_result
from .plugin_result_forwarding import (
    remember_dependency_result as remember_dependency_result,
    forwarded_dependency_result as forwarded_dependency_result,
)
from .plugin_subprocess import drain as drain
from .plugin_connections import (
    permitted_connections as permitted_connections,
    connection_result_to_wire as connection_result_to_wire,
    connection_result_from_wire as connection_result_from_wire,
    rejected as connection_rejected,
    resolve_connection_snapshot as resolve_connection_snapshot,
)
from .plugin_generation_protocol import PLUGIN_GENERATION_PROTOCOL as PLUGIN_GENERATION_PROTOCOL
from .plugin_events import event_rejection as event_rejection
from .plugin_tasks import task_rejection as task_rejection

from .plugin_callback_runtime.router import GenerationHostCallbackRouter as GenerationHostCallbackRouter
from .plugin_callback_runtime.worker_ports import (
    GenerationCapabilityProvider as GenerationCapabilityProvider,
    GenerationEventsProvider as GenerationEventsProvider,
    GenerationTaskProvider as GenerationTaskProvider,
    GenerationConnectionProvider as GenerationConnectionProvider,
    GenerationResourceProvider as GenerationResourceProvider,
    GenerationNotificationPort as GenerationNotificationPort,
)


__all__ = [
    "GenerationHostCallbackRouter",
    "GenerationNotificationPort",
]
