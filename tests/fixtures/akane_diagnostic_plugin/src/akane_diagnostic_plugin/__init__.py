"""Installed-wheel-only diagnostic plugin fixture for M65-C acceptance."""

from __future__ import annotations

from capcore_adapter_python import PythonCapabilityAdapter, PythonCapabilitySpec

from companion_v01.plugin_api import (
    AKANE_PLUGIN_API_VERSION,
    DIAGNOSTICS_INVOKE_PERMISSION,
    PluginManifest,
)


PLUGIN_ID = "akane.test.diagnostic"
CAPABILITY_ID = f"{PLUGIN_ID}.ping.v1"


def _diagnostic_ping() -> dict[str, object]:
    return {
        "plugin_id": PLUGIN_ID,
        "diagnostic": "ready",
        "plugin_api_version": AKANE_PLUGIN_API_VERSION,
    }


class DiagnosticPlugin:
    manifest = PluginManifest(
        plugin_id=PLUGIN_ID,
        plugin_version="0.1.0",
        plugin_api_version=AKANE_PLUGIN_API_VERSION,
        permissions=(DIAGNOSTICS_INVOKE_PERMISSION,),
    )

    def register(self, registrar: object) -> None:
        spec = PythonCapabilitySpec.from_callable(
            _diagnostic_ping,
            capability_id=CAPABILITY_ID,
            display_name="Diagnostic Ping",
            short_hint="Return a side-effect-free plugin readiness result.",
            visible_in=("diagnostics",),
            prompt_exposed=False,
            risk="low",
            confirm="never",
            effects=(),
            inputs=(),
        )
        adapter = PythonCapabilityAdapter(
            provider_id="provider.akane.test.diagnostic",
            capabilities=(spec,),
        )
        registrar.add_capability_adapter(adapter)  # type: ignore[attr-defined]


def create_plugin() -> DiagnosticPlugin:
    return DiagnosticPlugin()


__all__ = ["create_plugin"]
