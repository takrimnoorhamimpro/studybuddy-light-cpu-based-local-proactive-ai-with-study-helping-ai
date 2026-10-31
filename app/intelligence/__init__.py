"""Study Buddy intelligence observability package.

This package keeps capability discovery, tool execution telemetry and the
Tkinter dashboard outside the chat and feature implementations.
"""

from .capability_registry import CapabilityRegistry, build_default_registry
from .tool_monitor import ToolMonitor

__all__ = ["CapabilityRegistry", "ToolMonitor", "build_default_registry"]
