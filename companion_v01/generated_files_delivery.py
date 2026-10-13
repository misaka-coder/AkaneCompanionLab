"""Stable generated-file helpers; implementation lives in four responsibility modules."""
from __future__ import annotations

import importlib.util  # noqa: F401 - preserve the existing module binding
import json as json
import time as time
import zipfile as zipfile
from pathlib import Path as Path
from typing import Any as Any

from .generated_file_delivery_runtime.resolution import (
    resolve_generated_file as resolve_generated_file,
    resolve_generated_file_targets as resolve_generated_file_targets,
    resolve_generated_file_any_status as resolve_generated_file_any_status,
    find_generated_file_ambiguity_candidates as find_generated_file_ambiguity_candidates,
    format_generated_file_ambiguity as format_generated_file_ambiguity,
    normalize_targets as normalize_targets,
    resolve_sendable_file as resolve_sendable_file,
    resolve_latest_sendable_file as resolve_latest_sendable_file,
    resolve_generated_sendable_file as resolve_generated_sendable_file,
    resolve_attachment_sendable_file as resolve_attachment_sendable_file,
    generated_item_to_sendable_file as generated_item_to_sendable_file,
    attachment_item_to_sendable_file as attachment_item_to_sendable_file,
    resolve_generated_media_source as resolve_generated_media_source,
    resolve_attachment_media_source as resolve_attachment_media_source,
)

from .generated_file_delivery_runtime.delivery import (
    send_file as send_file,
    sendable_file_label as sendable_file_label,
    normalize_send_targets as normalize_send_targets,
)

from .generated_file_delivery_runtime.inspection import (
    GeneratedFileReadError as GeneratedFileReadError,
    inspect_generated_file as inspect_generated_file,
    normalize_generated_inspection_section as normalize_generated_inspection_section,
    normalize_inspection_max_chars as normalize_inspection_max_chars,
    inspect_generated_file_content as inspect_generated_file_content,
    read_generated_text_material as read_generated_text_material,
    read_plain_text_file as read_plain_text_file,
    read_generated_docx as read_generated_docx,
    read_generated_xlsx as read_generated_xlsx,
    read_generated_pdf as read_generated_pdf,
    inspect_generated_zip as inspect_generated_zip,
    render_zip_file_list as render_zip_file_list,
    resolve_zip_member_name as resolve_zip_member_name,
    read_zip_member_for_inspection as read_zip_member_for_inspection,
    slice_inspection_text as slice_inspection_text,
    clip_inspection_text as clip_inspection_text,
)

from .generated_file_delivery_runtime.management import (
    manage_generated_files as manage_generated_files,
    normalize_generated_file_action as normalize_generated_file_action,
    delete_generated_file_on_disk as delete_generated_file_on_disk,
)
