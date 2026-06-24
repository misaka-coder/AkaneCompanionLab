from __future__ import annotations

from typing import Any, ClassVar, Mapping, Protocol

from .types import CapabilityDescriptor, CapabilityResult, HealthStatus, InvocationContext


class CapabilityAdapter(Protocol):
    type: ClassVar[str]
    provider_id: str

    async def health(self) -> HealthStatus: ...

    async def list_capabilities(self) -> tuple[CapabilityDescriptor, ...]: ...

    async def invoke(
        self,
        capability_id: str,
        args: Mapping[str, Any],
        ctx: InvocationContext,
    ) -> CapabilityResult: ...

    async def aclose(self) -> None: ...
