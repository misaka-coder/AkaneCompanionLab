"""Single-plugin activation orchestration and staged adapter cleanup."""

from __future__ import annotations

from typing import Any
import asyncio

from ..instance_profile import PluginSelection

from .capabilities import collect_capabilities
from .cleanup import complete_cleanup
from .constants import _is_safe_version, _public_plugin_id
from .loading import audited_entry_point, load_plugin
from .models import PluginStatus, _ActivationFailure, _ActivePlugin, _CapabilityRegistration
from .ports import bind_registrar_ports
from .registrar import _StagedRegistrar
from .registration import accepted_plugin, register_contributions, validate_contributions


class PluginActivationMixin:
    async def _activate_plugin(
        self,
        selection: PluginSelection,
        entry_points: list[Any],
        *,
        reserved_capability_ids: frozenset[str],
        reserved_qq_commands: frozenset[str],
        reserved_skill_names: frozenset[str],
        check_health: bool = True,
    ) -> tuple[PluginStatus, _ActivePlugin | None, tuple[_CapabilityRegistration, ...]]:
        registrar = _StagedRegistrar()
        artifact_version = ""
        try:
            entry_point, artifact_version = audited_entry_point(selection, entry_points)
            plugin, manifest = load_plugin(
                entry_point, expected_plugin_id=selection.plugin_id,
                artifact_version=artifact_version, contribution_policy=self._contribution_policy,
            )
            bind_registrar_ports(self, registrar, plugin_id=selection.plugin_id, manifest=manifest)
            register_contributions(
                plugin, registrar, plugin_id=selection.plugin_id,
                reserved_qq_commands=reserved_qq_commands, reserved_skill_names=reserved_skill_names,
            )
            registrations = await collect_capabilities(
                self, registrar, plugin_id=selection.plugin_id, manifest=manifest,
                reserved_capability_ids=reserved_capability_ids, check_health=check_health,
            )
            validate_contributions(self._contribution_policy, manifest, registrar, registrations)
            return accepted_plugin(plugin, manifest, registrar, registrations, plugin_id=selection.plugin_id)
        except asyncio.CancelledError:
            registrar.seal()
            await complete_cleanup(self._close_adapters(reversed(registrar.adapters)))
            raise
        except _ActivationFailure as exc:
            registrar.seal()
            await complete_cleanup(self._close_adapters(reversed(registrar.adapters)))
            return (
                PluginStatus(
                    plugin_id=_public_plugin_id(selection.plugin_id),
                    enabled=True,
                    status=exc.status,
                    reason=exc.reason,
                    plugin_version=artifact_version if _is_safe_version(artifact_version) else "",
                    stage=exc.stage,
                    schema_errors=exc.schema_errors,
                ),
                None,
                (),
            )
        except Exception:
            registrar.seal()
            await complete_cleanup(self._close_adapters(reversed(registrar.adapters)))
            return (
                PluginStatus(
                    plugin_id=_public_plugin_id(selection.plugin_id),
                    enabled=True,
                    status="failed",
                    reason="plugin_activation_failed",
                ),
                None,
                (),
            )
