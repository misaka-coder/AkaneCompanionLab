"""Compatibility import for the public SDK child-process lifecycle."""

from companion_v01.plugin_subprocess import PluginProcessRunner as ProcessRunner
from companion_v01.plugin_subprocess import drain

__all__ = ["ProcessRunner", "drain"]
