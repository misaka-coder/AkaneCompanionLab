from __future__ import annotations

# Retain the optional-dependency patch point exposed by the original facade.
import importlib.util
from pathlib import Path
from typing import Any, Callable

from .image_materials import sniff_supported_image_media_type
from .store import MemoryStore

from .attachment_inbox_runtime.definitions import (
    ACTIVE_ATTACHMENT_STATUSES,
    READY_ATTACHMENT_STATUSES,
    AUTO_FOCUS_BATCH_SECONDS,
    DEFAULT_WORKSPACE_CHAR_BUDGET,
    WORKSPACE_ITEM_CHAR_BUDGET,
    WORKSPACE_MAX_TARGETS,
    AUTO_FOCUS_MAX_ITEMS,
    INTERNAL_ATTACHMENT_DETAIL_KEYS,
    NATIVE_IMAGE_MEDIA_TYPES,
    logger,
)
from .attachment_inbox_runtime.storage import AttachmentStorageMixin
from .attachment_inbox_runtime.sections import AttachmentSectionsMixin
from .attachment_inbox_runtime.text_reading import AttachmentTextReadingMixin
from .attachment_inbox_runtime.document_reading import AttachmentDocumentReadingMixin
from .attachment_inbox_runtime.section_selection import AttachmentSectionSelectionMixin
from .attachment_inbox_runtime.section_paging import AttachmentSectionPagingMixin
from .attachment_inbox_runtime.section_cache import AttachmentSectionCache
from .attachment_inbox_runtime.context import AttachmentContextMixin
from .attachment_inbox_runtime.focus_rendering import AttachmentFocusRenderingMixin
from .attachment_inbox_runtime.card_rendering import AttachmentCardRenderingMixin
from .attachment_inbox_runtime.followups import AttachmentFollowupsMixin
from .attachment_inbox_runtime.labels import AttachmentLabelsMixin
from .attachment_inbox_runtime.lifecycle import AttachmentLifecycleMixin
from .attachment_inbox_runtime.workspace import AttachmentWorkspaceMixin
from .attachment_inbox_runtime.cleanup import AttachmentCleanupMixin
from .attachment_inbox_runtime.matching import AttachmentMatchingMixin
from .attachment_inbox_runtime.target_selection import AttachmentTargetSelectionMixin
from .attachment_inbox_runtime.images import AttachmentImagesMixin


class AttachmentInboxService(
    AttachmentSectionPagingMixin,
    AttachmentStorageMixin,
    AttachmentSectionsMixin,
    AttachmentTextReadingMixin,
    AttachmentDocumentReadingMixin,
    AttachmentSectionSelectionMixin,
    AttachmentContextMixin,
    AttachmentFocusRenderingMixin,
    AttachmentCardRenderingMixin,
    AttachmentFollowupsMixin,
    AttachmentLabelsMixin,
    AttachmentLifecycleMixin,
    AttachmentWorkspaceMixin,
    AttachmentCleanupMixin,
    AttachmentMatchingMixin,
    AttachmentTargetSelectionMixin,
    AttachmentImagesMixin,
):
    """Session-scoped material workspace for user-provided files.

    Items here are short-lived source materials from clients such as QQ,
    desktop pet drag/drop, or URL imports. They are not gifts, character
    resources, generated outputs, or long-term memory.
    """

    def __init__(
        self,
        *,
        store: MemoryStore,
        base_dir: Path | None = None,
        legacy_base_dirs: list[Path] | tuple[Path, ...] | None = None,
        workspace_uri_resolver: Callable[[str], Path | None] | None = None,
        material_trace_recorder: Callable[..., Any] | None = None,
    ) -> None:
        self.store = store
        self.base_dir = Path(base_dir) if base_dir is not None else None
        self.legacy_base_dirs = [
            Path(item) for item in list(legacy_base_dirs or []) if self.base_dir is None or Path(item) != self.base_dir
        ]
        self.workspace_uri_resolver = workspace_uri_resolver
        self.material_trace_recorder = material_trace_recorder
        self._material_timeline_healthy = callable(material_trace_recorder)
        self._section_cache = AttachmentSectionCache()
