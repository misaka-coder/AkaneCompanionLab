from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
from companion_v01 import settings_catalog as sc
from companion_v01.routes.control_center import build_control_center_router


class SettingsCatalogDriftTests(unittest.TestCase):
    """The drift guard: this is what makes a new feature's switch auto-surface.
    Add a Settings field without a catalog entry and this test goes red."""

    def test_every_settings_field_is_catalogued_or_excluded(self) -> None:
        fields = set(config.Settings.model_fields.keys())
        missing = fields - sc.declared_keys() - sc.EXCLUDED_KEYS
        self.assertEqual(missing, set(), f"new Settings fields not in catalog: {sorted(missing)}")

    def test_catalog_has_no_stale_keys(self) -> None:
        fields = set(config.Settings.model_fields.keys())
        stale = sc.declared_keys() - fields
        self.assertEqual(stale, set(), f"catalog declares fields that no longer exist: {sorted(stale)}")

    def test_scopes_are_valid(self) -> None:
        catalog = sc.build_settings_catalog()
        for group in catalog["categories"]:
            for entry in group["settings"]:
                self.assertIn(entry["scope"], sc.VALID_SCOPES, entry["key"])

    def test_categories_carry_valid_tier_with_both_present(self) -> None:
        catalog = sc.build_settings_catalog()
        tiers = {group["tier"] for group in catalog["categories"]}
        self.assertTrue(tiers.issubset(sc.VALID_TIERS), tiers)
        self.assertIn(sc.TIER_COMMON, tiers)
        self.assertIn(sc.TIER_ADVANCED, tiers)
        # 1b feedback: the experimental multi-access group folds into advanced,
        # while QQ (the user relies on it) stays common.
        by_cat = {group["category"]: group["tier"] for group in catalog["categories"]}
        pub = next(name for name in by_cat if "公开访问" in name)
        self.assertEqual(by_cat[pub], sc.TIER_ADVANCED)
        qq = next(name for name in by_cat if name.startswith("QQ"))
        self.assertEqual(by_cat[qq], sc.TIER_COMMON)

    def test_sensitive_entries_never_expose_value_or_default(self) -> None:
        catalog = sc.build_settings_catalog()
        sensitive = [e for g in catalog["categories"] for e in g["settings"] if e["sensitive"]]
        self.assertTrue(sensitive, "expected API keys / cookie fields to be flagged sensitive")
        for entry in sensitive:
            self.assertNotIn("current", entry)
            self.assertNotIn("default", entry)
            self.assertIn("isSet", entry)

    def test_managed_entries_are_read_only_and_named(self) -> None:
        catalog = sc.build_settings_catalog()
        entries = {
            e["key"]: e
            for group in catalog["categories"]
            for e in group["settings"]
        }
        self.assertEqual(entries["STREAMING_TTS_ENABLED"]["managedIn"], sc.MANAGED_CAPABILITIES)
        self.assertFalse(entries["STREAMING_TTS_ENABLED"]["editable"])
        self.assertEqual(entries["VISION_BASE_URL"]["managedIn"], sc.MANAGED_MODEL_SERVICE)
        self.assertFalse(entries["VISION_BASE_URL"]["editable"])


class SettingsCatalogEndpointTests(unittest.TestCase):
    def _client(self) -> TestClient:
        app = FastAPI()
        app.include_router(build_control_center_router())
        return TestClient(app)

    def test_endpoint_returns_catalog(self) -> None:
        resp = self._client().get("/control-center/settings-catalog")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["status"], "available")
        self.assertTrue(body["categories"])
        self.assertIn("scopeLegend", body)

    def test_endpoint_does_not_leak_secret_values(self) -> None:
        original = config.TEXT_API_KEY
        try:
            config.TEXT_API_KEY = "fake-key-leaktest-DEADBEEF-must-not-appear"
            resp = self._client().get("/control-center/settings-catalog")
            self.assertEqual(resp.status_code, 200)
            self.assertNotIn("fake-key-leaktest-DEADBEEF-must-not-appear", resp.text)
        finally:
            config.TEXT_API_KEY = original


if __name__ == "__main__":
    unittest.main()
