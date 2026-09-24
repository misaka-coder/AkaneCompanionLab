"""A board observer that asks the character for a decision only explicitly."""

from typing import Any

from akane_plugin import EventBinding, EventReceipt, ObservationReceipt, Plugin, ToolContext, TurnReceipt


def create_plugin():
    plugin = Plugin("example.board", permissions=("context.observe", "agent.turn.request", "event.emit"))

    @plugin.tool
    async def update(state: dict[str, Any], ctx: ToolContext) -> ObservationReceipt:
        """Update the current board without asking for a reply."""
        return await ctx.observe("board", state)

    @plugin.tool
    async def decide(version: int, ctx: ToolContext) -> TurnReceipt:
        """Ask the character to choose a move, using the latest board if it changed."""
        return await ctx.request_turn("Choose the next move from the current board.",
            observations={"board": version}, stale="latest", coalesce_key="next-move")

    @plugin.tool
    async def status(request_id: str, ctx: ToolContext) -> TurnReceipt:
        """Read the actual model and delivery outcome of a previous request."""
        return await ctx.turn_status(request_id)

    @plugin.tool
    async def cancel(request_id: str, ctx: ToolContext) -> TurnReceipt:
        """Cancel a pending decision or request a running decision to stop."""
        return await ctx.cancel_turn(request_id)

    @plugin.tool
    async def enable(ctx: ToolContext) -> EventBinding:
        """Enable board-change decisions for this conversation and character."""
        return await ctx.events.bind("example.board.changed")

    @plugin.tool
    async def disable(scope_id: str, ctx: ToolContext) -> EventBinding:
        """Remove a board-change subscription and revoke its pending requests."""
        return await ctx.events.unbind(scope_id)

    @plugin.tool
    async def publish(state: dict[str, Any], ctx: ToolContext) -> EventReceipt:
        """Publish a new board to the explicitly enabled subscription."""
        return await ctx.events.emit("example.board.changed", state)

    @plugin.on("example.board.changed", name="changed", sources=("example.board",), scope="conversation")
    async def changed(event, ctx):
        observation = await ctx.observe("board", event.data)
        if observation.status != "observed":
            return observation
        return await ctx.request_turn("Choose the next move from the current board.",
            observations={"board": observation.version}, stale="latest", coalesce_key="next-move")

    return plugin
