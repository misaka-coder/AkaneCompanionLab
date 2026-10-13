"""Configured search profile selection, including QQ compatibility."""

from __future__ import annotations

import re

import config

from ..core import ToolExecutionContext


class SearchIdentityMixin:
    def _resolve_runtime_profile_user_id(self, context: ToolExecutionContext) -> str:
        return self._resolve_runtime_profile_user_id_values(
            profile_user_id=context.profile_user_id,
            client_mode=context.client_mode,
        )

    def _resolve_runtime_profile_user_id_values(self, *, profile_user_id: str, client_mode: str) -> str:
        if str(client_mode or "").strip().lower() != "qq_text":
            return str(profile_user_id or "").strip() or "master"
        raw_value = str(getattr(config, "QQ_WEB_SEARCH_PROFILE_USER_ID", "") or "").strip()
        if not raw_value:
            raw_value = str(getattr(config, "WEB_OWNER_PROFILE_USER_ID", "") or "master").strip()
        if raw_value.lower() in {"conversation", "context", "current"}:
            raw_value = str(profile_user_id or "").strip()
        if not raw_value or not re.fullmatch(r"[A-Za-z0-9_.-]+", raw_value):
            return "master"
        return raw_value
