"""Two-layer cover cache. Immutable audio objects, atomic completed manifests.

This is disposable business storage, not a Job store. The caller supplies an
authorized storage root and scope; no host resource identifiers are resolved.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid

from .errors import CoverSongError
from .models import matching_models


CACHE_VERSION = "cover-cache-v3"
HEX = re.compile(r"[0-9a-f]{64}")


def digest_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def cache_key(payload):
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def lookup_label(value):
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").lower())


class CoverCache:
    def __init__(self, root, *, scope):
        if not isinstance(scope, str) or not scope:
            raise ValueError("cover_cache_scope_required")
        # Hash the full identity, never a lossy filename sanitization/truncation.
        self.root = Path(root).resolve() / CACHE_VERSION / hashlib.sha256(scope.encode()).hexdigest()
        self.root.mkdir(parents=True, exist_ok=True)

    def _child(self, *parts):
        path = self.root.joinpath(*parts)
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("cover_cache_path_invalid")
        return path

    def _manifest(self, layer, key):
        if layer not in ("covers", "stems") or not HEX.fullmatch(key):
            raise ValueError("cover_cache_key_invalid")
        return self._child(layer, key + ".json")

    def _read(self, path):
        try:
            if path.stat().st_size > 128 * 1024:
                return {}
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, UnicodeError):
            return {}

    def get(self, layer, key, *, verify_content=True):
        manifest = self._read(self._manifest(layer, key))
        expected = {"cover"} if layer == "covers" else {"vocals", "instrumental"}
        if (
            manifest.get("version") != CACHE_VERSION
            or manifest.get("key") != key
            or manifest.get("layer") != layer
            or not isinstance(manifest.get("assets"), dict)
            or set(manifest["assets"]) != expected
        ):
            return None
        paths = {}
        try:
            for role, asset in manifest["assets"].items():
                if not isinstance(asset, dict):
                    return None
                sha, fmt = asset.get("sha256"), asset.get("format")
                if not isinstance(sha, str) or not HEX.fullmatch(sha) or fmt not in ("wav", "mp3", "flac"):
                    return None
                path = self._child("objects", sha + "." + fmt)
                if (
                    not path.is_file()
                    or path.stat().st_size != asset.get("size")
                    or path.stat().st_size <= 0
                    or (verify_content and digest_file(path) != sha)
                ):
                    return None
                paths[role] = path
        except (OSError, ValueError):
            return None
        metadata = manifest.get("metadata")
        if not isinstance(metadata, dict):
            return None
        return {"paths": paths, "metadata": metadata, "key": key}

    def put(self, layer, key, *, files, metadata):
        target = self._manifest(layer, key)
        expected = {"cover"} if layer == "covers" else {"vocals", "instrumental"}
        if set(files) != expected:
            raise ValueError("cover_cache_assets_invalid")
        assets = {}
        for role, source in files.items():
            source = Path(source)
            fmt = source.suffix.lower().lstrip(".")
            if fmt not in ("wav", "mp3", "flac") or not source.is_file() or source.stat().st_size <= 0:
                raise ValueError("cover_cache_audio_invalid")
            sha = digest_file(source)
            destination = self._child("objects", sha + "." + fmt)
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Always copy, never hardlink: later edits to a work/published file
            # must not mutate an object shared by other completed records.
            temporary = destination.with_name("." + uuid.uuid4().hex + ".tmp")
            try:
                shutil.copyfile(source, temporary)
                if digest_file(temporary) != sha:
                    raise OSError("cover_cache_source_changed")
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
            assets[role] = {"sha256": sha, "format": fmt, "size": destination.stat().st_size}
        manifest = {
            "version": CACHE_VERSION,
            "layer": layer,
            "key": key,
            "assets": assets,
            "metadata": metadata,
            "stored_at_ns": time.time_ns(),
        }
        raw = json.dumps(manifest, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(raw) > 128 * 1024:
            raise ValueError("cover_cache_manifest_too_large")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name("." + uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            # Only this final atomic replace makes the complete set visible.
            # Concurrent writers publish whole records; readers cannot combine
            # vocals from one generation with instrumental from another.
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def find_cover(self, *, song_title, artist, model_name, output_format, params=None):
        title, artist_key = lookup_label(song_title), lookup_label(artist)
        if not title:
            raise CoverSongError(
                stage="cache", reason="source_or_title_required", public_message="请提供歌曲材料或已翻唱过的歌曲名。"
            )
        candidates = []
        for path in self._child("covers").glob("*.json"):
            if not HEX.fullmatch(path.stem):
                continue
            manifest = self._read(path)
            meta = manifest.get("metadata")
            if not isinstance(meta, dict):
                continue
            if (
                lookup_label(meta.get("song_title")) != title
                or (artist_key and lookup_label(meta.get("artist")) != artist_key)
                or meta.get("output_format") != output_format
                or (params is not None and meta.get("params") != params)
                or not isinstance(meta.get("voice_model"), str)
                or not meta["voice_model"]
            ):
                continue
            entry = self.get("covers", path.stem, verify_content=False)
            if entry and entry["metadata"] == meta:
                try:
                    sequence = int(manifest.get("stored_at_ns") or 0)
                except (ValueError, TypeError):
                    continue
                candidates.append((sequence, entry))
        if model_name:
            names = matching_models([item[1]["metadata"]["voice_model"] for item in candidates], str(model_name))
            candidates = [item for item in candidates if item[1]["metadata"]["voice_model"] in names]
        # Verify the newest intact object per song/voice identity, not every
        # historical render. A corrupt newest object still falls back safely.
        matches, verified = [], set()
        for sequence, candidate in sorted(candidates, key=lambda item: item[0], reverse=True):
            meta = candidate["metadata"]
            identity = (lookup_label(meta.get("artist")), meta["voice_model"].lower())
            if identity in verified:
                continue
            entry = self.get("covers", candidate["key"])
            if entry and entry["metadata"] == meta:
                verified.add(identity)
                matches.append((sequence, entry))
        if not matches:
            raise CoverSongError(
                stage="cache",
                reason="cached_cover_not_found",
                public_message="没有找到匹配的已完成翻唱，请提供源材料。",
            )
        if not artist_key and len({lookup_label(x[1]["metadata"].get("artist")) for x in matches}) > 1:
            raise CoverSongError(
                stage="cache",
                reason="cached_cover_ambiguous",
                public_message="同名歌曲有不同原唱的翻唱缓存，请补充原唱。",
            )
        if len({x[1]["metadata"]["voice_model"].lower() for x in matches}) > 1:
            raise CoverSongError(
                stage="cache",
                reason="cached_voice_model_ambiguous",
                public_message="这首歌有不同音色的已完成翻唱，请指定音色模型。",
            )
        return max(matches, key=lambda item: item[0])[1]

    def has_cover(self, *, max_entries=128):
        # Availability hints do not stream gigabytes of cached media per turn.
        # Every actual restoration still performs full content verification.
        for index, path in enumerate(self._child("covers").glob("*.json")):
            if index >= max(1, min(1000, int(max_entries))):
                break
            if HEX.fullmatch(path.stem) and self.get("covers", path.stem, verify_content=False):
                return True
        return False
