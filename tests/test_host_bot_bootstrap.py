from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from companion_v01.host_bot_bootstrap import HostBotBootstrapError, build_host_bot_registry


BOT_PROFILE = """\
schema_version = 1
default_bot_id = "bot-a"

[[bots]]
bot_id = "bot-a"
enabled = true
display_name = "Akane A"
memory_space_id = "memory-a"
care_enabled = true

[[bots]]
bot_id = "bot-b"
enabled = true
display_name = "Akane B"
memory_space_id = "memory-b"
care_enabled = true

[[bots]]
bot_id = "bot-disabled"
enabled = false
display_name = "Disabled"
memory_space_id = "memory-disabled"
care_enabled = true
"""

QQ_BOT_PROFILE = """\
schema_version = 1
default_bot_id = "bot-a"

[[bots]]
bot_id = "bot-a"
enabled = true
display_name = "Akane"
wake_words = ["Akane"]
memory_space_id = "memory-a"

[bots.channels.qq]
enabled = true
profile_ref = "qq.bot-a"

[[bots]]
bot_id = "bot-b"
enabled = true
display_name = "Finance"
wake_words = ["金融助手"]
memory_space_id = "memory-b"

[bots.channels.qq]
enabled = true
profile_ref = "qq.bot-b"
"""

QQ_PROFILES = """\
schema_version = 1

[[profiles]]
profile_ref = "qq.bot-a"
bot_qq = "10000001"
onebot_http_url = "http://127.0.0.1:3001"
webhook_secret = "webhook-a"
onebot_access_token = "token-a"

[[profiles]]
profile_ref = "qq.bot-b"
bot_qq = "10000002"
onebot_http_url = "http://127.0.0.1:3002"
webhook_secret = "webhook-b"
onebot_access_token = "token-b"
"""


class _FakeRuntime:
    def __init__(self, bot_id: str, data_root: Path, *, desktop_satellite_service: Any = None) -> None:
        self.bot_id = bot_id
        self.display_name = bot_id
        self.desktop_satellite_service = desktop_satellite_service or object()
        self.runtime_layout = SimpleNamespace(data_root=Path(data_root).resolve())
        self.engine = SimpleNamespace(close=self._close_engine)
        self.instance_runtime = SimpleNamespace(release=self._release_lease)
        self.engine_close_count = 0
        self.lease_release_count = 0

    def _close_engine(self) -> None:
        self.engine_close_count += 1

    def _release_lease(self) -> None:
        self.lease_release_count += 1

    async def start(self) -> dict[str, str]:
        return {"status": "active", "reason": ""}

    async def stop(self) -> dict[str, str]:
        return {"status": "stopped", "reason": ""}


class _FakeFactory:
    def __init__(self, *, fail_bot_ids: set[str] | None = None) -> None:
        self.fail_bot_ids = set(fail_bot_ids or set())
        self.calls: list[dict[str, Any]] = []
        self.runtimes: list[_FakeRuntime] = []

    def create(self, **kwargs: Any) -> _FakeRuntime:
        self.calls.append(dict(kwargs))
        bot_config = kwargs.get("bot_config")
        bot_id = str(getattr(bot_config, "bot_id", "legacy-default"))
        if bot_id in self.fail_bot_ids:
            raise RuntimeError(f"private failure at {kwargs['data_root']}")
        runtime = _FakeRuntime(
            bot_id,
            Path(kwargs["data_root"]),
            desktop_satellite_service=kwargs.get("desktop_satellite_service"),
        )
        self.runtimes.append(runtime)
        return runtime


class HostBotBootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_bots_toml_preserves_legacy_single_bot_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            factory = _FakeFactory()
            result = build_host_bot_registry(
                factory=factory,
                host_data_root=Path(temp_dir),
                selected_instance_id="legacy-personal",
                explicit_data_root=True,
            )

        self.assertEqual(result.mode, "legacy_single")
        self.assertEqual(result.default_runtime.bot_id, "legacy-default")
        self.assertEqual(len(factory.calls), 1)
        self.assertEqual(factory.calls[0]["selected_instance_id"], "legacy-personal")
        self.assertNotIn("bot_config", factory.calls[0])

    async def test_bots_toml_constructs_enabled_bots_under_host_owned_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root.joinpath("bots.toml").write_text(BOT_PROFILE, encoding="utf-8")
            factory = _FakeFactory()
            result = build_host_bot_registry(factory=factory, host_data_root=root)

            self.assertEqual(result.mode, "bot_profile")
            self.assertEqual(result.configured_count, 2)
            self.assertEqual(result.runtime_count, 2)
            self.assertEqual(result.default_runtime.bot_id, "bot-a")
            self.assertEqual([call["bot_config"].bot_id for call in factory.calls], ["bot-a", "bot-b"])
            self.assertEqual(
                [Path(call["data_root"]) for call in factory.calls],
                [(root / "bots" / "memory-a").resolve(), (root / "bots" / "memory-b").resolve()],
            )
            self.assertTrue(all(call["explicit_data_root"] for call in factory.calls))
            self.assertIs(factory.runtimes[0].desktop_satellite_service, factory.runtimes[1].desktop_satellite_service)
            self.assertIsNone(factory.calls[0]["desktop_satellite_service"])
            self.assertIs(
                factory.calls[1]["desktop_satellite_service"],
                factory.runtimes[0].desktop_satellite_service,
            )

            started = await result.registry.start_all(timeout_seconds=1.0)
            self.assertEqual(started["status"], "active")

    async def test_default_bot_constructs_first_and_owns_shared_desktop_satellite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root.joinpath("bots.toml").write_text(
                BOT_PROFILE.replace('default_bot_id = "bot-a"', 'default_bot_id = "bot-b"'),
                encoding="utf-8",
            )
            factory = _FakeFactory()

            result = build_host_bot_registry(factory=factory, host_data_root=root)

        self.assertEqual(result.default_runtime.bot_id, "bot-b")
        self.assertEqual([call["bot_config"].bot_id for call in factory.calls], ["bot-b", "bot-a"])
        self.assertIsNone(factory.calls[0]["desktop_satellite_service"])
        self.assertIs(
            factory.calls[1]["desktop_satellite_service"],
            factory.runtimes[0].desktop_satellite_service,
        )
        self.assertIs(factory.runtimes[0].desktop_satellite_service, factory.runtimes[1].desktop_satellite_service)

    async def test_host_selects_each_bot_qq_profile_from_secret_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root.joinpath("bots.toml").write_text(QQ_BOT_PROFILE, encoding="utf-8")
            secrets_dir = root / "secrets"
            secrets_dir.mkdir()
            secrets_dir.joinpath("qq_profiles.toml").write_text(QQ_PROFILES, encoding="utf-8")
            factory = _FakeFactory()

            result = build_host_bot_registry(factory=factory, host_data_root=root)

        self.assertEqual(result.runtime_count, 2)
        selected = [call["qq_channel_profile"] for call in factory.calls]
        self.assertEqual([item.profile_ref for item in selected], ["qq.bot-a", "qq.bot-b"])
        self.assertEqual([item.bot_qq for item in selected], ["10000001", "10000002"])
        self.assertEqual(
            [item.onebot_http_url for item in selected],
            [
                "http://127.0.0.1:3001",
                "http://127.0.0.1:3002",
            ],
        )
        self.assertNotIn("webhook-a", repr(selected[0]))
        self.assertNotIn("token-b", repr(selected[1]))

    async def test_non_default_construction_failure_is_visible_but_does_not_abort_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root.joinpath("bots.toml").write_text(BOT_PROFILE, encoding="utf-8")
            result = build_host_bot_registry(
                factory=_FakeFactory(fail_bot_ids={"bot-b"}),
                host_data_root=root,
            )

            self.assertEqual(result.default_runtime.bot_id, "bot-a")
            self.assertEqual(result.runtime_count, 1)
            self.assertEqual(
                result.construction_failures, ({"bot_id": "bot-b", "reason": "bot_runtime_construction_failed"},)
            )
            public = result.public_snapshot()
            self.assertNotIn(temp_dir, str(public))
            self.assertNotIn("private failure", str(public))
            states = {item["bot_id"]: item["state"] for item in public["registry"]["bots"]}
            self.assertEqual(states, {"bot-a": "registered", "bot-b": "degraded"})

            started = await result.registry.start_all(timeout_seconds=1.0)
            self.assertEqual(started["status"], "degraded")
            self.assertEqual(result.registry.require("bot-a").bot_id, "bot-a")

    async def test_default_construction_failure_closes_sibling_runtimes_and_fails_host(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root.joinpath("bots.toml").write_text(BOT_PROFILE, encoding="utf-8")
            factory = _FakeFactory(fail_bot_ids={"bot-a"})

            with self.assertRaises(HostBotBootstrapError) as raised:
                build_host_bot_registry(factory=factory, host_data_root=root)

        self.assertEqual(raised.exception.reason, "default_bot_runtime_unavailable")
        self.assertEqual(len(factory.runtimes), 1)
        self.assertEqual(factory.runtimes[0].bot_id, "bot-b")
        self.assertEqual(factory.runtimes[0].engine_close_count, 1)
        self.assertEqual(factory.runtimes[0].lease_release_count, 1)
        self.assertNotIn(temp_dir, str(raised.exception))

    async def test_bots_toml_rejects_legacy_instance_selector_ambiguity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root.joinpath("bots.toml").write_text(BOT_PROFILE, encoding="utf-8")
            with self.assertRaises(HostBotBootstrapError) as raised:
                build_host_bot_registry(
                    factory=_FakeFactory(),
                    host_data_root=root,
                    selected_instance_id="legacy-personal",
                )

        self.assertEqual(raised.exception.reason, "bot_profile_and_instance_selector_conflict")


if __name__ == "__main__":
    unittest.main()
