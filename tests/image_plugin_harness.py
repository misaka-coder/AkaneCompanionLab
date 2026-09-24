"""Isolated install/invoke harness; no real credentials or implicit provider calls."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from companion_v01.extension_management import ExtensionManagementService, PluginSelectionStore
from companion_v01.local_capability_config import save_capability_approval_mode
from companion_v01.plugin_generation_candidate import PluginGenerationCandidateBuilder
from companion_v01.plugin_generation_runtime import PluginGenerationRuntime
from companion_v01.plugin_installation import ManagedPluginArtifactStore
from companion_v01.plugin_managed_artifacts import GeneratedFileManagedArtifactSink
from companion_v01.plugin_market import StaticPluginMarket
from companion_v01.plugin_resources import GeneratedFileResourceProvider
from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge
from companion_v01.plugin_capability_calls import EnginePluginCapabilityProvider
from companion_v01.capability_approval import CapabilityApprovalStore
from companion_v01.tool_handlers.core import ToolExecutionContext
from scripts.build_plugin_market import build_market
from tests.test_plugin_resources import services

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ID = "akane.image-generation"
CAPABILITY_ID = PLUGIN_ID + ".run.v1"
SERVICE_CAPABILITY_ID = PLUGIN_ID + ".service.image_generation.v1.generate"


class ImageHarness:
    plugin_id = PLUGIN_ID
    capability_id = CAPABILITY_ID

    def __init__(self, root, connection_provider, dependency_wheelhouse=None):
        self.root = Path(root)
        self.connection_provider = connection_provider
        self.dependency_wheelhouse = dependency_wheelhouse

    async def start(self):
        root = self.root
        manifest = getattr(self, "market_manifest", ROOT / "plugins/market.toml")
        index = getattr(self, "market_index", None)
        if index is None:
            index = await asyncio.to_thread(build_market, root / "market", manifest=manifest)
        self.artifacts = ManagedPluginArtifactStore(
            root / "artifacts",
            instance_id="image-test",
            project_root=ROOT,
            dependency_wheelhouse=self.dependency_wheelhouse,
        )
        selections = PluginSelectionStore(root / "selections.json", defaults=(), instance_id="image-test")
        self.selections = selections
        self.runtime = PluginGenerationRuntime(
            selections.load(),
            candidate_builder=PluginGenerationCandidateBuilder(
                source_resolver=self.artifacts,
                project_root=ROOT,
                work_root=root / "workers",
                plugin_storage_data_root=root / "data",
                plugin_storage_instance_id="image-test",
                service_bindings_provider=selections.load_service_bindings,
            ),
        )
        _, self.attachments, self.files = services(root)
        self.runtime.bind_managed_artifact_sink(GeneratedFileManagedArtifactSink(self.files))
        self.runtime.bind_resource_provider(GeneratedFileResourceProvider(self.files, work_root=root / "copies"))
        if self.connection_provider is not None:
            self.runtime.bind_connection_provider(self.connection_provider)
        engine_factory = getattr(self, "engine_factory", None)
        if engine_factory is None:
            from tests.test_plugin_engine_bridge import EngineFacade
            engine_factory = EngineFacade
        self.engine = engine_factory(PluginCapabilityToolBridge(self.runtime, config_base_dir=root))
        self.engine.store, self.engine.capability_config_base_dir = self.files.store, root
        self.approvals = CapabilityApprovalStore()
        self.engine.plugin_capability_source.bind_approval_store(self.approvals)
        self.runtime.bind_capability_provider(EnginePluginCapabilityProvider(self.engine))
        self.engine._get_generated_file_service = lambda: self.files
        self.service = ExtensionManagementService(
            plugin_runtime=self.runtime,
            selection_store=selections,
            artifact_store=self.artifacts,
            market=StaticPluginMarket(index),
        )
        await self.runtime.start()
        return self

    async def install(self):
        catalog = await self.service.browse_market()
        entry = next(item for item in catalog["plugins"] if item["plugin_id"] == self.plugin_id)
        staged = await self.service.stage_market(plugin_id=self.plugin_id, digest=entry["sha256"])
        if not staged["ok"]:
            raise AssertionError(json.dumps(staged, ensure_ascii=False))
        installed = await self.service.install_stage(
            stage_id=staged["stage_id"], approved_permissions=staged["permissions"]
        )
        if not installed["ok"]:
            raise AssertionError(json.dumps(installed, ensure_ascii=False))
        return installed

    async def invoke(self, **options):
        handler = self.handlers()[self.capability_id]
        return await asyncio.to_thread(
            handler.execute,
            call=handler.normalize_call({"type": self.capability_id, **options}),
            context=ToolExecutionContext("owner", "session", 1, {}, client_mode="desktop_pet"),
        )

    def handlers(self):
        from companion_v01.mode_profiles import ModeProfileRegistry
        return self.engine._resolve_tool_handlers(
            client_context=ModeProfileRegistry().resolve_from_payload({"client_mode": "desktop_pet"}),
            profile_user_id="owner",
            session_id="session",
        )

    def approve_test_profile(self, profile="owner"):
        # This harness has its own temporary config root, never user settings.
        service = save_capability_approval_mode(base_dir=self.root, profile_user_id=profile,
                                               capability_id=SERVICE_CAPABILITY_ID, mode="trusted_auto_allow")
        if not service["ok"]:
            return service
        return save_capability_approval_mode(
            base_dir=self.root, profile_user_id=profile, capability_id=self.capability_id, mode="trusted_auto_allow"
        )

    def resolve(self, handle):
        return self.files.resolve_input_resource(
            profile_user_id="owner", session_id="session", target=handle, timestamp=None
        )

    def register_image(self, path, *, mime="image/png", kind="image"):
        item = self.attachments.create_pending(
            profile_user_id="owner",
            session_id="session",
            source="test",
            kind=kind,
            origin_name=path.name,
            file_ext=path.suffix.lstrip("."),
            mime_type=mime,
            storage_relpath=path.name,
            file_size=path.stat().st_size,
        )
        self.attachments.mark_ready(profile_user_id="owner", session_id="session", attachment_id=item["attachment_id"])
        return item["attachment_handle"]

    async def close(self):
        await self.runtime.stop()
