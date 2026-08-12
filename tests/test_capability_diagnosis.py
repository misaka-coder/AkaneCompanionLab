"""Phase 5 focused tests: the /能力 host capability diagnosis command.

Covers the acceptance rows:
  - Shell off (1027-style group) shows 已关闭; Shell on (8727-style) shows 已开启.
  - Group vision off shows 已关闭.
  - Satellite offline shows 离线 while the stable tool-count row stays visible.
  - State is read live: a state flip changes the reply immediately.
  - No session id, path, endpoint or credential leak in the reply.
  - The command is reachable through the plugin command broker's host channel.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from companion_v01.capability_diagnosis import (
    CAPABILITY_COMMAND,
    build_capability_diagnosis,
    build_host_command_registrations,
)
from companion_v01.plugin_api import PluginQQCommandRequest
from companion_v01.plugin_qq_commands import PluginQQCommandBroker


class _FakeSelection:
    tool_names = ("exec_run", "load_material", "transcribe_media", "send_file", "load_skill")


class _FakeSkillEntry:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeSkillSnapshot:
    entries = (_FakeSkillEntry("media-inspect-convert"), _FakeSkillEntry("video-understanding"))


class _FakeSkillRegistry:
    def snapshot(self) -> _FakeSkillSnapshot:
        return _FakeSkillSnapshot()


class _FakeProvider:
    provider_id = "local"


class _FakeEngine:
    def __init__(
        self,
        *,
        shell_mode: str = "trusted_auto_allow",
        execution_enabled: bool = True,
        execution_qq_enabled: bool = True,
        chat_model: str = "claude-sonnet-5",
        vision_model: str = "gemini-3.5-flash",
        vision_enabled: bool = True,
        selection: object | None = None,
        base_dir: Path | None = None,
    ) -> None:
        self.capability_config_base_dir = str(base_dir or Path(tempfile.mkdtemp(prefix="capdiag_")))
        self.execution_provider = _FakeProvider() if execution_enabled else None
        self.skill_registry = _FakeSkillRegistry()
        self.tool_handlers = {"exec_run": _FakeExecHandler() if execution_enabled else {}}
        self._shell_mode = shell_mode
        self._execution_qq_enabled = execution_qq_enabled
        self._selection = selection if selection is not None else _FakeSelection()
        self.settings = _FakeSettings(chat_model=chat_model, vision_model=vision_model, vision_enabled=vision_enabled)

    def _resolve_client_protocol_context(self, payload):
        return {"client_mode": "qq_text"}

    def _resolve_capability_selection(self, **kwargs):
        return self._selection


class _FakeExecHandler:
    def capability_status(self) -> dict:
        return {"enabled": True, "status": "ready"}


class _FakeSettings:
    def __init__(self, *, chat_model: str, vision_model: str, vision_enabled: bool) -> None:
        self.chat_model_name = chat_model
        self.vision_model_name = vision_model
        self.vision_enabled = vision_enabled


class _FakeGateway:
    def __init__(self, *, master_qq: str = "10001", group_vision: bool = True) -> None:
        self._master_qq = master_qq
        self._group_vision = group_vision

    @property
    def master_qq(self) -> str:
        return self._master_qq

    def is_group_vision_enabled(self, group_id) -> bool:
        return self._group_vision


class _FakeSatellite:
    def __init__(self, status: str = "online") -> None:
        self._status = status

    def diagnostics(self) -> dict:
        return {"status": self._status, "connected": self._status == "online"}


class _FakeConfig:
    EXECUTION_QQ_ENABLED = True
    QQ_BOT_QQ = "99999"
    QQ_CHARACTER_PACK_ID = ""


def _build(
    engine: _FakeEngine,
    gateway: _FakeGateway,
    satellite: _FakeSatellite | None = None,
    *,
    session_id: str = "qq_group_shared_1027685626",
    group_id: int = 1027685626,
    is_group: bool = True,
    qq_number: int = 10001,
) -> dict:
    from companion_v01.local_capability_config import save_approval_policy_config

    save_approval_policy_config(
        base_dir=engine.capability_config_base_dir,
        profile_user_id=session_id,
        payload={
            "defaultMode": "trusted_auto_allow",
            "capabilityModes": {"exec_run": engine._shell_mode},
        },
    )
    return build_capability_diagnosis(
        engine=engine,
        qq_gateway=gateway,
        config_module=_FakeConfig,
        satellite_service=satellite,
        bot_label="Akane",
        profile_user_id=session_id,
        session_id=session_id,
        group_id=group_id,
        is_group=is_group,
        qq_number=qq_number,
    )


class CapabilityDiagnosisContentTests(unittest.TestCase):
    def test_shell_off_group_shows_closed(self) -> None:
        engine = _FakeEngine(shell_mode="disabled")
        result = _build(engine, _FakeGateway())
        reply = result["reply"]
        self.assertIn("Shell：已关闭", reply)
        self.assertIn("当前不可用：Shell：已关闭", reply)

    def test_shell_on_group_shows_enabled(self) -> None:
        engine = _FakeEngine(shell_mode="trusted_auto_allow")
        reply = _build(engine, _FakeGateway())["reply"]
        self.assertIn("Shell：已开启（直接执行）", reply)
        self.assertNotIn("当前不可用：Shell", reply)

    def test_state_flip_is_reflected_immediately(self) -> None:
        engine = _FakeEngine(shell_mode="trusted_auto_allow")
        gateway = _FakeGateway(group_vision=True)
        self.assertIn("群识图：已开启", _build(engine, gateway)["reply"])
        gateway._group_vision = False
        self.assertIn("群识图：已关闭", _build(engine, gateway)["reply"])
        engine._shell_mode = "disabled"
        self.assertIn("Shell：已关闭", _build(engine, gateway)["reply"])

    def test_owner_sees_execution_location_and_satellite_rows(self) -> None:
        engine = _FakeEngine()
        result = _build(engine, _FakeGateway(), _FakeSatellite(status="offline"))
        reply = result["reply"]
        self.assertTrue(result["is_owner"])
        self.assertIn("Shell 执行位置：宿主本机执行器", reply)
        self.assertIn("Satellite：离线", reply)
        self.assertIn("Satellite：离线", reply)
        self.assertIn("当前模型可见工具：5 个", reply)

    def test_member_sees_public_subset_without_owner_rows(self) -> None:
        engine = _FakeEngine()
        result = _build(engine, _FakeGateway(), _FakeSatellite(status="offline"), qq_number=5555)
        reply = result["reply"]
        self.assertFalse(result["is_owner"])
        self.assertIn("Chat 模型：claude-sonnet-5", reply)
        self.assertIn("当前模型可见工具：5 个", reply)
        self.assertNotIn("Shell 执行位置", reply)
        self.assertNotIn("\nSatellite：", reply)

    def test_private_chat_session_label(self) -> None:
        engine = _FakeEngine()
        reply = _build(
            engine,
            _FakeGateway(),
            session_id="qq_pri_1367185586",
            group_id=0,
            is_group=False,
            qq_number=10001,
        )["reply"]
        self.assertIn("会话：私聊 #", reply)
        self.assertNotIn("qq_pri_1367185586", reply)
        self.assertNotIn("群识图", reply)

    def test_reply_leaks_no_session_path_or_endpoint(self) -> None:
        engine = _FakeEngine(base_dir=Path(tempfile.gettempdir()))
        gateway = _FakeGateway()
        session_id = "qq_group_shared_1027685626"
        reply = _build(engine, gateway, session_id=session_id)["reply"]
        self.assertNotIn(session_id, reply)
        self.assertNotIn(tempfile.gettempdir(), reply)
        self.assertNotIn("api.", reply)
        self.assertNotIn("http", reply)
        self.assertNotIn("C:\\", reply)

    def test_unavailable_rows_listed_with_public_reasons(self) -> None:
        engine = _FakeEngine(shell_mode="disabled", vision_enabled=False)
        reply = _build(engine, _FakeGateway())["reply"]
        self.assertIn("Shell：已关闭", reply)
        self.assertIn("视觉模型：未配置", reply)


class CapabilityDiagnosisBrokerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _FakeEngine()
        self.gateway = _FakeGateway()

    def _broker(self) -> PluginQQCommandBroker:
        registrations = build_host_command_registrations(
            engine=self.engine,
            qq_gateway=self.gateway,
            config_module=_FakeConfig,
            satellite_service=_FakeSatellite(status="online"),
            bot_label="Akane",
        )
        return PluginQQCommandBroker(tuple(), host_registrations=registrations)

    def test_host_command_reachable_through_broker(self) -> None:
        broker = self._broker()
        self.assertIn(CAPABILITY_COMMAND, broker.registered_commands)
        self.assertTrue(broker.handles(CAPABILITY_COMMAND))

        result = asyncio.run(
            broker.dispatch(
                command=CAPABILITY_COMMAND,
                args="详情",
                qq_number=10001,
                group_id=1027685626,
                is_group=True,
                profile_user_id="qq_group_shared_1027685626",
                session_id="qq_group_shared_1027685626",
            )
        )
        self.assertTrue(result.handled)
        self.assertEqual(result.reason, "")
        self.assertIn("能力诊断", result.reply_text)
        self.assertIn("当前模型可见工具", result.reply_text)

    def test_host_command_takes_precedence_over_plugin_same_token(self) -> None:
        from companion_v01.plugin_api import PluginQQCommandResult
        from companion_v01.plugin_qq_commands import _PluginCommandRegistration

        class _PluginHandler:
            async def handle(self, request) -> PluginQQCommandResult:
                return PluginQQCommandResult(handled=True, reply_text="plugin reply", reason="")

        broker = PluginQQCommandBroker(
            (_PluginCommandRegistration(plugin_id="demo", command=CAPABILITY_COMMAND, handler=_PluginHandler()),),
            host_registrations=build_host_command_registrations(
                engine=self.engine,
                qq_gateway=self.gateway,
                config_module=_FakeConfig,
            ),
        )
        result = asyncio.run(
            broker.dispatch(
                command=CAPABILITY_COMMAND,
                args="",
                qq_number=10001,
                group_id=0,
                is_group=False,
                profile_user_id="master",
                session_id="master",
            )
        )
        self.assertIn("能力诊断", result.reply_text)
        self.assertNotIn("plugin reply", result.reply_text)

    def test_unregistered_command_passes_through(self) -> None:
        broker = self._broker()
        self.assertFalse(broker.handles("/other"))


if __name__ == "__main__":
    unittest.main()
