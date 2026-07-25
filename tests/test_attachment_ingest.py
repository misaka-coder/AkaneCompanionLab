from __future__ import annotations

import base64
import json
import tempfile
import time
import unittest
import importlib.util
import types
from pathlib import Path
from typing import Any
from unittest.mock import patch
import sys

from companion_v01.attachment_inbox import AttachmentInboxService
from companion_v01.attachment_ingest import (
    AttachmentIngestService,
    AttachmentMaterializationError,
    RemoteMediaDescriptor,
)
from companion_v01.store import MemoryStore


class FakeVisionService:
    def __init__(self, store: MemoryStore) -> None:
        self.store = store
        self.scheduled: list[dict[str, Any]] = []

    def schedule_attachment_image_observation(
        self,
        *,
        attachment: dict[str, Any] | None,
        source_path: Path,
    ) -> dict[str, Any]:
        assert attachment is not None
        self.scheduled.append({"attachment": attachment, "source_path": source_path})
        self.store.update_attachment_inbox_item(
            profile_user_id=str(attachment.get("profile_user_id") or ""),
            session_id=str(attachment.get("session_id") or ""),
            attachment_id=str(attachment.get("attachment_id") or ""),
            status="ready",
            summary_title="窗边小猫",
            short_hint="一张白猫趴在窗边的照片。",
            detail={
                **(attachment.get("detail") if isinstance(attachment.get("detail"), dict) else {}),
                "type": "attachment_image_observation",
                "summary_title": "窗边小猫",
                "summary": "一张白猫趴在窗边的照片。",
                "entities": ["白猫", "窗边"],
                "mood_tags": ["安静"],
            },
            updated_at=200,
        )
        return {"status": "pending"}


class FakePeerSocket:
    def __init__(self, peer_ip: str) -> None:
        self.peer_ip = peer_ip

    def getpeername(self) -> tuple[str, int]:
        return (self.peer_ip, 443)


class FakeStreamResponse:
    def __init__(
        self,
        *,
        peer_ip: str,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = dict(headers or {})
        self.chunks = list(chunks or [])
        self.raw = types.SimpleNamespace(
            connection=types.SimpleNamespace(sock=FakePeerSocket(peer_ip)),
        )
        self.closed = False

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError("remote http failure with hidden locator")

    def iter_content(self, *, chunk_size: int):
        del chunk_size
        yield from self.chunks

    def close(self) -> None:
        self.closed = True


class FakeCookieJar:
    def __init__(self) -> None:
        self.clear_calls = 0

    def clear(self) -> None:
        self.clear_calls += 1


class FakeHttpSession:
    def __init__(self, responses: list[FakeStreamResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.cookies = FakeCookieJar()
        self.trust_env = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def get(self, url: str, **kwargs: Any) -> FakeStreamResponse:
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected HTTP request")
        return self.responses.pop(0)


class AttachmentIngestTests(unittest.TestCase):
    def test_too_large_failure_records_observed_and_configured_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store)
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=None,
            )
            item = inbox.create_pending(
                profile_user_id="master",
                session_id="qq_group_1",
                source="qq",
                kind="file",
                origin_name="彩虹.flac",
                file_size=27_461_517,
                timestamp=100,
            )

            with patch("companion_v01.attachment_ingest.config.QQ_ATTACHMENT_MAX_BYTES", 20_971_520):
                service._mark_failed(item, "attachment_too_large", timestamp=101)

            stored = store.get_attachment_inbox_item(
                profile_user_id="master",
                session_id="qq_group_1",
                attachment_id=item["attachment_id"],
            )

        self.assertIsNotNone(stored)
        failure = stored["detail"]["failure"]
        self.assertEqual(failure["code"], "attachment_too_large")
        self.assertEqual(failure["observed_bytes"], 27_461_517)
        self.assertEqual(failure["limit_bytes"], 20_971_520)

    def test_local_text_file_is_registered_and_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source" / "计划.md"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("# 今日计划\n\n- 写代码\n- 测试 QQ 附件\n", encoding="utf-8")

            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store)
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=FakeVisionService(store),  # type: ignore[arg-type]
            )

            created = service.ingest_local_file(
                profile_user_id="master",
                session_id="qq_pri_1",
                source_path=source,
                kind="document",
                origin_name="计划.md",
                timestamp=100,
            )
            self.assertEqual(created["attachment_handle"], "file_001")

            item = self._wait_for_status(
                store,
                profile_user_id="master",
                session_id="qq_pri_1",
                status="ready",
            )

            self.assertEqual(item["attachment_handle"], "file_001")
            self.assertEqual(item["summary_title"], "计划.md")
            self.assertIn("文本文件", item["short_hint"])
            self.assertIn("今日计划", item["detail"].get("headings") or [])
            self.assertIn("测试 QQ 附件", item["detail"].get("text_preview") or "")
            self.assertTrue((root / "attachments" / item["storage_relpath"]).exists())

    @unittest.skipUnless(importlib.util.find_spec("docx") is not None, "python-docx is not installed")
    def test_local_docx_file_is_registered_and_parsed(self) -> None:
        from docx import Document  # type: ignore

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source" / "计划.docx"
            source.parent.mkdir(parents=True, exist_ok=True)
            document = Document()
            document.add_heading("学习目标", level=1)
            document.add_paragraph("每天复习数学和英语。")
            table = document.add_table(rows=2, cols=2)
            table.cell(0, 0).text = "时间"
            table.cell(0, 1).text = "任务"
            table.cell(1, 0).text = "8:00"
            table.cell(1, 1).text = "背单词"
            document.save(str(source))

            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store)
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=FakeVisionService(store),  # type: ignore[arg-type]
            )
            service.ingest_local_file(
                profile_user_id="master",
                session_id="qq_pri_1",
                source_path=source,
                kind="document",
                origin_name="计划.docx",
                timestamp=100,
            )

            item = self._wait_for_status(store, profile_user_id="master", session_id="qq_pri_1", status="ready")

            self.assertEqual(item["summary_title"], "计划.docx")
            self.assertEqual(item["detail"]["file_kind"], "docx")
            self.assertIn("学习目标", item["detail"].get("headings") or [])
            self.assertIn("每天复习数学和英语", item["detail"].get("text_preview") or "")
            self.assertEqual(item["detail"]["table_count"], 1)

    @unittest.skipUnless(importlib.util.find_spec("openpyxl") is not None, "openpyxl is not installed")
    def test_local_xlsx_file_is_registered_and_parsed(self) -> None:
        from openpyxl import Workbook  # type: ignore

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source" / "成绩.xlsx"
            source.parent.mkdir(parents=True, exist_ok=True)
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "成绩表"
            sheet.append(["姓名", "数学", "英语"])
            sheet.append(["Akane", 98, 95])
            workbook.save(str(source))

            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store)
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=FakeVisionService(store),  # type: ignore[arg-type]
            )
            service.ingest_local_file(
                profile_user_id="master",
                session_id="qq_pri_1",
                source_path=source,
                kind="document",
                origin_name="成绩.xlsx",
                timestamp=100,
            )

            item = self._wait_for_status(store, profile_user_id="master", session_id="qq_pri_1", status="ready")

            self.assertEqual(item["summary_title"], "成绩.xlsx")
            self.assertEqual(item["detail"]["file_kind"], "xlsx")
            self.assertIn("成绩表", item["detail"].get("sheet_names") or [])
            self.assertIn("姓名", item["detail"].get("columns") or [])
            self.assertIn("Akane", item["detail"].get("text_preview") or "")

    def test_local_image_is_registered_and_delegated_to_vision_service(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source" / "cat.png"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"\x89PNG\r\n\x1a\nstub")

            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store)
            fake_vision = FakeVisionService(store)
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=fake_vision,  # type: ignore[arg-type]
            )

            created = service.ingest_local_file(
                profile_user_id="master",
                session_id="qq_pri_1",
                source_path=source,
                kind="image",
                origin_name="cat.png",
                timestamp=100,
            )
            self.assertEqual(created["attachment_handle"], "img_001")

            item = self._wait_for_status(
                store,
                profile_user_id="master",
                session_id="qq_pri_1",
                status="ready",
            )

            self.assertEqual(item["attachment_handle"], "img_001")
            self.assertEqual(item["summary_title"], "窗边小猫")
            self.assertEqual(item["detail"]["entities"], ["白猫", "窗边"])
            self.assertEqual(len(fake_vision.scheduled), 1)
            self.assertTrue(fake_vision.scheduled[0]["source_path"].exists())

    def test_qq_payload_cannot_authorize_local_or_workspace_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            secret = root / "private" / "secret.txt"
            secret.parent.mkdir(parents=True, exist_ok=True)
            secret.write_text("TOP_SECRET_MATERIAL", encoding="utf-8")

            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store)
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=None,
                workspace_uri_resolver=lambda _uri: secret,
            )

            with patch("companion_v01.onebot_transport.requests.Session.request") as post_mock:
                created = service.ingest_qq_attachments(
                    profile_user_id="master",
                    session_id="qq_pri_1",
                    attachments=[
                        {
                            "kind": "document",
                            "origin_name": "note.txt",
                            "file": str(secret),
                            "path": str(secret),
                            "local_path": str(secret),
                            "workspace_uri": "workspace:/private/secret.txt",
                        }
                    ],
                    timestamp=100,
                )
                self.assertEqual(len(created), 1)
                item = self._wait_for_status(
                    store,
                    profile_user_id="master",
                    session_id="qq_pri_1",
                    status="failed",
                )

            post_mock.assert_called_once()
            self.assertEqual(post_mock.call_args.kwargs["json"], {"file": str(secret)})
            self.assertEqual(item["error_message"], "attachment_source_unavailable")
            self.assertNotIn("raw_error", item["detail"]["failure"])
            serialized_item = json.dumps(item, ensure_ascii=False)
            prompt = inbox.build_prompt_context(profile_user_id="master", session_id="qq_pri_1")
            self.assertNotIn(str(secret), serialized_item)
            self.assertNotIn("TOP_SECRET_MATERIAL", serialized_item)
            self.assertNotIn(str(secret), prompt)
            self.assertNotIn("TOP_SECRET_MATERIAL", prompt)
            self.assertEqual([path for path in (root / "attachments").rglob("*") if path.is_file()], [])

            legacy = inbox.create_pending(
                profile_user_id="master",
                session_id="qq_pri_1",
                source="qq",
                kind="document",
                origin_name="legacy.txt",
                timestamp=101,
            )
            store.update_attachment_inbox_item(
                profile_user_id="master",
                session_id="qq_pri_1",
                attachment_id=legacy["attachment_id"],
                status="failed",
                error_message=f"cannot read {secret} from https://secret.invalid/file?token=abc",
                detail={},
                updated_at=102,
            )
            legacy_prompt = inbox.build_prompt_context(profile_user_id="master", session_id="qq_pri_1")
            self.assertNotIn(str(secret), legacy_prompt)
            self.assertNotIn("secret.invalid", legacy_prompt)
            self.assertNotIn("token=abc", legacy_prompt)

    def test_signed_attachment_filename_is_sanitized_before_storage_and_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store, base_dir=root / "attachments")
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=None,
            )

            class FakeOneBotResponse:
                def raise_for_status(self) -> None:
                    return None

                def json(self) -> dict[str, Any]:
                    return {"status": "failed", "retcode": 100, "data": {}}

            with patch(
                "companion_v01.onebot_transport.requests.Session.request",
                return_value=FakeOneBotResponse(),
            ):
                service.ingest_qq_attachments(
                    profile_user_id="master",
                    session_id="qq_pri_1",
                    attachments=[
                        {
                            "kind": "file",
                            "file": "https://gchat.qpic.cn/download/cat.jpg?token=topsecret&expires=999",
                            "origin_name": "cat.jpg?token=topsecret&expires=999#fragment",
                        }
                    ],
                    timestamp=100,
                )
                item = self._wait_for_status(
                    store,
                    profile_user_id="master",
                    session_id="qq_pri_1",
                    status="failed",
                )

            serialized_item = json.dumps(item, ensure_ascii=False)
            prompt = inbox.build_prompt_context(profile_user_id="master", session_id="qq_pri_1")
            self.assertEqual(item["origin_name"], "cat.jpg")
            self.assertNotIn("topsecret", serialized_item)
            self.assertNotIn("expires=999", serialized_item)
            self.assertNotIn("topsecret", prompt)
            self.assertNotIn("expires=999", prompt)

    def test_onebot_cache_path_requires_allow_root_and_logical_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cached = root / "napcat-cache" / "cat.jpg"
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(b"cache payload")
            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store)
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=None,
            )
            target = root / "attachments" / "target.jpg"

            class FakeResponse:
                def __init__(self, *, status: str, retcode: int) -> None:
                    self.status = status
                    self.retcode = retcode

                def raise_for_status(self) -> None:
                    return None

                def json(self) -> dict[str, Any]:
                    return {
                        "status": self.status,
                        "retcode": self.retcode,
                        "data": {"path": str(cached)},
                    }

            with (
                patch("companion_v01.attachment_ingest.config.QQ_ONEBOT_CACHE_ROOTS", ""),
                patch(
                    "companion_v01.onebot_transport.requests.Session.request",
                    return_value=FakeResponse(status="ok", retcode=0),
                ),
            ):
                no_root = service._copy_from_onebot_cache(
                    item={"kind": "image", "origin_name": "cat.jpg"},
                    payload={"file": "cat.jpg"},
                    target_path=target,
                    origin_name="cat.jpg",
                )
            with (
                patch("companion_v01.attachment_ingest.config.QQ_ONEBOT_CACHE_ROOTS", str(cached.parent)),
                patch(
                    "companion_v01.onebot_transport.requests.Session.request",
                    return_value=FakeResponse(status="failed", retcode=0),
                ),
            ):
                logical_failure = service._copy_from_onebot_cache(
                    item={"kind": "image", "origin_name": "cat.jpg"},
                    payload={"file": "cat.jpg"},
                    target_path=target,
                    origin_name="cat.jpg",
                )

            self.assertIsNone(no_root)
            self.assertIsNone(logical_failure)
            self.assertFalse(target.exists())

    def test_trusted_onebot_cache_path_enforces_size_limit_without_partial_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cached = root / "napcat-cache" / "large.bin"
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(b"too large")
            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store)
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=None,
            )
            target = root / "attachments" / "target.bin"

            class FakeResponse:
                def raise_for_status(self) -> None:
                    return None

                def json(self) -> dict[str, Any]:
                    return {"status": "ok", "retcode": 0, "data": {"path": str(cached)}}

            with (
                patch("companion_v01.attachment_ingest.config.QQ_ATTACHMENT_MAX_BYTES", 4),
                patch("companion_v01.attachment_ingest.config.QQ_ONEBOT_CACHE_ROOTS", str(cached.parent)),
                patch("companion_v01.onebot_transport.requests.Session.request", return_value=FakeResponse()),
            ):
                with self.assertRaisesRegex(AttachmentMaterializationError, "attachment_too_large"):
                    service._copy_from_onebot_cache(
                        item={"kind": "file", "origin_name": "large.bin"},
                        payload={"file": "large.bin"},
                        target_path=target,
                        origin_name="large.bin",
                    )

            self.assertFalse(target.exists())
            self.assertFalse(target.with_name(f".{target.name}.part").exists())

    def test_image_uses_onebot_cache_before_direct_url_download(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cached = root / "napcat-cache" / "cat.jpg"
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(b"cached image payload")

            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store)
            fake_vision = FakeVisionService(store)
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=fake_vision,  # type: ignore[arg-type]
            )

            class FakeResponse:
                def raise_for_status(self) -> None:
                    return None

                def json(self) -> dict[str, Any]:
                    return {
                        "status": "ok",
                        "retcode": 0,
                        "data": {
                            "file": str(cached),
                            "url": "https://gchat.qpic.cn/download?bad=true",
                        },
                    }

            with (
                patch("companion_v01.attachment_ingest.config.QQ_ONEBOT_CACHE_ROOTS", str(cached.parent)),
                patch(
                    "companion_v01.onebot_transport.requests.Session.request", return_value=FakeResponse()
                ) as post_mock,
                patch("companion_v01.attachment_ingest.requests.Session") as session_mock,
            ):
                created = service.ingest_qq_attachments(
                    profile_user_id="master",
                    session_id="qq_pri_1",
                    attachments=[
                        {
                            "kind": "image",
                            "file": "cat.jpg",
                            "origin_name": "cat.jpg",
                            "url": "https://gchat.qpic.cn/download?bad=true",
                        }
                    ],
                    timestamp=100,
                )

                self.assertEqual(len(created), 1)
                item = self._wait_for_status(
                    store,
                    profile_user_id="master",
                    session_id="qq_pri_1",
                    status="ready",
                )

            post_mock.assert_called()
            session_mock.assert_not_called()
            saved_path = root / "attachments" / item["storage_relpath"]
            self.assertEqual(saved_path.read_bytes(), b"cached image payload")
            self.assertEqual(item["summary_title"], "窗边小猫")

    def test_image_materializes_authenticated_onebot_base64_without_shared_cache_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload_bytes = b"instance-owned image payload"
            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store)
            fake_vision = FakeVisionService(store)
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=fake_vision,  # type: ignore[arg-type]
            )

            class FakeResponse:
                def raise_for_status(self) -> None:
                    return None

                def json(self) -> dict[str, Any]:
                    return {
                        "status": "ok",
                        "retcode": 0,
                        "data": {
                            "file": "/app/.config/QQ/instance-private-cache/cat.jpg",
                            "url": "/app/.config/QQ/instance-private-cache/cat.jpg",
                            "base64": base64.b64encode(payload_bytes).decode("ascii"),
                        },
                    }

            with patch(
                "companion_v01.onebot_transport.requests.Session.request", return_value=FakeResponse()
            ) as post_mock:
                with patch("companion_v01.attachment_ingest.requests.Session") as session_mock:
                    created = service.ingest_qq_attachments(
                        profile_user_id="master",
                        session_id="qq_pri_1",
                        attachments=[{"kind": "image", "file": "cat.jpg", "origin_name": "cat.jpg"}],
                        timestamp=100,
                    )

                    self.assertEqual(len(created), 1)
                    item = self._wait_for_status(
                        store,
                        profile_user_id="master",
                        session_id="qq_pri_1",
                        status="ready",
                    )

            post_mock.assert_called()
            session_mock.assert_not_called()
            saved_path = root / "attachments" / item["storage_relpath"]
            self.assertEqual(saved_path.read_bytes(), payload_bytes)
            self.assertNotIn("/app/", str(saved_path).replace("\\", "/"))

    def test_onebot_base64_over_attachment_limit_is_rejected_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store)
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=FakeVisionService(store),  # type: ignore[arg-type]
            )
            target_path = root / "attachments" / "rejected.bin"

            class FakeResponse:
                def raise_for_status(self) -> None:
                    return None

                def json(self) -> dict[str, Any]:
                    return {
                        "status": "ok",
                        "retcode": 0,
                        "data": {"base64": base64.b64encode(b"too large").decode("ascii")},
                    }

            with patch("companion_v01.attachment_ingest.config.QQ_ATTACHMENT_MAX_BYTES", 4):
                with patch("companion_v01.onebot_transport.requests.Session.request", return_value=FakeResponse()):
                    result = service._copy_from_onebot_cache(
                        item={"kind": "file", "origin_name": "rejected.bin"},
                        payload={"file": "rejected.bin"},
                        target_path=target_path,
                        origin_name="rejected.bin",
                    )

            self.assertIsNone(result)
            self.assertFalse(target_path.exists())

    def test_retry_failed_image_reuses_original_handle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cached = root / "napcat-cache" / "retry.jpg"
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(b"retry image payload")

            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store)
            fake_vision = FakeVisionService(store)
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=fake_vision,  # type: ignore[arg-type]
            )
            failed = inbox.create_pending(
                profile_user_id="master",
                session_id="qq_pri_1",
                source="qq",
                kind="image",
                origin_name="retry.jpg",
                timestamp=100,
            )
            store.update_attachment_inbox_item(
                profile_user_id="master",
                session_id="qq_pri_1",
                attachment_id=failed["attachment_id"],
                status="failed",
                error_message="400 Client Error: Bad Request for url: https://gchat.qpic.cn/download?x=1",
                updated_at=110,
            )

            class FakeResponse:
                def raise_for_status(self) -> None:
                    return None

                def json(self) -> dict[str, Any]:
                    return {"status": "ok", "retcode": 0, "data": {"file": str(cached)}}

            with (
                patch("companion_v01.attachment_ingest.config.QQ_ONEBOT_CACHE_ROOTS", str(cached.parent)),
                patch("companion_v01.onebot_transport.requests.Session.request", return_value=FakeResponse()),
            ):
                result = service.retry_attachment(
                    profile_user_id="master",
                    session_id="qq_pri_1",
                    target="img_001",
                    kind="image",
                    timestamp=200,
                )

                self.assertTrue(result["ok"])
                self.assertEqual(result["status"], "retry_started")
                item = self._wait_for_status(
                    store,
                    profile_user_id="master",
                    session_id="qq_pri_1",
                    status="ready",
                )

            self.assertEqual(item["attachment_handle"], "img_001")
            self.assertEqual(item["attachment_id"], failed["attachment_id"])
            self.assertEqual(item["summary_title"], "窗边小猫")
            self.assertTrue((root / "attachments" / item["storage_relpath"]).exists())

    def test_local_media_file_gets_lightweight_media_card(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source" / "clip.mp4"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"fake video payload")

            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store)
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=FakeVisionService(store),  # type: ignore[arg-type]
            )

            def fake_run(command, **kwargs):
                class Result:
                    returncode = 0
                    stderr = ""
                    stdout = """
                    {
                      "format": {
                        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
                        "duration": "65.5",
                        "size": "2048",
                        "bit_rate": "256000"
                      },
                      "streams": [
                        {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720, "avg_frame_rate": "30/1"},
                        {"codec_type": "audio", "codec_name": "aac", "sample_rate": "44100", "channels": 2, "bit_rate": "128000"}
                      ]
                    }
                    """

                return Result()

            with (
                patch("companion_v01.attachment_ingest.shutil.which", return_value="ffprobe"),
                patch(
                    "companion_v01.attachment_ingest.subprocess.run",
                    side_effect=fake_run,
                ),
            ):
                created = service.ingest_local_file(
                    profile_user_id="master",
                    session_id="qq_pri_1",
                    source_path=source,
                    kind="file",
                    origin_name="clip.mp4",
                    timestamp=100,
                )
                self.assertEqual(created["attachment_handle"], "file_001")
                item = self._wait_for_status(
                    store,
                    profile_user_id="master",
                    session_id="qq_pri_1",
                    status="ready",
                )

            media_info = item["detail"].get("media_info") or {}
            self.assertEqual(item["attachment_handle"], "file_001")
            self.assertIn("时长 1:06", item["short_hint"])
            self.assertEqual(media_info["duration_seconds"], 65.5)
            self.assertEqual(media_info["audio"]["sample_rate"], 44100)
            self.assertEqual(media_info["video"]["width"], 1280)
            prompt = inbox.build_prompt_context(profile_user_id="master", session_id="qq_pri_1")
            self.assertIn("媒体信息", prompt)
            self.assertIn("1280x720", prompt)

    def test_fetch_media_from_url_registers_downloaded_media_into_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store, base_dir=root / "attachments")
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=FakeVisionService(store),  # type: ignore[arg-type]
                public_host_resolver=lambda *_args: ("93.184.216.34",),
            )

            source_url = "https://example.com/watch?v=1&token=topsecret"
            descriptor = RemoteMediaDescriptor(
                source_url=source_url,
                webpage_url=source_url,
                thumbnail_url="https://img.example.com/cover.jpg?token=thumbnail-secret",
                title="测试视频",
                ext="mp4",
                mime_type="video/mp4",
                kind="file",
                download_mode="yt_dlp",
                extractor="ExampleVideo",
                extractor_key="ExampleVideo",
                uploader="AkaneChannel",
                duration_seconds=12.5,
            )

            def fake_download(*, item, descriptor):
                target = root / "attachments" / "master" / "qq_pri_1" / "file_001.mp4"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"remote video payload")
                return target

            def fake_build_card(*, source_path, item, mime_type):
                return {
                    "summary_title": "测试视频.mp4",
                    "short_hint": "媒体文件 测试视频.mp4，格式 mp4，约 20 bytes。",
                    "detail": {
                        "summary": "媒体文件 测试视频.mp4，格式 mp4，约 20 bytes。",
                        "file_kind": "mp4",
                        "mime_type": mime_type,
                        "file_size": 20,
                        "media_info": {
                            "format_name": "mp4",
                            "duration_seconds": 12.5,
                            "file_size": 20,
                            "audio": {"codec": "aac", "sample_rate": 44100, "channels": 2},
                            "video": {"codec": "h264", "width": 1280, "height": 720, "fps": 30.0},
                        },
                    },
                }

            with patch.object(service, "_fetch_remote_media_descriptor", return_value=descriptor):
                with patch.object(service, "_download_remote_media", side_effect=fake_download):
                    with patch.object(service, "_build_file_card", side_effect=fake_build_card):
                        result = service.fetch_media_from_urls(
                            profile_user_id="master",
                            session_id="qq_pri_1",
                            urls=[source_url],
                            timestamp=100,
                        )

            self.assertTrue(result["ok"])
            self.assertEqual(len(result["items"]), 1)
            item = result["items"][0]
            self.assertEqual(item["attachment_handle"], "file_001")
            self.assertEqual(item["status"], "ready")
            self.assertEqual(item["source"], "remote_url")
            self.assertEqual(item["detail"]["remote_source"]["platform"], "ExampleVideo")
            self.assertEqual(item["detail"]["remote_source"]["uploader"], "AkaneChannel")
            self.assertTrue(item["source_event_id"].startswith("url_sha256:"))
            self.assertNotIn("topsecret", repr(item))
            self.assertNotIn("thumbnail-secret", repr(item))
            self.assertEqual(item["detail"]["remote_source"]["display_origin"], "https://example.com")
            self.assertNotIn("source_url", item["detail"]["remote_source"])
            self.assertNotIn("webpage_url", item["detail"]["remote_source"])
            self.assertNotIn("thumbnail_url", item["detail"]["remote_source"])

            prompt = inbox.build_prompt_context(profile_user_id="master", session_id="qq_pri_1")
            self.assertIn("平台 ExampleVideo", prompt)
            self.assertIn("发布者 AkaneChannel", prompt)
            self.assertIn("媒体信息", prompt)
            self.assertIn("链接 https://example.com", prompt)
            self.assertNotIn("topsecret", prompt)
            self.assertNotIn("thumbnail-secret", prompt)

    def test_fetch_media_from_url_clears_stale_failed_entry_for_same_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store, base_dir=root / "attachments")
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=FakeVisionService(store),  # type: ignore[arg-type]
                public_host_resolver=lambda *_args: ("93.184.216.34",),
            )
            url = "https://example.com/watch?v=stale"
            stale = store.add_attachment_inbox_item(
                profile_user_id="master",
                session_id="qq_pri_1",
                source="remote_url",
                kind="file",
                status="failed",
                origin_name="旧失败.mp4",
                source_event_id=url,
                error_message="Requested format is not available",
                timestamp=90,
            )
            descriptor = RemoteMediaDescriptor(
                source_url=url,
                webpage_url=url,
                title="测试视频",
                ext="mp4",
                mime_type="video/mp4",
                kind="file",
                download_mode="yt_dlp",
                extractor="ExampleVideo",
                extractor_key="ExampleVideo",
            )

            def fake_download(*, item, descriptor):
                target = root / "attachments" / "master" / "qq_pri_1" / "file_002.mp4"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"remote video payload")
                return target

            def fake_build_card(*, source_path, item, mime_type):
                return {
                    "summary_title": "测试视频.mp4",
                    "short_hint": "媒体文件 测试视频.mp4。",
                    "detail": {"summary": "媒体文件 测试视频.mp4。"},
                }

            with patch.object(service, "_fetch_remote_media_descriptor", return_value=descriptor):
                with patch.object(service, "_download_remote_media", side_effect=fake_download):
                    with patch.object(service, "_build_file_card", side_effect=fake_build_card):
                        result = service.fetch_media_from_urls(
                            profile_user_id="master",
                            session_id="qq_pri_1",
                            urls=[url],
                            timestamp=100,
                        )

            self.assertTrue(result["ok"])
            stale_after = store.get_attachment_inbox_item(
                profile_user_id="master",
                session_id="qq_pri_1",
                attachment_id=stale["attachment_id"],
            )
            self.assertIsNotNone(stale_after)
            self.assertEqual(stale_after["status"], "cleared")
            prompt = inbox.build_prompt_context(profile_user_id="master", session_id="qq_pri_1")
            self.assertIn("测试视频", prompt)
            self.assertNotIn("Requested format", prompt)

    def test_private_remote_url_is_rejected_before_pending_item_or_downloader(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store, base_dir=root / "attachments")
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=FakeVisionService(store),  # type: ignore[arg-type]
                public_host_resolver=lambda *_args: ("127.0.0.1",),
            )

            with patch.object(service, "_fetch_remote_media_descriptor") as descriptor_mock:
                result = service.fetch_media_from_urls(
                    profile_user_id="master",
                    session_id="qq_pri_1",
                    urls=["https://private.example/file.mp4?token=topsecret"],
                    timestamp=100,
                )

            self.assertFalse(result["ok"])
            self.assertEqual(result["items"], [])
            self.assertEqual(result["failed"][0]["url"], "https://private.example")
            self.assertNotIn("topsecret", repr(result))
            self.assertIn("出于安全原因", result["followup_context"])
            descriptor_mock.assert_not_called()
            self.assertEqual(
                store.list_attachment_inbox_items(
                    profile_user_id="master",
                    session_id="qq_pri_1",
                    limit=10,
                ),
                [],
            )

    def test_qq_private_attachment_url_fails_structurally_without_http_get_or_vision(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store, base_dir=root / "attachments")
            vision = FakeVisionService(store)
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=vision,  # type: ignore[arg-type]
                public_host_resolver=lambda *_args: ("127.0.0.1",),
            )

            class FakeOneBotResponse:
                def raise_for_status(self) -> None:
                    return None

                def json(self) -> dict[str, Any]:
                    return {"status": "failed", "retcode": 100, "data": {}}

            with (
                patch(
                    "companion_v01.onebot_transport.requests.Session.request",
                    return_value=FakeOneBotResponse(),
                ) as post_mock,
                patch("companion_v01.attachment_ingest.requests.Session") as session_mock,
            ):
                created = service.ingest_qq_attachments(
                    profile_user_id="master",
                    session_id="qq_pri_1",
                    attachments=[
                        {
                            "kind": "image",
                            "file": "private.jpg",
                            "origin_name": "private.jpg",
                            "url": "http://127.0.0.1/private?token=topsecret",
                        }
                    ],
                    timestamp=100,
                )
                self.assertEqual(len(created), 1)
                item = self._wait_for_status(
                    store,
                    profile_user_id="master",
                    session_id="qq_pri_1",
                    status="failed",
                )

            self.assertEqual(item["error_message"], "remote_url_private_address")
            self.assertIn("出于安全原因", item["short_hint"])
            self.assertNotIn("127.0.0.1", json.dumps(item, ensure_ascii=False))
            self.assertNotIn("topsecret", json.dumps(item, ensure_ascii=False))
            self.assertNotIn("127.0.0.1", inbox.build_prompt_context(profile_user_id="master", session_id="qq_pri_1"))
            self.assertEqual(vision.scheduled, [])
            self.assertEqual(post_mock.call_count, 2)
            session_mock.assert_not_called()

    def test_qq_public_attachment_url_fallback_downloads_after_onebot_miss(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store, base_dir=root / "attachments")
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=None,
                public_host_resolver=lambda *_args: ("93.184.216.34",),
            )

            class FakeOneBotResponse:
                def raise_for_status(self) -> None:
                    return None

                def json(self) -> dict[str, Any]:
                    return {"status": "failed", "retcode": 100, "data": {}}

            stream_response = FakeStreamResponse(
                peer_ip="93.184.216.34",
                headers={"Content-Length": "7"},
                chunks=[b"payload"],
            )
            session = FakeHttpSession([stream_response])
            with (
                patch(
                    "companion_v01.onebot_transport.requests.Session.request",
                    return_value=FakeOneBotResponse(),
                ) as post_mock,
                patch("companion_v01.attachment_ingest.requests.Session", return_value=session),
                patch("companion_v01.attachment_ingest.shutil.which", return_value=None),
            ):
                service.ingest_qq_attachments(
                    profile_user_id="master",
                    session_id="qq_pri_1",
                    attachments=[
                        {
                            "kind": "file",
                            "file": "clip.mp4",
                            "origin_name": "clip.mp4",
                            "url": "https://media.example/clip.mp4?token=topsecret",
                        }
                    ],
                    timestamp=100,
                )
                item = self._wait_for_status(
                    store,
                    profile_user_id="master",
                    session_id="qq_pri_1",
                    status="ready",
                )

            saved_path = root / "attachments" / item["storage_relpath"]
            self.assertEqual(saved_path.read_bytes(), b"payload")
            self.assertEqual(post_mock.call_count, 1)
            self.assertEqual(len(session.calls), 1)
            self.assertNotIn("topsecret", json.dumps(item, ensure_ascii=False))

    def test_unknown_public_webpage_does_not_enter_cookie_capable_ytdlp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store, base_dir=root / "attachments")
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=FakeVisionService(store),  # type: ignore[arg-type]
                public_host_resolver=lambda *_args: ("93.184.216.34",),
            )

            with patch("companion_v01.attachment_ingest.importlib.util.find_spec") as find_spec:
                result = service.fetch_media_from_urls(
                    profile_user_id="master",
                    session_id="qq_pri_1",
                    urls=["https://unknown.example/watch?token=topsecret"],
                    timestamp=100,
                )

            self.assertFalse(result["ok"])
            self.assertIn("不在可调用的媒体下载范围", result["followup_context"])
            self.assertNotIn("topsecret", repr(result))
            find_spec.assert_not_called()

    def test_unknown_public_media_direct_link_does_not_require_ytdlp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store, base_dir=root / "attachments")
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=None,
                public_host_resolver=lambda *_args: ("93.184.216.34",),
            )

            with patch("companion_v01.attachment_ingest.importlib.util.find_spec") as find_spec:
                descriptor = service._fetch_remote_media_descriptor(
                    url="https://unknown.example/media/clip.mp4?token=topsecret",
                )

            self.assertEqual(descriptor.download_mode, "direct")
            self.assertEqual(descriptor.extractor, "direct")
            find_spec.assert_not_called()

    def test_public_downloader_revalidates_redirects_and_writes_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store, base_dir=root / "attachments")
            addresses = {
                "media.example": ("93.184.216.34",),
                "cdn.example": ("93.184.216.35",),
            }
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=FakeVisionService(store),  # type: ignore[arg-type]
                public_host_resolver=lambda hostname, _port: addresses.get(hostname, ()),
            )
            first = FakeStreamResponse(
                peer_ip="93.184.216.34",
                status_code=302,
                headers={"Location": "https://cdn.example/final.mp4"},
            )
            second = FakeStreamResponse(
                peer_ip="93.184.216.35",
                headers={"Content-Length": "7"},
                chunks=[b"payload"],
            )
            session = FakeHttpSession([first, second])
            target = root / "attachments" / "download.mp4"

            with patch("companion_v01.attachment_ingest.requests.Session", return_value=session):
                service._download_to_path(
                    url="https://media.example/start",
                    target_path=target,
                    max_bytes=16,
                    headers={
                        "Accept": "*/*",
                        "aUtHoRiZaTiOn": "Bearer secret",
                        "CoOkIe": "session=secret",
                    },
                )

            self.assertEqual(target.read_bytes(), b"payload")
            self.assertFalse(target.with_name(".download.mp4.part").exists())
            self.assertFalse(session.trust_env)
            self.assertEqual(
                [call[0] for call in session.calls], ["https://media.example/start", "https://cdn.example/final.mp4"]
            )
            self.assertTrue(all(call[1]["allow_redirects"] is False for call in session.calls))
            self.assertTrue(
                all(
                    all(
                        key.casefold() not in {"authorization", "proxy-authorization", "cookie"}
                        for key in call[1]["headers"]
                    )
                    for call in session.calls
                )
            )
            self.assertEqual(session.cookies.clear_calls, 2)
            self.assertTrue(first.closed)
            self.assertTrue(second.closed)

    def test_public_downloader_blocks_private_redirect_before_second_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store, base_dir=root / "attachments")
            addresses = {
                "media.example": ("93.184.216.34",),
                "internal.example": ("10.0.0.8",),
            }
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=FakeVisionService(store),  # type: ignore[arg-type]
                public_host_resolver=lambda hostname, _port: addresses.get(hostname, ()),
            )
            response = FakeStreamResponse(
                peer_ip="93.184.216.34",
                status_code=302,
                headers={"Location": "http://internal.example/private"},
            )
            session = FakeHttpSession([response])
            target = root / "attachments" / "download.mp4"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"existing")

            with patch("companion_v01.attachment_ingest.requests.Session", return_value=session):
                with self.assertRaisesRegex(AttachmentMaterializationError, "remote_url_private_address"):
                    service._download_to_path(
                        url="https://media.example/start",
                        target_path=target,
                        max_bytes=16,
                    )

            self.assertEqual(len(session.calls), 1)
            self.assertEqual(target.read_bytes(), b"existing")
            self.assertFalse(target.with_name(".download.mp4.part").exists())

    def test_public_downloader_rejects_missing_or_excessive_redirects(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store, base_dir=root / "attachments")
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=None,
                public_host_resolver=lambda *_args: ("93.184.216.34",),
            )
            target = root / "attachments" / "download.mp4"

            missing_session = FakeHttpSession([FakeStreamResponse(peer_ip="93.184.216.34", status_code=302)])
            with patch(
                "companion_v01.attachment_ingest.requests.Session",
                return_value=missing_session,
            ):
                with self.assertRaisesRegex(AttachmentMaterializationError, "remote_url_redirect_missing"):
                    service._download_to_path(
                        url="https://media.example/start",
                        target_path=target,
                    )

            redirect_responses = [
                FakeStreamResponse(
                    peer_ip="93.184.216.34",
                    status_code=302,
                    headers={"Location": "/loop"},
                )
                for _index in range(4)
            ]
            limit_session = FakeHttpSession(redirect_responses)
            with patch(
                "companion_v01.attachment_ingest.requests.Session",
                return_value=limit_session,
            ):
                with self.assertRaisesRegex(AttachmentMaterializationError, "remote_url_redirect_limit"):
                    service._download_to_path(
                        url="https://media.example/start",
                        target_path=target,
                    )

            self.assertEqual(len(missing_session.calls), 1)
            self.assertEqual(len(limit_session.calls), 4)
            self.assertFalse(target.exists())
            self.assertFalse(target.with_name(".download.mp4.part").exists())

    def test_public_downloader_cleans_partial_file_on_size_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store, base_dir=root / "attachments")
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=FakeVisionService(store),  # type: ignore[arg-type]
                public_host_resolver=lambda *_args: ("93.184.216.34",),
            )
            targets_and_responses = (
                (
                    root / "attachments" / "declared.mp4",
                    FakeStreamResponse(
                        peer_ip="93.184.216.34",
                        headers={"Content-Length": "17"},
                        chunks=[b"unused"],
                    ),
                ),
                (
                    root / "attachments" / "streamed.mp4",
                    FakeStreamResponse(peer_ip="93.184.216.34", chunks=[b"12345678", b"9"]),
                ),
            )

            for target, response in targets_and_responses:
                with self.subTest(target=target.name):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b"existing")
                    session = FakeHttpSession([response])
                    with patch("companion_v01.attachment_ingest.requests.Session", return_value=session):
                        with self.assertRaisesRegex(AttachmentMaterializationError, "attachment_too_large"):
                            service._download_to_path(
                                url="https://media.example/file.mp4",
                                target_path=target,
                                max_bytes=8,
                            )
                    self.assertEqual(target.read_bytes(), b"existing")
                    self.assertFalse(target.with_name(f".{target.name}.part").exists())

    def test_remote_media_download_with_ytdlp_does_not_force_best_format(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store, base_dir=root / "attachments")
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=FakeVisionService(store),  # type: ignore[arg-type]
                public_host_resolver=lambda *_args: ("93.184.216.34",),
            )

            captured_options: dict[str, Any] = {}
            downloaded_target = root / "attachments" / "master" / "qq_pri_1" / "file_001.mp4"
            downloaded_target.parent.mkdir(parents=True, exist_ok=True)
            downloaded_target.write_bytes(b"video")

            class FakeYoutubeDL:
                def __init__(self, options: dict[str, Any]) -> None:
                    captured_options.update(options)

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb) -> bool:
                    return False

                def download(self, urls: list[str]) -> None:
                    return None

            fake_module = types.SimpleNamespace(YoutubeDL=FakeYoutubeDL)
            cookiefile = root / "bilibili-cookies.txt"
            cookiefile.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            with patch.dict(sys.modules, {"yt_dlp": fake_module}):
                with patch("companion_v01.attachment_ingest.config.REMOTE_MEDIA_YTDLP_COOKIEFILE", str(cookiefile)):
                    with patch(
                        "companion_v01.attachment_ingest.config.REMOTE_MEDIA_YTDLP_REFERER", "https://www.bilibili.com/"
                    ):
                        with patch.object(
                            service, "_locate_downloaded_remote_media_file", return_value=downloaded_target
                        ):
                            result = service._download_remote_media_with_yt_dlp(
                                descriptor=RemoteMediaDescriptor(
                                    source_url="https://b23.tv/demo",
                                    webpage_url="https://www.bilibili.com/video/BVdemo",
                                    title="测试视频",
                                    ext="mp4",
                                    mime_type="video/mp4",
                                    kind="file",
                                    download_mode="yt_dlp",
                                    extractor="BiliBili",
                                    extractor_key="BiliBili",
                                ),
                                target_dir=downloaded_target.parent,
                                handle="file_001",
                                timeout=30.0,
                                max_bytes=0,
                            )

            self.assertEqual(result, downloaded_target)
            self.assertNotIn("format", captured_options)
            self.assertEqual(captured_options["socket_timeout"], 30.0)
            self.assertEqual(captured_options["cookiefile"], str(cookiefile))
            self.assertEqual(
                captured_options["allowed_extractors"],
                ["BiliBili", "youtube", "Douyin", "Ixigua", "Kuaishou"],
            )
            self.assertEqual(captured_options["proxy"], "")
            headers = captured_options["http_headers"]
            self.assertIn("Mozilla/5.0", headers["User-Agent"])
            self.assertEqual(headers["Referer"], "https://www.bilibili.com/")

    def test_remote_media_412_error_stays_a_server_side_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store, base_dir=root / "attachments")
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=FakeVisionService(store),  # type: ignore[arg-type]
            )

            message = service._humanize_remote_fetch_error(
                "[BiliBili] 1ZJ6qBvEnZ: Unable to download JSON metadata: HTTP Error 412: Precondition Failed"
            )

            self.assertIn("平台风控", message)
            self.assertNotIn("REMOTE_MEDIA_YTDLP_COOKIEFILE", message)
            self.assertNotIn("cookies.txt", message)

    def test_bilibili_public_api_fallback_recovers_from_ytdlp_412(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store, base_dir=root / "attachments")
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=None,
                public_host_resolver=lambda *_args: ("93.184.216.34",),
            )
            view_payload = {
                "code": 0,
                "data": {
                    "bvid": "BV1Tdgh6aESA",
                    "aid": 123,
                    "cid": 456,
                    "title": "测试公开片段",
                    "duration": 42,
                    "desc": "公开视频简介",
                    "pic": "https://i0.hdslb.com/test.jpg",
                    "owner": {"name": "测试发布者", "mid": 789},
                },
            }
            play_payload = {
                "code": 0,
                "data": {
                    "format": "mp4720",
                    "durl": [
                        {
                            "url": "https://media.example/video.mp4",
                            "size": 1024,
                        }
                    ],
                },
            }
            with (
                patch.object(
                    service,
                    "_extract_remote_media_with_yt_dlp",
                    side_effect=AttachmentMaterializationError("remote_media_precondition_failed"),
                ),
                patch.object(
                    service,
                    "_resolve_bilibili_page_url",
                    return_value="https://www.bilibili.com/video/BV1Tdgh6aESA",
                ),
                patch.object(service, "_fetch_public_json", side_effect=[view_payload, play_payload]),
            ):
                descriptor = service._fetch_remote_media_descriptor(
                    url="https://b23.tv/example",
                )

            self.assertEqual(descriptor.download_mode, "bilibili_api")
            self.assertEqual(descriptor.title, "测试公开片段")
            self.assertEqual(descriptor.ext, "mp4")
            self.assertEqual(descriptor.file_size_hint, 1024)
            self.assertEqual(descriptor.media_urls, ("https://media.example/video.mp4",))

    def test_bilibili_public_api_download_uses_resolved_stream_not_page_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store, base_dir=root / "attachments")
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=None,
            )
            target = root / "video.mp4"
            descriptor = RemoteMediaDescriptor(
                source_url="https://b23.tv/example",
                webpage_url="https://www.bilibili.com/video/BV1Tdgh6aESA",
                title="测试公开片段",
                ext="mp4",
                mime_type="video/mp4",
                kind="file",
                download_mode="bilibili_api",
                media_urls=("https://media.example/video.mp4",),
            )
            with patch.object(service, "_download_to_path") as download:
                service._download_bilibili_media(
                    descriptor=descriptor,
                    target_path=target,
                    timeout=30,
                    max_bytes=1024 * 1024,
                )

            self.assertEqual(download.call_args.kwargs["url"], "https://media.example/video.mp4")
            self.assertEqual(
                download.call_args.kwargs["headers"]["Referer"],
                "https://www.bilibili.com/video/BV1Tdgh6aESA",
            )

    def test_bilibili_short_link_redirect_uses_pinned_public_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store, base_dir=root / "attachments")
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=None,
                public_host_resolver=lambda *_args: ("93.184.216.34",),
            )
            with patch.object(
                service,
                "_request_pinned_redirect",
                return_value=(302, "https://www.bilibili.com/video/BV1Tdgh6aESA"),
            ) as request_redirect:
                resolved = service._resolve_bilibili_page_url("https://b23.tv/example")

            self.assertEqual(resolved, "https://www.bilibili.com/video/BV1Tdgh6aESA")
            self.assertEqual(request_redirect.call_count, 1)
            first_target = request_redirect.call_args_list[0].kwargs["target"]
            self.assertEqual(first_target.hostname, "b23.tv")

    def test_ytdlp_common_options_reject_browser_cookie_import(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store, base_dir=root / "attachments")
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=FakeVisionService(store),  # type: ignore[arg-type]
            )

            with patch("companion_v01.attachment_ingest.config.REMOTE_MEDIA_YTDLP_COOKIEFILE", ""):
                with patch(
                    "companion_v01.attachment_ingest.config.REMOTE_MEDIA_YTDLP_COOKIES_FROM_BROWSER", "edge:Default"
                ):
                    with self.assertRaisesRegex(
                        AttachmentMaterializationError,
                        "remote_media_browser_cookies_forbidden",
                    ):
                        service._yt_dlp_common_options(timeout=12.0)

    def test_ytdlp_cookiefile_rejects_non_provider_domains(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store, base_dir=root / "attachments")
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=None,
            )
            cookiefile = root / "cookies.txt"
            cookiefile.write_text(
                "# Netscape HTTP Cookie File\n"
                ".bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\tprovider-cookie\n"
                ".internal.example\tTRUE\t/\tFALSE\t0\tsession\tinternal-cookie\n",
                encoding="utf-8",
            )

            with (
                patch(
                    "companion_v01.attachment_ingest.config.REMOTE_MEDIA_YTDLP_COOKIEFILE",
                    str(cookiefile),
                ),
                patch(
                    "companion_v01.attachment_ingest.config.REMOTE_MEDIA_YTDLP_COOKIES_FROM_BROWSER",
                    "",
                ),
            ):
                with self.assertRaisesRegex(
                    AttachmentMaterializationError,
                    "remote_media_cookie_domain_forbidden",
                ):
                    service._yt_dlp_common_options(timeout=12.0)

    def test_remote_media_browser_cookie_copy_error_is_not_delegated_to_user(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            inbox = AttachmentInboxService(store=store, base_dir=root / "attachments")
            service = AttachmentIngestService(
                base_dir=root / "attachments",
                store=store,
                attachment_service=inbox,
                vision_service=FakeVisionService(store),  # type: ignore[arg-type]
            )

            message = service._humanize_remote_fetch_error(
                "ERROR: Could not copy Chrome cookie database. See "
                "https://github.com/yt-dlp/yt-dlp/issues/7271 for more info"
            )

            self.assertIn("服务端配置问题", message)
            self.assertNotIn("REMOTE_MEDIA_YTDLP_COOKIEFILE", message)
            self.assertNotIn("cookies.txt", message)

    def _wait_for_status(
        self,
        store: MemoryStore,
        *,
        profile_user_id: str,
        session_id: str,
        status: str,
    ) -> dict[str, Any]:
        deadline = time.time() + 3
        while time.time() < deadline:
            items = store.list_attachment_inbox_items(
                profile_user_id=profile_user_id,
                session_id=session_id,
                statuses=[status],
                limit=10,
            )
            if items:
                return items[0]
            time.sleep(0.05)
        self.fail(f"attachment did not reach status {status!r}")


if __name__ == "__main__":
    unittest.main()
