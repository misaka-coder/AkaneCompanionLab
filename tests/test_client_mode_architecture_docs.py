from __future__ import annotations

import unittest
from pathlib import Path

from companion_v01.capability_registry import CapabilityRegistry
from companion_v01.client_protocol import ClientMode, default_capabilities_for_mode
from companion_v01.mode_profiles import ModeProfileRegistry
from companion_v01.prompt_profiles import PromptProfileRegistry


ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = ROOT / "docs" / "client-mode-architecture.md"


class ClientModeArchitectureDocsTests(unittest.TestCase):
    def test_architecture_doc_names_current_extension_points(self) -> None:
        text = DOC_PATH.read_text(encoding="utf-8")

        for marker in [
            "ClientMode",
            "ModeProfileRegistry",
            "PromptProfileRegistry",
            "CapabilityRegistry",
            "OutputAdapterRegistry",
            "DesktopPetOutputAdapter",
            "SCENE_STATIC_SYSTEM_BLOCKS",
            "DESKTOP_PET_SYSTEM_BLOCKS",
            "QQ_TEXT_SYSTEM_BLOCKS",
            "qq_delivery",
            "desktop_workspace",
            "web_scene",
        ]:
            self.assertIn(marker, text)

        for mode in ClientMode:
            self.assertIn(f"`{mode.value}`", text)

    def test_registered_modes_have_prompt_and_tool_policy(self) -> None:
        mode_registry = ModeProfileRegistry()
        prompt_registry = PromptProfileRegistry()
        capability_registry = CapabilityRegistry()

        for requested_mode in ClientMode:
            context = mode_registry.resolve(
                requested_mode=requested_mode,
                capabilities=default_capabilities_for_mode(requested_mode),
            )
            prompt_profile = prompt_registry.resolve(context)
            tool_names = capability_registry.tool_names_for_mode(context.effective_mode)

            self.assertTrue(prompt_profile.system_block_ids, requested_mode.value)
            self.assertTrue(tool_names, requested_mode.value)


if __name__ == "__main__":
    unittest.main()
