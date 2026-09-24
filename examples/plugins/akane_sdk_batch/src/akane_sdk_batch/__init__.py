"""A real text batch through public composition, with resource diagnostics."""
import asyncio
from akane_plugin import Plugin, Result, ToolCallError, ToolContext

PLUGIN_ID = "example.batch"


def create_plugin():
    plugin = Plugin(PLUGIN_ID, permissions=("capability.invoke",))

    @plugin.tool
    def measure(text: str) -> int:
        return len(text.encode("utf-8"))

    @plugin.tool
    async def chunk(text: str, ctx: ToolContext) -> int:
        return await ctx.tools.call(PLUGIN_ID + ".measure", {"text": text})

    @plugin.tool(input_schema={"type": "object", "properties": {
        "text": {"type": "string"}, "count": {"type": "integer", "minimum": 1},
        "peer": {"type": "string"}, "parallel": {"type": "boolean"},
    }, "required": ["text", "count"], "additionalProperties": False}, output_schema={"type": "object"})
    async def batch(arguments, ctx: ToolContext):
        before = await ctx.tools.budget()
        target = arguments.get("peer", PLUGIN_ID) + ".chunk"
        async def one():
            try:
                return await ctx.tools.call(target, {"text": arguments["text"]})
            except ToolCallError as exc:
                return {"status": exc.result.status, "reason": exc.result.reason, "details": exc.result.content}
        if arguments.get("parallel", False):
            outcomes = []
            for offset in range(0, arguments["count"], 8):
                group = await asyncio.gather(*(one() for _ in range(min(8, arguments["count"] - offset))))
                outcomes.extend(group)
                if any(isinstance(item, dict) for item in group):
                    break
        else:
            outcomes = []
            for _ in range(arguments["count"]):
                outcome = await one()
                outcomes.append(outcome)
                if isinstance(outcome, dict):
                    break
        value = {"before": before, "after": await ctx.tools.budget(), "outcomes": outcomes,
                 "completed": sum(isinstance(item, int) for item in outcomes),
                 "requested": arguments["count"]}
        if value["completed"] != value["requested"]:
            return Result(is_error=True, status="failed", reason="batch_incomplete", content=value)
        return value

    return plugin
