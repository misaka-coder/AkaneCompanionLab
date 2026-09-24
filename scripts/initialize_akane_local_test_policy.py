from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from companion_v01.local_capability_config import (
    APPROVAL_MODE_TRUSTED_AUTO_ALLOW,
    get_approval_policy_config,
    save_capability_approval_modes,
)


def initialize_local_test_policy(*, users_data_root: Path, profile_user_id: str = "master") -> tuple[str, str]:
    """Give a new loopback-only test profile full access without overriding later choices."""

    current = get_approval_policy_config(
        base_dir=users_data_root,
        profile_user_id=profile_user_id,
    )
    config_status = str(current.get("configStatus") or "")
    policy = current.get("approvalPolicy") or {}
    family_modes = {
        str(item.get("id") or ""): str(item.get("mode") or "")
        for item in policy.get("families") or []
        if isinstance(item, dict)
    }
    current_mode = (
        family_modes.get("ops", "ask_each_time")
        if family_modes.get("ops") == family_modes.get("extensions")
        else "mixed"
    )

    if config_status == "invalid_config":
        raise RuntimeError("local_test_approval_policy_invalid")
    if config_status != "missing":
        return "preserved", current_mode

    saved = save_capability_approval_modes(
        base_dir=users_data_root,
        profile_user_id=profile_user_id,
        modes={
            "ops": APPROVAL_MODE_TRUSTED_AUTO_ALLOW,
            "extensions": APPROVAL_MODE_TRUSTED_AUTO_ALLOW,
        },
    )
    if not saved.get("ok"):
        raise RuntimeError(str(saved.get("reason") or "local_test_approval_policy_save_failed"))
    return "initialized", APPROVAL_MODE_TRUSTED_AUTO_ALLOW


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--users-data-root", required=True, type=Path)
    parser.add_argument("--profile-user-id", default="master")
    args = parser.parse_args()
    status, mode = initialize_local_test_policy(
        users_data_root=args.users_data_root,
        profile_user_id=args.profile_user_id,
    )
    print(f"{status}:{mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
