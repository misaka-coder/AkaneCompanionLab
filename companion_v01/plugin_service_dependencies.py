"""Resolve declared service dependencies before activating prepared workers."""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from akane_plugin import ServiceDependency
from akane_plugin.contracts import is_valid_plugin_id
from akane_plugin.service_contracts import service_target, validate_service_dependencies


def provider_bindings(value):
    if not isinstance(value, (tuple, list)):
        raise ValueError("service_provider_bindings_invalid")
    result = {}
    for item in value:
        if not isinstance(item, dict) or set(item) != {"service_id", "version", "plugin_id"}:
            raise ValueError("service_provider_bindings_invalid")
        target = service_target(item["service_id"], "validate", item["version"])
        key = (target["service_id"], target["version"])
        if not is_valid_plugin_id(item["plugin_id"]) or key in result:
            raise ValueError("service_provider_bindings_invalid")
        result[key] = item["plugin_id"]
    return MappingProxyType(result)


@dataclass(frozen=True)
class ServiceDependencyPlan:
    activation_order: tuple[str, ...]
    waiting: Mapping[str, list[dict[str, Any]]]
    bindings: Mapping[tuple[str, int], str]
    bootstrap_errors: tuple[dict, ...] = ()


def resolve_dependencies(contributions, bindings=(), *, bootstrap_services=()):
    selected = provider_bindings(bindings)
    validate_service_dependencies(tuple(bootstrap_services))
    providers, requirements = {}, {}
    for plugin_id, contribution in contributions.items():
        requirements[plugin_id] = validate_service_dependencies(tuple(
            ServiceDependency(**item) for item in contribution.get("requires_services", ())
        ))
        for service in contribution.get("services", ()):
            target = service_target(service["service_id"], "validate", service["version"])
            providers.setdefault((target["service_id"], target["version"]), set()).add(plugin_id)
    edges = {plugin_id: set() for plugin_id in contributions}
    waiting = {}
    def resolve(dependency):
        key = (dependency.service_id, dependency.version)
        candidates = providers.get(key, set())
        chosen = selected.get(key)
        reason = ("service_provider_unavailable" if chosen and chosen not in candidates
                  else "service_dependency_missing" if not candidates
                  else "service_provider_ambiguous" if not chosen and len(candidates) > 1 else "")
        if reason:
            return None, {"reason": reason, "service_id": key[0], "version": key[1], "providers": sorted(candidates),
                          **({"selected_provider": chosen} if chosen else {})}
        return chosen or next(iter(candidates)), None
    for plugin_id, dependencies in requirements.items():
        for dependency in dependencies:
            provider, error = resolve(dependency)
            if error:
                waiting.setdefault(plugin_id, []).append(error)
            else:
                # A locally declared implementation is prepared with its
                # consumer. It needs no inter-plugin activation ordering edge.
                if provider != plugin_id:
                    edges[plugin_id].add(provider)

    # Tarjan marks only actual cycle members. Their consumers receive a
    # dependency-unavailable diagnosis instead of being mislabeled cyclic.
    stack, indices, lowlinks, on_stack, components = [], {}, {}, set(), []
    def visit(node):
        indices[node] = lowlinks[node] = len(indices)
        stack.append(node)
        on_stack.add(node)
        for peer in sorted(edges[node]):
            if peer not in indices:
                visit(peer)
                lowlinks[node] = min(lowlinks[node], lowlinks[peer])
            elif peer in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[peer])
        if lowlinks[node] == indices[node]:
            members = []
            while True:
                peer = stack.pop()
                on_stack.remove(peer)
                members.append(peer)
                if peer == node:
                    break
            if len(members) > 1:
                components.append(sorted(members))
    for plugin_id in sorted(edges):
        if plugin_id not in indices:
            visit(plugin_id)
    for members in components:
        for plugin_id in members:
            waiting.setdefault(plugin_id, []).append({"reason": "service_dependency_cycle", "plugins": members})
    changed = True
    while changed:
        changed = False
        for plugin_id, dependencies in edges.items():
            blocked = sorted(dependencies.intersection(waiting))
            if blocked and plugin_id not in waiting:
                waiting[plugin_id] = [{"reason": "service_dependency_unavailable", "plugins": blocked}]
                changed = True
    order = []
    def append(node):
        if node in order or node in waiting:
            return
        for peer in sorted(edges[node]):
            append(peer)
        order.append(node)
    bootstrap_errors = []
    for dependency in bootstrap_services:
        provider, error = resolve(dependency)
        if error:
            bootstrap_errors.append(error)
        elif provider in waiting:
            bootstrap_errors.append({"reason": "service_dependency_unavailable", "plugin_id": provider,
                                     "service_id": dependency.service_id, "version": dependency.version})
        else:
            append(provider)
    for plugin_id in sorted(edges):
        append(plugin_id)
    return ServiceDependencyPlan(tuple(order), MappingProxyType(waiting), selected, tuple(bootstrap_errors))
