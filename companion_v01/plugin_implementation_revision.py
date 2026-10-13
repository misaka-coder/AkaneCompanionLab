"""Stable installed implementation identity, without importing plugin code."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname


def distribution_revision(entry_point) -> str:
    distribution = getattr(entry_point, "dist", None)
    locate = getattr(distribution, "locate_file", None)
    if not callable(locate):
        return ""
    files = {}
    for item in getattr(distribution, "files", None) or ():
        relative = str(item).replace("\\", "/")
        if relative.endswith((".pyc", ".pyo", "/RECORD")) or "__pycache__" in relative.split("/"):
            continue
        path = Path(locate(item))
        if path.is_file():
            files[relative] = path
    # Editable wheels list import hooks rather than their actual source files.
    read_text = getattr(distribution, "read_text", None)
    direct = json.loads(read_text("direct_url.json") or "{}") if callable(read_text) else {}
    if not isinstance(direct, dict) or not isinstance(direct.get("dir_info", {}), dict):
        return ""
    if direct.get("dir_info", {}).get("editable"):
        url = urlparse(str(direct.get("url") or ""))
        if url.scheme != "file":
            return ""
        root = Path(url2pathname(unquote(url.path)))
        module = str(getattr(entry_point, "value", "")).split(":", 1)[0].split(".")[0]
        if not module.isidentifier():
            return ""
        found = False
        for parent in (root / "src", root):
            package = parent / module
            sources = package.rglob("*") if package.is_dir() else (package.with_suffix(".py"),)
            for path in sources:
                if path.is_file() and path.suffix not in {".pyc", ".pyo"} and "__pycache__" not in path.parts:
                    files["source/" + path.relative_to(parent).as_posix()] = path
                    found = True
        if not found:
            return ""
    if not files:
        return ""
    hasher = hashlib.sha256()
    hasher.update(str(getattr(entry_point, "value", "")).encode("utf-8"))
    for relative, path in sorted(files.items()):
        hasher.update(relative.encode("utf-8") + b"\x00")
        hasher.update(hashlib.sha256(path.read_bytes()).digest())
    return hasher.hexdigest()
