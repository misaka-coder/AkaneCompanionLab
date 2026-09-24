"""A pure data service with no model tool or host import."""
import statistics
from akane_plugin import Plugin

plugin = Plugin("example.statistics")
api = plugin.service("statistics", version=1)


@api.method
def summarize(values: list[float]) -> dict[str, float]:
    """Calculate count, mean, median, minimum and maximum of nonempty data."""
    return {"count": float(len(values)), "mean": statistics.fmean(values),
            "median": statistics.median(values), "minimum": min(values), "maximum": max(values)}


def create_plugin():
    return plugin
