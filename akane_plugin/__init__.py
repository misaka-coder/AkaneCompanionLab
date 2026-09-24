"""Public SDK for Akane plugins. Importable without an Akane host installation."""

from capcore import CapabilityDescriptor, CapabilityIOSlot, CapabilityResult, HealthStatus, InvocationContext

from .contracts import *  # noqa: F403
from .contracts import __all__ as _contracts_all
from .tools import Plugin, ToolCallError, ToolContext, Tools, Service, Services, ServiceContext, BackgroundContext
from .service_contracts import SERVICE_PROVIDE_PERMISSION
from .connections import ConnectionSpec, Connections, ConnectionError
from .results import Result
from .processes import PluginProcessRunner, drain
from .observations import ObservationReceipt
from .turns import TurnReceipt
from .tasks import TaskContext, TaskHandle, TaskReceipt
from .events import Event, EventInput, EventSubscription, EventDelivery, EventReceipt, EventBinding, Events, EventContext
from .timeline import TimelineContext, TimelineReceipt

__version__ = "0.14.0"

__all__ = [
    *_contracts_all,
    "CapabilityDescriptor", "CapabilityIOSlot", "CapabilityResult", "HealthStatus", "InvocationContext",
    "Service", "Services", "ServiceContext", "SERVICE_PROVIDE_PERMISSION",
    "ConnectionSpec", "Connections", "ConnectionError",
    "Plugin", "ToolCallError", "ToolContext", "Tools", "Result", "BackgroundContext",
    "PluginProcessRunner", "drain",
    "ObservationReceipt", "TurnReceipt", "TimelineContext", "TimelineReceipt",
    "TaskContext", "TaskHandle", "TaskReceipt",
    "Event", "EventInput", "EventSubscription", "EventDelivery", "EventReceipt", "EventBinding", "Events", "EventContext",
]
