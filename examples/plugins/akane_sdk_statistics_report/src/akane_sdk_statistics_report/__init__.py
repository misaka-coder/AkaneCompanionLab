"""Consume and inspect a versioned service using only the public SDK."""
from akane_plugin import Plugin, ToolContext, ServiceDependency

plugin = Plugin("example.statistics-report", permissions=("capability.invoke",),
                requires_services=(ServiceDependency("statistics", version=1),))


@plugin.tool
async def summarize(values: list[float], ctx: ToolContext) -> dict[str, float]:
    """Summarize a nonempty numeric dataset using the installed statistics service."""
    return await ctx.services.call("statistics", "summarize", {"values": values}, version=1)


@plugin.tool(output_schema={"type": "array", "items": {"type": "object"}})
async def describe_service(ctx: ToolContext):
    """Inspect the statistics providers, current selection and full method schemas without calculating."""
    return await ctx.services.list("statistics")


def create_plugin():
    return plugin
