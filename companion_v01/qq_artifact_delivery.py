"""QQ artifact identity, visible delivery receipts and plugin artifact hydration."""

from __future__ import annotations

from typing import Any


def _qq_file_delivery_event_identity(event: dict[str, Any]) -> tuple[str, str] | None:
    """Identify one physical file across streamed and final-frame event shapes."""

    event_type = str(event.get("type") or "").strip()
    if event_type == "generated_file_ready":
        item = event.get("generated_file") if isinstance(event.get("generated_file"), dict) else {}
        source_type = "generated"
    elif event_type in {"file_ready", "file_unavailable"}:
        item = event.get("file") if isinstance(event.get("file"), dict) else {}
        source_type = str(item.get("source_type") or "").strip().lower() or "file"
    else:
        return None
    source_id = str(
        item.get("generated_id")
        or item.get("attachment_id")
        or item.get("source_id")
        or item.get("generated_handle")
        or item.get("attachment_handle")
        or item.get("handle")
        or item.get("absolute_path")
        or ""
    ).strip()
    return (source_type, source_id) if source_id else None


def _qq_has_visible_action_receipt(events: Any) -> bool:
    """Recognize only a successful receipt emitted by the OneBot write surface."""

    return any(
        isinstance(event, dict)
        and str(event.get("type") or "").strip() == "qq_visible_action_receipt"
        and bool(event.get("ok"))
        and str(event.get("status") or "").strip().lower() not in {"failed", "invalid", "unavailable"}
        for event in list(events or [])
    )


def _hydrate_plugin_managed_artifact_events(
    *,
    engine: Any,
    context: Any,
    tool_events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve safe plugin handles to local paths only at the QQ transport edge."""

    service_getter = getattr(engine, "_get_generated_file_service", None)
    service = service_getter() if callable(service_getter) else None
    hydrated: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for raw_event in tool_events:
        if not isinstance(raw_event, dict):
            continue
        event = dict(raw_event)
        generated = event.get("generated_file")
        if (
            event.get("type") != "generated_file_ready"
            or str(event.get("delivery_scope") or "").strip().lower() != "plugin_managed_artifact"
            or not bool(event.get("send_to_user"))
            or not isinstance(generated, dict)
            or str(generated.get("absolute_path") or "").strip()
        ):
            hydrated.append(event)
            continue
        generated_id = str(generated.get("generated_id") or "").strip()
        generated_handle = str(generated.get("generated_handle") or "").strip()
        resolved = None
        if service is not None and callable(getattr(service, "resolve_generated_artifact", None)):
            target = generated_id or generated_handle
            try:
                resolved = service.resolve_generated_artifact(
                    profile_user_id=str(getattr(context, "profile_user_id", "") or ""),
                    session_id=str(getattr(context, "session_id", "") or ""),
                    target=target,
                )
            except Exception:
                resolved = None
        if isinstance(resolved, dict) and str(resolved.get("absolute_path") or "").strip():
            event["generated_file"] = resolved
        else:
            failures.append(
                {
                    "ok": False,
                    "reason": "managed_artifact_unavailable",
                    "generated_id": generated_id,
                }
            )
        hydrated.append(event)
    return hydrated, failures
