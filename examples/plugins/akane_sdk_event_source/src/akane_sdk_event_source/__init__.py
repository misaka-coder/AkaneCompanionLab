"""A producer and receipt consumer that knows only the public event API."""

from akane_plugin import EventReceipt, Plugin, ToolContext

plugin = Plugin("example.event-source", permissions=("event.emit",))


@plugin.tool
async def publish_numbers(a: int, b: int, ctx: ToolContext) -> EventReceipt:
    """Publish two integers; return an acceptance receipt before subscribers finish."""
    return await ctx.events.emit("example.numbers-ready", {"a": a, "b": b})


@plugin.tool
async def dispatch_status(dispatch_id: str, ctx: ToolContext) -> EventReceipt:
    """Read this conversation's dispatch receipt and each subscriber's actual outcome."""
    return await ctx.events.status(dispatch_id)


@plugin.on("example.sum-ready", sources=("example.event-calculator",))
async def accept_sum(event, ctx):
    # Returning the result makes it inspectable on the downstream receipt.
    # No conversation write, notification, or model request is implied.
    return event.data


def create_plugin():
    return plugin
