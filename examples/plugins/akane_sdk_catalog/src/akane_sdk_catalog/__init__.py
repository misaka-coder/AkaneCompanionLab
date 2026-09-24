"""Read a real HTTP JSON catalog; no host imports or sample success response."""

import asyncio
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

from akane_plugin import CapabilityResult, Plugin

plugin = Plugin("example.catalog", permissions=("network.read",))
CATALOG_SCHEMA = {
    "type": "array", "items": {
        "type": "object", "properties": {
            "name": {"type": "string"}, "quantity": {"type": "integer"}, "price": {"type": "number"},
        }, "required": ["name", "quantity", "price"], "additionalProperties": False,
    },
}


def _fetch(url):
    with urlopen(url, timeout=15) as response:
        return json.load(response)


@plugin.tool(effects=("network",), output_schema=CATALOG_SCHEMA)
async def fetch(url: str):
    """Read a JSON catalog of name, quantity and price rows from the requested HTTP URL."""
    if urlsplit(url).scheme not in {"http", "https"}:
        return CapabilityResult(is_error=True, status="validation_error", reason="catalog_http_url_required")
    try:
        return await asyncio.to_thread(_fetch, url)
    except (HTTPError, URLError, TimeoutError, OSError):
        return CapabilityResult(is_error=True, status="unavailable", reason="catalog_http_failed")
    except (ValueError, UnicodeError):
        return CapabilityResult(is_error=True, status="error", reason="catalog_json_invalid")


def create_plugin():
    return plugin
