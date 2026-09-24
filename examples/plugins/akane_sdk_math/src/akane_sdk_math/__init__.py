"""A complete function plugin using only the independently installed SDK."""

from akane_plugin import Plugin

plugin = Plugin("example.math")


@plugin.tool
def add(a: int, b: int = 1) -> int:
    """Add two integer values."""
    return a + b


def create_plugin():
    return plugin
