from __future__ import annotations

from dataclasses import dataclass


PERMISSION_ERROR_CODES = frozenset({10001003, 10001012, 10001024, 10001025})
RATE_LIMIT_ERROR_CODES = frozenset({10000016, 10003013, 10003015, 10003024})
RECONNECTING_ERROR_CODES = frozenset({10002013})
DISCONNECTED_ERROR_CODES = frozenset({10001009, 10001011, 10002014})


@dataclass(frozen=True)
class ErrorClassification:
    status: str
    authorized: bool | None
    quota_available: bool | None
    operational: bool


def classify_error_code(error_code: int) -> ErrorClassification:
    code = int(error_code or 0)
    if code == 0:
        return ErrorClassification(
            status="ok",
            authorized=True,
            quota_available=True,
            operational=True,
        )
    if code in PERMISSION_ERROR_CODES:
        return ErrorClassification(
            status="permission_denied",
            authorized=False,
            quota_available=None,
            operational=False,
        )
    if code in RATE_LIMIT_ERROR_CODES:
        return ErrorClassification(
            status="rate_limited",
            authorized=True,
            quota_available=False,
            operational=False,
        )
    if code in RECONNECTING_ERROR_CODES:
        return ErrorClassification(
            status="degraded",
            authorized=True,
            quota_available=True,
            operational=False,
        )
    if code in DISCONNECTED_ERROR_CODES:
        return ErrorClassification(
            status="disconnected",
            authorized=None,
            quota_available=None,
            operational=False,
        )
    return ErrorClassification(
        status="unavailable",
        authorized=None,
        quota_available=None,
        operational=False,
    )
