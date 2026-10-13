"""Builtin tool construction, channel binding and capability selection adapters."""
from __future__ import annotations

import logging
from typing import Any
from ..capability_registry import CapabilitySelection, CapabilitySnapshot
from ..client_protocol import ClientProtocolContext
from ..tool_runtime import BaseToolHandler, ToolExecutionContext, ToolExecutionResult
from .. import retrieval_engine

logger = logging.getLogger("akane.engine")


class ToolCapabilitiesMixin:
    def _build_tool_handlers(self) -> dict[str, BaseToolHandler]:
        from ..tool_handlers.catalog import build_builtin_tool_handlers

        handlers = build_builtin_tool_handlers(
            executor_broker=getattr(self, "executor_broker", None),
            store=self.store,
            sticker_assets=self.sticker_assets,
            capability_offer_source=self.capability_offer_source,
            capability_config_base_dir=self.capability_config_base_dir,
            memory_timeline_service=self._build_memory_timeline_tool_service(),
            context_libraries=getattr(
                self.desktop_pet_character_resources,
                "context_libraries",
                None,
            ),
            attachment_service=self._get_attachment_inbox_service(),
            image_material_resolver=self._get_image_material_resolver(),
            project_workspace_service=self._get_project_workspace_service(),
            workspace_file_service=self._get_workspace_file_service(),
            attachment_ingest_service=self._get_attachment_ingest_service(),
            generated_file_service=self._get_generated_file_service(),
            character_resource_service=self.desktop_pet_character_resources,
            retrieve_fn=self._execute_retrieve_memory_tool,
            memory_retrieval_enabled=getattr(
                getattr(self, "memcore_manager", None), "retrieval_mode", getattr(self, "memory_retrieval_mode", "hybrid")
            ) == "hybrid",
            memory_read_policy_provider=lambda: getattr(self, "memory_read_policy", None),
            skill_registry=self._get_skill_registry(),
            execution_provider=self._build_execution_provider(),
            approval_store=self._get_approval_store(),
            mcp_management_service=getattr(self, "mcp_management_service", None),
            extension_management_service=getattr(self, "extension_management_service", None),
        )
        from ..tool_handlers.character_shop import CharacterShopToolHandler
        handlers["read_character_shop"] = CharacterShopToolHandler(self)
        handlers["edit_character_shop"] = CharacterShopToolHandler(self, write=True)
        from ..tool_handlers.local_music import LocalMusicControlToolHandler
        handlers["local_music_control"] = LocalMusicControlToolHandler(lambda: getattr(self, "local_music_control", None))
        return handlers

    def bind_qq_delivery_port(self, delivery_port: Any | None) -> None:
        """Bind the live QQ transport port to QQ-only delivery handlers.

        The engine is constructed before the channel gateway, so the handlers
        are assembled first and receive the narrow port once the runtime owns a
        real gateway.  No delivery target or transport state enters prompts.
        """
        for tool_name in ("send_music_card", "send_audio", "onebot_action"):
            handler = self.tool_handlers.get(tool_name)
            binder = getattr(handler, "bind_delivery_port", None)
            if callable(binder):
                binder(delivery_port)

    def _resolve_tool_handlers(
        self,
        *,
        client_context: ClientProtocolContext | None = None,
        profile_user_id: str = "",
        session_id: str = "",
        domain_profile_id: str = "",
        capability_selection: CapabilitySelection | None = None,
    ) -> dict[str, BaseToolHandler]:
        from .tool_rounds import resolve_tool_handlers as _fn

        return _fn(
            self,
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
            domain_profile_id=domain_profile_id,
            capability_selection=capability_selection,
        )

    def _build_loadable_capability_catalog(self, *, ready_tool_names, profile_user_id):
        """One catalog renderer for conversation and isolated task prompts."""
        text, errors, _ = self._build_loadable_capability_catalog_projection(
            ready_tool_names=ready_tool_names, profile_user_id=profile_user_id)
        return text, errors

    def _build_loadable_capability_catalog_projection(self, *, ready_tool_names, profile_user_id):
        parts, errors = [], []
        entries = {}
        if "load_skill" in ready_tool_names:
            try:
                projection = self._get_skill_registry().prompt_catalog_projection(
                    available_tool_names=set(ready_tool_names))
                parts.append(str(projection["text"] or "").strip())
                entries = projection["entries"]
            except Exception as exc:
                logger.warning("skill catalog projection failed: %s", type(exc).__name__)
                errors.append("Skill 目录当前读取失败；保留上一版目录。")
        text = "\n\n".join(part for part in parts if part)
        return ("" if errors else text or "【可按需加载的 Skill】\n- 当前没有可按需加载的 Skill。", errors, entries)

    def _resolve_capability_selection(
        self,
        *,
        client_context: ClientProtocolContext | None = None,
        profile_user_id: str = "",
        session_id: str = "",
        domain_profile_id: str = "",
        intent_text: str = "",
        authorization_profile_user_id: str = "",
        character_pack_id: str = "",
    ) -> CapabilitySelection:
        from .tool_rounds import resolve_capability_selection as _fn

        return _fn(
            self,
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
            domain_profile_id=domain_profile_id,
            intent_text=intent_text,
            authorization_profile_user_id=authorization_profile_user_id,
            character_pack_id=character_pack_id,
        )

    def _build_mcp_adapter_tool_handlers(
        self,
        *,
        profile_user_id: str = "",
        client_context: ClientProtocolContext | None = None,
    ) -> dict[str, BaseToolHandler]:
        from .tool_rounds import build_mcp_adapter_tool_handlers as _fn

        return _fn(
            self,
            profile_user_id=profile_user_id,
            client_context=client_context,
        )

    def _build_python_adapter_tool_handlers(
        self,
        *,
        profile_user_id: str = "",
        client_context: ClientProtocolContext | None = None,
    ) -> dict[str, BaseToolHandler]:
        from .tool_rounds import build_python_adapter_tool_handlers as _fn

        return _fn(
            self,
            profile_user_id=profile_user_id,
            client_context=client_context,
        )

    def _legacy_mode_tool_names(
        self,
        client_context: ClientProtocolContext,
        *,
        domain_profile_id: str = "",
    ) -> list[str]:
        from .tool_rounds import legacy_mode_tool_names as _fn

        return _fn(self, client_context, domain_profile_id=domain_profile_id)

    def _build_capability_snapshot(
        self,
        *,
        client_context: ClientProtocolContext,
        profile_user_id: str,
        session_id: str,
    ) -> CapabilitySnapshot:
        from .tool_rounds import build_capability_snapshot as _fn

        return _fn(
            self,
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
        )

    def _execute_retrieve_memory_tool(
        self,
        *,
        call: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        return retrieval_engine.execute_retrieve_memory_tool(
            self,
            call=call,
            context=context,
        )
