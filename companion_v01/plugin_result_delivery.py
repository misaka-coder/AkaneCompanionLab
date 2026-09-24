"""The shared public artifact event projection for sync and completed tools."""

from collections.abc import Mapping


def plugin_artifact_events(result, *, capability_id, client_mode):
    content = getattr(result, "content", None)
    if getattr(result, "is_error", False) or not isinstance(content, Mapping):
        return []
    artifacts = content.get("managed_artifacts")
    if not isinstance(artifacts, list):
        return []
    events = []
    for artifact in artifacts:
        if not isinstance(artifact, Mapping):
            continue
        mode = artifact.get("delivery_mode", "file")
        if (not str(artifact.get("generated_id") or "").startswith("generated::")
                or not artifact.get("generated_handle")
                or (artifact.get("created_by_tool") != capability_id and artifact.get("forwarded_by_tool") != capability_id)
                or not isinstance(artifact.get("send_to_user"), bool) or not isinstance(mode, str)
                or mode not in {"file", "voice", "both"}):
            continue
        events.append({"type": "generated_file_ready", "generated_file": {
            key: artifact[key] for key in ("generated_id", "generated_handle", "output_title", "output_format",
                "mime_type", "file_size", "created_by_tool") if key in artifact},
            "send_to_user": artifact["send_to_user"], "delivery_scope": "plugin_managed_artifact",
            "delivery_mode": mode, "client_mode": client_mode})
    return events
