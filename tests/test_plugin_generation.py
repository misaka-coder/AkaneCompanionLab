from __future__ import annotations

import asyncio
import json
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path

from capcore import CapabilityDescriptor, CapabilityIOSlot, CapabilityResult, InvocationContext, build_tool_spec
from companion_v01.attachment_inbox import AttachmentInboxService
from companion_v01.generated_files import GeneratedFileService
from companion_v01.native_tool_schema import build_openai_native_tool_from_spec
from companion_v01.plugin_api import (
    BEFORE_OUTBOUND_PLAN_HOOK,
    BEFORE_TOOL_CALL_HOOK,
    NotificationIntent,
    NotificationResult,
    PluginDeliverySnapshot,
    PluginHookEnvelope,
    PluginInvocationContext,
    PluginOutboundDecoration,
    PluginOutboundPlanSnapshot,
    PluginQQCommandRequest,
    PluginQQCommandResult,
    PluginToolCallSnapshot,
    PluginToolResultSnapshot,
)
from companion_v01.plugin_generation import (
    PLUGIN_GENERATION_PROTOCOL,
    PluginGenerationError,
    PluginGenerationProcess,
)
from companion_v01.plugin_generation_codec import (
    PluginGenerationCodecError,
    capability_descriptor_from_wire,
    capability_descriptor_to_wire,
    capability_result_from_wire,
    capability_result_to_wire,
    invocation_context_from_wire,
    invocation_context_to_wire,
    json_snapshot,
    notification_intent_from_wire,
    notification_intent_to_wire,
    notification_result_from_wire,
    notification_result_to_wire,
    plugin_hook_dispatch_result_from_wire,
    plugin_hook_dispatch_result_to_wire,
    plugin_hook_envelope_from_wire,
    plugin_hook_envelope_to_wire,
    plugin_qq_command_result_from_wire,
    plugin_qq_command_result_to_wire,
    qq_command_dispatch_from_wire,
    qq_command_dispatch_to_wire,
)
from companion_v01.plugin_hooks import PluginHookDispatchResult
from companion_v01.plugin_managed_artifacts import GeneratedFileManagedArtifactSink
from companion_v01.plugin_qq_commands import _PluginCommandRegistration
from companion_v01.execution_local import TrustedLocalExecutor
from companion_v01.skill_runtime import SkillRegistry
from companion_v01.store import MemoryStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _generation_hook_envelope(
    *,
    hook_type: str = BEFORE_OUTBOUND_PLAN_HOOK,
    delay_ms: int = 0,
) -> PluginHookEnvelope:
    if hook_type == BEFORE_TOOL_CALL_HOOK:
        payload = PluginToolCallSnapshot(
            invocation_id="invoke-1",
            tool_name="web_search",
            source="native",
            profile_user_id="user-1",
            session_id="session-1",
            character_pack_id="reimu",
            arguments_json='{"query":"Akane"}',
        )
    else:
        payload = PluginOutboundPlanSnapshot(
            delivery_id="delivery-1",
            channel="qq_text",
            action="send_message",
            conversation_kind="group",
            target_id="427674145",
            segment_types=("text",),
            text="你好",
            reply_to_message_id="message-1",
            text_decoratable=True,
        )
    return PluginHookEnvelope(
        hook_id="hook-1",
        hook_type=hook_type,
        occurred_at=int(time.time()),
        subject=f"delay:{delay_ms}",
        payload=payload,
    )


def _write_plugin_site(root: Path, *, broken: bool = False) -> Path:
    site = root / "site"
    package = site / "generation_fixture"
    dist_info = site / "generation_fixture-0.1.0.dist-info"
    package.mkdir(parents=True)
    dist_info.mkdir(parents=True)
    if broken:
        source = "def create_plugin():\n    raise RuntimeError('broken')\n"
    else:
        source = textwrap.dedent(
            """
            from akane_plugin import Plugin

            plugin = Plugin("test.generation", permissions=("event.subscribe",))

            @plugin.on("conversation.direct.inbound", name="observe")
            async def observe(event, ctx):
                return event.data

            def create_plugin():
                return plugin
            """
        )
    (package / "__init__.py").write_text(source, encoding="utf-8")
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: generation-fixture\nVersion: 0.1.0\n",
        encoding="utf-8",
    )
    (dist_info / "entry_points.txt").write_text(
        "[akane.plugins.v1]\ntest.generation = generation_fixture:create_plugin\n",
        encoding="utf-8",
    )
    return site


def _write_capability_plugin_site(root: Path, *, typed_schema: bool = False, invalid_schema: bool = False) -> Path:
    site = root / "site"
    package = site / "generation_fixture"
    dist_info = site / "generation_fixture-0.1.0.dist-info"
    package.mkdir(parents=True)
    dist_info.mkdir(parents=True)
    source = textwrap.dedent(
        """
        import asyncio

        import json
        from capcore import (
            CapabilityDescriptor,
            CapabilityIOSlot,
            CapabilityResult,
            HealthStatus,
        )
        from companion_v01.plugin_api import (
            AKANE_PLUGIN_API_VERSION,
            CAPABILITY_PROMPT_INVOKE_PERMISSION,
            MANAGED_ARTIFACT_WRITE_PERMISSION,
            NOTIFICATION_SEND_PERMISSION,
            ManagedArtifactDraft,
            ManagedArtifactPayload,
            NotificationIntent,
            PluginManifest,
        )

        CAPABILITY_ID = "test.generation.echo.v1"
        ARTIFACT_CAPABILITY_ID = "test.generation.artifact.v1"
        NOTIFICATION_CAPABILITY_ID = "test.generation.notification.v1"

        INPUT_SCHEMA = {
            "type": "object", "$defs": {"cell": {"type": "object", "properties": {
                "count": {"type": "integer", "minimum": 1}}, "required": ["count"], "additionalProperties": False}},
            "properties": {"value": {"type": "string"}, "payload_json": {"type": "string"},
                           "rows": {"type": "array", "items": {"$ref": "#/$defs/cell"}, "minItems": 1}},
            "required": ["value", "rows"], "additionalProperties": False,
        }
        OUTPUT_SCHEMA = {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}

        import tempfile
        from pathlib import Path

        class Adapter:
            provider_id = "provider.test.generation"

            def __init__(self, notification_port):
                self._notification_port = notification_port
                self._work = tempfile.TemporaryDirectory()
                self._invoke_count = 0

            async def health(self):
                return HealthStatus(ok=True, status="ready")

            async def list_capabilities(self):
                return (
                    CapabilityDescriptor(
                        id=CAPABILITY_ID,
                        display_name="Generation echo",
                        short_hint="Echo public JSON values after an optional delay.",
                        visible_in=("diagnostics",),
                        prompt_exposed=True,
                        risk="low",
                        confirm="never",
                        effects=(),
                        trigger=None,
                        inputs=() if TYPED_SCHEMA else (
                            CapabilityIOSlot(
                                name="value", kind="string", required=True,
                                raw={"description": "Contract detail; " * 40 + "\\nFinal: preserve  two spaces."},
                            ),
                            CapabilityIOSlot(name="delay_ms", kind="integer", required=False),
                            CapabilityIOSlot(name="payload_json", kind="string", required=False),
                            CapabilityIOSlot(name="notes", kind="array", raw={
                                "items": {"type": "array", "items": {
                                    "type": "string",
                                    "description": "Nested detail; " * 40 + "\\nFinal: preserve  two spaces.",
                                }},
                            }),
                        ),
                        outputs=(),
                        raw={"contract": "generation-echo.v1"},
                        input_schema=INPUT_SCHEMA if TYPED_SCHEMA else None,
                        output_schema=OUTPUT_SCHEMA if TYPED_SCHEMA else None,
                    ),
                    CapabilityDescriptor(
                        id=ARTIFACT_CAPABILITY_ID,
                        display_name="Generation artifact",
                        short_hint="Create one host-managed Markdown artifact.",
                        visible_in=("diagnostics",),
                        prompt_exposed=True,
                        risk="low",
                        confirm="never",
                        effects=("filesystem",),
                        trigger=None,
                        inputs=(
                            CapabilityIOSlot(name="size", kind="integer", required=False),
                            CapabilityIOSlot(name="count", kind="integer", required=False),
                        ),
                        outputs=(
                            CapabilityIOSlot(
                                name="report",
                                kind="file",
                                required=True,
                                max_bytes=40 * 1024 * 1024,
                                delivery="generated_file",
                            ),
                        ),
                        raw={"contract": "generation-artifact.v1"},
                    ),
                    CapabilityDescriptor(
                        id=NOTIFICATION_CAPABILITY_ID,
                        display_name="Generation notification",
                        short_hint="Send one host-owned test notification.",
                        visible_in=("diagnostics",),
                        prompt_exposed=True,
                        risk="low",
                        confirm="never",
                        effects=(),
                        trigger=None,
                        inputs=(
                            CapabilityIOSlot(name="recipient_id", kind="string", required=True),
                            CapabilityIOSlot(name="text", kind="string", required=True),
                            CapabilityIOSlot(name="idempotency_key", kind="string", required=True),
                        ),
                        outputs=(),
                        raw={"contract": "generation-notification.v1"},
                    ),
                )

            async def invoke(self, capability_id, args, context):
                self._invoke_count += 1
                if capability_id == ARTIFACT_CAPABILITY_ID:
                    size = max(1, int(args.get("size", 24)))
                    drafts = []
                    for index in range(int(args.get("count", 1))):
                        source = Path(self._work.name) / f"report-{index}.md"
                        with source.open("wb") as stream:
                            remaining = size
                            while remaining:
                                chunk_size = min(remaining, 1024 * 1024)
                                stream.write(b"x" * chunk_size)
                                remaining -= chunk_size
                        drafts.append(ManagedArtifactDraft(
                            path=source, title=f"generation-report-{index}",
                            output_format="md", mime_type="text/markdown",
                            summary="A generation handoff test report.", send_to_user=True,
                        ))
                    return CapabilityResult(
                        is_error=False,
                        status="ok",
                        content=ManagedArtifactPayload(
                            content={"report": "ready"},
                            artifacts=tuple(drafts),
                        ),
                    )
                if capability_id == NOTIFICATION_CAPABILITY_ID:
                    delivery = await self._notification_port.send(
                        NotificationIntent(
                            channel="qq_text",
                            recipient_id=args["recipient_id"],
                            text=args["text"],
                            idempotency_key=args["idempotency_key"],
                        )
                    )
                    return CapabilityResult(
                        is_error=False,
                        status="ok",
                        content={
                            "delivery_ok": delivery.ok,
                            "delivery_status": delivery.status,
                            "delivery_reason": delivery.reason,
                        },
                    )
                await asyncio.sleep(max(0, args.get("delay_ms", 0)) / 1000)
                if "payload_json" in args:
                    return CapabilityResult(is_error=False, status="ok", content=json.loads(args["payload_json"]))
                return CapabilityResult(
                    is_error=False,
                    status="ok",
                    content={
                        "capability_id": capability_id,
                        "value": args["value"],
                        "invoke_count": self._invoke_count,
                        "profile_user_id": context.profile_user_id,
                        "session_id": context.session_id,
                        "client_mode": context.client_mode,
                    },
                )

            async def aclose(self):
                return None

        class Plugin:
            manifest = PluginManifest(
                plugin_id="test.generation",
                plugin_version="0.1.0",
                plugin_api_version=AKANE_PLUGIN_API_VERSION,
                permissions=(
                    CAPABILITY_PROMPT_INVOKE_PERMISSION,
                    MANAGED_ARTIFACT_WRITE_PERMISSION,
                    NOTIFICATION_SEND_PERMISSION,
                ),
            )

            def register(self, registrar):
                registrar.add_capability_adapter(
                    Adapter(registrar.get_notification_port())
                )

        def create_plugin():
            return Plugin()
        """
    )
    source = "TYPED_SCHEMA = " + repr(typed_schema) + "\n" + source
    if invalid_schema:
        source += '\nINPUT_SCHEMA["$defs"]["cell"]["properties"]["count"]["minimum"] = "many"\n'
    (package / "__init__.py").write_text(source, encoding="utf-8")
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: generation-fixture\nVersion: 0.1.0\n",
        encoding="utf-8",
    )
    (dist_info / "entry_points.txt").write_text(
        "[akane.plugins.v1]\ntest.generation = generation_fixture:create_plugin\n",
        encoding="utf-8",
    )
    return site


def _write_notification_job_plugin_site(root: Path) -> Path:
    site = root / "site"
    package = site / "generation_notification_fixture"
    dist_info = site / "generation_notification_fixture-0.1.0.dist-info"
    package.mkdir(parents=True)
    dist_info.mkdir(parents=True)
    source = textwrap.dedent(
        """
        from companion_v01.plugin_api import (
            AKANE_PLUGIN_API_VERSION,
            BACKGROUND_JOB_PERMISSION,
            NOTIFICATION_SEND_PERMISSION,
            NotificationIntent,
            PluginManifest,
        )

        class StartupNotificationJob:
            def __init__(self, notification_port):
                self._notification_port = notification_port

            async def start(self, controller):
                await self._notification_port.send(
                    NotificationIntent(
                        channel="qq_text",
                        recipient_id="user:123456",
                        text="background-ready",
                        idempotency_key="background-ready-1",
                    )
                )
                await controller.wait_for_shutdown()

            async def stop(self):
                return None

        class Plugin:
            manifest = PluginManifest(
                plugin_id="test.generation.notification-job",
                plugin_version="0.1.0",
                plugin_api_version=AKANE_PLUGIN_API_VERSION,
                permissions=(
                    BACKGROUND_JOB_PERMISSION,
                    NOTIFICATION_SEND_PERMISSION,
                ),
            )

            def register(self, registrar):
                registrar.add_background_service(
                    "startup-notification",
                    StartupNotificationJob(registrar.get_notification_port()),
                )

        def create_plugin():
            return Plugin()
        """
    )
    (package / "__init__.py").write_text(source, encoding="utf-8")
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\n"
        "Name: generation-notification-fixture\n"
        "Version: 0.1.0\n",
        encoding="utf-8",
    )
    (dist_info / "entry_points.txt").write_text(
        "[akane.plugins.v1]\n"
        "test.generation.notification-job = "
        "generation_notification_fixture:create_plugin\n",
        encoding="utf-8",
    )
    return site


def _write_background_lifecycle_plugin_site(
    root: Path,
    *,
    immediate_failure: bool = False,
) -> Path:
    site = root / "site"
    package = site / "generation_background_fixture"
    dist_info = site / "generation_background_fixture-0.1.0.dist-info"
    package.mkdir(parents=True)
    dist_info.mkdir(parents=True)
    source = textwrap.dedent(
        f"""
        import asyncio

        from companion_v01.plugin_api import (
            AKANE_PLUGIN_API_VERSION,
            BACKGROUND_JOB_PERMISSION,
            PluginManifest,
        )

        class FailingService:
            async def start(self, controller):
                del controller
                if {immediate_failure!r}:
                    raise RuntimeError("private-service-failure")
                await asyncio.sleep(0.15)
                raise RuntimeError("private-service-failure")

            async def stop(self):
                return None

        class CooperativeService:
            async def start(self, controller):
                await controller.wait_for_shutdown()

            async def stop(self):
                return None

        class Plugin:
            manifest = PluginManifest(
                plugin_id="test.generation.background",
                plugin_version="0.1.0",
                plugin_api_version=AKANE_PLUGIN_API_VERSION,
                permissions=(BACKGROUND_JOB_PERMISSION,),
            )

            def register(self, registrar):
                registrar.add_background_service("crash", FailingService())
                registrar.add_background_service("worker", CooperativeService())

        def create_plugin():
            return Plugin()
        """
    )
    (package / "__init__.py").write_text(source, encoding="utf-8")
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\n"
        "Name: generation-background-fixture\n"
        "Version: 0.1.0\n",
        encoding="utf-8",
    )
    (dist_info / "entry_points.txt").write_text(
        "[akane.plugins.v1]\n"
        "test.generation.background = "
        "generation_background_fixture:create_plugin\n",
        encoding="utf-8",
    )
    return site


def _write_hook_plugin_site(root: Path) -> Path:
    site = root / "site"
    package = site / "generation_hook_fixture"
    dist_info = site / "generation_hook_fixture-0.1.0.dist-info"
    package.mkdir(parents=True)
    dist_info.mkdir(parents=True)
    source = textwrap.dedent(
        """
        import asyncio

        from companion_v01.plugin_api import (
            AKANE_PLUGIN_API_VERSION,
            BEFORE_OUTBOUND_PLAN_HOOK,
            BEFORE_TOOL_CALL_HOOK,
            HOOK_SUBSCRIBE_PERMISSION,
            PluginHookResult,
            PluginManifest,
            PluginOutboundDecoration,
        )

        class Handler:
            async def handle_hook(self, hook):
                if hook.subject.startswith("delay:"):
                    await asyncio.sleep(int(hook.subject.split(":", 1)[1]) / 1000)
                decoration = None
                if hook.hook_type == BEFORE_OUTBOUND_PLAN_HOOK:
                    decoration = PluginOutboundDecoration(
                        text_prefix="[fixture] ",
                        text_suffix=" /ok",
                    )
                return PluginHookResult(
                    diagnostics=("generation-hook-observed",),
                    outbound_decoration=decoration,
                )

        class Plugin:
            manifest = PluginManifest(
                plugin_id="test.generation.hook",
                plugin_version="0.1.0",
                plugin_api_version=AKANE_PLUGIN_API_VERSION,
                permissions=(HOOK_SUBSCRIBE_PERMISSION,),
            )

            def register(self, registrar):
                registrar.add_hook_handler(BEFORE_TOOL_CALL_HOOK, Handler())
                registrar.add_hook_handler(BEFORE_OUTBOUND_PLAN_HOOK, Handler())

        def create_plugin():
            return Plugin()
        """
    )
    (package / "__init__.py").write_text(source, encoding="utf-8")
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: generation-hook-fixture\nVersion: 0.1.0\n",
        encoding="utf-8",
    )
    (dist_info / "entry_points.txt").write_text(
        "[akane.plugins.v1]\n"
        "test.generation.hook = generation_hook_fixture:create_plugin\n",
        encoding="utf-8",
    )
    return site


def _write_qq_command_plugin_site(root: Path) -> Path:
    site = root / "site"
    package = site / "generation_qq_command_fixture"
    dist_info = site / "generation_qq_command_fixture-0.1.0.dist-info"
    package.mkdir(parents=True)
    dist_info.mkdir(parents=True)
    source = textwrap.dedent(
        """
        import asyncio

        from companion_v01.plugin_api import (
            AKANE_PLUGIN_API_VERSION,
            PLUGIN_QQ_COMMAND_PERMISSION,
            PluginManifest,
            PluginQQCommandResult,
        )

        class Handler:
            async def handle(self, request):
                if request.args.startswith("delay:"):
                    await asyncio.sleep(int(request.args.split(":", 1)[1]) / 1000)
                if request.args == "raise":
                    raise RuntimeError("private-command-failure")
                if request.args == "long":
                    return PluginQQCommandResult(handled=True, reply_text="x" * 2500)
                return PluginQQCommandResult(
                    handled=True,
                    reply_text="|".join(
                        (
                            request.command,
                            request.args,
                            str(request.qq_number),
                            str(request.group_id),
                            str(request.is_group).lower(),
                            request.sender_role,
                            request.profile_user_id,
                            request.session_id,
                            request.character_pack_id,
                            request.idempotency_key,
                        )
                    ),
                )

        class SilentHandler:
            async def handle(self, request):
                del request
                return PluginQQCommandResult(handled=True)

        class Plugin:
            manifest = PluginManifest(
                plugin_id="test.generation.qq-command",
                plugin_version="0.1.0",
                plugin_api_version=AKANE_PLUGIN_API_VERSION,
                permissions=(PLUGIN_QQ_COMMAND_PERMISSION,),
            )

            def register(self, registrar):
                registrar.add_qq_command("/echo", Handler())
                registrar.add_qq_command("/silent", SilentHandler())

        def create_plugin():
            return Plugin()
        """
    )
    (package / "__init__.py").write_text(source, encoding="utf-8")
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\n"
        "Name: generation-qq-command-fixture\n"
        "Version: 0.1.0\n",
        encoding="utf-8",
    )
    (dist_info / "entry_points.txt").write_text(
        "[akane.plugins.v1]\n"
        "test.generation.qq-command = "
        "generation_qq_command_fixture:create_plugin\n",
        encoding="utf-8",
    )
    return site


def _write_prompt_plugin_site(root: Path) -> Path:
    site = root / "site"
    package = site / "generation_prompt_fixture"
    dist_info = site / "generation_prompt_fixture-0.1.0.dist-info"
    package.mkdir(parents=True)
    dist_info.mkdir(parents=True)
    source = textwrap.dedent(
        """
        from companion_v01.plugin_api import (
            AKANE_PLUGIN_API_VERSION,
            SYSTEM_PROMPT_CONTRIBUTION_PERMISSION,
            PluginManifest,
        )

        class Plugin:
            manifest = PluginManifest(
                plugin_id="test.generation.prompt",
                plugin_version="0.1.0",
                plugin_api_version=AKANE_PLUGIN_API_VERSION,
                permissions=(SYSTEM_PROMPT_CONTRIBUTION_PERMISSION,),
            )

            def register(self, registrar):
                registrar.add_prompt_block(
                    "z-evidence",
                    "Keep evidence next to the claim.\\r\\nPreserve source meaning.",
                )
                registrar.add_prompt_block(
                    "a-method",
                    "Use the shortest sufficient method.",
                )

        def create_plugin():
            return Plugin()
        """
    )
    (package / "__init__.py").write_text(source, encoding="utf-8")
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: generation-prompt-fixture\nVersion: 0.1.0\n",
        encoding="utf-8",
    )
    (dist_info / "entry_points.txt").write_text(
        "[akane.plugins.v1]\n"
        "test.generation.prompt = generation_prompt_fixture:create_plugin\n",
        encoding="utf-8",
    )
    return site


def _write_skill_plugin_site(root: Path) -> tuple[Path, Path]:
    site = root / "site"
    package = site / "generation_skill_fixture"
    skill_root = package / "sample-generation-skill"
    dist_info = site / "generation_skill_fixture-0.1.0.dist-info"
    skill_root.mkdir(parents=True)
    dist_info.mkdir(parents=True)
    source = textwrap.dedent(
        """
        from pathlib import Path

        from companion_v01.plugin_api import (
            AKANE_PLUGIN_API_VERSION,
            SKILL_CONTRIBUTION_PERMISSION,
            PluginManifest,
        )

        class Plugin:
            manifest = PluginManifest(
                plugin_id="test.generation.skill",
                plugin_version="0.1.0",
                plugin_api_version=AKANE_PLUGIN_API_VERSION,
                permissions=(SKILL_CONTRIBUTION_PERMISSION,),
            )

            def register(self, registrar):
                registrar.add_skill(
                    Path(__file__).parent / "sample-generation-skill"
                )

        def create_plugin():
            return Plugin()
        """
    )
    (package / "__init__.py").write_text(source, encoding="utf-8")
    (skill_root / "SKILL.md").write_text(
        "---\n"
        "name: sample-generation-skill\n"
        "description: A frozen workflow supplied by one plugin generation.\n"
        "metadata:\n"
        "  required_tools: [exec_run]\n"
        "---\n"
        "Follow the generation-specific workflow.\n",
        encoding="utf-8",
    )
    (skill_root / "reference.txt").write_text(
        "original generation reference\n",
        encoding="utf-8",
    )
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: generation-skill-fixture\nVersion: 0.1.0\n",
        encoding="utf-8",
    )
    (dist_info / "entry_points.txt").write_text(
        "[akane.plugins.v1]\n"
        "test.generation.skill = generation_skill_fixture:create_plugin\n",
        encoding="utf-8",
    )
    return site, skill_root


def _descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        id="test.generation.echo.v1",
        display_name="Generation echo",
        short_hint="Echo one value.",
        visible_in=("diagnostics",),
        prompt_exposed=True,
        risk="low",
        confirm="never",
        effects=(),
        trigger=None,
        inputs=(CapabilityIOSlot(name="value", kind="string", required=True),),
        outputs=(),
        raw={"contract": "generation-echo.v1"},
    )


class PluginGenerationCodecTests(unittest.TestCase):
    def test_public_capcore_values_round_trip_as_json(self) -> None:
        descriptor = _descriptor()
        context = InvocationContext(
            profile_user_id="主人",
            session_id="session-1",
            client_mode="qq_text",
        )
        result = CapabilityResult(
            is_error=False,
            status="ok",
            content={"text": "完成", "items": [1, True, None]},
        )
        intent = NotificationIntent(
            channel="qq_text",
            recipient_id="user:123",
            text="提醒",
            idempotency_key="notice-1",
        )
        notification_result = NotificationResult(
            ok=True,
            status="delivered",
            reason="",
        )
        hook_payloads = (
            _generation_hook_envelope(hook_type=BEFORE_TOOL_CALL_HOOK),
            PluginHookEnvelope(
                hook_id="hook-result",
                hook_type="after_tool_call",
                occurred_at=1_788_278_400,
                subject="tool:web_search",
                payload=PluginToolResultSnapshot(
                    invocation_id="invoke-1",
                    tool_name="web_search",
                    status="ok",
                    duration_ms=12.5,
                    reason="",
                    model_feedback="找到 3 条结果",
                    event_types=("tool.completed",),
                ),
            ),
            _generation_hook_envelope(),
            PluginHookEnvelope(
                hook_id="hook-delivery",
                hook_type="after_delivery",
                occurred_at=1_788_278_401,
                subject="delivery:delivery-1",
                payload=PluginDeliverySnapshot(
                    delivery_id="delivery-1",
                    channel="qq_text",
                    action="send_message",
                    conversation_kind="group",
                    target_id="427674145",
                    segment_types=("text",),
                    status="delivered",
                    duration_ms=8.25,
                    message_id="message-2",
                ),
            ),
        )
        hook_dispatch_result = PluginHookDispatchResult(
            ok=True,
            status="observed",
            diagnostics=(("test.plugin", "observed"),),
            outbound_decorations=(
                (
                    "test.plugin",
                    PluginOutboundDecoration(text_prefix="[前]", text_suffix="[后]"),
                ),
            ),
        )
        qq_command_args = {
            "command": "/echo",
            "args": "你好",
            "qq_number": 123456,
            "group_id": 654321,
            "is_group": True,
            "idempotency_key": "message-1",
            "sender_role": "admin",
            "profile_user_id": "profile:123456",
            "session_id": "qq:group:654321",
            "character_pack_id": "reimu",
            "conversation_ref": "acr1.opaque.signature",
        }
        qq_command_result = PluginQQCommandResult(
            handled=True,
            reply_text="完成",
            reason="",
        )
        self.assertEqual(
            capability_descriptor_from_wire(capability_descriptor_to_wire(descriptor)),
            descriptor,
        )
        self.assertEqual(
            invocation_context_from_wire(invocation_context_to_wire(context)),
            context,
        )
        plugin_context = PluginInvocationContext(
            profile_user_id="profile:123456",
            session_id="qq:group:654321",
            client_mode="qq_text",
            conversation_ref="acr1.opaque.signature",
        )
        self.assertEqual(
            invocation_context_from_wire(invocation_context_to_wire(plugin_context)),
            plugin_context,
        )
        self.assertEqual(
            capability_result_from_wire(capability_result_to_wire(result)),
            result,
        )
        self.assertEqual(
            notification_intent_from_wire(notification_intent_to_wire(intent)),
            intent,
        )
        self.assertEqual(
            notification_result_from_wire(
                notification_result_to_wire(notification_result)
            ),
            notification_result,
        )
        for hook in hook_payloads:
            with self.subTest(hook_type=hook.hook_type):
                self.assertEqual(
                    plugin_hook_envelope_from_wire(plugin_hook_envelope_to_wire(hook)),
                    hook,
                )
        self.assertEqual(
            plugin_hook_dispatch_result_from_wire(
                plugin_hook_dispatch_result_to_wire(hook_dispatch_result)
            ),
            hook_dispatch_result,
        )
        self.assertEqual(
            qq_command_dispatch_from_wire(
                qq_command_dispatch_to_wire(**qq_command_args)
            ),
            qq_command_args,
        )
        self.assertEqual(
            plugin_qq_command_result_from_wire(
                plugin_qq_command_result_to_wire(qq_command_result)
            ),
            qq_command_result,
        )

    def test_non_json_values_are_rejected_without_a_size_policy(self) -> None:
        with self.assertRaises(PluginGenerationCodecError):
            json_snapshot({"invalid": object()})
        with self.assertRaises(PluginGenerationCodecError):
            json_snapshot({"nested": {1: "key coercion is not allowed"}})
        with self.assertRaises(PluginGenerationCodecError):
            capability_descriptor_from_wire(
                {
                    **capability_descriptor_to_wire(_descriptor()),
                    "prompt_exposed": "true",
                }
            )
        payload = {"text": "字" * 100_000}
        self.assertEqual(json_snapshot(payload), payload)


class PluginGenerationProcessTests(unittest.TestCase):
    def test_complete_schema_and_canonical_values_cross_the_real_worker_boundary(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT, site_dir=_write_capability_plugin_site(root, typed_schema=True),
                    plugin_id="test.generation", work_dir=root / "work",
                )
                generation.start()
                try:
                    capability_id = "test.generation.echo.v1"
                    descriptor = generation.capability_descriptors[capability_id]
                    spec = build_tool_spec(descriptor)
                    native = build_openai_native_tool_from_spec(spec)
                    self.assertEqual(native["function"]["parameters"], descriptor.input_schema)
                    self.assertEqual(spec.output_schema, descriptor.output_schema)
                    self.assertEqual(native["function"]["parameters"]["properties"]["rows"]["items"], {"$ref": "#/$defs/cell"})
                    descriptor.input_schema["$defs"]["cell"]["properties"]["count"]["minimum"] = 0
                    self.assertEqual(generation.capability_descriptors[capability_id].input_schema["$defs"]["cell"]["properties"]["count"]["minimum"], 1)
                    context = InvocationContext("owner", "session", "web")
                    bad_input = await generation.invoke(capability_id, {"value": "bad", "rows": [{"count": 0}]}, context=context)
                    self.assertEqual(bad_input.status, "validation_error")
                    self.assertEqual(bad_input.content["errors"][0]["argument"], "rows[0].count")
                    good = await generation.invoke(capability_id, {"value": "good", "rows": [{"count": 1}]}, context=context)
                    self.assertFalse(good.is_error, good.reason)
                    self.assertTrue(good.has_value)
                    self.assertEqual(good.value, good.content)
                    self.assertEqual(good.value["invoke_count"], 1)
                    bad_output = await generation.invoke(capability_id, {
                        "value": "bad", "rows": [{"count": 1}], "payload_json": '{"value":0}',
                    }, context=context)
                    self.assertEqual(bad_output.reason, "plugin_result_schema_mismatch")
                    self.assertFalse(bad_output.content["retryable"])
                    self.assertEqual(bad_output.content["execution_status"], "completed")
                    after = await generation.invoke(capability_id, {"value": "after", "rows": [{"count": 2}]}, context=context)
                    self.assertEqual(after.value["invoke_count"], 3)
                finally:
                    generation.stop()
        asyncio.run(scenario())

    def test_invalid_worker_schema_returns_the_declaration_field(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            generation = PluginGenerationProcess(
                project_root=PROJECT_ROOT, site_dir=_write_capability_plugin_site(root, typed_schema=True, invalid_schema=True),
                plugin_id="test.generation", work_dir=root / "work",
            )
            try:
                with self.assertRaises(PluginGenerationError) as caught:
                    generation.start()
                self.assertEqual(caught.exception.reason, "invalid_schema")
                self.assertEqual(caught.exception.schema_errors[0]["field"], "$defs.cell.properties.count.minimum")
                self.assertFalse(generation.running)
            finally:
                generation.stop()

    def test_generation_starts_reports_health_and_drains(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            generation = PluginGenerationProcess(
                project_root=PROJECT_ROOT,
                site_dir=_write_plugin_site(root),
                plugin_id="test.generation",
                work_dir=root / "work",
            )

            ready = generation.start()
            self.assertTrue(ready["ok"])
            self.assertEqual(ready["protocol"], PLUGIN_GENERATION_PROTOCOL)
            self.assertEqual(ready["plugin_id"], "test.generation")
            self.assertGreater(ready["startup_ms"], 0)
            self.assertTrue(generation.running)
            public_status = generation.public_status_snapshot()
            self.assertEqual(public_status["plugin_id"], "test.generation")
            self.assertEqual(public_status["status"], "active")
            self.assertEqual(
                public_status["contribution_snapshot"],
                ready["contribution_snapshot"],
            )

            health = generation.health()
            self.assertTrue(health["ok"])
            self.assertEqual(health["status"], "active")
            self.assertEqual(health["snapshot"]["plugin_count"], 1)

            stopped = generation.stop()
            self.assertTrue(stopped["ok"])
            self.assertEqual(stopped["status"], "stopped")
            self.assertFalse(generation.running)
            self.assertEqual(generation.stop()["reason"], "already_stopped")

    def test_generation_publishes_stable_prompt_blocks_once_at_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            generation = PluginGenerationProcess(
                project_root=PROJECT_ROOT,
                site_dir=_write_prompt_plugin_site(root),
                plugin_id="test.generation.prompt",
                work_dir=root / "work",
            )

            ready = generation.start()
            try:
                self.assertTrue(ready["ok"])
                self.assertEqual(
                    ready["contribution_snapshot"]["prompt_blocks"],
                    ["a-method", "z-evidence"],
                )
                self.assertEqual(
                    generation.stable_system_prompt_blocks(),
                    (
                        "Use the shortest sufficient method.",
                        "Keep evidence next to the claim.\nPreserve source meaning.",
                    ),
                )
                health = generation.health()
                self.assertTrue(health["ok"])
                self.assertEqual(
                    generation.stable_system_prompt_blocks(),
                    (
                        "Use the shortest sufficient method.",
                        "Keep evidence next to the claim.\nPreserve source meaning.",
                    ),
                )
            finally:
                generation.stop()

    def test_generation_freezes_skills_behind_path_free_mounts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            site, source_skill = _write_skill_plugin_site(root)
            generation = PluginGenerationProcess(
                project_root=PROJECT_ROOT,
                site_dir=site,
                plugin_id="test.generation.skill",
                work_dir=root / "work",
            )

            ready = generation.start()
            exported_root: Path | None = None
            try:
                self.assertTrue(ready["ok"])
                self.assertEqual(
                    ready["contribution_snapshot"]["skills"],
                    ["sample-generation-skill"],
                )
                ready_json = json.dumps(ready, ensure_ascii=False)
                self.assertNotIn(str(site), ready_json)
                self.assertNotIn(str(root / "work"), ready_json)

                contributed = generation.skill_roots()
                self.assertEqual(len(contributed), 1)
                exported_root = contributed[0].root
                self.assertNotEqual(exported_root, source_skill.resolve())
                self.assertTrue(exported_root.is_relative_to(root / "work"))

                registry = SkillRegistry(
                    bundled_root=root / "bundled",
                    managed_root=root / "managed",
                    execution_workspace_root=root / "execution",
                    contributed_roots_provider=generation.skill_roots,
                )
                catalog = registry.prompt_catalog(
                    available_tool_names={"exec_run", "load_skill"}
                )
                self.assertIn("sample-generation-skill", catalog)
                self.assertNotIn("Follow the generation-specific workflow", catalog)
                loaded = registry.load("sample-generation-skill")
                self.assertEqual(loaded.status, "loaded")
                self.assertEqual(
                    loaded.content,
                    "Follow the generation-specific workflow.",
                )
                self.assertEqual(loaded.source, "plugin:test.generation.skill")
                self.assertTrue(loaded.execution_cwd.startswith("alias:plugin_skill_"))

                workspace = root / "execution"
                workspace.mkdir(parents=True, exist_ok=True)
                executor = TrustedLocalExecutor(
                    workspace_root=workspace,
                    run_log_dir=root / "runlogs",
                    mounts=registry.mount_paths(),
                )
                self.assertEqual(
                    executor.resolve_workdir(loaded.execution_cwd),
                    exported_root,
                )

                (source_skill / "reference.txt").write_text(
                    "mutated source reference\n",
                    encoding="utf-8",
                )
                reference = registry.load(
                    "sample-generation-skill",
                    resource="reference.txt",
                )
                self.assertEqual(reference.content, "original generation reference\n")
            finally:
                generation.stop()

            self.assertEqual(generation.skill_roots(), ())
            self.assertIsNotNone(exported_root)
            self.assertFalse(exported_root.exists())

    def test_live_generations_keep_distinct_skill_versions_and_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            site, source_skill = _write_skill_plugin_site(root)
            work_dir = root / "work"
            first = PluginGenerationProcess(
                project_root=PROJECT_ROOT,
                site_dir=site,
                plugin_id="test.generation.skill",
                work_dir=work_dir,
            )
            second = PluginGenerationProcess(
                project_root=PROJECT_ROOT,
                site_dir=site,
                plugin_id="test.generation.skill",
                work_dir=work_dir,
            )

            first.start()
            try:
                (source_skill / "reference.txt").write_text(
                    "next generation reference\n",
                    encoding="utf-8",
                )
                second.start()
                try:
                    first_root = first.skill_roots()[0]
                    second_root = second.skill_roots()[0]
                    self.assertNotEqual(first_root.mount_name, second_root.mount_name)
                    self.assertNotEqual(first_root.root, second_root.root)

                    first_registry = SkillRegistry(
                        bundled_root=root / "bundled-first",
                        managed_root=root / "managed-first",
                        execution_workspace_root=root / "execution-first",
                        contributed_roots_provider=first.skill_roots,
                    )
                    second_registry = SkillRegistry(
                        bundled_root=root / "bundled-second",
                        managed_root=root / "managed-second",
                        execution_workspace_root=root / "execution-second",
                        contributed_roots_provider=second.skill_roots,
                    )
                    self.assertEqual(
                        first_registry.load(
                            "sample-generation-skill",
                            resource="reference.txt",
                        ).content,
                        "original generation reference\n",
                    )
                    self.assertEqual(
                        second_registry.load(
                            "sample-generation-skill",
                            resource="reference.txt",
                        ).content,
                        "next generation reference\n",
                    )

                    first_export = first_root.root
                    second_export = second_root.root
                    first.stop()
                    self.assertFalse(first_export.exists())
                    self.assertTrue(second_export.exists())
                    self.assertEqual(len(second.skill_roots()), 1)
                finally:
                    second.stop()
            finally:
                first.stop()

    def test_generation_dispatches_hooks_with_diagnostics_and_text_decoration(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT,
                    site_dir=_write_hook_plugin_site(root),
                    plugin_id="test.generation.hook",
                    work_dir=root / "work",
                )
                ready = generation.start()
                try:
                    self.assertTrue(ready["ok"])
                    self.assertEqual(
                        generation.registered_hook_types,
                        (BEFORE_OUTBOUND_PLAN_HOOK, BEFORE_TOOL_CALL_HOOK),
                    )
                    self.assertTrue(generation.observes(BEFORE_OUTBOUND_PLAN_HOOK))

                    result = await generation.dispatch(_generation_hook_envelope())
                    self.assertTrue(result.ok, result.failures)
                    self.assertEqual(
                        result.diagnostics,
                        (("test.generation.hook", "generation-hook-observed"),),
                    )
                    self.assertEqual(
                        result.outbound_decorations,
                        (
                            (
                                "test.generation.hook",
                                PluginOutboundDecoration(
                                    text_prefix="[fixture] ",
                                    text_suffix=" /ok",
                                ),
                            ),
                        ),
                    )

                    sync_result = await asyncio.to_thread(
                        generation.dispatch_from_consumer,
                        _generation_hook_envelope(hook_type=BEFORE_TOOL_CALL_HOOK),
                    )
                    self.assertTrue(sync_result.ok, sync_result.failures)
                    self.assertEqual(sync_result.outbound_decorations, ())
                finally:
                    generation.stop()

        asyncio.run(scenario())

    def test_generation_hook_dispatch_cancels_without_poisoning_the_generation(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT,
                    site_dir=_write_hook_plugin_site(root),
                    plugin_id="test.generation.hook",
                    work_dir=root / "work",
                )
                generation.start()
                try:
                    pending = asyncio.create_task(
                        generation.dispatch(_generation_hook_envelope(delay_ms=1_000))
                    )
                    await asyncio.sleep(0.05)
                    pending.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await pending

                    healthy = await generation.dispatch(_generation_hook_envelope())
                    self.assertTrue(healthy.ok, healthy.failures)
                    self.assertTrue(generation.health()["ok"])

                    draining = asyncio.create_task(
                        generation.dispatch(_generation_hook_envelope(delay_ms=250))
                    )
                    await asyncio.sleep(0.05)
                    stop_task = asyncio.create_task(asyncio.to_thread(generation.stop))
                    await asyncio.sleep(0.05)
                    self.assertFalse(stop_task.done())
                    delivered = await draining
                    stopped = await stop_task
                    self.assertTrue(delivered.ok, delivered.failures)
                    self.assertTrue(stopped["ok"])
                finally:
                    generation.stop()

        asyncio.run(scenario())

    def test_failed_candidate_does_not_remain_running(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            generation = PluginGenerationProcess(
                project_root=PROJECT_ROOT,
                site_dir=_write_plugin_site(root, broken=True),
                plugin_id="test.generation",
                work_dir=root / "work",
            )

            with self.assertRaises(PluginGenerationError):
                generation.start()
            self.assertFalse(generation.running)

    def test_generation_publishes_and_invokes_capabilities(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT,
                    site_dir=_write_capability_plugin_site(root),
                    plugin_id="test.generation",
                    work_dir=root / "work",
                )
                ready = generation.start()
                try:
                    self.assertEqual(len(ready["capabilities"]), 3)
                    descriptor = generation.capability_descriptors["test.generation.echo.v1"]
                    spec = build_tool_spec(descriptor)
                    native = build_openai_native_tool_from_spec(spec)
                    properties = native["function"]["parameters"]["properties"]
                    self.assertEqual(
                        properties["value"]["description"],
                        "Contract detail; " * 40 + "\nFinal: preserve  two spaces.",
                    )
                    self.assertEqual(
                        properties["notes"]["items"]["items"]["description"],
                        "Nested detail; " * 40 + "\nFinal: preserve  two spaces.",
                    )
                    self.assertEqual(
                        tuple(generation.capability_descriptors),
                        (
                            "test.generation.artifact.v1",
                            "test.generation.echo.v1",
                            "test.generation.notification.v1",
                        ),
                    )
                    result = await generation.invoke(
                        "test.generation.echo.v1",
                        {"value": "你好", "notes": [["preserve  two spaces"]]},
                        context=InvocationContext(
                            profile_user_id="owner",
                            session_id="session-1",
                            client_mode="qq_text",
                        ),
                    )
                    self.assertFalse(result.is_error)
                    self.assertEqual(result.content["value"], "你好")
                    self.assertEqual(result.content["session_id"], "session-1")
                    payload = {
                        "token_count": 1024,
                        "command": "/help",
                        "path": "F:/projects/report.json",
                        "rows": [{"id": i, "text": "中文\n" * 100} for i in range(200)],
                    }
                    large = await generation.invoke(
                        "test.generation.echo.v1",
                        {"value": "public", "payload_json": json.dumps(payload, ensure_ascii=False)},
                        context=InvocationContext("owner", "session-1", "web"),
                    )
                    self.assertFalse(large.is_error, large.reason)
                    self.assertEqual(large.content, payload)
                    missing = await generation.invoke(
                        "test.generation.missing.v1",
                        {},
                        context=InvocationContext(),
                    )
                    self.assertTrue(missing.is_error)
                    self.assertEqual(missing.status, "not_found")
                    self.assertEqual(missing.reason, "unknown_capability")
                finally:
                    generation.stop()

        asyncio.run(scenario())

    def test_generation_materializes_artifact_through_host_owned_sink(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                work_dir = root / "work"
                store = MemoryStore(root / "store")
                service = GeneratedFileService(
                    base_dir=root / "outputs",
                    store=store,
                    attachment_service=AttachmentInboxService(
                        store=store,
                        base_dir=root / "attachments",
                    ),
                )
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT,
                    site_dir=_write_capability_plugin_site(root),
                    plugin_id="test.generation",
                    work_dir=work_dir,
                )
                generation.bind_managed_artifact_sink(
                    GeneratedFileManagedArtifactSink(service)
                )
                generation.start()
                try:
                    result = await generation.invoke(
                        "test.generation.artifact.v1",
                        {"size": 17 * 1024 * 1024, "count": 2},
                        context=InvocationContext(
                            profile_user_id="owner",
                            session_id="artifact-session",
                            client_mode="qq_text",
                        ),
                    )
                    self.assertFalse(result.is_error, result.reason)
                    self.assertEqual(len(result.content["managed_artifacts"]), 2)
                    artifact = result.content["managed_artifacts"][0]
                    resolved = service.resolve_generated_artifact(
                        profile_user_id="owner",
                        session_id="artifact-session",
                        target=artifact["generated_id"],
                    )

                    self.assertEqual(result.content["report"], "ready")
                    self.assertEqual(result.value, {"report": "ready"})
                    self.assertTrue(artifact["generated_id"].startswith("generated::"))
                    self.assertNotIn("generation-artifact", artifact["generated_id"])
                    self.assertNotIn("absolute_path", artifact)
                    self.assertNotIn("storage_relpath", artifact)
                    self.assertEqual(artifact["file_size"], 17 * 1024 * 1024)
                    self.assertIsNotNone(resolved)
                    self.assertEqual(
                        Path(resolved["absolute_path"]).stat().st_size,
                        17 * 1024 * 1024,
                    )
                    self.assertEqual(
                        list((work_dir / "outbox").rglob("*.bin")),
                        [],
                    )
                    self.assertEqual(
                        list((work_dir / "outbox").rglob("*.json")),
                        [],
                    )
                finally:
                    generation.stop()

        asyncio.run(scenario())

    def test_generation_artifact_requires_bound_host_sink_and_cleans_handoff(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                work_dir = root / "work"
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT,
                    site_dir=_write_capability_plugin_site(root),
                    plugin_id="test.generation",
                    work_dir=work_dir,
                )
                generation.start()
                try:
                    result = await generation.invoke(
                        "test.generation.artifact.v1",
                        {},
                        context=InvocationContext("owner", "artifact-session", "web"),
                    )
                    self.assertTrue(result.is_error)
                    self.assertEqual(result.reason, "managed_artifact_sink_unavailable")
                    self.assertEqual(list((work_dir / "outbox").rglob("*.*")), [])
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "plugin_generation_already_started",
                    ):
                        generation.bind_managed_artifact_sink(
                            GeneratedFileManagedArtifactSink(
                                GeneratedFileService(
                                    base_dir=root / "outputs",
                                    store=MemoryStore(root / "late-store"),
                                    attachment_service=AttachmentInboxService(
                                        store=MemoryStore(root / "late-attachments-store"),
                                        base_dir=root / "late-attachments",
                                    ),
                                )
                            )
                        )
                finally:
                    generation.stop()

        asyncio.run(scenario())

    def test_stop_drains_parent_artifact_materialization(self) -> None:
        async def scenario() -> None:
            class BlockingSink:
                def __init__(self, delegate: GeneratedFileManagedArtifactSink) -> None:
                    self.delegate = delegate
                    self.started = asyncio.Event()
                    self.release = asyncio.Event()

                async def materialize(self, draft, *, context, capability_id):
                    self.started.set()
                    await self.release.wait()
                    return await self.delegate.materialize(
                        draft,
                        context=context,
                        capability_id=capability_id,
                    )

            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                store = MemoryStore(root / "store")
                service = GeneratedFileService(
                    base_dir=root / "outputs",
                    store=store,
                    attachment_service=AttachmentInboxService(store=store),
                )
                sink = BlockingSink(GeneratedFileManagedArtifactSink(service))
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT,
                    site_dir=_write_capability_plugin_site(root),
                    plugin_id="test.generation",
                    work_dir=root / "work",
                )
                generation.bind_managed_artifact_sink(sink)
                generation.start()
                invocation = asyncio.create_task(
                    generation.invoke(
                        "test.generation.artifact.v1",
                        {},
                        context=InvocationContext("owner", "drain-artifact", "web"),
                    )
                )
                await sink.started.wait()
                stop_task = asyncio.create_task(asyncio.to_thread(generation.stop))
                await asyncio.sleep(0.05)
                self.assertFalse(stop_task.done())

                sink.release.set()
                result = await invocation
                stopped = await stop_task

                self.assertFalse(result.is_error, result.reason)
                self.assertTrue(stopped["ok"])
                self.assertFalse(generation.running)

        asyncio.run(scenario())

    def test_generation_notification_uses_host_port_and_deduplicates(self) -> None:
        class RecordingPort:
            def __init__(self) -> None:
                self.intents: list[NotificationIntent] = []

            async def send(self, intent: NotificationIntent) -> NotificationResult:
                self.intents.append(intent)
                if intent.idempotency_key == "invalid-result":
                    return NotificationResult(  # type: ignore[arg-type]
                        ok=True,
                        status=object(),
                    )
                return NotificationResult(ok=True, status="delivered")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            port = RecordingPort()
            generation = PluginGenerationProcess(
                project_root=PROJECT_ROOT,
                site_dir=_write_capability_plugin_site(root),
                plugin_id="test.generation",
                work_dir=root / "work",
            )
            generation.bind_notification_port(port)
            generation.start()
            try:
                async def invoke_notifications() -> tuple[
                    CapabilityResult,
                    CapabilityResult,
                    CapabilityResult,
                ]:
                    arguments = {
                        "recipient_id": "user:123456",
                        "text": "该休息一下了",
                        "idempotency_key": "generation-notice-1",
                    }
                    context = InvocationContext("owner", "notification", "qq_text")
                    first = await generation.invoke(
                        "test.generation.notification.v1",
                        arguments,
                        context=context,
                    )
                    duplicate = await generation.invoke(
                        "test.generation.notification.v1",
                        arguments,
                        context=context,
                    )
                    invalid = await generation.invoke(
                        "test.generation.notification.v1",
                        {**arguments, "idempotency_key": "invalid-result"},
                        context=context,
                    )
                    return first, duplicate, invalid

                first, duplicate, invalid = asyncio.run(invoke_notifications())
                self.assertEqual(first.content["delivery_status"], "delivered")
                self.assertEqual(
                    duplicate.content["delivery_status"],
                    "already_delivered",
                )
                self.assertEqual(invalid.content["delivery_status"], "error")
                self.assertEqual(
                    invalid.content["delivery_reason"],
                    "invalid_notification_result",
                )
                self.assertEqual(len(port.intents), 2)
                self.assertEqual(port.intents[0].text, "该休息一下了")
            finally:
                generation.stop()
            self.assertFalse(
                any(
                    thread.name.startswith(
                        f"plugin-generation-callback:{generation.generation_id[:8]}"
                    )
                    for thread in threading.enumerate()
                )
            )

    def test_generation_notification_without_host_port_is_structured(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT,
                    site_dir=_write_capability_plugin_site(root),
                    plugin_id="test.generation",
                    work_dir=root / "work",
                )
                generation.start()
                try:
                    result = await generation.invoke(
                        "test.generation.notification.v1",
                        {
                            "recipient_id": "group:123456",
                            "text": "hello",
                            "idempotency_key": "missing-port",
                        },
                        context=InvocationContext("owner", "notification", "web"),
                    )
                    self.assertFalse(result.is_error)
                    self.assertFalse(result.content["delivery_ok"])
                    self.assertEqual(
                        result.content["delivery_status"],
                        "not_configured",
                    )
                    self.assertEqual(
                        result.content["delivery_reason"],
                        "no_notification_port_bound",
                    )
                finally:
                    generation.stop()

        asyncio.run(scenario())

    def test_background_job_notification_reaches_host_after_ready(self) -> None:
        async def scenario() -> None:
            class ObservedPort:
                def __init__(self) -> None:
                    self.intent: NotificationIntent | None = None
                    self.delivered = asyncio.Event()

                async def send(self, intent: NotificationIntent) -> NotificationResult:
                    self.intent = intent
                    self.delivered.set()
                    return NotificationResult(ok=True, status="delivered")

            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                port = ObservedPort()
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT,
                    site_dir=_write_notification_job_plugin_site(root),
                    plugin_id="test.generation.notification-job",
                    work_dir=root / "work",
                )
                generation.bind_notification_port(port)
                ready = generation.start()
                try:
                    await asyncio.wait_for(port.delivered.wait(), timeout=2.0)
                    self.assertTrue(ready["ok"])
                    self.assertEqual(ready["capabilities"], [])
                    self.assertEqual(
                        generation.registered_background_service_ids,
                        ("startup-notification",),
                    )
                    self.assertIsNotNone(port.intent)
                    self.assertEqual(port.intent.text, "background-ready")
                    health = generation.health()
                    self.assertEqual(health["status"], "active")
                    self.assertEqual(
                        health["snapshot"]["background_services"],
                        [
                            {
                                "plugin_id": "test.generation.notification-job",
                                "service_id": "startup-notification",
                                "status": "running",
                                "reason": "",
                            }
                        ],
                    )

                    stopped = generation.stop()
                    self.assertTrue(stopped["ok"])
                    self.assertEqual(
                        stopped["snapshot"]["background_services"][0]["status"],
                        "stopped",
                    )
                finally:
                    generation.stop()

        asyncio.run(scenario())

    def test_generation_rejects_candidate_with_immediately_failed_service(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            generation = PluginGenerationProcess(
                project_root=PROJECT_ROOT,
                site_dir=_write_background_lifecycle_plugin_site(
                    root,
                    immediate_failure=True,
                ),
                plugin_id="test.generation.background",
                work_dir=root / "work",
            )

            with self.assertRaises(PluginGenerationError) as rejected:
                generation.start()

            self.assertEqual(rejected.exception.reason, "plugin_runtime_failed")
            self.assertFalse(generation.running)

    def test_background_service_failure_isolated_and_visible_across_generation(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT,
                    site_dir=_write_background_lifecycle_plugin_site(root),
                    plugin_id="test.generation.background",
                    work_dir=root / "work",
                )
                ready = generation.start()
                try:
                    self.assertTrue(ready["ok"])
                    self.assertEqual(
                        generation.registered_background_service_ids,
                        ("crash", "worker"),
                    )

                    deadline = asyncio.get_running_loop().time() + 2.0
                    health = generation.health()
                    while health["status"] != "degraded":
                        if asyncio.get_running_loop().time() >= deadline:
                            self.fail("background service failure was not projected")
                        await asyncio.sleep(0.02)
                        health = generation.health()

                    services = {
                        item["service_id"]: item
                        for item in health["snapshot"]["background_services"]
                    }
                    self.assertTrue(health["ok"])
                    self.assertEqual(health["reason"], "plugin_runtime_failed")
                    self.assertEqual(services["crash"]["status"], "failed")
                    self.assertEqual(services["crash"]["reason"], "job_failed")
                    self.assertEqual(services["worker"]["status"], "running")
                    self.assertNotIn("private-service-failure", repr(health))

                    stopped = generation.stop()
                    self.assertTrue(stopped["ok"])
                    stopped_services = {
                        item["service_id"]: item
                        for item in stopped["snapshot"]["background_services"]
                    }
                    self.assertEqual(stopped_services["worker"]["status"], "stopped")
                finally:
                    generation.stop()

        asyncio.run(scenario())

    def test_generation_qq_command_broker_preserves_context_and_host_precedence(self) -> None:
        async def scenario() -> None:
            class HostHandler:
                async def handle(
                    self,
                    request: PluginQQCommandRequest,
                ) -> PluginQQCommandResult:
                    return PluginQQCommandResult(
                        handled=True,
                        reply_text=f"host:{request.args}",
                    )

            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT,
                    site_dir=_write_qq_command_plugin_site(root),
                    plugin_id="test.generation.qq-command",
                    work_dir=root / "work",
                )
                ready = generation.start()
                try:
                    self.assertTrue(ready["ok"])
                    self.assertEqual(
                        generation.registered_qq_commands,
                        ("/echo", "/silent"),
                    )
                    broker = generation.build_qq_command_broker()
                    self.assertTrue(broker.handles("/ECHO"))
                    self.assertFalse(broker.handles("/unknown"))

                    result = await broker.dispatch(
                        command="/ECHO",
                        args="hello",
                        qq_number=123456,
                        group_id=654321,
                        is_group=True,
                        idempotency_key="message-1",
                        sender_role="ADMIN",
                        profile_user_id="profile:123456",
                        session_id="qq:group:654321",
                        character_pack_id="reimu",
                    )
                    fields = result.reply_text.split("|")
                    self.assertTrue(result.handled)
                    self.assertEqual(
                        fields[:9],
                        [
                            "/ECHO",
                            "hello",
                            "123456",
                            "654321",
                            "true",
                            "admin",
                            "profile:123456",
                            "qq:group:654321",
                            "reimu",
                        ],
                    )
                    self.assertEqual(len(fields[9]), 32)
                    self.assertNotEqual(fields[9], "message-1")

                    silent = await broker.dispatch(
                        command="/silent",
                        args="",
                        qq_number=123456,
                        group_id=0,
                        is_group=False,
                    )
                    self.assertTrue(silent.handled)
                    self.assertEqual(silent.reply_text, "")

                    failed = await broker.dispatch(
                        command="/echo",
                        args="raise",
                        qq_number=123456,
                        group_id=0,
                        is_group=False,
                    )
                    self.assertTrue(failed.handled)
                    self.assertEqual(failed.reason, "handler_exception")
                    self.assertNotIn("private-command-failure", repr(failed))

                    long_reply = await broker.dispatch(
                        command="/echo",
                        args="long",
                        qq_number=123456,
                        group_id=0,
                        is_group=False,
                    )
                    self.assertEqual(len(long_reply.reply_text), 2000)

                    slow_task = asyncio.create_task(
                        broker.dispatch(
                            command="/echo",
                            args="delay:80",
                            qq_number=1,
                            group_id=0,
                            is_group=False,
                        )
                    )
                    fast_task = asyncio.create_task(
                        broker.dispatch(
                            command="/echo",
                            args="delay:10",
                            qq_number=2,
                            group_id=0,
                            is_group=False,
                        )
                    )
                    slow, fast = await asyncio.gather(slow_task, fast_task)
                    self.assertIn("delay:80|1", slow.reply_text)
                    self.assertIn("delay:10|2", fast.reply_text)

                    host_broker = generation.build_qq_command_broker(
                        host_registrations=(
                            _PluginCommandRegistration(
                                plugin_id="host",
                                command="/echo",
                                handler=HostHandler(),
                            ),
                        )
                    )
                    host_result = await host_broker.dispatch(
                        command="/echo",
                        args="wins",
                        qq_number=123456,
                        group_id=0,
                        is_group=False,
                    )
                    self.assertEqual(host_result.reply_text, "host:wins")
                finally:
                    generation.stop()

        asyncio.run(scenario())

    def test_generation_qq_command_cancellation_and_stop_drain(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT,
                    site_dir=_write_qq_command_plugin_site(root),
                    plugin_id="test.generation.qq-command",
                    work_dir=root / "work",
                )
                generation.start()
                broker = generation.build_qq_command_broker()
                try:
                    pending = asyncio.create_task(
                        broker.dispatch(
                            command="/echo",
                            args="delay:1000",
                            qq_number=1,
                            group_id=0,
                            is_group=False,
                        )
                    )
                    await asyncio.sleep(0.05)
                    pending.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await pending

                    healthy = await broker.dispatch(
                        command="/echo",
                        args="healthy",
                        qq_number=1,
                        group_id=0,
                        is_group=False,
                    )
                    self.assertTrue(healthy.handled)
                    self.assertIn("healthy", healthy.reply_text)

                    draining = asyncio.create_task(
                        broker.dispatch(
                            command="/echo",
                            args="delay:250",
                            qq_number=1,
                            group_id=0,
                            is_group=False,
                        )
                    )
                    await asyncio.sleep(0.05)
                    stop_task = asyncio.create_task(asyncio.to_thread(generation.stop))
                    await asyncio.sleep(0.05)
                    self.assertFalse(stop_task.done())
                    delivered = await draining
                    stopped = await stop_task
                    self.assertTrue(delivered.handled)
                    self.assertIn("delay:250", delivered.reply_text)
                    self.assertTrue(stopped["ok"])
                finally:
                    generation.stop()

        asyncio.run(scenario())

    def test_concurrent_notification_callbacks_are_correlated(self) -> None:
        async def scenario() -> None:
            class DelayedPort:
                async def send(self, intent: NotificationIntent) -> NotificationResult:
                    await asyncio.sleep(0.05 if intent.text == "slow" else 0.01)
                    return NotificationResult(
                        ok=True,
                        status="delivered",
                        reason=intent.text,
                    )

            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT,
                    site_dir=_write_capability_plugin_site(root),
                    plugin_id="test.generation",
                    work_dir=root / "work",
                )
                generation.bind_notification_port(DelayedPort())
                generation.start()
                try:
                    async def invoke(text: str) -> CapabilityResult:
                        return await generation.invoke(
                            "test.generation.notification.v1",
                            {
                                "recipient_id": "user:123456",
                                "text": text,
                                "idempotency_key": f"correlated-{text}",
                            },
                            context=InvocationContext("owner", "concurrent", "web"),
                        )

                    slow_task = asyncio.create_task(invoke("slow"))
                    fast_task = asyncio.create_task(invoke("fast"))
                    slow, fast = await asyncio.gather(slow_task, fast_task)
                    self.assertEqual(slow.content["delivery_reason"], "slow")
                    self.assertEqual(fast.content["delivery_reason"], "fast")
                finally:
                    generation.stop()

        asyncio.run(scenario())

    def test_notification_callback_cancellation_and_stop_drain(self) -> None:
        async def scenario() -> None:
            class BlockingPort:
                def __init__(self) -> None:
                    self.calls = 0
                    self.started = asyncio.Event()
                    self.cancelled = asyncio.Event()
                    self.second_started = asyncio.Event()
                    self.release = asyncio.Event()

                async def send(self, intent: NotificationIntent) -> NotificationResult:
                    del intent
                    self.calls += 1
                    if self.calls == 1:
                        self.started.set()
                        try:
                            await asyncio.Future()
                        except asyncio.CancelledError:
                            self.cancelled.set()
                            raise
                    self.second_started.set()
                    await self.release.wait()
                    return NotificationResult(ok=True, status="delivered")

            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                port = BlockingPort()
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT,
                    site_dir=_write_capability_plugin_site(root),
                    plugin_id="test.generation",
                    work_dir=root / "work",
                )
                generation.bind_notification_port(port)
                generation.start()
                try:
                    pending = asyncio.create_task(
                        generation.invoke(
                            "test.generation.notification.v1",
                            {
                                "recipient_id": "user:123456",
                                "text": "cancel me",
                                "idempotency_key": "cancel-notification",
                            },
                            context=InvocationContext("owner", "cancel", "web"),
                        )
                    )
                    await port.started.wait()
                    pending.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await pending
                    await asyncio.wait_for(port.cancelled.wait(), timeout=2.0)
                    healthy = await generation.invoke(
                        "test.generation.echo.v1",
                        {"value": "after-notification-cancel"},
                        context=InvocationContext(session_id="cancel"),
                    )
                    self.assertEqual(
                        healthy.content["value"],
                        "after-notification-cancel",
                    )
                    draining = asyncio.create_task(
                        generation.invoke(
                            "test.generation.notification.v1",
                            {
                                "recipient_id": "user:123456",
                                "text": "drain me",
                                "idempotency_key": "drain-notification",
                            },
                            context=InvocationContext("owner", "drain", "web"),
                        )
                    )
                    await port.second_started.wait()
                    stop_task = asyncio.create_task(asyncio.to_thread(generation.stop))
                    await asyncio.sleep(0.05)
                    self.assertFalse(stop_task.done())

                    port.release.set()
                    delivered = await draining
                    stopped = await stop_task
                    self.assertEqual(delivered.content["delivery_status"], "delivered")
                    self.assertTrue(stopped["ok"])
                finally:
                    generation.stop()

        asyncio.run(scenario())

    def test_concurrent_invocations_and_health_are_correlated(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT,
                    site_dir=_write_capability_plugin_site(root),
                    plugin_id="test.generation",
                    work_dir=root / "work",
                )
                self.assertIsNone(generation.stop_timeout_seconds)
                generation.start()
                try:
                    context = InvocationContext(session_id="concurrent")
                    slow = asyncio.create_task(
                        generation.invoke(
                            "test.generation.echo.v1",
                            {"value": "slow", "delay_ms": 500},
                            context=context,
                        )
                    )
                    await asyncio.sleep(0.05)
                    fast = await generation.invoke(
                        "test.generation.echo.v1",
                        {"value": "fast", "delay_ms": 10},
                        context=context,
                    )
                    health = await asyncio.to_thread(generation.health)

                    self.assertEqual(fast.content["value"], "fast")
                    self.assertFalse(slow.done())
                    self.assertTrue(health["ok"])
                    self.assertEqual((await slow).content["value"], "slow")
                finally:
                    generation.stop()

        asyncio.run(scenario())

    def test_cancellation_reaches_the_worker_and_generation_remains_usable(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT,
                    site_dir=_write_capability_plugin_site(root),
                    plugin_id="test.generation",
                    work_dir=root / "work",
                )
                generation.start()
                try:
                    context = InvocationContext(session_id="cancel")
                    pending = asyncio.create_task(
                        generation.invoke(
                            "test.generation.echo.v1",
                            {"value": "cancel", "delay_ms": 5_000},
                            context=context,
                        )
                    )
                    await asyncio.sleep(0.1)
                    pending.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await pending
                    result = await asyncio.wait_for(
                        generation.invoke(
                            "test.generation.echo.v1",
                            {"value": "after-cancel"},
                            context=context,
                        ),
                        timeout=2.0,
                    )
                    self.assertEqual(result.content["value"], "after-cancel")
                finally:
                    generation.stop()

        asyncio.run(scenario())

    def test_stop_drains_an_invocation_accepted_before_stop(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT,
                    site_dir=_write_capability_plugin_site(root),
                    plugin_id="test.generation",
                    work_dir=root / "work",
                )
                generation.start()
                invocation = asyncio.create_task(
                    generation.invoke(
                        "test.generation.echo.v1",
                        {"value": "drain", "delay_ms": 400},
                        context=InvocationContext(session_id="drain"),
                    )
                )
                await asyncio.sleep(0.05)
                stop_task = asyncio.create_task(asyncio.to_thread(generation.stop))
                await asyncio.sleep(0.05)
                with self.assertRaises(PluginGenerationError) as rejected:
                    await generation.invoke(
                        "test.generation.echo.v1",
                        {"value": "late"},
                        context=InvocationContext(session_id="drain"),
                    )

                self.assertEqual(rejected.exception.reason, "plugin_generation_unavailable")
                self.assertEqual((await invocation).content["value"], "drain")
                stopped = await stop_task
                self.assertTrue(stopped["ok"])
                self.assertFalse(generation.running)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
