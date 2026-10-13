"""Render authored text as one managed PNG through normal delivery events."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
import sqlite3
import threading
import uuid
from typing import Any

from .core import BaseToolHandler, ToolExecutionContext, ToolExecutionResult, ToolFollowupEnvelope, operation_tool_result
from companion_v01.rendering.text_cards import TextCardDocument
from companion_v01.rendering.text_cards import layout_text_card
from companion_v01.rendering.text_cards import paint_text_card
from companion_v01.rendering.text_cards import renderer_fingerprint
from companion_v01.rendering.text_cards import validate_card_call


class RenderCardToolHandler(BaseToolHandler):
    tool_type = "render_card"
    default_exposure_mode = "on_demand"

    def __init__(self, *, generated_file_service: Any, image_material_resolver: Any,
                 character_resource_service: Any = None) -> None:
        self.generated_file_service = generated_file_service
        self.image_material_resolver = image_material_resolver
        self.character_resource_service = character_resource_service
        self._render_lock = threading.Lock()

    def capability_status(self, **_kwargs: Any) -> dict[str, Any]:
        try:
            from PIL import Image  # noqa: F401

            if not callable(getattr(self.generated_file_service, "register_generated_artifact", None)):
                raise RuntimeError("generated_file_service_unavailable")
            renderer_fingerprint("neutral")
        except (ImportError, OSError, RuntimeError) as exc:
            reason = str(exc) if isinstance(exc, RuntimeError) else "card_renderer_unavailable"
            return {"enabled": False, "status": "unavailable", "reason": reason}
        return {"enabled": True, "status": "ready"}

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or value.get("type") != self.tool_type:
            return None
        # Keep malformed values intact for a clear failure instead of silently
        # truncating a letter or manufacturing a successful partial card.
        return {"type": self.tool_type, **{key: value[key] for key in ("title", "body", "subtitle", "image_id") if key in value}}

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        def cancelled() -> bool:
            return bool(context.cancel_requested and context.cancel_requested())

        try:
            values = validate_card_call(call)
            if cancelled():
                raise ValueError("card_cancelled")
            if not context.profile_user_id or not context.session_id:
                raise ValueError("card_scope_required")
            if not callable(getattr(self.generated_file_service, "register_generated_artifact", None)):
                raise ValueError("card_storage_unavailable")
            image_path: Path | None = None
            image_source_id = ""
            if values["image_id"]:
                resolver = self.image_material_resolver
                if not callable(getattr(resolver, "resolve_many", None)):
                    raise ValueError("card_image_resolver_unavailable")
                resolution = resolver.resolve_many(
                    profile_user_id=context.profile_user_id, session_id=context.session_id,
                    targets=[values["image_id"]], max_count=1,
                )
                materials = resolution.get("materials") or []
                if not resolution.get("ok") or not materials:
                    reasons = resolution.get("unresolved") or [{}]
                    raise ValueError(str(reasons[0].get("reason") or "image_not_found"))
                image_path, image_source_id = materials[0].path, materials[0].source_id
            identity_builder = getattr(self.character_resource_service, "build_character_identity", None)
            identity = identity_builder(context.character_pack_id) if callable(identity_builder) else {}
            author = " ".join(str((identity or {}).get("assistant_name") or "").split())[:32]
            theme = "cecilia" if context.character_pack_id == "cecilia" else "neutral"
            document = TextCardDocument(values["title"], values["body"], values["subtitle"], author, theme)
            fingerprint = renderer_fingerprint(theme)
            origin = context.current_user_source_id or context.invocation_id or uuid.uuid4().hex
            key = hashlib.sha256(json.dumps([
                context.profile_user_id, context.session_id, context.character_pack_id,
                origin, values, author, fingerprint, image_source_id,
            ], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
            with self._render_lock:
                if cancelled():
                    raise ValueError("card_cancelled")
                layout = layout_text_card(document, image_path=image_path)
                if cancelled():
                    raise ValueError("card_cancelled")
                service = self.generated_file_service
                generated_id = f"generated::card_{key}"
                existing = service.store.get_generated_file(
                    profile_user_id=context.profile_user_id, session_id=context.session_id,
                    generated_id=generated_id,
                )
                if existing:
                    artifact = service.resolve_generated_artifact(
                        profile_user_id=context.profile_user_id, session_id=context.session_id, target=generated_id,
                    )
                    if artifact is None:
                        raise ValueError("card_previous_artifact_unavailable")
                else:
                    target = service.base_dir / "text_cards" / key / "card.png"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temporary = target.with_name(f".{target.stem}.{uuid.uuid4().hex}.tmp")
                    from PIL.PngImagePlugin import PngInfo

                    metadata = PngInfo()
                    metadata.add_text("Description", document.title + "\n\n" + layout.body)
                    metadata.add_text("Body", layout.body)
                    canvas = paint_text_card(document, layout, image_path=image_path)
                    try:
                        canvas.save(temporary, format="PNG", pnginfo=metadata, optimize=True)
                        if cancelled():
                            raise ValueError("card_cancelled")
                        temporary.replace(target)
                    finally:
                        canvas.close()
                        temporary.unlink(missing_ok=True)
                    try:
                        artifact = service.register_generated_artifact(
                            profile_user_id=context.profile_user_id, session_id=context.session_id,
                            output_path=target, output_title=document.title, output_format="png", mime_type="image/png",
                            content_card={"kind": "text_card", "character_pack_id": context.character_pack_id},
                            summary=layout.body[:160], created_by_tool=self.tool_type,
                            source_ids=[item for item in (context.current_user_source_id, image_source_id) if item],
                            generated_id=generated_id, timestamp=context.now_ts,
                        )
                    except sqlite3.IntegrityError:
                        # Another worker may have finished this exact card.
                        artifact = service.resolve_generated_artifact(
                            profile_user_id=context.profile_user_id, session_id=context.session_id, target=generated_id,
                        )
                        if artifact is None:
                            raise
                if cancelled():
                    raise ValueError("card_cancelled")
                # Transport will replace pending with its actual receipt.
                if artifact.get("delivery_status") not in {"sent", "delivered"}:
                    updated = service.mark_delivery_status(
                        profile_user_id=context.profile_user_id, session_id=context.session_id,
                        generated_id=artifact["generated_id"], delivery_status="pending", timestamp=context.now_ts,
                    )
                    if updated:
                        artifact.update(updated)
                handles = [artifact["generated_handle"]]
                followup = json.dumps({"ok": True, "status": "queued", "handles": handles}, ensure_ascii=False)
                followup += "\n图卡已生成并加入客户端发送队列，尚无送达回执；无需再次 send_file，也不必复述整篇正文。"
                return ToolExecutionResult(
                    tool_type=self.tool_type,
                    stream_events=[{"type": "generated_file_ready", "generated_file": artifact,
                                    "client_mode": context.client_mode, "send_to_user": True}],
                    followup_context=followup,
                    followup_envelope=ToolFollowupEnvelope(followup, producer_bounded=True),
                    state_updates={"generated_handles": handles},
                )
        except (OSError, ValueError, RuntimeError, ImportError, sqlite3.Error) as exc:
            known = str(exc)
            reason = known if re.fullmatch(r"(?:card_|image_|chinese_font_)[a-z0-9_]{1,80}", known) else "card_render_failed"
            return operation_tool_result(
                tool_type=self.tool_type,
                operation_result={"ok": False, "reason": reason,
                                  "followup_context": f"图卡没有完成（{reason}），本次没有加入发送队列。可先用文字回应。"},
            )
