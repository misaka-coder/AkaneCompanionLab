"""One installable policy; the deployer chooses its behavior through options."""
from akane_plugin import Plugin


def create_plugin():
    plugin = Plugin("example.execution-policy")
    policy = plugin.policy("example.effect_guard")

    @policy.before
    def decide(request):
        options = request["options"]
        blocked = options.get("blocked_tools", [])
        effects = options.get("blocked_effects", [])
        allowed = options.get("allow_effects")
        if any(not isinstance(items, list) or any(not isinstance(item, str) for item in items)
               for items in (blocked, effects, *([allowed] if allowed is not None else []))):
            raise ValueError("invalid_policy_options")
        if type(options.get("require_metadata", False)) is not bool:
            raise ValueError("invalid_policy_options")
        if options.get("require_metadata", False) and not request["risk"]:
            return {"decision": "deny", "reason": "deployment_metadata_required"}
        if allowed is not None and set(request["effects"]) - set(allowed):
            return {"decision": "deny", "reason": "deployment_effect_disabled"}
        if request["tool_id"] in blocked or set(request["effects"]).intersection(effects):
            return {"decision": "deny", "reason": "deployment_effect_disabled"}
        return {"decision": "allow"}

    @policy.transform
    def transform(request):
        # An explicit tool-to-factor map keeps unit conversion deployment-owned.
        factors = request["options"].get("numeric_factors", {})
        if not isinstance(factors, dict) or any(type(value) not in (int, float) for value in factors.values()):
            raise ValueError("invalid_policy_options")
        if request["tool_id"] not in factors:
            return {"action": "keep"}
        value = request["outcome"]["value"]
        if type(value) not in (int, float):
            raise ValueError("numeric_result_required")
        return {"action": "replace", "value": value * factors[request["tool_id"]]}

    @policy.present
    def present(request):
        prefix = request["options"].get("display_prefix")
        if prefix is None:
            return {"action": "keep"}
        if not isinstance(prefix, str):
            raise ValueError("invalid_policy_options")
        return {"action": "replace", "text": prefix + request["rendered"]}

    @policy.wrap
    def wrap(request):
        options = request["options"]
        if request["phase"] == "before":
            max_attempts = options.get("max_attempts", 1)
            if type(max_attempts) is not int or not 1 <= max_attempts <= 8:
                raise ValueError("invalid_policy_options")
            return {"action": "continue", "max_attempts": max_attempts}
        retry_statuses = options.get("retry_statuses", [])
        if not isinstance(retry_statuses, list) or any(not isinstance(item, str) for item in retry_statuses):
            raise ValueError("invalid_policy_options")
        return {"action": "retry" if request["outcome"]["status"] in retry_statuses else "return"}

    return plugin
