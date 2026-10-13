"""Per-turn capability selection with stable schemas and independently gated execution offers."""
from __future__ import annotations

from typing import Any, Mapping

from capcore import CapabilityToolSpec
from ..client_protocol import ClientMode
from ..desktop_satellite_specs import DESKTOP_SATELLITE_TOOL_SPECS
from ..executor_broker import CapabilityOfferSource, ExecutionReceipt
from .browser_contracts import OPEN_BROWSER_TOOL_SPEC
from .context import _execution_available_in_mode
from .models import CapabilityDisclosure, CapabilityModule, CapabilitySelection, CapabilitySnapshot
from .modules import MEDIA_WORKBENCH_SHELL_OVERLAP_TOOL_NAMES, build_default_modules
from .offers import ServerLocalOfferIndex


class CapabilityRegistry:
    """Select lightweight ability hints and full tool instructions per turn."""

    def __init__(
        self,
        modules: tuple[CapabilityModule, ...] | None = None,
        *,
        offer_source: CapabilityOfferSource | None = None,
        server_offer_index: "ServerLocalOfferIndex | None" = None,
    ) -> None:
        self.modules = self._default_modules() if modules is None else modules
        self.offer_source = offer_source
        # M66-E: server-local offer index for tools backed by capability_status() probes
        self.server_offer_index: ServerLocalOfferIndex | None = server_offer_index

    def select(
        self,
        snapshot: CapabilitySnapshot,
        *,
        allowed_tool_names: tuple[str, ...] | None = None,
        hidden_tool_names: tuple[str, ...] = (),
        intent_text: str = "",
        profile_user_id: str = "",
        session_id: str = "",
    ) -> CapabilitySelection:
        hints: list[str] = []
        tools: list[str] = []
        schema_tools: list[str] = []
        module_names: list[str] = []
        layer_names: list[str] = []
        disclosures: list[CapabilityDisclosure] = []
        seen_tools: set[str] = set()
        seen_schema_tools: set[str] = set()
        seen_hints: set[str] = set()
        seen_layers: set[str] = set()
        allowed = (
            {str(name or "").strip() for name in allowed_tool_names if str(name or "").strip()}
            if allowed_tool_names is not None
            else None
        )
        hidden = {str(name or "").strip() for name in hidden_tool_names if str(name or "").strip()}
        for module in self.modules:
            if not module.applies_to_mode(snapshot.client_mode):
                continue
            module_tools = tuple(
                tool_name
                for tool_name in module.tools
                if tool_name not in hidden and (allowed is None or tool_name in allowed)
            )
            if module.name == "media_workbench" and _execution_available_in_mode(snapshot):
                module_tools = tuple(
                    tool_name
                    for tool_name in module_tools
                    if tool_name not in MEDIA_WORKBENCH_SHELL_OVERLAP_TOOL_NAMES
                )
            if not module_tools:
                continue
            hint = module.light_hint.strip()
            is_ready = module.trigger(snapshot)
            if not is_ready:
                if hint and module.activation_hint.strip() and hint not in seen_hints:
                    seen_hints.add(hint)
                    hints.append(hint)
                if hint and module.activation_hint.strip():
                    disclosures.append(
                        CapabilityDisclosure(
                            capability_id=module.name,
                            state="latent",
                            summary=hint,
                            reason=module.latent_reason.strip(),
                            activation=module.activation_hint.strip(),
                            tool_names=module_tools,
                            unavailable_reason=module.unavailable_reason.strip(),
                            recovery_hint=module.recovery_hint.strip(),
                        )
                    )
                continue

            ready_module_tools: list[str] = []
            for tool_name in module_tools:
                if tool_name not in seen_schema_tools:
                    seen_schema_tools.add(tool_name)
                    schema_tools.append(tool_name)
                if tool_name in seen_tools:
                    ready_module_tools.append(tool_name)
                    continue
                # Server-local offer gate. The index is synchronized with the
                # current handler map before each selection and fails closed.
                if self.server_offer_index is not None:
                    client_mode_val = str(
                        getattr(getattr(snapshot, "client_mode", ""), "value", snapshot.client_mode) or ""
                    )
                    if not self.server_offer_index.is_offered(
                        tool_name,
                        profile_user_id=profile_user_id,
                        session_id=session_id,
                        client_mode=client_mode_val,
                    ):
                        disclosures.append(
                            CapabilityDisclosure(
                                capability_id=module.name,
                                state="unavailable",
                                summary=f"这项能力（{tool_name}）当前不可用。",
                                reason=(
                                    module.unavailable_reason
                                    or "这项能力依赖的本地组件或外部服务当前没有通过可用性检查。"
                                ),
                                activation=module.recovery_hint,
                                tool_names=(tool_name,),
                            )
                        )
                        continue
                seen_tools.add(tool_name)
                tools.append(tool_name)
                ready_module_tools.append(tool_name)

            if not ready_module_tools:
                continue
            if hint and hint not in seen_hints:
                seen_hints.add(hint)
                hints.append(hint)
            if hint:
                disclosures.append(
                    CapabilityDisclosure(
                        capability_id=module.name,
                        state="ready",
                        summary=hint,
                        tool_names=tuple(ready_module_tools),
                        unavailable_reason=module.unavailable_reason.strip(),
                        recovery_hint=module.recovery_hint.strip(),
                    )
                )
            module_names.append(module.name)
            layer = str(module.layer or "").strip()
            if layer and layer not in seen_layers:
                seen_layers.add(layer)
                layer_names.append(layer)
        tool_specs: list[CapabilityToolSpec] = []
        execution_receipts: dict[str, Mapping[str, Any]] = {}
        if snapshot.client_mode in {ClientMode.DESKTOP_PET, ClientMode.QQ_TEXT}:
            browser_allowed = "open_browser" not in hidden and (allowed is None or "open_browser" in allowed)
            if self.offer_source is not None and browser_allowed:
                receipt = self._resolve_offer_receipt(OPEN_BROWSER_TOOL_SPEC)
                hint = OPEN_BROWSER_TOOL_SPEC.description
                if "open_browser" not in seen_schema_tools:
                    schema_tools.append("open_browser")
                    seen_schema_tools.add("open_browser")
                tool_specs.append(OPEN_BROWSER_TOOL_SPEC)
                if receipt is not None:
                    if "desktop_browser_open" not in module_names:
                        module_names.append("desktop_browser_open")
                    if "desktop_browser" not in seen_layers:
                        layer_names.append("desktop_browser")
                        seen_layers.add("desktop_browser")
                    if hint not in seen_hints:
                        hints.append(hint)
                        seen_hints.add(hint)
                    if "open_browser" not in seen_tools:
                        tools.append("open_browser")
                        seen_tools.add("open_browser")
                    disclosures.append(
                        CapabilityDisclosure(
                            capability_id="desktop_browser_open",
                            state="ready",
                            summary=hint,
                            tool_names=("open_browser",),
                        )
                    )
                    execution_receipts["open_browser"] = receipt.as_dict()
                else:
                    disclosures.append(
                        CapabilityDisclosure(
                            capability_id="desktop_browser_open",
                            state="unavailable",
                            summary=hint,
                            reason="当前没有在线且已授权的桌面执行器。",
                            activation="桌面客户端重新连接后，这项能力会自动恢复。",
                            tool_names=("open_browser",),
                        )
                    )
            for spec in DESKTOP_SATELLITE_TOOL_SPECS:
                tool_name = spec.capability_id
                if (
                    self.offer_source is None
                    or tool_name in hidden
                    or (allowed is not None and tool_name not in allowed)
                ):
                    continue
                receipt = self._resolve_offer_receipt(spec)
                # The configured desktop capability profile owns the schema.
                # Executor readiness only controls whether a receipt can be
                # attached for this turn; reconnects must not rewrite the
                # provider prompt prefix and destroy its cache branch.
                if tool_name not in seen_schema_tools:
                    schema_tools.append(tool_name)
                    seen_schema_tools.add(tool_name)
                tool_specs.append(spec)
                if receipt is not None:
                    if spec.capability_id not in seen_layers:
                        layer_names.append(spec.capability_id)
                        seen_layers.add(spec.capability_id)
                    if spec.description not in seen_hints:
                        hints.append(spec.description)
                        seen_hints.add(spec.description)
                    if tool_name not in seen_tools:
                        tools.append(tool_name)
                        seen_tools.add(tool_name)
                    disclosures.append(
                        CapabilityDisclosure(
                            capability_id=spec.capability_id,
                            state="ready",
                            summary=spec.description,
                            tool_names=(tool_name,),
                        )
                    )
                    execution_receipts[tool_name] = receipt.as_dict()
                else:
                    disclosures.append(
                        CapabilityDisclosure(
                            capability_id=spec.capability_id,
                            state="unavailable",
                            summary=spec.description,
                            reason="当前没有在线且已授权的桌面执行器。",
                            activation="桌面客户端重新连接后，这项能力会自动恢复。",
                            tool_names=(tool_name,),
                        )
                    )
        return CapabilitySelection(
            light_hints=tuple(hints),
            tool_names=tuple(tools),
            module_names=tuple(module_names),
            schema_tool_names=tuple(schema_tools),
            layer_names=tuple(layer_names),
            disclosures=tuple(disclosures),
            tool_specs=tuple(tool_specs),
            execution_receipts=execution_receipts,
        )

    def _resolve_offer_receipt(self, spec: CapabilityToolSpec) -> ExecutionReceipt | None:
        source = self.offer_source
        if source is None:
            return None
        try:
            return source.resolve_receipt(spec)
        except Exception:
            return None

    def tool_names_for_mode(self, mode: ClientMode) -> tuple[str, ...]:
        selected: list[str] = []
        seen: set[str] = set()
        for module in self.modules:
            if not module.applies_to_mode(mode):
                continue
            for tool_name in module.tools:
                if tool_name in seen:
                    continue
                seen.add(tool_name)
                selected.append(tool_name)
        return tuple(selected)

    def _default_modules(self) -> tuple[CapabilityModule, ...]:
        return build_default_modules()
