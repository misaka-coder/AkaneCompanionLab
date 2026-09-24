"""SDK-only state events with observation, timeline and explicit turns."""

from akane_plugin import EventBinding, EventReceipt, ObservationReceipt, Plugin, ToolContext, TurnReceipt


PLUGIN_ID = "example.sdk-event-state"
STATE_EVENT = "example.state.changed"

STATE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "state_version": {"type": "integer", "minimum": 1},
        "state": {"type": "object"},
    },
    "required": ["state_version", "state"],
    "additionalProperties": False,
}


def create_plugin():
    plugin = Plugin(
        PLUGIN_ID,
        permissions=("capability.invoke", "event.emit", "context.observe", "agent.turn.request"),
    )

    @plugin.tool(input_schema=STATE_INPUT_SCHEMA, output_schema=STATE_INPUT_SCHEMA)
    def canonicalize(arguments):
        """Return the business state unchanged after validating its version."""
        return {
            "state_version": arguments["state_version"],
            "state": arguments["state"],
        }

    @plugin.tool(input_schema=STATE_INPUT_SCHEMA, output_schema={"type": "object"})
    async def observe(arguments, ctx: ToolContext) -> ObservationReceipt:
        """Replace the current observation without waking the model."""
        state = await ctx.tools.call(
            f"{PLUGIN_ID}.canonicalize",
            {
                "state_version": arguments["state_version"],
                "state": arguments["state"],
            },
        )
        return await ctx.observe("state", state)

    @plugin.tool(input_schema=STATE_INPUT_SCHEMA, output_schema={"type": "object"})
    async def publish(arguments, ctx: ToolContext) -> EventReceipt:
        """Publish one state fact after a real worker-side tool call."""
        state = await ctx.tools.call(
            f"{PLUGIN_ID}.canonicalize",
            {
                "state_version": arguments["state_version"],
                "state": arguments["state"],
            },
        )
        return await ctx.events.emit(
            STATE_EVENT,
            state,
            event_key=f"state:{state['state_version']}",
        )

    @plugin.tool
    async def status(dispatch_id: str, ctx: ToolContext) -> EventReceipt:
        """Read the host receipt for a publication in this conversation."""
        return await ctx.events.status(dispatch_id)

    @plugin.tool
    async def turn_status(request_id: str, ctx: ToolContext) -> TurnReceipt:
        """Read the host's actual model and delivery receipt."""
        return await ctx.turn_status(request_id)

    @plugin.tool
    async def decide(observation_version: int, ctx: ToolContext) -> TurnReceipt:
        """Request one ordinary Agent turn and reject stale observations."""
        return await ctx.request_turn(
            "Review the current state and choose the next action.",
            {"kind": "state_decision"},
            observations={"state": observation_version},
            stale="reject",
            coalesce_key="state-decision",
        )

    @plugin.tool
    async def bind(ctx: ToolContext):
        """Bind every conversation-scoped state subscriber explicitly."""
        bindings = []
        for name in ("capture", "timeline", "decide"):
            binding = await ctx.events.bind(f"{PLUGIN_ID}.{name}")
            bindings.append(binding.as_dict())
        return {"status": "bound", "bindings": bindings}

    @plugin.tool
    async def unbind(scope_id: str, ctx: ToolContext) -> EventBinding:
        """Revoke the conversation binding and its pending work."""
        return await ctx.events.unbind(scope_id)

    @plugin.tool(execution_class="long_task", followup="none")
    def checkpoint(state_version: int, steps: int = 1000000):
        """Compute a host-managed background checkpoint without owning a worker pool."""
        bounded_steps = max(1, min(int(steps), 20_000_000))
        checksum = 0
        for index in range(bounded_steps):
            checksum = (checksum + index + state_version) % 1_000_003
        return {"state_version": state_version, "steps": bounded_steps, "checksum": checksum}

    @plugin.on(STATE_EVENT, name="capture", sources=(PLUGIN_ID,), scope="conversation")
    async def capture(event, ctx):
        observation = await ctx.observe("state", event.data)
        return {
            "event": {
                "event_id": event.event_id,
                "source": event.source,
                "scope": event.scope,
                "version": event.version,
                "occurred_at_ms": event.occurred_at_ms,
                "received_at_ms": event.received_at_ms,
            },
            "data": event.data,
            "observation": observation.as_dict(),
        }

    @plugin.on(STATE_EVENT, name="timeline", sources=(PLUGIN_ID,), scope="conversation", persistence="timeline")
    async def timeline(event, ctx):
        return {"event_id": event.event_id, "state_version": event.data["state_version"]}

    @plugin.on(STATE_EVENT, name="decide", sources=(PLUGIN_ID,), scope="conversation")
    async def decide_on_event(event, ctx):
        return {"event_id": event.event_id, "state_version": event.data["state_version"]}

    return plugin


__all__ = ["create_plugin"]
