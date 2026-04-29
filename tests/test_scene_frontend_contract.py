from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class SceneFrontendContractTests(unittest.TestCase):
    def test_boot_fallback_uses_current_resource_tree(self) -> None:
        app_source = _read("web/app.js")
        index_source = _read("web/index.html")

        for source in (app_source, index_source):
            self.assertIn("/assets/scenes/街道/黄昏街道.png", source)
            self.assertIn("/assets/characters/猫娘/正常.png", source)
            self.assertNotIn("/assets/backgrounds/evening.png", source)
            self.assertNotIn("%E6%B0%B4%E6%89%8B%E6%9C%8D/normal.png", source)

    def test_scene_frontend_prefers_resource_manifest_over_hardcoded_visuals(self) -> None:
        source = _read("web/app.js")

        self.assertIn("fetchManifest", source)
        self.assertIn("buildManifestIndex", source)
        self.assertIn("resolveVisualState", source)
        self.assertIn("background?.path", source)
        self.assertIn("emotion?.path", source)

    def test_avatar_size_control_applies_to_static_and_live2d(self) -> None:
        app_source = _read("web/app.js")
        html_source = _read("web/index.html")
        css_source = _read("web/styles.css")

        self.assertIn("const DEFAULT_AVATAR_SCALE = 1.2", app_source)
        self.assertIn("角色大小", html_source)
        self.assertIn('max="180"', html_source)
        self.assertIn("--avatar-scale: 1.2", css_source)
        self.assertIn("transform: scale(var(--avatar-scale, 1.2))", css_source)
        self.assertIn('.scene[data-avatar-mode="live2d"] .sprite-frame', css_source)


if __name__ == "__main__":
    unittest.main()
