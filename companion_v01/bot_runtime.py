"""Public construction and lifecycle API for one Akane Bot.

Implementation lives in ``bot_runtime_components``.
"""

from .bot_runtime_components.runtime import BotRuntime, RuntimeMetrics, log_runtime_start_status
from .bot_runtime_components.factory import BotRuntimeFactory

__all__ = ["BotRuntime", "BotRuntimeFactory", "RuntimeMetrics", "log_runtime_start_status"]
