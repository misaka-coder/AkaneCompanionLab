"""Operator-only recovery; never exposed as a model capability."""

import argparse

from .rvc import RvcWebUiProvider


def main():
    parser = argparse.ArgumentParser(description="Clear an RVC fence ONLY after restarting the dedicated WebUI.")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--state-dir")
    parser.add_argument("--confirm-restarted", action="store_true", required=True)
    args = parser.parse_args()
    provider = RvcWebUiProvider(base_url=args.base_url, state_dir=args.state_dir)
    provider.lease.acknowledge_restart(confirmed=args.confirm_restarted)
    print("rvc_fence_cleared_after_operator_confirmation")


if __name__ == "__main__":
    main()
