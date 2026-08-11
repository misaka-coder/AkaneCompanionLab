"""M68 focused tests: media-inspect-convert Skill over exec_run vs legacy tools.

Covers the "Skill + Shell must replace dedicated media tools without loss" slice:
  1. QQ Shell off  -> legacy inspect/convert stay selected.
  2. QQ Shell on   -> legacy tools leave selection/schema.
  3. Desktop execution on -> legacy tools leave selection/schema.
  4. Provider readiness fluctuation never reintroduces the two tools.
  5. media-inspect-convert is visible in the bundled Skill catalog.
  6. Skill body is a real execution loop and contains no absolute path.
  7/8. inspect = load_skill -> exec_run; convert = load_skill -> exec_run -> send_file.
  12/13. failed ffmpeg produces no fake handle; no output_globs means no artifact claim.
  16. exec followups / skill body do not leak host absolute paths.

Items 9/10/11/14/15 are covered by the existing suites in
tests/test_execution_resources.py (staging, gen_* registration, exec_status
single registration, no-artifact-on-failure) and tests/test_memcore_integration.py
(tool-round retention and reloadable card via open_memory for exec_run), both
listed in the focused verification commands.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from array import array
from pathlib import Path

from companion_v01.attachment_inbox import AttachmentInboxService
from companion_v01.capability_registry import (
    CapabilityRegistry,
    CapabilitySnapshot,
    ServerLocalOfferIndex,
    MEDIA_WORKBENCH_SHELL_OVERLAP_TOOL_NAMES,
)
from companion_v01.client_protocol import ClientMode
from companion_v01.execution_resources import ExecutionResourceBridge
from companion_v01.execution_run import ExecutionRunOwner
from companion_v01.execution_specs import (
    ARTIFACT_STATUS_NOT_REGISTERED,
    ARTIFACT_STATUS_NOT_REQUESTED,
    ARTIFACT_STATUS_REGISTERED,
    EXEC_STATUS_COMPLETED,
    EXEC_STATUS_FAILED,
)
from companion_v01.generated_files import GeneratedFileService
from companion_v01.local_capability_config import save_approval_policy_config
from companion_v01.native_tool_schema import build_openai_native_tool_specs
from companion_v01.skill_runtime import SkillRegistry, SKILL_BUNDLED_MOUNT
from companion_v01.store import MemoryStore
from companion_v01.tool_handlers.core import ToolExecutionContext
from companion_v01.tool_handlers.execution import ExecRunToolHandler

ROOT = Path(__file__).resolve().parents[1]

SKILL_NAME = "media-inspect-convert"

_FFMPEG = shutil.which("ffmpeg")
_FFPROBE = shutil.which("ffprobe")


def _media_snapshot(
    mode: ClientMode,
    *,
    execution_enabled: bool = False,
    execution_qq_enabled: bool = False,
) -> CapabilitySnapshot:
    return CapabilitySnapshot(
        client_mode=mode,
        has_media_attachment=True,
        has_media_generated_file=True,
        execution_enabled=execution_enabled,
        execution_qq_enabled=execution_qq_enabled,
    )


class _ProbeHandler:
    def __init__(self, name: str, *, ready: bool) -> None:
        self.tool_type = name
        self.ready = ready

    def capability_status(self, **_kwargs):
        return {"enabled": self.ready, "status": "ready" if self.ready else "unavailable"}


def _native_bytes_from_names(handlers: dict, tool_names: tuple[str, ...]) -> bytes:
    specs = build_openai_native_tool_specs(handlers, allowed_tool_names=set(tool_names))
    return json.dumps([dict(item) for item in specs], ensure_ascii=False, sort_keys=False).encode("utf-8")


class _SpecHandler:
    def __init__(self, spec):
        self.tool_type = spec.capability_id
        self._spec = spec

    def tool_spec(self):
        return self._spec


class MediaSkillCapabilityProfileTests(unittest.TestCase):
    """Items 1-4: capability profile selection depends only on frozen host flags."""

    def _handlers(self):
        from companion_v01.capability_registry import (
            CONVERT_MEDIA_FILE_TOOL_SPEC,
            INSPECT_MEDIA_INFO_TOOL_SPEC,
        )

        return {
            INSPECT_MEDIA_INFO_TOOL_SPEC.capability_id: _SpecHandler(INSPECT_MEDIA_INFO_TOOL_SPEC),
            CONVERT_MEDIA_FILE_TOOL_SPEC.capability_id: _SpecHandler(CONVERT_MEDIA_FILE_TOOL_SPEC),
        }

    def test_qq_shell_off_keeps_legacy_media_tools(self) -> None:
        selection = CapabilityRegistry().select(
            _media_snapshot(ClientMode.QQ_TEXT, execution_enabled=True, execution_qq_enabled=False)
        )
        self.assertTrue(set(MEDIA_WORKBENCH_SHELL_OVERLAP_TOOL_NAMES).issubset(selection.tool_names))
        self.assertTrue(set(MEDIA_WORKBENCH_SHELL_OVERLAP_TOOL_NAMES).issubset(selection.schema_tool_names))

    def test_qq_shell_on_drops_overlap_tools_from_selection_and_schema(self) -> None:
        selection = CapabilityRegistry().select(
            _media_snapshot(ClientMode.QQ_TEXT, execution_enabled=True, execution_qq_enabled=True)
        )
        self.assertFalse(set(MEDIA_WORKBENCH_SHELL_OVERLAP_TOOL_NAMES).intersection(selection.tool_names))
        self.assertFalse(set(MEDIA_WORKBENCH_SHELL_OVERLAP_TOOL_NAMES).intersection(selection.schema_tool_names))
        self.assertTrue({"exec_run", "exec_status", "exec_cancel", "manage_skill"}.issubset(selection.tool_names))

    def test_desktop_execution_on_drops_overlap_tools(self) -> None:
        selection = CapabilityRegistry().select(_media_snapshot(ClientMode.DESKTOP_PET, execution_enabled=True))
        self.assertFalse(set(MEDIA_WORKBENCH_SHELL_OVERLAP_TOOL_NAMES).intersection(selection.tool_names))
        self.assertFalse(set(MEDIA_WORKBENCH_SHELL_OVERLAP_TOOL_NAMES).intersection(selection.schema_tool_names))
        self.assertIn("exec_run", selection.tool_names)

    def test_desktop_execution_off_keeps_legacy_media_tools(self) -> None:
        selection = CapabilityRegistry().select(_media_snapshot(ClientMode.DESKTOP_PET, execution_enabled=False))
        self.assertTrue(set(MEDIA_WORKBENCH_SHELL_OVERLAP_TOOL_NAMES).issubset(selection.tool_names))

    def test_provider_readiness_fluctuation_does_not_reintroduce_overlap_tools(self) -> None:
        index = ServerLocalOfferIndex()
        handlers = self._handlers()
        index.replace_handlers(
            {
                name: _ProbeHandler(name, ready=True)
                for name in MEDIA_WORKBENCH_SHELL_OVERLAP_TOOL_NAMES
            }
        )
        registry = CapabilityRegistry(server_offer_index=index)
        for ready in (True, False):
            for tool_name in MEDIA_WORKBENCH_SHELL_OVERLAP_TOOL_NAMES:
                index.replace_handlers(
                    {name: _ProbeHandler(name, ready=ready) for name in MEDIA_WORKBENCH_SHELL_OVERLAP_TOOL_NAMES}
                )
                selection = registry.select(
                    _media_snapshot(ClientMode.DESKTOP_PET, execution_enabled=True),
                    profile_user_id="alice",
                    session_id="s1",
                )
                self.assertNotIn(tool_name, selection.tool_names)
                self.assertNotIn(tool_name, selection.schema_tool_names)

    def test_same_profile_native_schema_is_byte_identical(self) -> None:
        registry = CapabilityRegistry()
        handlers = self._handlers()
        for name, snapshot in (
            ("qq_shell_on", _media_snapshot(ClientMode.QQ_TEXT, execution_enabled=True, execution_qq_enabled=True)),
            ("desktop_on", _media_snapshot(ClientMode.DESKTOP_PET, execution_enabled=True)),
            ("qq_shell_off", _media_snapshot(ClientMode.QQ_TEXT, execution_enabled=True, execution_qq_enabled=False)),
        ):
            first = registry.select(snapshot)
            second = registry.select(snapshot)
            self.assertEqual(first.schema_tool_names, second.schema_tool_names)
            self.assertEqual(
                _native_bytes_from_names(handlers, first.schema_tool_names),
                _native_bytes_from_names(handlers, second.schema_tool_names),
            )

    def test_shell_on_schema_no_longer_projects_the_two_media_tools(self) -> None:
        registry = CapabilityRegistry()
        selection = registry.select(_media_snapshot(ClientMode.DESKTOP_PET, execution_enabled=True))
        native = _native_bytes_from_names(self._handlers(), selection.schema_tool_names)
        self.assertNotIn(b'"inspect_media_info"', native)
        self.assertNotIn(b'"convert_media_file"', native)


class MediaSkillCatalogTests(unittest.TestCase):
    """Items 5-6: the bundled skill exists and is a real, leak-free loop."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.registry = SkillRegistry(
            bundled_root=ROOT / "skills",
            managed_root=root / "managed",
            execution_workspace_root=root / "workspace",
        )

    def test_media_inspect_convert_is_in_bundled_catalog(self) -> None:
        snapshot = self.registry.snapshot()
        entry = snapshot.by_name().get(SKILL_NAME)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.source, "bundled")
        self.assertIn("exec_run", entry.description)

    def test_skill_body_is_a_real_execution_loop_without_absolute_paths(self) -> None:
        loaded = self.registry.load(SKILL_NAME)
        self.assertEqual(loaded.status, "loaded")
        body = loaded.content
        self.assertIn("ffprobe", body)
        self.assertIn("ffmpeg", body)
        self.assertIn("input_resources", body)
        self.assertIn("output_globs", body)
        self.assertIn("generated_resources", body)
        self.assertIn("send_file", body)
        self.assertIn("exec_status", body)
        self.assertIn("registration_failed", body)
        self.assertIn("DRM-protected", body)
        self.assertIn("-hide_banner -loglevel error", body)
        self.assertIn("not", body)  # 边界声明：何时不要加载
        self.assertIn("When NOT to load", body)
        self.assertNotIn("C:", body)
        self.assertNotIn("C:\\", body)
        self.assertNotIn(str(ROOT), body)
        self.assertNotIn("alias:", body)

    def test_skill_does_not_promise_a_skill_dir_script(self) -> None:
        loaded = self.registry.load(SKILL_NAME)
        self.assertNotIn("scripts/", loaded.content)
        self.assertEqual(loaded.execution_cwd, f"alias:{SKILL_BUNDLED_MOUNT}")

    def test_skill_preserves_internal_silence_and_distinguishes_exact_trim(self) -> None:
        body = self.registry.load(SKILL_NAME).content
        self.assertIn("areverse,silenceremove=", body)
        self.assertIn("Do not replace this with `stop_periods=1`", body)
        self.assertIn("stream copy may start on a nearby keyframe", body)
        self.assertIn("-ss 00:00:05 -i inputs/source.mp4", body)


class MediaSkillExecutionLoopTests(unittest.TestCase):
    """Items 7/8/12/13/16: real ffprobe/ffmpeg through the exec_run resource path."""

    def setUp(self) -> None:
        if not (_FFMPEG and _FFPROBE):
            self.skipTest("ffmpeg/ffprobe unavailable")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.store = MemoryStore(root / "db")
        self.attachment_service = AttachmentInboxService(store=self.store, base_dir=root)
        self.generated_service = GeneratedFileService(
            base_dir=root / "generated_files",
            store=self.store,
            attachment_service=self.attachment_service,
        )
        self.bridge = ExecutionResourceBridge(
            generated_file_service=self.generated_service,
            workspace_root=self.workspace,
        )
        save_approval_policy_config(
            base_dir=root,
            profile_user_id="alice",
            payload={"defaultMode": "trusted_auto_allow"},
        )
        from companion_v01.execution_local import TrustedLocalExecutor

        self.provider = TrustedLocalExecutor(
            workspace_root=self.workspace,
            run_log_dir=root / "runlogs",
            provider_id="local",
        )
        self.handler = ExecRunToolHandler(
            execution_provider=self.provider,
            config_base_dir=root,
            approval_store=None,
            resource_bridge=self.bridge,
        )
        self.owner = ExecutionRunOwner(profile_user_id="alice", session_id="s1", provider_id="local")
        wav = root / "source.wav"
        self._write_wav(wav)
        self.handle = self._register_wav(wav)

    @staticmethod
    def _write_wav(path: Path, *, seconds: float = 3.0, sample_rate: int = 8000) -> None:
        count = int(sample_rate * seconds)
        samples = array("h")
        for index in range(count):
            amplitude = 8000 if (index // 40) % 2 == 0 else -8000
            samples.append(amplitude)
        with wave.open(str(path), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(sample_rate)
            writer.writeframes(samples.tobytes())

    def _register_wav(self, path: Path) -> str:
        stored = path.parent / "master" / path.name
        stored.parent.mkdir(parents=True, exist_ok=True)
        stored.write_bytes(path.read_bytes())
        item = self.store.add_attachment_inbox_item(
            profile_user_id="alice",
            session_id="s1",
            kind="audio",
            source="test",
            status="ready",
            origin_name=path.name,
            mime_type="audio/wav",
            file_ext=".wav",
            file_size=path.stat().st_size,
            storage_relpath=f"master/{path.name}",
            detail={"file_kind": "wav"},
            timestamp=100,
        )
        return str(item["attachment_handle"])

    def _context(self) -> ToolExecutionContext:
        return ToolExecutionContext(
            profile_user_id="alice",
            session_id="s1",
            now_ts=0,
            visual_payload={},
            client_mode="qq",
        )

    def _ffprobe_cmd(self) -> str:
        return "ffprobe -v error -show_format -show_streams -print_format json inputs/source.wav"

    def _ffmpeg_convert_cmd(self) -> str:
        return "mkdir outputs && ffmpeg -y -i inputs/source.wav -codec:a libmp3lame -q:a 2 outputs/result.mp3"

    @staticmethod
    def _write_wav_with_internal_pause(path: Path, *, sample_rate: int = 8000) -> None:
        samples = array("h")
        for amplitude, seconds in ((0, 0.25), (8000, 0.5), (0, 0.4), (-8000, 0.5), (0, 0.25)):
            samples.extend([amplitude] * int(sample_rate * seconds))
        with wave.open(str(path), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(sample_rate)
            writer.writeframes(samples.tobytes())

    def test_media_inspect_is_one_exec_run_without_cursor_paging(self) -> None:
        result = self.handler.execute(
            call={
                "type": "exec_run",
                "command": self._ffprobe_cmd(),
                "initial_wait_seconds": 2,
                "input_resources": [{"handle": self.handle, "as": "inputs/source.wav"}],
            },
            context=self._context(),
        )
        event = result.stream_events[0]
        self.assertEqual(event["status"], EXEC_STATUS_COMPLETED)
        state = result.state_updates["capability_execution"]
        self.assertIsNone(state.get("next_cursor"))
        self.assertIn("pcm_s16le", result.followup_context)
        self.assertNotIn(str(self._tmp.name), result.followup_context)

    def test_media_convert_registers_gen_and_send_file_resolves(self) -> None:
        result = self.handler.execute(
            call={
                "type": "exec_run",
                "command": self._ffmpeg_convert_cmd(),
                "initial_wait_seconds": 2,
                "input_resources": [{"handle": self.handle, "as": "inputs/source.wav"}],
                "output_globs": ["outputs/result.mp3"],
            },
            context=self._context(),
        )
        state = result.state_updates["capability_execution"]
        self.assertEqual(state["status"], EXEC_STATUS_COMPLETED)
        self.assertEqual(state.get("artifact_status"), ARTIFACT_STATUS_REGISTERED)
        resources = state.get("generated_resources", [])
        self.assertEqual(len(resources), 1)
        handle = str(resources[0]["handle"])
        self.assertTrue(handle.startswith("gen_"))
        self.assertEqual(resources[0]["name"], "result.mp3")
        self.assertEqual(resources[0]["media_type"], "audio/mpeg")
        self.assertNotIn("\\", handle)
        self.assertNotIn(":", handle)

        sent = self.generated_service.send_file(
            profile_user_id="alice",
            session_id="s1",
            target=handle,
        )
        self.assertTrue(sent["ok"], sent)
        self.assertEqual(sent["files"][0]["name"], "result.mp3")
        self.assertEqual(sent["files"][0]["file_ext"], "mp3")

    def test_failed_ffmpeg_does_not_register_fake_handle(self) -> None:
        result = self.handler.execute(
            call={
                "type": "exec_run",
                "command": "mkdir outputs && ffmpeg -y -i inputs/source.wav -codec:a nope outputs/result.mp3",
                "initial_wait_seconds": 2,
                "input_resources": [{"handle": self.handle, "as": "inputs/source.wav"}],
                "output_globs": ["outputs/result.mp3"],
            },
            context=self._context(),
        )
        state = result.state_updates["capability_execution"]
        self.assertEqual(state["status"], EXEC_STATUS_FAILED)
        self.assertEqual(state.get("artifact_status"), ARTIFACT_STATUS_NOT_REGISTERED)
        self.assertNotIn("generated_resources", state)
        self.assertIn("命令执行失败", result.followup_context)

    def test_no_output_globs_means_no_artifact_claim(self) -> None:
        result = self.handler.execute(
            call={
                "type": "exec_run",
                "command": self._ffprobe_cmd(),
                "initial_wait_seconds": 2,
                "input_resources": [{"handle": self.handle, "as": "inputs/source.wav"}],
            },
            context=self._context(),
        )
        state = result.state_updates["capability_execution"]
        self.assertEqual(state.get("artifact_status"), ARTIFACT_STATUS_NOT_REQUESTED)
        self.assertNotIn("generated_resources", state)

    def test_trim_silence_preserves_audio_after_an_internal_pause(self) -> None:
        source = Path(self._tmp.name) / "pause.wav"
        self._write_wav_with_internal_pause(source)
        handle = self._register_wav(source)
        command = (
            'mkdir outputs && ffmpeg -hide_banner -loglevel error -y -i inputs/source.wav -af '
            '"silenceremove=start_periods=1:start_duration=0.12:start_threshold=-50dB:'
            'start_silence=0.02:detection=peak,areverse,silenceremove=start_periods=1:'
            'start_duration=0.12:start_threshold=-50dB:start_silence=0.02:detection=peak,areverse" '
            'outputs/trimmed.wav'
        )
        result = self.handler.execute(
            call={
                "type": "exec_run",
                "command": command,
                "initial_wait_seconds": 2,
                "input_resources": [{"handle": handle, "as": "inputs/source.wav"}],
                "output_globs": ["outputs/trimmed.wav"],
            },
            context=self._context(),
        )
        state = result.state_updates["capability_execution"]
        self.assertEqual(state["status"], EXEC_STATUS_COMPLETED)
        generated = state["generated_resources"][0]
        file_ref = self.generated_service.resolve_input_resource(
            profile_user_id="alice",
            session_id="s1",
            target=generated["handle"],
            timestamp=0,
        )
        self.assertIsNotNone(file_ref)
        duration = float(
            subprocess.check_output(
                [
                    _FFPROBE,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(file_ref["absolute_path"]),
                ],
                text=True,
            ).strip()
        )
        self.assertGreater(duration, 1.1, "the second audible segment after the pause was lost")
        self.assertLess(duration, 1.8, "leading/trailing silence was not removed")


class _FakeLLM:
    pass


class _FakeEmbeddingProvider:
    name = "hashed"
    version = "test"
    dimension = 8

    def embed_text(self, text: str) -> list[float]:
        return [0.0] * self.dimension


class MediaSkillMemcoreTraceTests(unittest.TestCase):
    """Items 14-16: the skill loop's tool round stays fully recorded in MemCore.

    Mirrors tests.test_memcore_integration's exec_run settlement coverage but
    records the media-skill pair (load_skill + exec_run with a gen_* result) and
    checks the full result survives, is reloadable, and leaks no host path.
    """

    def test_load_skill_and_exec_run_round_survives_settlement_and_is_reloadable(self) -> None:
        import config
        from unittest.mock import patch

        from companion_v01.memcore_integration.manager import MemcoreManager

        media_result = (
            "命令已执行完成（exit_code=0，run_id=execrun_media）。\n"
            "stdout：\n{...ffprobe json ... duration=3.0, codec=pcm_s16le, sample_rate=8000 ...}\n"
            + ("x" * 1500)
            + "\n已登记生成资源：gen_001(result.mp3, audio/mpeg)。需要交付时调用 send_file，不要自动替用户发送。"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(config, "MEMCORE_OPERATION_PROJECTION_POLICY", "compact_after_terminal"):
                manager = MemcoreManager(
                    backend="memcore",
                    storage_path=Path(temp_dir) / "media.sqlite3",
                    visible_scope="conversation",
                    enable_flavor=False,
                    shadow_compare=False,
                    llm=_FakeLLM(),
                    embedding_provider=_FakeEmbeddingProvider(),
                )
                try:
                    opened = manager.begin_input_turn(
                        {"source_id": "user-media", "content": "转一下这个音频", "timestamp": 100},
                        profile_user_id="u1",
                        session_id="s1",
                        character_pack_id="char",
                    )
                    turn_id = str(opened.get("turn_id") or "")
                    batch = manager.record_tool_batch(
                        exchanges=[
                            {
                                "tool_name": "load_skill",
                                "tool_call_id": "call-load-skill",
                                "tool_input": {"name": SKILL_NAME},
                                "result": f"已加载 Skill media-inspect-convert（正文 {len('''AABODY''')} 字符）。",
                                "source": "load_skill",
                                "timestamp": 101,
                                "source_id_prefix": "tooltrace-skill",
                                "result_status": "success",
                            },
                            {
                                "tool_name": "exec_run",
                                "tool_call_id": "call-exec-media",
                                "tool_input": {
                                    "command": "mkdir outputs && ffmpeg -y -i inputs/source.wav "
                                    "-codec:a libmp3lame -q:a 2 outputs/result.mp3",
                                    "input_resources": [{"handle": "aud_001", "as": "inputs/source.wav"}],
                                    "output_globs": ["outputs/result.mp3"],
                                },
                                "result": media_result,
                                "source": "exec_run",
                                "timestamp": 102,
                                "source_id_prefix": "tooltrace-media",
                                "result_status": "success",
                            },
                        ],
                        turn_id=turn_id,
                        profile_user_id="u1",
                        session_id="s1",
                        character_pack_id="char",
                    )
                    self.assertTrue(batch["ok"], batch)
                    observation_sids = [
                        str(item["tool_result_source_id"])
                        for item in batch["exchanges"]
                        if item.get("tool_result_source_id")
                    ]
                    self.assertEqual(len(observation_sids), 2)
                    manager.build_context_projection(
                        provider_profile="openai",
                        profile_user_id="u1",
                        session_id="s1",
                        character_pack_id="char",
                    )
                    manager.complete_input_turn(
                        turn_id=turn_id,
                        assistant_record={"source_id": "assistant-media", "content": "转好了。", "timestamp": 103},
                        memory_metadata={},
                        provider_output_raw="转好了。",
                        profile_user_id="u1",
                        session_id="s1",
                        character_pack_id="char",
                        provider_profile="openai",
                        provider_projection={"role": "assistant", "content": "转好了。"},
                    )
                    projection = manager.build_context_projection(
                        provider_profile="openai",
                        profile_user_id="u1",
                        session_id="s1",
                        character_pack_id="char",
                    )
                    tool_payloads = [m for m in projection.get("payloads") or [] if m.get("role") == "tool"]
                    self.assertEqual(len(tool_payloads), 2)
                    card_text = "\n".join(str(m.get("content") or "") for m in tool_payloads)
                    self.assertIn("[compact_reloadable]", card_text)
                    self.assertIn("tool: exec_run", card_text)
                    self.assertIn("reload: open_memory(memory_id=", card_text)
                    self.assertIn("status: success", card_text)
                    self.assertNotIn("aud_001", card_text)
                    self.assertNotIn("run_id=execrun_media", card_text)
                    self.assertNotIn("x" * 64, card_text)
                    self.assertNotIn(str(temp_dir), card_text)
                    self.assertNotIn("C:\\", card_text)
                    system = manager._get_system(
                        profile_user_id="u1",
                        session_id="s1",
                        character_pack_id="char",
                    )
                    entries = manager._store.get_turn_entries(namespace=system.namespace, turn_id=turn_id)
                    observations = {
                        str(getattr(entry, "source_id", "") or ""): entry
                        for entry in entries
                        if str(getattr(entry, "source_id", "") or "") in observation_sids
                    }
                    self.assertEqual(set(observations), set(observation_sids))
                    media_observation = observations[observation_sids[1]]
                    stored = str(dict(media_observation.payload or {}).get("output") or "")
                    self.assertIn("run_id=execrun_media", stored)
                    self.assertIn("gen_001", stored)
                    self.assertIn("audio/mpeg", stored)
                    self.assertNotIn("C:\\", stored)
                    expanded = manager.open_memory(
                        profile_user_id="u1",
                        session_id="s1",
                        character_pack_id="char",
                        arguments={"memory_id": observation_sids[1], "view": "content"},
                    )
                    self.assertTrue(expanded["ok"], expanded)
                    self.assertIn("run_id=execrun_media", expanded["text"])
                    self.assertIn("gen_001", expanded["text"])
                finally:
                    manager.close()


if __name__ == "__main__":
    unittest.main()
