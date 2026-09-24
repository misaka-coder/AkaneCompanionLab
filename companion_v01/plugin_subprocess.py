"""Compatibility import; the public SDK owns subprocess lifetime handling."""

from akane_plugin.processes import PluginProcessRunner, drain, run_completed, stop_process, terminate

__all__ = ["PluginProcessRunner", "drain", "run_completed", "stop_process", "terminate"]
