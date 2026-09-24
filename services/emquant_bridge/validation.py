from __future__ import annotations

import re
from typing import Any


_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{1,39}$")


class EmQuantValidationError(ValueError):
    """Structured validation failure owned by the standalone EmQuant bridge."""

    def __init__(
        self,
        *,
        field: str,
        reason: str,
        code: str = "invalid_field",
        status: str = "invalid_data",
        provider: str = "emquant_bridge",
    ) -> None:
        self.field = str(field or "").strip()
        self.reason = str(reason or "invalid value").strip() or "invalid value"
        self.code = str(code or "invalid_field").strip() or "invalid_field"
        self.status = str(status or "invalid_data").strip() or "invalid_data"
        self.provider = str(provider or "").strip()
        super().__init__(f"{self.field or 'value'}: {self.reason}")

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "status": self.status,
            "code": self.code,
            "field": self.field,
            "provider": self.provider,
            "reason": self.reason,
        }


def normalize_emquant_code(
    value: Any,
    *,
    field: str = "code",
    status: str = "invalid_data",
    error_code: str = "invalid_field",
) -> str:
    code = str(value or "").strip().upper()
    if not code:
        raise EmQuantValidationError(
            field=field,
            reason="code is required",
            status=status,
            code=error_code,
        )
    if not _CODE_RE.fullmatch(code):
        raise EmQuantValidationError(
            field=field,
            reason="code contains unsupported characters or has an invalid length",
            status=status,
            code=error_code,
        )
    return code


__all__ = ["EmQuantValidationError", "normalize_emquant_code"]
