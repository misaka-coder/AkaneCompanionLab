"""Shared execution services, conversation/run ownership, and working-directory context."""

from __future__ import annotations

from typing import (
    Any,
)

from ...execution_resources import (
    ExecutionResourceBridge,
    ExecutionResourceScope,
)

from ...execution_run import (
    ExecutionRunOwner,
)

from ...project_workspace import (
    ProjectWorkspaceError,
    ProjectWorkspaceService,
)

from ..core import (
    BaseToolHandler,
    ToolExecutionContext,
)

from .approvals import (
    ExecutionApprovalPromptMixin,
)

from .receipts import (
    ExecutionReceiptMixin,
)


class _ExecToolHandlerBase(ExecutionApprovalPromptMixin, ExecutionReceiptMixin, BaseToolHandler):
    """Shared provider / owner / result plumbing for execution tools."""

    # The execution specs are complete canonical schemas.  Advertising them
    # through the provider's native tool channel keeps argument validation at
    # the protocol boundary instead of asking the model to reproduce the
    # compatibility JSON shape from prose.
    policy_accepted_native_tool = True

    def __init__(
        self,
        *,
        execution_provider: Any,
        config_base_dir: Any = None,
        approval_store: Any = None,
        resource_bridge: ExecutionResourceBridge | None = None,
        project_workspace_service: ProjectWorkspaceService | None = None,
    ) -> None:
        self.execution_provider = execution_provider
        self.config_base_dir = config_base_dir
        self.approval_store = approval_store
        self.resource_bridge = resource_bridge
        self.project_workspace_service = project_workspace_service

    def bind_job_runtime(self, runtime: Any | None) -> None:
        if runtime is not None and not callable(getattr(runtime, "begin", None)):
            raise TypeError("invalid_execution_job_runtime")
        self.execution_job_runtime = runtime

    def _owner(self, context: ToolExecutionContext) -> ExecutionRunOwner:
        provider = self.execution_provider
        session_id = str(context.session_id or "")
        request_context = context.request_context if isinstance(context.request_context, dict) else {}
        actor_id = str(request_context.get("actor_stable_id") or "").strip()
        if session_id.startswith("qq_group_shared_") and actor_id.startswith("qq:"):
            # A shared QQ group is one conversation, but a long-running command
            # still belongs to the member who started it.  Keep that host-only
            # ownership detail out of prompts and public run receipts while
            # preventing another member's ordinary message from resuming or
            # cancelling the previous member's process.
            session_id = f"{session_id}\x1f{actor_id}"
        return ExecutionRunOwner(
            profile_user_id=str(context.profile_user_id or ""),
            session_id=session_id,
            provider_id=str(getattr(provider, "provider_id", "local") or "local"),
        )

    def _project_scope(self, context: ToolExecutionContext):
        service = self.project_workspace_service
        if service is None:
            raise ProjectWorkspaceError("project_workspace_unconfigured")
        request_context = context.request_context if isinstance(context.request_context, dict) else {}
        return service.scope_for(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            client_mode=context.client_mode,
            actor_stable_id=str(request_context.get("actor_stable_id") or ""),
            actor_profile_user_id=str(request_context.get("actor_profile_user_id") or ""),
        )

    @staticmethod
    def _resource_scope(context: ToolExecutionContext) -> ExecutionResourceScope:
        """Keep artifacts in the conversation namespace, not run-control scope."""

        return ExecutionResourceScope(
            profile_user_id=str(context.profile_user_id or ""),
            session_id=str(context.session_id or ""),
        )

    def capability_status(self, **_kwargs: Any) -> dict[str, Any]:
        """Existing ServerLocalOfferIndex readiness probe (mirrors satellite)."""
        provider = self.execution_provider
        if provider is None:
            return {"enabled": False, "status": "unavailable", "reason": "execution_provider_unconfigured"}
        try:
            availability = provider.availability()
        except Exception:
            return {"enabled": False, "status": "unavailable", "reason": "availability_check_failed"}
        if not availability.enabled:
            return {"enabled": False, "status": "unavailable", "reason": str(availability.reason or "execution_unavailable")}
        return {"enabled": True, "status": str(availability.status or "ready") or "ready", "reason": ""}

    def working_directory_context(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        client_mode: str,
        actor_stable_id: str = "",
        actor_profile_user_id: str = "",
    ) -> dict[str, str]:
        """Return the exact execution coordinate for the current request tail."""

        service = self.project_workspace_service
        if service is None:
            return {}
        try:
            scope = service.scope_for(
                profile_user_id=profile_user_id,
                session_id=session_id,
                client_mode=client_mode,
                actor_stable_id=actor_stable_id,
                actor_profile_user_id=actor_profile_user_id,
            )
            current = service.current(scope=scope)
        except ProjectWorkspaceError:
            current = None
        if current is None:
            try:
                root = str(self.execution_provider.resolve_workdir(""))
            except Exception:
                root = "execution_root"
            return {"working_directory": root, "project": "none", "workspace_id": ""}
        return {
            "working_directory": str(current.get("working_directory") or ""),
            "project": str(current.get("display_name") or "unnamed"),
            "workspace_id": str(current.get("workspace_id") or ""),
        }
