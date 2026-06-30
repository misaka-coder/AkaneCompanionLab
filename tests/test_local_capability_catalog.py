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
        self.assertTrue(high["requiresConfirmation"])
        self.assertEqual(high["approvalMode"], "ask_each_time")
        self.assertEqual(medium["risk"], "medium")
        self.assertFalse(medium["requiresConfirmation"])
        self.assertEqual(medium["approvalMode"], "trusted_auto_allow")


if __name__ == "__main__":
    unittest.main()
