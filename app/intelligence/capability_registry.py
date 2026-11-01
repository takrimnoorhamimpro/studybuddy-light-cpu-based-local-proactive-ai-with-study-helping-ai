"""Central registry describing what the running Study Buddy instance can do."""
from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec
import os
import sys
import time
from typing import Callable, Any


@dataclass(frozen=True)
class Capability:
    id: str
    name: str
    category: str
    description: str
    probe: Callable[[], dict[str, Any]]


class CapabilityRegistry:
    """A live, extensible registry. Providers must return status + detail."""

    VALID_STATUSES = {
        "active", "available", "disabled", "unavailable", "checking",
        "degraded", "error",
    }

    def __init__(self):
        self._capabilities: dict[str, Capability] = {}

    def register(self, capability: Capability) -> None:
        if capability.id in self._capabilities:
            raise ValueError(f"Capability already registered: {capability.id}")
        self._capabilities[capability.id] = capability

    def snapshot(self) -> list[dict[str, Any]]:
        result = []
        for capability in self._capabilities.values():
            try:
                state = capability.probe() or {}
                status = state.get("status", "unavailable")
                if status not in self.VALID_STATUSES:
                    status = "error"
                result.append({
                    "id": capability.id,
                    "name": capability.name,
                    "category": capability.category,
                    "description": capability.description,
                    "status": status,
                    "detail": str(state.get("detail", "")),
                    "checked_at": time.time(),
                })
            except Exception as exc:
                result.append({
                    "id": capability.id,
                    "name": capability.name,
                    "category": capability.category,
                    "description": capability.description,
                    "status": "error",
                    "detail": f"Status check failed: {type(exc).__name__}: {exc}",
                    "checked_at": time.time(),
                })
        return result

    def get(self, capability_id: str) -> dict[str, Any] | None:
        return next((item for item in self.snapshot() if item["id"] == capability_id), None)

    @staticmethod
    def _memory_probe(app):
        try:
            with app.memory._db_lock:
                app.memory.db.execute("SELECT 1").fetchone()
            return {"status": "active", "detail": "SQLite memory database is connected"}
        except Exception as exc:
            return {"status": "error", "detail": f"Memory database unavailable: {exc}"}

    @staticmethod
    def _voice_dependencies():
        missing = [name for name in ("sounddevice", "numpy", "faster_whisper")
                   if find_spec(name) is None]
        return missing


def build_default_registry(app) -> CapabilityRegistry:
    """Register the built-in capabilities using the actual desktop app state."""
    registry = CapabilityRegistry()

    def add(cid, name, category, description, probe):
        registry.register(Capability(cid, name, category, description, probe))

    def ollama():
        state = getattr(app, "ollama_available", None)
        if state is None:
            return {"status": "checking", "detail": f"Checking {app.core.llm.url}"}
        if state:
            return {"status": "active", "detail": f"Connected · model {app.core.llm.model}"}
        return {"status": "unavailable", "detail": "Ollama did not respond to the last health check"}

    def camera_preview():
        if not app.settings.get("webcam_enabled", False):
            return {"status": "disabled", "detail": "Camera access is disabled in Settings"}
        if getattr(app, "camera_active", False):
            return {"status": "active", "detail": "Live preview is active; frames are not sent to Ollama"}
        return {"status": "available", "detail": "Configured; preview starts only after explicit user action"}

    def camera_analysis():
        if not app.settings.get("webcam_enabled", False):
            return {"status": "disabled", "detail": "Camera feature is disabled in Settings"}
        analyzer = getattr(app, "expression_analyzer", None)
        if analyzer is not None:
            if analyzer.available:
                return {"status": "active" if app.camera_active else "available",
                        "detail": "Local FER+ expression classifier is loaded"}
            return {"status": "degraded", "detail": analyzer.last_error or "Expression model unavailable"}
        model_path = os.path.join(app.root_dir, "models", "emotion-ferplus-8.onnx")
        if os.path.isfile(model_path):
            return {"status": "available", "detail": "Model asset found; loads when camera opens"}
        return {"status": "degraded", "detail": "Camera can preview, but FER+ model asset is missing"}

    def desktop_awareness():
        if not app.awareness_enabled:
            return {"status": "disabled", "detail": "Desktop awareness is off in Settings"}
        if sys.platform != "win32":
            return {"status": "unavailable", "detail": "Foreground app awareness currently requires Windows"}
        if getattr(app, "_awareness_started", False):
            return {"status": "active", "detail": "Coarse foreground app and idle-time monitoring"}
        return {"status": "available", "detail": "Enabled; monitor thread has not started"}

    def voice_input():
        if not app.voice_enabled:
            return {"status": "disabled", "detail": "Voice input is disabled in Settings"}
        missing = CapabilityRegistry._voice_dependencies()
        if missing:
            return {"status": "degraded", "detail": "Missing optional packages: " + ", ".join(missing)}
        return {"status": "active" if getattr(app, "voice_listening", False) else "available",
                "detail": "Microphone capture + local Faster-Whisper transcription"}

    def voice_output():
        if find_spec("pyttsx3") is None:
            return {"status": "degraded", "detail": "Optional pyttsx3 package is not installed"}
        return {"status": "available", "detail": "Local text-to-speech is available"}

    def tasks():
        state = CapabilityRegistry._memory_probe(app)
        state["detail"] = "Task CRUD uses the shared SQLite memory database" if state["status"] == "active" else state["detail"]
        return state

    def reminders():
        state = CapabilityRegistry._memory_probe(app)
        state["detail"] = "Persistent reminders use the shared SQLite database and scheduler" if state["status"] == "active" else state["detail"]
        return state

    def focus_timer():
        if getattr(app, "timer_running", False):
            return {"status": "active", "detail": f"Timer running · {app._format_time(app.timer_remaining)} remaining"}
        return {"status": "available", "detail": "Focus timer is ready"}

    def proactive():
        if not app.settings.get("proactive_enabled", True):
            return {"status": "disabled", "detail": "Proactive check-ins are disabled in Settings"}
        if app.awareness_enabled:
            return {"status": "active", "detail": "Context-aware scheduler is running; check-ins respect quiet hours and user boundaries"}
        return {"status": "available", "detail": "Reminder scheduler is available; proactive app-context check-ins need Desktop Awareness enabled"}

    def backup():
        return {"status": "available", "detail": "Encrypted backup and restore workflows are available"}

    def agent_tool_calling():
        orchestrator = getattr(app.core, "tool_orchestrator", None)
        tools = getattr(getattr(app.core, "tool_registry", None), "names", lambda: [])()
        if orchestrator is not None:
            return {"status": "active", "detail": f"Native Ollama tool-calling loop connected · {len(tools)} registered tools"}
        return {"status": "unavailable", "detail": "Native tool orchestrator is not attached"}

    add("local_ai", "Local AI / Ollama", "AI", "Local language model connection and response generation", ollama)
    add("agent_tool_calling", "LLM Tool Calling", "Agent", "Ollama selects registered tools; allow-listed executor returns results to the model", agent_tool_calling)
    add("memory", "Long-term Memory", "Memory", "SQLite profile, facts, notes and conversation history", lambda: CapabilityRegistry._memory_probe(app))
    add("tasks", "Tasks", "Productivity", "Create, list, complete and delete tasks", tasks)
    add("reminders", "Reminders", "Productivity", "Persistent reminders and due-time notifications", reminders)
    add("focus_timer", "Focus Timer", "Productivity", "Pomodoro and custom-length focus timer", focus_timer)
    add("camera_preview", "Camera Preview", "Perception", "Explicitly enabled live camera preview; no recording", camera_preview)
    add("camera_analysis", "Facial-expression Analysis", "Perception", "Optional local FER+ facial-expression estimate", camera_analysis)
    add("desktop_awareness", "Desktop Awareness", "Context", "Coarse active-app category and idle-time signals", desktop_awareness)
    add("voice_input", "Voice Input", "Voice", "Microphone capture and local speech-to-text", voice_input)
    add("voice_output", "Voice Output", "Voice", "Local text-to-speech playback", voice_output)
    add("proactive", "Proactive Agent", "Automation", "Context-aware check-ins with user-boundary safeguards", proactive)
    add("backup", "Encrypted Backup", "Storage", "Encrypted profile backup and restore", backup)
    return registry
