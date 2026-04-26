from __future__ import annotations

import unittest


QUICK_TESTS = [
    "tests.test_resource_visibility_contract",
    "tests.test_desktop_activity_runtime_contract",
    "tests.test_attachment_inbox.AttachmentInboxTests.test_inspect_attachment_requests_confirmation_for_ambiguous_target",
    "tests.test_generated_files.GeneratedFileTests.test_send_file_requests_confirmation_for_ambiguous_generated_name",
    "tests.test_generated_files.GeneratedFileTests.test_send_file_keeps_generated_exact_send_when_attachment_target_is_ambiguous",
]


def load_tests(
    loader: unittest.TestLoader,
    tests: unittest.TestSuite,
    pattern: str | None,
) -> unittest.TestSuite:
    suite = unittest.TestSuite()
    for name in QUICK_TESTS:
        suite.addTests(loader.loadTestsFromName(name))
    return suite


if __name__ == "__main__":
    unittest.main()
