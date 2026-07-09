from __future__ import annotations

from typing import Any

from capcore import CapabilityIOSlot
from capcore_adapter_python import PythonCapabilityAdapter, PythonCapabilitySpec

from companion_v01.text_utils import detect_time_of_day_from_text, extract_semantic_tags, normalize_text


VISIBLE_SURFACES = ("base", "web", "desktop", "qq")


class AkanePythonCapabilityAdapter(PythonCapabilityAdapter):
    """Akane-owned local callable adapter.

    The reusable package owns callable invocation. Akane owns which concrete
    local functions are exposed to the model.
    """

    def __init__(self) -> None:
        super().__init__(
            provider_id="provider.python.akane",
            capabilities=build_akane_python_capability_specs(),
        )

    def list_capabilities_sync(self) -> tuple[Any, ...]:
        return tuple(
            descriptor
            for capability_id, descriptor in self._descriptor_by_id.items()
            if self._spec_by_id[capability_id].enabled
        )


def build_akane_python_capability_specs() -> tuple[PythonCapabilitySpec, ...]:
    return (
        PythonCapabilitySpec.from_callable(
            _normalize_text_capability,
            capability_id="python.akane.normalize_text",
            display_name="Normalize Text",
            short_hint="Normalize whitespace in a short text string.",
            visible_in=VISIBLE_SURFACES,
            prompt_exposed=False,
            risk="low",
            confirm="never",
            inputs=(
                CapabilityIOSlot(
                    name="text",
                    kind="string",
                    required=True,
                    raw={"description": "Text to normalize.", "minLength": 1, "maxLength": 2000},
                ),
            ),
        ),
        PythonCapabilitySpec.from_callable(
            _extract_semantic_tags_capability,
            capability_id="python.akane.extract_semantic_tags",
            display_name="Extract Semantic Tags",
            short_hint="Extract compact semantic tags from short text.",
            visible_in=VISIBLE_SURFACES,
            prompt_exposed=False,
            risk="low",
            confirm="never",
            inputs=(
                CapabilityIOSlot(
                    name="text",
                    kind="string",
                    required=True,
                    raw={"description": "Text to tag.", "minLength": 1, "maxLength": 2000},
                ),
                CapabilityIOSlot(
                    name="limit",
                    kind="integer",
                    required=False,
                    raw={"description": "Maximum tags to return.", "minimum": 1, "maximum": 20},
                ),
            ),
        ),
        PythonCapabilitySpec.from_callable(
            _detect_time_of_day_capability,
            capability_id="python.akane.detect_time_of_day",
            display_name="Detect Time Of Day",
            short_hint="Detect morning, afternoon, night, or midnight hints in text.",
            visible_in=VISIBLE_SURFACES,
            prompt_exposed=False,
            risk="low",
            confirm="never",
            inputs=(
                CapabilityIOSlot(
                    name="text",
                    kind="string",
                    required=True,
                    raw={"description": "Text that may contain a coarse time-of-day hint.", "minLength": 1},
                ),
            ),
        ),
    )


def _normalize_text_capability(text: str) -> dict[str, str]:
    return {"normalized": normalize_text(text)}


def _extract_semantic_tags_capability(text: str, limit: int = 8) -> dict[str, Any]:
    bounded_limit = max(1, min(20, int(limit or 8)))
    return {"tags": extract_semantic_tags(text, limit=bounded_limit)}


def _detect_time_of_day_capability(text: str) -> dict[str, Any]:
    time_of_day = detect_time_of_day_from_text(text)
    return {
        "matched": bool(time_of_day),
        "time_of_day": time_of_day or "",
    }
