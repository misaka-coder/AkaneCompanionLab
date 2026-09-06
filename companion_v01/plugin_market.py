"""Trusted static distribution index. Staging still belongs to the existing store.

The administrator chooses the index (HTTPS or a local release directory). Hashes
pin reviewed bytes; they do not make third-party Python code safe or sandboxed.
No plugin code is imported during browsing and no index enters the system prompt.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .plugin_api import is_valid_permission_id, is_valid_plugin_id
from .plugin_installation import ManagedPluginArtifactStore, PluginInstallationError


MAX_INDEX_BYTES = 1024 * 1024
MAX_WHEEL_BYTES = 512 * 1024 * 1024
HASH_PATTERN = re.compile(r"[a-f0-9]{64}")


def _error(reason, status="failed"):
    return PluginInstallationError(reason, status=status)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise _error("market_redirect_not_allowed")


def _text(value, maximum=240):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise _error("market_index_invalid")
    return value.strip()


def parse_index(raw: bytes) -> tuple[dict, ...]:
    if len(raw) > MAX_INDEX_BYTES:
        raise _error("market_index_too_large")
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError
        entries = data["plugins"]
        if not isinstance(entries, list) or len(entries) > 200:
            raise ValueError
        result, seen = [], set()
        for item in entries:
            if not isinstance(item, dict):
                raise ValueError
            plugin_id = item["plugin_id"]
            if not is_valid_plugin_id(plugin_id) or plugin_id in seen:
                raise ValueError
            seen.add(plugin_id)
            digest = item["sha256"]
            if not isinstance(digest, str) or not HASH_PATTERN.fullmatch(digest):
                raise ValueError
            wheel = _text(item["wheel"], 250)
            parts = PurePosixPath(wheel).parts
            if (
                not parts
                or any(p in {"", ".", ".."} for p in wheel.split("/"))
                or not re.fullmatch(r"[A-Za-z0-9_./-]+\.whl", wheel)
                or PurePosixPath(wheel).is_absolute()
            ):
                raise ValueError
            size = item["size_bytes"]
            if type(size) is not int or not 0 < size <= MAX_WHEEL_BYTES:
                raise ValueError
            permissions = item["permissions"]
            if (
                not isinstance(permissions, list)
                or len(permissions) > 32
                or any(not is_valid_permission_id(p) for p in permissions)
                or len(set(permissions)) != len(permissions)
            ):
                raise ValueError
            requirements = item.get("requirements", [])
            if not isinstance(requirements, list) or len(requirements) > 20:
                raise ValueError
            result.append(
                dict(
                    plugin_id=plugin_id,
                    version=_text(item["version"], 64),
                    display_name=_text(item["display_name"], 100),
                    summary=_text(item["summary"], 600),
                    sha256=digest,
                    size_bytes=size,
                    wheel=wheel,
                    permissions=sorted(permissions),
                    requirements=[_text(value, 600) for value in requirements],
                )
            )
        return tuple(result)
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        raise _error("market_index_invalid") from None


class StaticPluginMarket:
    def __init__(self, source: str | Path):
        self.source = str(source)

    @property
    def remote(self):
        return self.source.lower().startswith(("https://", "http://"))

    @contextmanager
    def _open(self, wheel: str | None = None):
        try:
            if self.remote:
                parts = urlsplit(self.source)
                if parts.scheme != "https" or parts.username or parts.password or parts.query or parts.fragment:
                    raise _error("market_source_invalid", "invalid_request")
                url = urljoin(self.source, wheel) if wheel else self.source
                request = Request(
                    url, headers={"Accept": "application/octet-stream", "User-Agent": "Akane-Plugin-Market/1"}
                )
                with build_opener(_NoRedirect()).open(request, timeout=15) as stream:
                    yield stream
            else:
                index = Path(self.source).resolve()
                target = (index.parent / wheel).resolve() if wheel else index
                if wheel:
                    try:
                        target.relative_to(index.parent)
                    except ValueError:
                        raise _error("market_wheel_path_invalid") from None
                with target.open("rb") as stream:
                    yield stream
        except PluginInstallationError:
            raise
        except (OSError, ValueError):
            raise _error("market_wheel_unavailable" if wheel else "market_index_unavailable", "unavailable") from None

    def _entries(self):
        with self._open() as stream:
            raw = stream.read(MAX_INDEX_BYTES + 1)
        return parse_index(raw)

    def browse(self) -> dict:
        entries = self._entries()
        return dict(
            ok=True,
            status="ready",
            source_kind="https" if self.remote else "local_release",
            plugins=[{key: value for key, value in entry.items() if key != "wheel"} for entry in entries],
        )

    def stage(self, store: ManagedPluginArtifactStore, *, plugin_id: str, digest: str) -> dict:
        if not is_valid_plugin_id(plugin_id) or not isinstance(digest, str) or not HASH_PATTERN.fullmatch(digest):
            raise _error("market_selection_invalid", "invalid_request")
        entry = next((item for item in self._entries() if item["plugin_id"] == plugin_id), None)
        if entry is None:
            raise _error("market_plugin_not_found", "not_found")
        if entry["sha256"] != digest:
            raise _error("market_selection_changed", "conflict")
        with tempfile.TemporaryDirectory(prefix="akane-market-") as temporary:
            wheel = Path(temporary) / PurePosixPath(entry["wheel"]).name
            sha, total = hashlib.sha256(), 0
            with self._open(entry["wheel"]) as source, wheel.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    total += len(chunk)
                    if total > entry["size_bytes"]:
                        raise _error("market_wheel_size_mismatch")
                    sha.update(chunk)
                    output.write(chunk)
            if total != entry["size_bytes"]:
                raise _error("market_wheel_size_mismatch")
            if sha.hexdigest() != digest:
                raise _error("market_wheel_digest_mismatch")
            # Only now can the existing installer execute a probe of trusted bytes.
            staged = store.stage_wheel(wheel)
        if (
            staged["plugin_id"] != plugin_id
            or staged["version"] != entry["version"]
            or staged["digest"] != digest
            or sorted(staged["permissions"]) != entry["permissions"]
        ):
            store.discard_stage(staged["stage_id"])
            raise _error("market_manifest_mismatch")
        return staged
