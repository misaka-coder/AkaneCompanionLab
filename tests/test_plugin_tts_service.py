"""First-party TTS through managed installation and the actual worker boundary."""
import asyncio
import hashlib
import json
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from tests.image_plugin_harness import ImageHarness, ROOT
from tests.tts_service_benchmark import wav_bytes
from companion_v01.local_capability_config import (
    save_provider_config, save_voice_profile_config, save_capability_approval_mode,
)
from companion_v01.plugin_connections import ModelServicePluginConnectionProvider
from companion_v01.plugin_capability_calls import EnginePluginCapabilityProvider
from companion_v01.tool_runtime import ToolExecutionContext
from companion_v01.tts_provider_selection import GPT_SOVITS_PROVIDER_ID
from companion_v01.tts_service import synthesize_tts_service, TTSServiceError


class TTSServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_installed_gpt_service_reads_scoped_reference_and_registers_audio(self):
        with tempfile.TemporaryDirectory(prefix="tts-installed-") as temporary:
            root = Path(temporary)
            calls = []
            scopes = []
            blocked = threading.Event()
            release = threading.Event()
            state = {"block": False}
            audio = wav_bytes()

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *_args):
                    pass

                def do_POST(self):
                    body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    reference = Path(body.get("ref_audio_path", ""))
                    calls.append({**body, "reference_matches": reference.is_file() and reference.read_bytes() == audio})
                    if state["block"]:
                        blocked.set()
                        release.wait(10)
                    self.send_response(200)
                    response_audio = state.get("response_audio", audio)
                    self.send_header("Content-Type", state.get("media_type", "audio/wav"))
                    self.send_header("Content-Length", str(len(response_audio)))
                    self.end_headers()
                    self.wfile.write(response_audio)

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            source_engine = SimpleNamespace(capability_config_base_dir=root, settings=SimpleNamespace())
            class Connections(ModelServicePluginConnectionProvider):
                async def resolve(self, name, *, invocation):
                    scopes.append(invocation)
                    return await super().resolve(name, invocation=invocation)
            connection = Connections(source_engine, SimpleNamespace())
            harness = ImageHarness(root, connection, ROOT / "work/tts-migration-20260911/wheelhouse")
            harness.plugin_id = "akane.tts"
            import tomllib
            entries = tomllib.loads((ROOT / "plugins/market.toml").read_text(encoding="utf-8"))["plugins"]
            entry = next(item for item in entries if item["plugin_id"] == "akane.tts")
            manifest = root / "tts-market.toml"
            entry["source"] = "plugin-source"
            shutil.copytree(ROOT / "plugins/akane_tts", root / "plugin-source",
                ignore=shutil.ignore_patterns("__pycache__", "*.egg-info", "build"))
            manifest.write_text("schema_version = 1\n[[plugins]]\n" + "\n".join(
                f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in entry.items()), encoding="utf-8")
            harness.market_manifest = manifest
            try:
                await harness.start()
                broken = await self._stage_unactivatable(harness, root)
                first_failure = await harness.service.install_stage(stage_id=broken["stage_id"], approved_permissions=broken["permissions"])
                self.assertFalse(first_failure["ok"], first_failure)
                self.assertEqual(harness.runtime.status_snapshot()["plugin_count"], 0)
                tested = await harness.service.test_source(source_path=str(root / "plugin-source"))
                self.assertTrue(tested["ok"], tested)
                installed = await harness.install()
                self.assertTrue(installed["ok"], installed)
                capability_id = "akane.tts.service.tts.v1.synthesize"
                self.assertNotIn(capability_id, harness.handlers())
                saved = save_provider_config(base_dir=root, profile_user_id="owner", provider_id=GPT_SOVITS_PROVIDER_ID,
                    payload={"enabled": True, "endpoint": f"http://127.0.0.1:{server.server_port}"})
                self.assertTrue(saved["ok"], saved)
                reference = root / "reference.wav"
                reference.write_bytes(audio)
                saved = save_voice_profile_config(base_dir=root, profile_user_id="owner", voice_profile_id="voice_one",
                    payload={"enabled": True, "refAudioPath": str(reference), "promptText": "参考语音", "promptLang": "zh"})
                self.assertTrue(saved["ok"], saved)
                provider = EnginePluginCapabilityProvider(harness.engine)
                context = ToolExecutionContext("owner", "session", 1, {}, client_mode="desktop_pet", character_pack_id="character")
                arguments = {"text": "测试真实服务调用。", "voice": {"provider": GPT_SOVITS_PROVIDER_ID, "profile_id": "voice_one"}, "emotion": "happy"}
                denied = await provider.call_service("tts", "synthesize", arguments, context=context)
                self.assertTrue(denied.result.is_error, denied)
                self.assertEqual(calls, [])
                saved = save_capability_approval_mode(base_dir=root, profile_user_id="owner", capability_id=capability_id, mode="trusted_auto_allow")
                self.assertTrue(saved["ok"], saved)
                result = await provider.call_service("tts", "synthesize", arguments, context=context)
                self.assertFalse(result.result.is_error, result)
                self.assertEqual(result.origin["plugin_id"], "akane.tts")
                self.assertTrue(result.origin["generation_id"])
                self.assertEqual(len(calls), 1)
                self.assertTrue(calls[0]["reference_matches"])
                self.assertNotEqual(calls[0]["ref_audio_path"], str(reference))
                self.assertEqual(calls[0]["voice_profile_id"], "voice_one")
                artifacts = result.result.content["managed_artifacts"]
                self.assertEqual(len(artifacts), 1)
                resource = harness.resolve(artifacts[0]["generated_handle"])
                self.assertEqual(Path(resource["absolute_path"]).read_bytes(), audio)
                self.assertNotIn(str(reference), str(result))
                self.assertTrue(all(not scope.active and not scope.issued_resources for scope in scopes))
                expired = await harness.runtime._builder.resource_provider.open("voice-reference:any", invocation=scopes[-1])
                self.assertEqual(expired.reason, "resource_invocation_expired")
                self.assertIsNone(harness.files.resolve_input_resource(profile_user_id="other-user",
                    session_id="session", target=artifacts[0]["generated_handle"], timestamp=context.now_ts))
                self.assertIsNone(harness.files.resolve_input_resource(profile_user_id="owner",
                    session_id="other-session", target=artifacts[0]["generated_handle"], timestamp=context.now_ts))
                rotated = save_voice_profile_config(base_dir=root, profile_user_id="owner", voice_profile_id="voice_one",
                    payload={"enabled": True, "refAudioPath": str(reference), "promptText": "新参考语音", "promptLang": "zh",
                        "emotionVoiceMap": {"happy": {"refAudioPath": str(reference), "promptText": "快乐参考语音", "promptLang": "zh"}}})
                self.assertTrue(rotated["ok"], rotated)
                synthesized = await synthesize_tts_service(engine=harness.engine, text=arguments["text"],
                    voice=arguments["voice"], emotion="happy", context=context)
                self.assertEqual(synthesized.audio, audio)
                self.assertEqual(synthesized.emotion_voice_id, "happy")
                self.assertEqual(synthesized.origin["plugin_id"], "akane.tts")
                self.assertEqual(len(calls), 2)
                self.assertEqual(calls[-1]["prompt_text"], "快乐参考语音")
                self.assertNotEqual(synthesized.generated_handle, artifacts[0]["generated_handle"])
                before_temporary = harness.files.store.list_generated_files(profile_user_id="owner", session_id="session", limit=100)
                transient = await synthesize_tts_service(engine=harness.engine, text="临时播放音频。",
                    voice=arguments["voice"], emotion="happy", context=context, transient=True)
                self.assertTrue(transient.audio)
                self.assertEqual(transient.generated_handle, "")
                self.assertEqual(harness.files.store.list_generated_files(profile_user_id="owner", session_id="session", limit=100), before_temporary)

                from companion_v01.voice_runtime import AkaneVoiceTTSCommandExecutor, FileVoiceTextArtifactPort, FileVoiceAudioArtifactPort
                from companion_v01.tts_provider_runtime import ResolvedTTSClient
                from tests.test_voice_runtime_tts_executor import _command, _snapshot
                harness.engine.desktop_pet_character_resources = SimpleNamespace(
                    build_character_voice_preference=lambda pack: {"provider": "gpt_sovits", "profileId": "voice_one"})
                port_options = {"state_dir": root / "voice", "conversation_id": "conversation-1", "conversation_generation": 1}
                texts = FileVoiceTextArtifactPort(**port_options)
                audios = FileVoiceAudioArtifactPort(**port_options)
                command = _command()
                command["payload"]["text_artifact_ref"] = texts.put_text(artifact_key="first", text="真实队列音频。", media_type="text/plain").artifact_ref
                executor = AkaneVoiceTTSCommandExecutor(tts_client=ResolvedTTSClient(harness.engine, "owner", "session", "character"),
                    text_artifacts=texts, audio_artifacts=audios, conversation_id="conversation-1", conversation_generation=1)
                executed = await asyncio.to_thread(executor.execute, command, _snapshot())
                self.assertEqual(executed.status, "succeeded", executed)
                self.assertEqual(executed.observations[-1].payload["provider_id"], "akane.tts")
                persisted_ref = executed.observations[-1].payload["audio_artifact_ref"]
                persisted = FileVoiceAudioArtifactPort(**port_options).read_audio(persisted_ref)
                self.assertEqual(persisted.origin["plugin_id"], "akane.tts")
                self.assertNotIn("generated_handle", persisted.origin)
                replacement = AkaneVoiceTTSCommandExecutor(tts_client=SimpleNamespace(provider_id="new-provider"),
                    text_artifacts=texts, audio_artifacts=FileVoiceAudioArtifactPort(**port_options),
                    conversation_id="conversation-1", conversation_generation=1)
                count = len(calls)
                recovered = replacement.recover(command, _snapshot())
                self.assertEqual(recovered.observations[-1].payload["provider_id"], "akane.tts")
                self.assertEqual(recovered.observations[-1].payload["audio_artifact_ref"], persisted_ref)
                self.assertEqual(len(calls), count)
                state["block"] = True
                pending = asyncio.create_task(synthesize_tts_service(engine=harness.engine,
                    text=arguments["text"], voice=arguments["voice"], emotion="happy", context=context))
                self.assertTrue(await asyncio.to_thread(blocked.wait, 5))
                pending.cancel()
                await asyncio.sleep(.05)
                self.assertFalse(pending.done(), "Cancellation must drain the actual HTTP request")
                release.set()
                with self.assertRaises(TTSServiceError) as cancelled:
                    await asyncio.wait_for(pending, 5)
                self.assertIn(cancelled.exception.status, {"cancelled", "error"})
                state["block"] = False
                await self._verify_admin_switch(harness, root, reference, audio, calls, state, blocked, release)
                # Restore the first-party binding for the missing reference check.
                harness.selections.save_service_binding(service_id="tts", version=1, plugin_id="akane.tts",
                    expected_sha256=hashlib.sha256(harness.selections.path.read_bytes()).hexdigest())
                await harness.service.restart()
                from tests.tts_plugin_harness import verify_without_host
                await asyncio.to_thread(verify_without_host, harness, root)
                artifact_count = len(harness.files.store.list_generated_files(profile_user_id="owner", session_id="session", limit=100))
                for malformed, media_type in ((b"", "audio/wav"), (b"not-audio", "audio/wav"),
                                               (audio[:-20], "audio/wav"), (audio, "application/json")):
                    state["response_audio"], state["media_type"] = malformed, media_type
                    with self.assertRaises(TTSServiceError):
                        await synthesize_tts_service(engine=harness.engine, text="校验无效媒体", voice=arguments["voice"],
                            emotion="happy", context=context)
                state.pop("response_audio")
                state.pop("media_type")
                self.assertEqual(len(harness.files.store.list_generated_files(profile_user_id="owner", session_id="session", limit=100)), artifact_count)
                count = len(calls)
                reference.unlink()
                with self.assertRaises(TTSServiceError) as missing:
                    await synthesize_tts_service(engine=harness.engine, text=arguments["text"],
                        voice=arguments["voice"], emotion="happy", context=context)
                self.assertEqual(missing.exception.reason, "tts_reference_audio_unavailable")
                self.assertEqual(len(calls), count)
            finally:
                release.set()
                if hasattr(harness, "runtime"):
                    await harness.close()
                await asyncio.to_thread(server.shutdown)
                server.server_close()
                thread.join(timeout=3)

    async def _verify_admin_switch(self, harness, root, reference, first_audio, calls, state, blocked, release):
        import httpx
        from fastapi import FastAPI
        from companion_v01.routes.plugins import build_plugins_router
        from companion_v01.routes.voice import build_voice_router
        from companion_v01.routes.capabilities import build_capabilities_router
        from companion_v01.deployment_security import AdminWriteAuth
        from tests.test_petdesk_bridge import FakeRuntimeMetrics

        app = FastAPI()
        app.include_router(build_plugins_router(extension_management_service=harness.service,
            admin_auth=AdminWriteAuth("fixture-admin-token", True, False)))
        config = SimpleNamespace(DATA_DIR=root, WEB_OWNER_PROFILE_USER_ID="owner")
        app.include_router(build_voice_router(engine=harness.engine, config_module=config, tts_client=None,
            runtime_metrics=FakeRuntimeMetrics(), log_event=lambda *_args, **_kwargs: None))
        app.include_router(build_capabilities_router(engine=harness.engine, config_module=config,
            resolve_identity_from_query=lambda request: ("session", "owner")))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            denied = await client.get("/admin/plugins/status")
            self.assertEqual(denied.status_code, 401)
            client.headers["X-Akane-Admin-Token"] = "fixture-admin-token"
            selection_path = harness.selections.path
            cli_verified = False

            async def bind(plugin_id):
                nonlocal cli_verified
                if plugin_id == "example.tts-tone" and not cli_verified:
                    import subprocess, sys
                    command = [sys.executable, str(ROOT / "scripts/set_tts_service_binding.py"),
                        "--path", str(selection_path), "--instance-id", "image-test"]
                    reviewed = await asyncio.to_thread(subprocess.run, command, capture_output=True, text=True, timeout=30)
                    self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
                    saved = await asyncio.to_thread(subprocess.run, command + ["--plugin-id", plugin_id,
                        "--expected-sha256", json.loads(reviewed.stdout)["sha256"]],
                        capture_output=True, text=True, timeout=30)
                    self.assertEqual(saved.returncode, 0, saved.stderr)
                    result = json.loads(saved.stdout)
                    cli_verified = True
                else:
                    result = harness.selections.save_service_binding(service_id="tts", version=1, plugin_id=plugin_id,
                        expected_sha256=hashlib.sha256(selection_path.read_bytes()).hexdigest())
                self.assertFalse(result["activated"])
                return result

            await bind("akane.tts")
            self.assertEqual((await client.post("/admin/plugins/restart")).status_code, 200)
            payload = {"text": "用户入口调用。", "real_user_id": "owner", "session_id": "session",
                "voiceProvider": "gpt_sovits", "voiceProfileId": "voice_one"}
            before = await client.post("/tts", json=payload)
            self.assertEqual(before.status_code, 200, before.text)
            self.assertEqual(before.content, first_audio)
            self.assertEqual(before.headers["x-akane-tts-service-provider"], "akane.tts")
            release.clear()
            blocked.clear()
            state["block"] = True
            revoked_a = asyncio.create_task(client.post("/tts", json=payload))
            self.assertTrue(await asyncio.to_thread(blocked.wait, 5))
            state["block"] = False
            disabled_a = await client.post("/admin/plugins/akane.tts/enabled", json={"enabled": False})
            self.assertTrue(disabled_a.json()["ok"], disabled_a.text)
            release.set()
            discarded = await asyncio.wait_for(revoked_a, 5)
            self.assertEqual(discarded.status_code, 503, discarded.text)
            self.assertEqual(discarded.headers["x-akane-tts-provider"], "")
            enabled_a = await client.post("/admin/plugins/akane.tts/enabled", json={"enabled": True})
            self.assertTrue(enabled_a.json()["ok"], enabled_a.text)
            # Hold the actual managed-media read after the worker has returned;
            # revocation must still withdraw delivery after scope cleanup.
            from unittest.mock import patch
            from companion_v01.tts_service import _read_audio
            reading, finish_read = threading.Event(), threading.Event()
            def delayed_read(*args, **kwargs):
                audio = _read_audio(*args, **kwargs)
                reading.set()
                if not finish_read.wait(15):
                    raise RuntimeError("fixture_read_timeout")
                return audio
            with patch("companion_v01.tts_service._read_audio", delayed_read):
                reading_a = asyncio.create_task(client.post("/tts", json=payload))
                try:
                    self.assertTrue(await asyncio.to_thread(reading.wait, 5))
                    disabled_a = await client.post("/admin/plugins/akane.tts/enabled", json={"enabled": False})
                    self.assertTrue(disabled_a.json()["ok"], disabled_a.text)
                finally:
                    finish_read.set()
                discarded_read = await asyncio.wait_for(reading_a, 5)
                self.assertEqual(discarded_read.status_code, 503, discarded_read.text)
            enabled_a = await client.post("/admin/plugins/akane.tts/enabled", json={"enabled": True})
            self.assertTrue(enabled_a.json()["ok"], enabled_a.text)
            old_status = (await client.get("/admin/plugins/status")).json()
            broken = await self._stage_unactivatable(harness, root)
            bad_upgrade = await client.post(f'/admin/plugins/stages/{broken["stage_id"]}/install',
                json={"approved_permissions": broken["permissions"]})
            self.assertFalse(bad_upgrade.json()["ok"], bad_upgrade.text)
            restored_status = (await client.get("/admin/plugins/status")).json()
            self.assertGreaterEqual(restored_status["generation"], old_status["generation"])
            self.assertEqual(restored_status["status"], "active")
            last_good = await client.post("/tts", json=payload)
            self.assertEqual(last_good.status_code, 200, last_good.text)
            self.assertEqual(last_good.content, first_audio)
            preview = await client.post(f"/capabilities/providers/{GPT_SOVITS_PROVIDER_ID}/tts-test", json={
                "voiceProfileId": "voice_one", "refAudioPath": str(reference), "promptText": "未保存预览文本", "promptLang": "zh"})
            self.assertTrue(preview.json()["ok"], preview.text)
            self.assertEqual(calls[-1]["prompt_text"], "未保存预览文本")
            tested = await harness.service.test_source(source_path=str(ROOT / "examples/plugins/akane_sdk_tts_tone"))
            self.assertTrue(tested["ok"], tested)
            stage = await client.post("/admin/plugins/stages/source", json={"source_path": str(ROOT / "examples/plugins/akane_sdk_tts_tone")})
            staged = stage.json()
            self.assertTrue(staged["ok"], staged)
            self.assertFalse(any(permission.startswith("connection.") for permission in staged["permissions"]))
            install = await client.post(f'/admin/plugins/stages/{staged["stage_id"]}/install',
                json={"approved_permissions": staged["permissions"]})
            self.assertTrue(install.json()["ok"], install.text)
            release.clear()
            blocked.clear()
            state["block"] = True
            active_a = asyncio.create_task(client.post("/tts", json=payload))
            self.assertTrue(await asyncio.to_thread(blocked.wait, 5))
            state["block"] = False
            await bind("example.tts-tone")
            # Saving the file is not publication; current generation still uses A.
            still_old = await client.post("/tts", json=payload)
            self.assertEqual(still_old.status_code, 200, still_old.text)
            self.assertEqual(still_old.content, first_audio)
            old_generation = (await client.get("/admin/plugins/status")).json()["generation"]
            restarted = await client.post("/admin/plugins/restart")
            self.assertEqual(restarted.status_code, 200, restarted.text)
            status = (await client.get("/admin/plugins/status")).json()
            self.assertGreater(status["generation"], old_generation)
            self.assertFalse(active_a.done(), "Hot reload must retain the running A scope")
            release.set()
            completed_a = await asyncio.wait_for(active_a, 5)
            self.assertEqual(completed_a.status_code, 200, completed_a.text)
            self.assertEqual(completed_a.content, first_audio)
            self.assertEqual(completed_a.headers["x-akane-tts-service-provider"], "akane.tts")
            self.assertNotEqual(completed_a.headers["x-akane-tts-service-generation"],
                                before.headers["x-akane-tts-service-generation"])
            unavailable = await client.post("/tts", json=payload)
            self.assertEqual(unavailable.status_code, 503, unavailable.text)
            capability_id = "example.tts-tone.service.tts.v1.synthesize"
            save_capability_approval_mode(base_dir=root, profile_user_id="owner", capability_id=capability_id, mode="trusted_auto_allow")
            # Arbitrary valid voice identifiers reach the independent provider.
            fresh = await client.post("/tts", json={**payload, "voiceProvider": "example.custom-voice"})
            self.assertEqual(fresh.status_code, 200, fresh.text)
            self.assertNotEqual(fresh.content, first_audio)
            self.assertEqual(fresh.headers["x-akane-tts-service-provider"], "example.tts-tone")
            await self._verify_queued_switch(harness, root, client, bind, calls)
            reviewed = selection_path.read_bytes()
            with self.assertRaises(ValueError):
                harness.selections.save_service_binding(service_id="tts", version=1, plugin_id="../invalid",
                    expected_sha256=hashlib.sha256(reviewed).hexdigest())
            self.assertEqual(selection_path.read_bytes(), reviewed)
            with self.assertRaises(ValueError):
                harness.selections.save_service_binding(service_id="tts", version=1, plugin_id="akane.tts", expected_sha256="0" * 64)
            self.assertEqual(selection_path.read_bytes(), reviewed)
            await bind("example.missing")
            await client.post("/admin/plugins/restart")
            missing = await client.post("/tts", json=payload)
            self.assertEqual(missing.status_code, 503, missing.text)
            await bind("example.tts-tone")
            self.assertEqual((await client.post("/admin/plugins/restart")).status_code, 200)
            disabled = await client.post("/admin/plugins/example.tts-tone/enabled", json={"enabled": False})
            self.assertTrue(disabled.json()["ok"], disabled.text)
            self.assertEqual((await client.post("/tts", json=payload)).status_code, 503)
            enabled = await client.post("/admin/plugins/example.tts-tone/enabled", json={"enabled": True})
            self.assertTrue(enabled.json()["ok"], enabled.text)
            self.assertEqual((await client.post("/tts", json=payload)).status_code, 200)

    async def _stage_unactivatable(self, harness, root):
        source = root / "unactivatable-tts"
        if not source.exists():
            shutil.copytree(root / "plugin-source", source)
            module = source / "src/akane_tts/plugin.py"
            code = module.read_text(encoding="utf-8")
            code = code.replace('    async def health(self):',
                '    async def health(self):\n        import sys\n        if "--prepare-only" in sys.argv:\n            return HealthStatus(False, "unavailable", "fixture_tts_activation_failed")')
            module.write_text(code.replace('"0.1.0"', '"0.1.1"'), encoding="utf-8")
            project = source / "pyproject.toml"
            project.write_text(project.read_text(encoding="utf-8").replace('"0.1.0"', '"0.1.1"'), encoding="utf-8")
        stage = await harness.service.stage_source(source_path=str(source))
        self.assertTrue(stage["ok"], stage)
        return stage

    async def _verify_queued_switch(self, harness, root, admin, bind, calls):
        from tests.test_voice_runtime_production import (
            VoiceRuntimeProductionTests, _Adapter, _AheadOfTTSThinkingEngine,
            _open_request, _commit_realtime_turn, VOICE_PLAYBACK_OUTPUT_MODE,
        )
        from companion_v01.tts_provider_runtime import ResolvedTTSClient
        from dataclasses import replace

        fixture = VoiceRuntimeProductionTests()
        for cancelled in (False, True):
            await bind("akane.tts")
            self.assertEqual((await admin.post("/admin/plugins/restart")).status_code, 200)
            voice_root = root / ("queue-cancel" if cancelled else "queue-switch")
            manager = fixture._manager(voice_root)
            thinking = _AheadOfTTSThinkingEngine(manager)
            thinking.segments = ("第一段排队。", "第二段排队。", "第三段等待容量。")
            service = fixture._service(root=voice_root, manager=manager, adapter=_Adapter(), engine=thinking,
                tts_client_resolver=lambda **identity: ResolvedTTSClient(harness.engine, **identity))
            entered, release = threading.Event(), threading.Event()
            def barrier():
                entered.set()
                release.wait(20)
            service._background_tasks.submit(lane="voice-tts", name="fixture_barrier", fn=barrier)
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                request = replace(_open_request(output_mode=VOICE_PLAYBACK_OUTPUT_MODE), profile_user_id="owner")
                resolved = await asyncio.to_thread(service.create_coordinator, request)
                self.assertEqual(resolved.status, "ready", resolved)
                await _commit_realtime_turn(resolved.coordinator)
                self.assertTrue(await asyncio.to_thread(thinking.stream_consumed.wait, 5))
                host = resolved.coordinator.bridge.host
                deadline = asyncio.get_running_loop().time() + 3
                while not any(command.command_kind == "start_tts" for command in host.snapshot.pending_commands.values()):
                    self.assertLess(asyncio.get_running_loop().time(), deadline)
                    await asyncio.sleep(.01)
                before = len(calls)
                await bind("example.tts-tone")
                self.assertEqual((await admin.post("/admin/plugins/restart")).status_code, 200)
                if cancelled:
                    result = await asyncio.to_thread(resolved.coordinator.cancel_response, reason="character_changed")
                    self.assertEqual(result.status, "accepted", result)
                release.set()
                self.assertTrue(await asyncio.to_thread(service.wait_idle, timeout=10))
                self.assertEqual(len(calls), before, "Queued commands must never invoke old A")
                events = host._host.journal.load_events().events
                ready = [event for event in events if event.event_kind == "voice.tts.ready"]
                if cancelled:
                    self.assertEqual(ready, [])
                    self.assertIsNone(resolved.delivery_channel.take_outbound())
                else:
                    self.assertEqual(len(ready), 3)
                    self.assertTrue(all(event.payload["provider_id"] == "example.tts-tone" for event in ready))
                    self.assertEqual(len({event.payload["provider_generation"] for event in ready}), 1)
            finally:
                release.set()
                await asyncio.to_thread(service.close)
                manager.close()
