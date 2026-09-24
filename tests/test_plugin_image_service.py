"""Replace an installed image provider through public contracts and configuration."""

import asyncio
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest

from akane_plugin import PluginConnectionResult
from tests.dependency_supply_fixture import wheelhouse_for_plugins
from tests.image_plugin_harness import ImageHarness, ROOT, PLUGIN_ID, CAPABILITY_ID, SERVICE_CAPABILITY_ID
from tests import test_image_generation_plugin as image_acceptance
from plugins.akane_image_generation.tests.test_client import server, picture, encoded


ALTERNATE = "example.image-alternate"
ALTERNATE_METHOD = ALTERNATE + ".service.image_generation.v1.generate"


def alternative_source(root):
    """An independently staged provider release, with no host imports or edits.

    Reuse the actual HTTP image client to exercise the same full contract and
    cancellation semantics; the alternative chooses a different provider model.
    """
    source = root / "alternative"
    shutil.copytree(ROOT / "plugins/akane_image_generation", source,
                    ignore=shutil.ignore_patterns("__pycache__", "*.egg-info", "build", "dist"))
    manifest = source / "pyproject.toml"
    manifest.write_text(manifest.read_text(encoding="utf-8")
        .replace('name = "akane-image-generation"', 'name = "example-image-alternate"')
        .replace('"akane.image-generation" =', f'"{ALTERNATE}" ='), encoding="utf-8")
    module = source / "src/akane_image_generation/plugin.py"
    module.write_text(module.read_text(encoding="utf-8")
        .replace('PLUGIN_ID = "akane.image-generation"', f'PLUGIN_ID = "{ALTERNATE}"')
        .replace('    "capability.prompt.invoke",\n', '')
        .replace('return (descriptor(), service_descriptor())', 'return (service_descriptor(),)')
        .replace('model=connection.model,', 'model=connection.model + "-alternate",'), encoding="utf-8")
    return source


class ImageServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_installed_provider_switch_approval_receipt_and_single_image_registration(self):
        blue, red = picture(), picture("red")
        replies = ({"block": True, "wait_seconds": 30, "body": {"data": [{"b64_json": encoded(blue)}]}},
                   {"body": {"data": [{"b64_json": encoded(red)}]}})
        with tempfile.TemporaryDirectory() as directory, server(*replies) as (url, calls, waiting, release, _):
            root = Path(directory)
            connection_owners = []
            async def resolve(name, *, invocation):
                connection_owners.append(invocation.plugin_id)
                return PluginConnectionResult(True, "configured", base_url=url, api_key="fixture-key", model="fixture-model",
                    options={"allow_loopback_http": True, "transient_retry_count": 0})
            harness = await ImageHarness(
                root,
                SimpleNamespace(resolve=resolve),
                dependency_wheelhouse=wheelhouse_for_plugins("akane_image_generation"),
            ).start()
            pending = None
            binding = {"service_id": "image_generation", "version": 1, "plugin_id": PLUGIN_ID}
            try:
                await harness.install()
                self.assertTrue(harness.approve_test_profile()["ok"])
                harness.selections.save(harness.selections.load(), service_bindings=(binding,))
                staged = await harness.service.stage_source(source_path=str(alternative_source(root)))
                self.assertTrue(staged["ok"], staged)
                self.assertNotIn("capability.prompt.invoke", staged["permissions"])
                installed = await harness.service.install_stage(stage_id=staged["stage_id"],
                    approved_permissions=staged["permissions"])
                self.assertTrue(installed["ok"], installed)
                self.assertEqual(set(harness.handlers()), {CAPABILITY_ID})
                catalog, = harness.engine.plugin_capability_source.list_services("image_generation")
                self.assertEqual(catalog["selected_provider"], PLUGIN_ID)
                self.assertEqual(len(catalog["providers"]), 2)
                for provider in catalog["providers"]:
                    method, = provider["methods"]
                    self.assertIn("prompt", method["input_schema"]["required"])
                    self.assertEqual(method["output_schema"]["properties"]["output_count"]["type"], "integer")
                self.assertEqual(calls, [], "Preparation and health cannot perform provider business work")
                self.assertEqual(connection_owners, [])
                with harness.engine.plugin_capability_source.turn_scope():
                    pending = asyncio.create_task(harness.invoke(prompt="old provider image"))
                    self.assertTrue(await asyncio.to_thread(waiting.wait, 10))
                    harness.selections.save(harness.selections.load(), service_bindings=({**binding, "plugin_id": ALTERNATE},))
                    self.assertTrue((await harness.service.restart())["ok"])
                    # A service grant is checked against the selected implementation.
                    # The old tool's approval cannot silently authorize another provider.
                    denied = await harness.invoke(prompt="new provider image", send_to_user=True)
                    self.assertEqual(denied.capability_result.status, "approval_required", denied)
                    event, = [item for item in denied.stream_events if item["type"] == "capability_approval_required"]
                    self.assertEqual(event["capabilityId"], ALTERNATE_METHOD)
                    request_id = event["requestId"]
                    self.assertFalse(harness.approvals.get_request(profile_user_id="owner", session_id="other",
                                                                  request_id=request_id)["ok"])
                    self.assertTrue(harness.approvals.decide_request(profile_user_id="owner", request_id=request_id,
                                                                    payload={"decision": "approved"})["ok"])
                    self.assertEqual(len(calls), 1)
                    fresh = await harness.invoke(prompt="new provider image", send_to_user=True)
                    self.assertFalse(fresh.capability_result.is_error, fresh)
                    ready, = [item for item in fresh.stream_events if item["type"] == "generated_file_ready"]
                    self.assertTrue(ready["send_to_user"])
                    fresh_image = Path(harness.resolve(ready["generated_file"]["generated_handle"])["absolute_path"]).read_bytes()
                    self.assertEqual(fresh_image, red)
                    self.assertEqual(fresh.capability_result.content["managed_artifacts"][0]["created_by_tool"], ALTERNATE_METHOD)
                    self.assertEqual(json.loads(calls[1][2])["model"], "fixture-model-alternate")
                    release.set()
                    old = await asyncio.wait_for(pending, 10)
                    old_images = image_acceptance.ImageInstallationTests.images(self, harness, old, 1)
                    self.assertEqual(old_images[0][1], blue)
                    self.assertEqual(old.capability_result.content["managed_artifacts"][0]["created_by_tool"], SERVICE_CAPABILITY_ID)
                    self.assertEqual(json.loads(calls[0][2])["model"], "fixture-model")
                await harness.runtime._active.drain_retired()
                self.assertEqual(connection_owners, [PLUGIN_ID, ALTERNATE])
                rows = harness.files.store.list_generated_files(profile_user_id="owner", session_id="session", limit=100)
                self.assertEqual(len(rows), 2)
                self.assertTrue((await harness.service.set_enabled(plugin_id=ALTERNATE, enabled=False))["ok"])
                self.assertEqual(harness.handlers(), {})
                entry = next(item for item in harness.service.public_snapshot()["plugins"] if item["plugin_id"] == PLUGIN_ID)
                self.assertEqual(entry["runtime_status"], "waiting_dependency")
                self.assertEqual(entry["reason"], "service_provider_unavailable")
                self.assertEqual(len(calls), 2)
                self.assertEqual(Path(harness.resolve(old_images[0][0])["absolute_path"]).read_bytes(), blue)
                harness.selections.save(harness.selections.load(), service_bindings=(binding,))
                self.assertTrue((await harness.service.restart())["ok"])
                self.assertEqual(set(harness.handlers()), {CAPABILITY_ID})
                self.assertEqual(len(calls), 2)
            finally:
                release.set()
                if pending is not None and not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                await harness.close()
