import unittest

from scripts.verify_plugin_sdk_docs import check_dependency_claims


class SdkDocDependencyTests(unittest.TestCase):
    def test_old_toml_dependency_is_detected_without_markdown_backticks(self):
        self.assertTrue(check_dependency_claims('dependencies = ["akane-plugin>=0.8,<0.9"]', "README"))

    def test_lower_bound_above_current_patch_is_incompatible(self):
        self.assertTrue(check_dependency_claims('akane-plugin>=0.14.99,<0.15', "README"))

    def test_compatible_range_in_prose_or_toml(self):
        for text in ('`akane-plugin>=0.14,<0.15`', 'dependencies = ["akane-plugin>=0.14.2,<1"]'):
            self.assertFalse(check_dependency_claims(text, "README"))
