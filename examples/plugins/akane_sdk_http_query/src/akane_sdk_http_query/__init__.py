"""A separately installable HTTP query tool with plugin-declared configuration."""

import asyncio
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from akane_plugin import Plugin, Result, ToolContext


def create_plugin():
    plugin = Plugin("example.http-query", permissions=("network.read",))
    plugin.connection("query_api", description="HTTP JSON query endpoint and authentication", schema={
        "type": "object", "properties": {
            "endpoint": {"type": "string", "minLength": 1, "pattern": "^https?://"},
            "credential": {"type": "string", "minLength": 1},
            "timeout_seconds": {"type": "number", "minimum": 0.1, "maximum": 60, "default": 10},
        }, "required": ["endpoint", "credential"], "additionalProperties": False,
    }, private_fields=("credential",))

    @plugin.tool(effects=("network",), output_schema={"type": "object"})
    async def query(text: str, ctx: ToolContext):
        """Query the configured HTTP service and return its complete JSON object."""
        config = await ctx.connections.require("query_api")
        def fetch():
            separator = "&" if "?" in config["endpoint"] else "?"
            request = Request(config["endpoint"] + separator + urlencode({"q": text}),
                              headers={"Authorization": "Bearer " + config["credential"]})
            with urlopen(request, timeout=config["timeout_seconds"]) as response:
                return json.load(response)
        try:
            return await asyncio.to_thread(fetch)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            return Result(is_error=True, status="unavailable", reason="query_request_failed")

    return plugin
