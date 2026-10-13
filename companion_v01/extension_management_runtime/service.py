"""One management service object, shared runtime references and operation lock."""

from __future__ import annotations

import asyncio
from typing import Any
from ..plugin_installation import ManagedPluginArtifactStore
from .contracts import PluginManagementRuntime
from .selections import PluginSelectionStore
from .inventory import ManagementInventory
from .lifecycle import ManagementLifecycle
from .installation import ManagementInstallation
from .operations import ManagementOperations


class ExtensionManagementService(
    ManagementInventory, ManagementLifecycle, ManagementInstallation, ManagementOperations
):
    """Coordinate persisted selection and the one live plugin runtime."""

    def __init__(
        self,
        *,
        plugin_runtime: PluginManagementRuntime,
        selection_store: PluginSelectionStore,
        artifact_store: ManagedPluginArtifactStore | None = None,
        market: Any = None,
        sync_timeout_seconds: float | None = None,
        connection_settings: Any = None,
    ) -> None:
        self.plugin_runtime = plugin_runtime
        self.selection_store = selection_store
        self.artifact_store = artifact_store
        self.market = market
        self.connection_settings = connection_settings
        self.sync_timeout_seconds = None if sync_timeout_seconds is None else max(1.0, float(sync_timeout_seconds))
        self._operation_lock = asyncio.Lock()
