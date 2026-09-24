"""Versioned JSON service identities shared by declarations and host admission."""
import re
import copy
from capcore import build_tool_spec
from .contracts import is_valid_capability_id, ServiceDependency

SERVICE_PROVIDE_PERMISSION = "service.provide"
_METHOD = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def service_discovery_target(service_id=None):
    if service_id is not None and not is_valid_capability_id(service_id):
        raise ValueError("service_id_invalid")
    return {"operation": "services.list", "service_id": service_id}


def service_target(service_id, method, version=1):
    if not is_valid_capability_id(service_id):
        raise ValueError("service_id_invalid")
    if not isinstance(method, str) or not _METHOD.fullmatch(method):
        raise ValueError("service_method_invalid")
    if type(version) is not int or version < 1:
        raise ValueError("service_version_invalid")
    return {"service_id": service_id, "method": method, "version": version}


def service_method_info(descriptor):
    raw = descriptor.raw or {}
    if "service" not in raw:
        return None
    value = raw["service"]
    if not isinstance(value, dict) or set(value) != {"service_id", "method", "version"}:
        raise ValueError("service_descriptor_invalid")
    return service_target(**value)


def service_catalog(descriptors):
    """Project admitted methods into versioned contracts, never model tools."""
    contracts = {}
    for descriptor in descriptors:
        info = service_method_info(descriptor)
        if info is None:
            continue
        key = (info["service_id"], info["version"])
        spec = build_tool_spec(descriptor)
        item = contracts.setdefault(key, {"service_id": key[0], "version": key[1], "methods": []})
        item["methods"].append({"name": info["method"],
                                "input_schema": copy.deepcopy(spec.input_schema),
                                "output_schema": copy.deepcopy(spec.output_schema)})
    for item in contracts.values():
        item["methods"].sort(key=lambda method: method["name"])
    return [contracts[key] for key in sorted(contracts)]


def validate_service_dependencies(dependencies):
    if not isinstance(dependencies, tuple) or any(not isinstance(item, ServiceDependency) for item in dependencies):
        raise ValueError("service_dependencies_invalid")
    if len(set(dependencies)) != len(dependencies):
        raise ValueError("service_dependencies_duplicate")
    for dependency in dependencies:
        service_target(dependency.service_id, "validate", dependency.version)
    return dependencies
