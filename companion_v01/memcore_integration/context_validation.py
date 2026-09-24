"""Isolate legacy history without changing stored records or source ownership."""

from services.tool_history import isolate_tool_history


def isolate_context_surface(surface):
    history = list(surface.get("history_messages") or [])
    validation = isolate_tool_history(history)
    if not validation.issues:
        return surface
    from memcore import stable_projection_hash

    result = dict(surface)
    result["history_messages"] = validation.messages
    for field in ("message_source_ids", "message_projection_metadata"):
        original = list(surface.get(field) or [])
        if original:
            result[field] = [original[index] for index in validation.source_indexes] + original[len(history):]
    if result.get("message_projection_metadata"):
        metadata = [dict(item) for item in result["message_projection_metadata"]]
        for index, original in enumerate(validation.source_indexes):
            if validation.messages[index] != history[original]:
                metadata[index]["projection_status"] = "canonical_fallback"
        result["message_projection_metadata"] = metadata
    result["messages"] = [*validation.messages,
        *([dict(result["current_message"])] if result.get("current_message") is not None else []),
        *list(result.get("active_turn_messages") or [])]
    result["projection_hash"] = stable_projection_hash({key: result.get(key) for key in (
        "version", "provider_profile", "history_messages", "current_message", "active_turn_messages")})
    result["diagnostics"] = [*list(result.get("diagnostics") or []),
                             {"status": "isolated", "reason": "incomplete_tool_history"}]
    result["history_validation"] = {"status": "isolated", "issue_count": len(validation.issues),
                                     "issues": validation.issues[:100]}
    return result
