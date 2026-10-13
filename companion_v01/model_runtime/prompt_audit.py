"""Opt-in request fingerprints and usage audit records without raw prompt storage."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json
import threading

from .models import ModelBundle
from ..text_utils import estimate_prompt_tokens
import config


PROMPT_AUDIT_LOCK = threading.RLock()


class PromptAuditMixin:
    """Methods use the state initialized by LLMRuntime."""

    def register_cache_usage_scope(self, cache_key: str, scope_hash: str, client_mode: str) -> None:
        """Attach host routing evidence to usage only; never alter the request."""
        if not cache_key or len(scope_hash) != 64 or any(c not in "0123456789abcdef" for c in scope_hash):
            return
        with PROMPT_AUDIT_LOCK:
            scopes = getattr(self, "_cache_usage_scopes", None)
            if scopes is None:
                scopes = self._cache_usage_scopes = {}
            scopes[cache_key] = {"conversation_scope_hash": scope_hash, "client_mode": client_mode}
            while len(scopes) > 2048:
                scopes.pop(next(iter(scopes)))

    def _record_prompt_audit_if_enabled(
        self,
        *,
        bundle: ModelBundle,
        prompt_cache_key: str,
        request_payload: dict[str, Any],
        messages: list[dict[str, Any]],
        system_extra_blocks: list[str],
        history_turns: list[dict[str, Any]] | None,
        ephemeral_turns: list[dict[str, Any]] | None,
        post_user_turns: list[dict[str, Any]] | None,
        user_prompt: str,
        user_image_count: int,
        prompt_audit_sections: list[dict[str, Any]] | None,
        stream: bool,
        json_mode: bool,
        native_tools: list[dict[str, Any]],
    ) -> None:
        if not self._should_record_prompt_audit(prompt_cache_key):
            return
        try:
            payload_sections = self._build_payload_audit_sections(
                messages=messages,
                system_extra_blocks=system_extra_blocks,
                history_turns=history_turns,
                ephemeral_turns=ephemeral_turns,
                post_user_turns=post_user_turns,
                user_prompt=user_prompt,
            )
            native_tool_schema = self._audit_text_section(
                "payload.native_tools",
                json.dumps(native_tools, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str),
            )
            record = {
                "record_type": "prompt",
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                "instance_id": str(getattr(self, "instance_id", "local-default") or "local-default"),
                "runtime_object_id": f"{id(self):x}",
                "client_object_id": f"{id(bundle.client):x}",
                "bundle_role": str(getattr(bundle.client, "_akane_bundle_role", "") or ""),
                "prompt_cache_key": str(prompt_cache_key or ""),
                "model": str(getattr(bundle, "model", "") or ""),
                "protocol": str(
                    getattr(bundle.client, "_akane_protocol", getattr(bundle.client, "protocol", "")) or ""
                ),
                "stream": bool(stream),
                "json_mode": bool(json_mode),
                "message_count": len(messages),
                "history_turn_count": len(history_turns or []),
                "ephemeral_turn_count": len(ephemeral_turns or []),
                "post_user_turn_count": len(post_user_turns or []),
                "user_image_count": max(0, int(user_image_count or 0)),
                "native_tool_count": len(native_tools),
                "native_tool_names": [
                    str((item.get("function") or {}).get("name") or item.get("name") or "").strip()
                    for item in native_tools
                    if isinstance(item, dict)
                    and str((item.get("function") or {}).get("name") or item.get("name") or "").strip()
                ],
                "native_tool_schema": native_tool_schema,
                "request_field_order": [str(name) for name in request_payload],
                "request_field_fingerprints": [
                    {**native_tool_schema, "name": "payload.field.tools"}
                    if name == "tools" and value is native_tools
                    else self._audit_text_section(
                        f"payload.field.{name}",
                        json.dumps(
                            value,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                            default=str,
                        ),
                    )
                    for name, value in request_payload.items()
                    if name != "messages"
                ],
                "message_fingerprints": [
                    {
                        "index": index,
                        "role": str(message.get("role") or "").strip().lower(),
                        **{
                            key: value
                            for key, value in self._audit_text_section(
                                f"payload.messages.{index}",
                                json.dumps(
                                    message,
                                    ensure_ascii=False,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                    default=str,
                                ),
                            ).items()
                            if key != "name"
                        },
                    }
                    for index, message in enumerate(messages)
                ],
                "payload_totals": self._sum_audit_sections(payload_sections),
                "payload_sections": payload_sections,
                "source_sections": self._normalize_prompt_audit_sections(prompt_audit_sections),
            }
            self._append_prompt_audit_record(record)
        except Exception:
            pass

    def _should_record_prompt_audit(self, prompt_cache_key: str) -> bool:
        if not bool(getattr(config, "LLM_PROMPT_AUDIT_ENABLED", False)):
            return False
        key = str(prompt_cache_key or "").strip()
        if key == "chat:final" or key.startswith("chat:final:"):
            return True
        return bool(getattr(config, "LLM_PROMPT_AUDIT_INCLUDE_AUX", False))

    def _build_payload_audit_sections(
        self,
        *,
        messages: list[dict[str, Any]],
        system_extra_blocks: list[str],
        history_turns: list[dict[str, Any]] | None,
        ephemeral_turns: list[dict[str, Any]] | None,
        post_user_turns: list[dict[str, Any]] | None,
        user_prompt: str,
    ) -> list[dict[str, Any]]:
        sections: list[dict[str, Any]] = []
        if messages:
            sections.append(
                self._audit_text_section(
                    "payload.system_message", self._flatten_message_content(messages[0].get("content"))
                )
            )
        history_text = "\n".join(
            f"{str(turn.get('role') or '').strip().lower()}:{self._flatten_message_content(turn.get('content'))}"
            for turn in history_turns or []
            if str(turn.get("content") or "").strip()
        )
        sections.append(self._audit_text_section("payload.history_turns", history_text))
        sections.append(self._audit_text_section("payload.user_prompt", user_prompt))
        ephemeral_text = "\n".join(
            f"{str(turn.get('role') or '').strip().lower()}:{self._flatten_message_content(turn.get('content'))}"
            for turn in ephemeral_turns or []
            if str(turn.get("content") or "").strip()
        )
        if ephemeral_text:
            sections.append(self._audit_text_section("payload.ephemeral_turns", ephemeral_text))
        post_user_text = "\n".join(
            f"{str(turn.get('role') or '').strip().lower()}:{self._flatten_message_content(turn.get('content'))}"
            for turn in post_user_turns or []
            if turn.get("content")
        )
        if post_user_text:
            sections.append(self._audit_text_section("payload.post_user_turns", post_user_text))
        if system_extra_blocks:
            sections.append(self._audit_text_section("payload.system_extra_blocks", "\n\n".join(system_extra_blocks)))
        return sections

    def _normalize_prompt_audit_sections(self, sections: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        if not isinstance(sections, list):
            return normalized
        for idx, item in enumerate(sections):
            if isinstance(item, dict):
                name = str(item.get("name") or f"section_{idx}").strip() or f"section_{idx}"
                text = item.get("text", "")
            else:
                name = f"section_{idx}"
                text = item
            normalized.append(self._audit_text_section(name, self._flatten_message_content(text)))
        return normalized

    def _audit_text_section(self, name: str, text: str) -> dict[str, Any]:
        raw = str(text or "")
        digest = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:16] if raw else ""
        return {
            "name": str(name or "section"),
            "chars": len(raw),
            "estimated_tokens": self._estimate_prompt_tokens(raw),
            "sha256_16": digest,
            "empty": not bool(raw),
        }

    def _estimate_prompt_tokens(self, text: str) -> int:
        return estimate_prompt_tokens(text)

    def _sum_audit_sections(self, sections: list[dict[str, Any]]) -> dict[str, int]:
        return {
            "chars": sum(int(section.get("chars") or 0) for section in sections),
            "estimated_tokens": sum(int(section.get("estimated_tokens") or 0) for section in sections),
        }

    def _append_prompt_audit_record(self, record: dict[str, Any]) -> None:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_dir = getattr(self, "log_dir", None)
        log_root = Path(log_dir) if log_dir is not None else Path(str(getattr(config, "LOG_DIR", "") or "logs"))
        path = log_root / "llm_prompt_audit" / f"{day}.jsonl"
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        with PROMPT_AUDIT_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def _append_prompt_usage_audit(
        self,
        *,
        prompt_cache_key: str,
        read_tokens: int,
        creation_tokens: int,
        input_tokens: int,
        output_tokens: int,
        response: Any,
        requested_model: str = "",
    ) -> None:
        detailed_audit = self._should_record_prompt_audit(prompt_cache_key)
        model = ""
        if isinstance(response, dict):
            model = str(response.get("model") or "")
        else:
            model = str(getattr(response, "model", "") or "")
        usage = response.get("usage") if isinstance(response, dict) else getattr(response, "usage", None)
        cache_reported = self._cache_read_reported(usage)
        with PROMPT_AUDIT_LOCK:
            scope = dict(getattr(self, "_cache_usage_scopes", {}).get(prompt_cache_key, {}))
        record = {
                "record_type": "usage",
                **scope,
                "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "instance_id": str(getattr(self, "instance_id", "local-default") or "local-default"),
                "prompt_cache_key": str(prompt_cache_key or ""),
                "model": model,
                "requested_model": str(requested_model or ""),
                "reported_input_tokens": max(0, int(input_tokens or 0)),
                "reported_output_tokens": max(0, int(output_tokens or 0)),
                "cache_read_tokens": max(0, int(read_tokens or 0)),
                "cache_read_reported": cache_reported,
                "cache_creation_tokens": max(0, int(creation_tokens or 0)),
                "cache_hit_ratio": (
                    round(max(0, int(read_tokens or 0)) / max(1, int(input_tokens or 0)), 6)
                    if cache_reported and int(input_tokens or 0) > 0
                    else None
                ),
            }
        if detailed_audit:
            self._append_prompt_audit_record(record)
        log_dir = getattr(self, "log_dir", None)
        if scope and log_dir is not None:
            # Small numeric ledger for /缓存 works without enabling full
            # request fingerprints. No prompts or conversation IDs are saved.
            path = Path(log_dir) / "cache_usage" / f"{record['ts'][:10]}.jsonl"
            with PROMPT_AUDIT_LOCK:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    def _record_responses_request_audit_if_enabled(
        self,
        *,
        bundle: ModelBundle,
        request: dict[str, Any],
    ) -> None:
        cache_key = str(request.get("prompt_cache_key") or "").strip()
        namespace = self._settings_view().prompt_cache_namespace.strip().strip(":")
        audit_key = cache_key.removeprefix(f"{namespace}:") if namespace else cache_key
        if not self._should_record_prompt_audit(audit_key):
            return
        try:
            input_items = request.get("input") if isinstance(request.get("input"), list) else []
            tools = request.get("tools") if isinstance(request.get("tools"), list) else []

            def fingerprint(name: str, value: Any) -> dict[str, Any]:
                return self._audit_text_section(
                    name,
                    json.dumps(
                        value,
                        ensure_ascii=False,
                        sort_keys=False,
                        separators=(",", ":"),
                        default=str,
                    ),
                )

            self._append_prompt_audit_record(
                {
                    "record_type": "responses_request",
                    "ts": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                    "instance_id": str(getattr(self, "instance_id", "local-default") or "local-default"),
                    "runtime_object_id": f"{id(self):x}",
                    "client_object_id": f"{id(bundle.client):x}",
                    "bundle_role": str(getattr(bundle.client, "_akane_bundle_role", "") or ""),
                    "prompt_cache_key": cache_key,
                    "model": str(request.get("model") or ""),
                    "request_field_order": [str(name) for name in request],
                    "request_field_fingerprints": [
                        fingerprint(f"responses.field.{name}", value) for name, value in request.items()
                    ],
                    "input_item_fingerprints": [
                        {
                            "index": index,
                            "type": str(item.get("type") or "") if isinstance(item, dict) else "",
                            "role": str(item.get("role") or "") if isinstance(item, dict) else "",
                            "field_order": [str(name) for name in item] if isinstance(item, dict) else [],
                            **fingerprint(f"responses.input.{index}", item),
                        }
                        for index, item in enumerate(input_items)
                    ],
                    "tool_fingerprints": [
                        {
                            "index": index,
                            "name": str(item.get("name") or "") if isinstance(item, dict) else "",
                            "field_order": [str(name) for name in item] if isinstance(item, dict) else [],
                            **{
                                key: value
                                for key, value in fingerprint(f"responses.tools.{index}", item).items()
                                if key != "name"
                            },
                        }
                        for index, item in enumerate(tools)
                    ],
                }
            )
        except Exception:
            pass
