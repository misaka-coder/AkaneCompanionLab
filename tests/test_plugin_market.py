from __future__ import annotations

import asyncio
import copy
import hashlib
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from fastapi import FastAPI

from companion_v01.extension_management import ExtensionManagementService
from companion_v01.plugin_installation import PluginInstallationError
from companion_v01.plugin_market import MAX_INDEX_BYTES, StaticPluginMarket, parse_index
from companion_v01.routes.plugins import build_plugins_router


PLUGIN_ID = "akane.media-convert"
WHEEL_BYTES = b"test-archive-bytes"
DIGEST = hashlib.sha256(WHEEL_BYTES).hexdigest()


def catalog():
    return {
        "schema_version": 1,
        "plugins": [
            dict(
                plugin_id=PLUGIN_ID,
                version="0.1.0",
                display_name="Media converter",
                summary="Convert current media",
                sha256=DIGEST,
                wheel="releases/media-0.1.0-py3-none-any.whl",
                size_bytes=len(WHEEL_BYTES),
                permissions=["artifact.write"],
                requirements=["FFmpeg and FFprobe"],
            )
        ],
    }


def write_catalog(root, data=None):
    source = root / "index.json"
    source.write_text(json.dumps(data or catalog()), encoding="utf-8")
    wheel = root / catalog()["plugins"][0]["wheel"]
    wheel.parent.mkdir(exist_ok=True)
    wheel.write_bytes(WHEEL_BYTES)
    return source


def store():
    result = Mock()
    result.stage_wheel.return_value = dict(
        ok=True,
        status="staged",
        stage_id="a" * 32,
        plugin_id=PLUGIN_ID,
        version="0.1.0",
        permissions=["artifact.write"],
        digest=DIGEST,
    )
    return result


class MarketTests(unittest.TestCase):
    def test_parse_rejects_bad_shape_paths_duplicate_ids_and_excessive_sizes(self):
        for changes in (
            {"wheel": "../evil.whl"},
            {"wheel": "https://evil.example/evil.whl"},
            {"wheel": "/evil.whl"},
            {"wheel": "a/../../evil.whl"},
            {"wheel": "a\\evil.whl"},
            {"wheel": "a//evil.whl"},
            {"sha256": ""},
            {"size_bytes": True},
            {"size_bytes": 2**40},
            {"permissions": ["bad permission"]},
            {"requirements": "not-a-list"},
            {"display_name": "bad\nname"},
        ):
            data = catalog()
            data["plugins"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(PluginInstallationError):
                parse_index(json.dumps(data).encode())
        data = catalog()
        data["plugins"].append(copy.deepcopy(data["plugins"][0]))
        with self.assertRaises(PluginInstallationError):
            parse_index(json.dumps(data).encode())
        with self.assertRaises(PluginInstallationError):
            parse_index(b" " * (MAX_INDEX_BYTES + 1))

    def test_browse_does_not_import_or_stage_and_only_projects_public_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            market = StaticPluginMarket(write_catalog(root))
            result = market.browse()
            self.assertTrue(result["ok"])
            self.assertNotIn("wheel", result["plugins"][0])
            self.assertNotIn(str(root), str(result))
            self.assertEqual(result["plugins"][0]["requirements"], ["FFmpeg and FFprobe"])

    def test_verified_bytes_enter_only_existing_stager_and_temp_download_is_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            market = StaticPluginMarket(write_catalog(Path(temporary)))
            artifacts = store()
            observed = []

            def stage(path):
                self.assertEqual(path.read_bytes(), WHEEL_BYTES)
                observed.append(path)
                return artifacts.stage_wheel.return_value

            artifacts.stage_wheel.side_effect = stage
            result = market.stage(artifacts, plugin_id=PLUGIN_ID, digest=DIGEST)
            self.assertTrue(result["ok"])
            artifacts.stage_wheel.assert_called_once()
            self.assertFalse(observed[0].exists())
            artifacts.discard_stage.assert_not_called()

    def test_tampered_download_or_changed_selection_never_executes_probe(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_catalog(root)
            market, artifacts = StaticPluginMarket(source), store()
            for contents, reason in (
                (b"x", "market_wheel_size_mismatch"),
                (b"x" * len(WHEEL_BYTES), "market_wheel_digest_mismatch"),
            ):
                (root / catalog()["plugins"][0]["wheel"]).write_bytes(contents)
                with self.assertRaises(PluginInstallationError) as raised:
                    market.stage(artifacts, plugin_id=PLUGIN_ID, digest=DIGEST)
                self.assertEqual(raised.exception.reason, reason)
            with self.assertRaises(PluginInstallationError) as raised:
                market.stage(artifacts, plugin_id=PLUGIN_ID, digest="b" * 64)
            self.assertEqual(raised.exception.reason, "market_selection_changed")
            artifacts.stage_wheel.assert_not_called()

    def test_manifest_mismatch_discards_existing_stage_without_publishing(self):
        with tempfile.TemporaryDirectory() as temporary:
            market = StaticPluginMarket(write_catalog(Path(temporary)))
            artifacts = store()
            artifacts.stage_wheel.return_value["permissions"] = ["network.write"]
            with self.assertRaises(PluginInstallationError) as raised:
                market.stage(artifacts, plugin_id=PLUGIN_ID, digest=DIGEST)
            self.assertEqual(raised.exception.reason, "market_manifest_mismatch")
            artifacts.discard_stage.assert_called_once_with("a" * 32)
            artifacts.publish_stage.assert_not_called()

    def test_https_download_and_invalid_sources(self):
        opener = Mock()
        opener.open.side_effect = [io.BytesIO(json.dumps(catalog()).encode()), io.BytesIO(WHEEL_BYTES)]
        with patch("companion_v01.plugin_market.build_opener", return_value=opener):
            market = StaticPluginMarket("https://market.example/releases/index.json")
            market.stage(store(), plugin_id=PLUGIN_ID, digest=DIGEST)
        urls = [call.args[0].full_url for call in opener.open.call_args_list]
        self.assertEqual(
            urls,
            [
                "https://market.example/releases/index.json",
                "https://market.example/releases/releases/media-0.1.0-py3-none-any.whl",
            ],
        )
        for source in (
            "http://insecure.example/index.json",
            "https://user:secret@example.com/index.json",
            "https://example.com/index.json?token=secret",
        ):
            with self.assertRaises(PluginInstallationError) as raised:
                StaticPluginMarket(source).browse()
            self.assertEqual(raised.exception.reason, "market_source_invalid")


class MarketServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_waits_for_probe_and_discards_candidate(self):
        entered, release = threading.Event(), threading.Event()
        artifacts, market = store(), Mock()

        def stage(*args, **kwargs):
            entered.set()
            release.wait(5)
            return artifacts.stage_wheel.return_value

        market.stage.side_effect = stage
        service = ExtensionManagementService(
            plugin_runtime=Mock(), selection_store=Mock(), artifact_store=artifacts, market=market
        )
        task = asyncio.create_task(service.stage_market(plugin_id=PLUGIN_ID, digest=DIGEST))
        await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        artifacts.discard_stage.assert_called_once_with("a" * 32)
        self.assertFalse(service._operation_lock.locked())

    async def test_routes_browse_without_mutation_and_require_admin_for_stage(self):
        with tempfile.TemporaryDirectory() as temporary:
            artifacts = store()
            service = ExtensionManagementService(
                plugin_runtime=Mock(),
                selection_store=Mock(),
                artifact_store=artifacts,
                market=StaticPluginMarket(write_catalog(Path(temporary))),
            )
            service.public_snapshot = lambda: {"plugins": []}
            app = FastAPI()
            app.include_router(build_plugins_router(extension_management_service=service))
            for peer, allowed in (("127.0.0.1", True), ("203.0.113.1", False)):
                transport = httpx.ASGITransport(app=app, client=(peer, 1))
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    catalog_response = await client.get("/plugins/market")
                    self.assertEqual(catalog_response.status_code, 200)
                    response = await client.post(f"/admin/plugins/market/{PLUGIN_ID}/stage", json={"digest": DIGEST})
                    self.assertEqual(response.status_code, 200 if allowed else 403)
            artifacts.stage_wheel.assert_called_once()
