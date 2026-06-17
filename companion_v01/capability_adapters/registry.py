from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from .manifest_loader import load_manifest
from .types import CapabilityManifest, InvalidManifest


logger = logging.getLogger("akane.capability_adapter")


class CapabilityAdapterRegistry:
    def __init__(
        self,
        *,
        builtin_dir: Path,
        profile_dir_provider: Callable[[], Path],
    ) -> None:
        self.builtin_dir = Path(builtin_dir)
        self.profile_dir_provider = profile_dir_provider
        self._manifests: dict[str, CapabilityManifest] = {}
        self._invalid: list[InvalidManifest] = []

    def scan(self) -> None:
        manifests: dict[str, CapabilityManifest] = {}
        invalid: list[InvalidManifest] = []
        for source_layer, directory in (
            ("builtin", self.builtin_dir),
            ("profile", self._profile_dir()),
        ):
            for path in self._manifest_paths(directory):
                loaded = load_manifest(path, source_layer=source_layer)
                if isinstance(loaded, InvalidManifest):
                    invalid.append(loaded)
                    continue
                manifests[loaded.provider_id] = loaded
        self._manifests = manifests
        self._invalid = invalid
        logger.info(
            "CapabilityAdapterRegistry scanned: %s valid, %s invalid",
            len(self._manifests),
            len(self._invalid),
        )

    def list_manifests(self) -> tuple[CapabilityManifest, ...]:
        return tuple(self._manifests[key] for key in sorted(self._manifests))

    def list_invalid(self) -> tuple[InvalidManifest, ...]:
        return tuple(self._invalid)

    def get(self, provider_id: str) -> CapabilityManifest | None:
        return self._manifests.get(str(provider_id or "").strip())

    def reload(self, provider_id: str) -> None:
        logger.debug("CapabilityAdapterRegistry reload(%s) uses full re-scan in M1", provider_id)
        self.scan()

    def _profile_dir(self) -> Path:
        try:
            return Path(self.profile_dir_provider())
        except Exception as exc:
            logger.warning("CapabilityAdapterRegistry profile dir unavailable: %s", exc)
            return self.builtin_dir / ".profile_dir_unavailable"

    @staticmethod
    def _manifest_paths(directory: Path) -> tuple[Path, ...]:
        if not directory or not directory.is_dir():
            return ()
        return tuple(sorted(path for path in directory.glob("*.yaml") if path.is_file()))
