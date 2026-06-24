from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RetrievalPipelineResult:
    used_retrieval: bool
    confirmed_snippets: list[str]
    router_output: dict[str, Any]
    router_timing: dict[str, Any]
    retrieval_result: dict[str, Any]
    verifier_output: dict[str, Any]
    verifier_timing: dict[str, Any]
