"""Optional continuation metadata on the existing canonical CapCore result."""

from dataclasses import dataclass

from capcore import CapabilityResult


FOLLOWUP_MODES = frozenset({"auto", "required", "none"})


def validate_followup(value, *, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str) or value not in FOLLOWUP_MODES:
        raise ValueError("followup_must_be_auto_required_or_none")
    return value


@dataclass(frozen=True)
class Result(CapabilityResult):
    """Return a JSON value and optionally override this result's model followup.

    Program callers always receive the value/error. This option does not cancel
    other work or an explicitly required model consumer.
    """

    is_error: bool = False
    followup: str | None = None

    def __post_init__(self):
        validate_followup(self.followup, optional=True)
        if self.content is None and self.has_value:
            object.__setattr__(self, "content", self.value)

    def as_dict(self):
        payload = super().as_dict()
        if self.followup is not None:
            payload["followup"] = self.followup
        return payload
