"""Generation-frozen tool exposure and capability catalog reconciliation."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..plugin_invocation_scope import use_generation_scopes
from .response_catalog import _shape_capability_catalog_projection, _visible_projection_source_ids
from .response_contracts import ResponseEnvironment, ResponseRequest
from .response_projection import ResponseProjection, _memory_backend, _projection_failure_context


class ResponseExposure:
    def __init__(
        self, engine: Any, request: ResponseRequest, environment: ResponseEnvironment, projection: ResponseProjection
    ):
        self.engine = engine
        self.request = request
        self.environment = environment
        self.projection = projection
        self.native_tools: list[dict[str, Any]] = []
        self.native_legacy_exclusions: set[str] = set()
        self.block_reader = getattr(self.environment.prompt_builder, "_registered_stable_system_blocks", None)
        self.capability_selection, self.current_plugin_blocks = self._read_current_exposure()
        self.desired_capability_selection = self.capability_selection
        self.published_plugin_blocks = self.current_plugin_blocks
        self.exposure_lifecycle = {"ok": True, "status": "immediate", "reason": "memcore_authority_required"}
        self.exposure_supported = bool(
            _memory_backend() == "memcore"
            and self.projection.projection_read_active
            and self.projection.projection_authoritative
            and self.projection.projection_recovery_failure is None
            and "compaction_generation" in self.projection.provider_projection
            and (
                not self.environment.tool_capability_available
                or getattr(self.capability_selection, "capability_catalog", None) is not None
            )
        )

    def prepare(self) -> dict[str, Any] | None:
        self.synchronize_exposure()
        if not self.exposure_lifecycle.get("ok"):
            return _projection_failure_context(self.exposure_lifecycle, prompt_scope=self.request.prompt_scope)
        self._build_native_exposure()
        self._prepare_sections()
        self.synchronize_catalog(exposure_prepared=True)
        failure = self.failure_context()
        if failure is not None:
            return failure
        if not self.capability_catalog_lifecycle_enabled:
            self.shape_projection()
        return None

    def failure_context(self) -> dict[str, Any] | None:
        if not self.exposure_lifecycle.get("ok"):
            return _projection_failure_context(self.exposure_lifecycle, prompt_scope=self.request.prompt_scope)
        if (
            self.exposure_supported
            and self.capability_catalog_lifecycle_enabled
            and not self.capability_catalog_lifecycle.get("ok")
        ):
            return _projection_failure_context(
                self.capability_catalog_lifecycle, prompt_scope=self.request.prompt_scope
            )
        return None

    def shape_projection(self) -> None:
        if not (self.projection.projection_authoritative and self.projection.projection_read_active):
            return
        shaped, base = _shape_capability_catalog_projection(
            self.projection.provider_projection,
            compaction_generation=int(self.projection.provider_projection.get("compaction_generation") or 0),
            client_scope=self.catalog_client_scope,
        )
        self.projection.provider_projection = shaped
        if base:
            self.capability_catalog_context = base

    def _read_current_exposure(self):
        with use_generation_scopes(()):
            capability_selection = (
                self.engine._resolve_capability_selection(
                    client_context=self.environment.client_context,
                    profile_user_id=self.request.profile_user_id,
                    session_id=self.request.session_id,
                    domain_profile_id=self.environment.domain_profile.id,
                    intent_text=self.request.user_message,
                    authorization_profile_user_id=self.request.authorization_profile_user_id,
                    character_pack_id=self.request.character_pack_id,
                )
                if self.environment.tool_capability_available
                else None
            )
            current_plugin_blocks = tuple(self.block_reader()) if callable(self.block_reader) else ()
        return capability_selection, current_plugin_blocks

    def synchronize_exposure(self) -> None:
        if self.exposure_supported:
            # Compaction may await an external summary while providers/settings
            # change. A newly published generation must sample current authority.
            previous_generation = self.exposure_lifecycle.get("compaction_generation")
            if previous_generation is not None and previous_generation != self.projection.provider_projection.get(
                "compaction_generation"
            ):
                self.desired_capability_selection, self.current_plugin_blocks = self._read_current_exposure()
            from ..capability_exposure import freeze_for_projection

            self.capability_selection, self.published_plugin_blocks, self.exposure_lifecycle = freeze_for_projection(
                self.engine,
                self.desired_capability_selection,
                self.projection.provider_projection,
                plugin_blocks=self.current_plugin_blocks,
                profile_user_id=self.request.profile_user_id,
                session_id=self.request.session_id,
                character_pack_id=self.request.character_pack_id,
                client_mode=str(self.environment.client_context.effective_mode.value),
                domain_profile_id=self.environment.domain_profile.id,
                authorization_profile_user_id=self.request.authorization_profile_user_id,
            )

    def _build_native_exposure(self) -> None:
        # A rebuilt generation may have lost provider support or native-tool
        # permission. The new plan owns all schema/exclusion/alias fields.
        self.native_tools = []
        self.native_legacy_exclusions = set()
        schema_tool_names: tuple[str, ...] = ()
        if self.request.enable_native_tools and self.environment.tool_capability_available:
            from .. import tool_orchestration_engine as _toe

            ready_handlers = self.engine._resolve_tool_handlers(
                client_context=self.environment.client_context,
                profile_user_id=self.request.profile_user_id,
                session_id=self.request.session_id,
                domain_profile_id=self.environment.domain_profile.id,
                capability_selection=self.capability_selection,
            )
            schema_tool_names = tuple(
                getattr(self.capability_selection, "schema_tool_names", ())
                or getattr(self.capability_selection, "tool_names", ())
                or ()
            )
            frozen_handlers = getattr(self.capability_selection, "resolved_handlers", {})
            schema_handlers = {name: frozen_handlers[name] for name in schema_tool_names if name in frozen_handlers}
            try:
                provider_supports_native_tools = self.engine.llm.chat_supports_native_tools(
                    chat_model_override=self.request.chat_model_override,
                    execution_target=self.request.execution_target,
                )
            except TypeError:
                try:
                    provider_supports_native_tools = self.engine.llm.chat_supports_native_tools(
                        chat_model_override=self.request.chat_model_override
                    )
                except TypeError:
                    provider_supports_native_tools = self.engine.llm.chat_supports_native_tools()
            native_plan = _toe.build_native_tool_decision_plan(
                schema_handlers or ready_handlers,
                allow_tool_call=self.environment.tool_capability_available,
                provider_supports_native_tools=provider_supports_native_tools,
                allowed_tool_names=schema_tool_names,
            )
            if native_plan.enabled:
                self.native_tools = native_plan.tools
                self.native_legacy_exclusions = native_plan.legacy_prompt_exclusions
            elif native_plan.status == "unsupported":
                self.engine.llm.record_metric("native_tool_provider_unsupported")
        self._bind_native_names(schema_tool_names)

    def _bind_native_names(self, schema_tool_names: tuple[str, ...]) -> None:
        if self.capability_selection is None:
            return
        from ..native_tool_schema import native_tool_model_name_map

        resolved_native_names = tuple(name for name in schema_tool_names if name in self.native_legacy_exclusions)
        aliases = native_tool_model_name_map(self.native_tools)
        try:
            self.capability_selection = replace(
                self.capability_selection,
                native_tool_names=resolved_native_names,
                native_tool_aliases=aliases,
            )
        except TypeError:
            # Lightweight hosts may implement the same attribute contract.
            try:
                setattr(self.capability_selection, "native_tool_names", resolved_native_names)
                setattr(self.capability_selection, "native_tool_aliases", aliases)
            except (AttributeError, TypeError):
                pass

    def _prepare_sections(self) -> None:
        self.section_builder = getattr(self.engine, "_build_tool_prompt_context_sections", None)
        self._refresh_sections()
        self.current_capability_state = None
        if (getattr(self.desired_capability_selection, "capability_catalog", None) is not None
                or self.skill_catalog_entries is not None):
            from ..capability_exposure import catalog_state

            self.current_capability_state = catalog_state(self.desired_capability_selection, self.current_plugin_blocks,
                                                          skills=self.skill_catalog_entries)
        self.capability_catalog_context = self.catalog_source_content
        self.capability_catalog_lifecycle: dict[str, Any] = {
            "ok": False,
            "status": "skipped",
            "reason": "projection_not_authoritative",
        }
        self.catalog_writer = getattr(
            getattr(self.engine, "memcore_manager", None), "reconcile_capability_catalog", None
        )
        from ..owner_private_session import current_private_scope

        private_scope = current_private_scope(self.engine)
        self.catalog_client_scope = (
            self.environment.client_context.effective_mode.value if private_scope and private_scope.shared else ""
        )
        self._update_catalog_enabled()

    def _update_catalog_enabled(self) -> None:
        self.capability_catalog_lifecycle_enabled = bool(
            callable(self.catalog_writer) and self.capability_catalog_status == "ready" and self.catalog_source_content
        )

    def _refresh_sections(self) -> None:
        prompt_context_kwargs = {
            "allow_tool_call": self.environment.tool_capability_available,
            "client_context": self.environment.client_context,
            "profile_user_id": self.request.profile_user_id,
            "session_id": self.request.session_id,
            "exclude_tool_types": self.native_legacy_exclusions,
            "domain_profile_id": self.environment.domain_profile.id,
            "capability_selection": self.capability_selection,
            "include_capability_status": not bool(self.native_tools),
            "actor_stable_id": self.request.actor_stable_id,
            "actor_profile_user_id": self.request.actor_profile_user_id,
        }
        if callable(self.section_builder):
            tool_prompt_sections = dict(self.section_builder(**prompt_context_kwargs) or {})
        else:
            # Lightweight hosts may still expose only the composed diagnostic API.
            tool_prompt_sections = {
                "execution_context": "",
                "catalog_context": "",
                "catalog_status": "skipped",
                "round_context": self.engine._build_tool_prompt_context(**prompt_context_kwargs),
            }
        self.execution_context = str(tool_prompt_sections.get("execution_context") or "").strip()
        self.execution_stable_context = str(tool_prompt_sections.get("execution_stable_context") or "").strip()
        self.catalog_source_content = str(tool_prompt_sections.get("catalog_context") or "").strip()
        self.capability_catalog_status = str(tool_prompt_sections.get("catalog_status") or "skipped").strip()
        self.skill_catalog_entries = tool_prompt_sections.get("skill_catalog_entries")
        self.tool_prompt_context = str(tool_prompt_sections.get("round_context") or "").strip()
        # Native-channel guidance is shared in the stable system rules. Only
        # actual legacy contracts or current availability belong here.

    def synchronize_catalog(self, *, exposure_prepared: bool = False) -> None:
        previous_generation = self.exposure_lifecycle.get("compaction_generation")
        exposure_prepared = exposure_prepared and (
            previous_generation == self.projection.provider_projection.get("compaction_generation")
        )
        previous_native_tools = self.native_tools
        previous_native_exclusions = self.native_legacy_exclusions
        # Initial preparation already owns a complete exposure. Compaction
        # calls this without the flag and must re-read current authority.
        if not exposure_prepared:
            self.synchronize_exposure()
        if not self.exposure_lifecycle.get("ok"):
            self.capability_catalog_lifecycle = dict(self.exposure_lifecycle)
            return
        if not exposure_prepared:
            self._build_native_exposure()
        if (
            previous_generation != self.exposure_lifecycle.get("compaction_generation")
            or previous_native_tools != self.native_tools
            or previous_native_exclusions != self.native_legacy_exclusions
        ):
            self._refresh_sections()
            self._update_catalog_enabled()
            if (getattr(self.desired_capability_selection, "capability_catalog", None) is not None
                    or self.skill_catalog_entries is not None):
                from ..capability_exposure import catalog_state

                self.current_capability_state = catalog_state(
                    self.desired_capability_selection, self.current_plugin_blocks, skills=self.skill_catalog_entries
                )
        if not (
            self.projection.projection_authoritative
            and self.projection.projection_read_active
            and self.capability_catalog_lifecycle_enabled
        ):
            return
        generation = int(self.projection.provider_projection.get("compaction_generation") or 0)
        self.capability_catalog_lifecycle = self.catalog_writer(
            content=self.catalog_source_content,
            visible_source_ids=_visible_projection_source_ids(self.projection.provider_projection),
            compaction_generation=generation,
            anchor_source_id=self.projection.current_source_id,
            profile_user_id=self.request.profile_user_id,
            session_id=self.request.session_id,
            character_pack_id=self.request.character_pack_id,
            timestamp=self.request.now_ts,
            **({"client_scope": self.catalog_client_scope} if self.catalog_client_scope else {}),
            **(
                {"capability_state": self.current_capability_state} if self.current_capability_state is not None else {}
            ),
        )
        if self.capability_catalog_lifecycle.get("ok") and self.capability_catalog_lifecycle.get("changed"):
            refreshed_projection = self.projection.read(self.engine, self.request)
            if refreshed_projection.get("ok"):
                self.projection.adopt(refreshed_projection)
            else:
                self.capability_catalog_lifecycle = {
                    **dict(self.capability_catalog_lifecycle),
                    "ok": False,
                    "status": "projection_refresh_failed",
                    "reason": str(refreshed_projection.get("reason") or "projection_build_failed"),
                }
        shaped_projection, base_context = _shape_capability_catalog_projection(
            self.projection.provider_projection,
            compaction_generation=generation,
            client_scope=self.catalog_client_scope,
        )
        self.projection.provider_projection = shaped_projection
        if base_context:
            self.capability_catalog_context = base_context
