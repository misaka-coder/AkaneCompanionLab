"""Oversized unpaged completion text uses the existing generated-file reader."""
from capcore import CapabilityResult, InvocationContext

from .plugin_result_presentation import project_model_result, DEFAULT_PLUGIN_RESULT_PREVIEW_CHARS
from .tool_handlers.core import ToolExecutionResult


def bound_completion_result(job, result: ToolExecutionResult, *, engine=None):
    from .capability_adapters.mcp_stdio import _run_awaitable_blocking
    from .plugin_managed_artifacts import GeneratedFileManagedArtifactSink
    from .owner_private_session import private_turn_scope
    from contextlib import nullcontext

    service = engine._get_generated_file_service() if engine is not None else None
    sink = GeneratedFileManagedArtifactSink(service) if service is not None else None
    origin = {"user_id": job.owner.session_id, "real_user_id": job.owner.profile_user_id,
              "character_pack_id": job.character_pack_id, "client_mode": job.channel,
              "_memory_desktop_ref" if job.channel == "desktop_pet" else "_memory_qq_ref": job.delivery_target}

    async def project():
        with private_turn_scope(engine, origin) if engine is not None else nullcontext():
            return await project_model_result(
                result=CapabilityResult(is_error=job.status != "succeeded", content=result.followup_context),
                rendered=result.followup_context, stored_material=result.followup_context,
                capability_id=job.capability_id,
                context=InvocationContext(profile_user_id=job.owner.profile_user_id,
                                          session_id=job.owner.session_id, client_mode=job.channel),
                sink=sink, preview_chars=DEFAULT_PLUGIN_RESULT_PREVIEW_CHARS,
            )
    envelope = _run_awaitable_blocking(project())
    result.followup_envelope = envelope
    result.followup_context = envelope.content
    return result
