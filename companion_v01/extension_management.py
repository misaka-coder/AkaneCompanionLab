"""Public API for instance selection and installed extension management.

Implementation lives in ``extension_management_runtime``.
"""

from .extension_management_runtime.contracts import (
    PLUGIN_SELECTION_STATE_SCHEMA_VERSION,
    PLUGIN_SELECTION_STATE_FILENAME,
    PluginManagementRuntime,
)
from .extension_management_runtime.selections import PluginSelectionStore
from .extension_management_runtime.service import ExtensionManagementService

__all__ = [
    "ExtensionManagementService",
    "PLUGIN_SELECTION_STATE_FILENAME",
    "PLUGIN_SELECTION_STATE_SCHEMA_VERSION",
    "PluginManagementRuntime",
    "PluginSelectionStore",
]
