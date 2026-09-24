"""Disposable subprocess fault points around the real TTS/VoiceCore journals."""
import argparse
import asyncio
import json
import os
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


async def plugins(root):
    from tests.image_plugin_harness import ImageHarness
    from companion_v01.plugin_connections import ModelServicePluginConnectionProvider
    source = SimpleNamespace(capability_config_base_dir=root, settings=None)
    harness = ImageHarness(root, ModelServicePluginConnectionProvider(source, SimpleNamespace()))
    harness.plugin_id = "akane.tts"
    harness.engine_factory = lambda source: SimpleNamespace(plugin_capability_source=source, tool_handlers={})
    harness.market_index = root / "market" / "index.json"
    await harness.start()
    harness.engine.desktop_pet_character_resources = SimpleNamespace(
        build_character_voice_preference=lambda _: {"provider": "gpt_sovits", "profileId": "benchmark"})
    return harness


async def run(args):
    from tests.test_voice_runtime_production import VoiceRuntimeProductionTests, _Adapter, _open_request, _commit_realtime_turn, VOICE_PLAYBACK_OUTPUT_MODE
    from companion_v01.tts_provider_runtime import ResolvedTTSClient
    from companion_v01.voice_runtime.durable_ports import FileVoiceAudioArtifactPort
    from companion_v01.plugin_managed_artifacts import GeneratedFileManagedArtifactSink

    root = args.root
    voice_root = root / args.stage
    record = voice_root / "fault.json"
    harness = await plugins(root)
    fixture = VoiceRuntimeProductionTests()
    manager = fixture._manager(voice_root)
    service = fixture._service(root=voice_root, manager=manager, adapter=_Adapter(),
        tts_client_resolver=lambda **identity: ResolvedTTSClient(harness.engine, **identity))
    worker = next(process for process in harness.runtime._active._active.processes if process.plugin_id == "akane.tts")
    hit = threading.Event()
    def fault(**values):
        hit.set()
        record.write_text(json.dumps({"stage": args.stage, "worker_pid": worker._process.pid, **values}), encoding="utf-8")
        if args.stage == "worker_before_artifact":
            worker._process.kill()
            raise RuntimeError("fixture_worker_lost_before_artifact")
        os._exit(73)
    original_put = FileVoiceAudioArtifactPort.put_audio
    def put(port, **values):
        if args.stage == "artifact_before_association":
            fault(origin=values.get("origin", {}))
        result = original_put(port, **values)
        if args.stage == "association_before_receipt":
            fault(origin=values.get("origin", {}), audio_artifact_ref=result.artifact_ref)
        return result
    original_materialize = GeneratedFileManagedArtifactSink.materialize
    async def materialize(sink, *positional, **values):
        if args.stage in {"provider_before_artifact", "worker_before_artifact"}:
            fault()
        return await original_materialize(sink, *positional, **values)
    request = replace(_open_request(output_mode=VOICE_PLAYBACK_OUTPUT_MODE,
        voice_turn_id="recovered-turn" if args.recover else "original-turn"),
        profile_user_id="benchmark", session_id="benchmark")
    try:
        if args.recover:
            resolved = await asyncio.to_thread(service.create_coordinator, request)
            await asyncio.to_thread(service.wait_idle, timeout=10)
            events = [event for host in service._hosts.values() for event in host._host.journal.load_events().events]
            output = {"status": resolved.status, "reason": resolved.reason,
                "ready": [dict(event.payload) for event in events if event.event_kind == "voice.tts.ready"],
                "played": sum(event.event_kind == "voice.playback.completed" for event in events),
                "outbound": bool(resolved.delivery_channel and resolved.delivery_channel.take_outbound()),
                "artifacts": len(harness.files.store.list_generated_files(profile_user_id="benchmark", session_id="benchmark", limit=100))}
            args.output.write_text(json.dumps(output), encoding="utf-8")
            return
        with patch.object(FileVoiceAudioArtifactPort, "put_audio", put), patch.object(GeneratedFileManagedArtifactSink, "materialize", materialize):
            resolved = await asyncio.to_thread(service.create_coordinator, request)
            assert resolved.status == "ready", resolved
            await _commit_realtime_turn(resolved.coordinator)
            assert await asyncio.to_thread(service.wait_idle, timeout=15)
            if args.stage == "worker_before_artifact":
                assert hit.is_set()
                return
            events = resolved.coordinator.bridge.host._host.journal.load_events().events
            ready = [event for event in events if event.event_kind == "voice.tts.ready"]
            assert len(ready) == 1, ready
            if args.stage == "played_without_receipt":
                channel = resolved.delivery_channel
                delivery = channel.take_outbound()
                assert delivery is not None
                assert channel.mark_sent(delivery.delivery_id).ok
                assert channel.acknowledge("client.playback.enqueued", {"delivery_id": delivery.delivery_id}).ok
                assert channel.acknowledge("client.playback.started", {"delivery_id": delivery.delivery_id, "resume_token": "fixture-resume"}).ok
                # The fixture client consumed it; deliberately lose its completion ACK.
                fault(audio_artifact_ref=ready[0].payload["audio_artifact_ref"], client_consumptions=1)
            fault(audio_artifact_ref=ready[0].payload["audio_artifact_ref"])
    finally:
        await asyncio.to_thread(service.close)
        manager.close()
        await harness.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--recover", action="store_true")
    parser.add_argument("--output", type=Path)
    asyncio.run(run(parser.parse_args()))
