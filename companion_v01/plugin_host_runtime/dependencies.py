"""Service dependency readiness and withdrawal before publication."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .cleanup import complete_cleanup
from .contributions import _build_contribution_snapshots
from .models import _ActivationFailure
from .startup import StartupPlan


async def resolve_startup_dependencies(host: Any, plan: StartupPlan) -> None:
    if any(active.requires_services for active in plan.plugins.values()):
        from ..plugin_service_dependencies import resolve_dependencies
        def dependency_plan():
            return resolve_dependencies({item.plugin_id: item.as_dict() for item in _build_contribution_snapshots(
                plan.plugins, plan.capabilities, generation=host._generation + 1)})
        async def withdraw(plugin_id, status, reason):
            active = plan.plugins.pop(plugin_id)
            for capability_id in active.capability_ids:
                plan.capabilities.pop(capability_id, None)
            await complete_cleanup(host._close_adapters(reversed(active.adapters)))
            for index, item in enumerate(plan.statuses):
                if item.plugin_id == plugin_id:
                    plan.statuses[index] = replace(item, status=status, reason=reason)
        dependency_order = dependency_plan()
        for plugin_id in dependency_order.activation_order:
            active = plan.plugins[plugin_id]
            if not active.requires_services or plugin_id in dependency_plan().waiting:
                continue
            try:
                for adapter in active.adapters:
                    await host._check_adapter_health(adapter)
            except _ActivationFailure as exc:
                await withdraw(plugin_id, "failed", exc.reason)
        for plugin_id, errors in dependency_plan().waiting.items():
            await withdraw(plugin_id, "waiting_dependency", errors[0]["reason"])
        plan.adapters = [adapter for active in plan.plugins.values() for adapter in active.adapters]
