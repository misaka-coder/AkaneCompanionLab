"""Execution policies are versioned services selected by the deployer."""
from .contracts import is_valid_capability_id

POLICY_PROVIDE_PERMISSION = "policy.provide"
POLICY_SERVICE_PREFIX = "execution_policy."
POLICY_REQUEST_SCHEMA = {
    "type": "object", "properties": {
        "tool_id": {"type": "string"}, "invocation_id": {"type": "string"},
        "arguments": {"type": "object"}, "options": {"type": "object"},
        "executor": {"enum": ["server_local", "desktop_satellite"]},
        "effects": {"type": "array", "items": {"type": "string"}},
        "risk": {"type": "string"}, "client_mode": {"type": "string"},
    }, "required": ["tool_id", "invocation_id", "arguments", "options", "executor", "effects", "risk", "client_mode"],
    "additionalProperties": False,
}
POLICY_DECISION_SCHEMA = {
    "type": "object", "properties": {
        "decision": {"enum": ["allow", "deny"]},
        "reason": {"type": "string", "pattern": "^[a-z][a-z0-9_.-]{0,127}$"},
    }, "required": ["decision"], "additionalProperties": False,
}
POLICY_OBSERVATION_SCHEMA = {
    **POLICY_REQUEST_SCHEMA,
    "properties": {**POLICY_REQUEST_SCHEMA["properties"], "outcome": {
        "type": "object", "properties": {
            "status": {"type": "string"}, "reason": {"type": "string"}, "value": {},
        }, "required": ["status", "reason"], "additionalProperties": False,
    }}, "required": [*POLICY_REQUEST_SCHEMA["required"], "outcome"],
}
POLICY_TRANSFORM_SCHEMA = {
    "oneOf": [
        {"type": "object", "properties": {"action": {"const": "keep"}},
         "required": ["action"], "additionalProperties": False},
        {"type": "object", "properties": {"action": {"const": "replace"}, "value": {}},
         "required": ["action", "value"], "additionalProperties": False},
    ],
}


POLICY_PRESENTATION_SCHEMA = {
    **POLICY_OBSERVATION_SCHEMA,
    "properties": {**POLICY_OBSERVATION_SCHEMA["properties"], "rendered": {"type": "string"}},
    "required": [*POLICY_OBSERVATION_SCHEMA["required"], "rendered"],
}
POLICY_PRESENTATION_RESULT_SCHEMA = {
    "oneOf": [
        {"type": "object", "properties": {"action": {"const": "keep"}},
         "required": ["action"], "additionalProperties": False},
        {"type": "object", "properties": {"action": {"const": "replace"}, "text": {"type": "string"}},
         "required": ["action", "text"], "additionalProperties": False},
    ],
}


POLICY_WRAP_REQUEST_SCHEMA = {
    "type": "object", "properties": {
        "tool_id": {"type": "string"}, "invocation_id": {"type": "string"},
        "arguments": {"type": "object"}, "options": {"type": "object"},
        "executor": {"enum": ["server_local", "desktop_satellite"]},
        "effects": {"type": "array", "items": {"type": "string"}},
        "risk": {"type": "string"}, "client_mode": {"type": "string"},
        "phase": {"enum": ["before", "after"]},
        "attempt": {"type": "integer", "minimum": 1},
        "outcome": {"type": "object", "properties": {
            "status": {"type": "string"}, "reason": {"type": "string"}, "value": {},
        }, "required": ["status", "reason"], "additionalProperties": False},
    }, "required": ["tool_id", "invocation_id", "arguments", "options", "executor", "effects",
                     "risk", "client_mode", "phase", "attempt"],
    "additionalProperties": False,
}
POLICY_WRAP_RESULT_SCHEMA = {
    "oneOf": [
        {"type": "object", "properties": {
            "action": {"const": "continue"},
            "max_attempts": {"type": "integer", "minimum": 1, "maximum": 8},
        }, "required": ["action"], "additionalProperties": False},
        {"type": "object", "properties": {"action": {"const": "return"}},
         "required": ["action"], "additionalProperties": False},
        {"type": "object", "properties": {"action": {"const": "retry"}},
         "required": ["action"], "additionalProperties": False},
    ],
}


def policy_service_id(policy_id):
    if not is_valid_capability_id(policy_id) or not is_valid_capability_id(POLICY_SERVICE_PREFIX + policy_id):
        raise ValueError("execution_policy_id_invalid")
    return POLICY_SERVICE_PREFIX + policy_id


class Policy:
    """Declare a pure policy service; installation alone does not select it."""
    def __init__(self, plugin, policy_id):
        self._service = plugin.service(policy_service_id(policy_id), version=1)

    def before(self, function):
        return self._service.method(name="before", input_schema=POLICY_REQUEST_SCHEMA,
            output_schema=POLICY_DECISION_SCHEMA)(function)

    def observe(self, function):
        return self._service.method(name="observe", input_schema=POLICY_OBSERVATION_SCHEMA,
            output_schema={"type": "null"})(function)

    def transform(self, function):
        """Keep or replace a successful canonical value before final projection."""
        return self._service.method(name="transform", input_schema=POLICY_OBSERVATION_SCHEMA,
            output_schema=POLICY_TRANSFORM_SCHEMA)(function)

    def present(self, function):
        """Keep or replace model-facing text without changing the canonical value."""
        return self._service.method(name="present", input_schema=POLICY_PRESENTATION_SCHEMA,
            output_schema=POLICY_PRESENTATION_RESULT_SCHEMA)(function)

    def wrap(self, function):
        """Control a retryable execution lifecycle around one real dispatch.

        The host calls the method with ``phase`` set to ``before`` and
        ``after``.  ``before`` may raise the attempt limit; ``after`` may ask
        for another attempt.  The host still owns dispatch, cancellation and
        side-effect safety.
        """
        return self._service.method(name="wrap", input_schema=POLICY_WRAP_REQUEST_SCHEMA,
            output_schema=POLICY_WRAP_RESULT_SCHEMA)(function)
