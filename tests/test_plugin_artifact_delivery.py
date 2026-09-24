"""Actual worker/artifact handoff; QQ network boundaries are explicit doubles."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import textwrap
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from capcore import InvocationContext

from companion_v01.bot_runtime import _host_job_completion_request
from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.host_tool_jobs import _artifact_references
from companion_v01.plugin_api import ManagedArtifactDraft
from companion_v01.plugin_generation import PluginGenerationProcess
from companion_v01.plugin_generation_artifacts import GenerationArtifactOutboxSink, consume_generation_artifact
from companion_v01.plugin_managed_artifacts import (
    GeneratedFileManagedArtifactSink,
    ManagedArtifactError,
    normalize_managed_artifact_reference,
    validate_managed_artifact_draft,
)
from companion_v01.plugin_tool_bridge import PluginCapabilityToolHandler
from companion_v01.qq_gateway import NapCatQQGateway, QQChannelRuntimeConfig, QQMessageContext
from companion_v01.routes.qq import _hydrate_plugin_managed_artifact_events
from companion_v01.tool_runtime import ToolExecutionContext, ToolExecutionResult
from tests.test_plugin_resources import services

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = "test.audio-delivery"
CAPABILITY = PLUGIN + ".render.v1"


def write_plugin(root):
    site = root / "site"
    site.mkdir()
    (site / "audio_fixture.py").write_text(
        textwrap.dedent("""
        import io, wave
        from pathlib import Path
        from capcore import CapabilityDescriptor, CapabilityIOSlot, CapabilityResult, HealthStatus
        from akane_plugin import PluginManifest, ManagedArtifactDraft, ManagedArtifactPayload
        class Adapter:
            provider_id = "provider.test.audio-delivery"
            async def health(self):
                return HealthStatus(True, "ready")
            async def list_capabilities(self):
                return (CapabilityDescriptor(
                    id="test.audio-delivery.render.v1", display_name="Audio delivery test", short_hint="Test only.",
                    visible_in=("qq", "desktop"), prompt_exposed=True, risk="low", confirm="never",
                    effects=("filesystem",), trigger=None,
                    inputs=(CapabilityIOSlot("delivery_mode", "string"), CapabilityIOSlot("send_to_user", "boolean"),
                            CapabilityIOSlot("output_format", "string")),
                    outputs=(CapabilityIOSlot("file", "file", required=True, max_bytes=4096, delivery="generated_file"),), raw={},
                ),)
            async def invoke(self, capability_id, args, context):
                output = io.BytesIO()
                with wave.open(output, "wb") as audio:
                    audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                    audio.writeframes(b"\\x01\\x00" * 800)
                source = Path(__file__).parent / ("render." + args.get("output_format", "wav"))
                source.write_bytes(output.getvalue())
                return CapabilityResult(is_error=False, status="ok", content=ManagedArtifactPayload(
                    content={}, artifact=ManagedArtifactDraft.from_file(source, title="test-audio",
                        send_to_user=args.get("send_to_user", True),
                        delivery_mode=args.get("delivery_mode", "file"))))
            async def aclose(self): pass
        class Plugin:
            manifest = PluginManifest("test.audio-delivery", "0.1.0", 1, ("capability.prompt.invoke", "artifact.write"))
            def register(self, registrar): registrar.add_capability_adapter(Adapter())
        def create_plugin(): return Plugin()
    """),
        encoding="utf-8",
    )
    metadata = site / "audio_fixture-0.1.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: audio-fixture\nVersion: 0.1.0\n", encoding="utf-8")
    (metadata / "entry_points.txt").write_text(
        "[akane.plugins.v1]\ntest.audio-delivery = audio_fixture:create_plugin\n", encoding="utf-8"
    )
    return site


class ArtifactDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_worker_mode_survives_job_and_qq_handoff(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, _, files = services(root)
            process = PluginGenerationProcess(
                project_root=ROOT, site_dir=write_plugin(root), plugin_id=PLUGIN, work_dir=root / "worker"
            )
            process.bind_managed_artifact_sink(GeneratedFileManagedArtifactSink(files))
            await asyncio.to_thread(process.start)
            handler = PluginCapabilityToolHandler(
                capability_id=CAPABILITY,
                adapter=object(),
                descriptor=process.capability_descriptors[CAPABILITY],
                config_base_dir=root,
            )
            gateway = NapCatQQGateway(
                channel_config=QQChannelRuntimeConfig(
                    enabled=True,
                    profile_ref="test",
                    bot_id="10001",
                    onebot_http_url="http://127.0.0.1:9",
                    webhook_secret="",
                    onebot_access_token="",
                    require_webhook_auth=False,
                    require_self_id=True,
                )
            )
            context = QQMessageContext(
                should_respond=True,
                reason="test",
                target_id=10002,
                user_id=10002,
                session_id="session",
                profile_user_id="owner",
                clean_message="deliver test audio",
            )
            engine = SimpleNamespace(_get_generated_file_service=lambda: files)
            try:
                for mode, send, expected in (
                    ("voice", True, 1),
                    ("file", True, 1),
                    ("both", True, 2),
                    ("both", False, 0),
                ):
                    result = await process.invoke(
                        CAPABILITY,
                        {"delivery_mode": mode, "send_to_user": send},
                        context=InvocationContext("owner", "session", "qq_text"),
                    )
                    self.assertFalse(result.is_error, result.reason)
                    self.assertEqual(result.content["managed_artifacts"][0]["delivery_mode"], mode)
                    projected = handler._finalize_execution_result(
                        ToolExecutionResult(tool_type=CAPABILITY),
                        capability_result=result,
                        context=ToolExecutionContext("owner", "session", 1, {}, client_mode="qq_text"),
                    )
                    refs = _artifact_references(projected)
                    self.assertEqual(refs[0]["delivery_mode"], mode)
                    self.assertIs(refs[0]["send_to_user"], send)
                    store = HostJobStore(root / f"{mode}-{send}.db")
                    owner = HostJobOwner("owner", "session")
                    job_id = store.create(
                        owner=owner,
                        capability_source="tool",
                        capability_id=CAPABILITY,
                        payload={},
                        idempotency_key="test",
                        argument_fingerprint="test",
                    )["job_id"]
                    claim = store.claim(job_id, worker_id="test")
                    store.succeed(job_id, claim_token=claim["claim_token"], artifacts=refs)
                    request = _host_job_completion_request(store.get(job_id, owner=owner))
                    fields = dict(request.data)
                    self.assertEqual(fields["artifact_delivery_status"], "available_not_delivered")
                    if send:
                        self.assertEqual(fields["artifact_delivery_requests"], f"{refs[0]['handle']}={mode}")
                    else:
                        self.assertNotIn("artifact_delivery_requests", fields)
                    self.assertNotIn(str(root), request.message)
                    hydrated, failed = _hydrate_plugin_managed_artifact_events(
                        engine=engine, context=context, tool_events=projected.stream_events
                    )
                    self.assertFalse(failed)
                    response = SimpleNamespace(
                        raise_for_status=lambda: None, json=lambda: {"status": "ok", "retcode": 0}
                    )
                    with patch(
                        "companion_v01.onebot_transport.requests.Session.request", return_value=response
                    ) as transport:
                        sent = gateway.send_generated_files(context, hydrated)
                    self.assertTrue(sent["ok"], sent)
                    self.assertEqual(transport.call_count, expected)
                    if send:
                        actions = [call.args[1].rsplit("/", 1)[-1] for call in transport.call_args_list]
                        self.assertEqual(
                            actions,
                            ["send_private_msg"]
                            if mode == "voice"
                            else ["upload_private_file"]
                            if mode == "file"
                            else ["send_private_msg", "upload_private_file"],
                        )
                        if mode != "file":
                            segments = transport.call_args_list[0].kwargs["json"]["message"]
                            record = next(item for item in segments if item["type"] == "record")
                            real = Path(hydrated[0]["generated_file"]["absolute_path"]).resolve()
                            self.assertIn(record["data"]["file"], (str(real), real.as_uri()))
                    self.assertNotIn("absolute_path", str(projected.stream_events))
                    self.assertNotIn(str(root), str(sent))
                # Failed voice does not prevent the requested file branch; no fake all-success.
                hydrated[0]["send_to_user"] = True
                hydrated, failed = _hydrate_plugin_managed_artifact_events(
                    engine=engine, context=context, tool_events=hydrated
                )
                self.assertFalse(failed)
                with (
                    patch.object(gateway, "send_voice", return_value={"ok": False, "reason": "fixture_failure"}),
                    patch.object(gateway, "send_file", return_value={"ok": True}) as send_file,
                ):
                    failed = gateway.send_generated_files(context, hydrated)
                self.assertFalse(failed["ok"])
                send_file.assert_called_once()
                self.assertTrue(
                    files.resolve_generated_artifact(
                        profile_user_id="owner", session_id="session", target=refs[0]["handle"]
                    )
                )
            finally:
                await asyncio.to_thread(process.stop)

    async def test_modes_are_validated_bound_to_handoff_and_legacy_defaults_work(self):
        audio = ManagedArtifactDraft(
            data=b"audio", title="audio", output_format="wav", mime_type="audio/wav", delivery_mode="voice"
        )
        for draft, reason in (
            (replace(audio, delivery_mode="auto"), "delivery_invalid"),
            (replace(audio, delivery_mode=[]), "delivery_invalid"),
            (replace(audio, output_format="png", mime_type="image/png"), "voice_requires_audio"),
        ):
            with self.assertRaisesRegex(ManagedArtifactError, reason):
                validate_managed_artifact_draft(draft)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sink = GenerationArtifactOutboxSink(root)
            context = InvocationContext("owner", "session", "qq_text")
            ref = await sink.materialize(audio, context=context, capability_id=CAPABILITY)
            self.assertIsNone(
                normalize_managed_artifact_reference(
                    {**ref, "delivery_mode": "both"}, draft=audio, capability_id=CAPABILITY
                )
            )
            with self.assertRaisesRegex(ManagedArtifactError, "handoff_invalid"):
                consume_generation_artifact(root, {**ref, "delivery_mode": "both"}, capability_id=CAPABILITY)
            self.assertEqual(list(root.iterdir()), [])
            ref = await sink.materialize(
                replace(audio, delivery_mode="file"), context=context, capability_id=CAPABILITY
            )
            ref.pop("delivery_mode")
            metadata = next(root.glob("*.json"))
            value = json.loads(metadata.read_text(encoding="utf-8"))
            value.pop("delivery_mode")
            metadata.write_text(json.dumps(value), encoding="utf-8")
            staged = consume_generation_artifact(root, ref, capability_id=CAPABILITY)
            self.assertEqual(staged.draft.delivery_mode, "file")
            staged.cleanup()


if __name__ == "__main__":
    unittest.main()
