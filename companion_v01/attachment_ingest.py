"""Compatibility entry and instance state for attachment ingestion."""

from __future__ import annotations
import importlib.util
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable
import requests
import config
from .attachment_inbox import AttachmentInboxService
from .background_tasks import BackgroundTaskRunner
from .deployment_security import QQChannelRuntimeConfig
from .onebot_transport import OneBotActionTransport
from .public_url_policy import HostResolver
from .store import MemoryStore
from .vision_service import VisionObservationService

from .attachment_ingest_runtime.definitions import (
    AUDIO_MEDIA_SUFFIXES,
    AttachmentMaterializationError,
    DOCUMENT_SUFFIXES,
    IMAGE_SUFFIXES,
    MEDIA_SUFFIXES,
    QQ_ATTACHMENT_DEFAULT_MAX_BYTES,
    REMOTE_DOWNLOAD_MAX_REDIRECTS,
    REMOTE_DOWNLOAD_REDIRECT_STATUSES,
    REMOTE_MEDIA_DEFAULT_MAX_BYTES,
    REMOTE_MEDIA_DEFAULT_MAX_URLS,
    REMOTE_MEDIA_DEFAULT_REFERER,
    REMOTE_MEDIA_DEFAULT_TIMEOUT,
    REMOTE_MEDIA_DEFAULT_USER_AGENT,
    REMOTE_MEDIA_YTDLP_ALLOWED_EXTRACTORS,
    RemoteMediaDescriptor,
    TEXT_SUFFIXES,
)
from .attachment_ingest_runtime.file_support import AttachmentFileSupportMixin
from .attachment_ingest_runtime.file_cards import AttachmentFileCardsMixin
from .attachment_ingest_runtime.document_cards import AttachmentDocumentCardsMixin
from .attachment_ingest_runtime.media_cards import AttachmentMediaCardsMixin
from .attachment_ingest_runtime.qq_sources import AttachmentQQSourcesMixin
from .attachment_ingest_runtime.public_download import AttachmentPublicDownloadMixin
from .attachment_ingest_runtime.remote_descriptors import AttachmentRemoteDescriptorsMixin
from .attachment_ingest_runtime.remote_downloads import AttachmentRemoteDownloadsMixin
from .attachment_ingest_runtime.ytdlp import AttachmentYtDlpMixin
from .attachment_ingest_runtime.api import AttachmentIngestCallsMixin
from .attachment_ingest_runtime.processing import AttachmentProcessingMixin
from .attachment_ingest_runtime.retry import AttachmentRetryMixin
from .attachment_ingest_runtime.remote_media import AttachmentRemoteMediaMixin
from .attachment_ingest_runtime.failures import AttachmentFailuresMixin


logger = logging.getLogger("akane.attachment_ingest")


class AttachmentIngestService(
    AttachmentFileSupportMixin,
    AttachmentFileCardsMixin,
    AttachmentDocumentCardsMixin,
    AttachmentMediaCardsMixin,
    AttachmentQQSourcesMixin,
    AttachmentPublicDownloadMixin,
    AttachmentRemoteDescriptorsMixin,
    AttachmentRemoteDownloadsMixin,
    AttachmentYtDlpMixin,
    AttachmentIngestCallsMixin,
    AttachmentProcessingMixin,
    AttachmentRetryMixin,
    AttachmentRemoteMediaMixin,
    AttachmentFailuresMixin,
):
    def __init__(
        self,
        *,
        base_dir: Path,
        store: MemoryStore,
        attachment_service: AttachmentInboxService,
        vision_service: VisionObservationService | None,
        background_tasks: BackgroundTaskRunner | None = None,
        legacy_base_dirs: list[Path] | tuple[Path, ...] | None = None,
        ensure_storage_ready: Callable[[], Any] | None = None,
        workspace_uri_resolver: Callable[[str], Path | None] | None = None,
        qq_channel_config: QQChannelRuntimeConfig | None = None,
        public_host_resolver: HostResolver | None = None,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.legacy_base_dirs = [Path(item) for item in list(legacy_base_dirs or []) if Path(item) != self.base_dir]
        self.ensure_storage_ready = ensure_storage_ready
        self.workspace_uri_resolver = workspace_uri_resolver
        self.qq_channel_config = qq_channel_config
        transport_config = qq_channel_config or QQChannelRuntimeConfig(
            enabled=bool(getattr(config, "QQ_BRIDGE_ENABLED", False)),
            profile_ref="",
            bot_id=str(getattr(config, "QQ_BOT_QQ", "") or "").strip(),
            onebot_http_url=str(getattr(config, "QQ_ONEBOT_HTTP_URL", "http://127.0.0.1:3001") or "").strip(),
            webhook_secret="",
            onebot_access_token="",
            require_webhook_auth=False,
            require_self_id=False,
        )
        self._onebot_transport = OneBotActionTransport(transport_config)
        self.public_host_resolver = public_host_resolver
        self.store = store
        self.attachment_service = attachment_service
        self.vision_service = vision_service
        self.background_tasks = background_tasks or BackgroundTaskRunner(
            {"attachment": int(getattr(config, "BACKGROUND_ATTACHMENT_WORKERS", 3) or 3)},
            default_workers=1,
        )
