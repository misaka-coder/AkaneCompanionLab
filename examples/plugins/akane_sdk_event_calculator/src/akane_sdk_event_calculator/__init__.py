"""An independent event subscriber composing a normal public function tool."""

from akane_plugin import Plugin

plugin = Plugin("example.event-calculator", permissions=("capability.invoke", "event.emit"))


@plugin.tool
async def add(a: int, b: int) -> int:
    """Add two integers through the same contract used by model and program callers."""
    return a + b


@plugin.on("example.numbers-ready", sources=("example.event-source",))
async def calculate(event, ctx):
    value = await ctx.tools.call("example.event-calculator.add", event.data)
    # Returning the receipt explicitly links this dispatch: our delivery stays
    # pending until the receiver of sum-ready has reached its own terminal.
    return await ctx.events.emit("example.sum-ready", {"sum": value, "input_event_id": event.event_id})


def create_plugin():
    return plugin
