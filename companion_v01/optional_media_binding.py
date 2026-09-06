"""Existing product consumers of installed media capabilities; no ML fallback."""

from __future__ import annotations

import time
from uuid import uuid4

from .capability_registry import ExecutorBroker
from .mode_profiles import ModeProfileRegistry
from .tool_runtime import ToolExecutionContext


def prepare_timeline_vocals(engine, *, profile_user_id, session_id, source_id):
    """Call the installed capability through normal admission and execution.

    The timeline itself owns background work. This synchronous sub-operation
    does not submit another Job or deliver/play the registered files.
    """
    return _prepare_media(
        engine,
        profile_user_id=profile_user_id,
        session_id=session_id,
        source_id=source_id,
        capability_id="akane.audio-separation.run.v1",
        reason_prefix="separation",
        artifact_name="vocals",
        client_mode="desktop_pet",
        arguments={},
    )


def prepare_dataset_voice(engine, *, profile_user_id, session_id, source_id, client_mode="web"):
    # Preserve dataset clean_first's previous basic voice-focus recipe. No AI
    # download/automatic install, no second filter authority or nested Host Job.
    return _prepare_media(
        engine,
        profile_user_id=profile_user_id,
        session_id=session_id,
        source_id=source_id,
        capability_id="akane.voice-clean.run.v1",
        reason_prefix="cleaning",
        artifact_name="audio",
        client_mode=client_mode,
        arguments={"mode": "voice_focus", "quality": "basic", "post_filter": False},
    )


def _prepare_media(
    engine,
    *,
    profile_user_id,
    session_id,
    source_id,
    capability_id,
    reason_prefix,
    artifact_name,
    client_mode,
    arguments,
):
    if not profile_user_id or not session_id or not source_id:
        return {"status": "unavailable", "reason": f"{reason_prefix}_source_scope_missing"}
    try:
        client = ModeProfileRegistry().resolve_from_payload({"client_mode": client_mode})
        handler = engine._resolve_tool_handlers(
            profile_user_id=profile_user_id, session_id=session_id, client_context=client
        ).get(capability_id)
        if handler is None:
            return {"status": "unavailable", "reason": f"{reason_prefix}_plugin_unavailable"}
        invocation_id = f"media-preparation-{uuid4().hex}"
        call = handler.normalize_call(
            {
                "type": capability_id,
                "source_id": source_id,
                "output_format": "wav",
                "send_to_user": False,
                **arguments,
            }
        )
        context = ToolExecutionContext(
            profile_user_id,
            session_id,
            int(time.time()),
            {},
            client_mode=client_mode,
            invocation_id=invocation_id,
        )
        broker = getattr(engine, "executor_broker", None)
        if broker is None:
            broker = engine.executor_broker = ExecutorBroker(None)
        execution = broker.execute_server_local(
            tool_id=capability_id,
            invocation_id=invocation_id,
            dispatch=lambda: handler.execute(call=call, context=context),
            ledger_scope=f"{profile_user_id}\x1f{session_id}",
            request_data={"arguments": call},
        )
        result = execution.result
        if execution.status != "succeeded" or result is None:
            return {"status": "failed", "reason": f"{reason_prefix}_execution_unconfirmed"}
        if result.state_updates.get("adapter_capability_status") != "ok":
            return {"status": "unavailable", "reason": f"{reason_prefix}_not_admitted_or_failed"}
        # The v1 contract orders the vocals artifact first, instrumental second.
        event = next((e for e in result.stream_events if e.get("type") == "generated_file_ready"), {})
        handle = event.get("generated_file", {}).get("generated_handle", "")
        if not handle:
            return {"status": "failed", "reason": f"{reason_prefix}_{artifact_name}_missing"}
        resource = engine._get_generated_file_service().resolve_input_resource(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=handle,
            timestamp=None,
        )
        if not resource or not resource.get("absolute_path"):
            return {"status": "failed", "reason": f"{reason_prefix}_{artifact_name}_unavailable"}
        return {"status": "ready", "absolute_path": resource["absolute_path"], "handle": handle}
    except Exception:
        return {"status": "failed", "reason": f"{reason_prefix}_binding_failed"}
