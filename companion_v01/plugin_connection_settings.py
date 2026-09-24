"""Schema-backed plugin connections in the existing per-profile settings store."""

from copy import deepcopy
import json

from akane_plugin import ConnectionSpec, PluginConnectionResult
from akane_plugin.contracts import is_valid_plugin_id

from .local_capability_config import plugin_connection_config


def _failure(reason, status="invalid_config"):
    return {"ok": False, "status": status, "reason": reason}


def _record(section, plugin_id, name):
    plugin = section.get(plugin_id, {})
    if not isinstance(plugin, dict):
        raise ValueError("connection_config_invalid")
    record = plugin.get(name)
    if record is not None and (
        not isinstance(record, dict) or type(record.get("revision")) is not int or record["revision"] < 1
        or type(record.get("version")) is not int or record["version"] < 1
        or not isinstance(record.get("enabled"), bool) or not isinstance(record.get("values"), dict)
        or not isinstance(record.get("private_fields"), list)
        or any(not isinstance(key, str) for key in record["private_fields"])
    ):
        raise ValueError("connection_config_invalid")
    if record is not None:
        try:
            json.dumps(record, allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError("connection_config_invalid") from None
    return record


def _configuration(spec, record):
    if record is None:
        return _failure("connection_not_configured", "not_configured")
    if not record["enabled"]:
        return _failure("connection_disabled", "disabled")
    if record["version"] != spec.version:
        return _failure("connection_schema_version_changed")
    checked = spec.validate({**spec.defaults(), **record["values"]})
    if not checked.ok:
        return _failure("connection_values_invalid")
    return {"ok": True, "status": "configured", "reason": "", "values": checked.normalized_args}


def _public(spec, record):
    private = set(spec.private_fields) | set(record["private_fields"] if record else ())
    values = {**spec.defaults(), **(record["values"] if record else {})}
    state = _configuration(spec, record)
    return {"ok": True, "status": state["status"], "reason": state["reason"],
            "connection": spec.as_dict(), "revision": record["revision"] if record else 0,
            "enabled": record["enabled"] if record else False,
            "values": {key: deepcopy(value) for key, value in values.items() if key not in private},
            "configured_private_fields": sorted(key for key in private if key in values),
            "private_fields": sorted(private)}


class PluginConnectionSettings:
    def __init__(self, base_dir):
        self._base_dir = base_dir

    def _access(self, profile_user_id, plugin_id, spec, update=None):
        if not is_valid_plugin_id(plugin_id) or not isinstance(spec, ConnectionSpec):
            return _failure("connection_declaration_invalid")
        try:
            return plugin_connection_config(base_dir=self._base_dir, profile_user_id=profile_user_id, update=update)
        except ValueError as exc:
            reason = str(exc)
            return _failure(reason if reason in {"connection_profile_invalid", "connection_settings_unavailable",
                                                "connection_config_invalid"} else "connection_config_invalid")
        except Exception:
            return _failure("connection_settings_io_failed", "unavailable")

    def read(self, profile_user_id, plugin_id, spec):
        section = self._access(profile_user_id, plugin_id, spec)
        if section.get("ok") is False:
            return section
        try:
            return _public(spec, _record(section, plugin_id, spec.name))
        except (TypeError, ValueError):
            return _failure("connection_config_invalid")

    def resolve(self, profile_user_id, plugin_id, spec):
        section = self._access(profile_user_id, plugin_id, spec)
        if section.get("ok") is False:
            return PluginConnectionResult(False, section["status"], section["reason"])
        try:
            record = _record(section, plugin_id, spec.name)
            state = _configuration(spec, record)
            if not state["ok"]:
                return PluginConnectionResult(False, state["status"], state["reason"])
            return PluginConnectionResult(True, "configured", options=deepcopy(state["values"]),
                private_option_fields=tuple(sorted(set(spec.private_fields) | set(record["private_fields"]))))
        except (TypeError, ValueError):
            return PluginConnectionResult(False, "invalid_config", "connection_config_invalid")

    def save(self, profile_user_id, plugin_id, spec, payload):
        if (not isinstance(payload, dict) or set(payload) - {"values", "clear_fields", "enabled", "expected_revision"}
            or not isinstance(payload.get("values", {}), dict)
            or not isinstance(payload.get("clear_fields", []), list)
            or any(not isinstance(key, str) for key in payload.get("clear_fields", []))
            or type(payload.get("expected_revision")) is not int or payload["expected_revision"] < 0
            or ("enabled" in payload and not isinstance(payload["enabled"], bool))):
            return _failure("connection_update_invalid", "invalid_request")
        try:
            payload = json.loads(json.dumps(payload, allow_nan=False))
        except (TypeError, ValueError):
            return _failure("connection_values_invalid", "invalid_request")

        def update(section):
            record = _record(section, plugin_id, spec.name)
            revision = record["revision"] if record else 0
            if payload["expected_revision"] != revision:
                return {**_failure("connection_revision_conflict", "conflict"), "revision": revision}
            values = {**spec.defaults(), **(record["values"] if record else {})}
            clears = payload.get("clear_fields", [])
            if set(clears) & set(payload.get("values", {})):
                return _failure("connection_clear_update_conflict", "invalid_request")
            for key in clears:
                values.pop(key, None)
            values.update(payload.get("values", {}))
            enabled = payload.get("enabled", record["enabled"] if record else True)
            checked = spec.validate(values)
            # Disabled drafts may omit required fields, but never store malformed values.
            errors = [error for error in checked.errors if enabled or error.code != "missing_required"]
            if errors:
                return {**_failure("connection_values_invalid"), "errors": [
                    {"code": error.code, "field": error.argument if error.argument in spec.schema.get("properties", {}) else ""}
                    for error in errors]}
            new_record = {"version": spec.version, "revision": revision + 1, "enabled": enabled, "values": values,
                          "private_fields": sorted(set(spec.private_fields) | set(record["private_fields"] if record else ()))}
            # Diagnose the existing private RPC budget at save time as well as
            # invocation time. P7 will make this transport budget configurable.
            from .plugin_connections import connection_result_to_wire
            try:
                connection_result_to_wire(PluginConnectionResult(True, "configured", options=values,
                    private_option_fields=tuple(new_record["private_fields"])))
            except ValueError as exc:
                reason = "connection_result_too_large" if str(exc) == "connection_result_too_large" else "connection_values_invalid"
                return _failure(reason)
            section.setdefault(plugin_id, {})[spec.name] = new_record
            return _public(spec, new_record)

        return self._access(profile_user_id, plugin_id, spec, update=update)
