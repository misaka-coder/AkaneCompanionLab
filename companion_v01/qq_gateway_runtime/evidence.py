"""QQ evidence domain and its per-gateway state."""
from __future__ import annotations
from dataclasses import replace
from typing import Any, Mapping
from channelcore_onebot import (
    AttachmentRef,
    InboundMessage,
    resolve_quoted_message as resolve_onebot_quoted_message,
    resolve_forward_message as resolve_onebot_forward_message,
)
from .context import QQMessageContext


class QQEvidenceResolver:
    """Own evidence behavior and state; other operations use the injected port."""

    def __init__(self, *, gateway: Any) -> None:
        self.gateway = gateway

    def resolve_quoted_message_evidence(
        self,
        event: dict[str, Any],
        *,
        context: QQMessageContext,
    ) -> dict[str, Any]:
        """Project a package-resolved QQ quote into Akane's product contract.

        ``channelcore-onebot`` remains authoritative for OneBot lookup and scope
        validation.  This method only exposes the protocol-neutral evidence Akane
        needs for the current turn.  The returned attachment payload is internal
        input for the existing attachment inbox; callers must not log or expose the
        full payload because it may contain private media URLs or local paths.
        """
        _ = event  # Compatibility signature; the parsed event lives on context.
        inbound = context.inbound_message
        if inbound is None:
            return {
                "ok": False,
                "status": "inbound_context_missing",
                "quoted_message": None,
                "attachments": [],
            }

        result = resolve_onebot_quoted_message(
            inbound,
            call_action=self.gateway._call_onebot_action,
            timeout_seconds=5.0,
        )
        attachments = self._legacy_attachments(result.message.attachments if result.message is not None else ())
        status = result.status
        quoted_message: dict[str, Any] | None = None
        forward_evidence: dict[str, Any] = {}
        if result.ok and result.message is not None:
            message = result.message
            if message.chain.forwards:
                # The package has already validated quote scope and parsed its
                # ordered chain. Reuse the same forward resolver, without a
                # second parser or another model-visible lookup step.
                quoted_inbound = replace(
                    inbound, event_id=message.message_id, actor=message.actor,
                    conversation=message.conversation, chain=message.chain, timestamp=message.timestamp,
                )
                forward_evidence = self.gateway.resolve_forward_message_evidence(
                    event, context=replace(context, inbound_message=quoted_inbound),
                )
                attachments.extend(forward_evidence.get("attachments") or [])
            status = "resolved"
            actor_id = str(message.actor.id or "").strip()
            bot_account_id = str(inbound.bot_account_id or event.get("self_id") or self.gateway.bot_qq or "").strip()
            resolved_mentions = self.gateway.resolve_mention_labels(
                mentions=message.mentions,
                group_id=self.gateway._safe_int(message.conversation.id)
                if message.conversation.kind == "group"
                else 0,
            )
            quoted_message = {
                "message_id": message.message_id,
                "text": message.text,
                "actor_id": actor_id,
                "actor_label": message.actor.display_name,
                "actor_is_bot": bool(actor_id and bot_account_id and actor_id == bot_account_id),
                "timestamp": message.timestamp,
                "conversation_kind": message.conversation.kind,
                "conversation_id": message.conversation.id,
                "attachment_count": len(attachments),
                "mentions": self.gateway._project_mention_evidence(resolved_mentions),
            }
        payload: dict[str, Any] = {
            "ok": result.ok,
            "status": status,
            "quoted_message": quoted_message,
            "attachments": attachments,
        }
        if result.reason:
            payload["reason"] = result.reason
        if forward_evidence:
            payload["forwards"] = list(forward_evidence.get("forwards") or [])
        if result.message is not None or inbound.reply_to is not None:
            payload["message_id"] = (
                result.message.message_id
                if result.message is not None
                else str(inbound.reply_to.message_id or "").strip()
            )
        if result.message is not None:
            payload["attachment_count"] = len(attachments)
        return payload

    def resolve_quoted_attachments(
        self,
        event: dict[str, Any],
        *,
        context: QQMessageContext,
    ) -> dict[str, Any]:
        """Compatibility adapter for callers that only consumed quote attachments."""
        return self.gateway.resolve_quoted_message_evidence(event, context=context)

    def resolve_forward_message_evidence(
        self,
        event: dict[str, Any],
        *,
        context: QQMessageContext,
    ) -> dict[str, Any]:
        """Resolve every merged-forward reference through channelcore.

        Node text is safe product evidence.  Attachment locators remain internal
        inputs for the existing materialization boundary and must not be logged.
        """

        inbound = context.inbound_message
        if inbound is None:
            return {
                "ok": False,
                "status": "inbound_context_missing",
                "forwards": [],
                "attachments": [],
            }
        if not inbound.forwards:
            return {
                "ok": True,
                "status": "not_forward",
                "forwards": [],
                "attachments": [],
            }

        projected: list[dict[str, Any]] = []
        attachments: list[dict[str, Any]] = []
        resolved_count = 0
        source_part_ids: dict[str, str] = {}
        for part in inbound.chain.forward_parts:
            source_part_ids.setdefault(part.forward.forward_id, part.part_id)
        for forward_ref in inbound.forwards:
            inline_nodes = self._inline_forward_nodes(event, inbound, forward_ref.forward_id)
            result = None
            resolution_source = "onebot_action"
            if inline_nodes:
                # NapCat can attach parsed nodes to the authenticated event at
                # receipt time. Normalize them through the same channelcore
                # resolver as get_forward_msg, retaining its source binding.
                result = resolve_onebot_forward_message(
                    inbound,
                    forward_ref,
                    call_action=lambda *_args, **_kwargs: {
                        "status": "ok", "retcode": 0, "data": {"messages": inline_nodes},
                    },
                    timeout_seconds=8.0,
                )
                if result.ok and result.message is not None and result.message.nodes:
                    resolution_source = "event_inline"
                else:
                    result = None
            if result is None:
                result = resolve_onebot_forward_message(
                    inbound,
                    forward_ref,
                    call_action=self.gateway._call_onebot_action,
                    timeout_seconds=8.0,
                )
            entry: dict[str, Any] = {
                "source_part_id": source_part_ids.get(forward_ref.forward_id, ""),
                "source_message_id": inbound.event_id,
                "conversation_kind": inbound.conversation.kind,
                "conversation_id": inbound.conversation.id,
                "forward_id": forward_ref.forward_id,
                "ok": bool(result.ok and result.message is not None and result.message.nodes),
                "status": str(result.status or ""),
                "resolution_source": resolution_source,
                "nodes": [],
            }
            if result.reason:
                entry["reason"] = result.reason
            if result.ok and result.message is not None and result.message.nodes:
                resolved_count += 1
                for node in result.message.nodes:
                    node_attachments = self._legacy_attachments(node.attachments)
                    attachments.extend(node_attachments)
                    resolved_mentions = self.gateway.resolve_mention_labels(
                        mentions=tuple(getattr(node, "mentions", ()) or ()),
                        group_id=int(context.group_id or 0) if context.is_group else 0,
                    )
                    entry["nodes"].append(
                        {
                            "index": node.index,
                            "actor_id": node.actor.id,
                            "actor_label": node.actor.display_name,
                            "text": node.text,
                            "timestamp": node.timestamp,
                            "message_id": node.message_id,
                            "attachment_count": len(node_attachments),
                            "mentions": self.gateway._project_mention_evidence(resolved_mentions),
                        }
                    )
                entry["node_count"] = len(result.message.nodes)
            elif result.ok and result.message is not None:
                # OneBot can report success while providing no forwarded nodes.
                # Keep that state visible without treating the content as read.
                entry["status"] = "empty"
                entry["reason"] = "forward_nodes_empty"
                entry["node_count"] = 0
            projected.append(entry)

        overall_status = "resolved" if resolved_count == len(projected) else "partial" if resolved_count else "unavailable"
        return {
            "ok": resolved_count == len(projected),
            "status": overall_status,
            "forward_count": len(projected),
            "resolved_count": resolved_count,
            "forwards": projected,
            "attachments": attachments,
        }

    @staticmethod
    def _inline_forward_nodes(
        event: Mapping[str, Any], inbound: InboundMessage, forward_id: str,
    ) -> list[object] | None:
        """Accept inline nodes only from the event that produced this inbound message."""
        if str(event.get("message_id") or "").strip() != inbound.event_id:
            return None
        if str(event.get("self_id") or "").strip() != inbound.bot_account_id:
            return None
        if str(event.get("message_type") or "").strip() != inbound.conversation.kind:
            return None
        scope_key = "group_id" if inbound.conversation.kind == "group" else "user_id"
        if str(event.get(scope_key) or "").strip() != inbound.conversation.id:
            return None
        segments = event.get("message")
        if not isinstance(segments, list):
            return None
        for segment in segments:
            if not isinstance(segment, Mapping):
                continue
            if str(segment.get("type") or "").strip().lower() not in {
                "forward", "forward_msg", "merged_forward",
            }:
                continue
            data = segment.get("data")
            if not isinstance(data, Mapping):
                continue
            segment_id = str(data.get("id") or data.get("res_id") or data.get("forward_id") or "").strip()
            if segment_id != forward_id:
                continue
            nodes = data.get("content")
            if isinstance(nodes, list) and nodes:
                return nodes
        return None

    @staticmethod
    def _legacy_attachments(attachments: tuple[AttachmentRef, ...]) -> list[dict[str, Any]]:
        projected: list[dict[str, Any]] = []
        for attachment in attachments:
            legacy_kind = {
                "image": "image",
                "audio": "audio",
                "video": "video",
                "file": "document",
            }.get(attachment.kind)
            if not legacy_kind:
                continue
            item = {
                "kind": legacy_kind,
                "file": attachment.locator.file_id or attachment.name,
                "url": attachment.locator.url,
                "path": attachment.locator.path,
                "origin_name": attachment.name or attachment.locator.file_id,
                "mime_type": attachment.mime_type,
                "file_size": attachment.size,
                "source_message_id": attachment.source_message_id,
                "source_event_id": attachment.source_event_id,
                "segment_index": attachment.segment_index,
                "segment_type": attachment.segment_type,
            }
            metadata = attachment.metadata_dict()
            for key in (
                "quoted_message_id",
                "forward_id",
                "forward_node_index",
                "sender_id",
                "sender_label",
                "group_id",
            ):
                value = str(metadata.get(key) or "").strip()
                if value:
                    item[key] = value
            projected.append(item)
        return projected
