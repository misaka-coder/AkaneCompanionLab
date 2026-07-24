from __future__ import annotations

from types import SimpleNamespace
import unittest

from companion_v01.local_capability_catalog import build_local_capability_catalog
from companion_v01.tool_runtime import ToolMetadata


class MetadataHandler:
    def __init__(self, metadata: ToolMetadata) -> None:
        self._metadata = metadata

    def tool_metadata(self) -> ToolMetadata:
        return self._metadata


class StatusHandler:
    def __init__(self, *, status: str, reason: str = "") -> None:
        self._status = status
        self._reason = reason

    def capability_status(self) -> dict[str, object]:
        return {
            "enabled": self._status == "ready",
            "status": self._status,
            "reason": self._reason,
        }


class LocalCapabilityCatalogTests(unittest.TestCase):
    def test_backend_tool_catalog_projects_handler_risk_through_capcore(self) -> None:
        catalog = build_local_capability_catalog(
            engine=SimpleNamespace(
                tool_handlers={
                    "custom_high": MetadataHandler(ToolMetadata(risk="high")),
                    "custom_medium": MetadataHandler(ToolMetadata(risk="medium", requires_confirmation=True)),
                }
            ),
            config_module=SimpleNamespace(),
        )

        by_id = {item["id"]: item for item in catalog["capabilities"]}
        high = by_id["tool.custom_high"]
        medium = by_id["tool.custom_medium"]

        self.assertEqual(high["risk"], "high")
        self.assertEqual(high["confirm"], "always")
        self.assertTrue(high["requiresConfirmation"])
        self.assertEqual(high["approvalMode"], "ask_each_time")
        self.assertEqual(medium["risk"], "medium")
        self.assertEqual(medium["confirm"], "never")
        self.assertFalse(medium["requiresConfirmation"])
        self.assertEqual(medium["approvalMode"], "trusted_auto_allow")

    def test_static_provider_catalog_projects_confirm_through_capcore(self) -> None:
        catalog = build_local_capability_catalog(
            engine=SimpleNamespace(tool_handlers={}),
            config_module=SimpleNamespace(),
        )

        by_id = {item["id"]: item for item in catalog["capabilities"]}
        text_only = by_id["provider.voice.text_only"]

        self.assertEqual(text_only["risk"], "low")
        self.assertEqual(text_only["confirm"], "never")
        self.assertFalse(text_only["requiresConfirmation"])
        self.assertEqual(text_only["approvalMode"], "trusted_auto_allow")

    def test_python_adapter_capabilities_are_cataloged_as_internal_python_tools(self) -> None:
        catalog = build_local_capability_catalog(
            engine=SimpleNamespace(tool_handlers={}),
            config_module=SimpleNamespace(),
        )

        by_id = {item["id"]: item for item in catalog["capabilities"]}
        normalize = by_id["python.akane.normalize_text"]

        self.assertEqual(normalize["kind"], "python_tool")
        self.assertEqual(normalize["source"], "python_adapter")
        self.assertEqual(normalize["adapter"], "python")
        self.assertEqual(normalize["risk"], "low")
        self.assertEqual(normalize["confirm"], "never")
        self.assertFalse(normalize["requiresConfirmation"])
        self.assertFalse(normalize["exposedToPrompt"])

    def test_prompt_module_is_unavailable_when_runtime_tool_is_not_registered(self) -> None:
        catalog = build_local_capability_catalog(
            engine=SimpleNamespace(tool_handlers={}),
            config_module=SimpleNamespace(),
        )

        by_id = {item["id"]: item for item in catalog["capabilities"]}
        image_generation = by_id["prompt_module.image_generation"]

        self.assertFalse(image_generation["enabled"])
        self.assertEqual(image_generation["status"], "unavailable")
        self.assertEqual(image_generation["reason"], "tool_not_registered:generate_image")

    def test_prompt_module_uses_runtime_tool_readiness_as_its_source_of_truth(self) -> None:
        unavailable_catalog = build_local_capability_catalog(
            engine=SimpleNamespace(
                tool_handlers={
                    "generate_image": StatusHandler(
                        status="unavailable",
                        reason="provider_endpoint_missing",
                    )
                }
            ),
            config_module=SimpleNamespace(),
        )
        ready_catalog = build_local_capability_catalog(
            engine=SimpleNamespace(tool_handlers={"generate_image": StatusHandler(status="ready")}),
            config_module=SimpleNamespace(),
        )

        unavailable = {
            item["id"]: item for item in unavailable_catalog["capabilities"]
        }["prompt_module.image_generation"]
        ready = {
            item["id"]: item for item in ready_catalog["capabilities"]
        }["prompt_module.image_generation"]

        self.assertFalse(unavailable["enabled"])
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertEqual(unavailable["reason"], "tool_not_ready:generate_image")
        self.assertTrue(ready["enabled"])
        self.assertEqual(ready["status"], "ready")
        self.assertNotIn("reason", ready)


if __name__ == "__main__":
    unittest.main()
