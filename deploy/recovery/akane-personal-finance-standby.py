#!/usr/bin/env python3
"""Atomically toggle the personal Bot's finance plugin in the Host manifest."""

from __future__ import annotations

import argparse
import copy
import os
import re
import shutil
import tempfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MANIFEST_PATH = Path("/var/lib/akane-host/bots.toml")
BACKUP_ROOT = Path("/var/lib/akane-host/deploy-backups")
PERSONAL_BOT_IDS = frozenset({"personal", "akane-personal"})
FINANCE_PLUGIN_ID = "akane.finance"
BOT_TABLE_RE = re.compile(r"^\s*\[\[bots\]\]\s*(?:#.*)?$")
PLUGIN_TABLE_RE = re.compile(r"^\s*\[\[bots\.plugins\]\]\s*(?:#.*)?$")
TABLE_HEADER_RE = re.compile(r"^\s*\[")
BOT_ID_RE = re.compile(r"""^\s*bot_id\s*=\s*(['"])([^'"]+)\1\s*(?:#.*)?$""")
PLUGIN_ID_RE = re.compile(r"""^\s*id\s*=\s*(['"])([^'"]+)\1\s*(?:#.*)?$""")
ENABLED_RE = re.compile(r"^(\s*enabled\s*=\s*)(?:true|false)(\s*(?:#.*)?)$")


class ManifestError(RuntimeError):
    pass


def _load_manifest(text: str) -> dict[str, Any]:
    try:
        parsed = tomllib.loads(text)
    except Exception as exc:
        raise ManifestError("manifest_toml_invalid") from exc
    bots = parsed.get("bots")
    if not isinstance(bots, list):
        raise ManifestError("manifest_bots_missing")
    return parsed


def _personal_bot(parsed: dict[str, Any]) -> dict[str, Any]:
    matches = [
        bot
        for bot in parsed.get("bots", [])
        if isinstance(bot, dict) and str(bot.get("bot_id") or "").strip() in PERSONAL_BOT_IDS
    ]
    if len(matches) != 1:
        raise ManifestError("personal_bot_not_unique")
    return matches[0]


def finance_standby_enabled(text: str) -> bool:
    personal = _personal_bot(_load_manifest(text))
    plugins = personal.get("plugins")
    if plugins is None:
        return False
    if not isinstance(plugins, list):
        raise ManifestError("personal_plugins_invalid")
    matches = [
        plugin
        for plugin in plugins
        if isinstance(plugin, dict) and str(plugin.get("id") or "").strip() == FINANCE_PLUGIN_ID
    ]
    if len(matches) > 1:
        raise ManifestError("personal_finance_plugin_not_unique")
    return bool(matches and matches[0].get("enabled") is True)


def _expected_manifest(parsed: dict[str, Any], *, enabled: bool) -> dict[str, Any]:
    expected = copy.deepcopy(parsed)
    personal = _personal_bot(expected)
    plugins = personal.get("plugins")
    if plugins is None:
        plugins = []
        personal["plugins"] = plugins
    if not isinstance(plugins, list):
        raise ManifestError("personal_plugins_invalid")
    matches = [
        plugin
        for plugin in plugins
        if isinstance(plugin, dict) and str(plugin.get("id") or "").strip() == FINANCE_PLUGIN_ID
    ]
    if len(matches) > 1:
        raise ManifestError("personal_finance_plugin_not_unique")
    if matches:
        matches[0]["enabled"] = enabled
    else:
        plugins.append({"id": FINANCE_PLUGIN_ID, "enabled": enabled})
    return expected


def update_manifest_text(text: str, *, enabled: bool) -> str:
    parsed_before = _load_manifest(text)
    expected = _expected_manifest(parsed_before, enabled=enabled)
    if parsed_before == expected:
        return text

    newline = "\r\n" if "\r\n" in text else "\n"
    had_trailing_newline = text.endswith(("\n", "\r"))
    lines = text.splitlines()
    bot_starts = [index for index, line in enumerate(lines) if BOT_TABLE_RE.match(line)]
    personal_spans: list[tuple[int, int]] = []
    for position, start in enumerate(bot_starts):
        end = bot_starts[position + 1] if position + 1 < len(bot_starts) else len(lines)
        bot_ids = [
            match.group(2).strip()
            for line in lines[start + 1 : end]
            if (match := BOT_ID_RE.match(line))
        ]
        if len(bot_ids) == 1 and bot_ids[0] in PERSONAL_BOT_IDS:
            personal_spans.append((start, end))
    if len(personal_spans) != 1:
        raise ManifestError("personal_bot_table_not_unique")

    start, end = personal_spans[0]
    plugin_starts = [
        index for index in range(start + 1, end) if PLUGIN_TABLE_RE.match(lines[index])
    ]
    plugin_span: tuple[int, int] | None = None
    for position, plugin_start in enumerate(plugin_starts):
        plugin_end = next(
            (
                index
                for index in range(plugin_start + 1, end)
                if TABLE_HEADER_RE.match(lines[index])
            ),
            end,
        )
        plugin_ids = [
            match.group(2).strip()
            for line in lines[plugin_start + 1 : plugin_end]
            if (match := PLUGIN_ID_RE.match(line))
        ]
        if len(plugin_ids) == 1 and plugin_ids[0] == FINANCE_PLUGIN_ID:
            if plugin_span is not None:
                raise ManifestError("personal_finance_plugin_not_unique")
            plugin_span = (plugin_start, plugin_end)

    enabled_literal = "true" if enabled else "false"
    if plugin_span is None:
        insertion = end
        block = [
            "[[bots.plugins]]",
            f'id = "{FINANCE_PLUGIN_ID}"',
            f"enabled = {enabled_literal}",
        ]
        if insertion > 0 and lines[insertion - 1].strip():
            block.insert(0, "")
        if insertion < len(lines) and lines[insertion].strip():
            block.append("")
        lines[insertion:insertion] = block
    else:
        plugin_start, plugin_end = plugin_span
        enabled_lines = [
            index
            for index in range(plugin_start + 1, plugin_end)
            if ENABLED_RE.match(lines[index])
        ]
        if len(enabled_lines) > 1:
            raise ManifestError("personal_finance_enabled_not_unique")
        if enabled_lines:
            index = enabled_lines[0]
            match = ENABLED_RE.match(lines[index])
            if match is None:
                raise ManifestError("personal_finance_enabled_invalid")
            lines[index] = f"{match.group(1)}{enabled_literal}{match.group(2)}"
        else:
            lines.insert(plugin_end, f"enabled = {enabled_literal}")

    updated = newline.join(lines)
    if had_trailing_newline:
        updated += newline
    parsed_after = _load_manifest(updated)
    if parsed_after != expected:
        raise ManifestError("manifest_semantic_guard_failed")
    return updated


def _write_manifest(*, manifest_path: Path, enabled: bool) -> bool:
    current = manifest_path.read_text(encoding="utf-8")
    updated = update_manifest_text(current, enabled=enabled)
    if updated == current:
        return False

    stat = manifest_path.stat()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup_dir = BACKUP_ROOT / f"personal-finance-standby-{timestamp}"
    backup_dir.mkdir(parents=True, mode=0o700)
    shutil.copy2(manifest_path, backup_dir / "bots.toml")

    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=manifest_path.parent,
            prefix=".bots.toml.",
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, stat.st_mode & 0o7777)
        os.chown(temp_name, stat.st_uid, stat.st_gid)
        os.replace(temp_name, manifest_path)
        directory_fd = os.open(manifest_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("action", choices=("status", "enable", "disable"))
    args = parser.parse_args()
    try:
        changed = False
        if args.action == "enable":
            changed = _write_manifest(manifest_path=MANIFEST_PATH, enabled=True)
        elif args.action == "disable":
            changed = _write_manifest(manifest_path=MANIFEST_PATH, enabled=False)
        enabled = finance_standby_enabled(MANIFEST_PATH.read_text(encoding="utf-8"))
    except ManifestError as exc:
        print("AKANE_RESULT=MANIFEST_INVALID")
        print(f"AKANE_DETAIL={exc}")
        return 2
    except OSError as exc:
        print("AKANE_RESULT=MANIFEST_IO_FAILED")
        print(f"AKANE_DETAIL={type(exc).__name__}")
        return 3

    print(f"AKANE_PERSONAL_FINANCE_STANDBY={'enabled' if enabled else 'disabled'}")
    print(f"AKANE_CHANGED={'yes' if changed else 'no'}")
    print("AKANE_RESULT=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
