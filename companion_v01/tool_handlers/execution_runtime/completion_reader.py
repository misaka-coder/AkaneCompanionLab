"""Read one bounded terminal page using the original run/resource authority."""
from ...execution_run import execute_exec_status


class ExecutionCompletionReader:
    def __init__(self, handler, provider, context, initial):
        self.handler, self.provider, self.context = handler, provider, context
        self.run_id = str(initial.data["run_id"])
        self.owner = handler._owner(context)
        self.cursor = initial.data.get("next_cursor")

    def observe(self, mapped):
        if mapped.data.get("run_id") == self.run_id and mapped.data.get("next_cursor"):
            self.cursor = mapped.data["next_cursor"]

    def __call__(self):
        mapped = execute_exec_status(self.provider, owner=self.owner, run_id=self.run_id,
                                     cursor=self.cursor, wait_seconds=0)
        mapped = self.handler._settle_resources(mapped, run_id=self.run_id, context=self.context)
        result = self.handler._mapped_result(mapped)
        if mapped.event_status in {"unknown", "unavailable", "execution_unknown"}:
            from dataclasses import replace
            result.followup_envelope = replace(result.followup_envelope, complete=False,
                diagnostics={"reason": mapped.reason or "execution_output_unavailable"})
        return result

    def inspect(self):
        store = getattr(self.provider, "store", None)
        get = getattr(store, "get", None)
        record = get(self.run_id, owner=self.owner) if callable(get) else None
        if record is not None and record.status == "running":
            return None
        result = self()
        if result.state_updates.get("capability_execution", {}).get("status") == "running":
            return None
        return result
