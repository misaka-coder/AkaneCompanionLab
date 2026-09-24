from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from capcore import CapabilityResult, InvocationContext

from companion_v01.attachment_inbox import AttachmentInboxService
from companion_v01.generated_files import GeneratedFileService
from companion_v01.plugin_api import ManagedArtifactDraft, ManagedArtifactPayload
from companion_v01.plugin_generation_artifacts import GenerationArtifactOutboxSink, consume_generation_artifact
from companion_v01.plugin_managed_artifacts import (
    GeneratedFileManagedArtifactSink,
    ManagedArtifactError,
    normalize_managed_artifact_reference,
    validate_managed_artifact_draft,
)
from companion_v01.store import MemoryStore
from tests.test_plugin_managed_artifacts import ArtifactAdapter, ArtifactPlugin, CAPABILITY_ID, _descriptor, _host_for


class ArtifactLineageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = MemoryStore(self.root / "store")
        self.service = GeneratedFileService(
            base_dir=self.root / "outputs",
            store=self.store,
            attachment_service=AttachmentInboxService(store=self.store),
        )
        self.sink = GeneratedFileManagedArtifactSink(self.service)
        self.context = InvocationContext("user", "session", "web")

    def draft(self, **changes):
        return ManagedArtifactDraft(
            **{
                "data": b"content",
                "title": "report",
                "output_format": "md",
                "mime_type": "text/markdown",
                "send_to_user": False,
                **changes,
            }
        )

    async def materialize(self, draft, context=None):
        return await self.sink.materialize(draft, context=context or self.context, capability_id=CAPABILITY_ID)

    async def test_outbox_to_real_store_preserves_versions_and_sources(self):
        original = await self.materialize(self.draft())
        handle = original["generated_handle"]
        revised = self.draft(data=b"revised", source_handles=(handle,), revision_of=handle)
        outbox = GenerationArtifactOutboxSink(self.root / "outbox")
        pending = await outbox.materialize(revised, context=self.context, capability_id=CAPABILITY_ID)
        self.assertEqual(pending["revision_of"], handle)
        self.assertIsNotNone(normalize_managed_artifact_reference(pending, draft=revised, capability_id=CAPABILITY_ID))
        staged = consume_generation_artifact(self.root / "outbox", pending, capability_id=CAPABILITY_ID)
        try:
            result = await self.materialize(staged.draft)
            self.assertEqual(result["source_handles"], [handle])
            self.assertEqual(result["revision_of"], handle)
            self.assertNotIn(str(self.root), str(result))
            self.assertIsNotNone(
                normalize_managed_artifact_reference(result, draft=staged.draft, capability_id=CAPABILITY_ID)
            )
        finally:
            staged.cleanup()
        second = self.service.resolve_generated_artifact(
            profile_user_id="user", session_id="session", target=result["generated_handle"]
        )
        first = self.service.resolve_generated_artifact(profile_user_id="user", session_id="session", target=handle)
        self.assertEqual(second["version_no"], 2)
        self.assertEqual(second["version_of_generated_id"], first["generated_id"])
        self.assertIn(first["generated_id"], second["source_ids"])
        self.assertEqual(Path(first["absolute_path"]).read_bytes(), b"content")
        self.assertEqual(Path(second["absolute_path"]).read_bytes(), b"revised")
        third_ref = await self.materialize(self.draft(data=b"third", revision_of=second["generated_handle"]))
        third = self.service.resolve_generated_artifact(
            profile_user_id="user", session_id="session", target=third_ref["generated_handle"]
        )
        self.assertEqual(third["version_no"], 3)
        self.assertEqual(set(third["source_ids"]), {first["generated_id"], second["generated_id"]})

    async def test_lineage_cannot_cross_conversations_or_profiles(self):
        original = await self.materialize(self.draft())
        handle = original["generated_handle"]
        for context in (InvocationContext("other", "session", "web"), InvocationContext("user", "other", "web")):
            for lineage in ({"revision_of": handle}, {"source_handles": (handle,)}):
                with (
                    self.subTest(context=context, lineage=lineage),
                    self.assertRaisesRegex(ManagedArtifactError, "unavailable"),
                ):
                    await self.materialize(self.draft(**lineage), context)
        self.assertEqual(len(list((self.root / "outputs").rglob("*.md"))), 1)

    async def test_tampered_outbox_lineage_is_rejected(self):
        outbox = GenerationArtifactOutboxSink(self.root / "outbox")
        draft = self.draft(revision_of="gen_source")
        pending = await outbox.materialize(draft, context=self.context, capability_id=CAPABILITY_ID)
        with self.assertRaisesRegex(ManagedArtifactError, "handoff_invalid"):
            consume_generation_artifact(
                self.root / "outbox", {**pending, "revision_of": "gen_other"}, capability_id=CAPABILITY_ID
            )
        self.assertEqual(list((self.root / "outbox").iterdir()), [])

    async def test_old_outbox_without_lineage_fields_remains_compatible(self):
        outbox = GenerationArtifactOutboxSink(self.root / "outbox")
        pending = await outbox.materialize(self.draft(), context=self.context, capability_id=CAPABILITY_ID)
        metadata_path = next((self.root / "outbox").glob("*.json"))
        metadata = json.loads(metadata_path.read_text())
        metadata.pop("source_handles")
        metadata.pop("revision_of")
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        staged = consume_generation_artifact(self.root / "outbox", pending, capability_id=CAPABILITY_ID)
        self.assertEqual(staged.draft.source_handles, ())
        self.assertEqual(staged.draft.revision_of, "")
        staged.cleanup()

    async def test_lineage_requires_resource_read_permission(self):
        adapter = ArtifactAdapter(
            descriptor=_descriptor(),
            result=CapabilityResult(
                is_error=False, status="ok", content=ManagedArtifactPayload({}, self.draft(revision_of="gen_source"))
            ),
        )
        host = _host_for(ArtifactPlugin(adapter))
        host.bind_managed_artifact_sink(self.sink)
        await host.start()
        try:
            result = await host.invoke(CAPABILITY_ID, {}, context=self.context)
            self.assertTrue(result.is_error)
            self.assertEqual(result.reason, "managed_artifact_lineage_resource_permission_required")
        finally:
            await host.stop()
        self.assertEqual(list((self.root / "outputs").rglob("*.md")), [])

    def test_only_bounded_opaque_handles_are_allowed(self):
        for args in (
            {"source_handles": ["gen_source"]},
            {"source_handles": ("latest",)},
            {"source_handles": ("gen_source", "gen_source")},
            {"revision_of": "latest"},
            {"revision_of": "file_source"},
            {"revision_of": "gen_../../source"},
        ):
            with self.subTest(args=args), self.assertRaises(ManagedArtifactError):
                validate_managed_artifact_draft(self.draft(**args))


if __name__ == "__main__":
    unittest.main()
