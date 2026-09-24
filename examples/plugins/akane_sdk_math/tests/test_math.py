import unittest

from akane_sdk_math import add, create_plugin


class MathTests(unittest.TestCase):
    def test_arithmetic_and_declaration(self):
        self.assertEqual(add(41), 42)
        self.assertEqual(add(-3, 4), 1)
        self.assertEqual(create_plugin().manifest.plugin_id, "example.math")
