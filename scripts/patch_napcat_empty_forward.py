"""Apply or restore the tested NapCat v4.18.28 empty-forward workaround.

NapCat sometimes returns an empty parsed node list for a QQ-native merged
forward even though the same message's raw resId still resolves its nodes.
This patch retries the resource-ID path only after the message-ID path is empty.
The exact source match keeps updates to other NapCat versions fail-closed.
"""

from __future__ import annotations

import argparse
import os
import shutil
import tempfile
from pathlib import Path


ORIGINAL = """        if (l && Cw(l)) {
          const d = l.data.content;
          if (d)
            return { messages: d };
        }"""

PATCHED = """        if (l && Cw(l)) {
          const d = l.data.content;
          if (Array.isArray(d) && d.length > 0)
            return { messages: d };
          // Akane workaround: a QQ-native forward can parse to an empty list
          // even when its raw resource ID still yields the real nodes.
          const resId = c.elements?.find((part) => part.multiForwardMsgElement?.resId)?.multiForwardMsgElement?.resId;
          if (resId) {
            try {
              const fetched = await i(resId);
              if (fetched.messages?.length > 0)
                return fetched;
            } catch (error) {
              this.core.context.logger.logError("GetForwardMsg resource fallback failed", error);
            }
          }
          if (d)
            return { messages: d };
        }"""


def _backup_path(bundle: Path) -> Path:
    return bundle.with_name(bundle.name + ".akane-forward-original.bak")


def _replace_bytes(bundle: Path, payload: bytes) -> None:
    with tempfile.NamedTemporaryFile(dir=bundle.parent, suffix=".tmp", delete=False) as file:
        temporary = Path(file.name)
        file.write(payload)
    try:
        os.replace(temporary, bundle)
    finally:
        temporary.unlink(missing_ok=True)


def run(bundle: Path, action: str) -> str:
    bundle = bundle.resolve(strict=True)
    if bundle.name != "napcat.mjs" or not bundle.is_file():
        raise ValueError("expected_napcat_mjs_file")
    backup = _backup_path(bundle)
    content = bundle.read_text(encoding="utf-8")
    patched_count = content.count(PATCHED)
    original_count = content.count(ORIGINAL)

    if action == "check":
        if patched_count != 1 or not backup.is_file():
            raise ValueError("workaround_or_backup_missing")
        if backup.read_text(encoding="utf-8").count(ORIGINAL) != 1:
            raise ValueError("original_backup_invalid")
        return "patched"

    if action == "apply":
        if patched_count == 1:
            return run(bundle, "check")
        if original_count != 1:
            raise ValueError("unsupported_napcat_bundle")
        if backup.exists() and backup.read_bytes() != bundle.read_bytes():
            raise ValueError("existing_backup_does_not_match_bundle")
        if not backup.exists():
            shutil.copy2(bundle, backup)
        _replace_bytes(bundle, content.replace(ORIGINAL, PATCHED, 1).encode("utf-8"))
        return "patched"

    if action == "restore":
        if patched_count != 1 or not backup.is_file():
            raise ValueError("workaround_or_backup_missing")
        original = backup.read_bytes()
        if original.decode("utf-8").count(ORIGINAL) != 1:
            raise ValueError("original_backup_invalid")
        _replace_bytes(bundle, original)
        return "restored"

    raise ValueError("unknown_action")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "apply", "restore"))
    parser.add_argument("bundle", type=Path, help="Path to the installed napcat.mjs")
    args = parser.parse_args()
    print(run(args.bundle, args.action))


if __name__ == "__main__":
    main()
