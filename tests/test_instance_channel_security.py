from __future__ import annotations

import unittest
import tempfile
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from companion_v01.deployment_security import (
    AdminWriteAuth,
    DeploymentSecurityError,
    QQChannelRuntimeConfig,
    resolve_instance_deployment_security,
)
from companion_v01.bot_profile import parse_bot_host_profile
from companion_v01.attachment_ingest import AttachmentIngestService
from companion_v01.instance_profile import (
    InstanceContext,
    build_local_default_instance_context,
    instance_context_from_bot_config,
    parse_instance_manifest,
)
from companion_v01.qq_channel_profiles import QQChannelDeploymentProfile
from companion_v01.qq_gateway import NapCatQQGateway, QQMessageContext
from companion_v01.routes.control_center import build_control_center_router
from companion_v01.routes.model_services import build_model_services_router
from companion_v01.routes.plugins import build_plugins_router
from companion_v01.routes.qq import build_qq_router
from companion_v01.routes.system import build_system_router
from companion_v01 import settings_catalog


def _named_context(*, qq_enabled: bool = False, profile_ref: str = "") -> InstanceContext:
    manifest = parse_instance_manifest(
        {
            "schema_version": 1,
            "instance_id": "finance-prod",
            "character_pack_id": "akane",
            "features": {"care": False},
            "channels": {
                "qq": {
                    "enabled": qq_enabled,
                    "profile_ref": profile_ref,
                }
            },
            "plugins": [],
        },
        selected_instance_id="finance-prod",
    )
    return InstanceContext(manifest=manifest, source="manifest")


def _config(**overrides):
    values = {
        "AKANE_ADMIN_TOKEN": "admin-secret",
        "QQ_BRIDGE_ENABLED": False,
        "QQ_CHANNEL_PROFILE_REF": "",
        "QQ_BOT_QQ": "",
        "QQ_ONEBOT_HTTP_URL": "http://127.0.0.1:3001",
        "QQ_WEBHOOK_SECRET": "",
        "QQ_ONEBOT_ACCESS_TOKEN": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class InstanceDeploymentSecurityTests(unittest.TestCase):
    def test_named_instance_requires_admin_token_even_with_qq_disabled(self) -> None:
        with self.assertRaises(DeploymentSecurityError) as ctx:
            resolve_instance_deployment_security(
                _named_context(),
                _config(AKANE_ADMIN_TOKEN=""),
            )
        self.assertEqual(ctx.exception.reason, "admin_token_required")
        self.assertEqual(ctx.exception.field, "AKANE_ADMIN_TOKEN")

    def test_named_disabled_qq_ignores_legacy_bridge_switch(self) -> None:
        security = resolve_instance_deployment_security(
            _named_context(),
            _config(QQ_BRIDGE_ENABLED=True),
        )
        self.assertFalse(security.qq.enabled)
        self.assertTrue(security.admin.require_token)

    def test_named_enabled_qq_requires_complete_deployment_binding(self) -> None:
        context = _named_context(qq_enabled=True, profile_ref="finance-qq")
        cases = (
            ({}, "qq_channel_profile_ref_required"),
            ({"QQ_CHANNEL_PROFILE_REF": "other"}, "qq_channel_profile_ref_mismatch"),
            ({"QQ_CHANNEL_PROFILE_REF": "finance-qq"}, "qq_bot_id_required"),
            (
                {"QQ_CHANNEL_PROFILE_REF": "finance-qq", "QQ_BOT_QQ": "123456"},
                "qq_webhook_secret_required",
            ),
            (
                {
                    "QQ_CHANNEL_PROFILE_REF": "finance-qq",
                    "QQ_BOT_QQ": "123456",
                    "QQ_WEBHOOK_SECRET": "hook-secret",
                },
                "qq_onebot_access_token_required",
            ),
        )
        for overrides, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                with self.assertRaises(DeploymentSecurityError) as ctx:
                    resolve_instance_deployment_security(context, _config(**overrides))
                self.assertEqual(ctx.exception.reason, expected_reason)

    def test_named_enabled_qq_binds_immutable_profile_and_secrets(self) -> None:
        security = resolve_instance_deployment_security(
            _named_context(qq_enabled=True, profile_ref="finance-qq"),
            _config(
                QQ_CHANNEL_PROFILE_REF="finance-qq",
                QQ_BOT_QQ="123456",
                QQ_WEBHOOK_SECRET="hook-secret",
                QQ_ONEBOT_ACCESS_TOKEN="onebot-token",
            ),
        )
        self.assertTrue(security.qq.enabled)
        self.assertEqual(security.qq.profile_ref, "finance-qq")
        self.assertEqual(security.qq.bot_id, "123456")
        self.assertNotIn("hook-secret", repr(security))
        self.assertNotIn("onebot-token", repr(security))

    def test_canonical_bot_uses_only_its_selected_qq_profile(self) -> None:
        profile = parse_bot_host_profile(
            {
                "schema_version": 1,
                "default_bot_id": "bot-a",
                "bots": [
                    {
                        "bot_id": "bot-a",
                        "display_name": "Akane",
                        "memory_space_id": "memory-a",
                        "channels": {"qq": {"enabled": True, "profile_ref": "qq.bot-a"}},
                    }
                ],
            }
        )
        context = instance_context_from_bot_config(profile.require("bot-a"))
        selected = QQChannelDeploymentProfile(
            profile_ref="qq.bot-a",
            bot_qq="10000001",
            onebot_http_url="http://127.0.0.1:3101",
            webhook_secret="selected-webhook",
            onebot_access_token="selected-token",
        )
        security = resolve_instance_deployment_security(
            context,
            _config(
                QQ_CHANNEL_PROFILE_REF="qq.other",
                QQ_BOT_QQ="99999999",
                QQ_ONEBOT_HTTP_URL="http://127.0.0.1:3999",
                QQ_WEBHOOK_SECRET="other-webhook",
                QQ_ONEBOT_ACCESS_TOKEN="other-token",
            ),
            qq_channel_profile=selected,
        )

        self.assertEqual(security.qq.profile_ref, "qq.bot-a")
        self.assertEqual(security.qq.bot_id, "10000001")
        self.assertEqual(security.qq.onebot_http_url, "http://127.0.0.1:3101")
        self.assertEqual(security.qq.webhook_secret, "selected-webhook")
        self.assertEqual(security.qq.onebot_access_token, "selected-token")
        self.assertNotIn("other-webhook", repr(security))

        mismatched = QQChannelDeploymentProfile(
            profile_ref="qq.other",
            bot_qq="10000002",
            onebot_http_url="http://127.0.0.1:3102",
            webhook_secret="mismatched-webhook",
            onebot_access_token="mismatched-token",
        )
        with self.assertRaises(DeploymentSecurityError) as raised:
            resolve_instance_deployment_security(
                context,
                _config(),
                qq_channel_profile=mismatched,
            )
        self.assertEqual(raised.exception.reason, "qq_channel_profile_mismatch")

    def test_local_default_preserves_optional_secret_compatibility(self) -> None:
        security = resolve_instance_deployment_security(
            build_local_default_instance_context(),
            _config(
                AKANE_ADMIN_TOKEN="",
                QQ_BRIDGE_ENABLED=True,
                QQ_BOT_QQ="",
            ),
        )
        self.assertTrue(security.qq.enabled)
        self.assertFalse(security.qq.require_webhook_auth)
        self.assertFalse(security.qq.require_self_id)
        self.assertTrue(security.admin.allow_loopback_without_token)

    def test_app_resolves_security_before_engine_construction(self) -> None:
        root = Path(__file__).resolve().parents[1]
        app_source = (root / "companion_v01" / "app.py").read_text(encoding="utf-8")
        runtime_source = (root / "companion_v01" / "bot_runtime.py").read_text(encoding="utf-8")
        self.assertIn("BotRuntimeFactory(", app_source)
        self.assertNotIn("AkaneMemoryEngine(", app_source)
        self.assertLess(
            runtime_source.index("resolve_instance_deployment_security("),
            runtime_source.index("engine = AkaneMemoryEngine("),
        )

    def test_channel_and_admin_settings_are_deployment_owned_and_redacted(self) -> None:
        catalog = settings_catalog.build_settings_catalog(
            _config(
                QQ_WEBHOOK_SECRET="must-not-leak",
                QQ_ONEBOT_ACCESS_TOKEN="must-not-leak-either",
            )
        )
        entries = {entry["key"]: entry for group in catalog["categories"] for entry in group["settings"]}
        for key in (
            "AKANE_WORKSPACE_ROOT",
            "QQ_BRIDGE_ENABLED",
            "QQ_ONEBOT_HTTP_URL",
            "QQ_CHANNEL_PROFILE_REF",
            "QQ_BOT_QQ",
            "QQ_WEBHOOK_SECRET",
            "QQ_ONEBOT_ACCESS_TOKEN",
            "AKANE_ADMIN_TOKEN",
            "HOST",
            "PORT",
        ):
            self.assertEqual(entries[key]["managedIn"], settings_catalog.MANAGED_DEPLOYMENT)
            self.assertFalse(entries[key]["editable"])
        for key in ("QQ_WEBHOOK_SECRET", "QQ_ONEBOT_ACCESS_TOKEN", "AKANE_ADMIN_TOKEN"):
            self.assertTrue(entries[key]["sensitive"])
            self.assertNotIn("current", entries[key])


class ManagementWriteAuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.auth = AdminWriteAuth(
            token="admin-secret",
            require_token=True,
            allow_loopback_without_token=False,
        )

    def test_control_center_write_requires_token_even_on_loopback(self) -> None:
        app = FastAPI()
        app.include_router(build_control_center_router(admin_auth=self.auth))
        client = TestClient(app)

        denied = client.post("/control-center/actions/noop", json={})
        allowed = client.post(
            "/control-center/actions/noop",
            json={},
            headers={"Authorization": "Bearer admin-secret"},
        )

        self.assertEqual(denied.status_code, 401)
        self.assertEqual(denied.json()["reason"], "admin_auth_required")
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.json()["status"], "not-implemented")

    def test_model_service_write_rejects_before_read_or_reload(self) -> None:
        store = Mock()
        engine = Mock()
        app = FastAPI()
        app.include_router(
            build_model_services_router(
                store=store,
                config_module=SimpleNamespace(),
                engine=engine,
                admin_auth=self.auth,
            )
        )

        response = TestClient(app).post(
            "/control-center/model-service",
            json={"providerId": "ollama"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["reason"], "admin_auth_required")
        store.load.assert_not_called()
        store.save.assert_not_called()
        engine.reload_model_services.assert_not_called()

    def test_plugin_admin_diagnostics_require_named_instance_token(self) -> None:
        plugin_host = Mock()
        plugin_host.status_snapshot.return_value = {"ok": True, "status": "active"}
        app = FastAPI()
        app.include_router(build_plugins_router(plugin_host=plugin_host, admin_auth=self.auth))
        client = TestClient(app)

        denied = client.get("/admin/plugins/status")
        allowed = client.get(
            "/admin/plugins/status",
            headers={"Authorization": "Bearer admin-secret"},
        )

        self.assertEqual(denied.status_code, 401)
        self.assertEqual(allowed.status_code, 200)
        plugin_host.status_snapshot.assert_called_once()

    def test_memcore_admin_write_rejects_before_engine(self) -> None:
        engine = Mock()
        app = FastAPI()
        app.include_router(
            build_system_router(
                engine=engine,
                runtime_metrics=Mock(),
                public_guard=Mock(),
                log_event=Mock(),
                admin_auth=self.auth,
            )
        )

        response = TestClient(app).post("/admin/memcore/backfill")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["reason"], "admin_auth_required")
        engine.backfill_memcore_from_legacy_memory.assert_not_called()


class QQIngressAndOutboundAuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.channel = QQChannelRuntimeConfig(
            enabled=True,
            profile_ref="finance-qq",
            bot_id="123456",
            onebot_http_url="http://127.0.0.1:3001",
            webhook_secret="hook-secret",
            onebot_access_token="onebot-token",
            require_webhook_auth=True,
            require_self_id=True,
        )

    def _client(self, gateway: Mock) -> TestClient:
        metrics = Mock()
        app = FastAPI()
        app.include_router(
            build_qq_router(
                engine=Mock(),
                config_module=SimpleNamespace(QQ_BRIDGE_ENABLED=True),
                qq_gateway=gateway,
                runtime_metrics=metrics,
                logger=Mock(),
                log_event=Mock(),
                channel_config=self.channel,
            )
        )
        return TestClient(app)

    def test_missing_webhook_secret_rejects_before_json_or_gateway_state(self) -> None:
        gateway = Mock()
        response = self._client(gateway).post(
            "/api/qq/napcat/event",
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["reason"], "qq_webhook_auth_required")
        gateway.build_message_context.assert_not_called()

    def test_mismatched_self_id_rejects_before_gateway_state(self) -> None:
        gateway = Mock()
        response = self._client(gateway).post(
            "/api/qq/napcat/event",
            json={"post_type": "message", "self_id": "999999"},
            headers={"Authorization": "Bearer hook-secret"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["reason"], "qq_self_id_mismatch")
        gateway.build_message_context.assert_not_called()

    def test_matching_binding_reaches_gateway(self) -> None:
        gateway = Mock()
        gateway.build_message_context.return_value = QQMessageContext(
            False,
            "unsupported_message_type",
        )
        response = self._client(gateway).post(
            "/api/qq/napcat/event",
            json={"post_type": "message", "self_id": "123456"},
            headers={"X-Akane-Webhook-Secret": "hook-secret"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ignored")
        gateway.build_message_context.assert_called_once()

    def test_onebot_requests_carry_bearer_token(self) -> None:
        class FakeResponse:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {"status": "ok", "data": {}}

        gateway = NapCatQQGateway(channel_config=self.channel)
        context = QQMessageContext(
            True,
            "private_message",
            target_id=789,
            user_id=789,
            session_id="qq_pri_789",
            profile_user_id="qq_789",
        )
        with patch(
            "companion_v01.qq_gateway.requests.post",
            return_value=FakeResponse(),
        ) as mocked_post:
            result = gateway.send_reply(context, "hello")

        self.assertTrue(result["ok"])
        self.assertEqual(
            mocked_post.call_args.kwargs["headers"],
            {"Authorization": "Bearer onebot-token"},
        )

    def test_every_onebot_request_site_uses_bound_headers(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "companion_v01" / "qq_gateway.py").read_text(encoding="utf-8")
        request_sites = source.count("requests.get(") + source.count("requests.post(")
        self.assertGreater(request_sites, 0)
        self.assertEqual(request_sites, source.count("headers=self.onebot_headers"))

    def test_attachment_onebot_lookup_uses_same_bound_token(self) -> None:
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"status": "ok", "retcode": 0, "data": {}}

        with tempfile.TemporaryDirectory() as temp_dir:
            service = AttachmentIngestService(
                base_dir=Path(temp_dir),
                store=Mock(),
                attachment_service=Mock(),
                vision_service=None,
                background_tasks=Mock(),
                qq_channel_config=self.channel,
            )
            with patch(
                "companion_v01.attachment_ingest.requests.post",
                return_value=FakeResponse(),
            ) as mocked_post:
                service._copy_from_onebot_cache(
                    item={"kind": "image"},
                    payload={"file": "image-token"},
                    target_path=Path(temp_dir) / "target.png",
                    origin_name="target.png",
                )

        self.assertEqual(mocked_post.call_count, 2)
        for call in mocked_post.call_args_list:
            self.assertEqual(
                call.kwargs["headers"],
                {"Authorization": "Bearer onebot-token"},
            )


class InstanceDeploymentTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]

    def test_systemd_template_is_instance_parameterized(self) -> None:
        template = (self.root / "deploy" / "systemd" / "akane@.service.example").read_text(encoding="utf-8")
        self.assertIn("EnvironmentFile=/etc/akane/instances/%i.env", template)
        self.assertIn("Environment=AKANE_INSTANCE_ID=%i", template)
        self.assertIn("SyslogIdentifier=akane-%i", template)
        self.assertFalse((self.root / "deploy" / "systemd" / "akane.service.example").exists())

    def test_nginx_template_separates_ingress_and_management(self) -> None:
        template = (self.root / "deploy" / "nginx" / "akane.nginx.conf.example").read_text(encoding="utf-8")
        self.assertIn("upstream akane_finance_prod", template)
        self.assertIn("akane-finance-prod.access.log", template)
        self.assertIn("location = /api/qq/napcat/event", template)
        self.assertIn("location ^~ /control-center/", template)
        self.assertIn("location ^~ /admin/", template)
        self.assertGreaterEqual(template.count("deny all;"), 5)

    def test_vps_environment_binds_one_instance_root_port_and_secret_set(self) -> None:
        template = (self.root / "deploy" / "env" / ".env.vps.example").read_text(encoding="utf-8")
        for expected in (
            "AKANE_INSTANCE_ID=finance-prod",
            "AKANE_DATA_ROOT=/var/lib/akane/finance-prod",
            "PORT=10001",
            "AKANE_ADMIN_TOKEN=replace-with",
            "QQ_CHANNEL_PROFILE_REF=finance-qq",
            "QQ_BOT_QQ=replace-with-bot-qq-number",
            "QQ_WEBHOOK_SECRET=replace-with",
            "QQ_ONEBOT_ACCESS_TOKEN=replace-with",
        ):
            self.assertIn(expected, template)


class NamedInstanceApplicationSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]

    def _write_manifest(self, data_root: Path, *, qq_enabled: bool) -> None:
        manifest_dir = data_root / "instances" / "finance-prod"
        manifest_dir.mkdir(parents=True)
        profile_ref = "finance-qq" if qq_enabled else ""
        manifest_dir.joinpath("instance.toml").write_text(
            "\n".join(
                (
                    "schema_version = 1",
                    'instance_id = "finance-prod"',
                    'character_pack_id = "akane"',
                    "plugins = []",
                    "",
                    "[features]",
                    "care = false",
                    "",
                    "[channels.qq]",
                    f"enabled = {'true' if qq_enabled else 'false'}",
                    f'profile_ref = "{profile_ref}"',
                    "",
                )
            ),
            encoding="utf-8",
        )

    def _environment(self, data_root: Path) -> dict[str, str]:
        environment = dict(os.environ)
        environment.update(
            {
                "AKANE_DATA_ROOT": str(data_root),
                "AKANE_INSTANCE_ID": "finance-prod",
                "AKANE_ADMIN_TOKEN": "admin-secret",
                "EMBEDDING_PROVIDER": "hashed",
                "VISION_ENABLED": "false",
                "MEMORY_BACKEND": "legacy",
                "ENABLE_SEMANTIC_MEMORY": "false",
            }
        )
        return environment

    def test_named_disabled_qq_app_starts_with_minimal_public_health(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir)
            self._write_manifest(data_root, qq_enabled=False)
            environment = self._environment(data_root)
            environment["QQ_BRIDGE_ENABLED"] = "true"
            script = """
from fastapi.testclient import TestClient
from companion_v01.app import app, qq_gateway
assert qq_gateway is None
with TestClient(app) as client:
    assert client.get('/health').json() == {
        'status': 'ok',
        'instance_id': 'finance-prod',
        'root_binding': 'valid',
    }
    denied = client.post('/control-center/actions/noop', json={})
    assert denied.status_code == 401
"""
            completed = subprocess.run(
                [sys.executable, "-c", script],
                cwd=self.root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])

    def test_named_enabled_qq_missing_secret_fails_before_engine_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir)
            self._write_manifest(data_root, qq_enabled=True)
            environment = self._environment(data_root)
            environment.update(
                {
                    "QQ_CHANNEL_PROFILE_REF": "finance-qq",
                    "QQ_BOT_QQ": "123456",
                    "QQ_WEBHOOK_SECRET": "",
                    "QQ_ONEBOT_ACCESS_TOKEN": "",
                }
            )
            completed = subprocess.run(
                [sys.executable, "-c", "import companion_v01.app"],
                cwd=self.root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            database_path = data_root / "users_data" / "akane_memory_v01" / "akane_cloud.db"

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("qq_webhook_secret_required", completed.stderr)
            self.assertFalse(database_path.exists())


if __name__ == "__main__":
    unittest.main()
