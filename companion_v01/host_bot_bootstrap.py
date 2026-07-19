"""Host bootstrap that selects legacy single-Bot or canonical bots.toml mode."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bot_profile import BOT_PROFILE_FILENAME, load_bot_host_profile, resolve_bot_data_root
from .bot_registry import BotRegistry, BotRegistryError
from .qq_channel_profiles import QQ_CHANNEL_PROFILES_RELATIVE_PATH, load_qq_channel_profiles


_SAFE_REASON_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class HostBotBootstrapError(RuntimeError):
    def __init__(self, *, status: str = "unavailable", reason: str) -> None:
        self.status = str(status or "unavailable")
        self.reason = str(reason or "host_bot_bootstrap_failed")
        super().__init__(json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True))

    def as_dict(self) -> dict[str, str | bool]:
        return {"ok": False, "status": self.status, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class HostBotBootstrapResult:
    registry: BotRegistry
    default_runtime: Any
    mode: str
    configured_count: int
    runtime_count: int
    construction_failures: tuple[dict[str, str], ...]

    def public_snapshot(self) -> dict[str, Any]:
        return {
            "status": "degraded" if self.construction_failures else "ready",
            "mode": self.mode,
            "configured_count": self.configured_count,
            "runtime_count": self.runtime_count,
            "construction_failures": [dict(item) for item in self.construction_failures],
            "registry": self.registry.public_snapshot(),
        }


def build_host_bot_registry(
    *,
    factory: Any,
    host_data_root: Path,
    selected_instance_id: str = "",
    explicit_data_root: bool = False,
) -> HostBotBootstrapResult:
    """Build all configured runtimes while preserving legacy compatibility."""

    data_root = Path(host_data_root)
    profile_path = data_root / BOT_PROFILE_FILENAME
    if not profile_path.exists():
        runtime = factory.create(
            data_root=data_root,
            selected_instance_id=selected_instance_id,
            explicit_data_root=explicit_data_root,
        )
        registry = BotRegistry(default_bot_id=runtime.bot_id)
        registry.add(runtime, default=True)
        return HostBotBootstrapResult(
            registry=registry,
            default_runtime=runtime,
            mode="legacy_single",
            configured_count=1,
            runtime_count=1,
            construction_failures=(),
        )

    if str(selected_instance_id or "").strip():
        raise HostBotBootstrapError(
            status="invalid_config",
            reason="bot_profile_and_instance_selector_conflict",
        )

    profile = load_bot_host_profile(profile_path)
    qq_profiles = load_qq_channel_profiles(
        data_root / QQ_CHANNEL_PROFILES_RELATIVE_PATH,
        missing_ok=True,
    )
    registry = BotRegistry(default_bot_id=profile.default_bot_id)
    failures: list[dict[str, str]] = []
    for bot_config in profile.enabled_bots:
        bot_root = resolve_bot_data_root(data_root, bot_config)
        try:
            runtime = factory.create(
                data_root=bot_root,
                bot_config=bot_config,
                qq_channel_profile=(qq_profiles.get(bot_config.qq.profile_ref) if bot_config.qq.enabled else None),
                explicit_data_root=True,
            )
        except Exception as exc:  # noqa: BLE001 - one configured Bot must not abort its siblings
            reason = _safe_construction_reason(exc)
            registry.add_unavailable(
                bot_config,
                reason=reason,
                data_root=bot_root,
                default=bot_config.bot_id == profile.default_bot_id,
            )
            failures.append({"bot_id": bot_config.bot_id, "reason": reason})
            continue
        registry.add(runtime, default=bot_config.bot_id == profile.default_bot_id)

    try:
        default_runtime = registry.default()
    except BotRegistryError as exc:
        _close_constructed_runtimes(registry.values())
        raise HostBotBootstrapError(status="unavailable", reason="default_bot_runtime_unavailable") from exc

    return HostBotBootstrapResult(
        registry=registry,
        default_runtime=default_runtime,
        mode="bot_profile",
        configured_count=len(profile.enabled_bots),
        runtime_count=len(registry.values()),
        construction_failures=tuple(failures),
    )


def _safe_construction_reason(exc: Exception) -> str:
    candidate = str(getattr(exc, "reason", "") or "").strip()
    if candidate and _SAFE_REASON_PATTERN.fullmatch(candidate) is not None:
        return candidate
    return "bot_runtime_construction_failed"


def _close_constructed_runtimes(runtimes: tuple[Any, ...]) -> None:
    for runtime in reversed(runtimes):
        try:
            runtime.engine.close()
        except Exception:
            pass
        try:
            runtime.instance_runtime.release()
        except Exception:
            pass


__all__ = [
    "HostBotBootstrapError",
    "HostBotBootstrapResult",
    "build_host_bot_registry",
]
