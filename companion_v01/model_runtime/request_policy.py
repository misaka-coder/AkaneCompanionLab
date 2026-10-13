"""Provider protocol, thinking, response-format and prompt-cache request policy."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse
import re

from ..runtime_settings import normalize_reasoning_effort, normalize_thinking_mode
from .models import ModelBundle
from services.gemini_native_client import gemini_thinking_config


class RequestPolicyMixin:
    """Methods use the state initialized by LLMRuntime."""

    def _is_anthropic_protocol(self, bundle: ModelBundle) -> bool:
        return self._bundle_protocol(bundle) == "anthropic"

    def _is_responses_protocol(self, bundle: ModelBundle) -> bool:
        return self._bundle_protocol(bundle) == "responses"

    def _is_gemini_protocol(self, bundle: ModelBundle) -> bool:
        return self._bundle_protocol(bundle) == "gemini"

    def _supports_stream_usage(self, bundle: ModelBundle) -> bool:
        if self._is_responses_protocol(bundle):
            return False
        return self._supports_deepseek_thinking_control(bundle)

    def _is_gemini_family_model(self, bundle: ModelBundle) -> bool:
        if self._is_gemini_protocol(bundle):
            return True
        model = str(getattr(bundle, "model", "") or "").strip().lower()
        return bool(re.search(r"(?:^|[^a-z0-9])gemini(?:[-_.]|$)", model))

    def _bundle_base_host(self, bundle: ModelBundle) -> str:
        raw = str(getattr(bundle.client, "base_url", "") or "").strip()
        if raw and "://" not in raw:
            raw = f"https://{raw}"
        try:
            return str(urlparse(raw).hostname or "").strip().lower()
        except Exception:
            return ""

    def _build_reasoning_control_kwargs(self, *, bundle: ModelBundle) -> dict[str, Any]:
        if self._is_gemini_protocol(bundle):
            thinking = gemini_thinking_config(bundle.model, self._configured_reasoning_effort(bundle))
            return {"thinking_config": thinking} if thinking else {}
        if self._is_responses_protocol(bundle):
            effort = self._configured_reasoning_effort(bundle)
            return {"reasoning": {"effort": effort}} if effort else {}
        mode = self._deepseek_thinking_mode(bundle)
        if not mode:
            return {}
        extra_body: dict[str, Any] = {"thinking": {"type": mode}}
        if mode == "enabled":
            effort = self._configured_reasoning_effort(bundle)
            if effort in {"high", "max"}:
                extra_body["reasoning_effort"] = effort
        return {"extra_body": extra_body}

    def _deepseek_thinking_mode(self, bundle: ModelBundle) -> str:
        mode = normalize_thinking_mode(self._settings_view().llm_thinking_mode)
        if mode in {"", "default", "auto"}:
            return ""
        if not self._supports_deepseek_thinking_control(bundle):
            return ""
        return mode

    def _configured_reasoning_effort(self, bundle: ModelBundle | None = None) -> str:
        client = getattr(bundle, "client", bundle)
        role = str(getattr(client, "_akane_bundle_role", "") or "").strip().lower()
        settings = self._settings_view()
        role_value = {
            "aux": settings.llm_aux_reasoning_effort,
            "chat": settings.llm_chat_reasoning_effort,
        }.get(role, "")
        return normalize_reasoning_effort(role_value or settings.llm_reasoning_effort)

    def _supports_deepseek_thinking_control(self, bundle: ModelBundle) -> bool:
        protocol = (
            str(getattr(bundle.client, "_akane_protocol", getattr(bundle.client, "protocol", "")) or "").strip().lower()
        )
        if protocol != "openai":
            return False
        model = str(getattr(bundle, "model", "") or "").strip().lower()
        if model.startswith("deepseek-"):
            return True
        base_url = str(getattr(bundle.client, "base_url", "") or "").strip()
        try:
            hostname = str(urlparse(base_url).hostname or "").strip().lower()
        except Exception:
            hostname = ""
        return hostname == "api.deepseek.com" or hostname.endswith(".deepseek.com")

    def _should_use_response_json_mode(self, bundle: ModelBundle) -> bool:
        # Force response_format=json_object for OpenAI-compatible providers so the
        # structured persona JSON is actually enforced (not just prompt-requested).
        # Historically this was ollama-only, which left DeepSeek/OpenAI chat replies
        # unconstrained and prone to malformed JSON -> fallback. Anthropic uses a
        # different API and must NOT receive response_format.
        protocol = (
            str(getattr(bundle.client, "_akane_protocol", getattr(bundle.client, "protocol", "")) or "").strip().lower()
        )
        if protocol == "responses" and self._bundle_base_host(bundle) == "api.pinaic.com":
            # PinAI currently exposes the Responses endpoint, but its
            # gpt-5.6-sol upstream returns HTTP 502 whenever
            # text.format=json_object is present. Akane's prompts still carry
            # the strict JSON contract and the normal parser/fallback remains
            # authoritative; omit only this unsupported wire hint.
            return False
        return protocol in {"ollama", "openai", "responses", "gemini"}

    def _ensure_json_keyword(self, messages: list[dict[str, Any]]) -> None:
        # OpenAI/DeepSeek reject response_format=json_object unless the messages
        # contain the literal word "json". Akane's persona prompt is Chinese and may
        # not include it, so append a short note when it's missing.
        for message in messages:
            content = message.get("content")
            if isinstance(content, str) and "json" in content.lower():
                return
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and "json" in str(part.get("text") or "").lower():
                        return
        if messages and str(messages[0].get("role") or "") == "system":
            messages[0]["content"] = (
                str(messages[0].get("content") or "").rstrip()
                + "\n（本轮只输出一个合法的 JSON object，不要输出多余文字。）"
            )

    def _build_prompt_cache_kwargs(
        self,
        *,
        bundle: ModelBundle,
        prompt_cache_key: str,
    ) -> dict[str, Any]:
        if not self._should_send_prompt_cache_hints(bundle):
            return {}

        payload: dict[str, Any] = {}
        normalized_key = self._normalize_prompt_cache_key(prompt_cache_key)
        normalized_retention = self._normalize_prompt_cache_retention(self._settings_view().prompt_cache_retention)
        if (
            normalized_retention == "24h"
            and self._is_responses_protocol(bundle)
            and self._bundle_base_host(bundle) == "api.pinaic.com"
        ):
            # PinAI accepts 24h but does not populate or reuse its cache in that
            # mode. A repeated real 12k-token probe reused 11,008 tokens with
            # in-memory and zero with 24h, so translate the no-op mode instead
            # of silently reporting cache hints as enabled.
            normalized_retention = "in-memory"
            self._record_metric("prompt_cache_retention_compat")
        if normalized_key:
            payload["prompt_cache_key"] = normalized_key
        if normalized_retention:
            payload["prompt_cache_retention"] = normalized_retention
        return payload

    def _should_send_prompt_cache_hints(self, bundle: ModelBundle) -> bool:
        settings = self._settings_view()
        if not settings.prompt_cache_hints_enabled:
            return False
        protocol = (
            str(getattr(bundle.client, "_akane_protocol", getattr(bundle.client, "protocol", "")) or "").strip().lower()
        )
        if protocol not in {"openai", "responses"}:
            return False
        if protocol == "responses":
            return True
        if settings.prompt_cache_hints_force:
            return True
        base_url = str(getattr(bundle.client, "base_url", "") or "").strip()
        return self._looks_like_official_openai_base_url(base_url)

    def _looks_like_official_openai_base_url(self, base_url: str) -> bool:
        raw = str(base_url or "").strip()
        if not raw:
            return True
        try:
            parsed = urlparse(raw)
        except Exception:
            return False
        hostname = str(parsed.hostname or "").strip().lower()
        if not hostname:
            return False
        return hostname == "api.openai.com" or hostname.endswith(".openai.com")

    def _normalize_prompt_cache_key(self, prompt_cache_key: Any) -> str:
        raw = str(prompt_cache_key or "").strip().strip(":")
        if not raw:
            return ""
        namespace = self._settings_view().prompt_cache_namespace.strip().strip(":")
        return f"{namespace}:{raw}" if namespace else raw

    def _normalize_prompt_cache_retention(self, value: Any) -> str:
        raw = str(value or "").strip().lower()
        if raw in {"in_memory", "in-memory"}:
            return "in-memory"
        return raw if raw == "24h" else ""
