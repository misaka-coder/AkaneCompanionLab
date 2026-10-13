"""Memcore input assembly operations."""
from __future__ import annotations

import hashlib
import time
from typing import Any


def _project_forward_references(value: object) -> list[dict[str, Any]]:
    """Project only model-relevant merged-forward evidence into MemCore."""

    references: list[dict[str, Any]] = []
    for raw in list(value or []) if isinstance(value, (list, tuple)) else []:
        if not isinstance(raw, dict):
            continue
        entry = {
            key: raw[key]
            for key in ("source_part_id", "forward_id", "ok", "status", "reason", "node_count")
            if key in raw
        }
        nodes: list[dict[str, Any]] = []
        for raw_node in list(raw.get("nodes") or []):
            if not isinstance(raw_node, dict):
                continue
            node = {
                key: raw_node[key]
                for key in (
                    "index",
                    "actor_id",
                    "actor_display_name",
                    "text",
                    "timestamp",
                    "message_id",
                    "attachment_count",
                )
                if key in raw_node
            }
            mentions: list[dict[str, object]] = []
            seen_mentions: set[str] = set()
            for raw_mention in list(raw_node.get("mentions") or [])[:16]:
                if not isinstance(raw_mention, dict):
                    continue
                mention_id = str(raw_mention.get("actor_id") or "").strip()[:160]
                if not mention_id or mention_id in seen_mentions:
                    continue
                seen_mentions.add(mention_id)
                mentions.append(
                    {
                        "actor_id": mention_id,
                        "display_name": str(raw_mention.get("display_name") or "").strip()[:160],
                        "is_assistant": bool(raw_mention.get("is_assistant")),
                    }
                )
            if mentions:
                node["mentions"] = mentions
            nodes.append(node)
        entry["nodes"] = nodes
        references.append(entry)
    return references


class MemcoreInputAssemblyMixin:
    def _build_timeline_input(
        self,
        *,
        role: str,
        record: dict[str, Any],
        actor_stable_id: str = "",
        actor_display_name: str = "",
        observed: bool = False,
        target_actor_id: str = "",
        target_actor_display_name: str = "",
        turn_role: str | None,
        external_event: dict[str, Any] | None = None,
    ) -> Any:
        memcore = self._memcore_module or self._import_memcore()
        raw = dict(record or {})
        source_id = str(raw.get("source_id") or "").strip()
        timestamp = int(raw.get("timestamp") or time.time())
        metadata = raw.get("memory_metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        index_in_vector = bool(raw.get("index_in_vector", True))
        is_standalone = turn_role is None
        is_external_event = external_event is not None
        if external_event is not None:
            event_type = self._kind_suffix(external_event.get("event_type"), fallback="external")
            source = str(external_event.get("source") or "").strip()
            fields = dict(external_event.get("data") or {})
            payload = dict(fields)
            if source:
                payload["source"] = source
            semantic_text = memcore.render_external_event_text(
                event_type=event_type,
                fields=fields,
                source=source,
            )
            kind = f"event.{event_type}"
            origin = memcore.EntryOrigin.ENVIRONMENT
            compatibility_role = kind
            metadata = metadata or self._external_event_metadata(external_event)
        else:
            content = str(raw.get("content") or "")
            semantic_text = content
            payload = {"text": content}
            channel = str(raw.get("channel") or metadata.get("channel") or "")
            if role == "user" and channel in {"desktop", "qq_private", "qq_group"}:
                payload["channel"] = channel
            message_id = str(raw.get("source_message_id") or raw.get("message_id") or "").strip()[:160]
            if message_id:
                payload["message_id"] = message_id
            forward_references = raw.get("forward_references")
            if not forward_references and isinstance(metadata, dict):
                forward_references = metadata.get("forward_references")
            projected_forwards = _project_forward_references(forward_references)
            if projected_forwards:
                payload["forward_references"] = projected_forwards
            addressing = raw.get("message_addressing")
            if isinstance(addressing, dict) and addressing:
                reply_reference = addressing.get("reply_reference")
                if isinstance(reply_reference, dict) and any(
                    str(reply_reference.get(key) or "").strip()
                    for key in ("actor_id", "message_id", "excerpt")
                ):
                    payload["reply_reference"] = {
                        "actor_id": str(reply_reference.get("actor_id") or "").strip()[:160],
                        "actor_display_name": str(
                            reply_reference.get("actor_display_name") or ""
                        ).strip()[:160],
                        "message_id": str(reply_reference.get("message_id") or "").strip()[:160],
                        "excerpt": str(reply_reference.get("excerpt") or "").strip()[:1000],
                    }
                    try:
                        reply_timestamp = int(float(reply_reference.get("timestamp") or 0))
                    except (TypeError, ValueError):
                        reply_timestamp = 0
                    if reply_timestamp > 0:
                        payload["reply_reference"]["timestamp"] = reply_timestamp
                    conversation_kind = str(reply_reference.get("conversation_kind") or "").strip()[:32]
                    conversation_id = str(reply_reference.get("conversation_id") or "").strip()[:160]
                    if conversation_kind:
                        payload["reply_reference"]["conversation_kind"] = conversation_kind
                    if conversation_id:
                        payload["reply_reference"]["conversation_id"] = conversation_id
                    try:
                        attachment_count = max(0, int(reply_reference.get("attachment_count") or 0))
                    except (TypeError, ValueError):
                        attachment_count = 0
                    if attachment_count > 0:
                        payload["reply_reference"]["attachment_count"] = attachment_count
                    reply_mentions: list[dict[str, object]] = []
                    seen_reply_mentions: set[str] = set()
                    for mention in list(reply_reference.get("mentions") or [])[:16]:
                        if not isinstance(mention, dict):
                            continue
                        mention_id = str(mention.get("actor_id") or "").strip()[:160]
                        if not mention_id or mention_id in seen_reply_mentions:
                            continue
                        seen_reply_mentions.add(mention_id)
                        reply_mentions.append(
                            {
                                "actor_id": mention_id,
                                "display_name": str(mention.get("display_name") or "").strip()[:160],
                                "is_assistant": bool(mention.get("is_assistant")),
                            }
                        )
                    if reply_mentions:
                        payload["reply_reference"]["mentions"] = reply_mentions
                additional_mentions: list[dict[str, str]] = []
                seen_mentions: set[str] = set()
                for mention in list(addressing.get("mentions") or [])[:16]:
                    if not isinstance(mention, dict):
                        continue
                    mention_id = str(mention.get("actor_id") or "").strip()[:160]
                    if not mention_id or mention_id in seen_mentions:
                        continue
                    seen_mentions.add(mention_id)
                    additional_mentions.append(
                        {
                            "actor_id": mention_id,
                            "display_name": str(mention.get("display_name") or "").strip()[:160],
                        }
                    )
                if additional_mentions:
                    payload["mentioned_actors"] = additional_mentions
            if role == "assistant":
                kind = "message.assistant" if is_standalone else "message.assistant.intermediate"
                origin = memcore.EntryOrigin.ASSISTANT
                compatibility_role = "assistant"
            else:
                kind = "message.user.observed" if observed else "message.user"
                origin = memcore.EntryOrigin.USER
                compatibility_role = "user.observed" if observed else "user"
        has_memory_annotation = self._has_memory_annotation(metadata)
        retrieval_policy = (
            memcore.RetrievalPolicy.NEVER
            if role == "assistant" or (is_standalone and not index_in_vector)
            else memcore.RetrievalPolicy.EXPLICIT
            if is_standalone and is_external_event
            else memcore.RetrievalPolicy.AUTO
        )
        retrieval_visibility = (
            memcore.RetrievalVisibility.NEVER
            if retrieval_policy is memcore.RetrievalPolicy.NEVER
            else memcore.RetrievalVisibility.EXPLICIT
            if is_standalone and is_external_event
            else memcore.RetrievalVisibility.DEFAULT
            if is_standalone and has_memory_annotation
            else memcore.RetrievalVisibility.EXPLICIT
        )
        return memcore.TimelineEntryInput(
            source_id=source_id,
            kind=kind,
            origin=origin,
            turn_role=turn_role,
            semantic_text=semantic_text,
            timestamp=timestamp,
            payload=payload,
            actor=(
                self._build_actor(actor_stable_id, actor_display_name) if origin is memcore.EntryOrigin.USER else None
            ),
            target_actor=(self._build_actor(target_actor_id, target_actor_display_name) if target_actor_id else None),
            memory_metadata=metadata,
            annotation_status=(
                memcore.AnnotationStatus.ACCEPTED_HOST
                if is_standalone and has_memory_annotation and not is_external_event
                else memcore.AnnotationStatus.PLAIN
                if is_standalone and not is_external_event
                else memcore.AnnotationStatus.UNANNOTATED
            ),
            retrieval_policy=retrieval_policy,
            retrieval_visibility=retrieval_visibility,
            semanticize=bool(index_in_vector and role != "assistant"),
            compatibility_role=compatibility_role,
        )

    def _build_tool_entry_pair(self, exchange: dict[str, Any]) -> tuple[Any, Any]:
        memcore = self._memcore_module or self._import_memcore()
        tool_name = str(exchange.get("tool_name") or "unknown").strip() or "unknown"
        tool = self._kind_segment(tool_name, fallback="unknown")
        prefix = str(exchange.get("source_id_prefix") or "").strip()
        effective_ts = int(exchange.get("timestamp") or time.time())
        correlation_id = str(exchange.get("tool_call_id") or "").strip()
        if not correlation_id:
            correlation_id = (
                "call_"
                + hashlib.sha256(f"{tool}|{prefix}|{effective_ts}".encode("utf-8", errors="ignore")).hexdigest()[:16]
            )
        tool_input = exchange.get("tool_input")
        result = exchange.get("result")
        retention_anchor = exchange.get("retention_anchor")
        source = str(exchange.get("source") or "").strip()
        common = {
            "correlation_id": correlation_id,
            "memory_metadata": {},
            "retrieval_policy": memcore.RetrievalPolicy.EXPLICIT,
            "retrieval_visibility": memcore.RetrievalVisibility.EXPLICIT,
            "semanticize": True,
        }
        action = memcore.TimelineEntryInput(
            source_id=f"{prefix}:tool_use" if prefix else "",
            kind=f"tool.{tool}.call",
            origin=memcore.EntryOrigin.ASSISTANT,
            turn_role=memcore.TurnRole.ACTION,
            semantic_text=memcore.render_tool_use_text(tool_input=tool_input),
            timestamp=effective_ts,
            payload={"input": tool_input},
            trace_metadata={"tool_name": tool_name, "status": "running"},
            compatibility_role=f"assistant.tool_call {tool} {correlation_id}",
            **common,
        )
        observation_trace = {
            "tool_name": tool_name,
            "status": str(exchange.get("result_status") or "success"),
        }
        if isinstance(retention_anchor, dict) and retention_anchor:
            observation_trace["retention_anchor"] = dict(retention_anchor)
        observation = memcore.TimelineEntryInput(
            source_id=f"{prefix}:tool_result" if prefix else "",
            kind=f"tool.{tool}.result",
            origin=memcore.EntryOrigin.ENVIRONMENT,
            turn_role=memcore.TurnRole.OBSERVATION,
            semantic_text=memcore.render_tool_result_text(result=result, source=source),
            timestamp=effective_ts + 1,
            payload={"output": result, "source": source},
            trace_metadata=observation_trace,
            compatibility_role=f"tool.{tool} {correlation_id}",
            **common,
        )
        return action, observation

    @staticmethod
    def _validate_tool_batch_prepared(prepared: list[tuple[Any, Any]], *, operation: str) -> None:
        """Reject whole-batch violations before any entry is appended.

        Appends are per-entry in the store; duplicate correlation/source ids would
        only fail on the second append, leaving a partial batch behind. Validating
        up front keeps the batch all-or-nothing for construction-visible problems.
        """
        action_by_correlation: dict[str, str] = {}
        source_ids: set[str] = set()
        for action, observation in prepared:
            correlation = str(getattr(action, "correlation_id", "") or "").strip()
            if correlation:
                if correlation in action_by_correlation:
                    raise ValueError("duplicate_tool_call_id_in_batch")
                action_by_correlation[correlation] = str(action.source_id or "")
            for entry in (action, observation):
                source_id = str(getattr(entry, "source_id", "") or "").strip()
                if source_id and source_id in source_ids:
                    raise ValueError("duplicate_tool_entry_source_id_in_batch")
                if source_id:
                    source_ids.add(source_id)

    def _build_actor(self, stable_id: str, display_name: str = "") -> Any | None:
        actor_id = str(stable_id or "").strip()
        if not actor_id:
            return None
        memcore = self._memcore_module or self._import_memcore()
        return memcore.Actor(
            stable_id=actor_id[:160],
            display_name=str(display_name or "").strip()[:160],
        )
