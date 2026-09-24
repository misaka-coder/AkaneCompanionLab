"""Compatibility command for reproducible local provider-wire acceptance.

The retired G1 live loop is removed: it flattened tool results into plain text.
Use production clients with controlled transports and genuine MemCore pairs.
No credentials or external model endpoint are read by this command.
"""
from __future__ import annotations
import argparse
import os
import unittest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", default="work/tool-exposure-wire")
    args = parser.parse_args()
    os.environ["AKANE_EXPOSURE_WIRE_REPORT_DIR"] = args.report_dir
    suite = unittest.defaultTestLoader.loadTestsFromNames([
        "tests.test_tool_exposure_wire", "tests.test_tool_exposure_provider_wire",
        "tests.test_tool_exposure_development"])
    return 0 if unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
