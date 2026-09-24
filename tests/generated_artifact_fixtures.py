"""Real neutral text fixtures for storage/inspection/delivery tests, not authoring."""


def register_text_artifact(
    service,
    *,
    profile_user_id,
    session_id,
    output_title,
    output_format,
    content,
    timestamp,
    source_ids=(),
    revision_of="",
):
    if output_format not in {"txt", "md"}:
        raise ValueError("text fixture only")
    path = service.allocate_output_path(
        profile_user_id=profile_user_id,
        session_id=session_id,
        title=output_title,
        output_format=output_format,
        timestamp=timestamp,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="") as output:
        output.write(content)
    return service.register_generated_artifact(
        profile_user_id=profile_user_id,
        session_id=session_id,
        output_path=path,
        output_title=output_title,
        output_format=output_format,
        mime_type="text/plain" if output_format == "txt" else "text/markdown",
        content_card={"content_preview": content[:1200]},
        summary="Test artifact",
        created_by_tool="fixture.register",
        source_ids=source_ids,
        timestamp=timestamp,
        revision_of=revision_of,
    )
