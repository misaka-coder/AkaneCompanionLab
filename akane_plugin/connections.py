"""Declarative connection configuration; execution and private storage stay in the host."""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from capcore import CapabilityDescriptor, build_tool_spec, validate_invocation_args

from .contracts import PluginConnectionResult, is_valid_capability_id, is_valid_permission_id


def connection_permission(name):
    permission = f"connection.{name}.read"
    if not is_valid_capability_id(name) or not is_valid_permission_id(permission):
        raise ValueError("connection_name_invalid")
    return permission


def connection_name_from_permission(permission):
    if not isinstance(permission, str) or not permission.startswith("connection.") or not permission.endswith(".read"):
        return None
    name = permission[len("connection."):-len(".read")]
    try:
        connection_permission(name)
    except ValueError:
        return None
    return name


@dataclass(frozen=True)
class ConnectionSpec:
    """One plugin-local connection schema. Private fields name whole top-level values."""

    name: str
    schema: dict[str, Any]
    private_fields: tuple[str, ...] = ()
    description: str = ""
    version: int = 1

    def __post_init__(self):
        connection_permission(self.name)
        if type(self.version) is not int or self.version < 1 or not isinstance(self.description, str):
            raise ValueError("connection_declaration_invalid")
        if not isinstance(self.schema, dict) or self.schema.get("type") != "object":
            raise ValueError("connection_schema_object_required")
        # Compile and validate with the same public JSON Schema authority as tools.
        canonical = build_tool_spec(self.descriptor()).input_schema
        properties = canonical.get("properties", {})
        if (not isinstance(self.private_fields, tuple)
                or any(not isinstance(key, str) or key not in properties for key in self.private_fields)
                or len(set(self.private_fields)) != len(self.private_fields)):
            raise ValueError("connection_private_fields_invalid")
        object.__setattr__(self, "schema", deepcopy(canonical))
        # Declarations are public catalog data, never a source of credentials.
        def has_default(value):
            return isinstance(value, dict) and ("default" in value or any(has_default(v) for v in value.values())) or (
                isinstance(value, list) and any(has_default(v) for v in value))
        if any(has_default(properties[key]) for key in self.private_fields):
            raise ValueError("connection_private_default_forbidden")

    def descriptor(self):
        # A validation contract only; this descriptor is never registered as a tool.
        return CapabilityDescriptor("connection." + self.name, self.name, self.description, (), False,
                                    "low", "never", (), None, (), (), {}, input_schema=self.schema)

    def validate(self, values):
        return validate_invocation_args(self.descriptor(), values)

    def defaults(self):
        return {key: deepcopy(value["default"]) for key, value in self.schema.get("properties", {}).items()
                if "default" in value}

    def as_dict(self):
        return {"name": self.name, "version": self.version, "description": self.description,
                "schema": deepcopy(self.schema), "private_fields": list(self.private_fields)}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != {"name", "version", "description", "schema", "private_fields"}:
            raise ValueError("connection_declaration_invalid")
        if not isinstance(value["private_fields"], list):
            raise ValueError("connection_private_fields_invalid")
        return cls(**{**value, "private_fields": tuple(value["private_fields"])})


def validate_connection_specs(specs, permissions):
    if not isinstance(specs, tuple) or any(not isinstance(spec, ConnectionSpec) for spec in specs):
        raise ValueError("connection_declarations_invalid")
    if len({spec.name for spec in specs}) != len(specs):
        raise ValueError("connection_declarations_duplicate")
    for spec in specs:
        ConnectionSpec.from_dict(spec.as_dict())
        if connection_permission(spec.name) not in permissions:
            raise ValueError("connection_permission_required")


class ConnectionError(RuntimeError):
    """A structured connection failure without configuration values in its message."""

    def __init__(self, result):
        self.result = result
        super().__init__(result.reason)


class Connections:
    def __init__(self, port):
        self._port = port

    async def resolve(self, name):
        connection_permission(name)
        if self._port is None:
            return PluginConnectionResult(False, "unavailable", "connection_permission_required")
        return await self._port.resolve(name)

    async def require(self, name):
        """Get the declared configuration or raise a structured ConnectionError."""
        result = await self.resolve(name)
        if not result.ok:
            raise ConnectionError(result)
        return result.options
