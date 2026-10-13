"""Stable attachment-workspace constants shared by implementation modules."""

import logging

ACTIVE_ATTACHMENT_STATUSES = ("ready", "pending_observation", "failed")
READY_ATTACHMENT_STATUSES = ("ready",)
AUTO_FOCUS_BATCH_SECONDS = 90
DEFAULT_WORKSPACE_CHAR_BUDGET = 24000
WORKSPACE_ITEM_CHAR_BUDGET = 12000
WORKSPACE_MAX_TARGETS = 30
AUTO_FOCUS_MAX_ITEMS = WORKSPACE_MAX_TARGETS
INTERNAL_ATTACHMENT_DETAIL_KEYS = frozenset({"character_pack_id"})
NATIVE_IMAGE_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
logger = logging.getLogger("akane.attachment_inbox")

SECTION_EXTRACTION_CHAR_LIMIT = 4_000_000
